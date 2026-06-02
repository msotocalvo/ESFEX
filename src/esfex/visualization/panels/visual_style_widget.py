"""Reusable widget for editing visual style properties of map elements."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import VisualStyle
from esfex.visualization.i18n import tr


class VisualStyleWidget(QWidget):
    """A compact form for editing :class:`VisualStyle` properties.

    Parameters
    ----------
    show_color : bool
        Show a colour-picker button.
    show_size : bool
        Show a marker size spin-box (1-100 px).
    show_shape : bool
        Show a shape combo (circle / square / diamond) -- for nodes.
    show_opacity : bool
        Show an opacity spin-box (0.0-1.0) -- for zones.
    show_width : bool
        Show a line-width spin-box (1-20 px) -- for lines.
    """

    styleChanged = Signal()

    def __init__(
        self,
        *,
        show_color: bool = True,
        show_size: bool = True,
        show_shape: bool = False,
        show_opacity: bool = False,
        show_width: bool = False,
        default_color: str = "#3498db",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._updating = False
        self._default_color = default_color
        self._current_color = default_color

        group = QGroupBox(tr("visual_style.group_appearance"))
        form = QFormLayout(group)

        # -- Color button --
        self._color_btn: QPushButton | None = None
        if show_color:
            self._color_btn = QPushButton()
            self._color_btn.setFixedHeight(24)
            self._color_btn.clicked.connect(self._pick_color)
            self._apply_color_swatch(self._current_color)
            form.addRow(tr("visual_style.color"), self._color_btn)

        # -- Size spin --
        self._size_spin: QSpinBox | None = None
        if show_size:
            self._size_spin = QSpinBox()
            self._size_spin.setRange(0, 100)
            self._size_spin.setValue(0)
            self._size_spin.setSpecialValueText("Auto")
            self._size_spin.setToolTip(tr("visual_style.size_auto_tip"))
            self._size_spin.valueChanged.connect(self._on_changed)
            form.addRow(tr("visual_style.size"), self._size_spin)

        # -- Shape combo --
        self._shape_combo: QComboBox | None = None
        if show_shape:
            self._shape_combo = QComboBox()
            self._shape_combo.addItems([
                "circle", "square", "diamond",
                "triangle-up", "triangle-down",
                "hexagon", "pentagon",
                "horizontal-bar", "star",
            ])
            self._shape_combo.currentTextChanged.connect(self._on_changed)
            form.addRow(tr("visual_style.shape"), self._shape_combo)

        # -- Opacity spin --
        self._opacity_spin: QDoubleSpinBox | None = None
        if show_opacity:
            self._opacity_spin = QDoubleSpinBox()
            self._opacity_spin.setRange(0.0, 1.0)
            self._opacity_spin.setDecimals(2)
            self._opacity_spin.setSingleStep(0.05)
            self._opacity_spin.setValue(0.15)
            self._opacity_spin.valueChanged.connect(self._on_changed)
            form.addRow(tr("visual_style.opacity"), self._opacity_spin)

        # -- Width spin --
        self._width_spin: QSpinBox | None = None
        if show_width:
            self._width_spin = QSpinBox()
            self._width_spin.setRange(0, 20)
            self._width_spin.setValue(0)
            self._width_spin.setSpecialValueText("Auto")
            self._width_spin.setToolTip(tr("visual_style.width_auto_tip"))
            self._width_spin.valueChanged.connect(self._on_changed)
            form.addRow(tr("visual_style.width"), self._width_spin)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_default_color(self, color: str) -> None:
        """Update the default color for this widget.

        This should be called before load_style() to set the appropriate
        default for the element type being edited.
        """
        self._default_color = color

    def load_style(self, style: VisualStyle) -> None:
        """Populate the widgets from a :class:`VisualStyle` dataclass."""
        self._updating = True

        if self._color_btn is not None:
            # Use style.color if set, otherwise fall back to default
            color_to_show = style.color if style.color else self._default_color
            self._current_color = color_to_show
            self._apply_color_swatch(color_to_show)

        if self._size_spin is not None:
            self._size_spin.setValue(int(style.size) if style.size else 0)

        if self._shape_combo is not None and style.icon_shape:
            idx = self._shape_combo.findText(style.icon_shape)
            if idx >= 0:
                self._shape_combo.setCurrentIndex(idx)

        if self._opacity_spin is not None and style.opacity is not None:
            self._opacity_spin.setValue(style.opacity)

        if self._width_spin is not None:
            self._width_spin.setValue(int(style.width) if style.width else 0)

        self._updating = False

    def get_style(self) -> VisualStyle:
        """Return a :class:`VisualStyle` built from the current widget values.

        A size/width of 0 means "auto" — returns None so _effective_style
        falls through to the global-scaling auto-computed value.
        """
        size_val = self._size_spin.value() if self._size_spin is not None else 0
        return VisualStyle(
            color=self._current_color if self._color_btn is not None else None,
            size=float(size_val) if size_val > 0 else None,
            icon_shape=(
                self._shape_combo.currentText()
                if self._shape_combo is not None
                else None
            ),
            opacity=(
                self._opacity_spin.value()
                if self._opacity_spin is not None
                else None
            ),
            width=(
                float(self._width_spin.value())
                if self._width_spin is not None and self._width_spin.value() > 0
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._current_color), self, tr("visual_style.choose_color")
        )
        if color.isValid():
            self._current_color = color.name()
            self._apply_color_swatch(self._current_color)
            self._on_changed()

    def _on_changed(self) -> None:
        if not self._updating:
            self.styleChanged.emit()

    def _apply_color_swatch(self, hex_color: str) -> None:
        if self._color_btn is not None:
            self._color_btn.setStyleSheet(
                "QPushButton {"
                f" background-color: {hex_color}; border: 1px solid #888;"
                " border-radius: 2px; min-width: 25px; max-width: 25px; }"
            )
            self._color_btn.setText("")
            self._color_btn.setToolTip(hex_color)
