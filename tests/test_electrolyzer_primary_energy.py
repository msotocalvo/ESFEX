"""
Tests for Electrolyzer and PrimaryEnergy Julia models.

These tests verify that the Julia implementations build and solve correctly.
"""

import pytest
import numpy as np


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def electrolyzer_config():
    """Simple electrolyzer configuration for testing."""
    return {
        'rated_power': [10.0, 5.0],  # MW per node
        'eff_at_rated': [0.7, 0.7],
        'eff_at_min': [0.6, 0.6],
        'energy_per_kg_h2': 50.0,  # kWh/kg
        'ramp_up': [1.0, 1.0],
        'ramp_down': [1.0, 1.0],
        'invest_cost': [1000.0, 1000.0],  # $/MW
        'invest_max_power': [20.0, 10.0],  # MW
        'fixed_cost': [5.0, 5.0],  # $/MW/h
        'variable_cost': [1.0, 1.0],  # $/MWh
        'water_cost': 0.01,  # $/kg H2
        'life_time': [25.0, 25.0],
    }


@pytest.fixture
def primary_energy_config():
    """Simple primary energy configuration for testing."""
    return {
        'fuels': {
            'Gas': {
                'max_availability': [1000.0, 500.0],  # units/year
                'storage_capacity': [100.0, 50.0],  # units
                'initial_storage_level': [0.5, 0.5],  # fraction
                'min_storage_level': 0.1,
                'import_cost': [5.0, 6.0],  # $/unit
                'transport_cost': 0.1,  # $/unit/km
                'transport_losses': 0.01,  # %/100km
                'emission_factor': 0.2,  # tonnes CO2/MWh_th
            }
        },
        'fuel_definitions': {
            'Gas': {
                'price_base': 10.0,  # $/unit
                'price_growth_rate': 0.02,
                'energy_content': 10.0,  # MWh_th/unit
            }
        },
        'infrastructure': {
            'storage_facilities': {
                'Gas': {
                    'investment_cost': 50.0,  # $/unit
                    'expansion_limit': 1.0,
                    'efficiency': 0.95,
                    'lifetime': 30.0,
                }
            },
            'transport_pipelines': {
                'Gas': {
                    'capacity': 50.0,  # units/day
                    'investment_cost': 10.0,  # $/unit-day/km
                    'expansion_limit': 0.5,
                    'lifetime': 20.0,
                }
            }
        },
        'non_electric_demand': {
            'Industrial_Gas': {
                'fuel': 'Gas',
                'demand': [200.0, 100.0],  # units/year
                'growth_rate': 0.01,
                'seasonal_factors': [1/12] * 12,
            }
        },
        'transport_distances': [[0.0, 100.0], [100.0, 0.0]],  # km
        'penalties': {
            'Loss_of_fuel_supply': 1000.0,
        }
    }


# =============================================================================
# Electrolyzer Tests
# =============================================================================

