"""Modern reusable widgets: collapsible sections, toast notifications, empty states."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# ══════════════════════════════════════════════════════════════════
# CollapsibleSection
# ══════════════════════════════════════════════════════════════════


class CollapsibleSection(QWidget):
    """Animated collapsible section replacing QGroupBox.

    Usage::

        section = CollapsibleSection("Capacity")
        layout = section.content_layout  # QVBoxLayout for child widgets
        layout.addWidget(...)
        parent_layout.addWidget(section)
    """

    toggled = Signal(bool)  # expanded

    def __init__(
        self,
        title: str = "",
        expanded: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._expanded = expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header button ──
        self._header = QToolButton()
        self._header.setObjectName("collapsibleHeader")
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )
        self._header.clicked.connect(self._on_toggle)
        outer.addWidget(self._header)

        # ── Content area ──
        self._content = QWidget()
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(8, 4, 4, 4)
        self.content_layout.setSpacing(4)
        outer.addWidget(self._content)

        # ── Animation ──
        from esfex.visualization.theme import current_theme

        dur = current_theme().animations.duration_normal

        self._anim = QPropertyAnimation(self._content, b"maximumHeight")
        self._anim.setDuration(dur)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self._release_connected = False
        if not expanded:
            self._content.setMaximumHeight(0)

    # ── Public API ──

    @property
    def expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expand: bool, animate: bool = True):
        if expand == self._expanded:
            return
        self._expanded = expand
        self._header.setChecked(expand)
        self._header.setArrowType(
            Qt.ArrowType.DownArrow if expand else Qt.ArrowType.RightArrow
        )
        if animate:
            self._animate(expand)
        else:
            self._content.setMaximumHeight(16777215 if expand else 0)
        self.toggled.emit(expand)

    def set_title(self, title: str):
        self._header.setText(title)

    # ── Internals ──

    def _on_toggle(self):
        self.set_expanded(self._header.isChecked())

    def _animate(self, expand: bool):
        # Temporarily allow full size to measure
        self._content.setMaximumHeight(16777215)
        target_h = self._content.sizeHint().height()
        self._content.setMaximumHeight(0 if expand else target_h)
        self._anim.setStartValue(0 if expand else target_h)
        self._anim.setEndValue(target_h if expand else 0)
        if expand:
            # After expanding, remove height cap so content can resize
            if not self._release_connected:
                self._anim.finished.connect(self._release_height)
                self._release_connected = True
        else:
            if self._release_connected:
                self._anim.finished.disconnect(self._release_height)
                self._release_connected = False
        self._anim.start()

    def _release_height(self):
        if self._expanded:
            self._content.setMaximumHeight(16777215)


# ══════════════════════════════════════════════════════════════════
# Toast Notification
# ══════════════════════════════════════════════════════════════════

_TOAST_OBJECT_NAMES = {
    "success": "toastSuccess",
    "warning": "toastWarning",
    "error": "toastError",
    "info": "toastInfo",
}


class _ToastWidget(QFrame):
    """Single toast notification with slide-in animation."""

    closed = Signal()

    def __init__(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 4000,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName(_TOAST_OBJECT_NAMES.get(level, "toastInfo"))
        self.setFixedWidth(320)
        self.setMinimumHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # Icon prefix
        icons = {"success": "+", "warning": "!", "error": "x", "info": "i"}
        icon_label = QLabel(icons.get(level, "i"))
        icon_label.setFixedWidth(18)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-weight: bold; font-size: 14px; background: transparent;")
        layout.addWidget(icon_label)

        # Message
        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("background: transparent;")
        layout.addWidget(msg_label, stretch=1)

        # Close button
        close_btn = QPushButton("x")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "border: none; background: transparent; font-weight: bold; max-width: 20px;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._dismiss)
        layout.addWidget(close_btn)

        self._duration_ms = duration_ms

    def showEvent(self, event):
        super().showEvent(event)
        if self._duration_ms > 0:
            QTimer.singleShot(self._duration_ms, self._dismiss)

    def _dismiss(self):
        from esfex.visualization.theme import current_theme

        dur = current_theme().animations.duration_fast
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(dur)
        anim.setStartValue(self.height())
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.finished.connect(self._on_anim_done)
        self._dismiss_anim = anim  # prevent GC
        anim.start()

    def _on_anim_done(self):
        self.closed.emit()
        self.deleteLater()


class ToastManager(QWidget):
    """Manages a stack of toast notifications anchored to a parent widget.

    Usage::

        self.toasts = ToastManager(self)
        self.toasts.show_toast("File saved.", "success")
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.setAlignment(
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight
        )
        self.raise_()

    def show_toast(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = 4000,
    ):
        """Show a toast notification.

        Parameters
        ----------
        level : str
            ``"success"``, ``"warning"``, ``"error"``, or ``"info"``.
        """
        toast = _ToastWidget(message, level, duration_ms, parent=self)
        toast.closed.connect(lambda: self._remove(toast))
        self._layout.addWidget(toast)
        self._reposition()
        toast.show()

    def _remove(self, toast: _ToastWidget):
        self._layout.removeWidget(toast)
        self._reposition()

    def _reposition(self):
        """Keep the toast stack anchored to the bottom-right of parent."""
        p = self.parentWidget()
        if p is None:
            return
        w = 340
        h = min(self.sizeHint().height(), p.height() // 2)
        x = p.width() - w - 16
        y = p.height() - h - 16
        self.setGeometry(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition()


# ══════════════════════════════════════════════════════════════════
# Empty State Widget
# ══════════════════════════════════════════════════════════════════


class EmptyStateWidget(QWidget):
    """Placeholder shown when a list or panel has no content.

    Usage::

        empty = EmptyStateWidget(
            title="No systems yet",
            description="Click 'Add System' to create one.",
        )
        layout.addWidget(empty)
    """

    actionClicked = Signal()

    def __init__(
        self,
        title: str = "",
        description: str = "",
        action_text: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        # Icon placeholder — light circle with muted content
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setFixedSize(64, 64)
        self._icon_label.setStyleSheet("background: transparent;")
        layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel(title)
        self._title.setObjectName("emptyStateTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._desc = QLabel(description)
        self._desc.setObjectName("emptyStateDesc")
        self._desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc.setWordWrap(True)
        layout.addWidget(self._desc)

        if action_text:
            btn = QPushButton(action_text)
            btn.setObjectName("primaryButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self.actionClicked.emit)
            layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def paintEvent(self, event):
        """Draw a subtle circle icon placeholder."""
        super().paintEvent(event)
        from esfex.visualization.theme import current_theme

        c = current_theme().colors
        painter = QPainter(self._icon_label)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(c.border_medium))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = self._icon_label.rect().adjusted(8, 8, -8, -8)
        painter.drawEllipse(rect)
        # Draw a small "+" in the center
        cx, cy = rect.center().x(), rect.center().y()
        painter.drawLine(cx - 8, cy, cx + 8, cy)
        painter.drawLine(cx, cy - 8, cx, cy + 8)
        painter.end()

    def set_title(self, text: str):
        self._title.setText(text)

    def set_description(self, text: str):
        self._desc.setText(text)
