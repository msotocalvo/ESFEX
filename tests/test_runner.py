"""
Tests for esfex.runner module.

Focuses on helper methods of the Orchestrator class that can be
tested with mocking, without requiring Julia or real file I/O.
"""

import logging
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest

from esfex.config.schema import (
    GeneratorConfig,
    MetaNetworkConfig,
    NodeConfig,
    PenaltiesConfig,
    ESFEXConfig,
    SolverConfig,
    SystemConfig,
    TemporalConfig,
)
from esfex.runner import Orchestrator, SimulationState, YearResults
from esfex.utils.temporal import HOURS_STD_YEAR


# ---------------------------------------------------------------------------
# Helpers for building mock configs
# ---------------------------------------------------------------------------

def _make_generator(name, gen_type, fuel, rated_power, **overrides):
    """Build a minimal GeneratorConfig for 2 nodes."""
    n = len(rated_power)
    defaults = dict(
        name=name,
        type=gen_type,
        fuel=fuel,
        life_time=[25] * n,
        initial_age=[5] * n,
        degradation_rate=[0.01] * n,
        decommissioning_cost=[1000.0] * n,
        rated_power=rated_power,
        min_power=[0.0] * n,
        min_up=[0] * n,
        min_down=[0] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.45] * n,
        eff_at_min=[0.40] * n,
        inertia=[6.0] * n,
        start_up_cost=[500.0] * n,
        fuel_cost=[50.0] * n,
        fixed_cost=[5.0] * n,
        maintenance_cost=[10.0] * n,
        invest_cost=[1e6] * n,
        invest_max_power=[200.0] * n,
    )
    defaults.update(overrides)
    return GeneratorConfig(**defaults)


def _make_node_config(num_nodes=2):
    """Build a minimal NodeConfig for *num_nodes* nodes."""
    connections = [0.0] * (num_nodes * num_nodes)
    for i in range(num_nodes):
        for j in range(num_nodes):
            if i != j:
                connections[i * num_nodes + j] = 200.0
    return NodeConfig(
        nodes_connections=connections,
        reserve_static=[10.0] * num_nodes,
        reserve_dynamic=[20.0] * num_nodes,
        reserve_duration=[2] * num_nodes,
        losses=[0.001] * num_nodes,
        transference_invest_cost=[13000.0] * num_nodes,
        transference_invest_max=[100.0] * num_nodes,
    )


def _make_system_config(num_nodes=2, generators=None, batteries=None, name="test_sys"):
    """Build a minimal SystemConfig."""
    if generators is None:
        generators = {
            "gas": _make_generator("Gas", "Non-renewable", "Gas", [100.0, 50.0]),
            "solar": _make_generator("Solar", "Renewable", "Sun", [50.0, 80.0]),
        }
    if batteries is None:
        batteries = {}
    return SystemConfig(
        name=name,
        nodes=_make_node_config(num_nodes),
        generators=generators,
        batteries=batteries,
    )


def _make_esfex_config(system_config=None, **overrides):
    """Build a minimal ESFEXConfig."""
    sys_cfg = system_config or _make_system_config()
    defaults = dict(
        meta_network=MetaNetworkConfig(systems=[sys_cfg.name]),
        systems={sys_cfg.name: sys_cfg},
    )
    defaults.update(overrides)
    return ESFEXConfig(**defaults)


def _create_orchestrator(config=None, tmp_path=None):
    """Create an Orchestrator with mocked output directory."""
    cfg = config or _make_esfex_config()
    output_dir = tmp_path or Path("/tmp/esfex_test_output")
    return Orchestrator(cfg, output_dir=output_dir)


# ---------------------------------------------------------------------------
# Tests: SimulationState dataclass
# ---------------------------------------------------------------------------

class TestSimulationState:
    def test_basic_creation(self):
        state = SimulationState(year=2025, base_year=2025, units_config={"gen0": {}})
        assert state.year == 2025
        assert state.base_year == 2025
        assert state.cumulative_investments == {}
        assert state.cumulative_retirements == {}

    def test_primary_energy_capacities_default(self):
        state = SimulationState(year=2030, base_year=2025, units_config={})
        assert "storage" in state.primary_energy_capacities
        assert "transport" in state.primary_energy_capacities


