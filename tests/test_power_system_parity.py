"""
Parity tests for PowerSystem: Python/PuLP vs Julia/JuMP.

These tests verify that the Julia implementation produces
equivalent results to the Python/PuLP implementation.
"""

import pytest
import numpy as np
from pathlib import Path

# Import test fixtures
from conftest import (
    sample_demand,
    sample_solar_availability,
    sample_wind_availability,
)


def _build_simple_network(jl, ESFEX, num_nodes, connections, distances,
                          *, base_impedance=100.0, reactance_per_km=0.4,
                          voltage_kv=220.0, max_angle_diff_rad=0.524,
                          slack_bus=1):
    """One-bus-per-node NetworkConfig with no per-line/transformer/converter
    detail — matches what these parity tests need post-back-compat-ctor removal."""
    from esfex.bridge.converters import py_to_julia_matrix, py_to_julia_vector
    buses = jl.seval("BusData[]")
    for i in range(1, num_nodes + 1):
        role = "slack" if i == slack_bus else "PQ"
        jl.seval("push!")(buses, ESFEX.BusData(
            i, i, voltage_kv, 50.0, "AC", role, "load", 1.0))
    bus_to_node = py_to_julia_vector(list(range(1, num_nodes + 1)))
    return ESFEX.NetworkConfig(
        num_nodes, num_nodes, buses, bus_to_node,
        py_to_julia_matrix(connections),
        py_to_julia_matrix(distances),
        base_impedance, reactance_per_km, voltage_kv,
        max_angle_diff_rad, slack_bus,
        py_to_julia_vector([0.0] * num_nodes),
        py_to_julia_vector([0.0] * num_nodes),
        jl.seval("TransmissionLineData[]"),
        jl.seval("TransformerData[]"),
        jl.seval("ACDCConverterData[]"),
        jl.seval("FrequencyConverterData[]"),
        0.1,
    )


def _gen_reservoir_defaults(jl, n_nodes, hours):
    """Default (no-reservoir/no-risk) trailing args for GeneratorConfig."""
    from esfex.bridge.converters import py_to_julia_matrix, py_to_julia_vector
    z = lambda: py_to_julia_vector([0.0] * n_nodes)
    o = lambda: py_to_julia_vector([1.0] * n_nodes)
    return (
        50.0, "AC",
        z(), z(), z(), o(),                  # reservoir cap/initial/min/max
        py_to_julia_matrix(np.zeros((hours, n_nodes))),  # inflow
        o(), z(),                            # turbine_eff, evaporation
        z(), o(), True,                      # pump_cap, pump_eff, spillage
        z(), z(), o(),                       # reservoir invest_cost/max, risk
    )


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def simple_generator_config():
    """Create a simple generator configuration for testing."""
    return {
        "name": "TestGen",
        "type": "Non-renewable",
        "fuel": "Gas",
        "rated_power": [100.0, 50.0],  # 2 nodes
        "min_power": [0.3, 0.3],
        "eff_at_rated": [0.45, 0.45],
        "eff_at_min": [0.35, 0.35],
        "ramp_up": [0.5, 0.5],  # pu/min
        "ramp_down": [0.5, 0.5],
        "min_up": [2, 2],
        "min_down": [2, 2],
        "start_up_cost": [500.0, 500.0],
        "fuel_cost": [50.0, 55.0],  # $/MWh
        "fixed_cost": [2.0, 2.0],
        "maintenance_cost": [1.0, 1.0],
        "inertia": [5.0, 5.0],
        "invest_cost": [1000.0, 1000.0],
        "invest_max_power": [0.0, 0.0],  # No investment
        "reservable": True,
        "life_time": [25, 25],
        "initial_age": [0, 0],
        "degradation_rate": [0.005, 0.005],
    }


