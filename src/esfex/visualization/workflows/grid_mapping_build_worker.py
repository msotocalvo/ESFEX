"""Background worker for the Grid Builder "Building network" pipeline.

The whole build — node creation → ``build_grid_from_features`` → parameter
inference → simplification → isolated/dangling cleanup → visual wires → optional
availability — used to run synchronously on the GUI thread, freezing the Studio
for tens of minutes on large systems (e.g. all of Japan). This worker runs it on
a background ``QThread`` so the UI stays responsive and the build is cancellable.

The worker mutates ``model.state`` with the model's per-element signals **blocked
by the caller**; every mutating model method only touches plain ``state`` data
and a (suppressed) signal, so this is safe off the main thread. The caller emits
a single ``stateLoaded`` redraw on the main thread when the worker finishes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


@dataclass
class BuildParams:
    """Inputs captured on the main thread before the worker starts (so the
    worker never reads a Qt widget)."""

    node_positions: list
    n_clusters: int
    criterion_used: str
    features: list
    config: dict
    station_radius_km: float
    simplify_level: int
    min_component: int
    gen_availability: bool
    use_weather: bool
    cfg_path: str | None = None


class GridBuildWorker(QThread):
    """Run the Grid Builder build pipeline off the GUI thread."""

    progress = Signal(str)      # human-readable phase status
    finished = Signal(object)   # result dict (see _run)
    error = Signal(str)

    def __init__(self, model, params: BuildParams, parent=None):
        super().__init__(parent)
        self._model = model
        self._p = params
        self._cancelled = False
        self.phase_timings: list[tuple[str, float]] = []

    def cancel(self):
        self._cancelled = True

    def _timed(self, label, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            dt = time.perf_counter() - t0
            self.phase_timings.append((label, dt))
            logger.info("Grid build phase '%s': %.1fs", label, dt)

    def run(self):
        try:
            res = self._run()
            res["phase_timings"] = self.phase_timings
            res["cancelled"] = self._cancelled
            self.finished.emit(res)
        except Exception as exc:  # surfaced to the user; partial state kept
            logger.exception("Grid build worker error")
            self.error.emit(str(exc))

    # ------------------------------------------------------------------
    def _run(self) -> dict:
        from esfex.visualization.workflows.grid_mapping_builder import (
            build_grid_from_features,
        )
        from esfex.visualization.workflows.grid_mapping_inference import (
            infer_electrical_params,
        )
        from esfex.visualization.data.validation import (
            SimplificationConfig,
            apply_simplification_level,
            drop_dangling_refs,
            drop_isolated_components,
            rebuild_visual_wire_lines,
        )

        model, p = self._model, self._p
        out: dict = {"build_summary": "", "simplify_summary": "", "island_summary": ""}
        island_lines: list[str] = []

        with model.suspend_checkpoints():
            # ── Node creation (auto-nodes only; None → use existing nodes) ──
            if p.node_positions is not None:
                self.progress.emit(f"Creating {len(p.node_positions)} nodes…")
                # The Grid Builder owns the full node set: drop placeholder
                # nodes created with the system (e.g. default "Node 0").
                while model.state.nodes:
                    model.remove_node(len(model.state.nodes) - 1)
                for lat, lng, name in p.node_positions:
                    idx = model.add_node(name)
                    model.update_node(idx, centroid_lat=lat, centroid_lng=lng)
                if self._cancelled:
                    return out

            # ── Build network ──
            self.progress.emit("Building network…")
            result = self._timed("Build network", lambda: build_grid_from_features(
                model=model,
                features=p.features,
                bus_strategy=p.config.get("bus_strategy", "per_voltage"),
                snap_threshold_km=p.config.get("snap_threshold_km", 5.0),
                target_node=None,
                faithful=True,
                station_radius_km=p.station_radius_km,
                min_capacity_mw=p.config.get("min_capacity_mw", 0.0),
            ))
            out["build_summary"] = result.summary()
            if self._cancelled:
                return out

            # ── Parameter inference ──
            self.progress.emit("Inferring electrical parameters…")
            self._timed("Parameter inference",
                        lambda: infer_electrical_params(model.state))
            if self._cancelled:
                return out

            # ── Simplification ──
            self.progress.emit(f"Simplifying (level {p.simplify_level})…")
            simp_log, _issues = self._timed(
                f"Simplification (level {p.simplify_level})",
                lambda: apply_simplification_level(
                    model, p.simplify_level, SimplificationConfig()),
            )
            out["simplify_summary"] = "\n".join(simp_log)
            if self._cancelled:
                return out

            # ── Drop isolated debris components ──
            self.progress.emit("Dropping isolated components…")
            counts = self._timed("Drop isolated components",
                                  lambda: drop_isolated_components(
                                      model.state, min_buses=p.min_component,
                                      keep_largest=True))
            n_total = counts.get("_components_total", 0)
            n_dropped = counts.get("_components_dropped", 0)
            island_lines.append(
                f"Components: {n_total} total "
                f"(largest = {counts.get('_largest_size', 0)} buses, "
                f"top sizes: {counts.get('_top_sizes', [])})")
            island_lines.append(
                f"Threshold: drop components with < {p.min_component} bus(es), "
                f"keeping the largest")
            if n_dropped:
                island_lines.append(
                    f"→ Dropped {n_dropped} component(s): "
                    f"{counts['buses']} bus(es), {counts['lines']} line(s), "
                    f"{counts['transformers']} transformer(s), "
                    f"{counts['converters']} converter(s), "
                    f"{counts['generators']} generator(s), "
                    f"{counts['batteries']} battery(ies)")

            # ── Hard sweep: drop dangling references ──
            self.progress.emit("Removing dangling references…")
            ref_counts = self._timed("Drop dangling refs",
                                     lambda: drop_dangling_refs(model.state))
            if sum(ref_counts.values()) > 0:
                island_lines.append(
                    f"Dangling-reference sweep removed: "
                    f"{ref_counts['lines']} line(s), "
                    f"{ref_counts['transformers']} transformer(s), "
                    f"{ref_counts['converters']} converter(s), "
                    f"{ref_counts['generators']} generator(s), "
                    f"{ref_counts['batteries']} battery(ies) with broken refs.")

            # ── Rebuild visual wire-lines ──
            self.progress.emit("Rebuilding visual wires…")
            n_wires = self._timed("Rebuild visual wires",
                                  lambda: rebuild_visual_wire_lines(model.state))
            if n_wires:
                island_lines.append(
                    f"Rebuilt {n_wires} visual wire-line(s).")

            # ── Optional availability profile generation ──
            if p.gen_availability and model.state.generators and not self._cancelled:
                self.progress.emit("Generating availability profiles…")
                from esfex.plugins.availability_generator.grid_builder_hook import (
                    generate_for_grid_build,
                )
                out_dir = (Path(p.cfg_path).parent / "availability"
                           if p.cfg_path else Path.cwd() / "availability")
                written = self._timed("Availability profiles",
                                      lambda: generate_for_grid_build(
                                          model.state, out_dir,
                                          use_weather_data=p.use_weather))
                if written:
                    island_lines.append(
                        f"Generated {len(written)} availability profile(s) "
                        f"under {out_dir}.")
                # Wind/solar units with no real weather data are skipped (no
                # fabricated flat profile); every other generator is written.
                n_skipped = len(model.state.generators) - len(written)
                if n_skipped > 0:
                    island_lines.append(
                        f"{n_skipped} wind/solar generator(s) had no weather "
                        f"data available — left without an availability "
                        f"profile (no synthetic fallback).")

        out["island_summary"] = "\n".join(island_lines)
        out["simplify_level"] = p.simplify_level
        return out
