"""Availability Generator plugin for ESFEX.

Generates hourly availability (capacity factor) time series for renewable
generators (Solar PV, Wind) from weather reanalysis data.

Supported data sources:
- Open-Meteo Historical API (ERA5 reanalysis, no API key required)
- NASA POWER API (MERRA-2 reanalysis, no API key required)
- ERA5 via atlite (requires CDS API key)
"""

from __future__ import annotations

from esfex.plugins.protocol import PluginContext, ESFEXPlugin


class AvailabilityGeneratorPlugin(ESFEXPlugin):
    """Plugin that generates availability profiles from weather data."""

    def get_cli_commands(self) -> list:
        from .cli_commands import app

        return [app]

    def get_menu_items(self, menu_bar, main_window) -> None:
        from .gui_dialog import add_availability_menu_item

        add_availability_menu_item(menu_bar, main_window)


def create_plugin(context: PluginContext) -> ESFEXPlugin:
    """Factory function called by the plugin manager."""
    return AvailabilityGeneratorPlugin(context)