@pytest.fixture
def simple_solar_config():
    """Create a simple solar generator configuration."""
    return {
        "name": "Solar",
        "type": "Renewable",
        "fuel": "Solar",
        "rated_power": [50.0, 30.0],
        "min_power": [0.0, 0.0],
        "eff_at_rated": [1.0, 1.0],
        "eff_at_min": [1.0, 1.0],
        "ramp_up": [1.0, 1.0],
        "ramp_down": [1.0, 1.0],
        "min_up": [0, 0],
        "min_down": [0, 0],
        "start_up_cost": [0.0, 0.0],
        "fuel_cost": [0.0, 0.0],
        "fixed_cost": [0.0, 0.0],
        "maintenance_cost": [0.5, 0.5],
        "inertia": [0.0, 0.0],
        "invest_cost": [800.0, 800.0],
        "invest_max_power": [100.0, 100.0],
        "reservable": False,
        "life_time": [25, 25],
        "initial_age": [0, 0],
        "degradation_rate": [0.005, 0.005],
    }


@pytest.fixture
def simple_battery_config():
    """Create a simple battery configuration."""
    return {
        "name": "Battery",
        "capacity": [20.0, 10.0],  # MWh
        "MaxChargePower": [10.0, 5.0],  # MW
        "MaxDischargePower": [10.0, 5.0],
        "efficiency_charge": [0.95, 0.95],
        "efficiency_discharge": [0.95, 0.95],
        "max_DoD": [0.8, 0.8],  # 80% depth of discharge
        "soc_initial": [0.5, 0.5],
        "invest_cost": [150.0, 150.0],  # $/MW
        "invest_cost_energy": [100.0, 100.0],  # $/MWh
        "invest_max_power": [0.0, 0.0],
        "invest_max_capacity": [0.0, 0.0],
        "life_time": [15, 15],
    }


@pytest.fixture
def simple_node_config():
    """Create a simple 2-node network configuration."""
    return {
        "num_nodes": 2,
        "nodes_connections": [0.0, 100.0, 100.0, 0.0],  # Symmetric connection
        "reserve_static": [10.0, 10.0],
        "reserve_dynamic": [5.0, 5.0],
        "reserve_duration": [1, 1],
        "losses": [0.02, 0.02],
        "transference_invest_cost": [0.0, 0.0, 0.0, 0.0],
        "transference_invest_max": [0.0, 0.0, 0.0, 0.0],
    }


# =============================================================================
# Julia-only Tests (verify Julia model builds and solves)
# =============================================================================