@pytest.mark.julia
def test_electrolyzer_builds():
    """Test that electrolyzer model builds without errors."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_vector

    jl = get_julia()
    ESFEX = get_esfex_module()

    num_nodes = 2
    num_hours = 24

    # Create electrolyzer config
    config = ESFEX.ElectrolyzerConfig(
        py_to_julia_vector([10.0, 5.0]),  # rated_power
        py_to_julia_vector([0.7, 0.7]),   # eff_at_rated
        py_to_julia_vector([0.6, 0.6]),   # eff_at_min
        50.0,                             # energy_per_kg_h2
        py_to_julia_vector([1.0, 1.0]),   # ramp_up
        py_to_julia_vector([1.0, 1.0]),   # ramp_down
        py_to_julia_vector([1000.0, 1000.0]),  # invest_cost
        py_to_julia_vector([20.0, 10.0]), # invest_max_power
        py_to_julia_vector([5.0, 5.0]),   # fixed_cost
        py_to_julia_vector([1.0, 1.0]),   # variable_cost
        0.01,                             # water_cost
        py_to_julia_vector([25.0, 25.0]), # life_time
    )

    # Create model
    model = jl.seval("using JuMP, HiGHS; Model(HiGHS.Optimizer)")

    # Store in Julia namespace for function calls
    jl._e_model = model
    jl._e_config = config
    jl._e_num_nodes = num_nodes
    jl._e_num_hours = num_hours

    # Build variables
    vars = jl.seval("""
    build_electrolyzer_variables!(
        _e_model, _e_config, _e_num_nodes, _e_num_hours;
        var_prefix=""
    )
    """)

    assert vars is not None
    assert vars.investment is not None
    assert vars.power is not None
    assert vars.h2_production is not None


@pytest.mark.julia
def test_electrolyzer_solves():
    """Test that electrolyzer model solves and produces H2."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_vector

    jl = get_julia()
    ESFEX = get_esfex_module()

    num_nodes = 1
    num_hours = 24

    # Create config with existing capacity (no investment needed)
    config = ESFEX.ElectrolyzerConfig(
        py_to_julia_vector([10.0]),  # rated_power
        py_to_julia_vector([0.7]),   # eff_at_rated
        py_to_julia_vector([0.6]),   # eff_at_min
        50.0,                         # energy_per_kg_h2
        py_to_julia_vector([1.0]),   # ramp_up
        py_to_julia_vector([1.0]),   # ramp_down
        py_to_julia_vector([0.0]),   # invest_cost (no investment)
        py_to_julia_vector([0.0]),   # invest_max_power
        py_to_julia_vector([0.0]),   # fixed_cost
        py_to_julia_vector([1.0]),   # variable_cost
        0.0,                          # water_cost
        py_to_julia_vector([25.0]),  # life_time
    )

    # Create model with simple objective (maximize H2 production)
    model = jl.seval("using JuMP, HiGHS; Model(HiGHS.Optimizer)")

    jl._e_model = model
    jl._e_config = config
    jl._e_num_nodes = num_nodes
    jl._e_num_hours = num_hours

    # Build variables and constraints
    vars = jl.seval("""
    vars = build_electrolyzer_variables!(
        _e_model, _e_config, _e_num_nodes, _e_num_hours;
        var_prefix=""
    )
    add_electrolyzer_constraints!(
        _e_model, vars, _e_config, _e_num_nodes, _e_num_hours
    )
    vars
    """)

    jl._e_vars = vars

    # Set objective: minimize cost (which should maximize H2 since it's free)
    jl.seval("""
    cost_terms = get_electrolyzer_objective_terms(
        _e_vars, _e_config, _e_num_nodes, _e_num_hours
    )
    @objective(_e_model, Min, cost_terms)
    """)

    # Solve
    jl.seval("optimize!(_e_model)")

    # Check solution
    status = str(jl.seval("termination_status(_e_model)"))
    assert "OPTIMAL" in status

    # Extract results
    result = jl.seval("""
    extract_electrolyzer_solution(
        _e_model, _e_vars, _e_config, _e_num_nodes, _e_num_hours
    )
    """)

    # With no investment cost and only variable cost, optimizer should use some power
    # The variable cost is $1/MWh so it should find an optimal solution
    assert result is not None


# =============================================================================
# Primary Energy Tests
# =============================================================================

