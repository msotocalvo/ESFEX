"""
Comprehensive pytest tests for the Pydantic configuration schema.

Tests cover all major Pydantic models defined in
``esfex.config.schema``, including valid construction, default
values, boundary conditions, Literal type enforcement, model validators,
and property helpers.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from esfex.config.schema import (
    ACPowerFlowConfig,
    BatteryConfig,
    BusConfig,
    CO2BudgetConfig,
    CostCurveBlock,
    CostCurveConfig,
    CriticalityPenalties,
    ElectrolyzerConfig,
    FuelConfig,
    GeoCoordinate,
    GeneratorConfig,
    MasterProblemConfig,
    MetaNetworkConfig,
    MGAConfig,
    N1SecurityConfig,
    NodeConfig,
    PenaltiesConfig,
    ESFEXConfig,
    ScenarioMultipliers,
    SolverConfig,
    StochasticScenarioConfig,
    SystemConfig,
    TemporalConfig,
    normalize_cost_curve,
)


# ---------------------------------------------------------------------------
# Helpers: minimal valid dicts for reuse
# ---------------------------------------------------------------------------

def _node_1() -> dict:
    """Minimal valid NodeConfig dict for a single node."""
    return {"nodes_connections": [0.0]}


def _node_2() -> dict:
    """Minimal valid NodeConfig dict for two nodes."""
    return {"nodes_connections": [0.0, 100.0, 100.0, 0.0]}


def _gen_1(name: str = "Solar", fuel: str = "Solar") -> dict:
    """Minimal valid GeneratorConfig dict for a single node."""
    return {
        "name": name,
        "type": "Renewable",
        "fuel": fuel,
        "life_time": [25],
        "initial_age": [0],
        "degradation_rate": [0.005],
        "decommissioning_cost": [10000],
        "rated_power": [100.0],
        "min_power": [0.0],
        "min_up": [0],
        "min_down": [0],
        "ramp_up": [1.0],
        "ramp_down": [1.0],
        "eff_at_rated": [1.0],
        "eff_at_min": [1.0],
        "inertia": [0.0],
        "start_up_cost": [0.0],
        "fuel_cost": [0.0],
        "fixed_cost": [5.0],
        "maintenance_cost": [2.0],
        "invest_cost": [800000],
        "invest_max_power": [500.0],
    }


def _bat_1(name: str = "LiIon") -> dict:
    """Minimal valid BatteryConfig dict for a single node."""
    return {
        "name": name,
        "life_time": [15],
        "initial_age": [0],
        "degradation_rate": [0.01],
        "decommissioning_cost": [5000],
        "rated_power": [50.0],
        "min_power": [0.0],
        "min_up": [0],
        "min_down": [0],
        "ramp_up": [1.0],
        "ramp_down": [1.0],
        "eff_at_rated": [0.95],
        "eff_at_min": [0.90],
        "inertia": [0.0],
        "start_up_cost": [0.0],
        "fuel_cost": [0.0],
        "fixed_cost": [3.0],
        "maintenance_cost": [1.0],
        "invest_cost": [300000],
        "invest_cost_energy": [150000],
        "invest_max_power": [200.0],
        "invest_max_capacity": [800.0],
        "efficiency_charge": [0.95],
        "efficiency_discharge": [0.95],
        "soc_initial": [0.5],
        "max_DoD": [0.9],
        "capacity": [200.0],
        "MaxChargePower": [50.0],
        "MaxDischargePower": [50.0],
    }


def _system_minimal(name: str = "TestSys") -> dict:
    """Minimal valid SystemConfig dict (1 node, 1 generator)."""
    return {
        "name": name,
        "nodes": _node_1(),
        "generators": {"gen_0": _gen_1()},
    }


def _esfex_minimal() -> dict:
    """Minimal valid ESFEXConfig dict."""
    return {
        "meta_network": {"systems": ["TestSys"]},
        "systems": {"TestSys": _system_minimal()},
    }


# ========================================================================
# FuelConfig
# ========================================================================


class TestFuelConfig:
    def test_valid_construction(self):
        f = FuelConfig(name="Diesel", emission_factor=0.25, price_base=60)
        assert f.name == "Diesel"
        assert f.emission_factor == 0.25
        assert f.price_base == 60

    def test_defaults(self):
        f = FuelConfig(name="Wind", emission_factor=0, price_base=0)
        assert f.unit is None
        assert f.energy_content is None
        assert f.price_growth_rate == 0

    def test_emission_factor_zero(self):
        f = FuelConfig(name="Solar", emission_factor=0, price_base=0)
        assert f.emission_factor == 0

    def test_emission_factor_negative_raises(self):
        with pytest.raises(ValidationError):
            FuelConfig(name="Bad", emission_factor=-0.1, price_base=0)

    def test_price_base_negative_raises(self):
        with pytest.raises(ValidationError):
            FuelConfig(name="Bad", emission_factor=0, price_base=-1)

    def test_energy_content_negative_raises(self):
        with pytest.raises(ValidationError):
            FuelConfig(name="Bad", emission_factor=0, price_base=0, energy_content=-5)

    def test_optional_fields(self):
        f = FuelConfig(
            name="LNG",
            unit="MMBtu",
            emission_factor=0.05,
            energy_content=3.41,
            price_base=10,
            price_growth_rate=0.02,
        )
        assert f.unit == "MMBtu"
        assert f.energy_content == 3.41
        assert f.price_growth_rate == 0.02


# ========================================================================
# PenaltiesConfig
# ========================================================================


class TestPenaltiesConfig:
    def test_defaults(self):
        p = PenaltiesConfig()
        assert p.loss_of_load == 10e6
        assert p.max_curtailment_ratio == 0.05
        assert p.co2_cost == 10
        assert p.soc_violation == 1e6

    def test_max_curtailment_ratio_zero(self):
        p = PenaltiesConfig(max_curtailment_ratio=0)
        assert p.max_curtailment_ratio == 0

    def test_max_curtailment_ratio_one(self):
        p = PenaltiesConfig(max_curtailment_ratio=1)
        assert p.max_curtailment_ratio == 1

    def test_max_curtailment_ratio_below_zero_raises(self):
        with pytest.raises(ValidationError):
            PenaltiesConfig(max_curtailment_ratio=-0.01)

    def test_max_curtailment_ratio_above_one_raises(self):
        with pytest.raises(ValidationError):
            PenaltiesConfig(max_curtailment_ratio=1.01)

    def test_custom_penalties(self):
        p = PenaltiesConfig(loss_of_load=5e6, co2_cost=50, ev_loss=20)
        assert p.loss_of_load == 5e6
        assert p.co2_cost == 50
        assert p.ev_loss == 20


# ========================================================================
# CO2BudgetConfig
# ========================================================================


class TestCO2BudgetConfig:
    def test_defaults(self):
        c = CO2BudgetConfig()
        assert c.enabled is True
        assert c.annual_budget == 1e6

    def test_annual_budget_zero(self):
        c = CO2BudgetConfig(annual_budget=0)
        assert c.annual_budget == 0

    def test_annual_budget_negative_raises(self):
        with pytest.raises(ValidationError):
            CO2BudgetConfig(annual_budget=-100)

    def test_disabled(self):
        c = CO2BudgetConfig(enabled=False)
        assert c.enabled is False


# ========================================================================
# CriticalityPenalties
# ========================================================================


class TestCriticalityPenalties:
    def test_defaults(self):
        cp = CriticalityPenalties()
        assert cp.critical == 1000
        assert cp.high == 100
        assert cp.medium == 10
        assert cp.low == 1

    def test_custom(self):
        cp = CriticalityPenalties(critical=2000, high=200, medium=20, low=2)
        assert cp.critical == 2000
        assert cp.low == 2


# ========================================================================
# GeoCoordinate
# ========================================================================


class TestGeoCoordinate:
    def test_valid(self):
        g = GeoCoordinate(latitude=23.1, longitude=-82.3)
        assert g.latitude == 23.1
        assert g.longitude == -82.3

    def test_defaults(self):
        g = GeoCoordinate(latitude=0, longitude=0)
        assert g.label is None
        assert g.radius_km == 20.0

    def test_lat_boundary_min(self):
        g = GeoCoordinate(latitude=-90, longitude=0)
        assert g.latitude == -90

    def test_lat_boundary_max(self):
        g = GeoCoordinate(latitude=90, longitude=0)
        assert g.latitude == 90

    def test_lat_below_min_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=-91, longitude=0)

    def test_lat_above_max_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=91, longitude=0)

    def test_lng_boundary_min(self):
        g = GeoCoordinate(latitude=0, longitude=-180)
        assert g.longitude == -180

    def test_lng_boundary_max(self):
        g = GeoCoordinate(latitude=0, longitude=180)
        assert g.longitude == 180

    def test_lng_below_min_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=0, longitude=-181)

    def test_lng_above_max_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=0, longitude=181)

    def test_radius_km_lower_bound(self):
        g = GeoCoordinate(latitude=0, longitude=0, radius_km=0.1)
        assert g.radius_km == 0.1

    def test_radius_km_upper_bound(self):
        g = GeoCoordinate(latitude=0, longitude=0, radius_km=500)
        assert g.radius_km == 500

    def test_radius_km_below_min_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=0, longitude=0, radius_km=0.05)

    def test_radius_km_above_max_raises(self):
        with pytest.raises(ValidationError):
            GeoCoordinate(latitude=0, longitude=0, radius_km=501)

    def test_label(self):
        g = GeoCoordinate(latitude=10, longitude=20, label="Havana")
        assert g.label == "Havana"


# ========================================================================
# BusConfig
# ========================================================================


class TestBusConfig:
    def test_defaults(self):
        b = BusConfig()
        assert b.bus_id is None
        assert b.name == ""
        assert b.parent_node == 0
        assert b.voltage_kv == 220.0
        assert b.frequency_hz == 50.0
        assert b.current_type == "AC"
        assert b.bus_type == "PQ"
        assert b.demand_fraction == 1.0

    def test_valid_current_type_ac(self):
        b = BusConfig(current_type="AC")
        assert b.current_type == "AC"

    def test_valid_current_type_dc(self):
        b = BusConfig(current_type="DC")
        assert b.current_type == "DC"

    def test_invalid_current_type_raises(self):
        with pytest.raises(ValidationError):
            BusConfig(current_type="HVAC")

    def test_valid_bus_type_pq(self):
        b = BusConfig(bus_type="PQ")
        assert b.bus_type == "PQ"

    def test_valid_bus_type_pv(self):
        b = BusConfig(bus_type="PV")
        assert b.bus_type == "PV"

    def test_valid_bus_type_slack(self):
        b = BusConfig(bus_type="slack")
        assert b.bus_type == "slack"

    def test_invalid_bus_type_raises(self):
        with pytest.raises(ValidationError):
            BusConfig(bus_type="reference")

    def test_parent_node_negative_raises(self):
        with pytest.raises(ValidationError):
            BusConfig(parent_node=-1)


# ========================================================================
# GeneratorConfig
# ========================================================================


class TestGeneratorConfig:
    def test_valid_construction(self):
        g = GeneratorConfig(**_gen_1())
        assert g.name == "Solar"
        assert g.type == "Renewable"
        assert g.fuel == "Solar"
        assert g.rated_power == [100.0]

    def test_fuel_field(self):
        data = _gen_1(name="Diesel_Gen", fuel="Diesel")
        data["type"] = "Non-renewable"
        g = GeneratorConfig(**data)
        assert g.fuel == "Diesel"

    def test_defaults(self):
        g = GeneratorConfig(**_gen_1())
        assert g.technology is None
        assert g.reservable is True
        assert g.availability_file is None
        assert g.frequency_hz == 50.0
        assert g.current_type == "AC"

    def test_invalid_type_raises(self):
        data = _gen_1()
        data["type"] = "Nuclear"
        with pytest.raises(ValidationError):
            GeneratorConfig(**data)

    def test_current_type_dc(self):
        data = _gen_1()
        data["current_type"] = "DC"
        g = GeneratorConfig(**data)
        assert g.current_type == "DC"

    def test_current_type_ac_dc(self):
        data = _gen_1()
        data["current_type"] = "AC_DC"
        g = GeneratorConfig(**data)
        assert g.current_type == "AC_DC"

    def test_invalid_current_type_raises(self):
        data = _gen_1()
        data["current_type"] = "HVDC"
        with pytest.raises(ValidationError):
            GeneratorConfig(**data)

    def test_availability_alias(self):
        data = _gen_1()
        data["Availability"] = "solar_cf.csv"
        g = GeneratorConfig(**data)
        assert g.availability_file == "solar_cf.csv"

    def test_two_node_arrays(self):
        data = _gen_1()
        for key in [
            "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
            "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
            "eff_at_rated", "eff_at_min", "inertia", "start_up_cost", "fuel_cost",
            "fixed_cost", "maintenance_cost", "invest_cost", "invest_max_power",
        ]:
            val = data[key][0]
            data[key] = [val, val]
        g = GeneratorConfig(**data)
        assert len(g.rated_power) == 2


# ========================================================================
# BatteryConfig
# ========================================================================


class TestBatteryConfig:
    def test_valid_construction(self):
        b = BatteryConfig(**_bat_1())
        assert b.name == "LiIon"
        assert b.type == "Storage"
        assert b.fuel == "None"

    def test_defaults(self):
        b = BatteryConfig(**_bat_1())
        assert b.reservable is True
        assert b.spillage is True
        assert b.current_type == "DC"
        assert b.min_duration_hours is None
        assert b.max_duration_hours is None

    def test_efficiency_charge_in_list(self):
        b = BatteryConfig(**_bat_1())
        assert b.efficiency_charge == [0.95]
        assert b.efficiency_discharge == [0.95]

    def test_soc_initial(self):
        b = BatteryConfig(**_bat_1())
        assert b.soc_initial == [0.5]

    def test_invest_cost_energy(self):
        b = BatteryConfig(**_bat_1())
        assert b.invest_cost_energy == [150000]

    def test_throughput_degradation_cost_default_none(self):
        b = BatteryConfig(**_bat_1())
        assert b.throughput_degradation_cost is None

    def test_throughput_degradation_cost_set(self):
        data = _bat_1()
        data["throughput_degradation_cost"] = [0.5]
        b = BatteryConfig(**data)
        assert b.throughput_degradation_cost == [0.5]

    def test_availability_alias(self):
        data = _bat_1()
        data["Availability"] = "bat_avail.csv"
        b = BatteryConfig(**data)
        assert b.availability_file == "bat_avail.csv"


# ========================================================================
# ElectrolyzerConfig
# ========================================================================


class TestElectrolyzerConfig:
    def _elec_1(self) -> dict:
        return {
            "name": "PEM_1",
            "life_time": [20],
            "initial_age": [0],
            "degradation_rate": [0.01],
            "rated_power": [10.0],
            "min_power": [0.1],
            "ramp_up": [0.5],
            "ramp_down": [0.5],
            "eff_at_rated": [0.65],
            "eff_at_min": [0.55],
            "fixed_cost": [10.0],
            "variable_cost": [5.0],
            "invest_cost": [1500000],
            "invest_max_power": [50.0],
        }

    def test_valid_construction(self):
        e = ElectrolyzerConfig(**self._elec_1())
        assert e.name == "PEM_1"
        assert e.type == "Electrolyzer"
        assert e.fuel == "Hydrogen"
        assert e.technology == "PEM"

    def test_technology_pem(self):
        e = ElectrolyzerConfig(**self._elec_1())
        assert e.technology == "PEM"

    def test_technology_alkaline(self):
        data = self._elec_1()
        data["technology"] = "Alkaline"
        e = ElectrolyzerConfig(**data)
        assert e.technology == "Alkaline"

    def test_technology_soe(self):
        data = self._elec_1()
        data["technology"] = "SOE"
        e = ElectrolyzerConfig(**data)
        assert e.technology == "SOE"

    def test_invalid_technology_raises(self):
        data = self._elec_1()
        data["technology"] = "HTSE"
        with pytest.raises(ValidationError):
            ElectrolyzerConfig(**data)

    def test_defaults(self):
        e = ElectrolyzerConfig(**self._elec_1())
        assert e.energy_per_kg_h2 == 50.0
        assert e.water_cost == 0.001


# ========================================================================
# SolverConfig
# ========================================================================


class TestSolverConfig:
    def test_defaults(self):
        s = SolverConfig()
        assert s.name == "highs"
        assert s.threads == 4
        assert s.time_limit == 10800
        assert s.gap == 0.01
        assert s.verbose is False
        assert s.scale_constraints is True
        assert s.options == {}

    @pytest.mark.parametrize("solver_name", [
        "highs", "cbc", "glpk", "gurobi", "cplex", "scip", "xpress",
    ])
    def test_valid_solver_names(self, solver_name):
        s = SolverConfig(name=solver_name)
        assert s.name == solver_name

    def test_invalid_solver_name_raises(self):
        with pytest.raises(ValidationError):
            SolverConfig(name="mosek")

    def test_threads_minimum(self):
        s = SolverConfig(threads=1)
        assert s.threads == 1

    def test_threads_below_one_raises(self):
        with pytest.raises(ValidationError):
            SolverConfig(threads=0)

    def test_gap_zero(self):
        s = SolverConfig(gap=0)
        assert s.gap == 0

    def test_gap_one(self):
        s = SolverConfig(gap=1)
        assert s.gap == 1

    def test_gap_above_one_raises(self):
        with pytest.raises(ValidationError):
            SolverConfig(gap=1.01)

    def test_gap_negative_raises(self):
        with pytest.raises(ValidationError):
            SolverConfig(gap=-0.001)

    def test_time_limit_zero_unlimited(self):
        s = SolverConfig(time_limit=0)
        assert s.time_limit == 0

    def test_time_limit_negative_raises(self):
        with pytest.raises(ValidationError):
            SolverConfig(time_limit=-1)

    def test_options_dict(self):
        s = SolverConfig(options={"presolve": True, "lp_method": 1})
        assert s.options["presolve"] is True


# ========================================================================
# TemporalConfig
# ========================================================================


class TestTemporalConfig:
    def test_defaults(self):
        t = TemporalConfig()
        assert t.resolution_hours == 1
        assert t.rolling_horizon_hours == 48
        assert t.overlap_hours == 6
        assert t.investment_resolution == 8760
        assert t.primary_energy_resolution == 24
        assert t.use_rolling_horizon is True

    def test_resolution_hours_minimum(self):
        t = TemporalConfig(resolution_hours=1)
        assert t.resolution_hours == 1

    def test_resolution_hours_below_one_raises(self):
        with pytest.raises(ValidationError):
            TemporalConfig(resolution_hours=0)

    def test_rolling_horizon_hours_below_one_raises(self):
        with pytest.raises(ValidationError):
            TemporalConfig(rolling_horizon_hours=0)

    def test_overlap_hours_zero(self):
        t = TemporalConfig(overlap_hours=0)
        assert t.overlap_hours == 0

    def test_overlap_hours_negative_raises(self):
        with pytest.raises(ValidationError):
            TemporalConfig(overlap_hours=-1)


# ========================================================================
# N1SecurityConfig
# ========================================================================


class TestN1SecurityConfig:
    def test_defaults(self):
        n = N1SecurityConfig()
        assert n.enabled is False
        assert n.transmission_enabled is True
        assert n.generation_enabled is True
        assert n.transmission_reserve_factor == 0.70
        assert n.generation_reserve_percentage == 0.15

    def test_reserve_factor_zero(self):
        n = N1SecurityConfig(transmission_reserve_factor=0)
        assert n.transmission_reserve_factor == 0

    def test_reserve_factor_one(self):
        n = N1SecurityConfig(transmission_reserve_factor=1)
        assert n.transmission_reserve_factor == 1

    def test_reserve_factor_below_zero_raises(self):
        with pytest.raises(ValidationError):
            N1SecurityConfig(transmission_reserve_factor=-0.01)

    def test_reserve_factor_above_one_raises(self):
        with pytest.raises(ValidationError):
            N1SecurityConfig(transmission_reserve_factor=1.01)

    def test_generation_reserve_percentage_bounds(self):
        n = N1SecurityConfig(generation_reserve_percentage=0)
        assert n.generation_reserve_percentage == 0
        n2 = N1SecurityConfig(generation_reserve_percentage=1)
        assert n2.generation_reserve_percentage == 1

    def test_generation_reserve_percentage_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            N1SecurityConfig(generation_reserve_percentage=1.1)

    def test_generation_reserve_type_largest_unit(self):
        n = N1SecurityConfig(generation_reserve_type="largest_unit")
        assert n.generation_reserve_type == "largest_unit"

    def test_generation_reserve_type_percentage(self):
        n = N1SecurityConfig(generation_reserve_type="percentage")
        assert n.generation_reserve_type == "percentage"

    def test_generation_reserve_type_invalid_raises(self):
        with pytest.raises(ValidationError):
            N1SecurityConfig(generation_reserve_type="fixed")


# ========================================================================
# MGAConfig
# ========================================================================


class TestMGAConfig:
    def test_defaults(self):
        m = MGAConfig()
        assert m.enabled is False
        assert m.num_alternatives == 10
        assert m.slack_fraction == 0.05
        assert m.investment_threshold == 0.1

    def test_num_alternatives_min(self):
        m = MGAConfig(num_alternatives=1)
        assert m.num_alternatives == 1

    def test_num_alternatives_max(self):
        m = MGAConfig(num_alternatives=100)
        assert m.num_alternatives == 100

    def test_num_alternatives_below_min_raises(self):
        with pytest.raises(ValidationError):
            MGAConfig(num_alternatives=0)

    def test_num_alternatives_above_max_raises(self):
        with pytest.raises(ValidationError):
            MGAConfig(num_alternatives=101)

    def test_slack_fraction_zero(self):
        m = MGAConfig(slack_fraction=0.0)
        assert m.slack_fraction == 0.0

    def test_slack_fraction_max(self):
        m = MGAConfig(slack_fraction=0.5)
        assert m.slack_fraction == 0.5

    def test_slack_fraction_below_zero_raises(self):
        with pytest.raises(ValidationError):
            MGAConfig(slack_fraction=-0.01)

    def test_slack_fraction_above_max_raises(self):
        with pytest.raises(ValidationError):
            MGAConfig(slack_fraction=0.51)


# ========================================================================
# ACPowerFlowConfig
# ========================================================================


class TestACPowerFlowConfig:
    """ACPowerFlowConfig defaults and validation."""

    def test_defaults(self):
        ac = ACPowerFlowConfig()
        assert ac.enabled is False
        assert ac.base_mva == 100.0
        assert ac.voltage_min_pu == 0.90
        assert ac.voltage_max_pu == 1.10
        assert ac.default_power_factor == 0.85
        assert ac.load_power_factor == 0.9
        assert ac.q_slack_penalty == 100.0
        assert ac.min_reactance_pu == 0.01
        assert ac.tap_ratio_min == 0.5
        assert ac.tap_ratio_max == 2.0
        assert ac.q_min_ratio == 0.5

    def test_custom_values(self):
        ac = ACPowerFlowConfig(
            base_mva=200.0,
            voltage_min_pu=0.95,
            voltage_max_pu=1.05,
            load_power_factor=0.85,
            q_slack_penalty=50.0,
            min_reactance_pu=0.005,
            tap_ratio_min=0.3,
            tap_ratio_max=3.0,
            q_min_ratio=0.3,
        )
        assert ac.base_mva == 200.0
        assert ac.voltage_min_pu == 0.95
        assert ac.q_slack_penalty == 50.0

    def test_voltage_min_boundary(self):
        with pytest.raises(ValidationError):
            ACPowerFlowConfig(voltage_min_pu=0.0)  # must be > 0

    def test_voltage_max_boundary(self):
        with pytest.raises(ValidationError):
            ACPowerFlowConfig(voltage_max_pu=0.9)  # must be >= 1.0

    def test_power_factor_boundary(self):
        with pytest.raises(ValidationError):
            ACPowerFlowConfig(load_power_factor=1.5)  # must be <= 1.0
        with pytest.raises(ValidationError):
            ACPowerFlowConfig(default_power_factor=0.0)  # must be > 0


# ========================================================================
# MasterProblemConfig
# ========================================================================


class TestMasterProblemConfig:
    def test_defaults(self):
        m = MasterProblemConfig()
        assert m.stochastic is False
        assert m.representative_days == 5
        assert m.min_day_separation == 5
        assert m.use_tsam is False
        assert m.tsam_num_periods == 10
        assert m.tsam_method == "kmedoids"
        assert m.tsam_inter_period_linking is True

    def test_stochastic_flag(self):
        m = MasterProblemConfig(stochastic=True)
        assert m.stochastic is True

    def test_representative_days_minimum(self):
        m = MasterProblemConfig(representative_days=1)
        assert m.representative_days == 1

    def test_representative_days_below_one_raises(self):
        with pytest.raises(ValidationError):
            MasterProblemConfig(representative_days=0)

    def test_tsam_num_periods_min(self):
        m = MasterProblemConfig(tsam_num_periods=2)
        assert m.tsam_num_periods == 2

    def test_tsam_num_periods_max(self):
        m = MasterProblemConfig(tsam_num_periods=365)
        assert m.tsam_num_periods == 365

    def test_tsam_num_periods_below_min_raises(self):
        with pytest.raises(ValidationError):
            MasterProblemConfig(tsam_num_periods=1)

    def test_tsam_num_periods_above_max_raises(self):
        with pytest.raises(ValidationError):
            MasterProblemConfig(tsam_num_periods=366)

    def test_tsam_method_kmedoids(self):
        m = MasterProblemConfig(tsam_method="kmedoids")
        assert m.tsam_method == "kmedoids"

    def test_tsam_method_kmeans(self):
        m = MasterProblemConfig(tsam_method="kmeans")
        assert m.tsam_method == "kmeans"

    def test_tsam_method_invalid_raises(self):
        with pytest.raises(ValidationError):
            MasterProblemConfig(tsam_method="hierarchical")

    def test_mga_default(self):
        m = MasterProblemConfig()
        assert isinstance(m.mga, MGAConfig)
        assert m.mga.enabled is False


# ========================================================================
# ScenarioMultipliers
# ========================================================================


class TestScenarioMultipliers:
    def test_all_defaults_one(self):
        sm = ScenarioMultipliers()
        assert sm.invest_cost_renewables == 1.0
        assert sm.invest_cost_storage == 1.0
        assert sm.invest_cost_conventional == 1.0
        assert sm.invest_cost_transmission == 1.0
        assert sm.fuel_cost == 1.0
        assert sm.maintenance_cost == 1.0
        assert sm.discount_rate == 1.0
        assert sm.demand_growth == 1.0
        assert sm.fuel_price_growth == 1.0
        assert sm.carbon_price == 1.0

    def test_custom_multipliers(self):
        sm = ScenarioMultipliers(
            invest_cost_renewables=0.8,
            fuel_cost=1.5,
            carbon_price=2.0,
        )
        assert sm.invest_cost_renewables == 0.8
        assert sm.fuel_cost == 1.5
        assert sm.carbon_price == 2.0
        # Others remain default
        assert sm.invest_cost_storage == 1.0


# ========================================================================
# StochasticScenarioConfig
# ========================================================================


class TestStochasticScenarioConfig:
    def test_valid(self):
        s = StochasticScenarioConfig(name="base", probability=0.5)
        assert s.name == "base"
        assert s.probability == 0.5
        assert s.description == ""

    def test_probability_zero(self):
        s = StochasticScenarioConfig(name="zero", probability=0)
        assert s.probability == 0

    def test_probability_one(self):
        s = StochasticScenarioConfig(name="full", probability=1)
        assert s.probability == 1

    def test_probability_below_zero_raises(self):
        with pytest.raises(ValidationError):
            StochasticScenarioConfig(name="bad", probability=-0.1)

    def test_probability_above_one_raises(self):
        with pytest.raises(ValidationError):
            StochasticScenarioConfig(name="bad", probability=1.1)

    def test_multipliers_default(self):
        s = StochasticScenarioConfig(name="s1", probability=0.3)
        assert isinstance(s.multipliers, ScenarioMultipliers)
        assert s.multipliers.fuel_cost == 1.0

    def test_custom_multipliers(self):
        s = StochasticScenarioConfig(
            name="high_fuel",
            probability=0.25,
            multipliers=ScenarioMultipliers(fuel_cost=2.0),
        )
        assert s.multipliers.fuel_cost == 2.0


# ========================================================================
# SystemConfig
# ========================================================================


class TestSystemConfig:
    def test_minimal_valid(self):
        sc = SystemConfig(**_system_minimal())
        assert sc.name == "TestSys"
        assert sc.num_nodes == 1
        assert "gen_0" in sc.generators

    def test_defaults(self):
        sc = SystemConfig(**_system_minimal())
        assert sc.demand_path is None
        assert sc.demand_scale == 1.0
        assert sc.discount_rate == 0.05
        assert sc.target_re_penetration == 1.0
        assert sc.sim_rooftop is False
        assert sc.penalties.loss_of_load == 10e6

    def test_two_nodes_two_generators(self):
        data = {
            "name": "TwoNode",
            "nodes": _node_2(),
            "generators": {
                "gen_0": _gen_1(),
                "gen_1": _gen_1(name="Wind", fuel="Wind"),
            },
        }
        # Extend per-node arrays for 2 nodes
        for gkey in data["generators"]:
            g = data["generators"][gkey]
            for k, v in g.items():
                if isinstance(v, list):
                    g[k] = v * 2
        sc = SystemConfig(**data)
        assert sc.num_nodes == 2
        assert len(sc.generators) == 2

    def test_ensure_buses_auto_creates(self):
        sc = SystemConfig(**_system_minimal())
        assert len(sc.buses) == 1
        assert sc.buses[0].bus_id == "bus_0"
        assert sc.buses[0].parent_node == 0

    def test_ensure_buses_two_nodes(self):
        data = {
            "name": "TwoNode",
            "nodes": _node_2(),
            "generators": {"gen_0": _gen_1()},
        }
        # Extend per-node arrays
        g = data["generators"]["gen_0"]
        for k, v in g.items():
            if isinstance(v, list):
                g[k] = v * 2
        sc = SystemConfig(**data)
        assert len(sc.buses) == 2
        assert sc.buses[0].parent_node == 0
        assert sc.buses[1].parent_node == 1

    def test_ensure_buses_not_overridden_if_provided(self):
        data = _system_minimal()
        data["buses"] = [{"bus_id": "custom_bus", "name": "My Bus", "parent_node": 0}]
        sc = SystemConfig(**data)
        assert len(sc.buses) == 1
        assert sc.buses[0].bus_id == "custom_bus"

    def test_stochastic_probabilities_valid(self):
        data = _system_minimal()
        data["stochastic_scenarios"] = [
            {"name": "low", "probability": 0.3},
            {"name": "mid", "probability": 0.4},
            {"name": "high", "probability": 0.3},
        ]
        sc = SystemConfig(**data)
        assert len(sc.stochastic_scenarios) == 3

    def test_stochastic_probabilities_sum_not_one_raises(self):
        data = _system_minimal()
        data["stochastic_scenarios"] = [
            {"name": "low", "probability": 0.3},
            {"name": "mid", "probability": 0.3},
            {"name": "high", "probability": 0.3},
        ]
        with pytest.raises(ValidationError, match="must sum to 1.0"):
            SystemConfig(**data)

    def test_stochastic_probabilities_far_off_raises(self):
        data = _system_minimal()
        data["stochastic_scenarios"] = [
            {"name": "a", "probability": 0.5},
            {"name": "b", "probability": 0.1},
        ]
        with pytest.raises(ValidationError, match="must sum to 1.0"):
            SystemConfig(**data)

    def test_stochastic_probabilities_empty_passes(self):
        """No stochastic scenarios should not trigger validation."""
        data = _system_minimal()
        data["stochastic_scenarios"] = []
        sc = SystemConfig(**data)
        assert sc.stochastic_scenarios == []

    def test_stochastic_probabilities_within_tolerance(self):
        """Sum within 0.01 of 1.0 should pass."""
        data = _system_minimal()
        data["stochastic_scenarios"] = [
            {"name": "a", "probability": 0.505},
            {"name": "b", "probability": 0.5},
        ]
        sc = SystemConfig(**data)
        assert len(sc.stochastic_scenarios) == 2

    def test_num_nodes_property(self):
        sc = SystemConfig(**_system_minimal())
        assert sc.num_nodes == 1

    def test_num_buses_property(self):
        sc = SystemConfig(**_system_minimal())
        assert sc.num_buses == 1

    def test_alias_fields(self):
        """SystemConfig aliases (e.g., LOSS_DEMAND_TRHESHOLD) should work."""
        data = _system_minimal()
        data["LOSS_DEMAND_TRHESHOLD"] = 0.1
        data["SIM_ROOFTOP"] = True
        data["TARGET_RE_PENETRATION"] = 0.8
        sc = SystemConfig(**data)
        assert sc.loss_demand_threshold == 0.1
        assert sc.sim_rooftop is True
        assert sc.target_re_penetration == 0.8

    def test_batteries_included(self):
        data = _system_minimal()
        data["batteries"] = {"bat_0": _bat_1()}
        sc = SystemConfig(**data)
        assert "bat_0" in sc.batteries
        assert sc.batteries["bat_0"].name == "LiIon"

    def test_discount_rate_bounds(self):
        data = _system_minimal()
        data["discount_rate"] = 0
        sc = SystemConfig(**data)
        assert sc.discount_rate == 0

        data["discount_rate"] = 1
        sc = SystemConfig(**data)
        assert sc.discount_rate == 1

    def test_discount_rate_out_of_range_raises(self):
        data = _system_minimal()
        data["discount_rate"] = 1.1
        with pytest.raises(ValidationError):
            SystemConfig(**data)

    def test_demand_scale_must_be_positive(self):
        data = _system_minimal()
        data["demand_scale"] = 0
        with pytest.raises(ValidationError):
            SystemConfig(**data)

    def test_target_re_penetration_range(self):
        data = _system_minimal()
        data["TARGET_RE_PENETRATION"] = 0
        sc = SystemConfig(**data)
        assert sc.target_re_penetration == 0

        data["TARGET_RE_PENETRATION"] = 1
        sc2 = SystemConfig(**data)
        assert sc2.target_re_penetration == 1

    def test_target_re_penetration_out_of_range_raises(self):
        data = _system_minimal()
        data["TARGET_RE_PENETRATION"] = 1.1
        with pytest.raises(ValidationError):
            SystemConfig(**data)

    def test_soc_end_tolerance_bounds(self):
        data = _system_minimal()
        data["soc_end_tolerance"] = 0
        sc = SystemConfig(**data)
        assert sc.soc_end_tolerance == 0

        data["soc_end_tolerance"] = 0.5
        sc2 = SystemConfig(**data)
        assert sc2.soc_end_tolerance == 0.5

    def test_soc_end_tolerance_out_of_range_raises(self):
        data = _system_minimal()
        data["soc_end_tolerance"] = 0.51
        with pytest.raises(ValidationError):
            SystemConfig(**data)

    def test_reserve_margin_minimum(self):
        data = _system_minimal()
        data["reserve_margin"] = 1.0
        sc = SystemConfig(**data)
        assert sc.reserve_margin == 1.0

    def test_reserve_margin_below_minimum_raises(self):
        data = _system_minimal()
        data["reserve_margin"] = 0.99
        with pytest.raises(ValidationError):
            SystemConfig(**data)

    def test_power_flow_mode_default(self):
        sc = SystemConfig(**_system_minimal())
        assert sc.power_flow_mode == "dcopf"

    def test_power_flow_mode_acopf(self):
        data = _system_minimal()
        data["power_flow_mode"] = "acopf_polar"
        sc = SystemConfig(**data)
        assert sc.power_flow_mode == "acopf_polar"

    def test_power_flow_mode_invalid(self):
        data = _system_minimal()
        data["power_flow_mode"] = "invalid_mode"
        with pytest.raises(ValidationError):
            SystemConfig(**data)


# ========================================================================
# ESFEXConfig
# ========================================================================


class TestESFEXConfig:
    def test_minimal_valid(self):
        rc = ESFEXConfig(**_esfex_minimal())
        assert "TestSys" in rc.systems
        assert rc.simulation_mode == "development"

    def test_primary_system_property(self):
        rc = ESFEXConfig(**_esfex_minimal())
        ps = rc.primary_system
        assert ps.name == "TestSys"

    def test_get_system_valid(self):
        rc = ESFEXConfig(**_esfex_minimal())
        sys = rc.get_system("TestSys")
        assert sys.name == "TestSys"

    def test_get_system_missing_raises(self):
        rc = ESFEXConfig(**_esfex_minimal())
        with pytest.raises(KeyError, match="NoSuchSystem"):
            rc.get_system("NoSuchSystem")

    def test_validate_systems_exist_pass(self):
        """meta_network references an existing system."""
        rc = ESFEXConfig(**_esfex_minimal())
        assert rc is not None  # no error

    def test_validate_systems_exist_fail(self):
        """meta_network references a non-existent system."""
        data = _esfex_minimal()
        data["meta_network"]["systems"].append("Ghost")
        with pytest.raises(ValidationError, match="Ghost.*not defined"):
            ESFEXConfig(**data)

    def test_multi_system(self):
        data = {
            "meta_network": {"systems": ["SysA", "SysB"]},
            "systems": {
                "SysA": _system_minimal("SysA"),
                "SysB": _system_minimal("SysB"),
            },
        }
        rc = ESFEXConfig(**data)
        assert len(rc.systems) == 2
        assert rc.primary_system.name == "SysA"
        assert rc.get_system("SysB").name == "SysB"

    def test_defaults(self):
        rc = ESFEXConfig(**_esfex_minimal())
        assert rc.simulation_mode == "development"
        assert rc.unit_commitment_hours == 24
        assert rc.enable_primary_energy is True
        assert isinstance(rc.temporal, TemporalConfig)
        assert isinstance(rc.solver, SolverConfig)
        assert isinstance(rc.n1_security, N1SecurityConfig)
        assert isinstance(rc.master_problem, MasterProblemConfig)

    def test_simulation_mode_unit_commitment(self):
        data = _esfex_minimal()
        data["simulation_mode"] = "unit_commitment"
        rc = ESFEXConfig(**data)
        assert rc.simulation_mode == "unit_commitment"

    def test_simulation_mode_invalid_raises(self):
        data = _esfex_minimal()
        data["simulation_mode"] = "dispatch"
        with pytest.raises(ValidationError):
            ESFEXConfig(**data)

    def test_custom_solver(self):
        data = _esfex_minimal()
        data["solver"] = {"name": "gurobi", "threads": 8, "gap": 0.001}
        rc = ESFEXConfig(**data)
        assert rc.solver.name == "gurobi"
        assert rc.solver.threads == 8

    def test_custom_temporal(self):
        data = _esfex_minimal()
        data["temporal"] = {"resolution_hours": 2, "rolling_horizon_hours": 72}
        rc = ESFEXConfig(**data)
        assert rc.temporal.resolution_hours == 2
        assert rc.temporal.rolling_horizon_hours == 72

    def test_meta_network_required(self):
        data = _esfex_minimal()
        del data["meta_network"]
        with pytest.raises(ValidationError):
            ESFEXConfig(**data)

    def test_systems_required(self):
        data = _esfex_minimal()
        del data["systems"]
        with pytest.raises(ValidationError):
            ESFEXConfig(**data)

    def test_plugins_field_default_empty(self):
        rc = ESFEXConfig(**_esfex_minimal())
        assert rc.plugins == {}

    def test_plugins_field_accepts_dict(self):
        data = _esfex_minimal()
        data["plugins"] = {"weather": {"provider": "openmeteo"}, "hydrogen": {"enabled": True}}
        rc = ESFEXConfig(**data)
        assert rc.plugins["weather"]["provider"] == "openmeteo"
        assert rc.plugins["hydrogen"]["enabled"] is True

    def test_plugins_field_roundtrip(self):
        data = _esfex_minimal()
        data["plugins"] = {"my_plugin": {"x": 42, "nested": {"a": 1}}}
        rc = ESFEXConfig(**data)
        dumped = rc.model_dump()
        assert dumped["plugins"] == {"my_plugin": {"x": 42, "nested": {"a": 1}}}


# ========================================================================
# NodeConfig (supplementary)
# ========================================================================


class TestNodeConfig:
    def test_single_node(self):
        nc = NodeConfig(nodes_connections=[0.0])
        assert nc.num_nodes == 1
        assert nc.reserve_static == [0.0]
        assert nc.losses == [0.0]

    def test_two_nodes(self):
        nc = NodeConfig(nodes_connections=[0.0, 50.0, 50.0, 0.0])
        assert nc.num_nodes == 2
        assert len(nc.reserve_static) == 2
        # transference_invest_cost is per-node (N) since the master indexes
        # investments by destination node, not by edge — the old N×N shape
        # double-counted the cost on the symmetric reverse edge.
        assert len(nc.transference_invest_cost) == 2

    def test_explicit_num_nodes(self):
        nc = NodeConfig(num_nodes=2, nodes_connections=[0.0, 100.0, 100.0, 0.0])
        assert nc.num_nodes == 2

    def test_geo_coordinates(self):
        nc = NodeConfig(
            nodes_connections=[0.0],
            node_coordinates=[{"latitude": 23.0, "longitude": -82.0}],
            node_names=["Havana"],
        )
        assert nc.node_coordinates[0].latitude == 23.0
        assert nc.node_names[0] == "Havana"

    def test_auto_fill_defaults(self):
        """Empty arrays are filled to match num_nodes."""
        nc = NodeConfig(nodes_connections=[0, 10, 10, 0, 5, 0, 0, 5, 0])
        assert nc.num_nodes == 3
        assert len(nc.reserve_dynamic) == 3
        assert len(nc.reserve_duration) == 3
        # transference_invest_max is per-node (N), not per-edge (N×N).
        assert len(nc.transference_invest_max) == 3


# ========================================================================
# GeneratorConfig: Reservoir Fields
# ========================================================================


class TestGeneratorConfigReservoir:
    """Tests for reservoir hydroelectric fields on GeneratorConfig."""

    def test_reservoir_defaults_empty(self):
        g = GeneratorConfig(**_gen_1())
        assert g.reservoir_capacity == []
        assert g.reservoir_initial_level == []
        assert g.reservoir_min_level == []
        assert g.reservoir_max_level == []
        assert g.reservoir_inflow_file is None
        assert g.reservoir_turbine_efficiency == []
        assert g.reservoir_evaporation_rate == []
        assert g.reservoir_pump_capacity == []
        assert g.reservoir_pump_efficiency == []
        assert g.reservoir_spillage_allowed is True
        assert g.reservoir_invest_cost == []
        assert g.reservoir_invest_max == []

    def test_reservoir_with_values(self):
        data = _gen_1()
        data["reservoir_capacity"] = [500.0]
        data["reservoir_initial_level"] = [0.8]
        data["reservoir_min_level"] = [0.1]
        data["reservoir_max_level"] = [0.95]
        data["reservoir_inflow_file"] = "inflow_data.csv"
        data["reservoir_turbine_efficiency"] = [0.92]
        data["reservoir_evaporation_rate"] = [0.001]
        data["reservoir_pump_capacity"] = [50.0]
        data["reservoir_pump_efficiency"] = [0.87]
        data["reservoir_spillage_allowed"] = False
        data["reservoir_invest_cost"] = [100000.0]
        data["reservoir_invest_max"] = [200.0]
        g = GeneratorConfig(**data)
        assert g.reservoir_capacity == [500.0]
        assert g.reservoir_initial_level == [0.8]
        assert g.reservoir_min_level == [0.1]
        assert g.reservoir_max_level == [0.95]
        assert g.reservoir_inflow_file == "inflow_data.csv"
        assert g.reservoir_turbine_efficiency == [0.92]
        assert g.reservoir_evaporation_rate == [0.001]
        assert g.reservoir_pump_capacity == [50.0]
        assert g.reservoir_pump_efficiency == [0.87]
        assert g.reservoir_spillage_allowed is False
        assert g.reservoir_invest_cost == [100000.0]
        assert g.reservoir_invest_max == [200.0]

    def test_reservoir_two_nodes(self):
        data = _gen_1()
        # Expand all per-node arrays to 2 nodes
        for key in [
            "life_time", "initial_age", "degradation_rate", "decommissioning_cost",
            "rated_power", "min_power", "min_up", "min_down", "ramp_up", "ramp_down",
            "eff_at_rated", "eff_at_min", "inertia", "start_up_cost", "fuel_cost",
            "fixed_cost", "maintenance_cost", "invest_cost", "invest_max_power",
        ]:
            val = data[key][0]
            data[key] = [val, val]
        data["reservoir_capacity"] = [500.0, 0.0]
        data["reservoir_initial_level"] = [0.5, 0.0]
        data["reservoir_pump_capacity"] = [30.0, 0.0]
        g = GeneratorConfig(**data)
        assert g.reservoir_capacity == [500.0, 0.0]
        assert len(g.reservoir_pump_capacity) == 2

    def test_reservoir_spillage_allowed_default_true(self):
        data = _gen_1()
        data["reservoir_capacity"] = [100.0]
        g = GeneratorConfig(**data)
        assert g.reservoir_spillage_allowed is True

    def test_reservoir_no_inflow_file(self):
        data = _gen_1()
        data["reservoir_capacity"] = [100.0]
        g = GeneratorConfig(**data)
        assert g.reservoir_inflow_file is None

    def test_reservoir_partial_fields(self):
        """Only some reservoir fields set; rest stay default."""
        data = _gen_1()
        data["reservoir_capacity"] = [200.0]
        data["reservoir_turbine_efficiency"] = [0.88]
        g = GeneratorConfig(**data)
        assert g.reservoir_capacity == [200.0]
        assert g.reservoir_turbine_efficiency == [0.88]
        assert g.reservoir_pump_capacity == []
        assert g.reservoir_invest_max == []


# ===========================================================================
# CostCurveBlock
# ===========================================================================


class TestCostCurveBlock:
    """Tests for the CostCurveBlock model."""

    def test_valid_construction(self):
        b = CostCurveBlock(fraction=0.5, price=100.0)
        assert b.fraction == 0.5
        assert b.price == 100.0

    def test_fraction_boundary_zero(self):
        b = CostCurveBlock(fraction=0.0, price=10.0)
        assert b.fraction == 0.0

    def test_fraction_boundary_one(self):
        b = CostCurveBlock(fraction=1.0, price=10.0)
        assert b.fraction == 1.0

    def test_fraction_negative_fails(self):
        with pytest.raises(ValidationError):
            CostCurveBlock(fraction=-0.1, price=10.0)

    def test_fraction_above_one_fails(self):
        with pytest.raises(ValidationError):
            CostCurveBlock(fraction=1.1, price=10.0)

    def test_negative_price_fails(self):
        with pytest.raises(ValidationError):
            CostCurveBlock(fraction=0.5, price=-1.0)


# ===========================================================================
# CostCurveConfig
# ===========================================================================


class TestCostCurveConfig:
    """Tests for the CostCurveConfig model."""

    def test_default_curve_type_is_flat(self):
        c = CostCurveConfig()
        assert c.curve_type == "flat"

    def test_valid_curve_type_flat(self):
        c = CostCurveConfig(curve_type="flat")
        assert c.curve_type == "flat"

    def test_valid_curve_type_linear(self):
        c = CostCurveConfig(curve_type="linear")
        assert c.curve_type == "linear"

    def test_valid_curve_type_stepwise(self):
        c = CostCurveConfig(curve_type="stepwise")
        assert c.curve_type == "stepwise"

    def test_valid_curve_type_exponential(self):
        c = CostCurveConfig(curve_type="exponential")
        assert c.curve_type == "exponential"

    def test_invalid_curve_type_raises(self):
        with pytest.raises(ValidationError):
            CostCurveConfig(curve_type="polynomial")

    def test_default_num_segments(self):
        c = CostCurveConfig()
        assert c.num_segments == 5

    def test_num_segments_min_valid(self):
        c = CostCurveConfig(num_segments=2)
        assert c.num_segments == 2

    def test_num_segments_max_valid(self):
        c = CostCurveConfig(num_segments=20)
        assert c.num_segments == 20

    def test_num_segments_below_min_fails(self):
        with pytest.raises(ValidationError):
            CostCurveConfig(num_segments=1)

    def test_num_segments_above_max_fails(self):
        with pytest.raises(ValidationError):
            CostCurveConfig(num_segments=21)

    def test_flat_with_flat_price(self):
        c = CostCurveConfig(curve_type="flat", flat_price=42.0)
        assert c.flat_price == 42.0

    def test_linear_with_prices(self):
        c = CostCurveConfig(
            curve_type="linear", price_at_zero=10.0, price_at_max=50.0
        )
        assert c.price_at_zero == 10.0
        assert c.price_at_max == 50.0

    def test_stepwise_with_blocks(self):
        blocks = [
            CostCurveBlock(fraction=0.4, price=20.0),
            CostCurveBlock(fraction=0.6, price=40.0),
        ]
        c = CostCurveConfig(curve_type="stepwise", blocks=blocks)
        assert len(c.blocks) == 2
        assert c.blocks[0].fraction == 0.4

    def test_exponential_with_params(self):
        c = CostCurveConfig(
            curve_type="exponential", base_price=100.0, scale_factor=2.0
        )
        assert c.base_price == 100.0
        assert c.scale_factor == 2.0


# ===========================================================================
# normalize_cost_curve
# ===========================================================================


class TestNormalizeCostCurve:
    """Tests for the normalize_cost_curve() helper function."""

    def test_flat_default(self):
        """Default flat curve returns 1 block with fallback price 0."""
        c = CostCurveConfig(curve_type="flat")
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 1
        assert blocks[0].fraction == 1.0
        assert blocks[0].price == 0.0

    def test_flat_with_price(self):
        c = CostCurveConfig(curve_type="flat", flat_price=50.0)
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 1
        assert blocks[0].price == 50.0

    def test_flat_uses_fallback(self):
        c = CostCurveConfig(curve_type="flat")
        blocks = normalize_cost_curve(c, fallback_price=30.0)
        assert len(blocks) == 1
        assert blocks[0].price == 30.0

    def test_stepwise_passthrough(self):
        """Blocks that sum to 1.0 pass through unchanged."""
        src = [
            CostCurveBlock(fraction=0.4, price=20.0),
            CostCurveBlock(fraction=0.6, price=40.0),
        ]
        c = CostCurveConfig(curve_type="stepwise", blocks=src)
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 2
        assert blocks[0].fraction == 0.4
        assert blocks[0].price == 20.0
        assert blocks[1].fraction == 0.6
        assert blocks[1].price == 40.0

    def test_stepwise_empty_blocks(self):
        """None blocks fall back to single block with fallback price."""
        c = CostCurveConfig(curve_type="stepwise", blocks=None)
        blocks = normalize_cost_curve(c, fallback_price=15.0)
        assert len(blocks) == 1
        assert blocks[0].fraction == 1.0
        assert blocks[0].price == 15.0

    def test_stepwise_auto_normalizes(self):
        """Fractions [0.3, 0.3] sum to 0.6, auto-normalised to [0.5, 0.5]."""
        src = [
            CostCurveBlock(fraction=0.3, price=10.0),
            CostCurveBlock(fraction=0.3, price=20.0),
        ]
        c = CostCurveConfig(curve_type="stepwise", blocks=src)
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 2
        assert blocks[0].fraction == pytest.approx(0.5)
        assert blocks[1].fraction == pytest.approx(0.5)
        # Prices unchanged
        assert blocks[0].price == 10.0
        assert blocks[1].price == 20.0

    def test_stepwise_exact_sum(self):
        """Fractions that exactly sum to 1.0 are not re-normalised."""
        src = [
            CostCurveBlock(fraction=0.4, price=10.0),
            CostCurveBlock(fraction=0.6, price=20.0),
        ]
        c = CostCurveConfig(curve_type="stepwise", blocks=src)
        blocks = normalize_cost_curve(c)
        assert blocks[0].fraction == 0.4
        assert blocks[1].fraction == 0.6

    def test_linear_two_segments(self):
        """Linear with price_at_zero=100, price_at_max=200, 2 segments."""
        c = CostCurveConfig(
            curve_type="linear",
            price_at_zero=100.0,
            price_at_max=200.0,
            num_segments=2,
        )
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 2
        # Segment 0: mid = 0.25, price = 100 + 100*0.25 = 125
        assert blocks[0].fraction == pytest.approx(0.5)
        assert blocks[0].price == pytest.approx(125.0)
        # Segment 1: mid = 0.75, price = 100 + 100*0.75 = 175
        assert blocks[1].fraction == pytest.approx(0.5)
        assert blocks[1].price == pytest.approx(175.0)

    def test_linear_uses_fallback(self):
        """Linear with price_at_zero=None uses fallback_price."""
        c = CostCurveConfig(
            curve_type="linear",
            price_at_max=200.0,
            num_segments=2,
        )
        blocks = normalize_cost_curve(c, fallback_price=50.0)
        assert len(blocks) == 2
        # Segment 0: mid = 0.25, price = 50 + 150*0.25 = 87.5
        assert blocks[0].price == pytest.approx(87.5)

    def test_exponential_segments(self):
        """Exponential with base_price=100, scale_factor=1.0, 3 segments."""
        import math

        c = CostCurveConfig(
            curve_type="exponential",
            base_price=100.0,
            scale_factor=1.0,
            num_segments=3,
        )
        blocks = normalize_cost_curve(c)
        assert len(blocks) == 3
        # Each segment has fraction = 1/3
        for b in blocks:
            assert b.fraction == pytest.approx(1.0 / 3.0)
        # Segment 0: mid = 1/6, price = 100*exp(1/6)
        assert blocks[0].price == pytest.approx(100.0 * math.exp(1.0 / 6.0), rel=1e-4)
        # Segment 1: mid = 3/6 = 0.5, price = 100*exp(0.5)
        assert blocks[1].price == pytest.approx(100.0 * math.exp(0.5), rel=1e-4)
        # Segment 2: mid = 5/6, price = 100*exp(5/6)
        assert blocks[2].price == pytest.approx(100.0 * math.exp(5.0 / 6.0), rel=1e-4)

    def test_all_fractions_sum_to_one(self):
        """For each curve type, fractions must sum to ~1.0."""
        curves = [
            CostCurveConfig(curve_type="flat", flat_price=10.0),
            CostCurveConfig(
                curve_type="linear",
                price_at_zero=0.0,
                price_at_max=100.0,
                num_segments=7,
            ),
            CostCurveConfig(
                curve_type="stepwise",
                blocks=[
                    CostCurveBlock(fraction=0.3, price=10.0),
                    CostCurveBlock(fraction=0.7, price=20.0),
                ],
            ),
            CostCurveConfig(
                curve_type="exponential",
                base_price=50.0,
                scale_factor=0.5,
                num_segments=4,
            ),
        ]
        for curve in curves:
            blocks = normalize_cost_curve(curve)
            total = sum(b.fraction for b in blocks)
            assert total == pytest.approx(1.0), (
                f"Fractions for {curve.curve_type} sum to {total}"
            )


# ===========================================================================
# GeneratorConfig — fuel_cost_curve
# ===========================================================================


class TestGeneratorConfigCostCurve:
    """Tests for fuel_cost_curve on GeneratorConfig."""

    def test_fuel_cost_curve_default_none(self):
        g = GeneratorConfig(**_gen_1())
        assert g.fuel_cost_curve is None

    def test_fuel_cost_curve_single_node(self):
        data = _gen_1()
        data["fuel_cost_curve"] = [
            CostCurveConfig(
                curve_type="linear", price_at_zero=10.0, price_at_max=50.0
            )
        ]
        g = GeneratorConfig(**data)
        assert g.fuel_cost_curve is not None
        assert len(g.fuel_cost_curve) == 1
        assert g.fuel_cost_curve[0].curve_type == "linear"
        assert g.fuel_cost_curve[0].price_at_zero == 10.0
        assert g.fuel_cost_curve[0].price_at_max == 50.0

    def test_fuel_cost_curve_multi_node(self):
        """Generator with 2 nodes can have 2 cost curves."""
        data = _gen_1()
        # Duplicate per-node lists for 2 nodes
        for key in [
            "life_time", "initial_age", "degradation_rate",
            "decommissioning_cost", "rated_power", "min_power",
            "min_up", "min_down", "ramp_up", "ramp_down",
            "eff_at_rated", "eff_at_min", "inertia", "start_up_cost",
            "fuel_cost", "fixed_cost", "maintenance_cost",
            "invest_cost", "invest_max_power",
        ]:
            val = data[key][0]
            data[key] = [val, val]
        data["fuel_cost_curve"] = [
            CostCurveConfig(curve_type="flat", flat_price=20.0),
            CostCurveConfig(
                curve_type="stepwise",
                blocks=[
                    CostCurveBlock(fraction=0.5, price=30.0),
                    CostCurveBlock(fraction=0.5, price=60.0),
                ],
            ),
        ]
        g = GeneratorConfig(**data)
        assert len(g.fuel_cost_curve) == 2
        assert g.fuel_cost_curve[0].curve_type == "flat"
        assert g.fuel_cost_curve[1].curve_type == "stepwise"


# ===========================================================================
# BatteryConfig — discharge_cost_curve
# ===========================================================================


class TestBatteryConfigCostCurve:
    """Tests for discharge_cost_curve on BatteryConfig."""

    def test_discharge_cost_curve_default_none(self):
        b = BatteryConfig(**_bat_1())
        assert b.discharge_cost_curve is None

    def test_discharge_cost_curve_with_stepwise(self):
        data = _bat_1()
        data["discharge_cost_curve"] = [
            CostCurveConfig(
                curve_type="stepwise",
                blocks=[
                    CostCurveBlock(fraction=0.3, price=5.0),
                    CostCurveBlock(fraction=0.7, price=15.0),
                ],
            )
        ]
        b = BatteryConfig(**data)
        assert b.discharge_cost_curve is not None
        assert len(b.discharge_cost_curve) == 1
        assert b.discharge_cost_curve[0].curve_type == "stepwise"
        assert len(b.discharge_cost_curve[0].blocks) == 2
        assert b.discharge_cost_curve[0].blocks[0].price == 5.0
