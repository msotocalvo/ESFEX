"""Lazy data cache for the ResultsDialog.

Each chart in the dialog used to open the same HDF5 file independently
inside its ``_build_payload``, reread invariant configs (generators,
batteries, tech, colours), and re-list scenarios sorted by year. With
17 charts that was 17× file opens and 17× config rebuilds per refresh —
the dominant cost of a year-slider move.

This module exposes a thin :class:`ResultsCache` that:

* keeps a single ``h5py.File`` handle open for the dialog session,
* memoises configs and scenario listings per ``base_prefix``,

and is **opt-in via** a :class:`contextvars.ContextVar`. Helpers in
``results_charts`` consult the active cache transparently, so charts
that never receive a cache keep working exactly as before.

Per-scenario arrays (``gen_data``, ``bat_data``) are intentionally
**not** cached here — they are large [nodes × 8760] floats and would
balloon RAM for 25-year horizons. The cheap structural reads are
where the big speed-up lives.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

import h5py

logger = logging.getLogger(__name__)


# Set by ``activate(cache)`` for the duration of a chart render.
# Helpers in ``results_charts.py`` look it up and short-circuit
# redundant HDF5 reads when populated.
_ACTIVE_CACHE: ContextVar[Optional["ResultsCache"]] = ContextVar(
    "_active_results_cache", default=None,
)


class ResultsCache:
    """One per (dialog, h5 file). Recreate when the user picks a
    different system or a different results file.
    """

    def __init__(self, h5_path: Path):
        self.h5_path = Path(h5_path)
        self._h5f: Optional[h5py.File] = None

        # Per-base_prefix memoisation.
        self.gen_configs: dict[str, list[dict]] = {}
        self.bat_configs: dict[str, list[dict]] = {}
        self.tech_configs: dict[str, list[dict]] = {}
        self.bat_tech_configs: dict[str, list[dict]] = {}
        self.tech_colors: dict[str, dict[str, str]] = {}
        self.gen_types: dict[str, dict[str, str]] = {}
        self.node_names: dict[str, list[str]] = {}
        self.scenarios: dict[str, list[tuple[str, int]]] = {}

        # Global-ish (h5-file-wide) memoisation.
        self.tres: Optional[int] = None

        # MGA bundle (header / tech_range / parcoords / pathways /
        # spatial) per base_prefix — the five MGA-themed charts share it
        # so the HDF5 only gets read once per batch.
        self.mga_bundle: dict[str, dict] = {}

        # Transient per-scenario array cache (gen/battery dicts), shared
        # across charts WITHIN a batch render so the same scenario data is
        # read + node-summed once instead of once per chart. Bounded by a
        # byte budget so huge multi-node/25-year runs don't balloon RAM;
        # cleared after each batch via ``clear_scenario_data()``.
        self.scenario_data: dict = {}
        self._scenario_bytes: int = 0
        self.scenario_cache_budget: int = 512 * 1024 * 1024  # 512 MB

    def get_scenario_data(self, key):
        return self.scenario_data.get(key)

    def put_scenario_data(self, key, value, nbytes: int) -> None:
        """Cache ``value`` under ``key`` unless it would exceed the budget."""
        if key in self.scenario_data:
            return
        if self._scenario_bytes + nbytes > self.scenario_cache_budget:
            return  # Over budget: skip caching, fall back to direct reads.
        self.scenario_data[key] = value
        self._scenario_bytes += nbytes

    def clear_scenario_data(self) -> None:
        self.scenario_data.clear()
        self._scenario_bytes = 0

    @property
    def h5f(self) -> h5py.File:
        if self._h5f is None:
            self._h5f = h5py.File(self.h5_path, "r")
            logger.debug("ResultsCache opened %s", self.h5_path)
        return self._h5f

    def close(self) -> None:
        if self._h5f is not None:
            try:
                self._h5f.close()
            except Exception:
                logger.exception("ResultsCache: failed to close %s", self.h5_path)
            self._h5f = None


@contextlib.contextmanager
def open_h5(h5_path):
    """Drop-in replacement for ``with h5py.File(h5_path, 'r') as h5f``.

    Reuses the active cache's already-open handle when one is bound to
    the same path; otherwise opens (and closes) a fresh handle. The
    yielded object is always a real ``h5py.File`` — callers don't need
    to know which path they got.
    """
    cache = _ACTIVE_CACHE.get()
    if cache is not None and Path(cache.h5_path) == Path(h5_path):
        yield cache.h5f
    else:
        with h5py.File(h5_path, "r") as h5f:
            yield h5f


@contextlib.contextmanager
def activate(cache: Optional["ResultsCache"]):
    """Bind ``cache`` as the active cache for the current task.

    Use around a chart render so the memoising helpers in
    ``results_charts.py`` pick it up:

        with activate(self._cache):
            chart.update_chart(...)
    """
    token = _ACTIVE_CACHE.set(cache)
    try:
        yield cache
    finally:
        _ACTIVE_CACHE.reset(token)


def active_cache() -> Optional["ResultsCache"]:
    """Return the cache bound to the current task, or ``None``."""
    return _ACTIVE_CACHE.get()
