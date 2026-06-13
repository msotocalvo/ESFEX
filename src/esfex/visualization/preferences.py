"""User preferences: load, save, apply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QAction, QKeySequence

_PREFS_DIR = Path.home() / ".config" / "esfex"
_PREFS_FILE = _PREFS_DIR / "preferences.json"

# ── Default shortcuts ──────────────────────────────────────────────
# Maps action_id -> default QKeySequence string.

DEFAULT_SHORTCUTS: dict[str, str] = {
    # File menu
    "file.import": "Ctrl+O",
    "file.import_geo": "",
    "file.save": "Ctrl+S",
    "file.export": "Ctrl+Shift+S",
    "file.preferences": "Ctrl+,",
    # Toolbar -- mode actions
    "tool.select": "",
    "tool.add_system": "",
    "tool.line": "",
    "tool.generator": "",
    "tool.battery": "",
    "tool.transformer": "",
    "tool.bus": "",
    "tool.dev_zone": "",
    "tool.acdc_converter": "",
    "tool.freq_converter": "",
    "tool.electrolyzer": "",
    "tool.fuel_entry": "",
    "tool.fuel_storage": "",
    "tool.fuel_route": "",
    # Toolbar -- analysis actions
    "tool.validate": "",
    "tool.run": "",
    "tool.sensitivity": "",
    "tool.results": "",
}


# ── Default preference values ─────────────────────────────────────

DEFAULT_PREFERENCES: dict[str, dict[str, Any]] = {
    "general": {
        "theme": "GitHub Light",
        "font_size": 12,
        "language": "en",
        "auto_save": False,
        "auto_save_interval": 0,
        "startup": "empty",
        "undo_depth": 50,
        "notify_sim_complete": True,
        "auto_open_results": True,
        "recent_files_max": 10,
    },
    "map": {
        "default_basemap": "OpenStreetMap",
        "default_zoom": 7,
        "default_lat": 22.0,
        "default_lng": -79.0,
        "show_tooltips": True,
        "snap_to_grid": False,
        "label_font_size": 10,
        "animation_speed": "Normal",
        "show_minimap": False,
        "cluster_threshold": 0,
    },
    "solver": {
        "default_solver": "HiGHS",
        "threads": 4,
        "time_limit": 3600,
        "mip_gap": 0.001,
        "verbose": False,
        "scale_constraints": False,
        "presolve": "choose",
        "memory_limit": 0,
        "log_file": "",
        "feasibility_tol": 1e-7,
    },
    "editor": {
        "font_family": "Consolas",
        "font_size": 10,
        "tab_width": 4,
        "show_line_numbers": True,
        "word_wrap": False,
        "auto_indent": True,
        "console_font_size": 10,
        "console_max_lines": 5000,
    },
    "simulation": {
        "default_mode": "development",
        "default_resolution": 6,
        "default_rolling_horizon": 48,
        "default_overlap": 0,
        "default_primary_energy": False,
        "default_log_level": "basic",
        "log_to_file": False,
        "default_output_dir": "",
    },
    "results": {
        "chart_backend": "matplotlib",
        "export_dpi": 300,
        "default_export_format": "PNG",
        "color_palette": "ESFEX",
        "data_format": "HDF5",
        "csv_delimiter": ",",
        "include_metadata": True,
        "open_after_export": False,
    },
    "advanced": {
        "julia_sysimage": "",
        "julia_precompile": True,
        "max_workers": 1,
        "cache_strategy": "preload",
        "debug_mode": False,
        "cache_dir": str(Path.home() / ".cache" / "esfex"),
        "telemetry_opt_out": False,
    },
}


def get_preference(
    prefs: dict[str, Any], section: str, key: str, default: Any = None,
) -> Any:
    """Return a single preference value with fallback to DEFAULT_PREFERENCES."""
    val = prefs.get(section, {}).get(key)
    if val is not None:
        return val
    section_defaults = DEFAULT_PREFERENCES.get(section, {})
    if key in section_defaults:
        return section_defaults[key]
    return default


def get_export_dpi() -> int:
    """Return the user's preferred export DPI (default 300)."""
    prefs = load_preferences()
    # Check general (current location) then results (legacy)
    val = prefs.get("general", {}).get("export_dpi")
    if val is not None:
        return val
    return get_preference(prefs, "results", "export_dpi", 300)


def load_preferences() -> dict[str, Any]:
    """Load prefs from disk; return empty dict if no file."""
    if _PREFS_FILE.exists():
        try:
            with open(_PREFS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_preferences(prefs: dict[str, Any]) -> None:
    """Write prefs to disk, creating directory if needed."""
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)


# ── Recent files ──────────────────────────────────────────────────


def get_recent_files(prefs: dict[str, Any] | None = None) -> list[str]:
    """Return the list of recently opened config paths (newest first).

    Paths that no longer exist on disk are filtered out — this is
    typically called when populating a menu; showing stale entries
    that 404 on click would be hostile.
    """
    if prefs is None:
        prefs = load_preferences()
    raw = prefs.get("recent_files", [])
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and Path(p).is_file()]


def add_recent_file(path: str, max_entries: int | None = None) -> list[str]:
    """Add ``path`` to the front of the recent-files list and persist.

    Dedupe + truncate to ``max_entries`` (falls back to
    ``general.recent_files_max`` then 10).  Returns the updated list.
    """
    prefs = load_preferences()
    if max_entries is None:
        max_entries = get_preference(
            prefs, "general", "recent_files_max", 10,
        )
    abs_path = str(Path(path).expanduser().resolve())
    current = prefs.get("recent_files", [])
    if not isinstance(current, list):
        current = []
    # Dedupe (keep newest position) — compare by resolved path.
    deduped: list[str] = [abs_path]
    seen = {abs_path}
    for p in current:
        if not isinstance(p, str):
            continue
        rp = str(Path(p).expanduser().resolve())
        if rp in seen:
            continue
        seen.add(rp)
        deduped.append(rp)
    truncated = deduped[:max_entries]
    prefs["recent_files"] = truncated
    save_preferences(prefs)
    return truncated


def get_shortcuts(prefs: dict[str, Any]) -> dict[str, str]:
    """Return full shortcut map: defaults merged with user overrides."""
    overrides = prefs.get("shortcuts", {})
    merged = dict(DEFAULT_SHORTCUTS)
    for action_id, seq_str in overrides.items():
        if action_id in merged:
            merged[action_id] = seq_str
    return merged


def apply_shortcuts(
    action_registry: dict[str, QAction],
    shortcuts: dict[str, str],
) -> None:
    """Set QKeySequence on every registered action."""
    for action_id, action in action_registry.items():
        seq_str = shortcuts.get(action_id, "")
        action.setShortcut(QKeySequence(seq_str) if seq_str else QKeySequence())
