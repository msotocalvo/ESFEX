"""Python script editor panel for the ESFEX Studio (QGIS-style)."""

from __future__ import annotations

import keyword
import builtins
import re
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.theme import current_theme


# ======================================================================
# Syntax highlighter
# ======================================================================

class _PythonHighlighter(QSyntaxHighlighter):
    """Basic Python syntax highlighting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        self._build_rules()

    def _build_rules(self):
        self._rules.clear()
        syn = current_theme().syntax

        # Keywords
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(syn.keyword))
        kw_fmt.setFontWeight(700)
        kw_pattern = r"\b(?:" + "|".join(keyword.kwlist) + r")\b"
        self._rules.append((re.compile(kw_pattern), kw_fmt))

        # Builtins
        bi_fmt = QTextCharFormat()
        bi_fmt.setForeground(QColor(syn.builtin))
        bi_names = [n for n in dir(builtins) if not n.startswith("_")]
        bi_pattern = r"\b(?:" + "|".join(bi_names) + r")\b"
        self._rules.append((re.compile(bi_pattern), bi_fmt))

        # self
        self_fmt = QTextCharFormat()
        self_fmt.setForeground(QColor(syn.self_ref))
        self_fmt.setFontItalic(True)
        self._rules.append((re.compile(r"\bself\b"), self_fmt))

        # Numbers
        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(syn.number))
        self._rules.append((re.compile(r"\b\d+(\.\d*)?([eE][+-]?\d+)?\b"), num_fmt))

        # Decorators
        dec_fmt = QTextCharFormat()
        dec_fmt.setForeground(QColor(syn.decorator))
        self._rules.append((re.compile(r"@\w+"), dec_fmt))

        # Strings (single/double, single-line only)
        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(syn.string))
        self._rules.append((re.compile(r"f?\"\"\".*?\"\"\"|f?'''.*?'''", re.DOTALL), str_fmt))
        self._rules.append((re.compile(r"f?\"[^\"\\]*(\\.[^\"\\]*)*\""), str_fmt))
        self._rules.append((re.compile(r"f?'[^'\\]*(\\.[^'\\]*)*'"), str_fmt))

        # Comment (must be last to override other matches)
        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor(syn.comment))
        self._rules.append((re.compile(r"#[^\n]*"), cmt_fmt))

    def refresh_theme(self):
        """Re-read theme colors and re-highlight the document."""
        self._build_rules()
        self.rehighlight()

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ======================================================================
# Line number area
# ======================================================================

class _LineNumberArea(QWidget):
    """Gutter widget that paints line numbers for a QPlainTextEdit."""

    def __init__(self, editor: _CodeEditor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


# ======================================================================
# Code editor (QPlainTextEdit + line numbers)
# ======================================================================

class _CodeEditor(QPlainTextEdit):
    """Text editor with line numbers and Python syntax highlighting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("consoleWidget")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(
            self.fontMetrics().horizontalAdvance(" ") * 4
        )

        self._highlighter = _PythonHighlighter(self.document())

        # Line numbers
        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self._update_line_area_width()

    # -- Tab inserts spaces --
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab:
            self.insertPlainText("    ")
            return
        if event.key() == Qt.Key.Key_Backtab:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.KeepAnchor, 4,
            )
            if cursor.selectedText() == "    ":
                cursor.removeSelectedText()
            return
        super().keyPressEvent(event)

    # -- Line number support --
    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_area_width(self, _=0):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_area(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_area_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def refresh_theme(self):
        """Update syntax highlighting and line-number gutter to the current theme."""
        self._highlighter.refresh_theme()
        self._line_area.update()

    def apply_editor_preferences(self, prefs: dict) -> None:
        """Apply editor preference dict to this editor widget."""
        family = prefs.get("font_family")
        size = prefs.get("font_size")
        if family or size:
            current = self.font()
            font = QFont(
                family if family else current.family(),
                size if size else current.pointSize(),
            )
            self.setFont(font)

        tab_width = prefs.get("tab_width")
        if tab_width is not None:
            font = self.font()
            self.setTabStopDistance(
                QFontMetricsF(font).horizontalAdvance(" " * tab_width)
            )

        show_ln = prefs.get("show_line_numbers")
        if show_ln is not None:
            self._line_area.setVisible(show_ln)
            if show_ln:
                self._update_line_area_width()
            else:
                self.setViewportMargins(0, 0, 0, 0)

        word_wrap = prefs.get("word_wrap")
        if word_wrap is not None:
            self.setWordWrapMode(
                QTextOption.WrapMode.WordWrap
                if word_wrap
                else QTextOption.WrapMode.NoWrap
            )

    def paint_line_numbers(self, event):
        syn = current_theme().syntax
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor(syn.line_number_bg))

        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(syn.line_number_fg))
                painter.drawText(
                    0, top,
                    self._line_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_num + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

        painter.end()


# ======================================================================
# Script editor panel (tabs + toolbar)
# ======================================================================

class ScriptEditor(QWidget):
    """Multi-tab Python script editor with run/save/open controls."""

    runScript = Signal(str, str)  # (source, label)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Toolbar
        tb = QHBoxLayout()
        tb.setContentsMargins(2, 2, 2, 0)

        # File actions (New/Open/Save/Save As) are grouped under a single
        # menu button anchored at the panel's top-left corner, keeping the
        # toolbar uncluttered.
        self._menu_btn = QToolButton()
        self._menu_btn.setText("☰")  # hamburger glyph
        self._menu_btn.setToolTip(tr("script_editor.file_menu_tip"))
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setFixedHeight(24)
        self._file_menu = QMenu(self._menu_btn)
        self._act_new = self._file_menu.addAction(
            tr("script_editor.new_btn"), self._on_new,
        )
        self._act_open = self._file_menu.addAction(
            tr("script_editor.open_btn"), self._on_open,
        )
        self._act_save = self._file_menu.addAction(
            tr("script_editor.save_btn"), self._on_save,
        )
        self._act_save_as = self._file_menu.addAction(
            tr("script_editor.save_as_btn"), self._on_save_as,
        )
        self._menu_btn.setMenu(self._file_menu)
        tb.addWidget(self._menu_btn)

        tb.addStretch()

        # Run button mirrors the simulation Stop button's compact style
        # (glyph + word, 24px tall, tight padding) for visual consistency.
        self._btn_run = QPushButton("▶  " + tr("script_editor.run_btn"))
        self._btn_run.setToolTip(tr("script_editor.run_tip"))
        self._btn_run.setFixedHeight(24)
        self._btn_run.setStyleSheet(
            "QPushButton { padding: 2px 10px; font-size: 11px; }"
        )
        self._btn_run.clicked.connect(self._on_run)
        tb.addWidget(self._btn_run)

        layout.addLayout(tb)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._on_close_tab)
        layout.addWidget(self._tabs)

        # File paths per tab index
        self._file_paths: dict[int, str | None] = {}
        self._tab_counter = 0
        self._editor_prefs: dict = {}  # stored for new tabs

        # Start with one empty tab
        self._add_tab(tr("script_editor.untitled"))

    # ------------------------------------------------------------------
    # Retranslation
    # ------------------------------------------------------------------

    def retranslateUi(self):
        """Update translatable strings."""
        self._menu_btn.setToolTip(tr("script_editor.file_menu_tip"))
        self._act_new.setText(tr("script_editor.new_btn"))
        self._act_open.setText(tr("script_editor.open_btn"))
        self._act_save.setText(tr("script_editor.save_btn"))
        self._act_save_as.setText(tr("script_editor.save_as_btn"))
        self._btn_run.setText("▶  " + tr("script_editor.run_btn"))
        self._btn_run.setToolTip(tr("script_editor.run_tip"))
        # Update untitled tab names (tabs with no file path)
        for idx in range(self._tabs.count()):
            if self._file_paths.get(idx) is None:
                self._tabs.setTabText(idx, tr("script_editor.untitled"))

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def _add_tab(self, title: str, content: str = "") -> _CodeEditor:
        editor = _CodeEditor()
        if self._editor_prefs:
            editor.apply_editor_preferences(self._editor_prefs)
        if content:
            editor.setPlainText(content)
        idx = self._tabs.addTab(editor, title)
        self._file_paths[idx] = None
        self._tabs.setCurrentIndex(idx)
        self._tab_counter += 1
        return editor

    def _current_editor(self) -> _CodeEditor | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, _CodeEditor) else None

    def _on_close_tab(self, index: int):
        if self._tabs.count() <= 1:
            # Keep at least one tab, just clear it
            editor = self._tabs.widget(0)
            if isinstance(editor, _CodeEditor):
                editor.clear()
                self._tabs.setTabText(0, tr("script_editor.untitled"))
                self._file_paths[0] = None
            return
        self._file_paths.pop(index, None)
        self._tabs.removeTab(index)
        # Re-index file paths
        new_paths: dict[int, str | None] = {}
        for i in range(self._tabs.count()):
            old_key = None
            for k, v in list(self._file_paths.items()):
                w = self._tabs.widget(i)
                if w is not None:
                    old_key = k
                    break
            new_paths[i] = self._file_paths.get(i)
        self._file_paths = new_paths

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_new(self):
        self._tab_counter += 1
        self._add_tab(f"untitled_{self._tab_counter}")

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Python Script", "",
            "Python Files (*.py);;All Files (*)",
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        name = Path(path).name
        editor = self._add_tab(name, content)
        idx = self._tabs.currentIndex()
        self._file_paths[idx] = path

    def _on_save(self):
        idx = self._tabs.currentIndex()
        path = self._file_paths.get(idx)
        if path:
            self._save_to(path)
        else:
            self._on_save_as()

    def _on_save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Python Script", "",
            "Python Files (*.py);;All Files (*)",
        )
        if not path:
            return
        idx = self._tabs.currentIndex()
        self._file_paths[idx] = path
        self._tabs.setTabText(idx, Path(path).name)
        self._save_to(path)

    def _save_to(self, path: str):
        editor = self._current_editor()
        if editor is None:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(editor.toPlainText())

    def refresh_theme(self):
        """Propagate a theme change to all open editor tabs."""
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if isinstance(w, _CodeEditor):
                w.refresh_theme()

    def apply_preferences(self, prefs: dict) -> None:
        """Apply editor preferences to all open tabs and store for new tabs.

        Parameters
        ----------
        prefs:
            Dict with optional keys: ``font_family``, ``font_size``,
            ``tab_width``, ``show_line_numbers``, ``word_wrap``,
            ``auto_indent``.
        """
        self._editor_prefs = dict(prefs)
        for i in range(self._tabs.count()):
            w = self._tabs.widget(i)
            if isinstance(w, _CodeEditor):
                w.apply_editor_preferences(prefs)

    def _on_run(self):
        editor = self._current_editor()
        if editor is None:
            return
        source = editor.toPlainText()
        if not source.strip():
            return
        label = self._tabs.tabText(self._tabs.currentIndex())
        self.runScript.emit(source, label)
