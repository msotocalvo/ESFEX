"""Main window for the ESFEX Studio."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.data.gui_model import (
    RENEWABLE_FUELS,
    EndpointRef,
    GeoPoint,
    GuiGlobalSettings,
    GuiModel,
    GuiSystemState,
    VisualStyle,
)
from esfex.visualization.data.serializer import (
    config_to_global_settings,
    config_to_gui_states,
    config_to_inter_system_links,
    config_to_stochastic_scenarios,
    gui_state_to_yaml,
    inter_system_links_to_config_dict,
)
from esfex.visualization.map_widget import MapWidget
from esfex.visualization.sld_widget import SldWidget
from esfex.visualization.panels.element_tree import ElementTreePanel
from esfex.visualization.panels.battery_form import BatteryForm
from esfex.visualization.panels.fuel_entry_form import FuelEntryForm
from esfex.visualization.panels.generator_form import GeneratorForm
from esfex.visualization.panels.line_form import LineForm
from esfex.visualization.panels.node_form import NodeForm
from esfex.visualization.panels.properties import PropertiesPanel
from esfex.visualization.panels.toolbar import EditorToolbar
from esfex.visualization.panels.fuel_route_form import FuelRouteForm
from esfex.visualization.panels.fuel_storage_form import FuelStorageForm
from esfex.visualization.panels.fuel_source_form import FuelSourceForm
from esfex.visualization.panels.transformer_form import TransformerForm
from esfex.visualization.panels.zone_form import ZoneForm
from esfex.visualization.panels.fuel_form import FuelForm
from esfex.visualization.panels.system_form import SystemForm
from esfex.visualization.panels.global_settings_form import GlobalSettingsForm
from esfex.visualization.panels.electrolyzer_form import ElectrolyzerForm
from esfex.visualization.panels.ev_form import EVForm
from esfex.visualization.panels.rooftop_solar_form import RooftopSolarForm
from esfex.visualization.panels.python_console import PythonConsole
from esfex.visualization.panels.script_editor import ScriptEditor
from esfex.visualization.panels.stochastic_form import StochasticForm
from esfex.visualization.panels.acdc_converter_form import ACDCConverterForm
from esfex.visualization.panels.freq_converter_form import FreqConverterForm
from esfex.visualization.panels.bus_form import BusForm
from esfex.visualization.panels.investment_form import InvestmentForm
from esfex.visualization.panels.technology_form import TechnologyForm
from esfex.visualization.panels.results_dialog import ResultsDialog
from esfex.visualization.panels.results_panel import ResultsPanel

from esfex.visualization.theme import current_theme, get_zone_colors
from esfex.visualization.i18n import tr, language_changed, set_language


logger = logging.getLogger(__name__)

_SUPPORTED_GEO_EXTENSIONS = {".geojson", ".json", ".shp", ".kml", ".kmz", ".gpkg"}


@dataclass
class _GeoAssetInfo:
    """Metadata for an imported geo asset."""
    name: str
    geojson_data: dict
    file_path: str
    target_system: str = ""


def _read_geo_file(path: str) -> tuple[str, dict]:
    """Read any supported geo file, return (geojson_str, geojson_dict)."""
    ext = Path(path).suffix.lower()
    if ext in (".geojson", ".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data), data
    # All other formats require geopandas
    import geopandas as gpd  # noqa: F811
    if ext == ".kmz":
        import zipfile
        with zipfile.ZipFile(path) as z:
            kml_names = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ archive contains no .kml file")
            with z.open(kml_names[0]) as kml_file:
                gdf = gpd.read_file(kml_file, driver="KML")
    elif ext == ".kml":
        gdf = gpd.read_file(path, driver="KML")
    else:  # .shp, .gpkg
        gdf = gpd.read_file(path)
    geojson_str = gdf.to_json()
    return geojson_str, json.loads(geojson_str)


class _BusyOverlay(QWidget):
    """Semi-transparent overlay with a progress bar for long operations."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: rgba(0, 0, 0, 120);")

        container = QWidget(self)
        container.setFixedSize(320, 100)
        container.setStyleSheet(
            "background: #ffffff; border-radius: 8px;"
        )

        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(24, 18, 24, 18)
        vbox.setSpacing(10)

        self._label = QLabel("Processing...")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #2C3E50; font-size: 13px; background: transparent;")
        vbox.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setStyleSheet("""
            QProgressBar {
                background: #E8ECEF;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: #2980B9;
                border-radius: 4px;
            }
        """)
        vbox.addWidget(self._progress)

        self._container = container
        self.hide()

    def start(self, message: str = "Processing...", total: int = 100):
        self._restyle()
        self._label.setText(message)
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self.resize(self.parent().size())
        self._center_container()
        self.raise_()
        self.show()
        QApplication.processEvents()

    def _restyle(self):
        """Apply theme colours if a theme is active."""
        try:
            c = current_theme().colors
            self._container.setStyleSheet(
                f"background: {c.surface_primary}; border-radius: 8px;"
            )
            self._label.setStyleSheet(
                f"color: {c.text_primary}; font-size: 13px; background: transparent;"
            )
            self._progress.setStyleSheet(f"""
                QProgressBar {{
                    background: {c.surface_secondary};
                    border: none; border-radius: 4px;
                }}
                QProgressBar::chunk {{
                    background: {c.accent_primary};
                    border-radius: 4px;
                }}
            """)
        except Exception:
            pass

    def update_progress(self, value: int, message: str | None = None):
        self._progress.setValue(value)
        if message is not None:
            self._label.setText(message)
        QApplication.processEvents()

    def finish(self):
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_container()

    def _center_container(self):
        c = self._container
        x = (self.width() - c.width()) // 2
        y = (self.height() - c.height()) // 2
        c.move(x, y)