@pytest.mark.julia
def test_julia_power_system_builds():
    """Test that Julia PowerSystem model builds without errors."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_matrix, py_to_julia_vector

    jl = get_julia()
    ESFEX = get_esfex_module()

    # Create minimal input
    hours = 24
    n_nodes = 2
    n_gen = 1
    n_bat = 0

    # Create network config
    connections = np.array([[0.0, 100.0], [100.0, 0.0]])
    distances = np.array([[0.0, 50.0], [50.0, 0.0]])

    network = _build_simple_network(jl, ESFEX, 2, connections, distances)

    # Generator config (defaults trailing reservoir/freq/risk args).
    availability = np.ones((hours, n_nodes))
    gen = ESFEX.GeneratorConfig(
        "TestGen",
        "Non-renewable",
        "Gas",
        py_to_julia_vector([100.0, 50.0]),  # rated_power
        py_to_julia_vector([0.3, 0.3]),  # min_power
        py_to_julia_vector([0.45, 0.45]),  # eff_rated
        py_to_julia_vector([0.35, 0.35]),  # eff_min
        py_to_julia_vector([0.5, 0.5]),  # ramp_up
        py_to_julia_vector([0.5, 0.5]),  # ramp_down
        py_to_julia_vector([2.0, 2.0]),  # min_up
        py_to_julia_vector([2.0, 2.0]),  # min_down
        py_to_julia_vector([500.0, 500.0]),  # startup_cost
        py_to_julia_vector([50.0, 55.0]),  # fuel_cost
        py_to_julia_vector([2.0, 2.0]),  # fixed_cost
        py_to_julia_vector([1.0, 1.0]),  # maint_cost
        py_to_julia_vector([5.0, 5.0]),  # inertia
        py_to_julia_vector([1000.0, 1000.0]),  # invest_cost
        py_to_julia_vector([0.0, 0.0]),  # invest_max
        py_to_julia_matrix(availability),
        True,  # reservable
        py_to_julia_vector([25.0, 25.0]),  # life_time
        py_to_julia_vector([0.0, 0.0]),  # initial_age
        py_to_julia_vector([0.005, 0.005]),  # degradation
        py_to_julia_vector([0.0, 0.0]),  # decommissioning_cost
        *_gen_reservoir_defaults(jl, n_nodes, hours),
    )

    # Create generator vector using Julia
    jl._test_gen = gen
    generators = jl.seval("GeneratorConfig[_test_gen]")

    # Create temporal config
    temporal = ESFEX.TemporalConfig(
        hours, 1, 168, 24, 8760, 168, 6, 6, 4
    )

    # Create demand matrix
    demand = np.random.uniform(50, 100, (hours, n_nodes))

    # Create PowerSystemInput
    ps_input = ESFEX.PowerSystemInput(
        name="TestSystem",
        year=2025,
        base_year=2025,
        network=network,
        generators=generators,
        batteries=jl.seval("BatteryConfig[]"),
        demand=py_to_julia_matrix(demand),
        temporal=temporal,
        mode="economic_dispatch",
    )

    # Build model
    model, vars = ESFEX.create_power_system(ps_input)

    assert model is not None
    assert vars is not None


@pytest.mark.julia
@pytest.mark.timeout(180)
def test_julia_power_system_solves():
    """Test that Julia PowerSystem model solves and returns valid results.

    Solver/Julia warm-up plus extract_solution take >60 s on cold caches;
    bump the timeout so the test isn't reported as a hang.
    """
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_matrix, py_to_julia_vector

    jl = get_julia()
    ESFEX = get_esfex_module()

    # Create minimal input
    hours = 24
    n_nodes = 1  # Single node for simplicity

    # Create single-node network
    connections = np.array([[0.0]])
    distances = np.array([[0.0]])

    network = _build_simple_network(jl, ESFEX, 1, connections, distances)

    # Create generator with enough capacity
    availability = np.ones((hours, n_nodes))
    gen = ESFEX.GeneratorConfig(
        "Gas", "Non-renewable", "Gas",
        py_to_julia_vector([200.0]),  # rated_power
        py_to_julia_vector([0.0]),  # min_power
        py_to_julia_vector([0.45]),
        py_to_julia_vector([0.35]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([0.0]),  # no min up/down
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),  # no startup cost
        py_to_julia_vector([50.0]),  # fuel cost
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_matrix(availability),
        True,
        py_to_julia_vector([25.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),  # decommissioning_cost
        *_gen_reservoir_defaults(jl, n_nodes, hours),
    )

    jl._test_gen = gen
    generators = jl.seval("GeneratorConfig[_test_gen]")

    temporal = ESFEX.TemporalConfig(hours, 1, 168, 24, 8760, 168, 6, 6, 4)

    # Simple constant demand
    demand = np.full((hours, n_nodes), 100.0)

    ps_input = ESFEX.PowerSystemInput(
        name="TestSystem",
        year=2025,
        base_year=2025,
        network=network,
        generators=generators,
        batteries=jl.seval("BatteryConfig[]"),
        demand=py_to_julia_matrix(demand),
        temporal=temporal,
        mode="economic_dispatch",
    )

    # Build and solve
    model, vars = ESFEX.create_power_system(ps_input)

    jl._model = model
    jl.seval("using JuMP; optimize!(_model)")

    # Extract solution
    result = ESFEX.extract_solution(model, vars, ps_input)

    # Verify solution
    assert str(result.status).find("OPTIMAL") >= 0
    assert result.objective > 0
    assert result.total_generation > 0

    # Expected: 100 MW * 24 hours * $50/MWh = $120,000
    expected_cost = 100 * 24 * 50
    assert abs(result.objective - expected_cost) < 1.0  # Within $1


@pytest.mark.julia
def test_julia_renewable_dispatch():
    """Test that Julia correctly dispatches renewable generation."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_matrix, py_to_julia_vector

    jl = get_julia()
    ESFEX = get_esfex_module()

    hours = 24
    n_nodes = 1

    network = _build_simple_network(
        jl, ESFEX, 1, np.array([[0.0]]), np.array([[0.0]]),
    )

    # Solar with typical daily profile
    solar_avail = np.array([
        0.0, 0.0, 0.0, 0.0, 0.0, 0.1,
        0.3, 0.5, 0.7, 0.8, 0.9, 0.95,
        0.95, 0.9, 0.8, 0.7, 0.5, 0.3,
        0.1, 0.0, 0.0, 0.0, 0.0, 0.0
    ]).reshape(-1, 1)

    solar = ESFEX.GeneratorConfig(
        "Solar", "Renewable", "Solar",
        py_to_julia_vector([100.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),  # Free
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_matrix(solar_avail),
        False,
        py_to_julia_vector([25.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),  # decommissioning_cost
        *_gen_reservoir_defaults(jl, n_nodes, hours),
    )

    # Gas backup
    gas_avail = np.ones((hours, n_nodes))
    gas = ESFEX.GeneratorConfig(
        "Gas", "Non-renewable", "Gas",
        py_to_julia_vector([200.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.45]),
        py_to_julia_vector([0.35]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([1.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([50.0]),  # $50/MWh
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_matrix(gas_avail),
        True,
        py_to_julia_vector([25.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),
        py_to_julia_vector([0.0]),  # decommissioning_cost
        *_gen_reservoir_defaults(jl, n_nodes, hours),
    )

    jl._solar = solar
    jl._gas = gas
    generators = jl.seval("GeneratorConfig[_solar, _gas]")

    temporal = ESFEX.TemporalConfig(hours, 1, 168, 24, 8760, 168, 6, 6, 4)

    # Constant demand
    demand = np.full((hours, n_nodes), 80.0)

    ps_input = ESFEX.PowerSystemInput(
        name="TestSystem",
        year=2025,
        base_year=2025,
        network=network,
        generators=generators,
        batteries=jl.seval("BatteryConfig[]"),
        demand=py_to_julia_matrix(demand),
        temporal=temporal,
        mode="economic_dispatch",
    )

    model, vars = ESFEX.create_power_system(ps_input)
    jl._model = model
    jl.seval("optimize!(_model)")

    result = ESFEX.extract_solution(model, vars, ps_input)

    assert str(result.status).find("OPTIMAL") >= 0

    # Solar should be used first (free), gas fills the gap
    # RE penetration should be > 0
    assert result.re_penetration > 0.1  # At least some RE used


# =============================================================================
# Parity Tests (compare Python vs Julia)
# =============================================================================

@pytest.mark.julia
def test_simple_dispatch_parity():
    """
    Test that Python and Julia produce similar results for simple dispatch.

    Note: This is a placeholder for full parity testing once the Python
    PuLP implementation is wrapped for comparison.
    """
    # This test will be expanded when we have both implementations
    # running side by side with identical inputs
    pass


# =============================================================================
# Helper Functions
# =============================================================================

def assert_arrays_close(a, b, rtol=1e-3, atol=1e-3, name="arrays"):
    """Assert that two arrays are close within tolerance."""
    a = np.array(a)
    b = np.array(b)

    if a.shape != b.shape:
        raise AssertionError(f"{name} shapes differ: {a.shape} vs {b.shape}")

    if not np.allclose(a, b, rtol=rtol, atol=atol):
        max_diff = np.max(np.abs(a - b))
        raise AssertionError(
            f"{name} differ by up to {max_diff:.6f} "
            f"(rtol={rtol}, atol={atol})"
        )
