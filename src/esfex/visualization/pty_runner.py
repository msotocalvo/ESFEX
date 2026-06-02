"""PTY child runner for :class:`RunOutputView`.

Wraps :mod:`ptyprocess` in a ``QThread`` so the GUI event loop never
blocks on PTY reads. The thread loop pulls bytes off the master fd
and emits them; writes (keystrokes) and resize requests come in from
the GUI thread via thread-safe ``QObject`` methods.

Why a real PTY and not ``QProcess``:

* ``QProcess`` connects the child to pipes. ``isatty()`` returns
  ``False``, so Rich/Gurobi/etc. fall back to dumb output.
* Pipes have a 64 KB kernel buffer; if the GUI is slow to drain it,
  a write-burst either blocks the child (blocking fd) or — once
  juliacall flips the fd to ``O_NONBLOCK`` — raises
  ``BlockingIOError``. With a PTY, the kernel does line-disc buffering
  and the child sees a sane TTY.
* The child can ask for terminal size, receive ``SIGWINCH`` on resize,
  and read keystrokes. The previous ``QPlainTextEdit`` console could
  never do any of that.

If ``ptyprocess`` is not installed (or on Windows where ``fork`` /
``openpty`` are unavailable), :class:`PtyRunner` instantiation raises
:class:`PtyUnavailable`. The caller is expected to catch this and
fall back to its previous transport.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import QMutex, QMutexLocker, QThread, Signal

_log = logging.getLogger(__name__)


class PtyUnavailable(RuntimeError):
    """ptyprocess can't be imported or the platform lacks PTY support."""


