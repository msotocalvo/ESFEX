"""HDF5 → JSON loader for the interactive dashboard tab.

Mediates between :class:`DashboardView` (which speaks JS/QWebChannel)
and the HDF5 result files. Every method returns plain Python
primitives (dicts of lists, strings, ints, floats — JSON-friendly) so
the bridge layer can serialise with a single ``json.dumps``.

This is intentionally NOT a chart factory: we don't build figures
here. Plotly figures are constructed entirely on the JS side from the
JSON data we feed. That keeps Python concerned with data and JS
concerned with presentation — which is what makes "interactive"
(crossfilter, brush, legend toggling) feasible.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import h5py
import numpy as np

from esfex.visualization.panels.results_charts import (
    _open_scenario,
    _open_summary_results,
    _open_system_config,
    _sorted_scenarios,
    _system_node_range as _system_node_range_root,
    _DATASET_T,
    _GROUP_T,
)

logger = logging.getLogger(__name__)


def _collect_arrays(grp: h5py.Group, key: str) -> list[tuple[str, np.ndarray]]:
    """Return [(name, array), …] for ``grp[key]``.

    Tolerates both layouts ESFEX uses:
      * Single 3D Dataset under the key (legacy).
      * Group of per-element Datasets (current; one Dataset per
        generator name, e.g. ``lcoe/Cuba - Antonio Maceo Thermoelectric``).
    Returns an empty list if the key is missing or is neither.
    """
    if key not in grp:
        return []
    node = grp[key]
    if isinstance(node, _DATASET_T):
        return [(key, node[:])]
    if isinstance(node, _GROUP_T):
        out: list[tuple[str, np.ndarray]] = []
        for child_name in node:
            child = node[child_name]
            if isinstance(child, _DATASET_T):
                out.append((child_name, child[:]))
        return out
    return []


def _collect_arrays_with_fuel(
    grp: h5py.Group, key: str,
) -> list[tuple[str, np.ndarray, Optional[str]]]:
    """Like :func:`_collect_arrays` but also returns ``attrs["fuel"]``.

    The export added per-dataset ``fuel`` attrs in 2026-05; older
    HDF5 files don't carry them. Returns ``None`` for the fuel field
    in that case so callers can fall back to the name-based bucketing.
    """
    if key not in grp:
        return []
    node = grp[key]
    if isinstance(node, _DATASET_T):
        f = node.attrs.get("fuel")
        return [(key, node[:], str(f) if f is not None else None)]
    if isinstance(node, _GROUP_T):
        out: list[tuple[str, np.ndarray, Optional[str]]] = []
        for child_name in node:
            child = node[child_name]
            if isinstance(child, _DATASET_T):
                f = child.attrs.get("fuel")
                out.append((child_name, child[:],
                            str(f) if f is not None else None))
        return out
    return []


def _fmt_compact_usd(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:.1f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    if a >= 1e3:
        return f"${v/1e3:.1f}K"
    return f"${v:.0f}"


def _fmt_co2(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        return f"{v/1e6:.2f} Mt"
    if a >= 1e3:
        return f"{v/1e3:.1f} kt"
    return f"{v:.0f} t"


def _fmt_delta_pct(curr: float, prev: float, *, invert: bool = False) -> dict:
    """Return {text, direction} for a KPI delta annotation.

    ``invert=True`` means the metric is "lower is better" (cost, CO2,
    load shed) so a downward move is rendered as ``up`` (green) for
    the consumer's traffic-light intuition.
    """
    if prev is None or prev == 0:
        return {"text": "", "direction": "flat"}
    raw = (curr - prev) / abs(prev) * 100.0
    rounded = round(raw, 1)
    if rounded == 0:
        return {"text": "→ 0.0%", "direction": "flat"}
    up = rounded > 0
    is_improvement = (not up) if invert else up
    arrow = "↑" if up else "↓"
    direction = "up" if is_improvement else "down"
    return {"text": f"{arrow} {rounded:+.1f}%", "direction": direction}


def _fmt_delta_pp(curr: float, prev: float) -> dict:
    """Delta in percentage points — for ratios already expressed in %.

    Used for RE share so an increase is reported as ``+5.2pp`` rather
    than the misleading ``+8%`` you'd get with relative deltas.
    """
    if prev is None:
        return {"text": "", "direction": "flat"}
    raw_pp = (curr - prev) * 100.0
    rounded = round(raw_pp, 1)
    if rounded == 0:
        return {"text": "→ 0.0pp", "direction": "flat"}
    arrow = "↑" if rounded > 0 else "↓"
    direction = "up" if rounded > 0 else "down"
    return {"text": f"{arrow} {rounded:+.1f}pp", "direction": direction}


# ─────────────────────────────────────────────────────────────────
# Tech color palette
# ─────────────────────────────────────────────────────────────────
#
# Keyed by a normalised technology name (lowercased, alphanum). The
# matplotlib charts already define their own palette in
# ``results_charts._build_tech_color_map``; we use a simpler one here
# so the dashboard remains independent and JS can rely on it without
# extra round-trips. Falls back to plotly's default category when a
# technology isn't matched.
_TECH_COLORS = {
    "solar":      "#f1c40f",
    "pv":         "#f1c40f",
    "wind":       "#3498db",
    "hydro":      "#2980b9",
    "biomass":    "#27ae60",
    "fuel_oil":   "#7f8c8d",
    "fueloil":    "#7f8c8d",
    "oil":        "#7f8c8d",
    "diesel":     "#34495e",
    "gas":        "#e67e22",
    "naturalgas": "#e67e22",
    "coal":       "#1a1a1a",
    "battery":    "#9b59b6",
    "storage":    "#9b59b6",
    "hydrogen":   "#1abc9c",
    "otec":       "#16a085",
    "geothermal": "#c0392b",
    "nuclear":    "#8e44ad",
    "curtailment": "#bdc3c7",
}


def _color_for_tech(name: str) -> Optional[str]:
    key = "".join(c for c in name.lower() if c.isalnum())
    for tech_key, colour in _TECH_COLORS.items():
        if tech_key in key:
            return colour
    return None


# ─────────────────────────────────────────────────────────────────
# Main loader
# ─────────────────────────────────────────────────────────────────


class DashboardLoader:
    """Reads an HDF5 results file and produces JSON-friendly payloads.

    A new instance is created each time the user opens the Results
    dialog (or when they switch the underlying file). Cheap to
    construct; the actual HDF5 reads happen per-method, scoped to the
    fields the JS just asked for.

    The ``base_prefix`` argument is the same prefix the matplotlib
    charts use (e.g. ``"systems/Cuba"`` for per-system view, ``""``
    for the legacy root layout). It selects which ``summary_results``
    and ``detailed_results`` branch to read.
    """

    def __init__(
        self,
        h5_files: dict[str, Path],
        base_prefix_by_system: dict[str, str],
    ):
        # Caller supplies the same maps the existing ResultsDialog
        # already built (via _scan_results). Avoids duplicating the
        # discovery logic; the dashboard is just another consumer.
        self._h5_files = h5_files
        self._base_prefix = base_prefix_by_system
        # Cache of {system: {generator_name: fuel}} read lazily from
        # ``system_configuration``. Lets us bucket the per-generator
        # ``generation`` datasets by their declared fuel instead of
        # guessing from the plant name (which produced a 33-entry
        # legend full of un-matched plant names).
        self._fuel_map_cache: dict[str, dict[str, str]] = {}
        # Cache of {(h5_id, bp, year) → cost dict} for the per-system
        # cost reconstruction. The reconstruction iterates every
        # generator dataset, every node and every hour to recompute
        # operational costs that ESFEX writes only as model-wide
        # scalars; we'd repeat that work for every card and every year
        # in the range without this.
        self._cost_cache: dict[tuple, dict] = {}

    # ── Meta ──────────────────────────────────────────────────────

    def get_meta(self) -> dict:
        """Systems list + year domain for the filter bar bootstrap."""
        systems = list(self._h5_files.keys())
        # Pick the first available system to read year domain from.
        # All systems within one run share the same year list in
        # practice — if not, the user can change the system and the
        # JS-side year range will reset.
        years: list[int] = []
        sim_mode = ""
        if systems:
            first = systems[0]
            years = self._years_for(first)
            sim_mode = self._sim_mode_for(first)
        return {
            "systems": systems,
            "years": years,
            "system_default": systems[0] if systems else None,
            # Lets the JS decide planning vs. UC layout at bootstrap
            # without inspecting the data shape itself.
            "simulation_mode": sim_mode,
        }

    def _sim_mode_for(self, system: str) -> str:
        path = self._h5_files.get(system)
        if path is None or not path.exists():
            return ""
        try:
            with h5py.File(path, "r") as f:
                sm = f.attrs.get("simulation_mode", "")
                if isinstance(sm, bytes):
                    sm = sm.decode()
                return str(sm).strip().lower()
        except Exception:
            return ""

    def _years_for(self, system: str) -> list[int]:
        path = self._h5_files.get(system)
        if path is None or not path.exists():
            return []
        bp = self._base_prefix.get(system, "")
        try:
            with h5py.File(path, "r") as f:
                sg = _open_summary_results(f, bp)
                if sg is not None and "year" in sg:
                    return [int(y) for y in sg["year"][:]]
                if "summary_results" in f and "year" in f["summary_results"]:
                    return [int(y) for y in f["summary_results"]["year"][:]]
        except Exception:
            logger.exception("years lookup failed for system %r", system)
        return []

    # ── Overview payload ──────────────────────────────────────────

    def get_overview(self, system: str, year_range: Optional[tuple[int, int]]) -> dict:
        """Produce the full Overview payload for the JS to render.

        ``year_range`` is the inclusive (min_year, max_year) brushed
        in the trajectory chart; ``None`` means "all years available".
        KPIs are computed for the *last year* of the range (the
        cumulative end-state most useful for an at-a-glance read),
        with the delta computed against the *first year* of the range.

        UC runs follow a separate path (``_get_overview_uc``) — the
        planning layout (multi-year KPIs + trajectory + mix) collapses
        to a single point when the horizon is a few hours / one year, so
        UC gets its own operationally-meaningful payload (hourly LMP,
        hourly dispatch stack, operational KPIs).
        """
        path = self._h5_files.get(system)
        if path is None or not path.exists():
            return {"kpis": {}, "trajectory": None, "mix": None,
                    "mode": "planning"}
        bp = self._base_prefix.get(system, "")
        if self._sim_mode_for(system) == "unit_commitment":
            return self._get_overview_uc(path, bp)

        try:
            with h5py.File(path, "r") as f:
                years_all = self._read_years(f, bp)
                if not years_all:
                    return {"kpis": {}, "trajectory": None, "mix": None}

                years = self._filter_years(years_all, year_range)
                if not years:
                    return {"kpis": {}, "trajectory": None, "mix": None}

                trajectory = self._read_trajectory(f, bp, years_all, years)
                mix        = self._read_mix(f, bp, years_all, years, system)
                kpis       = self._compute_kpis(f, bp, years_all, years, mix=mix)
                # Waterfall: cost composition summed over the brushed
                # window so its total matches the (range-total) Total
                # Cost card.
                cost = self._read_cost_breakdown(f, bp, years)
        except Exception:
            logger.exception("get_overview failed (system=%r, range=%s)",
                             system, year_range)
            return {"kpis": {}, "trajectory": None, "mix": None, "cost": None}

        # Recompute RE% in the trajectory from the same generation data
        # the Mix chart uses. The HDF5's per-system
        # ``summary_results.renewable_penetration`` is unreliable when
        # the solver places investment generators in the global model
        # rather than the per-system one — the per-system summary then
        # under-counts renewable share (we observed a Cuba run where
        # summary said 10% but the actual mix was 57%). Deriving from
        # the mix ensures the Trajectory line and the Mix stack agree
        # by construction, regardless of solver bookkeeping quirks.
        self._patch_re_from_mix(trajectory, mix)

        return {
            "kpis": kpis, "trajectory": trajectory,
            "mix": mix,
            "cost": cost,
            "mode": "planning",
        }

    # ── UC overview payload ────────────────────────────────────
    #
    # A UC run is a single year of hourly data; the planning layout
    # (multi-year trajectory + per-year mix bars + multi-year KPIs)
    # collapses to a single point and is unreadable. The UC dashboard
    # is instead a single-window operational snapshot:
    #
    #   KPIs           — operational metrics for the whole horizon
    #                    (system cost, avg LMP, ENS, RE share,
    #                    battery cycles).
    #   Trajectory →   "Hourly LMP" line for the horizon with a
    #                  hourly load-shed bar overlay (adequacy at a
    #                  glance).
    #   Mix       →    Hourly dispatch stack (per-tech generation +
    #                  battery + curtailment + load-shed) — same
    #                  visualisation as UCDispatchStackChart, scaled
    #                  to dashboard size.
    #   Cost      —    Cost breakdown of the single year, reused
    #                  from the planning path (works for UC too —
    #                  it sums the operational year).
    def _get_overview_uc(self, path: Path, bp: str) -> dict:
        try:
            with h5py.File(path, "r") as f:
                scenarios = list(_sorted_scenarios(f, bp))
                if not scenarios:
                    return self._empty_uc_overview(reason="No scenarios")
                # UC writes a single operational year; use it.
                sc_key, year = scenarios[-1]
                sc = _open_scenario(f, bp, sc_key)

                hourly = self._read_uc_hourly(f, bp, sc)
                kpis = self._uc_kpis(f, bp, sc, hourly, year)
                cost = self._read_cost_breakdown(f, bp, [int(year)])
        except Exception:
            logger.exception("UC overview failed (system path=%s)", path)
            return self._empty_uc_overview(reason="Read error")

        return {
            "mode": "uc",
            "year": int(year),
            "kpis": kpis,
            # Reuse the dashboard's trajectory/mix slots — same keys,
            # different semantics. The JS branches on ``mode`` to render
            # them as hourly time series instead of multi-year curves.
            "trajectory": hourly.get("trajectory"),
            "mix": hourly.get("mix"),
            "cost": cost,
        }

    @staticmethod
    def _empty_uc_overview(reason: str = "") -> dict:
        return {
            "mode": "uc", "year": None, "kpis": {},
            "trajectory": None, "mix": None, "cost": None,
            "reason": reason,
        }

    def _read_uc_hourly(self, f: h5py.File, bp: str,
                        sc) -> dict:
        """Read the hourly series the UC dashboard needs: system-avg
        LMP, hourly demand / RE / thermal / battery / curtailment /
        load-shed. Returns the trajectory + mix payloads JS expects."""
        import numpy as _np
        rng = None
        try:
            from esfex.visualization.panels.results_charts import (
                _system_node_range as _sys_node_range,
            )
            rng = _sys_node_range(f, bp)
        except Exception:
            pass
        tres = int(f.attrs.get("temporal_resolution_hours", 1))

        # LMPs (system average + hourly load shed for the trajectory
        # overlay). The slicing proxy already returns per-system
        # averages for ``electricity_prices``.
        ep = sc["electricity_prices"][:] if "electricity_prices" in sc else _np.zeros(0)
        n_hours = int(ep.size)

        def _hourly_sum(key: str) -> _np.ndarray:
            if key not in sc:
                return _np.zeros(n_hours or 0)
            arr = sc[key][:]
            if arr.ndim < 2:
                return arr.astype(float)
            if rng is not None:
                lo, hi = rng
                arr = arr[lo:hi]
            s = arr.sum(axis=0).astype(float)
            if n_hours and len(s) < n_hours:
                s = _np.pad(s, (0, n_hours - len(s)))
            return s[:n_hours] if n_hours else s

        demand = _hourly_sum("demand")
        load_shed = _hourly_sum("loss_load")
        curtailment = _hourly_sum("curtailment")

        # Tech-bucketed generation. Reuse the planning mix's tech
        # canonicalisation so colors are consistent with the rest of
        # the GUI.
        try:
            from esfex.visualization.panels.results_charts import (
                _load_gen_data,
                _load_gen_configs,
                _tech_bucket_for_gen,
                get_generation_colors,
                get_generation_default_color,
            )
        except Exception:
            _load_gen_data = None
        tech_series: dict[str, _np.ndarray] = {}
        if _load_gen_data is not None:
            cfgs = _load_gen_configs(f, bp)
            gen_data = _load_gen_data(sc)
            for gi, (gname, arr) in enumerate(gen_data.items()):
                if arr.ndim < 2:
                    continue
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                hourly = arr.sum(axis=0).astype(float)
                if n_hours and len(hourly) < n_hours:
                    hourly = _np.pad(hourly, (0, n_hours - len(hourly)))
                hourly = hourly[:n_hours] if n_hours else hourly
                cfg = cfgs[gi] if gi < len(cfgs) else {}
                tech = _tech_bucket_for_gen(cfg, gname)
                if tech in tech_series:
                    tech_series[tech] = tech_series[tech] + hourly
                else:
                    tech_series[tech] = hourly.copy()

        # Battery charge / discharge as system totals.
        try:
            from esfex.visualization.panels.results_charts import _load_bat_data
            charge_d = _load_bat_data(sc, "battery_charge")
            discharge_d = _load_bat_data(sc, "battery_discharge")
        except Exception:
            charge_d, discharge_d = {}, {}
        def _bat_total(d):
            tot = _np.zeros(n_hours or 0)
            for arr in d.values():
                if arr.ndim < 2:
                    continue
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                s = arr.sum(axis=0).astype(float)
                if n_hours and len(s) < n_hours:
                    s = _np.pad(s, (0, n_hours - len(s)))
                tot += s[:n_hours] if n_hours else s
            return tot
        bat_charge = _bat_total(charge_d)
        bat_discharge = _bat_total(discharge_d)

        # Theme palette for the dashboard mix stack (same lookup as
        # the planning mix uses).
        try:
            palette = get_generation_colors()
            default_color = get_generation_default_color()
        except Exception:
            palette, default_color = {}, "#95A5A6"
        def _color_for(label: str) -> str:
            ll = label.lower()
            for key, clr in palette.items():
                if key.lower() in ll:
                    return clr
            return default_color

        trajectory = {
            "hours": list(range(n_hours)),
            "lmp": [float(v) for v in ep],
            "load_shed_mw": [float(v) for v in load_shed],
            "demand_mw": [float(v) for v in demand],
        }

        series = []
        for label, vals in sorted(
            tech_series.items(), key=lambda kv: -float(_np.mean(kv[1]))
        ):
            series.append({
                "label": label,
                "color": _color_for(label),
                "values": [float(v) for v in vals],
            })
        if bat_discharge.size and bat_discharge.any():
            series.append({
                "label": "Battery (discharge)",
                "color": "rgba(155, 89, 182, 0.85)",
                "values": [float(v) for v in bat_discharge],
            })
        # Charge + curtailment shown as negative in the JS stack.
        series_neg = []
        if bat_charge.size and bat_charge.any():
            series_neg.append({
                "label": "Battery (charge)",
                "color": "rgba(155, 89, 182, 0.45)",
                "values": [float(v) for v in bat_charge],
            })
        if curtailment.size and curtailment.any():
            series_neg.append({
                "label": "Curtailment",
                "color": "rgba(241, 196, 15, 0.7)",
                "values": [float(v) for v in curtailment],
            })
        if load_shed.size and load_shed.any():
            series.append({
                "label": "Load shed",
                "color": "rgba(231, 76, 60, 0.85)",
                "values": [float(v) for v in load_shed],
            })

        mix = {
            "hours": list(range(n_hours)),
            "series_pos": series,
            "series_neg": series_neg,
            "demand_mw": [float(v) for v in demand],
            "tres": tres,
        }
        return {"trajectory": trajectory, "mix": mix}

    def _uc_kpis(self, f: h5py.File, bp: str, sc, hourly: dict,
                 year: int) -> dict:
        """Operational KPIs for the UC dashboard.

        Card semantics — every KPI describes the whole UC horizon:
          • cost          — Σ total_cost over the horizon (USD)
          • avg_lmp       — energy-weighted average LMP (USD/MWh)
          • ens           — total unserved energy (MWh) + % of demand
          • re_share      — Σ renewable gen / Σ all gen (energy share)
          • battery_cycles — equivalent full cycles across all batteries
        """
        import numpy as _np

        trj = hourly.get("trajectory") or {}
        mix = hourly.get("mix") or {}
        tres = int(mix.get("tres", 1))
        lmp = _np.asarray(trj.get("lmp", []), dtype=float)
        demand = _np.asarray(trj.get("demand_mw", []), dtype=float)
        load_shed = _np.asarray(trj.get("load_shed_mw", []), dtype=float)
        series_pos = mix.get("series_pos", [])

        # System cost: sum the operational year scalar. The cost
        # breakdown values come from the same path the cost waterfall
        # uses, so cards and waterfall stay consistent.
        try:
            comp = self._per_system_cost(f, bp, int(year))
        except Exception:
            comp = None
        cost_v = comp["total"] if comp else None

        # Energy-weighted avg LMP: ignore hours with no demand to avoid
        # the VOLL-dominated all-hours average mis-reading. If the run
        # is fully at VOLL the value still reflects that.
        if demand.size and lmp.size and demand.sum() > 0:
            n = min(len(demand), len(lmp))
            avg_lmp = float((demand[:n] * lmp[:n]).sum() / demand[:n].sum())
        else:
            avg_lmp = None

        # Unserved energy & demand share.
        ens_mwh = float(load_shed.sum() * tres)
        dem_mwh = float(demand.sum() * tres)
        ens_pct = (ens_mwh / dem_mwh * 100.0) if dem_mwh > 0 else 0.0

        # RE share (energy-weighted). Use the same RE label set as the
        # planning mix.
        re_gen = total_gen = 0.0
        for s in series_pos:
            v = _np.asarray(s.get("values", []), dtype=float).sum() * tres
            total_gen += v
            if s.get("label") in self._RENEWABLE_MIX_LABELS:
                re_gen += v
        re_share = (re_gen / total_gen) if total_gen > 0 else None

        # Battery cycles (equivalent full): Σ discharge MWh / capacity.
        # The capacity we recover from the SOC dataset's peak (== full
        # MWh value the run reached at any node). For lack of a better
        # signal in a single year, this is the standard ex-post proxy.
        try:
            from esfex.visualization.panels.results_charts import _load_bat_data
            soc_d = _load_bat_data(sc, "battery_soc")
            dis_d = _load_bat_data(sc, "battery_discharge")
        except Exception:
            soc_d, dis_d = {}, {}
        cap_mwh = 0.0
        for arr in soc_d.values():
            if arr.size:
                cap_mwh += float(_np.nan_to_num(arr).max())
        dis_mwh = 0.0
        for arr in dis_d.values():
            if arr.size:
                dis_mwh += float(arr.sum()) * tres
        cycles = (dis_mwh / cap_mwh) if cap_mwh > 0 else 0.0

        def _card(value_text, delta_text=None,
                  direction="flat"):
            return {
                "value": value_text,
                "delta": {"text": delta_text or "", "direction": direction},
            }

        kpis = {
            "cost": _card(
                _fmt_compact_usd(cost_v) if cost_v is not None else None,
                "Operational cost (horizon)"),
            "avg_lmp": _card(
                f"{avg_lmp:,.1f} USD/MWh" if avg_lmp is not None else None,
                "Demand-weighted average"),
            "ens": _card(
                f"{ens_mwh:,.0f} MWh ({ens_pct:.1f}% demand)"
                if dem_mwh > 0 else None,
                "Unserved energy"),
            "re_share": _card(
                f"{re_share * 100:.1f}%" if re_share is not None else None,
                "Renewable share of generation"),
            "battery_cycles": _card(
                f"{cycles:.2f} eq. full cycles" if cap_mwh > 0 else "No batteries",
                "Throughput / capacity"),
        }
        return kpis

    # Mix-series labels that count as renewable for the recomputed
    # RE% in the trajectory. Matches the labels _canonical_tech can
    # emit. "Other" intentionally excluded — keyword fallback assigns
    # any plant whose name doesn't match a known tech to "Other",
    # which is safer to classify as non-renewable than to assume.
    _RENEWABLE_MIX_LABELS = frozenset({
        "Wind", "Solar", "Hydro", "Biomass", "OTEC",
        "Geothermal", "Nuclear", "Hydrogen",
    })

    @classmethod
    def _patch_re_from_mix(cls, trajectory: Optional[dict],
                           mix: Optional[dict]) -> None:
        """Overwrite trajectory.re_pct with values derived from mix.

        ``mix["series"]`` is a list of ``{label, values, color}`` where
        ``values`` is a per-year list aligned with ``mix["years"]``.
        We sum renewable-label series and divide by the total of all
        series for each year. Skips silently when shapes don't line up
        (defensive — the caller falls back to the summary_results value
        that ``_read_trajectory`` already populated).
        """
        if trajectory is None or mix is None:
            return
        series = mix.get("series") or []
        mix_years = mix.get("years") or []
        traj_years = trajectory.get("years") or []
        if not series or not mix_years or mix_years != traj_years:
            return
        n = len(traj_years)
        re_total = [0.0] * n
        all_total = [0.0] * n
        for s in series:
            vals = s.get("values") or []
            if len(vals) != n:
                continue
            is_re = s.get("label") in cls._RENEWABLE_MIX_LABELS
            for i, v in enumerate(vals):
                if v is None:
                    continue
                all_total[i] += float(v)
                if is_re:
                    re_total[i] += float(v)
        trajectory["re_pct"] = [
            (re_total[i] / all_total[i] * 100.0) if all_total[i] > 0 else None
            for i in range(n)
        ]

    # ── HDF5 helpers ──────────────────────────────────────────────

    @staticmethod
    def _read_years(f: h5py.File, bp: str) -> list[int]:
        sg = _open_summary_results(f, bp)
        if sg is not None and "year" in sg:
            return [int(y) for y in sg["year"][:]]
        if "summary_results" in f and "year" in f["summary_results"]:
            return [int(y) for y in f["summary_results"]["year"][:]]
        return []

    @staticmethod
    def _filter_years(years: list[int], rng: Optional[tuple[int, int]]) -> list[int]:
        if rng is None:
            return list(years)
        lo, hi = rng
        return [y for y in years if lo <= y <= hi]

    def _read_trajectory(
        self, f: h5py.File, bp: str,
        years_all: list[int], years_view: list[int],
    ) -> dict:
        """Three aligned series over years_view: cost, RE%, CO2 Mt.

        We read against years_all (which carries the dataset's natural
        ordering) and then project onto years_view for the user's
        selection. This keeps the per-year lookups O(1) by index even
        when the file's year ordering isn't sorted.
        """
        sr_self = self._summary_group(f, bp)
        sr_root = f.get("summary_results")
        idx_by_year = {y: i for i, y in enumerate(years_all)}

        def _vec(name: str, *, prefer_root: bool = False):
            grps = (sr_root, sr_self) if prefer_root else (sr_self, sr_root)
            for grp in grps:
                if grp is None or name not in grp:
                    continue
                arr = grp[name]
                if not isinstance(arr, _DATASET_T):
                    continue
                values = arr[:]
                return [
                    float(values[idx_by_year[y]]) if y in idx_by_year and
                    idx_by_year[y] < len(values) else None
                    for y in years_view
                ]
            return [None] * len(years_view)

        # Read per-system first so each subsystem's trajectory shows
        # its own cost curve; fall back to the root only when the
        # per-system summary is absent.
        cost = _vec("total_cost", prefer_root=False)
        re_pen = _vec("renewable_penetration")
        co2 = _vec("co2_emissions")

        return {
            "years": list(years_view),
            # Scale for readability: cost in M$, CO2 in Mt.
            "cost_musd": [None if v is None else v / 1e6 for v in cost],
            "re_pct":    [None if v is None else v * 100.0 for v in re_pen],
            "co2_mt":    [None if v is None else v / 1e6 for v in co2],
        }

    def _read_mix(
        self, f: h5py.File, bp: str,
        years_all: list[int], years_view: list[int],
        system: str = "",
    ) -> dict:
        """Stacked annual generation by technology.

        For each year in the selected range, we open the corresponding
        ``detailed_results/year_{Y}_threshold_0/generation`` group (one
        Dataset per generator), bucket each generator under a canonical
        tech label (matching the dashboard palette), and produce a
        per-tech list of annual GWh.
        """
        fuel_map = self._fuel_map_for(f, system)
        per_tech: dict[str, list[float]] = {}
        for y in years_view:
            sc_key = f"year_{y}_threshold_0"
            try:
                yrg = _open_scenario(f, bp, sc_key)
            except KeyError:
                # Pad every existing tech with None for this year.
                for series in per_tech.values():
                    series.append(0.0)
                continue
            year_totals: dict[str, float] = {}
            # Per-dataset ``attrs["fuel"]`` is the authoritative source
            # added by the export (2026-05+). When present, bucketing
            # is just ``fuel → tech label``; when absent (legacy h5),
            # _canonical_tech falls back to name matching.
            for gen_name, arr, fuel_attr in _collect_arrays_with_fuel(yrg, "generation"):
                if fuel_attr:
                    tech = self._tech_from_fuel(fuel_attr)
                else:
                    tech = self._canonical_tech(gen_name, fuel_map, system)
                # arr shape is [nodes, hours] — sum over both axes for
                # annual generation in MWh; convert to GWh.
                total_gwh = float(np.nansum(arr)) / 1000.0
                year_totals[tech] = year_totals.get(tech, 0.0) + total_gwh
            # Append per-tech, padding missing techs with 0 so all
            # series stay aligned in length.
            seen = set(year_totals.keys()) | set(per_tech.keys())
            for tech in seen:
                per_tech.setdefault(tech, [0.0] * (len(per_tech.get(tech, [])) or 0))
                # ensure series has trailing zeros up to this year-1
                while len(per_tech[tech]) < years_view.index(y):
                    per_tech[tech].append(0.0)
                per_tech[tech].append(year_totals.get(tech, 0.0))

        # Sort series by mean magnitude descending so the largest
        # contributors land at the bottom of the stack (more readable).
        series = []
        for tech, values in sorted(
            per_tech.items(),
            key=lambda kv: -float(np.mean(kv[1])) if kv[1] else 0.0,
        ):
            series.append({
                "label": tech,
                "values": values,
                "color": _color_for_tech(tech),
            })

        return {
            "years": list(years_view),
            "series": series,
        }

    # Cost-breakdown component → display label + sign convention.
    # ``benefit`` flag marks components that *reduce* the objective
    # (revenue / avoided cost) — they're stored positive in the HDF5
    # but should subtract in the waterfall.
    _COST_COMPONENTS = [
        ("fuel_cost",                "Fuel"),
        ("co2_emission_cost",        "CO₂ emissions"),
        ("fixed_om_cost",            "Fixed O&M"),
        ("maintenance_cost",         "Maintenance"),
        ("startup_cost",             "Start-up"),
        ("battery_maintenance_cost", "Battery O&M"),
        ("battery_degradation_cost", "Battery degradation"),
        ("investment_cost",          "Investment (CAPEX)"),
        ("electrolyzer_cost",        "Electrolyzer"),
        ("converter_cost",           "Converter"),
        ("reservoir_invest_cost",    "Reservoir CAPEX"),
        ("load_shedding_cost",       "Load shedding"),
        ("curtailment_cost",         "Curtailment"),
        ("rooftop_curtailment_cost", "Rooftop curtailment"),
        ("spillage_cost",            "Spillage"),
        ("reservoir_spillage_cost",  "Reservoir spillage"),
        ("reserve_static_cost",      "Static reserve"),
        ("reserve_dynamic_cost",     "Dynamic reserve"),
        ("inertia_cost",             "Inertia"),
        ("soc_violation_cost",       "SOC violation"),
        ("transfer_margin_cost",     "Transfer margin"),
        ("fre_penetration_cost",     "RE-penetration penalty"),
        ("npv_penalty_cost",         "NPV penalty"),
        ("delay_retirement_cost",    "Delayed retirement"),
        ("demand_shift_cost",        "Demand shift"),
        ("v2g_compensation",         "V2G compensation"),
    ]

    def _read_cost_breakdown(self, f: h5py.File, bp: str,
                             years: list[int]) -> Optional[dict]:
        """Cost composition summed over ``years``, as a waterfall payload.

        Two paths:
        1. **Per-system reconstruction** — preferred when ``bp`` selects
           a subsystem. We rebuild fuel / fixed_om / maintenance /
           startup / battery_maintenance / investment from the per-node
           datasets × per-node config coefficients, so each subsystem's
           waterfall reflects its own activity.
        2. **Legacy ``cost_breakdown/year_X/attrs``** — model-wide
           scalars written by the Julia solver. Used when (a) ``bp`` is
           empty (single-system run or Global pseudo-system) or (b) the
           reconstruction returned nothing for every year in range
           (e.g. legacy HDF5 with no system_configuration block).

        Components are summed across the range so the waterfall total
        matches the (range-total) Total Cost card. Returns ``None``
        when neither path produced data for any year in the range.
        """
        # ── Path 1: per-system reconstruction ─────────────────────
        comp_sum_recon: dict[str, float] = {}
        recon_total = 0.0
        recon_found = False
        if bp:
            for year in years:
                comp = self._per_system_cost(f, bp, year)
                if comp is None:
                    continue
                recon_found = True
                for k, v in comp.items():
                    if k == "total":
                        recon_total += float(v)
                    else:
                        comp_sum_recon[k] = comp_sum_recon.get(k, 0.0) + float(v)
            if recon_found:
                # Translate component keys to the same human labels the
                # legacy path produces so the JS waterfall renders the
                # same legend regardless of which path provided the data.
                label_by_key = dict(self._COST_COMPONENTS)
                steps = [
                    {"label": label_by_key.get(k, k), "value": v}
                    for k, v in comp_sum_recon.items() if abs(v) >= 1.0
                ]
                if steps:
                    steps.sort(key=lambda s: -abs(s["value"]))
                    return {
                        "years": [int(y) for y in years],
                        "steps": steps,
                        "total": recon_total,
                    }

        # ── Path 2: model-wide scalar attrs (fallback) ────────────
        comp_sum: dict[str, float] = {}
        total_sum = 0.0
        have_total = False
        found = False
        prefix = bp.rstrip("/") + "/" if bp else ""
        candidate_prefixes = (
            [prefix, "", "global/"] if prefix else ["", "global/"]
        )
        for year in years:
            grp_key = None
            for pfx in candidate_prefixes:
                trial = f"{pfx}cost_breakdown/year_{year}"
                if trial in f:
                    grp_key = trial
                    break
            if grp_key is None:
                continue
            attrs = f[grp_key].attrs
            found = True
            for key, _label in self._COST_COMPONENTS:
                if key not in attrs:
                    continue
                try:
                    comp_sum[key] = comp_sum.get(key, 0.0) + float(attrs[key])
                except (TypeError, ValueError):
                    continue
            if "total" in attrs:
                try:
                    total_sum += float(attrs["total"])
                    have_total = True
                except (TypeError, ValueError):
                    pass
        if not found:
            return None

        label_by_key = dict(self._COST_COMPONENTS)
        steps = [
            {"label": label_by_key[k], "value": v}
            for k, v in comp_sum.items() if abs(v) >= 1.0
        ]
        if not steps:
            return None
        steps.sort(key=lambda s: -abs(s["value"]))

        total = total_sum if have_total else sum(s["value"] for s in steps)
        return {"years": [int(y) for y in years], "steps": steps, "total": total}

    def _detailed_total(self, f: h5py.File, bp: str, years_view: list[int],
                        dataset_key: str) -> Optional[float]:
        """Sum a per-node-per-hour dataset over the range, × temporal
        resolution → physical total. Used for CO₂ and loss-of-load, which
        are unreliable in ``summary_results`` (per-system bookkeeping
        undercounts) but exact in ``detailed_results``."""
        tres = int(f.attrs.get("temporal_resolution_hours", 1))
        total, found = 0.0, False
        for y in years_view:
            sc_key = f"year_{y}_threshold_0"
            try:
                grp = _open_scenario(f, bp, sc_key)
            except KeyError:
                continue
            if dataset_key in grp and isinstance(grp[dataset_key], _DATASET_T):
                total += float(np.nansum(grp[dataset_key][:])) * tres
                found = True
        return total if found else None

    def _re_share_from_mix(self, mix: Optional[dict],
                           years_view: list[int]) -> Optional[float]:
        """Peak (max) annual renewable share over ``years_view`` derived
        from the generation mix (reliable), not from the unreliable
        ``summary_results.renewable_penetration``."""
        if not mix:
            return None
        myears = mix.get("years") or []
        idx = [i for i, y in enumerate(myears) if y in years_view]
        if not idx:
            return None
        series = mix.get("series", [])
        best: Optional[float] = None
        for i in idx:
            ren = tot = 0.0
            for s in series:
                vals = s.get("values") or []
                if i >= len(vals):
                    continue
                v = vals[i]
                tot += v
                if s.get("label") in self._RENEWABLE_MIX_LABELS:
                    ren += v
            if tot > 0:
                share = ren / tot
                if best is None or share > best:
                    best = share
        return best

    # ── Per-system cost reconstruction ────────────────────────────
    #
    # ``summary_results/total_cost`` and the ``cost_breakdown/year_X``
    # attrs are model-wide scalars: the same numbers regardless of which
    # subsystem the user picks. That's by design in the Julia
    # ``CostBreakdown`` struct (``types.jl:1429``) — every component is a
    # ``Float64``, not a vector. To answer "what does Cuba cost vs
    # IslaJuventud", we re-compute the operational components in Python
    # by walking the per-node-per-hour datasets the solver already
    # exports and weighting them with the per-node cost coefficients
    # from ``system_configuration``. This reproduces what
    # ``build_objective!`` did, restricted to the system's node range.

    @staticmethod
    def _attr_at_node(cfg: dict, key: str, node: int,
                      default: float = 0.0) -> float:
        """Resolve a (possibly per-node) config attribute to a scalar
        at ``node``. Handles three storage shapes:
        - native float/int (single-node config)
        - numpy array / list (vector per node)
        - string ``"[40.0, 35.0, ...]"`` produced by ``runner.py:5894``
          when Pydantic dumped a ``list[float]`` and the runner serialised
          it via ``str(v)``.

        Out-of-range node indices fall back to the last available value
        (heuristic: the solver replicates the last per-node coefficient
        for "extra" nodes when the config is shorter than ``num_nodes``).
        """
        v = cfg.get(key, default)
        if v is None:
            return default
        if isinstance(v, (int, float, np.floating, np.integer)):
            return float(v)
        if isinstance(v, np.ndarray):
            v = v.tolist()
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    import ast
                    v = ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    return default
            else:
                try:
                    return float(s)
                except ValueError:
                    return default
        if isinstance(v, (list, tuple)):
            if not v:
                return default
            n_eff = min(max(int(node), 0), len(v) - 1)
            try:
                return float(v[n_eff])
            except (TypeError, ValueError):
                return default
        return default

    def _per_system_cost(self, f: h5py.File, bp: str,
                         year: int) -> Optional[dict]:
        """Reconstruct the per-system cost breakdown for ``year``.

        Returns a dict with keys ``fuel_cost``, ``fixed_om_cost``,
        ``maintenance_cost``, ``startup_cost``,
        ``battery_maintenance_cost``, ``investment_cost`` and ``total``.
        Returns ``None`` if the scenario isn't in the file (e.g. the
        year wasn't simulated).

        Operational components are exact (same formula as the solver,
        with the per-node coefficient at each (g, n) pair). The
        investment component is taken from ``_investment_mw`` (which
        already filters by the system's node range) multiplied by the
        per-tech ``invest_cost`` declared in ``system_configuration``.

        Penalty / dual-cost components that ESFEX computes as
        model-wide scalars (load shedding, curtailment, CO₂, reserves,
        etc.) are NOT included here — they cannot be separated
        per-system without re-computing them from primal vars too.
        Surface them only when the user picks the "Global" pseudo
        system (or any single-system run, since the model-wide value
        IS that system's value then).
        """
        cache_key = (id(f), bp, int(year))
        if cache_key in self._cost_cache:
            return self._cost_cache[cache_key]

        sc_key = f"year_{year}_threshold_0"
        try:
            sc = _open_scenario(f, bp, sc_key)
        except KeyError:
            self._cost_cache[cache_key] = None
            return None

        # Read the GLOBAL scenario datasets, then slice to this system's
        # node range. ``_open_scenario`` returns a sliced view when
        # ``bp`` carries a subsystem prefix, but the per-system mirror
        # is collapsed in the Phase-2 layout — we re-derive the range
        # explicitly so the math is the same whether or not the mirror
        # exists.
        rng = self._system_node_range(f, bp)
        tres = int(f.attrs.get("temporal_resolution_hours", 1))

        from esfex.visualization.panels.results_charts import (
            _load_gen_configs, _load_bat_configs, _load_tech_configs,
            _load_bat_tech_configs, _load_gen_data, _load_bat_data,
        )
        gen_cfgs = _load_gen_configs(f, bp)
        bat_cfgs = _load_bat_configs(f, bp)
        tech_cfgs = _load_tech_configs(f, bp)
        bat_tech_cfgs = _load_bat_tech_configs(f, bp)

        # Stable mapping ``dataset name → index in gen_cfgs`` so we can
        # look up the right ``fuel_cost`` per generator. Datasets are
        # iterated in the same insertion order the runner wrote them,
        # so a positional zip is correct.
        gen_data = _load_gen_data(sc)
        gen_charge = _load_bat_data(sc, "battery_charge")
        gen_discharge = _load_bat_data(sc, "battery_discharge")

        def _slice_nodes(arr: np.ndarray) -> np.ndarray:
            """Return ``arr`` restricted to this system's node range
            along axis 0. ``arr`` is ``[nodes, hours]`` or flat ``[hours]``
            (single-node legacy export); the latter passes through."""
            if arr.ndim < 2 or rng is None:
                return arr
            lo, hi = rng
            return arr[lo:hi]

        fuel_c = fix_c = maint_c = startup_c = bat_maint_c = 0.0

        # Operational costs: Σ_{g, n∈sys, h} P[g,n,h] × coef[g,n] × Δt
        gen_names_in_order = list(gen_data.keys())
        for gi, gname in enumerate(gen_names_in_order):
            arr = gen_data[gname]
            cfg = gen_cfgs[gi] if gi < len(gen_cfgs) else {}
            sliced = _slice_nodes(arr)
            if sliced.ndim < 2:
                # Treat as node-0 series.
                energy_by_node = np.array([float(np.nansum(sliced)) * tres])
                lo = 0
            else:
                energy_by_node = np.nansum(sliced, axis=1) * tres
                lo = rng[0] if rng is not None else 0
            for k, e in enumerate(energy_by_node):
                if e == 0:
                    continue
                n = lo + k
                fuel_c  += float(e) * self._attr_at_node(cfg, "fuel_cost", n)
                fix_c   += float(e) * self._attr_at_node(cfg, "fixed_cost", n)
                maint_c += float(e) * self._attr_at_node(cfg, "maintenance_cost", n)

        # Startup: ESFEX writes gen_status (binary on/off) but the
        # number of starts is what the cost is keyed on. Without a
        # ``gen_startup`` dataset we fall back to counting 0→1
        # transitions in ``gen_status`` (same as the solver's
        # ``startup`` decision variable in UC mode).
        gen_status = sc.get("gen_status") if hasattr(sc, "get") else None
        if gen_status is not None:
            gs_data = (
                gen_status if isinstance(gen_status, dict)
                else {k: gen_status[k][:] for k in gen_status}
            )
            for gi, gname in enumerate(gs_data):
                arr = gs_data[gname]
                cfg = gen_cfgs[gi] if gi < len(gen_cfgs) else {}
                sliced = _slice_nodes(arr)
                if sliced.ndim < 2:
                    continue
                # transitions per node = sum of clip(diff, 0, 1)
                diffs = np.diff(sliced, axis=1, prepend=0)
                starts_by_node = np.clip(diffs, 0, 1).sum(axis=1)
                lo = rng[0] if rng is not None else 0
                for k, s in enumerate(starts_by_node):
                    if s == 0:
                        continue
                    n = lo + k
                    startup_c += float(s) * self._attr_at_node(
                        cfg, "start_up_cost", n
                    )

        # Battery maintenance: (charge + discharge) × per-node coeff.
        bat_names = list(set(gen_charge.keys()) | set(gen_discharge.keys()))
        for bi, bname in enumerate(bat_names):
            c_arr = gen_charge.get(bname, np.zeros(0))
            d_arr = gen_discharge.get(bname, np.zeros(0))
            cfg = bat_cfgs[bi] if bi < len(bat_cfgs) else {}
            for arr in (c_arr, d_arr):
                if arr.size == 0:
                    continue
                sliced = _slice_nodes(arr)
                if sliced.ndim < 2:
                    energy_by_node = np.array([float(np.nansum(sliced)) * tres])
                    lo = 0
                else:
                    energy_by_node = np.nansum(sliced, axis=1) * tres
                    lo = rng[0] if rng is not None else 0
                for k, e in enumerate(energy_by_node):
                    if e == 0:
                        continue
                    n = lo + k
                    bat_maint_c += float(e) * self._attr_at_node(
                        cfg, "maintenance_cost", n
                    )

        # Investment cost: MW added per-system × invest cost per MW.
        # We piggy-back on the existing ``_investment_mw`` logic but
        # need the tech-config invest_cost (a per-node vector stored
        # as a string by the runner).
        inv_cost = 0.0
        root_path = f"detailed_results/year_{year}_threshold_0"
        if root_path in f:
            for attr_key, attr_val in f[root_path].attrs.items():
                if not attr_key.startswith("investment_") or "_power_" not in attr_key:
                    continue
                try:
                    node = int(attr_key.rsplit("_", 1)[-1])
                    mw = float(attr_val)
                except (TypeError, ValueError):
                    continue
                if rng is not None and not (rng[0] <= node < rng[1]):
                    continue
                # Pick the tech config: the attr name carries the tech
                # index (e.g. ``investment_gen_power_3_7`` → gen tech 3,
                # node 7). Fallback to gen tech 0 if we can't parse.
                # ``investment_<bat|gen>_power_<t>_<n>``
                parts = attr_key.split("_")
                tech_pool = (
                    bat_tech_cfgs if "bat" in attr_key else tech_cfgs
                )
                try:
                    t_idx = int(parts[-2])
                except (ValueError, IndexError):
                    t_idx = 0
                tech_cfg = (
                    tech_pool[t_idx] if 0 <= t_idx < len(tech_pool) else {}
                )
                inv_cost += mw * self._attr_at_node(
                    tech_cfg, "invest_cost", node
                )

        components = {
            "fuel_cost": fuel_c,
            "fixed_om_cost": fix_c,
            "maintenance_cost": maint_c,
            "startup_cost": startup_c,
            "battery_maintenance_cost": bat_maint_c,
            "investment_cost": inv_cost,
        }
        components["total"] = sum(components.values())
        self._cost_cache[cache_key] = components
        return components

    def _compute_kpis(
        self, f: h5py.File, bp: str,
        years_all: list[int], years_view: list[int],
        mix: Optional[dict] = None,
    ) -> dict:
        """KPI values for the cards, aggregated over the whole brushed
        year range (not just the last year).

        Cost, CO₂, loss-of-load and investment are **summed** across the
        years in view (range totals). RE share is the energy-weighted
        renewable penetration over the range (a sum of percentages is
        meaningless), falling back to a plain mean when generation
        weights aren't available.
        """
        if not years_view:
            return {}
        sr_self = self._summary_group(f, bp)
        sr_root = f.get("summary_results")
        idx_by_year = {y: i for i, y in enumerate(years_all)}

        def _scalar(name: str, year: int, *, prefer_root: bool = False) -> Optional[float]:
            ix = idx_by_year.get(year)
            if ix is None:
                return None
            for grp in ((sr_root, sr_self) if prefer_root else (sr_self, sr_root)):
                if grp is None or name not in grp:
                    continue
                ds = grp[name]
                if not isinstance(ds, _DATASET_T):
                    continue
                if ix >= ds.shape[0]:
                    continue
                try:
                    return float(ds[ix])
                except Exception:
                    continue
            return None

        def _sum(name: str, *, prefer_root: bool = False) -> Optional[float]:
            vals = [v for v in (_scalar(name, y, prefer_root=prefer_root)
                                for y in years_view) if v is not None]
            return float(sum(vals)) if vals else None

        # Range-total semantics for the card values:
        #   • Cost / CO₂ / Load shed: sum over years_view (cumulative
        #     totals — the right framing for "what does this horizon
        #     cost / emit / lose in MWh").
        #   • New Capacity: sum of yearly MW additions over years_view
        #     (cumulative MW built across the horizon).
        #   • RE share: energy-weighted share across years_view (total
        #     renewable gen / total gen). Peak max would over-state
        #     years where everything spiked; mean would ignore that
        #     different years generate different absolute MWh.
        #
        # Per-card delta (homogeneous across cards EXCEPT Investment):
        #   • Cost / CO₂ / Load shed / RE share: change between the
        #     FIRST and LAST year of the range — the slope of the
        #     trajectory the user actually sees in the mix / trajectory
        #     chart. Snapshot vs snapshot, no aggregation involved, so
        #     the arrow always points the right way.
        #   • Investment: range-cumulative-total vs first-year addition
        #     (absolute MW). Tells the user "how much extra capacity
        #     beyond year-1 got booked over the rest of the horizon".
        first = years_view[0] if len(years_view) > 1 else None
        last = years_view[-1]

        # Per-system cost is reconstructed from the per-node datasets
        # × per-node config coefficients — the summary_results /
        # cost_breakdown values are model-wide scalars and would be
        # identical for every subsystem.
        def _cost_at_year(y: int) -> Optional[float]:
            comp = self._per_system_cost(f, bp, y)
            if comp is not None:
                return comp["total"]
            # Single-system run or legacy export: fall back to the
            # summary scalar (which equals the system's total when
            # there's no per-system decomposition to reconstruct).
            return _scalar("total_cost", y, prefer_root=False)

        cost_at = _cost_at_year
        co2_at  = lambda y: self._detailed_total(f, bp, [y], "CO2_emissions")
        load_at = lambda y: self._detailed_total(f, bp, [y], "loss_load")
        inv_at  = lambda y: self._investment_mw(f, bp, y)
        re_at   = lambda y: self._re_share_at_year(mix, y)

        def _sum_via(getter) -> Optional[float]:
            vals = [v for v in (getter(y) for y in years_view) if v is not None]
            return float(sum(vals)) if vals else None

        cost_vals = [v for v in (cost_at(y) for y in years_view) if v is not None]
        cost_v = float(sum(cost_vals)) if cost_vals else None
        co2_v  = self._detailed_total(f, bp, years_view, "CO2_emissions")
        if co2_v is None:
            co2_v = _sum("co2_emissions")
        load_v = self._detailed_total(f, bp, years_view, "loss_load")
        if load_v is None:
            load_v = _sum("loss_of_load")
        inv_v  = _sum_via(inv_at)
        re_v   = self._re_share_total(mix, years_view)
        if re_v is None:
            # Energy-weighted fallback from summary_results.
            num = den = 0.0
            for y in years_view:
                re = _scalar("renewable_penetration", y)
                gen = _scalar("total_generation", y)
                if re is not None and gen is not None and gen > 0:
                    num += re * gen
                    den += gen
            if den > 0:
                re_v = num / den

        def _delta(value_at, *, invert: bool = False, pp: bool = False) -> dict:
            """First-vs-last-year snapshot delta. Used for every card
            except Investment."""
            if first is None:
                return {"text": "", "direction": "flat"}
            v_first = value_at(first)
            v_last = value_at(last)
            if v_first is None or v_last is None:
                return {"text": "", "direction": "flat"}
            if pp:
                return _fmt_delta_pp(v_last, v_first)
            return _fmt_delta_pct(v_last, v_first, invert=invert)

        def _delta_inv() -> dict:
            """Investment-specific delta: cumulative range total minus
            the first year's addition, in absolute MW. The card shows
            the cumulative total; this tells the user how much of that
            total was booked after year-1."""
            if first is None or inv_v is None:
                return {"text": "", "direction": "flat"}
            v_first = inv_at(first)
            if v_first is None:
                return {"text": "", "direction": "flat"}
            diff = inv_v - v_first
            rounded = round(diff)
            if rounded == 0:
                return {"text": "→ 0 MW", "direction": "flat"}
            arrow = "↑" if rounded > 0 else "↓"
            direction = "up" if rounded > 0 else "down"
            return {
                "text": f"{arrow} {rounded:+,.0f} MW",
                "direction": direction,
            }

        def _card(value_text: Optional[str], delta: dict) -> dict:
            return {"value": value_text, "delta": delta}

        flat = {"text": "", "direction": "flat"}
        return {
            "cost": _card(
                _fmt_compact_usd(cost_v) if cost_v is not None else None,
                _delta(cost_at, invert=True) if cost_v is not None else flat),
            "re_share": _card(
                f"{re_v * 100:.1f}%" if re_v is not None else None,
                _delta(re_at, pp=True) if re_v is not None else flat),
            "co2": _card(
                _fmt_co2(co2_v) if co2_v is not None else None,
                _delta(co2_at, invert=True) if co2_v is not None else flat),
            "load_shed": _card(
                f"{load_v:,.0f} MWh" if load_v is not None else None,
                _delta(load_at, invert=True) if load_v is not None else flat),
            "investment": _card(
                f"{inv_v:,.0f} MW" if inv_v is not None else None,
                _delta_inv() if inv_v is not None else flat),
        }

    def _re_share_total(self, mix: Optional[dict],
                        years_view: list[int]) -> Optional[float]:
        """Energy-weighted RE share across ``years_view``: total
        renewable generation divided by total generation. Avoids the
        peak-max and arithmetic-mean traps — different years generate
        different absolute MWh, so the share has to be weighted by
        actual output."""
        if not mix:
            return None
        myears = mix.get("years") or []
        idx = [i for i, y in enumerate(myears) if y in years_view]
        if not idx:
            return None
        ren = tot = 0.0
        for s in mix.get("series", []):
            vals = s.get("values") or []
            is_re = s.get("label") in self._RENEWABLE_MIX_LABELS
            for i in idx:
                if i >= len(vals):
                    continue
                v = vals[i] or 0.0
                tot += v
                if is_re:
                    ren += v
        return (ren / tot) if tot > 0 else None

    def _re_share_at_year(self, mix: Optional[dict],
                          year: int) -> Optional[float]:
        """Single-year RE share derived from the mix. Returns a fraction
        (0–1) or ``None`` if the year is absent from the mix.

        Splitting this out from ``_re_share_from_mix`` (which returns
        the *peak* over a window) eliminates an asymmetry that hid the
        per-year share behind a max aggregation — fine for the "best
        year so far" card, wrong for delta math."""
        if not mix:
            return None
        myears = mix.get("years") or []
        try:
            i = myears.index(year)
        except ValueError:
            return None
        ren = tot = 0.0
        for s in mix.get("series", []):
            vals = s.get("values") or []
            if i >= len(vals):
                continue
            v = vals[i] or 0.0
            tot += v
            if s.get("label") in self._RENEWABLE_MIX_LABELS:
                ren += v
        return (ren / tot) if tot > 0 else None

    # ── Year-detail payload (drill-down click on Trajectory) ─────

    def get_year_detail(self, system: str, year: int) -> dict:
        """Per-year detail for the click-drill-down side panel.

        Returns:
          {
            "year": int,
            "kpis": same shape as overview KPIs but only for this year,
            "dispatch": {
              "hours": [int, …],          # synthetic hour index
              "series": [{"label","values","color"}, …]  # stacked by tech
            }
          }

        Errors are caught and surfaced as empty payloads so the JS
        side can render an "no data" placeholder rather than blowing
        up the page.
        """
        path = self._h5_files.get(system)
        if path is None or not path.exists() or year is None:
            return {"year": year, "kpis": {}, "dispatch": None}
        bp = self._base_prefix.get(system, "")
        try:
            with h5py.File(path, "r") as f:
                years_all = self._read_years(f, bp)
                if year not in years_all:
                    return {"year": year, "kpis": {}, "dispatch": None}
                kpis = self._compute_kpis(f, bp, years_all, [year])
                dispatch = self._read_year_dispatch(f, bp, year, system)
        except Exception:
            logger.exception("get_year_detail failed (system=%r, year=%s)", system, year)
            return {"year": year, "kpis": {}, "dispatch": None}
        return {"year": int(year), "kpis": kpis, "dispatch": dispatch}

    def _read_year_dispatch(
        self, f: h5py.File, bp: str, year: int, system: str = "",
    ) -> Optional[dict]:
        """Hour-resolution stacked dispatch for one year, by tech bucket.

        Each generator dataset in ``generation`` is shape (nodes, hours)
        — we sum over nodes to get the system-wide dispatch trace, then
        bucket per generator into a canonical tech via
        :meth:`_canonical_tech` (keyed off the declared fuel) so the
        legend stays short.

        We keep the native time resolution (no downsampling): Plotly
        handles ~1500 points comfortably, and the user gets exact
        information rather than an aggregated version.
        """
        sc_key = f"year_{year}_threshold_0"
        try:
            grp = _open_scenario(f, bp, sc_key)
        except KeyError:
            return None
        fuel_map = self._fuel_map_for(f, system)
        per_tech_hourly: dict[str, np.ndarray] = {}
        n_hours = 0
        for gen_name, arr, fuel_attr in _collect_arrays_with_fuel(grp, "generation"):
            if arr.ndim < 2:
                continue
            # Sum nodes -> 1D trace [hours]
            trace = np.nansum(arr, axis=0).astype(float)
            n_hours = max(n_hours, trace.shape[0])
            # Authoritative fuel from dataset attr when available;
            # fallback to name-based heuristics for legacy HDF5.
            if fuel_attr:
                tech = self._tech_from_fuel(fuel_attr)
            else:
                tech = self._canonical_tech(gen_name, fuel_map, system)
            if tech in per_tech_hourly:
                # Align lengths if some plants somehow report shorter
                # series (defensive — should not happen in practice).
                old = per_tech_hourly[tech]
                if trace.shape[0] == old.shape[0]:
                    per_tech_hourly[tech] = old + trace
                else:
                    m = min(trace.shape[0], old.shape[0])
                    per_tech_hourly[tech] = old[:m] + trace[:m]
            else:
                per_tech_hourly[tech] = trace
        if not per_tech_hourly or n_hours == 0:
            return None

        # Sort techs by mean magnitude descending — biggest contributors
        # land at the bottom of the stack.
        series = []
        for tech, values in sorted(
            per_tech_hourly.items(),
            key=lambda kv: -float(np.nanmean(kv[1])),
        ):
            series.append({
                "label": tech,
                "values": [None if not np.isfinite(v) else float(v)
                           for v in values.tolist()],
                "color": _color_for_tech(tech),
            })

        return {
            "hours": list(range(n_hours)),
            "series": series,
        }

    def _system_node_range(self, f: h5py.File, bp: str) -> Optional[tuple[int, int]]:
        """Global node index range [lo, hi) owned by the system behind
        ``bp`` (e.g. ``systems/Cuba``), from the run's subsystem offsets.
        Returns None for single-system runs (→ use all nodes)."""
        if not bp:
            return None
        name = bp.split("/")[-1]
        a = f.attrs
        names = a.get("subsystem_names")
        offs = a.get("subsystem_offsets")
        counts = a.get("subsystem_node_counts")
        if names is None or offs is None or counts is None:
            return None
        names = [n.decode() if isinstance(n, bytes) else str(n) for n in names]
        try:
            i = names.index(name)
        except ValueError:
            return None
        return (int(offs[i]), int(offs[i]) + int(counts[i]))

    def _investment_mw(self, f: h5py.File, bp: str, year: int) -> Optional[float]:
        # Legacy/dispatch layout: dedicated investment datasets under the
        # (possibly per-system) scenario group.
        sc_key = f"year_{year}_threshold_0"
        try:
            grp = _open_scenario(f, bp, sc_key)
        except KeyError:
            grp = None
        if grp is not None:
            total, found = 0.0, False
            for key in ("gen_investment_power", "bat_investment_power"):
                for _, arr in _collect_arrays(grp, key):
                    if arr.size > 0:
                        total += float(np.nansum(arr))
                        found = True
            if found:
                return total

        # MasterProblem layout: investment is booked in the GLOBAL (root)
        # model as per-scenario attrs ``investment_*_power_{t}_{n}`` keyed
        # by global node index. Per-system scenarios carry none, so read
        # the root and attribute each node-indexed addition to this system
        # via its subsystem node range.
        root_path = f"detailed_results/year_{year}_threshold_0"
        if root_path not in f:
            return None
        rng = self._system_node_range(f, bp)
        total, found = 0.0, False
        for attr_key, attr_val in f[root_path].attrs.items():
            if not attr_key.startswith("investment_") or "_power_" not in attr_key:
                continue
            try:
                node = int(attr_key.rsplit("_", 1)[-1])
                val = float(attr_val)
            except (TypeError, ValueError):
                continue
            if rng is not None and not (rng[0] <= node < rng[1]):
                continue
            total += val
            found = True
        return total if found else None

    @staticmethod
    def _summary_group(f: h5py.File, bp: str):
        return _open_summary_results(f, bp)

    # ── Tech-bucket resolution ────────────────────────────────────
    #
    # The HDF5 ``generation`` group keys (one dataset per generator)
    # do NOT match the ``@name`` attribute the solver wrote into
    # ``system_configuration/generators``: the dataset key often
    # comes from an OSM/geo lookup (e.g.
    # ``"Cuba - 10 De Octubre (nuevitas) Powerplant"``) while the
    # config name is ``"Cuba/Termoeléctrica 10 de Octubre"``. So an
    # exact-string lookup misses most plants and the chart falls
    # back to keyword guessing on the dataset name — which in turn
    # only matched English keywords, so all Cuban Spanish names
    # (``Parque Fotovoltaico``, ``Eólico``, ``Hidroeléctrica``,
    # ``Bioeléctrica``, ``Termoeléctrica``) ended up in "Other".
    #
    # The fix is two-pronged:
    #   1. Token-based fuzzy match between dataset-name and
    #      config-name (after stripping the system prefix and
    #      common stopwords): two or more shared significant words
    #      = same plant.
    #   2. Extended keyword set covering Spanish + the missing
    #      "Thermoelectric / Powership / Powerplant" patterns.

    def _fuel_map_for(self, f: h5py.File, system: str) -> list[tuple[set, str]]:
        """Return ``[(token_set, fuel), …]`` from system_configuration.

        Each entry holds the *normalised significant tokens* of a
        config plant name plus its declared fuel. Lookup is a
        token-intersection score (see :meth:`_canonical_tech`), not
        an exact-string compare — that lets us match
        ``"Cuba/Termoeléctrica 10 de Octubre"`` (config) against
        ``"Cuba - 10 De Octubre (nuevitas) Powerplant"`` (dataset).
        """
        if system in self._fuel_map_cache:
            return self._fuel_map_cache[system]

        entries: list[tuple[set, str]] = []
        bp = f"systems/{system}"
        cfg = _open_system_config(f, bp)
        if cfg is not None and "generators" in cfg:
            grp = cfg["generators"]
            for gen_key in grp:
                gnode = grp[gen_key]
                name = gnode.attrs.get("name")
                fuel = gnode.attrs.get("fuel")
                if name is None or fuel is None:
                    continue
                tokens = self._significant_tokens(str(name), system)
                if tokens:
                    entries.append((tokens, str(fuel)))

        self._fuel_map_cache[system] = entries
        return entries

    # Tokens that carry no identifying signal — they appear in
    # nearly every plant name and would pollute the intersection
    # score. We strip them before computing token overlap.
    _STOPWORDS = frozenset({
        "the", "de", "del", "la", "el", "los", "las", "y", "and",
        "power", "powerplant", "powerstation", "station", "plant",
        "node", "generator", "investment", "cuba", "islajuventud",
        "isla", "juventud", "thermoelectric", "thermoelec",
        # Frequent Spanish prefixes that appear in many plant names
        # but don't disambiguate them:
        "parque", "central", "ramal",
    })

    @classmethod
    def _significant_tokens(cls, name: str, system: str = "") -> set:
        """Lowercase set of plant-identifying tokens.

        Splits on common separators (``/``, ``-``, whitespace,
        parentheses), drops stopwords and the system name. Numbers
        and short tokens (<3 chars) are kept because plant indices
        and unit numbers ("10", "I", "II") matter for disambiguation.
        """
        import re
        # Normalise separators and parens to whitespace
        cleaned = re.sub(r"[\/\-\(\)\[\]]+", " ", name.lower())
        sys_lower = system.lower()
        tokens = set()
        for raw in cleaned.split():
            t = raw.strip(".,;:")
            if not t:
                continue
            if t in cls._STOPWORDS or t == sys_lower:
                continue
            tokens.add(t)
        return tokens

    def _tech_from_fuel(self, fuel: str) -> str:
        """Map a raw fuel string to a display tech label.

        ESFEX configs are inconsistent about renewable fuel naming —
        a PV plant's fuel may be ``"Sun"``, ``"Solar"`` or ``"PV"``;
        wind may be ``"Wind"``. We collapse the known synonyms so the
        legend has one ``Solar`` slice, not a ``Sun`` + ``Solar`` pair.
        """
        fl = fuel.strip().lower()
        return {
            "wind": "Wind",
            "solar": "Solar",
            "sun": "Solar",
            "pv": "Solar",
            "photovoltaic": "Solar",
            "biomass": "Biomass",
            "bio": "Biomass",
            "hydro": "Hydro",
            "water": "Hydro",
            "diesel": "Diesel",
            "fuel oil": "Fuel oil",
            "fuel_oil": "Fuel oil",
            "fueloil": "Fuel oil",
            "oil": "Fuel oil",
            "gas": "Gas",
            "natural gas": "Gas",
            "coal": "Coal",
            "hydrogen": "Hydrogen",
            "h2": "Hydrogen",
            "geothermal": "Geothermal",
            "otec": "OTEC",
            "nuclear": "Nuclear",
            "none": "Other",
            "": "Other",
        }.get(fl, fuel.strip().capitalize() or "Other")

    # Substring keyword fallback. Order matters: more specific keys
    # must come before generic ones (``"fuel oil"`` before ``"oil"``).
    # Mixes English + Spanish to cover Cuban plant naming conventions.
    _KEYWORD_BUCKETS = (
        # Renewable — Solar / PV
        ("fotovoltaic",    "Solar"),
        ("photovoltaic",   "Solar"),
        ("solar",          "Solar"),
        # Renewable — Wind
        ("eólic",          "Wind"),
        ("eolic",          "Wind"),
        ("wind",           "Wind"),
        # Renewable — Hydro
        ("hidroeléctric",  "Hydro"),
        ("hidroelectric",  "Hydro"),
        ("hydroelec",      "Hydro"),
        ("hydro",          "Hydro"),
        # Renewable — Biomass
        ("bioeléctric",    "Biomass"),
        ("bioelectric",    "Biomass"),
        ("biomass",        "Biomass"),
        ("biomasa",        "Biomass"),
        # Renewable — others
        ("otec",           "OTEC"),
        ("geotérm",        "Geothermal"),
        ("geothermal",     "Geothermal"),
        ("nuclear",        "Nuclear"),
        ("hidrógen",       "Hydrogen"),
        ("hydrogen",       "Hydrogen"),
        # Fossil — fuel-specific (must precede generic "oil" / "gas")
        ("fuel oil",       "Fuel oil"),
        ("fuel_oil",       "Fuel oil"),
        ("fueloil",        "Fuel oil"),
        ("diésel",         "Diesel"),
        ("dièsel",         "Diesel"),
        ("diesel",         "Diesel"),
        ("natural gas",    "Gas"),
        # Storage
        ("battery",        "Battery"),
        ("batería",        "Battery"),
        # Generic thermal patterns (Spanish & English) — catch-all for
        # fossil plants whose dataset name carries no fuel keyword
        ("termoeléctric",  "Thermal"),
        ("termoelectric",  "Thermal"),
        ("thermoelec",     "Thermal"),
        ("powership",      "Thermal"),  # floating fuel-oil generators
        ("powerplant",     "Thermal"),
        ("powerstation",   "Thermal"),
        ("power station",  "Thermal"),
        # Gas / oil at the very end so "biomass" and "fuel oil" win first
        ("gas",            "Gas"),
        ("oil",            "Fuel oil"),
    )

    def _canonical_tech(
        self, generator_name: str,
        fuel_map=None,
        system: str = "",
    ) -> str:
        """Group a generator into a broad tech bucket.

        Resolution order:
          1. **Token overlap with config**: if the dataset name shares
             ≥2 significant tokens with a config plant, use that
             plant's declared fuel. Works across the dataset-vs-config
             name mismatch (Spanish display name vs OSM-derived key).
          2. **Single-token + unique fuel match**: if exactly one
             config entry shares ≥1 token, accept it (handles short
             names with one distinctive word, e.g. ``"Hanabanilla"``).
          3. **Keyword fallback**: substring match on the lowercased
             name against the extended ES+EN keyword list.
          4. ``"Other"`` if everything fails (so a single bucket
             absorbs the unknowns instead of polluting the legend).
        """
        # Phase 1+2: config-driven match
        if fuel_map:
            ds_tokens = self._significant_tokens(generator_name, system)
            if ds_tokens:
                best_overlap = 0
                best_fuel: Optional[str] = None
                # Track single-token unique matches separately so we
                # can fall back to them when no 2-token overlap exists.
                single_hits: list[str] = []
                for cfg_tokens, fuel in fuel_map:
                    overlap = len(ds_tokens & cfg_tokens)
                    if overlap >= 2 and overlap > best_overlap:
                        best_overlap = overlap
                        best_fuel = fuel
                    elif overlap == 1 and best_overlap < 2:
                        single_hits.append(fuel)
                if best_fuel is not None:
                    return self._tech_from_fuel(best_fuel)
                # Single-token, only one config plant matched → trust it.
                if len(single_hits) == 1:
                    return self._tech_from_fuel(single_hits[0])

        # Phase 3: keyword fallback (ES + EN expanded)
        n = generator_name.lower()
        for kw, label in self._KEYWORD_BUCKETS:
            if kw in n:
                return label
        # Phase 4
        return "Other"
