"""Results visualization dialog — professional dashboard with matplotlib charts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QSize, QTimer, QEventLoop, QPoint, QEvent
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from esfex.visualization.i18n import tr
from esfex.visualization.panels.results_cache import (
    ResultsCache,
    activate as _activate_cache,
)
from esfex.visualization.panels.results_charts import (
    ResultsChartBase,
    _CHART_CLASSES,
    _available_chart_classes,
    _detect_capabilities,
)
from esfex.visualization.theme import current_theme

logger = logging.getLogger(__name__)

# Chart category grouping for the sidebar
_CHART_CATEGORIES = {
    "Generation": [
        "GenerationMixChart",
    ],
    "Storage": [
        "BatteryHeatmapChart",
        "BatteryOperationChart",
    ],
    "Economics": [
        "CFLcoeVallcoeChart",
        "ElectricityCostChart",
        "RevenueProfitabilityChart",
        "PriceDurationChart",
        "CarbonPenaltyChart",
        "CashFlowChart",
    ],
    "System": [
        "SystemMetricsEvolutionChart",
        "NetLoadHeatmapChart",
        "InterNodeFlowsChart",
        "FuelSupplyChart",
        "SankeyEnergyFlowChart",
        "FlexReliabilityChart",
    ],
    "UC Operations": [
        # Pricing
        "UCHourlyPriceChart",
        "UCPriceDurationChart",
        "UCLMPByNodeChart",
        "UCMarginalTechChart",
        # Commitment & dispatch
        "UCCommitmentHeatmapChart",
        "UCDispatchStackChart",
        "UCRampDistributionChart",
        "UCNetLoadDurationChart",
        # Adequacy
        "UCStorageSOCChart",
        "UCLoadShedCurtailmentChart",
    ],
    "Near-Optimal": [
        "MGARobustnessFrontierChart",
        "MGAParcoordsChart",
        "MGAPathwayChart",
        "MGASpatialChart",
        "MGAProjectionChart",
        "MGAAnnotatedDendrogramChart",
        "MGADecisionFactorsChart",
        "MGACompositionChart",
        "MGASimilarityChart",
    ],
    "Custom": [
        "CustomChart",
    ],
}

# Reverse map: class name -> category
_CLASS_TO_CATEGORY: dict[str, str] = {}
for _cat, _names in _CHART_CATEGORIES.items():
    for _n in _names:
        _CLASS_TO_CATEGORY[_n] = _cat

# Export format filter strings
_EXPORT_FILTERS = {
    "PNG": "PNG Image (*.png)",
    "SVG": "SVG Vector (*.svg)",
    "PDF": "PDF Document (*.pdf)",
    "JPG": "JPEG Image (*.jpg)",
    "TIFF": "TIFF Image (*.tiff)",
}


class _SidebarSeparator(QFrame):
    """Thin horizontal line for sidebar sections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setFixedHeight(1)


