"""GUI dialog for generating availability profiles from the editor.

Lazy-imported only when ``gui_mode=True`` to avoid PySide6 dependency
in CLI mode.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def add_availability_menu_item(menu_bar, main_window) -> None:
    """Add 'Generate Availability Profiles...' to the Plugins menu."""
    from PySide6.QtGui import QAction

    from esfex.visualization.i18n import tr

    # Find the Plugins menu (created by main_window._build_menu_bar)
    plugins_menu = getattr(main_window, "_plugins_menu", None)
    if plugins_menu is None:
        # Fallback: add to menu bar directly
        plugins_menu = menu_bar.addMenu("Plugins")

    act = QAction(tr("availability_generator.menu_action"), main_window)
    act.triggered.connect(lambda: _open_dialog(main_window))
    plugins_menu.addAction(act)


def _open_dialog(main_window) -> None:
    """Open the AvailabilityDialog."""
    dlg = AvailabilityDialog(main_window)
    dlg.exec()


class AvailabilityDialog:
    """Dialog for configuring and running availability profile generation.

    Uses lazy imports so the module can be safely imported without PySide6
    at module load time.
    """

    def __new__(cls, parent=None):
        """Create the actual QDialog instance."""
        return _AvailabilityDialogImpl(parent)


class _AvailabilityDialogImpl:
    """Actual implementation, instantiated only when the dialog opens."""

    def __init__(self, parent=None):
        from PySide6.QtCore import Qt, QThread, Signal
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDialog,
            QDoubleSpinBox,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QProgressBar,
            QPushButton,
            QRadioButton,
            QSpinBox,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )

        from esfex.visualization.i18n import tr

        self._tr = tr
        self._parent = parent
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle(tr("availability_generator.dialog_title"))
        self._dlg.setMinimumSize(700, 550)
        self._dlg.setModal(True)

        layout = QVBoxLayout(self._dlg)

        # ── System selector ──
        sys_layout = QHBoxLayout()
        sys_layout.addWidget(QLabel(tr("availability_generator.system")))
        self._sys_combo = QComboBox()
        sys_layout.addWidget(self._sys_combo, 1)
        layout.addLayout(sys_layout)

        # ── Generator table ──
        layout.addWidget(QLabel(tr("availability_generator.generators")))
        self._gen_table = QTableWidget(0, 5)
        self._gen_table.setHorizontalHeaderLabels([
            "", tr("availability_generator.gen_key"),
            tr("availability_generator.fuel"),
            tr("availability_generator.profile_type"),
            tr("availability_generator.node"),
        ])
        header = self._gen_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._gen_table.setMaximumHeight(200)
        layout.addWidget(self._gen_table)

        # ── Data source ──
        ds_group = QGroupBox(tr("availability_generator.data_source"))
        ds_layout = QVBoxLayout(ds_group)
        self._radio_om = QRadioButton("Open-Meteo (ERA5, no API key)")
        self._radio_np = QRadioButton("NASA POWER (MERRA-2, no API key)")
        self._radio_at = QRadioButton("ERA5 via atlite (requires CDS API key)")
        self._radio_om.setChecked(True)
        ds_layout.addWidget(self._radio_om)
        ds_layout.addWidget(self._radio_np)
        ds_layout.addWidget(self._radio_at)
        layout.addWidget(ds_group)

        # ── Years ──
        year_layout = QHBoxLayout()
        year_layout.addWidget(QLabel(tr("availability_generator.years")))
        self._year_from = QSpinBox()
        self._year_from.setRange(1979, 2025)
        self._year_from.setValue(2020)
        year_layout.addWidget(self._year_from)
        year_layout.addWidget(QLabel("to"))
        self._year_to = QSpinBox()
        self._year_to.setRange(1979, 2025)
        self._year_to.setValue(2020)
        year_layout.addWidget(self._year_to)
        year_layout.addStretch()
        layout.addLayout(year_layout)

        # ── Solar parameters ──
        self._solar_group = QGroupBox(tr("availability_generator.solar_params"))
        sf = QFormLayout(self._solar_group)
        self._efficiency = QDoubleSpinBox()
        self._efficiency.setRange(0.05, 0.50)
        self._efficiency.setValue(0.20)
        self._efficiency.setSingleStep(0.01)
        sf.addRow(tr("availability_generator.efficiency"), self._efficiency)

        self._tilt_combo = QComboBox()
        self._tilt_combo.addItems(["Latitude-optimal", "Custom"])
        sf.addRow(tr("availability_generator.tilt"), self._tilt_combo)

        self._tilt_value = QDoubleSpinBox()
        self._tilt_value.setRange(0, 90)
        self._tilt_value.setValue(20)
        self._tilt_value.setEnabled(False)
        sf.addRow("", self._tilt_value)
        self._tilt_combo.currentIndexChanged.connect(
            lambda i: self._tilt_value.setEnabled(i == 1)
        )

        self._azimuth = QDoubleSpinBox()
        self._azimuth.setRange(0, 360)
        self._azimuth.setValue(180)
        sf.addRow(tr("availability_generator.azimuth"), self._azimuth)

        self._tracking = QComboBox()
        self._tracking.addItems(["none", "horizontal", "vertical", "dual"])
        sf.addRow(tr("availability_generator.tracking"), self._tracking)
        layout.addWidget(self._solar_group)

        # ── Wind parameters ──
        self._wind_group = QGroupBox(tr("availability_generator.wind_params"))
        wf = QFormLayout(self._wind_group)

        self._turbine_combo = QComboBox()
        self._turbine_combo.addItem("Vestas_V112_3MW (default)")
        wf.addRow(tr("availability_generator.turbine"), self._turbine_combo)

        self._hub_height = QSpinBox()
        self._hub_height.setRange(30, 300)
        self._hub_height.setValue(80)
        self._hub_height.setSuffix(" m")
        wf.addRow(tr("availability_generator.hub_height"), self._hub_height)
        layout.addWidget(self._wind_group)

        # Lazy-load turbine database
        self._turbines_loaded = False

        # ── Progress ──
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        # ── Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._btn_generate = QPushButton(tr("availability_generator.generate"))
        self._btn_generate.clicked.connect(self._on_generate)
        btn_layout.addWidget(self._btn_generate)

        self._btn_close = QPushButton(tr("availability_generator.close"))
        self._btn_close.clicked.connect(self._dlg.close)
        btn_layout.addWidget(self._btn_close)
        layout.addLayout(btn_layout)

        # Populate
        self._populate_systems()
        self._sys_combo.currentIndexChanged.connect(self._populate_generators)
        if self._sys_combo.count() > 0:
            self._populate_generators(0)

    def exec(self):
        return self._dlg.exec()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate_systems(self):
        states = getattr(self._parent, "_all_states", {})
        for name in sorted(states.keys()):
            self._sys_combo.addItem(name)

    _SOLAR_HINTS = {"solar", "pv", "fotovoltaic", "photovoltaic", "sun"}
    _WIND_HINTS = {"wind", "eolic", "eólic", "turbine", "aerogenerador"}

    def _guess_profile_type(self, fuel: str) -> str:
        """Guess Solar/Wind from fuel name; return '--' if unknown."""
        lower = fuel.lower()
        for hint in self._SOLAR_HINTS:
            if hint in lower:
                return "Solar"
        for hint in self._WIND_HINTS:
            if hint in lower:
                return "Wind"
        return "--"

    def _populate_generators(self, index: int):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QCheckBox, QComboBox, QTableWidgetItem,
            QWidget, QHBoxLayout,
        )

        self._gen_table.setRowCount(0)

        sys_name = self._sys_combo.currentText()
        states = getattr(self._parent, "_all_states", {})
        state = states.get(sys_name)
        if state is None:
            return

        # generators is a dict[str, GuiGeneratorInstance]
        gens_dict = getattr(state, "generators", {})

        row = 0
        for gen_id, gen in gens_dict.items():
            gen_type = getattr(gen, "gen_type", "")
            if gen_type != "Renewable":
                continue

            self._gen_table.setRowCount(row + 1)

            # Checkbox
            chk = QCheckBox()
            chk.setChecked(True)
            chk_w = QWidget()
            chk_l = QHBoxLayout(chk_w)
            chk_l.addWidget(chk)
            chk_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_l.setContentsMargins(0, 0, 0, 0)
            self._gen_table.setCellWidget(row, 0, chk_w)

            # Gen key
            self._gen_table.setItem(row, 1, QTableWidgetItem(gen_id))

            # Fuel (read-only info)
            fuel = getattr(gen, "fuel", "")
            fuel_item = QTableWidgetItem(fuel)
            fuel_item.setFlags(fuel_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._gen_table.setItem(row, 2, fuel_item)

            # Profile type dropdown (Solar / Wind / --)
            combo = QComboBox()
            combo.addItems(["--", "Solar", "Wind"])
            guess = self._guess_profile_type(fuel)
            idx = combo.findText(guess)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(self._on_profile_type_changed)
            self._gen_table.setCellWidget(row, 3, combo)

            # Node
            node_idx = getattr(gen, "node", 0)
            self._gen_table.setItem(row, 4, QTableWidgetItem(str(node_idx)))

            row += 1

        self._update_param_visibility()

    def _on_profile_type_changed(self, _text: str):
        """Update Solar/Wind parameter visibility when user changes a combo."""
        self._update_param_visibility()

    def _update_param_visibility(self):
        """Show Solar/Wind param groups only if at least one gen uses them."""
        has_solar = False
        has_wind = False
        for row in range(self._gen_table.rowCount()):
            combo = self._gen_table.cellWidget(row, 3)
            if combo is None:
                continue
            pt = combo.currentText()
            if pt == "Solar":
                has_solar = True
            elif pt == "Wind":
                has_wind = True

        self._solar_group.setVisible(has_solar)
        self._wind_group.setVisible(has_wind)

        if has_wind and not self._turbines_loaded:
            self._load_turbines()

    def _load_turbines(self):
        """Populate turbine combo from atlite database (lazy)."""
        try:
            from windrex import load_turbine_database

            turbines = load_turbine_database()
            self._turbine_combo.clear()
            for t in turbines:
                label = f"{t.key} ({t.rated_power_mw:.1f} MW)"
                self._turbine_combo.addItem(label, t.key)
            if not turbines:
                self._turbine_combo.addItem("Vestas_V112_3MW (default)")
            self._turbines_loaded = True
        except Exception:
            self._turbine_combo.clear()
            self._turbine_combo.addItem("Vestas_V112_3MW (default)")

    # ------------------------------------------------------------------
    # Output directory resolution
    # ------------------------------------------------------------------

    def _resolve_output_dir(self) -> Path:
        """Determine where to write availability CSVs.

        Uses ``config_path`` parent / ``availability/`` when available,
        otherwise falls back to ``./availability/``.
        """
        config_path = getattr(self._parent, "_config_path", None)
        if config_path:
            return Path(config_path).parent / "availability"
        return Path.cwd() / "availability"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_generate(self):
        """Run the profile generation in a background thread."""
        from PySide6.QtCore import QObject, QThread, Signal
        from PySide6.QtWidgets import QMessageBox

        class Worker(QObject):
            progress = Signal(int, str)
            finished = Signal(dict)
            error = Signal(str)

            def __init__(self, func, kwargs):
                super().__init__()
                self._func = func
                self._kwargs = kwargs

            def run(self):
                try:
                    # Use signal as progress callback (thread-safe)
                    self._kwargs["progress_callback"] = \
                        lambda pct, msg: self.progress.emit(pct, msg)
                    result = self._func(**self._kwargs)
                    self.finished.emit(result)
                except Exception as exc:
                    self.error.emit(str(exc))

        # Gather parameters
        data_source = "open_meteo"
        if self._radio_np.isChecked():
            data_source = "nasa_power"
        elif self._radio_at.isChecked():
            data_source = "era5_atlite"

        years = list(range(self._year_from.value(), self._year_to.value() + 1))

        tilt = None
        if self._tilt_combo.currentIndex() == 1:
            tilt = self._tilt_value.value()

        solar_params = {
            "efficiency": self._efficiency.value(),
            "gamma_pmax": -0.40,
            "t_noct": 45.0,
            "tilt": tilt,
            "azimuth": self._azimuth.value(),
            "tracking": self._tracking.currentText(),
        }

        turbine_key = None
        if self._turbine_combo.currentData():
            turbine_key = self._turbine_combo.currentData()

        wind_params = {
            "turbine_key": turbine_key,
            "hub_height": self._hub_height.value(),
        }

        # Collect profile type selections and generator filter from table
        sys_name = self._sys_combo.currentText()
        profile_type_map: dict[str, str] = {}
        generator_filter: list[str] = []
        for row in range(self._gen_table.rowCount()):
            chk_w = self._gen_table.cellWidget(row, 0)
            chk = chk_w.findChild(type(chk_w.layout().itemAt(0).widget()))
            if chk is None or not chk.isChecked():
                continue
            gen_key = self._gen_table.item(row, 1).text()
            combo = self._gen_table.cellWidget(row, 3)
            pt = combo.currentText() if combo else "--"
            if pt in ("Solar", "Wind"):
                full_key = f"{sys_name}/{gen_key}"
                profile_type_map[full_key] = pt
                generator_filter.append(gen_key)

        if not profile_type_map:
            QMessageBox.warning(
                self._dlg, "Warning",
                "No generators selected with a Solar or Wind profile type.",
            )
            return

        # Build a ESFEXConfig from the current GUI state
        all_states = getattr(self._parent, "_all_states", {})
        if not all_states:
            QMessageBox.warning(
                self._dlg, "Error",
                "No model loaded. Please load a configuration first.",
            )
            return

        try:
            from esfex.visualization.data.serializer import gui_state_to_yaml
            import tempfile
            from esfex.config.loader import load_config

            # Sync current system state before export
            mw = self._parent
            cur_name = getattr(mw, "_current_system_name", None)
            model = getattr(mw, "model", None)
            if cur_name and model and cur_name in all_states:
                all_states[cur_name] = model.state

            # base_config: the loaded Pydantic config (or a default)
            base_config = getattr(mw, "_loaded_config", None)
            if base_config is None:
                create_default = getattr(mw, "_create_default_config", None)
                if create_default:
                    base_config = create_default()
            if base_config is None:
                raise RuntimeError("No base configuration available")

            tmp = Path(tempfile.mktemp(suffix=".yaml"))
            global_settings = getattr(model, "global_settings", None)
            stochastic = getattr(model, "stochastic_scenarios", [])
            inter_links = getattr(model, "inter_system_links", None)
            gui_state_to_yaml(
                all_states, base_config, tmp,
                inter_system_links=inter_links,
                global_settings=global_settings,
                stochastic_scenarios=stochastic,
            )
            cfg = load_config(tmp)
            tmp.unlink(missing_ok=True)
        except Exception as exc:
            QMessageBox.critical(
                self._dlg, "Error",
                f"Failed to export config: {exc}",
            )
            return

        from .generator import generate_availability_profiles

        output_dir = self._resolve_output_dir()

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._btn_generate.setEnabled(False)

        # Store selection info for _on_finished
        self._pending_sys_name = sys_name
        self._pending_gen_keys = generator_filter

        # Run in thread — Worker injects progress_callback via signal
        self._thread = QThread()
        self._worker = Worker(
            generate_availability_profiles,
            dict(
                config=cfg,
                years=years,
                output_dir=output_dir,
                data_source=data_source,
                solar_params=solar_params,
                wind_params=wind_params,
                generator_filter=generator_filter,
                profile_type_map=profile_type_map,
            ),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.start()

    def _on_progress(self, pct: int, msg: str):
        """Slot called on the main thread via Worker.progress signal."""
        self._progress_bar.setValue(pct)
        self._status_label.setText(msg)

    def _on_finished(self, result_map: dict):
        from PySide6.QtWidgets import QMessageBox

        self._btn_generate.setEnabled(True)
        self._progress_bar.setValue(100)
        self._status_label.setText(
            f"Done! Generated {len(result_map)} profile(s)."
        )

        # Update availability_file on the GUI model generators
        self._apply_paths_to_model(result_map)

        msg = "\n".join(
            f"  {k} -> {v}" for k, v in sorted(result_map.items())
        )
        QMessageBox.information(
            self._dlg,
            self._tr("availability_generator.done_title"),
            f"{self._tr('availability_generator.done_message')}\n\n{msg}",
        )

    def _apply_paths_to_model(self, result_map: dict[str, Path]):
        """Set availability_file on each generator in the GUI model."""
        sys_name = getattr(self, "_pending_sys_name", None)
        if not sys_name:
            return

        all_states = getattr(self._parent, "_all_states", {})
        state = all_states.get(sys_name)
        if state is None:
            return

        gens_dict = getattr(state, "generators", {})
        for full_key, csv_path in result_map.items():
            # full_key = "sys_name/gen_key"
            parts = full_key.split("/", 1)
            if len(parts) != 2:
                continue
            _, gen_key = parts

            # Find all generator instances that share this unit_key
            for gen in gens_dict.values():
                if getattr(gen, "instance_id", None) == gen_key:
                    gen.availability_file = str(csv_path)
                    break
                if getattr(gen, "unit_key", None) == gen_key:
                    gen.availability_file = str(csv_path)

    def _on_error(self, error_msg: str):
        from PySide6.QtWidgets import QMessageBox

        self._btn_generate.setEnabled(True)
        self._progress_bar.setVisible(False)
        QMessageBox.critical(self._dlg, "Error", error_msg)
