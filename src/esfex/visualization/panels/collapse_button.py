"""Collapse/expand arrow buttons for QSplitter handles.

A pill-shaped bump sits at the centre of the thin splitter handle.
The button is parented to a *top-level* widget (e.g. MainWindow) so
it is neither clipped by the narrow handle nor treated as a splitter
panel.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPolygon
from PySide6.QtWidgets import QAbstractButton, QSplitter, QWidget


class CollapseButton(QAbstractButton):
    """A pill-shaped bump with a small arrow, floating over a splitter
    handle but parented to an ancestor widget to avoid clipping.
    """

    _ARROW_HALF = 5
    _H_SIZE = (14, 81)       # (w, h) for horizontal splitters
    _V_SIZE = (81, 14)       # (w, h) for vertical splitters
    _RADIUS = 10
    _BG_NORMAL = QColor(200, 200, 200, 160)
    _BG_HOVER = QColor(170, 170, 170, 210)
    _ARROW_NORMAL = QColor(100, 100, 100)
    _ARROW_HOVER = QColor(60, 60, 60)

    def __init__(self, overlay_parent: QWidget, splitter: QSplitter,
                 handle_index: int, panel_index: int, collapse_dir: str):
        super().__init__(overlay_parent)
        self._splitter = splitter
        self._handle_index = handle_index
        self._panel_index = panel_index
        self._collapse_dir = collapse_dir
        self._collapsed = False
        self._saved_size = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        if splitter.orientation() == Qt.Orientation.Horizontal:
            self.setFixedSize(*self._H_SIZE)
        else:
            self.setFixedSize(*self._V_SIZE)

        self.clicked.connect(self._toggle)
        self.raise_()

    # ------------------------------------------------------------------

    def _toggle(self):
        sizes = self._splitter.sizes()
        if self._collapsed:
            sizes[self._panel_index] = self._saved_size or 250
            self._splitter.setSizes(sizes)
            self._collapsed = False
        else:
            self._saved_size = sizes[self._panel_index]
            sizes[self._panel_index] = 0
            self._splitter.setSizes(sizes)
            self._collapsed = True
        self.update()

    def reposition(self):
        """Move so the button is just outside the panel edge."""
        handle = self._splitter.handle(self._handle_index)
        if handle is None or not handle.isVisible():
            return
        parent = self.parentWidget()
        is_h = self._splitter.orientation() == Qt.Orientation.Horizontal

        if is_h:
            cy = handle.mapTo(parent, QPoint(0, handle.height() // 2)).y()
            if self._collapse_dir == "left":
                # Pill sits just to the right of the left panel's edge
                edge_x = handle.mapTo(parent, QPoint(0, 0)).x()
                self.move(edge_x, cy - self.height() // 2)
            else:
                # Pill sits just to the left of the right panel's edge
                edge_x = handle.mapTo(parent, QPoint(handle.width(), 0)).x()
                self.move(edge_x - self.width(), cy - self.height() // 2)
        else:
            cx = handle.mapTo(parent, QPoint(handle.width() // 2, 0)).x()
            if self._collapse_dir == "up":
                edge_y = handle.mapTo(parent, QPoint(0, 0)).y()
                self.move(cx - self.width() // 2, edge_y)
            else:
                # Pill sits just above the bottom panel's edge
                edge_y = handle.mapTo(parent, QPoint(0, handle.height())).y()
                self.move(cx - self.width() // 2, edge_y - self.height())

        self.raise_()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        hover = self.underMouse()
        w, h = self.width(), self.height()

        bg = self._BG_HOVER if hover else self._BG_NORMAL
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w), float(h),
                            self._RADIUS, self._RADIUS)
        painter.drawPath(path)

        arrow_color = self._ARROW_HOVER if hover else self._ARROW_NORMAL
        painter.setBrush(arrow_color)

        cx = w // 2
        cy = h // 2
        s = self._ARROW_HALF

        d = self._collapse_dir
        if self._collapsed:
            d = {"left": "right", "right": "left",
                 "up": "down", "down": "up"}[d]

        if d == "left":
            pts = [QPoint(cx - s, cy), QPoint(cx + s, cy - s), QPoint(cx + s, cy + s)]
        elif d == "right":
            pts = [QPoint(cx + s, cy), QPoint(cx - s, cy - s), QPoint(cx - s, cy + s)]
        elif d == "up":
            pts = [QPoint(cx, cy - s), QPoint(cx - s, cy + s), QPoint(cx + s, cy + s)]
        else:
            pts = [QPoint(cx, cy + s), QPoint(cx - s, cy - s), QPoint(cx + s, cy - s)]

        painter.drawPolygon(QPolygon(pts))
        painter.end()

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def enterEvent(self, _event):
        self.update()

    def leaveEvent(self, _event):
        self.update()


# ------------------------------------------------------------------
# Event watcher
# ------------------------------------------------------------------

class _Repositioner(QObject):
    """Watches for resize / move events and repositions all registered
    collapse buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list[CollapseButton] = []

    def add(self, btn: CollapseButton):
        self._buttons.append(btn)

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype in (QEvent.Type.Resize, QEvent.Type.Move,
                     QEvent.Type.LayoutRequest, QEvent.Type.Show):
            for btn in self._buttons:
                btn.reposition()
        return False


def add_collapse_button(
    overlay_parent: QWidget,
    splitter: QSplitter,
    handle_index: int,
    panel_index: int,
    collapse_dir: str,
) -> CollapseButton:
    """Attach a :class:`CollapseButton` centred over a splitter handle.

    Parameters
    ----------
    overlay_parent : QWidget
        A top-level widget (e.g. MainWindow) used as the button's parent
        so it is not clipped by the handle.
    splitter : QSplitter
        The splitter that owns the handle.
    handle_index : int
        1-based handle index.
    panel_index : int
        Index of the widget to collapse/expand.
    collapse_dir : str
        ``"left"`` / ``"right"`` / ``"up"`` / ``"down"``.
    """
    btn = CollapseButton(overlay_parent, splitter, handle_index,
                         panel_index, collapse_dir)

    # Shared repositioner per overlay parent
    repo_attr = "_collapse_repositioner"
    repo: _Repositioner | None = getattr(overlay_parent, repo_attr, None)
    if repo is None:
        repo = _Repositioner(parent=overlay_parent)
        setattr(overlay_parent, repo_attr, repo)
        overlay_parent.installEventFilter(repo)
    repo.add(btn)

    # Also watch the splitter and its handles for drag / resize
    splitter.installEventFilter(repo)
    for i in range(1, splitter.count()):
        h = splitter.handle(i)
        if h:
            h.installEventFilter(repo)

    btn.reposition()
    return btn
