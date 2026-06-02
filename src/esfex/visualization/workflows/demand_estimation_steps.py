"""Step widgets for the Demand Estimation wizard.

Five steps:
1. ScopeTargetStep   — study area, node selection, global configuration
2. ProxyDataStep     — fetch proxies, macro indicators, and ERA5 meteo data
3. MacroEconomicStep — year-by-year projection parameters table
4. BuildProfilesStep — combine all data → generate hourly demand (background)
5. CalibrationStep   — validate/calibrate and export per-node CSV files
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.theme import current_theme

logger = logging.getLogger(__name__)

# Optional matplotlib for demand chart
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _FigCanvas
    from matplotlib.figure import Figure as _Figure
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


# ── Shared helper ─────────────────────────────────────────────────────────────

def _scrolled(inner: QWidget) -> QScrollArea:
    """Wrap *inner* in a vertically scrollable area."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll


# ── Country reference list (ISO2, ISO3, display name) ─────────────────────────
# Sorted alphabetically by display name; used in Step 1 country comboboxes.

_COUNTRIES: list[tuple[str, str, str]] = sorted([
    ("AF","AFG","Afghanistan"),("AL","ALB","Albania"),("DZ","DZA","Algeria"),
    ("AD","AND","Andorra"),("AO","AGO","Angola"),("AG","ATG","Antigua and Barbuda"),
    ("AR","ARG","Argentina"),("AM","ARM","Armenia"),("AU","AUS","Australia"),
    ("AT","AUT","Austria"),("AZ","AZE","Azerbaijan"),("BS","BHS","Bahamas"),
    ("BH","BHR","Bahrain"),("BD","BGD","Bangladesh"),("BB","BRB","Barbados"),
    ("BY","BLR","Belarus"),("BE","BEL","Belgium"),("BZ","BLZ","Belize"),
    ("BJ","BEN","Benin"),("BT","BTN","Bhutan"),("BO","BOL","Bolivia"),
    ("BA","BIH","Bosnia and Herzegovina"),("BW","BWA","Botswana"),
    ("BR","BRA","Brazil"),("BN","BRN","Brunei"),("BG","BGR","Bulgaria"),
    ("BF","BFA","Burkina Faso"),("BI","BDI","Burundi"),("CV","CPV","Cabo Verde"),
    ("KH","KHM","Cambodia"),("CM","CMR","Cameroon"),("CA","CAN","Canada"),
    ("CF","CAF","Central African Republic"),("TD","TCD","Chad"),
    ("CL","CHL","Chile"),("CN","CHN","China"),("CO","COL","Colombia"),
    ("KM","COM","Comoros"),("CG","COG","Congo"),("CD","COD","Congo, Dem. Rep."),
    ("CR","CRI","Costa Rica"),("CI","CIV","Côte d'Ivoire"),("HR","HRV","Croatia"),
    ("CU","CUB","Cuba"),("CY","CYP","Cyprus"),("CZ","CZE","Czech Republic"),
    ("DK","DNK","Denmark"),("DJ","DJI","Djibouti"),("DM","DMA","Dominica"),
    ("DO","DOM","Dominican Republic"),("EC","ECU","Ecuador"),("EG","EGY","Egypt"),
    ("SV","SLV","El Salvador"),("GQ","GNQ","Equatorial Guinea"),
    ("ER","ERI","Eritrea"),("EE","EST","Estonia"),("SZ","SWZ","Eswatini"),
    ("ET","ETH","Ethiopia"),("FJ","FJI","Fiji"),("FI","FIN","Finland"),
    ("FR","FRA","France"),("GA","GAB","Gabon"),("GM","GMB","Gambia"),
    ("GE","GEO","Georgia"),("DE","DEU","Germany"),("GH","GHA","Ghana"),
    ("GR","GRC","Greece"),("GD","GRD","Grenada"),("GT","GTM","Guatemala"),
    ("GN","GIN","Guinea"),("GW","GNB","Guinea-Bissau"),("GY","GUY","Guyana"),
    ("HT","HTI","Haiti"),("HN","HND","Honduras"),("HU","HUN","Hungary"),
    ("IS","ISL","Iceland"),("IN","IND","India"),("ID","IDN","Indonesia"),
    ("IR","IRN","Iran"),("IQ","IRQ","Iraq"),("IE","IRL","Ireland"),
    ("IL","ISR","Israel"),("IT","ITA","Italy"),("JM","JAM","Jamaica"),
    ("JP","JPN","Japan"),("JO","JOR","Jordan"),("KZ","KAZ","Kazakhstan"),
    ("KE","KEN","Kenya"),("KI","KIR","Kiribati"),("KW","KWT","Kuwait"),
    ("KG","KGZ","Kyrgyzstan"),("LA","LAO","Laos"),("LV","LVA","Latvia"),
    ("LB","LBN","Lebanon"),("LS","LSO","Lesotho"),("LR","LBR","Liberia"),
    ("LY","LBY","Libya"),("LI","LIE","Liechtenstein"),("LT","LTU","Lithuania"),
    ("LU","LUX","Luxembourg"),("MG","MDG","Madagascar"),("MW","MWI","Malawi"),
    ("MY","MYS","Malaysia"),("MV","MDV","Maldives"),("ML","MLI","Mali"),
    ("MT","MLT","Malta"),("MH","MHL","Marshall Islands"),("MR","MRT","Mauritania"),
    ("MU","MUS","Mauritius"),("MX","MEX","Mexico"),("FM","FSM","Micronesia"),
    ("MD","MDA","Moldova"),("MC","MCO","Monaco"),("MN","MNG","Mongolia"),
    ("ME","MNE","Montenegro"),("MA","MAR","Morocco"),("MZ","MOZ","Mozambique"),
    ("MM","MMR","Myanmar"),("NA","NAM","Namibia"),("NR","NRU","Nauru"),
    ("NP","NPL","Nepal"),("NL","NLD","Netherlands"),("NZ","NZL","New Zealand"),
    ("NI","NIC","Nicaragua"),("NE","NER","Niger"),("NG","NGA","Nigeria"),
    ("MK","MKD","North Macedonia"),("NO","NOR","Norway"),("OM","OMN","Oman"),
    ("PK","PAK","Pakistan"),("PW","PLW","Palau"),("PA","PAN","Panama"),
    ("PG","PNG","Papua New Guinea"),("PY","PRY","Paraguay"),("PE","PER","Peru"),
    ("PH","PHL","Philippines"),("PL","POL","Poland"),("PT","PRT","Portugal"),
    ("QA","QAT","Qatar"),("RO","ROU","Romania"),("RU","RUS","Russia"),
    ("RW","RWA","Rwanda"),("KN","KNA","Saint Kitts and Nevis"),
    ("LC","LCA","Saint Lucia"),("VC","VCT","Saint Vincent and the Grenadines"),
    ("WS","WSM","Samoa"),("SM","SMR","San Marino"),("ST","STP","São Tomé and Príncipe"),
    ("SA","SAU","Saudi Arabia"),("SN","SEN","Senegal"),("RS","SRB","Serbia"),
    ("SC","SYC","Seychelles"),("SL","SLE","Sierra Leone"),("SG","SGP","Singapore"),
    ("SK","SVK","Slovakia"),("SI","SVN","Slovenia"),("SB","SLB","Solomon Islands"),
    ("SO","SOM","Somalia"),("ZA","ZAF","South Africa"),("SS","SSD","South Sudan"),
    ("ES","ESP","Spain"),("LK","LKA","Sri Lanka"),("SD","SDN","Sudan"),
    ("SR","SUR","Suriname"),("SE","SWE","Sweden"),("CH","CHE","Switzerland"),
    ("SY","SYR","Syria"),("TW","TWN","Taiwan"),("TJ","TJK","Tajikistan"),
    ("TZ","TZA","Tanzania"),("TH","THA","Thailand"),("TL","TLS","Timor-Leste"),
    ("TG","TGO","Togo"),("TO","TON","Tonga"),("TT","TTO","Trinidad and Tobago"),
    ("TN","TUN","Tunisia"),("TR","TUR","Turkey"),("TM","TKM","Turkmenistan"),
    ("TV","TUV","Tuvalu"),("UG","UGA","Uganda"),("UA","UKR","Ukraine"),
    ("AE","ARE","United Arab Emirates"),("GB","GBR","United Kingdom"),
    ("US","USA","United States"),("UY","URY","Uruguay"),("UZ","UZB","Uzbekistan"),
    ("VU","VUT","Vanuatu"),("VE","VEN","Venezuela"),("VN","VNM","Vietnam"),
    ("YE","YEM","Yemen"),("ZM","ZMB","Zambia"),("ZW","ZWE","Zimbabwe"),
], key=lambda x: x[2])

# iso3 → (iso2, name) lookup for auto-detection results
_ISO3_MAP: dict[str, tuple[str, str]] = {c[1]: (c[0], c[2]) for c in _COUNTRIES}


# ── Country combo with capped popup height ────────────────────────────────────

class _CountryComboBox(QComboBox):
    """QComboBox that limits the popup height to ~10 rows on all platforms."""

    _POPUP_MAX_HEIGHT = 220   # px — roughly 10 items

    def showPopup(self):
        super().showPopup()
        view = self.view()
        if view is None:
            return
        popup = view.window()
        if popup is self.window():
            return  # not detached yet — skip

        if popup.height() <= self._POPUP_MAX_HEIGHT:
            return  # already fits — nothing to do

        # Anchor point: bottom-left of the combo in screen coordinates
        anchor = self.mapToGlobal(self.rect().bottomLeft())

        popup.setFixedHeight(self._POPUP_MAX_HEIGHT)

        # Check if there is room below; if not, place above
        from PySide6.QtWidgets import QApplication
        screen = QApplication.screenAt(anchor)
        if screen is None:
            screen = QApplication.primaryScreen()
        screen_bottom = screen.availableGeometry().bottom()

        if anchor.y() + self._POPUP_MAX_HEIGHT > screen_bottom:
            # Not enough room below — show above the combo
            y = self.mapToGlobal(self.rect().topLeft()).y() - self._POPUP_MAX_HEIGHT
        else:
            y = anchor.y()

        popup.move(anchor.x(), y)


# =====================================================================
# Step 1: Scope & Target Selection (multi-system)
# =====================================================================


