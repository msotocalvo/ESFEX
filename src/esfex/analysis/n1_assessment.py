"""Integrated N-1 security assessment combining electrical, frequency, and voltage analysis.

This module provides a unified N-1 security assessment that evaluates
three dimensions of power system security for each contingency:

1. **Electrical** (thermal overloads, load shedding) -- from
   ``ContingencyAnalyzer`` or ``ACContingencyAnalyzer``.
2. **Frequency stability** (ROCOF, nadir) -- from ``FrequencyAnalyzer``.
3. **Voltage violations** -- from AC contingency results when available.

Each contingency receives a composite severity score that enables
ranking across heterogeneous violation types.  The analyzer works
in DC-only mode (frequency and voltage checks disabled) or full-AC
mode depending on what analyzers are supplied.

Severity score composition
--------------------------
- Thermal overload: ``max_overload_pct * 1.0``
- Load shedding: ``(total_load_shed_mw / total_demand) * 100``
- Frequency nadir below limit: ``(nadir_limit - nadir_hz) * 20``
- ROCOF above limit: ``|ROCOF - limit| * 10``
- Voltage deviation: ``|violation_pu| * 100`` per violated bus

References
----------
- ENTSO-E (2017). *Frequency Stability Evaluation Criteria*.
- IEEE Std 1547-2018. *Interconnection and Interoperability of DER*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from esfex.analysis.contingency import ContingencyAnalyzer, ContingencyResult
from esfex.analysis.frequency import FrequencyAnalyzer, FrequencyResponse
from esfex.analysis.ac_contingency import ACContingencyAnalyzer, ACContingencyResult

log = logging.getLogger(__name__)

# ── Default voltage limits (pu) ──
_DEFAULT_V_MIN = 0.95
_DEFAULT_V_MAX = 1.05


@dataclass
class N1SecurityAssessment:
    """Unified N-1 security assessment combining electrical, frequency, and voltage criteria.

    Attributes
    ----------
    element_id : str
        Identifier of the contingency element (generator, line, battery, etc.).
    element_type : str
        Type of contingency element: ``"generator"``, ``"line"``,
        ``"transformer"``, or ``"battery"``.
    description : str
        Human-readable description of the contingency.
    electrical : ContingencyResult
        Full electrical contingency result (thermal flows, load shedding).
    has_thermal_violations : bool
        True if any post-contingency line exceeds its thermal rating.
    has_load_shedding : bool
        True if load shedding was required to balance power.
    max_overload_pct : float
        Maximum line overload as a percentage above rated capacity.
    total_load_shed_mw : float
        Total load shed across all nodes (MW).
    frequency : FrequencyResponse | None
        Frequency response result, or None if not applicable.
    has_frequency_violation : bool
        True if ROCOF or nadir limits are violated.
    rocof_hz_per_s : float
        Rate of Change of Frequency (Hz/s).
    nadir_hz : float
        Frequency nadir (Hz).
    voltage_violations : list[dict]
        List of voltage violation dicts with keys ``bus_id``, ``vm_pu``, ``type``.
    has_voltage_violation : bool
        True if any bus voltage is outside limits.
    worst_voltage_pu : float
        Worst-case voltage magnitude (pu).
    is_secure : bool
        True only if ALL criteria pass (thermal, frequency, voltage).
    binding_constraint : str
        The most binding constraint type: ``"thermal"``, ``"frequency"``,
        ``"voltage"``, or ``"none"``.
    severity_score : float
        Composite score for ranking contingencies (higher = worse).
    """

    element_id: str
    element_type: str
    description: str

    # Electrical assessment
    electrical: ContingencyResult
    has_thermal_violations: bool = False
    has_load_shedding: bool = False
    max_overload_pct: float = 0.0
    total_load_shed_mw: float = 0.0

    # Frequency assessment (only for generator/battery loss)
    frequency: FrequencyResponse | None = None
    has_frequency_violation: bool = False
    rocof_hz_per_s: float = 0.0
    nadir_hz: float = 0.0

    # Voltage assessment (only when AC analysis available)
    voltage_violations: list[dict] = field(default_factory=list)
    has_voltage_violation: bool = False
    worst_voltage_pu: float = 1.0

    # Overall
    is_secure: bool = True
    binding_constraint: str = "none"
    severity_score: float = 0.0


class IntegratedN1Analyzer:
    """Unified N-1 security analyzer combining electrical, frequency, and voltage analysis.

    Orchestrates contingency evaluation across multiple analysis domains
    and produces a ranked list of ``N1SecurityAssessment`` results.

    Parameters
    ----------
    contingency_analyzer : ContingencyAnalyzer | ACContingencyAnalyzer
        DC or AC contingency analyzer for thermal/overload analysis.
    frequency_analyzer : FrequencyAnalyzer | None
        Frequency stability analyzer.  If ``None``, frequency checks
        are skipped for all contingencies.
    ac_bridge : Any | None
        AC power flow bridge (``NativeACBridge`` or ``PandapowerBridge``)
        for voltage analysis.  If ``None``, voltage checks are skipped.
    v_min : float
        Minimum allowable voltage magnitude (pu).
    v_max : float
        Maximum allowable voltage magnitude (pu).
    """

    def __init__(
        self,
        contingency_analyzer: ContingencyAnalyzer | ACContingencyAnalyzer,
        frequency_analyzer: FrequencyAnalyzer | None = None,
        ac_bridge: Any | None = None,
        v_min: float = _DEFAULT_V_MIN,
        v_max: float = _DEFAULT_V_MAX,
    ) -> None:
        self._contingency = contingency_analyzer
        self._frequency = frequency_analyzer
        self._ac_bridge = ac_bridge
        self._v_min = v_min
        self._v_max = v_max
        self._is_ac = isinstance(contingency_analyzer, ACContingencyAnalyzer)

    # ── Main API ──

    def assess_single(
        self,
        snapshot: dict[str, Any],
        element_type: str,
        element_id: str,
    ) -> N1SecurityAssessment:
        """Run full N-1 assessment for a single contingency.

        Runs electrical contingency analysis, then frequency analysis
        (for generator/battery loss only), and optionally AC voltage
        analysis.  Combines all results into a unified assessment with
        a composite severity score.

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
        element_type : str
            Type of element to trip: ``"generator"``, ``"line"``,
            ``"transformer"``, or ``"battery"``.
        element_id : str
            Identifier of the element to trip.

        Returns
        -------
        N1SecurityAssessment
            Unified assessment with severity score and binding constraint.
        """
        # ── 1. Electrical contingency analysis ──
        electrical = self._run_electrical(snapshot, element_type, element_id)

        has_thermal = len(electrical.overloaded_lines) > 0
        has_shed = electrical.total_load_shed_mw > 0
        max_overload = electrical.max_overload_pct
        total_shed = electrical.total_load_shed_mw

        # ── 2. Frequency analysis (generator/battery loss only) ──
        freq_response: FrequencyResponse | None = None
        has_freq_violation = False
        rocof = 0.0
        nadir = 0.0

        if element_type in ("generator", "battery") and self._frequency is not None:
            delta_p = self._get_lost_power(snapshot, element_type, element_id)
            if delta_p > 0:
                freq_response = self._frequency.analyze(snapshot, delta_p)
                has_freq_violation = not freq_response.is_stable or not freq_response.rocof_ok
                rocof = freq_response.rocof_hz_per_s
                nadir = freq_response.nadir_hz

        # ── 3. Voltage analysis (from AC results or bridge) ──
        v_violations: list[dict] = []
        has_v_violation = False
        worst_v = 1.0

        if self._is_ac and isinstance(electrical, ACContingencyResult):
            v_violations = list(electrical.voltage_violations)
            has_v_violation = len(v_violations) > 0
            if v_violations:
                worst_v = self._find_worst_voltage(v_violations)
        elif self._ac_bridge is not None:
            v_violations = self._run_voltage_check(snapshot, element_type, element_id)
            has_v_violation = len(v_violations) > 0
            if v_violations:
                worst_v = self._find_worst_voltage(v_violations)

        # ── 4. Determine binding constraint ──
        if has_thermal or has_shed:
            binding = "thermal"
        elif has_freq_violation:
            binding = "frequency"
        elif has_v_violation:
            binding = "voltage"
        else:
            binding = "none"

        # ── 5. Composite severity score ──
        score = self._compute_severity(
            max_overload, total_shed, snapshot,
            freq_response, v_violations,
        )

        is_secure = not has_thermal and not has_shed and not has_freq_violation and not has_v_violation

        description = electrical.element_description or f"Loss of {element_type} {element_id}"

        return N1SecurityAssessment(
            element_id=element_id,
            element_type=element_type,
            description=description,
            electrical=electrical,
            has_thermal_violations=has_thermal,
            has_load_shedding=has_shed,
            max_overload_pct=max_overload,
            total_load_shed_mw=total_shed,
            frequency=freq_response,
            has_frequency_violation=has_freq_violation,
            rocof_hz_per_s=rocof,
            nadir_hz=nadir,
            voltage_violations=v_violations,
            has_voltage_violation=has_v_violation,
            worst_voltage_pu=worst_v,
            is_secure=is_secure,
            binding_constraint=binding,
            severity_score=round(score, 2),
        )

    def assess_all(
        self,
        snapshot: dict[str, Any],
        max_contingencies: int = 50,
    ) -> list[N1SecurityAssessment]:
        """Run N-1 assessment for all contingencies.

        Builds the contingency list from the electrical analyzer, adds
        battery contingencies for discharging batteries, and evaluates
        each one.  Results are sorted by severity score (worst first).

        Parameters
        ----------
        snapshot : dict
            Operational snapshot from ``SldResultsLoader.get_timestep()``.
        max_contingencies : int
            Maximum number of contingencies to evaluate (highest-impact
            first).  Set to 0 for unlimited.

        Returns
        -------
        list[N1SecurityAssessment]
            Assessments sorted by ``severity_score`` descending.
        """
        contingencies = self._build_contingency_list(snapshot)

        if max_contingencies > 0:
            contingencies = contingencies[:max_contingencies]

        assessments: list[N1SecurityAssessment] = []
        for c in contingencies:
            try:
                assessment = self.assess_single(
                    snapshot, c["type"], c["element_id"],
                )
                assessments.append(assessment)
            except Exception:
                log.exception(
                    "Failed to assess contingency %s %s",
                    c["type"], c["element_id"],
                )

        assessments.sort(key=lambda a: a.severity_score, reverse=True)
        return assessments

    def get_security_summary(
        self, assessments: list[N1SecurityAssessment],
    ) -> dict[str, Any]:
        """Summarize N-1 security status across all assessed contingencies.

        Parameters
        ----------
        assessments : list[N1SecurityAssessment]
            List of assessments from ``assess_all()`` or manual calls.

        Returns
        -------
        dict
            Summary with keys:

            - ``total_contingencies``: number of contingencies assessed
            - ``secure_count``: contingencies passing all criteria
            - ``insecure_count``: contingencies failing at least one criterion
            - ``thermal_violations``: count with thermal overloads or load shedding
            - ``frequency_violations``: count with frequency limit violations
            - ``voltage_violations``: count with voltage limit violations
            - ``worst_contingency``: element_id of worst-scoring contingency
            - ``worst_score``: severity score of the worst contingency
            - ``binding_constraints``: dict mapping constraint type to count
        """
        total = len(assessments)
        secure = sum(1 for a in assessments if a.is_secure)
        insecure = total - secure

        thermal_count = sum(
            1 for a in assessments
            if a.has_thermal_violations or a.has_load_shedding
        )
        freq_count = sum(1 for a in assessments if a.has_frequency_violation)
        volt_count = sum(1 for a in assessments if a.has_voltage_violation)

        binding_counts: dict[str, int] = {}
        for a in assessments:
            b = a.binding_constraint
            binding_counts[b] = binding_counts.get(b, 0) + 1

        worst_id = ""
        worst_score = 0.0
        if assessments:
            worst = max(assessments, key=lambda a: a.severity_score)
            worst_id = worst.element_id
            worst_score = worst.severity_score

        return {
            "total_contingencies": total,
            "secure_count": secure,
            "insecure_count": insecure,
            "thermal_violations": thermal_count,
            "frequency_violations": freq_count,
            "voltage_violations": volt_count,
            "worst_contingency": worst_id,
            "worst_score": worst_score,
            "binding_constraints": binding_counts,
        }

    # ── Private helpers ──

    def _run_electrical(
        self,
        snapshot: dict[str, Any],
        element_type: str,
        element_id: str,
    ) -> ContingencyResult:
        """Dispatch to the appropriate electrical contingency method."""
        if element_type in ("generator", "battery"):
            return self._contingency.analyze_generator_loss(snapshot, element_id)
        elif element_type in ("line", "transformer"):
            return self._contingency.analyze_line_loss(snapshot, element_id)
        else:
            log.warning("Unknown contingency element type: %s", element_type)
            return ContingencyResult(
                contingency_type=element_type,
                element_id=element_id,
                element_description=f"Unknown type {element_type} for {element_id}",
            )

    def _get_lost_power(
        self,
        snapshot: dict[str, Any],
        element_type: str,
        element_id: str,
    ) -> float:
        """Determine the MW lost when an element trips.

        For generators, this is the current output.  For batteries,
        this is the current discharge power (net injection).
        """
        if element_type == "generator":
            return snapshot.get("generators", {}).get(
                element_id, {},
            ).get("output_mw", 0.0)
        elif element_type == "battery":
            bdata = snapshot.get("batteries", {}).get(element_id, {})
            discharge = bdata.get("discharge_mw", 0.0)
            charge = bdata.get("charge_mw", 0.0)
            # Net power injection lost when battery trips
            return max(0.0, discharge - charge)
        return 0.0

    def _run_voltage_check(
        self,
        snapshot: dict[str, Any],
        element_type: str,
        element_id: str,
    ) -> list[dict]:
        """Run AC voltage analysis using the AC bridge for non-AC analyzers.

        Returns a list of voltage violation dicts with keys:
        ``bus_id``, ``vm_pu``, ``type`` (``"under"`` or ``"over"``).
        """
        violations: list[dict] = []
        try:
            pf_result = self._ac_bridge.rerun_power_flow()
            if not pf_result.converged:
                log.debug(
                    "AC PF diverged for voltage check on %s %s",
                    element_type, element_id,
                )
                return violations

            for bus_id, vm in pf_result.bus_vm_pu.items():
                if vm < self._v_min:
                    violations.append({
                        "bus_id": bus_id,
                        "vm_pu": round(vm, 4),
                        "type": "under",
                    })
                elif vm > self._v_max:
                    violations.append({
                        "bus_id": bus_id,
                        "vm_pu": round(vm, 4),
                        "type": "over",
                    })
        except Exception:
            log.debug(
                "Voltage check failed for %s %s", element_type, element_id,
                exc_info=True,
            )
        return violations

    def _find_worst_voltage(self, violations: list[dict]) -> float:
        """Find the worst voltage magnitude from a list of violations."""
        worst = 1.0
        for vv in violations:
            vm = vv.get("vm_pu", 1.0)
            if vv.get("type") == "under":
                worst = min(worst, vm)
            else:
                worst = max(worst, vm)
        return worst

    def _compute_severity(
        self,
        max_overload_pct: float,
        total_load_shed_mw: float,
        snapshot: dict[str, Any],
        freq_response: FrequencyResponse | None,
        voltage_violations: list[dict],
    ) -> float:
        """Compute the composite severity score for ranking.

        The score combines thermal, load-shedding, frequency, and
        voltage contributions so that contingencies with different
        violation types can be compared on a single scale.
        """
        score = 0.0

        # Thermal: max overload percentage (0-100+)
        score += max_overload_pct * 1.0

        # Load shedding: MW shed as percentage of total demand
        total_demand = sum(
            v.get("demand_mw", 0.0)
            for v in snapshot.get("loads", {}).values()
        )
        if total_demand > 0:
            score += (total_load_shed_mw / total_demand) * 100.0

        # Frequency: deviation from limits
        if freq_response is not None and self._frequency is not None:
            nadir_limit = self._frequency.nadir_limit
            rocof_limit = self._frequency.rocof_limit
            if freq_response.nadir_hz < nadir_limit:
                score += (nadir_limit - freq_response.nadir_hz) * 20.0
            if not freq_response.rocof_ok:
                score += abs(freq_response.rocof_hz_per_s - rocof_limit) * 10.0

        # Voltage: deviation from limits per violated bus
        for vv in voltage_violations:
            vm = vv.get("vm_pu", 1.0)
            if vv.get("type") == "under":
                score += (self._v_min - vm) * 100.0
            else:
                score += (vm - self._v_max) * 100.0

        return score

    def _build_contingency_list(
        self, snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build the full contingency list including battery contingencies.

        Extends the electrical analyzer's contingency list with battery
        entries for batteries that are currently discharging.
        """
        contingencies = self._contingency.get_contingency_list(snapshot)

        # Add battery contingencies (discharging batteries act as generation)
        batteries = snapshot.get("batteries", {})
        existing_ids = {c["element_id"] for c in contingencies}

        for bat_id, bdata in batteries.items():
            if bat_id in existing_ids:
                continue
            discharge = bdata.get("discharge_mw", 0.0)
            charge = bdata.get("charge_mw", 0.0)
            net_injection = discharge - charge
            if net_injection > 0.1:
                contingencies.append({
                    "type": "battery",
                    "element_id": bat_id,
                    "description": f"Loss: {bat_id} ({net_injection:.0f} MW discharge)",
                    "impact_mw": net_injection,
                })

        # Re-sort by impact (highest first)
        contingencies.sort(key=lambda c: c["impact_mw"], reverse=True)
        return contingencies