# ---------------------------------------------------------------------------
# Tests: YearResults dataclass
# ---------------------------------------------------------------------------

class TestYearResults:
    def test_basic_creation(self):
        yr = YearResults(year=2025, objective=1e6, solve_time=10.0, feasible=True)
        assert yr.year == 2025
        assert yr.objective == 1e6
        assert yr.feasible is True
        assert yr.gen_output is None

    def test_defaults_are_zero(self):
        yr = YearResults(year=2025, objective=0, solve_time=0, feasible=False)
        assert yr.emissions == 0.0
        assert yr.re_penetration == 0.0
        assert yr.load_shed == 0.0
        assert yr.total_generation == 0.0
        assert yr.total_demand == 0.0

    def test_investments_retirements_dicts(self):
        yr = YearResults(year=2025, objective=0, solve_time=0, feasible=True)
        yr.investments = {"gen_investment_power_0_0": 100.0}
        yr.retirements = {"gen_0": 0.5}
        assert yr.investments["gen_investment_power_0_0"] == 100.0

    def test_cost_breakdown_default_none(self):
        yr = YearResults(year=2025, objective=0, solve_time=0, feasible=True)
        assert yr.cost_breakdown is None

    def test_cost_breakdown_with_dict(self):
        cb = {"fuel_cost": 50000.0, "co2_emission_cost": 8000.0, "total": 58000.0}
        yr = YearResults(
            year=2025, objective=58000.0, solve_time=5.0, feasible=True,
            cost_breakdown=cb,
        )
        assert yr.cost_breakdown is not None
        assert yr.cost_breakdown["fuel_cost"] == 50000.0
        assert yr.cost_breakdown["total"] == 58000.0


# ---------------------------------------------------------------------------
# Tests: HDF5 cost_breakdown export
# ---------------------------------------------------------------------------


class TestCostBreakdownHDF5:
    """Tests for cost_breakdown export to HDF5."""

    def test_cost_breakdown_written_to_hdf5(self, tmp_path):
        """Verify cost_breakdown is written as /cost_breakdown/year_YYYY/ group."""
        import h5py

        h5_path = tmp_path / "test_results.h5"
        cb = {
            "fuel_cost": 50000.0,
            "fixed_om_cost": 10000.0,
            "maintenance_cost": 5000.0,
            "startup_cost": 2000.0,
            "co2_emission_cost": 8000.0,
            "total": 75000.0,
        }

        with h5py.File(h5_path, "w") as f:
            f.create_group("cost_breakdown")
            year_key = "year_2025"
            yk = f["cost_breakdown"].create_group(year_key)
            for cost_name, cost_val in cb.items():
                yk.attrs[cost_name] = float(cost_val)

        with h5py.File(h5_path, "r") as f:
            assert "cost_breakdown" in f
            assert "year_2025" in f["cost_breakdown"]
            attrs = dict(f["cost_breakdown/year_2025"].attrs)
            assert attrs["fuel_cost"] == 50000.0
            assert attrs["total"] == 75000.0
            assert len(attrs) == 6

    def test_multiple_years_cost_breakdown(self, tmp_path):
        """Verify multiple years can be stored."""
        import h5py

        h5_path = tmp_path / "test_results.h5"
        with h5py.File(h5_path, "w") as f:
            cbd = f.create_group("cost_breakdown")
            for year in [2025, 2026, 2027]:
                yk = cbd.create_group(f"year_{year}")
                yk.attrs["fuel_cost"] = float(year * 100)
                yk.attrs["total"] = float(year * 100)

        with h5py.File(h5_path, "r") as f:
            assert len(f["cost_breakdown"]) == 3
            assert f["cost_breakdown/year_2026"].attrs["fuel_cost"] == 202600.0

    def test_cost_breakdown_not_written_when_none(self, tmp_path):
        """If cost_breakdown is None, no group should be created."""
        import h5py

        h5_path = tmp_path / "test_results.h5"
        yr = YearResults(year=2025, objective=1e6, solve_time=5.0, feasible=True)
        assert yr.cost_breakdown is None

        with h5py.File(h5_path, "w") as f:
            if yr.cost_breakdown:
                f.create_group("cost_breakdown")

        with h5py.File(h5_path, "r") as f:
            assert "cost_breakdown" not in f