class PtyRunner(QThread):
    """Run a child process attached to a pseudo-terminal.

    Signals:
      * :attr:`dataReceived` — emitted with raw ``bytes`` for each chunk
        read from the master fd. Receivers should not touch the data
        on the worker thread; Qt's queued connections marshal it back
        to whichever thread the receiver lives on.
      * :attr:`exited` — emitted once with the child's exit status
        (signed int, like ``os.waitpid``: negative means killed by
        signal of that absolute value).
    """

    dataReceived = Signal(bytes)
    exited = Signal(int)

    # Bytes per read(). Big enough to amortise syscalls on the
    # high-throughput case (Gurobi log) without starving the GUI.
    _READ_CHUNK = 65536

    def __init__(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)

        try:
            import ptyprocess  # noqa: F401  (presence test)
        except ImportError as exc:
            raise PtyUnavailable(
                "ptyprocess is required for the xterm.js run output view. "
                "Install it with: pip install ptyprocess"
            ) from exc

        if not hasattr(os, "fork"):
            # ptyprocess on Windows uses pywinpty; we don't ship a
            # winpty integration here. Surface the limitation explicitly
            # so the GUI can fall back to the old QProcess console.
            raise PtyUnavailable(
                "PTY runner is only supported on POSIX systems "
                "(Linux / macOS). Windows is not yet wired up."
            )

        self._args = list(args)
        # Build a real environment to hand to the child. We start from
        # the current process env so PATH / LANG / HOME survive, then
        # apply the caller-supplied overrides on top. Force TERM if the
        # caller didn't set one so curses-aware programs configure
        # themselves correctly.
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        merged_env.setdefault("TERM", "xterm-256color")
        # Some downstream tools key off COLUMNS/LINES even though the
        # PTY size is authoritative. Start with conservative defaults;
        # the GUI will call setwinsize once xterm.js reports the real
        # geometry.
        merged_env.setdefault("COLUMNS", "80")
        merged_env.setdefault("LINES", "24")
        self._env = merged_env
        self._cwd = cwd

        self._proc = None
        self._stop_requested = False
        self._write_mutex = QMutex()
        self._resize_queue: Optional[tuple[int, int]] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def run(self) -> None:
        from ptyprocess import PtyProcess

        try:
            self._proc = PtyProcess.spawn(
                self._args,
                env=self._env,
                cwd=self._cwd,
                # Generous default size; xterm.js will resize immediately.
                dimensions=(24, 80),
            )
        except FileNotFoundError as exc:
            _log.error("Cannot spawn %r: %s", self._args[0], exc)
            self.exited.emit(127)
            return
        except Exception:
            _log.exception("Unexpected failure spawning %r", self._args)
            self.exited.emit(1)
            return

        try:
            self._read_loop()
        finally:
            status = self._wait_for_exit()
            self.exited.emit(status)

    def _read_loop(self) -> None:
        proc = self._proc
        while not self._stop_requested:
            # Apply any queued resize first — the user can drag the
            # GUI window before any new data arrives.
            if self._resize_queue is not None:
                rows, cols = self._resize_queue
                self._resize_queue = None
                try:
                    proc.setwinsize(rows, cols)
                except Exception:
                    _log.debug("setwinsize raised", exc_info=True)

            try:
                data = proc.read(self._READ_CHUNK)
            except EOFError:
                return
            except OSError as exc:
                # EIO on Linux is the standard "PTY master closed" signal.
                if exc.errno == 5:  # EIO
                    return
                _log.debug("PTY read OSError", exc_info=True)
                return
            if not data:
                return
            # ptyprocess.read returns bytes on PtyProcess (not str).
            self.dataReceived.emit(data)

    def _wait_for_exit(self) -> int:
        if self._proc is None:
            return -1
        try:
            self._proc.close(force=True)
        except Exception:
            pass
        # PtyProcess.wait blocks until the child reaps. exitstatus is
        # populated after wait(); for a signal, signalstatus is set
        # instead. Convert to a single int: positive exit, negative -N
        # for SIGN (matching POSIX shell convention).
        try:
            self._proc.wait()
        except Exception:
            pass
        if self._proc.exitstatus is not None:
            return int(self._proc.exitstatus)
        if self._proc.signalstatus is not None:
            return -int(self._proc.signalstatus)
        return -1

    # ── GUI-thread API ────────────────────────────────────────────────

    def write(self, data: bytes) -> None:
        """Send keystrokes to the child's stdin."""
        if self._proc is None or not self.isRunning():
            return
        # PTY writes are serialised through a mutex even though we
        # never write from multiple threads — defensive in case the
        # caller hooks user_input from a queued connection raced with
        # a programmatic write (e.g. an "auto-answer" button).
        with QMutexLocker(self._write_mutex):
            try:
                self._proc.write(data)
            except Exception:
                _log.debug("PTY write failed", exc_info=True)

    def setwinsize(self, rows: int, cols: int) -> None:
        """Queue a resize. Applied at the next read-loop iteration."""
        # We don't call proc.setwinsize directly here: the read() blocks
        # in the worker thread, and ptyprocess uses fcntl on the master
        # fd which is technically OK from any thread but mixing reads
        # and ioctls from different threads is asking for surprise.
        # Queue + apply in the loop instead.
        self._resize_queue = (max(1, int(rows)), max(1, int(cols)))

    def interrupt(self) -> None:
        """Send SIGINT to the child — the exact equivalent of Ctrl+C.

        Writes the ETX byte (``\\x03``) to the PTY master. The PTY's
        line discipline interprets ETX as the INTR character and
        raises SIGINT on the *foreground process group*, so the
        signal reaches ESFEX and any subprocess it spawned — just
        like pressing Ctrl+C in a real terminal. This does NOT set
        ``_stop_requested``: a graceful interrupt should let the child
        unwind and exit on its own; the read loop keeps running so the
        user still sees the shutdown output.
        """
        if self._proc is None or not self.isRunning():
            return
        with QMutexLocker(self._write_mutex):
            try:
                self._proc.write(b"\x03")
            except Exception:
                _log.debug("PTY interrupt (\\x03) write failed", exc_info=True)

    def request_stop(self, force: bool = False) -> None:
        """Ask the child to exit; the read loop will see the flag.

        ``force=False`` (default) is a polite terminate (ptyprocess
        escalates SIGHUP → SIGCONT → SIGINT). ``force=True`` escalates
        all the way to SIGKILL for a process that ignores the polite
        signals.
        """
        self._stop_requested = True
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.terminate(force=force)
            except Exception:
                _log.debug("terminate(force=%s) raised", force, exc_info=True)
