"""Tests for the availability_generator plugin.

Covers solar CF, wind CF, generator orchestration, CLI, plugin structure,
and translation keys.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "esfex"
    / "plugins"
    / "availability_generator"
)

_TRANSLATIONS_DIR = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "esfex"
    / "visualization"
    / "translations"
)


# ===================================================================
# TestSolarCF
# ===================================================================


class TestSolarCF:
    """Unit tests for solar_cf.compute_solar_hourly_cf helper functions."""

    def test_constant_irradiance_cf(self):
        """At STC (1000 W/m2, 25C), CF should equal ~1.0."""
        from esfex.plugins.availability_generator.solar_cf import (
            _irradiance_to_hourly_cf,
        )

        ghi = np.full(8760, 1000.0)
        temp = np.full(8760, 25.0)
        cf = _irradiance_to_hourly_cf(ghi, temp, 0.20, -0.40, 45.0)
        # At STC: t_cell = 25 + (45-20)/800*1000 = 56.25
        # temp_factor = 1 + (-0.40/100)*(56.25-25) = 1 - 0.125 = 0.875
        # cf = 1.0 * 0.875 = 0.875
        np.testing.assert_allclose(cf, 0.875, atol=0.01)

    def test_temperature_derating(self):
        """Higher temp should reduce CF via gamma_pmax."""
        from esfex.plugins.availability_generator.solar_cf import (
            _irradiance_to_hourly_cf,
        )

        ghi = np.full(100, 800.0)
        temp_25 = np.full(100, 25.0)
        temp_45 = np.full(100, 45.0)

        cf_25 = _irradiance_to_hourly_cf(ghi, temp_25, 0.20, -0.40, 45.0)
        cf_45 = _irradiance_to_hourly_cf(ghi, temp_45, 0.20, -0.40, 45.0)

        assert cf_45.mean() < cf_25.mean()

    def test_zero_irradiance_gives_zero(self):
        """Night hours (GHI=0) must give CF=0."""
        from esfex.plugins.availability_generator.solar_cf import (
            _irradiance_to_hourly_cf,
        )

        ghi = np.zeros(24)
        temp = np.full(24, 20.0)
        cf = _irradiance_to_hourly_cf(ghi, temp, 0.20, -0.40, 45.0)
        np.testing.assert_array_equal(cf, 0.0)

    def test_output_shape_8760(self):
        """normalize_to_8760 must always return exactly 8760 elements."""
        from esfex.plugins.availability_generator.solar_cf import (
            _normalize_to_8760,
        )

        assert len(_normalize_to_8760(np.zeros(8760))) == 8760
        assert len(_normalize_to_8760(np.zeros(8784))) == 8760
        assert len(_normalize_to_8760(np.zeros(9000))) == 8760
        assert len(_normalize_to_8760(np.zeros(100))) == 8760

    def test_leap_year_truncation(self):
        """Leap year 8784 hours should drop Feb 29 (hours 1416-1439)."""
        from esfex.plugins.availability_generator.solar_cf import (
            _normalize_to_8760,
        )

        data = np.arange(8784, dtype=float)
        result = _normalize_to_8760(data)
        assert len(result) == 8760
        # Hour 1416 (start of Feb 29) should be removed
        assert result[1416] == 1440.0  # First hour of Mar 1

    def test_values_clipped_0_1(self):
        """All CF values must be in [0, 1]."""
        from esfex.plugins.availability_generator.solar_cf import (
            _irradiance_to_hourly_cf,
        )

        ghi = np.full(100, 1500.0)  # Very high
        temp = np.full(100, 10.0)
        cf = _irradiance_to_hourly_cf(ghi, temp, 0.20, -0.40, 45.0)
        assert cf.min() >= 0.0
        assert cf.max() <= 1.0


# ===================================================================
# TestWindCF
# ===================================================================


class TestWindCF:
    """Unit tests for wind_cf capacity factor computation."""

    def test_below_cutin_cf_zero(self):
        """Wind speed below cut-in should give CF=0."""
        from esfex.plugins.availability_generator.wind_cf import (
            _wind_speed_to_hourly_cf,
            _DEFAULT_WIND_SPEEDS,
            _DEFAULT_POWER_CURVE_MW,
            _DEFAULT_RATED_MW,
        )

        ws = np.full(100, 2.0)  # Below cut-in (~3 m/s)
        cf = _wind_speed_to_hourly_cf(
            ws, _DEFAULT_WIND_SPEEDS, _DEFAULT_POWER_CURVE_MW, _DEFAULT_RATED_MW,
        )
        np.testing.assert_allclose(cf, 0.0, atol=0.01)

    def test_at_rated_speed_cf_one(self):
        """At rated wind speed CF should be ~1.0."""
        from esfex.plugins.availability_generator.wind_cf import (
            _wind_speed_to_hourly_cf,
            _DEFAULT_WIND_SPEEDS,
            _DEFAULT_POWER_CURVE_MW,
            _DEFAULT_RATED_MW,
        )

        ws = np.full(100, 13.0)  # At rated (3.0 MW)
        cf = _wind_speed_to_hourly_cf(
            ws, _DEFAULT_WIND_SPEEDS, _DEFAULT_POWER_CURVE_MW, _DEFAULT_RATED_MW,
        )
        np.testing.assert_allclose(cf, 1.0, atol=0.01)

    def test_above_cutout_cf_zero(self):
        """Wind speed above cut-out (25 m/s) should give CF=0."""
        from esfex.plugins.availability_generator.wind_cf import (
            _wind_speed_to_hourly_cf,
            _DEFAULT_WIND_SPEEDS,
            _DEFAULT_POWER_CURVE_MW,
            _DEFAULT_RATED_MW,
        )

        ws = np.full(100, 30.0)
        cf = _wind_speed_to_hourly_cf(
            ws, _DEFAULT_WIND_SPEEDS, _DEFAULT_POWER_CURVE_MW, _DEFAULT_RATED_MW,
        )
        np.testing.assert_allclose(cf, 0.0, atol=0.01)

    def test_power_curve_interpolation(self):
        """Intermediate wind speeds should interpolate correctly."""
        from esfex.plugins.availability_generator.wind_cf import (
            _wind_speed_to_hourly_cf,
            _DEFAULT_WIND_SPEEDS,
            _DEFAULT_POWER_CURVE_MW,
            _DEFAULT_RATED_MW,
        )

        ws = np.array([7.0])  # ~1.08 MW in default curve
        cf = _wind_speed_to_hourly_cf(
            ws, _DEFAULT_WIND_SPEEDS, _DEFAULT_POWER_CURVE_MW, _DEFAULT_RATED_MW,
        )
        expected_cf = 1.08 / 3.0
        np.testing.assert_allclose(cf[0], expected_cf, atol=0.01)

    def test_output_shape_8760(self):
        """normalize_to_8760 for wind must also produce 8760 elements."""
        from esfex.plugins.availability_generator.wind_cf import (
            _normalize_to_8760,
        )

        assert len(_normalize_to_8760(np.zeros(8760))) == 8760
        assert len(_normalize_to_8760(np.zeros(8784))) == 8760


# ===================================================================
# TestGenerator
# ===================================================================


class TestGenerator:
    """Tests for the generator orchestrator."""

    def _make_config(self, num_nodes=1, fuel="Solar", gen_type="Renewable"):
        """Create a minimal mock config."""
        coords = [
            SimpleNamespace(latitude=10.0 + i, longitude=-70.0 + i)
            for i in range(num_nodes)
        ]
        gen = SimpleNamespace(
            fuel=fuel,
            type=gen_type,
            rated_power=[100.0] * num_nodes,
            invest_max_power=[0.0] * num_nodes,
        )
        nodes = SimpleNamespace(node_coordinates=coords)
        system = SimpleNamespace(
            generators={"gen_0": gen},
            nodes=nodes,
        )
        return SimpleNamespace(systems={"sys_a": system})

    def test_scan_renewable_only(self):
        """_collect_tasks should only pick Solar/Wind generators."""
        from esfex.plugins.availability_generator.generator import _collect_tasks

        cfg = self._make_config(fuel="Solar")
        tasks = _collect_tasks(cfg, None)
        assert len(tasks) == 1
        assert tasks[0][1] == "gen_0"
        assert tasks[0][4] == "Solar"  # profile_type

    def test_skip_non_renewable(self):
        """Non-renewable generators should be skipped."""
        from esfex.plugins.availability_generator.generator import _collect_tasks

        cfg = self._make_config(fuel="Diesel", gen_type="Non-renewable")
        tasks = _collect_tasks(cfg, None)
        assert len(tasks) == 0

    def test_profile_type_map_override(self):
        """Explicit profile_type_map should override fuel-name heuristics."""
        from esfex.plugins.availability_generator.generator import _collect_tasks

        cfg = self._make_config(fuel="Renewables", gen_type="Renewable")
        # Without map: "Renewables" doesn't match solar/wind hints
        tasks_no_map = _collect_tasks(cfg, None)
        assert len(tasks_no_map) == 0

        # With explicit map: should be included as Wind
        tasks_with_map = _collect_tasks(
            cfg, None, profile_type_map={"sys_a/gen_0": "Wind"},
        )
        assert len(tasks_with_map) == 1
        assert tasks_with_map[0][4] == "Wind"

    @patch(
        "esfex.plugins.availability_generator.generator.compute_solar_hourly_cf"
    )
    def test_single_node_csv_shape(self, mock_solar):
        """Single node, single year should produce (8760, 1) CSV."""
        from esfex.plugins.availability_generator.generator import (
            generate_availability_profiles,
        )

        mock_solar.return_value = np.random.rand(8760)

        cfg = self._make_config(num_nodes=1, fuel="Solar")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_availability_profiles(
                config=cfg,
                years=[2020],
                output_dir=Path(tmpdir),
                data_source="open_meteo",
            )
            assert len(result) == 1
            csv_path = list(result.values())[0]
            data = np.loadtxt(csv_path, delimiter=",")
            assert data.shape == (8760,)  # 1 column collapses to 1D

    @patch(
        "esfex.plugins.availability_generator.generator.compute_solar_hourly_cf"
    )
    def test_multi_node_csv_shape(self, mock_solar):
        """3 nodes should produce (8760, 3) CSV."""
        from esfex.plugins.availability_generator.generator import (
            generate_availability_profiles,
        )

        mock_solar.return_value = np.random.rand(8760)

        cfg = self._make_config(num_nodes=3, fuel="Solar")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_availability_profiles(
                config=cfg,
                years=[2020],
                output_dir=Path(tmpdir),
                data_source="open_meteo",
            )
            csv_path = list(result.values())[0]
            data = np.loadtxt(csv_path, delimiter=",")
            assert data.shape == (8760, 3)

    @patch(
        "esfex.plugins.availability_generator.generator.compute_solar_hourly_cf"
    )
    def test_multi_year_csv_shape(self, mock_solar):
        """3 years should produce (26280, 1) CSV."""
        from esfex.plugins.availability_generator.generator import (
            generate_availability_profiles,
        )

        mock_solar.return_value = np.random.rand(8760)

        cfg = self._make_config(num_nodes=1, fuel="Solar")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_availability_profiles(
                config=cfg,
                years=[2020, 2021, 2022],
                output_dir=Path(tmpdir),
                data_source="open_meteo",
            )
            csv_path = list(result.values())[0]
            data = np.loadtxt(csv_path, delimiter=",")
            assert data.shape == (8760 * 3,)

    @patch(
        "esfex.plugins.availability_generator.generator.compute_wind_hourly_cf"
    )
    def test_wind_generator_uses_wind_cf(self, mock_wind):
        """Wind fuel generator should call compute_wind_hourly_cf."""
        from esfex.plugins.availability_generator.generator import (
            generate_availability_profiles,
        )

        mock_wind.return_value = np.random.rand(8760)

        cfg = self._make_config(num_nodes=1, fuel="Wind")
        with tempfile.TemporaryDirectory() as tmpdir:
            generate_availability_profiles(
                config=cfg,
                years=[2020],
                output_dir=Path(tmpdir),
                data_source="open_meteo",
            )
            mock_wind.assert_called_once()


# ===================================================================
# TestCLI
# ===================================================================


class TestCLI:
    """Tests for CLI sub-commands."""

    def test_cli_app_exists(self):
        """CLI app should be importable."""
        from esfex.plugins.availability_generator.cli_commands import app

        assert app is not None
        assert app.info.name == "availability_generator"

    def test_cli_has_generate_command(self):
        """The generate command should be registered."""
        from esfex.plugins.availability_generator.cli_commands import app

        command_names = [
            cmd.name or cmd.callback.__name__
            for cmd in app.registered_commands
        ]
        assert "generate" in command_names

    def test_default_years_from_config(self):
        """_default_years should extract year from temporal config."""
        from esfex.plugins.availability_generator.cli_commands import (
            _default_years,
        )

        import datetime

        cfg = SimpleNamespace(
            temporal=SimpleNamespace(date_start=datetime.date(2021, 1, 1))
        )
        years = _default_years(cfg)
        assert years == [2021]

    def test_default_years_fallback(self):
        """_default_years should return [2020] on missing temporal."""
        from esfex.plugins.availability_generator.cli_commands import (
            _default_years,
        )

        cfg = SimpleNamespace()
        years = _default_years(cfg)
        assert years == [2020]


# ===================================================================
# TestPluginStructure
# ===================================================================


class TestPluginStructure:
    """Tests for plugin.json and factory."""

    def test_plugin_json_valid(self):
        """plugin.json must have name, version, category."""
        pj = _PLUGIN_DIR / "plugin.json"
        assert pj.exists()
        data = json.loads(pj.read_text())
        assert data["name"] == "availability_generator"
        assert "version" in data
        assert data["category"] == "data"

    def test_create_plugin_returns_instance(self):
        """Factory function should return a ESFEXPlugin."""
        from esfex.plugins.availability_generator import create_plugin

        ctx = SimpleNamespace(
            plugin_dir=_PLUGIN_DIR,
            metadata={"name": "availability_generator", "version": "1.0.0"},
        )
        plugin = create_plugin(ctx)
        assert plugin is not None

    def test_get_cli_commands_returns_list(self):
        """get_cli_commands should return a list with the Typer app."""
        from esfex.plugins.availability_generator import create_plugin

        ctx = SimpleNamespace(
            plugin_dir=_PLUGIN_DIR,
            metadata={"name": "availability_generator", "version": "1.0.0"},
        )
        plugin = create_plugin(ctx)
        cmds = plugin.get_cli_commands()
        assert isinstance(cmds, list)
        assert len(cmds) >= 1


# ===================================================================
# TestTranslations
# ===================================================================


class TestTranslations:
    """Verify that translation files contain availability_generator keys."""

    _REQUIRED_KEYS = [
        "menu_action",
        "dialog_title",
        "system",
        "generators",
        "gen_key",
        "fuel",
        "profile_type",
        "node",
        "nodes",
        "data_source",
        "years",
        "solar_params",
        "efficiency",
        "tilt",
        "azimuth",
        "tracking",
        "wind_params",
        "turbine",
        "hub_height",
        "generate",
        "close",
        "done_title",
        "done_message",
    ]

    def test_en_has_availability_keys(self):
        """English translations must have all availability_generator keys."""
        en = json.loads((_TRANSLATIONS_DIR / "en.json").read_text())
        section = en.get("availability_generator", {})
        for key in self._REQUIRED_KEYS:
            assert key in section, f"Missing en.json key: availability_generator.{key}"

    def test_es_has_availability_keys(self):
        """Spanish translations must have all availability_generator keys."""
        es = json.loads((_TRANSLATIONS_DIR / "es.json").read_text())
        section = es.get("availability_generator", {})
        for key in self._REQUIRED_KEYS:
            assert key in section, f"Missing es.json key: availability_generator.{key}"


# ===================================================================
# TestGridBuilderHookDedup — co-located weather queries collapse + parallelize
# ===================================================================


class TestGridBuilderHookDedup:
    """The Grid Builder availability hook must query the weather backend once
    per distinct location, not once per generator (the Japan-scale bottleneck)."""

    @staticmethod
    def _gen(lat, lng, fuel="Wind", rated=10.0):
        return SimpleNamespace(
            latitude=lat, longitude=lng, fuel=fuel,
            rated_power=rated, availability_file=None,
        )

    def test_colocated_generators_share_one_weather_fetch(self):
        from esfex.plugins.availability_generator import grid_builder_hook as gbh

        # Six wind units: three pairs of co-located turbines (within one cell).
        gens = {
            "w0": self._gen(35.00, 139.00),
            "w1": self._gen(35.001, 139.001),  # same ~11 km cell as w0
            "w2": self._gen(40.00, 141.00),
            "w3": self._gen(40.002, 141.002),  # same cell as w2
            "w4": self._gen(33.00, 130.00),
            "w5": self._gen(33.0, 130.0),       # identical coords to w4
        }
        state = SimpleNamespace(generators=gens)

        calls = {"n": 0}

        def fake_wind(lat, lng, year, source, rated_power_mw=1.0):
            calls["n"] += 1
            return np.full(8760, 0.4)

        with tempfile.TemporaryDirectory() as tmp, \
                patch("windrex.compute_wind_hourly_cf", fake_wind, create=True):
            written = gbh.generate_for_grid_build(
                state, Path(tmp), use_weather_data=True)

            # 6 generators, 3 distinct cells → exactly 3 backend queries.
            assert calls["n"] == 3
            # Every generator still gets its own CSV + availability_file.
            assert len(written) == 6
            for gid, gen in gens.items():
                assert gen.availability_file is not None
                assert Path(written[gid]).exists()

    def test_failed_fetch_falls_back_to_flat_profile(self):
        from esfex.plugins.availability_generator import grid_builder_hook as gbh

        gens = {"w0": self._gen(35.0, 139.0)}
        state = SimpleNamespace(generators=gens)

        def boom(*a, **k):
            raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as tmp, \
                patch("windrex.compute_wind_hourly_cf", boom, create=True):
            written = gbh.generate_for_grid_build(
                state, Path(tmp), use_weather_data=True)

            # The unit still gets a (flat) profile, not a crashed build.
            data = np.loadtxt(written["w0"], delimiter=",")
            assert data.shape == (8760,)
            assert np.allclose(data, 0.32)
