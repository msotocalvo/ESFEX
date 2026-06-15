"""Example ESFEX plugin — a Julia overlay that registers a custom constraint type.

Install it by copying this directory to::

    ~/.esfex/plugins/custom_constraint_example/

On the next solve, the manager ``include()``s ``overlay.jl`` into the Julia
session, which calls ``ESFEX.register_constraint_hook!("gen_cap", …)``. A config
``custom_constraints`` entry then activates it::

    custom_constraints:
      - name: cap_unit_1
        type: gen_cap          # the type the overlay registered
        params: {generator: 1, limit: 80.0}

This is the "power-user" path: arbitrary JuMP constraints in Julia, no change to
the core ESFEX source. For simple linear caps, prefer the built-in ``linear``
type (no plugin needed).
"""

from __future__ import annotations

from pathlib import Path

from esfex.plugins.protocol import ESFEXPlugin, PluginContext


class CustomConstraintExamplePlugin(ESFEXPlugin):
    """Contributes one Julia overlay that registers a ``gen_cap`` constraint."""

    def get_julia_modules(self) -> list[Path]:
        return [self.context.plugin_dir / "overlay.jl"]


def create_plugin(context: PluginContext) -> ESFEXPlugin:
    return CustomConstraintExamplePlugin(context)
