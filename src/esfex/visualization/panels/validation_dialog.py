"""Dialog for comprehensive network validation and simplification.

Runs validation in a background QThread to keep the UI responsive.
Results are grouped by severity (error / warning / info) and then
by category for easy navigation.  Double-clicking an issue emits
``elementRequested`` so the main window can highlight the element.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from esfex.visualization.data.validation import (
    CATEGORY_ORDER,
    InfrastructureSuggestion,
    SimplificationAction,
    SimplificationConfig,
    TopologySuggestion,
    ValidationIssue,
    _apply_small_gen_absorb,
    apply_infrastructure_simplification,
    apply_topology_suggestion,
    count_validators,
    find_dead_end_buses,
    find_infrastructure_simplifications,
    find_simplifications_for_level,
    preload_demand_data,
    simplify_network,
    validate_state,
)
from esfex.visualization.i18n import tr

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiModel

# Category display labels
_CATEGORY_LABELS: dict[str, str] = {
    "structural": "Structural (nodes, lines)",
    "electrical": "Electrical (buses, equipment references)",
    "demand": "Demand (load data, coverage)",
    "generation": "Generation (adequacy, availability files)",
    "fuel_network": "Fuel Network (supply chain integrity)",
    "connectivity": "Connectivity (isolated components)",
    "topology_audit": "Topology Audit (GUI vs solver agreement)",
    "risk": "Risk & Resilience (hazard exposure, fragility curves)",
}

# Custom data roles for storing element references on tree items
_ROLE_ELEM_TYPE = Qt.ItemDataRole.UserRole
_ROLE_ELEM_ID = Qt.ItemDataRole.UserRole + 1


def _sev_color(severity: str) -> str:
    """Return hex colour for a validation severity level."""
    from esfex.visualization.theme import get_validation_color
    return get_validation_color(severity)


# ------------------------------------------------------------------
# Background worker
# ------------------------------------------------------------------


class _ValidationWorker(QThread):
    """Run validation + dead-end detection + infrastructure analysis in background."""

    progress = Signal(int, int, str)   # step, total_steps, description
    finished = Signal(list, list, list)  # issues, simplification_actions, infra_suggestions

    def __init__(
        self,
        states: dict,
        categories: set[str],
        run_simplification: bool,
        infra_level: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._states = states  # {system_name: GuiSystemState}
        self._categories = categories
        self._run_simplification = run_simplification
        self._infra_level = infra_level  # 0 = skip infrastructure analysis

    def run(self):
        all_issues: list[ValidationIssue] = []
        all_actions: list[SimplificationAction] = []
        all_infra: list = []  # InfrastructureSuggestion | TopologySuggestion

        n_systems = len(self._states)
        n_val_per = count_validators(self._categories) if self._categories else 0
        n_val = n_val_per * n_systems
        extra = ((1 if self._run_simplification else 0) + (1 if self._infra_level else 0)) * n_systems
        total = n_val + extra

        step_offset = 0
        for sys_name, state in self._states.items():
            prefix = f"[{sys_name}] " if n_systems > 1 else ""

            # Phase 1: category validators
            if self._categories:
                def _on_progress(step: int, _total: int, desc: str, _off=step_offset, _pfx=prefix):
                    self.progress.emit(_off + step, total, _pfx + desc)

                issues = validate_state(
                    state,
                    categories=self._categories,
                    progress_callback=_on_progress,
                )
                # Tag issues with system name
                if n_systems > 1:
                    for iss in issues:
                        iss.message = f"[{sys_name}] {iss.message}"
                all_issues.extend(issues)
                step_offset += n_val_per

            # Phase 2: network simplification analysis
            if self._infra_level:
                self.progress.emit(
                    step_offset, total,
                    prefix + f"Analyzing network (Level {self._infra_level})...",
                )
                plan = find_simplifications_for_level(
                    state, level=self._infra_level,
                )
                all_infra.extend(plan.infrastructure_suggestions)
                all_infra.extend(plan.topology_suggestions)
                step_offset += 1

        # Done
        self.progress.emit(total, total, tr("validation.complete_title"))
        self.finished.emit(all_issues, all_actions, all_infra)


# ------------------------------------------------------------------
# Dialog
# ------------------------------------------------------------------


class ValidationDialog(QDialog):
    """Non-modal dialog for comprehensive network validation and simplification.

    Improvements over the initial implementation:
    * Validation runs in a **background QThread** — UI stays responsive.
    * Progress bar **always reaches 100 %** on completion.
    * Results are grouped by **severity → category** for easier navigation.
    * A **summary bar** shows error / warning / info counts.
    * **Double-click** an issue to request element focus in the main window.
    """

    simplificationApplied = Signal()
    dialogClosed = Signal()
    validationFinished = Signal(bool)        # True if no errors
    elementRequested = Signal(str, str)      # element_type, element_id

    def __init__(self, model: GuiModel, parent=None,
                 all_states: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("validation.title"))
        self.setMinimumSize(750, 600)
        self.resize(850, 650)
        self.setModal(False)

        self._model = model
        self._all_states = all_states  # {sys_name: GuiSystemState} or None
        self._issues: list[ValidationIssue] = []
        self._simplification_actions: list[SimplificationAction] = []
        self._infra_suggestions: list = []  # InfrastructureSuggestion | TopologySuggestion
        self._validated = False
        self._worker: _ValidationWorker | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Category checkboxes ──
        cat_group = QGroupBox(tr("validation.title"))
        cat_layout = QVBoxLayout(cat_group)
        self._cat_checks: dict[str, QCheckBox] = {}
        for key in CATEGORY_ORDER:
            label = _CATEGORY_LABELS.get(key, key)
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._cat_checks[key] = cb
            cat_layout.addWidget(cb)
        self._simplify_check = QCheckBox(tr("validation.network_simplification"))
        self._simplify_check.setChecked(True)
        cat_layout.addWidget(self._simplify_check)

        # Network simplification (levels)
        infra_row = QHBoxLayout()
        self._infra_check = QCheckBox("Network reduction (levels)")
        self._infra_check.setChecked(False)
        self._infra_check.setToolTip(
            "Analyze and suggest equipment merges, parallel line "
            "consolidation, bus elimination, and topology reduction."
        )
        infra_row.addWidget(self._infra_check)
        self._combo_infra_level = QComboBox()
        self._combo_infra_level.addItem("L1: Equipment + Parallel Lines", 1)
        self._combo_infra_level.addItem("L2: Radial & Series Bus Elim.", 2)
        self._combo_infra_level.addItem("L3: Intra-Node Bus Collapse", 3)
        self._combo_infra_level.addItem("L4: Full Node Collapse", 4)
        self._combo_infra_level.setToolTip(
            "Simplification level (cumulative):\n"
            "L1 — Equipment aggregation + parallel lines\n"
            "L2 — L1 + radial/series bus elimination\n"
            "L3 — L2 + intra-node voltage collapse\n"
            "L4 — L3 + full node collapse + small gen absorption"
        )
        self._combo_infra_level.setEnabled(False)
        self._infra_check.toggled.connect(self._combo_infra_level.setEnabled)
        infra_row.addWidget(self._combo_infra_level)
        infra_row.addStretch()
        cat_layout.addLayout(infra_row)

        layout.addWidget(cat_group)

        # ── Progress ──
        prog_layout = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        prog_layout.addWidget(self._progress, stretch=1)
        self._progress_label = QLabel("")
        self._progress_label.setMinimumWidth(200)
        prog_layout.addWidget(self._progress_label)
        layout.addLayout(prog_layout)

        # ── Summary ──
        self._summary_label = QLabel()
        bold = QFont()
        bold.setBold(True)
        self._summary_label.setFont(bold)
        self._summary_label.setVisible(False)
        layout.addWidget(self._summary_label)

        # ── Results tree ──
        self._results_tree = QTreeWidget()
        self._results_tree.setHeaderLabels([
            tr("validation.col_issue"),
            tr("validation.col_element"),
        ])
        self._results_tree.setColumnCount(2)
        self._results_tree.header().setStretchLastSection(True)
        self._results_tree.setRootIsDecorated(True)
        self._results_tree.setAlternatingRowColors(True)
        self._results_tree.setFont(QFont("Monospace", 9))
        self._results_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._results_tree, stretch=1)

        # ── Buttons ──
        btn_layout = QHBoxLayout()

        self._run_btn = QPushButton(tr("validation.run_btn"))
        self._run_btn.setDefault(True)
        self._run_btn.setMinimumWidth(140)
        self._run_btn.clicked.connect(self._on_run_validation)
        btn_layout.addWidget(self._run_btn)

        btn_layout.addStretch()

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setEnabled(False)
        self._select_all_btn.setToolTip(
            "Check all simplification suggestions."
        )
        self._select_all_btn.clicked.connect(self._on_select_all_infra)
        btn_layout.addWidget(self._select_all_btn)

        self._auto_fix_btn = QPushButton("Auto-fix errors")
        self._auto_fix_btn.setEnabled(False)
        self._auto_fix_btn.setToolTip(
            "Apply targeted fixes to flagged errors only:\n"
            "  • drop self-loop lines / transformers / converters\n"
            "  • drop elements pointing to deleted buses\n"
            "  • re-anchor stale endpoint refs\n"
            "  • rebuild visual transformer/equipment wire-lines\n"
            "Topology of the rest of the network is preserved."
        )
        self._auto_fix_btn.clicked.connect(self._on_auto_fix)
        btn_layout.addWidget(self._auto_fix_btn)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setToolTip(
            "Apply dead-end cleanup and checked network reductions."
        )
        self._apply_btn.clicked.connect(self._on_apply_all)
        btn_layout.addWidget(self._apply_btn)

        self._close_btn = QPushButton(tr("validation.close_btn"))
        self._close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Run validation (background)
    # ------------------------------------------------------------------

    def _on_run_validation(self):
        """Collect selected categories and start validation worker."""
        selected = {
            key for key, cb in self._cat_checks.items() if cb.isChecked()
        }
        run_simplification = self._simplify_check.isChecked()

        if not selected and not run_simplification:
            QMessageBox.information(
                self, tr("common.warning"),
                tr("validation.select_at_least_one"),
            )
            return

        # Build the states dict for validation
        if self._all_states:
            states_to_validate = dict(self._all_states)
        else:
            # Fallback: validate current system only
            name = getattr(self._model.state, 'name', 'System')
            states_to_validate = {name: self._model.state}

        # Pre-load demand data on main thread (lazy CSV read may mutate state)
        for state in states_to_validate.values():
            preload_demand_data(state)

        infra_level = 0
        if self._infra_check.isChecked():
            infra_level = self._combo_infra_level.currentData() or 1

        # Reset UI
        self._run_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._auto_fix_btn.setEnabled(False)
        self._select_all_btn.setEnabled(False)
        self._results_tree.clear()
        self._issues = []
        self._simplification_actions = []
        self._infra_suggestions = []
        self._summary_label.setVisible(False)
        self._progress.setRange(0, 0)  # indeterminate while starting
        self._progress_label.setText(tr("validation.starting"))

        # Launch worker
        self._worker = _ValidationWorker(
            states_to_validate, selected, run_simplification,
            infra_level=infra_level, parent=self,
        )
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    @Slot(int, int, str)
    def _on_worker_progress(self, step: int, total: int, desc: str):
        self._progress.setRange(0, total)
        self._progress.setValue(step)
        self._progress_label.setText(desc)

    @Slot(list, list, list)
    def _on_worker_finished(self, issues: list, actions: list, infra: list):
        self._issues = issues
        self._simplification_actions = actions
        self._infra_suggestions = infra

        # Each action button is enabled only when its action has
        # something to do:
        #   • Auto-fix: only if validation surfaced issues to repair.
        #   • Apply: only if there are simplification actions or
        #     infrastructure-reduction suggestions to apply.
        #   • Select All: only if there are items to select.
        has_simplifications = len(actions) > 0 or len(infra) > 0
        has_issues = len(issues) > 0
        self._auto_fix_btn.setEnabled(has_issues)
        self._apply_btn.setEnabled(has_simplifications)
        self._select_all_btn.setEnabled(has_simplifications)
        self._populate_results()
        self._update_summary()
        self._validated = True
        self._run_btn.setEnabled(True)
        self.validationFinished.emit(self.validated_ok)

        self._worker = None

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _update_summary(self):
        """Show a one-line summary of validation results."""
        n_err = sum(1 for i in self._issues if i.severity == "error")
        n_warn = sum(1 for i in self._issues if i.severity == "warning")
        n_info = sum(1 for i in self._issues if i.severity == "info")
        n_simp = len(self._simplification_actions)
        n_infra = len(self._infra_suggestions)

        if n_err == 0 and n_warn == 0 and n_info == 0 and n_simp == 0 and n_infra == 0:
            self._summary_label.setText(tr("validation.summary_ok"))
            self._summary_label.setStyleSheet(
                f"color: {_sev_color('info')}; padding: 4px;"
            )
        else:
            parts: list[str] = []
            if n_err:
                parts.append(tr("validation.summary_n_errors", n=n_err))
            if n_warn:
                parts.append(tr("validation.summary_n_warnings", n=n_warn))
            if n_info:
                parts.append(tr("validation.summary_n_info", n=n_info))
            if n_simp:
                parts.append(tr("validation.summary_n_simplifications", n=n_simp))
            if n_infra:
                total_red = sum(
                    getattr(s, "reduction", 0) or getattr(s, "elements_removed", 0)
                    for s in self._infra_suggestions
                )
                parts.append(f"{n_infra} simplification(s) (-{total_red} elements)")
            text = "  |  ".join(parts)
            worst = (
                _sev_color("error") if n_err
                else _sev_color("warning") if n_warn
                else _sev_color("info")
            )
            self._summary_label.setText(text)
            self._summary_label.setStyleSheet(
                f"color: {worst}; padding: 4px;"
            )

        self._summary_label.setVisible(True)

    # ------------------------------------------------------------------
    # Populate results tree
    # ------------------------------------------------------------------

    def _populate_results(self):
        self._results_tree.clear()

        errors = [i for i in self._issues if i.severity == "error"]
        warnings = [i for i in self._issues if i.severity == "warning"]
        infos = [i for i in self._issues if i.severity == "info"]

        if errors:
            self._add_issue_group(
                tr("validation.severity_errors"), errors,
                _sev_color("error"), expanded=True,
            )
        if warnings:
            self._add_issue_group(
                tr("validation.severity_warnings"), warnings,
                _sev_color("warning"), expanded=True,
            )
        if infos:
            self._add_issue_group(
                tr("validation.severity_info"), infos,
                _sev_color("info"), expanded=False,
            )

        if self._simplification_actions:
            self._add_simplification_group()

        if self._infra_suggestions:
            self._add_infrastructure_group()

        if not (errors or warnings or infos or self._simplification_actions
                or self._infra_suggestions):
            ok_item = QTreeWidgetItem(
                self._results_tree,
                [tr("validation.summary_ok"), ""],
            )
            from esfex.visualization.theme import current_theme
            ok_item.setForeground(0, QColor(current_theme().colors.status_success))

        self._results_tree.resizeColumnToContents(0)

    def _add_issue_group(
        self,
        title: str,
        issues: list[ValidationIssue],
        hex_color: str,
        expanded: bool = True,
    ):
        """Add a severity group with sub-groups per category."""
        color = QColor(hex_color)

        # Group issues by category
        by_cat: dict[str, list[ValidationIssue]] = {}
        for issue in issues:
            by_cat.setdefault(issue.category, []).append(issue)

        root = QTreeWidgetItem(
            self._results_tree,
            [f"{title} ({len(issues)})", ""],
        )
        root.setForeground(0, color)
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        root.setExpanded(expanded)

        for cat_name in sorted(by_cat):
            cat_issues = by_cat[cat_name]

            if len(by_cat) > 1:
                # Multiple categories — add sub-group node
                cat_node = QTreeWidgetItem(
                    root, [f"{cat_name} ({len(cat_issues)})", ""],
                )
                cat_node.setForeground(0, color)
                cat_node.setExpanded(expanded)
                parent = cat_node
            else:
                # Single category — attach directly to severity root
                parent = root

            for issue in cat_issues:
                elem = (
                    f"{issue.element_type}:{issue.element_id}"
                    if issue.element_id else ""
                )
                item = QTreeWidgetItem(parent, [issue.message, elem])
                item.setForeground(0, color)
                if issue.element_type and issue.element_id:
                    item.setData(0, _ROLE_ELEM_TYPE, issue.element_type)
                    item.setData(0, _ROLE_ELEM_ID, issue.element_id)

    def _add_simplification_group(self):
        """Add simplification actions to the results tree."""
        color = QColor(_sev_color("simplification"))
        n = len(self._simplification_actions)
        label = tr("validation.severity_simplification")
        root = QTreeWidgetItem(
            self._results_tree,
            [f"{label} ({n})", ""],
        )
        root.setForeground(0, color)
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        root.setExpanded(True)
        for action in self._simplification_actions:
            item = QTreeWidgetItem(root, [
                f"{action.action_type}: {action.reason}",
                action.element_id,
            ])
            item.setForeground(0, color)

    def _add_infrastructure_group(self):
        """Add infrastructure and topology simplification suggestions to the results tree."""
        from collections import OrderedDict

        from esfex.visualization.theme import current_theme
        color = QColor(current_theme().colors.status_info)

        total_count = len(self._infra_suggestions)
        root = QTreeWidgetItem(
            self._results_tree,
            [f"Network Simplification ({total_count} operation(s))", ""],
        )
        root.setForeground(0, color)
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        root.setExpanded(True)

        # Group by category
        _TOPO_LABELS = {
            "parallel_line_merge": "Parallel Line Consolidation",
            "radial_prune": "Radial Branch Pruning",
            "series_eliminate": "Series Bus Elimination (Kron)",
            "voltage_collapse": "Voltage Level Collapse",
            "full_node_collapse": "Full Node Collapse",
            "small_gen_absorb": "Small Generator Absorption",
        }

        # Separate infra and topo suggestions
        infra_items = []
        topo_groups: dict[str, list[tuple[int, object]]] = OrderedDict()

        for i, s in enumerate(self._infra_suggestions):
            if isinstance(s, InfrastructureSuggestion):
                infra_items.append((i, s))
            elif isinstance(s, TopologySuggestion):
                topo_groups.setdefault(s.action_type, []).append((i, s))

        # Equipment merges
        if infra_items:
            cat_item = QTreeWidgetItem(root, ["Equipment Aggregation", ""])
            cat_item.setForeground(0, color)
            cat_item.setExpanded(True)
            for idx, s in infra_items:
                item = QTreeWidgetItem(cat_item, [
                    s.description,
                    f"{s.equipment_type} | {s.total_rated_power:.1f} MW",
                ])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.UserRole + 2, idx)
                item.setForeground(0, color)

        # Topology operations by type
        for action_type, items in topo_groups.items():
            label = _TOPO_LABELS.get(action_type, action_type)
            cat_item = QTreeWidgetItem(root, [label, ""])
            cat_item.setForeground(0, color)
            cat_item.setExpanded(True)
            for idx, ts in items:
                detail = f"L{ts.level} | -{ts.elements_removed} elem"
                item = QTreeWidgetItem(cat_item, [ts.description, detail])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.UserRole + 2, idx)
                item.setForeground(0, color)

    # ------------------------------------------------------------------
    # Element navigation
    # ------------------------------------------------------------------

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int):
        elem_type = item.data(0, _ROLE_ELEM_TYPE)
        elem_id = item.data(0, _ROLE_ELEM_ID)
        if elem_type and elem_id:
            self.elementRequested.emit(str(elem_type), str(elem_id))

    # ------------------------------------------------------------------
    # Apply simplification
    # ------------------------------------------------------------------

    def _on_select_all_infra(self):
        """Check all simplification suggestions in the results tree."""
        def _check_all(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    child.setCheckState(0, Qt.CheckState.Checked)
                _check_all(child)

        for i in range(self._results_tree.topLevelItemCount()):
            item = self._results_tree.topLevelItem(i)
            _check_all(item)

    def _on_auto_fix(self):
        """Apply targeted error fixes (self-loops, dangling refs) to all systems.

        Lightweight alternative to ``_on_apply_all``: does NOT reduce
        topology, only removes strictly-broken elements and rebuilds
        visual wiring. Safe to run repeatedly.
        """
        from esfex.visualization.data.validation import auto_fix_errors

        if self._all_states:
            states_to_fix = dict(self._all_states)
        else:
            active_name = getattr(self._model.state, "name", "System")
            states_to_fix = {active_name: self._model.state}

        original_state = self._model.state
        per_system: list[tuple[str, dict]] = []
        try:
            for sys_name, sys_state in states_to_fix.items():
                self._model.state = sys_state
                counts = auto_fix_errors(sys_state)
                per_system.append((sys_name, counts))
        finally:
            self._model.state = original_state

        # Force a full GUI rebuild on the active system.
        self._model.stateLoaded.emit()

        # Build summary
        lines = []
        grand_total = 0
        for sys_name, c in per_system:
            sys_total = sum(v for k, v in c.items() if k != "wire_lines_rebuilt")
            grand_total += sys_total
            if sys_total or c.get("wire_lines_rebuilt"):
                lines.append(
                    f"{sys_name}: "
                    f"{c['self_loop_lines']}+{c['self_loop_transformers']}+"
                    f"{c['self_loop_converters']} self-loops, "
                    f"{c['dangling_lines']}+{c['dangling_transformers']}+"
                    f"{c['dangling_converters']} dangling edges, "
                    f"{c['dangling_generators']}+{c['dangling_batteries']}+"
                    f"{c['dangling_electrolyzers']} orphan equipment, "
                    f"{c['wire_lines_rebuilt']} wire-lines rebuilt"
                )
        if not lines:
            QMessageBox.information(
                self, "Auto-fix",
                "No structurally-broken elements found.",
            )
        else:
            QMessageBox.information(
                self, "Auto-fix complete",
                f"Removed/repaired {grand_total} element(s) across "
                f"{len(per_system)} system(s):\n\n" + "\n".join(lines)
                + "\n\nRe-run validation to confirm the remaining issues."
            )

        # Clear stale results — counts no longer match state
        self._results_tree.clear()
        self._summary_label.setVisible(False)
        self._issues = []
        self._auto_fix_btn.setEnabled(False)
        self._apply_btn.setEnabled(False)
        self._select_all_btn.setEnabled(False)

    def _on_apply_all(self):
        """Apply fixpoint simplification at the selected level to ALL systems."""
        level = self._combo_infra_level.currentData() or 1

        # Determine which states to simplify
        if self._all_states:
            states_to_apply = dict(self._all_states)
        else:
            active_name = getattr(self._model.state, "name", "System")
            states_to_apply = {active_name: self._model.state}

        reply = QMessageBox.question(
            self, "Apply Simplification",
            f"Apply Level {level} simplification with auto-repair to "
            f"{len(states_to_apply)} system(s)?\n\n"
            "The algorithm will iterate until the network is stable "
            "and structurally valid.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from esfex.visualization.data.validation import (
            SimplificationConfig,
            apply_simplification_level,
        )

        # Save original state to restore after per-system processing
        original_state = self._model.state
        all_logs: list[str] = []
        all_remaining: list = []

        for sys_name, sys_state in states_to_apply.items():
            all_logs.append(f"\n══ {sys_name} ══")
            # Temporarily point model at this system's state
            self._model.state = sys_state
            log, remaining = apply_simplification_level(
                self._model, level, SimplificationConfig(),
            )
            all_logs.extend(log)
            all_remaining.extend(remaining)

        # Restore the originally-active state
        self._model.state = original_state

        # Force full GUI rebuild
        self._model.stateLoaded.emit()

        self._infra_suggestions = []
        self._simplification_actions = []
        self._apply_btn.setEnabled(False)
        self._select_all_btn.setEnabled(False)

        # Refresh the tree to remove applied items
        self._populate_results()
        self._update_summary()

        n_remaining = len(all_remaining)
        if n_remaining:
            detail = "\n".join(all_logs[-15:])
            QMessageBox.warning(
                self, "Simplification Complete — Issues Remain",
                f"{n_remaining} issue(s) remain across "
                f"{len(states_to_apply)} system(s):\n\n{detail}\n\n"
                "Re-run validation to see full details.",
            )
        else:
            QMessageBox.information(
                self, "Simplification Complete",
                f"Simplified {len(states_to_apply)} system(s) successfully.\n\n"
                + "\n".join(all_logs[-10:]),
            )
        self.simplificationApplied.emit()

    # ------------------------------------------------------------------
    # Overrides & properties
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        self.dialogClosed.emit()
        super().closeEvent(event)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self._issues)

    @property
    def validated_ok(self) -> bool:
        return self._validated and not self.has_errors