class ScopeTargetStep(QWidget):
    """Select target nodes across all loaded systems, set horizon and mode."""

    validityChanged = Signal()

    def __init__(self, all_states: dict, map_widget=None, parent=None):
        super().__init__(parent)
        # all_states: system_name → GuiSystemState
        self._all_states = all_states
        self._map_widget = map_widget
        self._bounds: Optional[tuple[float, float, float, float]] = None
        self._country_combos: dict[str, QComboBox] = {}   # sys_name → combo
        self._detect_fetchers: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("wizard_demest.step1_instruction")))

        # ── Scope & Configuration (two columns) ──
        scope_group = QGroupBox(tr("wizard_demest.study_area") + " / " + tr("wizard_demest.global_config"))
        scope_cols = QHBoxLayout(scope_group)

        # Column 1: Study area
        col1 = QVBoxLayout()
        draw_row = QHBoxLayout()
        self._btn_draw = QPushButton(tr("wizard_demest.draw_domain"))
        self._btn_draw.clicked.connect(self._start_drawing)
        draw_row.addWidget(self._btn_draw)
        self._draw_status = QLabel("")
        self._draw_status.setWordWrap(True)
        draw_row.addWidget(self._draw_status, 1)
        col1.addLayout(draw_row)

        coord_form = QFormLayout()
        coord_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._spin_south = self._make_coord_spin(-90, 90)
        self._spin_north = self._make_coord_spin(-90, 90)
        self._spin_west = self._make_coord_spin(-180, 180)
        self._spin_east = self._make_coord_spin(-180, 180)
        coord_form.addRow("South:", self._spin_south)
        coord_form.addRow("North:", self._spin_north)
        coord_form.addRow("West:", self._spin_west)
        coord_form.addRow("East:", self._spin_east)
        self._btn_apply_coords = QPushButton(tr("wizard_demest.apply_coords"))
        self._btn_apply_coords.clicked.connect(self._apply_manual_coords)
        coord_form.addRow("", self._btn_apply_coords)
        col1.addLayout(coord_form)
        self._area_label = QLabel("")
        self._area_label.setStyleSheet("font-weight: bold;")
        col1.addWidget(self._area_label)
        col1.addStretch()

        # Column 2: Configuration
        col2 = QVBoxLayout()
        config_form = QFormLayout()
        config_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._spin_base_year = QSpinBox()
        self._spin_base_year.setRange(2000, 2100)
        self._spin_base_year.setValue(2025)
        self._spin_base_year.setMaximumWidth(100)
        config_form.addRow(tr("wizard_demest.base_year"), self._spin_base_year)

        self._spin_sim_years = QSpinBox()
        self._spin_sim_years.setRange(1, 50)
        self._spin_sim_years.setValue(25)
        self._spin_sim_years.setMaximumWidth(100)
        config_form.addRow(tr("wizard_demest.sim_years"), self._spin_sim_years)

        self._combo_resolution = QComboBox()
        for label, val in [
            ("15 min", 0.25), ("30 min", 0.5), ("1 hour", 1.0),
            ("2 hours", 2.0), ("3 hours", 3.0), ("6 hours", 6.0),
        ]:
            self._combo_resolution.addItem(label, val)
        self._combo_resolution.setCurrentIndex(2)   # 1 hour default
        self._combo_resolution.setMaximumWidth(120)
        config_form.addRow(tr("wizard_demest.resolution"), self._combo_resolution)
        col2.addLayout(config_form)
        col2.addStretch()

        scope_cols.addLayout(col1, 1)
        scope_cols.addLayout(col2, 1)
        layout.addWidget(scope_group)

        # ── Target nodes ──
        node_group = QGroupBox(tr("wizard_demest.target_nodes"))
        node_lay = QVBoxLayout(node_group)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([
            tr("wizard_demest.system_node"),   # 0
            tr("wizard_demest.country"),        # 1
            tr("wizard_demest.peak_demand"),    # 2
            tr("wizard_demest.latitude"),       # 3
            tr("wizard_demest.longitude"),      # 4
        ])
        self._tree.setColumnWidth(0, 190)
        self._tree.setColumnWidth(1, 200)
        self._tree.setColumnWidth(2, 80)
        self._tree.setColumnWidth(3, 75)
        self._tree.setColumnWidth(4, 75)
        self._tree.setMinimumHeight(420)
        self._tree.itemChanged.connect(self._on_item_changed)
        node_lay.addWidget(self._tree)

        btn_row = QHBoxLayout()
        self._btn_select_all = QPushButton(tr("wizard_demest.select_all"))
        self._btn_select_all.clicked.connect(self._select_all)
        btn_row.addWidget(self._btn_select_all)

        self._btn_deselect_all = QPushButton(tr("wizard_demest.deselect_all"))
        self._btn_deselect_all.clicked.connect(self._deselect_all)
        btn_row.addWidget(self._btn_deselect_all)
        btn_row.addStretch()

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet("color: #aaa; padding: 2px 0;")
        btn_row.addWidget(self._summary_label)
        node_lay.addLayout(btn_row)

        layout.addWidget(node_group, 1)

        outer.addWidget(_scrolled(inner))

        self._populate_tree()

        self._polygon_coords: list[tuple[float, float]] = []

        if self._map_widget:
            try:
                self._map_widget.bridge.domainPolygonDrawn.connect(
                    self._on_polygon_drawn
                )
                self._map_widget.install_draw_cancel_handler(
                    self, self._btn_draw,
                )
            except Exception as exc:
                # If the map widget's bridge isn't ready yet (Qt slot
                # connection or handler install raises), the Draw button
                # silently does nothing. Log so users can diagnose.
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to wire map widget domain-polygon drawing: %s. "
                    "The 'Draw' button on this step may not work.",
                    exc,
                )

    # ── Tree population ──────────────────────────────────────────────────────

    def populate(self, all_states: Optional[dict] = None) -> None:
        """Rebuild tree from current all_states (call when systems change)."""
        if all_states is not None:
            self._all_states = all_states
        self._populate_tree()

    def _make_country_combo(self) -> QComboBox:
        combo = _CountryComboBox()
        combo.addItem(tr("wizard_demest.country_detecting"), None)   # placeholder
        for iso2, iso3, name in _COUNTRIES:
            combo.addItem(name, {"iso2": iso2, "iso3": iso3, "name": name})
        combo.setMaximumWidth(195)
        return combo

    def _populate_tree(self) -> None:
        # Cancel any running detection fetchers
        for f in self._detect_fetchers:
            if hasattr(f, "cancel"):
                f.cancel()
        self._detect_fetchers.clear()
        self._country_combos.clear()

        self._tree.blockSignals(True)
        self._tree.clear()

        for sys_name, state in self._all_states.items():
            sys_item = QTreeWidgetItem(self._tree)
            sys_item.setText(0, sys_name)
            sys_item.setFlags(
                sys_item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            sys_item.setCheckState(0, Qt.CheckState.Unchecked)
            sys_item.setExpanded(True)

            # Country combo (col 1, system level only)
            combo = self._make_country_combo()
            combo.currentIndexChanged.connect(lambda *_: self.validityChanged.emit())
            self._tree.setItemWidget(sys_item, 1, combo)
            self._country_combos[sys_name] = combo

            try:
                nodes = state.nodes
            except Exception:
                nodes = []

            for node in nodes:
                try:
                    peak = node.demand.peak_mw if node.demand else 0.0
                    lat = node.centroid_lat
                    lon = node.centroid_lng
                    name = node.name
                    idx = node.index
                except Exception:
                    peak, lat, lon, name, idx = 0.0, 0.0, 0.0, str(node), 0

                node_item = QTreeWidgetItem(sys_item)
                node_item.setText(0, f"{name} (#{idx})")
                # col 1 left empty (country belongs to system row)
                node_item.setText(2, f"{peak:.1f} MW")
                node_item.setText(3, f"{lat:.4f}")
                node_item.setText(4, f"{lon:.4f}")
                node_item.setFlags(
                    node_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                node_item.setCheckState(0, Qt.CheckState.Checked)
                node_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "system_name": sys_name,
                    "node_index": idx,
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "peak_mw": peak,
                })

        self._tree.blockSignals(False)
        self._update_summary()
        self._auto_detect_countries()
        self.validityChanged.emit()

    def _auto_detect_countries(self) -> None:
        """Launch Nominatim reverse-geocoding for each system (non-blocking)."""
        from esfex.visualization.workflows.demand_estimation_fetchers import (
            CountryDetectorDemand,
        )
        for sys_name, state in self._all_states.items():
            try:
                nodes = state.nodes
                lats = [n.centroid_lat for n in nodes]
                lons = [n.centroid_lng for n in nodes]
                if not lats:
                    continue
                clat = sum(lats) / len(lats)
                clon = sum(lons) / len(lons)
                pad = 0.01
                bounds = (clat - pad, clon - pad, clat + pad, clon + pad)
            except Exception:
                continue

            f = CountryDetectorDemand(bounds=bounds, parent=self)
            # Capture sys_name in closure
            f.finished.connect(
                lambda iso3, cname, sn=sys_name: self._on_country_detected(sn, iso3, cname)
            )
            self._detect_fetchers.append(f)
            f.start()

    def _on_country_detected(self, sys_name: str, iso3: str, country_name: str) -> None:
        combo = self._country_combos.get(sys_name)
        if combo is None:
            return
        # Find matching entry by iso3
        for i in range(combo.count()):
            data = combo.itemData(i)
            if isinstance(data, dict) and data.get("iso3") == iso3:
                combo.setCurrentIndex(i)
                return
        # Not found — insert it at position 1 and select
        combo.insertItem(1, country_name, {"iso2": "", "iso3": iso3, "name": country_name})
        combo.setCurrentIndex(1)
        self.validityChanged.emit()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _on_item_changed(self, item, column):
        self._update_summary()
        self.validityChanged.emit()

    def _update_summary(self):
        nodes = self.get_selected_nodes()
        n = len(nodes)
        if n == 0:
            self._summary_label.setText(tr("wizard_demest.no_nodes_selected"))
        else:
            systems = len({nd["system_name"] for nd in nodes})
            self._summary_label.setText(
                f"{n} node(s) selected across {systems} system(s)"
            )

    def _select_all(self):
        root = self._tree.invisibleRootItem()
        for si in range(root.childCount()):
            sys_item = root.child(si)
            for ni in range(sys_item.childCount()):
                node_item = sys_item.child(ni)
                if node_item.flags() & Qt.ItemFlag.ItemIsEnabled:
                    node_item.setCheckState(0, Qt.CheckState.Checked)

    def _deselect_all(self):
        root = self._tree.invisibleRootItem()
        for si in range(root.childCount()):
            sys_item = root.child(si)
            for ni in range(sys_item.childCount()):
                node_item = sys_item.child(ni)
                node_item.setCheckState(0, Qt.CheckState.Unchecked)

    # ── Public API ───────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        if len(self.get_selected_nodes()) == 0:
            return False
        if not self.get_system_countries():
            return False
        return True

    def get_base_year(self) -> int:
        return self._spin_base_year.value()

    def get_sim_years(self) -> int:
        return self._spin_sim_years.value()

    def get_resolution(self) -> float:
        """Return temporal resolution in hours (e.g. 0.5 = 30 min, 6.0 = 6 h)."""
        return self._combo_resolution.currentData() or 1.0

    def get_national_demand_gwh(self) -> float:
        """Return user-specified annual demand in GWh (0 = auto-estimate)."""
        return 0.0

    def get_system_countries(self) -> dict[str, dict]:
        """Return {sys_name: {"iso2", "iso3", "name"}} for each system."""
        result = {}
        for sys_name, combo in self._country_combos.items():
            data = combo.currentData()
            if isinstance(data, dict):
                result[sys_name] = data
        return result

    def get_selected_nodes(self) -> list[dict]:
        """Return selected node dicts including the system's country info."""
        countries = self.get_system_countries()
        result = []
        root = self._tree.invisibleRootItem()
        for si in range(root.childCount()):
            sys_item = root.child(si)
            for ni in range(sys_item.childCount()):
                node_item = sys_item.child(ni)
                if node_item.checkState(0) == Qt.CheckState.Checked:
                    data = node_item.data(0, Qt.ItemDataRole.UserRole)
                    if data:
                        entry = dict(data)
                        entry["country"] = countries.get(entry["system_name"], {})
                        result.append(entry)
        return result

    # ── Scope / Study Area ────────────────────────────────────────────────────

    @staticmethod
    def _make_coord_spin(min_val, max_val) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(4)
        spin.setSingleStep(0.01)
        spin.setMaximumWidth(140)
        return spin

    def _start_drawing(self):
        self._draw_status.setText(tr("wizard_demest.drawing_polygon_hint"))
        self._btn_draw.setEnabled(False)
        wizard = self.window()
        if wizard:
            wizard.showMinimized()
        if self._map_widget:
            self._map_widget.enable_domain_polygon_draw()

    def _on_polygon_drawn(self, geojson_str: str):
        try:
            data = json.loads(geojson_str)
            coords_raw = data.get("geometry", {}).get("coordinates", [[]])
            ring = coords_raw[0] if coords_raw else []
            if not ring:
                raise ValueError("empty ring")
            # GeoJSON order: [lng, lat]
            self._polygon_coords = [(c[1], c[0]) for c in ring]
        except Exception:
            self._draw_status.setText(tr("wizard_demest.polygon_error"))
            self._btn_draw.setEnabled(True)
            return

        lats = [p[0] for p in self._polygon_coords]
        lons = [p[1] for p in self._polygon_coords]
        self._set_bounds(min(lats), min(lons), max(lats), max(lons))

        n_v = len(self._polygon_coords)
        s, w, n_, e = self._bounds
        self._draw_status.setText(
            f"{n_v} vertices · bbox ({s:.3f}, {w:.3f}) → ({n_:.3f}, {e:.3f})"
        )

        if self._map_widget:
            self._map_widget.disable_domain_polygon_draw()
            try:
                self._map_widget.show_domain_polygon(self._polygon_coords)
            except Exception:
                pass

        wizard = self.window()
        if wizard:
            wizard.showNormal()
            wizard.raise_()
            wizard.activateWindow()
        self._btn_draw.setEnabled(True)

    def _apply_manual_coords(self):
        s = self._spin_south.value()
        n = self._spin_north.value()
        w = self._spin_west.value()
        e = self._spin_east.value()
        if n <= s or e <= w:
            QMessageBox.warning(
                self,
                tr("wizard_demest.invalid_domain"),
                tr("wizard_demest.invalid_domain_msg"),
            )
            return
        self._set_bounds(s, w, n, e)

    def _set_bounds(self, south, west, north, east):
        self._bounds = (south, west, north, east)
        self._spin_south.setValue(south)
        self._spin_north.setValue(north)
        self._spin_west.setValue(west)
        self._spin_east.setValue(east)

        import math
        dlat = math.radians(north - south)
        dlon = math.radians(east - west)
        mid = math.radians((north + south) / 2)
        area = abs(dlat * 6371 * dlon * 6371 * math.cos(mid))
        self._area_label.setText(
            tr("wizard_demest.approx_area", area=f"{area:.1f}")
        )
        self._draw_status.setText(
            f"({south:.3f}, {west:.3f}) → ({north:.3f}, {east:.3f})"
        )

    def get_bounds(self) -> Optional[tuple[float, float, float, float]]:
        return self._bounds

    def get_polygon_coords(self) -> list[tuple[float, float]]:
        """Return drawn polygon as (lat, lon) list; empty if none drawn."""
        return self._polygon_coords

    def cancel_all(self):
        for f in self._detect_fetchers:
            if hasattr(f, "cancel"):
                f.cancel()
        self._detect_fetchers.clear()
        if self._map_widget:
            try:
                self._map_widget.disable_domain_polygon_draw()
                self._map_widget.clear_domain_polygon()
            except Exception:
                pass


# =====================================================================
# Step 2: Proxy Data — Building Footprints + Land Use
# =====================================================================