@pytest.mark.julia
def test_temporal_mapping():
    """Test that temporal mapping is created correctly."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module

    jl = get_julia()
    ESFEX = get_esfex_module()

    # 48 hours with 24-hour primary periods
    temporal = ESFEX.create_temporal_mapping(48, 24, 48)

    assert temporal.num_primary_periods == 2
    assert temporal.num_investment_periods == 1
    assert len(temporal.primary_period_indices) == 2
    assert len(temporal.investment_period_indices) == 1


@pytest.mark.julia
def test_primary_energy_builds():
    """Test that primary energy model builds without errors."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_vector, py_to_julia_matrix

    jl = get_julia()
    ESFEX = get_esfex_module()

    num_nodes = 2
    num_hours = 24

    # Create fuel config
    fuel = ESFEX.FuelConfig(
        "Gas",
        10.0,   # price_base
        0.02,   # price_growth_rate
        10.0,   # energy_content (MWh/unit)
        0.2,    # emission_factor
        py_to_julia_vector([1000.0, 500.0]),  # max_availability
        py_to_julia_vector([100.0, 50.0]),    # storage_capacity
        py_to_julia_vector([0.5, 0.5]),       # initial_storage_level
        0.1,    # min_storage_level
        py_to_julia_vector([5.0, 6.0]),       # import_cost
        0.1,    # transport_cost
        0.01,   # transport_losses
    )

    jl._fuel = fuel
    fuels = jl.seval("FuelConfig[_fuel]")

    # Create infrastructure config
    infra = ESFEX.FuelInfrastructureConfig(
        50.0,   # transport_capacity
        10.0,   # transport_investment_cost
        0.5,    # transport_expansion_limit
        50.0,   # storage_investment_cost
        1.0,    # storage_expansion_limit
        0.95,   # storage_efficiency
        20.0,   # lifetime_transport
        30.0,   # lifetime_storage
        -1.0,   # max_hourly_dispatch_rate (-1 = no limit)
    )

    jl._infra = infra
    infra_dict = jl.seval("Dict{String, FuelInfrastructureConfig}(\"Gas\" => _infra)")

    # Create NE demand
    ne_demand = ESFEX.NonElectricDemandConfig(
        "Industrial",
        "Gas",
        py_to_julia_vector([200.0, 100.0]),  # annual_demand
        0.01,  # growth_rate
        py_to_julia_vector([1/12] * 12),     # seasonal_factors
    )

    jl._ne = ne_demand
    ne_demands = jl.seval("NonElectricDemandConfig[_ne]")

    # Create distances matrix
    distances = np.array([[0.0, 100.0], [100.0, 0.0]])

    # Create input
    jl_input = ESFEX.PrimaryEnergyInput(
        2025,    # year
        2025,    # base_year
        num_nodes,
        num_hours,
        fuels,
        infra_dict,
        ne_demands,
        py_to_julia_matrix(distances),
        jl.seval("Dict{Int, Tuple{String, Float64, Float64, Float64}}()"),  # empty gen map
        24,      # primary_energy_resolution
        num_hours,  # investment_resolution
        0.05,    # discount_rate
        1000.0,  # loss_of_fuel_supply_penalty
        1.0,     # coupling_slack_penalty
        "development",  # mode
        jl.seval("Dict{String, Any}()"),  # cumulative_capacities
        jl.seval("nothing"),  # initial_storage_levels
        False,   # investment_from_master
        jl.seval("nothing"),  # h2_production_hourly
        jl.seval("Dict{Int, Vector{Float64}}()"),  # generator_rated_power
        jl.seval("nothing"),  # electrolyzer_config
    )

    # Create model
    model = jl.seval("using JuMP, HiGHS; Model(HiGHS.Optimizer)")

    jl._pe_model = model
    jl._pe_input = jl_input

    # Build primary energy model
    vars, temporal, prices = jl.seval("""
    vars, temporal, prices = create_primary_energy_model(_pe_model, _pe_input)
    (vars, temporal, prices)
    """)

    assert vars is not None
    assert temporal is not None
    assert prices is not None