class _BusyOverlay(QWidget):
    """Dimming overlay with a progress card, shown while charts load.

    Implemented as a **top-level frameless window** positioned exactly
    over the dialog — NOT a child widget. The Results dialog embeds
    several ``QWebEngineView``s whose native compositing surfaces paint
    over any sibling/child Qt widget, so a child overlay would be
    hidden behind the visible chart. A top-level window composites
    above them. It tracks the dialog's geometry via an event filter so
    it stays glued on move/resize.
    """

    _SCRIM_ALPHA = 170  # ~67% black — clearly dims without full black-out

    def __init__(self, parent: QWidget):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self._owner = parent
        # Translucent so paintEvent's scrim alpha is honoured; mouse
        # events are swallowed (not transparent) to lock interaction.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        container = QWidget(self)
        container.setFixedSize(320, 100)
        container.setStyleSheet("background: #ffffff; border-radius: 8px;")

        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(24, 18, 24, 18)
        vbox.setSpacing(10)

        self._label = QLabel("Loading charts...")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #2C3E50; font-size: 13px; background: transparent;")
        vbox.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setStyleSheet("""
            QProgressBar { background: #E8ECEF; border: none; border-radius: 4px; }
            QProgressBar::chunk { background: #2980B9; border-radius: 4px; }
        """)
        vbox.addWidget(self._progress)
        self._container = container
        # Follow the dialog as it moves / resizes.
        parent.installEventFilter(self)
        self.hide()

    def paintEvent(self, event):
        # The translucent window paints nothing by default; draw the
        # dimming scrim ourselves so the alpha is respected.
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, self._SCRIM_ALPHA))

    def eventFilter(self, obj, event):
        if obj is self._owner and self.isVisible() and event.type() in (
            QEvent.Type.Move, QEvent.Type.Resize,
        ):
            self._sync_geometry()
        return super().eventFilter(obj, event)

    def _sync_geometry(self):
        """Cover exactly the owner dialog in global screen coords."""
        try:
            tl = self._owner.mapToGlobal(QPoint(0, 0))
            self.setGeometry(tl.x(), tl.y(),
                             self._owner.width(), self._owner.height())
            self._center()
        except RuntimeError:
            pass

    def start(self, message: str = "Loading charts...", total: int = 100):
        self._restyle()
        self._label.setText(message)
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._sync_geometry()
        self.show()
        self.raise_()
        QApplication.processEvents()

    def _restyle(self):
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
                    background: {c.surface_secondary}; border: none; border-radius: 4px;
                }}
                QProgressBar::chunk {{
                    background: {c.accent_primary}; border-radius: 4px;
                }}
            """)
        except Exception:
            pass

    def update_progress(self, value: int, message: str | None = None):
        self._progress.setValue(value)
        if message is not None:
            self._label.setText(message)
        # Keep glued to the dialog in case it was moved between ticks.
        self._sync_geometry()
        QApplication.processEvents()

    def finish(self):
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center()

    def _center(self):
        c = self._container
        x = (self.width() - c.width()) // 2
        y = (self.height() - c.height()) // 2
        c.move(x, y)


class ResultsDialog(QDialog):
    """Dashboard dialog for viewing simulation results.

    Layout: left sidebar (system/year/chart list) | main content (chart + toolbar).
    Uses matplotlib for charts rendered as native Qt widgets.
    """

    def __init__(self, results_dir: str, map_widget: Any, parent=None,
                 results_file: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("results_dialog.title"))
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setMinimumSize(1100, 750)
        self.resize(1400, 850)
        self._results_dir = Path(results_dir)
        self._results_file = Path(results_file) if results_file else None
        self._map_widget = map_widget
        self._h5_files: dict[str, Path] = {}
        self._base_prefix: dict[str, str] = {}

        self._current_chart_idx: int = -1
        # Cache holds the open HDF5 handle + memoised configs/scenarios
        # for the current system. Recreated on system switch, closed
        # on dialog close. Set before _build_ui because
        # _load_system_years (called from there) populates it.
        self._cache: Optional[ResultsCache] = None
        # Set by closeEvent so delayed timers (e.g. theme reapply)
        # can short-circuit instead of touching deleted Qt objects.
        self._closing: bool = False

        self._scan_results()
        self._build_ui()
        self._apply_dashboard_style()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _scan_results(self):
        """Find HDF5 result files in the output directory."""
        # If a specific file was requested, use only that file
        if self._results_file and self._results_file.exists():
            all_h5 = [self._results_file]
        elif self._results_dir.exists():
            # Collect all non-MGA HDF5 files, prefer most recent
            all_h5 = []
            for p in self._results_dir.glob("*.h5"):
                if p.stem.startswith("mga_"):
                    continue
                all_h5.append(p)
            if not all_h5:
                for p in self._results_dir.rglob("*.h5"):
                    if p.stem.startswith("mga_"):
                        continue
                    all_h5.append(p)
            # Sort by modification time (newest first) so we prefer the latest run
            all_h5.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        else:
            return

        import h5py
        for p in all_h5:
            try:
                with h5py.File(p, "r") as f:
                    # Multi-system runs publish their member names as a
                    # root attribute; we no longer require the (legacy)
                    # ``/systems/`` group to exist.
                    sub_names = f.attrs.get("subsystem_names")
                    if sub_names is None and "systems" in f:
                        sub_names = list(f["systems"].keys())
                    if sub_names is not None and len(sub_names) > 0:
                        names_list = [
                            n.decode() if isinstance(n, bytes) else str(n)
                            for n in sub_names
                        ]
                        for sname in names_list:
                            if sname not in self._h5_files:
                                self._h5_files[sname] = p
                                self._base_prefix[sname] = f"systems/{sname}"
                        if "Global" not in self._h5_files:
                            self._h5_files["Global"] = p
                            self._base_prefix["Global"] = ""
                    else:
                        name = p.stem.replace("results_", "").replace("_results", "")
                        if name not in self._h5_files:
                            self._h5_files[name] = p
                            self._base_prefix[name] = ""
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        # ═══════════════════════════════════════════════════════════════
        # LEFT SIDEBAR
        # ═══════════════════════════════════════════════════════════════
        sidebar = QWidget()
        self._sidebar = sidebar
        sidebar.setObjectName("dashboardSidebar")
        sidebar.setMinimumWidth(220)
        sidebar.setMaximumWidth(300)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(12, 16, 12, 12)
        sb.setSpacing(6)

        # System combo and year slider live in the top toolbar
        # (built further down in the main content area) — the sidebar
        # carries only the chart navigation list. Construct the combo
        # now so callers that rely on ``_system_combo`` during ``_build_ui``
        # see it, but install it into the toolbar layout below.
        self._system_combo = QComboBox()
        self._system_combo.setObjectName("topbarCombo")
        self._system_combo.addItems(
            list(self._h5_files.keys()) or [tr("results_panel.no_results")]
        )
        self._system_combo.currentTextChanged.connect(self._on_system_changed)

        # -- Chart list --
        lbl_charts = QLabel("Charts")
        lbl_charts.setObjectName("sidebarLabel")
        sb.addWidget(lbl_charts)

        self._chart_list = QListWidget()
        self._chart_list.setObjectName("chartList")
        self._chart_list.setSpacing(1)

        # Instantiate all matplotlib chart objects
        self._charts: list[ResultsChartBase] = []
        self._chart_stack = QStackedWidget()
        self._params_stack = QStackedWidget()

        # ── Dashboard entry (first item in the sidebar) ────────────
        # Same sidebar / stack pattern as the matplotlib charts so it
        # feels native rather than bolted on. Tagged via ItemDataRole
        # so the chart-selection handler can route to it without
        # depending on the row index (which drifts as categories
        # change).
        self._dashboard_view = None
        try:
            from esfex.visualization.panels.dashboard_view import DashboardView
            self._dashboard_view = DashboardView()
        except Exception as exc:
            logger.exception("DashboardView could not be constructed: %s", exc)

        if self._dashboard_view is not None:
            dash_header = QListWidgetItem("  DASHBOARD")
            dash_header.setFlags(Qt.ItemFlag.NoItemFlags)
            fnt = dash_header.font(); fnt.setBold(True)
            fnt.setPointSize(fnt.pointSize() - 1)
            dash_header.setFont(fnt)
            dash_header.setSizeHint(QSize(0, 24))
            self._chart_list.addItem(dash_header)

            dash_item = QListWidgetItem("    Overview")
            dash_item.setSizeHint(QSize(0, 28))
            # Sentinel: lets the selection handler recognise this
            # entry without relying on a fragile index.
            dash_item.setData(Qt.ItemDataRole.UserRole, "dashboard")
            self._chart_list.addItem(dash_item)

            # Wrap in a container that mirrors the chart-page layout
            # so the QStackedWidget swap looks consistent.
            dash_container = QWidget()
            dash_layout = QVBoxLayout(dash_container)
            dash_layout.setContentsMargins(4, 4, 4, 4)
            dash_layout.addWidget(self._dashboard_view, 1)
            # Stack index 0 = dashboard; charts are appended at 1+
            # by the loop below.
            self._chart_stack.addWidget(dash_container)
            # Empty params slot for the dashboard (its filters live
            # inside the WebView, not in the matplotlib params bar).
            self._params_stack.addWidget(QWidget())

        # Filter the chart class list against the active HDF5's
        # capabilities — e.g. drop MGA / SPORES charts when the run
        # didn't produce a /mga/ group, drop fuel-supply charts when
        # primary energy is disabled, etc. The user never sees an
        # entry whose data is missing.
        first_path = next(iter(self._h5_files.values()), None) \
            if self._h5_files else None
        chart_classes = _available_chart_classes(first_path) \
            if first_path else list(_CHART_CLASSES)

        prev_category = ""
        for cls in chart_classes:
            chart = cls()
            self._charts.append(chart)

            # Category header
            cat = _CLASS_TO_CATEGORY.get(cls.__name__, "")
            if cat and cat != prev_category:
                header = QListWidgetItem(f"  {cat.upper()}")
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setForeground(
                    self._chart_list.palette().color(
                        self._chart_list.foregroundRole()
                    )
                )
                font = header.font()
                font.setBold(True)
                font.setPointSize(font.pointSize() - 1)
                header.setFont(font)
                header.setSizeHint(QSize(0, 24))
                self._chart_list.addItem(header)
                prev_category = cat

            # Chart entry
            chart_title = tr(chart.TR_KEY) if chart.TR_KEY else chart.TITLE
            item = QListWidgetItem(f"    {chart_title}")
            item.setSizeHint(QSize(0, 28))
            self._chart_list.addItem(item)

            # Chart page (canvas + navigation toolbar inside scroll area)
            container = QWidget()
            clayout = QVBoxLayout(container)
            clayout.setContentsMargins(4, 4, 4, 4)
            # Set minimum height so multi-subplot charts aren't squished
            if chart.fig is not None:
                chart.setMinimumHeight(int(chart.fig.get_figheight() * chart.fig.get_dpi()))
            else:
                chart.setMinimumHeight(600)
            clayout.addWidget(chart, 1)
            if isinstance(chart, FigureCanvasQTAgg):
                nav = NavigationToolbar2QT(chart, container)
                clayout.addWidget(nav)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(container)
            self._chart_stack.addWidget(scroll)

            # Params widget page (empty placeholder if no params)
            params = chart.get_params_widget()
            if params is not None:
                params_wrapper = QWidget()
                params_wrapper.setObjectName("chartParamsBar")
                pw_layout = QHBoxLayout(params_wrapper)
                pw_layout.setContentsMargins(16, 4, 16, 4)
                # Stretch the params widget to fill the bar width. Compact
                # param widgets keep their own trailing stretch, so they
                # still left-align; wider ones (Custom config) fill.
                pw_layout.addWidget(params, 1)
                self._params_stack.addWidget(params_wrapper)
            else:
                self._params_stack.addWidget(QWidget())

        self._chart_list.currentRowChanged.connect(self._on_chart_list_changed)
        # The params bar is a QStackedWidget; its natural sizeHint is the
        # MAX height over all pages, so the tall Custom-chart config table
        # would reserve blank space above every other chart. Cap the bar
        # to the current page's height instead.
        self._params_stack.currentChanged.connect(self._fit_params_height)
        sb.addWidget(self._chart_list, 1)

        splitter.addWidget(sidebar)

        # ═══════════════════════════════════════════════════════════════
        # MAIN CONTENT AREA
        # ═══════════════════════════════════════════════════════════════
        content = QWidget()
        content.setObjectName("dashboardContent")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # Title bar with chart name + export buttons
        title_bar = QWidget()
        title_bar.setObjectName("chartTitleBar")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(16, 8, 16, 8)

        self._chart_title_label = QLabel("")
        self._chart_title_label.setObjectName("chartTitle")
        tb_layout.addWidget(self._chart_title_label)
        tb_layout.addStretch()

        # System combo (canonical — also drives the embedded dashboard).
        self._system_label = QLabel(tr("results_panel.system"))
        self._system_label.setObjectName("topbarLabel")
        tb_layout.addWidget(self._system_label)
        self._system_combo.setMinimumWidth(140)
        tb_layout.addWidget(self._system_combo)
        tb_layout.addSpacing(16)

        # Year range slider — covers every multi-year chart. Single-year
        # charts (Sankey, Net Load Heatmap) have their own combo in their
        # params widget. ``QRangeSlider`` (superqt) gives two handles on a
        # single track; default is the full range so nothing is filtered
        # out until the user narrows it.
        self._year_label = QLabel("Years:")
        self._year_label.setObjectName("yearSliderLabel")
        tb_layout.addWidget(self._year_label)

        from superqt import QRangeSlider
        self._year_slider = QRangeSlider(Qt.Orientation.Horizontal)
        self._year_slider.setObjectName("yearSlider")
        self._year_slider.setFixedWidth(300)
        self._year_slider.setMinimum(0)
        self._year_slider.setMaximum(0)
        self._year_slider.setSingleStep(1)
        self._year_slider.setPageStep(1)
        self._year_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._year_slider.setTickInterval(1)
        self._style_year_slider()
        # Re-render only on release — full-batch re-rendering on every
        # intermediate value would freeze the slider drag (each step
        # invalidates and re-renders every chart, which takes seconds).
        # While dragging we still update the year-range label so the
        # user sees live feedback.
        self._year_slider.valueChanged.connect(self._on_year_slider_dragging)
        self._year_slider.sliderReleased.connect(self._on_year_slider_released)
        tb_layout.addWidget(self._year_slider)

        self._year_value_label = QLabel("")
        self._year_value_label.setObjectName("yearValueLabel")
        self._year_value_label.setMinimumWidth(110)
        tb_layout.addWidget(self._year_value_label)

        tb_layout.addSpacing(16)
        self._years_list: list[int] = []  # populated by _on_system_changed

        # Font-size slider: 50–250 → 0.5×–2.5× scale applied to every
        # Plotly chart in the dialog via the `setFontScale` JS helper.
        # Matplotlib charts ignore it (no JS bridge) — they're being
        # phased out as we migrate to interactive Plotly versions.
        tb_layout.addWidget(QLabel("Font:"))
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setObjectName("fontSlider")
        self._font_slider.setFixedWidth(140)
        self._font_slider.setRange(50, 250)
        self._font_slider.setValue(100)
        self._font_slider.setSingleStep(5)
        self._font_slider.setTickInterval(25)
        self._font_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._font_slider.valueChanged.connect(self._on_font_scale_changed)
        tb_layout.addWidget(self._font_slider)
        self._font_value_label = QLabel("1.0x")
        self._font_value_label.setObjectName("fontValueLabel")
        self._font_value_label.setFixedWidth(40)
        tb_layout.addWidget(self._font_value_label)
        tb_layout.addSpacing(16)

        # Export format selector + button
        self._export_format = QComboBox()
        self._export_format.setObjectName("exportCombo")
        self._export_format.addItems(list(_EXPORT_FILTERS.keys()))
        self._export_format.setCurrentText("PNG")
        self._export_format.setFixedWidth(90)
        tb_layout.addWidget(self._export_format)

        self._export_btn = QPushButton("Export")
        self._export_btn.setObjectName("exportButton")
        self._export_btn.clicked.connect(self._on_export)
        tb_layout.addWidget(self._export_btn)

        cl.addWidget(title_bar)

        # Params bar (stacked — shows params for selected chart)
        cl.addWidget(self._params_stack)

        # Chart area (stacked — shows one chart at a time)
        cl.addWidget(self._chart_stack, 1)

        splitter.addWidget(content)

        # Splitter proportions
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 900])

        # Loading overlay
        self._overlay = _BusyOverlay(self)

        # Now that scanning populated self._h5_files, push them to
        # the dashboard so its filter bar can populate the system
        # combo on first load.
        if self._dashboard_view is not None:
            self._dashboard_view.set_sources(self._h5_files, self._base_prefix)

        # Select first actual chart item (skip category headers)
        self._select_first_chart()

        # Load years (but defer chart rendering to after window is shown)
        if self._h5_files:
            self._load_system_years(self._system_combo.currentText())

    # ------------------------------------------------------------------
    # Chart selection & rendering
    # ------------------------------------------------------------------

    def _select_first_chart(self):
        """Select the first selectable item — Dashboard if present,
        otherwise the first matplotlib chart."""
        for row in range(self._chart_list.count()):
            item = self._chart_list.item(row)
            if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self._chart_list.setCurrentRow(row)
                break

    def _is_dashboard_item(self, item: Optional[QListWidgetItem]) -> bool:
        return bool(item) and item.data(Qt.ItemDataRole.UserRole) == "dashboard"

    def _has_dashboard_entry(self) -> bool:
        """True iff the Dashboard sidebar item was actually added.

        DashboardView construction is best-effort (try/except above),
        so the stack/list may or may not carry the entry. Callers use
        this to decide whether stack index 0 is the dashboard.
        """
        return self._dashboard_view is not None

    def _selectable_index_for_row(self, row: int) -> int:
        """Index into ``self._charts`` for a sidebar row.

        Returns -1 if the row points at the Dashboard entry (which is
        not a matplotlib chart). When the dashboard entry exists, the
        chart stack widget at index 0 is the dashboard and the
        matplotlib charts start at stack index 1.
        """
        count = 0
        for r in range(row + 1):
            it = self._chart_list.item(r)
            if it and it.flags() & Qt.ItemFlag.ItemIsSelectable:
                count += 1
        # If Dashboard is present and is the first selectable, subtract
        # 2 (one for the Dashboard slot, one to convert to 0-based).
        # If Dashboard is absent, subtract 1 (just the 0-based shift).
        offset = 2 if self._has_dashboard_entry() else 1
        return count - offset

    def _stack_index_for_chart(self, chart_idx: int) -> int:
        """Translate a matplotlib chart index to its stack widget index."""
        # If the dashboard occupies stack[0], shift everything by 1.
        return chart_idx + (1 if self._has_dashboard_entry() else 0)

    def _on_chart_list_changed(self, row: int):
        """Handle chart selection from sidebar list."""
        item = self._chart_list.item(row)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            return

        if self._is_dashboard_item(item):
            # Dashboard always sits at stack index 0 when present.
            self._current_chart_idx = -1
            self._chart_title_label.setText("Dashboard")
            self._params_stack.setCurrentIndex(0)
            self._fit_params_height()
            self._chart_stack.setCurrentIndex(0)
            return

        chart_idx = self._selectable_index_for_row(row)
        if 0 <= chart_idx < len(self._charts):
            self._current_chart_idx = chart_idx
            chart = self._charts[chart_idx]

            # Update title
            title = tr(chart.TR_KEY) if chart.TR_KEY else chart.TITLE
            self._chart_title_label.setText(title)

            # Show params and chart for this index
            stack_idx = self._stack_index_for_chart(chart_idx)
            self._params_stack.setCurrentIndex(stack_idx)
            self._fit_params_height()
            self._chart_stack.setCurrentIndex(stack_idx)

    def _fit_params_height(self, _idx: int = 0):
        """Cap the params bar to the current page's height so a tall page
        (Custom config table) doesn't reserve blank space on other charts."""
        w = self._params_stack.currentWidget()
        if w is None:
            return
        h = w.sizeHint().height()
        self._params_stack.setMaximumHeight(max(0, h))

    def _render_chart(self, idx: int):
        """Render a single chart. Caller is responsible for wrapping
        the call in ``_activate_cache(self._cache)``."""
        system_name = self._system_combo.currentText()
        path = self._h5_files.get(system_name)
        if not path or not path.exists():
            return
        if not self._years_list:
            return

        bp = self._base_prefix.get(system_name, "")
        year_range = self._current_year_range()
        chart = self._charts[idx]
        chart._safe_update(path, self._years_list, base_prefix=bp,
                           year_range=year_range)

    def _render_all_charts(self):
        """Render every chart up-front with a progress overlay.

        Combined with ``ResultsCache`` keeping the HDF5 handle and
        configs warm, the full batch takes ~5-15 s instead of the
        ~1 min it took before the cache existed. After this the user
        can switch charts instantly — nothing gets re-rendered until
        the year slider / system combo invalidates them.

        While iterating, we *bring each chart's QStackedWidget page
        to the front before rendering it*. That way the underlying
        QWebEngineView is actually visible and correctly sized when
        Plotly takes its measurements — otherwise the WebView's
        first paint happens at the wrong size and reappears
        "miniaturised in the top-left, then expanding" the next
        time the user picks it. The loading overlay covers the
        rapid swaps so the user only sees the progress bar.
        """
        system_name = self._system_combo.currentText()
        path = self._h5_files.get(system_name)
        if not path or not path.exists():
            return
        if not self._years_list:
            return

        total = len(self._charts)
        self._overlay.start("Rendering charts...", total)
        # Dim + lock the dialog while the batch runs. The sidebar holds
        # the only non-WebView interactive controls (chart list, system
        # combo, MGA table) — disabling it greys them out and blocks
        # navigation; the overlay on top blocks the rest (year/font
        # sliders, export). We deliberately do NOT disable the chart
        # stack, so the QWebEngineViews still paint at full size while
        # visible (the "render-while-visible" guarantee that prevents
        # the resize flash). Non-modal → only the Results Viewer is
        # locked; the main window stays usable.
        self._sidebar.setEnabled(False)

        bp = self._base_prefix.get(system_name, "")
        year_range = self._current_year_range()
        # Remember the page the user was looking at so we can restore
        # it once the batch is done.
        original_stack_idx = self._chart_stack.currentIndex()

        try:
            with _activate_cache(self._cache):
                # Render the Dashboard as part of the batch so it's ready
                # (and covered by the progress overlay) when the dialog
                # opens, just like the charts.
                self._render_dashboard_in_batch()
                for i, chart in enumerate(self._charts):
                    title = tr(chart.TR_KEY) if chart.TR_KEY else chart.TITLE
                    self._overlay.update_progress(i, f"Rendering: {title}")
                    # Make the chart's QWebEngineView the visible page in
                    # the stack so its size is well-defined when Plotly
                    # measures. Keep the overlay on top so the user only
                    # sees the progress bar, not the swaps.
                    target_idx = self._stack_index_for_chart(i)
                    self._chart_stack.setCurrentIndex(target_idx)
                    self._overlay.raise_()
                    QApplication.processEvents()
                    # Phase 1: wait until the WebView's HTML, plotly.min.js
                    # and the QWebChannel bridge are live. Without this,
                    # the runJavaScript("refresh()") fired by _safe_update
                    # lands before `refresh` exists and is a silent no-op.
                    if not self._wait_for_chart_ready(chart):
                        # Demoted from WARNING: this fires for charts whose
                        # QWebChannel bridge hasn't bound yet (lazy WebViews)
                        # — they paint on demand once the user navigates to
                        # them, so skipping during the batch is expected
                        # and not noteworthy.
                        logger.debug(
                            "Chart %s not ready within timeout — skipping",
                            chart.TITLE,
                        )
                        continue
                    try:
                        chart._safe_update(path, self._years_list,
                                           base_prefix=bp,
                                           year_range=year_range)
                        # Phase 2: ``_safe_update`` only enqueues the
                        # render in JS; block until Plotly has actually
                        # painted so the next iteration's stack swap
                        # doesn't move this view off-screen mid-paint.
                        if not self._wait_for_chart_rendered(chart):
                            # Demoted from WARNING for the same reason as
                            # the readiness check above — Plotly's paint
                            # signal arrives late on big figures, but the
                            # chart is rendered correctly by the time the
                            # user clicks it.
                            logger.debug(
                                "Chart %s did not finish painting within timeout",
                                chart.TITLE,
                            )
                    except Exception:
                        logger.exception("Failed to render chart %s", chart.TITLE)

            # Restore the page the user was on.
            self._chart_stack.setCurrentIndex(original_stack_idx)
        finally:
            # Drop the transient per-scenario array cache so it doesn't
            # hold the whole horizon's data in RAM between batches.
            if self._cache is not None:
                self._cache.clear_scenario_data()
            # Always re-enable + drop the overlay, even if a render
            # raised, so the dialog never gets stuck dimmed/locked.
            self._sidebar.setEnabled(True)
            self._overlay.update_progress(total, "Done")
            self._overlay.finish()

        # Push the current GUI theme to every chart so the first render
        # already blends with the surrounding window — the chart_theme.js
        # listener will keep re-applying it on subsequent restyle/relayout
        # cycles via the ``plotly_afterplot`` event.
        self._apply_theme_to_charts()

    def _render_dashboard_in_batch(self):
        """Ensure the Dashboard is painted as part of the render batch.

        The dashboard self-bootstraps on page load, so on the first open
        we wait for its plots to appear (nudging ``bootstrap()`` if it
        hasn't started). On later batches (year/system change) its plots
        already exist, so the wait returns immediately — we deliberately
        don't re-bootstrap, which would reset the dashboard's own filter
        bar (it has independent system/year controls)."""
        from types import SimpleNamespace
        dv = self._dashboard_view
        if dv is None or self._closing:
            return
        shim = SimpleNamespace(_view=dv)
        self._overlay.update_progress(0, "Rendering: Dashboard")
        # Bring the dashboard (stack index 0) to front so its webview is
        # sized when Plotly measures; keep the overlay on top.
        self._chart_stack.setCurrentIndex(0)
        self._overlay.raise_()
        QApplication.processEvents()
        painted_js = "document.querySelectorAll('.js-plotly-plot').length > 0"
        if self._wait_for_js_predicate(shim, painted_js, timeout_ms=1500):
            return
        try:
            dv.page().runJavaScript(
                "if (typeof bootstrap === 'function') { bootstrap(); }")
        except RuntimeError:
            return
        self._wait_for_js_predicate(shim, painted_js, timeout_ms=8000)

    def _wait_for_js_predicate(
        self, chart, predicate_js: str,
        timeout_ms: int, poll_ms: int = 80,
    ) -> bool:
        """Block (nested ``QEventLoop``) until ``predicate_js`` (a JS
        expression evaluating to ``true``) holds on the chart's page,
        or until ``timeout_ms`` elapses. Returns ``True`` on success,
        ``False`` on timeout / teardown. Matplotlib charts (no
        ``_view``) short-circuit to ``True`` — nothing to wait for.
        """
        view = getattr(chart, "_view", None)
        if view is None:
            return True
        if self._closing:
            return False

        loop = QEventLoop()
        result_holder = {"value": False, "done": False}

        def _finish(success: bool):
            if result_holder["done"]:
                return
            result_holder["done"] = True
            result_holder["value"] = success
            loop.quit()

        def _poll():
            if result_holder["done"] or self._closing:
                _finish(False)
                return
            try:
                page = view.page()
            except RuntimeError:
                _finish(False)
                return

            def _on_result(r):
                if result_holder["done"]:
                    return
                if r is True:
                    _finish(True)
                else:
                    QTimer.singleShot(poll_ms, _poll)

            try:
                page.runJavaScript(predicate_js, _on_result)
            except RuntimeError:
                _finish(False)

        QTimer.singleShot(timeout_ms, lambda: _finish(False))
        QTimer.singleShot(0, _poll)
        loop.exec()
        return result_holder["value"]

    # ── Two-phase wait: bridge ready, then plot painted ──
    # The chart-bootstrap JS (DOMContentLoaded handler) does roughly:
    #   new QWebChannel(...) → bridge = channel.objects.loader → refresh()
    # We must not call ``_safe_update`` until the bridge is connected,
    # otherwise the runJavaScript("refresh()") nudge from Python lands
    # before ``refresh`` and ``bridge`` exist and is a silent no-op.
    _BRIDGE_READY_JS = (
        "(function(){"
        "  return typeof Plotly    !== 'undefined' "
        "      && typeof refresh    === 'function' "
        "      && typeof bridge     !== 'undefined' "
        "      && bridge            !== null;"
        "})()"
    )
    # After ``_safe_update`` enqueues Plotly.react, wait for it to
    # actually paint. Plotly populates ``_fullLayout`` and
    # ``_fullData`` only on completion of newPlot/react.
    _PAINTED_JS = (
        "(function(){"
        "  var el = document.getElementById('plot');"
        "  return !!(el && el._fullLayout && el._fullData);"
        "})()"
    )

    def _wait_for_chart_ready(self, chart, timeout_ms: int = 10000) -> bool:
        """Wait until HTML + plotly.min.js + the QWebChannel bridge
        are all live in the chart's page."""
        return self._wait_for_js_predicate(
            chart, self._BRIDGE_READY_JS, timeout_ms,
        )

    def _wait_for_chart_rendered(self, chart, timeout_ms: int = 10000) -> bool:
        """Wait until Plotly has actually painted the chart."""
        return self._wait_for_js_predicate(
            chart, self._PAINTED_JS, timeout_ms,
        )

    def refresh_theme(self):
        """Public entry point: re-push the active GUI theme to all
        Plotly charts. Called by main_window when the user changes
        themes while the results dialog is open."""
        self._apply_theme_to_charts()

    def _build_theme_js(self) -> Optional[str]:
        """Return the JS snippet that pushes the active GUI palette
        into ``window._currentThemeColors`` and invokes ``applyTheme``.

        Returns ``None`` if the theme lookup fails — caller should
        skip the push in that case.
        """
        import json
        try:
            from esfex.visualization.theme import current_theme
            theme = current_theme()
            c = theme.colors
            colors = {
                "surface_primary":   c.surface_primary,
                "surface_secondary": c.surface_secondary,
                "text_primary":      c.text_primary,
                "text_secondary":    c.text_secondary,
                "border_light":      c.border_light,
                "border_medium":     c.border_medium,
            }
        except Exception:
            logger.exception("Theme lookup failed; charts keep default colours")
            return None
        colors_js = json.dumps(colors)
        # Two-part JS:
        #   1) Always cache the palette on window so even if applyTheme
        #      hasn't been parsed yet, the chart_theme.js initialiser
        #      picks it up the instant it loads.
        #   2) If applyTheme exists, call it now.
        # Also stash on localStorage as a belt-and-suspenders fallback
        # so a chart that fully reloads its DOM still finds the colours.
        return (
            "window._currentThemeColors = " + colors_js + "; "
            "try { localStorage.setItem('esfex_theme_colors', "
            + json.dumps(colors_js) + "); } catch (_) {} "
            "if (typeof applyTheme === 'function') { "
            "applyTheme(window._currentThemeColors); "
            "}"
        )

    def _push_theme_to(self, chart, js: str) -> None:
        """Run the theme-apply JS on one chart, swallowing teardown
        races (deleted view / page)."""
        if getattr(self, "_closing", False) or not js:
            return
        try:
            page = chart._view.page()
        except RuntimeError:
            # The underlying QWebEngineView was already deleted.
            return
        except Exception:
            logger.exception(
                "applyTheme: view lookup failed for %s",
                type(chart).__name__,
            )
            return
        try:
            page.runJavaScript(js)
        except RuntimeError:
            # Page deleted between lookup and runJavaScript.
            return
        except Exception:
            logger.exception(
                "applyTheme failed for %s", type(chart).__name__,
            )

    def _apply_theme_to_charts(self):
        """Propagate the active ColorPalette to every Plotly chart view.

        Charts that don't expose a ``_view`` (legacy matplotlib ones)
        are skipped quietly. The JS helper (``chart_theme.js``, injected
        in every chart HTML) restyles only background / font / grid
        colours; trace colours stay as the semantic palette so red /
        green / etc. keep their meaning across themes.
        """
        js = self._build_theme_js()
        if js is None:
            return
        plotly_charts = [c for c in self._charts if getattr(c, "_view", None)]
        logger.debug("Propagating theme to %d Plotly charts", len(plotly_charts))

        def _push():
            if getattr(self, "_closing", False):
                return
            for chart in plotly_charts:
                self._push_theme_to(chart, js)

        # Initial push, plus three retries at 300 ms / 1.5 s / 3 s. The
        # later passes catch charts whose HTML/JS loaded slowly and
        # whose first render happened well after the dialog opened.
        _push()
        QTimer.singleShot(300,  _push)
        QTimer.singleShot(1500, _push)
        QTimer.singleShot(3000, _push)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _on_export(self):
        """Export the current chart to the selected format."""
        if self._current_chart_idx < 0 or self._current_chart_idx >= len(self._charts):
            QMessageBox.information(self, "Export", "No chart to export.")
            return

        chart = self._charts[self._current_chart_idx]
        if not chart._loaded:
            QMessageBox.information(self, "Export", "No chart to export.")
            return

        fmt = self._export_format.currentText()
        ext = fmt.lower()

        filter_str = _EXPORT_FILTERS.get(fmt, "All files (*)")
        default_name = (chart.TITLE or "chart").replace(" ", "_").lower()

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Chart",
            str(Path.home() / f"{default_name}.{ext}"),
            filter_str,
        )
        if not file_path:
            return

        try:
            if chart.fig is not None:
                from esfex.visualization.preferences import get_export_dpi
                chart.fig.savefig(file_path, dpi=get_export_dpi(), bbox_inches="tight",
                                  facecolor=chart.fig.get_facecolor())
            elif hasattr(chart, "export_image"):
                chart.export_image(file_path)
            else:
                QMessageBox.warning(self, "Export", "Export not supported for this chart type.")
                return
            QMessageBox.information(
                self, "Export", f"Chart exported to:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Export Error", f"Failed to export chart:\n{e}"
            )

    # ------------------------------------------------------------------
    # Dashboard QSS
    # ------------------------------------------------------------------

    def _apply_dashboard_style(self):
        theme = current_theme()
        c = theme.colors
        t = theme.typography
        self.setStyleSheet(f"""
            /* Sidebar */
            QWidget#dashboardSidebar {{
                background-color: {c.surface_secondary};
                border-right: 1px solid {c.border_light};
            }}
            QLabel#sidebarLabel {{
                color: {c.text_secondary};
                font-family: {t.family_ui};
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
                padding: 4px 0 2px 0;
                letter-spacing: 0.8px;
            }}
            QWidget#dashboardSidebar QFrame {{
                background-color: {c.border_light};
            }}
            QComboBox#sidebarCombo {{
                background-color: {c.surface_primary};
                color: {c.text_primary};
                border: 1px solid {c.border_light};
                border-radius: 4px;
                padding: 5px 8px;
                min-height: 24px;
                max-width: 300px;
                font-size: 12px;
            }}
            QComboBox#sidebarCombo:hover {{
                border-color: {c.accent_primary};
            }}
            QComboBox#sidebarCombo::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox#sidebarCombo QAbstractItemView {{
                background-color: {c.surface_primary};
                color: {c.text_primary};
                border: 1px solid {c.border_light};
                selection-background-color: {c.selection_bg};
                selection-color: {c.text_primary};
            }}

            /* Chart list */
            QListWidget#chartList {{
                background-color: transparent;
                border: none;
                outline: none;
                color: {c.text_primary};
                font-size: 12px;
            }}
            QListWidget#chartList::item {{
                padding: 4px 8px;
                border-radius: 4px;
                margin: 1px 0;
            }}
            QListWidget#chartList::item:selected {{
                background-color: {c.accent_primary};
                color: white;
                font-weight: 600;
            }}
            QListWidget#chartList::item:hover:!selected {{
                background-color: {c.selection_bg};
            }}

            /* Main content area */
            QWidget#dashboardContent {{
                background-color: {c.surface_primary};
            }}

            /* Chart title bar */
            QWidget#chartTitleBar {{
                background-color: {c.surface_secondary};
                border-bottom: 1px solid {c.border_light};
            }}
            QLabel#chartTitle {{
                color: {c.text_primary};
                font-size: 14px;
                font-weight: 600;
                font-family: {t.family_ui};
            }}

            /* Year slider in title bar */
            QLabel#yearSliderLabel {{
                color: {c.text_secondary};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#yearValueLabel {{
                color: {c.text_primary};
                font-size: 12px;
                font-weight: 700;
                background: transparent;
                min-width: 40px;
            }}
            QSlider#yearSlider::groove:horizontal {{
                height: 4px;
                background: {c.border_medium};
                border-radius: 2px;
            }}
            QSlider#yearSlider::handle:horizontal {{
                background: {c.accent_primary};
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider#yearSlider::handle:horizontal:hover {{
                background: {c.accent_secondary};
            }}

            /* Export controls */
            QComboBox#exportCombo {{
                background-color: {c.surface_primary};
                color: {c.text_primary};
                border: 1px solid {c.border_light};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QPushButton#exportButton {{
                background-color: {c.accent_primary};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#exportButton:hover {{
                background-color: {c.accent_secondary};
            }}

            /* Chart params bar */
            QWidget#chartParamsBar {{
                background-color: {c.surface_secondary};
                border-bottom: 1px solid {c.border_light};
            }}

            /* Splitter handle */
            QSplitter::handle:horizontal {{
                width: 1px;
                background: {c.border_light};
            }}
        """)

    # ------------------------------------------------------------------
    # System / year handling
    # ------------------------------------------------------------------

    def _load_system_years(self, system_name: str):
        """Load year list and MGA data for a system (no chart rendering)."""
        self._years_list = []
        path = self._h5_files.get(system_name)

        # Swap the cache to the new system's file. Closing the previous
        # one releases its HDF5 handle; charts created from the old
        # cache won't see the new system until they re-render.
        if self._cache is not None:
            self._cache.close()
            self._cache = None
        if path is not None and path.exists():
            self._cache = ResultsCache(path)

        if not path or not path.exists():
            return

        bp = self._base_prefix.get(system_name, "")

        # Read the summary year list. Phase-2 collapsed the per-system
        # mirror into a sliced view on top of the root block, so
        # ``_open_summary_results`` handles both layouts and the
        # ``Global`` pseudo-system (which still has its own /global/
        # group with system-wide aggregates).
        try:
            import h5py
            from esfex.visualization.panels.results_charts import (
                _open_summary_results,
            )
            with h5py.File(path, "r") as f:
                if system_name == "Global" and "global/summary_results" in f:
                    sg = f["global/summary_results"]
                else:
                    sg = _open_summary_results(f, bp)
                if sg is not None and "year" in sg:
                    self._years_list = sorted(
                        set(int(y) for y in sg["year"][:])
                    )
        except Exception as e:
            QMessageBox.warning(
                self, tr("common.error"), tr("messages.hdf5_error_msg", e=e)
            )
            return

        # Update year range slider — reset to the full span every time
        # the system changes, since the previous system's range bounds
        # may not even apply to the new system's year list.
        self._year_slider.blockSignals(True)
        if self._years_list:
            last = len(self._years_list) - 1
            self._year_slider.setMinimum(0)
            self._year_slider.setMaximum(last)
            self._year_slider.setValue((0, last))
            self._year_value_label.setText(
                f"{self._years_list[0]}–{self._years_list[-1]}"
            )
        else:
            self._year_slider.setMinimum(0)
            self._year_slider.setMaximum(0)
            self._year_slider.setValue((0, 0))
            self._year_value_label.setText("")
        self._year_slider.blockSignals(False)
        # Also notify any per-chart year combos (Sankey, Net Load Heatmap)
        # so they offer the new system's year list.
        self._refresh_per_chart_year_combos()

    def _on_system_changed(self, system_name: str):
        """Handle system combo change.

        Swap to the new system's cache, mark every chart dirty and
        re-render the full batch — keeps navigation instant afterwards.
        Also drives the dashboard's internal system selector so it
        doesn't keep showing the previous system's KPIs.
        """
        self._load_system_years(system_name)
        for chart in self._charts:
            chart._loaded = False
        if self._dashboard_view is not None:
            try:
                self._dashboard_view.set_system(system_name)
            except Exception:
                logger.exception("Failed to sync dashboard system selector")
        self._render_all_charts()

    def showEvent(self, event):
        """Render every chart up-front so navigation is instant."""
        super().showEvent(event)
        if not self._charts or self._charts[0]._loaded:
            return
        # Defer slightly so the window is fully painted first
        QTimer.singleShot(50, self._render_all_charts)

    def _current_year_range(self) -> tuple[int, int] | None:
        """Return ``(year_min, year_max)`` for the slider, or ``None``
        if no years are loaded. Slider values are indices into
        ``self._years_list``; we resolve them to actual years here so
        downstream code never has to touch the slider's index space.
        """
        if not self._years_list:
            return None
        try:
            lo_idx, hi_idx = self._year_slider.value()
        except (TypeError, ValueError):
            return (self._years_list[0], self._years_list[-1])
        n = len(self._years_list)
        lo_idx = max(0, min(int(lo_idx), n - 1))
        hi_idx = max(0, min(int(hi_idx), n - 1))
        if hi_idx < lo_idx:
            lo_idx, hi_idx = hi_idx, lo_idx
        return (self._years_list[lo_idx], self._years_list[hi_idx])

    def _style_year_slider(self) -> None:
        """Visually separate the selected range from the deselected
        tails on the year ``QRangeSlider``.

        Two layers:
        * The **bar** (between the two handles, i.e. selected range)
          gets a solid accent colour. superqt's ``barColor`` expects a
          ``QColor`` it can ``.setAlphaF()`` on — passing a ``QBrush``
          (gradient) crashes the painter mid-frame, so the contrast
          here comes from colour, not gradient.
        * The **groove** (full track, i.e. the deselected tails poking
          out past the handles) is styled via QSS to a low-contrast
          neutral so it reads as "context, not data".
        """
        from PySide6.QtGui import QColor
        try:
            from esfex.visualization.theme import current_theme
            c = current_theme().colors
            accent_a = c.accent_primary
            accent_b = c.accent_secondary
            groove   = c.surface_secondary
            border   = c.border_light
        except Exception:
            accent_a, accent_b = "#2980B9", "#27AE60"
            groove, border = "#E5E7EB", "#CBD5E1"

        self._year_slider.barColor = QColor(accent_a)

        self._year_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                background: {groove};
                border: 1px solid {border};
                border-radius: 4px;
                height: 8px;
            }}
            QSlider::handle:horizontal {{
                background: {accent_a};
                border: 1px solid {border};
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {accent_b};
            }}
            """
        )

    def _update_year_label(self) -> tuple[int | None, int | None]:
        """Refresh the year-range readout from the slider's current
        position and return ``(y_min, y_max)`` (or ``None, None``)."""
        rng = self._current_year_range()
        if rng is None:
            self._year_value_label.setText("")
            return None, None
        y_min, y_max = rng
        self._year_value_label.setText(
            f"{y_min}" if y_min == y_max else f"{y_min}–{y_max}"
        )
        return y_min, y_max

    def _on_year_slider_dragging(self, _value):
        """Live label update while the user drags. Deliberately does
        NOT re-render — full-batch rendering on every intermediate
        step would freeze the drag. The release handler does the
        actual work."""
        self._update_year_label()

    def _on_year_slider_released(self):
        """User released the slider — invalidate every chart and
        re-render the full batch. Also propagates to the dashboard.

        Programmatic value changes (system swap, init) never call
        this because they bypass the user interaction signal, so the
        rendering they need is driven by ``_render_all_charts`` from
        their own code path.
        """
        y_min, y_max = self._update_year_label()
        for chart in self._charts:
            chart._loaded = False
        if self._dashboard_view is not None:
            try:
                if y_min is None:
                    self._dashboard_view.set_year_range(None, None)
                else:
                    # ``None,None`` if the user spans the entire timeline
                    # (clears the dashboard filter rather than narrowing
                    # to the full range, which has the same intent but a
                    # slightly different code path inside the JS).
                    if (
                        self._years_list
                        and y_min == self._years_list[0]
                        and y_max == self._years_list[-1]
                    ):
                        self._dashboard_view.set_year_range(None, None)
                    else:
                        self._dashboard_view.set_year_range(y_min, y_max)
            except Exception:
                logger.exception("Failed to sync dashboard year range")
        self._render_all_charts()

    def _refresh_per_chart_year_combos(self) -> None:
        """Push the current year list into single-year charts' combos.

        Sankey and Net Load Heatmap have a per-chart ``QComboBox`` of
        years built into their params widget (they can't be multi-year).
        Whenever the system changes, those combos need to be repopulated
        with the new system's year list.
        """
        for chart in self._charts:
            update = getattr(chart, "set_available_years", None)
            if callable(update):
                try:
                    update(list(self._years_list))
                except Exception:
                    logger.exception(
                        "Chart %s rejected new year list", chart.TITLE
                    )

    def closeEvent(self, event):
        """Hide the dialog instead of tearing it down.

        Re-rendering the chart set is expensive (5–15 s for the full
        batch even with the cache warm), so closing the window only
        hides it: the HDF5 handle, the per-scenario cache, and every
        rendered chart stay alive until the GUI itself shuts down or
        the active result file changes. The host (main_window) calls
        :meth:`force_close` when it needs to drop the dialog for real
        (new file loaded, application quit, etc.)."""
        if getattr(self, "_force_destroy", False):
            # Real teardown requested by the host — release the HDF5
            # handle and let Qt destroy the widget.
            self._closing = True
            if self._cache is not None:
                self._cache.close()
                self._cache = None
            super().closeEvent(event)
            return
        # Soft close: just hide. The user can re-open instantly via
        # the toolbar; charts and cache stay warm.
        event.ignore()
        self.hide()

    def force_close(self):
        """Destroy the dialog for good. Called by main_window when a
        new result file is loaded or the GUI shuts down."""
        self._force_destroy = True
        self.close()

    def _on_font_scale_changed(self, value: int):
        """Propagate a font scale to every Plotly chart (those that
        expose a ``_view`` attribute — i.e. embed a QWebEngineView).
        The matching JS helper (font_scale.js, loaded by every chart
        HTML) rescales every font.size in the layout proportionally.
        """
        scale = max(0.1, value / 100.0)
        self._font_value_label.setText(f"{scale:.1f}x")
        import json
        js = f"if (typeof setFontScale === 'function') setFontScale({json.dumps(scale)});"
        for chart in self._charts:
            view = getattr(chart, "_view", None)
            if view is not None:
                try:
                    view.page().runJavaScript(js)
                except Exception:
                    logger.debug("setFontScale failed for %s", type(chart).__name__)