class ProxyDataStep(QWidget):
    """Fetch spatial proxy datasets for demand estimation."""

    fetchingStarted = Signal()
    fetchingFinished = Signal()
    validityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bounds: Optional[tuple[float, float, float, float]] = None
        self._proxy_results: dict = {}
        self._fetchers: list = []
        self._pending_fetches: int = 0
        self._countries: dict[str, dict] = {}
        self._raw_macro_data: dict = {}
        self._macro_pending: int = 0
        self._base_year: int = 2025
        self._sim_years: int = 25
        self._meteo_data: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("wizard_demest.step2_instruction")))

        # ── Proxies (datasets + weights) — two columns ──
        proxy_group = QGroupBox(tr("wizard_demest.proxies"))
        proxy_cols = QHBoxLayout(proxy_group)

        # Column 1: Datasets
        pcol1 = QVBoxLayout()
        proxy_form = QFormLayout()
        proxy_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._chk_buildings = QCheckBox()
        self._chk_buildings.setChecked(True)
        proxy_form.addRow(tr("wizard_demest.proxy_buildings"), self._chk_buildings)

        self._combo_building_src = QComboBox()
        self._combo_building_src.addItem("Overture Maps", "overture")
        self._combo_building_src.addItem("Microsoft ML", "microsoft")
        self._combo_building_src.addItem("Google Open Buildings", "google")
        proxy_form.addRow(tr("wizard_demest.bldg_source"), self._combo_building_src)

        self._chk_population = QCheckBox()
        self._chk_population.setChecked(True)
        proxy_form.addRow(tr("wizard_demest.proxy_population"), self._chk_population)

        self._chk_nightlights = QCheckBox()
        self._chk_nightlights.setChecked(False)
        proxy_form.addRow(tr("wizard_demest.proxy_nightlights"), self._chk_nightlights)

        self._chk_landuse = QCheckBox()
        self._chk_landuse.setChecked(True)
        proxy_form.addRow(tr("wizard_demest.proxy_landuse"), self._chk_landuse)
        pcol1.addLayout(proxy_form)
        pcol1.addStretch()

        # Column 2: Weight method + manual weights
        pcol2 = QVBoxLayout()
        weight_form = QFormLayout()
        weight_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._combo_weight_method = QComboBox()
        self._combo_weight_method.setMaximumWidth(200)
        self._combo_weight_method.addItem(tr("wizard_demest.wm_manual"), "manual")
        self._combo_weight_method.addItem(tr("wizard_demest.wm_equal"), "equal")
        self._combo_weight_method.addItem(tr("wizard_demest.wm_entropy"), "entropy")
        self._combo_weight_method.addItem(tr("wizard_demest.wm_pca"), "pca")
        self._combo_weight_method.setToolTip(tr("wizard_demest.wm_tooltip"))
        weight_form.addRow(tr("wizard_demest.weight_method"), self._combo_weight_method)

        self._spin_w_buildings = self._make_weight_spin(0.35)
        weight_form.addRow(tr("wizard_demest.weight_buildings"), self._spin_w_buildings)
        self._spin_w_population = self._make_weight_spin(0.30)
        weight_form.addRow(tr("wizard_demest.weight_population"), self._spin_w_population)
        self._spin_w_nightlights = self._make_weight_spin(0.20)
        weight_form.addRow(tr("wizard_demest.weight_nightlights"), self._spin_w_nightlights)
        self._spin_w_landuse = self._make_weight_spin(0.15)
        weight_form.addRow(tr("wizard_demest.weight_landuse"), self._spin_w_landuse)
        btn_normalize = QPushButton(tr("wizard_demest.normalize_weights"))
        btn_normalize.clicked.connect(self._normalize_weights)
        weight_form.addRow("", btn_normalize)

        self._manual_weight_panel = QWidget()
        # We'll show/hide individual rows via the panel, but all in one form
        pcol2.addLayout(weight_form)
        # Keep reference so _on_weight_method_changed can show/hide weight spins
        self._weight_form = weight_form

        self._auto_weight_panel = QWidget()
        auto_lay = QVBoxLayout(self._auto_weight_panel)
        auto_lay.setContentsMargins(4, 4, 4, 4)
        self._lbl_weight_info = QLabel("")
        self._lbl_weight_info.setWordWrap(True)
        self._lbl_weight_info.setStyleSheet("color: #aaa; font-style: italic;")
        auto_lay.addWidget(self._lbl_weight_info)
        pcol2.addWidget(self._auto_weight_panel)
        pcol2.addStretch()

        proxy_cols.addLayout(pcol1, 1)
        proxy_cols.addLayout(pcol2, 1)

        self._combo_weight_method.currentIndexChanged.connect(self._on_weight_method_changed)
        self._on_weight_method_changed()
        layout.addWidget(proxy_group)

        # ── Climate & Saturation (two columns) ──
        cs_group = QGroupBox(tr("wizard_demest.indicators_climate"))
        cs_cols = QHBoxLayout(cs_group)

        # Column 1: Climate parameters
        clim_form = QFormLayout()
        clim_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._combo_ssp = QComboBox()
        for ssp in ["SSP1", "SSP2", "SSP3", "SSP4", "SSP5"]:
            self._combo_ssp.addItem(ssp, ssp)
        self._combo_ssp.setCurrentIndex(1)
        self._combo_ssp.setMaximumWidth(90)
        clim_form.addRow(tr("wizard_demest.ssp_scenario"), self._combo_ssp)

        self._spin_year = QSpinBox()
        self._spin_year.setRange(1980, 2025)
        self._spin_year.setValue(2022)
        self._spin_year.setMaximumWidth(100)
        clim_form.addRow(tr("wizard_demest.weather_year"), self._spin_year)

        self._spin_hdd_base = QDoubleSpinBox()
        self._spin_hdd_base.setRange(0, 30)
        self._spin_hdd_base.setValue(18.0)
        self._spin_hdd_base.setSuffix(" °C")
        self._spin_hdd_base.setMaximumWidth(100)
        clim_form.addRow(tr("wizard_demest.hdd_base"), self._spin_hdd_base)

        self._spin_cdd_base = QDoubleSpinBox()
        self._spin_cdd_base.setRange(15, 40)
        self._spin_cdd_base.setValue(24.0)
        self._spin_cdd_base.setSuffix(" °C")
        self._spin_cdd_base.setMaximumWidth(100)
        clim_form.addRow(tr("wizard_demest.cdd_base"), self._spin_cdd_base)

        # Hidden lat/lon — auto-set from Step 1 node centroid
        self._spin_lat = QDoubleSpinBox()
        self._spin_lat.setRange(-90, 90)
        self._spin_lat.setDecimals(4)
        self._spin_lat.setVisible(False)
        self._spin_lon = QDoubleSpinBox()
        self._spin_lon.setRange(-180, 180)
        self._spin_lon.setDecimals(4)
        self._spin_lon.setVisible(False)
        self._lbl_countries = QLabel("")
        self._lbl_countries.setVisible(False)

        cs_cols.addLayout(clim_form, 1)

        # Column 2: Saturation parameters (logistic)
        sat_form = QFormLayout()
        sat_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._spin_eff_sat = QDoubleSpinBox()
        self._spin_eff_sat.setRange(0.01, 1.0)
        self._spin_eff_sat.setSingleStep(0.05)
        self._spin_eff_sat.setDecimals(2)
        self._spin_eff_sat.setValue(0.50)
        self._spin_eff_sat.setToolTip(
            "Maximum cumulative energy intensity reduction (logistic ceiling).\n"
            "E.g. 0.50 = efficiency improvements can reduce intensity by at most 50%."
        )
        self._spin_eff_sat.setMaximumWidth(100)
        sat_form.addRow(tr("wizard_demest.eff_saturation"), self._spin_eff_sat)

        self._spin_eff_base = QDoubleSpinBox()
        self._spin_eff_base.setRange(0.0, 0.99)
        self._spin_eff_base.setSingleStep(0.05)
        self._spin_eff_base.setDecimals(2)
        self._spin_eff_base.setValue(0.0)
        self._spin_eff_base.setToolTip(
            "Current cumulative efficiency level at the base year.\n"
            "0 = no improvements yet, 0.3 = already 30% improved."
        )
        self._spin_eff_base.setMaximumWidth(100)
        sat_form.addRow(tr("wizard_demest.eff_base_level"), self._spin_eff_base)

        self._spin_elec_sat = QDoubleSpinBox()
        self._spin_elec_sat.setRange(0.01, 1.0)
        self._spin_elec_sat.setSingleStep(0.05)
        self._spin_elec_sat.setDecimals(2)
        self._spin_elec_sat.setValue(1.0)
        self._spin_elec_sat.setToolTip(
            "Maximum electrification penetration (logistic ceiling).\n"
            "1.0 = full electrification possible."
        )
        self._spin_elec_sat.setMaximumWidth(100)
        sat_form.addRow(tr("wizard_demest.elec_saturation"), self._spin_elec_sat)

        self._spin_elec_base = QDoubleSpinBox()
        self._spin_elec_base.setRange(0.0, 0.99)
        self._spin_elec_base.setSingleStep(0.05)
        self._spin_elec_base.setDecimals(2)
        self._spin_elec_base.setValue(0.0)
        self._spin_elec_base.setToolTip(
            "Current electrification level at the base year.\n"
            "0 = starting from scratch, 0.5 = already 50% electrified."
        )
        self._spin_elec_base.setMaximumWidth(100)
        sat_form.addRow(tr("wizard_demest.elec_base_level"), self._spin_elec_base)

        cs_cols.addLayout(sat_form, 1)
        layout.addWidget(cs_group)

        # ── Reference profile ──
        profile_group = QGroupBox(tr("wizard_demest.reference_profile"))
        profile_vlay = QVBoxLayout(profile_group)

        combo_row = QHBoxLayout()
        combo_row.addWidget(QLabel(tr("wizard_demest.profile_type")))
        self._combo_profile = QComboBox()
        self._combo_profile.setMinimumWidth(280)
        self._combo_profile.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_tropical"), "tropical_island"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_temperate"), "temperate_urban"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_arid"), "arid_industrial"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_flat"), "flat_baseload"
        )
        combo_row.addWidget(self._combo_profile)

        self._btn_edit_profile = QPushButton(tr("wizard_demest.edit_profile"))
        self._btn_edit_profile.clicked.connect(self._open_profile_editor)
        combo_row.addWidget(self._btn_edit_profile)
        combo_row.addStretch()
        profile_vlay.addLayout(combo_row)

        if _MPL_OK:
            self._profile_fig = _Figure(figsize=(6, 2.2), facecolor=current_theme().colors.surface_primary)
            self._profile_canvas = _FigCanvas(self._profile_fig)
            self._profile_canvas.setMinimumHeight(160)
            profile_vlay.addWidget(self._profile_canvas)
        else:
            self._profile_fig = None
            self._profile_canvas = None

        self._combo_profile.currentIndexChanged.connect(self._update_profile_chart)
        layout.addWidget(profile_group)

        # ── Fetch all ──
        fetch_row = QHBoxLayout()
        self._btn_fetch = QPushButton(tr("wizard_demest.fetch_all"))
        self._btn_fetch.clicked.connect(self._start_fetch)
        fetch_row.addWidget(self._btn_fetch)
        self._fetch_status = QLabel("")
        fetch_row.addWidget(self._fetch_status, 1)
        layout.addLayout(fetch_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._results_text = QTextEdit()
        self._results_text.setReadOnly(True)
        self._results_text.setMaximumHeight(100)
        self._results_text.setVisible(False)
        layout.addWidget(self._results_text)
        self._update_profile_chart()

        layout.addStretch()
        outer.addWidget(_scrolled(inner))

    @staticmethod
    def _make_weight_spin(default: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 1)
        spin.setSingleStep(0.05)
        spin.setValue(default)
        spin.setMaximumWidth(100)
        return spin

    def _on_weight_method_changed(self):
        method = self._combo_weight_method.currentData()
        is_manual = method == "manual"
        # Show/hide weight spinners and normalize button
        for w in (self._spin_w_buildings, self._spin_w_population,
                  self._spin_w_nightlights, self._spin_w_landuse):
            w.setVisible(is_manual)
            # Also hide the label (QFormLayout stores it)
            label = self._weight_form.labelForField(w)
            if label:
                label.setVisible(is_manual)
        self._auto_weight_panel.setVisible(not is_manual)
        info = {
            "equal": tr("wizard_demest.wm_equal_info"),
            "entropy": tr("wizard_demest.wm_entropy_info"),
            "pca": tr("wizard_demest.wm_pca_info"),
        }
        self._lbl_weight_info.setText(info.get(method, ""))

    def _normalize_weights(self):
        total = (
            self._spin_w_buildings.value()
            + self._spin_w_population.value()
            + self._spin_w_nightlights.value()
            + self._spin_w_landuse.value()
        )
        if total > 0:
            self._spin_w_buildings.setValue(self._spin_w_buildings.value() / total)
            self._spin_w_population.setValue(self._spin_w_population.value() / total)
            self._spin_w_nightlights.setValue(self._spin_w_nightlights.value() / total)
            self._spin_w_landuse.setValue(self._spin_w_landuse.value() / total)

    def _set_bounds(self, south, west, north, east):
        """Receive bounds from Step 1 propagation."""
        self._bounds = (south, west, north, east)
        self.validityChanged.emit()

    def _start_fetch(self):
        if self._bounds is None:
            QMessageBox.warning(
                self,
                tr("wizard_demest.no_domain"),
                tr("wizard_demest.no_domain_msg"),
            )
            return

        self._cancel_fetchers()
        self._proxy_results = {}
        self._raw_macro_data = {}
        self._pending_fetches = 0
        self._macro_pending = 0
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._btn_fetch.setEnabled(False)
        self._fetch_status.setText(tr("wizard_demest.fetching_all"))
        self.fetchingStarted.emit()
        self.validityChanged.emit()

        # ── Spatial proxies ──
        if self._chk_buildings.isChecked():
            self._pending_fetches += 1
            from esfex.visualization.workflows.data_fetchers import BuildingFetcher
            f = BuildingFetcher(
                source=self._combo_building_src.currentData(),
                bounds=self._bounds,
                parent=self,
            )
            f.finished.connect(self._on_buildings_done)
            f.error.connect(self._on_fetch_error)
            self._fetchers.append(f)
            f.start()

        if self._chk_landuse.isChecked():
            self._pending_fetches += 1
            from esfex.visualization.workflows.demand_estimation_fetchers import (
                OSMLandUseFetcher,
            )
            f = OSMLandUseFetcher(bounds=self._bounds, parent=self)
            f.finished.connect(self._on_landuse_done)
            f.error.connect(self._on_fetch_error)
            self._fetchers.append(f)
            f.start()

        # ── Macroeconomic indicators (WB + IMF) ──
        iso3_list = [
            info.get("iso3", "")
            for info in self._countries.values()
            if info.get("iso3")
        ]
        unique_iso3 = list(dict.fromkeys(iso3_list))  # preserve order, dedupe

        if not unique_iso3:
            logger.error(
                "No country ISO codes available — cannot fetch macro data. "
                "Countries dict: %s", self._countries,
            )
            QMessageBox.critical(
                self,
                tr("wizard_demest.step2"),
                "No country assigned to any system.\n"
                "Cannot fetch macroeconomic data without a country.\n\n"
                "Go back to Step 1 and ensure countries are detected/selected.",
            )
            self._btn_fetch.setEnabled(True)
            self._progress.setVisible(False)
            return

        if unique_iso3:
            from esfex.visualization.workflows.demand_estimation_fetchers import (
                WorldBankDemandFetcher, IMFDemandFetcher,
                UNPopulationFetcher, SSPProjectionFetcher,
            )
            primary_iso = unique_iso3[0]

            for iso3 in unique_iso3:
                self._macro_pending += 1
                wb = WorldBankDemandFetcher(country_iso=iso3, parent=self)
                wb.finished.connect(self._on_wb_done)
                wb.error.connect(lambda msg, c=iso3: self._on_macro_error(f"WB {c}: {msg}"))
                self._fetchers.append(wb)
                wb.start()

            self._macro_pending += 1
            imf = IMFDemandFetcher(country_iso=primary_iso, parent=self)
            imf.finished.connect(self._on_imf_done)
            imf.error.connect(lambda msg: self._on_macro_error(f"IMF: {msg}"))
            self._fetchers.append(imf)
            imf.start()

            # UN World Population Prospects — annual population projections
            self._macro_pending += 1
            un_pop = UNPopulationFetcher(
                country_iso=primary_iso,
                start_year=self._base_year - 5,
                end_year=self._base_year + self._sim_years + 5,
                parent=self,
            )
            un_pop.finished.connect(self._on_un_pop_done)
            un_pop.error.connect(lambda msg: self._on_macro_error(f"UN WPP: {msg}"))
            self._fetchers.append(un_pop)
            un_pop.start()

            # IIASA SSP — GDP and population projections under selected scenario
            self._macro_pending += 1
            ssp_scenario = self._combo_ssp.currentData() or "SSP2"
            ssp = SSPProjectionFetcher(
                country_iso=primary_iso,
                scenario=ssp_scenario,
                parent=self,
            )
            ssp.finished.connect(self._on_ssp_done)
            ssp.error.connect(lambda msg: self._on_macro_error(f"SSP: {msg}"))
            self._fetchers.append(ssp)
            ssp.start()

        # ── ERA5 meteorological data ──
        if self._spin_lat.value() != 0.0 or self._spin_lon.value() != 0.0:
            self._pending_fetches += 1
            from esfex.visualization.workflows.demand_estimation_fetchers import (
                ERA5TemperatureFetcher,
            )
            era5 = ERA5TemperatureFetcher(
                lat=self._spin_lat.value(),
                lon=self._spin_lon.value(),
                year=self._spin_year.value(),
                hdd_base=self._spin_hdd_base.value(),
                cdd_base=self._spin_cdd_base.value(),
                parent=self,
            )
            era5.finished.connect(self._on_meteo_done)
            era5.error.connect(self._on_fetch_error)
            self._fetchers.append(era5)
            era5.start()

        if self._pending_fetches == 0 and self._macro_pending == 0:
            self._finish_fetching()

    def _on_buildings_done(self, gdf):
        self._proxy_results["buildings"] = gdf
        self._check_fetch_complete()

    def _on_landuse_done(self, data):
        self._proxy_results["landuse"] = data
        self._check_fetch_complete()

    def _on_fetch_error(self, msg):
        logger.warning("Proxy fetch error: %s", msg)
        self._check_fetch_complete()

    def _check_fetch_complete(self):
        self._pending_fetches -= 1
        if self._pending_fetches <= 0 and self._macro_pending <= 0:
            self._finish_fetching()

    def _on_wb_done(self, data: dict):
        for k, v in data.items():
            if k not in self._raw_macro_data:
                self._raw_macro_data[k] = v
        self._on_macro_complete()

    def _on_imf_done(self, data: dict):
        self._raw_macro_data.update(data)
        self._on_macro_complete()

    def _on_un_pop_done(self, data: dict):
        self._raw_macro_data.update(data)
        self._on_macro_complete()

    def _on_ssp_done(self, data: dict):
        self._raw_macro_data.update(data)
        self._on_macro_complete()

    def _on_macro_error(self, msg: str):
        logger.warning("Macro fetch error: %s", msg)
        self._on_macro_complete()

    def _on_meteo_done(self, data: dict):
        self._meteo_data = data
        self._check_fetch_complete()

    def _on_macro_complete(self):
        self._macro_pending -= 1
        if self._macro_pending <= 0 and self._pending_fetches <= 0:
            self._finish_fetching()

    def _finish_fetching(self):
        self._btn_fetch.setEnabled(True)
        self._progress.setValue(100)
        self._fetch_status.setText(tr("wizard_demest.all_fetched"))
        self.fetchingFinished.emit()
        self.validityChanged.emit()

        lines = []
        bld = self._proxy_results.get("buildings")
        if bld is not None:
            lines.append(f"Buildings: {len(bld) if hasattr(bld, '__len__') else 0}")
        lu = self._proxy_results.get("landuse")
        if lu:
            lines.append(
                f"Land use: R={lu.get('residential_fraction', 0):.0%} "
                f"C={lu.get('commercial_fraction', 0):.0%} "
                f"I={lu.get('industrial_fraction', 0):.0%}"
            )
        if self._raw_macro_data:
            gdp = self._raw_macro_data.get("gdp_per_capita")
            if gdp:
                lines.append(f"GDP/capita: ${gdp:,.0f}")
            ec = self._raw_macro_data.get("electric_consumption_kwh_capita")
            if ec:
                lines.append(f"Electricity: {ec:.0f} kWh/capita")
        if self._meteo_data:
            t_mean = self._meteo_data.get("temp_mean")
            if t_mean is not None:
                lines.append(
                    f"ERA5: {self._meteo_data.get('temp_min', 0):.1f}–"
                    f"{self._meteo_data.get('temp_max', 0):.1f}°C  "
                    f"HDD={self._meteo_data.get('hdd_total', 0):.0f}  "
                    f"CDD={self._meteo_data.get('cdd_total', 0):.0f}"
                )
        self._results_text.setVisible(True)
        self._results_text.setPlainText("\n".join(lines) or "No data fetched.")

    def _cancel_fetchers(self):
        for f in self._fetchers:
            if hasattr(f, "cancel"):
                f.cancel()
        self._fetchers.clear()

    def cancel_all(self):
        self._cancel_fetchers()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_countries(
        self,
        countries: dict[str, dict],
        base_year: int = 2025,
        sim_years: int = 25,
    ) -> None:
        """Receive country assignments and horizon from Step 1."""
        self._countries = countries
        self._base_year = base_year
        self._sim_years = sim_years
        names = ", ".join(
            info.get("name", iso) for iso, info in countries.items()
        ) or tr("wizard_demest.countries_from_step1")
        self._lbl_countries.setText(names)

    def get_raw_macro_data(self) -> dict:
        return dict(self._raw_macro_data)

    def is_fetching(self) -> bool:
        """True while background data fetchers are still running."""
        return self._pending_fetches > 0 or self._macro_pending > 0

    def is_valid(self) -> bool:
        if self._bounds is None:
            return False
        if self.is_fetching():
            return False
        # Must have fetched macro data (population at minimum)
        if not self._raw_macro_data.get("population"):
            return False
        return True

    def get_bounds(self) -> Optional[tuple[float, float, float, float]]:
        return self._bounds

    def get_proxy_results(self) -> dict:
        return self._proxy_results

    def get_proxy_weights(self) -> dict:
        method = self._combo_weight_method.currentData()
        if method == "manual":
            return {
                "method": "manual",
                "buildings": self._spin_w_buildings.value(),
                "population": self._spin_w_population.value(),
                "nightlights": self._spin_w_nightlights.value(),
                "landuse": self._spin_w_landuse.value(),
            }
        return {"method": method}

    def get_enabled_proxies(self) -> dict[str, bool]:
        return {
            "buildings": self._chk_buildings.isChecked(),
            "population": self._chk_population.isChecked(),
            "nightlights": self._chk_nightlights.isChecked(),
            "landuse": self._chk_landuse.isChecked(),
        }

    # ── Meteo public API ─────────────────────────────────────────────────────

    def set_location(self, lat: float, lon: float) -> None:
        self._spin_lat.setValue(lat)
        self._spin_lon.setValue(lon)

    def set_weather_year(self, year: int) -> None:
        self._spin_year.setValue(year)

    def get_meteo_data(self) -> dict:
        d = dict(self._meteo_data)
        d["hdd_base_temp"] = self._spin_hdd_base.value()
        d["cdd_base_temp"] = self._spin_cdd_base.value()
        d["reference_profile"] = self._combo_profile.currentData()
        d["weather_year"] = self._spin_year.value()
        d["lat"] = self._spin_lat.value()
        d["lon"] = self._spin_lon.value()
        return d

    def get_saturation_params(self) -> dict:
        return {
            "efficiency_saturation": self._spin_eff_sat.value(),
            "efficiency_base_level": self._spin_eff_base.value(),
            "electrification_saturation": self._spin_elec_sat.value(),
            "electrification_base_level": self._spin_elec_base.value(),
        }

    def _update_profile_chart(self, _index: int = 0) -> None:
        if not _MPL_OK or self._profile_fig is None:
            return
        from esfex.visualization.workflows.demand_estimation_analysis import (
            _get_profile,
        )

        profile_type = self._combo_profile.currentData()
        try:
            series = _get_profile(profile_type)
        except Exception:
            return

        hours_in_series = len(series)
        complete_days = hours_in_series // 24
        daily = series[: complete_days * 24].reshape(complete_days, 24)
        avg_day = daily.mean(axis=0)
        peak = avg_day.max() if avg_day.max() > 0 else 1.0
        avg_day_norm = avg_day / peak

        self._profile_fig.clear()
        ax = self._profile_fig.add_subplot(111, facecolor=current_theme().colors.surface_primary)
        hours = np.arange(24)
        ax.fill_between(hours, avg_day_norm, alpha=0.35, color=current_theme().colors.accent_primary)
        ax.plot(hours, avg_day_norm, color=current_theme().colors.accent_primary, linewidth=1.5)
        ax.set_xlim(0, 23)
        ax.set_ylim(0, 1.05)
        _tc = current_theme().colors
        ax.set_xlabel("Hour of day", color=_tc.text_secondary, fontsize=8)
        ax.set_ylabel("Norm. demand", color=_tc.text_secondary, fontsize=8)
        ax.tick_params(colors=_tc.text_secondary, labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor(_tc.border_light)
        self._profile_fig.tight_layout(pad=0.4)
        self._profile_canvas.draw()

    def _open_profile_editor(self) -> None:
        """Open a dialog to edit the 24h × 12-month archetype shape table."""
        from esfex.visualization.workflows.demand_estimation_analysis import (
            ARCHETYPE_LIBRARY,
        )

        profile_key = self._combo_profile.currentData()
        archetype = ARCHETYPE_LIBRARY.get(profile_key)
        if archetype is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("wizard_demest.edit_profile"))
        dlg.setMinimumSize(700, 500)
        dlg_lay = QVBoxLayout(dlg)

        dlg_lay.addWidget(QLabel(
            tr("wizard_demest.edit_profile_note")
        ))

        # Table: rows = 24 hours, cols = shape keys
        shape_keys = sorted(archetype.shapes.keys())
        tbl = QTableWidget(24, len(shape_keys))
        tbl.setHorizontalHeaderLabels(shape_keys)
        tbl.setVerticalHeaderLabels([f"{h:02d}:00" for h in range(24)])
        for col, key in enumerate(shape_keys):
            shape = archetype.shapes[key]
            for row in range(min(24, len(shape))):
                it = QTableWidgetItem(f"{shape[row]:.4f}")
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(row, col, it)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        for c in range(len(shape_keys)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        dlg_lay.addWidget(tbl, 1)

        # Monthly factors row
        mf_group = QGroupBox(tr("wizard_demest.monthly_factors"))
        mf_lay = QHBoxLayout(mf_group)
        month_labels = ["Jan","Feb","Mar","Apr","May","Jun",
                        "Jul","Aug","Sep","Oct","Nov","Dec"]
        mf_spins: list[QDoubleSpinBox] = []
        for i, ml in enumerate(month_labels):
            vl = QVBoxLayout()
            vl.addWidget(QLabel(ml))
            sp = QDoubleSpinBox()
            sp.setRange(0.01, 3.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.01)
            sp.setValue(archetype.monthly_factors[i] if i < len(archetype.monthly_factors) else 1.0)
            sp.setMaximumWidth(70)
            mf_spins.append(sp)
            vl.addWidget(sp)
            mf_lay.addLayout(vl)
        dlg_lay.addWidget(mf_group)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_apply = QPushButton(tr("wizard_demest.apply_calibration"))
        btn_apply.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_apply)
        btn_cancel = QPushButton(tr("wizard_demest.cancel"))
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(btn_cancel)
        dlg_lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Read edited values back into a mutable copy of the archetype
        from esfex.visualization.workflows.demand_estimation_analysis import (
            ArchetypeProfile, _build_archetype,
        )

        new_shapes: dict[str, tuple] = {}
        for col, key in enumerate(shape_keys):
            vals = []
            for row in range(24):
                it = tbl.item(row, col)
                try:
                    vals.append(float(it.text()))
                except (ValueError, AttributeError):
                    vals.append(archetype.shapes[key][row])
            new_shapes[key] = tuple(vals)

        new_mf = tuple(sp.value() for sp in mf_spins)

        edited = ArchetypeProfile(
            name=archetype.name + "_edited",
            description="User-edited profile",
            monthly_factors=new_mf,
            shapes=new_shapes,
            winter_months=archetype.winter_months,
            summer_months=archetype.summer_months,
            hdd_beta=archetype.hdd_beta,
            cdd_beta=archetype.cdd_beta,
        )
        # Register in the library so the builder can find it
        ARCHETYPE_LIBRARY[edited.name] = edited
        # Add to combo and select
        self._combo_profile.addItem(
            f"{tr('wizard_demest.profile_type')}: {archetype.name} (edited)",
            edited.name,
        )
        self._combo_profile.setCurrentIndex(self._combo_profile.count() - 1)


# =====================================================================
# Step 3: Macroeconomic Indicators
# =====================================================================


class MacroEconomicStep(QWidget):
    """Year-by-year projection parameters + demand profile builder.

    The main UI element is a QTableWidget with years as rows and projection
    parameters as columns.  Data is pre-filled from World Bank / IMF / SSP
    downloads and fully editable by the user.  The Build button at the bottom
    triggers the demand estimation engine.
    """

    buildFinished = Signal()

    # ── Column indices ────────────────────────────────────────────────────────
    _COL_YEAR = 0
    _COL_GDP  = 1
    _COL_POP  = 2
    _COL_ELAS = 3
    _COL_EFF  = 4
    _COL_ELEC = 5
    _NCOLS    = 6

    # Default values for each editable column
    _DEFAULTS: dict[int, float] = {
        _COL_GDP:  0.030,
        _COL_POP:  0.010,
        _COL_ELAS: 0.80,
        _COL_EFF:  0.005,
        _COL_ELEC: 0.010,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._system_countries: dict[str, dict] = {}
        self._base_year: int = 2025
        self._sim_years: int = 25
        self._raw_data: dict = {}
        self._worker = None
        self._result = None
        self._build_inputs: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("wizard_demest.step3_instruction")))

        self._fetch_status = QLabel("")
        self._fetch_status.setStyleSheet("color: #aaa; font-size: 10px; padding: 2px 0;")
        layout.addWidget(self._fetch_status)

        # ── Year-by-year projection table ─────────────────────────────────────
        tbl_group = QGroupBox(tr("wizard_demest.projection_table"))
        tbl_lay = QVBoxLayout(tbl_group)

        self._table = QTableWidget(0, self._NCOLS)
        self._table.setHorizontalHeaderLabels([
            tr("wizard_demest.col_year"),
            tr("wizard_demest.col_gdp_growth"),
            tr("wizard_demest.col_pop_growth"),
            tr("wizard_demest.col_elasticity"),
            tr("wizard_demest.col_efficiency"),
            tr("wizard_demest.col_electrif"),
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, self._NCOLS):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(250)
        tbl_lay.addWidget(self._table)

        note = QLabel(tr("wizard_demest.table_note"))
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 10px; padding: 2px 0;")
        tbl_lay.addWidget(note)
        layout.addWidget(tbl_group, 1)

        # ── Build row ─────────────────────────────────────────────────────────
        build_row = QHBoxLayout()
        self._btn_build = QPushButton(tr("wizard_demest.build_profiles"))
        self._btn_build.clicked.connect(self._run_build)
        build_row.addWidget(self._btn_build)

        self._combo_engine = QComboBox()
        # TFT disabled for now — forward per-node generation uses XGBoost.
        self._combo_engine.addItem("Auto (XGBoost)", "auto")
        self._combo_engine.addItem("XGBoost", "xgboost")
        self._combo_engine.addItem("Archetype", "archetype")
        self._combo_engine.setMaximumWidth(180)
        build_row.addWidget(self._combo_engine)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        build_row.addWidget(self._progress, 1)

        self._build_status = QLabel("")
        self._build_status.setStyleSheet("color: #aaa; font-size: 10px;")
        build_row.addWidget(self._build_status)
        layout.addLayout(build_row)

        outer.addWidget(_scrolled(inner))
        self._init_table()

    # ── Table helpers ─────────────────────────────────────────────────────────

    def _init_table(self) -> None:
        """Rebuild rows for the current base_year / sim_years, keeping edited values."""
        # Preserve any user edits keyed by (year, col)
        saved: dict[tuple[int, int], str] = {}
        for r in range(self._table.rowCount()):
            yr_item = self._table.item(r, self._COL_YEAR)
            if yr_item is None:
                continue
            try:
                yr = int(yr_item.text())
            except ValueError:
                continue
            for c in range(1, self._NCOLS):
                it = self._table.item(r, c)
                if it is not None:
                    saved[(yr, c)] = it.text()

        self._table.blockSignals(True)
        self._table.setRowCount(self._sim_years)

        for row in range(self._sim_years):
            year = self._base_year + row
            # Year column — not editable
            yr_item = QTableWidgetItem(str(year))
            yr_item.setFlags(yr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            yr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, self._COL_YEAR, yr_item)

            for col, default in self._DEFAULTS.items():
                text = saved.get((year, col), self._fmt(col, default))
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, it)

        self._table.blockSignals(False)

    @staticmethod
    def _fmt(col: int, value: float) -> str:
        """Format a cell value according to its column type."""
        if col == MacroEconomicStep._COL_ELAS:
            return f"{value:.3f}"
        return f"{value:.4f}"

    def _set_col(self, col: int, year_values: dict[int, float],
                 overwrite: bool = True) -> None:
        """Fill a column from a {year: value} dict."""
        for row in range(self._table.rowCount()):
            yr_item = self._table.item(row, self._COL_YEAR)
            if yr_item is None:
                continue
            try:
                year = int(yr_item.text())
            except ValueError:
                continue
            if year not in year_values:
                continue
            it = self._table.item(row, col)
            if it is None:
                continue
            if overwrite or it.text() == self._fmt(col, self._DEFAULTS[col]):
                it.setText(self._fmt(col, year_values[year]))

    def _get_col(self, col: int) -> dict[int, float]:
        """Read a column as {year: value}."""
        result = {}
        for row in range(self._table.rowCount()):
            yr_item = self._table.item(row, self._COL_YEAR)
            val_item = self._table.item(row, col)
            if yr_item is None or val_item is None:
                continue
            try:
                result[int(yr_item.text())] = float(val_item.text())
            except ValueError:
                pass
        return result

    # ── Public API ────────────────────────────────────────────────────────────

    def set_bounds(self, bounds):
        pass  # kept for backward compatibility

    def set_countries(
        self,
        system_countries: dict[str, dict],
        base_year: int = 2025,
        sim_years: int = 25,
    ) -> None:
        """Receive country assignments and horizon from Step 1."""
        self._system_countries = system_countries
        self._base_year = base_year
        self._sim_years = sim_years
        self._init_table()

    def is_valid(self) -> bool:
        return True

    def get_macro_data(self) -> dict:
        gdp   = self._get_col(self._COL_GDP)
        pop   = self._get_col(self._COL_POP)
        elas  = self._get_col(self._COL_ELAS)
        eff   = self._get_col(self._COL_EFF)
        elec  = self._get_col(self._COL_ELEC)

        def _avg(d: dict) -> float:
            vals = [v for v in d.values() if v is not None]
            return sum(vals) / len(vals) if vals else 0.0

        primary = next(iter(self._system_countries.values()), {}) if self._system_countries else {}
        d = dict(self._raw_data)
        d.update({
            "country_iso":            primary.get("iso3", ""),
            "country_name":           primary.get("name", ""),
            "system_countries":       self._system_countries,
            # Per-year time series
            "gdp_growth_by_year":     gdp,
            "pop_growth_by_year":     pop,
            "elasticity_by_year":     elas,
            "efficiency_by_year":     eff,
            "electrification_by_year":elec,
            # Scalar averages for backward compatibility
            "gdp_growth_rate":        _avg(gdp),
            "demand_gdp_elasticity":  _avg(elas),
            "efficiency_improvement": _avg(eff),
            "electrification_growth": _avg(elec),
        })
        return d

    def cancel_all(self):
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()

    def is_valid(self) -> bool:
        return self._result is not None

    def get_result(self):
        return self._result

    def set_build_inputs(self, **kwargs):
        """Store inputs needed to run the build when the user clicks Build."""
        self._build_inputs = kwargs

    def _run_build(self):
        if not self._build_inputs:
            return
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._btn_build.setEnabled(False)
        self._build_status.setText(tr("wizard_demest.building_profiles"))

        from esfex.visualization.workflows.demand_estimation_analysis import (
            DemandEstimationConfig,
            DemandEstimationWorker,
            MacroData,
            MeteoData,
            ProxyData,
        )

        bi = self._build_inputs
        macro_data = self.get_macro_data()
        meteo_data = bi.get("meteo_data", {})
        proxy_results = bi.get("proxy_results", {})
        proxy_weights = bi.get("proxy_weights", {})
        nodes = bi.get("nodes", [])

        weight_method = proxy_weights.get("method", "manual")
        engine = self._combo_engine.currentData() or "auto"
        force_archetype = engine == "archetype"

        config = DemandEstimationConfig(
            base_year=self._base_year,
            ml_engine=engine if not force_archetype else "auto",
            simulation_years=self._sim_years,
            num_nodes=len(nodes),
            resolution_hours=bi.get("resolution", 1.0),
            national_demand_gwh=bi.get("national_demand_gwh", 0.0),
            force_archetype=force_archetype,
            weight_method=weight_method,
            weight_buildings=proxy_weights.get("buildings", 0.35),
            weight_population=proxy_weights.get("population", 0.30),
            weight_nightlights=proxy_weights.get("nightlights", 0.20),
            weight_landuse=proxy_weights.get("landuse", 0.15),
            reference_profile=meteo_data.get("reference_profile", "tropical_island"),
            hdd_base_temp=meteo_data.get("hdd_base_temp", 18.0),
            cdd_base_temp=meteo_data.get("cdd_base_temp", 24.0),
            heating_sensitivity=meteo_data.get("heating_sensitivity", 0.0),
            cooling_sensitivity=meteo_data.get("cooling_sensitivity", 0.02),
            gdp_growth_rate=macro_data.get("gdp_growth_rate", 0.03),
            demand_gdp_elasticity=macro_data.get("demand_gdp_elasticity", 0.8),
            efficiency_improvement=macro_data.get("efficiency_improvement", 0.005),
            electrification_growth=macro_data.get("electrification_growth", 0.01),
            efficiency_saturation=bi.get("efficiency_saturation", 0.50),
            efficiency_base_level=bi.get("efficiency_base_level", 0.0),
            electrification_saturation=bi.get("electrification_saturation", 1.0),
            electrification_base_level=bi.get("electrification_base_level", 0.0),
        )

        proxy = ProxyData(
            node_lats=[n["lat"] for n in nodes],
            node_lons=[n["lon"] for n in nodes],
            node_names=[n["name"] for n in nodes],
        )
        buildings_gdf = proxy_results.get("buildings")
        if buildings_gdf is not None and hasattr(buildings_gdf, "__len__"):
            proxy.building_weights = self._compute_building_weights(
                buildings_gdf, nodes
            )

        lu = proxy_results.get("landuse", {})
        if lu:
            n = len(nodes)
            proxy.landuse_weights = [1.0 / n] * n if n else []
            config.residential_fraction = lu.get("residential_fraction", 0.40)
            config.commercial_fraction = lu.get("commercial_fraction", 0.35)
            config.industrial_fraction = lu.get("industrial_fraction", 0.25)

        macro = MacroData(
            country_iso=macro_data.get("country_iso", ""),
            country_name=macro_data.get("country_name", ""),
            gdp_per_capita=macro_data.get("gdp_per_capita") or 0.0,
            population=macro_data.get("population") or 0.0,
            urbanization_pct=macro_data.get("urbanization_pct") or 50.0,
            electricity_access_pct=macro_data.get("electricity_access") or 100.0,
            electric_consumption_kwh_capita=macro_data.get(
                "electric_consumption_kwh_capita"
            ) or 0.0,
            gdp_growth_rate=macro_data.get("gdp_growth_rate", 0.03),
            gdp_time_series=macro_data.get("gdp_time_series", {}),
            consumption_time_series=macro_data.get("consumption_time_series", {}),
            gdp_growth_by_year=macro_data.get("gdp_growth_by_year", {}),
            pop_growth_by_year=macro_data.get("pop_growth_by_year", {}),
            elasticity_by_year=macro_data.get("elasticity_by_year", {}),
            efficiency_by_year=macro_data.get("efficiency_by_year", {}),
            electrification_by_year=macro_data.get("electrification_by_year", {}),
        )

        meteo = MeteoData(
            temperature_hourly=meteo_data.get("temperature_2m", []),
            humidity_hourly=meteo_data.get("relative_humidity_2m", []),
            hdd_hourly=meteo_data.get("hdd", []),
            cdd_hourly=meteo_data.get("cdd", []),
            lat=meteo_data.get("lat", 0),
            lon=meteo_data.get("lon", 0),
            year=meteo_data.get("weather_year", 2022),
        )

        self._worker = DemandEstimationWorker(
            config=config, proxy=proxy, macro=macro, meteo=meteo, parent=self
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_build_done)
        self._worker.error.connect(self._on_build_error)
        self._worker.start()

    @staticmethod
    def _compute_building_weights(gdf, nodes) -> list[float]:
        n = len(nodes)
        if n == 0:
            return []
        if n == 1:
            return [1.0]
        try:
            gdf_proj = gdf.to_crs(epsg=3857) if gdf.crs else gdf
            centroids_4326 = gdf_proj.geometry.centroid.to_crs(epsg=4326)
            cx = np.asarray(centroids_4326.x, dtype=np.float64)
            cy = np.asarray(centroids_4326.y, dtype=np.float64)
            node_lats = np.array([nd["lat"] for nd in nodes], dtype=np.float64)
            node_lons = np.array([nd["lon"] for nd in nodes], dtype=np.float64)
            dy = cy[:, np.newaxis] - node_lats[np.newaxis, :]
            dx = cx[:, np.newaxis] - node_lons[np.newaxis, :]
            best_node = np.argmin(dy ** 2 + dx ** 2, axis=1)
            if "footprint_area_m2" in gdf.columns:
                areas = gdf["footprint_area_m2"].fillna(100.0).to_numpy(dtype=np.float64)
                areas = np.where(areas <= 0, 100.0, areas)
            else:
                areas = np.full(len(gdf), 100.0, dtype=np.float64)
            weights_arr = np.zeros(n, dtype=np.float64)
            np.add.at(weights_arr, best_node, areas)
            total = weights_arr.sum()
            if total > 0:
                return (weights_arr / total).tolist()
            return [1.0 / n] * n
        except Exception:
            logger.exception("Building weight computation failed")
            return [1.0 / n] * n

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._build_status.setText(msg)

    def _on_build_done(self, result):
        self._result = result
        self._btn_build.setEnabled(True)
        self._progress.setValue(100)
        source = getattr(result, "demand_source", "")
        engine_label = "ML" if "ml" in source else "Archetype"
        warnings = getattr(result, "warnings", [])
        status = f"{tr('wizard_demest.build_complete')} [{engine_label}]"
        if warnings:
            status += f"  ⚠ {len(warnings)} warning(s)"
        self._build_status.setText(status)
        self.buildFinished.emit()

    def _on_build_error(self, msg):
        self._btn_build.setEnabled(True)
        self._progress.setVisible(False)
        self._build_status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Build Error", msg)

    def fill_from_data(self, data: dict) -> None:
        """Pre-fill the table from all data sources fetched in Step 2.

        Priority chain per column (later sources override earlier ones):
          GDP Growth:    IIASA SSP → IMF WEO (near-term override)
          Pop Growth:    IIASA SSP → UN WPP (preferred, annual)
          Elasticity:    WB historical consumption/GDP ratio
          Electrification: WB historical electricity access time series
        """
        if not data:
            return
        self._raw_data.update(data)
        sources: dict[str, str] = {}

        # ── GDP Growth ────────────────────────────────────────────────────────
        # Layer 1: IIASA SSP projections (long-term baseline)
        ssp_gdp: dict = data.get("ssp_gdp_growth", {})
        if ssp_gdp:
            self._set_col(self._COL_GDP, ssp_gdp)
            sources["gdp"] = f"IIASA SSP ({data.get('scenario', '?')})"

        # Layer 2: IMF WEO forecasts override near-term (~5 years)
        imf_forecast: dict = data.get("gdp_growth_rate_forecast", {})
        if imf_forecast:
            self._set_col(self._COL_GDP,
                          {int(k): float(v) for k, v in imf_forecast.items()})
            src = sources.get("gdp", "")
            sources["gdp"] = f"IMF WEO + {src}" if src else "IMF WEO"

        # Fallback: WB historical extrapolation if neither SSP nor IMF
        if "gdp" not in sources:
            gdp_ts: dict = data.get("gdp_time_series", {})
            gdp_rates = self._growth_rates_from_ts(
                gdp_ts, base_year=self._base_year, sim_years=self._sim_years,
            )
            if gdp_rates:
                self._set_col(self._COL_GDP, gdp_rates)
                sources["gdp"] = "World Bank (hist.)"

        # ── Population Growth ─────────────────────────────────────────────────
        # Layer 1: IIASA SSP population (fallback baseline)
        ssp_pop: dict = data.get("ssp_pop_growth", {})
        if ssp_pop:
            self._set_col(self._COL_POP, ssp_pop)
            sources["pop"] = f"IIASA SSP ({data.get('scenario', '?')})"

        # Layer 2: UN WPP overrides (preferred — annual, authoritative)
        un_pop: dict = data.get("un_pop_growth_rates", {})
        if un_pop:
            self._set_col(self._COL_POP, un_pop)
            sources["pop"] = "UN World Population Prospects"

        # Fallback: WB historical
        if "pop" not in sources:
            pop_ts: dict = data.get("population_time_series", {})
            pop_rates = self._growth_rates_from_ts(
                pop_ts, base_year=self._base_year, sim_years=self._sim_years,
            )
            if pop_rates:
                self._set_col(self._COL_POP, pop_rates)
                sources["pop"] = "World Bank (hist.)"

        # ── Elasticity (from WB historical consumption/GDP ratio) ─────────────
        gdp_ts_e: dict = data.get("gdp_time_series", {})
        cons_ts: dict = data.get("consumption_time_series", {})
        if len(cons_ts) >= 3 and len(gdp_ts_e) >= 3:
            cons_rates = self._growth_rates_from_ts(cons_ts, extrapolate_avg=False)
            gdp_rates_hist = self._growth_rates_from_ts(gdp_ts_e, extrapolate_avg=False)
            common = sorted(set(cons_rates) & set(gdp_rates_hist))
            elast_samples: list[float] = []
            for yr in common:
                gdp_g = gdp_rates_hist[yr]
                con_g = cons_rates[yr]
                if abs(gdp_g) > 0.005:
                    e = con_g / gdp_g
                    if 0.1 <= e <= 3.0:
                        elast_samples.append(e)
            if len(elast_samples) >= 2:
                elast_samples.sort()
                mid = len(elast_samples) // 2
                elasticity = (
                    elast_samples[mid]
                    if len(elast_samples) % 2
                    else (elast_samples[mid - 1] + elast_samples[mid]) / 2
                )
                elas_all = {
                    year: elasticity
                    for year in range(self._base_year, self._base_year + self._sim_years)
                }
                self._set_col(self._COL_ELAS, elas_all)
                sources["elas"] = "World Bank (hist.)"

        # ── Electrification (from WB historical electricity access) ───────────
        access_ts: dict = data.get("electricity_access_time_series", {})
        access_rates = self._growth_rates_from_ts(
            access_ts, base_year=self._base_year, sim_years=self._sim_years,
        )
        if access_rates:
            self._set_col(self._COL_ELEC, access_rates)
            sources["elec"] = "World Bank (hist.)"

        # ── Status message ────────────────────────────────────────────────────
        col_labels = {
            "gdp":  tr("wizard_demest.col_gdp_growth"),
            "pop":  tr("wizard_demest.col_pop_growth"),
            "elas": tr("wizard_demest.col_elasticity"),
            "elec": tr("wizard_demest.col_electrif"),
        }
        lines = []
        for key, label in col_labels.items():
            src = sources.get(key)
            if src:
                lines.append(f"{label}: {src}")
            else:
                lines.append(f"{label}: {tr('wizard_demest.kept_defaults')}")
        self._fetch_status.setText("\n".join(lines))

    # ── Data filling helpers ─────────────────────────────────────────────────

    @staticmethod
    def _growth_rates_from_ts(
        ts: dict, extrapolate_avg: bool = True,
        base_year: int = 0, sim_years: int = 0,
    ) -> dict[int, float]:
        """Compute year-on-year growth rates from a {year: value} time series.

        Returns {year: rate} where rate = (value[year]/value[year-1]) - 1.
        If *extrapolate_avg*, fills simulation years beyond historical data
        with the historical average growth rate.
        """
        if len(ts) < 2:
            return {}
        sorted_years = sorted(ts.keys())
        rates: dict[int, float] = {}
        for i in range(1, len(sorted_years)):
            prev_v = ts.get(sorted_years[i - 1])
            curr_v = ts.get(sorted_years[i])
            if prev_v and curr_v and prev_v > 0:
                rates[int(sorted_years[i])] = (curr_v / prev_v) - 1.0
        if extrapolate_avg and rates and base_year and sim_years:
            avg = sum(rates.values()) / len(rates)
            for year in range(base_year, base_year + sim_years):
                if year not in rates:
                    rates[year] = avg
        return rates


