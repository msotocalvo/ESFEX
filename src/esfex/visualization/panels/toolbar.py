"""Editor toolbar with drawing tools and layer switching."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QPainter

from esfex.visualization.i18n import tr

_ICONS_DIR = Path(__file__).resolve().parents[2] / "icons"

# Cache: (svg_name, hex_color) → QIcon
_icon_cache: dict[tuple[str, str], QIcon] = {}


def _icon_color_hex() -> str:
    """Return the hex color for toolbar icons from the active theme."""
    from esfex.visualization.theme import current_theme
    c = current_theme().colors
    return c.toolbar_icon or c.text_primary


def _svg_icon(png_name: str, color_hex: str) -> QIcon:
    """Load the SVG version of an icon, recolored to *color_hex*.

    The SVGs use ``fill="#000000"``; we replace that with the theme
    color before rendering to a crisp 64×64 pixmap.
    """
    svg_name = png_name.replace(".png", ".svg")
    svg_path = _ICONS_DIR / svg_name
    svg_data = svg_path.read_bytes()
    colored = svg_data.replace(b'fill="#000000"', f'fill="{color_hex}"'.encode())

    renderer = QSvgRenderer(colored)
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


def _icon(name: str) -> QIcon:
    color = _icon_color_hex()
    key = (name, color)
    if key in _icon_cache:
        return _icon_cache[key]
    icon = _svg_icon(name, color)
    _icon_cache[key] = icon
    return icon


class EditorToolbar(QToolBar):
    """Main toolbar for the grid editor."""

    modeChanged = Signal(str)
    layerChanged = Signal(str)      # electrical, primary_energy, all
    baseMapChanged = Signal(str)    # OpenStreetMap, Satellite, Terrain, Dark
    addSystemRequested = Signal()
    validateRequested = Signal()
    runRequested = Signal()
    sensitivityRequested = Signal()
    resultsRequested = Signal()
    riskRequested = Signal()

    def __init__(self, parent=None):
        super().__init__("Editor Tools", parent)
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        # Responsive sizing. The bar is COMPACT at its minimum (so it fits the
        # default window width) and grows to FILL wider windows. The button/
        # label TEXT scales WITH the icon — its font-size is tied to the icon
        # size in the stylesheet (the theme QSS, not the widget font, drives
        # toolbutton text), so the whole bar expands as one unit.
        # ``_fit_icon_to_width`` (from resizeEvent) picks the largest icon whose
        # laid-out width still fits the current bar, between these bounds.
        from esfex.visualization.ui_scale import scaled
        self._min_icon = min(20, scaled(20))    # compact floor (fits 1400 px)
        self._max_icon = 28                      # gentle cap (avoid oversized chrome)
        self._cur_icon = None
        self._apply_icon_scale(self._min_icon)

        # Drawing mode actions (mutually exclusive)
        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)

        # ── Select (default pointer, always first) ──
        self._act_select = QAction(_icon("selection.png"), tr("toolbar.select"), self)
        self._act_select.setCheckable(True)
        self._act_select.setChecked(True)
        self._act_select.setToolTip(tr("toolbar_tips.select"))
        self._act_select.triggered.connect(lambda: self.modeChanged.emit("select"))
        self._mode_group.addAction(self._act_select)
        self.addAction(self._act_select)

        self.addSeparator()

        # ── Add System ──
        self._act_add_system = QAction(_icon("system.png"), tr("toolbar.add_system"), self)
        self._act_add_system.setToolTip(tr("toolbar_tips.add_system"))
        self._act_add_system.triggered.connect(self.addSystemRequested.emit)
        self.addAction(self._act_add_system)

        self.addSeparator()

        # ── Mode actions ──

        self._act_add_line = QAction(_icon("power_line.png"), tr("toolbar.line"), self)
        self._act_add_line.setCheckable(True)
        self._act_add_line.setToolTip(tr("toolbar_tips.line"))
        self._act_add_line.triggered.connect(lambda: self.modeChanged.emit("add_line"))
        self._mode_group.addAction(self._act_add_line)
        self.addAction(self._act_add_line)

        self._act_add_generator = QAction(_icon("generator.png"), tr("toolbar.generator"), self)
        self._act_add_generator.setCheckable(True)
        self._act_add_generator.setToolTip(tr("toolbar_tips.generator"))
        self._act_add_generator.triggered.connect(
            lambda: self.modeChanged.emit("add_generator")
        )
        self._mode_group.addAction(self._act_add_generator)
        self.addAction(self._act_add_generator)

        self._act_add_battery = QAction(_icon("battery.png"), tr("toolbar.storage"), self)
        self._act_add_battery.setCheckable(True)
        self._act_add_battery.setToolTip(tr("toolbar_tips.storage"))
        self._act_add_battery.triggered.connect(
            lambda: self.modeChanged.emit("add_battery")
        )
        self._mode_group.addAction(self._act_add_battery)
        self.addAction(self._act_add_battery)

        self._act_add_transformer = QAction(_icon("trafo.png"), tr("toolbar.transformer"), self)
        self._act_add_transformer.setCheckable(True)
        self._act_add_transformer.setToolTip(tr("toolbar_tips.transformer"))
        self._act_add_transformer.triggered.connect(
            lambda: self.modeChanged.emit("add_transformer")
        )
        self._mode_group.addAction(self._act_add_transformer)
        self.addAction(self._act_add_transformer)

        self._act_add_bus = QAction(_icon("busbar.png"), tr("toolbar.bus"), self)
        self._act_add_bus.setCheckable(True)
        self._act_add_bus.setToolTip(tr("toolbar_tips.bus"))
        self._act_add_bus.triggered.connect(
            lambda: self.modeChanged.emit("add_bus")
        )
        self._mode_group.addAction(self._act_add_bus)
        self.addAction(self._act_add_bus)

        self._act_draw_zone = QAction(_icon("development.png"), tr("toolbar.dev_zone"), self)
        self._act_draw_zone.setCheckable(True)
        self._act_draw_zone.setToolTip(tr("toolbar_tips.dev_zone"))
        self._act_draw_zone.triggered.connect(lambda: self.modeChanged.emit("draw_zone"))
        self._mode_group.addAction(self._act_draw_zone)
        self.addAction(self._act_draw_zone)

        self._act_add_acdc_converter = QAction(
            _icon("converter.png"), tr("toolbar.acdc_conv"), self,
        )
        self._act_add_acdc_converter.setCheckable(True)
        self._act_add_acdc_converter.setToolTip(tr("toolbar_tips.acdc_conv"))
        self._act_add_acdc_converter.triggered.connect(
            lambda: self.modeChanged.emit("add_acdc_converter")
        )
        self._mode_group.addAction(self._act_add_acdc_converter)
        self.addAction(self._act_add_acdc_converter)

        self._act_add_freq_converter = QAction(
            _icon("frequency.png"), tr("toolbar.freq_conv"), self,
        )
        self._act_add_freq_converter.setCheckable(True)
        self._act_add_freq_converter.setToolTip(tr("toolbar_tips.freq_conv"))
        self._act_add_freq_converter.triggered.connect(
            lambda: self.modeChanged.emit("add_freq_converter")
        )
        self._mode_group.addAction(self._act_add_freq_converter)
        self.addAction(self._act_add_freq_converter)

        self._act_add_electrolyzer = QAction(
            _icon("electrolizer.png"), tr("toolbar.electrolyzer"), self,
        )
        self._act_add_electrolyzer.setCheckable(True)
        self._act_add_electrolyzer.setToolTip(tr("toolbar_tips.electrolyzer"))
        self._act_add_electrolyzer.triggered.connect(
            lambda: self.modeChanged.emit("add_electrolyzer")
        )
        self._mode_group.addAction(self._act_add_electrolyzer)
        self.addAction(self._act_add_electrolyzer)

        self._act_add_fuel_entry = QAction(_icon("fuel_entry.png"), tr("toolbar.fuel_entry"), self)
        self._act_add_fuel_entry.setCheckable(True)
        self._act_add_fuel_entry.setToolTip(tr("toolbar_tips.fuel_entry"))
        self._act_add_fuel_entry.triggered.connect(
            lambda: self.modeChanged.emit("add_fuel_entry")
        )
        self._mode_group.addAction(self._act_add_fuel_entry)
        self.addAction(self._act_add_fuel_entry)

        self._act_add_fuel_storage = QAction(
            _icon("fuel_storage.png"), tr("toolbar.fuel_storage"), self,
        )
        self._act_add_fuel_storage.setCheckable(True)
        self._act_add_fuel_storage.setToolTip(tr("toolbar_tips.fuel_storage"))
        self._act_add_fuel_storage.triggered.connect(
            lambda: self.modeChanged.emit("add_fuel_storage")
        )
        self._mode_group.addAction(self._act_add_fuel_storage)
        self.addAction(self._act_add_fuel_storage)

        self._act_add_fuel_route = QAction(
            _icon("fuel_transport.png"), tr("toolbar.fuel_route"), self,
        )
        self._act_add_fuel_route.setCheckable(True)
        self._act_add_fuel_route.setToolTip(tr("toolbar_tips.fuel_route"))
        self._act_add_fuel_route.triggered.connect(
            lambda: self.modeChanged.emit("add_fuel_route")
        )
        self._mode_group.addAction(self._act_add_fuel_route)
        self.addAction(self._act_add_fuel_route)

        # Action → icon filename (for theme refresh)
        self._icon_actions: list[tuple[QAction, str]] = [
            (self._act_select, "selection.png"),
            (self._act_add_system, "system.png"),
            (self._act_add_line, "power_line.png"),
            (self._act_add_generator, "generator.png"),
            (self._act_add_battery, "battery.png"),
            (self._act_add_transformer, "trafo.png"),
            (self._act_add_bus, "busbar.png"),
            (self._act_draw_zone, "development.png"),
            (self._act_add_acdc_converter, "converter.png"),
            (self._act_add_freq_converter, "frequency.png"),
            (self._act_add_electrolyzer, "electrolizer.png"),
            (self._act_add_fuel_entry, "fuel_entry.png"),
            (self._act_add_fuel_storage, "fuel_storage.png"),
            (self._act_add_fuel_route, "fuel_transport.png"),
        ]

        # All map-element actions (disabled until a system exists)
        self._map_actions: list[QAction] = [
            self._act_add_line,
            self._act_add_generator,
            self._act_add_battery,
            self._act_add_transformer,
            self._act_add_bus,
            self._act_draw_zone,
            self._act_add_acdc_converter,
            self._act_add_freq_converter,
            self._act_add_electrolyzer,
            self._act_add_fuel_entry,
            self._act_add_fuel_storage,
            self._act_add_fuel_route,
        ]
        for act in self._map_actions:
            act.setEnabled(False)

        self.addSeparator()

        self._act_validate = QAction(_icon("validate.png"), tr("toolbar.validate"), self)
        self._act_validate.setToolTip(tr("toolbar_tips.validate"))
        self._act_validate.triggered.connect(self.validateRequested.emit)
        self.addAction(self._act_validate)

        self._act_run = QAction(_icon("run.png"), tr("toolbar.run"), self)
        self._act_run.setToolTip(tr("toolbar_tips.run"))
        self._act_run.setEnabled(False)
        self._act_run.triggered.connect(self.runRequested.emit)
        self.addAction(self._act_run)

        self.addSeparator()

        self._act_sensitivity = QAction(_icon("sensibility.png"), tr("toolbar.sensitivity"), self)
        self._act_sensitivity.setToolTip(tr("toolbar_tips.sensitivity"))
        self._act_sensitivity.setEnabled(False)
        self._act_sensitivity.triggered.connect(self.sensitivityRequested.emit)
        self.addAction(self._act_sensitivity)

        self._act_results = QAction(_icon("results.png"), tr("toolbar.results"), self)
        self._act_results.setToolTip(tr("toolbar_tips.results"))
        self._act_results.setEnabled(False)
        self._act_results.triggered.connect(self.resultsRequested.emit)
        self.addAction(self._act_results)

        self._act_risk = QAction(_icon("risk.png"), tr("toolbar.risk"), self)
        self._act_risk.setToolTip(tr("toolbar_tips.risk"))
        self._act_risk.triggered.connect(self.riskRequested.emit)
        self.addAction(self._act_risk)

        self.addSeparator()

        # Layer selector with its caption above the combo.
        self._layer_label = QLabel(tr("toolbar.layer"))
        self._layer_combo = QComboBox()
        self._layer_combo.setToolTip(tr("toolbar.layer"))
        self._layer_combo.addItems([
            tr("layers.all"),
            tr("layers.electrical"),
            tr("layers.primary_energy"),
            tr("layers.results"),
        ])
        self._layer_combo.currentTextChanged.connect(self._on_layer_changed)
        self.addWidget(self._captioned(self._layer_label, self._layer_combo))

        # Base map selector with its caption above the combo.
        self._basemap_label = QLabel(tr("toolbar.base_map"))
        self._basemap_combo = QComboBox()
        self._basemap_combo.setToolTip(tr("toolbar.base_map"))
        for key, label_key in self._basemap_items():
            self._basemap_combo.addItem(tr(label_key), key)
        self._basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        self.addWidget(self._captioned(self._basemap_label, self._basemap_combo))

        # Add analysis actions to icon registry
        self._icon_actions.extend([
            (self._act_validate, "validate.png"),
            (self._act_run, "run.png"),
            (self._act_sensitivity, "sensibility.png"),
            (self._act_results, "results.png"),
            (self._act_risk, "risk.png"),
        ])

    @staticmethod
    def _captioned(label: QLabel, combo: QComboBox) -> QWidget:
        """Stack a caption label above its combo in a compact container."""
        label.setStyleSheet("font-size: 9px; padding: 0; margin: 0;")
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 0, 4, 0)
        v.setSpacing(0)
        v.addWidget(label)
        v.addWidget(combo)
        return w

    def _apply_icon_scale(self, icon: int) -> None:
        """Set the icon size and tie the button/combo font + overflow button to
        it, so the whole bar scales as one. No-op if the size is unchanged."""
        icon = int(icon)
        if icon == self._cur_icon:
            return
        self._cur_icon = icon
        fpx = max(7, round(icon * 0.45))
        self.setIconSize(QSize(icon, icon))
        ext, eic = icon + 8, icon
        self.setStyleSheet(f"""
            QToolBar QToolButton {{ font-size: {fpx}px; padding: 2px 4px; }}
            QToolBar QComboBox {{ font-size: {fpx}px; padding: 1px 3px; }}
            QToolBarExtension {{
                background-color: #2980b9;
                border-radius: 4px;
                margin: 4px 2px;
                padding: 4px 8px;
                min-width: {ext}px;
                min-height: {ext}px;
            }}
            QToolBarExtension::icon {{
                width: {eic}px;
                height: {eic}px;
            }}
            QToolBarExtension:hover {{
                background-color: #3498db;
            }}
        """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_icon_to_width()

    def _fit_icon_to_width(self) -> None:
        """Grow/shrink the icon (and, with it, the text) so the bar fills the
        available width without overflowing. ``sizeHint().width()`` reflects the
        new style synchronously, so this converges in one pass and adapts to the
        current language/theme automatically."""
        w = self.width()
        if w < 100:
            return
        lo, hi = self._min_icon, self._max_icon
        guard = 0
        # Grow to fill (leave a little breathing room so the >> never appears).
        while self._cur_icon < hi and self.sizeHint().width() < w - 30 and guard < 80:
            self._apply_icon_scale(self._cur_icon + 1)
            guard += 1
        # Shrink if we overflow.
        while self._cur_icon > lo and self.sizeHint().width() > w - 8 and guard < 160:
            self._apply_icon_scale(self._cur_icon - 1)
            guard += 1

    def refresh_icons(self):
        """Reload all toolbar icons (call after theme change)."""
        _icon_cache.clear()
        for action, icon_name in self._icon_actions:
            action.setIcon(_icon(icon_name))

    def retranslateUi(self):
        """Update all action texts and tooltips after a language change."""
        _tr_map = [
            (self._act_select, "toolbar.select", "toolbar_tips.select"),
            (self._act_add_system, "toolbar.add_system", "toolbar_tips.add_system"),
            (self._act_add_line, "toolbar.line", "toolbar_tips.line"),
            (self._act_add_generator, "toolbar.generator", "toolbar_tips.generator"),
            (self._act_add_battery, "toolbar.storage", "toolbar_tips.storage"),
            (self._act_add_transformer, "toolbar.transformer", "toolbar_tips.transformer"),
            (self._act_add_bus, "toolbar.bus", "toolbar_tips.bus"),
            (self._act_draw_zone, "toolbar.dev_zone", "toolbar_tips.dev_zone"),
            (self._act_add_acdc_converter, "toolbar.acdc_conv", "toolbar_tips.acdc_conv"),
            (self._act_add_freq_converter, "toolbar.freq_conv", "toolbar_tips.freq_conv"),
            (self._act_add_electrolyzer, "toolbar.electrolyzer", "toolbar_tips.electrolyzer"),
            (self._act_add_fuel_entry, "toolbar.fuel_entry", "toolbar_tips.fuel_entry"),
            (self._act_add_fuel_storage, "toolbar.fuel_storage", "toolbar_tips.fuel_storage"),
            (self._act_add_fuel_route, "toolbar.fuel_route", "toolbar_tips.fuel_route"),
            (self._act_validate, "toolbar.validate", "toolbar_tips.validate"),
            (self._act_run, "toolbar.run", "toolbar_tips.run"),
            (self._act_sensitivity, "toolbar.sensitivity", "toolbar_tips.sensitivity"),
            (self._act_results, "toolbar.results", "toolbar_tips.results"),
            (self._act_risk, "toolbar.risk", "toolbar_tips.risk"),
        ]
        for action, text_key, tip_key in _tr_map:
            action.setText(tr(text_key))
            action.setToolTip(tr(tip_key))

        # Captions above the combos + matching tooltips.
        self._layer_label.setText(tr("toolbar.layer"))
        self._basemap_label.setText(tr("toolbar.base_map"))
        self._layer_combo.setToolTip(tr("toolbar.layer"))
        self._basemap_combo.setToolTip(tr("toolbar.base_map"))

        # Rebuild layer combo items (preserve current index)
        idx = self._layer_combo.currentIndex()
        self._layer_combo.blockSignals(True)
        self._layer_combo.clear()
        self._layer_combo.addItems([tr(k) for k in (
            "layers.all", "layers.electrical",
            "layers.primary_energy", "layers.results",
        )])
        self._layer_combo.setCurrentIndex(idx)
        self._layer_combo.blockSignals(False)

        # Rebuild basemap combo (preserve canonical key as itemData)
        idx = self._basemap_combo.currentIndex()
        self._basemap_combo.blockSignals(True)
        self._basemap_combo.clear()
        for key, label_key in self._basemap_items():
            self._basemap_combo.addItem(tr(label_key), key)
        self._basemap_combo.setCurrentIndex(idx)
        self._basemap_combo.blockSignals(False)

    def set_map_actions_enabled(self, enabled: bool):
        """Enable or disable all map-element placement actions."""
        for act in self._map_actions:
            act.setEnabled(enabled)
        if not enabled:
            self.reset_mode()

    def set_validate_enabled(self, enabled: bool):
        """Enable or disable the Validate button."""
        self._act_validate.setEnabled(enabled)

    def set_run_enabled(self, enabled: bool):
        """Enable or disable the Run button."""
        self._act_run.setEnabled(enabled)

    def set_sensitivity_enabled(self, enabled: bool):
        """Enable or disable the Sensitivity button."""
        self._act_sensitivity.setEnabled(enabled)

    def set_results_enabled(self, enabled: bool):
        """Enable or disable the Results button."""
        self._act_results.setEnabled(enabled)

    def set_risk_enabled(self, enabled: bool):
        """Enable or disable the Risk button."""
        self._act_risk.setEnabled(enabled)

    def reset_mode(self):
        """Switch back to select mode."""
        self._act_select.setChecked(True)
        self.modeChanged.emit("select")

    def setDrawingActionsEnabled(self, enabled: bool):
        """Enable or disable all map drawing-mode actions.

        Used when switching to the SLD view (view-only, no drawing).
        """
        self.reset_mode()
        for action in self._mode_group.actions():
            if action is not self._act_select:
                action.setEnabled(enabled)

    def get_action_registry(self) -> dict[str, QAction]:
        """Return a mapping of action_id -> QAction for shortcut assignment."""
        return {
            "tool.select": self._act_select,
            "tool.add_system": self._act_add_system,
            "tool.line": self._act_add_line,
            "tool.generator": self._act_add_generator,
            "tool.battery": self._act_add_battery,
            "tool.transformer": self._act_add_transformer,
            "tool.bus": self._act_add_bus,
            "tool.dev_zone": self._act_draw_zone,
            "tool.acdc_converter": self._act_add_acdc_converter,
            "tool.freq_converter": self._act_add_freq_converter,
            "tool.electrolyzer": self._act_add_electrolyzer,
            "tool.fuel_entry": self._act_add_fuel_entry,
            "tool.fuel_storage": self._act_add_fuel_storage,
            "tool.fuel_route": self._act_add_fuel_route,
            "tool.validate": self._act_validate,
            "tool.run": self._act_run,
            "tool.sensitivity": self._act_sensitivity,
            "tool.results": self._act_results,
            "tool.risk": self._act_risk,
        }

    def _on_layer_changed(self, text: str):
        mapping = {
            "All": "all",
            "Electrical": "electrical",
            "Primary Energy": "primary_energy",
            "Results": "results",
        }
        self.layerChanged.emit(mapping.get(text, "all"))

    @staticmethod
    def _basemap_items() -> list[tuple[str, str]]:
        """(canonical key for JS setBaseMap, i18n label key) pairs."""
        return [
            ("OpenStreetMap", "basemaps.osm"),
            ("Satellite", "basemaps.satellite"),
            ("Terrain", "basemaps.terrain"),
            ("Dark", "basemaps.dark"),
            ("Offline", "basemaps.offline"),
        ]

    def _on_basemap_changed(self, index: int):
        key = self._basemap_combo.itemData(index)
        if key:
            self.baseMapChanged.emit(key)