class MainWindow(QMainWindow):
    """Three-pane editor: element tree | map | properties."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("app.title"))
        self.resize(1400, 850)
        # Window icon — redundant with QApplication.windowIcon() but some
        # Linux compositors ignore the app-level icon and only honour the
        # per-window one.
        from pathlib import Path
        from PySide6.QtGui import QIcon
        _icon_path = Path(__file__).resolve().parents[1] / "icons" / "icon.svg"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        # Data model
        self.model = GuiModel(self)
        self._loaded_config = None  # ESFEXConfig (set externally)
        self._raw_config_dict: dict | None = None  # raw YAML dict for GUI-only keys
        self._result_config = None  # set on export/save
        self._config_path: Optional[str] = None
        self._last_output_dir: str = ""
        self._all_states: dict[str, object] = {}  # system_name -> GuiSystemState
        self._current_system_name: str = ""
        self._suppress_fit_bounds: bool = False
        self._geo_assets: dict[str, _GeoAssetInfo] = {}
        self._next_geo_asset_id: int = 0
        self._validated_ok: bool = False
        self._run_completed: bool = False  # True after successful run, cleared on changes
        self._clipboard: dict | None = None  # {"type": str, "attrs": dict}
        self._vs_cache = None  # cached visual_scaling, refreshed in _on_state_loaded

        from esfex.visualization.panels.collapse_button import add_collapse_button

        # Main horizontal splitter: tree | center | properties
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(self._main_splitter)
        self._busy_overlay = _BusyOverlay(self)

        # Left: element tree
        self.element_tree = ElementTreePanel()
        self._main_splitter.addWidget(self.element_tree)

        # Center: vertical splitter (views on top, console on bottom)
        self._center_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.addWidget(self._center_splitter)

        # Tab-switching container: Geographic View | Single-Line Diagram
        view_container = self._setup_center_views()
        self._center_splitter.addWidget(view_container)

        # Bottom: horizontal splitter (console left | script editor right)
        bottom_hsplitter = QSplitter(Qt.Orientation.Horizontal)
        self._center_splitter.addWidget(bottom_hsplitter)

        import numpy as _np
        from esfex.visualization.scripting_api import esfex as _esfex
        self.console = PythonConsole(namespace={
            "model": self.model,
            "state": self.model.state,
            "config": None,
            "window": self,
            "esfex": _esfex,
            "np": _np,
        })

        # Subprocess output: xterm.js terminal. Coexists with the
        # Python REPL in a tab widget; "Run Output" focuses
        # automatically when a simulation starts (see _on_run_requested).
        # Bundled in resources/ since 2026-05-20 to replace the old
        # QPlainTextEdit-based run_subprocess path that suffered from
        # ANSI strip + pipe saturation + non-TTY interactions with rich.
        from esfex.visualization.run_output_view import RunOutputView
        from PySide6.QtWidgets import QTabWidget
        self.run_output = RunOutputView()

        self._console_tabs = QTabWidget()
        self._console_tabs.addTab(self.run_output, "Run Output")
        self._console_tabs.addTab(self.console, "Python REPL")
        bottom_hsplitter.addWidget(self._console_tabs)

        # Slightly taller tab bar so the corner Stop button sits
        # comfortably at the same baseline as the tab labels without
        # clipping. Default tab bar height (~22-26px depending on
        # theme) is too tight for a button with a glyph + word.
        self._console_tabs.tabBar().setMinimumHeight(30)

        # Stop bar lives in the QTabWidget's top-right corner (next to
        # the tab labels), not inside the Run Output panel itself.
        # That keeps the terminal viewport full-height and the cancel
        # control consistently reachable regardless of which tab is
        # showing. It's hidden until a run starts and hidden again
        # when it ends, so the corner is empty during idle.
        self._run_stop_bar = QWidget()
        self._run_stop_bar.setObjectName("runStopBar")
        _sb_layout = QHBoxLayout(self._run_stop_bar)
        # Tight margins/spacing so the bar fits the tab-bar baseline.
        _sb_layout.setContentsMargins(4, 0, 6, 0)
        _sb_layout.setSpacing(8)
        self._run_status_label = QLabel("")
        self._run_status_label.setObjectName("runStatusLabel")
        _sb_layout.addWidget(self._run_status_label)
        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setToolTip(
            "Cancel the running simulation (SIGINT, same as Ctrl+C). "
            "Press again to force-kill."
        )
        # Compact size that fits within the (now 30px) tab bar.
        self._stop_btn.setFixedHeight(24)
        self._stop_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; font-size: 11px; }"
        )
        self._stop_btn.clicked.connect(self._on_stop_run)
        _sb_layout.addWidget(self._stop_btn)
        self._run_stop_bar.setVisible(False)
        # Escalation flag: first Stop press = SIGINT, second = force kill.
        self._stop_escalated = False
        self._console_tabs.setCornerWidget(
            self._run_stop_bar, Qt.Corner.TopRightCorner,
        )

        # Stop bar visibility follows the run lifecycle.
        self.run_output.started.connect(self._on_run_started_ui)
        self.run_output.finished.connect(self._on_run_finished_ui)

        # Ctrl+C cancels the run. Scoped to the RunOutputView so it
        # doesn't shadow Copy in the Python REPL tab or elsewhere.
        # Inside the xterm terminal itself, Ctrl+C is handled by
        # xterm.js (it sends \x03 straight to the PTY = SIGINT);
        # this shortcut is the fallback for when focus sits on the
        # corner Stop button instead of inside the terminal.
        from PySide6.QtGui import QShortcut, QKeySequence
        _ctrlc = QShortcut(QKeySequence("Ctrl+C"), self.run_output)
        _ctrlc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        _ctrlc.activated.connect(self._on_stop_run)

        self.script_editor = ScriptEditor()
        self.script_editor.runScript.connect(self.console.run_script)
        # Script Editor still talks to the Python REPL, so flip the
        # tab to it when the user runs a snippet (otherwise the output
        # would appear on a hidden tab).
        self.script_editor.runScript.connect(
            lambda *_: self._console_tabs.setCurrentWidget(self.console)
        )
        bottom_hsplitter.addWidget(self.script_editor)

        # Bottom horizontal proportions (console 60% | editor 40%)
        bottom_hsplitter.setStretchFactor(0, 3)
        bottom_hsplitter.setStretchFactor(1, 2)

        # Center vertical proportions (map 75% | console+editor 25%)
        self._center_splitter.setStretchFactor(0, 3)
        self._center_splitter.setStretchFactor(1, 1)

        # Right: properties panel
        self.properties_panel = PropertiesPanel()
        self._main_splitter.addWidget(self.properties_panel)

        # Horizontal proportions (tree 20% | center 55% | props 25%)
        self._main_splitter.setStretchFactor(0, 2)
        self._main_splitter.setStretchFactor(1, 5)
        self._main_splitter.setStretchFactor(2, 3)

        # Collapse buttons on splitter handles (parented to MainWindow to avoid clipping)
        add_collapse_button(self, self._main_splitter, 1, 0, "left")    # left panel
        add_collapse_button(self, self._main_splitter, 2, 2, "right")   # right panel
        add_collapse_button(self, self._center_splitter, 1, 1, "down")  # bottom panel

        # Property forms — all factory-based for retranslation support.
        # Eager forms are materialized immediately after registration.
        _m = self.model
        self._pick_centroid_node: int | None = None

        # Initialise form references (set by post_create callbacks)
        self._node_form = None
        self._gen_form = None
        self._bat_form = None
        self._line_form = None
        self._bus_form = None
        self._system_form = None
        self._zone_form = None
        self._fuel_entry_form = None
        self._transformer_form = None
        self._fuel_route_form = None
        self._fuel_form = None
        self._electrolyzer_form = None
        self._acdc_converter_form = None
        self._freq_converter_form = None
        self._investment_form = None
        self._technology_form = None

        # ── Post-create callbacks (connect signals + store refs) ──

        def _wire_node(f):
            self._node_form = f
            f.centroidPickRequested.connect(self._on_centroid_pick_requested)

        def _wire_gen(f):
            self._gen_form = f

        def _wire_bat(f):
            self._bat_form = f

        def _wire_line(f):
            self._line_form = f
            f.editTraceToggled.connect(self._on_line_edit_trace_toggled)
            f.lineDeleteRequested.connect(self._on_line_delete_requested)

        def _wire_bus(f):
            self._bus_form = f
            f.busChanged.connect(self._on_bus_form_changed)

        def _wire_system(f):
            self._system_form = f
            f.systemRenamed.connect(self._on_system_renamed)

        def _wire_zone(f):
            self._zone_form = f
            f.zoneChanged.connect(self._on_zone_form_changed)
            f.zoneDeleteRequested.connect(self._on_zone_delete_requested)
            f.editPolygonToggled.connect(self._on_zone_edit_polygon_toggled)

        def _wire_fuel_entry(f):
            self._fuel_entry_form = f
            f.fuelEntryChanged.connect(self._on_fuel_entry_form_changed)

        def _wire_transformer(f):
            self._transformer_form = f
            f.transformerChanged.connect(self._on_transformer_form_changed)

        def _wire_fuel_route(f):
            self._fuel_route_form = f
            f.editTraceToggled.connect(self._on_fuel_route_edit_trace_toggled)
            f.fuelRouteDeleteRequested.connect(self._on_fuel_route_delete_requested)

        def _wire_fuel(f):
            self._fuel_form = f
            f.fuelDeleteRequested.connect(self._on_fuel_delete_requested)

        def _wire_electrolyzer(f):
            self._electrolyzer_form = f
            f.electrolyzerDeleteRequested.connect(self._on_electrolyzer_delete_requested)

        def _wire_acdc(f):
            self._acdc_converter_form = f
            f.converterChanged.connect(self._on_acdc_converter_form_changed)

        def _wire_freq(f):
            self._freq_converter_form = f
            f.converterChanged.connect(self._on_freq_converter_form_changed)

        def _wire_investment(f):
            self._investment_form = f
            f.investmentDeleteRequested.connect(self._on_investment_delete_requested)

        def _wire_technology(f):
            self._technology_form = f
            f.technologyDeleteRequested.connect(self._on_technology_delete_requested)

        def _wire_islink(f):
            self._islink_form = f
            f.linkChanged.connect(lambda lid: self.model.interSystemLinkUpdated.emit(lid))
            f.linkDeleteRequested.connect(self._on_islink_delete_requested)
            f.editTraceToggled.connect(self._on_islink_edit_trace_toggled)

        # ── Register all forms (factory + post_create) ──

        self.properties_panel.register_form("node", lambda: NodeForm(_m), _wire_node)
        self.properties_panel.register_form("generator", lambda: GeneratorForm(_m), _wire_gen)
        self.properties_panel.register_form("battery", lambda: BatteryForm(_m), _wire_bat)
        self.properties_panel.register_form("line", lambda: LineForm(_m), _wire_line)
        self.properties_panel.register_form("bus", lambda: BusForm(_m), _wire_bus)
        self.properties_panel.register_form("system_settings", lambda: SystemForm(_m), _wire_system)
        self.properties_panel.register_form("zone", lambda: ZoneForm(_m), _wire_zone)
        self.properties_panel.register_form("fuel_entry", lambda: FuelEntryForm(_m), _wire_fuel_entry)
        self.properties_panel.register_form("transformer", lambda: TransformerForm(_m), _wire_transformer)
        self.properties_panel.register_form("fuel_source", lambda: FuelSourceForm(_m))
        self.properties_panel.register_form("fuel_storage", lambda: FuelStorageForm(_m))
        self.properties_panel.register_form("fuel_route", lambda: FuelRouteForm(_m), _wire_fuel_route)
        self.properties_panel.register_form("fuel", lambda: FuelForm(_m), _wire_fuel)
        self.properties_panel.register_form("global_settings", lambda: GlobalSettingsForm(_m))
        self.properties_panel.register_form("electrolyzer", lambda: ElectrolyzerForm(_m), _wire_electrolyzer)
        self.properties_panel.register_form("ev_config", lambda: EVForm(_m))
        self.properties_panel.register_form("rooftop_solar", lambda: RooftopSolarForm(_m))
        self.properties_panel.register_form("stochastic", lambda: StochasticForm(_m))
        self.properties_panel.register_form("acdc_converter", lambda: ACDCConverterForm(_m), _wire_acdc)
        self.properties_panel.register_form("freq_converter", lambda: FreqConverterForm(_m), _wire_freq)
        self.properties_panel.register_form("investment_entry", lambda: InvestmentForm(_m), _wire_investment)
        self.properties_panel.register_form("technology", lambda: TechnologyForm(_m), _wire_technology)
        # Inter-system link form (rendered when user clicks an islink_*
        # polyline or its tree item). Dispatch is wired in
        # _on_element_selected via the islink_ id prefix.
        from esfex.visualization.panels.inter_system_link_form import InterSystemLinkForm
        self.properties_panel.register_form(
            "inter_system_link", lambda: InterSystemLinkForm(_m), _wire_islink,
        )

        # Eagerly materialize the most common forms
        for _etype in ("node", "generator", "battery", "line", "bus", "system_settings"):
            self.properties_panel._materialize(_etype)

        # Floating results overlay on the map
        self._results_panel = ResultsPanel(self.map_widget, parent=self.map_widget)
        self._results_layer_active = False
        self.map_widget.installEventFilter(self)

        # Toolbar
        self.toolbar = EditorToolbar(self)
        self.addToolBar(self.toolbar)

        # Menu bar
        self._build_menu_bar()

        # Live language switching
        language_changed.connect(self.retranslateUi)

        # Connect signals
        self._connect_toolbar()
        self._connect_map_bridge()
        self._connect_sld_bridge()
        self._connect_tree()
        self._connect_model()

        # Build action registry and apply user preferences
        self._action_registry: dict[str, QAction] = {
            "file.new": self._act_new,
            "file.import": self._act_import_config,
            "file.import_geo": self._act_import_geo,
            "file.save": self._act_save,
            "file.export": self._act_export,
            "file.preferences": self._act_preferences,
        }
        self._action_registry.update(self.toolbar.get_action_registry())

        from esfex.visualization.preferences import (
            apply_shortcuts,
            get_shortcuts,
            load_preferences,
        )

        self._user_prefs = load_preferences()
        apply_shortcuts(self._action_registry, get_shortcuts(self._user_prefs))

        # Auto-save timer (controlled by preferences)
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._on_autosave)
        _init_general = self._user_prefs.get("general", {})
        _init_auto_save = _init_general.get("auto_save_interval", 0)
        if _init_auto_save > 0:
            self._auto_save_timer.start(_init_auto_save * 60_000)

        # Runtime preference flags
        self._auto_open_results = _init_general.get("auto_open_results", False)
        self._auto_validate_before_run = _init_general.get("auto_validate", True)

        # Unsaved-changes tracking. Set True on any user mutation
        # (dataMutated signal); cleared on load/save. Drives the close
        # prompt and the window title asterisk.
        self._modified: bool = False

    # ------------------------------------------------------------------
    # View tab switching (Geographic / Single-Line Diagram)
    # ------------------------------------------------------------------

    def _setup_center_views(self) -> QWidget:
        """Create tab bar + stacked widget for map/SLD view switching."""
        # Initialize SLD-related attributes EARLY so _build_sld_ops_bar
        # (called from this method) can read them without AttributeError.
        if not hasattr(self, "_sld_merge_level"):
            self._sld_merge_level = 1

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # Breadcrumb label (left) + tab bar (right)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(4, 0, 0, 0)
        tab_row.setSpacing(0)

        self._breadcrumb_label = QLabel()
        self._apply_breadcrumb_theme()
        tab_row.addWidget(self._breadcrumb_label)
        tab_row.addStretch()

        # System summary: e.g. "Cuba — 12 Nodes · 24 Buses · …"
        self._system_summary_label = QLabel()
        self._apply_system_summary_theme()
        tab_row.addWidget(self._system_summary_label)
        tab_row.addSpacing(8)

        self._view_tab_bar = QTabBar()
        self._view_tab_bar.setExpanding(False)
        self._view_tab_bar.setDrawBase(False)
        # Only two fixed tabs that always fit; without this Qt shows a
        # pair of left/right scroll arrows whenever the row reflows.
        self._view_tab_bar.setUsesScrollButtons(False)
        self._view_tab_bar.addTab(tr("view.geographic"))
        self._view_tab_bar.addTab(tr("view.sld"))
        self._view_tab_bar.setStyleSheet(
            "QTabBar::tab { padding: 4px 12px; font-size: 11px; }"
        )
        self._view_tab_bar.currentChanged.connect(self._on_view_tab_changed)
        tab_row.addWidget(self._view_tab_bar)

        vbox.addLayout(tab_row)

        # SLD operational toolbar (hidden by default)
        self._sld_ops_bar = self._build_sld_ops_bar()
        self._sld_ops_bar.setVisible(False)
        vbox.addWidget(self._sld_ops_bar)

        self._view_stack = QStackedWidget()
        self.map_widget = MapWidget()
        self.sld_widget = SldWidget()
        self._sld_page_ready = False   # True once JS page signals ready
        self._sld_dirty = True         # True when state changed since last SLD render
        self._sld_pending_json = None  # Queued graph JSON if page not ready yet
        # SLD aggregation level: 0=full detail, 1=(substation, voltage), 2=substation
        self._sld_merge_level = 1
        self._view_stack.addWidget(self.map_widget)

        # SLD + analysis panel in a horizontal splitter
        from PySide6.QtWidgets import QSplitter, QScrollArea
        self._sld_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._sld_splitter.addWidget(self.sld_widget)

        # Analysis panel (hidden by default)
        from esfex.visualization.panels.analysis_panel import AnalysisPanel
        self._analysis_panel = AnalysisPanel(self.model)
        self._analysis_panel.scenarioChanged.connect(self._on_analysis_scenario_changed)
        self._analysis_panel.runAllN1Requested.connect(self._on_run_all_n1)
        self._analysis_panel.runScreeningRequested.connect(self._on_run_screening)
        scroll = QScrollArea()
        scroll.setWidget(self._analysis_panel)
        scroll.setWidgetResizable(True)
        self._analysis_scroll = scroll
        scroll.setVisible(False)
        self._sld_splitter.addWidget(scroll)
        self._sld_splitter.setStretchFactor(0, 3)
        self._sld_splitter.setStretchFactor(1, 1)
        # Prevent collapsing the analysis panel via splitter drag
        self._sld_splitter.setCollapsible(1, False)

        self._view_stack.addWidget(self._sld_splitter)
        vbox.addWidget(self._view_stack)

        return container

    def _on_view_tab_changed(self, index: int):
        """Switch between Geographic View and Single-Line Diagram."""
        self._view_stack.setCurrentIndex(index)
        if index == 1:
            # Switching to SLD → only rebuild if state changed since last render.
            # Defer the render call so Qt finishes laying out the widget first
            # (otherwise QWebEngineView has 0x0 dimensions and fitView fails).
            if self._sld_dirty:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, self._rebuild_sld)
            # Disable toolbar drawing-mode actions (SLD is view-only)
            self.toolbar.setDrawingActionsEnabled(False)
            # Show SLD operational toolbar
            self._sld_ops_bar.setVisible(True)
        else:
            # Switching to Map → re-enable drawing actions, mark SLD dirty
            self._sld_dirty = True
            self.toolbar.setDrawingActionsEnabled(True)
            # Hide SLD operational toolbar and stop animation
            self._sld_ops_bar.setVisible(False)
            if hasattr(self, "_sld_play_timer") and self._sld_play_timer.isActive():
                self._sld_play_timer.stop()
                self._sld_btn_play.setChecked(False)
                self._sld_btn_play.setText("\u25B6")

    def _rebuild_sld(self):
        """Regenerate the single-line diagram from the current GuiSystemState."""
        from esfex.visualization.sld.graph_builder import build_elk_graph

        state = self.model.state
        colors = self._get_sld_theme_colors()
        elk_graph = build_elk_graph(
            state, colors, merge_level=self._sld_merge_level,
        )
        graph_json = json.dumps(elk_graph)

        if not self._sld_page_ready:
            # Page hasn't loaded yet — queue for when it's ready
            self._sld_pending_json = graph_json
            return

        self.sld_widget.render_graph(graph_json)
        self._sld_dirty = False

    @staticmethod
    def _merge_level_label(level: int) -> str:
        return {
            0: "Full detail",
            1: "Substation × kV",
            2: "Substation only",
        }.get(level, f"Level {level}")

    def _on_sld_merge_changed(self, level: int):
        """Slider moved — switch aggregation level and rerender."""
        level = max(0, min(2, int(level)))
        if level == self._sld_merge_level:
            return
        self._sld_merge_level = level
        if hasattr(self, "_sld_merge_label"):
            self._sld_merge_label.setText(self._merge_level_label(level))
        self._rebuild_sld()

    def _get_sld_theme_colors(self) -> dict:
        """Collect theme colors for the SLD graph builder (electrical only)."""
        theme = current_theme()
        mc = theme.map_elements
        return {
            "gen-renewable": mc.generator_renewable,
            "gen-nonrenewable": mc.generator_nonrenewable,
            "battery": mc.battery,
            "transformer": mc.transformer,
            "electrolyzer": mc.electrolyzer,
            "acdc_converter": mc.acdc_converter,
            "freq_converter": mc.freq_converter,
            "load": "#E67E22",
        }

    def _on_sld_ready(self):
        """Called when the SLD finishes layout and rendering."""
        if not self._sld_page_ready:
            # First signal = page loaded. Flush any queued render.
            self._sld_page_ready = True
            if self._sld_pending_json is not None:
                self.sld_widget.render_graph(self._sld_pending_json)
                self._sld_pending_json = None
                self._sld_dirty = False
                return  # sldReady will fire again after this render

        # Reapply operational overlay if results are loaded
        if getattr(self, "_sld_results_loader", None) is not None:
            self._update_sld_ops_overlay()

    def _on_sld_element_selected(self, element_type: str, element_id: str):
        """SLD click → select in tree + show properties (no loop back to SLD)."""
        self._on_element_selected(element_type, element_id)

    # ------------------------------------------------------------------
    # SLD Operational Toolbar
    # ------------------------------------------------------------------

    def _build_sld_ops_bar(self) -> QWidget:
        """Build the compact SLD operational toolbar (year, hour, play, export)."""
        bar = QWidget()
        bar.setObjectName("sldOpsBar")
        bar.setFixedHeight(34)
        self._sld_ops_bar_widget = bar
        self._apply_sld_ops_bar_theme(bar)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(8)

        # Analysis mode toggle
        self._sld_btn_analysis = QPushButton(tr("analysis_panel.analysis_btn"))
        self._sld_btn_analysis.setCheckable(True)
        self._sld_btn_analysis.clicked.connect(self._on_sld_analysis_toggle)
        h.addWidget(self._sld_btn_analysis)

        # Load results button
        self._sld_btn_load = QPushButton("Load Results")
        self._sld_btn_load.clicked.connect(self._on_sld_load_results)
        h.addWidget(self._sld_btn_load)

        h.addWidget(QLabel("|"))

        # Aggregation slider: 0 = full detail, 1 = (substation, voltage), 2 = substation
        h.addWidget(QLabel("Detail:"))
        self._sld_merge_slider = QSlider(Qt.Orientation.Horizontal)
        self._sld_merge_slider.setMinimum(0)
        self._sld_merge_slider.setMaximum(2)
        self._sld_merge_slider.setValue(self._sld_merge_level)
        self._sld_merge_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._sld_merge_slider.setTickInterval(1)
        self._sld_merge_slider.setSingleStep(1)
        self._sld_merge_slider.setPageStep(1)
        self._sld_merge_slider.setFixedWidth(90)
        self._sld_merge_slider.setToolTip(
            "Aggregation level:\n"
            "  0 — Full (every bus shown)\n"
            "  1 — Substation × Voltage (default)\n"
            "  2 — Substation only"
        )
        self._sld_merge_slider.valueChanged.connect(self._on_sld_merge_changed)
        h.addWidget(self._sld_merge_slider)

        self._sld_merge_label = QLabel(self._merge_level_label(self._sld_merge_level))
        self._sld_merge_label.setMinimumWidth(110)
        h.addWidget(self._sld_merge_label)

        h.addWidget(QLabel("|"))

        # Year combo
        h.addWidget(QLabel("Year:"))
        self._sld_year_combo = QComboBox()
        self._sld_year_combo.setEnabled(False)
        self._sld_year_combo.currentIndexChanged.connect(self._on_sld_year_changed)
        h.addWidget(self._sld_year_combo)

        # Hour slider + spinbox
        h.addWidget(QLabel("Hour:"))
        self._sld_hour_slider = QSlider(Qt.Orientation.Horizontal)
        self._sld_hour_slider.setMinimum(0)
        self._sld_hour_slider.setMaximum(0)
        self._sld_hour_slider.setEnabled(False)
        self._sld_hour_slider.setMinimumWidth(120)
        self._sld_hour_slider.valueChanged.connect(self._on_sld_hour_changed)
        h.addWidget(self._sld_hour_slider)

        self._sld_hour_spin = QSpinBox()
        self._sld_hour_spin.setMinimum(0)
        self._sld_hour_spin.setMaximum(0)
        self._sld_hour_spin.setEnabled(False)
        self._sld_hour_spin.valueChanged.connect(self._on_sld_hour_spin_changed)

        self._sld_update_timer = QTimer(self)
        self._sld_update_timer.setSingleShot(True)
        self._sld_update_timer.setInterval(50)
        self._sld_update_timer.timeout.connect(self._update_sld_ops_overlay)
        h.addWidget(self._sld_hour_spin)

        h.addWidget(QLabel("|"))

        # Play / Pause
        self._sld_btn_play = QPushButton("\u25B6")
        self._sld_btn_play.setFixedWidth(32)
        self._sld_btn_play.setEnabled(False)
        self._sld_btn_play.setCheckable(True)
        self._sld_btn_play.clicked.connect(self._on_sld_play_toggle)
        h.addWidget(self._sld_btn_play)

        # Speed combo
        self._sld_speed_combo = QComboBox()
        self._sld_speed_combo.addItems(["1x", "2x", "5x", "10x"])
        self._sld_speed_combo.setEnabled(False)
        h.addWidget(self._sld_speed_combo)

        h.addWidget(QLabel("|"))

        # Contingency combo (Level 2)
        h.addWidget(QLabel("Contingency:"))
        self._sld_contingency_combo = QComboBox()
        self._sld_contingency_combo.addItem("None", None)
        self._sld_contingency_combo.setEnabled(False)
        self._sld_contingency_combo.setMinimumWidth(180)
        self._sld_contingency_combo.currentIndexChanged.connect(
            self._on_sld_contingency_changed,
        )
        h.addWidget(self._sld_contingency_combo)

        h.addStretch()

        # Export SVG
        self._sld_btn_export = QPushButton("Export SVG")
        self._sld_btn_export.setEnabled(False)
        self._sld_btn_export.clicked.connect(self._on_sld_export_svg)
        h.addWidget(self._sld_btn_export)

        # Clear overlay
        self._sld_btn_clear = QPushButton("Clear")
        self._sld_btn_clear.setEnabled(False)
        self._sld_btn_clear.clicked.connect(self._on_sld_clear_overlay)
        h.addWidget(self._sld_btn_clear)

        # Animation timer
        self._sld_play_timer = QTimer()
        self._sld_play_timer.timeout.connect(self._on_sld_play_tick)

        # Results loader reference
        self._sld_results_loader = None

        # Analysis mode state
        self._sld_analysis_mode = False
        self._analysis_freq_analyzer = None

        return bar

    def _on_sld_load_results(self):
        """Open HDF5 file picker and load results for SLD overlay."""
        from PySide6.QtWidgets import QFileDialog
        from esfex.visualization.sld.sld_results_loader import SldResultsLoader

        path, _ = QFileDialog.getOpenFileName(
            self, "Load Simulation Results", str(Path.home()),
            "HDF5 Files (*.h5 *.hdf5);;All Files (*)",
        )
        if not path:
            return

        try:
            loader = SldResultsLoader(path, self.model.state)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load results:\n{e}")
            return

        self._sld_results_loader = loader

        # Populate year combo
        self._sld_year_combo.blockSignals(True)
        self._sld_year_combo.clear()
        for y in loader.years:
            self._sld_year_combo.addItem(str(y), y)
        self._sld_year_combo.blockSignals(False)

        # Set hour range
        max_h = max(0, loader.hours_per_year - 1)
        self._sld_hour_slider.setMaximum(max_h)
        self._sld_hour_spin.setMaximum(max_h)

        # Build contingency analyzer (Level 2)
        self._sld_contingency_analyzer = None
        try:
            from esfex.analysis.contingency import (
                ContingencyAnalyzer,
                build_contingency_from_state,
            )
            self._sld_contingency_analyzer = build_contingency_from_state(
                self.model.state,
                loader.num_nodes,
            )
        except Exception:
            log.debug("Could not build contingency analyzer", exc_info=True)

        # Enable controls
        self._sld_year_combo.setEnabled(True)
        self._sld_hour_slider.setEnabled(True)
        self._sld_hour_spin.setEnabled(True)
        self._sld_btn_play.setEnabled(True)
        self._sld_speed_combo.setEnabled(True)
        self._sld_btn_export.setEnabled(True)
        self._sld_btn_clear.setEnabled(True)
        self._sld_contingency_combo.setEnabled(True)

        # Show initial timestep
        self._update_sld_ops_overlay()

        # Populate contingency combo
        self._populate_contingency_combo()

    def _on_sld_year_changed(self, _index: int):
        self._update_sld_ops_overlay()

    def _on_sld_hour_changed(self, value: int):
        self._sld_hour_spin.blockSignals(True)
        self._sld_hour_spin.setValue(value)
        self._sld_hour_spin.blockSignals(False)
        self._sld_update_timer.start()

    def _on_sld_hour_spin_changed(self, value: int):
        self._sld_hour_slider.blockSignals(True)
        self._sld_hour_slider.setValue(value)
        self._sld_hour_slider.blockSignals(False)
        self._sld_update_timer.start()

    def _update_sld_ops_overlay(self):
        """Fetch snapshot from loader and push to SLD JS overlay."""
        loader = self._sld_results_loader
        if loader is None:
            return
        year_data = self._sld_year_combo.currentData()
        if year_data is None:
            return
        year = int(year_data)
        hour = self._sld_hour_slider.value()
        snapshot = loader.get_timestep(year, hour)
        self.sld_widget.update_operational_data(json.dumps(snapshot))

    def _on_sld_play_toggle(self, checked: bool):
        if checked:
            self._sld_btn_play.setText("\u23F8")
            speed_txt = self._sld_speed_combo.currentText()
            speed = int(speed_txt.replace("x", ""))
            self._sld_play_timer.start(max(50, 1000 // speed))
        else:
            self._sld_btn_play.setText("\u25B6")
            self._sld_play_timer.stop()

    def _on_sld_play_tick(self):
        cur = self._sld_hour_slider.value()
        mx = self._sld_hour_slider.maximum()
        if cur >= mx:
            self._sld_hour_slider.setValue(0)
        else:
            self._sld_hour_slider.setValue(cur + 1)

    def _on_sld_export_svg(self):
        """Request SVG export from JS and save to file."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "Export SLD as SVG", str(Path.home() / "sld_export.svg"),
            "SVG Files (*.svg);;All Files (*)",
        )
        if not path:
            return

        self._sld_export_path = path
        # Connect once to receive the SVG markup
        self.sld_widget.bridge.svgExported.connect(self._on_sld_svg_received)
        self.sld_widget.export_svg()

    def _on_sld_svg_received(self, svg_markup: str):
        """Write exported SVG to file."""
        self.sld_widget.bridge.svgExported.disconnect(self._on_sld_svg_received)
        path = getattr(self, "_sld_export_path", None)
        if path and svg_markup:
            try:
                Path(path).write_text(svg_markup, encoding="utf-8")
                self.statusBar().showMessage(f"SVG exported to {path}", 5000)
            except Exception as e:
                QMessageBox.warning(self, "Export Error", str(e))

    def _on_sld_clear_overlay(self):
        """Remove operational overlay from SLD."""
        self.sld_widget.clear_operational_data()
        self.sld_widget.clear_contingency_data()
        self._sld_results_loader = None
        self._sld_contingency_analyzer = None
        self._analysis_freq_analyzer = None

        # If in analysis mode, turn it off
        if getattr(self, "_sld_analysis_mode", False):
            self._sld_analysis_mode = False
            self._sld_btn_analysis.setChecked(False)
            self._analysis_scroll.setVisible(False)
            self._sld_btn_load.setEnabled(True)

        self._sld_year_combo.clear()
        self._sld_year_combo.setEnabled(False)
        self._sld_hour_slider.setEnabled(False)
        self._sld_hour_spin.setEnabled(False)
        self._sld_btn_play.setEnabled(False)
        self._sld_speed_combo.setEnabled(False)
        self._sld_btn_export.setEnabled(False)
        self._sld_btn_clear.setEnabled(False)
        self._sld_contingency_combo.blockSignals(True)
        self._sld_contingency_combo.clear()
        self._sld_contingency_combo.addItem("None", None)
        self._sld_contingency_combo.setEnabled(False)
        self._sld_contingency_combo.blockSignals(False)
        if self._sld_play_timer.isActive():
            self._sld_play_timer.stop()
            self._sld_btn_play.setChecked(False)
            self._sld_btn_play.setText("\u25B6")

    def _on_sld_analysis_toggle(self, checked: bool):
        """Toggle real-time analysis mode."""
        self._sld_analysis_mode = checked
        self._analysis_scroll.setVisible(checked)

        if checked:
            # Set splitter sizes so the analysis panel gets ~350px
            total_w = self._sld_splitter.width()
            panel_w = min(380, max(300, total_w // 3))
            self._sld_splitter.setSizes([total_w - panel_w, panel_w])

            # Collapse the properties panel to make room for the dispatch panel
            self._collapse_properties_panel()

            # Disable HDF5 time controls (analysis mode uses live data)
            self._sld_btn_load.setEnabled(False)
            self._sld_year_combo.setEnabled(False)
            self._sld_hour_slider.setEnabled(False)
            self._sld_hour_spin.setEnabled(False)
            self._sld_btn_play.setEnabled(False)
            self._sld_speed_combo.setEnabled(False)

            # Populate the analysis panel from current editor state
            self._analysis_panel.populate()

            # Build analyzers from editor state
            self._analysis_freq_analyzer = None
            self._sld_contingency_analyzer = None
            self._pp_bridge = None
            self._dc_contingency_analyzer = None
            try:
                from esfex.analysis.frequency import (
                    FrequencyAnalyzer,
                    build_gen_freq_params_from_state,
                )
                params = build_gen_freq_params_from_state(self.model.state)
                # Read nominal frequency from the system's buses
                f_nom = 50.0
                for bus in self.model.state.buses.values():
                    if bus.frequency_hz and bus.frequency_hz > 0:
                        f_nom = bus.frequency_hz
                        break
                # Adapt nadir limit to the system frequency
                nadir_limit = f_nom - 1.0  # 1 Hz below nominal
                self._analysis_freq_analyzer = FrequencyAnalyzer(
                    params, f_nom=f_nom, nadir_limit=nadir_limit,
                )
            except Exception:
                log.debug("Could not build frequency analyzer from state", exc_info=True)

            try:
                from esfex.analysis.contingency import build_contingency_from_state
                dc_analyzer = build_contingency_from_state(
                    self.model.state,
                    len(self.model.state.nodes),
                )
                self._dc_contingency_analyzer = dc_analyzer
                self._sld_contingency_analyzer = dc_analyzer
            except Exception:
                log.debug("Could not build contingency analyzer from state", exc_info=True)

            # Build integrated N-1 analyzer (combines electrical + frequency)
            self._integrated_n1 = None
            try:
                from esfex.analysis.n1_assessment import IntegratedN1Analyzer
                if self._dc_contingency_analyzer is not None:
                    self._integrated_n1 = IntegratedN1Analyzer(
                        contingency_analyzer=self._dc_contingency_analyzer,
                        frequency_analyzer=self._analysis_freq_analyzer,
                    )
            except Exception:
                log.debug("Could not build integrated N-1 analyzer", exc_info=True)

            # Set up AC power flow bridge — prefer native Julia NR solver
            ac_available = False
            try:
                from esfex.analysis.native_ac_bridge import NativeACBridge
                if NativeACBridge.is_available():
                    self._ac_bridge = NativeACBridge(self.model.state)
                    ac_available = True
            except Exception:
                log.debug("Native AC bridge not available", exc_info=True)

            # Fall back to pandapower bridge if native is not available
            if not ac_available:
                try:
                    from esfex.analysis.pandapower_bridge import PandapowerBridge
                    if PandapowerBridge.is_available():
                        self._ac_bridge = PandapowerBridge(self.model.state)
                        ac_available = True
                except Exception:
                    log.debug("pandapower bridge not available", exc_info=True)

            # Pandapower bridge (separate) — only for IEC 60909 short-circuit
            pp_available = False
            try:
                from esfex.analysis.pandapower_bridge import PandapowerBridge
                if PandapowerBridge.is_available():
                    self._pp_bridge = PandapowerBridge(self.model.state)
                    pp_available = True
            except Exception:
                log.debug("pandapower bridge not available", exc_info=True)

            # Configure mode selector (AC always if native available,
            # SC only if pandapower available)
            self._analysis_panel.setup_modes(
                ac_available, sc_available=pp_available,
            )
            self._analysis_panel.analysisModeChanged.connect(
                self._on_analysis_mode_changed,
            )

            # Enable contingency combo and export/clear
            self._sld_contingency_combo.setEnabled(True)
            self._sld_btn_export.setEnabled(True)
            self._sld_btn_clear.setEnabled(True)

            # Run initial analysis
            self._run_analysis_from_scenario()
        else:
            # Restore normal mode
            self._sld_btn_load.setEnabled(True)
            self._analysis_freq_analyzer = None
            self._ac_bridge = None
            self._pp_bridge = None
            self._analysis_panel.hide_pf_status()
            try:
                self._analysis_panel.analysisModeChanged.disconnect(
                    self._on_analysis_mode_changed,
                )
            except RuntimeError:
                pass
            # Clear overlays
            self.sld_widget.clear_operational_data()
            self.sld_widget.clear_contingency_data()
            self._sld_contingency_combo.blockSignals(True)
            self._sld_contingency_combo.clear()
            self._sld_contingency_combo.addItem("None", None)
            self._sld_contingency_combo.setEnabled(False)
            self._sld_contingency_combo.blockSignals(False)
            self._sld_btn_export.setEnabled(False)
            self._sld_btn_clear.setEnabled(False)

            # Restore properties panel
            self._restore_properties_panel()

    def _collapse_properties_panel(self) -> None:
        """Collapse the right properties panel to free space for analysis."""
        sizes = self._main_splitter.sizes()
        props_idx = 2  # tree=0, center=1, props=2
        if sizes[props_idx] > 0:
            self._saved_props_size = sizes[props_idx]
            sizes[props_idx] = 0
            self._main_splitter.setSizes(sizes)

    def _restore_properties_panel(self) -> None:
        """Restore the right properties panel to its previous size."""
        saved = getattr(self, "_saved_props_size", 0)
        if saved > 0:
            sizes = self._main_splitter.sizes()
            sizes[2] = saved
            self._main_splitter.setSizes(sizes)
            self._saved_props_size = 0

    def _on_analysis_mode_changed(self, mode: str):
        """Handle analysis mode switch (dc/ac/sc)."""
        if mode == "ac" or mode == "sc":
            # Switch to AC contingency analyzer using native or pandapower bridge
            bridge = getattr(self, "_ac_bridge", None)
            if bridge is not None:
                try:
                    from esfex.analysis.ac_contingency import ACContingencyAnalyzer
                    dc_fallback = getattr(self, "_dc_contingency_analyzer", None)
                    self._sld_contingency_analyzer = ACContingencyAnalyzer(
                        bridge, dc_fallback=dc_fallback,
                    )
                except Exception:
                    log.debug("Could not build AC contingency analyzer", exc_info=True)
        else:
            # Switch back to DC
            self._sld_contingency_analyzer = getattr(
                self, "_dc_contingency_analyzer", None,
            )
            self._analysis_panel.hide_pf_status()

        # Rerun analysis immediately
        if getattr(self, "_sld_analysis_mode", False):
            self._run_analysis_from_scenario()

    def _on_analysis_scenario_changed(self):
        """Handle changes from the analysis panel dispatch scenario."""
        if not getattr(self, "_sld_analysis_mode", False):
            return
        self._run_analysis_from_scenario()

    def _run_analysis_from_scenario(self):
        """Build snapshot from analysis panel and update SLD overlays."""
        from esfex.analysis.snapshot_builder import build_snapshot_from_scenario

        scenario = self._analysis_panel.get_scenario()
        snapshot = build_snapshot_from_scenario(self.model.state, scenario)

        mode = self._analysis_panel.get_analysis_mode()
        ac_bridge = getattr(self, "_ac_bridge", None)
        pp_bridge = getattr(self, "_pp_bridge", None)

        # ── AC / SC mode ──
        if mode in ("ac", "sc") and ac_bridge is not None:
            pf_result = ac_bridge.run_power_flow(scenario)
            if pf_result.converged:
                self._merge_ac_results(snapshot, pf_result)
                self._analysis_panel.update_pf_status(
                    converged=True,
                    iterations=pf_result.iterations,
                    violations=len(pf_result.voltage_violations),
                    losses_mw=pf_result.total_losses_mw,
                )

                # Short-circuit analysis (IEC 60909 — pandapower only)
                if mode == "sc" and pp_bridge is not None:
                    # Need pandapower network for SC; run its PF first
                    pp_pf = pp_bridge.run_power_flow(scenario)
                    if pp_pf.converged:
                        sc_result = pp_bridge.run_short_circuit()
                        if sc_result.ik_ka:
                            snapshot["system"]["short_circuit"] = {
                                "ik_ka": sc_result.ik_ka,
                                "ip_ka": sc_result.ip_ka,
                                "sk_mva": sc_result.sk_mva,
                            }
            else:
                self._analysis_panel.update_pf_status(converged=False)
        elif mode == "dc":
            self._analysis_panel.hide_pf_status()

        # Frequency analysis
        freq_analyzer = getattr(self, "_analysis_freq_analyzer", None)
        if freq_analyzer is not None:
            # Largest online gen output → worst-case ΔP
            max_output = max(
                (g["output_mw"] for g in snapshot["generators"].values()
                 if g.get("status", 1) > 0),
                default=0.0,
            )
            if max_output > 0:
                freq_resp = freq_analyzer.analyze(snapshot, max_output)
                snapshot["system"]["frequency"] = {
                    "rocof_hz_s": freq_resp.rocof_hz_per_s,
                    "nadir_hz": freq_resp.nadir_hz,
                    "steady_state_hz": freq_resp.steady_state_hz,
                    "t_nadir_s": freq_resp.t_nadir_s,
                    "h_total_mws": freq_resp.h_total_mws,
                    "delta_p_mw": freq_resp.delta_p_mw,
                    "is_stable": freq_resp.is_stable,
                    "rocof_ok": freq_resp.rocof_ok,
                    "f_nom_hz": freq_analyzer.f_nom,
                }

        self.sld_widget.update_operational_data(json.dumps(snapshot))

        # Populate contingency combo from scenario
        self._populate_contingency_combo_from_snapshot(snapshot)

        # If contingency is selected, rerun it
        ctg_data = self._sld_contingency_combo.currentData()
        if ctg_data is not None:
            self._run_contingency_on_snapshot(snapshot, ctg_data)

    def _merge_ac_results(self, snapshot: dict, pf_result) -> None:
        """Merge AC power flow results into the snapshot dict."""
        # Update node voltages
        for ni, node_data in snapshot.get("nodes", {}).items():
            # Find which bus(es) belong to this node
            for bus_id, bus in self.model.state.buses.items():
                if bus.parent_node == ni:
                    if bus_id in pf_result.bus_vm_pu:
                        node_data["vm_pu"] = pf_result.bus_vm_pu[bus_id]
                    if bus_id in pf_result.bus_va_deg:
                        node_data["voltage_angle_deg"] = pf_result.bus_va_deg[bus_id]
                    break  # Use first bus in node

        # Update line flows
        for edge_id, line_data in snapshot.get("lines", {}).items():
            if edge_id in pf_result.line_p_from_mw:
                line_data["flow_mw"] = pf_result.line_p_from_mw[edge_id]
                line_data["q_from_mvar"] = pf_result.line_q_from_mvar.get(edge_id, 0.0)
                line_data["p_loss_mw"] = pf_result.line_p_loss_mw.get(edge_id, 0.0)
                line_data["loading_pct"] = pf_result.line_loading_pct.get(edge_id, 0.0)
                cap = line_data.get("capacity_mw", 0.0)
                if cap > 0:
                    line_data["utilization_pct"] = round(
                        abs(pf_result.line_p_from_mw[edge_id]) / cap * 100, 1,
                    )

        # Update generator Q
        for gen_id, gen_data in snapshot.get("generators", {}).items():
            if gen_id in pf_result.gen_q_mvar:
                gen_data["q_mvar"] = pf_result.gen_q_mvar[gen_id]

        # System-level power flow summary
        snapshot["system"]["power_flow"] = {
            "converged": pf_result.converged,
            "iterations": pf_result.iterations,
            "total_losses_mw": pf_result.total_losses_mw,
            "voltage_violations": pf_result.voltage_violations,
        }

    def _populate_contingency_combo(self):
        """Populate the contingency combo with available N-1 contingencies."""
        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        loader = self._sld_results_loader
        if analyzer is None or loader is None:
            return

        year_data = self._sld_year_combo.currentData()
        if year_data is None:
            return
        year = int(year_data)
        hour = self._sld_hour_slider.value()
        snapshot = loader.get_timestep(year, hour)
        self._populate_contingency_combo_from_snapshot(snapshot)

    def _populate_contingency_combo_from_snapshot(self, snapshot: dict):
        """Populate the contingency combo from a snapshot dict."""
        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        if analyzer is None:
            return
        contingencies = analyzer.get_contingency_list(snapshot)

        # Preserve current selection if possible
        current_text = self._sld_contingency_combo.currentText()

        self._sld_contingency_combo.blockSignals(True)
        self._sld_contingency_combo.clear()
        self._sld_contingency_combo.addItem("None", None)
        restore_idx = 0
        for i, ctg in enumerate(contingencies):
            self._sld_contingency_combo.addItem(
                ctg["description"],
                ctg,
            )
            if ctg["description"] == current_text:
                restore_idx = i + 1
        self._sld_contingency_combo.setCurrentIndex(restore_idx)
        self._sld_contingency_combo.blockSignals(False)

    def _on_sld_contingency_changed(self, index: int):
        """Handle contingency combo selection."""
        ctg_data = self._sld_contingency_combo.currentData()
        if ctg_data is None:
            self.sld_widget.clear_contingency_data()
            return

        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        if analyzer is None:
            return

        # Get snapshot from the right source
        if getattr(self, "_sld_analysis_mode", False):
            from esfex.analysis.snapshot_builder import build_snapshot_from_scenario
            scenario = self._analysis_panel.get_scenario()
            snapshot = build_snapshot_from_scenario(self.model.state, scenario)
        else:
            loader = self._sld_results_loader
            if loader is None:
                return
            year_data = self._sld_year_combo.currentData()
            if year_data is None:
                return
            year = int(year_data)
            hour = self._sld_hour_slider.value()
            snapshot = loader.get_timestep(year, hour)

        self._run_contingency_on_snapshot(snapshot, ctg_data)

    def _run_contingency_on_snapshot(self, snapshot: dict, ctg_data: dict):
        """Run contingency analysis on a snapshot and update the SLD."""
        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        if analyzer is None:
            return

        ctg_type = ctg_data["type"]
        eid = ctg_data["element_id"]

        # Use integrated N-1 analyzer if available (adds frequency + voltage)
        integrated = getattr(self, "_integrated_n1", None)
        if integrated is not None:
            try:
                assessment = integrated.assess_single(snapshot, ctg_type, eid)
                import dataclasses
                result_dict = dataclasses.asdict(assessment.electrical)
                # Enrich with integrated assessment fields
                result_dict["severity_score"] = assessment.severity_score
                result_dict["binding_constraint"] = assessment.binding_constraint
                result_dict["is_n1_secure"] = assessment.is_secure
                if assessment.frequency is not None:
                    result_dict["rocof_hz_per_s"] = assessment.rocof_hz_per_s
                    result_dict["nadir_hz"] = assessment.nadir_hz
                    result_dict["has_frequency_violation"] = assessment.has_frequency_violation
                if assessment.voltage_violations:
                    result_dict["voltage_violations"] = assessment.voltage_violations
                self.sld_widget.update_contingency_data(json.dumps(result_dict))
                return
            except Exception:
                log.debug("Integrated N-1 failed, falling back to basic", exc_info=True)

        # Fallback: basic electrical-only contingency
        if ctg_type in ("generator", "battery"):
            result = analyzer.analyze_generator_loss(snapshot, eid)
        elif ctg_type in ("line", "transformer"):
            result = analyzer.analyze_line_loss(snapshot, eid)
        else:
            return

        import dataclasses
        result_dict = dataclasses.asdict(result)
        self.sld_widget.update_contingency_data(json.dumps(result_dict))

    def _on_run_all_n1(self, depth: str, redispatch: str, pi_threshold: float):
        """Run full N-1 or N-1-1 analysis on the current scenario snapshot."""
        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        if analyzer is None:
            self._analysis_panel.update_nk_summary("No contingency analyzer available.")
            return

        from esfex.analysis.snapshot_builder import build_snapshot_from_scenario
        scenario = self._analysis_panel.get_scenario()
        snapshot = build_snapshot_from_scenario(self.model.state, scenario)

        try:
            if depth == "n1_1" and hasattr(analyzer, "analyze_n1_1"):
                results = analyzer.analyze_n1_1(
                    snapshot, redistribution=redispatch
                )
                n_ctg = len(results) if results else 0
                n_violations = sum(
                    1 for r in (results or []) if not r.is_secure
                )
                self._analysis_panel.update_nk_summary(
                    f"N-1-1: {n_ctg} pairs analyzed, {n_violations} violations"
                )
            else:
                results = analyzer.analyze_all_contingencies(
                    snapshot, redistribution=redispatch
                )
                n_ctg = len(results) if results else 0
                n_violations = sum(
                    1 for r in (results or []) if not r.is_secure
                )
                self._analysis_panel.update_nk_summary(
                    f"N-1: {n_ctg} contingencies, {n_violations} violations"
                )
        except Exception as exc:
            log.warning("N-1 analysis failed: %s", exc, exc_info=True)
            self._analysis_panel.update_nk_summary(f"Error: {exc}")

    def _on_run_screening(self, redispatch: str, pi_threshold: float):
        """Run PI-based contingency screening on the current scenario."""
        analyzer = getattr(self, "_sld_contingency_analyzer", None)
        if analyzer is None:
            self._analysis_panel.update_nk_summary("No contingency analyzer available.")
            return

        from esfex.analysis.snapshot_builder import build_snapshot_from_scenario
        scenario = self._analysis_panel.get_scenario()
        snapshot = build_snapshot_from_scenario(self.model.state, scenario)

        try:
            if hasattr(analyzer, "screen_contingencies"):
                ranked = analyzer.screen_contingencies(
                    snapshot, pi_threshold=pi_threshold
                )
                n_total = len(ranked) if ranked else 0
                n_critical = sum(
                    1 for r in (ranked or []) if r.pi > pi_threshold
                ) if pi_threshold > 0 else n_total
                self._analysis_panel.update_nk_summary(
                    f"Screening: {n_critical}/{n_total} critical contingencies"
                )
            else:
                self._analysis_panel.update_nk_summary(
                    "Screening not available (no PTDF/LODF support)"
                )
        except Exception as exc:
            log.warning("Screening failed: %s", exc, exc_info=True)
            self._analysis_panel.update_nk_summary(f"Error: {exc}")

    def _apply_sld_ops_bar_theme(self, bar: QWidget | None = None):
        """Apply current theme colors to the SLD operational toolbar."""
        theme = current_theme()
        c = theme.colors
        bar = bar or getattr(self, "_sld_ops_bar_widget", None)
        if bar is None:
            return
        bar.setStyleSheet(f"""
            #sldOpsBar {{
                background: {c.surface_secondary};
                border-top: 1px solid {c.border_light};
                border-bottom: 1px solid {c.border_light};
            }}
            #sldOpsBar QLabel {{
                color: {c.text_primary};
                font-size: 11px;
                background: transparent;
            }}
            #sldOpsBar QComboBox {{
                font-size: 11px;
                min-width: 60px;
                color: {c.text_primary};
                background: {c.surface_primary};
                border: 1px solid {c.border_light};
                border-radius: 3px;
                padding: 1px 4px;
            }}
            #sldOpsBar QComboBox::drop-down {{
                border: none;
            }}
            #sldOpsBar QSpinBox {{
                font-size: 11px;
                min-width: 55px;
                color: {c.text_primary};
                background: {c.surface_primary};
                border: 1px solid {c.border_light};
                border-radius: 3px;
                padding: 1px 4px;
            }}
            #sldOpsBar QPushButton {{
                font-size: 11px;
                padding: 2px 10px;
                color: {c.text_on_dark};
                background: {c.accent_primary};
                border: 1px solid {c.accent_primary_hover};
                border-radius: 3px;
            }}
            #sldOpsBar QPushButton:hover {{
                background: {c.accent_primary_hover};
            }}
            #sldOpsBar QPushButton:pressed {{
                background: {c.accent_primary_pressed};
            }}
            #sldOpsBar QPushButton:disabled {{
                background: {c.border_light};
                color: {c.text_disabled};
                border-color: {c.border_light};
            }}
            #sldOpsBar QPushButton:checked {{
                background: {c.accent_secondary};
                border-color: {c.accent_secondary_hover};
            }}
            #sldOpsBar QSlider::groove:horizontal {{
                height: 4px;
                background: {c.border_medium};
                border-radius: 2px;
            }}
            #sldOpsBar QSlider::handle:horizontal {{
                width: 12px;
                height: 12px;
                margin: -4px 0;
                background: {c.accent_primary};
                border-radius: 6px;
            }}
            #sldOpsBar QSlider::sub-page:horizontal {{
                background: {c.accent_primary};
                border-radius: 2px;
            }}
        """)

    def _apply_breadcrumb_theme(self):
        """Apply current theme colors to the breadcrumb label."""
        c = current_theme().colors
        self._breadcrumb_label.setStyleSheet(
            f"QLabel {{ color: {c.text_secondary}; font-size: 11px;"
            f" padding: 2px 4px; }}"
        )

    def _apply_system_summary_theme(self):
        """Apply current theme colors to the system summary label."""
        c = current_theme().colors
        self._system_summary_label.setStyleSheet(
            f"QLabel {{ color: {c.text_secondary}; font-size: 11px;"
            f" padding: 0 12px; }}"
        )

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu_bar(self):
        menu_bar = self.menuBar()

        # ── File menu ──
        self._file_menu = menu_bar.addMenu(tr("menu.file"))
        file_menu = self._file_menu

        self._act_new = QAction(tr("menu.new_scenario"), self)
        self._act_new.setShortcut(QKeySequence("Ctrl+N"))
        self._act_new.triggered.connect(self._on_new_scenario)
        file_menu.addAction(self._act_new)

        file_menu.addSeparator()

        self._act_import_config = QAction(tr("menu.import_config"), self)
        self._act_import_config.setShortcut(QKeySequence("Ctrl+O"))
        self._act_import_config.triggered.connect(self._on_import)
        file_menu.addAction(self._act_import_config)

        self._act_import_system = QAction(tr("menu.import_system"), self)
        self._act_import_system.triggered.connect(self._on_import_system_from_config)
        file_menu.addAction(self._act_import_system)

        self._act_import_geo = QAction(tr("menu.import_geo"), self)
        self._act_import_geo.triggered.connect(self._on_import_geo_asset)
        file_menu.addAction(self._act_import_geo)

        file_menu.addSeparator()

        self._act_save = QAction(tr("menu.save"), self)
        self._act_save.setShortcut(QKeySequence("Ctrl+S"))
        self._act_save.triggered.connect(self._on_save)
        file_menu.addAction(self._act_save)

        self._act_export = QAction(tr("menu.export_as"), self)
        self._act_export.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._act_export.triggered.connect(self._on_export)
        file_menu.addAction(self._act_export)

        file_menu.addSeparator()

        # Recent Files submenu — populated dynamically each time it's
        # shown so deleted files disappear without needing app restart.
        self._recent_menu = file_menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._populate_recent_menu)

        file_menu.addSeparator()

        self._act_load_results = QAction(tr("menu.load_results"), self)
        self._act_load_results.setShortcut(QKeySequence("Ctrl+R"))
        self._act_load_results.triggered.connect(self._on_load_results)
        file_menu.addAction(self._act_load_results)

        file_menu.addSeparator()

        self._act_preferences = QAction(tr("menu.preferences"), self)
        self._act_preferences.setShortcut(QKeySequence("Ctrl+,"))
        self._act_preferences.triggered.connect(self._on_preferences)
        file_menu.addAction(self._act_preferences)

        # ── Edit menu ──
        self._edit_menu = menu_bar.addMenu(tr("menu.edit"))
        edit_menu = self._edit_menu

        self._act_undo = QAction(tr("menu.undo"), self)
        self._act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self._act_undo.setEnabled(False)
        self._act_undo.triggered.connect(self._on_undo)
        edit_menu.addAction(self._act_undo)

        self._act_redo = QAction(tr("menu.redo"), self)
        self._act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self._act_redo.setEnabled(False)
        self._act_redo.triggered.connect(self._on_redo)
        edit_menu.addAction(self._act_redo)

        # ── Workflows menu ──
        self._workflows_menu = menu_bar.addMenu(tr("menu.workflows"))
        workflows_menu = self._workflows_menu

        self._act_grid_mapping = QAction(tr("menu.grid_mapping"), self)
        self._act_grid_mapping.triggered.connect(self._on_grid_mapping_workflow)
        workflows_menu.addAction(self._act_grid_mapping)

        workflows_menu.addSeparator()

        self._act_solar_rooftop = QAction(tr("menu.solar_rooftop"), self)
        self._act_solar_rooftop.triggered.connect(self._on_solar_rooftop_workflow)
        workflows_menu.addAction(self._act_solar_rooftop)

        self._act_otec_studio = QAction(tr("menu.otec"), self)
        self._act_otec_studio.triggered.connect(self._on_otec_studio)
        workflows_menu.addAction(self._act_otec_studio)

        self._act_wind = QAction(tr("menu.wind"), self)
        self._act_wind.triggered.connect(self._on_wind_workflow)
        workflows_menu.addAction(self._act_wind)

        self._act_solar_pv = QAction(tr("menu.solar_pv"), self)
        self._act_solar_pv.triggered.connect(self._on_solar_pv_workflow)
        workflows_menu.addAction(self._act_solar_pv)

        self._act_ev_v2g = QAction(tr("menu.ev_v2g"), self)
        self._act_ev_v2g.triggered.connect(self._on_ev_v2g_workflow)
        workflows_menu.addAction(self._act_ev_v2g)

        self._act_demand_estimation = QAction(tr("menu.demand_estimation"), self)
        self._act_demand_estimation.triggered.connect(
            self._on_demand_estimation_workflow
        )
        workflows_menu.addAction(self._act_demand_estimation)

        workflows_menu.addSeparator()

        self._act_financial = QAction(tr("menu.financial_analysis"), self)
        self._act_financial.triggered.connect(self._on_financial_workflow)
        workflows_menu.addAction(self._act_financial)

        # ── Plugins menu ──
        self._plugins_menu = menu_bar.addMenu(tr("menu.plugins"))

        self._act_manage_plugins = QAction(tr("menu.manage_plugins"), self)
        self._act_manage_plugins.triggered.connect(self._on_manage_plugins)
        self._plugins_menu.addAction(self._act_manage_plugins)

        self._plugins_menu.addSeparator()

        # ── Help menu ──
        self._help_menu = menu_bar.addMenu(tr("menu.help"))
        help_menu = self._help_menu

        self._act_documentation = QAction(tr("menu.documentation"), self)
        self._act_documentation.setShortcut(QKeySequence("F1"))
        self._act_documentation.triggered.connect(self._on_documentation)
        help_menu.addAction(self._act_documentation)

        self._act_shortcuts = QAction(tr("menu.keyboard_shortcuts"), self)
        self._act_shortcuts.triggered.connect(self._on_keyboard_shortcuts)
        help_menu.addAction(self._act_shortcuts)

        help_menu.addSeparator()

        self._act_about = QAction(tr("menu.about"), self)
        self._act_about.triggered.connect(self._on_about)
        help_menu.addAction(self._act_about)

    # ------------------------------------------------------------------
    # Dynamic language retranslation
    # ------------------------------------------------------------------

    def retranslateUi(self):
        """Update all translatable strings in the main window."""
        self.setWindowTitle(tr("app.title"))

        # Menus
        self._file_menu.setTitle(tr("menu.file"))
        self._edit_menu.setTitle(tr("menu.edit"))
        self._workflows_menu.setTitle(tr("menu.workflows"))
        self._plugins_menu.setTitle(tr("menu.plugins"))
        self._help_menu.setTitle(tr("menu.help"))

        # Edit menu actions
        self._act_undo.setText(tr("menu.undo"))
        self._act_redo.setText(tr("menu.redo"))

        # File menu actions
        self._act_new.setText(tr("menu.new_scenario"))
        self._act_import_config.setText(tr("menu.import_config"))
        self._act_import_system.setText(tr("menu.import_system"))
        self._act_import_geo.setText(tr("menu.import_geo"))
        self._act_save.setText(tr("menu.save"))
        self._act_export.setText(tr("menu.export_as"))
        self._act_load_results.setText(tr("menu.load_results"))
        self._act_preferences.setText(tr("menu.preferences"))

        # Workflows menu actions
        self._act_grid_mapping.setText(tr("menu.grid_mapping"))
        self._act_solar_rooftop.setText(tr("menu.solar_rooftop"))
        self._act_otec_studio.setText(tr("menu.otec"))
        self._act_wind.setText(tr("menu.wind"))
        self._act_solar_pv.setText(tr("menu.solar_pv"))
        self._act_ev_v2g.setText(tr("menu.ev_v2g"))
        self._act_demand_estimation.setText(tr("menu.demand_estimation"))
        self._act_financial.setText(tr("menu.financial_analysis"))

        # Plugins menu actions
        self._act_manage_plugins.setText(tr("menu.manage_plugins"))

        # Help menu actions
        self._act_documentation.setText(tr("menu.documentation"))
        self._act_shortcuts.setText(tr("menu.keyboard_shortcuts"))
        self._act_about.setText(tr("menu.about"))

        # View tabs
        self._view_tab_bar.setTabText(0, tr("view.geographic"))
        self._view_tab_bar.setTabText(1, tr("view.sld"))

        # SLD ops bar
        if hasattr(self, '_sld_btn_analysis'):
            self._sld_btn_analysis.setText(tr("analysis_panel.analysis_btn"))

        # Delegate to child panels
        self.toolbar.retranslateUi()
        self.element_tree.retranslateUi()
        self.properties_panel.retranslateUi()
        self.script_editor.retranslateUi()
        self._results_panel.retranslateUi()
        self._analysis_panel.retranslateUi()

        # Refresh strip system-summary so its labels follow the new language
        self._refresh_system_summary()

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_toolbar(self):
        self.toolbar.modeChanged.connect(self._on_mode_changed)
        self.toolbar.layerChanged.connect(self._on_layer_changed)
        self.toolbar.baseMapChanged.connect(self.map_widget.set_base_map)
        self.toolbar.addSystemRequested.connect(self._on_add_system_requested)
        self.toolbar.validateRequested.connect(self._on_validate)
        self.toolbar.runRequested.connect(self._on_run_requested)
        self.toolbar.sensitivityRequested.connect(self._on_sensitivity_requested)
        self.toolbar.resultsRequested.connect(self._on_results_requested)
        self.toolbar.riskRequested.connect(self._on_risk_workflow)
        self.console.subprocessFinished.connect(self._on_subprocess_finished)

    def _connect_map_bridge(self):
        bridge = self.map_widget.bridge
        bridge.mapReady.connect(self._on_map_ready)
        bridge.lineDrawn.connect(self._on_line_drawn)
        bridge.zoneDrawn.connect(self._on_zone_drawn)
        bridge.elementSelected.connect(self._on_element_selected)
        bridge.elementDeselected.connect(self._on_element_deselected)
        # New signals for adding elements at map
        if hasattr(bridge, "fuelEntryPlaced"):
            bridge.fuelEntryPlaced.connect(self._on_fuel_entry_placed)
        if hasattr(bridge, "elementPlaced"):
            bridge.elementPlaced.connect(self._on_element_placed)
        if hasattr(bridge, "elementDragged"):
            bridge.elementDragged.connect(self._on_element_dragged)
        if hasattr(bridge, "polylineTraceCompleted"):
            bridge.polylineTraceCompleted.connect(self._on_polyline_trace_completed)
        if hasattr(bridge, "fuelRouteTraceCompleted"):
            bridge.fuelRouteTraceCompleted.connect(self._on_fuel_route_trace_completed)
        if hasattr(bridge, "zoneEdited"):
            bridge.zoneEdited.connect(self._on_zone_edited)
        if hasattr(bridge, "lineEdited"):
            bridge.lineEdited.connect(self._on_line_edited)
        if hasattr(bridge, "fuelRouteEdited"):
            bridge.fuelRouteEdited.connect(self._on_fuel_route_edited)
        if hasattr(bridge, "modeReset"):
            bridge.modeReset.connect(self._on_mode_reset_from_js)
        if hasattr(bridge, "elementPlacedOnLine"):
            bridge.elementPlacedOnLine.connect(self._on_element_placed_on_line)
        if hasattr(bridge, "elementDroppedOnLine"):
            bridge.elementDroppedOnLine.connect(self._on_element_dropped_on_line)
        if hasattr(bridge, "markerContextAction"):
            bridge.markerContextAction.connect(self._on_marker_context_action)

    def _connect_sld_bridge(self):
        bridge = self.sld_widget.bridge
        bridge.sldReady.connect(self._on_sld_ready)
        bridge.elementSelected.connect(self._on_sld_element_selected)
        bridge.elementDeselected.connect(self._on_element_deselected)

    def _connect_tree(self):
        self.element_tree.elementSelected.connect(self._on_tree_element_selected)
        self.element_tree.elementFocused.connect(self._on_tree_element_focused)
        self.element_tree.systemSwitchRequested.connect(self._switch_to_system)
        self.element_tree.deleteRequested.connect(self._on_tree_delete_requested)
        self.element_tree.batchDeleteRequested.connect(self._on_tree_batch_delete)
        self.element_tree.duplicateRequested.connect(self._on_tree_duplicate_requested)
        self.element_tree.copyRequested.connect(self._on_tree_copy_requested)
        self.element_tree.pasteRequested.connect(self._on_tree_paste_requested)
        self.element_tree.geoAssetVisibilityChanged.connect(
            self._on_geo_asset_visibility_changed
        )
        self.element_tree.parseGeoAssetRequested.connect(
            self._on_parse_geo_asset
        )
        self.element_tree.addNodeRequested.connect(self._on_add_node_to_system)
        self.element_tree.addFuelRequested.connect(self._on_add_fuel_to_system)
        self.element_tree.addTechnologyRequested.connect(self._on_add_technology_to_system)
        self.element_tree.addInvestmentRequested.connect(self._on_add_investment_to_system)
        self.element_tree.multiElementSelected.connect(self._on_multi_element_selected)
        self.element_tree.deleteSystemRequested.connect(self._on_delete_system)

    def _connect_model(self):
        m = self.model
        m.nodeAdded.connect(self._on_model_node_added)
        m.nodeRemoved.connect(self._on_model_node_removed)
        m.nodeUpdated.connect(self._on_model_node_updated)
        m.generatorAdded.connect(self._on_model_gen_added)
        m.generatorRemoved.connect(self._on_model_gen_removed)
        m.batteryAdded.connect(self._on_model_bat_added)
        m.batteryRemoved.connect(self._on_model_bat_removed)
        m.lineAdded.connect(self._on_model_line_added)
        m.lineRemoved.connect(self._on_model_line_removed)
        m.zoneAdded.connect(self._on_model_zone_added)
        m.zoneRemoved.connect(self._on_model_zone_removed)
        m.fuelEntryAdded.connect(self._on_model_fuel_entry_added)
        m.fuelEntryRemoved.connect(self._on_model_fuel_entry_removed)
        m.transformerAdded.connect(self._on_model_transformer_added)
        m.transformerRemoved.connect(self._on_model_transformer_removed)
        m.generatorUpdated.connect(self._on_model_gen_updated)
        m.batteryUpdated.connect(self._on_model_bat_updated)
        m.lineUpdated.connect(self._on_model_line_updated)
        m.fuelSourceAdded.connect(self._on_model_fuel_source_added)
        m.fuelSourceRemoved.connect(self._on_model_fuel_source_removed)
        m.fuelSourceUpdated.connect(self._on_model_fuel_source_updated)
        m.fuelStorageAdded.connect(self._on_model_fuel_storage_added)
        m.fuelStorageRemoved.connect(self._on_model_fuel_storage_removed)
        m.fuelStorageUpdated.connect(self._on_model_fuel_storage_updated)
        m.fuelRouteAdded.connect(self._on_model_fuel_route_added)
        m.fuelRouteRemoved.connect(self._on_model_fuel_route_removed)
        m.fuelRouteUpdated.connect(self._on_model_fuel_route_updated)
        m.interSystemLinkAdded.connect(self._on_model_islink_added)
        m.interSystemLinkRemoved.connect(self._on_model_islink_removed)
        m.interSystemLinkUpdated.connect(self._on_model_islink_updated)
        m.stateLoaded.connect(self._on_state_loaded)
        m.dataMutated.connect(self._mark_modified)
        # NOTE: Form signals (line_form, bus_form, etc.) are connected
        # via post_create callbacks in properties_panel.register_form().
        # Fuel (FuelConfig) signals
        m.fuelAdded.connect(self._on_model_fuel_added)
        m.fuelRemoved.connect(self._on_model_fuel_removed)
        m.fuelUpdated.connect(self._on_model_fuel_updated)
        # Electrolyzer signals
        m.electrolyzerAdded.connect(self._on_model_electrolyzer_added)
        m.electrolyzerRemoved.connect(self._on_model_electrolyzer_removed)
        m.electrolyzerUpdated.connect(self._on_model_electrolyzer_updated)
        # AC/DC Converter signals
        m.acdcConverterAdded.connect(self._on_model_acdc_converter_added)
        m.acdcConverterRemoved.connect(self._on_model_acdc_converter_removed)
        # Frequency Converter signals
        m.freqConverterAdded.connect(self._on_model_freq_converter_added)
        m.freqConverterRemoved.connect(self._on_model_freq_converter_removed)
        # Bus signals
        m.busAdded.connect(self._on_model_bus_added)
        m.busRemoved.connect(self._on_model_bus_removed)
        m.busUpdated.connect(self._on_model_bus_updated)
        # Investment portfolio signals
        m.investmentEntryAdded.connect(self._on_model_investment_added)
        m.investmentEntryRemoved.connect(self._on_model_investment_removed)
        m.investmentEntryUpdated.connect(self._on_model_investment_updated)
        # Technology signals
        m.technologyAdded.connect(self._on_model_technology_added)
        m.technologyRemoved.connect(self._on_model_technology_removed)
        m.technologyUpdated.connect(self._on_model_technology_updated)
        # Re-render map when global visual scaling changes
        m.globalSettingsUpdated.connect(self._on_visual_scaling_changed)
        # Undo/redo state
        m.undoChanged.connect(self._on_undo_changed)

        # Invalidate validation on structural model changes
        for sig in (
            m.nodeAdded, m.nodeRemoved, m.generatorAdded, m.generatorRemoved,
            m.batteryAdded, m.batteryRemoved, m.lineAdded, m.lineRemoved,
            m.transformerAdded, m.transformerRemoved,
            m.fuelEntryAdded, m.fuelEntryRemoved, m.electrolyzerAdded,
            m.electrolyzerRemoved, m.acdcConverterAdded, m.acdcConverterRemoved,
            m.freqConverterAdded, m.freqConverterRemoved, m.busAdded, m.busRemoved,
            m.stateLoaded,
            # Attribute/property changes
            m.nodeUpdated, m.generatorUpdated, m.batteryUpdated,
            m.lineUpdated, m.busUpdated, m.electrolyzerUpdated,
            m.fuelUpdated, m.technologyUpdated, m.investmentEntryUpdated,
            m.systemSettingsUpdated, m.globalSettingsUpdated,
        ):
            sig.connect(self._invalidate_validation)

        # Keep the strip's system summary in sync. Cardinality-changing
        # events trigger a refresh; ``stateLoaded`` covers full reloads
        # and system switches.
        for sig in (
            m.nodeAdded, m.nodeRemoved,
            m.busAdded, m.busRemoved,
            m.generatorAdded, m.generatorRemoved,
            m.transformerAdded, m.transformerRemoved,
            m.lineAdded, m.lineRemoved,
            m.stateLoaded,
        ):
            sig.connect(self._refresh_system_summary)

    # ------------------------------------------------------------------
    # Toolbar slots
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: str):
        if mode == "draw_zone":
            self.map_widget.enable_polygon_draw()
        else:
            self.map_widget.disable_polygon_draw()
            self.map_widget.set_mode(mode)

    def _on_mode_reset_from_js(self):
        """ESC pressed in the map — reset toolbar back to Select."""
        self.toolbar.reset_mode()

    def eventFilter(self, obj, event):
        """Reposition the floating results overlay when the map resizes."""
        from PySide6.QtCore import QEvent
        if obj is self.map_widget and event.type() == QEvent.Type.Resize:
            if self._results_layer_active:
                self._results_panel.reposition(
                    event.size().width(), event.size().height(),
                )
        return super().eventFilter(obj, event)

    def _on_layer_changed(self, layer: str):
        # Deactivate results layer when switching away
        if self._results_layer_active and layer != "results":
            self._results_panel.deactivate()
            self._results_layer_active = False

        if layer == "electrical":
            self.map_widget.show_electrical_layer()
        elif layer == "primary_energy":
            self.map_widget.show_primary_energy_layer()
        elif layer == "results":
            self._activate_results_layer()
        else:
            self.map_widget.show_all_layers()

    def _activate_results_layer(self):
        """Show the Results layer and its floating overlay."""
        self.map_widget.show_results_layer()
        self._results_layer_active = True

        # Provide output dir if available
        output_dir = getattr(self, "_last_output_dir", "")
        if output_dir:
            self._results_panel.set_output_dir(output_dir)

        # Provide GUI node coordinates as fallback (one entry per node index,
        # using the first bus belonging to that node)
        state = self.model.state
        num_nodes = len(state.nodes)
        coords: list[tuple[float, float]] = [(0.0, 0.0)] * num_nodes
        for bus in state.buses.values():
            idx = bus.parent_node
            if 0 <= idx < num_nodes and coords[idx] == (0.0, 0.0):
                coords[idx] = (bus.latitude, bus.longitude)
        if coords:
            self._results_panel.set_gui_node_coords(coords)

        self._results_panel.activate()
        self._results_panel.reposition(
            self.map_widget.width(), self.map_widget.height(),
        )

    def _switch_to_system(self, system_name: str):
        """Switch to a different system, saving current state first."""
        if system_name == self._current_system_name:
            return
        # Save current state back
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state
        # Load new state
        if system_name in self._all_states:
            self._current_system_name = system_name
            self.element_tree.set_current_system(system_name)
            self.model.load_state(self._all_states[system_name])
            self.console.update_namespace(state=self.model.state)
            self._refresh_system_summary()

    def _switch_to_system_quiet(self, system_name: str):
        """Switch to a different system, doing a full rebuild but
        suppressing the map zoom/fit-bounds.

        Used when clicking an element from another system — the full
        state load must happen (so properties resolve correctly), but
        the map should stay at the current view position.
        """
        if system_name == self._current_system_name:
            return
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state
        if system_name in self._all_states:
            self._current_system_name = system_name
            self.element_tree.set_current_system(system_name)
            self._suppress_fit_bounds = True
            self.model.load_state(self._all_states[system_name])
            self._suppress_fit_bounds = False
            self.console.update_namespace(state=self.model.state)
            self._refresh_system_summary()

    def _resolve_cross_system_id(self, element_id: str) -> str:
        """Resolve a possibly system-qualified element id.

        The element tree prefixes ids with ``<system>/`` when the
        clicked element lives under a different system than the active
        one (see element_tree._on_selection_changed). Switching to that
        system quietly first means property forms resolve the id against
        the correct state — so the user no longer has to click the
        system before navigating to one of its elements. Returns the
        bare id with any recognized system prefix stripped.
        """
        if "/" in element_id:
            sys_name, raw_id = element_id.rsplit("/", 1)
            if sys_name in self._all_states:
                if sys_name != self._current_system_name:
                    self._switch_to_system_quiet(sys_name)
                return raw_id
        return element_id

    def _on_system_renamed(self, old_name: str, new_name: str):
        """Handle system name change from the system settings form."""
        if new_name in self._all_states and new_name != old_name:
            QMessageBox.warning(
                self, tr("messages.duplicate_name_title"),
                tr("messages.duplicate_name", name=new_name),
            )
            # Revert
            self._model.state.name = old_name
            return
        # Update _all_states dict
        state = self._all_states.pop(old_name, None)
        if state is not None:
            self._all_states[new_name] = state
        if self._current_system_name == old_name:
            self._current_system_name = new_name
        # Update element tree
        self.element_tree.rename_system(old_name, new_name)

    def _update_map_actions_state(self):
        """Enable/disable map-element toolbar actions based on system count."""
        self.toolbar.set_map_actions_enabled(len(self._all_states) > 0)

    def _create_system_for_wizard(self, name: str) -> bool:
        """Create a new system (called by grid mapping wizard).

        Returns True if created, False if name already exists.
        Replicates the "Add system" GUI action, including creating a
        default node so the system is immediately usable.
        """
        if name in self._all_states:
            return False
        new_state = GuiSystemState(name=name)
        self._all_states[name] = new_state
        self.element_tree.add_system(name)
        self._switch_to_system(name)
        # Every system needs at least one node (same as _on_add_system_requested)
        self.model.add_node(name="Node 0")
        self._update_map_actions_state()
        return True

    def _on_add_system_requested(self):
        """Show dialog to create a new empty system."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, tr("messages.new_system_title"), tr("messages.new_system_prompt"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._all_states:
            QMessageBox.warning(
                self, tr("messages.duplicate_name_title"),
                tr("messages.duplicate_name", name=name),
            )
            return
        new_state = GuiSystemState(name=name)
        self._all_states[name] = new_state
        # Auto-select new system for simulation
        g = self.model.global_settings
        if name not in g.systems_to_simulate:
            g.systems_to_simulate.append(name)
        self.element_tree.add_system(name)
        self._switch_to_system(name)
        # Every system needs at least one node
        self.model.add_node(name="Node 0")
        self._update_map_actions_state()
        self._sync_systems_to_form()

    def _on_add_node_to_system(self, system_name: str):
        """Add a new node to the specified system via context menu."""
        if system_name != self._current_system_name:
            self._switch_to_system(system_name)
        self._ensure_system_exists()
        self.model.add_node()

    def _on_add_fuel_to_system(self, system_name: str):
        """Add a new fuel to the specified system via context menu."""
        if system_name != self._current_system_name:
            self._switch_to_system(system_name)
        self._on_add_fuel_requested()

    def _on_add_investment_to_system(self, system_name: str, technology_type: str):
        """Add a new investment entry to the specified system."""
        if system_name != self._current_system_name:
            self._switch_to_system(system_name)
        pretty = technology_type.replace("_", " ").title()
        entry_id = self.model.add_investment_entry(
            name=f"New {pretty}",
            technology_type=technology_type,
        )
        # Auto-populate with all nodes
        from esfex.visualization.data.gui_model import GuiInvestmentNodeData
        entry = self.model.state.investment_portfolio[entry_id]
        for node in self.model.state.nodes:
            entry.node_data.append(GuiInvestmentNodeData(node_index=node.index))
        # Select it
        self.properties_panel.show_element("investment_entry", entry_id)

    def _on_add_technology_to_system(self, system_name: str):
        """Add a new technology to the specified system via context menu."""
        if system_name != self._current_system_name:
            self._switch_to_system(system_name)
        tech_id = self.model.add_technology(name="New Technology")
        self.properties_panel.show_element("technology", tech_id)

    def _on_delete_system(self, system_name: str):
        """Delete a system after confirmation."""
        if len(self._all_states) <= 1:
            QMessageBox.warning(
                self, tr("common.warning"),
                "Cannot delete the last remaining system.",
            )
            return
        reply = QMessageBox.question(
            self, tr("tree_ctx.delete_system"),
            f"Are you sure you want to delete system '{system_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # If deleting the current system, switch to another first
        if system_name == self._current_system_name:
            other = next(n for n in self._all_states if n != system_name)
            self._switch_to_system(other)
        del self._all_states[system_name]
        # Remove from simulation list
        g = self.model.global_settings
        if system_name in g.systems_to_simulate:
            g.systems_to_simulate.remove(system_name)
        self.element_tree.remove_system(system_name)
        self._update_map_actions_state()
        self._sync_systems_to_form()

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _on_undo(self):
        self.model.undo()

    def _on_redo(self):
        self.model.redo()

    def _on_undo_changed(self):
        self._act_undo.setEnabled(self.model.can_undo)
        self._act_redo.setEnabled(self.model.can_redo)

    def _on_preferences(self):
        from esfex.visualization.panels.preferences_dialog import (
            PreferencesDialog,
        )
        from esfex.visualization.preferences import (
            DEFAULT_SHORTCUTS,
            apply_shortcuts,
            get_shortcuts,
            save_preferences,
        )

        current = get_shortcuts(self._user_prefs)
        dlg = PreferencesDialog(self._user_prefs, current, parent=self)
        if dlg.exec() == PreferencesDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result is not None:
                # Shortcuts: store only overrides
                new_shortcuts = result.get("shortcuts", {})
                overrides = {
                    aid: seq
                    for aid, seq in new_shortcuts.items()
                    if seq != DEFAULT_SHORTCUTS.get(aid, "")
                }
                self._user_prefs["shortcuts"] = overrides
                apply_shortcuts(self._action_registry, new_shortcuts)

                # Other categories: store directly
                for key in ("general", "map", "solver",
                            "simulation", "editor"):
                    if key in result:
                        self._user_prefs[key] = result[key]

                save_preferences(self._user_prefs)

                # Apply theme + font-size change live
                general = result.get("general", {})
                theme_name = general.get("theme", "Light")
                if theme_name == "System":
                    theme_name = "Light"
                font_size = general.get("font_size", None)
                from esfex.visualization.theme import (
                    apply_theme as _apply_theme,
                    get_theme_by_name,
                )
                app = QApplication.instance()
                if app is not None:
                    _apply_theme(
                        app,
                        get_theme_by_name(theme_name),
                        font_size=font_size,
                    )
                    self.map_widget._inject_theme(True)
                    self.toolbar.refresh_icons()
                    self.script_editor.refresh_theme()
                    self.element_tree.refresh_theme()
                    self._apply_sld_ops_bar_theme()
                    self._apply_breadcrumb_theme()
                    self._apply_system_summary_theme()

                # Apply language change live
                new_lang = general.get("language", "en")
                from esfex.visualization.i18n import get_language
                if new_lang != get_language():
                    set_language(new_lang)

                # Apply general preferences
                auto_save_interval = general.get("auto_save_interval", 0)
                if auto_save_interval > 0:
                    self._auto_save_timer.start(auto_save_interval * 60_000)
                else:
                    self._auto_save_timer.stop()
                self._auto_open_results = general.get(
                    "auto_open_results", False,
                )
                self._auto_validate_before_run = general.get(
                    "auto_validate", True,
                )

                # Apply editor preferences
                editor_prefs = result.get("editor", {})
                if hasattr(self, "script_editor"):
                    self.script_editor.apply_preferences(editor_prefs)

    # ------------------------------------------------------------------
    # Plugins menu
    # ------------------------------------------------------------------

    def _on_manage_plugins(self):
        """Open the Plugins Manager dialog."""
        from esfex.visualization.panels.plugins_dialog import PluginsDialog

        dlg = PluginsDialog(parent=self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Help menu handlers
    # ------------------------------------------------------------------

    def _on_documentation(self):
        """Open the built-in documentation viewer."""
        from esfex.visualization.panels.doc_viewer import DocViewerDialog

        dlg = DocViewerDialog(parent=self)
        dlg.exec()

    def _on_keyboard_shortcuts(self):
        """Show a dialog listing all keyboard shortcuts."""
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QTextBrowser,
            QVBoxLayout,
        )

        shortcuts = [
            ("Ctrl+N", tr("menu.new_scenario")),
            ("Ctrl+O", tr("menu.import_config")),
            ("Ctrl+S", tr("menu.save")),
            ("Ctrl+Shift+S", tr("menu.export_as")),
            ("Ctrl+R", tr("menu.load_results")),
            ("Ctrl+,", tr("menu.preferences")),
            ("F1", tr("menu.documentation")),
        ]
        rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 4px;'>"
            f"<code>{key}</code></td><td>{desc}</td></tr>"
            for key, desc in shortcuts
        )
        html = (
            "<table style='border-collapse:collapse;'>"
            "<tr style='border-bottom:1px solid #999;'>"
            f"<th style='text-align:left;padding:4px;'>{tr('shortcuts_dialog.shortcut')}</th>"
            f"<th style='text-align:left;padding:4px;'>{tr('shortcuts_dialog.action')}</th>"
            "</tr>"
            f"{rows}</table>"
        )

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("shortcuts_dialog.title"))
        dlg.resize(420, 320)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(html)
        layout.addWidget(browser)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok)
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)
        dlg.exec()

    def _on_about(self):
        """Show the About ESFEX dialog with logo."""
        from pathlib import Path

        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        from esfex import __author__, __version__

        logo_path = Path(__file__).resolve().parents[1] / "icons" / "esfex.png"

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("about_dialog.title"))
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)

        # Logo
        if logo_path.exists():
            logo_label = QLabel()
            pixmap = QPixmap(str(logo_path)).scaledToWidth(
                360, Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        # Text
        text_label = QLabel(
            tr("about_dialog.text", version=__version__, author=__author__),
        )
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setOpenExternalLinks(True)
        layout.addWidget(text_label)

        # OK button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(dlg.accept)
        layout.addWidget(btn_box)

        dlg.exec()

    # ------------------------------------------------------------------
    # Workflow wizard helpers
    # ------------------------------------------------------------------

    def _open_wizard(self, attr: str, factory):
        """Open a workflow wizard, reusing or replacing the existing one.

        If the wizard is already visible, raise and focus it instead of
        creating a duplicate.  If a previous instance exists but is hidden
        (closed), destroy it first to avoid accumulation.
        """
        existing = getattr(self, attr, None)
        if existing is not None:
            if existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            # Previous wizard was closed — clean up
            existing.close()
            existing.deleteLater()

        wizard = factory()
        setattr(self, attr, wizard)
        wizard.show()

    def _on_solar_rooftop_workflow(self):
        """Open the Solar Rooftop Analysis wizard."""
        from esfex.visualization.workflows.solar_rooftop_wizard import (
            SolarRooftopWizard,
        )
        self._open_wizard("_solar_wizard", lambda: SolarRooftopWizard(
            map_widget=self.map_widget, model=self.model, parent=self,
        ))

    def _on_otec_studio(self):
        """Open the OTEC Studio workbench (non-linear OTEX explorer)."""
        from esfex.visualization.workflows.otec_studio.window import (
            OTECStudioWindow,
        )
        self._open_wizard("_otec_studio", lambda: OTECStudioWindow(
            parent=self, model=self.model, map_widget=self.map_widget,
        ))

    def _on_wind_workflow(self):
        """Open the Wind Resource Assessment wizard."""
        from esfex.visualization.workflows.wind_wizard import WindWizard
        self._open_wizard("_wind_wizard", lambda: WindWizard(
            map_widget=self.map_widget, model=self.model, parent=self,
        ))

    def _on_solar_pv_workflow(self):
        """Open the Solar PV Potential Assessment wizard."""
        from esfex.visualization.workflows.solar_pv_wizard import (
            SolarPVWizard,
        )
        self._open_wizard("_solar_pv_wizard", lambda: SolarPVWizard(
            map_widget=self.map_widget, model=self.model, parent=self,
        ))

    def _on_grid_mapping_workflow(self):
        """Open the Grid Builder wizard."""
        from esfex.visualization.workflows.grid_mapping_wizard import (
            GridMappingWizard,
        )
        self._open_wizard("_grid_wizard", lambda: GridMappingWizard(
            map_widget=self.map_widget,
            model=self.model,
            all_states=self._all_states,
            switch_system_fn=self._switch_to_system,
            create_system_fn=self._create_system_for_wizard,
            parent=self,
        ))

    def _on_ev_v2g_workflow(self):
        """Open the EV & V2G Assessment wizard."""
        from esfex.visualization.workflows.ev_wizard import EVWizardDialog
        self._open_wizard("_ev_wizard", lambda: EVWizardDialog(
            map_widget=self.map_widget, model=self.model, parent=self,
        ))

    def _on_demand_estimation_workflow(self):
        """Open the Demand Estimation wizard."""
        from esfex.visualization.workflows.demand_estimation_wizard import (
            DemandEstimationWizard,
        )
        self._open_wizard(
            "_demand_estimation_wizard",
            lambda: DemandEstimationWizard(
                map_widget=self.map_widget,
                all_states=self._all_states,
                parent=self,
            ),
        )

    def _on_financial_workflow(self):
        """Open the Financial Analysis wizard."""
        from esfex.visualization.workflows.financial_wizard import FinancialWizard
        self._open_wizard("_financial_wizard", lambda: FinancialWizard(parent=self))

    def _on_risk_workflow(self):
        """Open the Risk & Resilience Analysis workbench."""
        from esfex.visualization.workflows.risk_wizard import RiskWorkbench
        self._open_wizard("_risk_wizard", lambda: RiskWorkbench(
            map_widget=self.map_widget, model=self.model,
            all_states=self._all_states, parent=self,
        ))

    def _on_new_scenario(self):
        """Clear everything and start a fresh empty scenario."""
        reply = QMessageBox.question(
            self,
            tr("menu.new_scenario"),
            "Discard current work and create a new empty scenario?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Remove all systems from the element tree
        for name in list(self._all_states.keys()):
            self.element_tree.remove_system(name)

        # Clear inter-system links
        self.model.clear_inter_system_links()
        self.element_tree.clear_inter_system_links()

        # Reset internal state
        self._all_states.clear()
        self._loaded_config = None
        self._raw_config_dict = None
        self._config_path = None
        self._last_output_dir = ""
        self._validated_ok = False
        self.toolbar.set_run_enabled(False)

        # Clear geo asset overlays
        for asset_id in list(self._geo_assets.keys()):
            self.map_widget.remove_geo_asset(asset_id)
            self.element_tree.remove_geo_asset(asset_id)
        self._geo_assets.clear()

        # Reset global settings and stochastic scenarios
        self.model.global_settings = GuiGlobalSettings()
        self.model.stochastic_scenarios = []

        # Clear undo history
        self.model.clear_undo()

        # Create a fresh empty system with a default node
        name = "System_1"
        new_state = GuiSystemState(name=name)
        self._all_states[name] = new_state
        self._current_system_name = name
        self.element_tree.add_system(name)
        self.element_tree.set_current_system(name)
        self.model.load_state(new_state)
        self.model.add_node(name="Node 0")

        # Update console namespace
        self.console.update_namespace(config=None, state=self.model.state)
        self._update_map_actions_state()

        self.setWindowTitle(tr("app.title"))

    def _on_import(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu.import_config"), "", "YAML Files (*.yaml *.yml)"
        )
        if path:
            self._load_config_file(path)

    def _on_import_system_from_config(self):
        """Import systems from a YAML config WITHOUT replacing existing ones."""
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu.import_system"), "", "YAML Files (*.yaml *.yml)",
        )
        if not path:
            return

        try:
            from esfex.config.loader import load_config

            config = load_config(path)
            new_states = config_to_gui_states(config)

            if not new_states:
                QMessageBox.information(
                    self, tr("common.info"),
                    tr("messages.no_systems_in_config"),
                )
                return

            # Preview the import so the user sees what will be added and
            # which names will conflict (= will trigger an individual
            # rename prompt) before any modal popups. Lets them cancel
            # if it's not the file they meant to import.
            conflicts = [s for s in new_states if s in self._all_states]
            non_conflicts = [s for s in new_states if s not in self._all_states]
            preview_lines = [f"Import {len(new_states)} system(s) from:\n  {path}\n"]
            if non_conflicts:
                preview_lines.append("Will be added directly:")
                for s in non_conflicts:
                    preview_lines.append(f"  • {s}")
                preview_lines.append("")
            if conflicts:
                preview_lines.append("Name conflicts — will prompt for a new name for each:")
                for s in conflicts:
                    preview_lines.append(f"  • {s}")
                preview_lines.append("")
            preview_lines.append("Proceed?")
            ret = QMessageBox.question(
                self, "Import preview",
                "\n".join(preview_lines),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                return

            # Ensure current editing state is saved before adding new systems
            if self._current_system_name and self._current_system_name in self._all_states:
                self._all_states[self._current_system_name] = self.model.state

            added = []
            for sys_name, gui_state in new_states.items():
                final_name = sys_name
                # Handle name conflicts by appending suffix
                if final_name in self._all_states:
                    final_name, ok = QInputDialog.getText(
                        self, tr("messages.name_conflict_title"),
                        tr("messages.name_conflict_prompt", name=sys_name),
                    )
                    if not ok or not final_name.strip():
                        continue
                    final_name = final_name.strip()
                    if final_name in self._all_states:
                        QMessageBox.warning(
                            self, tr("messages.duplicate_name_title"),
                            tr("messages.duplicate_name", name=final_name),
                        )
                        continue
                    gui_state.name = final_name

                self._all_states[final_name] = gui_state
                self.element_tree.add_system(final_name)
                added.append(final_name)

            if added:
                # Switch to the first newly added system
                self._switch_to_system(added[0])
                self._update_map_actions_state()

                # Merge inter-system links from the new config
                for lk in config_to_inter_system_links(config):
                    # Only add links whose both systems exist in the studio
                    if lk.from_system in self._all_states and lk.to_system in self._all_states:
                        self.model.add_inter_system_link(
                            link_id=lk.link_id,
                            link_type=lk.link_type,
                            from_system=lk.from_system,
                            to_system=lk.to_system,
                            from_node=lk.from_node,
                            to_node=lk.to_node,
                            capacity_mw=lk.capacity_mw,
                            investment_cost=lk.investment_cost,
                            max_investment_mw=lk.max_investment_mw,
                            loss_factor=lk.loss_factor,
                            distance_km=lk.distance_km,
                            cost_per_mw_km=lk.cost_per_mw_km,
                            waypoints=lk.waypoints,
                            from_endpoint=lk.from_endpoint,
                            to_endpoint=lk.to_endpoint,
                        )

                QMessageBox.information(
                    self, tr("common.info"),
                    tr("messages.systems_imported", names=", ".join(added)),
                )

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, tr("common.error"),
                f"Failed to import systems:\n{e}",
            )

    def _on_import_geo_asset(self):
        """Import a geo file as a reference overlay (GeoJSON/SHP/KML/KMZ/GPKG)."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu.import_geo"),
            "",
            "Geo Files (*.geojson *.json *.shp *.kml *.kmz *.gpkg);;"
            "GeoJSON (*.geojson *.json);;Shapefile (*.shp);;"
            "KML/KMZ (*.kml *.kmz);;GeoPackage (*.gpkg)",
        )
        if not path:
            return

        try:
            import os

            name = os.path.splitext(os.path.basename(path))[0]

            ext = Path(path).suffix.lower()
            if ext not in (".geojson", ".json"):
                # Formats requiring geopandas
                try:
                    import geopandas  # noqa: F401
                except ImportError:
                    QMessageBox.warning(
                        self, tr("messages.missing_dep_title"),
                        f"'{ext}' support requires geopandas.\n"
                        "Install with: pip install geopandas",
                    )
                    return

            geojson_str, geojson_data = _read_geo_file(path)

            asset_id = f"geo_{self._next_geo_asset_id}"
            self._next_geo_asset_id += 1
            self._geo_assets[asset_id] = _GeoAssetInfo(
                name=name,
                geojson_data=geojson_data,
                file_path=path,
                target_system="",
            )

            # Assign a color based on index
            colors = ["#e67e22", "#9b59b6", "#1abc9c", "#e74c3c",
                       "#3498db", "#2ecc71", "#f39c12", "#34495e"]
            color = colors[int(asset_id.split("_")[1]) % len(colors)]

            info_text = os.path.basename(path)

            self.map_widget.add_geo_asset(asset_id, geojson_str, name, color)
            self.element_tree.add_geo_asset(asset_id, name, info_text)

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, tr("common.error"),
                f"Failed to import geo asset:\n{e}",
            )

    def _on_save(self):
        """Save to the current config path (Ctrl+S)."""
        if self._config_path:
            self._save_config_file(self._config_path)
        else:
            self._on_export()

    # ------------------------------------------------------------------
    # Unsaved-changes tracking, autosave, close prompt
    # ------------------------------------------------------------------

    def _mark_modified(self):
        """Slot: data was mutated by user (edit, undo, redo)."""
        if not self._modified:
            self._modified = True
            self._update_window_title()

    def _clear_modified(self):
        """Called after successful save or load."""
        if self._modified:
            self._modified = False
            self._update_window_title()

    def _update_window_title(self):
        base = "ESFEX"
        if self._config_path:
            base = f"{Path(self._config_path).name} — ESFEX"
        if self._modified:
            base = "• " + base  # bullet prefix; • is clearer than *
        self.setWindowTitle(base)

    def _on_autosave(self):
        """Periodic autosave handler. Writes silently — no dialogs.

        Behaviour:
        * No unsaved changes → no-op.
        * Has _config_path → write to that path, clear modified flag.
        * No _config_path → write to ~/.cache/esfex/autosave.yaml
          (recoverable on next launch).
        Does NOT call _save_config_file because the latter runs
        pre-save validation that can show a blocking dialog — autosave
        must never block the user.
        """
        if not self._modified:
            return
        self._ensure_system_exists()
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state
        if self._loaded_config is None:
            self._loaded_config = self._create_default_config()

        if self._config_path:
            target = Path(self._config_path)
            is_cache = False
        else:
            cache_dir = Path.home() / ".cache" / "esfex"
            cache_dir.mkdir(parents=True, exist_ok=True)
            target = cache_dir / "autosave.yaml"
            is_cache = True

        try:
            gui_state_to_yaml(
                self._all_states, self._loaded_config, str(target),
                inter_system_links=self.model.inter_system_links,
                global_settings=self.model.global_settings,
                stochastic_scenarios=self.model.stochastic_scenarios,
            )
            if not is_cache:
                # Wrote to the canonical config path — clean state.
                self._clear_modified()
            self.statusBar().showMessage(f"Autosaved to {target}", 3000)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Autosave failed; data still in memory",
            )
            self.statusBar().showMessage("Autosave failed (see log)", 5000)

    def _populate_recent_menu(self):
        """Rebuild the File→Open Recent submenu just before it's shown."""
        from esfex.visualization.preferences import get_recent_files
        self._recent_menu.clear()
        recents = get_recent_files()  # already filters non-existent
        if not recents:
            placeholder = QAction("(no recent files)", self)
            placeholder.setEnabled(False)
            self._recent_menu.addAction(placeholder)
            return
        for path in recents:
            label = Path(path).name
            full = path
            act = QAction(f"{label}\t{full}", self)
            # Capture `full` by default arg so each lambda binds its own path.
            act.triggered.connect(
                lambda _checked=False, p=full: self._open_recent_file(p)
            )
            self._recent_menu.addAction(act)
        self._recent_menu.addSeparator()
        clear_act = QAction("Clear list", self)
        clear_act.triggered.connect(self._clear_recent_files)
        self._recent_menu.addAction(clear_act)

    def _open_recent_file(self, path: str):
        """Load a config from the Recent Files menu (with unsaved check)."""
        if self._modified:
            ret = QMessageBox.question(
                self, "Unsaved changes",
                "Open another config? Unsaved changes will be lost.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
        self._load_config_file(path)

    def _clear_recent_files(self):
        """Wipe the recent-files list from preferences."""
        from esfex.visualization.preferences import (
            load_preferences, save_preferences,
        )
        prefs = load_preferences()
        prefs["recent_files"] = []
        save_preferences(prefs)

    def closeEvent(self, event):
        """Prompt to save unsaved changes and confirm cancellation of any
        in-flight optimization before closing the window."""
        from PySide6.QtWidgets import QMessageBox

        if self._modified:
            ret = QMessageBox.question(
                self, "Unsaved changes",
                "There are unsaved changes. Save before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if ret == QMessageBox.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.Save:
                self._on_save()
                # Save may have been cancelled (no path picked) — re-check.
                if self._modified:
                    event.ignore()
                    return

        # If an optimization run is in-flight, confirm before killing it —
        # a 25-year horizon can take 4+ h and an accidental window close
        # would wipe out the progress with no warning.
        run_output = getattr(self, "run_output", None)
        if run_output is not None and run_output.is_running():
            ret = QMessageBox.warning(
                self, "Optimization in progress",
                "An optimization run is currently in progress. "
                "Closing the window will cancel it and lose all unsaved progress.\n\n"
                "Are you sure you want to close?",
                QMessageBox.Cancel | QMessageBox.Close,
                QMessageBox.Cancel,  # default: keep the run alive
            )
            if ret == QMessageBox.Cancel:
                event.ignore()
                return
            # Confirmed cancellation — stop the subprocess so its PTY
            # child doesn't outlive the GUI.
            run_output.stop()

        event.accept()

    def _on_export(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, tr("menu.export_as"), "", "YAML Files (*.yaml *.yml)"
        )
        if path:
            # Ensure .yaml extension
            if not path.endswith((".yaml", ".yml")):
                path += ".yaml"
            self._config_path = path
            self._save_config_file(path)

    # ------------------------------------------------------------------
    # Map bridge slots
    # ------------------------------------------------------------------

    def _on_map_ready(self):
        """Map initialised -- load config if one was provided, or rebuild after crash."""
        if self._loaded_config is not None:
            self.statusBar().showMessage("Loading configuration...")
            QTimer.singleShot(0, self._deferred_populate)
        elif getattr(self.map_widget, '_crash_recovered', False):
            # Map was reloaded after WebEngine render process crash — rebuild
            self.map_widget._crash_recovered = False
            self.statusBar().showMessage("Map recovered from crash. Rebuilding...")
            QTimer.singleShot(50, self._on_state_loaded)
        else:
            # Honor `general.startup` preference. Default "empty" preserves
            # the previous behaviour. "last_config" reopens the newest
            # entry from recent_files (filtered for existence by
            # get_recent_files).
            from esfex.visualization.preferences import (
                get_preference, get_recent_files,
            )
            startup = get_preference(
                self._user_prefs, "general", "startup", "empty",
            )
            if startup == "last_config":
                recents = get_recent_files()
                if recents:
                    last = recents[0]
                    self.statusBar().showMessage(f"Reopening {last}…")
                    QTimer.singleShot(
                        0, lambda p=last: self._load_config_file(p),
                    )

    def _deferred_populate(self):
        """Run ``_populate_from_config`` after the first paint."""
        self._populate_from_config(
            self._loaded_config, raw_dict=self._raw_config_dict,
        )
        self.console.update_namespace(
            config=self._loaded_config, state=self.model.state,
        )
        self.statusBar().clearMessage()

    def _on_line_drawn(self, geojson_str: str):
        data = json.loads(geojson_str)
        props = data.get("properties", {})
        from_node = props.get("from_node")
        to_node = props.get("to_node")
        if from_node is not None and to_node is not None:
            # Inline validation
            if from_node == to_node:
                QMessageBox.warning(self, tr("messages.invalid_line_title"), tr("messages.self_loop_msg"))
                return
            node_indices = {n.index for n in self.model.state.nodes}
            if from_node not in node_indices or to_node not in node_indices:
                QMessageBox.warning(self, tr("messages.invalid_line_title"), tr("messages.no_nodes_msg"))
                return
            self.model.add_line(from_node, to_node, capacity_mw=100.0)

    def _on_zone_drawn(self, geojson_str: str):
        data = json.loads(geojson_str)
        coords = data.get("geometry", {}).get("coordinates", [[]])
        if coords and coords[0]:
            polygon = [GeoPoint(c[1], c[0]) for c in coords[0]]  # GeoJSON is [lng, lat]
            node = self.model.state.nodes[0].index if self.model.state.nodes else 0
            self.model.add_zone(
                name=f"Zone {len(self.model.state.development_zones)}",
                technology="Solar",
                polygon=polygon,
                node=node,
            )

    def _update_breadcrumb(self, element_type: str, element_id: str):
        """Set the breadcrumb label to the element's hierarchical path."""
        self._breadcrumb_label.setText(
            self.element_tree.breadcrumb(element_type, element_id)
        )

    def _refresh_system_summary(self, *_args):
        """Update the strip's system summary with current cardinalities.

        Accepts (and ignores) any positional args so it can be wired to
        signals with arbitrary signatures. Reads counts in O(1) per
        collection — cheap to call on every structural mutation.
        """
        sys_name = self._current_system_name or ""
        s = self.model.state
        n_nodes = len(s.nodes)
        n_buses = len(s.buses)
        n_gens = len(s.generators)
        n_trafos = len(s.transformers)
        # transmission_lines includes wire-lines (decorative). Surface
        # the count of *real* (logical) lines for accuracy.
        n_lines = sum(
            1 for ln in s.transmission_lines
            if not (
                (ln.from_endpoint
                 and ln.from_endpoint.element_type in (
                     "generator", "battery", "electrolyzer",
                     "transformer", "acdc_converter", "freq_converter",
                 ))
                or (ln.to_endpoint
                    and ln.to_endpoint.element_type in (
                        "generator", "battery", "electrolyzer",
                        "transformer", "acdc_converter", "freq_converter",
                    ))
            )
        )
        if not sys_name:
            self._system_summary_label.clear()
            return
        # Reuse the per-category labels already translated for the
        # element tree (en/es/ja); no new translation keys needed.
        parts = [
            f"{n_nodes} {tr('tree.nodes')}",
            f"{n_buses} {tr('tree.buses')}",
            f"{n_gens} {tr('tree.generators')}",
            f"{n_trafos} {tr('tree.transformers')}",
            f"{n_lines} {tr('tree.transmission_lines')}",
        ]
        self._system_summary_label.setText(
            f"<b>{sys_name}</b> — " + " · ".join(parts)
        )

    def _on_element_selected(self, element_type: str, element_id: str):
        # If the element belongs to another system (prefixed ID like
        # "Cuba/54"), switch to that system quietly — update the model
        # state and tree but do NOT rebuild the map or zoom.
        if "/" in element_id:
            sys_name, raw_id = element_id.rsplit("/", 1)
            if (sys_name in self._all_states
                    and sys_name != self._current_system_name):
                self._switch_to_system_quiet(sys_name)
            element_id = raw_id

        # Inter-system links travel through the same map layer as
        # transmission lines (add_transmission_line) so the JS sends
        # element_type='line' for them. Detect by id prefix and
        # re-route to the dedicated form so the properties panel
        # surfaces capacity / loss / cost fields instead of failing.
        if element_type == "line" and element_id.startswith("islink_"):
            element_type = "inter_system_link"

        self.element_tree.select_element(element_type, element_id)
        self.properties_panel.show_element(element_type, element_id)
        self._update_breadcrumb(element_type, element_id)

    def _on_element_deselected(self):
        if self._zone_form is not None:
            self._zone_form.stop_editing()
        if self._line_form is not None:
            self._line_form.stop_editing()
        if self._fuel_route_form is not None:
            self._fuel_route_form.stop_editing()
        self.properties_panel.clear()
        self._breadcrumb_label.clear()


    def _nearest_node_to(self, lat: float, lng: float) -> int:
        """Return the index of the node geographically closest to *(lat, lng)*.

        Uses stored node centroids first, then falls back to bus positions.
        """
        import math

        nodes = self.model.state.nodes
        if not nodes:
            return 0

        best_idx = nodes[0].index
        best_dist = float("inf")
        for node in nodes:
            # Prefer stored centroid (from config node_coordinates)
            if node.centroid_lat != 0.0 or node.centroid_lng != 0.0:
                dlat = lat - node.centroid_lat
                dlng = lng - node.centroid_lng
                d = math.sqrt(dlat * dlat + dlng * dlng)
                if d < best_dist:
                    best_dist = d
                    best_idx = node.index
                continue
            # Fallback: bus positions
            buses = self.model.get_buses_for_node(node.index)
            for bus in buses:
                if bus.latitude == 0.0 and bus.longitude == 0.0:
                    continue
                dlat = lat - bus.latitude
                dlng = lng - bus.longitude
                d = math.sqrt(dlat * dlat + dlng * dlng)
                if d < best_dist:
                    best_dist = d
                    best_idx = node.index
        return best_idx

    def _create_element_for_mode(
        self, mode: str, lat: float, lng: float
    ) -> tuple[str, str] | tuple[None, None]:
        """Create an element for *mode* at *(lat, lng)*.

        Returns ``(element_type, element_id)`` on success,
        ``(None, None)`` on failure.
        """
        nearest = self._nearest_node_to(lat, lng)

        if mode == "add_fuel_entry":
            existing_names = {fe.name for fe in self.model.state.fuel_entry_points}
            n = len(self.model.state.fuel_entry_points)
            name = f"Fuel Entry {n}"
            while name in existing_names:
                n += 1
                name = f"Fuel Entry {n}"
            idx = self.model.add_fuel_entry(
                name=name, fuels=[], node=nearest, lat=lat, lng=lng,
            )
            return "fuel_entry", str(idx)

        if mode == "add_generator":
            existing_keys = {g.unit_key for g in self.model.state.generators.values()}
            idx = len(existing_keys) + 1
            while f"unit_{idx}" in existing_keys:
                idx += 1
            existing_names = {g.name for g in self.model.state.generators.values()}
            n = len(existing_keys) + 1
            name = f"Generator {n}"
            while name in existing_names:
                n += 1
                name = f"Generator {n}"
            inst_id = self.model.add_generator_instance(
                unit_key=f"unit_{idx}",
                name=name,
                gen_type="Renewable",
                fuel="None",
                node=nearest,
                rated_power=0.0,
                latitude=lat,
                longitude=lng,
            )
            return "generator", inst_id

        if mode == "add_battery":
            existing_keys = {b.unit_key for b in self.model.state.batteries.values()}
            idx = len(self.model.state.generators) + len(existing_keys) + 1
            while f"unit_{idx}" in existing_keys:
                idx += 1
            existing_names = {b.name for b in self.model.state.batteries.values()}
            n = len(existing_keys) + 1
            name = f"Storage {n}"
            while name in existing_names:
                n += 1
                name = f"Storage {n}"
            inst_id = self.model.add_battery_instance(
                unit_key=f"unit_{idx}",
                name=name,
                node=nearest,
                rated_power=0.0,
                capacity=0.0,
                MaxChargePower=0.0,
                MaxDischargePower=0.0,
                latitude=lat,
                longitude=lng,
            )
            return "battery", inst_id

        if mode == "add_transformer":
            existing_names = {t.name for t in self.model.state.transformers}
            n = len(self.model.state.transformers)
            name = f"Transformer {n}"
            while name in existing_names:
                n += 1
                name = f"Transformer {n}"
            idx = self.model.add_transformer(
                name=name, from_node=nearest, to_node=nearest,
                latitude=lat, longitude=lng,
            )
            return "transformer", str(idx)

        if mode == "add_electrolyzer":
            existing_keys = {
                e.unit_key for e in self.model.state.electrolyzers.values()
            }
            idx = len(existing_keys) + 1
            while f"electrolyzer_{idx}" in existing_keys:
                idx += 1
            existing_names = {e.name for e in self.model.state.electrolyzers.values()}
            n = len(existing_keys) + 1
            name = f"Electrolyzer {n}"
            while name in existing_names:
                n += 1
                name = f"Electrolyzer {n}"
            inst_id = self.model.add_electrolyzer_instance(
                unit_key=f"electrolyzer_{idx}",
                name=name,
                node=nearest,
                latitude=lat,
                longitude=lng,
            )
            return "electrolyzer", inst_id

        if mode == "add_acdc_converter":
            existing_names = {c.name for c in self.model.state.acdc_converters}
            n = len(self.model.state.acdc_converters)
            name = f"AC/DC Conv. {n}"
            while name in existing_names:
                n += 1
                name = f"AC/DC Conv. {n}"
            idx = self.model.add_acdc_converter(
                name=name, from_node=nearest, to_node=nearest,
                latitude=lat, longitude=lng,
            )
            return "acdc_converter", str(idx)

        if mode == "add_freq_converter":
            existing_names = {c.name for c in self.model.state.freq_converters}
            n = len(self.model.state.freq_converters)
            name = f"Freq. Conv. {n}"
            while name in existing_names:
                n += 1
                name = f"Freq. Conv. {n}"
            idx = self.model.add_freq_converter(
                name=name, from_node=nearest, to_node=nearest,
                latitude=lat, longitude=lng,
            )
            return "freq_converter", str(idx)

        if mode == "add_bus":
            bus_id = self.model.add_bus(
                parent_node=nearest,
                latitude=lat,
                longitude=lng,
            )
            return "bus", bus_id

        if mode == "add_fuel_storage":
            existing_names = {fs.name for fs in self.model.state.fuel_storages.values()}
            n = len(self.model.state.fuel_storages)
            name = f"Fuel Storage {n}"
            while name in existing_names:
                n += 1
                name = f"Fuel Storage {n}"
            sid = self.model.add_fuel_storage(
                name=name, fuel="", node=nearest,
                latitude=lat, longitude=lng,
            )
            return "fuel_storage", sid

        return None, None

    def _on_fuel_entry_placed(self, lat: float, lng: float):
        self._ensure_system_exists()
        try:
            etype, eid = self._create_element_for_mode("add_fuel_entry", lat, lng)
            if etype:
                self.properties_panel.show_element(etype, eid)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, tr("common.error"), tr("messages.failed_add_fuel_entry", e=e))

    # Equipment types that get the auto-connect chain on placement
    _AUTO_CONNECT_TYPES = {
        "generator", "battery", "electrolyzer",
        "acdc_converter", "freq_converter",
    }

    def _on_centroid_pick_requested(self, node_index: int):
        """User clicked 'Pick on map' in the node form."""
        self._pick_centroid_node = node_index
        self.map_widget.set_mode("pick_centroid")

    def _on_element_placed(self, mode: str, lat: float, lng: float):
        """Unified handler for placing generators, batteries, transformers, fuel storages.

        For generators, batteries, and electrolyzers the full connection
        chain is created automatically:

            equipment → line → bus_lv → line → transformer → line → bus_hv
        """
        # ── Centroid pick mode ──
        if mode == "pick_centroid":
            node_idx = self._pick_centroid_node
            self._pick_centroid_node = None
            self.toolbar.reset_mode()
            if node_idx is not None:
                self.model.update_node(node_idx, centroid_lat=lat, centroid_lng=lng)
                self._node_form.set_centroid(lat, lng)
            return

        self._ensure_system_exists()
        try:
            etype, eid = self._create_element_for_mode(mode, lat, lng)
            if etype is None:
                return
            # Auto-connect chain for equipment types
            if etype in self._AUTO_CONNECT_TYPES and self.model.state.buses:
                from esfex.visualization.data.auto_complete import (
                    auto_connect_single_equipment,
                )
                auto_connect_single_equipment(
                    self.model, etype, eid, lat, lng,
                )
            self.properties_panel.show_element(etype, eid)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, tr("common.error"), tr("messages.failed_add_element", e=e))

    # ── Auto-connect: split line when element placed/dropped on it ──

    def _split_line_with_element(
        self,
        line_type: str,
        line_id: str,
        segment_index: int,
        element_type: str,
        element_id: str,
        lat: float,
        lng: float,
    ) -> bool:
        """Split a transmission line or fuel route at *segment_index*.

        The original line is removed and replaced by two new lines, both
        connecting through the element identified by *(element_type,
        element_id)*.

        Returns ``True`` if the split succeeded.
        """
        from esfex.visualization.data.gui_model import EndpointRef, GeoPoint

        if line_type == "transmission":
            return self._split_transmission_line(
                line_id, segment_index, element_type, element_id,
            )
        if line_type == "fuel_route":
            return self._split_fuel_route(
                line_id, segment_index, element_type, element_id,
            )
        return False

    def _split_transmission_line(
        self,
        line_id: str,
        segment_index: int,
        element_type: str,
        element_id: str,
    ) -> bool:
        from esfex.visualization.data.connectivity_rules import is_valid_connection
        from esfex.visualization.data.gui_model import EndpointRef, GeoPoint

        original = None
        for ln in self.model.state.transmission_lines:
            if ln.line_id == line_id:
                original = ln
                break
        if original is None:
            return False

        # Validate connections in both directions
        from_type = original.from_endpoint.element_type if original.from_endpoint else "node"
        to_type = original.to_endpoint.element_type if original.to_endpoint else "node"

        if not is_valid_connection(from_type, element_type):
            QMessageBox.warning(
                self,
                tr("messages.invalid_line_title"),
                tr("messages.invalid_line_msg",
                   from_type=from_type, to_type=element_type),
            )
            return False
        if not is_valid_connection(element_type, to_type):
            QMessageBox.warning(
                self,
                tr("messages.invalid_line_title"),
                tr("messages.invalid_line_msg",
                   from_type=element_type, to_type=to_type),
            )
            return False

        # Split waypoints
        wps1 = [GeoPoint(wp.lat, wp.lng) for wp in original.waypoints[:segment_index]]
        wps2 = [GeoPoint(wp.lat, wp.lng) for wp in original.waypoints[segment_index:]]

        new_ref = EndpointRef(element_type, element_id)
        new_node = self.model.resolve_endpoint_node(new_ref)
        if new_node is None:
            new_node = 0

        # Preserve properties
        capacity = original.capacity_mw

        # Remove original then create two new lines
        self.model.remove_line(line_id)

        self.model.add_line(
            from_node=original.from_node,
            to_node=new_node,
            capacity_mw=capacity,
            waypoints=wps1,
            from_endpoint=original.from_endpoint,
            to_endpoint=new_ref,
        )
        self.model.add_line(
            from_node=new_node,
            to_node=original.to_node,
            capacity_mw=capacity,
            waypoints=wps2,
            from_endpoint=EndpointRef(element_type, element_id),
            to_endpoint=original.to_endpoint,
        )
        return True

    def _split_fuel_route(
        self,
        route_id: str,
        segment_index: int,
        element_type: str,
        element_id: str,
    ) -> bool:
        from esfex.visualization.data.gui_model import EndpointRef, GeoPoint

        _allowed = {"node", "fuel_entry", "fuel_storage"}
        if element_type not in _allowed:
            QMessageBox.warning(
                self,
                tr("messages.invalid_route_title"),
                tr("messages.invalid_route_msg"),
            )
            return False

        original = None
        for rt in self.model.state.fuel_transport_routes:
            if rt.route_id == route_id:
                original = rt
                break
        if original is None:
            return False

        wps1 = [GeoPoint(wp.lat, wp.lng) for wp in original.waypoints[:segment_index]]
        wps2 = [GeoPoint(wp.lat, wp.lng) for wp in original.waypoints[segment_index:]]

        new_ref = EndpointRef(element_type, element_id)
        new_node = self.model.resolve_endpoint_node(new_ref)
        if new_node is None:
            new_node = 0

        capacity = original.capacity
        fuels = list(original.fuels)

        self.model.remove_fuel_route(route_id)

        self.model.add_fuel_route(
            from_node=original.from_node,
            to_node=new_node,
            fuels=fuels,
            capacity=capacity,
            waypoints=wps1,
            from_endpoint=original.from_endpoint,
            to_endpoint=new_ref,
        )
        self.model.add_fuel_route(
            from_node=new_node,
            to_node=original.to_node,
            fuels=fuels,
            capacity=capacity,
            waypoints=wps2,
            from_endpoint=EndpointRef(element_type, element_id),
            to_endpoint=original.to_endpoint,
        )
        return True

    def _element_display_name(self, element_type: str, element_id: str) -> str:
        """Return a human-readable label for an element, e.g. 'Generator unit_1'."""
        label = element_type.replace("_", " ").title()
        s = self.model.state
        if element_type == "generator":
            inst = s.generators.get(element_id)
            if inst and inst.name:
                return f"{label} '{inst.name}'"
        elif element_type == "battery":
            inst = s.batteries.get(element_id)
            if inst and inst.name:
                return f"{label} '{inst.name}'"
        elif element_type == "electrolyzer":
            inst = s.electrolyzers.get(element_id)
            if inst and inst.name:
                return f"{label} '{inst.name}'"
        elif element_type == "bus":
            bus = s.buses.get(element_id)
            if bus and bus.name:
                return f"{label} '{bus.name}'"
        return f"{label} {element_id}"

    def _confirm_line_split(
        self, element_type: str, element_id: str, line_id: str,
    ) -> bool:
        """Show a confirmation dialog before splitting a line."""
        element_name = self._element_display_name(element_type, element_id)
        reply = QMessageBox.question(
            self,
            tr("messages.confirm_split_title"),
            tr("messages.confirm_split_msg",
               element=element_name, line=line_id),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_element_placed_on_line(
        self,
        mode: str,
        lat: float,
        lng: float,
        line_type: str,
        line_id: str,
        segment_index: int,
    ):
        """Handle placement of a new element onto an existing line/route.

        Equipment types (generators, batteries, electrolyzers) always use the
        auto-connect chain instead of splitting the line — they must never be
        connected directly to an HV bus.
        """
        self._ensure_system_exists()
        try:
            etype, eid = self._create_element_for_mode(mode, lat, lng)
            if etype is None:
                return
            # Equipment types → auto-connect chain (same as normal placement)
            if etype in self._AUTO_CONNECT_TYPES and self.model.state.buses:
                from esfex.visualization.data.auto_complete import (
                    auto_connect_single_equipment,
                )
                auto_connect_single_equipment(
                    self.model, etype, eid, lat, lng,
                )
            elif self._confirm_line_split(etype, eid, line_id):
                self._split_line_with_element(
                    line_type, line_id, segment_index, etype, eid, lat, lng,
                )
            self.properties_panel.show_element(etype, eid)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, tr("common.error"), str(e))

    def _on_element_dropped_on_line(
        self,
        element_type: str,
        element_id: str,
        lat: float,
        lng: float,
        line_type: str,
        line_id: str,
        segment_index: int,
    ):
        """Handle drag of an existing element onto a line/route."""
        # Guard: skip if element is already an endpoint of this line
        if line_type == "transmission":
            for ln in self.model.state.transmission_lines:
                if ln.line_id == line_id:
                    if (ln.from_endpoint
                            and ln.from_endpoint.element_type == element_type
                            and ln.from_endpoint.element_id == element_id):
                        break
                    if (ln.to_endpoint
                            and ln.to_endpoint.element_type == element_type
                            and ln.to_endpoint.element_id == element_id):
                        break
                    # Not an endpoint — ask user then split
                    self._on_element_dragged(element_type, element_id, lat, lng)
                    if self._confirm_line_split(element_type, element_id, line_id):
                        self._split_line_with_element(
                            line_type, line_id, segment_index,
                            element_type, element_id, lat, lng,
                        )
                    return

        elif line_type == "fuel_route":
            for rt in self.model.state.fuel_transport_routes:
                if rt.route_id == line_id:
                    if (rt.from_endpoint
                            and rt.from_endpoint.element_type == element_type
                            and rt.from_endpoint.element_id == element_id):
                        break
                    if (rt.to_endpoint
                            and rt.to_endpoint.element_type == element_type
                            and rt.to_endpoint.element_id == element_id):
                        break
                    self._on_element_dragged(element_type, element_id, lat, lng)
                    if self._confirm_line_split(element_type, element_id, line_id):
                        self._split_line_with_element(
                            line_type, line_id, segment_index,
                            element_type, element_id, lat, lng,
                        )
                    return

        # Fallback: just a normal drag (element was already on this line)
        self._on_element_dragged(element_type, element_id, lat, lng)

    def _on_add_fuel_requested(self):
        """Show dialog to create a new fuel (GuiFuel)."""
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, tr("messages.new_fuel_title"), tr("messages.new_fuel_prompt"))
        if not ok or not name.strip():
            return
        name = name.strip()
        fuel_id = name
        if fuel_id in self.model.state.fuels:
            QMessageBox.warning(
                self, tr("messages.duplicate_name_title"),
                tr("messages.duplicate_name", name=fuel_id),
            )
            return
        self.model.add_fuel(fuel_id=fuel_id, name=name)
        self.properties_panel.show_element("fuel", fuel_id)

    def _on_element_dragged(self, element_type: str, element_id: str,
                            lat: float, lng: float):
        """Handle drag of a generator/battery/fuel_entry/transformer marker."""
        if element_type == "generator":
            inst = self.model.state.generators.get(element_id)
            if inst:
                inst.latitude = lat
                inst.longitude = lng
        elif element_type == "battery":
            inst = self.model.state.batteries.get(element_id)
            if inst:
                inst.latitude = lat
                inst.longitude = lng
        elif element_type == "fuel_entry":
            try:
                idx = int(element_id)
                if 0 <= idx < len(self.model.state.fuel_entry_points):
                    fe = self.model.state.fuel_entry_points[idx]
                    fe.coordinate = GeoPoint(lat, lng, fe.name)
            except (ValueError, IndexError):
                pass
        elif element_type == "transformer":
            try:
                idx = int(element_id)
                if idx < len(self.model.state.transformers):
                    tr = self.model.state.transformers[idx]
                    tr.latitude = lat
                    tr.longitude = lng
            except (ValueError, IndexError):
                pass
        elif element_type == "bus":
            bus = self.model.state.buses.get(element_id)
            if bus:
                bus.latitude = lat
                bus.longitude = lng
        elif element_type == "electrolyzer":
            inst = self.model.state.electrolyzers.get(element_id)
            if inst:
                inst.latitude = lat
                inst.longitude = lng
        elif element_type == "acdc_converter":
            try:
                idx = int(element_id)
                if idx < len(self.model.state.acdc_converters):
                    self.model.state.acdc_converters[idx].latitude = lat
                    self.model.state.acdc_converters[idx].longitude = lng
            except (ValueError, IndexError):
                pass
        elif element_type == "freq_converter":
            try:
                idx = int(element_id)
                if idx < len(self.model.state.freq_converters):
                    self.model.state.freq_converters[idx].latitude = lat
                    self.model.state.freq_converters[idx].longitude = lng
            except (ValueError, IndexError):
                pass
        elif element_type == "fuel_storage":
            inst = self.model.state.fuel_storages.get(element_id)
            if inst:
                inst.latitude = lat
                inst.longitude = lng
        # Update line endpoints referencing this element
        self._update_lines_for_element(element_type, element_id, lat, lng)
        # Update fuel route endpoints referencing this element
        self._update_fuel_routes_for_element(element_type, element_id, lat, lng)

    def _update_lines_for_element(self, element_type: str, element_id: str,
                                   lat: float, lng: float):
        """Update line endpoints snapped to a moved element."""
        for ln in self.model.state.transmission_lines:
            if (ln.from_endpoint
                    and ln.from_endpoint.element_type == element_type
                    and ln.from_endpoint.element_id == element_id):
                self.map_widget.update_line_endpoint(ln.line_id, 0, lat, lng)
            if (ln.to_endpoint
                    and ln.to_endpoint.element_type == element_type
                    and ln.to_endpoint.element_id == element_id):
                self.map_widget.update_line_endpoint(ln.line_id, -1, lat, lng)

    # ------------------------------------------------------------------
    # Tree slots
    # ------------------------------------------------------------------

    def _on_tree_element_selected(self, element_type: str, element_id: str):
        element_id = self._resolve_cross_system_id(element_id)
        # Mirror the dispatch in _on_element_selected: tree items for
        # inter-system links arrive as element_type='inter_system_link'
        # (their own root), so no remap needed here — keep symmetry
        # only for the line→islink case that comes from the map.
        if element_type == "line" and element_id.startswith("islink_"):
            element_type = "inter_system_link"
        self.properties_panel.show_element(element_type, element_id)
        self._update_breadcrumb(element_type, element_id)
        # Sync selection to SLD if it is the active view
        if self._view_stack.currentIndex() == 1:
            self.sld_widget.select_element(element_type, element_id)

    def _on_multi_element_selected(self, element_type: str, element_ids: list):
        """Show multi-edit form for several elements of the same type."""
        element_ids = [self._resolve_cross_system_id(i) for i in element_ids]
        self.properties_panel.show_elements(element_type, element_ids)
        if element_ids:
            self._update_breadcrumb(element_type, element_ids[0])

    def _on_tree_element_focused(self, element_type: str, element_id: str):
        """Center the map/SLD on the double-clicked element."""
        element_id = self._resolve_cross_system_id(element_id)
        if self._view_stack.currentIndex() == 1:
            # SLD active → highlight in SLD
            self.sld_widget.select_element(element_type, element_id)
        else:
            # Map active → center map
            pos = self._resolve_element_position(element_type, element_id)
            if pos:
                lat, lng = pos
                self.map_widget.set_map_view(lat, lng, 12)
        self.properties_panel.show_element(element_type, element_id)
        self._update_breadcrumb(element_type, element_id)

    def _resolve_element_position(
        self, element_type: str, element_id: str,
    ) -> tuple[float, float] | None:
        """Get (lat, lng) for any element type."""
        state = self.model.state

        if element_type == "node":
            pass  # Nodes are abstract — no geographic position

        elif element_type == "generator":
            inst = state.generators.get(element_id)
            if inst:
                return (inst.latitude, inst.longitude)

        elif element_type == "battery":
            inst = state.batteries.get(element_id)
            if inst:
                return (inst.latitude, inst.longitude)

        elif element_type == "line":
            for ln in state.transmission_lines:
                if ln.line_id == element_id:
                    coords = self._build_line_coords(ln)
                    if coords:
                        avg_lat = sum(c[0] for c in coords) / len(coords)
                        avg_lng = sum(c[1] for c in coords) / len(coords)
                        return (avg_lat, avg_lng)
                    break

        elif element_type == "zone":
            try:
                idx = int(element_id)
                if idx < len(state.development_zones):
                    zone = state.development_zones[idx]
                    if zone.polygon:
                        avg_lat = sum(p.lat for p in zone.polygon) / len(zone.polygon)
                        avg_lng = sum(p.lng for p in zone.polygon) / len(zone.polygon)
                        return (avg_lat, avg_lng)
            except ValueError:
                pass

        elif element_type == "fuel_entry":
            try:
                idx = int(element_id)
                if idx < len(state.fuel_entry_points):
                    fe = state.fuel_entry_points[idx]
                    return (fe.coordinate.lat, fe.coordinate.lng)
            except ValueError:
                pass

        elif element_type == "transformer":
            try:
                idx = int(element_id)
                if idx < len(state.transformers):
                    tr = state.transformers[idx]
                    return (tr.latitude, tr.longitude)
            except ValueError:
                pass

        elif element_type == "bus":
            bus = state.buses.get(element_id)
            if bus:
                return (bus.latitude, bus.longitude)

        elif element_type == "fuel_route":
            for rt in getattr(state, 'fuel_transport_routes', []):
                if rt.route_id == element_id:
                    coords = self._build_fuel_route_coords(rt)
                    if coords:
                        avg_lat = sum(c[0] for c in coords) / len(coords)
                        avg_lng = sum(c[1] for c in coords) / len(coords)
                        return (avg_lat, avg_lng)
                    break

        return None

    # ------------------------------------------------------------------
    # Model -> View synchronization
    # ------------------------------------------------------------------

    def _on_model_node_added(self, index: int):
        node = self.model.get_node(index)
        if node:
            self.element_tree.add_node(index, node.name)

    def _on_model_node_removed(self, index: int):
        self.element_tree.remove_node(index)

    def _on_model_node_updated(self, index: int):
        # Nodes are logical only (no map markers); tree label is static
        pass

    def _on_model_gen_updated(self, instance_id: str):
        inst = self.model.state.generators.get(instance_id)
        if inst:
            estyle = self._effective_style(inst.style, auto_size=self._auto_electrical_marker(inst.rated_power))
            self.map_widget.update_marker_style("generator", instance_id, estyle)
            self.map_widget.update_marker_tooltip(
                "generator", instance_id,
                f"{inst.name} ({inst.rated_power:.0f} MW)",
            )
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.update_generator(instance_id, label, info)

    def _on_model_bat_updated(self, instance_id: str):
        inst = self.model.state.batteries.get(instance_id)
        if inst:
            estyle = self._effective_style(inst.style, auto_size=self._auto_energy_marker(inst.capacity))
            self.map_widget.update_marker_style("battery", instance_id, estyle)
            self.map_widget.update_marker_tooltip(
                "battery", instance_id,
                f"{inst.name} ({inst.capacity:.0f} MWh)",
            )
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.capacity:.0f} MWh"
            self.element_tree.update_battery(instance_id, label, info)

    def _on_model_line_updated(self, line_id: str):
        for ln in self.model.state.transmission_lines:
            if ln.line_id == line_id:
                estyle = self._effective_style(ln.style, auto_width=self._auto_electrical_line(ln.capacity_mw))
                self.map_widget.update_line_style(line_id, estyle)
                self.map_widget.update_line_tooltip(
                    line_id, f"{ln.line_id}: {ln.capacity_mw:.0f} MW",
                )
                coords = self._build_line_coords(ln)
                if len(coords) >= 2:
                    self.map_widget.update_line_coords(line_id, coords)
                label = f"{ln.line_id}: Node {ln.from_node} -> Node {ln.to_node}"
                self.element_tree.update_line(
                    line_id, label, f"{ln.capacity_mw:.0f} MW"
                )
                self._refresh_transformer_form_if_active()
                return

    def _on_model_gen_added(self, instance_id: str):
        inst = self.model.state.generators.get(instance_id)
        if inst:
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            estyle = self._effective_style(inst.style, auto_size=self._auto_electrical_marker(inst.rated_power))
            self.map_widget.add_generator_marker(
                instance_id,
                inst.latitude, inst.longitude,
                inst.name, inst.gen_type, inst.rated_power,
                node_index=inst.node,
                style=estyle,
            )
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.add_generator(instance_id, label, info)

    def _on_model_gen_removed(self, instance_id: str):
        self.map_widget.remove_generator_marker(instance_id)
        self.element_tree.remove_generator(instance_id)

    def _on_model_bat_added(self, instance_id: str):
        inst = self.model.state.batteries.get(instance_id)
        if inst:
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            estyle = self._effective_style(inst.style, auto_size=self._auto_energy_marker(inst.capacity))
            self.map_widget.add_battery_marker(
                instance_id,
                inst.latitude, inst.longitude,
                inst.name, inst.capacity,
                node_index=inst.node,
                style=estyle,
            )
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.capacity:.0f} MWh"
            self.element_tree.add_battery(instance_id, label, info)

    def _on_model_bat_removed(self, instance_id: str):
        self.map_widget.remove_battery_marker(instance_id)
        self.element_tree.remove_battery(instance_id)

    def _on_model_line_added(self, line_id: str):
        for ln in self.model.state.transmission_lines:
            if ln.line_id == line_id:
                coords = self._build_line_coords(ln)
                if len(coords) >= 2:
                    estyle = self._effective_style(ln.style, auto_width=self._auto_electrical_line(ln.capacity_mw))
                    self.map_widget.add_transmission_line(
                        line_id, coords, ln.capacity_mw, style=estyle,
                    )
                label = f"{ln.line_id}: Node {ln.from_node} -> Node {ln.to_node}"
                self.element_tree.add_line(line_id, label, f"{ln.capacity_mw:.0f} MW")
                self._refresh_transformer_form_if_active()
                return

    def _on_model_line_removed(self, line_id: str):
        self.map_widget.remove_transmission_line(line_id)
        self.element_tree.remove_line(line_id)
        self._refresh_transformer_form_if_active()

    def _on_model_zone_added(self, index: int):
        if index < len(self.model.state.development_zones):
            zone = self.model.state.development_zones[index]
            coords = [(p.lat, p.lng) for p in zone.polygon]
            color = get_zone_colors().get(zone.technology, "#2ecc71")
            # Sync zone style color with technology color on creation
            if not zone.style.color:
                zone.style.color = color
            self.map_widget.add_development_zone(
                str(index), coords, zone.name, zone.technology, color
            )
            self.element_tree.add_zone(str(index), zone.name, zone.technology)

    def _on_model_zone_removed(self, index: int):
        self.map_widget.remove_development_zone(str(index))
        self.element_tree.remove_zone(str(index))

    def _on_zone_form_changed(self, index: int):
        """Update map polygon, style, and tree label when zone form changes."""
        state = self.model.state
        if index >= len(state.development_zones):
            return
        zone = state.development_zones[index]
        # Update polygon shape
        coords = [(p.lat, p.lng) for p in zone.polygon]
        self.map_widget.update_zone_polygon(str(index), coords)
        # Update style
        if zone.style:
            self.map_widget.update_zone_style(str(index), zone.style)
        # Update tree label
        label = zone.name
        if zone.node is not None:
            nd = self.model.get_node(zone.node)
            if nd:
                label = f"{zone.name} @ {nd.name}"
        self.element_tree.update_zone(str(index), label, zone.technology)

    def _on_zone_edit_polygon_toggled(self, index: int, enabled: bool):
        """Enable/disable polygon vertex editing on the map."""
        if enabled:
            self.map_widget.enable_zone_editing(str(index))
        else:
            self.map_widget.disable_zone_editing(str(index))

    def _on_zone_edited(self, zone_id: str, coords_json: str):
        """Handle polygon vertex changes from Leaflet.Draw editing."""
        import json as _json
        idx = int(zone_id)
        state = self.model.state
        if idx >= len(state.development_zones):
            return
        zone = state.development_zones[idx]
        from esfex.visualization.data.gui_model import GeoPoint
        coords = _json.loads(coords_json)
        zone.polygon = [GeoPoint(lat=c[0], lng=c[1]) for c in coords]
        # Update area display in zone form
        self._zone_form.update_area_display()

    def _on_zone_delete_requested(self, index: int):
        """Handle zone deletion from the zone form."""
        self.model.remove_zone(index)
        self.properties_panel.clear()

    # ------------------------------------------------------------------
    # Line trace editing
    # ------------------------------------------------------------------

    def _on_line_edit_trace_toggled(self, line_id: str, enabled: bool):
        """Enable/disable polyline vertex editing on the map."""
        if enabled:
            self.map_widget.enable_line_editing(line_id)
        else:
            self.map_widget.disable_line_editing(line_id)

    def _on_line_delete_requested(self, line_id: str):
        """Handle line deletion from the line form."""
        self.model.remove_line(line_id)
        self.properties_panel.clear()

    def _on_islink_delete_requested(self, link_id: str):
        """Handle inter-system link deletion from the islink form."""
        self.model.remove_inter_system_link(link_id)
        self.properties_panel.clear()

    def _propagate_islink_bus_properties(self, link_id: str) -> None:
        """Mirror GuiModel._propagate_line_properties for inter-system links.

        Reads voltage_kv / frequency_hz / current_type from the endpoint
        buses (each living in its respective system's GuiSystemState) and
        copies them onto the link so the properties form auto-fills them
        the same way a standard transmission line does.

        NOTE: this function only mutates the dataclass — it does NOT
        refresh the form. The caller is responsible for repainting the
        widgets if needed (form.load_element). Calling load_element from
        here would recurse infinitely because the form itself triggers
        propagate from its own load_element.
        """
        for lk in self.model.inter_system_links:
            if lk.link_id != link_id:
                continue
            # Sync active state into _all_states so we can read both sides.
            if self._current_system_name:
                self._all_states[self._current_system_name] = self.model.state
            from_state = self._all_states.get(lk.from_system)
            to_state = self._all_states.get(lk.to_system)
            from_bus = (from_state.buses.get(lk.from_endpoint.element_id)
                        if from_state and lk.from_endpoint
                        and lk.from_endpoint.element_type == "bus" else None)
            to_bus = (to_state.buses.get(lk.to_endpoint.element_id)
                      if to_state and lk.to_endpoint
                      and lk.to_endpoint.element_type == "bus" else None)
            src = from_bus or to_bus
            if src is not None:
                lk.voltage_kv = src.voltage_kv
                lk.frequency_hz = src.frequency_hz
                lk.current_type = src.current_type
            return

    def _on_islink_edit_trace_toggled(self, link_id: str, enabled: bool):
        """Enable/disable polyline vertex editing for an inter-system link.

        Reuses the map widget's line editor since islinks render through
        the same Leaflet polyline layer as intra-system transmission
        lines (id-prefixed `islink_*`).
        """
        if enabled:
            self.map_widget.enable_line_editing(link_id)
        else:
            self.map_widget.disable_line_editing(link_id)

    # ------------------------------------------------------------------
    # Fuel route trace editing
    # ------------------------------------------------------------------

    def _on_fuel_route_edit_trace_toggled(self, route_id: str, enabled: bool):
        """Enable/disable fuel route polyline vertex editing on the map."""
        if enabled:
            self.map_widget.enable_fuel_route_editing(route_id)
        else:
            self.map_widget.disable_fuel_route_editing(route_id)

    def _on_fuel_route_delete_requested(self, route_id: str):
        """Handle fuel route deletion from the route form."""
        self.model.remove_fuel_route(route_id)
        self.properties_panel.clear()

    def _on_fuel_route_edited(self, route_id: str, coords_json: str):
        """Handle fuel route polyline vertex changes from editing."""
        import json as _json
        coords = _json.loads(coords_json)
        route = None
        for rt in self.model.state.fuel_transport_routes:
            if rt.route_id == route_id:
                route = rt
                break
        if not route or len(coords) < 2:
            return
        route.waypoints = [GeoPoint(lat=c[0], lng=c[1]) for c in coords[1:-1]]
        # Recalculate length from new geometry
        self._auto_update_fuel_route_length(route_id)

    def _on_line_edited(self, line_id: str, coords_json: str):
        """Handle polyline vertex changes from Leaflet.Draw editing."""
        import json as _json
        coords = _json.loads(coords_json)
        # Find the line
        line = None
        for ln in self.model.state.transmission_lines:
            if ln.line_id == line_id:
                line = ln
                break
        if not line or len(coords) < 2:
            return
        # Middle coords become waypoints (first/last are endpoints)
        line.waypoints = [GeoPoint(lat=c[0], lng=c[1]) for c in coords[1:-1]]
        # Recalculate length from new geometry
        self._auto_update_line_length(line_id)

    def _on_model_fuel_entry_added(self, index: int):
        if index < len(self.model.state.fuel_entry_points):
            fe = self.model.state.fuel_entry_points[index]
            fuels_str = ", ".join(fe.fuels) if fe.fuels else ""
            total_import = sum(fp.max_import_rate for fp in fe.fuel_params.values())
            estyle = self._effective_style(fe.style, auto_size=self._auto_fuel_marker(total_import))
            self.map_widget.add_fuel_entry_marker(
                str(index), fe.coordinate.lat, fe.coordinate.lng,
                fe.name, fuels_str, node_index=fe.node, style=estyle,
            )
            self.element_tree.add_fuel_entry(str(index), fe.name, fuels_str)

    def _on_model_transformer_added(self, index: int):
        if index < len(self.model.state.transformers):
            tr = self.model.state.transformers[index]
            mva = getattr(tr, 'rated_power_mva', 0)
            estyle = self._effective_style(tr.style, auto_size=self._auto_electrical_marker(mva))
            self.map_widget.add_transformer_marker(
                str(index),
                tr.latitude, tr.longitude,
                tr.name, rated_power_mva=mva,
                node_index=tr.from_node, style=estyle,
            )
            self.element_tree.add_transformer(str(index), tr.name)

    def _on_model_transformer_removed(self, index: int):
        self.map_widget.remove_transformer_marker(str(index))
        self.element_tree.remove_transformer(str(index))
        self.map_widget.reindex_marker_registry("transformer")

    def _on_model_fuel_entry_removed(self, index: int):
        self.map_widget.remove_fuel_entry_marker(str(index))
        self.element_tree.remove_fuel_entry(str(index))
        self.map_widget.reindex_marker_registry("fuel_entry")

    def _on_fuel_entry_form_changed(self, index: int):
        if index < len(self.model.state.fuel_entry_points):
            fe = self.model.state.fuel_entry_points[index]
            fuels_str = ", ".join(fe.fuels) if fe.fuels else ""
            total_import = sum(fp.max_import_rate for fp in fe.fuel_params.values())
            estyle = self._effective_style(fe.style, auto_size=self._auto_fuel_marker(total_import))
            self.map_widget.update_marker_style("fuel_entry", str(index), estyle)
            self.map_widget.update_marker_tooltip(
                "fuel_entry", str(index),
                f"{fe.name} ({fuels_str})" if fuels_str else fe.name,
            )
            self.map_widget.update_marker_position("fuel_entry", str(index), fe.coordinate.lat, fe.coordinate.lng)
            self.element_tree.update_fuel_entry(str(index), fe.name, fuels_str)

    def _on_transformer_form_changed(self, index: int):
        if index < len(self.model.state.transformers):
            tr = self.model.state.transformers[index]
            mva = getattr(tr, 'rated_power_mva', 0) or 0
            estyle = self._effective_style(tr.style, auto_size=self._auto_electrical_marker(mva))
            self.map_widget.update_marker_style("transformer", str(index), estyle)
            self.map_widget.update_marker_tooltip(
                "transformer", str(index),
                f"{tr.name} ({mva:.0f} MVA)",
            )
            self.element_tree.update_transformer(str(index), tr.name)

    def _refresh_transformer_form_if_active(self):
        """Re-load the transformer form if it is currently displayed.

        Called when lines change, so the HV/LV labels update dynamically.
        """
        if (
            self.properties_panel._stack.currentWidget() is self._transformer_form
            and self._transformer_form._current_idx is not None
        ):
            self._transformer_form.load_element(
                str(self._transformer_form._current_idx)
            )

    def _on_model_fuel_source_added(self, source_id: str):
        src = self.model.state.fuel_sources.get(source_id)
        if src:
            self.element_tree.add_fuel_source(source_id, src.name, src.unit)

    def _on_model_fuel_source_removed(self, source_id: str):
        self.element_tree.remove_fuel_source(source_id)

    def _on_model_fuel_source_updated(self, source_id: str):
        src = self.model.state.fuel_sources.get(source_id)
        if src:
            self.element_tree.update_fuel_source(source_id, src.name, src.unit)

    def _on_model_fuel_storage_added(self, storage_id: str):
        inst = self.model.state.fuel_storages.get(storage_id)
        if inst:
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            total_cap = sum(fp.capacity for fp in inst.fuel_params.values())
            fuels_str = ", ".join(inst.fuels) if inst.fuels else ""
            estyle = self._effective_style(inst.style, auto_size=self._auto_fuel_marker(total_cap))
            self.map_widget.add_fuel_storage_marker(
                storage_id,
                inst.latitude, inst.longitude,
                inst.name, fuels_str, capacity=total_cap,
                node_index=inst.node, style=estyle,
            )
            label = f"{inst.name} @ {node_name}"
            self.element_tree.add_fuel_storage(storage_id, label, fuels_str)

    def _on_model_fuel_storage_removed(self, storage_id: str):
        self.map_widget.remove_fuel_storage_marker(storage_id)
        self.element_tree.remove_fuel_storage(storage_id)

    def _on_model_fuel_storage_updated(self, storage_id: str):
        inst = self.model.state.fuel_storages.get(storage_id)
        if inst:
            total_cap = sum(fp.capacity for fp in inst.fuel_params.values())
            fuels_str = ", ".join(inst.fuels) if inst.fuels else ""
            estyle = self._effective_style(inst.style, auto_size=self._auto_fuel_marker(total_cap))
            self.map_widget.update_marker_style("fuel_storage", storage_id, estyle)
            self.map_widget.update_marker_tooltip(
                "fuel_storage", storage_id,
                f"{inst.name} ({fuels_str})" if fuels_str else inst.name,
            )
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            label = f"{inst.name} @ {node_name}"
            self.element_tree.update_fuel_storage(storage_id, label, fuels_str)

    def _on_model_fuel_route_added(self, route_id: str):
        for rt in self.model.state.fuel_transport_routes:
            if rt.route_id == route_id:
                coords = self._build_fuel_route_coords(rt)
                fuels_str = ", ".join(rt.fuels) if rt.fuels else ""
                if len(coords) >= 2:
                    estyle = self._effective_style(rt.style, auto_width=self._auto_fuel_line(rt.capacity))
                    self.map_widget.add_fuel_transport_route(
                        route_id, coords, fuels_str, rt.capacity, style=estyle,
                    )
                # Auto-calculate length from geometry
                km = self._compute_fuel_route_length(rt)
                rt.length_km = km if km > 0 else None
                label = f"{rt.route_id}: Node {rt.from_node} -> Node {rt.to_node}"
                self.element_tree.add_fuel_route(route_id, label, fuels_str)
                return

    def _on_model_fuel_route_removed(self, route_id: str):
        self.map_widget.remove_fuel_transport_route(route_id)
        self.element_tree.remove_fuel_route(route_id)

    def _on_model_fuel_route_updated(self, route_id: str):
        for rt in self.model.state.fuel_transport_routes:
            if rt.route_id == route_id:
                estyle = self._effective_style(rt.style, auto_width=self._auto_fuel_line(rt.capacity))
                self.map_widget.update_fuel_route_style(route_id, estyle)
                self.map_widget.update_fuel_route_tooltip(
                    route_id, f"{rt.route_id}: {rt.capacity:.1f} units/h",
                )
                coords = self._build_fuel_route_coords(rt)
                if len(coords) >= 2:
                    self.map_widget.update_fuel_route_coords(route_id, coords)
                fuels_str = ", ".join(rt.fuels) if rt.fuels else ""
                label = f"{rt.route_id}: Node {rt.from_node} -> Node {rt.to_node}"
                self.element_tree.update_fuel_route(route_id, label, fuels_str)
                return

    # ------------------------------------------------------------------
    # Fuel (FuelConfig) model handlers
    # ------------------------------------------------------------------

    def _on_model_fuel_added(self, fuel_id: str):
        if fuel_id in RENEWABLE_FUELS:
            return
        fuel = self.model.state.fuels.get(fuel_id)
        if fuel:
            self.element_tree.add_fuel(fuel_id, fuel.name, fuel.fuel_id)

    def _on_model_fuel_removed(self, fuel_id: str):
        self.element_tree.remove_fuel(fuel_id)

    def _on_model_fuel_updated(self, fuel_id: str):
        fuel = self.model.state.fuels.get(fuel_id)
        if fuel:
            self.element_tree.update_fuel(fuel_id, fuel.name, fuel.fuel_id)

    def _on_fuel_delete_requested(self, fuel_id: str):
        self.model.remove_fuel(fuel_id)
        self.properties_panel.clear()

    # ------------------------------------------------------------------
    # Electrolyzer model handlers
    # ------------------------------------------------------------------

    def _on_model_electrolyzer_added(self, instance_id: str):
        inst = self.model.state.electrolyzers.get(instance_id)
        if inst:
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            estyle = self._effective_style(inst.style, auto_size=self._auto_electrical_marker(inst.rated_power))
            self.map_widget.add_electrolyzer_marker(
                instance_id,
                inst.latitude, inst.longitude,
                inst.name, inst.rated_power,
                node_index=inst.node, style=estyle,
            )
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.add_electrolyzer(instance_id, label, info)

    def _on_model_electrolyzer_removed(self, instance_id: str):
        self.map_widget.remove_electrolyzer_marker(instance_id)
        self.element_tree.remove_electrolyzer(instance_id)

    def _on_model_electrolyzer_updated(self, instance_id: str):
        inst = self.model.state.electrolyzers.get(instance_id)
        if inst:
            estyle = self._effective_style(inst.style, auto_size=self._auto_electrical_marker(inst.rated_power))
            self.map_widget.update_marker_style("electrolyzer", instance_id, estyle)
            self.map_widget.update_marker_tooltip(
                "electrolyzer", instance_id,
                f"{inst.name} ({inst.rated_power:.0f} MW)",
            )
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.update_electrolyzer(instance_id, label, info)

    def _on_electrolyzer_delete_requested(self, instance_id: str):
        self.model.remove_electrolyzer(instance_id)
        self.properties_panel.clear()

    # ------------------------------------------------------------------
    # AC/DC Converter model handlers
    # ------------------------------------------------------------------

    def _on_model_acdc_converter_added(self, index: int):
        if index < len(self.model.state.acdc_converters):
            conv = self.model.state.acdc_converters[index]
            estyle = self._effective_style(conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva))
            self.map_widget.add_acdc_converter_marker(
                str(index),
                conv.latitude, conv.longitude,
                conv.name, conv.rated_power_mva,
                node_index=conv.from_node, style=estyle,
            )
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.add_acdc_converter(str(index), label, info)

    def _on_model_acdc_converter_removed(self, index: int):
        self.map_widget.remove_acdc_converter_marker(str(index))
        self.element_tree.remove_acdc_converter(str(index))
        self.map_widget.reindex_marker_registry("acdc_converter")

    def _on_acdc_converter_form_changed(self, index: int):
        if index < len(self.model.state.acdc_converters):
            conv = self.model.state.acdc_converters[index]
            estyle = self._effective_style(conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva))
            self.map_widget.update_marker_style("acdc_converter", str(index), estyle)
            self.map_widget.update_marker_tooltip(
                "acdc_converter", str(index),
                f"{conv.name} ({conv.rated_power_mva:.0f} MVA)",
            )
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.update_acdc_converter(str(index), label, info)

    # ------------------------------------------------------------------
    # Frequency Converter model handlers
    # ------------------------------------------------------------------

    def _on_model_freq_converter_added(self, index: int):
        if index < len(self.model.state.freq_converters):
            conv = self.model.state.freq_converters[index]
            estyle = self._effective_style(conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva))
            self.map_widget.add_freq_converter_marker(
                str(index),
                conv.latitude, conv.longitude,
                conv.name, conv.rated_power_mva,
                node_index=conv.from_node, style=estyle,
            )
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.add_freq_converter(str(index), label, info)

    def _on_model_freq_converter_removed(self, index: int):
        self.map_widget.remove_freq_converter_marker(str(index))
        self.element_tree.remove_freq_converter(str(index))
        self.map_widget.reindex_marker_registry("freq_converter")

    def _on_freq_converter_form_changed(self, index: int):
        if index < len(self.model.state.freq_converters):
            conv = self.model.state.freq_converters[index]
            estyle = self._effective_style(conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva))
            self.map_widget.update_marker_style("freq_converter", str(index), estyle)
            self.map_widget.update_marker_tooltip(
                "freq_converter", str(index),
                f"{conv.name} ({conv.rated_power_mva:.0f} MVA)",
            )
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.update_freq_converter(str(index), label, info)

    # ------------------------------------------------------------------
    # Bus model handlers
    # ------------------------------------------------------------------

    def _on_model_bus_added(self, bus_id: str):
        bus = self.model.state.buses.get(bus_id)
        if bus:
            node = self.model.get_node(bus.parent_node)
            node_name = node.name if node else f"Node {bus.parent_node}"
            estyle = self._effective_style(bus.style)
            self.map_widget.add_bus_marker(
                bus_id,
                bus.latitude, bus.longitude,
                bus.name, bus.voltage_kv,
                node_index=bus.parent_node, style=estyle,
            )
            label = f"{bus.name} @ {node_name}"
            info = f"{bus.voltage_kv:.0f} kV {bus.current_type}"
            self.element_tree.add_bus(bus_id, label, info)

    def _on_model_bus_removed(self, bus_id: str):
        self.map_widget.remove_bus_marker(bus_id)
        self.element_tree.remove_bus(bus_id)

    def _on_model_bus_updated(self, bus_id: str):
        bus = self.model.state.buses.get(bus_id)
        if bus:
            estyle = self._effective_style(bus.style)
            self.map_widget.update_marker_style("bus", bus_id, estyle)
            self.map_widget.update_marker_tooltip(
                "bus", bus_id,
                f"{bus.name} ({bus.voltage_kv:.0f} kV)",
            )
            node = self.model.get_node(bus.parent_node)
            node_name = node.name if node else f"Node {bus.parent_node}"
            label = f"{bus.name} @ {node_name}"
            info = f"{bus.voltage_kv:.0f} kV {bus.current_type}"
            self.element_tree.update_bus(bus_id, label, info)
            # Propagate electrical properties to connected equipment
            self.model.propagate_bus_properties(bus_id)
            self._refresh_active_form_if_inherited()

    def _refresh_active_form_if_inherited(self):
        """Refresh the active form if it shows inherited electrical properties."""
        stack = self.properties_panel._stack
        current = stack.currentWidget()
        # Re-load the current form so inherited values are refreshed
        if current is self._gen_form and self._gen_form._current_id:
            self._gen_form.load_element(self._gen_form._current_id)
        elif current is self._bat_form and self._bat_form._current_id:
            self._bat_form.load_element(self._bat_form._current_id)
        elif current is self._line_form and self._line_form._current_id:
            self._line_form.load_element(self._line_form._current_id)
        elif current is self._transformer_form and self._transformer_form._current_idx is not None:
            self._transformer_form.load_element(str(self._transformer_form._current_idx))
        elif current is self._acdc_converter_form and self._acdc_converter_form._current_idx is not None:
            self._acdc_converter_form.load_element(str(self._acdc_converter_form._current_idx))
        elif current is self._freq_converter_form and self._freq_converter_form._current_idx is not None:
            self._freq_converter_form.load_element(str(self._freq_converter_form._current_idx))

    def _on_bus_form_changed(self, bus_id: str):
        self.model.busUpdated.emit(bus_id)

    # ------------------------------------------------------------------
    # Investment portfolio handlers
    # ------------------------------------------------------------------

    def _on_model_investment_added(self, entry_id: str):
        entry = self.model.state.investment_portfolio.get(entry_id)
        if entry:
            self.element_tree.add_investment_entry(
                entry_id, entry.name, entry.technology_type,
            )

    def _on_model_investment_removed(self, entry_id: str):
        self.element_tree.remove_investment_entry(entry_id)

    def _on_model_investment_updated(self, entry_id: str):
        entry = self.model.state.investment_portfolio.get(entry_id)
        if entry:
            self.element_tree.update_investment_entry(
                entry_id, entry.name, entry.technology_type,
            )

    def _on_investment_delete_requested(self, entry_id: str):
        self.model.remove_investment_entry(entry_id)
        self.properties_panel.clear()

    # ------------------------------------------------------------------
    # Technology model handlers
    # ------------------------------------------------------------------

    def _on_model_technology_added(self, tech_id: str):
        tech = self.model.state.technologies.get(tech_id)
        if tech:
            self.element_tree.add_technology(tech_id, tech.name, tech.category)

    def _on_model_technology_removed(self, tech_id: str):
        self.element_tree.remove_technology(tech_id)

    def _on_model_technology_updated(self, tech_id: str):
        tech = self.model.state.technologies.get(tech_id)
        if tech:
            self.element_tree.update_technology(tech_id, tech.name, tech.category)

    def _on_technology_delete_requested(self, tech_id: str):
        self.model.remove_technology(tech_id)
        self.properties_panel.clear()

    # ------------------------------------------------------------------
    # Geo asset handlers
    # ------------------------------------------------------------------

    def _on_geo_asset_visibility_changed(self, asset_id: str, visible: bool):
        self.map_widget.set_geo_asset_visible(asset_id, visible)

    def _on_parse_geo_asset(self, asset_id: str):
        """Open parse dialog to convert geo asset features into system elements."""
        import os

        from PySide6.QtWidgets import QDialog

        from esfex.visualization.data.geo_asset_parser import apply_assignments
        from esfex.visualization.panels.parse_geo_asset_dialog import (
            ParseGeoAssetDialog,
        )

        info = self._geo_assets.get(asset_id)
        if not info:
            return

        system_names = list(self._all_states.keys())
        if not system_names:
            return

        # Determine default target system
        target_system = info.target_system or self._current_system_name
        if target_system not in self._all_states:
            target_system = self._current_system_name

        target_state = self._all_states.get(target_system)
        nodes = list(target_state.nodes) if target_state else []

        def _get_nodes_for_system(sys_name: str) -> list:
            st = self._all_states.get(sys_name)
            return list(st.nodes) if st else []

        dialog = ParseGeoAssetDialog(
            info.geojson_data, info.name,
            system_names=system_names,
            default_system=target_system,
            nodes=nodes,
            on_system_changed=_get_nodes_for_system,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        assignments = dialog.get_assignments()
        if not assignments:
            return

        snap_km = dialog.get_snap_threshold()
        selected_system = dialog.get_target_system()

        # Update stored target if user changed it
        if selected_system != info.target_system:
            info.target_system = selected_system
            self.element_tree.update_geo_asset_info(
                asset_id,
                f"{os.path.basename(info.file_path)} [{selected_system}]",
            )

        state = self._all_states.get(selected_system)
        if state is None:
            return

        # Switch to that system if not already active
        if selected_system != self._current_system_name:
            self._switch_to_system(selected_system)

        result = apply_assignments(state, assignments, snap_km)

        # load_state emits stateLoaded → _on_state_loaded rebuilds map + tree
        self.model.load_state(state)

        QMessageBox.information(self, tr("messages.parse_complete_title"), result.summary())

    # ------------------------------------------------------------------
    # Unified tree delete handler
    # ------------------------------------------------------------------

    def _on_tree_delete_requested(self, element_type: str, element_id: str):
        """Handle delete button from the element tree panel."""
        if element_type == "node":
            self.model.remove_node(int(element_id))
        elif element_type == "generator":
            self.model.remove_generator(element_id)
        elif element_type == "battery":
            self.model.remove_battery(element_id)
        elif element_type == "line":
            self.model.remove_line(element_id)
        elif element_type == "zone":
            self.model.remove_zone(int(element_id))
        elif element_type == "transformer":
            idx = int(element_id)
            self._reindex_line_endpoints("transformer", idx)
            self.model.remove_transformer(idx)
        elif element_type == "fuel_entry":
            idx = int(element_id)
            self._reindex_line_endpoints("fuel_entry", idx)
            self.model.remove_fuel_entry(idx)
        elif element_type == "fuel_source":
            self.model.remove_fuel_source(element_id)
        elif element_type == "fuel_storage":
            self.model.remove_fuel_storage(element_id)
        elif element_type == "fuel_route":
            self.model.remove_fuel_route(element_id)
        elif element_type == "fuel":
            self.model.remove_fuel(element_id)
        elif element_type == "electrolyzer":
            self.model.remove_electrolyzer(element_id)
        elif element_type == "acdc_converter":
            self.model.remove_acdc_converter(int(element_id))
        elif element_type == "freq_converter":
            self.model.remove_freq_converter(int(element_id))
        elif element_type == "bus":
            self.model.remove_bus(element_id)
        elif element_type == "technology":
            self.model.remove_technology(element_id)
        elif element_type == "inter_system_link":
            self.model.remove_inter_system_link(element_id)
        elif element_type == "investment_entry":
            self.model.remove_investment_entry(element_id)
        elif element_type == "geo_asset":
            self.map_widget.remove_geo_asset(element_id)
            self.element_tree.remove_geo_asset(element_id)
            self._geo_assets.pop(element_id, None)
        else:
            logger.warning(
                "Delete requested for unhandled element type %r (id=%r)",
                element_type, element_id,
            )
            return
        self.properties_panel.clear()

    def _on_tree_batch_delete(self, items: list):
        """Handle batch deletion with progress overlay.

        Items are pre-sorted by ``_sort_deletes_reverse`` (leaf types first,
        index-based types in descending order).  We additionally deduplicate:
        deleting a node/bus cascades to its children, so child entries already
        removed by a cascade are skipped to avoid stale-index corruption.
        """
        logger.info(
            "_on_tree_batch_delete invoked with %d items: %s",
            len(items), items,
        )
        # Re-order: delete leaves first, then buses, then nodes last.
        # Within each group, index-based types stay in reverse order.
        _TYPE_ORDER = {
            "generator": 0, "battery": 0, "electrolyzer": 0,
            "line": 0, "fuel_source": 0, "fuel_storage": 0,
            "fuel_route": 0, "fuel": 0, "technology": 0,
            "inter_system_link": 0, "geo_asset": 0,
            "investment_entry": 0,
            "transformer": 1, "fuel_entry": 1,
            "acdc_converter": 1, "freq_converter": 1, "zone": 1,
            "bus": 2,
            "node": 3,
        }
        items = sorted(
            items,
            key=lambda x: (
                _TYPE_ORDER.get(x[0], 1),
                -int(x[1]) if x[1].lstrip("-").isdigit() else 0,
            ),
        )
        total = len(items)
        if total <= 3:
            # Small batch — no overlay needed
            self.model.begin_bulk_update()
            try:
                for etype, eid in items:
                    if self._element_still_exists(etype, eid):
                        self._on_tree_delete_requested(etype, eid)
            finally:
                self.model.end_bulk_update()
            return

        self._busy_overlay.start(
            tr("overlay.deleting", n=total),
            total,
        )
        self.model.begin_bulk_update()
        self._batch_items = items
        self._batch_idx = 0
        QTimer.singleShot(0, self._process_batch_chunk)

    _BATCH_CHUNK = 50  # elements per event-loop tick

    def _process_batch_chunk(self):
        """Process a chunk of the batch deletion, then yield to the event loop."""
        items = self._batch_items
        total = len(items)
        end = min(self._batch_idx + self._BATCH_CHUNK, total)

        for i in range(self._batch_idx, end):
            etype, eid = items[i]
            if self._element_still_exists(etype, eid):
                self._on_tree_delete_requested(etype, eid)

        self._batch_idx = end
        self._busy_overlay.update_progress(
            end,
            tr("overlay.deleting_progress", done=end, total=total),
        )

        if self._batch_idx < total:
            QTimer.singleShot(0, self._process_batch_chunk)
        else:
            self.model.end_bulk_update()
            self._busy_overlay.finish()
            del self._batch_items
            del self._batch_idx

    def _element_still_exists(self, etype: str, eid: str) -> bool:
        """Check whether an element is still present in the model.

        Used during batch deletion to skip items already removed by a
        cascaded parent deletion (e.g. a bus deletion cascading to its
        generators/transformers).
        """
        s = self.model.state
        if etype == "generator":
            return eid in s.generators
        if etype == "battery":
            return eid in s.batteries
        if etype == "electrolyzer":
            return eid in s.electrolyzers
        if etype == "bus":
            return eid in s.buses
        if etype == "line":
            return any(ln.line_id == eid for ln in s.transmission_lines)
        if etype == "node":
            idx = int(eid)
            return 0 <= idx < len(s.nodes)
        if etype == "transformer":
            return 0 <= int(eid) < len(s.transformers)
        if etype == "fuel_entry":
            return 0 <= int(eid) < len(s.fuel_entry_points)
        if etype == "zone":
            return 0 <= int(eid) < len(s.development_zones)
        if etype == "acdc_converter":
            return 0 <= int(eid) < len(s.acdc_converters)
        if etype == "freq_converter":
            return 0 <= int(eid) < len(s.freq_converters)
        if etype == "fuel_source":
            return eid in s.fuel_sources
        if etype == "fuel_storage":
            return eid in s.fuel_storages
        if etype == "fuel_route":
            return any(rt.route_id == eid for rt in s.fuel_transport_routes)
        if etype == "fuel":
            return eid in s.fuels
        if etype == "technology":
            return eid in s.technologies
        if etype == "inter_system_link":
            return eid in getattr(s, "inter_system_links", {})
        if etype == "investment_entry":
            return eid in getattr(s, "investment_portfolio", {})
        return True  # Unknown type — attempt deletion

    def _reindex_line_endpoints(self, element_type: str, deleted_idx: int):
        """Adjust line/fuel-route endpoint refs after an index-based element is deleted."""
        for ln in self.model.state.transmission_lines:
            for ep in (ln.from_endpoint, ln.to_endpoint):
                if ep and ep.element_type == element_type:
                    ep_idx = int(ep.element_id)
                    if ep_idx == deleted_idx:
                        ep.element_type = "node"
                        ep.element_id = "0"
                    elif ep_idx > deleted_idx:
                        ep.element_id = str(ep_idx - 1)
        for rt in self.model.state.fuel_transport_routes:
            for ep in (rt.from_endpoint, rt.to_endpoint):
                if ep and ep.element_type == element_type:
                    ep_idx = int(ep.element_id)
                    if ep_idx == deleted_idx:
                        ep.element_type = "node"
                        ep.element_id = "0"
                    elif ep_idx > deleted_idx:
                        ep.element_id = str(ep_idx - 1)

    # ------------------------------------------------------------------
    # Duplicate element
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_name(base_name: str, existing_names: set[str]) -> str:
        """Generate a unique name like 'X (copy)', 'X (copy 2)', etc."""
        # Strip existing " (copy...)" suffix to get the true base
        import re
        base = re.sub(r"\s*\(copy(?:\s+\d+)?\)\s*$", "", base_name)
        candidate = f"{base} (copy)"
        if candidate not in existing_names:
            return candidate
        n = 2
        while True:
            candidate = f"{base} (copy {n})"
            if candidate not in existing_names:
                return candidate
            n += 1

    def _on_tree_duplicate_requested(self, element_type: str, element_id: str):
        """Duplicate an element via the context menu."""
        from copy import deepcopy

        state = self.model.state

        if element_type == "generator":
            orig = state.generators.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = len(state.generators)
            while f"{clone.unit_key}_copy_{counter}" in state.generators:
                counter += 1
            clone.instance_id = f"{clone.unit_key}_copy_{counter}"
            # Give the clone its own unit_key so the serializer treats it as
            # an independent generator (prevents phantom lines on reload).
            clone.unit_key = clone.instance_id
            names = {g.name for g in state.generators.values()}
            clone.name = self._unique_name(orig.name, names)
            # Disconnect from bus so user can reposition freely
            clone.bus = ""
            clone.node = 0
            clone.latitude += 0.03
            clone.longitude += 0.03
            state.generators[clone.instance_id] = clone
            self.model.generatorAdded.emit(clone.instance_id)

        elif element_type == "battery":
            orig = state.batteries.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = len(state.batteries)
            while f"{clone.unit_key}_copy_{counter}" in state.batteries:
                counter += 1
            clone.instance_id = f"{clone.unit_key}_copy_{counter}"
            # Give the clone its own unit_key so the serializer treats it as
            # an independent battery (prevents phantom lines on reload).
            clone.unit_key = clone.instance_id
            names = {b.name for b in state.batteries.values()}
            clone.name = self._unique_name(orig.name, names)
            # Disconnect from bus so user can reposition freely
            clone.bus = ""
            clone.node = 0
            clone.latitude += 0.03
            clone.longitude += 0.03
            state.batteries[clone.instance_id] = clone
            self.model.batteryAdded.emit(clone.instance_id)

        elif element_type == "node":
            node_idx = int(element_id)
            orig = self.model.get_node(node_idx)
            if not orig:
                return
            names = {n.name for n in state.nodes}
            new_idx = self.model.add_node(
                name=self._unique_name(orig.name, names),
            )

        elif element_type == "bus":
            orig = state.buses.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            new_id = f"bus_{state._next_bus_id}"
            state._next_bus_id += 1
            clone.bus_id = new_id
            names = {b.name for b in state.buses.values()}
            clone.name = self._unique_name(orig.name, names)
            clone.latitude += 0.03
            clone.longitude += 0.03
            state.buses[new_id] = clone
            self.model.busAdded.emit(new_id)

        elif element_type == "fuel_entry":
            try:
                idx = int(element_id)
                if idx < len(state.fuel_entry_points):
                    orig = state.fuel_entry_points[idx]
                    clone = deepcopy(orig)
                    names = {fe.name for fe in state.fuel_entry_points}
                    clone.name = self._unique_name(orig.name, names)
                    clone.coordinate = deepcopy(orig.coordinate)
                    if clone.coordinate:
                        clone.coordinate.latitude += 0.03
                        clone.coordinate.longitude += 0.03
                    clone.node = 0
                    state.fuel_entry_points.append(clone)
                    self.model.stateLoaded.emit()
            except ValueError:
                pass

        elif element_type == "fuel_source":
            orig = state.fuel_sources.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = 0
            new_id = f"{element_id}_copy"
            while new_id in state.fuel_sources:
                counter += 1
                new_id = f"{element_id}_copy_{counter}"
            clone.source_id = new_id
            names = {fs.name for fs in state.fuel_sources.values()}
            clone.name = self._unique_name(orig.name, names)
            state.fuel_sources[new_id] = clone
            self.model.fuelSourceAdded.emit(new_id)

        elif element_type == "fuel_storage":
            orig = state.fuel_storages.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = 0
            new_id = f"{element_id}_copy"
            while new_id in state.fuel_storages:
                counter += 1
                new_id = f"{element_id}_copy_{counter}"
            clone.storage_id = new_id
            names = {fs.name for fs in state.fuel_storages.values()}
            clone.name = self._unique_name(orig.name, names)
            state.fuel_storages[new_id] = clone
            self.model.fuelStorageAdded.emit(new_id)

        elif element_type == "fuel":
            orig = state.fuels.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = 0
            new_id = f"{element_id}_copy"
            while new_id in state.fuels:
                counter += 1
                new_id = f"{element_id}_copy_{counter}"
            clone.fuel_id = new_id
            names = {f.name for f in state.fuels.values()}
            clone.name = self._unique_name(orig.name, names)
            state.fuels[new_id] = clone
            self.model.fuelAdded.emit(new_id)

        elif element_type == "electrolyzer":
            orig = state.electrolyzers.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = 0
            new_id = f"{element_id}_copy"
            while new_id in state.electrolyzers:
                counter += 1
                new_id = f"{element_id}_copy_{counter}"
            clone.instance_id = new_id
            # Give the clone its own unit_key so the serializer treats it as
            # an independent electrolyzer (prevents phantom lines on reload).
            clone.unit_key = new_id
            names = {e.name for e in state.electrolyzers.values()}
            clone.name = self._unique_name(orig.name, names)
            # Disconnect from bus so user can reposition freely
            clone.bus = ""
            clone.node = 0
            clone.latitude += 0.03
            clone.longitude += 0.03
            state.electrolyzers[new_id] = clone
            self.model.electrolyzerAdded.emit(new_id)

        elif element_type == "transformer":
            try:
                idx = int(element_id)
                if idx < len(state.transformers):
                    clone = deepcopy(state.transformers[idx])
                    names = {t.name for t in state.transformers}
                    clone.name = self._unique_name(clone.name, names)
                    # Disconnect from network so user can reposition freely
                    clone.from_bus = ""
                    clone.to_bus = ""
                    clone.from_node = 0
                    clone.to_node = 0
                    clone.latitude += 0.03
                    clone.longitude += 0.03
                    state.transformers.append(clone)
                    self.model.stateLoaded.emit()
            except ValueError:
                pass

        elif element_type == "line":
            for line in state.transmission_lines:
                if line.line_id == element_id:
                    clone = deepcopy(line)
                    clone.line_id = f"line_{state._next_line_id}"
                    state._next_line_id += 1
                    # Clear endpoint refs so the clone doesn't share connections
                    # with the original (which caused both lines to move together)
                    clone.from_endpoint = None
                    clone.to_endpoint = None
                    state.transmission_lines.append(clone)
                    self.model.lineAdded.emit(clone.line_id)
                    break

        elif element_type == "acdc_converter":
            try:
                idx = int(element_id)
                if idx < len(state.acdc_converters):
                    clone = deepcopy(state.acdc_converters[idx])
                    names = {c.name for c in state.acdc_converters}
                    clone.name = self._unique_name(clone.name, names)
                    # Disconnect from network so user can reposition freely
                    clone.from_bus = ""
                    clone.to_bus = ""
                    clone.from_node = 0
                    clone.to_node = 0
                    clone.latitude += 0.03
                    clone.longitude += 0.03
                    state.acdc_converters.append(clone)
                    self.model.stateLoaded.emit()
            except ValueError:
                pass

        elif element_type == "freq_converter":
            try:
                idx = int(element_id)
                if idx < len(state.freq_converters):
                    clone = deepcopy(state.freq_converters[idx])
                    names = {c.name for c in state.freq_converters}
                    clone.name = self._unique_name(clone.name, names)
                    # Disconnect from network so user can reposition freely
                    clone.from_bus = ""
                    clone.to_bus = ""
                    clone.from_node = 0
                    clone.to_node = 0
                    clone.latitude += 0.03
                    clone.longitude += 0.03
                    state.freq_converters.append(clone)
                    self.model.stateLoaded.emit()
            except ValueError:
                pass

        elif element_type == "technology":
            orig = state.technologies.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            counter = 0
            new_id = f"{element_id}_copy"
            while new_id in state.technologies:
                counter += 1
                new_id = f"{element_id}_copy_{counter}"
            clone.tech_id = new_id
            names = {t.name for t in state.technologies.values()}
            clone.name = self._unique_name(orig.name, names)
            state.technologies[new_id] = clone
            self.model.technologyAdded.emit(new_id)

        elif element_type == "investment_entry":
            orig = state.investment_portfolio.get(element_id)
            if not orig:
                return
            clone = deepcopy(orig)
            new_id = f"inv_{state._next_investment_id}"
            state._next_investment_id += 1
            clone.entry_id = new_id
            names = {e.name for e in state.investment_portfolio.values()}
            clone.name = self._unique_name(orig.name, names)
            state.investment_portfolio[new_id] = clone
            self.model.investmentEntryAdded.emit(new_id)

    # ------------------------------------------------------------------
    # Copy / Paste attributes
    # ------------------------------------------------------------------

    # Fields to SKIP when copying attributes (identity + spatial + style).
    # Everything else is considered a "parameter" that gets pasted.
    _PASTE_SKIP_FIELDS: dict[str, set[str]] = {
        "generator": {
            "instance_id", "name", "node", "bus", "latitude", "longitude", "style",
        },
        "battery": {
            "instance_id", "name", "node", "bus", "latitude", "longitude", "style",
        },
        "electrolyzer": {
            "instance_id", "name", "node", "bus", "latitude", "longitude", "style",
        },
        "bus": {
            "bus_id", "name", "parent_node", "latitude", "longitude", "style",
        },
        "line": {
            "line_id", "name", "from_bus", "to_bus", "from_node", "to_node",
            "waypoints", "from_endpoint", "to_endpoint", "style",
        },
        "transformer": {
            "name", "from_bus", "to_bus", "from_node", "to_node",
            "latitude", "longitude", "style",
        },
        "acdc_converter": {
            "name", "from_bus", "to_bus", "from_node", "to_node",
            "latitude", "longitude", "style",
        },
        "freq_converter": {
            "name", "from_bus", "to_bus", "from_node", "to_node",
            "latitude", "longitude", "style",
        },
        "fuel_entry": {
            "name", "coordinate", "style",
        },
        "fuel_source": {
            "source_id", "name",
        },
        "fuel_storage": {
            "storage_id", "name", "latitude", "longitude", "style",
        },
        "fuel": {
            "fuel_id", "name",
        },
        "technology": {
            "tech_id", "name",
        },
        "investment_entry": {
            "entry_id", "name",
        },
    }

    def _get_element_obj(self, element_type: str, element_id: str):
        """Resolve an element object by type + id."""
        state = self.model.state
        if element_type == "generator":
            return state.generators.get(element_id)
        elif element_type == "battery":
            return state.batteries.get(element_id)
        elif element_type == "electrolyzer":
            return state.electrolyzers.get(element_id)
        elif element_type == "bus":
            return state.buses.get(element_id)
        elif element_type == "line":
            for ln in state.transmission_lines:
                if ln.line_id == element_id:
                    return ln
            return None
        elif element_type == "transformer":
            try:
                return state.transformers[int(element_id)]
            except (ValueError, IndexError):
                return None
        elif element_type == "acdc_converter":
            try:
                return state.acdc_converters[int(element_id)]
            except (ValueError, IndexError):
                return None
        elif element_type == "freq_converter":
            try:
                return state.freq_converters[int(element_id)]
            except (ValueError, IndexError):
                return None
        elif element_type == "fuel_entry":
            try:
                return state.fuel_entry_points[int(element_id)]
            except (ValueError, IndexError):
                return None
        elif element_type == "fuel_source":
            return state.fuel_sources.get(element_id)
        elif element_type == "fuel_storage":
            return state.fuel_storages.get(element_id)
        elif element_type == "fuel":
            return state.fuels.get(element_id)
        elif element_type == "technology":
            return state.technologies.get(element_id)
        elif element_type == "investment_entry":
            return state.investment_portfolio.get(element_id)
        return None

    def _on_tree_copy_requested(self, element_type: str, element_id: str):
        """Copy an element's non-positional attributes to the clipboard."""
        from dataclasses import fields as dc_fields

        obj = self._get_element_obj(element_type, element_id)
        if obj is None:
            return

        skip = self._PASTE_SKIP_FIELDS.get(element_type, set())
        attrs = {}
        for f in dc_fields(obj):
            if f.name not in skip:
                attrs[f.name] = getattr(obj, f.name)

        self._clipboard = {"type": element_type, "attrs": attrs}
        src_name = getattr(obj, "name", element_id)
        self.statusBar().showMessage(
            f"Copied attributes from {element_type} '{src_name}'", 3000,
        )

    def _on_tree_paste_requested(self, element_type: str, element_id: str):
        """Paste clipboard attributes onto a target element of the same type."""
        if not self._clipboard or self._clipboard["type"] != element_type:
            QMessageBox.warning(
                self, tr("tree_ctx.paste_attributes"),
                "Clipboard is empty or contains a different element type.",
            )
            return

        obj = self._get_element_obj(element_type, element_id)
        if obj is None:
            return

        attrs = self._clipboard["attrs"]
        for key, value in attrs.items():
            if hasattr(obj, key):
                setattr(obj, key, value)

        # Emit update signals so the UI refreshes
        self._emit_update_signal(element_type, element_id)

        target_name = getattr(obj, "name", element_id)
        self.statusBar().showMessage(
            f"Pasted attributes onto {element_type} '{target_name}'", 3000,
        )

    def _on_marker_context_action(self, action: str, element_type: str, element_id: str):
        """Dispatch a right-click context menu action from a map marker to the correct handler."""
        if action == "duplicate":
            self._on_tree_duplicate_requested(element_type, element_id)
        elif action == "copy":
            self._on_tree_copy_requested(element_type, element_id)
        elif action == "paste":
            self._on_tree_paste_requested(element_type, element_id)
        elif action == "delete":
            self._on_tree_delete_requested(element_type, element_id)

    def _emit_update_signal(self, element_type: str, element_id: str):
        """Emit the appropriate model update signal after pasting."""
        m = self.model
        if element_type == "generator":
            m.generatorUpdated.emit(element_id)
        elif element_type == "battery":
            m.batteryUpdated.emit(element_id)
        elif element_type == "line":
            m.lineUpdated.emit(element_id)
        elif element_type == "fuel_source":
            m.fuelSourceUpdated.emit(element_id)
        elif element_type == "fuel_storage":
            m.fuelStorageUpdated.emit(element_id)
        elif element_type == "fuel_route":
            m.fuelRouteUpdated.emit(element_id)
        else:
            # For types without a granular update signal, reload the full state
            m.stateLoaded.emit()

    # ------------------------------------------------------------------
    # Inter-system link model handlers
    # ------------------------------------------------------------------

    def _build_islink_coords(self, lk) -> list[tuple[float, float]]:
        """Build polyline coords for an inter-system link.

        Resolves ``from_endpoint`` against ``_all_states[from_system]`` and
        ``to_endpoint`` against ``_all_states[to_system]`` so the endpoints
        anchor to the right system's geometry. Waypoints are inlined
        between the two endpoints in trace order.
        """
        # Sync current state into the multi-system store before lookups.
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state

        coords: list[tuple[float, float]] = []
        from_state = self._all_states.get(lk.from_system)
        to_state = self._all_states.get(lk.to_system)
        if lk.from_endpoint and from_state is not None:
            start = self._resolve_endpoint_position(lk.from_endpoint, from_state)
            if start:
                coords.append(start)
        for wp in lk.waypoints or []:
            coords.append((wp.lat, wp.lng))
        if lk.to_endpoint and to_state is not None:
            end = self._resolve_endpoint_position(lk.to_endpoint, to_state)
            if end:
                coords.append(end)
        return coords

    def _on_model_islink_added(self, link_id: str):
        for lk in self.model.inter_system_links:
            if lk.link_id == link_id:
                label = f"{lk.from_system}:{lk.from_node} -> {lk.to_system}:{lk.to_node}"
                info = f"{lk.capacity_mw:.0f} MW" if lk.link_type == "transmission" else lk.fuel
                self.element_tree.add_inter_system_link(
                    link_id, lk.link_type, label, info,
                )
                # Render the polyline on the map — fuel routes and
                # transmission links both reuse the line layer; the
                # GuiInterSystemLink.style default is purple so it is
                # visually distinct from intra-system blue lines.
                coords = self._build_islink_coords(lk)
                if len(coords) >= 2:
                    auto_w = (
                        self._auto_electrical_line(lk.capacity_mw)
                        if lk.link_type == "transmission"
                        else self._auto_fuel_line(lk.capacity_mw or 0.0)
                    )
                    estyle = self._effective_style(lk.style, auto_width=auto_w)
                    self.map_widget.add_transmission_line(
                        link_id, coords, lk.capacity_mw, style=estyle,
                    )
                return

    def _on_model_islink_removed(self, link_id: str):
        self.element_tree.remove_inter_system_link(link_id)
        # Also drop the polyline from the map (mirror line removal).
        self.map_widget.remove_transmission_line(link_id)

    def _on_model_islink_updated(self, link_id: str):
        for lk in self.model.inter_system_links:
            if lk.link_id == link_id:
                label = f"{lk.from_system}:{lk.from_node} -> {lk.to_system}:{lk.to_node}"
                info = f"{lk.capacity_mw:.0f} MW" if lk.link_type == "transmission" else lk.fuel
                self.element_tree.update_inter_system_link(link_id, label, info)
                # Re-render the polyline: endpoints / waypoints may have
                # moved. Remove + add is the simplest correct path.
                self.map_widget.remove_transmission_line(link_id)
                coords = self._build_islink_coords(lk)
                if len(coords) >= 2:
                    auto_w = (
                        self._auto_electrical_line(lk.capacity_mw)
                        if lk.link_type == "transmission"
                        else self._auto_fuel_line(lk.capacity_mw or 0.0)
                    )
                    estyle = self._effective_style(lk.style, auto_width=auto_w)
                    self.map_widget.add_transmission_line(
                        link_id, coords, lk.capacity_mw, style=estyle,
                    )
                return

    # ------------------------------------------------------------------
    # Visual scaling helpers
    # ------------------------------------------------------------------

    def _get_vs(self):
        if self._vs_cache is None:
            self._vs_cache = self.model.global_settings.visual_scaling
        return self._vs_cache

    # Hard ceiling on auto-computed marker size. Without this, a single
    # huge fuel storage (e.g. 3 000 fuel units * 0.5 px/unit = 1 500 px)
    # would render as a blob covering several zoom levels of the map.
    _MARKER_MAX_PX: float = 40.0

    def _auto_electrical_marker(self, mw_or_mva: float) -> float:
        sc = self._get_vs()
        return min(self._MARKER_MAX_PX,
                   max(sc.marker_min_px, sc.electrical_marker_scale * mw_or_mva))

    def _auto_energy_marker(self, mwh: float) -> float:
        sc = self._get_vs()
        return min(self._MARKER_MAX_PX,
                   max(sc.marker_min_px, sc.energy_marker_scale * mwh))

    def _auto_fuel_marker(self, fuel_units: float) -> float:
        sc = self._get_vs()
        return min(self._MARKER_MAX_PX,
                   max(sc.marker_min_px, sc.fuel_marker_scale * fuel_units))

    # Hard ceiling on auto-computed line width.  Without this, a 10 GW
    # transmission line could render at 50 px which is unusable.
    _LINE_MAX_PX: float = 8.0

    def _auto_electrical_line(self, mw: float) -> float:
        sc = self._get_vs()
        if mw <= 0:
            return sc.line_min_px
        return min(self._LINE_MAX_PX,
                   max(sc.line_min_px, sc.electrical_line_scale * mw))

    def _auto_fuel_line(self, fuel_units: float) -> float:
        sc = self._get_vs()
        return min(self._LINE_MAX_PX,
                   max(sc.line_min_px, sc.fuel_line_scale * fuel_units))

    def _effective_style(self, base_style, auto_size=None, auto_width=None):
        """Merge auto-computed size with user style. User values take precedence."""
        s = base_style or VisualStyle()
        return VisualStyle(
            color=s.color,
            size=s.size if s.size is not None else auto_size,
            icon_shape=s.icon_shape,
            opacity=s.opacity,
            width=s.width if s.width is not None else auto_width,
        )

    def _on_visual_scaling_changed(self):
        """Re-render all map elements when global visual scaling parameters change."""
        self._vs_cache = None
        self._on_state_loaded()

    def _style_dict(self, style) -> dict | None:
        """Convert a VisualStyle to a plain dict for batch JSON serialization."""
        from esfex.visualization.map_widget import _style_to_dict
        return _style_to_dict(style)

    def _populate_tree_for_system(self, sys_name: str, state):
        """Populate the element tree for a non-active system (tree only, no map).

        Called during config load so all systems show their elements
        immediately, not just the active one.
        """
        old_sys = self.element_tree._current_system
        self.element_tree.set_current_system(sys_name)
        self.element_tree.begin_batch()

        for node in state.nodes:
            self.element_tree.add_node(node.index, node.name)

        for bus_id, bus in state.buses.items():
            node_name = f"Node {bus.parent_node}"
            for n in state.nodes:
                if n.index == bus.parent_node:
                    node_name = n.name
                    break
            self.element_tree.add_bus(bus_id, f"{bus.name} @ {node_name}",
                                      f"{bus.voltage_kv:.0f} kV")

        for inst_id, inst in state.generators.items():
            node_name = f"Node {inst.node}"
            for n in state.nodes:
                if n.index == inst.node:
                    node_name = n.name
                    break
            self.element_tree.add_generator(
                inst_id, f"{inst.name} @ {node_name}", f"{inst.rated_power:.0f} MW")

        for inst_id, inst in state.batteries.items():
            node_name = f"Node {inst.node}"
            for n in state.nodes:
                if n.index == inst.node:
                    node_name = n.name
                    break
            self.element_tree.add_battery(
                inst_id, f"{inst.name} @ {node_name}", f"{inst.capacity:.0f} MWh")

        for ln in state.transmission_lines:
            self.element_tree.add_line(
                ln.line_id, f"{ln.line_id}: N{ln.from_node}→N{ln.to_node}",
                f"{ln.capacity_mw:.0f} MW")

        for i, zone in enumerate(state.development_zones):
            self.element_tree.add_zone(str(i), zone.name, zone.technology)

        for i, tr_inst in enumerate(state.transformers):
            tr_id = getattr(tr_inst, "instance_id", str(i))
            self.element_tree.add_transformer(
                tr_id, getattr(tr_inst, "name", tr_id), "")

        for el_id, el in state.electrolyzers.items():
            self.element_tree.add_electrolyzer(el_id, getattr(el, "name", el_id), "")

        for tid, tech in state.technologies.items():
            self.element_tree.add_technology(tid, tech.name, tech.category)

        # Fuel-related elements: same set _on_state_loaded adds for the active
        # system. Without these the non-active system tab showed "0 fuels"
        # even though state.fuels was correctly loaded by the serializer.
        for fid, fuel in state.fuels.items():
            if fid not in RENEWABLE_FUELS:
                self.element_tree.add_fuel(fid, fuel.name, fid)
        for i, fe in enumerate(state.fuel_entry_points):
            fuels_str = ", ".join(fe.fuels) if fe.fuels else ""
            self.element_tree.add_fuel_entry(str(i), fe.name, fuels_str)
        for src_id, src in state.fuel_sources.items():
            self.element_tree.add_fuel_source(src_id, src.name, src.unit)
        for sid, fst in state.fuel_storages.items():
            fuels_str = ", ".join(fst.fuels) if fst.fuels else ""
            node_name = f"Node {fst.node}"
            for n in state.nodes:
                if n.index == fst.node:
                    node_name = n.name
                    break
            self.element_tree.add_fuel_storage(sid, f"{fst.name} @ {node_name}", fuels_str)
        for rt in state.fuel_transport_routes:
            fuels_str = ", ".join(rt.fuels) if rt.fuels else ""
            label = f"{rt.route_id}: Node {rt.from_node} -> Node {rt.to_node}"
            self.element_tree.add_fuel_route(rt.route_id, label, fuels_str)

        self.element_tree.end_batch()
        self.element_tree.set_current_system(old_sys)

    def _on_state_loaded(self):
        """Rebuild all map markers and tree items from current state.

        Uses batch APIs to minimise Qt→JS IPC crossings and suppress
        per-item tree count updates.
        """
        self.map_widget.clear_all()
        self.element_tree.clear_all()

        self._vs_cache = self.model.global_settings.visual_scaling
        state = self.model.state

        # -- Collect all map elements into a single list --
        batch: list[dict] = []

        # -- Tree in batch mode (defer _update_count until the end) --
        self.element_tree.begin_batch()

        # Nodes (logical only — no map markers)
        for node in state.nodes:
            self.element_tree.add_node(node.index, node.name)

        # Buses
        for bus_id, bus in state.buses.items():
            node = self.model.get_node(bus.parent_node)
            node_name = node.name if node else f"Node {bus.parent_node}"
            batch.append({
                "type": "bus",
                "id": bus_id,
                "lat": bus.latitude,
                "lng": bus.longitude,
                "name": bus.name,
                "voltageKv": bus.voltage_kv,
                "nodeIndex": bus.parent_node,
                "style": self._style_dict(self._effective_style(bus.style)),
            })
            label = f"{bus.name} @ {node_name}"
            info = f"{bus.voltage_kv:.0f} kV {bus.current_type}"
            self.element_tree.add_bus(bus_id, label, info)

        # Transmission lines
        for ln in state.transmission_lines:
            coords = self._build_line_coords(ln)
            if len(coords) >= 2:
                batch.append({
                    "type": "line",
                    "id": ln.line_id,
                    "coords": coords,
                    "capacityMw": ln.capacity_mw,
                    "style": self._style_dict(self._effective_style(
                        ln.style, auto_width=self._auto_electrical_line(ln.capacity_mw),
                    )),
                })
            label = f"{ln.line_id}: Node {ln.from_node} -> Node {ln.to_node}"
            self.element_tree.add_line(ln.line_id, label, f"{ln.capacity_mw:.0f} MW")

        # Generators (instance-based)
        for inst_id, inst in state.generators.items():
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            batch.append({
                "type": "generator",
                "id": inst_id,
                "lat": inst.latitude,
                "lng": inst.longitude,
                "name": inst.name,
                "genType": inst.gen_type,
                "ratedPowerMw": inst.rated_power,
                "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_electrical_marker(inst.rated_power),
                )),
            })
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.add_generator(inst_id, label, info)

        # Batteries (instance-based)
        for inst_id, inst in state.batteries.items():
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            batch.append({
                "type": "battery",
                "id": inst_id,
                "lat": inst.latitude,
                "lng": inst.longitude,
                "name": inst.name,
                "capacityMwh": inst.capacity,
                "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_energy_marker(inst.capacity),
                )),
            })
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.capacity:.0f} MWh"
            self.element_tree.add_battery(inst_id, label, info)

        # Development zones
        for i, zone in enumerate(state.development_zones):
            coords = [(p.lat, p.lng) for p in zone.polygon]
            color = zone.style.color if zone.style.color else get_zone_colors().get(zone.technology, "#2ecc71")
            opacity = zone.style.opacity if zone.style.opacity is not None else None
            batch.append({
                "type": "zone",
                "id": str(i),
                "coords": coords,
                "name": zone.name,
                "technology": zone.technology,
                "color": color,
                "opacity": opacity,
            })
            zone_label = zone.name
            if zone.node is not None:
                zn = self.model.get_node(zone.node)
                zone_label = f"{zone.name} @ {zn.name}" if zn else zone_label
            self.element_tree.add_zone(str(i), zone_label, zone.technology)

        # Fuel entry points
        for i, fe in enumerate(state.fuel_entry_points):
            fuels_str = ", ".join(fe.fuels) if fe.fuels else ""
            total_import = sum(fp.max_import_rate for fp in fe.fuel_params.values())
            batch.append({
                "type": "fuel_entry",
                "id": str(i),
                "lat": fe.coordinate.lat,
                "lng": fe.coordinate.lng,
                "name": fe.name,
                "fuel": fuels_str,
                "maxAvailability": total_import,
                "nodeIndex": fe.node,
                "style": self._style_dict(self._effective_style(
                    fe.style, auto_size=self._auto_fuel_marker(total_import),
                )),
            })
            self.element_tree.add_fuel_entry(str(i), fe.name, fuels_str)

        # Fuel sources (system-level, no map marker)
        for src_id, src in state.fuel_sources.items():
            self.element_tree.add_fuel_source(src_id, src.name, src.unit)

        # Fuel storages
        for sid, fst in state.fuel_storages.items():
            node = self.model.get_node(fst.node)
            node_name = node.name if node else f"Node {fst.node}"
            total_cap = sum(fp.capacity for fp in fst.fuel_params.values())
            fuels_str = ", ".join(fst.fuels) if fst.fuels else ""
            batch.append({
                "type": "fuel_storage",
                "id": sid,
                "lat": fst.latitude,
                "lng": fst.longitude,
                "name": fst.name,
                "fuel": fuels_str,
                "capacity": total_cap,
                "nodeIndex": fst.node,
                "style": self._style_dict(self._effective_style(
                    fst.style, auto_size=self._auto_fuel_marker(total_cap),
                )),
            })
            label = f"{fst.name} @ {node_name}"
            self.element_tree.add_fuel_storage(sid, label, fuels_str)

        # Fuel transport routes
        for rt in state.fuel_transport_routes:
            coords = self._build_fuel_route_coords(rt)
            fuels_str = ", ".join(rt.fuels) if rt.fuels else ""
            if len(coords) >= 2:
                batch.append({
                    "type": "fuel_route",
                    "id": rt.route_id,
                    "coords": coords,
                    "fuel": fuels_str,
                    "capacity": rt.capacity,
                    "style": self._style_dict(self._effective_style(
                        rt.style, auto_width=self._auto_fuel_line(rt.capacity),
                    )),
                })
                # Auto-calculate length from geometry
                km = self._compute_fuel_route_length(rt)
                if km > 0:
                    rt.length_km = km
            label = f"{rt.route_id}: Node {rt.from_node} -> Node {rt.to_node}"
            self.element_tree.add_fuel_route(rt.route_id, label, fuels_str)

        # Transformers
        for i, tr in enumerate(state.transformers):
            mva = getattr(tr, 'rated_power_mva', 0) or 0
            batch.append({
                "type": "transformer",
                "id": str(i),
                "lat": tr.latitude,
                "lng": tr.longitude,
                "name": tr.name,
                "ratedPowerMva": mva,
                "nodeIndex": tr.from_node,
                "style": self._style_dict(self._effective_style(
                    tr.style, auto_size=self._auto_electrical_marker(mva),
                )),
            })
            self.element_tree.add_transformer(str(i), tr.name)

        # Fuels (FuelConfig) — skip renewable defaults
        for fid, fuel in state.fuels.items():
            if fid not in RENEWABLE_FUELS:
                self.element_tree.add_fuel(fid, fuel.name, fid)

        # Electrolyzers
        for el_id, inst in state.electrolyzers.items():
            node = self.model.get_node(inst.node)
            node_name = node.name if node else f"Node {inst.node}"
            batch.append({
                "type": "electrolyzer",
                "id": el_id,
                "lat": inst.latitude,
                "lng": inst.longitude,
                "name": inst.name,
                "ratedPower": inst.rated_power,
                "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_electrical_marker(inst.rated_power),
                )),
            })
            label = f"{inst.name} @ {node_name}"
            info = f"{inst.rated_power:.0f} MW"
            self.element_tree.add_electrolyzer(el_id, label, info)

        # AC/DC Converters
        for i, conv in enumerate(state.acdc_converters):
            batch.append({
                "type": "acdc_converter",
                "id": str(i),
                "lat": conv.latitude,
                "lng": conv.longitude,
                "name": conv.name,
                "ratedPower": conv.rated_power_mva,
                "nodeIndex": conv.from_node,
                "style": self._style_dict(self._effective_style(
                    conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva),
                )),
            })
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.add_acdc_converter(str(i), label, info)

        # Frequency Converters
        for i, conv in enumerate(state.freq_converters):
            batch.append({
                "type": "freq_converter",
                "id": str(i),
                "lat": conv.latitude,
                "lng": conv.longitude,
                "name": conv.name,
                "ratedPower": conv.rated_power_mva,
                "nodeIndex": conv.from_node,
                "style": self._style_dict(self._effective_style(
                    conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva),
                )),
            })
            label = f"{conv.name}: N{conv.from_node} -> N{conv.to_node}"
            info = f"{conv.rated_power_mva:.0f} MVA"
            self.element_tree.add_freq_converter(str(i), label, info)

        # Technologies
        for tech_id, tech in state.technologies.items():
            self.element_tree.add_technology(tech_id, tech.name, tech.category)

        # Investment portfolio
        for entry_id, entry in state.investment_portfolio.items():
            self.element_tree.add_investment_entry(
                entry_id, entry.name, entry.technology_type,
            )

        # -- End tree batch (refresh all counts at once) --
        self.element_tree.end_batch()

        # -- Add elements from OTHER systems (same rendering, prefixed IDs) --
        for sys_name, other_state in self._all_states.items():
            if sys_name == self._current_system_name:
                continue
            self._collect_state_batch(other_state, batch, id_prefix=f"{sys_name}/")

        # -- Inter-system links: live in model.inter_system_links (not in
        # any single state), so the per-state rebuild above misses them.
        # Re-emit each link's polyline + tree item on every state load so
        # switching the active system doesn't visually erase the links.
        # IMPORTANT: clear the tree's islink sections AND any prior
        # polyline on the map before re-adding, otherwise every system
        # switch piles a duplicate item on top of the existing one.
        self.element_tree.clear_inter_system_links()
        for lk in self.model.inter_system_links:
            self.map_widget.remove_transmission_line(lk.link_id)
        for lk in self.model.inter_system_links:
            label = f"{lk.from_system}:{lk.from_node} -> {lk.to_system}:{lk.to_node}"
            info = (f"{lk.capacity_mw:.0f} MW"
                    if lk.link_type == "transmission" else (lk.fuel or ""))
            self.element_tree.add_inter_system_link(
                lk.link_id, lk.link_type, label, info,
            )
            coords = self._build_islink_coords(lk)
            if len(coords) >= 2:
                auto_w = (
                    self._auto_electrical_line(lk.capacity_mw)
                    if lk.link_type == "transmission"
                    else self._auto_fuel_line(lk.capacity_mw or 0.0)
                )
                estyle = self._effective_style(lk.style, auto_width=auto_w)
                batch.append({
                    "type": "line",
                    "id": lk.link_id,
                    "coords": coords,
                    "capacityMw": lk.capacity_mw,
                    "style": self._style_dict(estyle),
                })

        # -- Send map elements via IPC --
        if batch:
            _CHUNK = 100
            if len(batch) <= _CHUNK:
                self.map_widget.load_batch(json.dumps(batch))
            else:
                # Large systems: detach layers, add in chunks, re-attach.
                # Each chunk is a separate runJavaScript call to avoid
                # exceeding QWebEngine's string size limits.
                self.map_widget.set_canvas_mode(len(batch))
                self.map_widget.detach_layers()
                for i in range(0, len(batch), _CHUNK):
                    chunk_json = json.dumps(batch[i:i + _CHUNK])
                    self.map_widget.load_batch_raw(chunk_json)
                self.map_widget.reattach_layers()
                self.map_widget._run_js("_finishCanvasMode()")

        # Fit map to bounds (consider ALL systems) — unless suppressed
        # by a quiet system switch (user clicked element from another system)
        if self._suppress_fit_bounds:
            pass
        elif state.map_center:
            self.map_widget.set_map_view(
                state.map_center.lat, state.map_center.lng, state.map_zoom
            )
        else:
            pts: list[tuple[float, float]] = []
            for st in self._all_states.values():
                for g in st.generators.values():
                    if g.latitude != 0 or g.longitude != 0:
                        pts.append((g.latitude, g.longitude))
                for b in st.batteries.values():
                    if b.latitude != 0 or b.longitude != 0:
                        pts.append((b.latitude, b.longitude))
                for bus in st.buses.values():
                    if bus.latitude != 0 or bus.longitude != 0:
                        pts.append((bus.latitude, bus.longitude))
            if pts:
                lats = [p[0] for p in pts]
                lngs = [p[1] for p in pts]
                self.map_widget.fit_bounds(
                    min(lats) - 0.5, min(lngs) - 0.5,
                    max(lats) + 0.5, max(lngs) + 0.5,
                )

        # Invalidate the cached SLD so the next view of it (or, if the
        # SLD tab is currently active, immediately) rebuilds from the
        # newly-loaded state. Without this, switching systems leaves
        # the SLD frozen on the previous system's diagram.
        self._sld_dirty = True
        if (hasattr(self, "_view_stack")
                and self._view_stack.currentIndex() == 1):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self._rebuild_sld)

    # ------------------------------------------------------------------
    # Add element dialogs
    # ------------------------------------------------------------------

    def _collect_state_batch(
        self, st, batch: list[dict], id_prefix: str = "",
    ):
        """Append map element dicts from *st* into *batch*.

        When *id_prefix* is non-empty the element IDs are prefixed so
        they don't collide with the active system's IDs.  The element
        names include the prefix for tooltip clarity.
        """
        def _id(eid):
            return f"{id_prefix}{eid}" if id_prefix else eid

        # Buses
        for bus_id, bus in st.buses.items():
            batch.append({
                "type": "bus", "id": _id(bus_id),
                "lat": bus.latitude, "lng": bus.longitude,
                "name": bus.name, "voltageKv": bus.voltage_kv,
                "nodeIndex": bus.parent_node,
                "style": self._style_dict(self._effective_style(bus.style)),
            })

        # Transmission lines
        for ln in st.transmission_lines:
            coords = self._build_line_coords(ln, state=st)
            if len(coords) >= 2:
                batch.append({
                    "type": "line", "id": _id(ln.line_id),
                    "coords": coords, "capacityMw": ln.capacity_mw,
                    "style": self._style_dict(self._effective_style(
                        ln.style, auto_width=self._auto_electrical_line(ln.capacity_mw),
                    )),
                })

        # Generators
        for inst_id, inst in st.generators.items():
            batch.append({
                "type": "generator", "id": _id(inst_id),
                "lat": inst.latitude, "lng": inst.longitude,
                "name": inst.name, "genType": inst.gen_type,
                "ratedPowerMw": inst.rated_power, "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_electrical_marker(inst.rated_power),
                )),
            })

        # Batteries
        for inst_id, inst in st.batteries.items():
            batch.append({
                "type": "battery", "id": _id(inst_id),
                "lat": inst.latitude, "lng": inst.longitude,
                "name": inst.name, "capacityMwh": inst.capacity,
                "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_energy_marker(inst.capacity),
                )),
            })

        # Development zones
        for i, zone in enumerate(st.development_zones):
            coords = [(p.lat, p.lng) for p in zone.polygon]
            color = zone.style.color if zone.style.color else get_zone_colors().get(zone.technology, "#2ecc71")
            opacity = zone.style.opacity if zone.style.opacity is not None else None
            batch.append({
                "type": "zone", "id": _id(str(i)),
                "coords": coords, "name": zone.name,
                "technology": zone.technology,
                "color": color, "opacity": opacity,
            })

        # Fuel entry points
        for i, fe in enumerate(st.fuel_entry_points):
            fuels_str = ", ".join(fe.fuels) if fe.fuels else ""
            total_import = sum(fp.max_import_rate for fp in fe.fuel_params.values())
            batch.append({
                "type": "fuel_entry", "id": _id(str(i)),
                "lat": fe.coordinate.lat, "lng": fe.coordinate.lng,
                "name": fe.name, "fuel": fuels_str,
                "maxAvailability": total_import, "nodeIndex": fe.node,
                "style": self._style_dict(self._effective_style(
                    fe.style, auto_size=self._auto_fuel_marker(total_import),
                )),
            })

        # Fuel storages
        for sid, fst in st.fuel_storages.items():
            total_cap = sum(fp.capacity for fp in fst.fuel_params.values())
            fuels_str = ", ".join(fst.fuels) if fst.fuels else ""
            batch.append({
                "type": "fuel_storage", "id": _id(sid),
                "lat": fst.latitude, "lng": fst.longitude,
                "name": fst.name, "fuel": fuels_str,
                "capacity": total_cap, "nodeIndex": fst.node,
                "style": self._style_dict(self._effective_style(
                    fst.style, auto_size=self._auto_fuel_marker(total_cap),
                )),
            })

        # Fuel transport routes
        for rt in st.fuel_transport_routes:
            coords = self._build_fuel_route_coords(rt, state=st)
            fuels_str = ", ".join(rt.fuels) if rt.fuels else ""
            if len(coords) >= 2:
                batch.append({
                    "type": "fuel_route", "id": _id(rt.route_id),
                    "coords": coords, "fuel": fuels_str,
                    "capacity": rt.capacity,
                    "style": self._style_dict(self._effective_style(
                        rt.style, auto_width=self._auto_fuel_line(rt.capacity),
                    )),
                })

        # Transformers
        for i, tr_item in enumerate(st.transformers):
            mva = getattr(tr_item, 'rated_power_mva', 0) or 0
            batch.append({
                "type": "transformer", "id": _id(str(i)),
                "lat": tr_item.latitude, "lng": tr_item.longitude,
                "name": tr_item.name, "ratedPowerMva": mva,
                "nodeIndex": tr_item.from_node,
                "style": self._style_dict(self._effective_style(
                    tr_item.style, auto_size=self._auto_electrical_marker(mva),
                )),
            })

        # Electrolyzers
        for el_id, inst in st.electrolyzers.items():
            batch.append({
                "type": "electrolyzer", "id": _id(el_id),
                "lat": inst.latitude, "lng": inst.longitude,
                "name": inst.name, "ratedPower": inst.rated_power,
                "nodeIndex": inst.node,
                "style": self._style_dict(self._effective_style(
                    inst.style, auto_size=self._auto_electrical_marker(inst.rated_power),
                )),
            })

        # AC/DC Converters
        for i, conv in enumerate(st.acdc_converters):
            batch.append({
                "type": "acdc_converter", "id": _id(str(i)),
                "lat": conv.latitude, "lng": conv.longitude,
                "name": conv.name, "ratedPower": conv.rated_power_mva,
                "nodeIndex": conv.from_node,
                "style": self._style_dict(self._effective_style(
                    conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva),
                )),
            })

        # Frequency Converters
        for i, conv in enumerate(st.freq_converters):
            batch.append({
                "type": "freq_converter", "id": _id(str(i)),
                "lat": conv.latitude, "lng": conv.longitude,
                "name": conv.name, "ratedPower": conv.rated_power_mva,
                "nodeIndex": conv.from_node,
                "style": self._style_dict(self._effective_style(
                    conv.style, auto_size=self._auto_electrical_marker(conv.rated_power_mva),
                )),
            })

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _resolve_endpoint_position(self, ref: EndpointRef, state=None) -> tuple[float, float] | None:
        """Get (lat, lng) of an EndpointRef element."""
        etype, eid = ref.element_type, ref.element_id
        state = state or self.model.state

        if etype == "node":
            try:
                idx = int(eid)
                if idx < len(state.nodes):
                    nd = state.nodes[idx]
                    if nd.centroid_lat != 0.0 or nd.centroid_lng != 0.0:
                        return (nd.centroid_lat, nd.centroid_lng)
            except (ValueError, IndexError):
                pass

        elif etype == "bus":
            bus = state.buses.get(eid)
            if bus:
                return (bus.latitude, bus.longitude)

        elif etype == "generator":
            inst = state.generators.get(eid)
            if inst:
                return (inst.latitude, inst.longitude)

        elif etype == "battery":
            inst = state.batteries.get(eid)
            if inst:
                return (inst.latitude, inst.longitude)

        elif etype == "electrolyzer":
            inst = state.electrolyzers.get(eid)
            if inst:
                return (inst.latitude, inst.longitude)

        elif etype == "transformer":
            try:
                idx = int(eid)
                if idx < len(state.transformers):
                    tr = state.transformers[idx]
                    return (tr.latitude, tr.longitude)
            except (ValueError, IndexError):
                pass

        elif etype == "acdc_converter":
            try:
                idx = int(eid)
                if idx < len(state.acdc_converters):
                    conv = state.acdc_converters[idx]
                    return (conv.latitude, conv.longitude)
            except (ValueError, IndexError):
                pass

        elif etype == "freq_converter":
            try:
                idx = int(eid)
                if idx < len(state.freq_converters):
                    conv = state.freq_converters[idx]
                    return (conv.latitude, conv.longitude)
            except (ValueError, IndexError):
                pass

        elif etype == "fuel_entry":
            try:
                idx = int(eid)
                if idx < len(state.fuel_entry_points):
                    fe = state.fuel_entry_points[idx]
                    if fe.coordinate:
                        return (fe.coordinate.lat, fe.coordinate.lng)
            except (ValueError, IndexError):
                pass
            # Fallback by name
            for fe in state.fuel_entry_points:
                if fe.name == eid and fe.coordinate:
                    return (fe.coordinate.lat, fe.coordinate.lng)

        elif etype == "fuel_storage":
            inst = state.fuel_storages.get(eid)
            if inst:
                return (inst.latitude, inst.longitude)

        return None

    def _build_line_coords(self, ln, state=None) -> list[tuple[float, float]]:
        """Build polyline coordinates from endpoint positions + waypoints."""
        coords = []
        if ln.from_endpoint:
            start = self._resolve_endpoint_position(ln.from_endpoint, state)
            if start:
                coords.append(start)
        for wp in ln.waypoints:
            coords.append((wp.lat, wp.lng))
        if ln.to_endpoint:
            end = self._resolve_endpoint_position(ln.to_endpoint, state)
            if end:
                coords.append(end)
        return coords

    @staticmethod
    def _haversine_km(lat1, lng1, lat2, lng2):
        import math
        la1, lo1 = math.radians(lat1), math.radians(lng1)
        la2, lo2 = math.radians(lat2), math.radians(lng2)
        dlat, dlng = la2 - la1, lo2 - lo1
        a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
        return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _compute_line_length(self, ln) -> float:
        """Calculate line length in km from its full polyline path."""
        coords = self._build_line_coords(ln)
        if len(coords) < 2:
            return 0.0
        return round(sum(
            self._haversine_km(coords[i][0], coords[i][1],
                               coords[i + 1][0], coords[i + 1][1])
            for i in range(len(coords) - 1)
        ), 2)

    def _auto_update_line_length(self, line_id: str):
        """Recalculate and store line length, update form if visible."""
        for ln in self.model.state.transmission_lines:
            if ln.line_id == line_id:
                km = self._compute_line_length(ln)
                ln.length_km = km if km > 0 else None
                self._line_form.set_length_km(km)
                return

    def _compute_islink_length(self, lk) -> float:
        """Haversine length of an inter-system link's polyline (km)."""
        coords = self._build_islink_coords(lk)
        if len(coords) < 2:
            return 0.0
        return round(sum(
            self._haversine_km(coords[i][0], coords[i][1],
                               coords[i + 1][0], coords[i + 1][1])
            for i in range(len(coords) - 1)
        ), 2)

    def _auto_update_islink_length(self, link_id: str):
        """Recalculate length for an islink and push it to the form if open."""
        for lk in self.model.inter_system_links:
            if lk.link_id == link_id:
                km = self._compute_islink_length(lk)
                lk.length_km = km if km > 0 else None
                # distance_km is what the solver pipeline reads; keep both
                # in sync so a freshly-drawn link gets a sensible km
                # value even when the user never opens the properties form.
                lk.distance_km = km if km > 0 else (lk.distance_km or 0.0)
                if (self._islink_form is not None
                        and getattr(self._islink_form, "_current_id", None) == link_id
                        and hasattr(self._islink_form, "_length_km")):
                    self._islink_form._length_km.setValue(km)
                return

    def _compute_fuel_route_length(self, rt) -> float:
        """Calculate fuel route length in km from its full polyline path."""
        coords = self._build_fuel_route_coords(rt)
        if len(coords) < 2:
            return 0.0
        return round(sum(
            self._haversine_km(coords[i][0], coords[i][1],
                               coords[i + 1][0], coords[i + 1][1])
            for i in range(len(coords) - 1)
        ), 2)

    def _auto_update_fuel_route_length(self, route_id: str):
        """Recalculate and store fuel route length, update form if visible."""
        for rt in self.model.state.fuel_transport_routes:
            if rt.route_id == route_id:
                km = self._compute_fuel_route_length(rt)
                rt.length_km = km if km > 0 else None
                self._fuel_route_form.set_length_km(km)
                return

    def _build_fuel_route_coords(self, rt, state=None) -> list[tuple[float, float]]:
        """Build polyline coordinates for a fuel transport route."""
        coords = []
        if rt.from_endpoint:
            start = self._resolve_endpoint_position(rt.from_endpoint, state)
            if start:
                coords.append(start)
        for wp in rt.waypoints:
            coords.append((wp.lat, wp.lng))
        if rt.to_endpoint:
            end = self._resolve_endpoint_position(rt.to_endpoint, state)
            if end:
                coords.append(end)
        return coords

    def _update_fuel_routes_for_element(self, element_type: str, element_id: str,
                                         lat: float, lng: float):
        """Update fuel route endpoints snapped to a moved element."""
        for rt in self.model.state.fuel_transport_routes:
            updated = False
            if (rt.from_endpoint
                    and rt.from_endpoint.element_type == element_type
                    and rt.from_endpoint.element_id == element_id):
                updated = True
            if (rt.to_endpoint
                    and rt.to_endpoint.element_type == element_type
                    and rt.to_endpoint.element_id == element_id):
                updated = True
            if updated:
                coords = self._build_fuel_route_coords(rt)
                if len(coords) >= 2:
                    self.map_widget.update_fuel_route_coords(rt.route_id, coords)
                # Recalculate length after endpoint move
                self._auto_update_fuel_route_length(rt.route_id)

    def _on_fuel_route_trace_completed(self, from_type: str, from_id: str,
                                       to_type: str, to_id: str,
                                       waypoints_json: str):
        """Handle completion of a fuel route polyline trace from JS.

        Supports both intra-system fuel routes (added to the active
        SystemConfig.fuel_transport_routes) and inter-system fuel routes
        (added to meta_network.systems_links with link_type='fuel_route').
        """
        _allowed = {"node", "fuel_entry", "fuel_storage"}
        if from_type not in _allowed or to_type not in _allowed:
            QMessageBox.warning(
                self, tr("messages.invalid_route_title"),
                "Fuel transport routes must connect nodes, fuel entry "
                "or fuel storage points.",
            )
            return

        from_ref = EndpointRef(from_type, from_id)
        to_ref = EndpointRef(to_type, to_id)

        # Resolve each endpoint across ALL loaded systems (multi-system aware).
        from_sys, from_node = self._resolve_endpoint_with_system(from_ref)
        to_sys, to_node = self._resolve_endpoint_with_system(to_ref)

        if from_node is None or to_node is None or from_sys is None or to_sys is None:
            QMessageBox.warning(self, tr("messages.invalid_route_title"),
                                tr("messages.invalid_route_msg"))
            return

        waypoints = []
        try:
            raw = json.loads(waypoints_json) if waypoints_json else []
            for wp in raw:
                waypoints.append(GeoPoint(wp["lat"], wp["lng"]))
        except (json.JSONDecodeError, KeyError):
            pass

        # Inter-system fuel route → goes into meta_network.systems_links.
        if from_sys != to_sys:
            def _local_ref(ref, owner_sys):
                eid = ref.element_id or ""
                pref = f"{owner_sys}/"
                if eid.startswith(pref):
                    return EndpointRef(ref.element_type, eid[len(pref):])
                return ref
            link_id = self.model.add_inter_system_link(
                link_type="fuel_route",
                from_system=from_sys,
                to_system=to_sys,
                from_node=from_node,
                to_node=to_node,
                capacity_mw=0.0,
                waypoints=waypoints,
                from_endpoint=_local_ref(from_ref, from_sys),
                to_endpoint=_local_ref(to_ref, to_sys),
            )
            if link_id:
                self.statusBar().showMessage(
                    f"Inter-system fuel route {link_id} created "
                    f"({from_sys}/{from_node} → {to_sys}/{to_node}). "
                    "Edit fuel / capacity in the properties panel.",
                    8000,
                )
            return

        # Intra-system fuel route → switch to that system if needed.
        if from_sys != self._current_system_name:
            self._switch_to_system_quiet(from_sys)

        self.model.add_fuel_route(
            from_node=from_node,
            to_node=to_node,
            fuels=[],
            capacity=0.0,
            waypoints=waypoints,
            from_endpoint=from_ref,
            to_endpoint=to_ref,
        )

    def _resolve_endpoint_with_system(self, ref) -> tuple[str | None, int | None]:
        """Locate an endpoint across every system loaded in the GUI.

        Returns ``(system_name, node_idx)``. ``system_name`` is the key in
        ``self._all_states`` that owns the endpoint; ``node_idx`` is the
        local node index inside that system. Returns ``(None, None)`` if
        the endpoint doesn't exist in any loaded system.

        Convention: elements from non-active systems are rendered on the
        map with IDs prefixed ``"{sys_name}/{local_id}"`` (see
        :meth:`_collect_state_batch` ``id_prefix`` argument). Strip that
        prefix here and route the lookup to the corresponding state.

        We sync the current system's live state into ``_all_states``
        first so the search sees the user's latest edits before the
        save that ``_all_states`` storage normally requires.
        """
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state

        from esfex.visualization.data.gui_model import EndpointRef

        # If the id is prefixed (other-system element on the map), route
        # the lookup directly to that system's state — saves an O(N) scan
        # and prevents false positives where two systems share an id.
        raw_id = ref.element_id or ""
        if "/" in raw_id:
            sys_prefix, _, local_id = raw_id.partition("/")
            st = self._all_states.get(sys_prefix)
            if st is not None:
                local_ref = EndpointRef(ref.element_type, local_id)
                node = self.model.resolve_endpoint_node(local_ref, state=st)
                if node is not None:
                    return sys_prefix, node
            # Prefix didn't match any known system; fall through to scan.

        # No prefix → element on the active system map layer. Try active
        # first (fast path; preserves legacy behaviour), then scan others.
        if self._current_system_name:
            cur = self._all_states.get(self._current_system_name)
            if cur is not None:
                node = self.model.resolve_endpoint_node(ref, state=cur)
                if node is not None:
                    return self._current_system_name, node
        for sname, st in self._all_states.items():
            if sname == self._current_system_name:
                continue
            node = self.model.resolve_endpoint_node(ref, state=st)
            if node is not None:
                return sname, node
        return None, None

    def _on_polyline_trace_completed(self, from_type: str, from_id: str,
                                      to_type: str, to_id: str,
                                      waypoints_json: str):
        """Handle completion of a polyline trace from JS.

        Supports both intra-system lines (added to the active
        SystemConfig.transmission_lines_geo) and inter-system links
        (added to meta_network.systems_links via add_inter_system_link).
        """
        from esfex.visualization.data.connectivity_rules import (
            get_connection_error_message,
            is_valid_connection,
        )

        # Validate connection BEFORE creating line
        if not is_valid_connection(from_type, to_type):
            QMessageBox.warning(
                self,
                tr("messages.invalid_line_title"),
                get_connection_error_message(from_type, to_type),
            )
            return  # Reject invalid line

        from_ref = EndpointRef(from_type, from_id)
        to_ref = EndpointRef(to_type, to_id)

        from_sys, from_node = self._resolve_endpoint_with_system(from_ref)
        to_sys, to_node = self._resolve_endpoint_with_system(to_ref)

        if from_node is None or to_node is None or from_sys is None or to_sys is None:
            QMessageBox.warning(self, tr("messages.invalid_line_title"),
                                tr("messages.invalid_route_msg"))
            return

        # Parse waypoints
        waypoints = []
        try:
            raw = json.loads(waypoints_json) if waypoints_json else []
            for wp in raw:
                waypoints.append(GeoPoint(wp["lat"], wp["lng"]))
        except (json.JSONDecodeError, KeyError):
            pass

        # --- Inter-system link: endpoints in different systems ---
        if from_sys != to_sys:
            # The id arriving from JS for an other-system element carries
            # the "{sys_name}/" prefix that _collect_state_batch injects
            # for rendering. The link must store endpoints local to each
            # owner state (no prefix), otherwise the renderer can't
            # resolve them back to geo coords. Strip the prefix here.
            def _local_ref(ref, owner_sys):
                eid = ref.element_id or ""
                pref = f"{owner_sys}/"
                if eid.startswith(pref):
                    return EndpointRef(ref.element_type, eid[len(pref):])
                return ref
            link_id = self.model.add_inter_system_link(
                link_type="transmission",
                from_system=from_sys,
                to_system=to_sys,
                from_node=from_node,
                to_node=to_node,
                capacity_mw=100.0,
                waypoints=waypoints,
                from_endpoint=_local_ref(from_ref, from_sys),
                to_endpoint=_local_ref(to_ref, to_sys),
            )
            if link_id:
                # Auto-compute length (haversine over endpoints + waypoints)
                # and propagate voltage/frequency/current_type from the
                # endpoint buses — mirrors what add_line + propagate_bus_to_element
                # do for intra-system lines.
                self._auto_update_islink_length(link_id)
                self._propagate_islink_bus_properties(link_id)
                self.statusBar().showMessage(
                    f"Inter-system link {link_id} created "
                    f"({from_sys}/{from_node} ↔ {to_sys}/{to_node}). "
                    "Edit its distance_km / cost in the properties panel.",
                    8000,
                )
            return

        # --- Intra-system line: ensure we add to the right system ---
        if from_sys != self._current_system_name:
            # Switch to the system that owns these endpoints before adding.
            self._switch_to_system_quiet(from_sys)

        line_id = self.model.add_line(
            from_node=from_node,
            to_node=to_node,
            capacity_mw=100.0,
            waypoints=waypoints,
            from_endpoint=from_ref,
            to_endpoint=to_ref,
        )
        if line_id:
            self._auto_update_line_length(line_id)

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def _create_default_config(self):
        """Create a minimal default ESFEXConfig from current GUI state."""
        from esfex.config.schema import (
            MetaNetworkConfig,
            NodeConfig,
            ESFEXConfig,
            SystemConfig,
        )

        system_names = list(self._all_states.keys())
        if not system_names:
            system_names = [self._current_system_name or "System_1"]

        systems = {}
        for name in system_names:
            state = self._all_states.get(name)
            n_nodes = len(state.nodes) if state else 1
            n_nodes = max(n_nodes, 1)
            systems[name] = SystemConfig(
                name=name,
                nodes=NodeConfig(
                    num_nodes=n_nodes,
                    nodes_connections=[0.0] * (n_nodes * n_nodes),
                ),
                fuel_transport_distances=[[0.0] * n_nodes for _ in range(n_nodes)],
                fuels={},
            )

        return ESFEXConfig(
            meta_network=MetaNetworkConfig(systems=system_names),
            systems=systems,
        )

    def _ensure_system_exists(self):
        """Ensure at least one system exists, creating a default if needed."""
        if self._current_system_name and self._current_system_name in self._all_states:
            return
        name = "System_1"
        new_state = self.model.state
        new_state.name = name
        self._all_states[name] = new_state
        self._current_system_name = name
        self.element_tree.add_system(name)
        self.element_tree.set_current_system(name)
        if not new_state.nodes:
            self.model.add_node(name="Node 0")
        self._update_map_actions_state()

    def _populate_from_config(self, config, *, raw_dict: dict | None = None):
        """Load a ESFEXConfig and populate GUI."""
        from esfex.config.schema import ESFEXConfig

        if not isinstance(config, ESFEXConfig):
            return

        # Global settings BEFORE load_state, so visual_scaling is available
        # when _on_state_loaded rebuilds markers. Force-invalidate the
        # visual-scaling cache: direct attribute assignment doesn't
        # emit ``globalSettingsUpdated`` so the cache wouldn't otherwise
        # observe the swap.
        self.model.global_settings = config_to_global_settings(config, raw_dict=raw_dict)
        self._vs_cache = None
        self.model.stochastic_scenarios = config_to_stochastic_scenarios(config)

        states = config_to_gui_states(config)
        self._all_states = states
        system_names = list(states.keys())
        first_name = system_names[0]
        self._current_system_name = first_name
        for sys_name in system_names:
            self.element_tree.add_system(sys_name)

        # Load first system into model (triggers _on_state_loaded for map + tree)
        self.element_tree.set_current_system(first_name)
        self.model.load_state(states[first_name])

        # Populate tree for other systems (not loaded into model, tree-only)
        for sys_name in system_names[1:]:
            self._populate_tree_for_system(sys_name, states[sys_name])

        # Default: all systems selected for simulation if not set
        g = self.model.global_settings
        if not g.systems_to_simulate:
            g.systems_to_simulate = list(system_names)
        self._sync_systems_to_form()

        # Inter-system links
        self.model.clear_inter_system_links()
        self.element_tree.clear_inter_system_links()
        for lk in config_to_inter_system_links(config):
            self.model.add_inter_system_link(
                link_id=lk.link_id,
                link_type=lk.link_type,
                from_system=lk.from_system,
                to_system=lk.to_system,
                from_node=lk.from_node,
                to_node=lk.to_node,
                capacity_mw=lk.capacity_mw,
                investment_cost=lk.investment_cost,
                max_investment_mw=lk.max_investment_mw,
                loss_factor=lk.loss_factor,
                distance_km=lk.distance_km,
                cost_per_mw_km=lk.cost_per_mw_km,
                # Restore GUI geometry + LineForm-parity electrical
                # metadata, all saved in SystemLinkConfig.* so the
                # polyline and the properties form round-trip cleanly.
                waypoints=lk.waypoints,
                from_endpoint=lk.from_endpoint,
                to_endpoint=lk.to_endpoint,
                voltage_kv=lk.voltage_kv,
                line_type=lk.line_type,
                length_km=lk.length_km,
                base_impedance=lk.base_impedance,
                reactance_per_km=lk.reactance_per_km,
                susceptance_pu=lk.susceptance_pu,
                num_circuits=lk.num_circuits,
                frequency_hz=lk.frequency_hz,
                current_type=lk.current_type,
                decorative=lk.decorative,
                style=lk.style,
            )

        self._update_map_actions_state()

    def _auto_validate_states(self, states_dict: dict) -> tuple[list, list]:
        """Run validate_state across all systems; return (errors, warnings).

        Issue messages get a ``[system_name] `` prefix when there is more
        than one system, so the user can locate the offending one.  Never
        raises: validator exceptions are logged and skipped so the caller
        can use this helper inside load/save flows without aborting them.

        Inter-system links live outside any single state, so we also
        invoke ``validate_inter_system_links`` once at the end with the
        full link list and the multi-state dict.
        """
        import logging
        from esfex.visualization.data.validation import (
            validate_inter_system_links,
            validate_state,
        )
        errors: list = []
        warnings: list = []
        n = len(states_dict)
        for sname, state in states_dict.items():
            if not state:
                continue
            try:
                issues = validate_state(state)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Auto-validation crashed for system %r", sname,
                )
                continue
            for iss in issues:
                if n > 1:
                    iss.message = f"[{sname}] {iss.message}"
                if iss.severity == "error":
                    errors.append(iss)
                elif iss.severity == "warning":
                    warnings.append(iss)

        # Inter-system links — cross-state validation.
        try:
            # Sync current edits into _all_states before the check so
            # validation sees the latest bus / node config.
            if self._current_system_name:
                states_dict[self._current_system_name] = self.model.state
            islink_issues = validate_inter_system_links(
                self.model.inter_system_links, states_dict,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Auto-validation crashed for inter-system links",
            )
            islink_issues = []
        for iss in islink_issues:
            if iss.severity == "error":
                errors.append(iss)
            elif iss.severity == "warning":
                warnings.append(iss)
        return errors, warnings

    def _load_config_file(self, path: str):
        """Load a YAML config and populate the GUI.

        Large configs (cuba.yaml ≈ 18 s) would freeze the window with
        no visible feedback, so we drive a QProgressDialog whose label
        names the current stage. Range (0, 0) gives an indeterminate
        animation — sufficient to prove the app is alive when we have
        no good ETA. The dialog is window-modal and non-cancelable
        (cancelling mid-load isn't atomic for the model rebuild path).
        """
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QProgressDialog

        progress = QProgressDialog(
            f"Loading {Path(path).name}…", None, 0, 0, self,
        )
        progress.setWindowTitle("Loading")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        # Show immediately rather than using setMinimumDuration: with
        # an indeterminate range, setValue() never gets called so the
        # Qt-side auto-show heuristic won't fire reliably.
        progress.show()
        QApplication.processEvents()

        def _phase(text: str):
            progress.setLabelText(text)
            QApplication.processEvents()

        try:
            try:
                _phase("Reading YAML…")
                import yaml as _yaml
                from esfex.config.loader import load_config

                # Read raw YAML dict to preserve GUI-only keys (e.g. visual_scaling)
                with open(path, "r", encoding="utf-8") as fh:
                    raw_dict = _yaml.safe_load(fh) or {}

                _phase("Parsing config schema…")
                config = load_config(path)
                self._loaded_config = config
                self._raw_config_dict = raw_dict
                self._config_path = path
                # Mirror the CLI's default (``output or Path("./results")``,
                # cli.py:218) so the panel looks where the runner actually
                # writes. Deriving from ``config.parent`` produced
                # ``configs/results`` while the runner wrote to ``./results``
                # — the panel then opened a non-existent path.
                self._last_output_dir = "results"
                # A new config implies a new run pool — drop any cached
                # Results dialog tied to the previous config.
                self._invalidate_results_dialog_cache()

                _phase("Building GUI model…")
                self._populate_from_config(config, raw_dict=raw_dict)
                self.model.clear_undo()
                self.console.update_namespace(config=config, state=self.model.state)
                # Freshly loaded → no unsaved changes; refresh window title.
                self._clear_modified()
                self._update_window_title()
                # Remember in recents (after load succeeded — don't record
                # paths that fail to parse).
                try:
                    from esfex.visualization.preferences import add_recent_file
                    add_recent_file(path)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "Failed to update recent files",
                    )
            except Exception as e:
                QMessageBox.critical(self, tr("messages.load_error_title"), tr("messages.load_error_msg", e=e))
                return

            # Post-load auto-validation (non-blocking informational).
            # Inside the same progress dialog so the user gets one
            # continuous "Loading…" experience instead of two flashes.
            _phase("Validating…")
            try:
                errors, warnings = self._auto_validate_states(self._all_states)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Auto-validation post-load failed")
                errors, warnings = [], []
            if errors or warnings:
                parts = []
                if errors:
                    parts.append(f"{len(errors)} error(s)")
                if warnings:
                    parts.append(f"{len(warnings)} warning(s)")
                # Hide the progress dialog before stacking the modal warning.
                progress.hide()
                QMessageBox.warning(
                    self, tr("common.warning"),
                    f"Loaded {path}\n\n"
                    f"Validation found {', '.join(parts)}. "
                    "Click 'Validate' for details."
                )
        finally:
            progress.close()

    def _save_config_file(self, path: str):
        """Export current GUI state to YAML."""
        try:
            # Ensure a system exists and sync current editing state
            self._ensure_system_exists()
            if self._current_system_name:
                self._all_states[self._current_system_name] = self.model.state

            # Create a default base config if none was loaded
            if self._loaded_config is None:
                self._loaded_config = self._create_default_config()

            # Pre-save auto-validation. Block on errors (user can override);
            # surface warnings but proceed. Crashes here are non-fatal.
            try:
                errors, warnings = self._auto_validate_states(self._all_states)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Auto-validation pre-save failed")
                errors, warnings = [], []
            if errors:
                parts = [f"{len(errors)} error(s)"]
                if warnings:
                    parts.append(f"{len(warnings)} warning(s)")
                ret = QMessageBox.question(
                    self, tr("common.warning"),
                    f"Validation found {', '.join(parts)}.\n\n"
                    "Saving will persist the current state regardless. "
                    "Click 'Validate' for details before deciding.\n\n"
                    "Save anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return

            gui_state_to_yaml(
                self._all_states, self._loaded_config, path,
                inter_system_links=self.model.inter_system_links,
                global_settings=self.model.global_settings,
                stochastic_scenarios=self.model.stochastic_scenarios,
            )
            self._config_path = path
            # State on disk now matches in-memory state.
            self._clear_modified()
            self._update_window_title()
            try:
                from esfex.visualization.preferences import add_recent_file
                add_recent_file(path)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to update recent files",
                )
            QMessageBox.information(self, tr("messages.saved_title"), tr("messages.saved_msg", path=path))
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, tr("messages.save_error_title"), tr("messages.save_error_msg", e=e))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _on_validate(self):
        """Open the validation dialog for all systems selected for simulation."""
        from esfex.visualization.panels.validation_dialog import ValidationDialog

        # Save current system state before collecting all states
        self._all_states[self._current_system_name] = self.model.state

        # Collect states for systems to simulate
        g = self.model.global_settings
        systems_to_sim = g.systems_to_simulate or list(self._all_states.keys())
        states_to_validate = {
            name: self._all_states[name]
            for name in systems_to_sim
            if name in self._all_states
        }

        dlg = ValidationDialog(self.model, parent=self,
                               all_states=states_to_validate)
        dlg.simplificationApplied.connect(self._invalidate_validation)
        dlg.simplificationApplied.connect(lambda: self.model.stateLoaded.emit())
        dlg.validationFinished.connect(self._on_validation_finished)
        dlg.dialogClosed.connect(lambda: self._on_validation_closed(dlg))
        dlg.elementRequested.connect(self._on_tree_element_focused)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    # ------------------------------------------------------------------
    # Run simulation
    # ------------------------------------------------------------------

    def _on_validation_finished(self, ok: bool):
        """Enable Run button immediately when validation passes (dialog still open)."""
        self._validated_ok = ok
        self.toolbar.set_run_enabled(ok and not self._run_completed)
        if ok:
            self.toolbar.set_validate_enabled(False)
            # Pre-warm Julia dependency cache in background
            self._ensure_julia_deps_precompiled()

    def _on_validation_closed(self, dlg):
        """Update validation state when the dialog is closed."""
        self._validated_ok = dlg.validated_ok
        self.toolbar.set_run_enabled(self._validated_ok and not self._run_completed)
        if self._validated_ok:
            self.toolbar.set_validate_enabled(False)

    def _ensure_julia_deps_precompiled(self):
        """Ensure Julia dependency cache is warm (background, fire-and-forget).

        Runs ``Pkg.precompile()`` in a background subprocess so that when the
        user clicks *Run*, the Julia startup in the simulation subprocess is
        faster.  If a sysimage already exists this is a no-op (instant return).
        """
        try:
            from esfex.bridge.julia_setup import _find_sysimage

            if _find_sysimage():
                return  # sysimage exists → startup already fast

            import shutil
            import subprocess as _sp

            julia_exe = shutil.which("julia")
            if julia_exe is None:
                return

            from esfex.bridge.julia_setup import get_julia_path

            julia_dir = str(get_julia_path())
            _sp.Popen(
                [julia_exe, f"--project={julia_dir}", "-e",
                 "using Pkg; Pkg.precompile()"],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
        except Exception:
            pass  # best-effort, never block the UI

    def _sync_systems_to_form(self):
        """Notify the global settings form about available systems."""
        form = self.properties_panel.get_form("global_settings")
        if form is not None:
            form.set_available_systems(list(self._all_states.keys()))

    def _invalidate_validation(self, *_args):
        """Mark validation as stale so the user must re-validate before running."""
        self._validated_ok = False
        self._run_completed = False
        self.toolbar.set_run_enabled(False)
        self.toolbar.set_validate_enabled(True)

    def _on_run_requested(self):
        """Save current config to a temp file and launch the simulation."""
        if self._loaded_config is None:
            QMessageBox.warning(
                self, tr("common.warning"),
                "Import a YAML configuration before running the simulation.",
            )
            return

        import tempfile
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog

        # Save current editing state
        if self._current_system_name:
            self._all_states[self._current_system_name] = self.model.state

        # Use previously saved output directory, or fall back to the
        # CLI's default ``./results`` (cli.py:218) so the panel and the
        # runner agree on the location regardless of where the config
        # file lives.
        if not hasattr(self, "_last_output_dir") or not self._last_output_dir:
            self._last_output_dir = "results"

        output_dir = self._last_output_dir

        # Save config to a temporary YAML file
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix=".yaml", prefix="esfex_run_", delete=False,
            )
            tmp_path = tmp.name
            tmp.close()

            gui_state_to_yaml(
                self._all_states, self._loaded_config, tmp_path,
                inter_system_links=self.model.inter_system_links,
                global_settings=self.model.global_settings,
                stochastic_scenarios=self.model.stochastic_scenarios,
            )
        except Exception as e:
            QMessageBox.critical(
                self, tr("common.error"),
                f"Failed to save configuration for simulation:\n{e}",
            )
            return

        # Run simulation as subprocess in the console
        cmd = [sys.executable, "-m", "esfex.cli", "run",
               "--config", tmp_path, "--output", output_dir]
        if self.model.global_settings.console_log_level == "high":
            cmd.append("--verbose")

        # Pass sysimage path to subprocess for faster Julia startup
        env_extra: dict[str, str] = {}
        try:
            from esfex.bridge.julia_setup import _find_sysimage

            sysimage = _find_sysimage()
            if sysimage:
                env_extra["PYTHON_JULIACALL_SYSIMAGE"] = str(sysimage)
        except Exception:
            pass

        self.toolbar.set_run_enabled(False)
        # Surface the run output on the dedicated terminal tab. If the
        # xterm runner isn't available (e.g. ptyprocess missing on this
        # install) the RunOutputView writes a notice into the terminal
        # itself, and we fall back to the legacy QProcess console so
        # the user still sees something.
        from esfex.visualization.pty_runner import PtyUnavailable
        try:
            # Switch to the Run Output tab before spawning so the user
            # sees activity immediately even on slow first paint.
            if hasattr(self, "_console_tabs"):
                self._console_tabs.setCurrentWidget(self.run_output)
            self.run_output.run(cmd, env=env_extra or None)
            # Give the terminal keyboard focus so Ctrl+C reaches xterm
            # (which forwards it as SIGINT to the PTY) without an extra
            # click into the terminal first.
            self.run_output.setFocus()
            # Route completion back to the existing handler so the
            # toolbar / results panel updates the same way they did
            # under the old QProcess path. Disconnect first to avoid
            # stacked connections after repeated runs.
            try:
                self.run_output.finished.disconnect(self._on_subprocess_finished)
            except (TypeError, RuntimeError):
                pass
            self.run_output.finished.connect(self._on_subprocess_finished)
        except PtyUnavailable:
            # Fall back to the previous QProcess console.
            self.console.run_subprocess(
                cmd, label="ESFEX Simulation", env=env_extra or None,
            )

    # ------------------------------------------------------------------
    # Run cancellation (Stop bar + Ctrl+C)
    # ------------------------------------------------------------------

    def _on_run_started_ui(self):
        """Show the Stop bar when a simulation run begins."""
        self._stop_escalated = False
        self._stop_btn.setText("■  Stop")
        self._stop_btn.setEnabled(True)
        self._run_status_label.setText("Running…")
        self._run_stop_bar.setVisible(True)

    def _on_run_finished_ui(self, *_):
        """Hide the Stop bar when the run ends (success, error, or kill)."""
        self._run_stop_bar.setVisible(False)
        self._stop_escalated = False
        # A finished run typically produced fresh results in
        # _last_output_dir — drop the cached Results dialog so the next
        # open picks them up instead of replaying the previous render.
        self._invalidate_results_dialog_cache()

    def _invalidate_results_dialog_cache(self):
        """Destroy the cached ResultsDialog (if any) and clear the key.

        Called whenever a fresh result file appears in the active
        output directory — at the end of a simulation, after the user
        explicitly loads a new file, etc. ``_show_results`` will then
        build a new dialog next time it's invoked."""
        dlg = getattr(self, "_results_dialog", None)
        if dlg is not None:
            try:
                dlg.force_close()
            except Exception:
                pass
        self._results_dialog = None
        self._results_dialog_key = None

    def _on_stop_run(self):
        """Cancel the running simulation.

        First press sends SIGINT — graceful, identical to Ctrl+C, so
        ESFEX gets a chance to unwind cleanly. Second press escalates
        to a force kill for a process that ignores the interrupt.
        """
        run_output = getattr(self, "run_output", None)
        if run_output is None or not run_output.is_running():
            return
        if not self._stop_escalated:
            run_output.interrupt()
            self._stop_escalated = True
            self._stop_btn.setText("■  Force kill")
            self._run_status_label.setText(
                "Interrupt sent — press again to force-kill"
            )
        else:
            run_output.stop(force=True)
            self._stop_btn.setEnabled(False)
            self._run_status_label.setText("Force kill sent…")

    def _on_subprocess_finished(self, exit_code: int):
        """Enable the Results and Sensitivity buttons when a simulation completes."""
        if exit_code == 0:
            self.toolbar.set_results_enabled(True)
            self.toolbar.set_sensitivity_enabled(True)
            self._run_completed = True
            self.toolbar.set_run_enabled(False)
            # Pre-set output dir on results panel so it's ready
            output_dir = getattr(self, "_last_output_dir", "")
            if output_dir:
                self._results_panel.set_output_dir(output_dir)
        else:
            # Re-enable Run so the user can retry after fixing issues
            self.toolbar.set_run_enabled(True)

    def _on_load_results(self):
        """Open a file picker to load previous HDF5 results."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu.load_results"), str(Path.home()),
            "HDF5 Files (*.h5 *.hdf5);;All Files (*)",
        )
        if not path:
            return
        d = str(Path(path).parent)
        self._last_output_dir = d
        self._last_results_file = path
        self.toolbar.set_results_enabled(True)
        self.toolbar.set_sensitivity_enabled(True)
        # Point the map results panel at the specific file
        self._results_panel.set_output_dir(d)
        # Drop the cached Results dialog so the new file is read fresh
        # instead of reusing the previous render. ``_show_results`` does
        # the same when the key differs, but invalidating here covers
        # the case where the user picked the same path twice (the cache
        # key would otherwise match and the stale dialog would surface).
        self._invalidate_results_dialog_cache()
        # Switch to results layer and open the results dialog
        self._show_results()

    def _on_results_requested(self):
        """Handle the Results toolbar button click."""
        output_dir = getattr(self, "_last_output_dir", "")
        if not output_dir:
            QMessageBox.warning(
                self, tr("common.warning"),
                "No simulation output directory is set. Run a simulation first.",
            )
            return
        self._show_results()

    def _show_results(self):
        """Switch to the Results layer and open the ResultsDialog.

        Reuses the existing dialog instance when the result source has
        not changed. Closing the dialog only hides it, so charts and
        the HDF5 cache stay warm; re-opening shows them instantly
        instead of re-rendering the full batch (5–15 s)."""
        # Activate results layer on the map
        results_label = tr("layers.results")
        if self.toolbar._layer_combo.currentText() == results_label:
            self._activate_results_layer()
        else:
            self.toolbar._layer_combo.setCurrentText(results_label)
        output_dir = getattr(self, "_last_output_dir", "")
        specific_file = getattr(self, "_last_results_file", "")
        if not output_dir:
            return

        # Cache key — when both elements match the prior open the
        # rendered dialog is still valid and we can just show it again.
        # ``specific_file`` is normalised to "" because the host clears
        # it after each successful open (so the next round-trip would
        # otherwise look like a fresh request even when nothing changed).
        key = (output_dir, specific_file)
        cached = getattr(self, "_results_dialog", None)
        cached_key = getattr(self, "_results_dialog_key", None)
        if (cached is not None
                and cached_key is not None
                and (cached_key == key
                     # No file change at all → reuse regardless of
                     # specific_file (which is consumed after each open).
                     or (not specific_file and cached_key[0] == output_dir))):
            cached.show()
            cached.raise_()
            cached.activateWindow()
            # Consume specific_file as before so subsequent opens use
            # the directory scan.
            self._last_results_file = ""
            return

        # Source changed (different output dir or new specific file) —
        # tear down the previous dialog (releasing its HDF5 handle)
        # before creating a fresh one.
        if cached is not None:
            try:
                cached.force_close()
            except Exception:
                pass
            self._results_dialog = None
            self._results_dialog_key = None

        dlg = ResultsDialog(
            output_dir, self.map_widget, parent=self,
            results_file=specific_file,
        )
        self._last_results_file = ""  # Clear so next open uses directory scan
        # Non-modal: the user must be able to keep interacting with
        # the main window (map, config, …) while the Results Viewer
        # is open. ``exec()`` would block the whole app; ``show()``
        # runs it modeless. Keep a reference so it isn't garbage
        # collected the moment this method returns.
        dlg.setModal(False)
        self._results_dialog = dlg
        self._results_dialog_key = key
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_sensitivity_requested(self):
        """Open the Global Sensitivity Analysis dialog."""
        from esfex.visualization.panels.sensitivity_dialog import SensitivityDialog

        output_dir = getattr(self, "_last_output_dir", "")
        if not output_dir:
            QMessageBox.warning(
                self, tr("common.warning"),
                "Run a simulation first to generate LP files for analysis.",
            )
            return

        config_path = getattr(self, "_config_path", "") or ""
        dlg = SensitivityDialog(config_path, output_dir, parent=self)
        dlg.exec()

    # ------------------------------------------------------------------
    # GeoJSON Import
    # ------------------------------------------------------------------

    def _on_import_geojson(self):
        """Import nodes, lines, and zones from a GeoJSON file."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Import GeoJSON", "", "GeoJSON Files (*.geojson *.json)"
        )
        if not path:
            return

        try:
            from esfex.visualization.data.geojson_importer import import_geojson

            result = import_geojson(self.model.state, path)

            # Refresh the view
            self.model.stateLoaded.emit()

            # Show results
            lines = [
                f"Nodes added: {result.nodes_added}",
                f"Lines added: {result.lines_added}",
                f"Zones added: {result.zones_added}",
            ]
            if result.warnings:
                lines.append(f"\nWarnings ({len(result.warnings)}):")
                for w in result.warnings:
                    lines.append(f"  - {w}")
            if result.errors:
                lines.append(f"\nErrors ({len(result.errors)}):")
                for e in result.errors:
                    lines.append(f"  - {e}")

            QMessageBox.information(self, tr("common.info"), "\n".join(lines))

            # Auto-run validation after import
            from esfex.visualization.data.validation import validate_state
            issues = validate_state(self.model.state)
            if issues:
                errors = [i for i in issues if i.severity == "error"]
                warnings = [i for i in issues if i.severity == "warning"]
                if errors or warnings:
                    parts = []
                    if errors:
                        parts.append(f"{len(errors)} error(s)")
                    if warnings:
                        parts.append(f"{len(warnings)} warning(s)")
                    QMessageBox.warning(
                        self, tr("common.warning"),
                        f"Validation found {', '.join(parts)}. "
                        "Click 'Validate' for details."
                    )
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, tr("common.error"), f"Failed to import:\n{e}"
            )