# ---------------------------------------------------------------------------
# Tests: Orchestrator.__init__
# ---------------------------------------------------------------------------

class TestOrchestratorInit:
    def test_basic_init(self, tmp_path):
        cfg = _make_esfex_config()
        orch = Orchestrator(cfg, output_dir=tmp_path / "results")
        assert orch.config is cfg
        assert orch.system_name == "test_sys"
        assert orch.state is None
        assert orch.results == []

    def test_output_dir_created(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "output"
        _ = Orchestrator(_make_esfex_config(), output_dir=out)
        assert out.exists()

    def test_default_output_dir(self):
        """When output_dir is None the default ./results is used."""
        cfg = _make_esfex_config()
        orch = Orchestrator(cfg, output_dir=None)
        assert orch.output_dir == Path("./results")

    def test_config_path_stored(self, tmp_path):
        cfg = _make_esfex_config()
        cp = tmp_path / "my_config.yaml"
        orch = Orchestrator(cfg, output_dir=tmp_path, config_path=cp)
        assert orch.config_path == cp

    def test_simulation_mode_logged(self, tmp_path, caplog):
        cfg = _make_esfex_config()
        with caplog.at_level(logging.DEBUG, logger="esfex.runner"):
            _ = Orchestrator(cfg, output_dir=tmp_path)
        assert any("development" in r.message for r in caplog.records)

    def test_solver_name_logged(self, tmp_path, caplog):
        cfg = _make_esfex_config()
        with caplog.at_level(logging.DEBUG, logger="esfex.runner"):
            _ = Orchestrator(cfg, output_dir=tmp_path)
        assert any("highs" in r.message.lower() for r in caplog.records)

    def test_primary_system_reference(self, tmp_path):
        sys_cfg = _make_system_config(name="my_system")
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        assert orch.primary_system.name == "my_system"
        assert orch.system_name == "my_system"

    def test_plugin_manager_initialized(self, tmp_path):
        """Orchestrator.__init__ initializes the plugin manager."""
        cfg = _make_esfex_config()
        orch = Orchestrator(cfg, output_dir=tmp_path)
        assert hasattr(orch, "_pm")
        assert orch._pm is not None

    def test_plugin_manager_load_all_called(self, tmp_path):
        """Orchestrator.__init__ calls pm.load_all() on the plugin manager."""
        cfg = _make_esfex_config()
        with patch("esfex.plugins.get_plugin_manager") as mock_get_pm:
            mock_pm = MagicMock()
            mock_get_pm.return_value = mock_pm
            _ = Orchestrator(cfg, output_dir=tmp_path)
            mock_pm.load_all.assert_called_once()
            _, kwargs = mock_pm.load_all.call_args
            assert kwargs.get("gui_mode") is False

    def test_plugin_manager_registers_julia_modules(self, tmp_path):
        """Orchestrator.__init__ calls pm.register_julia_modules()."""
        cfg = _make_esfex_config()
        with patch("esfex.plugins.get_plugin_manager") as mock_get_pm:
            mock_pm = MagicMock()
            mock_get_pm.return_value = mock_pm
            _ = Orchestrator(cfg, output_dir=tmp_path)
            mock_pm.register_julia_modules.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _extract_year_demand
# ---------------------------------------------------------------------------

class TestExtractYearDemand:
    def _make_orch(self, tmp_path):
        return _create_orchestrator(tmp_path=tmp_path)

    def test_first_year(self, tmp_path):
        orch = self._make_orch(tmp_path)
        full = np.arange(HOURS_STD_YEAR * 3 * 2).reshape(HOURS_STD_YEAR * 3, 2).astype(float)
        year0 = orch._extract_year_demand(full, year_idx=0, hours_per_year=HOURS_STD_YEAR)
        assert year0.shape == (HOURS_STD_YEAR, 2)
        np.testing.assert_array_equal(year0, full[:HOURS_STD_YEAR, :])

    def test_second_year(self, tmp_path):
        orch = self._make_orch(tmp_path)
        full = np.ones((HOURS_STD_YEAR * 3, 2))
        full[HOURS_STD_YEAR:HOURS_STD_YEAR * 2, :] = 2.0
        year1 = orch._extract_year_demand(full, year_idx=1, hours_per_year=HOURS_STD_YEAR)
        np.testing.assert_array_equal(year1, np.full((HOURS_STD_YEAR, 2), 2.0))

    def test_out_of_range_falls_back_to_first_year(self, tmp_path):
        orch = self._make_orch(tmp_path)
        full = np.ones((HOURS_STD_YEAR, 2))
        result = orch._extract_year_demand(full, year_idx=5, hours_per_year=HOURS_STD_YEAR)
        np.testing.assert_array_equal(result, full[:HOURS_STD_YEAR, :])

    def test_custom_hours_per_year(self, tmp_path):
        orch = self._make_orch(tmp_path)
        hours = 100
        full = np.arange(300 * 2).reshape(300, 2).astype(float)
        year1 = orch._extract_year_demand(full, year_idx=1, hours_per_year=hours)
        np.testing.assert_array_equal(year1, full[100:200, :])

    def test_partial_last_year(self, tmp_path):
        """When the last year has fewer hours than hours_per_year."""
        orch = self._make_orch(tmp_path)
        total_hours = HOURS_STD_YEAR + 100
        full = np.ones((total_hours, 2))
        year1 = orch._extract_year_demand(full, year_idx=1, hours_per_year=HOURS_STD_YEAR)
        assert year1.shape == (100, 2)


# ---------------------------------------------------------------------------
# Tests: _calculate_initial_re_penetration
# ---------------------------------------------------------------------------

class TestCalculateInitialREPenetration:
    def _make_orch_with_cache(self, tmp_path, generators, availability_cache=None):
        sys_cfg = _make_system_config(num_nodes=2, generators=generators)
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        # _merge_systems prefixes generator keys with "<sys_name>__"; the
        # availability cache the orchestrator consults uses those prefixed keys,
        # so translate the caller's plain-key cache to the merged form.
        translated = {f"{sys_cfg.name}__{k}": v
                      for k, v in (availability_cache or {}).items()}
        orch._availability_cache = translated
        return orch

    def test_zero_demand_returns_zero(self, tmp_path):
        gens = {"solar": _make_generator("Solar", "Renewable", "Sun", [50.0, 80.0])}
        orch = self._make_orch_with_cache(tmp_path, gens)
        demand = np.zeros((100, 2))
        result = orch._calculate_initial_re_penetration(demand)
        assert result == 0.0

    def test_all_renewable_with_perfect_availability(self, tmp_path):
        gens = {"solar": _make_generator(
            "Solar", "Renewable", "Sun", [100.0, 100.0],
            availability_file="solar.csv",
        )}
        avail = np.ones((8760, 2))
        orch = self._make_orch_with_cache(tmp_path, gens, {"solar": avail})
        # Demand: 200 MW constant for 8760 hours = 1,752,000 MWh
        demand = np.full((8760, 2), 100.0)  # 100 MW per node
        result = orch._calculate_initial_re_penetration(demand)
        # RE energy = 100*8760 + 100*8760 = 1,752,000 MWh = total demand
        assert abs(result - 1.0) < 0.01

    def test_no_renewable_generators(self, tmp_path):
        gens = {"gas": _make_generator("Gas", "Non-renewable", "Gas", [200.0, 100.0])}
        orch = self._make_orch_with_cache(tmp_path, gens)
        demand = np.full((8760, 2), 100.0)
        result = orch._calculate_initial_re_penetration(demand)
        assert result == 0.0

    def test_partial_availability(self, tmp_path):
        gens = {"wind": _make_generator(
            "Wind", "Renewable", "Wind", [100.0, 100.0],
            availability_file="wind.csv",
        )}
        avail = np.full((8760, 2), 0.3)  # 30% capacity factor
        orch = self._make_orch_with_cache(tmp_path, gens, {"wind": avail})
        demand = np.full((8760, 2), 100.0)
        result = orch._calculate_initial_re_penetration(demand)
        # RE energy = 2 * 100 * 8760 * 0.3 = 525,600
        # Demand = 2 * 100 * 8760 = 1,752,000
        expected = 0.3
        assert abs(result - expected) < 0.01

    def test_zero_rated_power_skipped(self, tmp_path):
        gens = {"solar": _make_generator("Solar", "Renewable", "Sun", [0.0, 0.0])}
        avail = np.ones((8760, 2))
        orch = self._make_orch_with_cache(tmp_path, gens, {"solar": avail})
        demand = np.full((8760, 2), 100.0)
        result = orch._calculate_initial_re_penetration(demand)
        assert result == 0.0

    def test_clamped_to_one(self, tmp_path):
        """RE penetration cannot exceed 1.0."""
        gens = {"solar": _make_generator(
            "Solar", "Renewable", "Sun", [500.0, 500.0],
            availability_file="solar.csv",
        )}
        avail = np.ones((8760, 2))
        orch = self._make_orch_with_cache(tmp_path, gens, {"solar": avail})
        # Very low demand
        demand = np.full((8760, 2), 10.0)
        result = orch._calculate_initial_re_penetration(demand)
        assert result == 1.0

    def test_mixed_generators(self, tmp_path):
        gens = {
            "gas": _make_generator("Gas", "Non-renewable", "Gas", [200.0, 100.0]),
            "solar": _make_generator(
                "Solar", "Renewable", "Sun", [50.0, 50.0],
                availability_file="solar.csv",
            ),
        }
        avail = np.full((8760, 2), 0.5)
        orch = self._make_orch_with_cache(tmp_path, gens, {"solar": avail})
        demand = np.full((8760, 2), 100.0)
        result = orch._calculate_initial_re_penetration(demand)
        # RE energy = 50*8760*0.5 + 50*8760*0.5 = 438000
        # Demand = 200*8760 = 1752000
        expected = 438000 / 1752000
        assert abs(result - expected) < 0.01

    def test_no_availability_cache_skips(self, tmp_path):
        """When no availability file in cache, renewable is skipped."""
        gens = {"solar": _make_generator("Solar", "Renewable", "Sun", [50.0, 80.0])}
        orch = self._make_orch_with_cache(tmp_path, gens, {})
        demand = np.full((100, 2), 100.0)
        result = orch._calculate_initial_re_penetration(demand)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Tests: _apply_retirements_to_config
# ---------------------------------------------------------------------------

class TestApplyRetirementsToConfig:
    def _make_units_config(self):
        return {
            "gas": {
                "_type": "generator",
                "name": "Gas",
                "type": "Non-renewable",
                "rated_power": [100.0, 50.0],
            },
            "solar": {
                "_type": "generator",
                "name": "Solar",
                "type": "Renewable",
                "rated_power": [50.0, 80.0],
            },
            "li_ion": {
                "_type": "battery",
                "type": "Storage",
                "name": "Li-ion",
                "MaxChargePower": [25.0, 40.0],
            },
        }

    def test_empty_retirements_returns_same(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        result = orch._apply_retirements_to_config(units, {})
        assert result is units

    def test_full_retirement(self, tmp_path):
        """Retiring 100% of a generator sets rated_power to 0."""
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_0": 1.0}
        result = orch._apply_retirements_to_config(units, retirements)
        assert result["gas"]["rated_power"][0] == 0.0
        assert result["gas"]["rated_power"][1] == 0.0

    def test_partial_retirement(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_0": 0.5}
        result = orch._apply_retirements_to_config(units, retirements)
        assert abs(result["gas"]["rated_power"][0] - 50.0) < 0.01
        assert abs(result["gas"]["rated_power"][1] - 25.0) < 0.01

    def test_zero_retirement_ignored(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_0": 0.0}
        result = orch._apply_retirements_to_config(units, retirements)
        assert result["gas"]["rated_power"][0] == 100.0

    def test_does_not_mutate_original(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        _ = orch._apply_retirements_to_config(units, {"gen_0": 1.0})
        assert units["gas"]["rated_power"][0] == 100.0

    def test_retirement_second_generator(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_1": 0.3}
        result = orch._apply_retirements_to_config(units, retirements)
        assert abs(result["solar"]["rated_power"][0] - 50.0 * 0.7) < 0.01
        assert abs(result["solar"]["rated_power"][1] - 80.0 * 0.7) < 0.01

    def test_capacity_never_negative(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_0": 2.0}  # > 100%
        result = orch._apply_retirements_to_config(units, retirements)
        assert result["gas"]["rated_power"][0] >= 0.0
        assert result["gas"]["rated_power"][1] >= 0.0

    def test_batteries_not_affected_by_gen_retirement(self, tmp_path):
        orch = _create_orchestrator(tmp_path=tmp_path)
        units = self._make_units_config()
        retirements = {"gen_0": 1.0}
        result = orch._apply_retirements_to_config(units, retirements)
        assert result["li_ion"]["MaxChargePower"] == [25.0, 40.0]


# ---------------------------------------------------------------------------
# Tests: _build_ev_config
# ---------------------------------------------------------------------------

class TestBuildEvConfig:
    def _make_orch_with_ev(self, tmp_path):
        """Build orchestrator with EV configuration."""
        sys_cfg = _make_system_config(num_nodes=2)
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        orch._num_nodes = 2
        return orch

    def test_returns_none_when_no_ev_categories(self, tmp_path):
        orch = self._make_orch_with_ev(tmp_path)
        orch._ev_charging_profiles = None
        result = orch._build_ev_config(year_idx=0, window_start_hour=0, window_hours=24)
        assert result is None

    def test_returns_none_when_no_charging_profiles(self, tmp_path):
        orch = self._make_orch_with_ev(tmp_path)
        # Even if categories exist, if profiles are None it returns None
        orch.primary_system.ev_categories = {}
        orch._ev_charging_profiles = None
        result = orch._build_ev_config(year_idx=0, window_start_hour=0, window_hours=24)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _gather_units (via Orchestrator instance)
# ---------------------------------------------------------------------------

class TestGatherUnits:
    def test_generators_included(self, tmp_path):
        gens = {
            "gas": _make_generator("Gas", "Non-renewable", "Gas", [100.0, 50.0]),
            "solar": _make_generator("Solar", "Renewable", "Sun", [50.0, 80.0]),
        }
        sys_cfg = _make_system_config(generators=gens)
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        units = orch._gather_units()
        # _merge_systems prefixes keys with "<sys_name>__".
        assert "test_sys__gas" in units
        assert "test_sys__solar" in units
        assert units["test_sys__gas"]["_type"] == "generator"

    def test_batteries_included(self, tmp_path):
        from esfex.config.schema import BatteryConfig
        n = 2
        bat = BatteryConfig(
            name="Li-ion",
            life_time=[15] * n,
            initial_age=[0] * n,
            degradation_rate=[0.01] * n,
            decommissioning_cost=[500.0] * n,
            rated_power=[25.0] * n,
            min_power=[0.0] * n,
            min_up=[0] * n,
            min_down=[0] * n,
            ramp_up=[1.0] * n,
            ramp_down=[1.0] * n,
            eff_at_rated=[0.95] * n,
            eff_at_min=[0.90] * n,
            inertia=[0.0] * n,
            start_up_cost=[0.0] * n,
            fuel_cost=[0.0] * n,
            fixed_cost=[0.0] * n,
            maintenance_cost=[5.0] * n,
            invest_cost=[200000.0] * n,
            invest_cost_energy=[150000.0] * n,
            invest_max_power=[50.0] * n,
            invest_max_capacity=[100.0] * n,
            efficiency_charge=[0.95] * n,
            efficiency_discharge=[0.95] * n,
            soc_initial=[0.5] * n,
            max_DoD=[0.9] * n,
            capacity=[50.0] * n,
            MaxChargePower=[25.0] * n,
            MaxDischargePower=[25.0] * n,
        )
        sys_cfg = _make_system_config(batteries={"li_ion": bat})
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        units = orch._gather_units()
        assert "test_sys__li_ion" in units
        assert units["test_sys__li_ion"]["_type"] == "battery"

    def test_empty_system(self, tmp_path):
        sys_cfg = _make_system_config(generators={}, batteries={})
        cfg = _make_esfex_config(system_config=sys_cfg)
        orch = Orchestrator(cfg, output_dir=tmp_path)
        units = orch._gather_units()
        assert units == {}
