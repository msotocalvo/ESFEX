"""Horizontal header view that word-wraps column labels."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader

_PAD = 4


class WordWrapHeaderView(QHeaderView):
    """A :class:`QHeaderView` that word-wraps column labels to fit the
    available column width, growing the header height as needed.
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

    # ------------------------------------------------------------------
    # Size computation
    # ------------------------------------------------------------------

    def sectionSizeFromContents(self, logicalIndex: int) -> QSize:
        base = super().sectionSizeFromContents(logicalIndex)
        model = self.model()
        if model is None:
            return base
        text = model.headerData(
            logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole
        )
        if not text:
            return base
        width = self.sectionSize(logicalIndex)
        if width <= 0:
            width = self.defaultSectionSize() or 80
        fm = self.fontMetrics()
        text_rect = fm.boundingRect(
            QRect(0, 0, width - 2 * _PAD, 10000),
            int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignCenter),
            str(text),
        )
        needed = text_rect.height() + 2 * _PAD
        return QSize(base.width(), max(base.height(), needed))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintSection(self, painter, rect, logicalIndex):  # noqa: N802
        if not rect.isValid():
            return
        painter.save()

        # Build style option for this section
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex

        # Section position (Beginning / Middle / End / Only)
        visual = self.visualIndex(logicalIndex)
        n = self.count()
        if n == 1:
            opt.position = QStyleOptionHeader.SectionPosition.OnlyOneSection
        elif visual == 0:
            opt.position = QStyleOptionHeader.SectionPosition.Beginning
        elif visual == n - 1:
            opt.position = QStyleOptionHeader.SectionPosition.End
        else:
            opt.position = QStyleOptionHeader.SectionPosition.Middle

        # State flags
        state = QStyle.StateFlag.State_None
        if self.isEnabled():
            state |= QStyle.StateFlag.State_Enabled
        if self.window().isActiveWindow():
            state |= QStyle.StateFlag.State_Active
        opt.state = state

        # Draw background only (blank the text so the style doesn't paint it)
        model = self.model()
        text = ""
        if model is not None:
            raw = model.headerData(
                logicalIndex, self.orientation(), Qt.ItemDataRole.DisplayRole
            )
            text = str(raw) if raw else ""
        opt.text = ""
        self.style().drawControl(
            QStyle.ControlElement.CE_Header, opt, painter, self
        )

        # Draw text ourselves with word-wrap
        if text:
            text_rect = rect.adjusted(_PAD, _PAD, -_PAD, -_PAD)
            painter.drawText(
                text_rect,
                int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignCenter),
                text,
            )

        painter.restore()
