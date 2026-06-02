"""Global simulation settings form (not per-system)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from esfex.config.schema import SporesObjective

from esfex.utils.temporal import HOURS_STD_YEAR
from esfex.visualization.data.gui_model import GuiModel
from esfex.visualization.i18n import tr


class GlobalSettingsForm(QWidget):
    """Editor for top-level simulation settings."""

    globalSettingsChanged = Signal()

    def __init__(self, model: GuiModel, parent=None):
        super().__init__(parent)
        self._model = model
        self._updating = False

        content_layout = QVBoxLayout(self)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(8)

        # ── Section 0: Systems to Simulate ──
        self._group_systems = QGroupBox(tr("global_form.group_systems"))
        self._systems_layout = QVBoxLayout(self._group_systems)
        self._systems_layout.setContentsMargins(6, 6, 6, 6)
        self._systems_layout.setSpacing(2)
        self._system_checks: dict[str, QCheckBox] = {}
        # Populated dynamically via set_available_systems()
        content_layout.addWidget(self._group_systems)

        # ── Section 1: Simulation ──
        group_sim = QGroupBox(tr("global_form.tab_simulation"))
        sl = QFormLayout(group_sim)
        sl.setContentsMargins(6, 6, 6, 6)
        sl.setSpacing(4)

        self._sim_mode = QComboBox()
        self._sim_mode.addItem(tr("global_form.mode_development"), "development")
        self._sim_mode.addItem(tr("global_form.mode_unit_commitment"), "unit_commitment")
        self._sim_mode.currentIndexChanged.connect(self._on_changed)
        self._sim_mode.currentIndexChanged.connect(self._refresh_sim_mode_widgets)
        sl.addRow(tr("global_form.sim_mode"), self._sim_mode)

        # Mode-specific widgets (visibility toggled by _refresh_sim_mode_widgets):
        # * unit_commitment mode → UC Hours spinbox (caps the dispatch horizon).
        # * development mode → Enable-UC checkbox so the user can request binary
        #   commitment inside each rolling-horizon window
        #   (master_problem.use_uc_in_dispatch). Off by default = LP econ dispatch.
        # Both rows are stored on the layout so we can hide/show with
        # QFormLayout.setRowVisible.
        self._uc_hours = QSpinBox()
        self._uc_hours.setRange(1, HOURS_STD_YEAR)
        self._uc_hours.editingFinished.connect(self._on_changed)
        sl.addRow(tr("global_form.uc_hours"), self._uc_hours)
        self._sl = sl  # remember the QFormLayout for setRowVisible later

        self._enable_uc_in_dispatch = QCheckBox(tr("common.enable"))
        self._enable_uc_in_dispatch.toggled.connect(self._on_changed)
        sl.addRow("Enable UC", self._enable_uc_in_dispatch)

        self._date_start = QLineEdit()
        self._date_start.setPlaceholderText(tr("global_form.date_placeholder"))
        self._date_start.editingFinished.connect(self._on_changed)
        sl.addRow(tr("global_form.date_start"), self._date_start)

        self._enable_pe = QCheckBox(tr("common.enable"))
        self._enable_pe.toggled.connect(self._on_changed)
        sl.addRow(tr("global_form.primary_energy"), self._enable_pe)

        self._console_log_level = QComboBox()
        self._console_log_level.addItem(tr("global_form.log_level_basic"), "basic")
        self._console_log_level.addItem(tr("global_form.log_level_high"), "high")
        self._console_log_level.currentIndexChanged.connect(self._on_changed)
        sl.addRow(tr("global_form.console_log_level"), self._console_log_level)

        content_layout.addWidget(group_sim)

        # ── Section 2: Temporal ──
        group_temp = QGroupBox(tr("global_form.group_temporal"))
        tl = QFormLayout(group_temp)
        tl.setContentsMargins(6, 6, 6, 6)
        tl.setSpacing(4)

        self._resolution = QSpinBox()
        self._resolution.setRange(1, 24)
        self._resolution.editingFinished.connect(self._on_changed)
        tl.addRow(tr("global_form.resolution"), self._resolution)

        self._rolling_horizon = QSpinBox()
        self._rolling_horizon.setRange(1, HOURS_STD_YEAR)
        self._rolling_horizon.editingFinished.connect(self._on_changed)
        tl.addRow(tr("global_form.rolling_horizon"), self._rolling_horizon)

        self._overlap = QSpinBox()
        self._overlap.setRange(0, 720)
        self._overlap.editingFinished.connect(self._on_changed)
        tl.addRow(tr("global_form.overlap"), self._overlap)

        self._inv_resolution = QSpinBox()
        self._inv_resolution.setRange(1, 87600)
        self._inv_resolution.editingFinished.connect(self._on_changed)
        tl.addRow(tr("global_form.inv_resolution"), self._inv_resolution)

        self._pe_resolution = QSpinBox()
        self._pe_resolution.setRange(1, HOURS_STD_YEAR)
        self._pe_resolution.editingFinished.connect(self._on_changed)
        tl.addRow(tr("global_form.pe_resolution"), self._pe_resolution)

        self._use_rolling = QCheckBox(tr("global_form.enable"))
        self._use_rolling.toggled.connect(self._on_changed)
        tl.addRow(tr("global_form.use_rolling"), self._use_rolling)

        content_layout.addWidget(group_temp)

        # ── Section 3: Power Flow formulation ──
        # Placed BEFORE the solver section on purpose: the OPF formulation
        # constrains which solvers are valid (AC-OPF NLP modes need Ipopt,
        # acopf_sdp needs SCS/Mosek, convex relaxations work with HiGHS/
        # Gurobi), so the user picks the formulation first. The OPF formulation
        # is also a MODEL-WIDE choice — multi-system configs merge into a
        # single network solved with one formulation (runner._merge_systems) —
        # hence Global Settings, not per-System. Data stays on model.state
        # (the GuiModel keeps a single GuiSystemState), so no serializer change.
        group_pf = QGroupBox(tr("system_form.group_power_flow"))
        pfl = QFormLayout(group_pf)
        pfl.setContentsMargins(6, 6, 6, 6)
        pfl.setSpacing(4)

        self._pf_mode = QComboBox()
        self._pf_modes = [
            ("dcopf", tr("system_form.pf_dcopf")),
            ("dcopf_ac_verify", tr("system_form.pf_dcopf_ac_verify")),
            ("acopf_soc", tr("system_form.pf_acopf_soc")),
            ("acopf_qc", tr("system_form.pf_acopf_qc")),
            ("acopf_sdp", tr("system_form.pf_acopf_sdp")),
            ("acopf_polar", tr("system_form.pf_acopf_polar")),
            ("acopf_rect", tr("system_form.pf_acopf_rect")),
        ]
        for key, label in self._pf_modes:
            self._pf_mode.addItem(label, key)
        self._pf_mode.currentIndexChanged.connect(self._on_pf_mode_changed)
        pfl.addRow(tr("system_form.pf_mode"), self._pf_mode)

        # NOTE: the DC slack bus is NOT a widget here. The slack/reference bus
        # is designated per-bus via the bus editor's "Bus Type" = "slack"
        # (BusConfig.bus_type) — converters.py scans every bus of the merged
        # network and uses whichever is typed "slack", so the user can place
        # it on any bus of any system. The legacy dc_power_flow.slack_bus
        # integer index is only a fallback when no bus is typed "slack".
        # The DC angle-difference limit was removed from the formulation, so
        # it is not exposed either.

        self._ac_base_mva = QDoubleSpinBox()
        self._ac_base_mva.setRange(1, 100000)
        self._ac_base_mva.setDecimals(3)
        self._ac_base_mva.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_base_mva"), self._ac_base_mva)

        self._ac_v_min = QDoubleSpinBox()
        self._ac_v_min.setRange(0.5, 1.0)
        self._ac_v_min.setDecimals(3)
        self._ac_v_min.setSingleStep(0.01)
        self._ac_v_min.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_v_min"), self._ac_v_min)

        self._ac_v_max = QDoubleSpinBox()
        self._ac_v_max.setRange(1.0, 1.5)
        self._ac_v_max.setDecimals(3)
        self._ac_v_max.setSingleStep(0.01)
        self._ac_v_max.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_v_max"), self._ac_v_max)

        self._ac_default_pf = QDoubleSpinBox()
        self._ac_default_pf.setRange(0.1, 1.0)
        self._ac_default_pf.setDecimals(2)
        self._ac_default_pf.setSingleStep(0.05)
        self._ac_default_pf.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_default_pf"), self._ac_default_pf)

        self._ac_load_pf = QDoubleSpinBox()
        self._ac_load_pf.setRange(0.1, 1.0)
        self._ac_load_pf.setDecimals(2)
        self._ac_load_pf.setSingleStep(0.05)
        self._ac_load_pf.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_load_pf"), self._ac_load_pf)

        self._ac_q_penalty = QDoubleSpinBox()
        self._ac_q_penalty.setRange(0, 100000)
        self._ac_q_penalty.setDecimals(3)
        self._ac_q_penalty.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_q_penalty"), self._ac_q_penalty)

        self._ac_min_x = QDoubleSpinBox()
        self._ac_min_x.setRange(0.001, 1.0)
        self._ac_min_x.setDecimals(4)
        self._ac_min_x.setSingleStep(0.001)
        self._ac_min_x.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_min_x"), self._ac_min_x)

        self._ac_tap_min = QDoubleSpinBox()
        self._ac_tap_min.setRange(0.01, 1.0)
        self._ac_tap_min.setDecimals(2)
        self._ac_tap_min.setSingleStep(0.05)
        self._ac_tap_min.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_tap_min"), self._ac_tap_min)

        self._ac_tap_max = QDoubleSpinBox()
        self._ac_tap_max.setRange(1.0, 10.0)
        self._ac_tap_max.setDecimals(2)
        self._ac_tap_max.setSingleStep(0.1)
        self._ac_tap_max.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_tap_max"), self._ac_tap_max)

        self._ac_q_min_ratio = QDoubleSpinBox()
        self._ac_q_min_ratio.setRange(0, 1.0)
        self._ac_q_min_ratio.setDecimals(2)
        self._ac_q_min_ratio.setSingleStep(0.05)
        self._ac_q_min_ratio.editingFinished.connect(self._on_changed)
        pfl.addRow(tr("system_form.ac_q_min_ratio"), self._ac_q_min_ratio)

        # Store AC widget refs for visibility toggling
        self._ac_widgets = [
            self._ac_base_mva, self._ac_v_min, self._ac_v_max,
            self._ac_default_pf, self._ac_load_pf, self._ac_q_penalty,
            self._ac_min_x, self._ac_tap_min, self._ac_tap_max,
            self._ac_q_min_ratio,
        ]
        self._ac_labels = []
        for i in range(pfl.rowCount()):
            label_item = pfl.itemAt(i, QFormLayout.LabelRole)
            field_item = pfl.itemAt(i, QFormLayout.FieldRole)
            if field_item and field_item.widget() in self._ac_widgets:
                if label_item and label_item.widget():
                    self._ac_labels.append(label_item.widget())

        # group_pf is appended in the canonical ordering block at the
        # end of __init__ (see comment there); same for the other groups.

        # ── Section 3b: Solver ──
        # Unified "Solver Options" group: holds the 6 generic solver fields
        # (name / threads / time_limit / gap / verbose / scale) AND embeds
        # the solver-specific sub-group below them. The user-facing label
        # reflects the unification — "Solver Options" instead of two
        # separate "Solver" + "Solver-Specific Options" panels.
        group_solver = QGroupBox(tr("global_form.solver_options")
                                  if hasattr(tr("global_form.solver_options"), "__class__")
                                  else "Solver Options")
        # tr() returns a str always; the conditional above is defensive
        # in case the key is missing — fall back to literal "Solver Options".
        group_solver.setTitle("Solver Options")
        svl = QFormLayout(group_solver)
        svl.setContentsMargins(6, 6, 6, 6)
        svl.setSpacing(4)
        self._solver_group_layout = svl  # kept for contextual enable/disable

        from esfex.config.solver import SOLVER_OPTIONS, detect_available_solvers

        self._available_solvers = detect_available_solvers()
        self._solver_name = QComboBox()
        self._solver_name_keys: list[str] = []
        for name, is_available in self._available_solvers.items():
            if not is_available:
                continue
            self._solver_name.addItem(name)
            self._solver_name_keys.append(name)
        self._solver_name.currentIndexChanged.connect(self._on_solver_changed)
        svl.addRow(tr("global_form.solver"), self._solver_name)

        self._solver_threads = QSpinBox()
        self._solver_threads.setRange(1, 128)
        self._solver_threads.editingFinished.connect(self._on_changed)
        svl.addRow(tr("global_form.threads"), self._solver_threads)

        self._solver_time = QSpinBox()
        self._solver_time.setRange(1, 86400)
        self._solver_time.editingFinished.connect(self._on_changed)
        svl.addRow(tr("global_form.time_limit"), self._solver_time)

        self._solver_gap = QDoubleSpinBox()
        self._solver_gap.setRange(0, 1)
        self._solver_gap.setDecimals(4)
        self._solver_gap.editingFinished.connect(self._on_changed)
        svl.addRow(tr("global_form.gap"), self._solver_gap)

        self._solver_verbose = QCheckBox(tr("global_form.verbose"))
        self._solver_verbose.toggled.connect(self._on_changed)
        svl.addRow("", self._solver_verbose)

        self._solver_scale = QCheckBox(tr("global_form.scale_constraints"))
        self._solver_scale.toggled.connect(self._on_changed)
        svl.addRow("", self._solver_scale)

        # Solver-specific options live INSIDE the unified group as a
        # sub-section, so collapsing/showing them when the chosen solver
        # has no extra options still keeps everything visually in one
        # block. Defer add to content_layout — we reorder all sections
        # at the end of __init__.
        # Plain QWidget container (not a QGroupBox): the parent group already
        # provides the "Solver Options" title and frame — a nested groupbox
        # would draw a redundant sub-title.
        self._solver_opts_group = QWidget()
        self._solver_opts_layout = QFormLayout(self._solver_opts_group)
        self._solver_opts_layout.setContentsMargins(0, 0, 0, 0)
        self._solver_opts_layout.setSpacing(4)
        self._solver_opt_widgets: dict[str, QWidget] = {}
        svl.addRow(self._solver_opts_group)

        # Build initial solver options for default solver
        self._rebuild_solver_options("highs")

        # ── Section 4: N-1 Security ──
        group_n1 = QGroupBox(tr("global_form.group_n1"))
        n1l = QFormLayout(group_n1)
        n1l.setContentsMargins(6, 6, 6, 6)
        n1l.setSpacing(4)

        self._n1_enabled = QCheckBox(tr("global_form.enable_n1"))
        self._n1_enabled.toggled.connect(self._on_changed)
        n1l.addRow("", self._n1_enabled)

        self._n1_trans_enabled = QCheckBox(tr("global_form.enable"))
        self._n1_trans_enabled.toggled.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_trans"), self._n1_trans_enabled)

        self._n1_trans_reserve = QDoubleSpinBox()
        self._n1_trans_reserve.setRange(0, 1)
        self._n1_trans_reserve.setDecimals(3)
        self._n1_trans_reserve.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_trans_reserve"), self._n1_trans_reserve)

        self._n1_crit_line = QDoubleSpinBox()
        self._n1_crit_line.setRange(0, 1)
        self._n1_crit_line.setDecimals(3)
        self._n1_crit_line.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_crit_line"), self._n1_crit_line)

        self._n1_gen_enabled = QCheckBox(tr("global_form.enable"))
        self._n1_gen_enabled.toggled.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_gen"), self._n1_gen_enabled)

        self._n1_gen_type = QComboBox()
        self._n1_gen_type.addItem(tr("global_form.reserve_largest"), "largest_unit")
        self._n1_gen_type.addItem(tr("global_form.reserve_percentage"), "percentage")
        self._n1_gen_type.addItem(tr("global_form.reserve_fixed"), "fixed_mw")
        self._n1_gen_type.currentIndexChanged.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_gen_type"), self._n1_gen_type)

        self._n1_gen_pct = QDoubleSpinBox()
        self._n1_gen_pct.setRange(0, 1)
        self._n1_gen_pct.setDecimals(3)
        self._n1_gen_pct.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_gen_pct"), self._n1_gen_pct)

        # ── N-k / SCOPF sub-section ──
        self._n1_scopf_enabled = QCheckBox(tr("global_form.n1_scopf"))
        self._n1_scopf_enabled.setToolTip(
            tr("global_form.n1_scopf_tip"))
        self._n1_scopf_enabled.toggled.connect(self._on_n1_scopf_toggled)
        n1l.addRow("", self._n1_scopf_enabled)

        self._n1_scopf_max_iter = QSpinBox()
        self._n1_scopf_max_iter.setRange(1, 20)
        self._n1_scopf_max_iter.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_scopf_max_iter"), self._n1_scopf_max_iter)

        self._n1_scopf_tol = QDoubleSpinBox()
        self._n1_scopf_tol.setRange(0, 1)
        self._n1_scopf_tol.setDecimals(4)
        self._n1_scopf_tol.setSingleStep(0.001)
        self._n1_scopf_tol.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_scopf_tol"), self._n1_scopf_tol)

        self._n1_corrective = QCheckBox(tr("global_form.n1_corrective"))
        self._n1_corrective.setToolTip(
            tr("global_form.n1_corrective_tip"))
        self._n1_corrective.toggled.connect(self._on_changed)
        n1l.addRow("", self._n1_corrective)

        # ── N-k Depth & Analysis Options ──
        self._n1_depth = QComboBox()
        self._n1_depth.addItem("N-1", "n1")
        self._n1_depth.addItem("N-1-1", "n1_1")
        self._n1_depth.currentIndexChanged.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_depth"), self._n1_depth)

        self._n1_redispatch = QComboBox()
        self._n1_redispatch.addItem(tr("global_form.n1_redispatch_prorata"), "pro_rata")
        self._n1_redispatch.addItem(tr("global_form.n1_redispatch_droop"), "droop")
        self._n1_redispatch.currentIndexChanged.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_redispatch"), self._n1_redispatch)

        self._n1_pi_threshold = QDoubleSpinBox()
        self._n1_pi_threshold.setRange(0, 100)
        self._n1_pi_threshold.setDecimals(2)
        self._n1_pi_threshold.setSingleStep(0.1)
        self._n1_pi_threshold.setToolTip(tr("global_form.n1_pi_tip"))
        self._n1_pi_threshold.editingFinished.connect(self._on_changed)
        n1l.addRow(tr("global_form.n1_pi_threshold"), self._n1_pi_threshold)

        self._n1_transformer_ctg = QCheckBox(
            tr("global_form.n1_transformer_ctg"))
        self._n1_transformer_ctg.setToolTip(
            tr("global_form.n1_transformer_ctg_tip"))
        self._n1_transformer_ctg.toggled.connect(self._on_changed)
        n1l.addRow("", self._n1_transformer_ctg)

        self._n1_battery_ctg = QCheckBox(
            tr("global_form.n1_battery_ctg"))
        self._n1_battery_ctg.setToolTip(
            tr("global_form.n1_battery_ctg_tip"))
        self._n1_battery_ctg.toggled.connect(self._on_changed)
        n1l.addRow("", self._n1_battery_ctg)

        # Initial visibility
        self._n1_scopf_max_iter.setEnabled(False)
        self._n1_scopf_tol.setEnabled(False)

        # group_n1 added in the canonical ordering block at end of __init__.

        # ── Section 5: Master Problem ──
        group_mp = QGroupBox(tr("global_form.group_master"))
        mpl = QFormLayout(group_mp)
        mpl.setContentsMargins(6, 6, 6, 6)
        mpl.setSpacing(4)

        self._mp_stochastic = QCheckBox(tr("global_form.enable_stochastic"))
        self._mp_stochastic.toggled.connect(self._on_changed)
        mpl.addRow("", self._mp_stochastic)

        self._mp_rep_days = QSpinBox()
        self._mp_rep_days.setRange(1, 365)
        self._mp_rep_days.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_rep_days"), self._mp_rep_days)

        self._mp_min_sep = QSpinBox()
        self._mp_min_sep.setRange(0, 365)
        self._mp_min_sep.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_min_sep"), self._mp_min_sep)

        # TSAM sub-section
        self._mp_use_tsam = QCheckBox(tr("global_form.mp_use_tsam"))
        self._mp_use_tsam.toggled.connect(self._on_tsam_toggled)
        mpl.addRow("", self._mp_use_tsam)

        self._mp_tsam_num_periods = QSpinBox()
        self._mp_tsam_num_periods.setRange(2, 365)
        self._mp_tsam_num_periods.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_tsam_num_periods"), self._mp_tsam_num_periods)

        self._mp_tsam_method = QComboBox()
        self._mp_tsam_method.addItems(["kmedoids", "kmeans"])
        self._mp_tsam_method.currentIndexChanged.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_tsam_method"), self._mp_tsam_method)

        self._mp_tsam_inter_period_linking = QCheckBox(
            tr("global_form.mp_tsam_inter_period_linking")
        )
        self._mp_tsam_inter_period_linking.toggled.connect(self._on_changed)
        mpl.addRow("", self._mp_tsam_inter_period_linking)

        # MGA / SPORES sub-section
        self._mp_mga_enabled = QCheckBox(tr("global_form.mp_mga_enabled"))
        self._mp_mga_enabled.toggled.connect(self._on_mga_toggled)
        mpl.addRow("", self._mp_mga_enabled)

        # Method selector: classical MGA (HSJ loop) vs SPORES (one alt
        # per objective). The two paths live behind one ``MGAConfig`` in
        # the schema and route to different Julia entry points in the
        # adapter (Phase 3 of the SPORES roadmap).
        self._mp_mga_method = QComboBox()
        self._mp_mga_method.addItem(tr("global_form.mp_mga_method_mga"), "mga")
        self._mp_mga_method.addItem(tr("global_form.mp_mga_method_spores"), "spores")
        self._mp_mga_method.currentIndexChanged.connect(self._on_mga_method_changed)
        mpl.addRow(tr("global_form.mp_mga_method"), self._mp_mga_method)

        # Objective checklist — only meaningful when method='spores'.
        # Each row is the SporesObjective enum value as a checkable item.
        # Compact ~120 px tall list so it doesn't dominate the form when
        # method=mga (where it stays disabled).
        self._mp_mga_objectives = QListWidget()
        self._mp_mga_objectives.setSelectionMode(
            QListWidget.SelectionMode.NoSelection
        )
        self._mp_mga_objectives.setFixedHeight(120)
        # The objective labels are short ("Tech equity", "Min total build",
        # …). The default behaviour stretches the QListWidget to fill the
        # form-field column (~360 px on common screens), which leaves a
        # large empty area on the right of every row. Capping at 60 % of
        # that natural width (≈ 220 px) tightens the visual footprint
        # without truncating any label.
        self._mp_mga_objectives.setMaximumWidth(220)
        for obj in SporesObjective:
            item = QListWidgetItem(
                tr(f"global_form.mp_mga_objective_{obj.value}")
            )
            item.setData(Qt.ItemDataRole.UserRole, obj.value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._mp_mga_objectives.addItem(item)
        self._mp_mga_objectives.itemChanged.connect(
            lambda _it: self._on_changed()
        )
        mpl.addRow(tr("global_form.mp_mga_objectives"), self._mp_mga_objectives)

        self._mp_mga_num_alts = QSpinBox()
        self._mp_mga_num_alts.setRange(1, 100)
        self._mp_mga_num_alts.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_mga_num_alts"), self._mp_mga_num_alts)

        self._mp_mga_slack = QDoubleSpinBox()
        self._mp_mga_slack.setRange(0.0, 0.5)
        self._mp_mga_slack.setDecimals(3)
        self._mp_mga_slack.setSingleStep(0.01)
        self._mp_mga_slack.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_mga_slack"), self._mp_mga_slack)

        self._mp_mga_threshold = QDoubleSpinBox()
        self._mp_mga_threshold.setRange(0.0, 1000.0)
        self._mp_mga_threshold.setDecimals(3)
        self._mp_mga_threshold.editingFinished.connect(self._on_changed)
        mpl.addRow(tr("global_form.mp_mga_threshold"), self._mp_mga_threshold)

        # group_mp added in the canonical ordering block at end of __init__.

        # ── Section 5b: Risk & Resilience ──
        group_risk = QGroupBox(tr("global_form.group_risk"))
        rl = QFormLayout(group_risk)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(4)

        self._risk_enabled = QCheckBox(tr("global_form.enable_risk"))
        self._risk_enabled.toggled.connect(self._on_risk_toggled)
        rl.addRow("", self._risk_enabled)

        self._risk_measure = QComboBox()
        self._risk_measure.addItem(tr("global_form.risk_expected"), "expected")
        self._risk_measure.addItem(tr("global_form.risk_cvar"), "cvar")
        self._risk_measure.addItem(tr("global_form.risk_minimax"), "minimax_regret")
        self._risk_measure.currentIndexChanged.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_measure"), self._risk_measure)

        self._risk_cvar_alpha = QDoubleSpinBox()
        self._risk_cvar_alpha.setRange(0.01, 0.99)
        self._risk_cvar_alpha.setDecimals(2)
        self._risk_cvar_alpha.setSingleStep(0.05)
        self._risk_cvar_alpha.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_cvar_alpha"), self._risk_cvar_alpha)

        self._risk_cvar_lambda = QDoubleSpinBox()
        self._risk_cvar_lambda.setRange(0.0, 1.0)
        self._risk_cvar_lambda.setDecimals(2)
        self._risk_cvar_lambda.setSingleStep(0.1)
        self._risk_cvar_lambda.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_cvar_lambda"), self._risk_cvar_lambda)

        self._risk_combination = QComboBox()
        self._risk_combination.addItem(tr("global_form.risk_independent"), "independent")
        self._risk_combination.addItem(tr("global_form.risk_copula"), "copula")
        self._risk_combination.addItem(tr("global_form.risk_mcda"), "mcda")
        self._risk_combination.currentIndexChanged.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_combination"), self._risk_combination)

        # VOLL sub-section
        rl.addRow(QLabel(tr("global_form.risk_voll_header")))

        self._risk_voll_res = QDoubleSpinBox()
        self._risk_voll_res.setRange(0, 1_000_000)
        self._risk_voll_res.setDecimals(0)
        self._risk_voll_res.setSuffix(" $/MWh")
        self._risk_voll_res.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_voll_residential"), self._risk_voll_res)

        self._risk_voll_com = QDoubleSpinBox()
        self._risk_voll_com.setRange(0, 1_000_000)
        self._risk_voll_com.setDecimals(0)
        self._risk_voll_com.setSuffix(" $/MWh")
        self._risk_voll_com.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_voll_commercial"), self._risk_voll_com)

        self._risk_voll_ind = QDoubleSpinBox()
        self._risk_voll_ind.setRange(0, 1_000_000)
        self._risk_voll_ind.setDecimals(0)
        self._risk_voll_ind.setSuffix(" $/MWh")
        self._risk_voll_ind.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_voll_industrial"), self._risk_voll_ind)

        self._risk_voll_crit = QDoubleSpinBox()
        self._risk_voll_crit.setRange(0, 1_000_000)
        self._risk_voll_crit.setDecimals(0)
        self._risk_voll_crit.setSuffix(" $/MWh")
        self._risk_voll_crit.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_voll_critical"), self._risk_voll_crit)

        # Temperature-dependent demand
        self._risk_base_temp = QDoubleSpinBox()
        self._risk_base_temp.setRange(-20, 40)
        self._risk_base_temp.setDecimals(1)
        self._risk_base_temp.setSuffix(" \u00b0C")
        self._risk_base_temp.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_base_temp"), self._risk_base_temp)

        self._risk_heat_coeff = QDoubleSpinBox()
        self._risk_heat_coeff.setRange(0, 100)
        self._risk_heat_coeff.setDecimals(4)
        self._risk_heat_coeff.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_heat_coeff"), self._risk_heat_coeff)

        self._risk_cool_coeff = QDoubleSpinBox()
        self._risk_cool_coeff.setRange(0, 100)
        self._risk_cool_coeff.setDecimals(4)
        self._risk_cool_coeff.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_cool_coeff"), self._risk_cool_coeff)

        # Insurance & Monte Carlo
        self._risk_insurance = QDoubleSpinBox()
        self._risk_insurance.setRange(0, 1)
        self._risk_insurance.setDecimals(4)
        self._risk_insurance.setSingleStep(0.001)
        self._risk_insurance.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_insurance"), self._risk_insurance)

        self._risk_mc_samples = QSpinBox()
        self._risk_mc_samples.setRange(100, 100000)
        self._risk_mc_samples.setSingleStep(100)
        self._risk_mc_samples.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_mc_samples"), self._risk_mc_samples)

        self._risk_mc_seed = QSpinBox()
        self._risk_mc_seed.setRange(0, 999999)
        self._risk_mc_seed.editingFinished.connect(self._on_changed)
        rl.addRow(tr("global_form.risk_mc_seed"), self._risk_mc_seed)

        # Initial enabled state
        self._update_risk_enabled(False)

        # group_risk added in the canonical ordering block at end of __init__.

        # ── Section 6: Visual Scaling ──
        group_vs = QGroupBox(tr("global_form.group_scaling"))
        vsl = QFormLayout(group_vs)
        vsl.setContentsMargins(6, 6, 6, 6)
        vsl.setSpacing(4)

        self._vs_marker_min = QDoubleSpinBox()
        self._vs_marker_min.setRange(1, 100)
        self._vs_marker_min.setDecimals(1)
        self._vs_marker_min.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_marker_min"), self._vs_marker_min)

        self._vs_elec_marker = QDoubleSpinBox()
        self._vs_elec_marker.setRange(0.0001, 10.0)
        self._vs_elec_marker.setDecimals(4)
        self._vs_elec_marker.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_elec_marker"), self._vs_elec_marker)

        self._vs_energy_marker = QDoubleSpinBox()
        self._vs_energy_marker.setRange(0.0001, 10.0)
        self._vs_energy_marker.setDecimals(4)
        self._vs_energy_marker.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_energy_marker"), self._vs_energy_marker)

        self._vs_fuel_marker = QDoubleSpinBox()
        self._vs_fuel_marker.setRange(0.0001, 100.0)
        self._vs_fuel_marker.setDecimals(4)
        self._vs_fuel_marker.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_fuel_marker"), self._vs_fuel_marker)

        self._vs_line_min = QDoubleSpinBox()
        self._vs_line_min.setRange(0.5, 50)
        self._vs_line_min.setDecimals(1)
        self._vs_line_min.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_line_min"), self._vs_line_min)

        self._vs_elec_line = QDoubleSpinBox()
        self._vs_elec_line.setRange(0.0001, 10.0)
        self._vs_elec_line.setDecimals(4)
        self._vs_elec_line.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_elec_line"), self._vs_elec_line)

        self._vs_fuel_line = QDoubleSpinBox()
        self._vs_fuel_line.setRange(0.0001, 100.0)
        self._vs_fuel_line.setDecimals(4)
        self._vs_fuel_line.editingFinished.connect(self._on_changed)
        vsl.addRow(tr("global_form.vs_fuel_line"), self._vs_fuel_line)

        # ─── Final section ordering ──────────────────────────────────
        # Master Problem now lives RIGHT AFTER Power Flow (closer to
        # the model-structure decisions it depends on); the unified
        # "Solver Options" block sits JUST BEFORE Visual Scaling so the
        # last visible group before display tweaks is the run config.
        # The six addWidget calls scattered through __init__ are kept
        # but inert (commented-out below); the canonical ordering lives
        # here so it's auditable in one place.
        for grp in (
            group_pf,
            group_mp,
            group_n1,
            group_risk,
            group_solver,
            group_vs,
        ):
            content_layout.addWidget(grp)

        # Add stretch at the end
        content_layout.addStretch()

        # Initial formulation→solver compatibility state (before first load).
        self._refresh_solver_compat()


    def set_available_systems(self, system_names: list[str]):
        """Rebuild the systems checkbox list when systems change."""
        # Remove old checkboxes
        for cb in self._system_checks.values():
            self._systems_layout.removeWidget(cb)
            cb.deleteLater()
        self._system_checks.clear()

        g = self._model.global_settings
        selected = set(g.systems_to_simulate) if g.systems_to_simulate else set(system_names)

        for name in system_names:
            cb = QCheckBox(name)
            cb.setChecked(name in selected)
            cb.toggled.connect(self._on_system_check_changed)
            self._systems_layout.addWidget(cb)
            self._system_checks[name] = cb

    def _on_system_check_changed(self):
        """Update the model when system checkboxes change."""
        if self._updating:
            return
        self._on_changed()

    def load_element(self, element_id: str = ""):
        """Load global settings. element_id is ignored."""
        self._updating = True
        g = self._model.global_settings

        # Systems to simulate
        if self._system_checks:
            selected = set(g.systems_to_simulate) if g.systems_to_simulate else set(self._system_checks.keys())
            for name, cb in self._system_checks.items():
                cb.setChecked(name in selected)

        # Simulation
        idx = self._sim_mode.findData(g.simulation_mode)
        if idx >= 0:
            self._sim_mode.setCurrentIndex(idx)
        self._uc_hours.setValue(g.unit_commitment_hours)
        self._enable_uc_in_dispatch.setChecked(bool(g.mp_use_uc_in_dispatch))
        self._refresh_sim_mode_widgets()
        self._date_start.setText(g.date_start)
        self._enable_pe.setChecked(g.enable_primary_energy)
        idx = self._console_log_level.findData(g.console_log_level)
        if idx >= 0:
            self._console_log_level.setCurrentIndex(idx)

        # Temporal
        self._resolution.setValue(g.resolution_hours)
        self._rolling_horizon.setValue(g.rolling_horizon_hours)
        self._overlap.setValue(g.overlap_hours)
        self._inv_resolution.setValue(g.investment_resolution)
        self._pe_resolution.setValue(g.primary_energy_resolution)
        self._use_rolling.setChecked(g.use_rolling_horizon)

        # Solver – find by key, not display text
        solver_key = g.solver_name.lower()
        if solver_key in self._solver_name_keys:
            idx = self._solver_name_keys.index(solver_key)
            self._solver_name.setCurrentIndex(idx)
        else:
            idx = self._solver_name.findText(g.solver_name)
            if idx >= 0:
                self._solver_name.setCurrentIndex(idx)
        self._solver_threads.setValue(g.solver_threads)
        self._solver_time.setValue(g.solver_time_limit)
        self._solver_gap.setValue(g.solver_gap)
        self._solver_verbose.setChecked(g.solver_verbose)
        self._solver_scale.setChecked(g.solver_scale_constraints)

        # Solver-specific options
        self._rebuild_solver_options(solver_key)
        self._load_solver_options(g.solver_specific_options)

        # N-1
        self._n1_enabled.setChecked(g.n1_enabled)
        self._n1_trans_enabled.setChecked(g.n1_transmission_enabled)
        self._n1_trans_reserve.setValue(g.n1_transmission_reserve_factor)
        self._n1_crit_line.setValue(g.n1_critical_line_threshold)
        self._n1_gen_enabled.setChecked(g.n1_generation_enabled)
        idx = self._n1_gen_type.findData(g.n1_generation_reserve_type)
        if idx >= 0:
            self._n1_gen_type.setCurrentIndex(idx)
        self._n1_gen_pct.setValue(g.n1_generation_reserve_percentage)
        self._n1_scopf_enabled.setChecked(g.n1_scopf_enabled)
        self._n1_scopf_max_iter.setValue(g.n1_scopf_max_iterations)
        self._n1_scopf_tol.setValue(g.n1_scopf_violation_tolerance)
        self._n1_corrective.setChecked(g.n1_corrective_enabled)
        self._n1_scopf_max_iter.setEnabled(g.n1_scopf_enabled)
        self._n1_scopf_tol.setEnabled(g.n1_scopf_enabled)
        idx = self._n1_depth.findData(g.n1_contingency_depth)
        if idx >= 0:
            self._n1_depth.setCurrentIndex(idx)
        idx = self._n1_redispatch.findData(g.n1_redistribution_mode)
        if idx >= 0:
            self._n1_redispatch.setCurrentIndex(idx)
        self._n1_pi_threshold.setValue(g.n1_pi_screening_threshold)
        self._n1_transformer_ctg.setChecked(g.n1_transformer_contingencies)
        self._n1_battery_ctg.setChecked(g.n1_battery_contingencies)

        # Master Problem
        self._mp_stochastic.setChecked(g.mp_stochastic)
        self._mp_rep_days.setValue(g.mp_representative_days)
        self._mp_min_sep.setValue(g.mp_min_day_separation)
        self._mp_use_tsam.setChecked(g.mp_use_tsam)
        self._mp_tsam_num_periods.setValue(g.mp_tsam_num_periods)
        idx = self._mp_tsam_method.findText(g.mp_tsam_method)
        if idx >= 0:
            self._mp_tsam_method.setCurrentIndex(idx)
        self._mp_tsam_inter_period_linking.setChecked(g.mp_tsam_inter_period_linking)
        self._update_tsam_enabled(g.mp_use_tsam)

        # MGA / SPORES
        self._mp_mga_enabled.setChecked(g.mp_mga_enabled)
        midx = self._mp_mga_method.findData(g.mp_mga_method)
        self._mp_mga_method.setCurrentIndex(max(midx, 0))
        # Sync the objective checklist against the persisted list.
        selected = set(g.mp_mga_objectives or [])
        for i in range(self._mp_mga_objectives.count()):
            item = self._mp_mga_objectives.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if key in selected
                else Qt.CheckState.Unchecked
            )
        self._mp_mga_num_alts.setValue(g.mp_mga_num_alternatives)
        self._mp_mga_slack.setValue(g.mp_mga_slack_fraction)
        self._mp_mga_threshold.setValue(g.mp_mga_investment_threshold)
        self._update_mga_enabled(g.mp_mga_enabled)

        # Risk & Resilience
        self._risk_enabled.setChecked(g.risk_enabled)
        idx = self._risk_measure.findData(g.risk_measure)
        if idx >= 0:
            self._risk_measure.setCurrentIndex(idx)
        self._risk_cvar_alpha.setValue(g.risk_cvar_alpha)
        self._risk_cvar_lambda.setValue(g.risk_cvar_lambda)
        idx = self._risk_combination.findData(g.risk_combination_method)
        if idx >= 0:
            self._risk_combination.setCurrentIndex(idx)
        self._risk_voll_res.setValue(g.risk_voll_residential)
        self._risk_voll_com.setValue(g.risk_voll_commercial)
        self._risk_voll_ind.setValue(g.risk_voll_industrial)
        self._risk_voll_crit.setValue(g.risk_voll_critical)
        self._risk_base_temp.setValue(g.risk_base_temperature)
        self._risk_heat_coeff.setValue(g.risk_heating_coefficient)
        self._risk_cool_coeff.setValue(g.risk_cooling_coefficient)
        self._risk_insurance.setValue(g.risk_insurance_premium_rate)
        self._risk_mc_samples.setValue(g.risk_monte_carlo_samples)
        self._risk_mc_seed.setValue(g.risk_monte_carlo_seed)
        self._update_risk_enabled(g.risk_enabled)

        # Visual Scaling
        vs = g.visual_scaling
        self._vs_marker_min.setValue(vs.marker_min_px)
        self._vs_elec_marker.setValue(vs.electrical_marker_scale)
        self._vs_energy_marker.setValue(vs.energy_marker_scale)
        self._vs_fuel_marker.setValue(vs.fuel_marker_scale)
        self._vs_line_min.setValue(vs.line_min_px)
        self._vs_elec_line.setValue(vs.electrical_line_scale)
        self._vs_fuel_line.setValue(vs.fuel_line_scale)

        # Power Flow (data on model.state — single GuiSystemState)
        st = self._model.state
        mode_idx = self._pf_mode.findData(st.power_flow_mode)
        self._pf_mode.setCurrentIndex(max(mode_idx, 0))
        ac = st.ac_power_flow
        self._ac_base_mva.setValue(ac.base_mva)
        self._ac_v_min.setValue(ac.voltage_min_pu)
        self._ac_v_max.setValue(ac.voltage_max_pu)
        self._ac_default_pf.setValue(ac.default_power_factor)
        self._ac_load_pf.setValue(ac.load_power_factor)
        self._ac_q_penalty.setValue(ac.q_slack_penalty)
        self._ac_min_x.setValue(ac.min_reactance_pu)
        self._ac_tap_min.setValue(ac.tap_ratio_min)
        self._ac_tap_max.setValue(ac.tap_ratio_max)
        self._ac_q_min_ratio.setValue(ac.q_min_ratio)

        self._updating = False
        self._update_ac_visibility()
        self._refresh_solver_compat()

    def _on_n1_scopf_toggled(self, checked: bool):
        """Enable/disable SCOPF iteration fields and trigger change."""
        self._n1_scopf_max_iter.setEnabled(checked)
        self._n1_scopf_tol.setEnabled(checked)
        self._on_changed()

    def _on_tsam_toggled(self, checked: bool):
        """Enable/disable TSAM-specific fields and trigger change."""
        self._update_tsam_enabled(checked)
        self._on_changed()

    def _update_tsam_enabled(self, enabled: bool):
        """Toggle TSAM sub-fields enabled state."""
        self._mp_tsam_num_periods.setEnabled(enabled)
        self._mp_tsam_method.setEnabled(enabled)
        self._mp_tsam_inter_period_linking.setEnabled(enabled)
        # Disable legacy rep-days fields when TSAM is active
        self._mp_rep_days.setEnabled(not enabled)
        self._mp_min_sep.setEnabled(not enabled)

    def _on_mga_toggled(self, checked: bool):
        """Enable/disable MGA-specific fields and trigger change."""
        self._update_mga_enabled(checked)
        self._on_changed()

    def _on_mga_method_changed(self, _idx: int):
        """Re-evaluate which MGA sub-fields are enabled when the method
        changes; ``num_alternatives`` is meaningful only for the
        classical MGA loop, ``objectives`` only for SPORES."""
        self._update_mga_enabled(self._mp_mga_enabled.isChecked())
        self._on_changed()

    def _update_mga_enabled(self, enabled: bool):
        """Toggle MGA / SPORES sub-fields enabled / visible state.

        ``num_alternatives`` lives in the MGA path; ``objectives`` lives
        in the SPORES path. The method-specific knobs are *hidden*
        rather than just greyed out when the other method is selected —
        a disabled field that doesn't apply to the chosen method is
        visual clutter. The shared knobs (slack, threshold, method
        combo) follow the section's master enable flag."""
        method = self._mp_mga_method.currentData() or "mga"
        is_spores = (method == "spores")

        # Universal knobs (apply to both methods when enabled).
        self._mp_mga_method.setEnabled(enabled)
        self._mp_mga_slack.setEnabled(enabled)
        self._mp_mga_threshold.setEnabled(enabled)

        # Method-specific knobs: hide the row entirely (label + widget)
        # for the inactive method. setRowVisible would be cleaner but is
        # Qt 6.4+; we fall back to per-widget hiding via
        # labelForField so older Qt builds keep working.
        form_layout = self._mp_mga_objectives.parent().layout()
        for widget, show in (
            (self._mp_mga_num_alts,    enabled and not is_spores),
            (self._mp_mga_objectives,  enabled and is_spores),
        ):
            widget.setEnabled(show)
            widget.setVisible(show)
            label = form_layout.labelForField(widget)
            if label is not None:
                label.setVisible(show)

    def _on_risk_toggled(self, checked: bool):
        """Enable/disable Risk & Resilience fields and trigger change."""
        self._update_risk_enabled(checked)
        self._on_changed()

    def _update_risk_enabled(self, enabled: bool):
        """Toggle Risk sub-fields enabled state."""
        self._risk_measure.setEnabled(enabled)
        self._risk_cvar_alpha.setEnabled(enabled)
        self._risk_cvar_lambda.setEnabled(enabled)
        self._risk_combination.setEnabled(enabled)
        self._risk_voll_res.setEnabled(enabled)
        self._risk_voll_com.setEnabled(enabled)
        self._risk_voll_ind.setEnabled(enabled)
        self._risk_voll_crit.setEnabled(enabled)
        self._risk_base_temp.setEnabled(enabled)
        self._risk_heat_coeff.setEnabled(enabled)
        self._risk_cool_coeff.setEnabled(enabled)
        self._risk_insurance.setEnabled(enabled)
        self._risk_mc_samples.setEnabled(enabled)
        self._risk_mc_seed.setEnabled(enabled)

    def _refresh_sim_mode_widgets(self):
        """Show UC Hours only in unit_commitment mode; Enable-UC only in dev.

        Called whenever the simulation_mode combo changes AND on
        load_element so the initial state matches the loaded config.
        Use QFormLayout.setRowVisible (Qt 6.4+) so both the field and
        its label hide together.
        """
        mode = self._sim_mode.currentData() or "development"
        is_uc_mode = (mode == "unit_commitment")
        if hasattr(self, "_sl") and self._sl is not None:
            try:
                # Row 1 (after sim_mode at row 0): UC Hours
                self._sl.setRowVisible(self._uc_hours, is_uc_mode)
                # Row 2: Enable UC checkbox
                self._sl.setRowVisible(self._enable_uc_in_dispatch, not is_uc_mode)
            except (AttributeError, TypeError):
                # setRowVisible needs Qt 6.4+; fall back to widget-level
                # visibility (label stays visible but is harmless).
                self._uc_hours.setVisible(is_uc_mode)
                self._enable_uc_in_dispatch.setVisible(not is_uc_mode)

    def _on_changed(self):
        if self._updating:
            return
        self._model.checkpoint()
        g = self._model.global_settings

        # Systems to simulate
        g.systems_to_simulate = [
            name for name, cb in self._system_checks.items() if cb.isChecked()
        ]

        # Simulation
        g.simulation_mode = self._sim_mode.currentData() or "development"
        g.unit_commitment_hours = self._uc_hours.value()
        g.mp_use_uc_in_dispatch = self._enable_uc_in_dispatch.isChecked()
        g.date_start = self._date_start.text()
        g.enable_primary_energy = self._enable_pe.isChecked()
        g.console_log_level = self._console_log_level.currentData() or "basic"

        # Temporal
        g.resolution_hours = self._resolution.value()
        g.rolling_horizon_hours = self._rolling_horizon.value()
        g.overlap_hours = self._overlap.value()
        g.investment_resolution = self._inv_resolution.value()
        g.primary_energy_resolution = self._pe_resolution.value()
        g.use_rolling_horizon = self._use_rolling.isChecked()

        # Solver – store the key, not the display text
        idx = self._solver_name.currentIndex()
        if 0 <= idx < len(self._solver_name_keys):
            g.solver_name = self._solver_name_keys[idx]
        else:
            g.solver_name = self._solver_name.currentText()
        g.solver_threads = self._solver_threads.value()
        g.solver_time_limit = self._solver_time.value()
        g.solver_gap = self._solver_gap.value()
        g.solver_verbose = self._solver_verbose.isChecked()
        g.solver_scale_constraints = self._solver_scale.isChecked()

        # Solver-specific options
        g.solver_specific_options = self._collect_solver_options()

        # N-1
        g.n1_enabled = self._n1_enabled.isChecked()
        g.n1_transmission_enabled = self._n1_trans_enabled.isChecked()
        g.n1_transmission_reserve_factor = self._n1_trans_reserve.value()
        g.n1_critical_line_threshold = self._n1_crit_line.value()
        g.n1_generation_enabled = self._n1_gen_enabled.isChecked()
        g.n1_generation_reserve_type = self._n1_gen_type.currentData() or "largest_unit"
        g.n1_generation_reserve_percentage = self._n1_gen_pct.value()
        g.n1_scopf_enabled = self._n1_scopf_enabled.isChecked()
        g.n1_scopf_max_iterations = self._n1_scopf_max_iter.value()
        g.n1_scopf_violation_tolerance = self._n1_scopf_tol.value()
        g.n1_corrective_enabled = self._n1_corrective.isChecked()
        g.n1_contingency_depth = self._n1_depth.currentData() or "n1"
        g.n1_redistribution_mode = self._n1_redispatch.currentData() or "pro_rata"
        g.n1_pi_screening_threshold = self._n1_pi_threshold.value()
        g.n1_transformer_contingencies = self._n1_transformer_ctg.isChecked()
        g.n1_battery_contingencies = self._n1_battery_ctg.isChecked()

        # Master Problem
        g.mp_stochastic = self._mp_stochastic.isChecked()
        g.mp_representative_days = self._mp_rep_days.value()
        g.mp_min_day_separation = self._mp_min_sep.value()
        g.mp_use_tsam = self._mp_use_tsam.isChecked()
        g.mp_tsam_num_periods = self._mp_tsam_num_periods.value()
        g.mp_tsam_method = self._mp_tsam_method.currentText()
        g.mp_tsam_inter_period_linking = self._mp_tsam_inter_period_linking.isChecked()

        # MGA / SPORES
        g.mp_mga_enabled = self._mp_mga_enabled.isChecked()
        g.mp_mga_method = self._mp_mga_method.currentData() or "mga"
        # Collect checked objectives; preserve list order matching the
        # enum (deterministic across saves / loads).
        g.mp_mga_objectives = [
            self._mp_mga_objectives.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._mp_mga_objectives.count())
            if self._mp_mga_objectives.item(i).checkState()
                == Qt.CheckState.Checked
        ]
        g.mp_mga_num_alternatives = self._mp_mga_num_alts.value()
        g.mp_mga_slack_fraction = self._mp_mga_slack.value()
        g.mp_mga_investment_threshold = self._mp_mga_threshold.value()

        # Risk & Resilience
        g.risk_enabled = self._risk_enabled.isChecked()
        g.risk_measure = self._risk_measure.currentData() or "expected"
        g.risk_cvar_alpha = self._risk_cvar_alpha.value()
        g.risk_cvar_lambda = self._risk_cvar_lambda.value()
        g.risk_combination_method = self._risk_combination.currentData() or "independent"
        g.risk_voll_residential = self._risk_voll_res.value()
        g.risk_voll_commercial = self._risk_voll_com.value()
        g.risk_voll_industrial = self._risk_voll_ind.value()
        g.risk_voll_critical = self._risk_voll_crit.value()
        g.risk_base_temperature = self._risk_base_temp.value()
        g.risk_heating_coefficient = self._risk_heat_coeff.value()
        g.risk_cooling_coefficient = self._risk_cool_coeff.value()
        g.risk_insurance_premium_rate = self._risk_insurance.value()
        g.risk_monte_carlo_samples = self._risk_mc_samples.value()
        g.risk_monte_carlo_seed = self._risk_mc_seed.value()

        # Visual Scaling
        vs = g.visual_scaling
        vs.marker_min_px = self._vs_marker_min.value()
        vs.electrical_marker_scale = self._vs_elec_marker.value()
        vs.energy_marker_scale = self._vs_energy_marker.value()
        vs.fuel_marker_scale = self._vs_fuel_marker.value()
        vs.line_min_px = self._vs_line_min.value()
        vs.electrical_line_scale = self._vs_elec_line.value()
        vs.fuel_line_scale = self._vs_fuel_line.value()

        # Power Flow (stored on model.state — single GuiSystemState)
        st = self._model.state
        mode_data = self._pf_mode.currentData()
        if mode_data:
            st.power_flow_mode = mode_data
        ac = st.ac_power_flow
        ac.base_mva = self._ac_base_mva.value()
        ac.voltage_min_pu = self._ac_v_min.value()
        ac.voltage_max_pu = self._ac_v_max.value()
        ac.default_power_factor = self._ac_default_pf.value()
        ac.load_power_factor = self._ac_load_pf.value()
        ac.q_slack_penalty = self._ac_q_penalty.value()
        ac.min_reactance_pu = self._ac_min_x.value()
        ac.tap_ratio_min = self._ac_tap_min.value()
        ac.tap_ratio_max = self._ac_tap_max.value()
        ac.q_min_ratio = self._ac_q_min_ratio.value()

        self._model.globalSettingsUpdated.emit()
        self.globalSettingsChanged.emit()

    def _on_pf_mode_changed(self):
        """OPF formulation changed — toggle AC-field visibility, re-filter the
        compatible solvers, and persist."""
        if self._updating:
            return
        self._update_ac_visibility()
        self._refresh_solver_compat()
        self._on_changed()

    def _update_ac_visibility(self):
        """Show AC power flow widgets only for AC-OPF formulations."""
        is_ac = self._pf_mode.currentData() not in ("dcopf", None)
        for w in self._ac_widgets:
            w.setVisible(is_ac)
        for lbl in self._ac_labels:
            lbl.setVisible(is_ac)

    def _refresh_solver_compat(self):
        """Grey out solvers incompatible with the selected OPF formulation.

        The runner uses the configured solver verbatim (no internal override),
        so an incompatible choice fails the operational solve. Greying the
        incompatible entries — and switching the selection to a compatible one
        when the current pick becomes invalid — keeps the config solvable.
        """
        from esfex.config.solver import FORMULATION_SOLVERS

        if not hasattr(self, "_solver_name"):
            return
        formulation = self._pf_mode.currentData() or "dcopf"
        compatible = FORMULATION_SOLVERS.get(formulation, set())
        smodel = self._solver_name.model()
        cur = self._solver_name.currentIndex()
        first_ok = -1
        for i in range(self._solver_name.count()):
            key = (self._solver_name_keys[i]
                   if i < len(self._solver_name_keys) else "").lower()
            ok = key in compatible
            item = smodel.item(i) if smodel is not None else None
            if item is not None:
                item.setEnabled(ok)
            if ok and first_ok < 0:
                first_ok = i
        # If the current solver is now incompatible, switch to the first valid
        # one (this cascades into _on_solver_changed → rebuild solver options).
        cur_key = (self._solver_name_keys[cur].lower()
                   if 0 <= cur < len(self._solver_name_keys) else "")
        if cur_key and cur_key not in compatible and first_ok >= 0:
            self._solver_name.setCurrentIndex(first_ok)

    # ── Solver-specific options ──────────────────────────────────────

    def _on_solver_changed(self, index: int):
        """Rebuild solver-specific options when the solver combo changes."""
        if self._updating or index < 0:
            return
        solver_key = self._solver_name_keys[index]
        self._rebuild_solver_options(solver_key)
        self._on_changed()

    def _rebuild_solver_options(self, solver_name: str):
        """Rebuild the solver-specific options group for *solver_name*."""
        from esfex.config.solver import SOLVER_OPTIONS

        # Clear existing widgets
        while self._solver_opts_layout.count():
            item = self._solver_opts_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._solver_opt_widgets.clear()

        # Single source of truth for the active solver — shared with
        # _refresh_solver_option_states so the catalog it consults always
        # matches the widgets actually built here.
        self._current_solver_key = solver_name.lower()

        opts = SOLVER_OPTIONS.get(solver_name.lower(), [])
        if not opts:
            self._solver_opts_group.setVisible(False)
            self._refresh_solver_option_states()
            return

        self._solver_opts_group.setVisible(True)
        for opt in opts:
            key = opt["key"]
            label = opt["label"]

            if opt["type"] == "combo":
                widget = QComboBox()
                widget.addItems([str(c) for c in opt["choices"]])
                default_idx = (
                    opt["choices"].index(opt["default"])
                    if opt["default"] in opt["choices"]
                    else 0
                )
                widget.setCurrentIndex(default_idx)
                widget.currentIndexChanged.connect(self._on_changed)
                # A combo may be a controller for other options' enabled state.
                widget.currentIndexChanged.connect(
                    self._refresh_solver_option_states)
            elif opt["type"] == "float":
                widget = QDoubleSpinBox()
                widget.setRange(opt.get("min", 0), opt.get("max", 1e6))
                widget.setDecimals(opt.get("decimals", 4))
                widget.setValue(opt.get("default", 0))
                widget.editingFinished.connect(self._on_changed)
            elif opt["type"] == "int":
                widget = QSpinBox()
                widget.setRange(opt.get("min", 0), opt.get("max", 2_147_483_647))
                widget.setValue(opt.get("default", 0))
                widget.editingFinished.connect(self._on_changed)
            elif opt["type"] == "bool":
                widget = QCheckBox()
                widget.setChecked(opt.get("default", False))
                widget.toggled.connect(self._on_changed)
            else:
                continue

            self._solver_opt_widgets[key] = widget
            self._solver_opts_layout.addRow(label, widget)

        self._refresh_solver_option_states()

    def _refresh_solver_option_states(self):
        """Enable only the solver options compatible with the current solver
        and LP-method selection; grey out the rest.

        Purely a UX aid — incompatible options are disabled (not removed) and
        their stored values are still serialized. Rules are declarative:
        ``enabled_when`` per option in ``SOLVER_OPTIONS`` and
        ``THREADS_INERT_WHEN`` for the global Threads field.
        """
        from esfex.config.solver import SOLVER_OPTIONS, THREADS_INERT_WHEN

        solver_key = getattr(self, "_current_solver_key", "highs")
        opt_map = {o["key"]: o for o in SOLVER_OPTIONS.get(solver_key, [])}

        def _current_label(ctrl_key: str):
            w = self._solver_opt_widgets.get(ctrl_key)
            return w.currentText() if isinstance(w, QComboBox) else None

        def _set_row_enabled(layout, widget, enabled: bool):
            widget.setEnabled(enabled)
            lbl = layout.labelForField(widget)
            if lbl is not None:
                lbl.setEnabled(enabled)

        # Per-option enabled_when rules.
        for key, widget in self._solver_opt_widgets.items():
            opt = opt_map.get(key)
            if opt is None:
                continue
            rule = opt.get("enabled_when") or {}
            enabled = True
            for ctrl_key, allowed in rule.items():
                cur = _current_label(ctrl_key)
                if cur is not None and cur not in allowed:
                    enabled = False
                    break
            _set_row_enabled(self._solver_opts_layout, widget, enabled)

        # Global Threads field — inert for serial (simplex) LP algorithms.
        threads_enabled = True
        if solver_key in THREADS_INERT_WHEN:
            rule = THREADS_INERT_WHEN[solver_key]
            if not rule:
                threads_enabled = False  # solver has no parallel mode at all
            else:
                for ctrl_key, inert_labels in rule.items():
                    cur = _current_label(ctrl_key)
                    if cur is not None and cur in inert_labels:
                        threads_enabled = False
                        break
        _set_row_enabled(
            self._solver_group_layout, self._solver_threads, threads_enabled)

    def _load_solver_options(self, options: dict):
        """Populate solver-specific widgets from a dict of option values.

        Accepts both the new format (output keys are ``opt["attr"]``, the
        solver's real attribute name) and legacy GUI format (output keys are
        ``opt["key"]``, the GUI's internal name).
        """
        from esfex.config.solver import SOLVER_OPTIONS

        idx = self._solver_name.currentIndex()
        solver_key = self._solver_name_keys[idx] if 0 <= idx < len(self._solver_name_keys) else "highs"
        opts = SOLVER_OPTIONS.get(solver_key, [])
        opt_map = {o["key"]: o for o in opts}

        for key, widget in self._solver_opt_widgets.items():
            opt = opt_map.get(key)
            if opt is None:
                continue
            # Look up the value under either the legacy key or the new attr.
            attr = opt.get("attr", key)
            if attr in options:
                val = options[attr]
            elif key in options:
                val = options[key]
            else:
                continue

            if opt["type"] == "combo" and isinstance(widget, QComboBox):
                # If the option declares a label↔integer mapping, translate
                # the stored integer back to its label for display.
                values = opt.get("values")
                choices = opt.get("choices")
                if values is not None and choices is not None and val in values:
                    label = choices[values.index(val)]
                else:
                    label = str(val)
                idx_v = widget.findText(label)
                if idx_v >= 0:
                    widget.setCurrentIndex(idx_v)
            elif opt["type"] == "float" and isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(val))
            elif opt["type"] == "int" and isinstance(widget, QSpinBox):
                widget.setValue(int(val))
            elif opt["type"] == "bool" and isinstance(widget, QCheckBox):
                widget.setChecked(bool(val))

        # Reflect loaded LP-method selection in the enabled/disabled states.
        self._refresh_solver_option_states()

    def _collect_solver_options(self) -> dict:
        """Collect current solver-specific option values into a dict.

        Output keys use the solver's actual attribute name (``opt["attr"]``)
        rather than the GUI's internal key (``opt["key"]``). This is the name
        the underlying solver expects (e.g. HiGHS expects ``solver``, not
        ``solver_method``).

        For combo options that declare a ``values`` array (label → integer
        mapping, e.g. HiGHS ``simplex_scale_strategy``), the integer value
        is stored — not the label string. The solver rejects string values
        where it expects integers.
        """
        from esfex.config.solver import SOLVER_OPTIONS

        idx = self._solver_name.currentIndex()
        solver_key = self._solver_name_keys[idx] if 0 <= idx < len(self._solver_name_keys) else "highs"
        opts = SOLVER_OPTIONS.get(solver_key, [])
        opt_map = {o["key"]: o for o in opts}

        result = {}
        for key, widget in self._solver_opt_widgets.items():
            opt = opt_map.get(key)
            if opt is None:
                continue
            attr = opt.get("attr", key)  # solver attribute name (output key)
            if opt["type"] == "combo" and isinstance(widget, QComboBox):
                label = widget.currentText()
                # Map label → integer value if a `values` array is declared
                values = opt.get("values")
                choices = opt.get("choices")
                if values is not None and choices is not None and label in choices:
                    result[attr] = values[choices.index(label)]
                else:
                    result[attr] = label
            elif opt["type"] == "float" and isinstance(widget, QDoubleSpinBox):
                result[attr] = widget.value()
            elif opt["type"] == "int" and isinstance(widget, QSpinBox):
                result[attr] = widget.value()
            elif opt["type"] == "bool" and isinstance(widget, QCheckBox):
                result[attr] = widget.isChecked()
        return result
