"""Matplotlib chart widgets for post-simulation results visualization.

Provides 12 chart types matching the legacy ESFEX_Visualizer, embedded in
Qt via FigureCanvasQTAgg for use inside the Results Dialog.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

from esfex.utils.temporal import HOURS_STD_YEAR
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from PySide6.QtCore import QObject, Qt, QUrl, Slot
from PySide6.QtGui import QAction, QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from esfex.visualization.i18n import tr
from esfex.visualization.panels.results_cache import (
    active_cache as _active_cache,
    open_h5 as _open_h5,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Color scheme (delegated to the centralized theme)
# ──────────────────────────────────────────────────────────────

from esfex.visualization.theme import (
    get_generation_colors,
    get_generation_default_color,
    get_tab10,
)

RENEWABLE_KEYWORDS = {"solar", "wind", "biomass", "hydro", "hydroelectric", "otec"}
THERMAL_KEYWORDS = {"gas", "oil", "fuel", "diesel", "turbine", "engine"}


def _build_tech_color_map(
    h5f: h5py.File, base_prefix: str = "",
) -> dict[str, str]:
    """Build {tech_name_or_fuel: color} from HDF5 technology configs.

    Reads ``color`` attributes written by the runner from user-assigned
    technology colors.  Keys are the technology *name* and its *fuel*
    (both map to the same color so generators can be matched by fuel).
    """
    cache = _active_cache()
    if cache is not None and base_prefix in cache.tech_colors:
        return cache.tech_colors[base_prefix]
    cmap: dict[str, str] = {}
    for cfg in _load_tech_configs(h5f, base_prefix):
        color = cfg.get("color", "")
        if not color:
            continue
        color = str(color)
        name = str(cfg.get("name", ""))
        fuel = str(cfg.get("fuel", ""))
        if name:
            cmap[name] = color
        if fuel:
            cmap[fuel] = color
    for cfg in _load_bat_tech_configs(h5f, base_prefix):
        color = cfg.get("color", "")
        if not color:
            continue
        color = str(color)
        name = str(cfg.get("name", ""))
        if name:
            cmap[name] = color
    if cache is not None:
        cache.tech_colors[base_prefix] = cmap
    return cmap


def _color_for(name: str, tech_colors: dict[str, str] | None = None) -> str:
    """Resolve display color for a generation source *name*.

    For real generators, callers should have already resolved the
    colour explicitly via :func:`_build_gen_tech_map`. This helper is
    for synthesised labels (``Battery discharge``, ``Curtailment``,
    technology display names) where the name itself is the lookup key.
    """
    if tech_colors:
        if name in tech_colors:
            return tech_colors[name]
        # Substring match for technology display names that include a
        # system prefix (``Cuba/Wind Turbine``) — keeps the synthesised
        # labels matching their tech entry.
        nl = name.lower()
        for key, color in tech_colors.items():
            kl = key.lower()
            if kl in nl or nl in kl:
                return color
    gen_colors = get_generation_colors()
    if name in gen_colors:
        return gen_colors[name]
    for key, color in gen_colors.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return color
    return get_generation_default_color()


def _lighten(hex_color: str, factor: float) -> str:
    """Blend a hex colour toward white (factor 0 = same, 1 = white)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


def _darken(hex_color: str, factor: float) -> str:
    """Blend a hex colour toward black (factor 0 = same, 1 = black)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r * (1 - factor))
    g = int(g * (1 - factor))
    b = int(b * (1 - factor))
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_renewable(name: str) -> bool:
    nl = name.lower()
    return any(kw in nl for kw in RENEWABLE_KEYWORDS)


# ──────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────

def _sum_nodes(arr: np.ndarray) -> np.ndarray:
    """Sum a [nodes × hours] or [gen × nodes × hours] array across the node axis."""
    if arr.ndim == 2:
        return arr.sum(axis=0)
    if arr.ndim == 3:
        return arr.sum(axis=(0, 1))
    return arr


def _year_hours(temporal_res: int = 1) -> int:
    """Expected number of time-steps in one year."""
    return HOURS_STD_YEAR // max(temporal_res, 1)


def _trim_year(arr: np.ndarray, temporal_res: int = 1) -> np.ndarray:
    """Trim array to at most one year's worth of time-steps (last axis)."""
    yh = _year_hours(temporal_res)
    if arr.ndim == 1:
        return arr[:yh]
    if arr.ndim == 2:
        return arr[:, :yh]
    if arr.ndim == 3:
        return arr[:, :, :yh]
    return arr


def _aggregate(data: np.ndarray, resolution: str, temporal_res: int = 1) -> np.ndarray:
    """Aggregate hourly-resolution data to daily/monthly/yearly.

    Data is first trimmed to at most one year (8760 / temporal_res steps)
    to avoid length mismatches from rolling-horizon overlap.
    """
    yh = _year_hours(temporal_res)
    data = data[:yh]
    n = len(data)
    if resolution == "daily":
        chunk = max(1, 24 // temporal_res)
        n_chunks = n // chunk
        if n_chunks == 0:
            return data
        trimmed = data[: n_chunks * chunk]
        return trimmed.reshape(n_chunks, chunk).sum(axis=1)
    if resolution == "monthly":
        chunk = max(1, 730 // temporal_res)
        n_chunks = max(1, n // chunk)
        out = []
        for i in range(n_chunks):
            s = data[i * chunk: (i + 1) * chunk]
            out.append(s.sum())
        return np.array(out)
    if resolution == "yearly":
        return np.array([data.sum()])
    return data  # hourly


def _sorted_scenarios(h5f: h5py.File, base_prefix: str = ""):
    """Yield (scenario_key, year) tuples sorted by year."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.scenarios:
        yield from cache.scenarios[base_prefix]
        return
    # Prefer per-system mirror when present (legacy layout); otherwise
    # fall back to the root detailed_results — the scenario list and
    # years are identical across systems.
    det_path = f"{base_prefix}/detailed_results" if base_prefix else "detailed_results"
    if det_path not in h5f:
        det_path = "detailed_results"
    if det_path not in h5f:
        if cache is not None:
            cache.scenarios[base_prefix] = []
        return
    items = []
    for key in h5f[det_path]:
        sc = h5f[det_path][key]
        year = int(sc.attrs.get("year", 0))
        items.append((key, year))
    items = sorted(items, key=lambda x: x[1])
    if cache is not None:
        cache.scenarios[base_prefix] = items
    yield from items


def _scenario_cache_get(kind: str, scenario_grp: h5py.Group):
    """Return cached per-scenario data for (kind, scenario path), or None."""
    cache = _active_cache()
    if cache is None:
        return None, None
    key = (kind, scenario_grp.name)
    return cache.get_scenario_data(key), key


def _scenario_cache_put(key, out: dict):
    cache = _active_cache()
    if cache is None or key is None:
        return
    nbytes = sum(int(a.nbytes) for a in out.values()
                 if isinstance(a, np.ndarray))
    cache.put_scenario_data(key, out, nbytes)


def _load_gen_data(scenario_grp: h5py.Group) -> dict[str, np.ndarray]:
    """Load generation data {name: [nodes x hours]} from scenario group.

    Handles both flat datasets (``generation/Solar PV``) and nested groups
    created when generator names contained ``/`` (``generation/Cuba/Solar PV``).
    Result is memoised per scenario in the active cache so charts sharing
    the same scenario read + parse it once per batch.
    """
    cached, ckey = _scenario_cache_get("gen", scenario_grp)
    if cached is not None:
        return cached
    out = {}
    if "generation" not in scenario_grp:
        return out

    def _collect(grp: h5py.Group, prefix: str = ""):
        for key in grp:
            item = grp[key]
            full = f"{prefix}{key}" if not prefix else f"{prefix} - {key}"
            if isinstance(item, _DATASET_T):
                out[full] = item[:]
            elif isinstance(item, _GROUP_T):
                _collect(item, full)

    _collect(scenario_grp["generation"])
    _scenario_cache_put(ckey, out)
    return out


def _load_bat_data(scenario_grp: h5py.Group, key: str) -> dict[str, np.ndarray]:
    """Load battery charge/discharge/soc data {name: [nodes x hours]}.

    Handles nested groups caused by ``/`` in battery names. Memoised per
    (scenario, key) in the active cache for reuse across charts.
    """
    cached, ckey = _scenario_cache_get("bat:" + key, scenario_grp)
    if cached is not None:
        return cached
    out = {}
    if key not in scenario_grp:
        return out
    grp = scenario_grp[key]
    if isinstance(grp, _DATASET_T):
        out = {"total": grp[:]}
        _scenario_cache_put(ckey, out)
        return out

    def _collect(g: h5py.Group, prefix: str = ""):
        for k in g:
            item = g[k]
            full = f"{prefix}{k}" if not prefix else f"{prefix} - {k}"
            if isinstance(item, _DATASET_T):
                out[full] = item[:]
            elif isinstance(item, _GROUP_T):
                _collect(item, full)

    _collect(grp)
    _scenario_cache_put(ckey, out)
    return out


def _get_temporal_res(h5f: h5py.File) -> int:
    cache = _active_cache()
    if cache is not None and cache.tres is not None:
        return cache.tres
    tres = int(h5f.attrs.get("temporal_resolution_hours", 1))
    if cache is not None:
        cache.tres = tres
    return tres


def _prefixed(base_prefix: str, path: str) -> str:
    """Resolve an HDF5 path with optional base_prefix."""
    return f"{base_prefix}/{path}" if base_prefix else path


# ──────────────────────────────────────────────────────────────
# System slicing — read per-system scenario data from the global
# (root) detailed_results block by slicing the node axis.
#
# Background: ESFEX writes per-system mirrors of the global
# detailed_results under ``/systems/{name}/`` that inflate the
# .h5 by 25-40 %. The mirrors are pure slices of the root
# arrays along the node axis, so they are derivable.
#
# Strategy: ``_open_scenario(h5f, bp, sc_key)`` returns
#   - the raw root group when ``bp`` is empty,
#   - the raw per-system group when the legacy mirror is still
#     present (backward compat with old result files),
#   - a slicing proxy onto the root group otherwise.
#
# The proxies implement the subset of the h5py.Group / Dataset
# API the charts actually use: ``__contains__``, ``keys``,
# ``__iter__``, ``__getitem__``, ``.attrs``, ``.shape``,
# ``.dtype`` and ``__array__``.
# ──────────────────────────────────────────────────────────────

# Datasets at scenario root whose first axis is the node axis
# (shape ``[N, T]`` in the global block).
_SLICE_AXIS0_DATASETS = frozenset({
    "demand",
    "CO2_emissions",
    "EV_V2G", "EV_charging", "EV_loss", "EV_soc",
    "loss_load",
    "loss_of_reserve_dynamic", "loss_of_reserve_static",
    "nodal_electricity_prices",
    "reserve_dynamic", "reserve_static",
    "voltage_angle",
    # ``curtailment`` is ``[N, T]`` in files written by the post-Phase-2bis
    # runner but ``[T]`` in older files; the proxy downgrades the mode at
    # runtime when it sees a 1D dataset.
    "curtailment",
})
# 3D ``[N, N, T]``.
_SLICE_BOTH_AXES_DATASETS = frozenset({"power_flow"})
# Scenario-level groups whose direct dataset children are per-tech
# ``[N, T]`` arrays. Member keys are filtered to system-owned
# techs (prefix match on system name).
_TECH_GROUPS = frozenset({
    "generation",
    "capacity_factor", "lcoe", "vallcoe",
    "battery_capacity_factor", "battery_charge", "battery_discharge",
    "battery_lcoe", "battery_soc", "battery_vallcoe",
})
# Special: root layout is ``[T, N]`` (legacy transpose bug — per-system
# block was written as ``[N, T]``). The proxy emulates the per-system
# shape so chart code does not need to change.
_ROOFTOP_KEY = "rooftop_generation"


def _system_node_range(
    h5f: h5py.File, base_prefix: str
) -> Optional[tuple[int, int]]:
    """Return the ``[lo, hi)`` global-node index range owned by the
    system behind ``base_prefix`` (e.g. ``"systems/Cuba"``). ``None``
    means no slicing applies (single-system run, or layout attrs
    missing)."""
    if not base_prefix:
        return None
    name = base_prefix.split("/")[-1]
    a = h5f.attrs
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


def _tech_belongs_to_system(tech_name: str, system_name: str) -> bool:
    """Tech dataset keys follow ``"{System} - {Tech}"`` or
    ``"Investment {System} - {Tech}"`` — match by system prefix."""
    if not system_name:
        return True
    rest = tech_name
    if rest.startswith("Investment "):
        rest = rest[len("Investment "):]
    return rest.startswith(system_name + " ")


class _SlicedDatasetView:
    """h5py.Dataset proxy that slices node axes on read.

    ``mode`` selects the slicing rule:
    - ``"axis0"``: ``arr[lo:hi, ...]``
    - ``"both"``:  ``arr[lo:hi, lo:hi, ...]``
    - ``"rooftop"``: ``arr[:, lo:hi].T`` (root is ``[T, N]``)
    - ``"none"``: no slicing (time-only or scalar datasets).
    """

    __slots__ = ("_ds", "_lo", "_hi", "_mode")

    def __init__(self, ds, lo: int, hi: int, mode: str):
        self._ds = ds
        self._lo = lo
        self._hi = hi
        self._mode = mode

    @property
    def shape(self):
        s = self._ds.shape
        n = self._hi - self._lo
        if self._mode == "axis0":
            return (n,) + s[1:]
        if self._mode == "both":
            return (n, n) + s[2:]
        if self._mode == "rooftop":
            return (n, s[0]) if len(s) == 2 else s
        return s

    @property
    def dtype(self):
        return self._ds.dtype

    @property
    def ndim(self):
        return len(self.shape)

    @property
    def attrs(self):
        return self._ds.attrs

    def __len__(self):
        return self.shape[0]

    def _materialise(self):
        if self._mode == "axis0":
            return self._ds[self._lo:self._hi]
        if self._mode == "both":
            return self._ds[self._lo:self._hi, self._lo:self._hi]
        if self._mode == "rooftop":
            return self._ds[:, self._lo:self._hi].T
        return self._ds[...]

    def __getitem__(self, key):
        if key is Ellipsis or key == () or key == slice(None):
            return self._materialise()
        # For complex indexing we materialise first; charts always use
        # ``[:]`` so the overhead does not matter.
        return self._materialise()[key]

    def __array__(self, dtype=None, copy=None):
        arr = self._materialise()
        if dtype is not None:
            arr = arr.astype(dtype, copy=False)
        return arr


class _SlicedGroupView:
    """h5py.Group proxy for sub-groups of a scenario.

    When ``leaf_name`` names a per-tech group (``generation``,
    ``lcoe`` …), iteration and membership are filtered to
    system-owned techs and dataset children are returned as
    ``axis0``-sliced views."""

    __slots__ = ("_g", "_lo", "_hi", "_sys", "_is_tech_group")

    def __init__(self, g, lo: int, hi: int, sys_name: str, leaf_name: str):
        self._g = g
        self._lo = lo
        self._hi = hi
        self._sys = sys_name
        self._is_tech_group = leaf_name in _TECH_GROUPS

    @property
    def attrs(self):
        return self._g.attrs

    def __contains__(self, key):
        if "/" in key:
            head, rest = key.split("/", 1)
            return head in self and rest in self[head]
        if key not in self._g:
            return False
        if self._is_tech_group and not _tech_belongs_to_system(key, self._sys):
            return False
        return True

    def keys(self):
        if self._is_tech_group:
            return [k for k in self._g.keys()
                    if _tech_belongs_to_system(k, self._sys)]
        return list(self._g.keys())

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())

    def __getitem__(self, key):
        if "/" in key:
            head, rest = key.split("/", 1)
            return self[head][rest]
        obj = self._g[key]
        if isinstance(obj, _DATASET_T):
            # Per-tech leaves in tech groups are [N, T]; otherwise
            # leave untouched.
            mode = "axis0" if self._is_tech_group else "none"
            return _SlicedDatasetView(obj, self._lo, self._hi, mode)
        return _SlicedGroupView(obj, self._lo, self._hi, self._sys, key)


class _SlicedScenarioView:
    """h5py.Group proxy for a scenario, slicing the node axis to
    emulate the per-system mirror that used to live under
    ``/systems/{name}/detailed_results/{scenario}/``."""

    __slots__ = ("_g", "_lo", "_hi", "_sys")

    def __init__(self, g, lo: int, hi: int, sys_name: str):
        self._g = g
        self._lo = lo
        self._hi = hi
        self._sys = sys_name

    @property
    def attrs(self):
        return self._g.attrs

    @property
    def name(self):
        # Include the system slug so per-scenario caches keyed on
        # ``.name`` do not collide between systems sharing the same
        # root scenario group.
        return f"{self._g.name}#sys={self._sys}"

    def __contains__(self, key):
        if "/" in key:
            head, rest = key.split("/", 1)
            return head in self and rest in self[head]
        return key in self._g

    def keys(self):
        return self._g.keys()

    def __iter__(self):
        return iter(self._g)

    def __getitem__(self, key):
        if "/" in key:
            head, rest = key.split("/", 1)
            return self[head][rest]
        if key == "electricity_prices":
            # Derive per-system average from nodal_electricity_prices
            # sliced to this system's node range, so the time series
            # reflects the subsystem's price (not the global average).
            if "nodal_electricity_prices" in self._g:
                nep = self._g["nodal_electricity_prices"]
                if nep.ndim == 2 and nep.shape[0] >= self._hi:
                    return _ArrayShim(
                        np.nan_to_num(nep[self._lo:self._hi, :], nan=0.0).mean(axis=0)
                    )
            # Fall through to raw root dataset when nodal data is absent.
        obj = self._g[key]
        if isinstance(obj, _DATASET_T):
            if key in _SLICE_AXIS0_DATASETS:
                # Downgrade to no-slicing for legacy 1D layouts where the
                # node axis was already aggregated away (e.g. pre-Phase 2-bis
                # curtailment).
                mode = "axis0" if obj.ndim >= 2 else "none"
            elif key in _SLICE_BOTH_AXES_DATASETS:
                mode = "both"
            elif key == _ROOFTOP_KEY:
                mode = "rooftop"
            else:
                mode = "none"
            return _SlicedDatasetView(obj, self._lo, self._hi, mode)
        return _SlicedGroupView(obj, self._lo, self._hi, self._sys, key)


# Type tuples are populated at the end of the slicing-helper block,
# once _ArrayShim and _SyntheticSummary are defined.


def _open_scenario(h5f: h5py.File, base_prefix: str, sc_key: str):
    """Open a scenario as a Group (or Group-like proxy) for the
    system behind ``base_prefix``.

    Falls back to the legacy per-system mirror when present, so old
    result files keep working unchanged."""
    if not base_prefix:
        return h5f["detailed_results"][sc_key]
    legacy = f"{base_prefix}/detailed_results"
    if legacy in h5f and sc_key in h5f[legacy]:
        return h5f[legacy][sc_key]
    rng = _system_node_range(h5f, base_prefix)
    if rng is None or "detailed_results" not in h5f or sc_key not in h5f["detailed_results"]:
        # Single-system run, or root data missing — read root straight.
        if "detailed_results" in h5f and sc_key in h5f["detailed_results"]:
            return h5f["detailed_results"][sc_key]
        raise KeyError(sc_key)
    name = base_prefix.split("/")[-1]
    return _SlicedScenarioView(
        h5f["detailed_results"][sc_key], rng[0], rng[1], name
    )


def _open_system_config(h5f: h5py.File, base_prefix: str):
    """Open the system_configuration group for the system behind
    ``base_prefix``.

    - Empty bp → root group (no filtering).
    - Legacy per-system block present → that group (back-compat).
    - Otherwise → ``_SystemConfigView`` over the root group, filtering
      generators / technologies / batteries / battery_technologies by
      system prefix and slicing the node arrays."""
    if not base_prefix:
        return h5f["system_configuration"] if "system_configuration" in h5f else None
    legacy = f"{base_prefix}/system_configuration"
    if legacy in h5f:
        return h5f[legacy]
    if "system_configuration" not in h5f:
        return None
    name = base_prefix.split("/")[-1]
    rng = _system_node_range(h5f, base_prefix)
    return _SystemConfigView(h5f["system_configuration"], name, rng)


class _FilteredConfigGroup:
    """Group proxy for ``system_configuration/{generators,batteries,
    technologies,battery_technologies}`` that keeps only the entries
    whose ``attrs["key"]`` starts with ``"{system}__"`` or whose
    ``attrs["name"]`` starts with ``"{system}/"``.

    **Preserves the original HDF5 child keys** (e.g. ``technology_5``,
    ``technology_8``) rather than renumbering them to ``technology_0``,
    ``technology_1``. This is what callers expect when they look up by
    the *global* index returned by ``_cumulative_tech_investments`` —
    the attrs ``investment_tech_investment_power_{t}_{n}`` written to
    the root scenario use global ``t`` indices, and the labels read
    here must agree.

    The HDF5 group lists children in **lexicographic** order
    (``technology_0, technology_1, technology_10, technology_11, …``),
    which silently mis-aligns indices when callers iterate. We sort
    by the trailing integer so iteration order matches the index order
    a human would expect.

    The ``num_*`` attr reports the **filtered** count so iteration via
    ``range(num_*)`` no longer works — call sites must iterate the
    keys directly (``for k in grp: …``) or use ``len(grp)``."""

    __slots__ = ("_g", "_sys", "_singular", "_num_attr", "_filtered_keys")

    def __init__(self, g, sys_name: str, singular: str, num_attr: str):
        self._g = g
        self._sys = sys_name
        self._singular = singular
        self._num_attr = num_attr
        self._filtered_keys: list[str] | None = None

    def _build(self) -> list[str]:
        """Return the ordered list of original HDF5 keys that belong to
        this subsystem. Cached after the first call."""
        if self._filtered_keys is not None:
            return self._filtered_keys
        out: list[str] = []
        for child_key in self._g.keys():
            obj = self._g[child_key]
            key_attr = obj.attrs.get("key", "")
            if isinstance(key_attr, bytes):
                key_attr = key_attr.decode()
            name_attr = obj.attrs.get("name", "")
            if isinstance(name_attr, bytes):
                name_attr = name_attr.decode()
            if (str(key_attr).startswith(f"{self._sys}__")
                    or str(name_attr).startswith(f"{self._sys}/")):
                out.append(child_key)
        # Sort numerically by the trailing index so HDF5's lexicographic
        # ordering doesn't surface to callers.
        def _idx(k: str) -> int:
            try:
                return int(k.rsplit("_", 1)[-1])
            except ValueError:
                return 1 << 30
        out.sort(key=_idx)
        self._filtered_keys = out
        return out

    @property
    def attrs(self):
        # The runner-written ``num_*`` attr on the underlying group is
        # the GLOBAL count; we report the FILTERED count so callers
        # iterating ``range(num_*)`` see only this subsystem's items.
        # All other attribute keys delegate to the underlying group.
        underlying = self._g.attrs
        num_attr_name = self._num_attr
        filtered_n = len(self._build())

        class _A:
            def get(self, key, default=None):
                if key == num_attr_name:
                    return filtered_n
                return underlying.get(key, default)
            def __getitem__(self, key):
                if key == num_attr_name:
                    return filtered_n
                return underlying[key]
            def __contains__(self, key):
                if key == num_attr_name:
                    return True
                return key in underlying
            def __iter__(self):
                return iter(underlying)
            def items(self):
                return underlying.items()
        return _A()

    def __contains__(self, key):
        return key in self._build()

    def keys(self):
        return list(self._build())

    def __iter__(self):
        return iter(self._build())

    def __len__(self):
        return len(self._build())

    def __getitem__(self, key):
        if key not in self._build():
            raise KeyError(key)
        return self._g[key]


class _SlicedNodesGroup:
    """Proxy for ``system_configuration/nodes`` that slices the
    coordinate / name arrays to this system's node range."""

    __slots__ = ("_g", "_lo", "_hi")

    def __init__(self, g, lo: int, hi: int):
        self._g = g
        self._lo = lo
        self._hi = hi

    @property
    def attrs(self):
        class _A:
            def __init__(self, n):
                self._n = n
            def get(self, key, default=None):
                return self._n if key == "num_nodes" else default
            def __getitem__(self, key):
                return self._n if key == "num_nodes" else None
            def __contains__(self, key):
                return key == "num_nodes"
        return _A(self._hi - self._lo)

    def __contains__(self, key):
        return key in self._g

    def keys(self):
        return self._g.keys()

    def __iter__(self):
        return iter(self._g)

    def __getitem__(self, key):
        ds = self._g[key]
        return _ArrayShim(ds[self._lo:self._hi])


class _SystemConfigView:
    """Proxy for the root ``system_configuration`` group that
    exposes the same shape as the legacy per-system mirror by
    filtering / slicing on read."""

    _SECTIONS = {
        "generators":          ("generator", "num_generators"),
        "batteries":           ("battery", "num_batteries"),
        "technologies":        ("technology", "num_technologies"),
        "battery_technologies": ("battery_technology", "num_battery_technologies"),
    }

    __slots__ = ("_g", "_sys", "_rng")

    def __init__(self, g, sys_name: str, rng):
        self._g = g
        self._sys = sys_name
        self._rng = rng

    @property
    def attrs(self):
        return self._g.attrs

    def __contains__(self, key):
        return key in self._g

    def keys(self):
        return self._g.keys()

    def __iter__(self):
        return iter(self._g)

    def __getitem__(self, key):
        obj = self._g[key]
        if key in self._SECTIONS:
            singular, num_attr = self._SECTIONS[key]
            return _FilteredConfigGroup(obj, self._sys, singular, num_attr)
        if key == "nodes" and self._rng is not None:
            return _SlicedNodesGroup(obj, self._rng[0], self._rng[1])
        return obj


def _open_summary_results(h5f: h5py.File, base_prefix: str):
    """Open a system's summary_results group.

    Returns the legacy per-system group when present. When absent,
    aggregates the relevant scalars on-the-fly from the root
    detailed_results sliced to the system's node range, and returns
    them as a dict-like ``_SyntheticSummary`` shim."""
    if not base_prefix:
        return h5f["summary_results"] if "summary_results" in h5f else None
    legacy = f"{base_prefix}/summary_results"
    if legacy in h5f:
        return h5f[legacy]
    if "summary_results" not in h5f:
        return None
    rng = _system_node_range(h5f, base_prefix)
    if rng is None:
        return h5f["summary_results"]
    name = base_prefix.split("/")[-1]
    return _SyntheticSummary(h5f, rng[0], rng[1], name)


# ──────────────────────────────────────────────────────────────
# Capability detection — drives chart filtering in ResultsDialog
# and variable filtering in ResultsPanel so the user never sees
# entries whose data is missing from the active HDF5.
# ──────────────────────────────────────────────────────────────

def _detect_capabilities(h5f: h5py.File) -> set[str]:
    """Inspect ``h5f`` and return the set of capability tags it has.

    The tags are stable, lowercase identifiers a caller can match
    against ``CHART_CLASS._REQUIRES`` (a set of tag strings) or against
    the per-variable map in ``results_panel``. New capabilities can be
    added by extending this function and tagging the consumers.

    Tags currently emitted:
      - ``"mga"`` — ``/mga/`` group with at least one non-optimal
        alternative.
      - ``"primary_energy"`` — at least one scenario carries a
        ``primary_energy/`` sub-group (fuel supply / Sankey charts).
      - ``"power_flow"`` — at least one scenario carries a
        ``power_flow`` dataset (inter-node flow chart, map flow lines).
      - ``"multi_node"`` — root attr ``num_nodes`` > 1.
      - ``"investment"`` — at least one scenario carries
        ``investment_*`` attrs (capacity-expansion runs).
      - ``"detailed_results"`` — at least one populated scenario in
        ``/detailed_results/`` (gates almost every operational chart).
      - ``"mode_development"`` / ``"mode_unit_commitment"`` /
        ``"mode_economic_dispatch"`` — read from the ``simulation_mode``
        root attr written by ``runner.py:5899``. Mutually exclusive
        within a single HDF5; future UC-specific charts gate on
        ``"mode_unit_commitment"`` so they only surface for the right
        kind of run.
    """
    caps: set[str] = set()

    # Simulation mode (authoritative, no heuristics): the runner stamps
    # this attr from config.simulation_mode before writing any results,
    # so the viewer can trust it. Falls back to the empty string for
    # legacy HDF5s that pre-date the attribute — in that case no
    # mode-specific tag is emitted and mode-gated charts stay hidden.
    sim_mode = h5f.attrs.get("simulation_mode", "")
    if isinstance(sim_mode, bytes):
        sim_mode = sim_mode.decode()
    sim_mode = str(sim_mode).strip().lower()
    if sim_mode in ("development", "unit_commitment", "economic_dispatch"):
        caps.add(f"mode_{sim_mode}")

    # /mga/ with usable alternatives
    mga = h5f.get("mga")
    if mga is not None:
        n_alts = int(mga.attrs.get("num_alternatives", 0))
        if n_alts >= 2:  # 1 = only the cost-optimal seed, no alternatives
            caps.add("mga")

    # Multi-node?
    if int(h5f.attrs.get("num_nodes", 1)) > 1:
        caps.add("multi_node")

    # Probe the first scenario for the other capabilities.
    det = h5f.get("detailed_results")
    if det is not None and len(list(det.keys())) > 0:
        caps.add("detailed_results")
        first_key = sorted(det.keys())[0]
        sc = det[first_key]
        if "primary_energy" in sc:
            caps.add("primary_energy")
        if "power_flow" in sc:
            caps.add("power_flow")
        # investment_* attrs live on the scenario, not as sub-groups
        for attr_key in sc.attrs:
            if attr_key.startswith("investment_"):
                caps.add("investment")
                break

    return caps


# Map chart class name → set of capability tags the chart requires.
# A chart whose requirements are not all present in the active HDF5
# is filtered out of the sidebar before instantiation.
# Charts not listed are assumed to be always-available.
_CHART_REQUIREMENTS: dict[str, set[str]] = {
    # MGA / SPORES — need the /mga/ group with alternatives
    "MGARobustnessFrontierChart":  {"mga"},
    "MGAParcoordsChart":           {"mga"},
    "MGAPathwayChart":             {"mga"},
    "MGASpatialChart":             {"mga"},
    "MGAProjectionChart":          {"mga"},
    "MGAAnnotatedDendrogramChart": {"mga"},
    "MGADecisionFactorsChart":     {"mga"},
    "MGACompositionChart":         {"mga"},
    "MGASimilarityChart":          {"mga"},
    # Primary-energy / fuel-supply analytics
    "FuelSupplyChart":             {"primary_energy"},
    # SankeyEnergyFlowChart is intentionally NOT gated on primary_energy:
    # it builds its primary-energy column from the generation series +
    # per-tech fuel configs, so it renders meaningfully even when the
    # ``/detailed_results/<scenario>/primary_energy`` group is absent.
    # Multi-node only
    "InterNodeFlowsChart":         {"multi_node"},
    # UC Operations — gated on simulation_mode == unit_commitment so the
    # category is hidden in development / economic_dispatch runs.
    "UCHourlyPriceChart":          {"mode_unit_commitment"},
    "UCCommitmentHeatmapChart":    {"mode_unit_commitment"},
    "UCDispatchStackChart":        {"mode_unit_commitment"},
    "UCLoadShedCurtailmentChart":  {"mode_unit_commitment"},
    "UCMarginalTechChart":         {"mode_unit_commitment"},
    "UCPriceDurationChart":        {"mode_unit_commitment"},
    "UCStorageSOCChart":           {"mode_unit_commitment"},
    "UCNetLoadDurationChart":      {"mode_unit_commitment"},
    "UCRampDistributionChart":     {"mode_unit_commitment"},
    # LMP by Node requires multi-node + nodal prices to be interesting;
    # capability check on multi_node hides it for single-node runs where
    # all nodes would coincide.
    "UCLMPByNodeChart":            {"mode_unit_commitment", "multi_node"},
    # Planning-only charts — inherently multi-year evolution / financial
    # analytics. A UC run carries a single year of hourly data and these
    # charts collapse to a single point or a blank canvas, so we gate
    # them on ``mode_development``. Side benefit: the sidebar in UC runs
    # only shows charts that produce meaningful output.
    "SystemMetricsEvolutionChart": {"mode_development"},
    "CFLcoeVallcoeChart":          {"mode_development"},
    "ElectricityCostChart":        {"mode_development"},
    "RevenueProfitabilityChart":   {"mode_development"},
    "CarbonPenaltyChart":          {"mode_development"},
    "CashFlowChart":               {"mode_development"},
    "BatteryHeatmapChart":         {"mode_development"},
    "PriceDurationChart":          {"mode_development"},
    # Multi-year-flavored charts the user vetoed for UC: ``Generation
    # Mix`` and ``Battery Operation`` collapse to ~1 bucket in a 24h UC
    # window; the Sankey + Fuel Supply tell a horizon-scale story that
    # doesn't fit a single-week operational view.
    "GenerationMixChart":          {"mode_development"},
    "BatteryOperationChart":       {"mode_development"},
    "SankeyEnergyFlowChart":       {"mode_development"},
    "FuelSupplyChart":             {"mode_development"},
}


def _available_chart_classes(h5_path, capabilities: set[str] | None = None) -> list:
    """Return ``_CHART_CLASSES`` filtered by the active HDF5's
    capabilities. ``h5_path`` may be ``None`` (returns the full list).

    ``capabilities`` lets callers reuse a previously-detected set
    (e.g. by ResultsPanel which needs the same information for its
    variable combo) instead of probing the file twice.
    """
    if capabilities is None:
        if h5_path is None:
            return list(_CHART_CLASSES)
        try:
            with h5py.File(h5_path, "r") as f:
                capabilities = _detect_capabilities(f)
        except Exception:
            return list(_CHART_CLASSES)

    out = []
    for cls in _CHART_CLASSES:
        req = _CHART_REQUIREMENTS.get(cls.__name__, set())
        if req.issubset(capabilities):
            out.append(cls)
    return out


class _SyntheticSummary:
    """Dict-like aggregate of per-system summary scalars rebuilt from
    the root detailed_results when the per-system mirror is absent.

    Computed lazily, cached per instance. Mirrors the keys the charts
    read: ``year``, ``co2_emissions``, ``renewable_penetration``,
    ``loss_of_load``, ``total_cost`` (the last is a copy of the
    system-wide cost since cost is not separable per-system here)."""

    def __init__(self, h5f, lo: int, hi: int, sys_name: str):
        self._h5f = h5f
        self._lo = lo
        self._hi = hi
        self._sys = sys_name
        self._cache: dict = {}

    @property
    def attrs(self):
        return self._h5f["summary_results"].attrs

    def __contains__(self, key):
        return key in {"year", "co2_emissions", "renewable_penetration",
                       "loss_of_load", "total_cost"}

    def keys(self):
        return ["year", "co2_emissions", "renewable_penetration",
                "loss_of_load", "total_cost"]

    def __iter__(self):
        return iter(self.keys())

    def __getitem__(self, key):
        if key in self._cache:
            return self._cache[key]
        arr = self._compute(key)
        self._cache[key] = _ArrayShim(arr)
        return self._cache[key]

    def _compute(self, key: str) -> np.ndarray:
        sr = self._h5f.get("summary_results")
        if key == "year":
            return sr["year"][:] if sr is not None and "year" in sr else np.array([])
        if key == "total_cost":
            # Cost is system-global; per-system attribution lives elsewhere.
            return sr["total_cost"][:] if sr is not None and "total_cost" in sr else np.array([])
        # Aggregate from detailed_results sliced to this system.
        dr = self._h5f.get("detailed_results")
        if dr is None:
            return np.array([])
        ordered = sorted(
            dr.keys(),
            key=lambda k: int(dr[k].attrs.get("year", 0))
            if "year" in dr[k].attrs else 0,
        )
        vals = []
        for sc_key in ordered:
            sc = dr[sc_key]
            if key == "co2_emissions":
                if "CO2_emissions" not in sc:
                    vals.append(0.0); continue
                arr = sc["CO2_emissions"][self._lo:self._hi]
                vals.append(float(np.sum(arr)))
            elif key == "loss_of_load":
                if "loss_load" not in sc:
                    vals.append(0.0); continue
                arr = sc["loss_load"][self._lo:self._hi]
                vals.append(float(np.sum(arr)))
            elif key == "renewable_penetration":
                vals.append(self._renewable_penetration_for(sc))
        return np.asarray(vals)

    # Fuel values that count as renewable when the config-derived
    # lookup is unavailable (very old HDF5 files).
    _RENEWABLE_FUELS = frozenset({
        "sun", "wind", "water", "biomass", "geothermal", "tidal", "wave",
    })

    def _renewable_lookup(self) -> Optional[set]:
        """Set of h5safe-canonical generation dataset keys whose
        config-declared type is ``"Renewable"``, built from
        ``system_configuration/{generators,technologies}``. This is
        the authoritative classification — it reads the same
        ``attrs["type"]`` field the runner wrote when building the
        system config block."""
        cached = self._cache.get("_re_lookup")
        if cached is not None:
            return cached
        sc = self._h5f.get("system_configuration")
        if sc is None:
            return None
        names: set = set()
        gens = sc.get("generators")
        if gens is not None:
            n = int(gens.attrs.get("num_generators", 0))
            for i in range(n):
                gk = f"generator_{i}"
                if gk not in gens:
                    continue
                g = gens[gk]
                t = g.attrs.get("type", "")
                if isinstance(t, bytes):
                    t = t.decode()
                if str(t) == "Renewable":
                    nm = g.attrs.get("name", "")
                    if isinstance(nm, bytes):
                        nm = nm.decode()
                    names.add(str(nm).replace("/", " - "))
        techs = sc.get("technologies")
        if techs is not None:
            n = int(techs.attrs.get("num_technologies", 0))
            for i in range(n):
                tk = f"technology_{i}"
                if tk not in techs:
                    continue
                t = techs[tk]
                ttype = t.attrs.get("type", "")
                if isinstance(ttype, bytes):
                    ttype = ttype.decode()
                if str(ttype) == "Renewable":
                    nm = t.attrs.get("name", "")
                    if isinstance(nm, bytes):
                        nm = nm.decode()
                    # Investment generators are written as
                    # ``"Investment <h5safe(tech.name)>"`` so register
                    # both prefixed and bare forms.
                    safe = str(nm).replace("/", " - ")
                    names.add(safe)
                    names.add(f"Investment {safe}")
        self._cache["_re_lookup"] = names
        return names

    def _renewable_penetration_for(self, sc) -> float:
        """Σ RE generation in slice / Σ all generation in slice.

        Note: this intentionally differs from the legacy per-system
        ``summary_results.renewable_penetration``, which was derived
        as ``re_gen / demand`` and is known to be unreliable for
        multi-system runs with shared investments (see the dashboard
        helper ``_re_share_from_mix`` that bypasses the legacy summary
        for the same reason). The mix-based ratio reported here is
        what the rest of the UI considers authoritative."""
        if "generation" not in sc:
            return 0.0
        g = sc["generation"]
        re_lookup = self._renewable_lookup()
        re_total = 0.0
        all_total = 0.0
        for name in g.keys():
            if not _tech_belongs_to_system(name, self._sys):
                continue
            ds = g[name]
            arr = ds[self._lo:self._hi]
            tot = float(np.sum(np.clip(arr, 0, None)))
            all_total += tot
            if re_lookup is not None:
                is_re = name in re_lookup
            else:
                fuel = ds.attrs.get("fuel")
                if fuel is not None:
                    if isinstance(fuel, bytes):
                        fuel = fuel.decode()
                    is_re = str(fuel).strip().lower() in self._RENEWABLE_FUELS
                else:
                    nl = name.lower()
                    is_re = any(k in nl for k in (
                        "solar", "wind", "hydro", "hidro", "bioel", "biomass",
                        "eólic", "eolic", "fotovolt", "rooftop",
                    ))
            if is_re:
                re_total += tot
        return (re_total / all_total) if all_total > 0 else 0.0


class _ArrayShim:
    """Bare-minimum wrapper that makes a numpy array look like an
    h5py.Dataset for ``arr[:]`` access."""

    __slots__ = ("_a",)

    def __init__(self, a: np.ndarray):
        self._a = np.asarray(a)

    def __getitem__(self, key):
        if key is Ellipsis or key == () or key == slice(None):
            return self._a
        return self._a[key]

    @property
    def shape(self):
        return self._a.shape

    @property
    def dtype(self):
        return self._a.dtype

    def __len__(self):
        return self._a.shape[0] if self._a.ndim else 0

    def __array__(self, dtype=None, copy=None):
        return self._a if dtype is None else self._a.astype(dtype, copy=False)


# Type tuples used in isinstance checks across the chart code:
# treat the slicing proxies as if they were the real h5py types.
_DATASET_T = (h5py.Dataset, _SlicedDatasetView, _ArrayShim)
_GROUP_T = (h5py.Group, _SlicedGroupView, _SlicedScenarioView, _SyntheticSummary)


def _get_gen_types(h5f: h5py.File, base_prefix: str = "") -> dict[str, str]:
    """Return {gen_name: type} from system_configuration."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.gen_types:
        return cache.gen_types[base_prefix]
    types: dict[str, str] = {}
    sc = _open_system_config(h5f, base_prefix)
    if sc is None or "generators" not in sc:
        if cache is not None:
            cache.gen_types[base_prefix] = types
        return types
    gen_grp = sc["generators"]
    n = int(gen_grp.attrs.get("num_generators", 0))
    for i in range(n):
        gk = f"generator_{i}"
        if gk in gen_grp:
            g = gen_grp[gk]
            name = g.attrs.get("name", gk)
            if isinstance(name, bytes):
                name = name.decode()
            gtype = g.attrs.get("type", "Unknown")
            if isinstance(gtype, bytes):
                gtype = gtype.decode()
            types[name] = gtype
    if cache is not None:
        cache.gen_types[base_prefix] = types
    return types


def _get_node_names(h5f: h5py.File, base_prefix: str = "") -> list[str]:
    cache = _active_cache()
    if cache is not None and base_prefix in cache.node_names:
        return cache.node_names[base_prefix]
    out: list[str] = []
    sc = _open_system_config(h5f, base_prefix)
    if sc is not None and "nodes" in sc:
        ng = sc["nodes"]
        # Try both dataset names: "name" (current) and "nodes_names" (legacy)
        for ds_key in ("name", "nodes_names"):
            if ds_key in ng:
                raw = ng[ds_key][:]
                out = [n.decode() if isinstance(n, bytes) else str(n) for n in raw]
                break
    if cache is not None:
        cache.node_names[base_prefix] = out
    return out


def _canonical_tech_name(name: str) -> tuple[str, str]:
    """Map a generator/component name to (canonical_label, category).

    Returns (label, category) where category is one of: renewable, rooftop,
    thermal, storage_discharge, storage_charge, curtailment, spillage, reserve.

    Used ONLY for synthesised series whose name we control directly
    (Battery discharge, Battery charge, Curtailment, Spillage, Reserves,
    EV V2G, Solar rooftop). For real generator data, the caller resolves
    label / colour / category via the explicit ``technology`` field in
    ``system_configuration/generators`` — see :func:`_build_gen_tech_map`.
    """
    nl = name.lower()
    if "rooftop" in nl:
        return "Solar rooftop", "rooftop"
    if "spillage" in nl:
        return "Spillage", "spillage"
    if "discharge" in nl:
        return "Battery discharge", "storage_discharge"
    if "charge" in nl:
        return "Battery charge", "storage_charge"
    if "battery" in nl or "storage" in nl or "li-ion" in nl:
        return name, "storage_discharge"
    if "curtailment" in nl:
        return "Curtailment", "curtailment"
    if "reserve" in nl:
        return "Reserves", "reserve"
    return name, "thermal"


def _categorize_gen_names(
    names: list[str],
    gen_tech_map: dict[str, dict] | None = None,
) -> dict[str, list[str]]:
    """Classify generator/component names into technology categories.

    Returns dict with keys: renewable, rooftop, thermal, storage_discharge,
    storage_charge, curtailment, spillage, reserve.

    If ``gen_tech_map`` is supplied (built via :func:`_build_gen_tech_map`
    from the HDF5 ``technology`` field), its ``category`` entry is used
    directly — no name-based heuristic. Synthesised series (Battery
    discharge, Curtailment, ...) fall back to :func:`_canonical_tech_name`.
    """
    cats: dict[str, list[str]] = {
        "renewable": [], "rooftop": [], "thermal": [],
        "storage_discharge": [], "storage_charge": [],
        "curtailment": [], "spillage": [], "reserve": [],
    }
    m = gen_tech_map or {}
    for name in names:
        info = _resolve_gen_tech(name, m) if m else None
        if info is not None:
            cats[info["category"]].append(name)
        else:
            _, cat = _canonical_tech_name(name)
            cats[cat].append(name)
    return cats


# Maps the ``type`` attribute of a TechnologyConfig (written verbatim
# into the HDF5 technology entry) onto the visualisation categories.
_TECH_TYPE_TO_CATEGORY = {
    "renewable":     "renewable",
    "non-renewable": "thermal",
    "non_renewable": "thermal",
    "storage":       "storage_discharge",
    "electrolyzer":  "thermal",
}

# Generators that share a name get an ``(1)``, ``(2)`` ... suffix
# appended by h5py when their groups collide. Same for the
# ``"Investment "`` prefix the runner adds to investment-series
# entries that mirror a TechnologyConfig.
import re as _re
_NAME_SUFFIX_RE = _re.compile(r"\s*\(\d+\)$")
_INVESTMENT_PREFIX = "Investment "


def _resolve_gen_tech(name: str, gen_tech_map: dict[str, dict]) -> dict | None:
    """Look up ``name`` in ``gen_tech_map`` tolerating two transforms
    that the HDF5 writer / loader silently introduces:

    * Duplicate generator names get an ``(N)`` suffix appended by
      h5py (two generators with the same ``name`` in
      system_configuration end up as ``X`` and ``X (1)`` in
      ``detailed_results/.../generation``).
    * Investment-tied series carry an ``"Investment "`` prefix so
      the runner can distinguish dispatch of existing vs newly
      built capacity.

    Returns the resolved info dict or ``None`` if neither form
    matches — caller decides how to fall back honestly.
    """
    if name in gen_tech_map:
        return gen_tech_map[name]
    if name.startswith(_INVESTMENT_PREFIX):
        sans_prefix = name[len(_INVESTMENT_PREFIX):]
        if sans_prefix in gen_tech_map:
            return gen_tech_map[sans_prefix]
        sans_both = _NAME_SUFFIX_RE.sub("", sans_prefix)
        if sans_both != sans_prefix and sans_both in gen_tech_map:
            return gen_tech_map[sans_both]
    sans_suffix = _NAME_SUFFIX_RE.sub("", name)
    if sans_suffix != name and sans_suffix in gen_tech_map:
        return gen_tech_map[sans_suffix]
    return None


def _build_gen_tech_map(
    gen_configs: list[dict],
    tech_configs: list[dict],
    bat_configs: list[dict] | None = None,
    bat_tech_configs: list[dict] | None = None,
) -> dict[str, dict]:
    """Return ``{generator_or_battery_name: {label, color, category, tech_key}}``
    by matching each component's explicit ``technology`` attribute
    (written by the runner from ``GeneratorConfig.technology``) against
    the technology table's ``key`` attribute. No name heuristics.

    Generators whose technology can't be resolved are intentionally
    *omitted* from the map — callers see ``None`` and can decide
    whether to fall back to ``_canonical_tech_name`` (legacy HDF5s
    that pre-date the ``technology`` field) or render them raw.
    """
    # Step 1: index techs by the short key (``key`` is system-scoped
    # like ``Cuba__tech_wind``; the generator's ``technology`` attr
    # carries just ``tech_wind``). Also indexes the full prefixed key
    # so a generator that happens to store the long form still resolves.
    techs: dict[str, dict] = {}
    for src in (tech_configs or [], bat_tech_configs or []):
        is_storage = src is bat_tech_configs
        for cfg in src:
            key = cfg.get("key", "")
            if isinstance(key, bytes):
                key = key.decode()
            key = str(key)
            if not key:
                continue
            name = cfg.get("name", "")
            if isinstance(name, bytes):
                name = name.decode()
            color = cfg.get("color", "")
            if isinstance(color, bytes):
                color = color.decode()
            ttype = cfg.get("type", "")
            if isinstance(ttype, bytes):
                ttype = ttype.decode()
            # Strip the system prefix from the display label
            # ("Cuba/Wind Turbine" → "Wind Turbine") so identical
            # technologies across systems collapse into one legend
            # entry in cross-system charts.
            display = str(name).split("/", 1)[-1] if "/" in str(name) else str(name)
            if is_storage:
                category = "storage_discharge"
            else:
                category = _TECH_TYPE_TO_CATEGORY.get(
                    str(ttype).strip().lower(), "thermal",
                )
            info = {
                "label":    display,
                "color":    str(color),
                "category": category,
                "tech_key": key,
            }
            short = key.split("__", 1)[-1]
            techs[short] = info
            techs[key] = info

    # Step 2: walk every generator/battery and resolve its tech entry.
    # We index by BOTH the original name (``Cuba/Generator node/6225271924``)
    # AND the display form produced by ``_load_gen_data`` when the
    # name contains slashes (``Cuba - Generator node - 6225271924``).
    # h5py uses ``/`` as a path separator, so generators with slashes
    # in their name are written as nested groups and the loader
    # reconstructs the label with ``" - "``. Without indexing both,
    # the lookup against the loaded payload silently misses every
    # generator whose name was slash-segmented.
    out: dict[str, dict] = {}
    for src, default_cat in ((gen_configs or [], None),
                             (bat_configs or [], "storage_discharge")):
        for cfg in src:
            name = cfg.get("name", "")
            if isinstance(name, bytes):
                name = name.decode()
            name = str(name)
            if not name:
                continue
            tech_id = cfg.get("technology", "")
            if isinstance(tech_id, bytes):
                tech_id = tech_id.decode()
            tech_id = str(tech_id).strip()
            if not (tech_id and tech_id in techs):
                continue  # leave unmapped — caller falls back honestly.
            info = techs[tech_id]
            if default_cat is not None:
                # Battery: keep category fixed regardless of the
                # tech entry's nominal type.
                info = {**info, "category": default_cat}
            out[name] = info
            display_name = name.replace("/", " - ")
            if display_name != name:
                out[display_name] = info

    # Step 3: also index every TechnologyConfig by its full name so
    # synthesised series the runner writes as "Investment {tech.name}"
    # (e.g. "Investment Cuba - Wind Turbine" — see runner's investment
    # block) can be resolved via the "Investment " prefix stripper in
    # ``_resolve_gen_tech``.
    for src in (tech_configs or [], bat_tech_configs or []):
        is_storage = src is bat_tech_configs
        for cfg in src:
            name = cfg.get("name", "")
            if isinstance(name, bytes):
                name = name.decode()
            name = str(name)
            if not name:
                continue
            short_key = ""
            key = cfg.get("key", "")
            if isinstance(key, bytes):
                key = key.decode()
            key = str(key)
            if key:
                short_key = key.split("__", 1)[-1]
            if short_key in techs:
                info = techs[short_key]
            elif key in techs:
                info = techs[key]
            else:
                continue
            if is_storage:
                info = {**info, "category": "storage_discharge"}
            out.setdefault(name, info)
            display_name = name.replace("/", " - ")
            if display_name != name:
                out.setdefault(display_name, info)
    return out


def _build_tech_label_index(
    tech_configs: list[dict],
    bat_tech_configs: list[dict] | None = None,
) -> dict[str, dict]:
    """Return ``{tech_full_name: {label, color, category, tech_key}}``
    so investment series (which reference a TechnologyConfig by name)
    can be resolved without name heuristics. Same {label, color,
    category} schema as :func:`_build_gen_tech_map`.
    """
    out: dict[str, dict] = {}
    for src in (tech_configs or [], bat_tech_configs or []):
        is_storage = src is bat_tech_configs
        for cfg in src:
            name = cfg.get("name", "")
            if isinstance(name, bytes):
                name = name.decode()
            name = str(name)
            if not name:
                continue
            color = cfg.get("color", "")
            if isinstance(color, bytes):
                color = color.decode()
            ttype = cfg.get("type", "")
            if isinstance(ttype, bytes):
                ttype = ttype.decode()
            key = cfg.get("key", "")
            if isinstance(key, bytes):
                key = key.decode()
            display = name.split("/", 1)[-1] if "/" in name else name
            if is_storage:
                category = "storage_discharge"
            else:
                category = _TECH_TYPE_TO_CATEGORY.get(
                    str(ttype).strip().lower(), "thermal",
                )
            out[name] = {
                "label":    display,
                "color":    str(color),
                "category": category,
                "tech_key": str(key),
            }
    return out


def _aggregate_by_technology(
    gen_data: dict[str, np.ndarray], tres: int, resolution: str = "monthly",
    gen_tech_map: dict[str, dict] | None = None,
) -> dict[str, np.ndarray]:
    """Aggregate generation data by technology.

    Takes ``{gen_name: array}`` and returns
    ``{tech_label: summed_aggregated_array}``. When ``gen_tech_map``
    is provided (recommended), each generator's bucket comes from its
    explicit ``technology`` attribute resolved against the technology
    table. Generators not in the map (legacy HDF5 without the field)
    fall back to :func:`_canonical_tech_name` to keep older files
    rendering without crashing.
    """
    tech_agg: dict[str, np.ndarray] = {}
    m = gen_tech_map or {}
    for name, arr in gen_data.items():
        info = _resolve_gen_tech(name, m) if m else None
        if info is not None:
            label = info["label"]
        else:
            label, _ = _canonical_tech_name(name)
        total = _sum_nodes(arr) if arr.ndim >= 2 else arr
        agg = _aggregate(total, resolution, tres)
        if label in tech_agg:
            ml = min(len(tech_agg[label]), len(agg))
            tech_agg[label][:ml] += agg[:ml]
            if len(agg) > len(tech_agg[label]):
                tech_agg[label] = np.concatenate([
                    tech_agg[label], agg[len(tech_agg[label]):]
                ])
        else:
            tech_agg[label] = agg.copy()
    return tech_agg


def _load_config_section(
    h5f: h5py.File, base_prefix: str, section: str,
    singular: str, num_attr: str,
) -> list[dict]:
    """Iterate ``{singular}_{i}`` children of a system_configuration
    section (``generators`` / ``batteries`` / ``technologies`` /
    ``battery_technologies``) and return the list of ``dict(attrs)``."""
    configs: list[dict] = []
    sc = _open_system_config(h5f, base_prefix)
    if sc is None or section not in sc:
        return configs
    grp = sc[section]
    n = int(grp.attrs.get(num_attr, 0))
    for i in range(n):
        ck = f"{singular}_{i}"
        if ck in grp:
            configs.append(dict(grp[ck].attrs))
    return configs


def _load_gen_configs(h5f: h5py.File, base_prefix: str = "") -> list[dict]:
    """Load generator configs from system_configuration."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.gen_configs:
        return cache.gen_configs[base_prefix]
    configs = _load_config_section(
        h5f, base_prefix, "generators", "generator", "num_generators")
    if cache is not None:
        cache.gen_configs[base_prefix] = configs
    return configs


def _load_bat_configs(h5f: h5py.File, base_prefix: str = "") -> list[dict]:
    """Load battery configs from system_configuration."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.bat_configs:
        return cache.bat_configs[base_prefix]
    configs = _load_config_section(
        h5f, base_prefix, "batteries", "battery", "num_batteries")
    if cache is not None:
        cache.bat_configs[base_prefix] = configs
    return configs


def _load_tech_configs(h5f: h5py.File, base_prefix: str = "") -> list[dict]:
    """Load technology investment configs from system_configuration."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.tech_configs:
        return cache.tech_configs[base_prefix]
    configs = _load_config_section(
        h5f, base_prefix, "technologies", "technology", "num_technologies")
    if cache is not None:
        cache.tech_configs[base_prefix] = configs
    return configs


def _load_bat_tech_configs(h5f: h5py.File, base_prefix: str = "") -> list[dict]:
    """Load battery technology investment configs from system_configuration."""
    cache = _active_cache()
    if cache is not None and base_prefix in cache.bat_tech_configs:
        return cache.bat_tech_configs[base_prefix]
    configs = _load_config_section(
        h5f, base_prefix, "battery_technologies",
        "battery_technology", "num_battery_technologies")
    if cache is not None:
        cache.bat_tech_configs[base_prefix] = configs
    return configs


def _load_investment_data(sc: h5py.Group, tech_configs=None, bat_tech_configs=None,
                         fallback_sc: h5py.Group = None) -> dict:
    """Load investment data from a scenario group.

    Reads from two sources:
    1. Scenario attrs: investment_tech_investment_power_{t}_{n} (MasterProblem)
       and investment_bat_tech_investment_{power|capacity}_{bt}_{n}
    2. Scenario sub-groups: gen_investment_power, bat_investment_power (operational)

    If *fallback_sc* is provided (e.g. global scenario for per-system views),
    investment attrs are read from there when the primary scenario has none.

    Returns dict with:
      tech_investments: {tech_name: total_MW}
      bat_tech_power_investments: {tech_name: total_MW}
      bat_tech_capacity_investments: {tech_name: total_MWh}
      tech_costs: {tech_name: invest_cost_per_MW}
      bat_tech_costs: {tech_name: invest_cost_per_MW}
      gen_investment_power: np.array (legacy, per-generator)
      bat_investment_power: np.array (legacy, per-battery)
    """
    out = {}

    # --- MasterProblem technology investments (from scenario attrs) ---
    tech_inv = {}  # {tech_idx: total_MW}
    bat_tech_pow_inv = {}  # {bt_idx: total_MW}
    bat_tech_cap_inv = {}  # {bt_idx: total_MWh}

    # Use the scenario that has investment attrs (fall back to global)
    attrs_source = sc
    has_inv_attrs = any(k.startswith("investment_") for k in sc.attrs)
    if not has_inv_attrs and fallback_sc is not None:
        attrs_source = fallback_sc

    for attr_key, attr_val in attrs_source.attrs.items():
        if not attr_key.startswith("investment_"):
            continue
        suffix = attr_key[len("investment_"):]

        if suffix.startswith("tech_investment_power_"):
            parts = suffix.split("_")
            # tech_investment_power_{t}_{n}
            t_idx = int(parts[-2])
            tech_inv[t_idx] = tech_inv.get(t_idx, 0.0) + float(attr_val)

        elif suffix.startswith("bat_tech_investment_power_"):
            parts = suffix.split("_")
            bt_idx = int(parts[-2])
            bat_tech_pow_inv[bt_idx] = bat_tech_pow_inv.get(bt_idx, 0.0) + float(attr_val)

        elif suffix.startswith("bat_tech_investment_capacity_"):
            parts = suffix.split("_")
            bt_idx = int(parts[-2])
            bat_tech_cap_inv[bt_idx] = bat_tech_cap_inv.get(bt_idx, 0.0) + float(attr_val)

    # Map tech indices to names and costs
    tech_configs = tech_configs or []
    bat_tech_configs = bat_tech_configs or []

    tech_investments = {}
    tech_costs = {}
    for t_idx, total_mw in tech_inv.items():
        if t_idx < len(tech_configs):
            tc = tech_configs[t_idx]
            name = tc.get("name", f"Technology_{t_idx}")
            if isinstance(name, bytes):
                name = name.decode()
            tech_investments[name] = total_mw
            if "invest_cost" in tc:
                tech_costs[name] = _parse_invest_cost(tc["invest_cost"])
        else:
            tech_investments[f"Technology_{t_idx}"] = total_mw
    out["tech_investments"] = tech_investments
    out["tech_costs"] = tech_costs

    bat_tech_power_investments = {}
    bat_tech_costs = {}
    for bt_idx, total_mw in bat_tech_pow_inv.items():
        if bt_idx < len(bat_tech_configs):
            btc = bat_tech_configs[bt_idx]
            name = btc.get("name", f"BatTech_{bt_idx}")
            if isinstance(name, bytes):
                name = name.decode()
            bat_tech_power_investments[name] = total_mw
            if "invest_cost_power" in btc:
                bat_tech_costs[name] = _parse_invest_cost(btc["invest_cost_power"])
        else:
            bat_tech_power_investments[f"BatTech_{bt_idx}"] = total_mw
    out["bat_tech_power_investments"] = bat_tech_power_investments
    out["bat_tech_costs"] = bat_tech_costs

    bat_tech_capacity_investments = {}
    for bt_idx, total_mwh in bat_tech_cap_inv.items():
        if bt_idx < len(bat_tech_configs):
            btc = bat_tech_configs[bt_idx]
            name = btc.get("name", f"BatTech_{bt_idx}")
            if isinstance(name, bytes):
                name = name.decode()
            bat_tech_capacity_investments[name] = total_mwh
        else:
            bat_tech_capacity_investments[f"BatTech_{bt_idx}"] = total_mwh
    out["bat_tech_capacity_investments"] = bat_tech_capacity_investments

    # --- Operational dispatch investments (legacy sub-groups) ---
    for key in ("gen_investment_power", "bat_investment_power", "bat_investment_capacity"):
        if key in sc:
            obj = sc[key]
            if isinstance(obj, _DATASET_T):
                arr = obj[:]
                if arr.ndim == 2:
                    out[key] = arr.sum(axis=1)
                else:
                    out[key] = arr
            elif isinstance(obj, _GROUP_T):
                # Per-generator/battery datasets within a group
                vals = {}
                for name in obj:
                    if isinstance(obj[name], _DATASET_T):
                        vals[name] = float(np.sum(obj[name][:]))
                if vals:
                    out[key + "_by_name"] = vals

    return out


def _load_decommissioning_data(
    sc: h5py.Group, gen_configs: list[dict] = None,
    fallback_sc: h5py.Group = None,
) -> tuple[dict[str, float], dict[int, float]]:
    """Load decommissioning (retirement) data.

    Reads from:
    1. Scenario attrs: retirement_gen_{idx} (cumulative fraction retired)
    2. Scenario sub-groups: decommissioning, gen_forced_replacement (legacy)

    If *fallback_sc* is provided, retirement attrs are read from there when
    the primary scenario has none.

    Returns:
      (by_name: {tech_name: total_MW}, raw_fractions: {gen_idx: fraction})
    """
    out = {}
    raw_fractions = {}  # {gen_idx: fraction} for incremental computation

    # --- MasterProblem retirements (from scenario attrs) ---
    gen_configs = gen_configs or []
    attrs_source = sc
    has_ret_attrs = any(k.startswith("retirement_") for k in sc.attrs)
    if not has_ret_attrs and fallback_sc is not None:
        attrs_source = fallback_sc
    for attr_key, attr_val in attrs_source.attrs.items():
        if not attr_key.startswith("retirement_"):
            continue
        suffix = attr_key[len("retirement_"):]

        if suffix.startswith("gen_"):
            try:
                g_idx = int(suffix.split("_")[1])
            except (ValueError, IndexError):
                continue
            fraction = float(attr_val)
            raw_fractions[g_idx] = fraction
            if fraction > 0 and g_idx < len(gen_configs):
                gc = gen_configs[g_idx]
                name = gc.get("name", f"Gen_{g_idx}")
                if isinstance(name, bytes):
                    name = name.decode()
                # Compute MW retired from fraction × rated_power
                rated = gc.get("rated_power", 0)
                rated_mw = _parse_invest_cost(rated)  # handles str/list/scalar
                ret_mw = fraction * rated_mw
                out[name] = out.get(name, 0.0) + ret_mw

    # --- Explicit decommissioning group (if present) ---
    if "decommissioning" in sc:
        grp = sc["decommissioning"]
        for name in grp:
            if isinstance(grp[name], _DATASET_T):
                data = grp[name][:]
                out[name] = float(data.sum()) if data.size > 0 else 0.0

    # Legacy support
    for legacy_key in ("gen_forced_replacement", "bat_forced_replacement"):
        if legacy_key in sc and not out:
            grp = sc[legacy_key]
            for name in grp:
                if isinstance(grp[name], _DATASET_T):
                    data = grp[name][:]
                    out[name] = float(data.sum()) if data.size > 0 else 0.0

    return out, raw_fractions


def _parse_invest_cost(val) -> float:
    """Parse invest_cost from HDF5 attrs (can be scalar, array, or string)."""
    if isinstance(val, (int, float, np.integer, np.floating)):
        return float(val)
    if isinstance(val, np.ndarray):
        return float(np.mean(val))
    if isinstance(val, (bytes, str)):
        s = val.decode() if isinstance(val, bytes) else val
        import ast
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple)):
                return float(np.mean(parsed))
            return float(parsed)
        except Exception:
            return 0.0
    return 0.0


# ──────────────────────────────────────────────────────────────
# Base chart class
# ──────────────────────────────────────────────────────────────

class ResultsChartBase(FigureCanvasQTAgg):
    """Base matplotlib chart widget with optional parameter toolbar."""

    TITLE = "Chart"
    TR_KEY = ""  # Translation key; resolved at combo population time.

    def __init__(self, nrows: int = 1, ncols: int = 1, figsize=(10, 6)):
        self.fig = Figure(figsize=figsize, dpi=100)
        super().__init__(self.fig)
        if nrows * ncols == 1:
            self.ax = self.fig.add_subplot(111)
        else:
            self.axes = self.fig.subplots(nrows, ncols)
        self.fig.tight_layout(pad=2.5)
        self._loaded = False
        # Snapshot primary axes so we can remove colorbars / twin axes later
        self._primary_axes: set = set(self.fig.axes)

    def _remove_extra_axes(self):
        """Remove any axes not in the original set (colorbars, twin axes)."""
        for ax in self.fig.axes[:]:
            if ax not in self._primary_axes:
                ax.remove()
        # Also remove figure-level legends carried over by some charts
        for leg in self.fig.legends[:]:
            leg.remove()

    def get_params_widget(self) -> Optional[QWidget]:
        """Override to return a controls row for chart parameters."""
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kwargs):
        """Subclass must override: read HDF5 + render."""
        raise NotImplementedError

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        # Remove stale colorbars / twin axes BEFORE rendering
        self._remove_extra_axes()
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            if hasattr(self, "ax"):
                self.ax.clear()
                self.ax.text(0.5, 0.5, f"Error: {e}", transform=self.ax.transAxes,
                             ha="center", va="center", fontsize=10, color="red")
            self.draw()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 1 — Generation Mix (Plotly interactive, monthly stacked
# area + yearly investments/retirements, rendered via QWebEngine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _MixChartBridge(QObject):
    """QWebChannel bridge — exposes the payload to the JS renderer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class _LoggingWebPage(QWebEnginePage):
    """QWebEnginePage subclass that forwards JS console warnings and
    errors to the Python logger. INFO-level messages (typical chatter
    from Plotly / Mapbox / our own scripts) are dropped to keep the
    Python output readable; flip to ``DEBUG`` to see everything."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # type: ignore[override]
        # Qt levels: 0=info, 1=warning, 2=error.
        try:
            lvl = int(level)
        except Exception:
            lvl = 0
        if lvl <= 0:
            # Drop info-level noise; surface via DEBUG for those who
            # want it without polluting normal stderr.
            src = (source_id or "").rsplit("/", 1)[-1] or "?"
            logger.debug("[JS INFO] %s (%s:%d)", message, src, int(line_number))
            return
        lvl_name = "WARN" if lvl == 1 else "ERROR"
        src = (source_id or "").rsplit("/", 1)[-1] or "?"
        logger.warning(
            "[JS %s] %s (%s:%d)", lvl_name, message, src, int(line_number),
        )



class GenerationMixChart(QWidget):
    """Interactive Plotly version of the Generation Mix chart.

    Same data pipeline as the original matplotlib implementation
    (preserved in git for reference) but rendered with Plotly.js inside
    a ``QWebEngineView``. Mirrors the dashboard's static-HTML + QWebChannel
    pattern: a small ``mix_chart.html`` shell loads ``plotly.min.js`` and
    ``mix_chart.js``; ``mix_chart.js`` pulls the figure payload from this
    widget's ``_MixChartBridge`` and calls ``Plotly.react``.

    Duck-typed for ``results_dialog.py``: ``TITLE``, ``TR_KEY``, ``fig=None``
    (triggers ``export_image`` instead of ``savefig`` and 600px minHeight),
    ``update_chart``, ``_safe_update``, ``_loaded``, ``get_params_widget``.
    """

    TITLE = "Generation Mix"
    TR_KEY = "results_charts.gen_mix"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _MixChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        # Allow the loaded file:// page to fetch sibling assets
        # (plotly.min.js, mix_chart.js).
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "mix_chart.html")))

        # Duck-typed surface for results_dialog
        self.fig = None
        self._loaded = False

    # ── Public chart API ─────────────────────────────────────────

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        # JS bootstraps with refresh() on DOMContentLoaded; this kick
        # handles the case where Python updates land after page load.
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        # No matplotlib figure to savefig. Export the current page as
        # PDF via QtWebEngine's printToPdf — works whatever the user
        # picked from the file-type filter (Plotly's modebar already
        # offers PNG via its built-in download button on the chart).
        from PySide6.QtCore import QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape,
            margins=QSizeF(15, 15) if False else None,  # default margins
        )
        self._view.page().printToPdf(str(file_path))

    # ── Payload builder — mirrors the matplotlib data pipeline ────

    def _build_payload(
        self, h5_path: Path, years: list[int], **kw,
    ) -> dict:
        """Read HDF5 and return the JSON-friendly dict the JS consumes.

        Preserves the exact same series, agglomerations and category
        ordering as the legacy matplotlib chart so the two are visually
        equivalent — only the rendering changes.
        """
        bp = kw.get("base_prefix", "")
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
            tech_configs = _load_tech_configs(h5f, bp)
            bat_tech_configs = _load_bat_tech_configs(h5f, bp)
            tech_colors = _build_tech_color_map(h5f, bp)
            # Explicit gen → tech resolution from HDF5
            # ``GeneratorConfig.technology`` ↔ ``TechnologyConfig.key``.
            # Every entry has {label, color, category, tech_key} so we
            # never have to guess from the generator name.
            gen_tech_map = _build_gen_tech_map(
                gen_configs, tech_configs, bat_configs, bat_tech_configs,
            )

            all_gen: dict[str, list] = {}
            demand_all: list = []
            year_list: list[int] = []
            total_months = 0

            gen_inv_by_tech: dict[str, np.ndarray] = {}
            bat_inv_by_tech: dict[str, np.ndarray] = {}
            ret_by_tech: dict[str, np.ndarray] = {}
            total_cost_by_year: list[float] = []
            master_re_targets: list[float] = []
            # Battery operation data entries are named after their
            # battery technology ("Investment Cuba - Li-ion Battery");
            # collect them so the operation series can inherit the
            # configured battery-technology colour.
            battery_entry_names: set[str] = set()

            scenarios = list(_sorted_scenarios(h5f, bp))
            prev_retirement_fracs: dict[int, float] = {}
            _is_per_system = bp.startswith("systems/")
            global_tech_configs = (
                _load_tech_configs(h5f, "") if _is_per_system else tech_configs
            )
            global_bat_tech_configs = (
                _load_bat_tech_configs(h5f, "") if _is_per_system else bat_tech_configs
            )
            # Tech-name → info for investment-series lookups (full
            # display name like "Cuba/Solar PV"). Same shape as the
            # entries in ``gen_tech_map``. Derived label index lets
            # us look up by short canonical label too.
            tech_name_to_info = _build_tech_label_index(
                global_tech_configs, global_bat_tech_configs,
            )
            label_to_info: dict[str, dict] = {}
            for _info in (
                list(gen_tech_map.values()) + list(tech_name_to_info.values())
            ):
                label_to_info.setdefault(_info["label"], _info)

            for sc_key, year in scenarios:
                sc = _open_scenario(h5f, bp, sc_key)
                master_re_targets.append(
                    float(sc.attrs.get("master_re_target", 0.0)) * 100
                )
                year_list.append(year)
                months_this_year = 0

                gen_data = _load_gen_data(sc)
                tech_monthly = _aggregate_by_technology(
                    gen_data, tres, "monthly", gen_tech_map=gen_tech_map,
                )
                for tech_label, monthly in tech_monthly.items():
                    all_gen.setdefault(tech_label, []).extend(monthly.tolist())
                    months_this_year = max(months_this_year, len(monthly))

                for bkey, label in [("battery_discharge", "Battery discharge"),
                                    ("battery_charge", "Battery charge")]:
                    bat = _load_bat_data(sc, bkey)
                    if bat:
                        year_total = None
                        for bname, arr in bat.items():
                            battery_entry_names.add(bname)
                            t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                            m = np.array(_aggregate(t, "monthly", tres))
                            if year_total is None:
                                year_total = m
                            else:
                                ml = min(len(year_total), len(m))
                                year_total[:ml] += m[:ml]
                            months_this_year = max(months_this_year, len(m))
                        if year_total is not None:
                            all_gen.setdefault(label, []).extend(year_total.tolist())

                bat_spill = _load_bat_data(sc, "battery_spillage")
                if bat_spill:
                    year_spill = None
                    for bname, arr in bat_spill.items():
                        battery_entry_names.add(bname)
                        t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                        m = np.array(_aggregate(t, "monthly", tres))
                        if year_spill is None:
                            year_spill = m
                        else:
                            ml = min(len(year_spill), len(m))
                            year_spill[:ml] += m[:ml]
                    if year_spill is not None:
                        all_gen.setdefault("Battery spillage", []).extend(year_spill.tolist())

                for ev_key, ev_label in [("EV_V2G", "V2G discharge"),
                                         ("EV_charging", "V2G charge")]:
                    if ev_key in sc:
                        ev_arr = sc[ev_key][:]
                        t = _sum_nodes(ev_arr) if ev_arr.ndim >= 2 else ev_arr
                        m = _aggregate(t, "monthly", tres)
                        all_gen.setdefault(ev_label, []).extend(m.tolist())

                if "curtailment" in sc:
                    curt = sc["curtailment"][:]
                    ct = _sum_nodes(curt) if curt.ndim >= 2 else curt
                    cm = _aggregate(ct, "monthly", tres)
                    all_gen.setdefault("Curtailment", []).extend(cm.tolist())

                for rkey, rlabel in [("reserve_dynamic", "Dynamic reserve"),
                                     ("reserve_static", "Static reserve")]:
                    if rkey in sc:
                        rd = sc[rkey][:]
                        rt = _sum_nodes(rd) if rd.ndim >= 2 else rd
                        rm = _aggregate(rt, "monthly", tres)
                        all_gen.setdefault(rlabel, []).extend(rm.tolist())

                if "demand" in sc:
                    dem = sc["demand"][:]
                    dt = _sum_nodes(dem) if dem.ndim >= 2 else dem
                    dm = _aggregate(dt, "monthly", tres)
                    demand_all.extend(dm.tolist())
                    months_this_year = max(months_this_year, len(dm))

                if "rooftop_generation" in sc:
                    rg = sc["rooftop_generation"][:]
                    rgt = _sum_nodes(rg) if rg.ndim >= 2 else rg
                    rgm = _aggregate(rgt, "monthly", tres)
                    all_gen.setdefault("Solar rooftop", []).extend(rgm.tolist())

                if months_this_year == 0:
                    months_this_year = 12
                total_months += months_this_year

                # ── Investment data ──
                year_idx = len(year_list) - 1
                fallback = None
                if _is_per_system and "detailed_results" in h5f and sc_key in h5f["detailed_results"]:
                    fallback = h5f["detailed_results"][sc_key]
                inv_data = _load_investment_data(
                    sc, tech_configs=global_tech_configs,
                    bat_tech_configs=global_bat_tech_configs,
                    fallback_sc=fallback,
                )
                year_cost = 0.0
                _sys_filter = bp.split("/")[-1] if _is_per_system else None

                for tech_name, inv_mw in inv_data.get("tech_investments", {}).items():
                    if inv_mw > 0 and (_sys_filter is None or tech_name.startswith(_sys_filter + "/")):
                        info = tech_name_to_info.get(tech_name)
                        if info is None:
                            continue
                        canon = info["label"]
                        if canon not in gen_inv_by_tech:
                            gen_inv_by_tech[canon] = np.zeros(len(scenarios))
                        gen_inv_by_tech[canon][year_idx] += float(inv_mw) / 1000
                        cost_per_mw = inv_data.get("tech_costs", {}).get(tech_name, 0)
                        year_cost += float(inv_mw) * cost_per_mw / 1e6

                for bt_name, inv_mw in inv_data.get(
                    "bat_tech_power_investments", {}
                ).items():
                    if inv_mw > 0 and (_sys_filter is None or bt_name.startswith(_sys_filter + "/")):
                        info = tech_name_to_info.get(bt_name)
                        if info is None:
                            continue
                        canon = info["label"]
                        if canon not in bat_inv_by_tech:
                            bat_inv_by_tech[canon] = np.zeros(len(scenarios))
                        bat_inv_by_tech[canon][year_idx] += float(inv_mw) / 1000
                        cost_per_mw = inv_data.get("bat_tech_costs", {}).get(bt_name, 0)
                        year_cost += float(inv_mw) * cost_per_mw / 1e6

                if "gen_investment_power" in inv_data:
                    for gi, inv_mw in enumerate(inv_data["gen_investment_power"]):
                        if inv_mw > 0 and gi < len(gen_configs):
                            gc = gen_configs[gi]
                            gn = gc.get("name", f"Gen_{gi}")
                            if isinstance(gn, bytes):
                                gn = gn.decode()
                            info = _resolve_gen_tech(gn, gen_tech_map)
                            if info is None:
                                continue
                            gn_canon = info["label"]
                            if gn_canon not in gen_inv_by_tech:
                                gen_inv_by_tech[gn_canon] = np.zeros(len(scenarios))
                            gen_inv_by_tech[gn_canon][year_idx] += float(inv_mw) / 1000
                            if "invest_cost" in gc:
                                year_cost += float(inv_mw) * _parse_invest_cost(gc["invest_cost"]) / 1e6

                if "bat_investment_power" in inv_data:
                    for bi, inv_mw in enumerate(inv_data["bat_investment_power"]):
                        if inv_mw > 0 and bi < len(bat_configs):
                            bc = bat_configs[bi]
                            bn = bc.get("name", f"Battery_{bi}")
                            if isinstance(bn, bytes):
                                bn = bn.decode()
                            info = _resolve_gen_tech(bn, gen_tech_map)
                            if info is None:
                                continue
                            bn_canon = info["label"]
                            if bn_canon not in bat_inv_by_tech:
                                bat_inv_by_tech[bn_canon] = np.zeros(len(scenarios))
                            bat_inv_by_tech[bn_canon][year_idx] += float(inv_mw) / 1000
                            if "invest_cost" in bc:
                                year_cost += float(inv_mw) * _parse_invest_cost(bc["invest_cost"]) / 1e6

                total_cost_by_year.append(year_cost)

                # ── Retirements (incremental) ──
                ret_gen_configs = _load_gen_configs(h5f, "") if _is_per_system else gen_configs
                _decomm, raw_fracs = _load_decommissioning_data(
                    sc, gen_configs=ret_gen_configs, fallback_sc=fallback,
                )
                for g_idx, frac in raw_fracs.items():
                    prev = prev_retirement_fracs.get(g_idx, 0.0)
                    delta = frac - prev
                    if delta > 1e-6 and g_idx < len(ret_gen_configs):
                        gc = ret_gen_configs[g_idx]
                        name = gc.get("name", f"Gen_{g_idx}")
                        if isinstance(name, bytes):
                            name = name.decode()
                        if _sys_filter and not name.startswith(_sys_filter + "/"):
                            continue
                        rated = gc.get("rated_power", 0)
                        rated_mw = _parse_invest_cost(rated)
                        inc_mw = delta * rated_mw
                        info = _resolve_gen_tech(name, gen_tech_map)
                        if info is None:
                            continue
                        canon = info["label"]
                        if canon not in ret_by_tech:
                            ret_by_tech[canon] = np.zeros(len(scenarios))
                        ret_by_tech[canon][year_idx] += inc_mw / 1000
                prev_retirement_fracs = raw_fracs.copy()

        if not all_gen:
            return {"year_list": [], "total_months": 0}

        # Pad shorter series
        for key in list(all_gen):
            arr = np.array(all_gen[key], dtype=float)
            if len(arr) < total_months:
                arr = np.pad(arr, (0, total_months - len(arr)), mode="constant")
            all_gen[key] = arr
        demand_array = np.array(demand_all, dtype=float) if demand_all else None
        if demand_array is not None and len(demand_array) < total_months:
            demand_array = np.pad(demand_array,
                                  (0, total_months - len(demand_array)),
                                  mode="constant")

        cats = _categorize_gen_names(list(all_gen.keys()), gen_tech_map=label_to_info)

        # Battery operation series inherit the configured colour of the
        # battery technology that produced them. The data entries are
        # named after the tech ("Investment Cuba - Li-ion Battery"), so
        # we resolve them through gen_tech_map (which indexes battery
        # techs by name). Charge is a lighter shade and spillage darker,
        # derived from the same config colour so the three read
        # distinctly while still honouring the user's choice. With a
        # single battery tech this is exact; with several it uses the
        # first resolved colour (the aggregate series can only be one).
        battery_overrides: dict[str, str] = {}
        _bat_color = ""
        for _bname in battery_entry_names:
            _info = _resolve_gen_tech(_bname, gen_tech_map)
            if _info and _info.get("color"):
                _bat_color = _info["color"]
                break
        if _bat_color:
            battery_overrides["Battery discharge"] = _bat_color
            battery_overrides["Battery charge"] = _lighten(_bat_color, 0.5)
            battery_overrides["Battery spillage"] = _darken(_bat_color, 0.4)

        def _resolve_color(tech_label: str) -> str:
            if tech_label in battery_overrides:
                return battery_overrides[tech_label]
            info = label_to_info.get(tech_label)
            if info and info.get("color"):
                return info["color"]
            return _color_for(tech_label, tech_colors)

        # Positive stack (subplot a): renewable + rooftop + thermal + storage_discharge
        positive_series = []
        for cat_key in ("renewable", "rooftop", "thermal", "storage_discharge"):
            for tech in cats[cat_key]:
                if tech in all_gen and np.any(all_gen[tech] > 0):
                    positive_series.append({
                        "label": tech,
                        "values_gwh": (all_gen[tech] / 1000).tolist(),
                        "color": _resolve_color(tech),
                        "category": cat_key,
                    })

        # Negative stack (subplot a): storage_charge, curtailment, spillage, reserve
        negative_series = []
        for cat_key in ("storage_charge", "curtailment", "spillage", "reserve"):
            for tech in cats[cat_key]:
                if tech in all_gen and np.any(all_gen[tech] > 0):
                    negative_series.append({
                        "label": tech,
                        "values_gwh": (all_gen[tech] / 1000).tolist(),
                        "color": _resolve_color(tech),
                        "category": cat_key,
                    })

        # Renewable penetration (% by month)
        zero_arr = np.zeros(total_months, dtype=float)
        renewable_total = sum(
            (all_gen.get(t, zero_arr) for t in cats["renewable"]),
            start=zero_arr.copy(),
        )
        thermal_total = sum(
            (all_gen.get(t, zero_arr) for t in cats["thermal"]),
            start=zero_arr.copy(),
        )
        total_gen_arr = renewable_total + thermal_total
        with np.errstate(divide="ignore", invalid="ignore"):
            re_pen = np.divide(
                renewable_total, total_gen_arr,
                out=np.zeros_like(total_gen_arr, dtype=float),
                where=total_gen_arr != 0,
            ) * 100.0

        # RE target step function
        re_target_arr = np.zeros(total_months, dtype=float)
        if master_re_targets:
            monthly = []
            for t_val in master_re_targets:
                monthly.extend([t_val] * 12)
            re_target_arr = np.array(monthly[:total_months], dtype=float)
            if len(re_target_arr) < total_months:
                re_target_arr = np.pad(re_target_arr,
                                       (0, total_months - len(re_target_arr)),
                                       mode="edge")

        # Subplot b: investments — order: renewable, batteries, thermal
        n_years = len(year_list)
        inv_series = []
        for tech in cats["renewable"]:
            vals = gen_inv_by_tech.get(tech)
            if vals is not None and np.any(vals > 0):
                inv_series.append({
                    "label": tech,
                    "values_gw": vals[:n_years].tolist(),
                    "color": _resolve_color(tech),
                    "category": "renewable_inv",
                })
        for bn, vals in bat_inv_by_tech.items():
            if np.any(vals > 0):
                inv_series.append({
                    "label": bn,
                    "values_gw": vals[:n_years].tolist(),
                    "color": _resolve_color(bn),
                    "category": "battery_inv",
                })
        for tech in cats["thermal"]:
            vals = gen_inv_by_tech.get(tech)
            if vals is not None and np.any(vals > 0):
                inv_series.append({
                    "label": tech,
                    "values_gw": vals[:n_years].tolist(),
                    "color": _resolve_color(tech),
                    "category": "thermal_inv",
                })

        # Subplot b: retirements
        ret_series = []
        for tech_name, vals in ret_by_tech.items():
            if np.any(vals > 0):
                ret_series.append({
                    "label": f"{tech_name} (retired)",
                    "values_gw": vals[:n_years].tolist(),
                    "color": _resolve_color(tech_name),
                })

        return {
            "year_list": list(year_list),
            "total_months": int(total_months),
            "positive_series": positive_series,
            "negative_series": negative_series,
            "demand_gwh": (demand_array / 1000).tolist() if demand_array is not None else None,
            "operational_re_pct": re_pen.tolist(),
            "re_target_pct": re_target_arr.tolist(),
            "investments": inv_series,
            "retirements": ret_series,
            "cost_musd_by_year": list(total_cost_by_year[:n_years]),
        }



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 3 — Summary Metrics (line + scatter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SummaryMetricsChart(ResultsChartBase):
    TITLE = "Summary Metrics"
    TR_KEY = "results_charts.summary_metrics"

    def __init__(self):
        super().__init__(nrows=1, ncols=2, figsize=(12, 5))

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        ax1, ax2 = self.axes
        ax1.clear()
        ax2.clear()
        with _open_h5(h5_path) as h5f:
            bp = kw.get("base_prefix", "")
            sg = _open_summary_results(h5f, bp)
            if sg is None:
                ax1.text(0.5, 0.5, "No summary data", transform=ax1.transAxes, ha="center")
                self.draw()
                return
            yrs = sg["year"][:] if "year" in sg else np.array([])
            cost = sg["total_cost"][:] if "total_cost" in sg else np.array([])
            re_pen = sg["renewable_penetration"][:] if "renewable_penetration" in sg else np.array([])
            co2 = sg["co2_emissions"][:] if "co2_emissions" in sg else np.array([])

        if len(yrs) > 0 and len(cost) > 0:
            idx = np.argsort(yrs)
            ax1.plot(yrs[idx], cost[idx] / 1e6, "o-", linewidth=2, markersize=5, color="#2980b9")
            ax1.set_xlabel("Year", fontweight="bold")
            ax1.set_ylabel("Total Cost (M$)", fontweight="bold")
            ax1.set_title("System Cost Evolution", fontweight="bold")
            ax1.grid(True, alpha=0.3)

        if len(re_pen) > 0 and len(co2) > 0:
            ax2.scatter(re_pen * 100, co2 / 1e6, s=50, alpha=0.7, c="#27ae60")
            ax2.set_xlabel("RE Penetration (%)", fontweight="bold")
            ax2.set_ylabel("CO2 Emissions (Mt)", fontweight="bold")
            ax2.set_title("Emissions vs RE Penetration", fontweight="bold")
            ax2.grid(True, alpha=0.3)

        self.fig.tight_layout()
        self.draw()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 4 — Battery Heatmap (monthly net flow, smoothed)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _BatteryHeatmapBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class BatteryHeatmapChart(QWidget):
    """Interactive Plotly version of the Battery Heatmap.

    12-month × N-year heatmap of the net battery flow
    (charge − discharge) with optional Gaussian smoothing controlled by
    the params widget. Smoothing is applied Python-side (same scipy
    call as the matplotlib version) so the JS just renders.
    """

    TITLE = "Storage Activity"
    TR_KEY = "results_charts.storage_activity"

    # Plotly.js built-in named colorscales, validated against the
    # bundled plotly.min.js. Order: perceptually-uniform sequential
    # first, then classic / diverging. The Qt combo limits visible
    # entries so the dropdown scrolls instead of stretching the bar.
    _COLORSCALES = [
        "Turbo", "Viridis", "Plasma", "Magma", "Inferno", "Cividis",
        "Jet", "Hot", "Greys", "Electric",
        "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
    ]

    def __init__(self):
        super().__init__()
        self._sigma = 1.0
        self._colormap = "Turbo"

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _BatteryHeatmapBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "battery_heatmap.html")))

        self._last_args: Optional[tuple] = None
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)

        wl.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(self._sigma)
        sp.valueChanged.connect(self._on_sigma_changed)
        wl.addWidget(sp)

        wl.addWidget(QLabel("Colormap"))
        cb = QComboBox()
        cb.addItems(self._COLORSCALES)
        cb.setCurrentText(self._colormap)
        # Compact: cap the popup height so it scrolls instead of
        # expanding to fit all 16 entries.
        cb.setMaxVisibleItems(8)
        cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
        cb.currentTextChanged.connect(self._on_colormap_changed)
        wl.addWidget(cb)

        wl.addStretch()
        return w

    def _on_sigma_changed(self, v: float):
        self._sigma = float(v)
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("BatteryHeatmapChart re-render failed")

    def _on_colormap_changed(self, name: str):
        # Visual-only change — restyle the existing trace in-place
        # instead of rebuilding the payload. The colormap is also
        # baked into the next full render through ``_build_payload``
        # so a sigma change preserves the user's pick.
        self._colormap = str(name)
        import json
        js = (
            f"if (typeof setColormap === 'function') "
            f"setColormap({json.dumps(self._colormap)});"
        )
        self._view.page().runJavaScript(js)

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    @staticmethod
    def _scenario_prices(sc) -> Optional[np.ndarray]:
        for k in ("electricity_prices", "nodal_electricity_prices"):
            if k in sc:
                a = sc[k][:]
                return a.mean(axis=0) if a.ndim == 2 else a
        return None

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        rows: list[np.ndarray] = []
        year_labels: list[str] = []
        # Arbitrage P&L per scenario (subplot b)
        revenue_musd: list[float] = []
        cost_musd: list[float] = []
        discharge_mwh_yr: list[float] = []

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                charge_d = _load_bat_data(sc, "battery_charge")
                discharge_d = _load_bat_data(sc, "battery_discharge")

                total_c = np.zeros(1)
                total_d = np.zeros(1)
                for arr in charge_d.values():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    if len(t) > len(total_c):
                        total_c = np.zeros(len(t))
                    total_c[: len(t)] += t
                for arr in discharge_d.values():
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    if len(t) > len(total_d):
                        total_d = np.zeros(len(t))
                    total_d[: len(t)] += t

                # Subplot a) is now discharge-only — "how much energy
                # the batteries actually delivered each month", easier
                # to read than the prior signed net flow.
                monthly = _aggregate(total_d, "monthly", tres)
                if len(monthly) > 0:
                    if len(monthly) < 12:
                        monthly = np.pad(monthly, (0, 12 - len(monthly)),
                                         mode="constant")
                    rows.append(monthly[:12])
                    year_labels.append(str(year))

                    # Arbitrage P&L: aligns price[t] × charge/discharge
                    # MWh. Skip silently if prices aren't available for
                    # this scenario — the subplot will just show zeros.
                    prices = self._scenario_prices(sc)
                    rev = cost = 0.0
                    d_mwh_total = 0.0
                    if prices is not None and len(total_c) > 1 and len(total_d) > 1:
                        n = min(len(prices), len(total_c), len(total_d))
                        p = prices[:n]
                        c_mwh = total_c[:n] * tres
                        d_mwh = total_d[:n] * tres
                        rev = float(np.sum(p * d_mwh)) / 1e6
                        cost = float(np.sum(p * c_mwh)) / 1e6
                        d_mwh_total = float(np.sum(d_mwh))
                    revenue_musd.append(rev)
                    cost_musd.append(cost)
                    discharge_mwh_yr.append(d_mwh_total)

        if not rows:
            return {"error": "No battery data"}

        data = np.array(rows).T  # shape: [12 months, N years]
        if self._sigma > 0:
            try:
                from scipy.ndimage import gaussian_filter
                data = gaussian_filter(data, sigma=self._sigma)
            except ImportError:
                # scipy missing → skip smoothing rather than failing the chart
                logger.debug("scipy not available; skipping heatmap smoothing")

        # Net margin $/MWh discharged
        margin = [
            ((revenue_musd[i] - cost_musd[i]) * 1e6 / discharge_mwh_yr[i])
            if discharge_mwh_yr[i] > 0 else 0.0
            for i in range(len(year_labels))
        ]

        return {
            "years": year_labels,
            "month_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "values_mwh": data.tolist(),
            "sigma": float(self._sigma),
            "colormap": self._colormap,
            "discharge_revenue_musd": revenue_musd,
            "charge_cost_musd": cost_musd,
            "margin_dollar_per_mwh": margin,
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 5 — Battery Operation (bar chart, charge + / discharge -)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _BatteryOperationBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class BatteryOperationChart(QWidget):
    """Interactive Plotly version of the Battery Operation chart.

    Three bar series at the chosen resolution: Charge (positive),
    Discharge (negative), Spillage (negative, stacked below discharge).
    Resolution combobox in the params widget triggers an immediate
    re-render (same _last_args caching pattern as the other charts).
    """

    TITLE = "Battery Operation"
    TR_KEY = "results_charts.battery_operation"

    def __init__(self):
        super().__init__()
        # Default to ``daily`` for multi-year planning runs; switched to
        # ``hourly`` automatically on the first ``_build_payload`` call
        # when the active HDF5 carries the ``mode_unit_commitment`` tag
        # (UC horizons are 24 h to a few weeks — daily/monthly/yearly
        # bucketing collapses them into 1-bar charts).
        self._resolution = "daily"
        self._user_overrode_resolution = False
        self._mode_synced = False

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _BatteryOperationBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "battery_operation.html")))

        self._last_args: Optional[tuple] = None
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel(tr("results_charts.resolution")))
        cb = QComboBox()
        cb.addItems(["hourly", "daily", "monthly", "yearly"])
        cb.setCurrentText(self._resolution)
        cb.currentTextChanged.connect(self._on_resolution_changed)
        wl.addWidget(cb)
        self._resolution_combo = cb
        wl.addStretch()
        return w

    def _on_resolution_changed(self, name: str):
        self._resolution = str(name)
        # Mark as a sticky user pick so subsequent UC-mode auto-sync
        # doesn't overwrite it.
        self._user_overrode_resolution = True
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("BatteryOperationChart re-render failed")

    def _sync_default_resolution_for_file(self, h5_path: Path) -> None:
        """First time we open a file, pick a sensible default
        resolution. UC runs default to ``hourly`` (24 h horizons would
        collapse to 1 bar otherwise); development / economic_dispatch
        runs keep the multi-year ``daily`` default. Subsequent user
        picks via the combo are sticky."""
        if self._mode_synced or self._user_overrode_resolution:
            return
        try:
            import h5py
            with h5py.File(h5_path, "r") as f:
                sm = f.attrs.get("simulation_mode", "")
                if isinstance(sm, bytes):
                    sm = sm.decode()
                sm = str(sm).strip().lower()
                if sm == "unit_commitment":
                    self._resolution = "hourly"
                    cb = getattr(self, "_resolution_combo", None)
                    if cb is not None:
                        cb.blockSignals(True)
                        cb.setCurrentText("hourly")
                        cb.blockSignals(False)
        except Exception:
            logger.debug(
                "BatteryOperationChart mode-sync failed", exc_info=True
            )
        finally:
            self._mode_synced = True

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._sync_default_resolution_for_file(h5_path)
        self._last_args = (h5_path, list(years), dict(kw))
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        # Multi-year: honor the global range slider. ``year_range`` is
        # ``(y_min, y_max)`` inclusive; if unset or empty we fall back to
        # the full timeline so old call sites keep working.
        year_range = kw.get("year_range")
        if year_range is None:
            if years:
                y_min, y_max = int(years[0]), int(years[-1])
            else:
                y_min, y_max = None, None
        else:
            y_min, y_max = int(year_range[0]), int(year_range[1])

        # Aggregate all batteries (same _total helper as matplotlib)
        def _total(data_dict):
            total = np.zeros(1)
            for arr in data_dict.values():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                if len(t) > len(total):
                    old = total
                    total = np.zeros(len(t))
                    total[:len(old)] = old
                total[:len(t)] += t
            return total

        # Per-year aggregates concatenated along the x axis. For
        # ``yearly`` resolution this yields one point per selected year;
        # for ``monthly`` 12 points per year; for ``daily`` 365 per year.
        per_year_blocks: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]] = []
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            for sc_key, year in scenarios:
                if y_min is not None and (year < y_min or year > y_max):
                    continue
                sc = _open_scenario(h5f, bp, sc_key)
                charge_d = _load_bat_data(sc, "battery_charge")
                discharge_d = _load_bat_data(sc, "battery_discharge")
                spillage_d = _load_bat_data(sc, "battery_spillage")
                c_total = _total(charge_d)
                d_total = _total(discharge_d)
                s_total = _total(spillage_d)
                c_agg = _aggregate(c_total, self._resolution, tres) / 1e3
                d_agg = _aggregate(d_total, self._resolution, tres) / 1e3
                s_agg = _aggregate(s_total, self._resolution, tres) / 1e3
                # Pad shorter series so all three line up — same reason
                # as the single-year version: a missing spillage series
                # would otherwise collapse the year's block to length 1.
                n_y = max(len(c_agg), len(d_agg), len(s_agg))
                if n_y == 0:
                    continue
                def _fit(arr, n=n_y):
                    if len(arr) >= n:
                        return arr[:n]
                    return np.pad(arr, (0, n - len(arr)), mode="constant")
                per_year_blocks.append(
                    (int(year), _fit(c_agg), _fit(d_agg), _fit(s_agg))
                )

        if not per_year_blocks:
            return {"error": "No battery data in selected range"}

        sel_years = [b[0] for b in per_year_blocks]
        c_concat = np.concatenate([b[1] for b in per_year_blocks])
        d_concat = np.concatenate([b[2] for b in per_year_blocks])
        s_concat = np.concatenate([b[3] for b in per_year_blocks])
        x_labels = self._x_labels_multi(self._resolution, per_year_blocks)

        return {
            "year": (
                f"{sel_years[0]}–{sel_years[-1]}"
                if len(sel_years) > 1 else str(sel_years[0])
            ),
            "resolution": self._resolution,
            "x_labels": x_labels,
            "charge_gwh": [float(v) for v in c_concat],
            "discharge_gwh": [float(v) for v in d_concat],
            "spillage_gwh": [float(v) for v in s_concat],
        }

    @staticmethod
    def _x_labels_multi(
        resolution: str,
        per_year_blocks: list[tuple[int, np.ndarray, np.ndarray, np.ndarray]],
    ) -> list[str]:
        """Year-prefixed labels so multi-year concatenated series are
        unambiguous along the x axis."""
        labels: list[str] = []
        if resolution == "monthly":
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            for year, c_agg, _d, _s in per_year_blocks:
                n_y = len(c_agg)
                labels.extend(
                    f"{year}-{months[i % 12]}" if i < 12
                    else f"{year}-M{i+1}"
                    for i in range(n_y)
                )
        elif resolution == "yearly":
            for year, _c, _d, _s in per_year_blocks:
                labels.append(str(year))
        else:  # daily (and any other fallback)
            for year, c_agg, _d, _s in per_year_blocks:
                n_y = len(c_agg)
                labels.extend(f"{year}-D{i+1}" for i in range(n_y))
        return labels





# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 9 — Net Load Heatmap (month × hour-of-day)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _NetLoadHeatmapBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class NetLoadHeatmapChart(QWidget):
    """Interactive Plotly version of the Net Load Heatmap.

    Two side-by-side 12-month × hour-of-day heatmaps for the selected
    year: average net load (MW) and average ramp (MW/h). Defaults
    mirror the matplotlib chart's cmaps (Jet for NL, RdBu_r for ramp).
    Each subplot has an independent colormap dropdown — same scrollable
    Qt combo pattern as BatteryHeatmapChart.
    """

    TITLE = "Net Load Heatmap"
    TR_KEY = "results_charts.net_load_heatmap"

    # Same Plotly.js-validated list as battery_heatmap.
    _COLORSCALES = [
        "Turbo", "Viridis", "Plasma", "Magma", "Inferno", "Cividis",
        "Jet", "Hot", "Greys", "Electric",
        "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
    ]

    def __init__(self):
        super().__init__()
        self._sigma = 1.0
        # Defaults reproduce the matplotlib cmaps: "jet" for net load,
        # "RdBu_r" for the ramp (RdBu + reverse to put red on positive
        # ramps, blue on negative).
        self._colormap_a = "Jet"
        self._colormap_b = "RdBu"
        self._reverse_b = True

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _NetLoadHeatmapBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "net_load_heatmap.html")
        ))

        self._last_args: Optional[tuple] = None
        self.fig = None
        self._loaded = False
        # Per-chart single-year selector — heatmap is hour×day for a
        # single year so the global range slider doesn't apply.
        self._available_years: list[int] = []
        self._selected_year_idx: int = 0
        self._year_combo: Optional[QComboBox] = None

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)

        wl.addWidget(QLabel("Year:"))
        yc = QComboBox()
        yc.addItems([str(y) for y in self._available_years])
        if self._available_years:
            yc.setCurrentIndex(
                min(self._selected_year_idx, len(self._available_years) - 1)
            )
        yc.currentIndexChanged.connect(self._on_year_combo_changed)
        wl.addWidget(yc)
        self._year_combo = yc

        wl.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(self._sigma)
        sp.valueChanged.connect(self._on_sigma_changed)
        wl.addWidget(sp)

        def _make_cmap_combo(default: str) -> QComboBox:
            cb = QComboBox()
            cb.addItems(self._COLORSCALES)
            cb.setCurrentText(default)
            cb.setMaxVisibleItems(8)
            cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
            return cb

        wl.addWidget(QLabel("a) Net Load"))
        cb_a = _make_cmap_combo(self._colormap_a)
        cb_a.currentTextChanged.connect(self._on_colormap_a_changed)
        wl.addWidget(cb_a)

        wl.addWidget(QLabel("b) Ramp"))
        cb_b = _make_cmap_combo(self._colormap_b)
        cb_b.currentTextChanged.connect(self._on_colormap_b_changed)
        wl.addWidget(cb_b)

        wl.addStretch()
        return w

    def set_available_years(self, years: list[int]) -> None:
        prev = (
            self._available_years[self._selected_year_idx]
            if (self._available_years
                and 0 <= self._selected_year_idx < len(self._available_years))
            else None
        )
        self._available_years = list(years)
        if prev is not None and prev in self._available_years:
            self._selected_year_idx = self._available_years.index(prev)
        else:
            self._selected_year_idx = 0
        if self._year_combo is not None:
            self._year_combo.blockSignals(True)
            self._year_combo.clear()
            self._year_combo.addItems([str(y) for y in self._available_years])
            if self._available_years:
                self._year_combo.setCurrentIndex(self._selected_year_idx)
            self._year_combo.blockSignals(False)

    def _on_year_combo_changed(self, idx: int):
        if idx < 0:
            return
        self._selected_year_idx = int(idx)
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("NetLoadHeatmapChart re-render failed")

    def _on_sigma_changed(self, v: float):
        self._sigma = float(v)
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("NetLoadHeatmapChart re-render failed")

    def _set_colormap(self, which: int, name: str):
        """Visual-only update for one subplot's heatmap colorscale."""
        import json
        # Picking a new colormap intentionally drops the reverse flag —
        # if the user wanted reversed they can pick a colormap whose
        # natural direction already matches.
        if which == 0:
            self._colormap_a = str(name)
        else:
            self._colormap_b = str(name)
            self._reverse_b = False
        js = (
            f"if (typeof setColormap === 'function') "
            f"setColormap({int(which)}, {json.dumps(str(name))});"
        )
        self._view.page().runJavaScript(js)

    def _on_colormap_a_changed(self, name: str):
        self._set_colormap(0, name)

    def _on_colormap_b_changed(self, name: str):
        self._set_colormap(1, name)

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        # Single-year chart: ignore the global range slider and use the
        # per-chart combo's year instead.
        kw = dict(kw)
        kw.pop("year_range", None)
        kw["year_idx"] = self._selected_year_idx
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        import pandas as pd
        bp = kw.get("base_prefix", "")
        year_idx = kw.get("year_idx", 0)

        # Detect simulation mode so the heatmap can re-shape its rows
        # to days×hour-of-day in UC runs (a 24h or 7d horizon would
        # leave 11 of the 12 monthly rows empty in the default layout).
        is_uc = False
        try:
            with _open_h5(h5_path) as _f:
                sm = _f.attrs.get("simulation_mode", "")
                if isinstance(sm, bytes):
                    sm = sm.decode()
                is_uc = str(sm).strip().lower() == "unit_commitment"
        except Exception:
            is_uc = False

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            if year_idx >= len(scenarios):
                year_idx = 0
            sc_key, year = scenarios[year_idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "demand" not in sc:
                return {"error": "No demand data"}
            dem = sc["demand"][:]
            demand = _sum_nodes(dem) if dem.ndim >= 2 else dem
            # In UC keep the full operational horizon (often < 1 year);
            # in planning, cap to 1 year to match the month×hour layout.
            yh_cap = demand.size if is_uc else _year_hours(tres)
            demand = demand[:yh_cap]
            gen_data = _load_gen_data(sc)
            re_gen = np.zeros(yh_cap)
            for name, arr in gen_data.items():
                nl = name.lower()
                if "wind" in nl or "solar" in nl:
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    t = t[:yh_cap]
                    re_gen[:len(t)] += t
            net_load = demand[:len(re_gen)] - re_gen[:len(demand)]

        if len(net_load) == 0:
            return {"error": "Empty net load"}

        steps_per_day = max(1, 24 // max(1, tres))
        ramp = np.diff(net_load, prepend=net_load[0])

        if is_uc:
            # Day × hour-of-day. Pad the tail so we always emit complete
            # rows (an incomplete last day shows up as a partially-coloured
            # bottom row instead of breaking the reshape).
            n_steps = len(net_load)
            n_days = max(1, (n_steps + steps_per_day - 1) // steps_per_day)
            pad_to = n_days * steps_per_day
            if n_steps < pad_to:
                net_load = np.pad(net_load, (0, pad_to - n_steps), mode="constant")
                ramp = np.pad(ramp, (0, pad_to - n_steps), mode="constant")
            nl_vals = net_load.reshape(n_days, steps_per_day)
            ramp_vals = ramp.reshape(n_days, steps_per_day)
            row_labels = [f"Day {d + 1}" for d in range(n_days)]
        else:
            yh = _year_hours(tres)
            if len(net_load) < yh:
                net_load = np.pad(net_load, (0, yh - len(net_load)), mode="constant")
                ramp = np.pad(ramp, (0, yh - len(ramp)), mode="constant")
            freq = f"{tres}h"
            idx = pd.date_range(start=f"{year}-01-01", periods=yh, freq=freq)
            df = pd.DataFrame({"NL": net_load[:yh], "ramp": ramp[:yh]}, index=idx)
            df["tod"] = (df.index.hour // tres)
            avg_nl = df.groupby([df.index.month, "tod"])["NL"].mean().unstack()
            avg_ramp = df.groupby([df.index.month, "tod"])["ramp"].mean().unstack()
            for grp in (avg_nl, avg_ramp):
                for m in range(1, 13):
                    if m not in grp.index:
                        grp.loc[m] = 0.0
            avg_nl = avg_nl.sort_index()
            avg_ramp = avg_ramp.sort_index()
            nl_vals = avg_nl.values
            ramp_vals = avg_ramp.values
            row_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        if self._sigma > 0:
            try:
                from scipy.ndimage import gaussian_filter
                nl_vals = gaussian_filter(nl_vals, sigma=self._sigma)
                ramp_vals = gaussian_filter(ramp_vals, sigma=self._sigma)
            except ImportError:
                logger.debug("scipy not available; skipping NL smoothing")

        ramp_vals = np.nan_to_num(ramp_vals, nan=0.0, posinf=0.0, neginf=0.0)
        hour_labels = [f"{h * tres:02d}:00" for h in range(steps_per_day)]

        return {
            "year": int(year),
            # Same key as before so the JS doesn't fork on this; the
            # label semantics shift from "month" to "day" in UC mode
            # but the heatmap rendering is identical.
            "months": row_labels,
            "hours": hour_labels,
            "avg_nl_mw": nl_vals.tolist(),
            "avg_ramp_mw_h": ramp_vals.tolist(),
            "sigma": float(self._sigma),
            "colormap_a": self._colormap_a,
            "colormap_b": self._colormap_b,
            "reverse_b": bool(self._reverse_b),
            "mode": "uc" if is_uc else "planning",
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 10 — CF / LCOE / VALLCOE (scatter + KDE)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _TechPerformanceBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})



class CFLcoeVallcoeChart(QWidget):
    """Interactive Plotly version of Technology Performance.

    Two stacked subplots sharing the technology axis:
      a) Capacity Factor per tech — violin distribution + scatter points
         coloured by year (RdBu reversed colormap).
      b) LCOE & VALCOE per tech — two violins (blue / red) overlaid
         with triangle (LCOE) and square (VALCOE) markers.

    Data pipeline is the same as the matplotlib chart: one (cf, lcoe,
    vallcoe) average per (technology, year), so each technology slot
    carries one point per scenario year.
    """

    TITLE = "CF / LCOE / VALCOE"
    TR_KEY = "results_charts.cf_lcoe_vallcoe"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        # Install the logging page so any JS error (which is otherwise
        # invisible because the WebEngineView has no exposed devtools)
        # ends up in the Python logger.
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _TechPerformanceBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "tech_performance.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    # ── Helpers ported verbatim from the matplotlib class ────────

    @staticmethod
    def _build_gen_to_tech(
        gen_configs: list[dict],
        tech_configs: list[dict],
        bat_tech_configs: list[dict],
        bat_configs: list[dict] | None = None,
    ) -> dict[str, str]:
        tech_id_to_display: dict[str, str] = {}
        fuel_to_display: dict[str, str] = {}
        for cfg in tech_configs:
            key = str(cfg.get("key", ""))
            tname = str(cfg.get("name", ""))
            fuel = str(cfg.get("fuel", ""))
            tech_id = key.split("__", 1)[-1] if "__" in key else key
            display = tname.split("/", 1)[-1] if "/" in tname else tname
            if tech_id and display:
                tech_id_to_display[tech_id] = display
            if fuel and display:
                fuel_to_display.setdefault(fuel, display)

        bat_tech_id_to_display: dict[str, str] = {}
        for cfg in bat_tech_configs:
            key = str(cfg.get("key", ""))
            tname = str(cfg.get("name", ""))
            tech_id = key.split("__", 1)[-1] if "__" in key else key
            display = tname.split("/", 1)[-1] if "/" in tname else tname
            if tech_id and display:
                bat_tech_id_to_display[tech_id] = display

        name_to_tech: dict[str, str] = {}
        for cfg in gen_configs:
            cfg_name = str(cfg.get("name", ""))
            dataset_name = cfg_name.replace("/", " - ")
            tech_attr = str(cfg.get("technology", ""))
            display = tech_id_to_display.get(tech_attr, "")
            if not display:
                fuel = str(cfg.get("fuel", ""))
                display = fuel_to_display.get(fuel, fuel)
            if not display:
                display = cfg_name.split("/", 1)[-1] if "/" in cfg_name else cfg_name
            name_to_tech[dataset_name] = display
            name_to_tech[cfg_name] = display

        for cfg in tech_configs:
            tname = str(cfg.get("name", ""))
            display = tname.split("/", 1)[-1] if "/" in tname else tname
            inv_dataset = tname.replace("/", " - ")
            name_to_tech[f"Investment {inv_dataset}"] = display
            name_to_tech[inv_dataset] = display
            name_to_tech[tname] = display

        for cfg in bat_tech_configs:
            tname = str(cfg.get("name", ""))
            display = tname.split("/", 1)[-1] if "/" in tname else tname
            inv_dataset = tname.replace("/", " - ")
            name_to_tech[f"Investment {inv_dataset}"] = display
            name_to_tech[inv_dataset] = display
            name_to_tech[tname] = display

        for cfg in (bat_configs or []):
            cfg_name = str(cfg.get("name", ""))
            dataset_name = cfg_name.replace("/", " - ")
            tech_attr = str(cfg.get("technology", ""))
            display = bat_tech_id_to_display.get(tech_attr, "")
            if not display:
                display = cfg_name.split("/", 1)[-1] if "/" in cfg_name else cfg_name
            name_to_tech[dataset_name] = display
            name_to_tech[cfg_name] = display

        return name_to_tech

    @staticmethod
    def _resolve_tech(name: str, name_to_tech: dict[str, str]) -> str:
        if name in name_to_tech:
            return name_to_tech[name]
        stripped = name.replace("Investment ", "", 1) if name.startswith("Investment ") else ""
        if stripped and stripped in name_to_tech:
            return name_to_tech[stripped]
        for key, tech in name_to_tech.items():
            if key and key in name:
                return tech
        return name

    @staticmethod
    def _avg_positive(arr: np.ndarray) -> float:
        valid = arr[arr > 0]
        return float(np.mean(valid)) if len(valid) > 0 else 0.0

    # ── Payload builder ──────────────────────────────────────────

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        cf_by_tech: dict[str, dict[int, list[float]]] = {}
        lcoe_by_tech: dict[str, dict[int, list[float]]] = {}
        vallcoe_by_tech: dict[str, dict[int, list[float]]] = {}

        with _open_h5(h5_path) as h5f:
            gen_configs = _load_gen_configs(h5f, bp)
            b_configs = _load_bat_configs(h5f, bp)
            t_configs = _load_tech_configs(h5f, bp)
            bt_configs = _load_bat_tech_configs(h5f, bp)
            gen_to_tech = self._build_gen_to_tech(
                gen_configs, t_configs, bt_configs, b_configs,
            )
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                for prefix, is_bat in [("capacity_factor", False),
                                       ("battery_capacity_factor", True)]:
                    if prefix not in sc:
                        continue
                    grp = sc[prefix]
                    lcoe_key = "battery_lcoe" if is_bat else "lcoe"
                    vallcoe_key = "battery_vallcoe" if is_bat else "vallcoe"
                    for name in grp:
                        fuel = self._resolve_tech(name, gen_to_tech)
                        if is_bat and fuel == name:
                            fuel = f"Storage: {name.replace('_', ' ')}"
                        cf_arr = grp[name][:]
                        avg_cf = self._avg_positive(cf_arr) * 100
                        if avg_cf <= 0:
                            continue
                        avg_lcoe = 0.0
                        if lcoe_key in sc and name in sc[lcoe_key]:
                            avg_lcoe = self._avg_positive(sc[lcoe_key][name][:])
                        avg_vallcoe = 0.0
                        if vallcoe_key in sc and name in sc[vallcoe_key]:
                            avg_vallcoe = self._avg_positive(sc[vallcoe_key][name][:])
                        cf_by_tech.setdefault(fuel, {}).setdefault(year, []).append(avg_cf)
                        if avg_lcoe > 0:
                            lcoe_by_tech.setdefault(fuel, {}).setdefault(year, []).append(avg_lcoe)
                        if avg_vallcoe > 0:
                            vallcoe_by_tech.setdefault(fuel, {}).setdefault(year, []).append(avg_vallcoe)

        if not cf_by_tech:
            return {"error": "No CF/LCOE data"}

        techs = sorted(cf_by_tech.keys())
        all_years = sorted({y for d in cf_by_tech.values() for y in d})
        yr_min, yr_max = (min(all_years), max(all_years)) if all_years else (0, 0)

        # Match matplotlib defaults for column/jitter geometry.
        col_w = 0.7
        area_w = 0.34
        rng = np.random.default_rng(42)

        # Flatten per (tech, year) — exactly as matplotlib did.
        def _flatten(by_tech: dict) -> dict[str, dict[int, float]]:
            out: dict[str, dict[int, float]] = {}
            for tech in techs:
                yr_data = by_tech.get(tech, {})
                for yr in sorted(yr_data.keys()):
                    vals = yr_data[yr]
                    if not vals:
                        continue
                    out.setdefault(tech, {})[int(yr)] = float(np.mean(vals))
            return out

        cf_flat = _flatten(cf_by_tech)
        lcoe_flat = _flatten(lcoe_by_tech)
        vallcoe_flat = _flatten(vallcoe_by_tech)

        # Build scatter points carrying tech_idx + jittered x.
        # Same horizontal jitter range as the matplotlib version:
        # x_jitter = i + uniform(-col_w/2, 0, n_pts)
        def _scatter_points(flat_map: dict, key: str) -> list[dict]:
            pts = []
            for i, tech in enumerate(techs):
                yr_data = flat_map.get(tech, {})
                if not yr_data:
                    continue
                yrs_sorted = sorted(yr_data.keys())
                n = len(yrs_sorted)
                jx = i + rng.uniform(-col_w / 2, 0, n)
                for yr, xj in zip(yrs_sorted, jx):
                    val = yr_data[yr]
                    if key in ("lcoe", "vallcoe") and val <= 0:
                        continue
                    pts.append({
                        "tech_idx": int(i),
                        "tech": tech,
                        "year": int(yr),
                        "x": float(xj),
                        key: float(val),
                    })
            return pts

        cf_pts = _scatter_points(cf_flat, "cf")
        lcoe_pts = _scatter_points(lcoe_flat, "lcoe")
        vallcoe_pts = _scatter_points(vallcoe_flat, "vallcoe")

        # ── KDE curves (Gaussian-sum, same as matplotlib) ────────────
        # Each curve is a closed polygon: left edge (vertical) + KDE
        # silhouette on the right, anchored at x_right = i + col_w/2 - 0.23.
        def _kde_curve(values: list[float], y_lo: float, y_hi: float,
                       sigma: float, tech_idx: int) -> Optional[dict]:
            if len(values) < 2:
                return None
            y_grid = np.linspace(y_lo, y_hi, 100)
            kde = np.zeros_like(y_grid)
            for v in values:
                kde += np.exp(-0.5 * ((y_grid - v) / sigma) ** 2)
            peak = float(kde.max())
            if peak <= 1e-6:
                return None
            kde = kde / peak * area_w
            x_left = tech_idx + col_w / 2 - 0.23
            x_right = x_left + kde
            # Closed polygon: walk up the left edge then back down the right
            poly_x = [float(x_left)] * len(y_grid) + [float(v) for v in reversed(x_right.tolist())]
            poly_y = [float(v) for v in y_grid.tolist()] + [float(v) for v in reversed(y_grid.tolist())]
            return {"x": poly_x, "y": poly_y, "tech_idx": int(tech_idx)}

        # CF KDE — y range fixed at [0, 100] (CF is a percentage)
        cf_kdes = []
        for i, tech in enumerate(techs):
            vals = list(cf_flat.get(tech, {}).values())
            curve = _kde_curve(vals, 0.0, 100.0, sigma=1.5, tech_idx=i)
            if curve:
                cf_kdes.append(curve)

        # LCOE/VALCOE KDE — y range based on max observed cost, padded.
        # Matplotlib used a hard-coded [0, 500]; here we compute it so
        # the KDE always fits the data.
        all_lcoe_means = [v for d in lcoe_flat.values() for v in d.values() if v > 0]
        all_vallcoe_means = [v for d in vallcoe_flat.values() for v in d.values() if v > 0]
        max_cost = max(
            (max(all_lcoe_means) if all_lcoe_means else 0.0),
            (max(all_vallcoe_means) if all_vallcoe_means else 0.0),
            100.0,
        ) * 1.10
        lcoe_kdes = []
        vallcoe_kdes = []
        for i, tech in enumerate(techs):
            l_vals = [v for v in lcoe_flat.get(tech, {}).values() if v > 0]
            v_vals = [v for v in vallcoe_flat.get(tech, {}).values() if v > 0]
            lc = _kde_curve(l_vals, 0.0, max_cost, sigma=8.0, tech_idx=i)
            vc = _kde_curve(v_vals, 0.0, max_cost, sigma=8.0, tech_idx=i)
            if lc:
                lcoe_kdes.append(lc)
            if vc:
                vallcoe_kdes.append(vc)

        return {
            "techs": techs,
            "years_min": int(yr_min),
            "years_max": int(yr_max),
            "cf_points": cf_pts,
            "lcoe_points": lcoe_pts,
            "vallcoe_points": vallcoe_pts,
            "cf_kdes": cf_kdes,
            "lcoe_kdes": lcoe_kdes,
            "vallcoe_kdes": vallcoe_kdes,
            "max_cost": float(max_cost),
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 11 — Electricity Cost Analysis (contour + distribution)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _ElectricityCostBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class ElectricityCostChart(QWidget):
    """Interactive Plotly version of the Electricity Cost chart.

    Two stacked subplots: a daily-price contour over years (with annual
    average line on a secondary y axis) and a generation-weighted
    price-distribution histogram (Renewable vs Non-Renewable). Params
    widget exposes the gaussian-smoothing sigma (data rebuild) and a
    colormap dropdown for the contour (visual restyle, no rebuild).
    """

    TITLE = "Electricity Cost"
    TR_KEY = "results_charts.electricity_cost"

    # Same Plotly.js-validated list as battery_heatmap.
    _COLORSCALES = [
        "Turbo", "Viridis", "Plasma", "Magma", "Inferno", "Cividis",
        "Jet", "Hot", "Greys", "Electric",
        "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
    ]

    def __init__(self):
        super().__init__()
        self._sigma = 1.0
        self._colormap = "Turbo"

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _ElectricityCostBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "electricity_cost.html")
        ))

        self._last_args: Optional[tuple] = None
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)

        wl.addWidget(QLabel(tr("results_charts.smoothing")))
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 5.0)
        sp.setSingleStep(0.1)
        sp.setValue(self._sigma)
        sp.valueChanged.connect(self._on_sigma_changed)
        wl.addWidget(sp)

        wl.addWidget(QLabel("Colormap"))
        cb = QComboBox()
        cb.addItems(self._COLORSCALES)
        cb.setCurrentText(self._colormap)
        cb.setMaxVisibleItems(8)
        cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
        cb.currentTextChanged.connect(self._on_colormap_changed)
        wl.addWidget(cb)

        wl.addStretch()
        return w

    def _on_sigma_changed(self, v: float):
        self._sigma = float(v)
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("ElectricityCostChart re-render failed")

    def _on_colormap_changed(self, name: str):
        self._colormap = str(name)
        import json
        js = (
            f"if (typeof setColormap === 'function') "
            f"setColormap({json.dumps(self._colormap)});"
        )
        self._view.page().runJavaScript(js)

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            spd = max(1, 24 // tres)
            days_per_year = 365
            yr_list: list[int] = []
            year_price_data: dict[int, np.ndarray] = {}
            re_price_weight: list[tuple[float, float]] = []
            nonre_price_weight: list[tuple[float, float]] = []

            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                yr_list.append(int(year))

                prices = None
                for pk in ("electricity_prices", "nodal_electricity_prices"):
                    if pk in sc:
                        p = sc[pk][:]
                        prices = p.mean(axis=0) if p.ndim == 2 else p
                        break
                if prices is not None:
                    year_price_data[int(year)] = prices

                if "technology_selling_prices" in sc:
                    tsp = sc["technology_selling_prices"]

                    def _collect_prices(grp, prefix=""):
                        for k in grp:
                            item = grp[k]
                            full = f"{prefix}/{k}" if prefix else k
                            if isinstance(item, _GROUP_T):
                                if "prices_weights" in item:
                                    tech_type = item.attrs.get("technology_type", "")
                                    if isinstance(tech_type, bytes):
                                        tech_type = tech_type.decode()
                                    is_re = _is_renewable(full) or tech_type == "Renewable"
                                    pw = item["prices_weights"][:]
                                    target = re_price_weight if is_re else nonre_price_weight
                                    for row in pw:
                                        price = float(row[0])
                                        gen_mw = float(row[1])
                                        gen_mwh = gen_mw * tres
                                        if gen_mwh > 0:
                                            target.append((price, gen_mwh))
                                else:
                                    _collect_prices(item, full)
                    _collect_prices(tsp)

        n_years = len(yr_list)
        if not yr_list:
            return {"error": "No price data"}

        # Daily cost matrix [days_per_year × n_years]
        cost_matrix = np.zeros((days_per_year, n_years), dtype=float)
        annual_avg_prices: list[float] = []
        timesteps_per_year = HOURS_STD_YEAR // tres
        for i, year in enumerate(yr_list):
            prices = year_price_data.get(year, np.array([]))
            if len(prices) > 0:
                if len(prices) < timesteps_per_year:
                    factor = max(1, timesteps_per_year // len(prices))
                    prices = np.repeat(prices, factor)[:timesteps_per_year]
                for day in range(days_per_year):
                    start_t = day * spd
                    end_t = min((day + 1) * spd, len(prices))
                    if start_t < len(prices):
                        day_prices = prices[start_t:end_t]
                        valid = day_prices[day_prices > 0]
                        if len(valid) > 0:
                            cost_matrix[day, i] = float(np.mean(valid))
                valid_all = prices[prices > 0]
                annual_avg_prices.append(
                    float(np.mean(valid_all)) if len(valid_all) > 0 else float("nan")
                )
            else:
                annual_avg_prices.append(float("nan"))

        # Smoothing along the day axis (same as matplotlib gaussian_filter1d)
        if self._sigma > 0:
            try:
                from scipy.ndimage import gaussian_filter1d
                cost_matrix = gaussian_filter1d(cost_matrix, sigma=self._sigma * 2, axis=0)
            except ImportError:
                logger.debug("scipy not available; skipping smoothing")

        valid_prices = cost_matrix[cost_matrix > 0]
        if len(valid_prices) > 0:
            price_min = float(np.percentile(valid_prices, 2))
            price_max = float(np.percentile(valid_prices, 98))
        else:
            price_min, price_max = 0.0, 100.0

        # Generation-weighted price distribution
        all_prices = ([p for p, _ in re_price_weight]
                      + [p for p, _ in nonre_price_weight])
        if all_prices:
            p01 = float(np.percentile(all_prices, 1))
            p99 = float(np.percentile(all_prices, 99))
        else:
            p01, p99 = -50.0, 200.0
        hist_lo = min(p01, 0.0)
        hist_hi = p99 * 1.1

        def _filter(pw_list):
            arr_p = np.array([p for p, _ in pw_list if hist_lo <= p <= hist_hi])
            arr_w = np.array([w for p, w in pw_list if hist_lo <= p <= hist_hi])
            return arr_p, arr_w / 1000.0  # MWh → GWh

        re_p, re_w = _filter(re_price_weight)
        nr_p, nr_w = _filter(nonre_price_weight)

        mean_re = (float(np.average(re_p, weights=re_w))
                   if len(re_p) > 5 and re_w.sum() > 0 else None)
        mean_nr = (float(np.average(nr_p, weights=nr_w))
                   if len(nr_p) > 5 and nr_w.sum() > 0 else None)

        # NaN -> None for JSON
        annual_clean = [None if (v != v) else float(v) for v in annual_avg_prices]

        return {
            "year_labels": [str(y) for y in yr_list],
            "day_indices": list(range(days_per_year)),
            "cost_matrix": cost_matrix.tolist(),
            "annual_avg_prices": annual_clean,
            "price_min": price_min,
            "price_max": price_max,
            "ren_prices": [float(v) for v in re_p],
            "ren_weights_gwh": [float(v) for v in re_w],
            "nonren_prices": [float(v) for v in nr_p],
            "nonren_weights_gwh": [float(v) for v in nr_w],
            "hist_lo": float(hist_lo),
            "hist_hi": float(hist_hi),
            "mean_ren": mean_re,
            "mean_nonren": mean_nr,
            "colormap": self._colormap,
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 12 — Inter-Node Flows (stacked bar imports/exports)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _InterNodeFlowsBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class InterNodeFlowsChart(QWidget):
    """Interactive Plotly version of Inter-Node Flows.

    Per-year stacked bars: imports (positive) above the zero line,
    exports (negative) below. One colour per node; the import/export
    pair share the colour with different opacities. Same data pipeline
    as the matplotlib chart — sums positive `power_flow` slices across
    other nodes per scenario, divides by 1e3 to land in GWh.
    """

    TITLE = "Inter-Node Flows"
    TR_KEY = "results_charts.inter_node_flows"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _InterNodeFlowsBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "inter_node_flows.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")

        # Detect UC mode and switch to an hourly net-flow time-series.
        # In a planning run we report annual imports/exports per year
        # (the original GWh stack); in UC the year is fixed so we
        # surface net MW flow per hour per node instead, which is what
        # the operator wants to see (when is each node importing? how
        # variable is congestion within the day?).
        is_uc = False
        try:
            with _open_h5(h5_path) as _f:
                sm = _f.attrs.get("simulation_mode", "")
                if isinstance(sm, bytes):
                    sm = sm.decode()
                is_uc = str(sm).strip().lower() == "unit_commitment"
        except Exception:
            is_uc = False

        if is_uc:
            return self._build_payload_uc(h5_path, bp, **kw)
        return self._build_payload_planning(h5_path, bp)

    def _build_payload_planning(self, h5_path: Path, bp: str) -> dict:
        yr_list: list[int] = []
        imports_by_yr: dict[int, dict[int, float]] = {}
        exports_by_yr: dict[int, dict[int, float]] = {}
        num_nodes = 0
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            node_names = _get_node_names(h5f, bp)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                if "power_flow" not in sc:
                    continue
                pf = sc["power_flow"][:]
                if pf.ndim != 3:
                    continue
                n = pf.shape[0]
                num_nodes = max(num_nodes, n)
                yr_list.append(int(year))
                imp: dict[int, float] = {}
                exp: dict[int, float] = {}
                for node in range(n):
                    imp_val = 0.0
                    exp_val = 0.0
                    for other in range(n):
                        if other == node:
                            continue
                        imp_val += float(np.maximum(pf[other, node, :], 0).sum())
                        exp_val += float(np.maximum(pf[node, other, :], 0).sum())
                    imp[node] = imp_val * tres / 1e3
                    exp[node] = exp_val * tres / 1e3
                imports_by_yr[int(year)] = imp
                exports_by_yr[int(year)] = exp
        if not yr_list or num_nodes == 0:
            return {"error": "No power flow data"}
        palette = get_tab10()
        nodes_payload = []
        for node in range(num_nodes):
            label = node_names[node] if node < len(node_names) else f"Node {node}"
            imp_vals = [float(imports_by_yr.get(y, {}).get(node, 0.0)) for y in yr_list]
            exp_vals = [float(exports_by_yr.get(y, {}).get(node, 0.0)) for y in yr_list]
            if not any(v != 0 for v in imp_vals) and not any(v != 0 for v in exp_vals):
                continue
            nodes_payload.append({
                "label": str(label),
                "color": palette[node % len(palette)],
                "imports_gwh": imp_vals,
                "exports_gwh": exp_vals,
            })
        return {
            "mode": "planning",
            "years": [str(y) for y in yr_list],
            "nodes": nodes_payload,
        }

    def _build_payload_uc(self, h5_path: Path, bp: str, **kw) -> dict:
        """Hourly net flow per node: positive = importing, negative =
        exporting. Computed as ``Σ_other pf[other, n, t] −
        Σ_other pf[n, other, t]`` for each hour, per node, from the
        single UC year scenario."""
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            node_names = _get_node_names(h5f, bp)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            # Pick the last year in range (snapshot semantics, same as
            # other UC charts).
            y_lo = y_hi = None
            if year_range is not None:
                y_lo, y_hi = int(year_range[0]), int(year_range[1])
            sel_idx = len(scenarios) - 1
            if y_lo is not None:
                for i in range(len(scenarios) - 1, -1, -1):
                    _, y = scenarios[i]
                    if y_lo <= y <= y_hi:
                        sel_idx = i
                        break
            sc_key, year = scenarios[sel_idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "power_flow" not in sc:
                return {"error": "power_flow missing"}
            pf = sc["power_flow"][:]
            if pf.ndim != 3:
                return {"error": "power_flow not 3-D"}
            n_nodes, _, n_hours = pf.shape
            # Net flow per node-hour: sum incoming − sum outgoing.
            # ``pf[other, n, t]`` is flow from ``other`` to ``n`` when
            # positive; mask negatives so we don't double-count.
            pf_pos = np.maximum(pf, 0)
            in_flow = pf_pos.sum(axis=0)        # [n_nodes, n_hours]
            out_flow = pf_pos.sum(axis=1)       # [n_nodes, n_hours]
            net = in_flow - out_flow

        palette = get_tab10()
        nodes_payload = []
        for node in range(n_nodes):
            label = node_names[node] if node < len(node_names) else f"Node {node}"
            series = net[node].tolist()
            if max(abs(v) for v in series) < 1e-3:
                continue
            nodes_payload.append({
                "label": str(label),
                "color": palette[node % len(palette)],
                "net_mw": [float(v) for v in series],
            })
        if not nodes_payload:
            return {"error": "Power flows are all zero — nothing to plot"}
        return {
            "mode": "uc",
            "year": int(year),
            "hours": list(range(n_hours)),
            "nodes": nodes_payload,
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 12 — MGA Comparison
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_MGA_RENEWABLE_FUELS = {"sun", "wind", "biomass", "water", "otec",
                        "geothermal", "hydrogen"}
_MGA_THERMAL_FUELS = {"diesel", "fuel_oil", "fueloil", "fuel oil", "gas",
                      "natural gas", "coal"}


def _mga_tech_metadata(h5f, group_key: str) -> list[dict]:
    """Read technology entries (name + fuel + type) ordered by index.

    ``group_key`` is ``"technologies"`` or ``"battery_technologies"``.
    Returns ``[{"key","name","fuel","type"}, ...]`` indexed by the suffix
    integer so it aligns with the ``tech_investment``/``bat_tech_*``
    last-but-one axis."""
    out: list[dict] = []
    sc = h5f.get(f"system_configuration/{group_key}")
    if sc is None:
        return out
    keys = sorted(sc.keys(), key=lambda k: int(k.rsplit("_", 1)[-1]))
    for k in keys:
        attrs = sc[k].attrs
        def _s(v):
            return v.decode() if isinstance(v, bytes) else (str(v) if v is not None else "")
        out.append({
            "key": k,
            "name": _s(attrs.get("name")) or k,
            "fuel": _s(attrs.get("fuel")).strip().lower(),
            "type": _s(attrs.get("type")).strip().lower(),
        })
    return out


def _mga_category(fuel: str, type_: str, is_battery: bool) -> str:
    """Bucket a tech into Solar / Wind / Other RE / Storage / Thermal for
    the pathway stacked area."""
    if is_battery:
        return "Storage"
    f = (fuel or "").lower()
    if "sun" in f or "solar" in f or "pv" in f:
        return "Solar"
    if "wind" in f:
        return "Wind"
    if f in _MGA_RENEWABLE_FUELS or type_ == "renewable":
        return "Other RE"
    if f in _MGA_THERMAL_FUELS:
        return "Thermal"
    return "Other"


_MGA_CATEGORY_ORDER = ["Solar", "Wind", "Other RE", "Storage", "Thermal", "Other"]
_MGA_CATEGORY_COLORS = {
    "Solar":    "#F4D03F",
    "Wind":     "#5DADE2",
    "Other RE": "#27AE60",
    "Storage":  "#8E44AD",
    "Thermal":  "#7F8C8D",
    "Other":    "#BDC3C7",
}

# SPORES objective palette. Keyed on the SporesObjective enum values
# (lowercase snake_case strings) that the runner writes to
# /mga/alternative_N/attrs["objective"]. The cost-optimal seed gets the
# same red the rest of the UI uses for "the optimum" so it pops in
# every chart; the four SPORES objectives use distinct categorical
# hues that read in both light + dark mode.
_MGA_OBJECTIVE_COLORS = {
    "cost_optimal":        "#E74C3C",
    "hsj_diversity":       "#3498DB",
    "min_total_build":     "#27AE60",
    "max_tech_equity":     "#E67E22",
    "max_regional_equity": "#8E44AD",
    "evolutionary_dist":   "#16A085",
}
# Human-readable labels for the legend and hover text.
_MGA_OBJECTIVE_LABELS = {
    "cost_optimal":        "Cost-optimal",
    "hsj_diversity":       "HSJ diversity",
    "min_total_build":     "Min total build",
    "max_tech_equity":     "Tech equity",
    "max_regional_equity": "Regional equity",
    "evolutionary_dist":   "Evolutionary dist.",
}


def _read_mga_node_labels(h5f, n: int) -> list[str]:
    """Read node names from system_configuration; pad with 'Node K'."""
    out: list[str] = []
    sc = h5f.get("system_configuration/nodes")
    if sc is not None and "name" in sc:
        raw = sc["name"][:]
        out = [r.decode() if isinstance(r, bytes) else str(r) for r in raw]
    while len(out) < n:
        out.append(f"Node {len(out)}")
    return out[:n]


def _build_mga_bundle(h5f, base_prefix: str) -> dict:
    """Read the /mga group and return the shared analytical bundle used
    by all five MGA-themed charts. ``base_prefix`` is reserved for future
    per-system MGA but currently the data lives at root."""
    mga = h5f.get("mga")
    if mga is None:
        return {"error": "No MGA results.\nEnable MGA in Global Settings > "
                         "Master Problem and re-run."}
    slack = float(mga.attrs.get("slack_fraction", 0))
    optimal_cost = float(mga.attrs.get("optimal_cost", 0))
    years_arr = mga.attrs.get("years")
    if hasattr(years_arr, "tolist"):
        years_arr = years_arr.tolist()
    years_arr = [int(y) for y in (years_arr or [])]

    # SPORES (Phase 4 export): the method tag and ordered objective list
    # live as root attrs on /mga/. Pre-Phase-4 result files default to
    # "mga" so legacy charts keep their existing colour conventions.
    method_attr = mga.attrs.get("method", "mga")
    if isinstance(method_attr, bytes):
        method_attr = method_attr.decode()
    method = str(method_attr).lower()
    objectives_attr = mga.attrs.get("objectives")
    if objectives_attr is None:
        objectives_attr = []
    elif hasattr(objectives_attr, "tolist"):
        # Numpy arrays raise on truthy checks, so we always coerce.
        objectives_attr = objectives_attr.tolist()
    method_objectives = [
        (o.decode() if isinstance(o, bytes) else str(o))
        for o in objectives_attr
    ]

    tech_meta = _mga_tech_metadata(h5f, "technologies")
    bat_meta = _mga_tech_metadata(h5f, "battery_technologies")
    n_tech, n_bat = len(tech_meta), len(bat_meta)

    all_meta = (
        [{**m, "is_bat": False} for m in tech_meta]
        + [{**m, "is_bat": True}  for m in bat_meta]
    )
    for m in all_meta:
        m["category"] = _mga_category(m["fuel"], m["type"], m["is_bat"])
    all_labels = [m["name"] for m in all_meta]

    alt_keys = sorted(
        [k for k in mga.keys() if k.startswith("alternative_")],
        key=lambda k: int(k.rsplit("_", 1)[-1]),
    )
    alts: list[dict] = []
    invest_matrix: list[list[float]] = []
    yearly_by_alt: list[list[np.ndarray]] = []
    opt_idx = 0

    for k in alt_keys:
        g = mga[k]
        aid = int(g.attrs.get("alternative_id", int(k.rsplit("_", 1)[-1])))
        cost = float(g.attrs.get("cost", 0))
        div = g.attrs.get("diversity_objective")
        div_v: Optional[float] = float(div) if div is not None else None
        is_opt = bool(g.attrs.get("is_optimal", False))
        # Per-alt SPORES objective tag (Phase 4 export). Pre-Phase-4
        # HDF5 files don't carry the attr; fall back to the historically
        # accurate default ("cost_optimal" for the seed, "hsj_diversity"
        # for the rest) so charts that group by objective keep working.
        objective_raw = g.attrs.get(
            "objective",
            "cost_optimal" if is_opt else "hsj_diversity",
        )
        if isinstance(objective_raw, bytes):
            objective_raw = objective_raw.decode()
        objective_label = str(objective_raw)

        per_tech_total: list[float] = []
        per_tech_yearly: list[np.ndarray] = []
        if "tech_investment" in g:
            ti = g["tech_investment"][:]
            for t in range(min(ti.shape[1], n_tech)):
                v = ti[:, t, :]
                per_tech_total.append(float(np.nansum(v)))
                per_tech_yearly.append(v.sum(axis=1))
        while len(per_tech_total) < n_tech:
            per_tech_total.append(0.0)
            per_tech_yearly.append(np.zeros(len(years_arr)))
        if "bat_tech_power_investment" in g:
            bi = g["bat_tech_power_investment"][:]
            for b in range(min(bi.shape[1], n_bat)):
                v = bi[:, b, :]
                per_tech_total.append(float(np.nansum(v)))
                per_tech_yearly.append(v.sum(axis=1))
        while len(per_tech_total) < n_tech + n_bat:
            per_tech_total.append(0.0)
            per_tech_yearly.append(np.zeros(len(years_arr)))
        invest_matrix.append(per_tech_total)
        yearly_by_alt.append(per_tech_yearly)

        if "re_penetration" in g:
            re = np.asarray(g["re_penetration"][:]) * 100.0
        else:
            re = np.zeros(len(years_arr))
        re_pct = [float(v) for v in re]
        re_peak = float(np.nanmax(re)) if re.size else 0.0
        re_final = float(re[-1]) if re.size else 0.0

        tot_re = sum(per_tech_total[i] for i, m in enumerate(all_meta)
                     if m["category"] in ("Solar", "Wind", "Other RE"))
        tot_st = sum(per_tech_total[i] for i, m in enumerate(all_meta)
                     if m["category"] == "Storage")
        tot_th = sum(per_tech_total[i] for i, m in enumerate(all_meta)
                     if m["category"] == "Thermal")

        cost_pct = ((cost - optimal_cost) / optimal_cost * 100
                    if optimal_cost > 0 else 0.0)
        alts.append({
            "id": aid, "is_optimal": is_opt,
            "cost_busd": cost / 1e9,
            "cost_pct_above_optimal": cost_pct,
            "diversity": div_v,
            "re_peak_pct": re_peak,
            "re_final_pct": re_final,
            "tot_re_mw": tot_re,
            "tot_storage_mw": tot_st,
            "tot_thermal_mw": tot_th,
            "re_trajectory_pct": re_pct,
            "objective": objective_label,
        })
        if is_opt:
            opt_idx = len(alts) - 1

    if not alts:
        return {"error": "No alternatives found in /mga"}

    inv_arr = np.array(invest_matrix, dtype=float)
    mins = inv_arr.min(axis=0)
    maxs = inv_arr.max(axis=0)
    meds = np.median(inv_arr, axis=0)
    means = inv_arr.mean(axis=0)
    opts = inv_arr[opt_idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        cv = np.where(means > 1e-3, inv_arr.std(axis=0) / means, 0.0)
    keep = maxs > 1e-3
    keep_idx = np.where(keep)[0].tolist()
    keep_idx.sort(key=lambda i: -float(cv[i]))

    def _at(arr, idx): return [float(arr[i]) for i in idx]
    tech_range = {
        "labels":     [all_labels[i] for i in keep_idx],
        "category":   [all_meta[i]["category"] for i in keep_idx],
        "min_mw":     _at(mins, keep_idx),
        "max_mw":     _at(maxs, keep_idx),
        "median_mw":  _at(meds, keep_idx),
        "mean_mw":    _at(means, keep_idx),
        "optimal_mw": _at(opts, keep_idx),
        "cv":         _at(cv,   keep_idx),
    }
    built_all = np.all(inv_arr > 1e-3, axis=0)
    robust = [all_labels[i] for i in keep_idx
              if cv[i] < 0.15 and built_all[i]]
    flexible = [all_labels[i] for i in keep_idx if cv[i] > 0.5]

    # ── L1 distance from the cost-optimal plan per alternative ──
    # Computed here (rather than later from the per-node decision
    # matrix) so the parallel-coordinates payload below can pick it up
    # when method='spores'. We use ``inv_arr`` (alt × tech, totals
    # summed across nodes) — losing the spatial dimension that the
    # Spatial Divergence chart already covers, but keeping the
    # method-agnostic property the Robust Frontier scatter needs.
    cost_optimal_distance = [0.0] * len(alts)
    if inv_arr.shape[0] >= 2 and inv_arr.shape[1] > 0:
        col_max = inv_arr.max(axis=0)
        col_min = inv_arr.min(axis=0)
        col_range = col_max - col_min
        active = col_range > 1e-9
        if active.any():
            seed = inv_arr[opt_idx]
            norm = np.where(active, col_range, 1.0)
            for ai in range(inv_arr.shape[0]):
                diffs = np.abs(inv_arr[ai] - seed) / norm
                cost_optimal_distance[ai] = float(diffs[active].mean())
    for ai, a in enumerate(alts):
        a["cost_optimal_distance"] = cost_optimal_distance[ai]

    # Parallel coordinates: only include the "Diversity" dimension for
    # classical MGA. SPORES alternatives carry per-objective values
    # (HSJ score, Gini-min, …) that live in incompatible unit spaces,
    # so plotting them on a shared axis is meaningless. Replace it
    # with the agnostic L1 distance from the cost-optimal plan, which
    # is comparable across methods.
    parcoord_dims = [
        ("Cost ($B)",         [a["cost_busd"] for a in alts]),
    ]
    if method == "spores":
        parcoord_dims.append(
            ("Distance from opt.",
             [a["cost_optimal_distance"] for a in alts])
        )
    else:
        parcoord_dims.append(
            ("Diversity", [(a["diversity"] or 0.0) for a in alts])
        )
    parcoord_dims.extend([
        ("Peak RE (%)",   [a["re_peak_pct"] for a in alts]),
        ("Renewable MW",  [a["tot_re_mw"] for a in alts]),
        ("Storage MW",    [a["tot_storage_mw"] for a in alts]),
        ("Thermal MW",    [a["tot_thermal_mw"] for a in alts]),
    ])
    parcoords = {
        "dim_labels": [d[0] for d in parcoord_dims],
        "dim_values": [d[1] for d in parcoord_dims],
        "alt_ids":    [a["id"] for a in alts],
        "is_optimal": [a["is_optimal"] for a in alts],
    }

    pathway_cats = ["Solar", "Wind", "Other RE", "Storage", "Thermal"]
    pathways_alts = []
    for ai, a in enumerate(alts):
        cat_stack: dict[str, np.ndarray] = {
            c: np.zeros(len(years_arr)) for c in pathway_cats
        }
        for i, m in enumerate(all_meta):
            cat = m["category"]
            if cat not in cat_stack:
                continue
            cat_stack[cat] += yearly_by_alt[ai][i][:len(years_arr)]
        cumstack = {c: np.cumsum(v) for c, v in cat_stack.items()}
        pathways_alts.append({
            "id": a["id"], "is_optimal": a["is_optimal"],
            "stack_mw": {c: cumstack[c].tolist() for c in pathway_cats},
        })

    spatial_techs = keep_idx
    inv_by_node: list[list[list[float]]] = []
    for k in alt_keys:
        g = mga[k]
        per_tech_node: list[list[float]] = []
        if "tech_investment" in g:
            ti = g["tech_investment"][:]
            for t in range(min(ti.shape[1], n_tech)):
                per_tech_node.append(ti[:, t, :].sum(axis=0).tolist())
        while len(per_tech_node) < n_tech:
            per_tech_node.append([0.0])
        if "bat_tech_power_investment" in g:
            bi = g["bat_tech_power_investment"][:]
            for b in range(min(bi.shape[1], n_bat)):
                per_tech_node.append(bi[:, b, :].sum(axis=0).tolist())
        while len(per_tech_node) < n_tech + n_bat:
            per_tech_node.append([0.0])
        inv_by_node.append(per_tech_node)
    n_nodes = max((len(row[0]) if row else 0) for row in inv_by_node) if inv_by_node else 0
    std_rows: list[list[float]] = []
    mean_rows: list[list[float]] = []
    for ti in spatial_techs:
        arr = np.array([
            inv_by_node[ai][ti][:n_nodes]
            + [0.0] * max(0, n_nodes - len(inv_by_node[ai][ti]))
            for ai in range(len(alt_keys))
        ], dtype=float)
        std_rows.append(arr.std(axis=0).tolist())
        mean_rows.append(arr.mean(axis=0).tolist())
    node_labels = _read_mga_node_labels(h5f, n_nodes)
    spatial = {
        "tech_labels": [all_labels[i] for i in spatial_techs],
        "node_labels": node_labels,
        "std_mw": std_rows,
        "mean_mw": mean_rows,
    }

    # ── Decision vectors [n_alts × (n_techs × n_nodes)] for PCA / similarity ──
    n_alts = len(alt_keys)
    n_dims = (n_tech + n_bat) * max(n_nodes, 1)
    decision_matrix = np.zeros((n_alts, n_dims))
    for ai in range(n_alts):
        flat: list[float] = []
        for ti in range(n_tech + n_bat):
            row = list(inv_by_node[ai][ti][:n_nodes])
            row += [0.0] * max(0, n_nodes - len(row))
            flat.extend(row)
        decision_matrix[ai, :len(flat)] = flat

    # ── PCA 2-D projection ──
    pca: Optional[dict] = None
    if n_alts >= 2 and n_dims > 0:
        Xc = decision_matrix - decision_matrix.mean(axis=0, keepdims=True)
        sd = decision_matrix.std(axis=0, keepdims=True)
        sd[sd < 1e-9] = 1.0
        Xs = Xc / sd
        try:
            U, S, _Vt = np.linalg.svd(Xs, full_matrices=False)
            var = (S ** 2) / max((S ** 2).sum(), 1e-12)
            pca = {
                "x":       (U[:, 0] * S[0]).tolist() if len(S) >= 1 else [0.0] * n_alts,
                "y":       (U[:, 1] * S[1]).tolist() if len(S) >= 2 else [0.0] * n_alts,
                "var_pc1": float(var[0]) if len(var) >= 1 else 0.0,
                "var_pc2": float(var[1]) if len(var) >= 2 else 0.0,
            }
        except np.linalg.LinAlgError:
            pca = None

    # ── t-SNE 2-D projection (optional, scikit-learn) ──
    tsne_data: Optional[dict] = None
    try:
        from sklearn.manifold import TSNE
        if n_alts >= 4 and n_dims > 0:
            perplexity = max(2, min(5, n_alts - 1))
            Xc2 = decision_matrix - decision_matrix.mean(axis=0, keepdims=True)
            sd2 = decision_matrix.std(axis=0, keepdims=True)
            sd2[sd2 < 1e-9] = 1.0
            Xs2 = Xc2 / sd2
            Y = TSNE(n_components=2, perplexity=perplexity,
                     init="pca", random_state=42, learning_rate="auto",
                     ).fit_transform(Xs2)
            tsne_data = {"x": Y[:, 0].tolist(), "y": Y[:, 1].tolist()}
    except Exception:
        tsne_data = None

    projections = {
        "pca": pca, "tsne": tsne_data,
        "alt_ids":    [a["id"] for a in alts],
        "is_optimal": [a["is_optimal"] for a in alts],
        "cost_busd":  [a["cost_busd"] for a in alts],
        "cost_pct_above_optimal": [a["cost_pct_above_optimal"] for a in alts],
        "diversity":  [(a["diversity"] or 0.0) for a in alts],
        "re_peak_pct":[a["re_peak_pct"] for a in alts],
        # SPORES objective tag per alternative; the JS picks the colour
        # encoding based on header.method.
        "objective":  [a["objective"] for a in alts],
    }

    # ── Pairwise alternative similarity (Euclidean distance) ──
    sim_dist = np.zeros((n_alts, n_alts))
    for i in range(n_alts):
        for j in range(n_alts):
            sim_dist[i, j] = float(np.linalg.norm(
                decision_matrix[i] - decision_matrix[j]))
    order: list[int] = list(range(n_alts))
    clusters: list[int] = [1] * n_alts
    # Linkage matrix kept for the dedicated dendrogram chart below.
    Z_link = None
    try:
        from scipy.cluster.hierarchy import linkage, leaves_list, fcluster
        from scipy.spatial.distance import squareform
        if n_alts >= 2:
            condensed = squareform(sim_dist, checks=False)
            Z_link = linkage(condensed, method="average")
            order = leaves_list(Z_link).tolist()
            n_target = max(2, min(3, n_alts - 1))
            clusters = fcluster(Z_link, t=n_target, criterion="maxclust").tolist()
    except Exception:
        pass
    # Cartesian dendrogram drawn above the heatmap. Leaves sit at the
    # column index of the reordered matrix (so x=i corresponds to
    # column i of the displayed heatmap); each merge step contributes
    # one U-shaped segment.
    dendro_links: list[dict] = []
    dendro_max_h = 0.0
    if Z_link is not None and n_alts >= 2:
        leaf_to_col = {leaf: i for i, leaf in enumerate(order)}
        node_pos: dict[int, tuple[float, float]] = {
            leaf: (float(leaf_to_col[leaf]), 0.0) for leaf in range(n_alts)
        }
        for step, row in enumerate(Z_link):
            a_idx, b_idx, dist, _count = row
            a_idx, b_idx = int(a_idx), int(b_idx)
            ax, ay = node_pos[a_idx]
            bx, by = node_pos[b_idx]
            cx = (ax + bx) / 2.0
            cy = float(dist)
            node_pos[n_alts + step] = (cx, cy)
            dendro_max_h = max(dendro_max_h, cy)
            dendro_links.append({
                "x": [ax, ax, bx, bx],
                "y": [ay, cy, cy, by],
            })
    similarity = {
        "alt_ids":    [a["id"] for a in alts],
        "is_optimal": [a["is_optimal"] for a in alts],
        "distance":   sim_dist.tolist(),
        "order":      order,
        "clusters":   clusters,
        "dendro_links": dendro_links,
        "dendro_max_height": float(dendro_max_h * 1.05),
    }

    # ── Bi-clustered alternatives × decision-factors heatmap ──
    # Rows (alternatives) are reordered by the Euclidean-distance
    # linkage the Similarity chart already computed (same Z_link →
    # the same alt-clustering across every MGA chart). Columns are
    # reordered by correlation distance, which captures co-investment
    # patterns regardless of absolute magnitude.
    #
    # Two granularities are pre-built so the user can toggle without a
    # round-trip:
    #   * "tech"      — one column per technology (n_tech + n_bat ≈ 13)
    #   * "tech_node" — one column per (technology, node) cell that any
    #                   alternative actually used > 1 MW; surfaces the
    #                   spatial substitution that the "tech" view hides
    #                   (e.g., is Alt A putting Solar PV in Cuba vs Alt B
    #                   in Holguín?)
    df_alt_order = list(range(n_alts))
    df_alt_links: list[dict] = []
    df_alt_max_h = 0.0
    if Z_link is not None and n_alts >= 2:
        df_alt_order = order
        leaf_to_row = {leaf: i for i, leaf in enumerate(df_alt_order)}
        node_pos: dict[int, tuple[float, float]] = {
            leaf: (0.0, float(leaf_to_row[leaf])) for leaf in range(n_alts)
        }
        for step, row in enumerate(Z_link):
            a_idx, b_idx, dist, _count = row
            a_idx, b_idx = int(a_idx), int(b_idx)
            ax, ay = node_pos[a_idx]
            bx, by = node_pos[b_idx]
            cx = float(dist)
            cy = (ay + by) / 2.0
            node_pos[n_alts + step] = (cx, cy)
            df_alt_max_h = max(df_alt_max_h, cx)
            # U-shape rotated 90°: row index on Y, distance on X. The
            # JS reverses the xaxis range so distance grows leftwards
            # away from the heatmap.
            df_alt_links.append({
                "x": [ax, cx, cx, bx],
                "y": [ay, ay, by, by],
            })

    def _make_df_view(col_data: np.ndarray,
                      col_labels: list[str],
                      col_categories: list[str]) -> dict:
        """Build a bi-clustered view payload.

        ``col_data`` is ``[n_alts, n_cols]`` (still in the original
        column order); we run correlation-distance linkage on the
        columns, reorder both axes, and return the JSON-ready dict the
        JS expects. Empty / single-column inputs degrade gracefully."""
        n_cols = col_data.shape[1] if col_data.ndim == 2 else 0
        col_order = list(range(n_cols))
        col_links: list[dict] = []
        col_max_h = 0.0
        if n_cols >= 2:
            try:
                from scipy.cluster.hierarchy import (
                    linkage as _linkage, leaves_list as _leaves,
                )
                from scipy.spatial.distance import pdist as _pdist
                d = _pdist(col_data.T, metric="correlation")
                d = np.nan_to_num(d, nan=1.0)
                Z_col = _linkage(d, method="average")
                col_order = _leaves(Z_col).tolist()
                leaf_to_col = {leaf: i for i, leaf in enumerate(col_order)}
                cpos: dict[int, tuple[float, float]] = {
                    leaf: (float(leaf_to_col[leaf]), 0.0)
                    for leaf in range(n_cols)
                }
                for step, row in enumerate(Z_col):
                    a_idx, b_idx, dist, _count = row
                    a_idx, b_idx = int(a_idx), int(b_idx)
                    ax, ay = cpos[a_idx]
                    bx, by = cpos[b_idx]
                    cx = (ax + bx) / 2.0
                    cy = float(dist)
                    cpos[n_cols + step] = (cx, cy)
                    col_max_h = max(col_max_h, cy)
                    col_links.append({
                        "x": [ax, ax, bx, bx],
                        "y": [ay, cy, cy, by],
                    })
            except Exception:
                pass
        # Reorder rows + columns and convert to JSON-friendly lists.
        if n_cols == 0:
            matrix = [[] for _ in df_alt_order]
        else:
            matrix = col_data[np.ix_(df_alt_order, col_order)].tolist()
        ordered_labels = [col_labels[i] for i in col_order]
        ordered_cats = [col_categories[i] for i in col_order]
        ordered_colors = [_MGA_CATEGORY_COLORS.get(c, "#7f8c8d")
                          for c in ordered_cats]
        return {
            "tech_labels":     ordered_labels,
            "tech_categories": ordered_cats,
            "tech_colors":     ordered_colors,
            "matrix":          matrix,
            "tech_dendro_links": col_links,
            "tech_max_height": float(col_max_h * 1.05),
        }

    # ── View A: tech (aggregated across nodes) ──
    tech_keep = [t for t in range(inv_arr.shape[1])
                 if inv_arr[:, t].max() > 1e-3]
    view_tech = _make_df_view(
        col_data=inv_arr[:, tech_keep],
        col_labels=[all_labels[t] for t in tech_keep],
        col_categories=[all_meta[t]["category"] for t in tech_keep],
    )

    # ── View B: tech × node (drill-down to placement) ──
    # Build the (tech, node) → per-alt vector matrix from inv_by_node,
    # keep only cells where any alternative invested > 1 MW so the
    # heatmap doesn't drown in zero columns.
    tn_threshold = 1.0  # MW
    tn_cells: list[tuple[int, int]] = []   # (tech_idx, node_idx)
    tn_col_data: list[list[float]] = []
    for t in range(n_tech + n_bat):
        for nd in range(n_nodes):
            col = [
                (inv_by_node[ai][t][nd]
                 if (t < len(inv_by_node[ai])
                     and nd < len(inv_by_node[ai][t]))
                 else 0.0)
                for ai in range(n_alts)
            ]
            if max(col) > tn_threshold:
                tn_cells.append((t, nd))
                tn_col_data.append(col)
    if tn_col_data:
        tn_arr = np.asarray(tn_col_data, dtype=float).T   # → [alts × cells]
    else:
        tn_arr = np.zeros((n_alts, 0))
    tn_col_labels = []
    tn_col_categories = []
    for t, nd in tn_cells:
        tech_full = all_labels[t]
        tech_short = tech_full.split("/", 1)[-1]
        node_lab = (node_labels[nd] if nd < len(node_labels)
                    else f"Node {nd}")
        tn_col_labels.append(f"{tech_short} @ {node_lab}")
        tn_col_categories.append(all_meta[t]["category"])
    view_tech_node = _make_df_view(
        col_data=tn_arr,
        col_labels=tn_col_labels,
        col_categories=tn_col_categories,
    )

    df_alt_labels = [
        (("★ " if alts[i]["is_optimal"] else "") + f"Alt {alts[i]['id']}")
        for i in df_alt_order
    ]
    decision_factors = {
        "alt_ids":          [alts[i]["id"] for i in df_alt_order],
        "is_optimal":       [alts[i]["is_optimal"] for i in df_alt_order],
        "alt_labels":       df_alt_labels,
        "alt_dendro_links": df_alt_links,
        "alt_max_height":   float(df_alt_max_h * 1.05),
        "views": {
            "tech":      view_tech,
            "tech_node": view_tech_node,
        },
    }

    # ── Annotated circular dendrogram ──
    # The clustering tree is drawn radially (root in centre, leaves on
    # the rim) and wrapped with three concentric annotation tracks
    # built from the same alternatives data the other charts use:
    #   - Track 0 = stacked categorical bar (MW share per category)
    #   - Track 1 = peak RE share heatmap arc
    #   - Track 2 = cost premium (% above optimal) heatmap arc
    # Coordinates are pre-converted to Cartesian here so the JS only
    # has to feed them to Plotly scatter line/fill traces — no client
    # side trig or scipy is required.
    annotated_dendrogram: dict = {
        "alt_ids":      [a["id"] for a in alts],
        "is_optimal":   [a["is_optimal"] for a in alts],
        "clusters":     clusters,
        "leaf_order":   order,
        "leaf_radius":  1.0,
        "tree_links":   [],   # list of {x: [...], y: [...]} for each merge
        "leaf_labels":  [],   # {x, y, text, angle_deg, is_optimal}
        "ring_segments":[],   # list of {x, y, color, hover} polygons
        "ring_radii":   [],   # boundary radii used by the rim
        "max_height":   0.0,
        "categories":   [],   # legend order (Solar / Wind / ... )
        "category_colors": {},
    }
    if Z_link is not None and n_alts >= 2:
        import math

        # Equally-spaced angles (radians) for each leaf in display
        # order, starting at the top (-π/2) and going clockwise.
        leaf_angle = [
            -math.pi / 2 + 2 * math.pi * i / n_alts for i in range(n_alts)
        ]
        leaf_to_angle = {leaf: leaf_angle[i] for i, leaf in enumerate(order)}

        max_h = float(np.max(Z_link[:, 2])) if Z_link.shape[0] > 0 else 1.0
        R_tree = 1.0    # leaf radius (tree's outer envelope)
        # Convert linkage height into a radial position: leaves at
        # R_tree, root at 0.05·R_tree so it never collapses to a point.
        def height_to_r(h):
            return R_tree * (1.0 - 0.95 * (h / max_h))

        node_polar: dict[int, tuple[float, float]] = {
            leaf: (R_tree, leaf_to_angle[leaf]) for leaf in range(n_alts)
        }

        def arc_segment(r, th0, th1, steps=20):
            """Polar arc at radius r from angle th0 to th1, returned as
            (xs, ys) lists in Cartesian coordinates."""
            if th1 < th0:
                th0, th1 = th1, th0
            # If the arc spans more than π, draw it the short way around
            # so the dendrogram never wraps past the opposite side.
            if th1 - th0 > math.pi:
                th0 += 2 * math.pi
                th0, th1 = th1, th0
            ths = np.linspace(th0, th1, max(steps, 6))
            return ([r * math.cos(t) for t in ths],
                    [r * math.sin(t) for t in ths])

        for step, row in enumerate(Z_link):
            a_idx, b_idx, dist, _count = row
            a_idx, b_idx = int(a_idx), int(b_idx)
            ra, tha = node_polar[a_idx]
            rb, thb = node_polar[b_idx]
            r_parent = height_to_r(float(dist))
            # Parent angle = midpoint of the children's angles (taking
            # the short way around the circle).
            d_th = thb - tha
            if d_th > math.pi:
                tha += 2 * math.pi
            elif d_th < -math.pi:
                thb += 2 * math.pi
            th_parent = (tha + thb) / 2.0
            # 1) Radial segment from each child to the parent's radius.
            for r_child, th_child in ((ra, tha), (rb, thb)):
                annotated_dendrogram["tree_links"].append({
                    "x": [r_child * math.cos(th_child),
                          r_parent * math.cos(th_child)],
                    "y": [r_child * math.sin(th_child),
                          r_parent * math.sin(th_child)],
                })
            # 2) Arc at the parent's radius joining the two children.
            xs, ys = arc_segment(r_parent, tha, thb)
            annotated_dendrogram["tree_links"].append({"x": xs, "y": ys})
            node_polar[n_alts + step] = (r_parent, th_parent)

        annotated_dendrogram["max_height"] = float(max_h)

        # Leaf labels on the rim, just outside the outermost annotation
        # track. Rotation is set so the text reads outward from the
        # centre — JS keeps it upright when the angle lands in the
        # left half of the circle.
        R_lab = R_tree + 0.78
        for i, leaf in enumerate(order):
            th = leaf_angle[i]
            annotated_dendrogram["leaf_labels"].append({
                "x": R_lab * math.cos(th),
                "y": R_lab * math.sin(th),
                "angle_deg": math.degrees(th),
                "text": f"Alt {alts[leaf]['id']}",
                "is_optimal": bool(alts[leaf]["is_optimal"]),
                "cluster": int(clusters[leaf]),
            })

        # ── Annotation tracks ──
        cats_seen = []
        cat_color = {}
        per_alt_cat_mw: list[dict[str, float]] = []
        for ai in range(n_alts):
            sums: dict[str, float] = {}
            for ti, m in enumerate(all_meta):
                v = float(inv_arr[ai, ti])
                if v < 1e-3:
                    continue
                sums[m["category"]] = sums.get(m["category"], 0.0) + v
                if m["category"] not in cat_color:
                    cat_color[m["category"]] = _MGA_CATEGORY_COLORS.get(
                        m["category"], "#7f8c8d")
                    cats_seen.append(m["category"])
            per_alt_cat_mw.append(sums)
        annotated_dendrogram["categories"] = list(cats_seen)
        annotated_dendrogram["category_colors"] = dict(cat_color)

        # Track radial bands (inner to outer).
        T0_R0, T0_R1 = R_tree + 0.02, R_tree + 0.42  # Composition stack
        T1_R0, T1_R1 = R_tree + 0.46, R_tree + 0.56  # RE peak
        T2_R0, T2_R1 = R_tree + 0.60, R_tree + 0.70  # Cost premium
        annotated_dendrogram["ring_radii"] = [T0_R0, T0_R1, T1_R0, T1_R1,
                                              T2_R0, T2_R1, R_lab]

        # Per-leaf wedge half-width — leave a tiny gap so wedges read
        # as discrete bars and not a continuous ring.
        half = math.pi / n_alts * 0.9

        def wedge_polygon(r0, r1, th_centre, hw, color, hover):
            """A four-arc polygon (inner arc + outer arc + radial sides),
            ready to plug into a Plotly scatter trace with fill=toself."""
            th0, th1 = th_centre - hw, th_centre + hw
            xs, ys = [], []
            # Outer arc th0 → th1
            ths = np.linspace(th0, th1, 14)
            xs.extend(r1 * math.cos(t) for t in ths)
            ys.extend(r1 * math.sin(t) for t in ths)
            # Inner arc th1 → th0
            ths = np.linspace(th1, th0, 14)
            xs.extend(r0 * math.cos(t) for t in ths)
            ys.extend(r0 * math.sin(t) for t in ths)
            xs.append(xs[0]); ys.append(ys[0])
            return {
                "x": xs, "y": ys, "color": color, "hover": hover,
                "track": None,
            }

        # ── Track 0: stacked categorical composition (one stacked bar
        #    per leaf, oriented radially). The radial extent of each
        #    sub-wedge ∝ MW share of its category in that alternative. ──
        cost_pct = [a["cost_pct_above_optimal"] for a in alts]
        re_peak  = [a["re_peak_pct"] for a in alts]
        max_cost = max(cost_pct + [0.001])
        max_re   = max(re_peak + [0.001])
        # Reds and greens for the two scalar rings.
        def red_scale(v):
            v = max(0.0, min(1.0, v))
            return f"rgba(192,57,43,{0.25 + 0.65 * v:.3f})"
        def green_scale(v):
            v = max(0.0, min(1.0, v))
            return f"rgba(39,174,96,{0.25 + 0.65 * v:.3f})"

        # Track 1 swaps semantics by method:
        #   * ``"mga"``    → peak RE (green ramp) — original behaviour.
        #   * ``"spores"`` → SPORES objective tag, one colour per
        #                    objective from the bundle palette. Without
        #                    this swap the RE peak ring saturates at 100%
        #                    for every SPORES alt (each objective drives
        #                    the system to its limit) so the green ramp
        #                    carries no signal.
        track1_kind = "objective" if method == "spores" else "re_peak"
        annotated_dendrogram["track1_kind"] = track1_kind

        for i, leaf in enumerate(order):
            th_c = leaf_angle[i]
            sums = per_alt_cat_mw[leaf]
            total = sum(sums.values())
            r_lo = T0_R0
            r_extent = T0_R1 - T0_R0
            for cat in cats_seen:
                v = sums.get(cat, 0.0)
                if total <= 0 or v <= 0:
                    continue
                share = v / total
                r_hi = r_lo + r_extent * share
                w = wedge_polygon(
                    r_lo, r_hi, th_c, half, cat_color[cat],
                    f"<b>Alt {alts[leaf]['id']}</b><br>{cat}: "
                    f"{v:,.0f} MW ({share * 100:.0f}%)")
                w["track"] = "composition"
                annotated_dendrogram["ring_segments"].append(w)
                r_lo = r_hi
            # ── Track 1 — RE peak (green ramp) for MGA, objective tag
            #    for SPORES ──
            if track1_kind == "re_peak":
                v_re = re_peak[leaf] / max_re if max_re > 0 else 0.0
                w_re = wedge_polygon(
                    T1_R0, T1_R1, th_c, half, green_scale(v_re),
                    f"<b>Alt {alts[leaf]['id']}</b><br>Peak RE: "
                    f"{re_peak[leaf]:.1f}%")
                w_re["track"] = "re_peak"
                annotated_dendrogram["ring_segments"].append(w_re)
            else:
                obj = alts[leaf]["objective"]
                obj_color = _MGA_OBJECTIVE_COLORS.get(obj, "#7f8c8d")
                obj_label = _MGA_OBJECTIVE_LABELS.get(
                    obj, obj.replace("_", " ").title())
                w_obj = wedge_polygon(
                    T1_R0, T1_R1, th_c, half, obj_color,
                    f"<b>Alt {alts[leaf]['id']}</b><br>"
                    f"Objective: {obj_label}")
                w_obj["track"] = "objective"
                annotated_dendrogram["ring_segments"].append(w_obj)
            # ── Track 2 ── Cost premium (red ramp) ──
            v_c = cost_pct[leaf] / max_cost if max_cost > 0 else 0.0
            w_c = wedge_polygon(
                T2_R0, T2_R1, th_c, half, red_scale(v_c),
                f"<b>Alt {alts[leaf]['id']}</b><br>+"
                f"{cost_pct[leaf]:.2f}% above optimal")
            w_c["track"] = "cost_premium"
            annotated_dendrogram["ring_segments"].append(w_c)

    # ── Composition per alt: Category > Tech for treemap ──
    composition_alts: list[dict] = []
    for ai, a in enumerate(alts):
        cat_totals: dict[str, float] = {}
        techs_in_cat: dict[str, list[tuple[str, float]]] = {}
        for ti, m in enumerate(all_meta):
            v = float(inv_arr[ai, ti])
            if v < 1e-3:
                continue
            cat = m["category"]
            cat_totals[cat] = cat_totals.get(cat, 0.0) + v
            techs_in_cat.setdefault(cat, []).append((m["name"], v))
        labels = ["All"]
        parents = [""]
        values = [sum(cat_totals.values())]
        for cat, ctot in cat_totals.items():
            labels.append(cat)
            parents.append("All")
            values.append(ctot)
            for tname, tval in techs_in_cat[cat]:
                labels.append(tname)
                parents.append(cat)
                values.append(tval)
        composition_alts.append({
            "id": a["id"], "is_optimal": a["is_optimal"],
            "labels": labels, "parents": parents, "values": values,
        })

    # SPORES vs MGA branding for the bundle. Each alt already carries
    # ``objective``; we hand the JS a precomputed colour map keyed on
    # the objective string and a list of distinct objectives in display
    # order so the chart can render a stable legend without scanning
    # the alt list.
    distinct_objectives = list(method_objectives)
    if not distinct_objectives:
        seen: set = set()
        for a in alts:
            o = a["objective"]
            if a["is_optimal"] or o in seen:
                continue
            seen.add(o); distinct_objectives.append(o)
    objective_palette = {
        o: _MGA_OBJECTIVE_COLORS.get(o, "#7f8c8d")
        for o in (["cost_optimal"] + distinct_objectives)
    }
    objective_labels_map = {
        o: _MGA_OBJECTIVE_LABELS.get(o, o.replace("_", " ").title())
        for o in objective_palette.keys()
    }

    return {
        "header": {
            "optimal_cost_busd": optimal_cost / 1e9,
            "cost_limit_busd":   optimal_cost * (1 + slack) / 1e9,
            "slack_pct": slack * 100.0,
            "n_alternatives": len(alts),
            "robust_techs": robust,
            "flexible_techs": flexible,
            "method": method,
            "objectives": distinct_objectives,
            "objective_colors": objective_palette,
            "objective_labels": objective_labels_map,
        },
        "years": years_arr,
        "alternatives": alts,
        "tech_range": tech_range,
        "parcoords": parcoords,
        "pathways": {
            "categories": pathway_cats,
            "colors":     [_MGA_CATEGORY_COLORS[c] for c in pathway_cats],
            "alts":       pathways_alts,
        },
        "spatial": spatial,
        "projections": projections,
        "similarity":  similarity,
        "decision_factors": decision_factors,
        "annotated_dendrogram": annotated_dendrogram,
        "composition": composition_alts,
    }


def _load_mga_bundle(h5_path: Path, base_prefix: str) -> dict:
    """Cache-aware loader: all five MGA charts share the same bundle so
    the HDF5 read + analytics happen exactly once per batch render."""
    cache = _active_cache()
    if cache is not None:
        store = getattr(cache, "mga_bundle", None)
        if store is None:
            cache.mga_bundle = store = {}
        if base_prefix in store:
            return store[base_prefix]
    with _open_h5(h5_path) as h5f:
        bundle = _build_mga_bundle(h5f, base_prefix)
    if cache is not None and "error" not in bundle:
        cache.mga_bundle[base_prefix] = bundle
    return bundle


class _MGAChartBridge(QObject):
    """Shared QWebChannel bridge for all five MGA charts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class _MGAChartBase(QWidget):
    """Shared scaffolding for the five MGA charts: QWebEngineView + bridge
    + standard ``_safe_update``/``export_image``. Subclasses set
    ``TITLE`` / ``TR_KEY`` / ``_HTML_FILE`` and implement
    :py:meth:`_build_payload`."""

    TITLE = "MGA"
    TR_KEY = ""
    _HTML_FILE = ""

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _MGAChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        s = self._view.page().settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / self._HTML_FILE)))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        raise NotImplementedError


class MGARobustnessFrontierChart(_MGAChartBase):
    """Two-panel view that contextualises the cost slack against the
    tech-level robustness it buys.

    Top — Cost ↔ Diversity Frontier: every alternative as a point in
    (diversity, system cost) space, with the cost-slack envelope
    shaded so the eye instantly sees how much extra cost the MGA run
    is willing to spend for configurational diversity.

    Bottom — Decision Robustness: horizontal range plot of total
    invested MW per technology, min↔max across alternatives, sorted
    by coefficient of variation. The narrowest bars at the bottom of
    the list are the "must-build" technologies that survive every
    near-optimal alternative; the widest at the top are the swappable
    ones the slack let MGA explore.

    Reading top-to-bottom answers a single question end-to-end: *given
    this much cost slack, which technologies are still nailed down?*"""

    TITLE = "Robust Frontier"
    TR_KEY = "results_charts.mga_robust_frontier"
    _HTML_FILE = "mga_robustness_frontier.html"

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {
            "header":       b["header"],
            "tech_range":   b["tech_range"],
            "alternatives": b["alternatives"],
        }


class MGAParcoordsChart(_MGAChartBase):
    """Parallel-coordinates view across cost, diversity, peak RE and
    capacity totals — each alternative a polyline. Reveals criterion
    correlations (e.g. does low cost imply low diversity?)."""

    TITLE = "Criterion Trade-offs"
    TR_KEY = "results_charts.mga_criterion_tradeoffs"
    _HTML_FILE = "mga_parcoords.html"

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {"header": b["header"], "parcoords": b["parcoords"]}


class MGAPathwayChart(_MGAChartBase):
    """Pathway divergence — cumulative installed MW by category, one
    small-multiples mini stacked-area per alternative. Shows *when*
    alternatives diverge in their deployment timing."""

    TITLE = "Deployment Pathways"
    TR_KEY = "results_charts.mga_deployment_pathways"
    _HTML_FILE = "mga_pathway.html"

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {"header": b["header"], "years": b["years"], "pathways": b["pathways"]}


class MGASpatialChart(_MGAChartBase):
    """Spatial divergence heatmap — σ across alternatives of investment
    per (technology, node). Hotter cells = where the choice is most
    contested between alternatives."""

    TITLE = "Spatial Divergence"
    TR_KEY = "results_charts.mga_spatial"
    _HTML_FILE = "mga_spatial.html"

    # Same set the other heatmap charts use, so the UI feels consistent.
    _COLORSCALES = [
        "YlOrRd", "Turbo", "Viridis", "Plasma", "Magma", "Inferno",
        "Cividis", "Jet", "Hot", "Greys", "Electric",
        "YlGnBu", "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
    ]

    def __init__(self):
        super().__init__()
        self._colormap = "YlOrRd"

    def get_params_widget(self) -> Optional[QWidget]:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("Colormap"))
        cb = QComboBox()
        cb.addItems(self._COLORSCALES)
        cb.setCurrentText(self._colormap)
        cb.setMaxVisibleItems(8)
        cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
        cb.currentTextChanged.connect(self._on_colormap_changed)
        wl.addWidget(cb)
        wl.addStretch()
        return w

    def _on_colormap_changed(self, name: str):
        import json
        self._colormap = str(name)
        js = (
            f"if (typeof setColormap === 'function') "
            f"setColormap({json.dumps(self._colormap)});"
        )
        self._view.page().runJavaScript(js)

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {"header": b["header"], "spatial": b["spatial"],
                "colormap": self._colormap}


class MGAProjectionChart(_MGAChartBase):
    """Each alternative is a point in a 130-dim decision space (techs ×
    nodes). We project to 2-D via PCA (linear, interpretable) or t-SNE
    (non-linear, accentuates clusters). Reveals families of similar
    near-optimal solutions — the canonical SPORES visualisation."""

    TITLE = "Alternative Map"
    TR_KEY = "results_charts.mga_alternative_map"
    _HTML_FILE = "mga_projection.html"

    def __init__(self):
        super().__init__()
        self._method = "pca"

    def get_params_widget(self) -> Optional[QWidget]:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("Method:"))
        cb = QComboBox()
        for d, lab in (("pca", "PCA"), ("tsne", "t-SNE")):
            cb.addItem(lab, d)
        cb.setCurrentText("PCA" if self._method == "pca" else "t-SNE")
        cb.currentIndexChanged.connect(
            lambda _i, c=cb: self._on_method_changed(c.currentData()))
        wl.addWidget(cb)
        wl.addStretch()
        return w

    def _on_method_changed(self, method: str):
        import json
        self._method = str(method)
        self._view.page().runJavaScript(
            f"if (typeof setMethod === 'function') "
            f"setMethod({json.dumps(self._method)});"
        )

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {
            "header": b["header"],
            "projections": b["projections"],
            "method": self._method,
        }


class MGADecisionFactorsChart(_MGAChartBase):
    """Bi-clustered heatmap of alternatives × technologies. Rows are
    reordered by Euclidean-distance linkage over the decision vector
    (same tree the Similarity / Annotated Dendrogram charts use);
    columns are reordered by *correlation* distance over the MW
    pattern across alternatives, so technologies that move together
    end up adjacent. Cell colour encodes MW invested. Useful to see
    which decision factors discriminate between alternatives and
    which ones every alternative agrees on."""

    TITLE = "Decision Factors"
    TR_KEY = "results_charts.mga_decision_factors"
    _HTML_FILE = "mga_decision_factors.html"

    _COLORSCALES = [
        "Viridis", "Plasma", "Magma", "Inferno", "Cividis",
        "YlOrRd", "YlGnBu", "Turbo", "Jet", "Hot", "Greys",
        "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
        "Electric",
    ]

    # The two granularities the bundle pre-computes; ``tech`` is the
    # aggregated default, ``tech_node`` drills down to the placement
    # decision (one column per (technology, node) cell that any
    # alternative actually used > 1 MW).
    _GRANULARITIES = [
        ("tech",      "Tech"),
        ("tech_node", "Tech × Node"),
    ]

    def __init__(self):
        super().__init__()
        self._colormap = "Viridis"
        self._granularity = "tech"

    def get_params_widget(self) -> Optional[QWidget]:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("Granularity"))
        gb = QComboBox()
        for value, label in self._GRANULARITIES:
            gb.addItem(label, value)
        # Match selection to the saved key, not the display label.
        idx = next((i for i, (v, _) in enumerate(self._GRANULARITIES)
                    if v == self._granularity), 0)
        gb.setCurrentIndex(idx)
        gb.currentIndexChanged.connect(
            lambda _i, c=gb: self._on_granularity_changed(c.currentData())
        )
        wl.addWidget(gb)
        wl.addSpacing(12)
        wl.addWidget(QLabel("Colormap"))
        cb = QComboBox()
        cb.addItems(self._COLORSCALES)
        cb.setCurrentText(self._colormap)
        cb.setMaxVisibleItems(8)
        cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
        cb.currentTextChanged.connect(self._on_colormap_changed)
        wl.addWidget(cb)
        wl.addStretch()
        return w

    def _on_colormap_changed(self, name: str):
        import json
        self._colormap = str(name)
        self._view.page().runJavaScript(
            f"if (typeof setColormap === 'function') "
            f"setColormap({json.dumps(self._colormap)});"
        )

    def _on_granularity_changed(self, key: str):
        import json
        self._granularity = str(key)
        self._view.page().runJavaScript(
            f"if (typeof setGranularity === 'function') "
            f"setGranularity({json.dumps(self._granularity)});"
        )

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {
            "header": b["header"],
            "decision_factors": b["decision_factors"],
            "colormap": self._colormap,
            "granularity": self._granularity,
        }


class MGAAnnotatedDendrogramChart(_MGAChartBase):
    """Circular average-linkage dendrogram of MGA alternatives wrapped
    with concentric annotation rings: the inner rings carry the
    investment composition (category → MW) per alternative, the outer
    rings encode the cost penalty and the peak RE share. Combines the
    clustering view (which alternatives fuse) with the per-leaf
    attributes (why they cluster the way they do)."""

    TITLE = "Cluster Tree"
    TR_KEY = "results_charts.mga_cluster_tree"
    _HTML_FILE = "mga_annotated_dendrogram.html"

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {
            "header": b["header"],
            "annotated_dendrogram": b["annotated_dendrogram"],
        }


class MGACompositionChart(_MGAChartBase):
    """Small-multiples treemap: one treemap per alternative showing how
    investment is partitioned by category and technology. Complements the
    pathway view (which is temporal) with the FINAL composition."""

    TITLE = "Investment Composition"
    TR_KEY = "results_charts.mga_investment_composition"
    _HTML_FILE = "mga_composition.html"

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {"header": b["header"], "composition": b["composition"]}


class MGASimilarityChart(_MGAChartBase):
    """Clustered heatmap of pairwise Euclidean distances between MGA
    alternatives. Rows and columns share the same average-linkage
    leaf order; the dendrogram is drawn above the heatmap so the
    cluster structure (and the fusion distances) read at a glance."""

    TITLE = "Pairwise Similarity"
    TR_KEY = "results_charts.mga_pairwise_similarity"
    _HTML_FILE = "mga_similarity.html"

    # Same set the other heatmap charts (Spatial Divergence) use, so the
    # UI feels consistent across the MGA section.
    _COLORSCALES = [
        "Viridis", "Plasma", "Magma", "Inferno", "Cividis",
        "YlOrRd", "YlGnBu", "Turbo", "Jet", "Hot", "Greys",
        "RdBu", "Bluered", "Portland", "Earth", "Picnic", "Rainbow",
        "Electric",
    ]

    def __init__(self):
        super().__init__()
        self._colormap = "Viridis"

    def get_params_widget(self) -> Optional[QWidget]:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("Colormap"))
        cb = QComboBox()
        cb.addItems(self._COLORSCALES)
        cb.setCurrentText(self._colormap)
        cb.setMaxVisibleItems(8)
        cb.setStyleSheet("QComboBox { combobox-popup: 0; }")
        cb.currentTextChanged.connect(self._on_colormap_changed)
        wl.addWidget(cb)
        wl.addStretch()
        return w

    def _on_colormap_changed(self, name: str):
        import json
        self._colormap = str(name)
        js = (
            f"if (typeof setColormap === 'function') "
            f"setColormap({json.dumps(self._colormap)});"
        )
        self._view.page().runJavaScript(js)

    def _build_payload(self, h5_path, years, **kw):
        b = _load_mga_bundle(h5_path, kw.get("base_prefix", ""))
        if "error" in b:
            return b
        return {
            "header": b["header"],
            "similarity": b["similarity"],
            "colormap": self._colormap,
        }


# Placeholder so legacy callers/references don't crash; the new charts
# above replace it in `_CHART_CLASSES`.
class MGAComparisonChart(_MGAChartBase):
    """Deprecated placeholder kept so older configurations that still
    reference ``MGAComparisonChart`` by name don't crash; the five new
    ``MGA*`` charts above replace it in ``_CHART_CLASSES``."""

    TITLE = "MGA (deprecated)"
    TR_KEY = "results_charts.mga_comparison"
    _HTML_FILE = "mga_robust.html"

    def _build_payload(self, h5_path, years, **kw):
        return {"error": "This chart has been split into five MGA charts."}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart 12 — Fuel Supply (primary energy supply/demand by fuel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Color palette for fuel types
_FUEL_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#34495e", "#d35400", "#7f8c8d",
]


class _FuelSupplyBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class FuelSupplyChart(QWidget):
    """Interactive Plotly version of Fuel Supply.

    Two stacked subplots: yearly generation by fuel type (stacked bars)
    and total generation vs. demand (side-by-side bars, with Loss of
    Load stacked on the demand bar when present). Same data pipeline as
    the matplotlib version — joins generator names to their ``fuel``
    config attribute, falls back on the canonical-tech category.
    """

    TITLE = "Fuel Supply"
    TR_KEY = "results_charts.fuel_supply"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _FuelSupplyBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "fuel_supply.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_gen_by_fuel: dict[int, dict[str, float]] = {}
        year_cost_by_fuel: dict[int, dict[str, float]] = {}
        all_fuels: set[str] = set()

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)

            # gen_name → (fuel, avg_fuel_cost $/MWh). Average across
            # the time-varying fuel_cost vector so each fuel gets a
            # single price for the year-cost aggregation.
            gen_fuel_info: dict[str, tuple[str, float]] = {}
            for gc in gen_configs:
                name = gc.get("name", "")
                if isinstance(name, bytes):
                    name = name.decode()
                fuel = gc.get("fuel", "")
                if isinstance(fuel, bytes):
                    fuel = fuel.decode()
                if not fuel or fuel.lower() in ("none", ""):
                    continue
                fc_raw = gc.get("fuel_cost", None)
                avg_fc = 0.0
                if fc_raw is not None:
                    if isinstance(fc_raw, (list, np.ndarray)):
                        vals = [float(v) for v in fc_raw if float(v) > 0]
                        avg_fc = float(np.mean(vals)) if vals else 0.0
                    elif isinstance(fc_raw, str):
                        try:
                            parsed = [float(v) for v in fc_raw.strip("[]").split(",")
                                      if float(v.strip()) > 0]
                            avg_fc = float(np.mean(parsed)) if parsed else 0.0
                        except (ValueError, TypeError):
                            avg_fc = 0.0
                    else:
                        try:
                            v = float(fc_raw)
                            avg_fc = v if v > 0 else 0.0
                        except (ValueError, TypeError):
                            avg_fc = 0.0
                gen_fuel_info[name] = (fuel, avg_fc)

            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                gen_data = _load_gen_data(sc)
                gen_by_fuel: dict[str, float] = {}
                cost_by_fuel: dict[str, float] = {}

                for gen_name, arr in gen_data.items():
                    total = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    gen_mwh = float(np.sum(total)) * tres
                    # Match generator → (fuel, avg_fuel_cost)
                    matched_fuel = None
                    matched_fc = 0.0
                    for cfg_name, (cfg_fuel, cfg_fc) in gen_fuel_info.items():
                        suffix = cfg_name.split("/")[-1] if "/" in cfg_name else cfg_name
                        if cfg_name.endswith(gen_name) or gen_name.endswith(suffix):
                            matched_fuel = cfg_fuel
                            matched_fc = cfg_fc
                            break
                    if matched_fuel is None:
                        _, cat = _canonical_tech_name(gen_name)
                        matched_fuel = "Renewable" if cat == "renewable" else "Other"
                    gen_by_fuel[matched_fuel] = gen_by_fuel.get(matched_fuel, 0.0) + gen_mwh
                    all_fuels.add(matched_fuel)
                    cost = gen_mwh * matched_fc
                    if cost > 0:
                        cost_by_fuel[matched_fuel] = cost_by_fuel.get(matched_fuel, 0.0) + cost

                year_gen_by_fuel[int(year)] = gen_by_fuel
                year_cost_by_fuel[int(year)] = cost_by_fuel

        if not year_gen_by_fuel:
            return {"error": "No generation data"}

        sorted_years = sorted(year_gen_by_fuel.keys())
        fuel_list = sorted(all_fuels)
        # Same palette as the matplotlib version. Reuse the same colour
        # for the same fuel across both subplots — legendgroup binds
        # the two bars together so a single legend click toggles both.
        fuels_payload = []
        fuel_costs_payload = []
        for i, fuel in enumerate(fuel_list):
            gen_vals = [float(year_gen_by_fuel.get(y, {}).get(fuel, 0.0)) / 1e3
                        for y in sorted_years]
            cost_vals = [float(year_cost_by_fuel.get(y, {}).get(fuel, 0.0)) / 1e6
                         for y in sorted_years]
            color = _FUEL_COLORS[i % len(_FUEL_COLORS)]
            if any(v > 0 for v in gen_vals):
                fuels_payload.append({
                    "label": fuel, "color": color, "values_gwh": gen_vals,
                })
            if any(v > 0 for v in cost_vals):
                fuel_costs_payload.append({
                    "label": fuel, "color": color, "values_musd": cost_vals,
                })

        # Trend lines: sum across fuels per year, in the same units as
        # the corresponding subplot (GWh / M$).
        total_gen_gwh = [
            sum(year_gen_by_fuel.get(y, {}).values()) / 1e3
            for y in sorted_years
        ]
        total_cost_musd = [
            sum(year_cost_by_fuel.get(y, {}).values()) / 1e6
            for y in sorted_years
        ]

        return {
            "years": [str(y) for y in sorted_years],
            "fuels": fuels_payload,
            "fuel_costs": fuel_costs_payload,
            "total_gen_gwh": total_gen_gwh,
            "total_cost_musd": total_cost_musd,
        }




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Energy Flow Sankey (Plotly + QWebEngineView)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _SankeyBridge(QObject):
    """JS-facing bridge that ships the Plotly figure dict to the view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class SankeyEnergyFlowChart(QWidget):
    """Interactive Plotly Sankey of system-wide energy flows.

    Columns:
      0  Primary Energy (Sun, Wind, Fuel Oil, Gas, Water, Biomass …)
      1  Technologies   (Solar PV, Wind Turbine, Diesel, …)
      2  Electricity Bus
      3  End uses       (per-node demand, storage charge, curtailment,
                         unserved energy, transmission losses)

    Migrated from the matplotlib + Kaleido PNG wrapper to a native
    Plotly + QWebChannel render so hover / drag / zoom work and the
    GUI theme applies in real time, like every other chart.
    """

    TITLE = "Energy Flow (Sankey)"
    TR_KEY = "results_charts.sankey_energy_flow"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _SankeyBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "sankey.html")))

        self.fig = None
        self._loaded = False
        # Per-chart single-year combo — Sankey is inherently single-year,
        # so the global range slider doesn't apply. We track the active
        # year locally and offer a combo in the params bar.
        self._available_years: list[int] = []
        self._selected_year_idx: int = 0
        self._year_combo: Optional[QComboBox] = None
        self._last_args: Optional[tuple] = None

    def get_params_widget(self) -> Optional[QWidget]:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(QLabel("Year:"))
        cb = QComboBox()
        cb.addItems([str(y) for y in self._available_years])
        if self._available_years:
            cb.setCurrentIndex(
                min(self._selected_year_idx, len(self._available_years) - 1)
            )
        cb.currentIndexChanged.connect(self._on_year_combo_changed)
        wl.addWidget(cb)
        wl.addStretch()
        self._year_combo = cb
        return w

    def set_available_years(self, years: list[int]) -> None:
        """Populate the per-chart year combo whenever the host's system
        selection changes. Keep the prior year if it still exists; fall
        back to the last available year otherwise."""
        prev = (
            self._available_years[self._selected_year_idx]
            if (self._available_years
                and 0 <= self._selected_year_idx < len(self._available_years))
            else None
        )
        self._available_years = list(years)
        if prev is not None and prev in self._available_years:
            self._selected_year_idx = self._available_years.index(prev)
        else:
            self._selected_year_idx = (
                len(self._available_years) - 1 if self._available_years else 0
            )
        if self._year_combo is not None:
            self._year_combo.blockSignals(True)
            self._year_combo.clear()
            self._year_combo.addItems([str(y) for y in self._available_years])
            if self._available_years:
                self._year_combo.setCurrentIndex(self._selected_year_idx)
            self._year_combo.blockSignals(False)

    def _on_year_combo_changed(self, idx: int):
        if idx < 0:
            return
        self._selected_year_idx = int(idx)
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("SankeyEnergyFlowChart re-render failed")

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        # Override year_idx with the per-chart combo's value so the
        # global range slider doesn't drive Sankey (single-year only).
        kw = dict(kw)
        kw.pop("year_range", None)
        kw["year_idx"] = self._selected_year_idx
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    # ── Payload builder (same data pipeline as before — only the
    # render path changes from PNG → JSON).
    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        import plotly.graph_objects as go

        bp = kw.get("base_prefix", "")
        year_idx = kw.get("year_idx", 0)
        tech_colors = get_generation_colors()
        default_color = get_generation_default_color()

        # ── Sankey graph primitives ──
        _nodes: list[str] = []
        _node_colors: list[str] = []
        _node_idx: dict[str, int] = {}
        _links_src: list[int] = []
        _links_tgt: list[int] = []
        _links_val: list[float] = []
        _links_clr: list[str] = []

        def _idx(name: str, color: str = "") -> int:
            if name in _node_idx:
                return _node_idx[name]
            i = len(_nodes)
            _node_idx[name] = i
            _nodes.append(name)
            _node_colors.append(color or default_color)
            return i

        def _link(src: int, tgt: int, val: float, clr: str):
            if val < 0.01:
                return
            _links_src.append(src)
            _links_tgt.append(tgt)
            _links_val.append(round(val, 2))
            _links_clr.append(clr)

        def _color_for(name: str) -> str:
            nl = name.lower()
            for key, clr in tech_colors.items():
                if key.lower() in nl:
                    return clr
            return default_color

        def _rgba(hex_color: str, alpha: float = 0.4) -> str:
            h = hex_color.lstrip("#")[:6]
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"

        # ── Fixed palette ──
        CLR_GRID_IN = "#DC2626"
        CLR_GRID_OUT = "#DC2626"
        CLR_STORAGE = "#7C3AED"
        CLR_CURT = "#E67E22"
        CLR_CONV_LOSS = "#991B1B"
        CLR_TX_LOSS = "#991B1B"
        CLR_UNSERVED = "#E74C3C"
        CLR_DEMAND_NODE = "#94A3B8"

        # Fuel colours
        FUEL_COLORS: dict[str, str] = {
            "Sun": "#FF6B35", "Wind": "#41B3D4", "Water": "#1E3A8A",
            "Biomass": "#2D5016", "Gas": "#8B4513", "Fuel_oil": "#2C2C2C",
            "Diesel": "#1C1C1C", "Other": "#6E6E6E", "OTEC": "#0E7490",
        }

        # ── Accumulators ──
        gen_by_tech: dict[str, float] = {}
        bat_charge_by_type: dict[str, float] = {}
        bat_discharge_by_type: dict[str, float] = {}
        demand_by_node: dict[str, float] = {}
        curtailment_total = 0.0
        loss_load_total = 0.0
        tx_loss_total = 0.0
        _tech_fuel: dict[str, str] = {}
        _tech_eff: dict[str, list[float]] = {}
        _bat_to_tech: dict[str, str] = {}
        _bat_eff: dict[str, float] = {}
        node_names: list[str] = []
        node_losses: list[float] = []

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            tech_configs = _load_tech_configs(h5f, bp)
            bat_tech_configs = _load_bat_tech_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
            gen_to_tech = CFLcoeVallcoeChart._build_gen_to_tech(
                gen_configs, tech_configs, bat_tech_configs, bat_configs,
            )

            # tech_display → fuel
            for cfg in tech_configs:
                tname = str(cfg.get("name", ""))
                display = tname.split("/", 1)[-1] if "/" in tname else tname
                fuel = str(cfg.get("fuel", ""))
                if fuel and display:
                    _tech_fuel[display] = fuel
            for cfg in gen_configs:
                fuel = str(cfg.get("fuel", ""))
                tech = CFLcoeVallcoeChart._resolve_tech(
                    str(cfg.get("name", "")).replace("/", " - "), gen_to_tech)
                if fuel and tech:
                    _tech_fuel.setdefault(tech, fuel)

            # tech_display → efficiency
            for cfg in gen_configs:
                tech = CFLcoeVallcoeChart._resolve_tech(
                    str(cfg.get("name", "")).replace("/", " - "), gen_to_tech)
                eff_raw = cfg.get("eff_at_rated", "")
                vals: list[float] = []
                if isinstance(eff_raw, str):
                    try:
                        import ast
                        eff_list = ast.literal_eval(eff_raw)
                        vals = [v for v in eff_list if v > 0]
                    except Exception:
                        pass
                elif isinstance(eff_raw, (list, np.ndarray)):
                    vals = [v for v in eff_raw if v > 0]
                elif eff_raw and float(eff_raw) > 0:
                    vals = [float(eff_raw)]
                if vals and tech:
                    _tech_eff.setdefault(tech, []).extend(vals)

            # bat_name → bat_tech display + round-trip efficiency
            for cfg in bat_configs or []:
                bname = str(cfg.get("name", ""))
                short = bname.split(" - ", 1)[-1] if " - " in bname else bname
                _bat_to_tech[bname] = short
                eff_ch = float(cfg.get("eff_charge", 0.95) or 0.95)
                eff_dc = float(cfg.get("eff_discharge", 0.95) or 0.95)
                _bat_eff[short] = eff_ch * eff_dc
            for cfg in bat_tech_configs:
                tname = str(cfg.get("name", ""))
                display = tname.split("/", 1)[-1] if "/" in tname else tname
                for bname in list(_bat_to_tech):
                    if display.lower() in bname.lower():
                        _bat_to_tech[bname] = display

            # Node names + line losses
            cfg = _open_system_config(h5f, bp)
            if cfg is not None and "nodes" in cfg:
                nn_grp = cfg["nodes"]
                if "name" in nn_grp:
                    try:
                        node_names = [x.decode("utf-8") if isinstance(x, bytes)
                                      else str(x) for x in nn_grp["name"][:]]
                    except Exception:
                        node_names = [f"Node {i}" for i in
                                      range(nn_grp["latitude"].shape[0])]
                if "losses" in nn_grp:
                    node_losses = list(nn_grp["losses"][:])

            # ── Select single year ──
            scenarios = list(_sorted_scenarios(h5f, bp))
            if year_idx >= len(scenarios):
                year_idx = len(scenarios) - 1
            sc_key, sel_year = scenarios[year_idx]
            sc = _open_scenario(h5f, bp, sc_key)
            gen_data = _load_gen_data(sc)

            # Generation by technology
            for name, arr in gen_data.items():
                tech = CFLcoeVallcoeChart._resolve_tech(name, gen_to_tech)
                gwh = float(np.sum(arr)) * tres / 1000.0
                if gwh > 0:
                    gen_by_tech[tech] = gen_by_tech.get(tech, 0) + gwh

            # Battery by type
            for dset_name, accum in [("battery_charge", bat_charge_by_type),
                                     ("battery_discharge", bat_discharge_by_type)]:
                bd = _load_bat_data(sc, dset_name)
                for bname, arr in bd.items():
                    bt = _bat_to_tech.get(bname, bname.split(" - ", 1)[-1]
                                          if " - " in bname else bname)
                    gwh = float(np.sum(arr)) * tres / 1000.0
                    if gwh > 0:
                        accum[bt] = accum.get(bt, 0) + gwh

            # Per-node demand
            if "demand" in sc:
                dem = sc["demand"][:]
                for ni in range(dem.shape[0]):
                    nname = node_names[ni] if ni < len(node_names) else f"Node {ni}"
                    gwh = float(np.sum(dem[ni, :])) * tres / 1000.0
                    if gwh > 0:
                        demand_by_node[nname] = demand_by_node.get(nname, 0) + gwh

            # Curtailment & loss of load
            if "curtailment" in sc:
                curtailment_total = float(np.sum(sc["curtailment"][:])) * tres / 1000.0
            if "loss_load" in sc:
                loss_load_total = float(np.sum(sc["loss_load"][:])) * tres / 1000.0

            # Transmission losses from node loss factors
            if node_losses and "demand" in sc:
                dem = sc["demand"][:]
                for ni in range(dem.shape[0]):
                    if ni < len(node_losses) and node_losses[ni] > 0:
                        node_dem = float(np.sum(dem[ni, :])) * tres / 1000.0
                        tx_loss_total += node_dem * node_losses[ni]
            # Fallback: estimate from power_flow if no loss factors
            if tx_loss_total < 0.01 and "power_flow" in sc:
                pf = sc["power_flow"][:]
                if pf.ndim == 3:
                    total_flow = float(np.sum(np.abs(pf))) * tres / 1000.0
                    tx_loss_total = total_flow * 0.02

        # ── Build the Sankey ──
        if not gen_by_tech:
            self.fig.clf()
            ax = self.fig.add_axes([0, 0, 1, 1])
            ax.text(0.5, 0.5, "No generation data available",
                    ha="center", va="center", fontsize=14, color="#888")
            ax.set_axis_off()
            self.draw()
            return

        total_generation = sum(gen_by_tech.values())
        total_bat_discharge = sum(bat_discharge_by_type.values())
        total_bat_charge = sum(bat_charge_by_type.values())

        # ── Column 0: Primary Energy Sources ──
        # ── Column 1: Generation Technologies ──
        # ── Column 2: Power Grid (In) ──
        grid_in = _idx("Power Grid (In)", CLR_GRID_IN)

        sorted_techs = sorted(gen_by_tech.items(), key=lambda x: -x[1])
        conv_loss_total = 0.0

        for tech, gen_gwh in sorted_techs:
            clr = _color_for(tech)
            tech_nd = _idx(f"Gen: {tech}", clr)

            eff_vals = _tech_eff.get(tech, [])
            avg_eff = float(np.mean(eff_vals)) if eff_vals else 1.0
            if avg_eff <= 0 or avg_eff > 1:
                avg_eff = 1.0
            primary_gwh = gen_gwh / avg_eff
            losses_gwh = primary_gwh - gen_gwh
            conv_loss_total += losses_gwh

            fuel = _tech_fuel.get(tech, tech)
            fuel_clr = FUEL_COLORS.get(fuel, _color_for(fuel))
            fuel_nd = _idx(f"Fuel: {fuel}", fuel_clr)

            # Fuel → Gen Technology
            _link(fuel_nd, tech_nd, primary_gwh, _rgba(fuel_clr, 0.35))
            # Gen Technology → Grid In
            _link(tech_nd, grid_in, gen_gwh, _rgba(clr, 0.35))
            # Gen Technology → Conversion Losses
            if losses_gwh > 0.1:
                loss_nd = _idx("Conversion Losses", CLR_CONV_LOSS)
                _link(tech_nd, loss_nd, losses_gwh, _rgba(CLR_CONV_LOSS, 0.2))

        # ── Column 3: Power Grid (Out) ──
        grid_out = _idx("Power Grid (Out)", CLR_GRID_OUT)
        grid_internal_loss = total_generation * 0.005  # ~0.5% internal grid loss
        grid_throughput = total_generation - grid_internal_loss

        # Grid In → Grid Out (net of internal losses)
        _link(grid_in, grid_out, grid_throughput, _rgba(CLR_GRID_IN, 0.25))
        # Grid In → Conversion Losses (internal)
        if grid_internal_loss > 0.1:
            _link(grid_in, _idx("Conversion Losses", CLR_CONV_LOSS),
                  grid_internal_loss, _rgba(CLR_CONV_LOSS, 0.15))

        # ── Storage: split In/Out per type ──
        bat_rt_losses_total = 0.0
        for bt in set(list(bat_charge_by_type) + list(bat_discharge_by_type)):
            ch = bat_charge_by_type.get(bt, 0)
            dc = bat_discharge_by_type.get(bt, 0)
            rt_eff = _bat_eff.get(bt, 0.85)
            rt_loss = ch - dc if ch > dc else ch * (1 - rt_eff)
            bat_rt_losses_total += max(rt_loss, 0)

            st_in = _idx(f"Storage: {bt} (In)", CLR_STORAGE)
            st_out = _idx(f"Storage: {bt} (Out)", CLR_STORAGE)

            # Grid Out → Storage In
            if ch > 0.01:
                _link(grid_out, st_in, ch, _rgba(CLR_STORAGE, 0.25))
            # Storage In → Storage Out (minus round-trip losses)
            if ch > 0.01 and dc > 0.01:
                _link(st_in, st_out, dc, _rgba(CLR_STORAGE, 0.25))
                loss = ch - dc
                if loss > 0.1:
                    _link(st_in, _idx("Conversion Losses", CLR_CONV_LOSS),
                          loss, _rgba(CLR_CONV_LOSS, 0.15))
            # Storage Out → Grid Out
            if dc > 0.01:
                _link(st_out, grid_out, dc, _rgba(CLR_STORAGE, 0.25))

        # ── Grid Out → Transmission Losses ──
        if tx_loss_total > 0.1:
            _link(grid_out, _idx("Transmission Losses", CLR_TX_LOSS),
                  tx_loss_total, _rgba(CLR_TX_LOSS, 0.2))

        # ── Grid Out → Curtailment ──
        if curtailment_total > 0.1:
            _link(grid_out, _idx("Curtailment", CLR_CURT),
                  curtailment_total, _rgba(CLR_CURT, 0.25))

        # ── Grid Out → Per-node demands ──
        for nname, gwh in sorted(demand_by_node.items(), key=lambda x: -x[1]):
            nd = _idx(nname, CLR_DEMAND_NODE)
            _link(grid_out, nd, gwh, _rgba(CLR_DEMAND_NODE, 0.25))

        # ── Unserved Energy → Grid Out (deficit) ──
        if loss_load_total > 0.1:
            _link(_idx("Unserved Energy", CLR_UNSERVED), grid_out,
                  loss_load_total, _rgba(CLR_UNSERVED, 0.25))

        # ── Build Plotly figure ──
        # Compute node throughput for labels
        node_in = [0.0] * len(_nodes)
        node_out = [0.0] * len(_nodes)
        for s, t, v in zip(_links_src, _links_tgt, _links_val):
            node_out[s] += v
            node_in[t] += v
        node_totals = [max(node_in[i], node_out[i]) for i in range(len(_nodes))]
        labels = [
            f"{_nodes[i]}<br>{node_totals[i]:,.0f} GWh"
            for i in range(len(_nodes))
        ]

        # Summary annotation
        total_demand = sum(demand_by_node.values())
        re_fuels = {"Sun", "Wind", "Water", "Biomass", "OTEC"}
        re_gen = sum(gwh for tech, gwh in gen_by_tech.items()
                     if _tech_fuel.get(tech, "") in re_fuels)
        re_pct = (re_gen / total_generation * 100) if total_generation > 0 else 0

        pfig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(
                label=labels,
                color=_node_colors,
                pad=20,
                thickness=20,
                line=dict(color="#2C3E50", width=0.5),
            ),
            link=dict(
                source=_links_src,
                target=_links_tgt,
                value=_links_val,
                color=_links_clr,
            ),
        ))

        title_text = (
            f"Energy Flow — {sel_year}"
            f"  |  Generation: {total_generation:,.0f} GWh"
            f"  |  Demand: {total_demand:,.0f} GWh"
            f"  |  RE: {re_pct:.1f}%"
        )
        # Pick up the active GUI palette so the Sankey blends with the
        # surrounding window. Falls back to white if the theme module
        # isn't importable for any reason.
        try:
            from esfex.visualization.theme import current_theme
            _cp = current_theme().colors
            _bg = _cp.surface_primary or "white"
            _fg = _cp.text_primary or "black"
        except Exception:
            _bg, _fg = "white", "black"

        pfig.update_layout(
            title=dict(text=title_text,
                       font=dict(size=14, family="Arial, sans-serif",
                                 color=_fg)),
            font=dict(size=11, family="Arial, sans-serif", color=_fg),
            margin=dict(l=10, r=10, t=50, b=10),
            paper_bgcolor=_bg,
            plot_bgcolor=_bg,
        )

        # Serialise the figure to Plotly JSON for the JS side to
        # newPlot. fig.to_plotly_json() returns a dict ready for the
        # bridge — no PNG render needed.
        return pfig.to_plotly_json()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Revenue & Profitability by Technology (interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _RevenueProfitabilityBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class RevenueProfitabilityChart(QWidget):
    """Revenue and per-technology profitability over the planning horizon.

    Reads ``technology_selling_prices/*`` attrs (``average_selling_price``,
    ``total_revenue``, ``total_generation``, ``technology_type``) — the
    solver already aggregates revenues, so we just bucket by canonical
    technology name and compare against the corresponding ``lcoe`` /
    ``battery_lcoe`` entries.
    """

    TITLE = "Revenue & Profitability"
    TR_KEY = "results_charts.revenue_profitability"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _RevenueProfitabilityBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "revenue_profitability.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    # ── Payload builder ──────────────────────────────────────────

    @staticmethod
    def _walk_selling_prices(grp, prefix=""):
        """Recursively yield (full_name, attrs) for every leaf group that
        carries a ``prices_weights`` dataset (i.e. an actual generator)."""
        for k in grp:
            item = grp[k]
            if not isinstance(item, _GROUP_T):
                continue
            full = f"{prefix}/{k}" if prefix else k
            if "prices_weights" in item:
                yield full, dict(item.attrs)
            else:
                yield from RevenueProfitabilityChart._walk_selling_prices(item, full)

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        # {year: {fuel_bucket: {revenue_$, gen_mwh}}}
        per_year: dict[int, dict[str, dict[str, float]]] = {}
        # gen-weighted LCOE accumulators across all years
        lcoe_sum_w: dict[str, float] = {}
        gen_for_lcoe: dict[str, float] = {}
        bucket_color: dict[str, str] = {}

        with _open_h5(h5_path) as h5f:
            # Resolve each selling-price / LCOE entry to its technology
            # via the explicit ``technology`` field on the generator
            # (matched to the technology table's ``key``), the same
            # mechanism Generation Mix uses. This buckets every plant
            # under its real tech (Wind Turbine, Solar PV, …) instead
            # of falling back to the "Other" catch-all when the
            # heuristic name scan can't classify a Spanish / ID name.
            gen_configs = _load_gen_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
            tech_configs = _load_tech_configs(h5f, bp)
            bat_tech_configs = _load_bat_tech_configs(h5f, bp)
            gen_tech_map = _build_gen_tech_map(
                gen_configs, tech_configs, bat_configs, bat_tech_configs,
            )

            def _resolve_bucket(name: str) -> tuple[str, str]:
                """Return ``(label, color)`` for a selling-price / LCOE
                entry. Only entries with no resolvable technology fall
                through to the grey "Other" bucket."""
                info = _resolve_gen_tech(name, gen_tech_map)
                if info is not None and info.get("label"):
                    return info["label"], (info.get("color") or "#7F8C8D")
                return "Other", "#7F8C8D"

            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                year_buckets: dict[str, dict[str, float]] = {}

                if "technology_selling_prices" in sc:
                    for name, attrs in self._walk_selling_prices(sc["technology_selling_prices"]):
                        rev = float(attrs.get("total_revenue", 0.0))
                        gen = float(attrs.get("total_generation", 0.0))
                        bucket, color = _resolve_bucket(name)
                        b = year_buckets.setdefault(bucket, {"revenue": 0.0, "gen_mwh": 0.0})
                        b["revenue"] += rev
                        b["gen_mwh"] += gen
                        if bucket not in bucket_color:
                            bucket_color[bucket] = color

                for grp_key in ("lcoe", "battery_lcoe"):
                    if grp_key not in sc:
                        continue
                    grp = sc[grp_key]
                    for name in grp:
                        arr = grp[name][:]
                        valid = arr[arr > 0]
                        if len(valid) == 0:
                            continue
                        avg = float(np.mean(valid))
                        bucket, _color = _resolve_bucket(name)
                        gen_mwh = year_buckets.get(bucket, {}).get("gen_mwh", 0.0)
                        if gen_mwh <= 0:
                            continue
                        lcoe_sum_w[bucket] = lcoe_sum_w.get(bucket, 0.0) + avg * gen_mwh
                        gen_for_lcoe[bucket] = gen_for_lcoe.get(bucket, 0.0) + gen_mwh

                per_year[int(year)] = year_buckets

        if not per_year:
            return {"error": "No revenue data"}

        sorted_years = sorted(per_year.keys())
        buckets = sorted({t for d in per_year.values() for t in d})

        # Per-bucket revenue series (M$). Drop buckets that earn nothing.
        techs_payload = []
        totals_musd = [0.0] * len(sorted_years)
        for bk in buckets:
            rev_series = []
            for i, y in enumerate(sorted_years):
                rev = per_year.get(y, {}).get(bk, {}).get("revenue", 0.0)
                rev_musd = rev / 1e6
                rev_series.append(rev_musd)
                totals_musd[i] += rev_musd
            if any(v > 0 for v in rev_series):
                techs_payload.append({
                    "label": bk,
                    "color": bucket_color.get(bk, "#7F8C8D"),
                    "revenue_musd": rev_series,
                })

        # Summary: weighted-average selling price + gen-weighted LCOE
        # across all years, profit margin = (price − LCOE) / price.
        summary = []
        for bk in buckets:
            total_rev = sum(per_year.get(y, {}).get(bk, {}).get("revenue", 0.0)
                            for y in sorted_years)
            total_gen = sum(per_year.get(y, {}).get(bk, {}).get("gen_mwh", 0.0)
                            for y in sorted_years)
            if total_gen <= 0:
                continue
            avg_price = total_rev / total_gen
            gen_w = gen_for_lcoe.get(bk, 0.0)
            avg_lcoe = (lcoe_sum_w[bk] / gen_w) if gen_w > 0 else 0.0
            margin_pct = (
                ((avg_price - avg_lcoe) / avg_price * 100.0)
                if avg_price > 1e-6 else None
            )
            summary.append({
                "label": bk,
                "color": bucket_color.get(bk, "#7F8C8D"),
                "avg_price": float(avg_price),
                "lcoe": float(avg_lcoe),
                "margin_pct": float(margin_pct) if margin_pct is not None else None,
            })
        summary.sort(key=lambda s: s["avg_price"], reverse=True)

        return {
            "years": [str(y) for y in sorted_years],
            "techs": techs_payload,
            "totals_musd": totals_musd,
            "summary": summary,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Price Duration & Composition (interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _PriceDurationBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class PriceDurationChart(QWidget):
    """Price duration curves + energy/congestion split.

    Pulls ``electricity_prices`` for the duration curve (one line per
    scenario year) and ``electricity_prices_energy`` /
    ``nodal_electricity_prices_congestion`` for the composition split.
    """

    TITLE = "Price Duration & Composition"
    TR_KEY = "results_charts.price_duration"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _PriceDurationBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "price_duration.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    @staticmethod
    def _ts_array(sc, key):
        if key not in sc:
            return None
        a = sc[key][:]
        return a.mean(axis=0) if a.ndim == 2 else a

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        duration_curves = []
        monthly_labels: list[str] = []
        monthly_energy: list[float] = []
        monthly_congestion: list[float] = []

        # Year palette — Plotly's "Viridis" sampled at evenly spaced
        # points so multi-year curves are distinguishable.
        def _year_color(i: int, n: int) -> str:
            t = i / max(1, n - 1)
            # Hand-picked viridis-ish 5-stop gradient (matches the chart's
            # plotly.min.js without depending on Plotly's colour module).
            stops = [
                (0.0, (68, 1, 84)),
                (0.25, (59, 82, 139)),
                (0.50, (33, 145, 140)),
                (0.75, (94, 201, 98)),
                (1.0, (253, 231, 37)),
            ]
            for j in range(len(stops) - 1):
                if stops[j][0] <= t <= stops[j + 1][0]:
                    f = (t - stops[j][0]) / (stops[j + 1][0] - stops[j][0])
                    r0, g0, b0 = stops[j][1]
                    r1, g1, b1 = stops[j + 1][1]
                    return "rgb(%d,%d,%d)" % (
                        int(r0 + (r1 - r0) * f),
                        int(g0 + (g1 - g0) * f),
                        int(b0 + (b1 - b0) * f),
                    )
            return "rgb(0,0,0)"

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            n = len(scenarios)
            for i, (sc_key, year) in enumerate(scenarios):
                sc = _open_scenario(h5f, bp, sc_key)

                # Price duration curve
                prices = self._ts_array(sc, "electricity_prices")
                if prices is None:
                    prices = self._ts_array(sc, "nodal_electricity_prices")
                if prices is not None and len(prices) > 0:
                    valid = prices[~np.isnan(prices)]
                    if len(valid) > 0:
                        sorted_desc = np.sort(valid)[::-1]
                        # Down-sample the curve to keep the payload small
                        # (we still hit every key inflection point at 500
                        # samples — enough resolution for the eye).
                        N_TARGET = 500
                        if len(sorted_desc) > N_TARGET:
                            idx = np.linspace(0, len(sorted_desc) - 1, N_TARGET,
                                              dtype=int)
                            sorted_desc = sorted_desc[idx]
                        x_pct = np.linspace(0, 100, len(sorted_desc)).tolist()
                        duration_curves.append({
                            "year": int(year),
                            "color": _year_color(i, n),
                            "prices_sorted_desc": [float(v) for v in sorted_desc],
                            "x_pct": x_pct,
                        })

                # Energy + congestion components, monthly-averaged.
                # These two arrays only live in the *global*
                # detailed_results group (not the per-system ones), so
                # fall back to the global scenario of the same key when
                # the per-system view doesn't have them.
                energy = self._ts_array(sc, "electricity_prices_energy")
                congestion = self._ts_array(sc, "nodal_electricity_prices_congestion")
                if (energy is None or congestion is None) \
                        and "detailed_results" in h5f \
                        and sc_key in h5f["detailed_results"]:
                    g_sc = h5f["detailed_results"][sc_key]
                    if energy is None:
                        energy = self._ts_array(g_sc, "electricity_prices_energy")
                    if congestion is None:
                        congestion = self._ts_array(g_sc, "nodal_electricity_prices_congestion")
                if energy is None and congestion is None:
                    continue
                # Use Python's helper to bin into 12 months at the
                # tres-aware resolution.
                e_monthly = _aggregate(energy, "monthly", tres) if energy is not None else None
                c_monthly = _aggregate(congestion, "monthly", tres) if congestion is not None else None
                # For prices we want averages, not sums — _aggregate
                # returns sums, so divide by hours-per-month at this tres.
                hpm = max(1, (HOURS_STD_YEAR // tres) // 12)
                if e_monthly is not None:
                    e_monthly = np.array(e_monthly) / hpm
                if c_monthly is not None:
                    c_monthly = np.array(c_monthly) / hpm

                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                ref = e_monthly if e_monthly is not None else c_monthly
                for m_idx in range(min(12, len(ref))):
                    monthly_labels.append(f"{year} {months[m_idx]}")
                    monthly_energy.append(
                        float(e_monthly[m_idx]) if e_monthly is not None else 0.0
                    )
                    # Treat the congestion component as the absolute
                    # premium over the energy price so the stacked area
                    # reads "total = energy + congestion".
                    monthly_congestion.append(
                        float(abs(c_monthly[m_idx]))
                        if c_monthly is not None else 0.0
                    )

        if not duration_curves and not monthly_labels:
            return {"error": "No price data"}

        return {
            "years": [str(dc["year"]) for dc in duration_curves],
            "duration_curves": duration_curves,
            "monthly": {
                "labels": monthly_labels,
                "energy": monthly_energy,
                "congestion": monthly_congestion,
            },
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Carbon & Reliability Penalties (interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _CarbonPenaltyBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class CarbonPenaltyChart(QWidget):
    """CO₂ emissions / intensity + reliability penalty quantities.

    The "feasibility cost" view: emissions normalised by demand, plus
    any reserve / loss-of-load violations that the solver couldn't
    cover. A run with no penalties leaves the bottom subplot empty —
    a visual confirmation of operational feasibility.
    """

    TITLE = "Carbon & Penalty Costs"
    TR_KEY = "results_charts.carbon_penalty"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _CarbonPenaltyBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "carbon_penalty.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    @staticmethod
    def _sum_scenario(sc, key, tres):
        """Sum a node × timestep dataset → total MWh (or tons) for the year."""
        if key not in sc:
            return 0.0
        a = sc[key][:]
        flat = _sum_nodes(a) if a.ndim >= 2 else a
        return float(np.sum(flat)) * tres

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        yr_list: list[int] = []
        co2_t: list[float] = []           # tons per year
        demand_mwh: list[float] = []       # MWh per year — for intensity
        loss_load: list[float] = []
        rsv_dyn: list[float] = []
        rsv_sta: list[float] = []

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                yr_list.append(int(year))

                # CO₂: the dataset stores tCO₂/h-equivalent per node,
                # so summing × tres gives total tons for the year.
                co2_t.append(self._sum_scenario(sc, "CO2_emissions", tres))

                # Demand for intensity normalisation.
                if "demand" in sc:
                    d = sc["demand"][:]
                    df = _sum_nodes(d) if d.ndim >= 2 else d
                    demand_mwh.append(float(np.sum(df)) * tres)
                else:
                    demand_mwh.append(0.0)

                loss_load.append(self._sum_scenario(sc, "loss_load", tres))
                rsv_dyn.append(self._sum_scenario(sc, "loss_of_reserve_dynamic", tres))
                rsv_sta.append(self._sum_scenario(sc, "loss_of_reserve_static", tres))

        if not yr_list:
            return {"error": "No data"}

        co2_mt = [v / 1e6 for v in co2_t]
        # Intensity = (g CO₂) / (kWh demand) = tons * 1e6 g/t  /  (MWh * 1e3 kWh/MWh)
        #          = tons / MWh * 1000.
        intensity = [
            (co2_t[i] / demand_mwh[i] * 1000.0) if demand_mwh[i] > 0 else 0.0
            for i in range(len(yr_list))
        ]

        return {
            "years": [str(y) for y in yr_list],
            "co2_mt": co2_mt,
            "co2_intensity_g_per_kwh": intensity,
            "loss_load_mwh": loss_load,
            "reserve_dynamic_violation_mwh": rsv_dyn,
            "reserve_static_violation_mwh": rsv_sta,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Cash Flow (interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _CashFlowBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class CashFlowChart(QWidget):
    """Project-level cash-flow view.

    Inflows: Revenue (from ``technology_selling_prices.total_revenue``).
    Outflows: Fuel cost (gen MWh × avg ``fuel_cost``), Investment CapEx
    (per-year sum of investment_cost × invest_mw), Loss-of-Load penalty
    (``loss_load`` MWh × VOLL configured in the params bar), CO₂ cost
    (``CO2_emissions`` × avg ``co2_emission_cost`` from gen_configs).

    Subplot (b) layers the cumulative undiscounted cash flow on top of
    the same series discounted at the user-selected rate (NPV running),
    and pin-points the first year the cumulative crosses zero.
    """

    TITLE = "Cash Flow"
    TR_KEY = "results_charts.cash_flow"

    def __init__(self):
        super().__init__()
        # Default assumptions (editable from the params bar):
        # 7% real WACC and 5,000 $/MWh VOLL are common defaults in
        # capacity-planning studies. The user can override anytime.
        self._discount_rate = 0.07
        self._voll = 5000.0

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _CashFlowBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "cash_flow.html")
        ))

        self._last_args: Optional[tuple] = None
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> QWidget:
        w = QWidget()
        wl = QHBoxLayout(w)
        wl.setContentsMargins(0, 0, 0, 0)

        wl.addWidget(QLabel("Discount rate (%)"))
        sp_d = QDoubleSpinBox()
        sp_d.setRange(0.0, 30.0)
        sp_d.setSingleStep(0.5)
        sp_d.setValue(self._discount_rate * 100)
        sp_d.valueChanged.connect(self._on_discount_changed)
        wl.addWidget(sp_d)

        wl.addWidget(QLabel("VOLL ($/MWh)"))
        sp_v = QDoubleSpinBox()
        sp_v.setRange(0.0, 100000.0)
        sp_v.setSingleStep(500.0)
        sp_v.setDecimals(0)
        sp_v.setValue(self._voll)
        sp_v.valueChanged.connect(self._on_voll_changed)
        wl.addWidget(sp_v)

        wl.addStretch()
        return w

    def _on_discount_changed(self, pct: float):
        self._discount_rate = float(pct) / 100.0
        self._rerender()

    def _on_voll_changed(self, voll: float):
        self._voll = float(voll)
        self._rerender()

    def _rerender(self):
        if self._last_args is None:
            return
        h5_path, years, kw = self._last_args
        try:
            self.update_chart(h5_path, years, **kw)
        except Exception:
            logger.exception("CashFlowChart re-render failed")

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    @staticmethod
    def _walk_selling_prices(grp, prefix=""):
        for k in grp:
            item = grp[k]
            if not isinstance(item, _GROUP_T):
                continue
            full = f"{prefix}/{k}" if prefix else k
            if "prices_weights" in item:
                yield full, dict(item.attrs)
            else:
                yield from CashFlowChart._walk_selling_prices(item, full)

    @staticmethod
    def _scalar_avg_cost(value) -> float:
        """Coerce gen_config cost fields (scalar, list, np array, string)
        into a single non-negative average."""
        if value is None:
            return 0.0
        if isinstance(value, (list, np.ndarray)):
            vals = [float(v) for v in value if float(v) > 0]
            return float(np.mean(vals)) if vals else 0.0
        if isinstance(value, str):
            try:
                parsed = [float(v) for v in value.strip("[]").split(",")
                          if float(v.strip()) > 0]
                return float(np.mean(parsed)) if parsed else 0.0
            except (ValueError, TypeError):
                return 0.0
        try:
            v = float(value)
            return v if v > 0 else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        yr_list: list[int] = []
        revenue: list[float] = []        # $
        fuel_cost: list[float] = []      # $
        capex: list[float] = []          # $
        co2_cost: list[float] = []       # $
        loss_load_cost: list[float] = [] # $

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
            t_configs = _load_tech_configs(h5f, bp)
            bt_configs = _load_bat_tech_configs(h5f, bp)
            _is_per_system = bp.startswith("systems/")
            global_t = (_load_tech_configs(h5f, "")
                        if _is_per_system else t_configs)
            global_bt = (_load_bat_tech_configs(h5f, "")
                         if _is_per_system else bt_configs)
            sys_filter = bp.split("/")[-1] if _is_per_system else None

            # Pre-compute (fuel, $/MWh fuel, $/ton co2) per generator
            gen_fuel_info: dict[str, tuple[float, float]] = {}
            for gc in gen_configs:
                name = gc.get("name", "")
                if isinstance(name, bytes):
                    name = name.decode()
                gen_fuel_info[name] = (
                    self._scalar_avg_cost(gc.get("fuel_cost")),
                    self._scalar_avg_cost(gc.get("co2_emission_cost")),
                )

            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                yr_list.append(int(year))

                # Revenue
                year_rev = 0.0
                if "technology_selling_prices" in sc:
                    for _name, attrs in self._walk_selling_prices(sc["technology_selling_prices"]):
                        year_rev += float(attrs.get("total_revenue", 0.0))
                revenue.append(year_rev)

                # Fuel cost & CO₂ cost from generation × per-gen rates
                gen_data = _load_gen_data(sc)
                year_fuel = 0.0
                year_co2_cost = 0.0
                # CO₂ tons summed across the scenario (used for cost
                # only if any generator carries a non-zero rate).
                co2_total_t = 0.0
                if "CO2_emissions" in sc:
                    a = sc["CO2_emissions"][:]
                    co2_total_t = float(np.sum(
                        _sum_nodes(a) if a.ndim >= 2 else a
                    )) * tres
                # Average $/ton across configs (weighted by their gen
                # share would be more accurate, but matches the
                # matplotlib chart's existing simplification).
                co2_rates = [r for (_f, r) in gen_fuel_info.values() if r > 0]
                avg_co2_rate = float(np.mean(co2_rates)) if co2_rates else 0.0
                year_co2_cost = co2_total_t * avg_co2_rate

                for gen_name, arr in gen_data.items():
                    total = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    gen_mwh = float(np.sum(total)) * tres
                    if gen_mwh <= 0:
                        continue
                    # Best-effort match against gen_configs by suffix
                    matched_fc = 0.0
                    for cfg_name, (fc, _co2) in gen_fuel_info.items():
                        suffix = cfg_name.split("/")[-1] if "/" in cfg_name else cfg_name
                        if cfg_name.endswith(gen_name) or gen_name.endswith(suffix):
                            matched_fc = fc
                            break
                    year_fuel += gen_mwh * matched_fc
                fuel_cost.append(year_fuel)
                co2_cost.append(year_co2_cost)

                # Loss of Load × VOLL
                year_loss_mwh = 0.0
                if "loss_load" in sc:
                    a = sc["loss_load"][:]
                    year_loss_mwh = float(np.sum(
                        _sum_nodes(a) if a.ndim >= 2 else a
                    )) * tres
                loss_load_cost.append(year_loss_mwh * self._voll)

                # Investment CapEx for this year (gen + battery techs
                # + legacy per-unit investments). Uses the same fallback
                # to global scenario as GenerationMixChart.
                fallback = None
                if _is_per_system and "detailed_results" in h5f and sc_key in h5f["detailed_results"]:
                    fallback = h5f["detailed_results"][sc_key]
                inv_data = _load_investment_data(
                    sc, tech_configs=global_t, bat_tech_configs=global_bt,
                    fallback_sc=fallback,
                )
                year_capex = 0.0
                for tech_name, inv_mw in inv_data.get("tech_investments", {}).items():
                    if inv_mw > 0 and (sys_filter is None or tech_name.startswith(sys_filter + "/")):
                        rate = inv_data.get("tech_costs", {}).get(tech_name, 0)
                        year_capex += float(inv_mw) * rate
                for bt_name, inv_mw in inv_data.get(
                    "bat_tech_power_investments", {},
                ).items():
                    if inv_mw > 0 and (sys_filter is None or bt_name.startswith(sys_filter + "/")):
                        rate = inv_data.get("bat_tech_costs", {}).get(bt_name, 0)
                        year_capex += float(inv_mw) * rate
                if "gen_investment_power" in inv_data:
                    for gi, inv_mw in enumerate(inv_data["gen_investment_power"]):
                        if inv_mw > 0 and gi < len(gen_configs):
                            gc = gen_configs[gi]
                            if "invest_cost" in gc:
                                year_capex += float(inv_mw) * _parse_invest_cost(gc["invest_cost"])
                if "bat_investment_power" in inv_data:
                    for bi, inv_mw in enumerate(inv_data["bat_investment_power"]):
                        if inv_mw > 0 and bi < len(bat_configs):
                            bc = bat_configs[bi]
                            if "invest_cost" in bc:
                                year_capex += float(inv_mw) * _parse_invest_cost(bc["invest_cost"])
                capex.append(year_capex)

        if not yr_list:
            return {"error": "No cash flow data"}

        # ── Aggregate into per-year M$ series ──
        n = len(yr_list)
        rev_m  = [v / 1e6 for v in revenue]
        fuel_m = [v / 1e6 for v in fuel_cost]
        co2_m  = [v / 1e6 for v in co2_cost]
        ll_m   = [v / 1e6 for v in loss_load_cost]
        cpx_m  = [v / 1e6 for v in capex]

        net_m = [
            rev_m[i] - fuel_m[i] - co2_m[i] - ll_m[i] - cpx_m[i]
            for i in range(n)
        ]
        cost_m = [fuel_m[i] + co2_m[i] + ll_m[i] + cpx_m[i] for i in range(n)]

        # Per-year running totals
        cumulative: list[float] = []
        cumulative_revenue: list[float] = []
        cumulative_cost: list[float] = []
        r_net = r_rev = r_cost = 0.0
        for i in range(n):
            r_net += net_m[i]
            r_rev += rev_m[i]
            r_cost += cost_m[i]
            cumulative.append(r_net)
            cumulative_revenue.append(r_rev)
            cumulative_cost.append(r_cost)
        # NPV: discount each year's net to year 0 then accumulate.
        # Index-based (0, 1, …) since solver scenarios are already at
        # the planning-period cadence.
        cumulative_npv: list[float] = []
        running_npv = 0.0
        for i, v in enumerate(net_m):
            running_npv += v / ((1.0 + self._discount_rate) ** i)
            cumulative_npv.append(running_npv)

        # Payback: first year (calendar) when undiscounted cumulative ≥ 0
        # AND the project actually had a CapEx outflow before then
        # (so a system that's profitable from year 0 doesn't report a
        # spurious "payback" at the first year).
        payback_year = None
        had_capex = False
        for i, v in enumerate(net_m):
            if cpx_m[i] > 1e-6:
                had_capex = True
            if had_capex and cumulative[i] >= 0:
                payback_year = str(yr_list[i])
                break

        # Components for the bar stack: order matters because Plotly's
        # `relative` mode stacks in array order. Outflows are emitted
        # with sign=-1 so the JS flips them to negative bars.
        components = [
            {"label": "Revenue",     "color": "#27AE60",
             "values_musd": rev_m,  "sign":  1},
            {"label": "Fuel cost",   "color": "#E67E22",
             "values_musd": fuel_m, "sign": -1},
            {"label": "CO₂ cost",    "color": "#7F8C8D",
             "values_musd": co2_m,  "sign": -1},
            {"label": "Loss of Load","color": "#C0392B",
             "values_musd": ll_m,   "sign": -1},
            {"label": "Investment",  "color": "#2980B9",
             "values_musd": cpx_m,  "sign": -1},
        ]

        return {
            "years": [str(y) for y in yr_list],
            "components": components,
            "net_musd": net_m,
            "cumulative_musd": cumulative,
            "cumulative_revenue_musd": cumulative_revenue,
            "cumulative_cost_musd": cumulative_cost,
            "cumulative_npv_musd": cumulative_npv,
            "discount_rate": float(self._discount_rate),
            "voll": float(self._voll),
            "payback_year": payback_year,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — System Metrics Evolution (interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _SystemMetricsBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class SystemMetricsEvolutionChart(QWidget):
    """Bird's-eye view of system-level KPIs across the planning horizon.

    One row per metric, one marker per scenario year normalised 0–100%
    on that metric's own min/max range. Marker fill encodes the year so
    a 25-year horizon reads as a colour gradient from blue (early) to
    red (late). Min / max absolute values are annotated at the row ends
    so the normalisation never hides the underlying magnitudes.

    Metrics are grouped into six categories (reliability, security,
    flexibility, adequacy, economics, environment) and drawn as faint
    horizontal bands so the eye can scan by topic.
    """

    TITLE = "System Metrics Evolution"
    TR_KEY = "results_charts.system_metrics"

    # Metric registry. Each entry: (label, unit, category, key) — the
    # ``key`` selects the extractor function below. Categories drive
    # the ordering AND the band colour.
    _METRICS = [
        # System Reliability
        ("Loss of Load Probability", "p.u.",     "System Reliability", "lolp"),
        ("Loss of Load Hours",       "h/yr",     "System Reliability", "lolh"),
        ("Loss of Load Frequency",   "events/yr","System Reliability", "lolf"),
        ("Energy Not Served",        "MWh/yr",   "System Reliability", "ens"),
        ("Peak Unserved Power",      "MW",       "System Reliability", "peak_uns"),
        ("Reserve Shortfall",        "MWh/yr",   "System Reliability", "rsv_shortfall"),
        ("Reserve Margin",           "%",        "System Reliability", "reserve_margin"),
        # Operational Security
        ("Dynamic Reserve Adequacy", "%",      "Operational Security", "res_adq_dyn"),
        ("Static Reserve Adequacy",  "%",      "Operational Security", "res_adq_sta"),
        # System Flexibility
        ("RE Curtailment",           "GWh/yr", "System Flexibility", "curtailment"),
        ("Net Load Variability",     "MW",     "System Flexibility", "nl_var"),
        ("Max Ramp Up Rate",         "MW/h",   "System Flexibility", "ramp_up"),
        ("Max Ramp Down Rate",       "MW/h",   "System Flexibility", "ramp_down"),
        ("Storage Utilization",      "%",      "System Flexibility", "storage_util"),
        ("V2G Flexibility Provision","GWh/yr", "System Flexibility", "v2g"),
        # Resource Adequacy
        ("Renewable Penetration",    "%",      "Resource Adequacy", "re_pen"),
        ("Total Installed Capacity", "MW",     "Resource Adequacy", "cap_total"),
        ("Storage Energy Capacity",  "MWh",    "Resource Adequacy", "storage_cap"),
        # Economic Performance
        ("System LCOE",              "$/MWh",  "Economic Performance", "lcoe_sys"),
        ("Annual Investment Cost",   "M$",     "Economic Performance", "inv_cost"),
        ("Annual Net Cash Flow",     "M$",     "Economic Performance", "net_cf"),
        # Environmental Impact
        ("CO₂ Emissions Rate",       "kg/MWh", "Environmental Impact", "co2_rate"),
        ("CO₂ Emissions Total",      "Mt/yr",  "Environmental Impact", "co2_total"),
    ]

    _CATEGORY_COLORS = {
        "System Reliability":     "rgba(231,76,60,0.07)",
        "Operational Security":   "rgba(241,196,15,0.07)",
        "System Flexibility":     "rgba(52,152,219,0.07)",
        "Resource Adequacy":      "rgba(46,204,113,0.07)",
        "Economic Performance":   "rgba(155,89,182,0.07)",
        "Environmental Impact":   "rgba(127,140,141,0.07)",
    }

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _SystemMetricsBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "system_metrics.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    # ── Per-scenario metric extractor ────────────────────────────

    @staticmethod
    def _flat(sc, key):
        """Return a node-summed timeseries or None if the key is absent."""
        if key not in sc:
            return None
        a = sc[key][:]
        return _sum_nodes(a) if a.ndim >= 2 else a

    def _extract_metrics(self, sc, sc_key, h5f, tres, gen_configs,
                         bat_configs, tech_configs, bat_tech_configs) -> dict:
        out: dict[str, float] = {}

        ll = self._flat(sc, "loss_load")
        dem = self._flat(sc, "demand")
        if ll is not None and len(ll) > 0:
            deficit = ll > 1e-6
            # LoLP: fraction of timesteps with any unserved energy
            out["lolp"] = float(np.sum(deficit)) / float(len(ll))
            out["ens"] = float(np.sum(ll)) * tres  # MWh/yr
            # LoLH: hours/yr with a shortfall (each step spans `tres` h)
            out["lolh"] = float(np.sum(deficit)) * tres
            # LoLF: number of distinct shortfall events — count rising
            # edges (no-deficit → deficit), plus one if the year opens
            # already in deficit.
            di = deficit.astype(np.int8)
            edges = int(np.sum(np.diff(di) == 1))
            out["lolf"] = float(edges + (1 if di[0] == 1 else 0))
            # Peak unserved power: worst instantaneous system shortfall.
            out["peak_uns"] = float(np.max(ll))
        if dem is not None and len(dem) > 0:
            peak_dem = float(np.max(dem))
            # Reserve margin = (installed capacity − peak demand) / peak demand
            installed_mw = 0.0
            for gc in gen_configs:
                rp = gc.get("rated_power", 0)
                try:
                    installed_mw += float(_parse_invest_cost(rp))
                except Exception:
                    pass
            if peak_dem > 0:
                out["reserve_margin"] = (installed_mw / peak_dem - 1.0) * 100.0
            out["cap_total"] = installed_mw

        rsv_shortfall_mwh = 0.0
        _saw_reserve = False
        for src, mkey in (("loss_of_reserve_dynamic", "res_adq_dyn"),
                          ("loss_of_reserve_static", "res_adq_sta")):
            r = self._flat(sc, src)
            if r is not None and len(r) > 0:
                _saw_reserve = True
                # Adequacy = % of timesteps WITHOUT a reserve violation
                out[mkey] = (1.0 - float(np.sum(r > 1e-6)) / float(len(r))) * 100.0
                # Accumulate the absolute unmet-reserve energy (MWh/yr)
                # across both dynamic and static reserve products.
                rsv_shortfall_mwh += float(np.sum(r)) * tres
        if _saw_reserve:
            out["rsv_shortfall"] = rsv_shortfall_mwh

        cur = self._flat(sc, "curtailment")
        if cur is not None:
            out["curtailment"] = float(np.sum(cur)) * tres / 1e3  # GWh

        # Net load = demand - (wind + solar generation)
        if dem is not None and len(dem) > 0:
            gen_data = _load_gen_data(sc)
            re_gen = np.zeros(len(dem), dtype=float)
            for name, arr in gen_data.items():
                nl = name.lower()
                if "wind" in nl or "solar" in nl or "fotovolt" in nl or "eólic" in nl:
                    t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                    ml = min(len(re_gen), len(t))
                    re_gen[:ml] += t[:ml]
            net_load = dem - re_gen
            out["nl_var"] = float(np.std(net_load))
            if len(net_load) > 1:
                ramp = np.diff(net_load) / tres  # MW per hour
                out["ramp_up"] = float(np.max(ramp))
                out["ramp_down"] = float(np.min(ramp))

        # Storage utilisation: how much of the rated capacity the battery
        # actually swings through over the year. (max−min)/max × 100 —
        # a battery that never cycles reads 0; one that fully cycles
        # between full and empty reads 100.
        bat_soc = _load_bat_data(sc, "battery_soc")
        if bat_soc:
            soc_total = None
            for arr in bat_soc.values():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                if soc_total is None:
                    soc_total = t.astype(float).copy()
                else:
                    ml = min(len(soc_total), len(t))
                    soc_total[:ml] += t[:ml]
            if soc_total is not None and len(soc_total) > 0:
                max_soc = float(np.max(soc_total))
                min_soc = float(np.min(soc_total))
                if max_soc > 0:
                    out["storage_util"] = (max_soc - min_soc) / max_soc * 100.0
                out["storage_cap"] = max_soc

        # V2G annual provision (GWh)
        ev = self._flat(sc, "EV_V2G")
        if ev is not None:
            out["v2g"] = float(np.sum(ev)) * tres / 1e3
        else:
            out["v2g"] = 0.0

        # Renewable penetration — prefer the summary value if exposed
        # in the scenario's attrs, otherwise compute from generation.
        re_attr = sc.attrs.get("renewable_penetration")
        if re_attr is not None:
            try:
                out["re_pen"] = float(re_attr) * 100.0
            except Exception:
                pass
        if "re_pen" not in out:
            gen_data = _load_gen_data(sc)
            re_g = 0.0
            tot_g = 0.0
            for name, arr in gen_data.items():
                t = _sum_nodes(arr) if arr.ndim >= 2 else arr
                v = float(np.sum(t)) * tres
                tot_g += v
                if _is_renewable(name):
                    re_g += v
            if tot_g > 0:
                out["re_pen"] = re_g / tot_g * 100.0

        # System LCOE: total_cost / total generation (rough proxy).
        total_cost = sc.attrs.get("total_cost")
        total_gen_mwh = 0.0
        gen_data = _load_gen_data(sc)
        for name, arr in gen_data.items():
            t = _sum_nodes(arr) if arr.ndim >= 2 else arr
            total_gen_mwh += float(np.sum(t)) * tres
        if total_cost is not None and total_gen_mwh > 0:
            try:
                out["lcoe_sys"] = float(total_cost) / total_gen_mwh
            except Exception:
                pass

        # Annual Investment Cost (M$). Needs tech_configs +
        # bat_tech_configs to resolve per-tech cost rates, plus the
        # global scenario fallback for per-system views (investment
        # attrs may live only in the global tree).
        fallback = None
        if "detailed_results" in h5f and sc_key in h5f["detailed_results"]:
            fallback = h5f["detailed_results"][sc_key]
        try:
            inv_data = _load_investment_data(
                sc, tech_configs=tech_configs,
                bat_tech_configs=bat_tech_configs,
                fallback_sc=fallback,
            )
            inv_cost = 0.0
            for nm, mw in inv_data.get("tech_investments", {}).items():
                if mw > 0:
                    inv_cost += float(mw) * inv_data.get("tech_costs", {}).get(nm, 0)
            for nm, mw in inv_data.get("bat_tech_power_investments", {}).items():
                if mw > 0:
                    inv_cost += float(mw) * inv_data.get("bat_tech_costs", {}).get(nm, 0)
            # Legacy per-generator investments (operational paths).
            if "gen_investment_power" in inv_data:
                for gi, mw in enumerate(inv_data["gen_investment_power"]):
                    if mw > 0 and gi < len(gen_configs):
                        gc = gen_configs[gi]
                        if "invest_cost" in gc:
                            inv_cost += float(mw) * _parse_invest_cost(gc["invest_cost"])
            if "bat_investment_power" in inv_data:
                for bi, mw in enumerate(inv_data["bat_investment_power"]):
                    if mw > 0 and bi < len(bat_configs):
                        bc = bat_configs[bi]
                        if "invest_cost" in bc:
                            inv_cost += float(mw) * _parse_invest_cost(bc["invest_cost"])
            out["inv_cost"] = inv_cost / 1e6
        except Exception:
            logger.exception("inv_cost extraction failed")
            out["inv_cost"] = 0.0

        # CO₂ totals + rate
        co2 = self._flat(sc, "CO2_emissions")
        if co2 is not None:
            co2_t = float(np.sum(co2)) * tres
            out["co2_total"] = co2_t / 1e6
            if total_gen_mwh > 0:
                # tons / MWh × 1000 g/kg / 1000 kg = kg/MWh
                out["co2_rate"] = co2_t / total_gen_mwh * 1000.0

        return out

    # ── Payload builder ──────────────────────────────────────────

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        per_year_metrics: dict[int, dict[str, float]] = {}

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            gen_configs = _load_gen_configs(h5f, bp)
            bat_configs = _load_bat_configs(h5f, bp)
            # For per-system views the investment attrs are written to
            # the *global* tree, so always use the global tech configs
            # for the cost resolution path.
            _is_per_system = bp.startswith("systems/")
            tech_configs = (
                _load_tech_configs(h5f, "") if _is_per_system
                else _load_tech_configs(h5f, bp)
            )
            bat_tech_configs = (
                _load_bat_tech_configs(h5f, "") if _is_per_system
                else _load_bat_tech_configs(h5f, bp)
            )
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                m = self._extract_metrics(
                    sc, sc_key, h5f, tres,
                    gen_configs, bat_configs,
                    tech_configs, bat_tech_configs,
                )
                per_year_metrics[int(year)] = m

            # Net cash flow per year proxy: revenue (selling prices) −
            # investment cost. We do this here since it spans the same
            # scenarios we just iterated.
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                rev = 0.0
                if "technology_selling_prices" in sc:
                    for _name, attrs in self._walk_selling(sc["technology_selling_prices"]):
                        rev += float(attrs.get("total_revenue", 0.0))
                inv = per_year_metrics.get(int(year), {}).get("inv_cost", 0.0) * 1e6
                per_year_metrics.setdefault(int(year), {})["net_cf"] = (rev - inv) / 1e6

        if not per_year_metrics:
            return {"error": "No data"}

        sorted_years = sorted(per_year_metrics.keys())
        ymin, ymax = sorted_years[0], sorted_years[-1]

        metrics_payload = []
        for label, unit, category, key in self._METRICS:
            raw_vals = [per_year_metrics.get(y, {}).get(key) for y in sorted_years]
            if all(v is None for v in raw_vals):
                continue
            # Keep Nones as null so the chart shows gaps (instead of
            # zero markers that would skew the min/max normalisation).
            cleaned = [None if v is None else float(v) for v in raw_vals]
            present = [v for v in cleaned if v is not None]
            mn, mx = float(min(present)), float(max(present))
            # Skip metrics that don't vary at all over the horizon —
            # min == max collapses every marker to a single point and
            # adds a noisy row without information value.
            if mx - mn < 1e-9:
                continue
            rng = mx - mn
            normalized = [
                (None if v is None else ((v - mn) / rng) * 100.0)
                for v in cleaned
            ]
            metrics_payload.append({
                "label": f"{label} ({unit})",
                "category": category,
                "values_str": [
                    ("n/a" if v is None else _fmt_val(v)) for v in cleaned
                ],
                "normalized": normalized,
                "min_str": _fmt_val(mn),
                "max_str": _fmt_val(mx),
            })

        return {
            "years": [int(y) for y in sorted_years],
            "years_min": int(ymin),
            "years_max": int(ymax),
            "categories": [
                {"name": cat, "color": col}
                for cat, col in self._CATEGORY_COLORS.items()
            ],
            "metrics": metrics_payload,
        }

    @staticmethod
    def _walk_selling(grp, prefix=""):
        for k in grp:
            item = grp[k]
            if not isinstance(item, _GROUP_T):
                continue
            full = f"{prefix}/{k}" if prefix else k
            if "prices_weights" in item:
                yield full, dict(item.attrs)
            else:
                yield from SystemMetricsEvolutionChart._walk_selling(item, full)


def _fmt_val(v: float) -> str:
    """Compact number formatter for the row min/max annotations."""
    av = abs(v)
    if av >= 1e6:
        return f"{v / 1e6:,.1f}M"
    if av >= 1e3:
        return f"{v / 1e3:,.1f}k"
    if av >= 10:
        return f"{v:,.1f}"
    if av >= 0.01:
        return f"{v:,.2f}"
    if av == 0:
        return "0"
    return f"{v:.2e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared helpers for the reliability / flexibility charts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sys_series(sc: h5py.Group, key: str, yh: int) -> Optional[np.ndarray]:
    """Return a one-year system-wide [steps] series for ``key``.

    Sums across the node axis (and generator axis for 3-D arrays) and
    trims to one year. When ``key`` is a *group* (per-battery / per-unit
    datasets, e.g. ``battery_discharge``), all member datasets are summed.
    Returns ``None`` if the dataset/group is absent or empty.
    """
    if key not in sc:
        return None
    obj = sc[key]

    def _flat(a: np.ndarray) -> np.ndarray:
        return _sum_nodes(a) if a.ndim >= 2 else np.asarray(a).reshape(-1)

    if isinstance(obj, _GROUP_T):
        parts: list[np.ndarray] = []
        obj.visititems(
            lambda _n, node: parts.append(_flat(node[:]))
            if isinstance(node, _DATASET_T) else None
        )
        if not parts:
            return None
        m = min(len(p) for p in parts)
        acc = np.zeros(m)
        for p in parts:
            acc += p[:m]
        return acc[:yh]

    return _flat(obj[:])[:yh]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Flexibility & Reliability (interactive Plotly, two subplots)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class _FlexReliabilityBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class FlexReliabilityChart(QWidget):
    """Flexibility requirement vs. reliability outcome, side by side.

      (a) Net-load ramp-duration curves, one line per year (signed
          net-load ramp in MW/h, sorted descending). Net load =
          demand − variable RE (wind + solar). Steeper/longer tails in
          later years signal a growing flexibility requirement.
      (b) Failure mode by year: stacked unserved-energy + dynamic/static
          reserve shortfalls (MWh, left axis) with a line for the number
          of hours under an inertia deficit (right axis). Empty bars
          confirm operational adequacy at a glance.
    """

    TITLE = "Flexibility & Reliability"
    TR_KEY = "results_charts.flex_reliability"

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _FlexReliabilityBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "flex_reliability.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    @staticmethod
    def _net_load(sc: h5py.Group, yh: int) -> Optional[np.ndarray]:
        """demand − (wind + solar utility generation), one-year [steps]."""
        if "demand" not in sc:
            return None
        dem = sc["demand"][:]
        demand = _sum_nodes(dem) if dem.ndim >= 2 else np.asarray(dem).reshape(-1)
        demand = demand[:yh]
        re = np.zeros(len(demand))
        for name, arr in _load_gen_data(sc).items():
            nl = name.lower()
            if "wind" in nl or "solar" in nl:
                t = _sum_nodes(arr) if arr.ndim >= 2 else np.asarray(arr).reshape(-1)
                t = t[:len(demand)]
                re[:len(t)] += t
        return demand - re

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        # In UC mode the year axis collapses to a single point; instead
        # the operator wants to see the hourly net-load ramp profile and
        # hourly stress events (load shed, reserve shortfalls, inertia
        # deficit) within the operational window. Planning runs keep
        # the multi-year duration curves + annual-bar layout.
        is_uc = False
        try:
            with _open_h5(h5_path) as _f:
                sm = _f.attrs.get("simulation_mode", "")
                if isinstance(sm, bytes):
                    sm = sm.decode()
                is_uc = str(sm).strip().lower() == "unit_commitment"
        except Exception:
            is_uc = False

        if is_uc:
            return self._build_payload_uc(h5_path, bp, **kw)
        return self._build_payload_planning(h5_path, bp)

    def _build_payload_planning(self, h5_path: Path, bp: str) -> dict:
        yr_list: list[int] = []
        ramp_curves: list[list[float]] = []
        ens_year: list[float] = []
        rsv_dyn: list[float] = []
        rsv_sta: list[float] = []
        inertia_hours: list[float] = []
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            yh = _year_hours(tres)
            for sc_key, year in _sorted_scenarios(h5f, bp):
                sc = _open_scenario(h5f, bp, sc_key)
                yr_list.append(int(year))
                net = self._net_load(sc, yh)
                if net is not None and len(net) >= 2:
                    net = np.nan_to_num(net, nan=0.0, posinf=0.0, neginf=0.0)
                    ramp = np.diff(net) / max(tres, 1)
                    ramp_curves.append(np.sort(ramp)[::-1].tolist())
                else:
                    ramp_curves.append([])
                ll = _sys_series(sc, "loss_load", yh)
                ens_year.append(float(np.nansum(ll) * tres) if ll is not None else 0.0)
                rd = _sys_series(sc, "loss_of_reserve_dynamic", yh)
                rsv_dyn.append(float(np.nansum(rd) * tres) if rd is not None else 0.0)
                rs = _sys_series(sc, "loss_of_reserve_static", yh)
                rsv_sta.append(float(np.nansum(rs) * tres) if rs is not None else 0.0)
                inert = _sys_series(sc, "loss_of_inertia", yh)
                if inert is not None:
                    inert = np.nan_to_num(inert, nan=0.0, posinf=0.0, neginf=0.0)
                    inertia_hours.append(float(np.sum(inert > 1e-9) * tres))
                else:
                    inertia_hours.append(0.0)
        if not yr_list:
            return {"error": "No data"}
        max_len = max((len(c) for c in ramp_curves if c), default=1)
        x_pct = (np.arange(max_len) / max(max_len - 1, 1) * 100.0).tolist()
        any_event = any(v > 0 for v in ens_year) or any(v > 0 for v in rsv_dyn) \
            or any(v > 0 for v in rsv_sta) or any(v > 0 for v in inertia_hours)
        return {
            "mode": "planning",
            "years": [str(y) for y in yr_list],
            "x_pct": x_pct,
            "ramp_curves_mw_h": ramp_curves,
            "ens_year_mwh": ens_year,
            "reserve_dynamic_mwh": rsv_dyn,
            "reserve_static_mwh": rsv_sta,
            "inertia_deficit_hours": inertia_hours,
            "any_event": bool(any_event),
        }

    def _build_payload_uc(self, h5_path: Path, bp: str, **kw) -> dict:
        """UC-specific layout: hourly net-load ramp time-series + hourly
        stress events (load shed, reserve shortfalls, inertia deficit).
        Uses the last year in ``year_range`` (snapshot semantics matching
        the rest of the UC chart set)."""
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            sel_idx = len(scenarios) - 1
            if year_range is not None:
                y_lo, y_hi = int(year_range[0]), int(year_range[1])
                for i in range(len(scenarios) - 1, -1, -1):
                    _, y = scenarios[i]
                    if y_lo <= y <= y_hi:
                        sel_idx = i
                        break
            sc_key, year = scenarios[sel_idx]
            sc = _open_scenario(h5f, bp, sc_key)
            # Net load + hourly ramp. Use the full operational horizon
            # rather than capping at one year — UC windows are short.
            if "demand" not in sc:
                return {"error": "demand missing"}
            dem = sc["demand"][:]
            demand = _sum_nodes(dem) if dem.ndim >= 2 else np.asarray(dem).reshape(-1)
            n_hours = int(demand.size)
            re = np.zeros(n_hours)
            for name, arr in _load_gen_data(sc).items():
                nl = name.lower()
                if "wind" in nl or "solar" in nl:
                    t = _sum_nodes(arr) if arr.ndim >= 2 else np.asarray(arr).reshape(-1)
                    re[:len(t)] += t[:n_hours]
            net = (demand - re).astype(float)
            ramp = np.diff(net, prepend=net[0]) / max(tres, 1)

            def _hourly(key: str) -> np.ndarray:
                arr = _sys_series(sc, key, n_hours)
                if arr is None:
                    return np.zeros(n_hours)
                arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
                if len(arr) < n_hours:
                    arr = np.pad(arr, (0, n_hours - len(arr)))
                return arr[:n_hours].astype(float)

            ls = _hourly("loss_load")
            rd = _hourly("loss_of_reserve_dynamic")
            rs = _hourly("loss_of_reserve_static")
            inertia = _hourly("loss_of_inertia")

        ramp_sorted = np.sort(ramp)[::-1]
        return {
            "mode": "uc",
            "year": int(year),
            "hours": list(range(n_hours)),
            "net_load_mw": [float(v) for v in net],
            "ramp_mw_h": [float(v) for v in ramp],
            "ramp_sorted_mw_h": [float(v) for v in ramp_sorted],
            "x_pct": [float(v) for v in
                      (np.arange(n_hours) / max(n_hours - 1, 1) * 100.0)],
            # Hourly stress events (MW). Cumulative MWh on the side
            # gives totals consistent with the planning bar chart.
            "loss_load_mw": [float(v) for v in ls],
            "loss_reserve_dynamic_mw": [float(v) for v in rd],
            "loss_reserve_static_mw": [float(v) for v in rs],
            "loss_inertia": [float(v) for v in inertia],
            "totals_mwh": {
                "loss_load": float(ls.sum() * tres),
                "loss_reserve_dynamic": float(rd.sum() * tres),
                "loss_reserve_static": float(rs.sum() * tres),
                "loss_inertia_hours": float((inertia > 1e-9).sum() * tres),
            },
            "any_event": bool(
                ls.sum() > 0 or rd.sum() > 0 or rs.sum() > 0
                or (inertia > 1e-9).any()
            ),
        }



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chart — Custom (user-built, interactive Plotly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# System-level series the Custom chart can plot. Tuple = (hdf5 key,
# label, unit, node-aggregation). Entries are offered only when the key
# is present in the loaded results.
_CUSTOM_STD_VARS = [
    ("demand", "Demand", "MW", "sum"),
    ("curtailment", "Curtailment", "MW", "sum"),
    ("electricity_prices", "Electricity price (avg)", "$/MWh", "sum"),
    ("electricity_prices_energy", "Price — energy component", "$/MWh", "sum"),
    ("loss_load", "Loss of load", "MW", "sum"),
    ("reserve_static", "Reserve (static)", "MW", "sum"),
    ("reserve_dynamic", "Reserve (dynamic)", "MW", "sum"),
    ("loss_of_reserve_static", "Reserve shortfall (static)", "MW", "sum"),
    ("loss_of_reserve_dynamic", "Reserve shortfall (dynamic)", "MW", "sum"),
    ("CO2_emissions", "CO₂ emissions", "tCO₂", "sum"),
    ("voltage_angle", "Voltage angle (mean)", "rad", "mean"),
    ("loss_of_inertia", "Inertia deficit", "GW·s", "sum"),
    ("EV_charging", "EV charging", "MW", "sum"),
    ("EV_V2G", "EV V2G", "MW", "sum"),
    ("EV_soc", "EV fleet SOC", "MWh", "sum"),
    ("EV_loss", "EV unmet charging", "MW", "sum"),
    ("battery_charge", "Battery charge", "MW", "sum"),
    ("battery_discharge", "Battery discharge", "MW", "sum"),
    ("battery_soc", "Battery SOC", "MWh", "sum"),
    ("rooftop_generation", "Rooftop solar", "MW", "sum"),
]
_CUSTOM_AGG_OF = {k: agg for k, _l, _u, agg in _CUSTOM_STD_VARS}


def _custom_catalog(sc: h5py.Group) -> list[dict]:
    """Discover the system-level series available in scenario ``sc``.

    Returns ordered ``[{id, label, unit}]``. Pure-Python discovery so the
    catalogue adapts to whatever the run actually wrote.
    """
    cat: list[dict] = []
    for key, label, unit, _agg in _CUSTOM_STD_VARS:
        if key in sc:
            cat.append({"id": key, "label": label, "unit": unit})
    if "generation" in sc:
        cat.append({"id": "__total_gen__", "label": "Total generation",
                    "unit": "MW"})
    if "demand" in sc and "generation" in sc:
        cat.append({"id": "__net_load__", "label": "Net load (demand − VRE)",
                    "unit": "MW"})
    return cat


def _custom_extract(sc: h5py.Group, vid: str, yh: int) -> Optional[np.ndarray]:
    """Return the one-year [steps] series for catalogue id ``vid``."""
    if vid == "__net_load__":
        if "demand" not in sc:
            return None
        dem = sc["demand"][:]
        demand = _sum_nodes(dem) if dem.ndim >= 2 else np.asarray(dem).reshape(-1)
        demand = demand[:yh]
        re = np.zeros(len(demand))
        for name, arr in _load_gen_data(sc).items():
            nl = name.lower()
            if "wind" in nl or "solar" in nl:
                t = _sum_nodes(arr) if arr.ndim >= 2 else np.asarray(arr).reshape(-1)
                re[:min(len(t), len(re))] += t[:len(re)]
        return demand - re
    if vid == "__total_gen__":
        tot = None
        for _name, arr in _load_gen_data(sc).items():
            t = _sum_nodes(arr) if arr.ndim >= 2 else np.asarray(arr).reshape(-1)
            tot = t.copy() if tot is None else tot[:len(t)] + t[:len(tot)]
        return None if tot is None else tot[:yh]
    if vid.startswith("gen::"):
        name = vid[len("gen::"):]
        arr = _load_gen_data(sc).get(name)
        if arr is None:
            return None
        t = _sum_nodes(arr) if arr.ndim >= 2 else np.asarray(arr).reshape(-1)
        return t[:yh]
    # Direct dataset/group.
    if vid not in sc:
        return None
    if _CUSTOM_AGG_OF.get(vid) == "mean":
        a = sc[vid][:]
        flat = a.mean(axis=0) if a.ndim >= 2 else np.asarray(a).reshape(-1)
        return flat[:yh]
    return _sys_series(sc, vid, yh)


def _reduce(arr: np.ndarray, stat: str) -> float:
    """Reduce an array to a scalar using ``stat`` (mean/sum/max/min)."""
    if arr.size == 0:
        return 0.0
    fn = {"sum": np.nansum, "max": np.nanmax, "min": np.nanmin}.get(stat, np.nanmean)
    return float(fn(arr))


def _custom_aggregate(arr: np.ndarray, agg: str, tres: int,
                      stat: str = "mean") -> np.ndarray:
    """Aggregate a [steps] series to raw / daily / monthly using ``stat``
    (mean / sum / max / min) within each time bucket."""
    if agg == "raw" or arr.size == 0:
        return arr
    if agg == "daily":
        chunk = max(1, 24 // tres)
    else:  # monthly
        chunk = max(1, 730 // tres)
    n = (len(arr) // chunk) * chunk
    if n == 0:
        return arr
    g = arr[:n].reshape(-1, chunk)
    fn = {"sum": np.nansum, "max": np.nanmax, "min": np.nanmin}.get(stat, np.nanmean)
    return fn(g, axis=1)


def _custom_transform(y: np.ndarray, kind: str) -> tuple[np.ndarray, str]:
    """Apply a per-series transform. Returns (values, unit_suffix)."""
    if y.size == 0 or kind in (None, "", "none"):
        return y, ""
    if kind == "cumulative":
        return np.nancumsum(y), " (cum.)"
    if kind == "rolling":
        w = max(2, int(round(len(y) * 0.05)))
        kernel = np.ones(w) / w
        return np.convolve(np.nan_to_num(y), kernel, mode="same"), " (roll.)"
    if kind == "normalize":
        lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
        rng = hi - lo
        return ((y - lo) / rng * 100.0 if rng > 0 else np.zeros_like(y)), "%"
    if kind == "derivative":
        d = np.diff(y, prepend=y[:1])
        return d, " (Δ)"
    return y, ""


class _ProportionalTable(QTableWidget):
    """Table whose columns each keep a fixed *fraction* of the viewport
    width, so every column scales proportionally as the window resizes
    (Qt's Stretch mode would only grow a subset)."""

    def __init__(self, weights: list[float], parent=None):
        super().__init__(0, len(weights), parent)
        self._weights = list(weights)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.apply_widths()

    def apply_widths(self):
        vp = self.viewport().width()
        total = sum(self._weights)
        if vp <= 0 or total <= 0:
            return
        acc = 0
        last = self.columnCount() - 1
        for c in range(last):
            w = max(1, int(round(vp * self._weights[c] / total)))
            self.setColumnWidth(c, w)
            acc += w
        # Last column takes the rounding remainder so widths sum to vp.
        self.setColumnWidth(last, max(1, vp - acc))
        # Re-lay-out so existing cell widgets resize to the new column
        # widths (the view doesn't auto-resize index widgets on a
        # programmatic column-width change).
        self.doItemsLayout()


class _CustomConfigWidget(QWidget):
    """Configuration toolbar for the Custom chart: X source, aggregation,
    year multi-select, bar mode, per-axis log/range controls, and an
    editable per-variable table. Calls ``on_change`` on any edit."""

    # Relative column widths (Variable/Name widest); applied as a fraction
    # of the table width so all columns scale with the window.
    _COL_WEIGHTS = [
        2.6,   # Variable
        1.8,   # Name
        1.15,  # Aggregate
        0.9,   # Stat
        1.4,   # Transform
        1.1,   # Type
        0.65,  # Axis
        1.05,  # Axis Scale
        1.45,  # Line
        0.75,  # Color
        0.75,  # Vis
        0.45,  # ✕
    ]

    _TYPES = [("line", "Line"), ("bar", "Bar"),
              ("scatter", "Scatter"), ("area", "Area")]
    _STATS = [("mean", "Mean"), ("sum", "Sum"), ("max", "Max"), ("min", "Min")]
    _TRANSFORMS = [("none", "None"), ("cumulative", "Cumulative"),
                   ("rolling", "Rolling"), ("normalize", "Normalize"),
                   ("derivative", "Derivative")]
    _DASHES = [("solid", "──"), ("dash", "- -"), ("dot", "···"),
               ("dashdot", "-·-")]
    _AGGS = [("raw", "Raw"), ("daily", "Daily"), ("monthly", "Monthly")]
    _SCALES = [("linear", "Linear"), ("log", "Log")]

    # Column indices.
    (C_VAR, C_NAME, C_AGG, C_STAT, C_TRANS, C_TYPE, C_AXIS, C_SCALE,
     C_LINE, C_COLOR, C_VIS, C_RM) = range(12)

    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change
        self._catalog: list[dict] = []
        self._suppress = True   # until first populate

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # ── Row 1: X / years / bar mode / per-axis range / add ──
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("custom_chart.x_axis")))
        self.x_combo = QComboBox()
        self.x_combo.addItem(tr("custom_chart.time"), "__time__")
        self.x_combo.currentIndexChanged.connect(self._changed)
        top.addWidget(self.x_combo)

        top.addStretch()
        self.add_btn = QPushButton(tr("custom_chart.add_variable"))
        self.add_btn.clicked.connect(lambda: self._add_row(None))
        top.addWidget(self.add_btn)
        outer.addLayout(top)

        # ── Per-variable table ──
        self.table = _ProportionalTable(self._COL_WEIGHTS)
        self.table.setHorizontalHeaderLabels([
            tr("custom_chart.variable"), tr("custom_chart.name"),
            tr("custom_chart.aggregate"), tr("custom_chart.stat"),
            tr("custom_chart.transform"), tr("custom_chart.type"),
            tr("custom_chart.axis"), tr("custom_chart.axis_scale"),
            tr("custom_chart.line"), tr("custom_chart.color"),
            tr("custom_chart.visible"), tr("custom_chart.remove"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setMaximumHeight(170)
        # Fill the panel width and grow/shrink with the window.
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum,
        )
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(20)
        # All columns Fixed; _ProportionalTable.resizeEvent gives each a
        # fraction of the width, so they all scale together with the window.
        for c in range(self.table.columnCount()):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
        outer.addWidget(self.table, 1)

    # -- population (after data loads) --
    def populate(self, catalog: list[dict], years: list[int]):
        self._suppress = True
        self._catalog = catalog

        prev_x = self.x_combo.currentData()
        self.x_combo.blockSignals(True)
        self.x_combo.clear()
        self.x_combo.addItem(tr("custom_chart.time"), "__time__")
        for v in catalog:
            self.x_combo.addItem(v["label"], v["id"])
        ix = self.x_combo.findData(prev_x)
        self.x_combo.setCurrentIndex(ix if ix >= 0 else 0)
        self.x_combo.blockSignals(False)

        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, self.C_VAR)
            if isinstance(cb, QComboBox):
                self._fill_var_combo(cb, cb.currentData())
        if self.table.rowCount() == 0 and catalog:
            self._add_row(catalog[0]["id"])

        self._suppress = False

    def _fill_var_combo(self, cb: QComboBox, keep_id):
        cb.blockSignals(True)
        cb.clear()
        for v in self._catalog:
            cb.addItem(v["label"], v["id"])
        ix = cb.findData(keep_id)
        cb.setCurrentIndex(ix if ix >= 0 else 0)
        cb.blockSignals(False)

    def _mk_combo(self, items):
        cb = QComboBox()
        for d, label in items:
            cb.addItem(label, d)
        cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cb.setMinimumWidth(0)
        cb.currentIndexChanged.connect(self._changed)
        return cb

    def _add_row(self, var_id):
        r = self.table.rowCount()
        self.table.insertRow(r)

        var_cb = QComboBox()
        var_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        var_cb.setMinimumWidth(0)
        self._fill_var_combo(var_cb, var_id)
        var_cb.currentIndexChanged.connect(self._changed)
        self.table.setCellWidget(r, self.C_VAR, var_cb)

        name_ed = QLineEdit()
        name_ed.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name_ed.setMinimumWidth(0)
        name_ed.setPlaceholderText(tr("custom_chart.auto"))
        name_ed.editingFinished.connect(self._changed)
        self.table.setCellWidget(r, self.C_NAME, name_ed)

        self.table.setCellWidget(r, self.C_AGG, self._mk_combo(self._AGGS))
        self.table.setCellWidget(r, self.C_STAT, self._mk_combo(self._STATS))
        self.table.setCellWidget(r, self.C_TRANS, self._mk_combo(self._TRANSFORMS))
        self.table.setCellWidget(r, self.C_TYPE, self._mk_combo(self._TYPES))

        axis_cb = QComboBox()
        axis_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        axis_cb.setMinimumWidth(0)
        for a in (1, 2, 3, 4):
            axis_cb.addItem(str(a), a)
        axis_cb.currentIndexChanged.connect(self._changed)
        self.table.setCellWidget(r, self.C_AXIS, axis_cb)

        self.table.setCellWidget(r, self.C_SCALE, self._mk_combo(self._SCALES))

        # Line style (dash) + width in one compact cell.
        line_w = QWidget()
        lh = QHBoxLayout(line_w)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(2)
        dash_cb = self._mk_combo(self._DASHES)
        dash_cb.setObjectName("dash")
        width_sp = QDoubleSpinBox()
        width_sp.setRange(0.5, 8.0)
        width_sp.setSingleStep(0.5)
        width_sp.setValue(1.8)
        width_sp.setFixedWidth(52)
        width_sp.valueChanged.connect(self._changed)
        width_sp.setObjectName("width")
        lh.addWidget(dash_cb)
        lh.addWidget(width_sp)
        self.table.setCellWidget(r, self.C_LINE, line_w)

        color_btn = QPushButton(tr("custom_chart.auto"))
        color_btn.setProperty("colorHex", "")
        color_btn.clicked.connect(lambda _=False, b=color_btn: self._pick_color(b))
        self.table.setCellWidget(r, self.C_COLOR, color_btn)

        vis_w = QWidget()
        vh = QHBoxLayout(vis_w)
        vh.setContentsMargins(0, 0, 0, 0)
        vh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vis_cb = QCheckBox()
        vis_cb.setChecked(True)
        vis_cb.toggled.connect(self._changed)
        vh.addWidget(vis_cb)
        self.table.setCellWidget(r, self.C_VIS, vis_w)

        rm = QPushButton("✕")
        rm.clicked.connect(lambda _=False, w=var_cb: self._remove_row(w))
        self.table.setCellWidget(r, self.C_RM, rm)

        # Size the freshly added row's widgets to the proportional widths.
        self.table.apply_widths()

        if not self._suppress:
            self._changed()

    def _pick_color(self, btn: QPushButton):
        cur = btn.property("colorHex") or "#3498db"
        col = QColorDialog.getColor(QColor(cur), self, tr("custom_chart.color"))
        if not col.isValid():
            return
        hexv = col.name()
        btn.setProperty("colorHex", hexv)
        btn.setText("")
        btn.setStyleSheet(
            "QPushButton {"
            f" background-color: {hexv}; border: 1px solid #888;"
            " border-radius: 2px; min-width: 36px; }"
        )
        self._changed()

    def _remove_row(self, marker_widget):
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, self.C_VAR) is marker_widget:
                self.table.removeRow(r)
                break
        self._changed()

    def _changed(self, *_):
        if not self._suppress:
            self._on_change()

    def get_config(self) -> dict:
        rows = []
        for r in range(self.table.rowCount()):
            vcb = self.table.cellWidget(r, self.C_VAR)
            if vcb is None:
                continue
            name_ed = self.table.cellWidget(r, self.C_NAME)
            agg_cb = self.table.cellWidget(r, self.C_AGG)
            stat_cb = self.table.cellWidget(r, self.C_STAT)
            trans_cb = self.table.cellWidget(r, self.C_TRANS)
            type_cb = self.table.cellWidget(r, self.C_TYPE)
            axis_cb = self.table.cellWidget(r, self.C_AXIS)
            scale_cb = self.table.cellWidget(r, self.C_SCALE)
            line_w = self.table.cellWidget(r, self.C_LINE)
            color_btn = self.table.cellWidget(r, self.C_COLOR)
            vis_w = self.table.cellWidget(r, self.C_VIS)
            dash_cb = line_w.findChild(QComboBox, "dash") if line_w else None
            width_sp = line_w.findChild(QDoubleSpinBox, "width") if line_w else None
            vis_cb = vis_w.findChild(QCheckBox) if vis_w else None
            rows.append({
                "var": vcb.currentData(),
                "name": (name_ed.text().strip() if name_ed else ""),
                "agg": agg_cb.currentData() if agg_cb else "raw",
                "stat": stat_cb.currentData() if stat_cb else "mean",
                "transform": trans_cb.currentData() if trans_cb else "none",
                "type": type_cb.currentData() if type_cb else "line",
                "axis": int(axis_cb.currentData()) if axis_cb else 1,
                "scale": scale_cb.currentData() if scale_cb else "linear",
                "line_dash": dash_cb.currentData() if dash_cb else "solid",
                "line_width": float(width_sp.value()) if width_sp else 1.8,
                "color": (color_btn.property("colorHex") if color_btn else "") or "",
                "visible": vis_cb.isChecked() if vis_cb else True,
            })

        return {
            "x": self.x_combo.currentData(),
            "rows": rows,
        }


class _CustomBridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("get_data serialization failed")
            return json.dumps({"error": str(e)})


class CustomChart(QWidget):
    """User-built chart: pick any system-level variables, assign each to
    one of four Y axes, choose its representation (line/bar/scatter/area),
    aggregate over time, overlay multiple years, and optionally plot one
    variable against another on the X axis."""

    TITLE = "Custom"
    TR_KEY = "results_charts.custom"

    def __init__(self):
        super().__init__()
        self._cfg = _CustomConfigWidget(self._rerender)
        self._catalog_ready = False
        self._last_args: Optional[tuple] = None

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _CustomBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)

        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)

        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "custom_chart.html")
        ))

        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return self._cfg

    def _rerender(self):
        if self._last_args is not None:
            h5_path, years, kw = self._last_args
            try:
                self._push(h5_path, years, **kw)
            except Exception:
                logger.exception("CustomChart re-render failed")

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        self._last_args = (h5_path, list(years), dict(kw))
        self._ensure_catalog(h5_path, **kw)
        self._push(h5_path, years, **kw)

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _ensure_catalog(self, h5_path: Path, **kw):
        if self._catalog_ready:
            return
        bp = kw.get("base_prefix", "")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return
            sc = _open_scenario(h5f, bp, scenarios[0][0])
            catalog = _custom_catalog(sc)
            all_years = [y for _k, y in scenarios]
        self._cfg.populate(catalog, all_years)
        self._catalog_ready = True

    def _push(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        cfg = self._cfg.get_config()
        rows = cfg.get("rows", [])
        if not rows:
            return {"error": "Add at least one variable"}

        x_id = cfg.get("x", "__time__")
        x_is_time = (x_id == "__time__")

        cat_by_id: dict[str, dict] = {}
        series_out: list[dict] = []
        axis_log: dict[int, bool] = {}

        def _axis_of(row):
            a = int(row.get("axis", 1))
            return a if a in (1, 2, 3, 4) else 1

        # Hours-per-step for the continuous timeline X axis, per resolution.
        _step_hours = {"daily": 24, "monthly": 730}

        with _open_h5(h5_path) as h5f:
            tres = _get_temporal_res(h5f)
            yh = _year_hours(tres)
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            cat_by_id = {v["id"]: v for v in
                         _custom_catalog(_open_scenario(h5f, bp, scenarios[0][0]))}
            # Every year is concatenated into ONE continuous series per
            # variable, spanning the full results horizon.
            chosen = list(scenarios)
            scs = [_open_scenario(h5f, bp, k) for k, _y in chosen]
            first_year = int(chosen[0][1])

            for vi, row in enumerate(rows):
                vid = row.get("var")
                if not vid:
                    continue
                agg_v = row.get("agg", "raw")
                stat = row.get("stat", "mean")

                # Concatenate each year's aggregated chunk end to end.
                y_parts = []
                for sc in scs:
                    yr = _custom_extract(sc, vid, yh)
                    if yr is None:
                        continue
                    y_parts.append(_custom_aggregate(np.nan_to_num(yr), agg_v, tres, stat))
                if not y_parts:
                    continue
                yfull = np.concatenate(y_parts)
                yfull, _sfx = _custom_transform(yfull, row.get("transform", "none"))

                if x_is_time:
                    # Express the continuous timeline in calendar years so
                    # the axis reads 2025, 2026, … instead of raw hours.
                    step = _step_hours.get(agg_v, tres)
                    xs = [first_year + (i * step) / HOURS_STD_YEAR
                          for i in range(len(yfull))]
                else:
                    x_parts = []
                    for sc in scs:
                        xr = _custom_extract(sc, x_id, yh)
                        if xr is None:
                            continue
                        x_parts.append(_custom_aggregate(np.nan_to_num(xr), agg_v, tres))
                    if not x_parts:
                        continue
                    xfull = np.concatenate(x_parts)
                    m = min(len(xfull), len(yfull))
                    xs = [float(v) for v in xfull[:m]]
                    yfull = yfull[:m]

                axis = _axis_of(row)
                if row.get("scale") == "log":
                    axis_log[axis] = True
                base_unit = cat_by_id.get(vid, {}).get("unit", "")
                transform = row.get("transform", "none")
                unit = "%" if transform == "normalize" else base_unit
                label = cat_by_id.get(vid, {}).get("label", vid)
                custom = (row.get("name") or "").strip()
                series_out.append({
                    "name": custom if custom else label,
                    "type": row.get("type", "line"),
                    "axis": axis,
                    "x": xs,
                    "y": [float(v) for v in yfull],
                    "color": row.get("color", ""),
                    "line_dash": row.get("line_dash", "solid"),
                    "line_width": float(row.get("line_width", 1.8)),
                    "visible": bool(row.get("visible", True)),
                    "label": label, "unit": unit,
                    "var_index": vi, "year_index": 0, "n_years": 1,
                })

        if not series_out:
            return {"error": "No data for the selected variables/years"}

        if x_is_time:
            x_title = "Year"
        else:
            xc = cat_by_id.get(x_id, {})
            x_title = f"{xc.get('label', x_id)} ({xc.get('unit', '')})"

        # Axis titles carry the variable name(s) and their unit, e.g.
        # "Demand (MW)" or "Demand, Net load (MW)".
        axes_meta: dict[int, dict] = {}
        for s in series_out:
            meta = axes_meta.setdefault(s["axis"], {"labels": [], "units": set()})
            if s["label"] not in meta["labels"]:
                meta["labels"].append(s["label"])
            if s.get("unit"):
                meta["units"].add(s["unit"])
        axes = {}
        for ax, meta in axes_meta.items():
            lbls = ", ".join(meta["labels"][:3])
            if len(meta["labels"]) > 3:
                lbls += "…"
            u = " / ".join(sorted(meta["units"]))
            entry = {"title": f"{lbls} ({u})" if u else lbls}
            if axis_log.get(ax):
                entry["log"] = True
            axes[str(ax)] = entry

        return {
            "x_is_time": x_is_time,
            "x_title": x_title,
            "axes": axes,
            "series": series_out,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UC (Unit Commitment) charts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Only meaningful for runs where ``simulation_mode == "unit_commitment"``.
# They surface what UC actually decides (binary commitment, hourly
# dispatch, locational marginal prices) at the timestep granularity
# planning charts collapse away. Gated by ``mode_unit_commitment``
# capability so they're hidden in development / economic_dispatch runs.


class _UCChartBridge(QObject):
    """Shared bridge for UC charts. Each chart instantiates its own,
    but the slot contract is the same: ``get_data()`` returns the
    chart's JSON payload (set by Python via ``set_payload``)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: Optional[dict] = None

    def set_payload(self, payload: Optional[dict]):
        self._payload = payload

    @Slot(result=str)
    def get_data(self) -> str:
        import json
        if self._payload is None:
            return json.dumps({"error": "No data loaded"})
        try:
            return json.dumps(self._payload)
        except Exception as e:
            logger.exception("UC chart payload serialization failed")
            return json.dumps({"error": str(e)})


def _uc_select_year_idx(h5f, bp: str, year_range) -> int:
    """Pick a year scenario index that lives inside ``year_range``.

    UC charts are single-year by nature (hourly granularity over a year
    is already a lot of data); when the user has the global range
    slider open to multiple years we render the LAST year in the range
    so the snapshot matches the dashboard's last-year semantics.
    """
    scenarios = list(_sorted_scenarios(h5f, bp))
    if not scenarios:
        return -1
    if year_range is None:
        return len(scenarios) - 1
    y_lo, y_hi = int(year_range[0]), int(year_range[1])
    # Walk backwards so we pick the latest year in range.
    for i in range(len(scenarios) - 1, -1, -1):
        _, y = scenarios[i]
        if y_lo <= y <= y_hi:
            return i
    return len(scenarios) - 1


class UCHourlyPriceChart(QWidget):
    """Hourly locational marginal price for a single year.

    Shows the system-average LMP as a line with the per-node spread
    as a translucent band underneath. Wide band = transmission limit
    is binding (LMPs diverge between nodes); narrow band = uniform
    system price. Source: ``detailed_results/year_X/electricity_prices``
    + ``nodal_electricity_prices`` (the latter populated by Ruta A's
    UC dual recovery; zero for pre-recovery legacy HDF5 files).
    """

    TITLE = "Hourly Price Profile"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "uc_hourly_price.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            if idx < 0:
                return {"error": "No scenarios"}
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "electricity_prices" not in sc:
                return {"error": "electricity_prices missing — run UC with dual recovery"}
            sys_avg = sc["electricity_prices"][:]
            hours = list(range(int(len(sys_avg))))
            node_min = node_max = None
            if "nodal_electricity_prices" in sc:
                nodal = sc["nodal_electricity_prices"][:]
                if nodal.ndim == 2 and nodal.shape[1] == len(sys_avg):
                    node_min = [float(v) for v in nodal.min(axis=0)]
                    node_max = [float(v) for v in nodal.max(axis=0)]
        return {
            "year": int(year),
            "hours": hours,
            "system_avg": [float(v) for v in sys_avg],
            "node_min": node_min,
            "node_max": node_max,
        }


class UCCommitmentHeatmapChart(QWidget):
    """Binary commitment heatmap: units × hours, on/off per cell.

    The signature visualization of a UC run. Filters to committable
    units (gen_status present + at least one transition or some hours
    off) — pure renewables would be flat 1s and add noise. Units are
    labelled ``"<tech> @ node_X"`` so the user can see which physical
    asset is committing where.
    """

    TITLE = "Commitment Heatmap"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "uc_commitment_heatmap.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "gen_status" not in sc:
                return {"error": "gen_status missing — not a UC run"}
            status_grp = sc["gen_status"]
            # Each entry is [nodes, hours]. Flatten to per-node rows but
            # only keep rows that actually commit (mix of 0s and 1s) so
            # the heatmap shows decisions, not always-on RE.
            rng = _system_node_range(h5f, bp)
            units: list[str] = []
            rows: list[list[int]] = []
            n_hours = 0
            for gen_name in sorted(status_grp.keys()):
                arr = status_grp[gen_name][:]
                if arr.ndim < 2:
                    continue
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                    node_offset = lo
                else:
                    node_offset = 0
                if n_hours == 0:
                    n_hours = arr.shape[1]
                for n_i in range(arr.shape[0]):
                    row = arr[n_i, :]
                    if not (row.max() > 0.5 and row.min() < 0.5):
                        # Always-on or always-off — not interesting
                        # for the commitment heatmap.
                        continue
                    units.append(f"{gen_name} @ n{node_offset + n_i}")
                    rows.append([int(round(float(v))) for v in row])
        return {
            "year": int(year),
            "hours": list(range(n_hours)),
            "units": units,
            "status": rows,
        }


class UCDispatchStackChart(QWidget):
    """Hourly stacked dispatch: which tech serves load every hour.

    Stack order: renewables at the bottom, thermal in the middle,
    battery discharge / load shedding on top of the positive side;
    battery charge and curtailment on the negative side. Demand
    overlays as a black line — the positive stack must equal demand
    when there's no load shedding.
    """

    TITLE = "Hourly Dispatch"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(str(resources_dir / "uc_dispatch_stack.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "generation" not in sc:
                return {"error": "generation missing"}
            rng = _system_node_range(h5f, bp)

            # Build fuel/tech maps the same way the dashboard mix does.
            gen_configs = _load_gen_configs(h5f, bp)
            gen_data = _load_gen_data(sc)
            # Iterate generators in insertion order; positional index
            # into gen_configs gives the config dict with name/fuel.
            tech_series: dict[str, np.ndarray] = {}
            n_hours = 0
            for gi, (gen_name, arr) in enumerate(gen_data.items()):
                if arr.ndim < 2:
                    continue
                if rng is not None:
                    lo, hi = rng
                    sliced = arr[lo:hi]
                else:
                    sliced = arr
                hourly = sliced.sum(axis=0)
                if n_hours == 0:
                    n_hours = int(hourly.shape[0])
                cfg = gen_configs[gi] if gi < len(gen_configs) else {}
                tech = _tech_bucket_for_gen(cfg, gen_name)
                if tech in tech_series:
                    tech_series[tech] = tech_series[tech] + hourly
                else:
                    tech_series[tech] = hourly.copy()

            # Battery aggregated
            def _agg_bat(key: str) -> np.ndarray:
                grp = _load_bat_data(sc, key)
                if not grp:
                    return np.zeros(n_hours)
                total = np.zeros(n_hours)
                for arr in grp.values():
                    if arr.ndim < 2:
                        continue
                    if rng is not None:
                        lo, hi = rng
                        arr = arr[lo:hi]
                    s = arr.sum(axis=0)
                    if len(s) < n_hours:
                        s = np.pad(s, (0, n_hours - len(s)))
                    total += s[:n_hours]
                return total
            bat_charge = _agg_bat("battery_charge")
            bat_discharge = _agg_bat("battery_discharge")

            def _agg_per_node(key: str) -> np.ndarray:
                if key not in sc:
                    return np.zeros(n_hours)
                arr = sc[key][:]
                if arr.ndim < 2:
                    return arr[:n_hours] if arr.size else np.zeros(n_hours)
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                s = arr.sum(axis=0)
                if len(s) < n_hours:
                    s = np.pad(s, (0, n_hours - len(s)))
                return s[:n_hours]
            curtailment = _agg_per_node("curtailment")
            load_shed = _agg_per_node("loss_load")
            demand = _agg_per_node("demand")

        # Tech color palette so the chart picks up the same colors the
        # rest of the dashboard uses.
        try:
            palette = get_generation_colors()
            default_color = get_generation_default_color()
        except Exception:
            palette, default_color = {}, "#95A5A6"
        tech_colors = {}
        for tech in tech_series:
            tl = tech.lower()
            picked = default_color
            for key, clr in palette.items():
                if key.lower() in tl:
                    picked = clr
                    break
            tech_colors[tech] = picked

        return {
            "year": int(year),
            "hours": list(range(n_hours)),
            "gen_by_tech": {
                t: [float(v) for v in s] for t, s in tech_series.items()
            },
            "bat_charge": [float(v) for v in bat_charge],
            "bat_discharge": [float(v) for v in bat_discharge],
            "curtailment": [float(v) for v in curtailment],
            "load_shed": [float(v) for v in load_shed],
            "demand": [float(v) for v in demand],
            "tech_colors": tech_colors,
        }


def _tech_bucket_for_gen(cfg: dict, gen_name: str) -> str:
    """Lightweight tech bucketing for the dispatch chart.

    Prefer the config-declared fuel (string scalar in the HDF5 attrs);
    fall back to keyword matching on the generator name. Mirrors the
    canonical labels the dashboard's RE-share logic expects so the
    stack colors align with the dashboard's mix chart.
    """
    fuel = str(cfg.get("fuel", "")).lower()
    if fuel in ("sun", "solar", "pv", "photovoltaic"):
        return "Solar"
    if fuel in ("wind",):
        return "Wind"
    if fuel in ("water", "hydro"):
        return "Hydro"
    if fuel in ("biomass", "bio"):
        return "Biomass"
    if fuel in ("geothermal",):
        return "Geothermal"
    if fuel in ("otec",):
        return "OTEC"
    if fuel in ("nuclear",):
        return "Nuclear"
    if fuel in ("hydrogen", "h2"):
        return "Hydrogen"
    if "oil" in fuel or fuel in ("diesel", "fuel oil", "fuel_oil"):
        return "Fuel oil"
    if "gas" in fuel:
        return "Gas"
    if "coal" in fuel:
        return "Coal"
    # Fall back to substring match on the dataset name.
    nl = gen_name.lower()
    for kw, label in (
        ("solar", "Solar"), ("photovolt", "Solar"), ("fotovolt", "Solar"),
        ("wind", "Wind"), ("eolic", "Wind"), ("eólic", "Wind"),
        ("hydro", "Hydro"), ("hidroelect", "Hydro"), ("hidroeléct", "Hydro"),
        ("biomass", "Biomass"), ("bioelect", "Biomass"),
        ("geotherm", "Geothermal"),
        ("nuclear", "Nuclear"),
        ("diesel", "Fuel oil"), ("fuel oil", "Fuel oil"), ("fuel_oil", "Fuel oil"),
        ("gas", "Gas"),
        ("coal", "Coal"),
    ):
        if kw in nl:
            return label
    return "Other"


class UCLoadShedCurtailmentChart(QWidget):
    """Load-shedding and curtailment timeline — adequacy diagnostic.

    Two stacked subplots:
      Top: hourly load-shed (bars) + cumulative MWh line.
      Bottom: hourly curtailment (bars) + cumulative MWh line.

    Banner shows totals + peak single-hour values + load-shed as % of
    demand. A glance answers "was the run adequate, and how much
    renewable energy did the system waste?".
    """

    TITLE = "Load Shed & Curtailment"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_loadshed_curtailment.html")
        ))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            rng = _system_node_range(h5f, bp)

            def _hourly_total(key: str) -> np.ndarray:
                if key not in sc:
                    return np.zeros(0)
                arr = sc[key][:]
                if arr.ndim < 2:
                    return arr.astype(float)
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                return arr.sum(axis=0).astype(float)

            ls = _hourly_total("loss_load")
            curt = _hourly_total("curtailment")
            dem = _hourly_total("demand")

        n_hours = max(len(ls), len(curt), len(dem))
        if n_hours == 0:
            return {"error": "No timeline data"}

        def _fit(a: np.ndarray) -> np.ndarray:
            if len(a) >= n_hours:
                return a[:n_hours]
            return np.pad(a, (0, n_hours - len(a)), mode="constant")

        ls = _fit(ls)
        curt = _fit(curt)
        dem = _fit(dem)

        ls_cum = np.cumsum(ls)
        curt_cum = np.cumsum(curt)

        return {
            "year": int(year),
            "hours": list(range(n_hours)),
            "load_shed_mw": [float(v) for v in ls],
            "curtailment_mw": [float(v) for v in curt],
            "load_shed_cum_mwh": [float(v) for v in ls_cum],
            "curtailment_cum_mwh": [float(v) for v in curt_cum],
            "demand_total_mwh": float(dem.sum()),
            "load_shed_total_mwh": float(ls.sum()),
            "load_shed_max_mw": float(ls.max()) if ls.size else 0.0,
            "curtailment_total_mwh": float(curt.sum()),
            "curtailment_max_mw": float(curt.max()) if curt.size else 0.0,
        }


class UCMarginalTechChart(QWidget):
    """Marginal technology heatmap — which tech sets the system price.

    Ex-post merit-order analysis: for each hour, identify the ON gen
    whose variable cost is highest among those dispatching strictly
    between 0 and their rated power (the "partially loaded" units,
    which can move at the margin). The tech bucket of that gen is the
    marginal tech for the hour. Falls back to the highest-cost ON unit
    if no partial dispatcher exists, or to a synthetic "Load shed"
    bucket if no gen is on (price = VOLL).

    The visualisation pairs an hour-timeline coloured by marginal tech
    with a bar chart of "hours marginal" per tech so the user can both
    see the time pattern AND the overall share.
    """

    TITLE = "Marginal Technology"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_marginal_tech.html")
        ))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }"
        )

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }"
            )

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            rng = _system_node_range(h5f, bp)

            gen_configs = _load_gen_configs(h5f, bp)
            gen_data = _load_gen_data(sc)
            status_grp = sc.get("gen_status") if hasattr(sc, "get") else None
            # Per-hour system-wide load shed (sliced to system nodes).
            # When this is > 0 the balance constraint is pegged to VOLL
            # and the marginal "unit" is the load-shed variable itself,
            # not whichever thermal is dispatching at its rated power.
            ls_hourly: Optional[np.ndarray] = None
            if "loss_load" in sc:
                ls_arr = sc["loss_load"][:]
                if ls_arr.ndim >= 2:
                    if rng is not None:
                        lo, hi = rng
                        ls_arr = ls_arr[lo:hi]
                    ls_hourly = ls_arr.sum(axis=0).astype(float)
                else:
                    ls_hourly = ls_arr.astype(float)
            status_by_gen: dict[str, np.ndarray] = {}
            if status_grp is not None:
                for k in status_grp:
                    arr = status_grp[k][:]
                    if arr.ndim < 2:
                        continue
                    if rng is not None:
                        lo, hi = rng
                        arr = arr[lo:hi]
                    status_by_gen[k] = arr

            # Determine common hour count from generation.
            n_hours = 0
            for arr in gen_data.values():
                if arr.ndim >= 2:
                    n_hours = max(n_hours, int(arr.shape[1]))
            if n_hours == 0:
                return {"error": "No generation data"}

            # LMPs for hover (already in USD/MWh after the convert step).
            hour_lmp = None
            if "electricity_prices" in sc:
                ep = sc["electricity_prices"][:]
                hour_lmp = [float(v) for v in ep[:n_hours]]

            # Per-gen rated capacity (max of fuel_cost-scaled gen_output
            # would be circular; pull from config "rated_power" when
            # available, else from the per-hour max observed).
            def _max_node(arr: np.ndarray) -> np.ndarray:
                # Sliced array shape [nodes_in_sys, hours]; for marginal
                # logic we collapse to system-level total per gen.
                return arr.sum(axis=0).astype(float)

            gen_names = list(gen_data.keys())
            n_gen = len(gen_names)
            # Variable cost per gen (fuel + maintenance, per-node averaged).
            var_costs = np.zeros(n_gen)
            rated = np.zeros(n_gen)
            gen_total = np.zeros((n_gen, n_hours))
            gen_on    = np.zeros((n_gen, n_hours), dtype=bool)
            techs: list[str] = []
            for gi, gname in enumerate(gen_names):
                arr = gen_data[gname]
                if arr.ndim < 2:
                    continue
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                tot = _max_node(arr)
                if len(tot) < n_hours:
                    tot = np.pad(tot, (0, n_hours - len(tot)))
                gen_total[gi, :] = tot[:n_hours]
                # Status: ON if any node has gen_status > 0.5 OR if no
                # gen_status exists (RE renewables don't have status).
                st = status_by_gen.get(gname)
                if st is not None and st.ndim >= 2:
                    on_per_h = (st > 0.5).any(axis=0)
                    if len(on_per_h) < n_hours:
                        on_per_h = np.pad(on_per_h, (0, n_hours - len(on_per_h)))
                    gen_on[gi, :] = on_per_h[:n_hours]
                else:
                    # No gen_status — treat as ON whenever output > 0.
                    gen_on[gi, :] = gen_total[gi, :] > 1e-3
                cfg = gen_configs[gi] if gi < len(gen_configs) else {}
                fc = _attr_scalar(cfg, "fuel_cost")
                mc = _attr_scalar(cfg, "maintenance_cost")
                var_costs[gi] = fc + mc
                # Rated power: prefer config attr, fall back to observed max.
                rp = _attr_scalar(cfg, "rated_power")
                rated[gi] = rp if rp > 0 else float(np.max(gen_total[gi, :]))
                techs.append(_tech_bucket_for_gen(cfg, gname))

        if n_gen == 0:
            return {"error": "No generators"}

        # Build tech label index. We add a synthetic "Load shed" bucket
        # so hours with zero ON gens have a colour.
        SHED = "Load shed"
        unique_techs = sorted(set(techs))
        if SHED not in unique_techs:
            unique_techs.append(SHED)
        label_to_idx = {lbl: i for i, lbl in enumerate(unique_techs)}

        # Pick tech palette in sync with the dispatch chart.
        try:
            palette = get_generation_colors()
            default_color = get_generation_default_color()
        except Exception:
            palette, default_color = {}, "#95A5A6"
        tech_colors = []
        for lbl in unique_techs:
            if lbl == SHED:
                tech_colors.append("#E74C3C")
                continue
            ll = lbl.lower()
            picked = default_color
            for key, clr in palette.items():
                if key.lower() in ll:
                    picked = clr
                    break
            tech_colors.append(picked)

        # Per-hour: find marginal gen.
        marginal_idx = np.full(n_hours, label_to_idx[SHED], dtype=int)
        for h in range(n_hours):
            # Load shedding active → price = VOLL, marginal is "Load shed".
            # Threshold is small (1 MW) to ignore numerical noise from
            # the solver; anything above that means the balance closed
            # via shed and the LMP is uniformly at the shedding cost.
            if ls_hourly is not None and ls_hourly[h] > 1.0:
                marginal_idx[h] = label_to_idx[SHED]
                continue
            on_mask = gen_on[:, h]
            if not on_mask.any():
                continue
            on_gens = np.where(on_mask)[0]
            # Partial dispatchers: output strictly between 0 and rated.
            outs = gen_total[on_gens, h]
            caps = rated[on_gens]
            partial = (outs > 1e-3) & (outs < caps - 1e-3)
            candidates = on_gens[partial] if partial.any() else on_gens
            # Highest variable cost among candidates → marginal.
            best = candidates[np.argmax(var_costs[candidates])]
            marginal_idx[h] = label_to_idx[techs[best]]

        # Count hours per tech.
        hours_marginal = {}
        for lbl in unique_techs:
            hours_marginal[lbl] = int((marginal_idx == label_to_idx[lbl]).sum())

        return {
            "year": int(year),
            "hours": list(range(n_hours)),
            "tech_indices": list(range(len(unique_techs))),
            "tech_labels": unique_techs,
            "tech_colors": tech_colors,
            "marginal_idx": [int(v) for v in marginal_idx],
            "hours_marginal": hours_marginal,
            "hour_lmp": hour_lmp,
        }


class UCPriceDurationChart(QWidget):
    """Price duration curve — LMPs sorted desc vs. % of hours.

    The classic market-design diagnostic. Three background bands flag
    the price regime: peak (top 10%), mid-merit (10-60%), baseload
    (60-100%). Subtitle reports mean / median / P95 plus a "scarcity"
    threshold (hours above 95th percentile) to flag stress.
    """

    TITLE = "Price Duration Curve"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_price_duration.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }")

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }")

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "electricity_prices" not in sc:
                return {"error": "electricity_prices missing"}
            prices = np.asarray(sc["electricity_prices"][:], dtype=float)
        if prices.size == 0:
            return {"error": "Empty price series"}
        sorted_prices = np.sort(prices)[::-1]
        n = len(sorted_prices)
        # Resample to 0–100% in 1pp steps so the chart is always 101
        # points regardless of the simulation length. Linear interp on
        # the empirical CDF.
        pct = np.linspace(0.0, 100.0, 101)
        source_x = np.linspace(0.0, 100.0, n)
        resampled = np.interp(pct, source_x, sorted_prices)
        p95 = float(np.percentile(prices, 95))
        scarcity_hours = int((prices >= p95).sum())
        return {
            "year": int(year),
            "sorted_prices": [float(v) for v in resampled],
            "pct_hours": [float(v) for v in pct],
            "scarcity_threshold": p95,
            "scarcity_hours": scarcity_hours,
            "mean": float(prices.mean()),
            "median": float(np.median(prices)),
            "p95": p95,
            "voll_estimate": float(prices.max()),
        }


class UCStorageSOCChart(QWidget):
    """Storage SOC trajectory + daily cycle count per battery.

    Top: SOC % per battery over the horizon. Bottom: equivalent full
    cycles per day (sum daily discharge MWh / capacity MWh). Helps
    diagnose whether the run uses storage as designed (arbitrage,
    ancillary) vs. cycling near nameplate limits.
    """

    TITLE = "Storage SOC & Cycles"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_storage_soc.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }")

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }")

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            rng = _system_node_range(h5f, bp)
            tres = _get_temporal_res(h5f)
            bat_configs = _load_bat_configs(h5f, bp)
            soc = _load_bat_data(sc, "battery_soc")
            charge = _load_bat_data(sc, "battery_charge")
            discharge = _load_bat_data(sc, "battery_discharge")

        if not soc and not charge and not discharge:
            return {"error": "No battery data in scenario"}

        # Determine hours from any battery (they should all share n_hours).
        ref = next(
            (a for a in soc.values() if a.ndim >= 2),
            next((a for a in charge.values() if a.ndim >= 2),
                 next((a for a in discharge.values() if a.ndim >= 2), None)),
        )
        if ref is None:
            return {"error": "Battery arrays empty"}
        n_hours = ref.shape[1]

        steps_per_day = max(1, int(24 / max(1, tres)))
        n_days = max(1, n_hours // steps_per_day)
        usable = n_days * steps_per_day

        def _slice_sys(arr: np.ndarray) -> np.ndarray:
            if arr.ndim < 2:
                return arr
            if rng is not None:
                lo, hi = rng
                return arr[lo:hi]
            return arr

        bat_names = sorted(set(soc.keys()) | set(charge.keys()) | set(discharge.keys()))
        out: dict[str, dict] = {
            "soc_pct": {}, "daily_cycles": {}, "totals": {},
        }
        for bi, bname in enumerate(bat_names):
            soc_arr = _slice_sys(soc.get(bname, np.zeros((1, n_hours))))
            ch_arr  = _slice_sys(charge.get(bname, np.zeros((1, n_hours))))
            dh_arr  = _slice_sys(discharge.get(bname, np.zeros((1, n_hours))))
            cfg = bat_configs[bi] if bi < len(bat_configs) else {}
            # Capacity (MWh): prefer per-node max of soc; fall back to
            # the config's rated_capacity attr.
            cap_mwh = float(soc_arr.sum(axis=0).max()) if soc_arr.size else 0.0
            if cap_mwh <= 0:
                cap_mwh = _attr_scalar(cfg, "rated_capacity", 0.0)
            soc_total = soc_arr.sum(axis=0)[:n_hours]
            soc_pct = (
                (soc_total / cap_mwh * 100.0) if cap_mwh > 0
                else np.zeros_like(soc_total)
            )
            dh_total = dh_arr.sum(axis=0)[:usable]
            # Equivalent full cycles per day = sum(daily discharge MWh) / cap
            day_disc = dh_total.reshape(n_days, steps_per_day).sum(axis=1) * tres
            cycles_per_day = (
                day_disc / cap_mwh if cap_mwh > 0 else np.zeros(n_days)
            )
            out["soc_pct"][bname] = [float(v) for v in soc_pct]
            out["daily_cycles"][bname] = [float(v) for v in cycles_per_day]
            out["totals"][bname] = {
                "charge_mwh": float(ch_arr.sum() * tres),
                "discharge_mwh": float(dh_arr.sum() * tres),
                "capacity_mwh": cap_mwh,
            }

        return {
            "year": int(year),
            "hours": list(range(n_hours)),
            "days": list(range(1, n_days + 1)),
            "batteries": bat_names,
            **out,
        }


class UCNetLoadDurationChart(QWidget):
    """Net Load Duration Curve.

    Side-by-side duration curves of gross demand vs. net load (demand
    minus renewable generation), both sorted descending. The gap
    between the two lines is the contribution of RE to peak shaving.
    Negative net-load hours flag RE surplus (over-build hints).
    """

    TITLE = "Net Load Duration"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_netload_duration.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }")

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }")

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "demand" not in sc:
                return {"error": "demand missing"}
            rng = _system_node_range(h5f, bp)
            dem_arr = sc["demand"][:]
            if dem_arr.ndim >= 2:
                if rng is not None:
                    lo, hi = rng
                    dem_arr = dem_arr[lo:hi]
                demand = dem_arr.sum(axis=0).astype(float)
            else:
                demand = dem_arr.astype(float)
            n_hours = demand.size
            # Sum RE generation: walk gen_data and accept generators
            # whose canonical bucket matches one of the renewable labels.
            gen_configs = _load_gen_configs(h5f, bp)
            gen_data = _load_gen_data(sc)
            re_total = np.zeros(n_hours)
            RE_LABELS = {"Wind", "Solar", "Hydro", "Biomass", "OTEC",
                         "Geothermal", "Nuclear", "Hydrogen"}
            for gi, (gname, arr) in enumerate(gen_data.items()):
                if arr.ndim < 2:
                    continue
                cfg = gen_configs[gi] if gi < len(gen_configs) else {}
                tech = _tech_bucket_for_gen(cfg, gname)
                if tech not in RE_LABELS:
                    continue
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                hourly = arr.sum(axis=0).astype(float)
                if len(hourly) < n_hours:
                    hourly = np.pad(hourly, (0, n_hours - len(hourly)))
                re_total += hourly[:n_hours]
        if n_hours == 0:
            return {"error": "Empty horizon"}
        net_load = demand - re_total
        demand_sorted = np.sort(demand)[::-1]
        net_sorted = np.sort(net_load)[::-1]
        pct = np.linspace(0.0, 100.0, 101)
        source_x = np.linspace(0.0, 100.0, n_hours)
        demand_rs = np.interp(pct, source_x, demand_sorted)
        net_rs = np.interp(pct, source_x, net_sorted)
        return {
            "year": int(year),
            "pct_hours": [float(v) for v in pct],
            "demand_sorted": [float(v) for v in demand_rs],
            "net_load_sorted": [float(v) for v in net_rs],
            "peak_demand_mw": float(demand.max()),
            "peak_netload_mw": float(net_load.max()),
            "min_netload_mw": float(net_load.min()),
            "hours_negative_netload": int((net_load < 0).sum()),
        }


class UCRampDistributionChart(QWidget):
    """Hour-to-hour ramp distribution per technology.

    Box plot of ``|ΔP|`` for each tech with a P95 diamond overlaid.
    Wide boxes / tall whiskers identify techs that cycled aggressively;
    narrow boxes near zero are baseload. Useful to diagnose whether
    ramp_rate constraints are binding the dispatch.
    """

    TITLE = "Ramp Distribution"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_ramp_distribution.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }")

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }")

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            rng = _system_node_range(h5f, bp)
            gen_configs = _load_gen_configs(h5f, bp)
            gen_data = _load_gen_data(sc)
            # Aggregate ramp magnitudes per tech bucket.
            ramps_by_tech: dict[str, list[float]] = {}
            for gi, (gname, arr) in enumerate(gen_data.items()):
                if arr.ndim < 2:
                    continue
                cfg = gen_configs[gi] if gi < len(gen_configs) else {}
                tech = _tech_bucket_for_gen(cfg, gname)
                if rng is not None:
                    lo, hi = rng
                    arr = arr[lo:hi]
                # System-level hourly output for this generator, then
                # |ΔP| between consecutive hours.
                hourly = arr.sum(axis=0).astype(float)
                if hourly.size < 2:
                    continue
                d = np.abs(np.diff(hourly))
                # Skip series that never moved (always 0 or always
                # constant) — they'd add a 0-only box of noise.
                if d.max() < 1e-6:
                    continue
                ramps_by_tech.setdefault(tech, []).extend(float(v) for v in d)
        if not ramps_by_tech:
            return {"error": "No ramping generators"}

        # Order techs by P95 descending so the most aggressive techs
        # land on the left of the box plot.
        techs_sorted = sorted(
            ramps_by_tech.keys(),
            key=lambda t: -float(np.percentile(ramps_by_tech[t], 95)),
        )
        p95 = {
            t: float(np.percentile(ramps_by_tech[t], 95)) for t in techs_sorted
        }

        try:
            palette = get_generation_colors()
            default_color = get_generation_default_color()
        except Exception:
            palette, default_color = {}, "#95A5A6"
        tech_colors = {}
        for tech in techs_sorted:
            tl = tech.lower()
            picked = default_color
            for key, clr in palette.items():
                if key.lower() in tl:
                    picked = clr
                    break
            tech_colors[tech] = picked

        return {
            "year": int(year),
            "techs": techs_sorted,
            "ramps": {t: ramps_by_tech[t] for t in techs_sorted},
            "p95": p95,
            "tech_colors": tech_colors,
        }


class UCLMPByNodeChart(QWidget):
    """Locational marginal prices by node — congestion map.

    Heatmap node × hour of ``nodal_electricity_prices``, with a per-
    node mean LMP bar (ranked) and a system-average line for context.
    Wide spread between nodes' means = transmission constraints active.
    """

    TITLE = "LMP by Node"
    TR_KEY = None

    def __init__(self):
        super().__init__()
        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingWebPage(self._view))
        self._bridge = _UCChartBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("loader", self._bridge)
        self._view.page().setWebChannel(self._channel)
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view)
        resources_dir = Path(__file__).parent.parent / "resources"
        self._view.load(QUrl.fromLocalFile(
            str(resources_dir / "uc_lmp_by_node.html")))
        self.fig = None
        self._loaded = False

    def get_params_widget(self) -> Optional[QWidget]:
        return None

    def update_chart(self, h5_path: Path, years: list[int], **kw):
        payload = self._build_payload(h5_path, years, **kw)
        self._bridge.set_payload(payload)
        self._view.page().runJavaScript(
            "if (typeof refresh === 'function') { refresh(); }")

    def _safe_update(self, h5_path: Path, years: list[int], **kwargs):
        try:
            self.update_chart(h5_path, years, **kwargs)
            self._loaded = True
        except Exception as e:
            logger.warning(f"Chart {self.TITLE} failed: {e}")
            self._bridge.set_payload({"error": f"Failed to load data: {e}"})
            self._view.page().runJavaScript(
                "if (typeof refresh === 'function') { refresh(); }")

    def export_image(self, file_path):
        self._view.page().printToPdf(str(file_path))

    def _build_payload(self, h5_path: Path, years: list[int], **kw) -> dict:
        bp = kw.get("base_prefix", "")
        year_range = kw.get("year_range")
        with _open_h5(h5_path) as h5f:
            scenarios = list(_sorted_scenarios(h5f, bp))
            if not scenarios:
                return {"error": "No scenarios"}
            idx = _uc_select_year_idx(h5f, bp, year_range)
            sc_key, year = scenarios[idx]
            sc = _open_scenario(h5f, bp, sc_key)
            if "nodal_electricity_prices" not in sc:
                return {"error": "nodal_electricity_prices missing"}
            nodal = np.asarray(sc["nodal_electricity_prices"][:], dtype=float)
            rng = _system_node_range(h5f, bp)
            if nodal.ndim != 2:
                return {"error": "nodal prices not 2-D"}
            if rng is not None:
                lo, hi = rng
                nodal = nodal[lo:hi]
                node_offset = lo
            else:
                node_offset = 0
            # Try to pull node names from system_configuration.
            node_labels: list[str] = []
            sc_cfg = _open_system_config(h5f, bp)
            try:
                if sc_cfg is not None and "nodes" in sc_cfg \
                        and "name" in sc_cfg["nodes"]:
                    names = sc_cfg["nodes"]["name"][:]
                    for i in range(nodal.shape[0]):
                        gi = node_offset + i
                        if gi < len(names):
                            v = names[gi]
                            if isinstance(v, bytes):
                                v = v.decode(errors="replace")
                            node_labels.append(str(v))
                        else:
                            node_labels.append(f"Node {gi}")
            except Exception:
                node_labels = []
            if not node_labels:
                node_labels = [f"Node {node_offset + i}" for i in range(nodal.shape[0])]

        if nodal.size == 0:
            return {"error": "Empty LMP matrix"}

        return {
            "year": int(year),
            "hours": list(range(nodal.shape[1])),
            "nodes": node_labels,
            "z": [[float(v) for v in row] for row in nodal],
            "mean_by_node": [float(v) for v in nodal.mean(axis=1)],
            "system_avg": [float(v) for v in nodal.mean(axis=0)],
        }


def _attr_scalar(cfg: dict, key: str, default: float = 0.0) -> float:
    """Read a config attr that may be a scalar float, a vector (list/
    ndarray), or a stringified list. Returns the **mean** of vectors
    (per-node) so the marginal logic has a single representative cost
    per generator. For string forms we ``ast.literal_eval`` first.
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
        try:
            return float(sum(v) / len(v))
        except (TypeError, ValueError):
            return default
    return default


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Charts Panel (container with tabs)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Ordered so charts within the same sidebar category are contiguous —
# results_dialog emits a new category header whenever the running
# category changes, so out-of-order entries cause duplicate headers
# (e.g. two "GENERATION" or "SYSTEM" rows in the sidebar).
_CHART_CLASSES = [
    # Generation
    GenerationMixChart,
    # Storage
    BatteryHeatmapChart,
    BatteryOperationChart,
    # Economics
    CFLcoeVallcoeChart,
    ElectricityCostChart,
    RevenueProfitabilityChart,
    PriceDurationChart,
    CarbonPenaltyChart,
    CashFlowChart,
    # System
    SystemMetricsEvolutionChart,
    NetLoadHeatmapChart,
    InterNodeFlowsChart,
    FuelSupplyChart,
    SankeyEnergyFlowChart,
    # Flexibility & Reliability
    FlexReliabilityChart,
    # UC Operations — only meaningful for simulation_mode == "unit_commitment"
    UCHourlyPriceChart,
    UCCommitmentHeatmapChart,
    UCDispatchStackChart,
    UCLoadShedCurtailmentChart,
    UCMarginalTechChart,
    UCPriceDurationChart,
    UCStorageSOCChart,
    UCNetLoadDurationChart,
    UCRampDistributionChart,
    UCLMPByNodeChart,
    # MGA (split into thematic charts)
    MGARobustnessFrontierChart,
    MGAParcoordsChart,
    MGAPathwayChart,
    MGASpatialChart,
    MGAProjectionChart,
    MGAAnnotatedDendrogramChart,
    MGADecisionFactorsChart,
    MGACompositionChart,
    MGASimilarityChart,
    # Custom
    CustomChart,
]


class ChartsPanel(QWidget):
    """Container widget with a combo selector for all chart types."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h5_path: Optional[Path] = None
        self._years: list[int] = []
        self._base_prefix: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Chart selector combo
        from PySide6.QtWidgets import QStackedWidget
        selector = QHBoxLayout()
        selector.addWidget(QLabel(tr("results_charts.select_chart")))
        self._combo = QComboBox()
        selector.addWidget(self._combo, 1)
        layout.addLayout(selector)

        # Stacked widget holding one page per chart
        self._stack = QStackedWidget()
        self._charts: list[ResultsChartBase] = []

        for cls in _CHART_CLASSES:
            chart = cls()
            self._charts.append(chart)

            container = QWidget()
            clayout = QVBoxLayout(container)
            clayout.setContentsMargins(4, 4, 4, 4)

            params = chart.get_params_widget()
            if params is not None:
                clayout.addWidget(params)

            clayout.addWidget(chart, 1)

            if isinstance(chart, FigureCanvasQTAgg):
                nav = NavigationToolbar2QT(chart, container)
                clayout.addWidget(nav)

            self._stack.addWidget(container)
            title = tr(chart.TR_KEY) if chart.TR_KEY else chart.TITLE
            self._combo.addItem(title)

        self._combo.currentIndexChanged.connect(self._on_chart_changed)
        layout.addWidget(self._stack, 1)

    def set_data_source(self, h5_path: Path, years: list[int], base_prefix: str = ""):
        self._h5_path = h5_path
        self._years = years
        self._base_prefix = base_prefix
        for chart in self._charts:
            chart._loaded = False
        self._render_current()

    def _on_chart_changed(self, index: int):
        self._stack.setCurrentIndex(index)
        self._render_current()

    def _render_current(self):
        idx = self._combo.currentIndex()
        if idx < 0 or self._h5_path is None:
            return
        chart = self._charts[idx]
        if not chart._loaded:
            chart._safe_update(self._h5_path, self._years, base_prefix=self._base_prefix)
