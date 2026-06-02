"""Embedded xterm.js terminal for ESFEX subprocess output.

The previous run console (``PythonConsole.run_subprocess``) wrote
``QProcess`` stdout into a ``QPlainTextEdit``. That has three
well-documented problems:

* It is not a TTY — programs that ask ``sys.stdout.isatty()`` (Rich,
  Gurobi, every modern CLI) see ``False`` and switch to degraded
  output (no colours, weird wrapping, less progress feedback).
* ``\\r`` carriage returns used by progress bars only work because the
  Python side parses them by hand (``_on_proc_stdout``). Anything
  fancier (Rich's "Live" region, ``\\x1b[2J`` clear, ``\\x1b[A`` cursor
  up) is silently dropped.
* The pipe can saturate when output bursts — combined with the fds
  being non-blocking after juliacall touches them, you get the
  ``BlockingIOError`` cascade that ended the last simulation run.

``RunOutputView`` is a ``QWebEngineView`` hosting xterm.js. A
companion :class:`PtyRunner` runs the child in a real PTY so the
child sees a 24x80-ish terminal and never blocks on the host's
read rate. Bytes flow:

* PTY -> ``PtyRunner.dataReceived(bytes)`` -> ``RunOutputView.feed(bytes)``
  -> JS ``_writeBase64`` -> ``term.write(Uint8Array)``.
* Keystrokes typed inside xterm -> JS ``onData`` -> bridge slot
  ``user_input(str)`` -> ``PtyRunner.write(bytes)`` -> PTY.

QWebChannel is the bridge. Bytes are framed as base64 because
QWebChannel marshals to JSON which can't carry arbitrary 8-bit data.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from esfex.visualization.pty_runner import PtyRunner, PtyUnavailable

_log = logging.getLogger(__name__)

_RESOURCES_DIR = Path(__file__).parent / "resources"


class _Bridge(QObject):
    """QWebChannel-exposed object the page talks to.

    Kept tiny on purpose: every slot is the JS-facing contract and
    must be back-compatible with the bundled ``terminal.html``.
    """

    # Re-emitted to the owning RunOutputView, which routes them to
    # the active PtyRunner.
    userInput = Signal(str)
    userResize = Signal(int, int)  # rows, cols

    @Slot(str)
    def user_input(self, data: str) -> None:
        self.userInput.emit(data)

    @Slot(int, int)
    def user_resize(self, rows: int, cols: int) -> None:
        self.userResize.emit(rows, cols)


class RunOutputView(QWebEngineView):
    """xterm.js-backed view for running ESFEX (or any) subprocess.

    Public surface:

    * :meth:`run`    — spawn ``args[0]`` with ``args[1:]`` in a PTY.
    * :meth:`stop`   — SIGTERM the child; force-kills if it ignores.
    * :meth:`is_running` — does the user have an active child?
    * :attr:`finished` — Signal emitted with the exit status code when
      the child exits (or -1 if it was killed externally).
    """

    finished = Signal(int)
    started = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # WebChannel + bridge: the page can talk back to us before the
        # PTY exists; pendinginput is buffered on the JS side.
        self._bridge = _Bridge(self)
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("runner", self._bridge)
        self.page().setWebChannel(self._channel)

        # Local files in resources/ load images and the xterm bundles;
        # without these settings the page can't reach its siblings.
        settings = self.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )

        # Load the static page. Subsequent runs just feed bytes into
        # xterm — we never reload.
        self._html_url = QUrl.fromLocalFile(str(_RESOURCES_DIR / "terminal.html"))
        self._page_ready = False
        self.loadFinished.connect(self._on_load_finished)
        self.load(self._html_url)

        # PTY child runner, recreated per run.
        self._pty: Optional[PtyRunner] = None
        self._pending_size: Optional[tuple[int, int]] = None

        # Bridge -> PTY input/resize routing.
        self._bridge.userInput.connect(self._on_user_input)
        self._bridge.userResize.connect(self._on_user_resize)

    # ── Public API ────────────────────────────────────────────────────

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        """Spawn args in a PTY. Output streams into the terminal.

        If a previous run is still active it is stopped first.
        """
        if not args:
            raise ValueError("run() requires at least the executable in args[0]")

        self.stop()  # idempotent for a fresh view
        # Wipe the previous run's scrollback so the user starts each
        # invocation with a clean slate (matches the "[process exited]"
        # boundary the old QProcess console drew). The legacy notice +
        # exit line are not part of the program's output, so losing
        # them on rerun is the natural behaviour.
        self.clear()

        try:
            self._pty = PtyRunner(args, env=env, cwd=cwd, parent=self)
        except PtyUnavailable as exc:
            # ptyprocess missing or PTY not supported (Windows). Surface
            # this in the terminal itself, not as a dialog, so the user
            # sees it in context.
            self._notice(
                f"Cannot start: {exc}. "
                "Install ptyprocess (pip install ptyprocess) or run "
                "ESFEX from a real terminal."
            )
            return

        self._pty.dataReceived.connect(self._on_pty_data)
        self._pty.exited.connect(self._on_pty_exited)
        self._pty.start()
        self._notice(f"$ {' '.join(args)}")
        self.started.emit()

        # If JS already sent its initial size before the PTY existed,
        # forward it now.
        if self._pending_size is not None:
            rows, cols = self._pending_size
            self._pty.setwinsize(rows, cols)

    def interrupt(self) -> None:
        """Graceful cancel — send SIGINT (same as Ctrl+C) to the child."""
        if self._pty is not None and self._pty.isRunning():
            self._pty.interrupt()
            self._notice("[interrupt sent — Ctrl+C]")

    def stop(self, force: bool = False) -> None:
        """Terminate the child. ``force=True`` escalates to SIGKILL."""
        if self._pty is not None and self._pty.isRunning():
            self._pty.request_stop(force=force)
            self._notice("[force kill sent]" if force else "[stop requested]")

    def is_running(self) -> bool:
        return self._pty is not None and self._pty.isRunning()

    def clear(self) -> None:
        self._call_js("_clear()")

    # ── PTY -> terminal ───────────────────────────────────────────────

    def _on_pty_data(self, data: bytes) -> None:
        if not self._page_ready:
            # Drop: the page hasn't loaded yet so the JS function is
            # not addressable. In practice this only happens for the
            # very first chunk of the very first run when the user
            # clicked Run before the WebEngineView painted; the PTY
            # itself buffers a few KB so a tiny race is benign.
            return
        b64 = base64.b64encode(data).decode("ascii")
        # Embed safely: b64 is alphanum + '+/=' only, so a quoted
        # f-string is fine. No need for _js_arg here.
        self._call_js(f"_writeBase64('{b64}')")

    def _on_pty_exited(self, status: int) -> None:
        self._notice(f"[process exited: {status}]")
        self.finished.emit(status)

    # ── Terminal -> PTY ───────────────────────────────────────────────

    def _on_user_input(self, data: str) -> None:
        if self._pty is None or not self._pty.isRunning():
            return
        # The page sends Python str; encode UTF-8 (xterm.js gives us
        # UTF-8 strings from keyboard input by default).
        self._pty.write(data.encode("utf-8", errors="replace"))

    def _on_user_resize(self, rows: int, cols: int) -> None:
        self._pending_size = (rows, cols)
        if self._pty is not None and self._pty.isRunning():
            self._pty.setwinsize(rows, cols)

    # ── Page lifecycle ────────────────────────────────────────────────

    def _on_load_finished(self, ok: bool) -> None:
        self._page_ready = bool(ok)
        if not ok:
            _log.error("terminal.html failed to load")

    def _notice(self, text: str) -> None:
        """Print a wrapper notice (e.g. command echo, exit status)."""
        if not self._page_ready:
            return
        # Escape for embedding in JS single-quoted string literal.
        # json.dumps gives us a valid JS string (double-quoted),
        # including escaping of \\, \n,   etc. Strip surrounding
        # quotes so we can wrap in _appendNotice('...').
        escaped = json.dumps(text)
        self._call_js(f"_appendNotice({escaped})")

    def _call_js(self, script: str) -> None:
        self.page().runJavaScript(script)
