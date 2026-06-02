"""Embedded Python REPL for the ESFEX Studio."""

from __future__ import annotations

import code
import io
import re
import sys
import traceback
from typing import Any

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

_ANSI_RE = re.compile(r"\x1b\[[\d;]*[A-Za-z]|\x1b\][\d;]*\x07?")


class PythonConsole(QPlainTextEdit):
    """Interactive Python console widget.

    Embeds a ``code.InteractiveInterpreter`` whose namespace can be
    pre-populated with objects the user should have access to (e.g.
    the GUI model and configuration).
    """

    subprocessFinished = Signal(int)  # exit_code (0 = success)

    _PS1 = ">>> "
    _PS2 = "... "

    def __init__(self, namespace: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("consoleWidget")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        ns = {"__name__": "__console__", "__doc__": None}
        if namespace:
            ns.update(namespace)
        self._interpreter = code.InteractiveInterpreter(locals=ns)

        self._history: list[str] = []
        self._history_idx: int = 0
        self._current_edit: str = ""        
 
        # Multi-line buffer
        self._buffer: list[str] = []

        # Subprocess management
        self._process: QProcess | None = None

        # Write banner
        banner = (
            "Python Console - ESFEX Studio\n"
            "Available: model, state, config, window, esfex, np\n"
            "Type esfex.help() for scripting API reference.\n"
        )
        self.appendPlainText(banner)
        self._write_prompt()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_namespace(self, **kwargs: Any):
        """Add or update variables in the interpreter namespace."""
        self._interpreter.locals.update(kwargs)

    def run_script(self, source: str, label: str = "<script>"):
        """Execute a complete script and display output in the console."""
        self.appendPlainText(f"\n--- Running {label} ---")

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        capture = io.StringIO()
        sys.stdout = capture
        sys.stderr = capture

        try:
            compiled = code.compile_command(source, label, "exec")
            if compiled is not None:
                self._interpreter.runcode(compiled)
        except SyntaxError:
            traceback.print_exc(file=capture)
        except SystemExit:
            pass
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output = capture.getvalue()
        if output:
            if output.endswith("\n"):
                output = output[:-1]
            self.appendPlainText(output)

        self.appendPlainText(f"--- Finished {label} ---\n")
        self._write_prompt()

    def run_subprocess(
        self,
        args: list[str],
        label: str = "process",
        env: dict[str, str] | None = None,
    ):
        """Run an external command and stream output to the console.

        Args:
            args: Command arguments (first element is the executable).
            label: Display label for the process.
            env: Extra environment variables to set for the subprocess.
        """
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self.appendPlainText("[error] A subprocess is already running. "
                                "Use Ctrl+C to cancel it first.\n")
            return

        self.appendPlainText(f"\n--- {label} ---")
        self.appendPlainText(f"$ {' '.join(args)}\n")

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._process.finished.connect(self._on_proc_finished)

        # Set extra environment variables (e.g. PYTHON_JULIACALL_SYSIMAGE)
        if env:
            from PySide6.QtCore import QProcessEnvironment

            proc_env = QProcessEnvironment.systemEnvironment()
            for k, v in env.items():
                proc_env.insert(k, v)
            self._process.setProcessEnvironment(proc_env)

        self._proc_label = label
        self._process.start(args[0], args[1:])

    def cancel_subprocess(self):
        """Kill the running subprocess, if any."""
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self.appendPlainText("\n--- Cancelling subprocess ---")
            self._process.kill()

    @property
    def subprocess_running(self) -> bool:
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    def _on_proc_stdout(self):
        if self._process is None:
            return
        data = self._process.readAllStandardOutput().data()
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        # Strip all ANSI escape sequences (colors, cursor movement, etc.)
        clean = _ANSI_RE.sub("", text)

        # Handle carriage returns: Rich progress bars use \r to update
        # the current line in-place.  Split on \r and keep the last
        # segment (the most recent state of each line).
        for line in clean.split("\n"):
            if not line:
                continue
            # Take only the text after the last \r (in-place overwrite)
            parts = line.split("\r")
            visible = parts[-1]
            if not visible:
                # Line was purely \r — skip
                continue
            if parts[0] != visible and len(parts) > 1:
                # This was an in-place update → replace the last line
                self._replace_last_block(visible)
            else:
                self.appendPlainText(visible)

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _replace_last_block(self, text: str):
        """Replace the content of the last line in the console."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def _on_proc_finished(self, exit_code: int, exit_status):
        label = getattr(self, "_proc_label", "process")
        if exit_status == QProcess.ExitStatus.CrashExit:
            self.appendPlainText(f"\n--- {label} crashed ---\n")
        elif exit_code != 0:
            self.appendPlainText(f"\n--- {label} failed (exit code {exit_code}) ---\n")
        else:
            self.appendPlainText(f"\n--- {label} completed ---\n")
        self._process = None
        self._write_prompt()
        self.subprocessFinished.emit(exit_code)

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent):
        cursor = self.textCursor()
        # Prevent editing above the current prompt line
        if cursor.blockNumber() < self._prompt_block():
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)

        key = event.key()
        modifiers = event.modifiers()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_enter()
            return

        if key == Qt.Key.Key_Up:
            self._history_navigate(-1)
            return

        if key == Qt.Key.Key_Down:
            self._history_navigate(1)
            return

        if key == Qt.Key.Key_Home:
            # Move to start of editable area (after prompt)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            prompt_len = len(self._current_prompt())
            cursor.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.MoveAnchor,
                prompt_len,
            )
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                old = self.textCursor()
                cursor.setPosition(old.position(), QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            return

        if key == Qt.Key.Key_Backspace:
            # Don't delete the prompt
            col = cursor.positionInBlock()
            if col <= len(self._current_prompt()):
                return

        if key == Qt.Key.Key_C and modifiers & Qt.KeyboardModifier.ControlModifier:
            if not self.textCursor().hasSelection():
                if self.subprocess_running:
                    self.cancel_subprocess()
                    return
                # Ctrl+C without selection: cancel current input
                self.appendPlainText("")
                self._buffer.clear()
                self._write_prompt()
                return

        super().keyPressEvent(event)

    def _handle_enter(self):
        line = self._get_current_line()

        # Append to display
        self.appendPlainText("")

        # Add to history if non-empty
        stripped = line.strip()
        if stripped and (not self._history or self._history[-1] != stripped):
            self._history.append(stripped)
        self._history_idx = len(self._history)

        self._buffer.append(line)
        source = "\n".join(self._buffer)

        # Check if the source is complete (returns False if it needs more input)
        needs_more = self._run_source(source)

        if needs_more:
            self._write_prompt(continuation=True)
        else:
            self._buffer.clear()
            self._write_prompt()

    def _run_source(self, source: str) -> bool:
        """Execute source code. Returns True if more input is needed."""
        # Capture stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        capture = io.StringIO()
        sys.stdout = capture
        sys.stderr = capture

        try:
            needs_more = self._interpreter.runsource(source, "<console>")
        except SystemExit:
            needs_more = False
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output = capture.getvalue()
        if output:
            # Remove trailing newline to avoid extra blank line
            if output.endswith("\n"):
                output = output[:-1]
            self.appendPlainText(output)

        return needs_more

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def _history_navigate(self, direction: int):
        if not self._history:
            return

        if self._history_idx == len(self._history):
            self._current_edit = self._get_current_line()

        new_idx = self._history_idx + direction
        if new_idx < 0:
            new_idx = 0
        elif new_idx > len(self._history):
            new_idx = len(self._history)

        self._history_idx = new_idx

        if new_idx == len(self._history):
            text = self._current_edit
        else:
            text = self._history[new_idx]

        self._replace_current_line(text)

    # ------------------------------------------------------------------
    # Line helpers
    # ------------------------------------------------------------------

    def _prompt_block(self) -> int:
        return self.blockCount() - 1

    def _current_prompt(self) -> str:
        return self._PS2 if self._buffer else self._PS1

    def _get_current_line(self) -> str:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        line = cursor.selectedText()
        prompt = self._current_prompt()
        if line.startswith(prompt):
            return line[len(prompt):]
        return line

    def _replace_current_line(self, text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        prompt = self._current_prompt()
        cursor.insertText(prompt + text)
        self.setTextCursor(cursor)

    def _write_prompt(self, continuation: bool = False):
        prompt = self._PS2 if continuation else self._PS1
        self.appendPlainText(prompt)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        clear_action = QAction("Clear console", self)
        clear_action.triggered.connect(self.clear_console)
        menu.addAction(clear_action)
        menu.exec(event.globalPos())

    def clear_console(self):
        """Clear all output and reset to a fresh prompt."""
        self._buffer.clear()
        self.clear()
        self._write_prompt()