@pytest.mark.julia
def test_primary_energy_solves():
    """Test that primary energy model solves with simple case."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_vector, py_to_julia_matrix

    jl = get_julia()
    ESFEX = get_esfex_module()

    num_nodes = 1
    num_hours = 24

    # Create minimal config: single node, single fuel, no NE demand
    fuel = ESFEX.FuelConfig(
        "Gas",
        10.0,   # price_base
        0.0,    # price_growth_rate
        10.0,   # energy_content
        0.0,    # emission_factor
        py_to_julia_vector([1000.0]),  # max_availability
        py_to_julia_vector([100.0]),   # storage_capacity
        py_to_julia_vector([0.5]),     # initial_storage_level
        0.0,    # min_storage_level (allow full depletion)
        py_to_julia_vector([0.0]),     # import_cost
        0.0,    # transport_cost
        0.0,    # transport_losses
    )

    jl._fuel = fuel
    fuels = jl.seval("FuelConfig[_fuel]")

    # No infrastructure needed for single node
    infra_dict = jl.seval("Dict{String, FuelInfrastructureConfig}()")

    # No NE demand
    ne_demands = jl.seval("NonElectricDemandConfig[]")

    # Single node distance matrix
    distances = np.array([[0.0]])

    jl_input = ESFEX.PrimaryEnergyInput(
        2025, 2025,
        num_nodes, num_hours,
        fuels, infra_dict, ne_demands,
        py_to_julia_matrix(distances),
        jl.seval("Dict{Int, Tuple{String, Float64, Float64, Float64}}()"),
        24, num_hours, 0.05, 1000.0,
        1.0,  # coupling_slack_penalty
        "development",
        jl.seval("Dict{String, Any}()"),
        jl.seval("nothing"),
        False,  # investment_from_master
        jl.seval("nothing"),  # h2_production_hourly
        jl.seval("Dict{Int, Vector{Float64}}()"),  # generator_rated_power
        jl.seval("nothing"),  # electrolyzer_config
    )

    model = jl.seval("using JuMP, HiGHS; Model(HiGHS.Optimizer)")

    jl._pe_model = model
    jl._pe_input = jl_input

    # Build and solve
    jl.seval("""
    vars, temporal, prices = create_primary_energy_model(_pe_model, _pe_input)
    cost_terms = get_primary_energy_objective_terms(vars, _pe_input, temporal, prices)
    # cost_terms is Dict{Symbol, AffExpr} (one entry per PE sub-cost).
    @objective(_pe_model, Min, sum(values(cost_terms)))
    optimize!(_pe_model)
    """)

    status = str(jl.seval("termination_status(_pe_model)"))
    assert "OPTIMAL" in status


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.julia
def test_primary_energy_power_system_coupling():
    """Test that primary energy and power system can be coupled."""
    from esfex.bridge.julia_setup import get_julia, get_esfex_module
    from esfex.bridge.converters import py_to_julia_vector, py_to_julia_matrix

    jl = get_julia()
    ESFEX = get_esfex_module()

    num_nodes = 1
    num_hours = 24

    # Create a gas generator (reservoir/freq/risk trailing defaults required).
    z = lambda: py_to_julia_vector([0.0])
    o = lambda: py_to_julia_vector([1.0])
    no_inflow = py_to_julia_matrix(np.zeros((num_hours, num_nodes)))
    gas_gen = ESFEX.GeneratorConfig(
        "GasPlant", "Non-renewable", "Gas",
        py_to_julia_vector([100.0]),  # rated_power
        py_to_julia_vector([0.0]),    # min_power
        py_to_julia_vector([0.45]),   # efficiency_rated
        py_to_julia_vector([0.35]),   # efficiency_min
        py_to_julia_vector([1.0]),    # ramp_up
        py_to_julia_vector([1.0]),    # ramp_down
        py_to_julia_vector([0.0]),    # min_up_time
        py_to_julia_vector([0.0]),    # min_down_time
        py_to_julia_vector([0.0]),    # start_up_cost
        py_to_julia_vector([50.0]),   # fuel_cost
        py_to_julia_vector([0.0]),    # fixed_cost
        py_to_julia_vector([0.0]),    # maintenance_cost
        py_to_julia_vector([0.0]),    # inertia
        py_to_julia_vector([0.0]),    # invest_cost
        py_to_julia_vector([0.0]),    # invest_max
        py_to_julia_matrix(np.ones((num_hours, num_nodes))),  # availability
        True,   # reservable
        py_to_julia_vector([25.0]),   # life_time
        py_to_julia_vector([0.0]),    # initial_age
        py_to_julia_vector([0.0]),    # degradation_rate
        py_to_julia_vector([0.0]),    # decommissioning_cost
        50.0, "AC",
        z(), z(), z(), o(),  # reservoir capacity/initial/min/max
        no_inflow, o(), z(), z(), o(), True,
        z(), z(), o(),                # reservoir invest_cost/max, risk_coefficient
    )

    jl._gen = gas_gen
    generators = jl.seval("GeneratorConfig[_gen]")

    # Create network — full 18-arg NetworkConfig (back-compat ctor removed).
    buses = jl.seval("BusData[]")
    jl.seval("push!")(buses, ESFEX.BusData(
        1, 1, 220.0, 50.0, "AC", "slack", "load", 1.0))
    network = ESFEX.NetworkConfig(
        1, 1, buses, py_to_julia_vector([1]),
        py_to_julia_matrix(np.array([[0.0]])),
        py_to_julia_matrix(np.array([[0.0]])),
        100.0, 0.4, 220.0, 0.524, 1,
        py_to_julia_vector([0.0]),  # transference_invest_cost
        py_to_julia_vector([0.0]),  # transference_invest_max
        jl.seval("TransmissionLineData[]"),
        jl.seval("TransformerData[]"),
        jl.seval("ACDCConverterData[]"),
        jl.seval("FrequencyConverterData[]"),
        0.1,
    )

    # Create temporal
    temporal = ESFEX.TemporalConfig(num_hours, 1, 168, 24, 8760, 168, 6, 6, 4)

    # Create demand
    demand = np.full((num_hours, num_nodes), 50.0)

    # Create PowerSystem input
    ps_input = ESFEX.PowerSystemInput(
        name="Test",
        year=2025,
        base_year=2025,
        network=network,
        generators=generators,
        batteries=jl.seval("BatteryConfig[]"),
        demand=py_to_julia_matrix(demand),
        temporal=temporal,
        mode="economic_dispatch",
    )

    # Build PowerSystem
    ps_model, ps_vars = ESFEX.create_power_system(ps_input)

    # Create primary energy with coupling to generator
    fuel = ESFEX.FuelConfig(
        "Gas", 10.0, 0.0, 10.0, 0.0,
        py_to_julia_vector([1000.0]),
        py_to_julia_vector([500.0]),
        py_to_julia_vector([0.5]),
        0.0,
        py_to_julia_vector([0.0]),
        0.0, 0.0,
    )

    jl._fuel = fuel
    fuels = jl.seval("FuelConfig[_fuel]")

    # Generator fuel map: gen 1 uses Gas
    # MWhe/unit = efficiency * energy_content = 0.45 * 10 = 4.5
    gen_map = jl.seval("Dict{Int, Tuple{String, Float64, Float64, Float64}}(1 => (\"Gas\", 4.5, 10.0, 0.45))")

    # Build generator rated power for coupling
    gen_rated = jl.seval("Dict{Int, Vector{Float64}}(1 => [100.0])")

    pe_input = ESFEX.PrimaryEnergyInput(
        2025, 2025,
        num_nodes, num_hours,
        fuels,
        jl.seval("Dict{String, FuelInfrastructureConfig}()"),
        jl.seval("NonElectricDemandConfig[]"),
        py_to_julia_matrix(np.array([[0.0]])),
        gen_map,
        24, num_hours, 0.05, 1000.0,
        1.0,  # coupling_slack_penalty
        "economic_dispatch",
        jl.seval("Dict{String, Any}()"),
        jl.seval("nothing"),
        False,  # investment_from_master
        jl.seval("nothing"),  # h2_production_hourly
        gen_rated,  # generator_rated_power
        jl.seval("nothing"),  # electrolyzer_config
    )

    jl._ps_model = ps_model
    jl._pe_input = pe_input
    jl._ps_vars = ps_vars

    # Create primary energy in same model
    pe_vars, pe_temporal, pe_prices = jl.seval("""
    pe_vars, pe_temporal, pe_prices = create_primary_energy_model(_ps_model, _pe_input)
    (pe_vars, pe_temporal, pe_prices)
    """)

    # Add coupling constraints
    jl._pe_vars = pe_vars
    jl.seval("""
    couple_primary_energy_to_power_system!(_ps_model, _pe_vars, _ps_vars, _pe_input)
    """)

    # Add primary energy costs to objective
    jl._pe_temporal = pe_temporal
    jl._pe_prices = pe_prices
    jl.seval("""
    pe_costs = get_primary_energy_objective_terms(_pe_vars, _pe_input, _pe_temporal, _pe_prices)
    current_obj = objective_function(_ps_model)
    @objective(_ps_model, Min, current_obj + sum(values(pe_costs)))
    """)

    # Solve
    jl.seval("optimize!(_ps_model)")

    status = str(jl.seval("termination_status(_ps_model)"))
    assert "OPTIMAL" in status

    # Extract and check that fuel was used
    obj_value = float(jl.seval("objective_value(_ps_model)"))
    assert obj_value > 0
