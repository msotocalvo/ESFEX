"""Preferences dialog with two-column category layout."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.panels.word_wrap_header import WordWrapHeaderView
from esfex.visualization.preferences import DEFAULT_PREFERENCES, DEFAULT_SHORTCUTS


def _action_label(action_id: str) -> str:
    """Return translated display name for an action ID."""
    key = f"preferences_actions.{action_id}"
    result = tr(key)
    return result if result != key else action_id


# ── Category pages ────────────────────────────────────────────────


class _GeneralPage(QWidget):
    """General application preferences."""

    def __init__(self, prefs: dict[str, Any], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        gen = prefs.get("general", {})

        # ── Appearance ──
        appearance = QGroupBox(tr("preferences.group_appearance"))
        form = QFormLayout(appearance)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(
            ["Light", "GitHub Light", "VS Code Dark+", "Dracula", "One Dark Pro"]
        )
        current_theme = gen.get("theme", "Light")
        idx = self.theme_combo.findText(current_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        form.addRow(tr("preferences.theme"), self.theme_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(gen.get("font_size", 12))
        self.font_size_spin.setSuffix(" px")
        form.addRow(tr("preferences.font_size"), self.font_size_spin)

        from esfex.visualization.i18n import get_available_languages

        self.language_combo = QComboBox()
        self._lang_codes: list[str] = []
        current_lang = gen.get("language", "en")
        for code, display_name in get_available_languages().items():
            self._lang_codes.append(code)
            self.language_combo.addItem(display_name)
            if code == current_lang:
                self.language_combo.setCurrentIndex(
                    self.language_combo.count() - 1
                )
        self._initial_lang = current_lang
        form.addRow(tr("preferences.language"), self.language_combo)

        layout.addWidget(appearance)

        # ── Behaviour ──
        behaviour = QGroupBox(tr("preferences.group_behaviour"))
        bform = QFormLayout(behaviour)

        self.auto_validate = QCheckBox(tr("preferences.auto_validate"))
        self.auto_validate.setChecked(gen.get("auto_validate", True))
        bform.addRow(self.auto_validate)

        self.auto_save = QCheckBox(tr("preferences.auto_save"))
        self.auto_save.setChecked(gen.get("auto_save", False))
        bform.addRow(self.auto_save)

        self.auto_save_interval = QSpinBox()
        self.auto_save_interval.setRange(0, 60)
        self.auto_save_interval.setValue(gen.get("auto_save_interval", 0))
        self.auto_save_interval.setSuffix(" min")
        self.auto_save_interval.setSpecialValueText(
            tr("preferences.auto_save_interval_tip")
        )
        bform.addRow(tr("preferences.auto_save_interval"), self.auto_save_interval)

        self.undo_depth = QSpinBox()
        self.undo_depth.setRange(10, 200)
        self.undo_depth.setValue(gen.get("undo_depth", 50))
        bform.addRow(tr("preferences.undo_depth"), self.undo_depth)

        self.auto_open_results = QCheckBox(tr("preferences.auto_open_results"))
        self.auto_open_results.setChecked(gen.get("auto_open_results", False))
        bform.addRow(self.auto_open_results)

        self.debug_mode = QCheckBox(tr("preferences.debug_mode"))
        self.debug_mode.setChecked(gen.get("debug_mode", False))
        bform.addRow(self.debug_mode)

        self.export_dpi = QSpinBox()
        self.export_dpi.setRange(72, 600)
        self.export_dpi.setValue(gen.get("export_dpi", 300))
        self.export_dpi.setSuffix(" dpi")
        bform.addRow(tr("preferences.export_dpi"), self.export_dpi)

        layout.addWidget(behaviour)
        layout.addStretch()

    def language_changed(self) -> bool:
        """Return True if the user picked a different language."""
        idx = self.language_combo.currentIndex()
        return self._lang_codes[idx] != self._initial_lang if idx >= 0 else False

    def collect(self) -> dict[str, Any]:
        idx = self.language_combo.currentIndex()
        lang = self._lang_codes[idx] if idx >= 0 else "en"
        return {
            "theme": self.theme_combo.currentText(),
            "font_size": self.font_size_spin.value(),
            "language": lang,
            "auto_validate": self.auto_validate.isChecked(),
            "auto_save": self.auto_save.isChecked(),
            "auto_save_interval": self.auto_save_interval.value(),
            "undo_depth": self.undo_depth.value(),
            "auto_open_results": self.auto_open_results.isChecked(),
            "debug_mode": self.debug_mode.isChecked(),
            "export_dpi": self.export_dpi.value(),
        }


class _MapPage(QWidget):
    """Map display preferences."""

    def __init__(self, prefs: dict[str, Any], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        m = prefs.get("map", {})

        group = QGroupBox(tr("preferences.group_map_defaults"))
        form = QFormLayout(group)

        self.basemap_combo = QComboBox()
        self.basemap_combo.addItems(["OpenStreetMap", "Satellite", "Terrain", "Dark", "Offline"])
        current = m.get("default_basemap", "OpenStreetMap")
        idx = self.basemap_combo.findText(current)
        if idx >= 0:
            self.basemap_combo.setCurrentIndex(idx)
        form.addRow(tr("preferences.default_basemap"), self.basemap_combo)

        self.default_zoom = QSpinBox()
        self.default_zoom.setRange(1, 18)
        self.default_zoom.setValue(m.get("default_zoom", 7))
        form.addRow(tr("preferences.default_zoom"), self.default_zoom)

        self.default_lat = QDoubleSpinBox()
        self.default_lat.setRange(-90.0, 90.0)
        self.default_lat.setDecimals(4)
        self.default_lat.setValue(m.get("default_lat", 22.0))
        self.default_lat.setSuffix(" °")
        form.addRow(tr("preferences.default_lat"), self.default_lat)

        self.default_lng = QDoubleSpinBox()
        self.default_lng.setRange(-180.0, 180.0)
        self.default_lng.setDecimals(4)
        self.default_lng.setValue(m.get("default_lng", -79.0))
        self.default_lng.setSuffix(" °")
        form.addRow(tr("preferences.default_lng"), self.default_lng)

        self.label_font_size = QSpinBox()
        self.label_font_size.setRange(6, 20)
        self.label_font_size.setValue(m.get("label_font_size", 10))
        self.label_font_size.setSuffix(" pt")
        form.addRow(tr("preferences.label_font_size"), self.label_font_size)

        layout.addWidget(group)
        layout.addStretch()

    def collect(self) -> dict[str, Any]:
        return {
            "default_basemap": self.basemap_combo.currentText(),
            "default_zoom": self.default_zoom.value(),
            "default_lat": self.default_lat.value(),
            "default_lng": self.default_lng.value(),
            "label_font_size": self.label_font_size.value(),
        }


class _SolverSimulationPage(QWidget):
    """Solver defaults and simulation defaults for new projects."""

    def __init__(self, prefs: dict[str, Any], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        s = prefs.get("solver", {})
        sim = prefs.get("simulation", {})

        # ── Solver Defaults ──
        solver_group = QGroupBox(tr("preferences.group_solver_defaults"))
        sform = QFormLayout(solver_group)

        self.solver_combo = QComboBox()
        self.solver_combo.addItems(
            ["HiGHS", "Gurobi", "CPLEX", "SCIP", "Xpress", "CBC", "GLPK"]
        )
        current = s.get("default_solver", "HiGHS")
        idx = self.solver_combo.findText(current, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.solver_combo.setCurrentIndex(idx)
        sform.addRow(tr("preferences.default_solver"), self.solver_combo)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 64)
        self.threads_spin.setValue(s.get("threads", 4))
        sform.addRow(tr("preferences.threads"), self.threads_spin)

        self.time_limit_spin = QSpinBox()
        self.time_limit_spin.setRange(60, 86400)
        self.time_limit_spin.setSingleStep(60)
        self.time_limit_spin.setValue(s.get("time_limit", 3600))
        self.time_limit_spin.setSuffix(" s")
        sform.addRow(tr("preferences.time_limit"), self.time_limit_spin)

        self.mip_gap = QDoubleSpinBox()
        self.mip_gap.setRange(0.0, 1.0)
        self.mip_gap.setDecimals(4)
        self.mip_gap.setSingleStep(0.0001)
        self.mip_gap.setValue(s.get("mip_gap", 0.001))
        sform.addRow(tr("preferences.mip_gap"), self.mip_gap)

        self.verbose = QCheckBox(tr("preferences.verbose_solver"))
        self.verbose.setChecked(s.get("verbose", False))
        sform.addRow(self.verbose)

        self.scale_constraints = QCheckBox(tr("preferences.scale_constraints"))
        self.scale_constraints.setChecked(s.get("scale_constraints", False))
        sform.addRow(self.scale_constraints)

        layout.addWidget(solver_group)

        # ── Simulation Defaults ──
        sim_group = QGroupBox(tr("preferences.group_mode_resolution"))
        mform = QFormLayout(sim_group)

        self.default_mode = QComboBox()
        self.default_mode.addItem(tr("preferences.sim_mode_development"), "development")
        self.default_mode.addItem(tr("preferences.sim_mode_uc"), "unit_commitment")
        current_mode = sim.get("default_mode", "development")
        for i in range(self.default_mode.count()):
            if self.default_mode.itemData(i) == current_mode:
                self.default_mode.setCurrentIndex(i)
                break
        mform.addRow(tr("preferences.sim_default_mode"), self.default_mode)

        self.default_resolution = QSpinBox()
        self.default_resolution.setRange(1, 24)
        self.default_resolution.setValue(sim.get("default_resolution", 6))
        self.default_resolution.setSuffix(" h")
        mform.addRow(tr("preferences.sim_default_resolution"), self.default_resolution)

        self.default_rolling_horizon = QSpinBox()
        self.default_rolling_horizon.setRange(1, 8760)
        self.default_rolling_horizon.setValue(sim.get("default_rolling_horizon", 48))
        self.default_rolling_horizon.setSuffix(" h")
        mform.addRow(tr("preferences.sim_default_rolling"), self.default_rolling_horizon)

        self.default_overlap = QSpinBox()
        self.default_overlap.setRange(0, 720)
        self.default_overlap.setValue(sim.get("default_overlap", 0))
        self.default_overlap.setSuffix(" h")
        mform.addRow(tr("preferences.sim_default_overlap"), self.default_overlap)

        self.default_primary_energy = QCheckBox(tr("preferences.sim_default_pe"))
        self.default_primary_energy.setChecked(
            sim.get("default_primary_energy", False)
        )
        mform.addRow(self.default_primary_energy)

        self.default_log_level = QComboBox()
        self.default_log_level.addItem("Basic", "basic")
        self.default_log_level.addItem("High", "high")
        current_log = sim.get("default_log_level", "basic")
        for i in range(self.default_log_level.count()):
            if self.default_log_level.itemData(i) == current_log:
                self.default_log_level.setCurrentIndex(i)
                break
        mform.addRow(tr("preferences.sim_default_log_level"), self.default_log_level)

        layout.addWidget(sim_group)
        layout.addStretch()

    def collect_solver(self) -> dict[str, Any]:
        return {
            "default_solver": self.solver_combo.currentText(),
            "threads": self.threads_spin.value(),
            "time_limit": self.time_limit_spin.value(),
            "mip_gap": self.mip_gap.value(),
            "verbose": self.verbose.isChecked(),
            "scale_constraints": self.scale_constraints.isChecked(),
        }

    def collect_simulation(self) -> dict[str, Any]:
        return {
            "default_mode": self.default_mode.currentData(),
            "default_resolution": self.default_resolution.value(),
            "default_rolling_horizon": self.default_rolling_horizon.value(),
            "default_overlap": self.default_overlap.value(),
            "default_primary_energy": self.default_primary_energy.isChecked(),
            "default_log_level": self.default_log_level.currentData(),
        }


class _EditorPage(QWidget):
    """Script editor preferences."""

    def __init__(self, prefs: dict[str, Any], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        e = prefs.get("editor", {})

        group = QGroupBox(tr("preferences.group_script_editor"))
        sform = QFormLayout(group)

        self.font_family = QFontComboBox()
        self.font_family.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        current_family = e.get("font_family", "Consolas")
        self.font_family.setCurrentFont(QFont(current_family))
        sform.addRow(tr("preferences.editor_font_family"), self.font_family)

        self.font_size = QSpinBox()
        self.font_size.setRange(6, 24)
        self.font_size.setValue(e.get("font_size", 10))
        self.font_size.setSuffix(" pt")
        sform.addRow(tr("preferences.editor_font_size"), self.font_size)

        self.tab_width = QSpinBox()
        self.tab_width.setRange(2, 8)
        self.tab_width.setValue(e.get("tab_width", 4))
        sform.addRow(tr("preferences.editor_tab_width"), self.tab_width)

        self.show_line_numbers = QCheckBox(tr("preferences.editor_show_line_numbers"))
        self.show_line_numbers.setChecked(e.get("show_line_numbers", True))
        sform.addRow(self.show_line_numbers)

        self.word_wrap = QCheckBox(tr("preferences.editor_word_wrap"))
        self.word_wrap.setChecked(e.get("word_wrap", False))
        sform.addRow(self.word_wrap)

        layout.addWidget(group)
        layout.addStretch()

    def collect(self) -> dict[str, Any]:
        return {
            "font_family": self.font_family.currentFont().family(),
            "font_size": self.font_size.value(),
            "tab_width": self.tab_width.value(),
            "show_line_numbers": self.show_line_numbers.isChecked(),
            "word_wrap": self.word_wrap.isChecked(),
        }


class _ShortcutsPage(QWidget):
    """Keyboard shortcut customization."""

    def __init__(self, current_shortcuts: dict[str, str], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setHorizontalHeader(WordWrapHeaderView(self._table))
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels([
            tr("preferences.col_action"),
            tr("preferences.col_shortcut"),
            tr("preferences.col_default"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        self._action_ids: list[str] = list(DEFAULT_SHORTCUTS.keys())
        self._key_edits: list[QKeySequenceEdit] = []

        self._table.setRowCount(len(self._action_ids))
        for row, action_id in enumerate(self._action_ids):
            label_item = QTableWidgetItem(_action_label(action_id))
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, label_item)

            kse = QKeySequenceEdit()
            current = current_shortcuts.get(action_id, "")
            if current:
                kse.setKeySequence(QKeySequence(current))
            self._key_edits.append(kse)
            self._table.setCellWidget(row, 1, kse)

            default_str = DEFAULT_SHORTCUTS.get(action_id, "")
            default_item = QTableWidgetItem(default_str if default_str else "(none)")
            default_item.setFlags(
                default_item.flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            self._table.setItem(row, 2, default_item)

        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton(tr("preferences.reset_defaults"))
        reset_btn.clicked.connect(self._on_reset_defaults)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_reset_defaults(self):
        for row, action_id in enumerate(self._action_ids):
            default_str = DEFAULT_SHORTCUTS.get(action_id, "")
            if default_str:
                self._key_edits[row].setKeySequence(QKeySequence(default_str))
            else:
                self._key_edits[row].clear()

    def collect(self) -> dict[str, str]:
        shortcuts: dict[str, str] = {}
        for row, action_id in enumerate(self._action_ids):
            seq = self._key_edits[row].keySequence()
            shortcuts[action_id] = seq.toString() if not seq.isEmpty() else ""
        return shortcuts

    def validate(self) -> list[str]:
        """Return conflict messages, empty if OK."""
        shortcuts = self.collect()
        seen: dict[str, str] = {}
        conflicts: list[str] = []
        for action_id, seq_str in shortcuts.items():
            if not seq_str:
                continue
            if seq_str in seen:
                a = _action_label(seen[seq_str])
                b = _action_label(action_id)
                conflicts.append(
                    f'"{seq_str}" is assigned to both "{a}" and "{b}"'
                )
            else:
                seen[seq_str] = action_id
        return conflicts


# ── Main dialog ───────────────────────────────────────────────────


class PreferencesDialog(QDialog):
    """Two-column preferences dialog: category list + options panel."""

    def __init__(
        self,
        user_prefs: dict[str, Any],
        current_shortcuts: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("preferences.title"))
        self.setMinimumSize(700, 500)
        self.resize(780, 580)
        self.setModal(True)
        self.setStyleSheet(
            "QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit, "
            "QFontComboBox { max-width: 350px; min-width: 180px; }"
        )

        self._user_prefs = user_prefs
        self._current_shortcuts = current_shortcuts
        self._result_prefs: dict[str, Any] | None = None

        root = QVBoxLayout(self)

        body = QHBoxLayout()
        root.addLayout(body, stretch=1)

        self._cat_list = QListWidget()
        self._cat_list.setFixedWidth(170)
        self._cat_list.setSpacing(2)
        font = self._cat_list.font()
        font.setPointSize(font.pointSize() + 1)
        self._cat_list.setFont(font)
        body.addWidget(self._cat_list)

        self._stack = QStackedWidget()
        body.addWidget(self._stack, stretch=1)

        # ── Pages ──
        self._general_page = _GeneralPage(user_prefs)
        self._map_page = _MapPage(user_prefs)
        self._solver_sim_page = _SolverSimulationPage(user_prefs)
        self._editor_page = _EditorPage(user_prefs)
        self._shortcuts_page = _ShortcutsPage(current_shortcuts)

        categories = [
            (tr("preferences.cat_general"), self._general_page),
            (tr("preferences.cat_map"), self._map_page),
            (tr("preferences.cat_solver_sim"), self._solver_sim_page),
            (tr("preferences.cat_editor"), self._editor_page),
            (tr("preferences.cat_shortcuts"), self._shortcuts_page),
        ]

        for label, page in categories:
            item = QListWidgetItem(label)
            self._cat_list.addItem(item)
            self._stack.addWidget(page)

        self._cat_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._cat_list.setCurrentRow(0)

        # ── OK / Cancel ──
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        root.addWidget(button_box)

    def _on_accept(self):
        conflicts = self._shortcuts_page.validate()
        if conflicts:
            QMessageBox.warning(
                self,
                tr("preferences.conflict_title"),
                tr("preferences.conflict_msg") + "\n\n" + "\n".join(conflicts),
            )
            return

        self._result_prefs = {
            "general": self._general_page.collect(),
            "map": self._map_page.collect(),
            "solver": self._solver_sim_page.collect_solver(),
            "simulation": self._solver_sim_page.collect_simulation(),
            "editor": self._editor_page.collect(),
            "shortcuts": self._shortcuts_page.collect(),
        }
        self.accept()

    def get_result(self) -> dict[str, Any] | None:
        """Return full preferences dict if accepted, else None."""
        return self._result_prefs

    def get_shortcuts(self) -> dict[str, str] | None:
        if self._result_prefs is None:
            return None
        return self._result_prefs.get("shortcuts")
