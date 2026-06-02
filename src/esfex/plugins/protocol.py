"""
Plugin protocol for ESFEX.

Defines the base class and data structures that all ESFEX plugins must use.
Plugins extend ESFEX without modifying its core source code — Julia modules
are included at runtime as overlays, never altering the native .jl files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import numpy as np
    import typer
    from pydantic import BaseModel

    from esfex.config.schema import ESFEXConfig

logger = logging.getLogger(__name__)


@dataclass
class PluginMeta:
    """Metadata describing a ESFEX plugin.

    Read from ``plugin.json`` in the plugin directory.
    """

    name: str
    """Unique slug identifier, e.g. ``"weather_forecast"``."""

    version: str
    """Semantic version string, e.g. ``"0.2.1"``."""

    description: str = ""
    author: str = ""
    url: str = ""

    requires_plugins: list[str] = field(default_factory=list)
    """Other plugin names that must be loaded first."""

    priority: int = 0
    """Lower values execute first."""

    category: str = "general"
    """One of: ``"data"``, ``"analysis"``, ``"visualization"``, ``"model"``, ``"general"``."""

    python_dependencies: list[str] = field(default_factory=list)
    """Pip requirement strings for informational purposes (e.g. ``["requests>=2.28"]``)."""


@dataclass
class PluginContext:
    """Context injected into the plugin factory ``create_plugin(context)``."""

    config: Optional[ESFEXConfig]
    """The loaded ESFEX configuration, or *None* if not yet available."""

    plugin_dir: Path
    """Root directory of the plugin (where ``plugin.json`` lives)."""

    data_dir: Path
    """Persistent data directory for this plugin: ``~/.esfex/plugin_data/{name}/``."""

    gui_mode: bool = False
    """*True* when running inside the Studio."""


class ESFEXPlugin:
    """Base class for ESFEX plugins.

    All methods have no-op default implementations.  Subclasses override
    only the hooks they need.

    **Important**: Plugins NEVER modify ESFEX source files.  Julia modules
    returned by :meth:`get_julia_modules` are ``include()``-d at runtime as
    overlays — the solver sees the native formulation plus the plugin's
    extensions, but the core ``.jl`` files remain untouched.
    """

    meta: PluginMeta

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    # ── Lifecycle ─────────────────────────────────────────────────────

    def setup(self) -> None:
        """Called after the plugin is instantiated. Perform one-time init."""

    def teardown(self) -> None:
        """Called when the plugin manager shuts down. Release resources."""

    # ── Configuration ─────────────────────────────────────────────────

    def get_config_schema(self) -> Optional[type[BaseModel]]:
        """Return a Pydantic model validating ``plugins.{name}`` config section."""
        return None

    def on_config_loaded(self, config: ESFEXConfig) -> None:
        """Called after the full configuration has been loaded and validated."""

    # ── Runner hooks ──────────────────────────────────────────────────

    def pre_simulation(self, *, config: ESFEXConfig, output_dir: Path) -> None:
        """Called before the simulation starts."""

    def post_demand_loaded(
        self,
        *,
        base_demand: np.ndarray,
        ev_demand: np.ndarray,
        total_demand: np.ndarray,
        config: ESFEXConfig,
    ) -> Optional[np.ndarray]:
        """Called after demand is loaded. Return modified total_demand or *None*."""
        return None

    def pre_master_problem(self, *, config: ESFEXConfig, years: list[int]) -> None:
        """Called before the master problem is solved."""

    def post_master_problem(
        self,
        *,
        investments: dict[str, Any],
        retirements: dict[str, Any],
        config: ESFEXConfig,
    ) -> None:
        """Called after master problem solution."""

    def pre_year(
        self,
        *,
        year: int,
        year_idx: int,
        units_config: dict[str, Any],
        config: ESFEXConfig,
    ) -> None:
        """Called before each year's operational dispatch."""

    def post_year(
        self,
        *,
        year: int,
        result: Any,
        hdf5_file: Any,
        output_dir: Path,
        config: ESFEXConfig,
    ) -> None:
        """Called after each year's results are available.

        *hdf5_file* is an open ``h5py.File`` in append mode.
        Plugins may write to ``plugins/{name}/`` group.
        """

    def post_simulation(
        self,
        *,
        results: list[Any],
        hdf5_path: Path,
        output_dir: Path,
        config: ESFEXConfig,
    ) -> None:
        """Called after all years are complete and HDF5 is finalized."""

    # ── Julia (runtime overlay — does NOT modify ESFEX source) ───────

    def get_julia_modules(self) -> list[Path]:
        """Return ``.jl`` files to ``include()`` after ``ESFEX.jl``.

        These modules can define functions invoked by Julia-side callbacks
        during model construction — e.g.
        ``add_hydrogen_constraints!(model, vars, input)``.

        The native ESFEX Julia code is never modified on disk.
        """
        return []

    # ── CLI ────────────────────────────────────────────────────────────

    def get_cli_commands(self) -> list[typer.Typer]:
        """Return Typer sub-apps to register as ``esfex <name> ...``."""
        return []

    # ── GUI (only called when gui_mode=True) ──────────────────────────

    def get_tree_categories(self) -> list[dict[str, str]]:
        """Return category descriptors for the element tree.

        Each dict should have ``{"key": "...", "label": "...", "element_type": "..."}``.
        """
        return []

    def get_forms(self, model: Any) -> list[tuple[str, Any]]:
        """Return ``(element_type, QWidget)`` pairs for the properties panel."""
        return []

    def get_toolbar_actions(self, toolbar: Any, main_window: Any) -> list[Any]:
        """Return ``QAction`` instances to add to the toolbar."""
        return []

    def get_menu_items(self, menu_bar: Any, main_window: Any) -> None:
        """Add items to the menu bar."""

    def get_result_variables(self) -> list[tuple[str, str, str, str]]:
        """Return ``(display_name, hdf5_key, aggregation, viz_type)`` tuples."""
        return []

    def get_map_layers(self, map_widget: Any) -> None:
        """Add custom layers to the map widget."""

    def get_translations(self) -> dict[str, dict[str, str]]:
        """Return ``{lang: {key: value}}`` translation mappings."""
        return {}