# =====================================================================
# Step 4: Meteorological & Climate Data
# =====================================================================


class MeteoClimateStep(QWidget):
    """Fetch ERA5 temperature data and compute HDD/CDD."""

    fetchFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._meteo_data: dict = {}
        self._fetcher = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("wizard_demest.step4_instruction")))

        # ── Location & year ──
        loc_group = QGroupBox(tr("wizard_demest.meteo_location"))
        loc_lay = QFormLayout(loc_group)
        loc_lay.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._spin_lat = QDoubleSpinBox()
        self._spin_lat.setRange(-90, 90)
        self._spin_lat.setDecimals(4)
        self._spin_lat.setMaximumWidth(140)
        loc_lay.addRow(tr("wizard_demest.latitude"), self._spin_lat)

        self._spin_lon = QDoubleSpinBox()
        self._spin_lon.setRange(-180, 180)
        self._spin_lon.setDecimals(4)
        self._spin_lon.setMaximumWidth(140)
        loc_lay.addRow(tr("wizard_demest.longitude"), self._spin_lon)

        self._spin_year = QSpinBox()
        self._spin_year.setRange(1980, 2025)
        self._spin_year.setValue(2022)
        self._spin_year.setMaximumWidth(100)
        loc_lay.addRow(tr("wizard_demest.weather_year"), self._spin_year)

        layout.addWidget(loc_group)

        # ── Temperature thresholds ──
        thresh_group = QGroupBox(tr("wizard_demest.temperature_thresholds"))
        thresh_lay = QFormLayout(thresh_group)
        thresh_lay.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._spin_hdd_base = QDoubleSpinBox()
        self._spin_hdd_base.setRange(0, 30)
        self._spin_hdd_base.setValue(18.0)
        self._spin_hdd_base.setSuffix(" °C")
        self._spin_hdd_base.setMaximumWidth(100)
        thresh_lay.addRow(tr("wizard_demest.hdd_base"), self._spin_hdd_base)

        self._spin_cdd_base = QDoubleSpinBox()
        self._spin_cdd_base.setRange(15, 40)
        self._spin_cdd_base.setValue(24.0)
        self._spin_cdd_base.setSuffix(" °C")
        self._spin_cdd_base.setMaximumWidth(100)
        thresh_lay.addRow(tr("wizard_demest.cdd_base"), self._spin_cdd_base)

        self._spin_heat_sens = QDoubleSpinBox()
        self._spin_heat_sens.setRange(0, 1)
        self._spin_heat_sens.setSingleStep(0.01)
        self._spin_heat_sens.setDecimals(3)
        self._spin_heat_sens.setValue(0.000)
        self._spin_heat_sens.setSuffix(" MW/°h")
        self._spin_heat_sens.setMaximumWidth(120)
        thresh_lay.addRow(tr("wizard_demest.heating_sensitivity"), self._spin_heat_sens)

        self._spin_cool_sens = QDoubleSpinBox()
        self._spin_cool_sens.setRange(0, 1)
        self._spin_cool_sens.setSingleStep(0.01)
        self._spin_cool_sens.setDecimals(3)
        self._spin_cool_sens.setValue(0.020)
        self._spin_cool_sens.setSuffix(" MW/°h")
        self._spin_cool_sens.setMaximumWidth(120)
        thresh_lay.addRow(tr("wizard_demest.cooling_sensitivity"), self._spin_cool_sens)

        layout.addWidget(thresh_group)

        # ── Reference profile ──
        profile_group = QGroupBox(tr("wizard_demest.reference_profile"))
        profile_vlay = QVBoxLayout(profile_group)

        combo_row = QHBoxLayout()
        combo_row.addWidget(QLabel(tr("wizard_demest.profile_type")))
        self._combo_profile = QComboBox()
        self._combo_profile.addItem(
            tr("wizard_demest.profile_tropical"), "tropical_island"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_temperate"), "temperate_urban"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_arid"), "arid_industrial"
        )
        self._combo_profile.addItem(
            tr("wizard_demest.profile_flat"), "flat_baseload"
        )
        combo_row.addWidget(self._combo_profile)
        combo_row.addStretch()
        profile_vlay.addLayout(combo_row)

        note_lbl = QLabel(tr("wizard_demest.profile_synthetic_note"))
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet("color: #aaa; font-style: italic; font-size: 10px;")
        profile_vlay.addWidget(note_lbl)

        if _MPL_OK:
            self._profile_fig = _Figure(figsize=(6, 2.2), facecolor=current_theme().colors.surface_primary)
            self._profile_canvas = _FigCanvas(self._profile_fig)
            self._profile_canvas.setMinimumHeight(160)
            profile_vlay.addWidget(self._profile_canvas)
        else:
            self._profile_fig = None
            self._profile_canvas = None

        self._combo_profile.currentIndexChanged.connect(self._update_profile_chart)
        layout.addWidget(profile_group)
        self._update_profile_chart()

        # ── Fetch ──
        fetch_row = QHBoxLayout()
        self._btn_fetch = QPushButton(tr("wizard_demest.fetch_meteo"))
        self._btn_fetch.clicked.connect(self._fetch_meteo)
        fetch_row.addWidget(self._btn_fetch)
        self._fetch_status = QLabel("")
        fetch_row.addWidget(self._fetch_status, 1)
        layout.addLayout(fetch_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setMaximumHeight(100)
        self._summary_text.setVisible(False)
        layout.addWidget(self._summary_text)

        layout.addStretch()
        outer.addWidget(_scrolled(inner))

    def set_location(self, lat: float, lon: float) -> None:
        self._spin_lat.setValue(lat)
        self._spin_lon.setValue(lon)

    def _update_profile_chart(self, _index: int = 0) -> None:
        if not _MPL_OK or self._profile_fig is None:
            return
        from esfex.visualization.workflows.demand_estimation_analysis import (
            _get_profile,
        )
        import numpy as np

        profile_type = self._combo_profile.currentData()
        try:
            series = _get_profile(profile_type)  # shape (8760,)
        except Exception:
            return

        # Compute 24-hour average daily pattern
        hours_in_series = len(series)
        complete_days = hours_in_series // 24
        daily = series[: complete_days * 24].reshape(complete_days, 24)
        avg_day = daily.mean(axis=0)
        peak = avg_day.max() if avg_day.max() > 0 else 1.0
        avg_day_norm = avg_day / peak  # normalise to [0,1]

        self._profile_fig.clear()
        ax = self._profile_fig.add_subplot(111, facecolor=current_theme().colors.surface_primary)
        hours = np.arange(24)
        ax.fill_between(hours, avg_day_norm, alpha=0.35, color=current_theme().colors.accent_primary)
        ax.plot(hours, avg_day_norm, color=current_theme().colors.accent_primary, linewidth=1.5)
        ax.set_xlim(0, 23)
        ax.set_ylim(0, 1.05)
        _tc = current_theme().colors
        ax.set_xlabel("Hour of day", color=_tc.text_secondary, fontsize=8)
        ax.set_ylabel("Norm. demand", color=_tc.text_secondary, fontsize=8)
        ax.tick_params(colors=_tc.text_secondary, labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor(_tc.border_light)
        self._profile_fig.tight_layout(pad=0.4)
        self._profile_canvas.draw()

    def _fetch_meteo(self):
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._btn_fetch.setEnabled(False)
        self._fetch_status.setText(tr("wizard_demest.fetching_meteo"))

        from esfex.visualization.workflows.demand_estimation_fetchers import (
            ERA5TemperatureFetcher,
        )
        self._fetcher = ERA5TemperatureFetcher(
            lat=self._spin_lat.value(),
            lon=self._spin_lon.value(),
            year=self._spin_year.value(),
            hdd_base=self._spin_hdd_base.value(),
            cdd_base=self._spin_cdd_base.value(),
            parent=self,
        )
        self._fetcher.progress.connect(lambda p, _: self._progress.setValue(p))
        self._fetcher.finished.connect(self._on_meteo_done)
        self._fetcher.error.connect(self._on_meteo_error)
        self._fetcher.start()

    def _on_meteo_done(self, data: dict):
        self._meteo_data = data
        self._btn_fetch.setEnabled(True)
        self._progress.setValue(100)
        self._fetch_status.setText(tr("wizard_demest.meteo_fetched"))

        temps = data.get("temperature_2m", [])
        if temps:
            t = np.array(temps)
            lines = [
                f"Temperature: min={t.min():.1f}°C  max={t.max():.1f}°C  mean={t.mean():.1f}°C",
                f"HDD total: {data.get('hdd_total', 0):.0f} °C·h",
                f"CDD total: {data.get('cdd_total', 0):.0f} °C·h",
            ]
            self._summary_text.setPlainText("\n".join(lines))
            self._summary_text.setVisible(True)

        self.fetchFinished.emit()

    def _on_meteo_error(self, msg: str):
        self._btn_fetch.setEnabled(True)
        self._fetch_status.setText(f"Error: {msg}")

    def cancel_all(self):
        if self._fetcher and hasattr(self._fetcher, "cancel"):
            self._fetcher.cancel()

    # ── Public API ───────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        return True

    def get_meteo_data(self) -> dict:
        d = dict(self._meteo_data)
        d["hdd_base_temp"] = self._spin_hdd_base.value()
        d["cdd_base_temp"] = self._spin_cdd_base.value()
        d["heating_sensitivity"] = self._spin_heat_sens.value()
        d["cooling_sensitivity"] = self._spin_cool_sens.value()
        d["reference_profile"] = self._combo_profile.currentData()
        d["weather_year"] = self._spin_year.value()
        d["lat"] = self._spin_lat.value()
        d["lon"] = self._spin_lon.value()
        return d


# =====================================================================
# Step 5: Build Profiles (background computation)
# =====================================================================


class BuildProfilesStep(QWidget):
    """Assemble inputs and run the demand profile builder."""

    buildFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._result = None
        self._nodes: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        layout.addWidget(QLabel(tr("wizard_demest.step5_instruction")))

        self._input_summary = QTextEdit()
        self._input_summary.setReadOnly(True)
        self._input_summary.setMaximumHeight(150)
        layout.addWidget(self._input_summary)

        btn_row = QHBoxLayout()
        self._btn_build = QPushButton(tr("wizard_demest.build_profiles"))
        self._btn_build.clicked.connect(self._run_build)
        btn_row.addWidget(self._btn_build)
        self._build_status = QLabel("")
        btn_row.addWidget(self._build_status, 1)
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._results_text = QTextEdit()
        self._results_text.setReadOnly(True)
        self._results_text.setVisible(False)
        layout.addWidget(self._results_text)

        layout.addStretch()
        outer.addWidget(_scrolled(inner))

    def set_inputs(
        self,
        nodes, mode, base_year, sim_years, resolution,
        proxy_results, proxy_weights, macro_data, meteo_data,
        national_demand_gwh=0.0,
    ):
        self._nodes = nodes
        self._mode = mode
        self._base_year = base_year
        self._sim_years = sim_years
        self._resolution = resolution
        self._national_demand_gwh = national_demand_gwh
        self._proxy_results = proxy_results
        self._proxy_weights = proxy_weights
        self._macro_data = macro_data
        self._meteo_data = meteo_data

        # Human-readable resolution label
        if resolution < 1.0:
            res_label = f"{int(round(resolution * 60))} min"
        elif resolution == 1.0:
            res_label = "1 hour"
        else:
            res_label = f"{int(resolution)} hours"

        systems = len({n["system_name"] for n in nodes})
        lines = [
            f"Mode: {mode}",
            f"Nodes: {len(nodes)} across {systems} system(s)",
            f"Base year: {base_year}  ·  Simulation: {sim_years} yr",
            f"Resolution: {res_label}",
        ]
        if national_demand_gwh > 0:
            lines.append(f"National demand: {national_demand_gwh:.0f} GWh (user override)")
        elif macro_data.get("electric_consumption_kwh_capita") and macro_data.get("population"):
            kwh = macro_data["electric_consumption_kwh_capita"]
            pop = macro_data["population"]
            est = kwh * pop / 1e6
            lines.append(f"National demand: ~{est:.0f} GWh (from {kwh:.0f} kWh/cap × {pop:.0f})")
        else:
            lines.append("National demand: NO DATA — build will fail!")
        if macro_data.get("country_name"):
            lines.append(f"Country: {macro_data['country_name']}")
        if macro_data.get("gdp_per_capita"):
            lines.append(f"GDP/capita: ${macro_data['gdp_per_capita']:.0f}")
        if meteo_data.get("reference_profile"):
            lines.append(f"Profile: {meteo_data['reference_profile']}")
        self._input_summary.setPlainText("\n".join(lines))

    def _run_build(self):
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._btn_build.setEnabled(False)
        self._build_status.setText(tr("wizard_demest.building_profiles"))

        from esfex.visualization.workflows.demand_estimation_analysis import (
            DemandEstimationConfig,
            DemandEstimationWorker,
            MacroData,
            MeteoData,
            ProxyData,
        )

        weight_method = self._proxy_weights.get("method", "manual")
        config = DemandEstimationConfig(
            mode=self._mode,
            base_year=self._base_year,
            simulation_years=self._sim_years,
            num_nodes=len(self._nodes),
            resolution_hours=self._resolution,
            national_demand_gwh=getattr(self, "_national_demand_gwh", 0.0),
            weight_method=weight_method,
            weight_buildings=self._proxy_weights.get("buildings", 0.35),
            weight_population=self._proxy_weights.get("population", 0.30),
            weight_nightlights=self._proxy_weights.get("nightlights", 0.20),
            weight_landuse=self._proxy_weights.get("landuse", 0.15),
            reference_profile=self._meteo_data.get("reference_profile", "tropical_island"),
            hdd_base_temp=self._meteo_data.get("hdd_base_temp", 18.0),
            cdd_base_temp=self._meteo_data.get("cdd_base_temp", 24.0),
            heating_sensitivity=self._meteo_data.get("heating_sensitivity", 0.0),
            cooling_sensitivity=self._meteo_data.get("cooling_sensitivity", 0.02),
            gdp_growth_rate=self._macro_data.get("gdp_growth_rate", 0.03),
            demand_gdp_elasticity=self._macro_data.get("demand_gdp_elasticity", 0.8),
            efficiency_improvement=self._macro_data.get("efficiency_improvement", 0.005),
            electrification_growth=self._macro_data.get("electrification_growth", 0.01),
        )

        proxy = ProxyData(
            node_lats=[n["lat"] for n in self._nodes],
            node_lons=[n["lon"] for n in self._nodes],
            node_names=[n["name"] for n in self._nodes],
        )
        buildings_gdf = self._proxy_results.get("buildings")
        if buildings_gdf is not None and hasattr(buildings_gdf, "__len__"):
            proxy.building_weights = self._compute_building_weights(buildings_gdf)

        lu = self._proxy_results.get("landuse", {})
        if lu:
            n = len(self._nodes)
            proxy.landuse_weights = [1.0 / n] * n if n else []
            config.residential_fraction = lu.get("residential_fraction", 0.40)
            config.commercial_fraction = lu.get("commercial_fraction", 0.35)
            config.industrial_fraction = lu.get("industrial_fraction", 0.25)

        _pop = self._macro_data.get("population")
        _kwh = self._macro_data.get("electric_consumption_kwh_capita")
        _gdp = self._macro_data.get("gdp_per_capita")
        _acc = self._macro_data.get("electricity_access")
        logger.warning(
            "=== _run_build MACRO DEBUG ===\n"
            "  macro_data keys: %s\n"
            "  population: %r (type %s)\n"
            "  electric_consumption_kwh_capita: %r (type %s)\n"
            "  gdp_per_capita: %r (type %s)\n"
            "  electricity_access: %r (type %s)\n"
            "  national_demand_gwh: %r\n"
            "  num_nodes: %d",
            sorted(self._macro_data.keys()),
            _pop, type(_pop).__name__,
            _kwh, type(_kwh).__name__,
            _gdp, type(_gdp).__name__,
            _acc, type(_acc).__name__,
            getattr(self, "_national_demand_gwh", 0.0),
            len(self._nodes),
        )

        macro = MacroData(
            country_iso=self._macro_data.get("country_iso", ""),
            country_name=self._macro_data.get("country_name", ""),
            gdp_per_capita=self._macro_data.get("gdp_per_capita") or 0.0,
            population=self._macro_data.get("population") or 0.0,
            urbanization_pct=self._macro_data.get("urbanization_pct") or 50.0,
            electricity_access_pct=self._macro_data.get("electricity_access") or 100.0,
            electric_consumption_kwh_capita=self._macro_data.get(
                "electric_consumption_kwh_capita"
            ) or 0.0,
            gdp_growth_rate=self._macro_data.get("gdp_growth_rate", 0.03),
            gdp_time_series=self._macro_data.get("gdp_time_series", {}),
            consumption_time_series=self._macro_data.get("consumption_time_series", {}),
            # Per-year projection rates from Step 3 table
            gdp_growth_by_year=self._macro_data.get("gdp_growth_by_year", {}),
            pop_growth_by_year=self._macro_data.get("pop_growth_by_year", {}),
            elasticity_by_year=self._macro_data.get("elasticity_by_year", {}),
            efficiency_by_year=self._macro_data.get("efficiency_by_year", {}),
            electrification_by_year=self._macro_data.get("electrification_by_year", {}),
        )

        meteo = MeteoData(
            temperature_hourly=self._meteo_data.get("temperature_2m", []),
            humidity_hourly=self._meteo_data.get("relative_humidity_2m", []),
            hdd_hourly=self._meteo_data.get("hdd", []),
            cdd_hourly=self._meteo_data.get("cdd", []),
            lat=self._meteo_data.get("lat", 0),
            lon=self._meteo_data.get("lon", 0),
            year=self._meteo_data.get("weather_year", 2022),
        )

        self._worker = DemandEstimationWorker(
            config=config, proxy=proxy, macro=macro, meteo=meteo, parent=self
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_build_done)
        self._worker.error.connect(self._on_build_error)
        self._worker.start()

    def _compute_building_weights(self, gdf) -> list[float]:
        n = len(self._nodes)
        if n == 0:
            return []
        if n == 1:
            return [1.0]
        try:
            # Reproject to metric CRS for centroid accuracy
            gdf_proj = gdf.to_crs(epsg=3857) if gdf.crs else gdf
            centroids_4326 = gdf_proj.geometry.centroid.to_crs(epsg=4326)

            # Vectorized: all centroid coords as numpy arrays
            cx = np.asarray(centroids_4326.x, dtype=np.float64)  # lon
            cy = np.asarray(centroids_4326.y, dtype=np.float64)  # lat

            node_lats = np.array([nd["lat"] for nd in self._nodes], dtype=np.float64)
            node_lons = np.array([nd["lon"] for nd in self._nodes], dtype=np.float64)

            # Squared distances: shape (n_buildings, n_nodes)
            dy = cy[:, np.newaxis] - node_lats[np.newaxis, :]
            dx = cx[:, np.newaxis] - node_lons[np.newaxis, :]
            best_node = np.argmin(dy ** 2 + dx ** 2, axis=1)

            # Areas — vectorized column access
            if "footprint_area_m2" in gdf.columns:
                areas = gdf["footprint_area_m2"].fillna(100.0).to_numpy(dtype=np.float64)
                areas = np.where(areas <= 0, 100.0, areas)
            else:
                areas = np.full(len(gdf), 100.0, dtype=np.float64)

            weights_arr = np.zeros(n, dtype=np.float64)
            np.add.at(weights_arr, best_node, areas)

            total = weights_arr.sum()
            if total > 0:
                return (weights_arr / total).tolist()
            return [1.0 / n] * n
        except Exception:
            logger.exception("Building weight computation failed")
            return [1.0 / n] * n

    def _on_progress(self, pct, msg):
        self._progress.setValue(pct)
        self._build_status.setText(msg)

    def _on_build_done(self, result):
        self._result = result
        self._btn_build.setEnabled(True)
        self._progress.setValue(100)
        self._build_status.setText(tr("wizard_demest.build_complete"))

        logger.warning(
            "=== BUILD RESULT DEBUG ===\n"
            "  demand.shape: %s, demand.dtype: %s\n"
            "  demand.min: %.4f, demand.max: %.4f, demand.mean: %.4f\n"
            "  total_peak_mw: %.2f\n"
            "  total_annual_gwh: %.2f\n"
            "  demand_source: %s\n"
            "  demand_multi_year shape: %s",
            result.demand.shape, result.demand.dtype,
            result.demand.min(), result.demand.max(), result.demand.mean(),
            result.total_peak_mw,
            result.total_annual_gwh,
            getattr(result, "demand_source", "?"),
            result.demand_multi_year.shape if result.demand_multi_year is not None else None,
        )

        lines = []

        # Show warnings prominently if demand data source is unreliable
        warnings = getattr(result, "warnings", [])
        source = getattr(result, "demand_source", "")
        if warnings:
            for w in warnings:
                lines.append(f"⚠ {w}")
            lines.append("")
        if source:
            lines.append(f"Demand source: {source}")

        lines += [
            f"Total peak:   {result.total_peak_mw:.2f} MW",
            f"Annual energy:{result.total_annual_gwh:.2f} GWh",
            f"Load factor:  {result.total_load_factor:.3f}",
            "",
        ]
        for i, nd in enumerate(self._nodes):
            if i < len(result.peak_mw):
                lines.append(
                    f"  {nd['name']}: {result.peak_mw[i]:.2f} MW  "
                    f"{result.annual_gwh[i]:.2f} GWh"
                )
        self._results_text.setPlainText("\n".join(lines))
        self._results_text.setVisible(True)
        self.buildFinished.emit()

    def _on_build_error(self, msg):
        self._btn_build.setEnabled(True)
        self._build_status.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Build Error", msg)

    def cancel_all(self):
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()

    def is_valid(self) -> bool:
        return self._result is not None

    def get_result(self):
        return self._result


# =====================================================================
# Step 6: Calibration & Export
# =====================================================================


class CalibrationStep(QWidget):
    """Validate, calibrate, and export estimated demand."""

    def __init__(self, all_states: dict | None = None, parent=None):
        super().__init__(parent)
        self._all_states = all_states or {}
        self._result = None
        self._calibrated_result = None
        self._nodes: list[dict] = []
        self._base_year = 2025
        self._sim_years = 25
        self._exported_paths: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)

        # ── Calibration + Metrics (two columns) ──
        top_row = QHBoxLayout()

        # Left: calibration controls
        cal_col = QVBoxLayout()
        known_group = QGroupBox(tr("wizard_demest.known_data"))
        known_lay = QFormLayout(known_group)
        known_lay.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self._spin_known_peak = QDoubleSpinBox()
        self._spin_known_peak.setRange(0, 100_000)
        self._spin_known_peak.setDecimals(1)
        self._spin_known_peak.setSuffix(" MW")
        self._spin_known_peak.setMaximumWidth(160)
        known_lay.addRow(tr("wizard_demest.known_peak"), self._spin_known_peak)

        self._spin_known_annual = QDoubleSpinBox()
        self._spin_known_annual.setRange(0, 1_000_000)
        self._spin_known_annual.setDecimals(1)
        self._spin_known_annual.setSuffix(" GWh")
        self._spin_known_annual.setMaximumWidth(160)
        known_lay.addRow(tr("wizard_demest.known_annual"), self._spin_known_annual)

        btn_cal_row = QHBoxLayout()
        self._btn_cal = QPushButton(tr("wizard_demest.apply_calibration"))
        self._btn_cal.clicked.connect(self._apply_calibration)
        btn_cal_row.addWidget(self._btn_cal)
        self._cal_status = QLabel("")
        self._cal_status.setStyleSheet("color: #aaa; font-size: 10px;")
        btn_cal_row.addWidget(self._cal_status, 1)
        known_lay.addRow("", btn_cal_row)

        cal_col.addWidget(known_group)
        cal_col.addStretch()

        # Right: metrics summary
        metrics_col = QVBoxLayout()
        self._metrics_text = QTextEdit()
        self._metrics_text.setReadOnly(True)
        metrics_col.addWidget(self._metrics_text)

        top_row.addLayout(cal_col, 1)
        top_row.addLayout(metrics_col, 1)
        layout.addLayout(top_row)

        # ── Temporal resolution slider ──
        self._agg_levels = [
            ("Multi-year", 8760),
            ("Monthly", 730),
            ("Bi-weekly", 336),
            ("Weekly", 168),
            ("3-day", 72),
            ("Daily", 24),
            ("6 h", 6),
            ("3 h", 3),
            ("Hourly", 1),
        ]

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.addWidget(QLabel("Multi-year"))
        self._slider_agg = QSlider(Qt.Orientation.Horizontal)
        self._slider_agg.setRange(0, len(self._agg_levels) - 1)
        self._slider_agg.setValue(0)
        self._slider_agg.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider_agg.setTickInterval(1)
        self._slider_agg.setMaximumHeight(22)
        self._slider_agg.valueChanged.connect(self._on_agg_changed)
        slider_row.addWidget(self._slider_agg, 1)
        slider_row.addWidget(QLabel("Hourly"))
        layout.addLayout(slider_row)

        # ── Interactive chart ──
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self._chart_web = QWebEngineView()
            layout.addWidget(self._chart_web, 1)
        except ImportError:
            self._chart_web = None
            layout.addWidget(QLabel(
                "Install PySide6-WebEngine and plotly for interactive charts."
            ), 1)

        outer.addWidget(_scrolled(inner))

    def set_result(self, result, nodes=None, base_year=None, sim_years=None):
        self._result = result
        self._calibrated_result = result  # default: pass unchanged
        if nodes is not None:
            self._nodes = nodes
        if base_year is not None:
            self._base_year = base_year
        if sim_years is not None:
            self._sim_years = sim_years
        # Pre-populate spinboxes with computed values
        self._spin_known_peak.setValue(result.total_peak_mw)
        self._spin_known_annual.setValue(result.total_annual_gwh)
        self._show_metrics(result)
        self._update_chart(result)
        self._cal_status.setText("")

    def _show_metrics(self, result):
        if not result:
            return
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lines = [
            f"Peak:         {result.total_peak_mw:.2f} MW",
            f"Annual:       {result.total_annual_gwh:.2f} GWh",
            f"Load factor:  {result.total_load_factor:.3f}",
            "",
            "Monthly GWh:",
        ]
        for i, m in enumerate(months):
            if i < len(result.monthly_gwh):
                lines.append(f"  {m}: {result.monthly_gwh[i]:.2f}")
        lines += ["", "Per node:"]
        for i in range(len(result.peak_mw)):
            nd_name = self._nodes[i]["name"] if i < len(self._nodes) else f"node_{i}"
            lines.append(
                f"  {nd_name}: {result.peak_mw[i]:.2f} MW  "
                f"{result.annual_gwh[i]:.2f} GWh  "
                f"LF={result.load_factor[i]:.3f}"
            )
        self._metrics_text.setPlainText("\n".join(lines))

    def _apply_calibration(self):
        if not self._result:
            return
        import copy

        known_annual = self._spin_known_annual.value()
        known_peak = self._spin_known_peak.value()

        # Compute scale factor from user-edited values vs original
        orig_annual = self._result.total_annual_gwh
        orig_peak = self._result.total_peak_mw

        # Determine scale factor: annual energy takes priority
        if known_annual > 0 and orig_annual > 0 and abs(known_annual - orig_annual) > 0.1:
            scale = known_annual / orig_annual
        elif known_peak > 0 and orig_peak > 0 and abs(known_peak - orig_peak) > 0.1:
            scale = known_peak / orig_peak
        else:
            # No change
            self._cal_status.setText("No changes to apply.")
            return

        result = copy.deepcopy(self._result)

        # Scale base year demand
        demand = np.array(result.demand, dtype=np.float64) * scale
        result.demand = demand

        # Scale multi-year demand
        if result.demand_multi_year is not None:
            result.demand_multi_year = (
                np.array(result.demand_multi_year, dtype=np.float64) * scale
            )

        # Recompute metrics from scaled demand
        if demand.ndim > 1:
            total = demand.sum(axis=1)
        else:
            total = demand
        result.total_peak_mw = float(total.max())
        result.total_annual_gwh = float(total.sum() / 1000.0)
        avg = float(total.mean())
        result.total_load_factor = avg / result.total_peak_mw if result.total_peak_mw > 0 else 0

        n = demand.shape[1] if demand.ndim > 1 else 1
        result.peak_mw, result.annual_gwh, result.load_factor = [], [], []
        for ni in range(n):
            col = demand[:, ni] if demand.ndim > 1 else demand
            pk = float(col.max())
            an = float(col.sum() / 1000.0)
            result.peak_mw.append(pk)
            result.annual_gwh.append(an)
            result.load_factor.append(float(col.mean() / pk) if pk > 0 else 0)

        res_h = getattr(result.config, "resolution_hours", 1.0)
        mh_1h = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]
        mh = [max(1, int(round(m / res_h))) for m in mh_1h]
        result.monthly_gwh, h = [], 0
        for m in mh:
            result.monthly_gwh.append(
                float(total[h : h + m].sum() * res_h / 1000.0)
            )
            h += m

        self._calibrated_result = result
        self._spin_known_peak.setValue(result.total_peak_mw)
        self._spin_known_annual.setValue(result.total_annual_gwh)
        self._show_metrics(result)
        self._update_chart(result)
        self._cal_status.setText(
            f"Scaled ×{scale:.3f} — {tr('wizard_demest.calibration_applied')}"
        )

    def _on_agg_changed(self, idx: int) -> None:
        self._update_chart(self._calibrated_result)

    def _update_chart(self, result) -> None:
        if result is None or self._chart_web is None:
            return

        import plotly.graph_objects as go

        # Use multi-year data (all simulation years), per node
        data_my = result.demand_multi_year
        if data_my is None:
            data_my = result.demand
        demand = np.array(data_my, dtype=np.float64)
        if demand.ndim == 1:
            demand = demand[:, np.newaxis]
        n_steps, n_nodes = demand.shape

        sim_years = getattr(result.config, "simulation_years", 1)
        base_year = getattr(result.config, "base_year", 2025)
        res_h = getattr(result.config, "resolution_hours", 1.0)
        hours_per_year = n_steps // max(sim_years, 1)

        node_names = [
            self._nodes[i]["name"] if i < len(self._nodes) else f"Node {i}"
            for i in range(n_nodes)
        ]

        # Aggregate to selected temporal resolution
        agg_idx = self._slider_agg.value()
        agg_label, block_h = self._agg_levels[agg_idx]
        steps_per_block = max(1, int(round(block_h / res_h)))

        if steps_per_block > 1 and steps_per_block <= n_steps:
            n_blocks = n_steps // steps_per_block
            trimmed = demand[: n_blocks * steps_per_block, :]
            agg = trimmed.reshape(n_blocks, steps_per_block, n_nodes).mean(axis=1)
            x_hours = (np.arange(n_blocks) + 0.5) * block_h
        else:
            agg = demand
            x_hours = np.arange(n_steps) * res_h

        # Build x-axis as fractional years (e.g. 2025.0, 2025.5, 2026.0 ...)
        x_years = (base_year + x_hours / (hours_per_year * res_h)).tolist()

        # Theme colors
        c = current_theme().colors
        _PALETTE = [
            "#3498DB", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6",
            "#1ABC9C", "#E67E22", "#34495E", "#16A085", "#C0392B",
            "#2980B9", "#8E44AD", "#27AE60", "#D35400", "#7F8C8D",
        ]

        fig = go.Figure()
        n_pts = len(x_years)
        use_bars = n_pts <= 60

        for ni in range(n_nodes):
            color = _PALETTE[ni % len(_PALETTE)]
            y_vals = agg[:, ni].tolist()
            if use_bars:
                fig.add_trace(go.Bar(
                    x=x_years, y=y_vals, name=node_names[ni],
                    marker_color=color, opacity=0.85,
                    hovertemplate="%{x:.1f}: %{y:.0f} MW<extra>"
                    + node_names[ni] + "</extra>",
                ))
            else:
                fig.add_trace(go.Scatter(
                    x=x_years, y=y_vals, name=node_names[ni],
                    mode="lines", stackgroup="demand",
                    line=dict(width=0.5, color=color),
                    fillcolor=color,
                    hovertemplate="%{y:.0f} MW<extra>"
                    + node_names[ni] + "</extra>",
                ))

        total_agg = agg.sum(axis=1)
        pk = float(total_agg.max())
        mn = float(total_agg.mean())
        lf = mn / pk if pk > 0 else 0
        annual_gwh = float(demand.sum()) * res_h / (1000.0 * sim_years)

        # X-axis tick values at year boundaries
        tick_vals = list(range(base_year, base_year + sim_years + 1))
        tick_text = [str(y) for y in tick_vals]

        fig.update_layout(
            barmode="stack" if use_bars else None,
            title=dict(
                text=(
                    f"{annual_gwh:.0f} GWh/yr · "
                    f"Peak {pk:.0f} MW · Mean {mn:.0f} MW · LF {lf:.2f} · "
                    f"[{agg_label}]"
                ),
                font=dict(size=11, color=c.text_primary),
                y=0.98, x=0.01, xanchor="left", yanchor="top",
            ),
            xaxis=dict(
                tickvals=tick_vals, ticktext=tick_text,
                color=c.text_secondary, gridcolor=c.border_light,
                zeroline=False,
            ),
            yaxis=dict(
                title="MW", color=c.text_secondary,
                gridcolor=c.border_light, zeroline=False,
                rangemode="tozero",
            ),
            plot_bgcolor=c.surface_primary,
            paper_bgcolor=c.surface_primary,
            font=dict(color=c.text_primary, size=10),
            legend=dict(
                orientation="h", yanchor="top", y=-0.08,
                xanchor="center", x=0.5,
                font=dict(size=9),
            ),
            margin=dict(l=50, r=15, t=30, b=60),
            hovermode="x unified",
        )

        self._chart_web.setHtml(fig.to_html(
            include_plotlyjs="cdn", full_html=True,
            config={"displayModeBar": True, "scrollZoom": True},
        ))

    def export_csv(self):
        """Export demand as separate CSV files per node."""
        result = self._calibrated_result or self._result
        if not result:
            return

        dir_path = QFileDialog.getExistingDirectory(
            self, tr("wizard_demest.select_export_dir")
        )
        if not dir_path:
            return

        import os

        demand_my = result.demand_multi_year
        demand = result.demand
        data = demand_my if demand_my is not None else demand

        ncols = data.shape[1] if data.ndim > 1 else 1
        self._exported_paths.clear()
        for ni in range(ncols):
            nd_name = self._nodes[ni]["name"] if ni < len(self._nodes) else f"node_{ni}"
            col = data[:, ni] if data.ndim > 1 else data
            path = os.path.join(dir_path, f"demand_{nd_name}.csv")
            np.savetxt(path, col, fmt="%.4f")
            self._exported_paths.append(path)

    def is_valid(self) -> bool:
        return self._calibrated_result is not None

    def get_calibrated_result(self):
        return self._calibrated_result

    def get_exported_paths(self) -> list[str]:
        return self._exported_paths
