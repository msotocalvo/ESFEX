"""
types.jl - Core type definitions for ESFEX optimization models

Defines Julia structs that mirror the Python Pydantic models for
seamless data transfer between Python and Julia.

All vectors are indexed per-node unless otherwise specified.
Matrices are indexed as [time, node] (Julia column-major order).
"""

# UNIT CONVENTION: All monetary values in this module are in M$ (millions of USD).
# Physical units: MW (power), MWh (energy), tonnes (CO2), km, etc.
# The Python adapter layer (converters.py) scales user-facing $ values by 1e-6.

# Note: JuMP and MOI are imported from the parent module (ESFEX.jl)
# These will be available when types.jl is included from ESFEX.jl

using Dates: isleapyear

"""Standard (non-leap) year hours. Use `hours_in_year(year)` for year-specific calculations."""
const HOURS_STD_YEAR = 8760

"""Return number of hours in a calendar year (8760 or 8784 for leap years)."""
hours_in_year(year::Int) = isleapyear(year) ? 8784 : HOURS_STD_YEAR

# =============================================================================
# PWL (Piecewise Linear) Transmission Loss Model
# =============================================================================

"""
    PWLLossSegments

Pre-computed piecewise linear loss segments for transmission lines.

Approximates quadratic losses P_loss(f) = g_l × f² using N linear segments.
For each line l and segment k, the marginal loss slope is m_k = g_l × (f_k + f_{k-1}).
Convexity guarantees correct LP behavior without binary variables.
"""
struct PWLLossSegments
    num_segments::Int                       # N segments per line
    segment_widths::Vector{Vector{Float64}} # [line][k] = Δf_k = f_max/N
    slopes::Vector{Vector{Float64}}         # [line][k] = m_k = g_l × (2k-1) × Δf
    conductances::Vector{Float64}           # [line] = g_l = R/(R²+X²)
end

# =============================================================================
# Configuration Structs (Input)
# =============================================================================

"""
    TransmissionLineData

Per-line transmission data for enhanced DC/AC power flow.

# Fields
- `line_id::String`: Unique line identifier (e.g., "line_0")
- `from_node::Int`: Source node (1-indexed)
- `to_node::Int`: Destination node (1-indexed)
- `capacity_mw::Float64`: Thermal capacity per circuit (MW)
- `reactance_pu::Float64`: Series reactance in p.u. (on system base)
- `resistance_pu::Float64`: Series resistance in p.u. (for AC PF / losses)
- `susceptance_pu::Float64`: Shunt charging susceptance in p.u. (for AC PF)
- `length_km::Float64`: Line length (km)
- `voltage_kv::Float64`: Rated voltage (kV)
- `num_circuits::Int`: Number of parallel circuits
"""
struct TransmissionLineData
    line_id::String
    from_node::Int
    to_node::Int
    capacity_mw::Float64
    reactance_pu::Float64
    resistance_pu::Float64
    susceptance_pu::Float64
    length_km::Float64
    voltage_kv::Float64
    num_circuits::Int
    frequency_hz::Float64
    current_type::String  # "AC" or "DC"
end

"""
    BusData

Electrical bus within a geographic node.

# Fields
- `bus_id::Int`: Bus position index (1-indexed)
- `parent_node::Int`: Parent geographic node (1-indexed)
- `voltage_kv::Float64`: Nominal voltage (kV)
- `frequency_hz::Float64`: Nominal frequency (Hz)
- `current_type::String`: "AC" or "DC"
- `bus_type::String`: "PQ", "PV", or "slack" (AC power flow classification)
- `role::String`: "connection", "load", or "mixed" — semantic purpose of the bus.
  Connection buses do not carry demand and are skipped from load_shed/reserve
  variable creation; their KCL only enforces flow balance.
- `demand_fraction::Float64`: Fraction of parent node's demand served by this bus
"""
struct BusData
    bus_id::Int
    parent_node::Int
    voltage_kv::Float64
    frequency_hz::Float64
    current_type::String
    bus_type::String
    role::String
    demand_fraction::Float64
end

"""
    TransformerData

Transformer branch connecting two buses at different voltage levels.

In DC power flow: modeled as a branch with series reactance.
In AC power flow: full model with tap ratio, resistance, and reactance.

# Fields
- `name::String`: Transformer identifier
- `from_node::Int`: Primary (HV) bus (1-indexed)
- `to_node::Int`: Secondary (LV) bus (1-indexed)
- `from_voltage_kv::Float64`: Primary voltage (kV)
- `to_voltage_kv::Float64`: Secondary voltage (kV)
- `rated_power_mva::Float64`: Rated apparent power (MVA)
- `impedance_pu::Float64`: Series impedance magnitude (p.u. on transformer base)
- `resistance_pu::Float64`: Series resistance (p.u.), derived from losses
- `reactance_pu::Float64`: Series reactance (p.u.), sqrt(z² - r²)
- `tap_ratio::Float64`: Off-nominal tap ratio = from_voltage / to_voltage (p.u.)
- `losses_fraction::Float64`: Active power losses as fraction of rated
"""
struct TransformerData
    name::String
    from_node::Int
    to_node::Int
    from_voltage_kv::Float64
    to_voltage_kv::Float64
    rated_power_mva::Float64
    impedance_pu::Float64
    resistance_pu::Float64
    reactance_pu::Float64
    tap_ratio::Float64
    losses_fraction::Float64
end

"""
    ACDCConverterData

AC/DC converter (rectifier/inverter) branch connecting AC and DC buses.

In DC power flow: bidirectional power transfer with directional efficiencies.
from_node = AC side, to_node = DC side.

# Fields
- `name::String`: Converter identifier
- `converter_type::String`: "VSC" (Voltage Source Converter) or "LCC" (Line Commutated)
- `from_node::Int`: AC-side bus (1-indexed)
- `to_node::Int`: DC-side bus (1-indexed)
- `from_voltage_kv::Float64`: AC-side voltage (kV)
- `dc_voltage_kv::Float64`: DC-side voltage (kV)
- `rated_power_mva::Float64`: Rated apparent power (MVA)
- `min_power_mva::Float64`: Minimum operating power (MVA)
- `efficiency_rectify::Float64`: AC→DC efficiency (0-1)
- `efficiency_invert::Float64`: DC→AC efficiency (0-1)
- `standby_losses_mw::Float64`: Standby power losses (MW)
- `reactive_power_min_mvar::Float64`: Min reactive power (MVAr, negative = absorb)
- `reactive_power_max_mvar::Float64`: Max reactive power (MVAr)
- `power_factor::Float64`: Power factor
- `impedance_pu::Float64`: Series impedance (p.u.)
- `resistance_pu::Float64`: Series resistance (p.u.)
- `invest_cost::Float64`: Investment cost (\$/MW)
- `fixed_cost::Float64`: Fixed O&M cost (\$/MW/year)
- `variable_cost::Float64`: Variable cost (\$/MWh)
- `invest_max_power::Float64`: Maximum investment capacity (MW)
- `life_time::Int`: Economic lifetime (years)
- `initial_age::Int`: Initial age (years)
- `degradation_rate::Float64`: Annual degradation rate
"""
struct ACDCConverterData
    name::String
    converter_type::String
    from_node::Int
    to_node::Int
    from_voltage_kv::Float64
    dc_voltage_kv::Float64
    rated_power_mva::Float64
    min_power_mva::Float64
    efficiency_rectify::Float64
    efficiency_invert::Float64
    standby_losses_mw::Float64
    reactive_power_min_mvar::Float64
    reactive_power_max_mvar::Float64
    power_factor::Float64
    impedance_pu::Float64
    resistance_pu::Float64
    invest_cost::Float64
    fixed_cost::Float64
    variable_cost::Float64
    invest_max_power::Float64
    life_time::Int
    initial_age::Int
    degradation_rate::Float64
end

"""
    FrequencyConverterData

Frequency converter branch connecting buses at different frequencies.

Bidirectional power transfer with directional efficiencies.
from_node = frequency A side, to_node = frequency B side.

# Fields
- `name::String`: Converter identifier
- `from_node::Int`: Frequency-A bus (1-indexed)
- `to_node::Int`: Frequency-B bus (1-indexed)
- `from_frequency_hz::Float64`: Frequency on from_node side (Hz)
- `to_frequency_hz::Float64`: Frequency on to_node side (Hz)
- `rated_power_mva::Float64`: Rated apparent power (MVA)
- `min_power_mva::Float64`: Minimum operating power (MVA)
- `efficiency_a_to_b::Float64`: A→B efficiency (0-1)
- `efficiency_b_to_a::Float64`: B→A efficiency (0-1)
- `standby_losses_mw::Float64`: Standby power losses (MW)
- `reactive_power_min_mvar::Float64`: Min reactive power (MVAr)
- `reactive_power_max_mvar::Float64`: Max reactive power (MVAr)
- `impedance_pu::Float64`: Series impedance (p.u.)
- `resistance_pu::Float64`: Series resistance (p.u.)
- `invest_cost::Float64`: Investment cost (\$/MW)
- `fixed_cost::Float64`: Fixed O&M cost (\$/MW/year)
- `variable_cost::Float64`: Variable cost (\$/MWh)
- `invest_max_power::Float64`: Maximum investment capacity (MW)
- `life_time::Int`: Economic lifetime (years)
- `initial_age::Int`: Initial age (years)
- `degradation_rate::Float64`: Annual degradation rate
"""
struct FrequencyConverterData
    name::String
    from_node::Int
    to_node::Int
    from_frequency_hz::Float64
    to_frequency_hz::Float64
    rated_power_mva::Float64
    min_power_mva::Float64
    efficiency_a_to_b::Float64
    efficiency_b_to_a::Float64
    standby_losses_mw::Float64
    reactive_power_min_mvar::Float64
    reactive_power_max_mvar::Float64
    impedance_pu::Float64
    resistance_pu::Float64
    invest_cost::Float64
    fixed_cost::Float64
    variable_cost::Float64
    invest_max_power::Float64
    life_time::Int
    initial_age::Int
    degradation_rate::Float64
end

"""
    NetworkConfig

Network configuration for DC power flow modeling.

# Fields
- `num_nodes::Int`: Number of geographic nodes in the network
- `num_buses::Int`: Number of electrical buses (>= num_nodes)
- `buses::Vector{BusData}`: Bus definitions (one or more per node)
- `bus_to_node::Vector{Int}`: Mapping from bus index to parent node index (1-indexed)
- `connections::Matrix{Float64}`: Adjacency matrix with line capacities (MW)
- `distances::Matrix{Float64}`: Distance matrix (km)
- `base_impedance::Float64`: Base impedance (Ω)
- `reactance_per_km::Float64`: Line reactance per km (Ω/km)
- `voltage_level_kv::Float64`: Nominal voltage level (kV)
- `max_angle_diff_rad::Float64`: Maximum voltage angle difference (radians)
- `slack_bus::Int`: Slack bus index (1-indexed in Julia)
- `transference_invest_cost::Vector{Float64}`: Transmission investment cost per node (\$/MW)
- `transference_invest_max::Vector{Float64}`: Maximum transmission investment per node (MW)
- `transmission_lines::Vector{TransmissionLineData}`: Per-line data (empty = legacy adjacency mode)
- `transformers::Vector{TransformerData}`: Transformer branches (empty = none)
- `acdc_converters::Vector{ACDCConverterData}`: AC/DC converter branches (empty = none)
- `freq_converters::Vector{FrequencyConverterData}`: Frequency converter branches (empty = none)
"""
struct NetworkConfig
    num_nodes::Int
    num_buses::Int
    buses::Vector{BusData}
    bus_to_node::Vector{Int}
    connections::Matrix{Float64}
    distances::Matrix{Float64}
    base_impedance::Float64
    reactance_per_km::Float64
    voltage_level_kv::Float64
    max_angle_diff_rad::Float64
    slack_bus::Int
    transference_invest_cost::Vector{Float64}
    transference_invest_max::Vector{Float64}
    transmission_lines::Vector{TransmissionLineData}
    transformers::Vector{TransformerData}
    acdc_converters::Vector{ACDCConverterData}
    freq_converters::Vector{FrequencyConverterData}
    default_r_to_x_ratio::Float64       # Default R/X ratio when resistance not specified (0.1)
end

"""
    GeneratorConfig

Generator/technology configuration.

# Fields
- `name::String`: Generator name/identifier
- `type::String`: Technology type ("Renewable", "Non-renewable", "Storage")
- `fuel::String`: Fuel type (e.g., "Solar", "Wind", "Gas", "Diesel")
- `rated_power::Vector{Float64}`: Rated power per node (MW)
- `min_power::Vector{Float64}`: Minimum power as fraction of rated (per node)
- `efficiency_rated::Vector{Float64}`: Efficiency at rated power
- `efficiency_min::Vector{Float64}`: Efficiency at minimum power
- `ramp_up::Vector{Float64}`: Ramp up rate per node (pu/min)
- `ramp_down::Vector{Float64}`: Ramp down rate per node (pu/min)
- `min_up_time::Vector{Float64}`: Minimum up time per node (hours)
- `min_down_time::Vector{Float64}`: Minimum down time per node (hours)
- `start_up_cost::Vector{Float64}`: Startup cost per node (\$/start)
- `fuel_cost::Vector{Float64}`: Variable fuel cost per node (\$/MWh)
- `fixed_cost::Vector{Float64}`: Fixed O&M cost per node (\$/MWh)
- `maintenance_cost::Vector{Float64}`: Maintenance cost per node (\$/MWh)
- `inertia::Vector{Float64}`: Inertia constant H per node (seconds)
- `invest_cost::Vector{Float64}`: Investment cost per node (\$/MW)
- `invest_max::Vector{Float64}`: Maximum investment per node (MW)
- `availability::Matrix{Float64}`: Availability matrix [hours × nodes]
- `reservable::Bool`: Whether this generator can provide reserves
- `life_time::Vector{Float64}`: Economic lifetime per node (years)
- `initial_age::Vector{Float64}`: Initial age per node (years)
- `degradation_rate::Vector{Float64}`: Annual degradation rate per node
- `decommissioning_cost::Vector{Float64}`: Decommissioning cost per node (\$/MW)
- `frequency_hz::Float64`: Operating frequency (Hz), default 50.0
- `current_type::String`: Current type ("AC", "DC", or "AC_DC")
- `reservoir_capacity::Vector{Float64}`: Reservoir capacity per node (MWh-eq), 0 = no reservoir
- `reservoir_initial_level::Vector{Float64}`: Initial reservoir level fraction (0-1)
- `reservoir_min_level::Vector{Float64}`: Minimum reservoir level fraction (0-1)
- `reservoir_max_level::Vector{Float64}`: Maximum reservoir level fraction (0-1)
- `reservoir_inflow::Matrix{Float64}`: Inflow time series [hours × nodes] (MW-eq)
- `reservoir_turbine_efficiency::Vector{Float64}`: Water→electricity efficiency (0-1)
- `reservoir_evaporation_rate::Vector{Float64}`: Per-hour evaporation fraction
- `reservoir_pump_capacity::Vector{Float64}`: Pump-back capacity per node (MW), 0 = no pumping
- `reservoir_pump_efficiency::Vector{Float64}`: Pump-back efficiency (0-1)
- `reservoir_spillage_allowed::Bool`: Whether reservoir spillage is permitted
- `reservoir_invest_cost::Vector{Float64}`: Reservoir expansion cost per node (\$/MWh)
- `reservoir_invest_max::Vector{Float64}`: Max reservoir expansion per node (MWh)
"""
struct GeneratorConfig
    name::String
    type::String
    fuel::String
    rated_power::Vector{Float64}
    min_power::Vector{Float64}
    efficiency_rated::Vector{Float64}
    efficiency_min::Vector{Float64}
    ramp_up::Vector{Float64}
    ramp_down::Vector{Float64}
    min_up_time::Vector{Float64}
    min_down_time::Vector{Float64}
    start_up_cost::Vector{Float64}
    fuel_cost::Vector{Float64}
    fixed_cost::Vector{Float64}
    maintenance_cost::Vector{Float64}
    inertia::Vector{Float64}
    invest_cost::Vector{Float64}
    invest_max::Vector{Float64}
    availability::Matrix{Float64}
    reservable::Bool
    life_time::Vector{Float64}
    initial_age::Vector{Float64}
    degradation_rate::Vector{Float64}
    decommissioning_cost::Vector{Float64}
    frequency_hz::Float64
    current_type::String
    # Reservoir hydroelectric (optional — zeros = no reservoir)
    reservoir_capacity::Vector{Float64}
    reservoir_initial_level::Vector{Float64}
    reservoir_min_level::Vector{Float64}
    reservoir_max_level::Vector{Float64}
    reservoir_inflow::Matrix{Float64}
    reservoir_turbine_efficiency::Vector{Float64}
    reservoir_evaporation_rate::Vector{Float64}
    reservoir_pump_capacity::Vector{Float64}
    reservoir_pump_efficiency::Vector{Float64}
    reservoir_spillage_allowed::Bool
    reservoir_invest_cost::Vector{Float64}
    reservoir_invest_max::Vector{Float64}
    risk_coefficient::Vector{Float64}     # Geographic risk derating per node (0-1)
    # Mandatory minimum release (MW-eq) per node — ecological / minimum
    # environmental flow. Default 0 is neutral. (Minimum stable generation is
    # already handled by the existing `min_power` field.)
    reservoir_min_release::Vector{Float64}
    # Hydraulic cascade. Water released by this reservoir (turbined + spilled)
    # becomes inflow to the reservoir named `cascade_downstream` after
    # `cascade_delay_hours` of travel time. Empty name = terminal reservoir
    # (its release leaves the modelled system). Plant-level (scalar), since the
    # cascade topology links whole hydro plants, not individual nodes.
    cascade_downstream::String
    cascade_delay_hours::Int
end

# Backward-compatible constructors. The struct grew over time, so older call
# sites pass fewer positional arguments and the newer fields take neutral
# defaults: reservoir_min_release = zeros, cascade_downstream = "" (terminal),
# cascade_delay_hours = 0. Dispatch is by argument count (39 / 40 / full 42).

# 39-arg: through risk_coefficient.
function GeneratorConfig(
    name, type, fuel, rated_power, min_power, efficiency_rated, efficiency_min,
    ramp_up, ramp_down, min_up_time, min_down_time, start_up_cost, fuel_cost,
    fixed_cost, maintenance_cost, inertia, invest_cost, invest_max, availability,
    reservable, life_time, initial_age, degradation_rate, decommissioning_cost,
    frequency_hz, current_type, reservoir_capacity, reservoir_initial_level,
    reservoir_min_level, reservoir_max_level, reservoir_inflow,
    reservoir_turbine_efficiency, reservoir_evaporation_rate,
    reservoir_pump_capacity, reservoir_pump_efficiency, reservoir_spillage_allowed,
    reservoir_invest_cost, reservoir_invest_max, risk_coefficient,
)
    n = length(rated_power)
    return GeneratorConfig(
        name, type, fuel, rated_power, min_power, efficiency_rated, efficiency_min,
        ramp_up, ramp_down, min_up_time, min_down_time, start_up_cost, fuel_cost,
        fixed_cost, maintenance_cost, inertia, invest_cost, invest_max, availability,
        reservable, life_time, initial_age, degradation_rate, decommissioning_cost,
        frequency_hz, current_type, reservoir_capacity, reservoir_initial_level,
        reservoir_min_level, reservoir_max_level, reservoir_inflow,
        reservoir_turbine_efficiency, reservoir_evaporation_rate,
        reservoir_pump_capacity, reservoir_pump_efficiency, reservoir_spillage_allowed,
        reservoir_invest_cost, reservoir_invest_max, risk_coefficient,
        zeros(Float64, n), "", 0,  # min_release, cascade_downstream, cascade_delay_hours
    )
end

# 40-arg: through reservoir_min_release (cascade defaults to none).
function GeneratorConfig(
    name, type, fuel, rated_power, min_power, efficiency_rated, efficiency_min,
    ramp_up, ramp_down, min_up_time, min_down_time, start_up_cost, fuel_cost,
    fixed_cost, maintenance_cost, inertia, invest_cost, invest_max, availability,
    reservable, life_time, initial_age, degradation_rate, decommissioning_cost,
    frequency_hz, current_type, reservoir_capacity, reservoir_initial_level,
    reservoir_min_level, reservoir_max_level, reservoir_inflow,
    reservoir_turbine_efficiency, reservoir_evaporation_rate,
    reservoir_pump_capacity, reservoir_pump_efficiency, reservoir_spillage_allowed,
    reservoir_invest_cost, reservoir_invest_max, risk_coefficient,
    reservoir_min_release,
)
    return GeneratorConfig(
        name, type, fuel, rated_power, min_power, efficiency_rated, efficiency_min,
        ramp_up, ramp_down, min_up_time, min_down_time, start_up_cost, fuel_cost,
        fixed_cost, maintenance_cost, inertia, invest_cost, invest_max, availability,
        reservable, life_time, initial_age, degradation_rate, decommissioning_cost,
        frequency_hz, current_type, reservoir_capacity, reservoir_initial_level,
        reservoir_min_level, reservoir_max_level, reservoir_inflow,
        reservoir_turbine_efficiency, reservoir_evaporation_rate,
        reservoir_pump_capacity, reservoir_pump_efficiency, reservoir_spillage_allowed,
        reservoir_invest_cost, reservoir_invest_max, risk_coefficient,
        reservoir_min_release, "", 0,  # cascade_downstream, cascade_delay_hours
    )
end

"""
    BatteryConfig

Battery/storage configuration.

# Fields
- `name::String`: Battery name/identifier
- `capacity::Vector{Float64}`: Energy capacity per node (MWh)
- `max_charge_power::Vector{Float64}`: Maximum charge power per node (MW)
- `max_discharge_power::Vector{Float64}`: Maximum discharge power per node (MW)
- `charge_efficiency::Vector{Float64}`: Charging efficiency (0-1)
- `discharge_efficiency::Vector{Float64}`: Discharging efficiency (0-1)
- `soc_min::Vector{Float64}`: Minimum SOC as fraction (0-1)
- `soc_max::Vector{Float64}`: Maximum SOC as fraction (0-1)
- `soc_initial::Vector{Float64}`: Initial SOC as fraction (0-1)
- `self_discharge::Vector{Float64}`: Self-discharge rate per hour
- `invest_cost_power::Vector{Float64}`: Power investment cost (\$/MW)
- `invest_cost_capacity::Vector{Float64}`: Energy investment cost (\$/MWh)
- `invest_max_power::Vector{Float64}`: Max power investment (MW)
- `invest_max_capacity::Vector{Float64}`: Max energy investment (MWh)
- `life_time::Vector{Float64}`: Economic lifetime per node (years)
- `initial_age::Vector{Float64}`: Initial age per node (years)
- `decommissioning_cost::Vector{Float64}`: Decommissioning cost per node (\$/MW)
- `min_duration_hours::Float64`: Minimum energy-to-power ratio (hours)
- `max_duration_hours::Float64`: Maximum energy-to-power ratio (hours)
- `maintenance_cost::Vector{Float64}`: Maintenance cost per node (\$/MWh throughput)
- `inertia::Vector{Float64}`: Inertia constant per node (seconds) - for synthetic inertia contribution
- `spillage::Bool`: Whether spillage is allowed for this battery (release energy without grid injection)
- `current_type::String`: Current type ("AC" or "DC")
- `degradation_rate::Vector{Float64}`: Age-based capacity degradation rate per node
"""
struct BatteryConfig
    name::String
    capacity::Vector{Float64}
    max_charge_power::Vector{Float64}
    max_discharge_power::Vector{Float64}
    charge_efficiency::Vector{Float64}
    discharge_efficiency::Vector{Float64}
    soc_min::Vector{Float64}
    soc_max::Vector{Float64}
    soc_initial::Vector{Float64}
    self_discharge::Vector{Float64}
    invest_cost_power::Vector{Float64}
    invest_cost_capacity::Vector{Float64}
    invest_max_power::Vector{Float64}
    invest_max_capacity::Vector{Float64}
    life_time::Vector{Float64}
    initial_age::Vector{Float64}
    decommissioning_cost::Vector{Float64}
    min_duration_hours::Float64
    max_duration_hours::Float64
    maintenance_cost::Vector{Float64}  # Matches Python legacy batteries[bat]['maintenance_cost'][node]
    inertia::Vector{Float64}  # Matches Python legacy batteries[bat]['inertia'][node]
    spillage::Bool  # Matches Python legacy batteries[bat].get('spillage', False)
    current_type::String  # "AC" or "DC"
    degradation_rate::Vector{Float64}  # Age-based capacity degradation rate per node
    throughput_degradation_cost::Vector{Float64}  # $/MWh discharged — cycling wear cost
    risk_coefficient::Vector{Float64}  # Geographic risk derating per node (0-1)
end

# =============================================================================
# Candidate Technology Structs (for per-technology investment in MasterProblem)
# =============================================================================

"""
    TechnologyConfig

Candidate generation technology for new investment.
Unlike GeneratorConfig (existing physical units), technologies define
what CAN be built. The master problem creates investment variables per-technology,
not per-generator.

# Fields
- `name::String`: Technology name (e.g., "Solar PV", "Wind")
- `type::String`: "Renewable" or "Non-renewable"
- `fuel::String`: Fuel type (e.g., "Solar", "Wind", "Gas")
- `invest_cost::Vector{Float64}`: Investment cost per bus (\$/MW)
- `invest_max::Vector{Float64}`: Maximum investment per bus (MW)
- `availability::Matrix{Float64}`: Availability matrix [hours × buses]
- Other fields mirror GeneratorConfig for operational dispatch
"""
struct TechnologyConfig
    name::String
    type::String
    fuel::String
    invest_cost::Vector{Float64}
    invest_max::Vector{Float64}
    availability::Matrix{Float64}
    eff_at_rated::Vector{Float64}
    eff_at_min::Vector{Float64}
    ramp_up::Vector{Float64}
    ramp_down::Vector{Float64}
    min_up_time::Vector{Float64}
    min_down_time::Vector{Float64}
    min_power::Vector{Float64}
    fuel_cost::Vector{Float64}
    fixed_cost::Vector{Float64}
    maintenance_cost::Vector{Float64}
    start_up_cost::Vector{Float64}
    inertia::Vector{Float64}
    life_time::Vector{Float64}
    degradation_rate::Vector{Float64}
    decommissioning_cost::Vector{Float64}
    frequency_hz::Float64
    current_type::String
    reservable::Bool
    risk_coefficient::Vector{Float64}  # Geographic risk derating per node (0-1)
end

"""
    BatteryTechnologyConfig

Candidate battery/storage technology for new investment.

# Fields
- `name::String`: Technology name (e.g., "Lithium-Ion")
- Investment: cost and max per bus for power (MW) and capacity (MWh)
- Duration: min/max energy-to-power ratio
- Performance: charge/discharge efficiency, degradation
"""
struct BatteryTechnologyConfig
    name::String
    invest_cost_power::Vector{Float64}
    invest_cost_capacity::Vector{Float64}
    invest_max_power::Vector{Float64}
    invest_max_capacity::Vector{Float64}
    min_duration_hours::Float64
    max_duration_hours::Float64
    charge_efficiency::Vector{Float64}
    discharge_efficiency::Vector{Float64}
    degradation_rate::Vector{Float64}
    life_time::Vector{Float64}
    soc_initial::Vector{Float64}
    soc_min::Vector{Float64}
    soc_max::Vector{Float64}
    maintenance_cost::Vector{Float64}
    inertia::Vector{Float64}
    throughput_degradation_cost::Vector{Float64}
    spillage::Bool
    current_type::String
    decommissioning_cost::Vector{Float64}
    risk_coefficient::Vector{Float64}  # Geographic risk derating per node (0-1)
end

"""
    TemporalConfig

Temporal configuration for simulation.

# Fields
- `hours::Int`: Total simulation hours
- `resolution_hours::Int`: Time step resolution (hours)
- `rolling_horizon_hours::Int`: Rolling horizon window size (hours)
- `overlap_hours::Int`: Overlap between windows (hours)
- `investment_resolution::Int`: Investment decision resolution (hours)
- `primary_energy_resolution::Int`: Primary energy model resolution (hours)
- `battery_soc_resolution::Int`: Points per day for battery SOC upscaling (Python: 6)
- `ev_resolution::Int`: Points per day for EV upscaling (Python: 6)
- `reserve_resolution::Int`: Blocks per day for reserve upscaling (Python: 4)
"""
struct TemporalConfig
    hours::Int
    resolution_hours::Int
    rolling_horizon_hours::Int
    overlap_hours::Int
    investment_resolution::Int
    primary_energy_resolution::Int
    battery_soc_resolution::Int
    ev_resolution::Int
    reserve_resolution::Int
end

"""
    PenaltyConfig

Penalty costs for constraint violations.

# Fields
- `loss_of_load::Float64`: Value of lost load (\$/MWh)
- `curtailment::Float64`: Curtailment penalty (\$/MWh)
- `loss_of_reserve_static::Float64`: Static reserve shortage penalty (\$/MW)
- `loss_of_reserve_dynamic::Float64`: Dynamic reserve shortage penalty (\$/MW)
- `co2_cost::Float64`: CO2 emission cost (\$/tonne)
"""
struct PenaltyConfig
    loss_of_load::Float64
    curtailment::Float64
    loss_of_reserve_static::Float64
    loss_of_reserve_dynamic::Float64
    co2_cost::Float64
end

"""
    TargetConfig

System targets and constraints.

# Fields
- `re_penetration_target::Float64`: Renewable energy target (fraction 0-1)
- `co2_budget::Float64`: CO2 budget (tonnes)
- `inertia_limit::Float64`: Minimum system inertia (MWs)
"""
struct TargetConfig
    re_penetration_target::Float64
    co2_budget::Float64
    inertia_limit::Float64
end

"""
    SolverSettings

Solver configuration.

# Fields
- `threads::Int`: Number of solver threads
- `time_limit::Float64`: Maximum solve time (seconds)
- `gap::Float64`: MIP optimality gap tolerance
- `verbose::Bool`: Enable solver output
"""
struct SolverSettings
    threads::Int
    time_limit::Float64
    gap::Float64
    verbose::Bool
end

# =============================================================================
# Electric Vehicle Types (must be before PowerSystemInput)
# =============================================================================

"""
    EVConfig

Configuration for electric vehicle fleet at a node.

# Fields
- `num_vehicles::Vector{Float64}`: Number of vehicles per node
- `battery_capacity_kwh::Float64`: Battery capacity per vehicle (kWh)
- `max_charge_power_kw::Float64`: Maximum charging power per vehicle (kW)
- `max_discharge_power_kw::Float64`: Maximum V2G discharge power per vehicle (kW)
- `charge_efficiency::Float64`: Charging efficiency (0-1)
- `discharge_efficiency::Float64`: V2G discharge efficiency (0-1)
- `min_soc::Float64`: Minimum SOC fraction (0-1)
- `max_soc::Float64`: Maximum SOC fraction (0-1)
- `target_soc::Float64`: Target SOC at end of day (0-1)
- `availability_profile::Matrix{Float64}`: Fraction of vehicles available [hour, node]
- `driving_consumption_profile::Matrix{Float64}`: Energy consumption from driving [hour, node] (MWh)
- `v2g_compensation::Float64`: Compensation for V2G provision (\$/MWh)
- `loss_penalty::Float64`: Penalty for not meeting target SOC (\$/MWh)
- `initial_soc::Vector{Float64}`: Initial SOC per node (MWh)
"""
struct EVConfig
    num_vehicles::Vector{Float64}
    battery_capacity_kwh::Float64
    max_charge_power_kw::Float64
    max_discharge_power_kw::Float64
    charge_efficiency::Float64
    discharge_efficiency::Float64
    min_soc::Float64
    max_soc::Float64
    target_soc::Float64
    availability_profile::Matrix{Float64}
    driving_consumption_profile::Matrix{Float64}
    v2g_compensation::Float64
    loss_penalty::Float64
    initial_soc::Vector{Float64}  # Initial SOC per node (MWh)
end

"""
    EVVariables

Container for EV decision variables.
"""
mutable struct EVVariables
    # Charging power (node × hour)
    charging::Matrix{VariableRef}
    # V2G discharge power (node × hour)
    v2g::Matrix{VariableRef}
    # State of charge (node × hour+1)
    soc::Matrix{VariableRef}
    # SOC loss/violation (node × hour)
    loss::Matrix{VariableRef}
end

"""
    EVResult

Solution output from EV model.
"""
struct EVResult
    charging::Matrix{Float64}
    v2g::Matrix{Float64}
    soc::Matrix{Float64}
    loss::Matrix{Float64}
    total_charging::Float64
    total_v2g::Float64
    total_loss::Float64
end

# =============================================================================
# Electrolyzer Types (must be before PowerSystemInput)
# =============================================================================

"""
    ElectrolyzerConfig

Configuration for electrolyzer/hydrogen production system.

# Fields
- `rated_power::Vector{Float64}`: Existing rated power per node (MW)
- `eff_at_rated::Vector{Float64}`: Efficiency at rated power (kWh_e/kg_H2)
- `eff_at_min::Vector{Float64}`: Efficiency at minimum power
- `energy_per_kg_h2::Float64`: Specific energy consumption (kWh/kg H2)
- `ramp_up::Vector{Float64}`: Ramp up rate (fraction/hour)
- `ramp_down::Vector{Float64}`: Ramp down rate (fraction/hour)
- `invest_cost::Vector{Float64}`: Investment cost (\$/MW)
- `invest_max_power::Vector{Float64}`: Maximum investment per node (MW)
- `fixed_cost::Vector{Float64}`: Fixed O&M cost (\$/MW/h)
- `variable_cost::Vector{Float64}`: Variable cost (\$/MWh)
- `water_cost::Float64`: Water cost (\$/kg H2)
- `life_time::Vector{Float64}`: Economic lifetime per node (years)
"""
struct ElectrolyzerConfig
    rated_power::Vector{Float64}
    eff_at_rated::Vector{Float64}
    eff_at_min::Vector{Float64}
    energy_per_kg_h2::Float64
    ramp_up::Vector{Float64}
    ramp_down::Vector{Float64}
    invest_cost::Vector{Float64}
    invest_max_power::Vector{Float64}
    fixed_cost::Vector{Float64}
    variable_cost::Vector{Float64}
    water_cost::Float64
    life_time::Vector{Float64}
end

"""
    ElectrolyzerVariables

Container for electrolyzer decision variables.
"""
mutable struct ElectrolyzerVariables
    # Investment (node)
    investment::Vector{VariableRef}
    # Operational (node × hour)
    power::Matrix{VariableRef}
    h2_production::Matrix{VariableRef}
end

"""
    ElectrolyzerResult

Solution output from the electrolyzer model.
"""
struct ElectrolyzerResult
    # Investment decisions (MW per node)
    investment::Vector{Float64}
    # Operational results (node × hour)
    power::Matrix{Float64}
    h2_production::Matrix{Float64}
    # Totals
    total_investment::Float64
    total_h2_produced::Float64
    total_power_consumed::Float64
end

# =============================================================================
# PowerSystem Input (Complete Configuration)
# =============================================================================

"""
    PowerSystemInput

Complete input configuration for the PowerSystem optimization model.

This struct contains all configuration needed to build and solve the
operational dispatch or unit commitment problem.

# Fields
## System Identification
- `name::String`: System name/identifier
- `year::Int`: Simulation year
- `base_year::Int`: Base year for calculations

## Network
- `network::NetworkConfig`: Network topology and parameters

## Units
- `generators::Vector{GeneratorConfig}`: Generator configurations
- `batteries::Vector{BatteryConfig}`: Battery configurations

## Demand
- `demand::Matrix{Float64}`: Demand matrix [hours × nodes] (MW)
- `sectoral_demand::Dict{String, Matrix{Float64}}`: Demand by sector

## Temporal
- `temporal::TemporalConfig`: Time configuration

## Penalties
- `penalties::PenaltyConfig`: Penalty costs for violations

## Targets
- `targets::TargetConfig`: System targets (RE penetration, CO2, etc.)

## Mode
- `mode::String`: Operation mode ("development", "unit_commitment", "economic_dispatch")

## Solver
- `solver::SolverSettings`: Solver configuration

## Optional: CO2 factors by fuel
- `fuel_co2::Dict{String, Float64}`: CO2 emission factors (tonnes/MWh)
"""

# =============================================================================
# Cost Curve (Bidding/Offer Curve) Segment
# =============================================================================

"""
    CostSegment

A single segment in a piecewise-linear (PWL) cost/bidding curve.

- `fraction`: Fraction of total capacity (Pmax) covered by this segment (0-1).
              All segments for a generator must sum to 1.0.
- `marginal_cost`: Marginal cost for energy dispatched in this segment (dollars/MWh).
"""
struct CostSegment
    fraction::Float64
    marginal_cost::Float64
end

struct PowerSystemInput
    # System identification
    name::String
    year::Int
    base_year::Int

    # Network
    network::NetworkConfig

    # Units
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}

    # Demand (hours × nodes)
    demand::Matrix{Float64}
    sectoral_demand::Dict{String, Matrix{Float64}}

    # Temporal
    temporal::TemporalConfig

    # Penalties (expanded for clarity)
    loss_of_load_penalty::Float64
    loss_of_reserve_static::Float64
    loss_of_reserve_dynamic::Float64
    co2_cost::Float64
    curtailment_cost::Float64           # $/MWh — penalty for spilled RE energy

    # Targets
    re_penetration_target::Float64
    co2_budget::Float64
    inertia_limit::Float64

    # Mode
    mode::String

    # Solver settings
    solver_name::String
    threads::Int
    time_limit::Float64
    gap::Float64
    verbose::Bool
    solver_options::Dict{String, Any}

    # Optional CO2 factors
    fuel_co2::Dict{String, Float64}

    # EV configuration (optional)
    ev_config::Union{EVConfig, Nothing}

    # Electrolyzer configuration (optional)
    electrolyzer_config::Union{ElectrolyzerConfig, Nothing}

    # Sectoral load criticality weights for load shedding penalty
    sectoral_criticality::Dict{String, Float64}

    # Sectoral delay tolerance for demand shifting (hours)
    # Matches Python legacy electric_demand[sector]['delay_tolerance']
    sectoral_delay_tolerance::Dict{String, Int}

    # Hourly inertia limit vector (if empty, use scalar inertia_limit)
    inertia_limit_hourly::Vector{Float64}

    # Inertia penalty for loss of inertia
    loss_of_inertia_penalty::Float64

    # N-1 Security parameters (matches Python legacy power_system.py lines 55-61)
    n1_security_enabled::Bool
    n1_transmission_enabled::Bool
    n1_generation_enabled::Bool
    n1_transmission_reserve_factor::Float64  # Fraction of capacity usable under N-1 (e.g., 0.7)
    n1_generation_reserve_type::String  # "largest_unit" or "percentage"
    n1_generation_reserve_percentage::Float64  # Used if reserve_type == "percentage"
    n1_scopf_enabled::Bool                    # Use SCOPF instead of preventive N-1
    n1_corrective_enabled::Bool               # Allow corrective actions (battery response)
    n1_scopf_max_iterations::Int              # Max SCOPF iterations
    n1_scopf_violation_tolerance::Float64     # Violation tolerance for SCOPF convergence

    # Rooftop solar generation (hours × nodes) - optional
    rooftop_generation::Union{Matrix{Float64}, Nothing}

    # Generator initial status for carry-over between rolling horizons
    # Dict{gen_idx => Dict{node => status (0.0 or 1.0)}}
    # Matches Python legacy generator_initial_status (lines 163-209)
    generator_initial_status::Dict{Int, Dict{Int, Float64}}

    # Last-timestep generator output from previous rolling window (MW).
    # Empty Dict = no boundary; ramp constraint at t=1 is then relaxed
    # (preserves legacy behaviour). Populated when present:
    # Dict{gen_idx => Dict{bus_idx => MW}}.
    generator_output_prev::Dict{Int, Dict{Int, Float64}}

    # Last-timestep reservoir level from previous rolling window (MWh-eq).
    # Empty Dict = no boundary; reservoir uses configured initial fraction.
    # Populated when present: Dict{gen_idx => Dict{bus_idx => MWh}}.
    reservoir_level_prev::Dict{Int, Dict{Int, Float64}}

    # Pending retirements - units scheduled for retirement but can be delayed for feasibility
    # Matches Python legacy pending_retirements (lines 77-80, 1388-1447)
    # Format: Dict{"gen" => Dict{gen_idx => Dict{node => original_capacity}},
    #              "bat" => Dict{bat_idx => Dict{node => original_capacity}}}
    pending_retirements::Dict{String, Dict{Int, Dict{Int, Float64}}}

    # Electricity price vector (hourly) - for spillage cost calculation
    # Matches Python legacy self.electricity_price[t] (line 1763)
    electricity_price::Vector{Float64}

    # Penalty coefficients from config (instead of deriving from VOLL)
    fre_penetration_penalty::Float64
    transfer_margin_penalty::Float64
    rooftop_curtailment_penalty::Float64
    co2_budget_violation_penalty::Float64
    delay_retirement_penalty_per_mw::Float64

    # Reserve requirements per bus from config
    reserve_static_requirement::Dict{Int, Float64}
    reserve_dynamic_requirement::Dict{Int, Float64}

    # Demand constraints
    loss_demand_threshold::Float64
    max_annual_system_cost::Float64
    max_node_investment::Vector{Float64}

    # NPV/lifecycle tracking (P2: matches Python legacy power_system.py lines 528-938)
    # unit_npv: (gen_idx, node) → NPV value (or ("bat_N", node) → NPV)
    unit_npv::Dict{Tuple{Int,Int}, Float64}
    # replacement_needed: (gen_idx, node) → bool (true if remaining life <= 2 or NPV < threshold)
    replacement_needed::Dict{Tuple{Int,Int}, Bool}
    # Battery replacement: ("bat_idx", node) → NPV/bool (use negative gen_idx for batteries: -(bat_idx+1))
    bat_unit_npv::Dict{Tuple{Int,Int}, Float64}
    bat_replacement_needed::Dict{Tuple{Int,Int}, Bool}
    # force_replacement_threshold: NPV threshold below which replacement is forced
    force_replacement_threshold::Float64
    # decommissioning costs per unit: (gen_idx, node) → cost
    decommissioning_cost_gen::Dict{Tuple{Int,Int}, Float64}
    decommissioning_cost_bat::Dict{Tuple{Int,Int}, Float64}
    # discount_rate for NPV computation
    discount_rate::Float64

    # Configurable parameters (previously hardcoded)
    soc_end_tolerance::Float64              # Battery end-of-horizon SOC tolerance (±fraction)
    cyclic_end_soc::Bool                    # If true, force end-of-window SOC == initial (cyclic)
    min_cycling_ratio::Float64              # Min battery cycling as fraction of capacity
    min_cycling_period_days::Float64        # Period for min cycling calculation (days)
    reserve_static_default_ratio::Float64   # Default static reserve as fraction of demand
    soc_violation_penalty::Float64          # Penalty for SOC limit violation
    flexible_demand_benefit_ratio::Float64  # Fraction of price for flex demand benefit
    demand_shift_cost_rate::Float64         # Cost rate per hour of shift distance
    dynamic_reserve_contribution::Float64   # Fraction of rated_power for dynamic reserve
    max_decommission_cost_per_mw::Float64  # Cap on decommissioning cost ($/MW)
    max_npv_penalty_per_mw::Float64        # Cap on NPV-based penalty ($/MW)

    # Hours in this specific year (8760 or 8784 for leap years)
    hours_per_year::Int

    # PWL loss model: 0=no losses, -1=linear legacy, N>0=PWL with N segments
    pwl_loss_segments::Int

    # Bidding/offer cost curves: gen_index => Dict{bus_index => Vector{CostSegment}}
    # Only populated for generators with multi-segment curves (>1 block).
    # For flat-cost generators the dict is empty → zero overhead.
    gen_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}}
    bat_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}}

    # ACOPF configuration
    # Power flow mode: "dcopf", "dcopf_ac_verify", "acopf_soc", "acopf_qc",
    #                  "acopf_sdp", "acopf_polar", "acopf_rect"
    power_flow_mode::String
    acopf_base_mva::Float64            # Base MVA for ACOPF (100.0)
    acopf_v_min::Float64               # Min voltage p.u. (0.9)
    acopf_v_max::Float64               # Max voltage p.u. (1.1)
    acopf_default_power_factor::Float64 # Default PF for Q limit estimation (0.85)
    acopf_load_power_factor::Float64   # Load PF for reactive demand (0.9)
    acopf_q_slack_penalty::Float64     # Q slack penalty $/MVAr (100.0)
    acopf_min_reactance_pu::Float64    # Min reactance clamp (0.01)
    acopf_tap_ratio_min::Float64       # Tap ratio lower bound (0.5)
    acopf_tap_ratio_max::Float64       # Tap ratio upper bound (2.0)
    acopf_q_min_ratio::Float64         # Q_min = -ratio × Q_max (0.5)
    # Reactive power limits: gen_idx → Vector{Float64} per node (MVAr)
    gen_q_limits::Dict{Int, Vector{Float64}}      # Q_max per gen
    gen_q_limits_min::Dict{Int, Vector{Float64}}   # Q_min per gen
end

# Constructor with sensible defaults
function PowerSystemInput(;
    name::String,
    year::Int,
    base_year::Int = year,
    network::NetworkConfig,
    generators::Vector{GeneratorConfig},
    batteries::Vector{BatteryConfig},
    demand::Matrix{Float64},
    sectoral_demand::Dict{String, Matrix{Float64}} = Dict{String, Matrix{Float64}}(),
    temporal::TemporalConfig,
    loss_of_load_penalty::Float64 = 0.01,
    loss_of_reserve_static::Float64 = 0.01,
    loss_of_reserve_dynamic::Float64 = 0.01,
    co2_cost::Float64 = 0.0,
    curtailment_cost::Float64 = 2e-5,   # M$/MWh — already-scaled (\$20/MWh × 1e-6)
    re_penetration_target::Float64 = 0.0,
    co2_budget::Float64 = Inf,
    inertia_limit::Float64 = 0.0,
    mode::String = "economic_dispatch",
    solver_name::String = "highs",
    threads::Int = 4,
    time_limit::Float64 = 3600.0,
    gap::Float64 = 0.01,
    verbose::Bool = false,
    solver_options::Dict{String, Any} = Dict{String, Any}(),
    fuel_co2::Dict{String, Float64} = Dict{String, Float64}(),
    ev_config::Union{EVConfig, Nothing} = nothing,
    electrolyzer_config::Union{ElectrolyzerConfig, Nothing} = nothing,
    sectoral_criticality::Dict{String, Float64} = Dict{String, Float64}(),
    sectoral_delay_tolerance::Dict{String, Int} = Dict{String, Int}(),
    inertia_limit_hourly::Vector{Float64} = Float64[],
    loss_of_inertia_penalty::Float64 = 1.0,
    # N-1 Security parameters (matches Python legacy power_system.py lines 55-61)
    n1_security_enabled::Bool = false,
    n1_transmission_enabled::Bool = false,
    n1_generation_enabled::Bool = false,
    n1_transmission_reserve_factor::Float64 = 0.7,
    n1_generation_reserve_type::String = "largest_unit",
    n1_generation_reserve_percentage::Float64 = 0.15,
    n1_scopf_enabled::Bool = false,
    n1_corrective_enabled::Bool = false,
    n1_scopf_max_iterations::Int = 5,
    n1_scopf_violation_tolerance::Float64 = 0.01,
    # Rooftop solar generation
    rooftop_generation::Union{Matrix{Float64}, Nothing} = nothing,
    # Generator initial status for rolling horizon carry-over
    generator_initial_status::Dict{Int, Dict{Int, Float64}} = Dict{Int, Dict{Int, Float64}}(),
    # Last-timestep gen_output from previous window (for t=1 ramp continuity).
    # Empty = no boundary; preserves legacy behaviour (no t=1 ramp).
    generator_output_prev::Dict{Int, Dict{Int, Float64}} = Dict{Int, Dict{Int, Float64}}(),
    # Last-timestep reservoir level from previous window (MWh-eq).
    # Empty = no boundary; reservoir uses configured initial fraction.
    reservoir_level_prev::Dict{Int, Dict{Int, Float64}} = Dict{Int, Dict{Int, Float64}}(),
    # Pending retirements for delayed retirement mechanism
    pending_retirements::Dict{String, Dict{Int, Dict{Int, Float64}}} = Dict{String, Dict{Int, Dict{Int, Float64}}}(),
    # Electricity price vector (hourly) - matches Python legacy self.electricity_price
    electricity_price::Vector{Float64} = Float64[],
    # Penalty coefficients from config (instead of deriving from VOLL)
    fre_penetration_penalty::Float64 = 0.0,       # 0 = fallback to loss_of_load * tres * 100
    transfer_margin_penalty::Float64 = 0.0,        # 0 = fallback to loss_of_load * 0.1
    rooftop_curtailment_penalty::Float64 = 5e-6,
    co2_budget_violation_penalty::Float64 = 5e-4,
    delay_retirement_penalty_per_mw::Float64 = 0.05,
    # Reserve requirements per bus from config
    reserve_static_requirement::Dict{Int, Float64} = Dict{Int, Float64}(),
    reserve_dynamic_requirement::Dict{Int, Float64} = Dict{Int, Float64}(),
    # Demand constraints
    loss_demand_threshold::Float64 = 1.0,
    max_annual_system_cost::Float64 = Inf,
    max_node_investment::Vector{Float64} = Float64[],
    # NPV/lifecycle tracking (P2)
    unit_npv::Dict{Tuple{Int,Int}, Float64} = Dict{Tuple{Int,Int}, Float64}(),
    replacement_needed::Dict{Tuple{Int,Int}, Bool} = Dict{Tuple{Int,Int}, Bool}(),
    bat_unit_npv::Dict{Tuple{Int,Int}, Float64} = Dict{Tuple{Int,Int}, Float64}(),
    bat_replacement_needed::Dict{Tuple{Int,Int}, Bool} = Dict{Tuple{Int,Int}, Bool}(),
    force_replacement_threshold::Float64 = -1.0,
    decommissioning_cost_gen::Dict{Tuple{Int,Int}, Float64} = Dict{Tuple{Int,Int}, Float64}(),
    decommissioning_cost_bat::Dict{Tuple{Int,Int}, Float64} = Dict{Tuple{Int,Int}, Float64}(),
    discount_rate::Float64 = 0.08,
    # Configurable parameters (previously hardcoded)
    soc_end_tolerance::Float64 = 0.05,
    cyclic_end_soc::Bool = true,
    min_cycling_ratio::Float64 = 0.8,
    min_cycling_period_days::Float64 = 7.0,
    reserve_static_default_ratio::Float64 = 0.15,
    soc_violation_penalty::Float64 = 1.0,
    flexible_demand_benefit_ratio::Float64 = 0.5,
    demand_shift_cost_rate::Float64 = 1e-7,
    dynamic_reserve_contribution::Float64 = 0.5,
    max_decommission_cost_per_mw::Float64 = 0.5,
    max_npv_penalty_per_mw::Float64 = 1.0,
    hours_per_year::Int = hours_in_year(year),
    # PWL loss model: 0=no losses, -1=linear legacy, N>0=PWL with N segments
    pwl_loss_segments::Int = 3,
    # Bidding/offer cost curves (empty = all generators use flat fuel_cost)
    gen_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}} = Dict{Int, Dict{Int, Vector{CostSegment}}}(),
    bat_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}} = Dict{Int, Dict{Int, Vector{CostSegment}}}(),
    # ACOPF configuration
    power_flow_mode::String = "dcopf",
    acopf_base_mva::Float64 = 100.0,
    acopf_v_min::Float64 = 0.90,
    acopf_v_max::Float64 = 1.10,
    acopf_default_power_factor::Float64 = 0.85,
    acopf_load_power_factor::Float64 = 0.9,
    acopf_q_slack_penalty::Float64 = 1e-4,
    acopf_min_reactance_pu::Float64 = 0.01,
    acopf_tap_ratio_min::Float64 = 0.5,
    acopf_tap_ratio_max::Float64 = 2.0,
    acopf_q_min_ratio::Float64 = 0.5,
    gen_q_limits::Dict{Int, Vector{Float64}} = Dict{Int, Vector{Float64}}(),
    gen_q_limits_min::Dict{Int, Vector{Float64}} = Dict{Int, Vector{Float64}}()
)
    return PowerSystemInput(
        name, year, base_year,
        network, generators, batteries,
        demand, sectoral_demand,
        temporal,
        loss_of_load_penalty,
        loss_of_reserve_static, loss_of_reserve_dynamic,
        co2_cost,
        curtailment_cost,
        re_penetration_target, co2_budget, inertia_limit,
        mode,
        solver_name, threads, time_limit, gap, verbose, solver_options,
        fuel_co2,
        ev_config,
        electrolyzer_config,
        sectoral_criticality,
        sectoral_delay_tolerance,
        inertia_limit_hourly,
        loss_of_inertia_penalty,
        n1_security_enabled,
        n1_transmission_enabled,
        n1_generation_enabled,
        n1_transmission_reserve_factor,
        n1_generation_reserve_type,
        n1_generation_reserve_percentage,
        n1_scopf_enabled,
        n1_corrective_enabled,
        n1_scopf_max_iterations,
        n1_scopf_violation_tolerance,
        rooftop_generation,
        generator_initial_status,
        generator_output_prev,
        reservoir_level_prev,
        pending_retirements,
        electricity_price,
        fre_penetration_penalty,
        transfer_margin_penalty,
        rooftop_curtailment_penalty,
        co2_budget_violation_penalty,
        delay_retirement_penalty_per_mw,
        reserve_static_requirement,
        reserve_dynamic_requirement,
        loss_demand_threshold,
        max_annual_system_cost,
        max_node_investment,
        unit_npv,
        replacement_needed,
        bat_unit_npv,
        bat_replacement_needed,
        force_replacement_threshold,
        decommissioning_cost_gen,
        decommissioning_cost_bat,
        discount_rate,
        soc_end_tolerance,
        cyclic_end_soc,
        min_cycling_ratio,
        min_cycling_period_days,
        reserve_static_default_ratio,
        soc_violation_penalty,
        flexible_demand_benefit_ratio,
        demand_shift_cost_rate,
        dynamic_reserve_contribution,
        max_decommission_cost_per_mw,
        max_npv_penalty_per_mw,
        hours_per_year,
        pwl_loss_segments,
        gen_cost_curves,
        bat_cost_curves,
        power_flow_mode,
        acopf_base_mva,
        acopf_v_min,
        acopf_v_max,
        acopf_default_power_factor,
        acopf_load_power_factor,
        acopf_q_slack_penalty,
        acopf_min_reactance_pu,
        acopf_tap_ratio_min,
        acopf_tap_ratio_max,
        acopf_q_min_ratio,
        gen_q_limits,
        gen_q_limits_min
    )
end

# =============================================================================
# PowerSystem Variables (Decision Variables Container)
# =============================================================================

"""
    PowerSystemVariables

Container for all JuMP decision variables in the PowerSystem model.

Variables are organized by category. Optional variables (unit commitment,
investment) are `nothing` when not applicable.

# Variable Dimensions
- Generation: [generator, node, hour]
- Storage: [battery, node, hour]
- Network: [node, hour] or Dict{(from, to), Vector}
- Investment: [unit, node]
"""
mutable struct PowerSystemVariables
    # Generation (gen × bus × hour) — SparseAxisArray (only active gen-bus pairs)
    gen_output::Any
    gen_status::Any      # Nothing in non-UC mode
    gen_startup::Any     # Nothing in non-UC mode
    gen_shutdown::Any    # Nothing in non-UC mode

    # Curtailment (node × hour) - spilled renewable energy per node
    curtailment::Array{VariableRef, 2}

    # FRE penetration loss slack (node × hour) - RE constraint slack
    fre_penetration_loss::Array{VariableRef, 2}

    # CO2 emissions (node × hour)
    co2_emissions::Union{Array{VariableRef, 2}, Nothing}

    # Storage (bat × node × hour) — SparseAxisArray (only active bat-bus pairs)
    bat_charge::Any
    bat_discharge::Any
    bat_soc::Any
    bat_charge_status::Any      # Nothing in non-UC mode
    soc_violation::Any           # Nothing if not needed
    bat_spillage::Any            # Nothing if not needed

    # Transmission (from × to → hour vector) — legacy node-pair indexing
    power_flow::Dict{Tuple{Int,Int}, Vector{VariableRef}}
    # Per physical line flow variables (line_index → hour vector) — for parallel lines
    power_flow_by_line::Union{Vector{Vector{VariableRef}}, Nothing}
    voltage_angle::Array{VariableRef, 2}  # node × hour
    # Transfer margin violation (from × to × hour)
    # Slack variable for transmission capacity violations
    transfer_margin::Union{Dict{Tuple{Int,Int}, Vector{VariableRef}}, Nothing}

    # Reserves (node × hour)
    reserve_static::Array{VariableRef, 2}
    reserve_dynamic::Array{VariableRef, 2}
    reserve_static_loss::Array{VariableRef, 2}
    reserve_dynamic_loss::Array{VariableRef, 2}

    # Load shedding (node × hour)
    load_shed::Array{VariableRef, 2}

    # Investment (development mode only) — SparseAxisArray (only active gen/bat-bus pairs)
    gen_investment::Any       # Nothing or SparseAxisArray [g, b]
    bat_investment_power::Any # Nothing or SparseAxisArray [bi, b]
    bat_investment_capacity::Any  # Nothing or SparseAxisArray [bi, b]
    transfer_investment::Union{Dict{Tuple{Int,Int}, VariableRef}, Nothing}

    # EV variables (node × hour) - optional
    ev_charging::Union{Array{VariableRef, 2}, Nothing}
    ev_v2g::Union{Array{VariableRef, 2}, Nothing}
    ev_soc::Union{Array{VariableRef, 2}, Nothing}  # node × (hour+1)
    ev_loss::Union{Array{VariableRef, 2}, Nothing}

    # Electrolyzer variables (node × hour) - optional
    electrolyzer_power::Union{Array{VariableRef, 2}, Nothing}

    # Inertia (hour) - optional
    loss_of_inertia::Union{Vector{VariableRef}, Nothing}

    # Constraint references (for dual extraction)
    balance_constraints::Union{Dict{Tuple{Int,Int}, Any}, Nothing}

    # Sectoral load shedding (sector -> node × hour)
    loss_of_load_sectoral::Union{Dict{String, Matrix{VariableRef}}, Nothing}

    # Flexible demand variables (sector -> node × hour)
    flexible_demand_curtailed::Union{Dict{String, Matrix{VariableRef}}, Nothing}

    # CO2 budget violation slack variable
    # Single variable for total emissions exceeding budget
    co2_budget_violation::Union{VariableRef, Nothing}

    # Rooftop solar curtailment (node × hour)
    rooftop_curtailment::Union{Array{VariableRef, 2}, Nothing}

    # Delay retirement variables
    # Binary: 1 = delay retirement (keep operating), 0 = proceed with retirement
    gen_delay_retirement::Union{Dict{Tuple{Int,Int}, VariableRef}, Nothing}
    bat_delay_retirement::Union{Dict{Tuple{Int,Int}, VariableRef}, Nothing}
    # Original capacities for delayed units
    gen_delay_retirement_capacity::Dict{Tuple{Int,Int}, Float64}
    bat_delay_retirement_capacity::Dict{Tuple{Int,Int}, Float64}

    # EV charge/discharge mutual exclusivity status (node × hour)
    ev_charge_status::Union{Array{VariableRef, 2}, Nothing}

    # Demand shifting variables - sparse t-to-t_dest pairs (P3)
    # sector → Dict{(bus, t, t_dest) => VariableRef}
    # Matches Python legacy flexible_demand_shifted[sector][node][t][t_dest]
    demand_shift::Union{Dict{String, Dict{Tuple{Int,Int,Int}, VariableRef}}, Nothing}

    # AC/DC converter variables (converter × hour)
    acdc_rectify::Union{Matrix{VariableRef}, Nothing}   # AC→DC power per converter per hour
    acdc_invert::Union{Matrix{VariableRef}, Nothing}    # DC→AC power per converter per hour

    # Frequency converter variables (converter × hour)
    freq_flow_a_to_b::Union{Matrix{VariableRef}, Nothing}  # A→B power per converter per hour
    freq_flow_b_to_a::Union{Matrix{VariableRef}, Nothing}  # B→A power per converter per hour

    # NPV forced replacement variables
    # Continuous variables for units where replacement_needed=true
    gen_forced_replacement::Union{Dict{Tuple{Int,Int}, VariableRef}, Nothing}
    bat_forced_replacement::Union{Dict{Tuple{Int,Int}, VariableRef}, Nothing}

    # Reservoir hydroelectric variables (gen × node × hour) — SparseAxisArray
    reservoir_level::Any         # Water level (MWh-eq), dim3 = hours+1
    reservoir_spillage::Any      # Overflow (MW-eq)
    reservoir_pump::Any          # Pump-back power consumed (MW)
    reservoir_invest_capacity::Union{Array{VariableRef, 2}, Nothing}  # Reservoir expansion (gen × node)

    # Sparse lookup maps for gen/bat active bus pairs
    buses_of_gen::Vector{Vector{Int}}   # gen_idx → [active bus indices]
    gens_at_bus::Vector{Vector{Int}}    # bus_idx → [gen indices with capacity here]
    buses_of_bat::Vector{Vector{Int}}   # bat_idx → [active bus indices]
    bats_at_bus::Vector{Vector{Int}}    # bus_idx → [bat indices with capacity here]

    # Segment output variables for PWL cost curves (bidding curves)
    # Only populated for units with >1 segment; empty otherwise.
    # (gen/bat_idx, bus) → Matrix{VariableRef} [segment × hour]
    gen_seg_output::Dict{Tuple{Int,Int}, Any}
    bat_seg_discharge::Dict{Tuple{Int,Int}, Any}

    # ACOPF variables (nothing when using DCOPF)
    acopf_vars::Any  # Union{ACOPFVariables, Nothing}
end

# Constructor with optional fields defaulting to nothing
function PowerSystemVariables(
    gen_output, gen_status, gen_startup, gen_shutdown,
    curtailment, fre_penetration_loss,
    bat_charge, bat_discharge, bat_soc,
    power_flow, voltage_angle,
    reserve_static, reserve_dynamic, reserve_static_loss, reserve_dynamic_loss,
    load_shed,
    gen_investment, bat_investment_power, bat_investment_capacity, transfer_investment,
    buses_of_gen, gens_at_bus, buses_of_bat, bats_at_bus;
    power_flow_by_line = nothing,
    co2_emissions = nothing,
    bat_charge_status = nothing,
    soc_violation = nothing,
    bat_spillage = nothing,
    transfer_margin = nothing,
    ev_charging = nothing,
    ev_v2g = nothing,
    ev_soc = nothing,
    ev_loss = nothing,
    electrolyzer_power = nothing,
    loss_of_inertia = nothing,
    balance_constraints = nothing,
    loss_of_load_sectoral = nothing,
    flexible_demand_curtailed = nothing,
    co2_budget_violation = nothing,
    rooftop_curtailment = nothing,
    gen_delay_retirement = nothing,
    bat_delay_retirement = nothing,
    gen_delay_retirement_capacity = Dict{Tuple{Int,Int}, Float64}(),
    bat_delay_retirement_capacity = Dict{Tuple{Int,Int}, Float64}(),
    ev_charge_status = nothing,
    demand_shift = nothing,
    acdc_rectify = nothing,
    acdc_invert = nothing,
    freq_flow_a_to_b = nothing,
    freq_flow_b_to_a = nothing,
    gen_forced_replacement = nothing,
    bat_forced_replacement = nothing,
    reservoir_level = nothing,
    reservoir_spillage = nothing,
    reservoir_pump = nothing,
    reservoir_invest_capacity = nothing,
    gen_seg_output = Dict{Tuple{Int,Int}, Any}(),
    bat_seg_discharge = Dict{Tuple{Int,Int}, Any}(),
    acopf_vars = nothing
)
    return PowerSystemVariables(
        gen_output, gen_status, gen_startup, gen_shutdown,
        curtailment, fre_penetration_loss, co2_emissions,
        bat_charge, bat_discharge, bat_soc, bat_charge_status, soc_violation, bat_spillage,
        power_flow, power_flow_by_line, voltage_angle, transfer_margin,
        reserve_static, reserve_dynamic, reserve_static_loss, reserve_dynamic_loss,
        load_shed,
        gen_investment, bat_investment_power, bat_investment_capacity, transfer_investment,
        ev_charging, ev_v2g, ev_soc, ev_loss,
        electrolyzer_power,
        loss_of_inertia,
        balance_constraints,
        loss_of_load_sectoral,
        flexible_demand_curtailed,
        co2_budget_violation,
        rooftop_curtailment,
        gen_delay_retirement,
        bat_delay_retirement,
        gen_delay_retirement_capacity,
        bat_delay_retirement_capacity,
        ev_charge_status,
        demand_shift,
        acdc_rectify,
        acdc_invert,
        freq_flow_a_to_b,
        freq_flow_b_to_a,
        gen_forced_replacement,
        bat_forced_replacement,
        reservoir_level,
        reservoir_spillage,
        reservoir_pump,
        reservoir_invest_capacity,
        buses_of_gen,
        gens_at_bus,
        buses_of_bat,
        bats_at_bus,
        gen_seg_output,
        bat_seg_discharge,
        acopf_vars
    )
end

# =============================================================================
# PowerSystem Result (Solution Output)
# =============================================================================

"""
    CostBreakdown

Granular decomposition of the operational cost from `build_objective!`.
Energy-based costs are already scaled by `temporal_resolution_hours`.
"""
struct CostBreakdown
    fuel_cost::Float64
    fixed_om_cost::Float64
    maintenance_cost::Float64
    startup_cost::Float64
    battery_maintenance_cost::Float64
    battery_degradation_cost::Float64
    load_shedding_cost::Float64
    curtailment_cost::Float64
    reserve_static_cost::Float64
    reserve_dynamic_cost::Float64
    co2_emission_cost::Float64
    fre_penetration_cost::Float64
    inertia_cost::Float64
    soc_violation_cost::Float64
    transfer_margin_cost::Float64
    v2g_compensation::Float64
    flexible_demand_benefit::Float64
    investment_cost::Float64
    electrolyzer_cost::Float64
    converter_cost::Float64
    spillage_cost::Float64
    delay_retirement_cost::Float64
    reservoir_spillage_cost::Float64
    demand_shift_cost::Float64
    rooftop_curtailment_cost::Float64
    npv_penalty_cost::Float64
    reservoir_invest_cost::Float64
    # PrimaryEnergy sub-costs (merged into cost_expressions by
    # PrimaryEnergyAdapter.integrate_with_power_system).
    pe_supply_cost::Float64
    pe_loss_cost::Float64
    pe_excess_cost::Float64
    pe_transport_cost::Float64
    pe_investment_cost::Float64
    pe_coupling_slack_cost::Float64
    pe_electrolyzer_cost::Float64
    # N-1 SCOPF reliability-shortfall penalty.
    n1_security_shortfall_cost::Float64
    total::Float64
end


"""
    PowerSystemResult

Solution output from the PowerSystem optimization model.
Contains all solution values, metrics, and optional investment decisions.
"""
struct PowerSystemResult
    # Solve status
    status::MOI.TerminationStatusCode
    objective::Float64
    solve_time::Float64

    # Generation (gen × node × hour)
    gen_output::Array{Float64, 3}
    gen_status::Union{Array{Float64, 3}, Nothing}
    gen_startup::Union{Array{Float64, 3}, Nothing}
    gen_shutdown::Union{Array{Float64, 3}, Nothing}

    # Curtailment (node × hour) - spilled renewable energy
    curtailment::Array{Float64, 2}
    total_curtailment::Float64

    # Storage
    bat_charge::Array{Float64, 3}
    bat_discharge::Array{Float64, 3}
    bat_soc::Array{Float64, 3}

    # Transmission
    power_flow::Dict{Tuple{Int,Int}, Vector{Float64}}
    power_flow_by_line::Union{Vector{Vector{Float64}}, Nothing}  # Per physical line flows
    voltage_angle::Matrix{Float64}
    # ACOPF AC-specific outputs (filled when power_flow_mode is acopf_*).
    # Default to nothing for DC runs.
    voltage_magnitude::Union{Matrix{Float64}, Nothing}              # [bus × hour] in p.u.
    reactive_generation::Union{Array{Float64, 3}, Nothing}          # [gen × bus × hour] in MVAr
    transfer_investment::Union{Dict{Tuple{Int,Int}, Float64}, Nothing}

    # Reserves (node × hour)
    reserve_static::Matrix{Float64}
    reserve_dynamic::Matrix{Float64}
    reserve_static_loss::Matrix{Float64}
    reserve_dynamic_loss::Matrix{Float64}

    # Load shedding (node × hour)
    load_shed::Matrix{Float64}

    # CO2 emissions (node × hour)
    co2_emissions::Matrix{Float64}

    # Dual prices (node × hour)
    energy_prices::Matrix{Float64}

    # System metrics
    total_generation::Float64
    total_demand::Float64
    total_losses::Float64
    re_penetration::Float64
    total_co2::Float64
    load_shed_total::Float64

    # Investment decisions (development mode)
    gen_investment::Union{Matrix{Float64}, Nothing}
    bat_investment_power::Union{Matrix{Float64}, Nothing}
    bat_investment_capacity::Union{Matrix{Float64}, Nothing}

    # Battery spillage (bat × node × hour)
    bat_spillage::Union{Array{Float64, 3}, Nothing}

    # EV variables (node × hour)
    ev_charging::Union{Matrix{Float64}, Nothing}
    ev_v2g::Union{Matrix{Float64}, Nothing}
    ev_soc::Union{Matrix{Float64}, Nothing}
    ev_loss::Union{Matrix{Float64}, Nothing}

    # System-wide variables
    loss_of_inertia::Union{Vector{Float64}, Nothing}

    # Transfer margin (node-pair → hours)
    transfer_margin::Union{Dict{Tuple{Int,Int}, Vector{Float64}}, Nothing}

    # Reservoir hydroelectric results (gen × node × hour)
    reservoir_level::Union{Array{Float64, 3}, Nothing}
    reservoir_spillage::Union{Array{Float64, 3}, Nothing}
    reservoir_pump::Union{Array{Float64, 3}, Nothing}
    reservoir_invest_capacity::Union{Matrix{Float64}, Nothing}

    # N-1 security results
    n1_gen_reserve_duals::Union{Vector{Float64}, Nothing}       # [hour] — dual of gen N-1 reserve
    n1_trans_reserve_duals::Union{Dict{Tuple{Int,Int,Int}, Vector{Float64}}, Nothing}  # (line,outage,dir) → [hour]
    n1_binding_contingencies::Union{Vector{String}, Nothing}    # names of binding contingencies
    n1_security_cost::Float64                                   # incremental cost of N-1 constraints

    # Granular cost decomposition (populated after solve)
    cost_breakdown::Union{CostBreakdown, Nothing}
end


# =============================================================================
# Primary Energy Types
# =============================================================================

"""
    FuelConfig

Configuration for a primary energy fuel/source.

# Fields
- `name::String`: Fuel identifier (e.g., "Gas", "Diesel", "Hydrogen")
- `price_base::Float64`: Base price (\$/unit)
- `price_growth_rate::Float64`: Annual price growth rate
- `energy_content::Float64`: Energy content (MWh_th/physical unit)
- `emission_factor::Float64`: CO2 emissions (tonnes/MWh_th)
- `max_availability::Vector{Float64}`: Max annual availability per node (units/year)
- `storage_capacity::Vector{Float64}`: Storage capacity per node (units)
- `initial_storage_level::Vector{Float64}`: Initial storage as fraction (0-1)
- `min_storage_level::Float64`: Minimum storage level fraction
- `import_cost::Vector{Float64}`: Import cost per node (\$/unit)
- `transport_cost::Float64`: Transport cost (\$/unit/km)
- `transport_losses::Float64`: Transport losses (%/100km)
"""
struct FuelConfig
    name::String
    price_base::Float64
    price_growth_rate::Float64
    energy_content::Float64
    emission_factor::Float64
    max_availability::Vector{Float64}
    storage_capacity::Vector{Float64}
    initial_storage_level::Vector{Float64}
    min_storage_level::Float64
    import_cost::Vector{Float64}
    transport_cost::Float64
    transport_losses::Float64
end

"""
    FuelInfrastructureConfig

Configuration for fuel transport and storage infrastructure.

# Fields
- `transport_capacity::Float64`: Daily transport capacity (units/day)
- `transport_investment_cost::Float64`: Investment cost (\$/unit-day/km)
- `transport_expansion_limit::Float64`: Maximum expansion factor
- `storage_investment_cost::Float64`: Storage investment cost (\$/unit)
- `storage_expansion_limit::Float64`: Maximum storage expansion factor
- `storage_efficiency::Float64`: Storage round-trip efficiency
- `lifetime_transport::Float64`: Transport infrastructure lifetime (years)
- `lifetime_storage::Float64`: Storage infrastructure lifetime (years)
- `max_hourly_dispatch_rate::Float64`: Max hourly dispatch as fraction of capacity (-1.0 = no limit)
"""
struct FuelInfrastructureConfig
    transport_capacity::Float64
    transport_investment_cost::Float64
    transport_expansion_limit::Float64
    storage_investment_cost::Float64
    storage_expansion_limit::Float64
    storage_efficiency::Float64
    lifetime_transport::Float64
    lifetime_storage::Float64
    max_hourly_dispatch_rate::Float64  # -1.0 means no limit
end

"""
    FuelRouteParams

Per-fuel transport parameters for a specific route.

# Fields
- `capacity::Float64`: Daily transport capacity for this fuel on this route (units/day)
- `transport_cost::Float64`: Transport cost per unit per km (\$/unit/km)
- `transport_losses::Float64`: Loss fraction per 100 km
"""
struct FuelRouteParams
    capacity::Float64
    transport_cost::Float64
    transport_losses::Float64
end

"""
    TransportRoute

A unidirectional fuel transport route between two nodes.

# Fields
- `route_id::String`: Unique route identifier
- `from_node::Int`: Origin node (1-indexed)
- `to_node::Int`: Destination node (1-indexed)
- `distance_km::Float64`: Route distance in km
- `fuel_params::Dict{String, FuelRouteParams}`: Per-fuel parameters on this route
"""
struct TransportRoute
    route_id::String
    from_node::Int
    to_node::Int
    distance_km::Float64
    fuel_params::Dict{String, FuelRouteParams}
end

"""
    NonElectricDemandConfig

Configuration for non-electric fuel demand by sector.

# Fields
- `sector::String`: Sector name (e.g., "Industrial", "Transport", "Residential")
- `fuel::String`: Fuel type
- `annual_demand::Vector{Float64}`: Annual demand per node (units/year)
- `growth_rate::Float64`: Annual growth rate
- `seasonal_factors::Vector{Float64}`: Monthly seasonal factors (12 values, sum to 1)
"""
struct NonElectricDemandConfig
    sector::String
    fuel::String
    annual_demand::Vector{Float64}
    growth_rate::Float64
    seasonal_factors::Vector{Float64}
end

"""
    PrimaryEnergyInput

Complete input for the Primary Energy optimization model.

# Fields
- `year::Int`: Simulation year
- `base_year::Int`: Base year for growth calculations
- `num_nodes::Int`: Number of nodes
- `hours::Int`: Total simulation hours
- `fuels::Vector{FuelConfig}`: Fuel configurations
- `infrastructure::Dict{String, FuelInfrastructureConfig}`: Infrastructure per fuel
- `non_electric_demand::Vector{NonElectricDemandConfig}`: Non-electric demands
- `transport_routes::Vector{TransportRoute}`: Fuel transport routes with per-fuel parameters
- `generator_fuel_map::Dict{Int, Tuple{String, Float64, Float64, Float64}}`:
    Generator to fuel mapping {gen_idx => (fuel_id, MWhe/unit, MWhth/unit, efficiency)}
- `primary_energy_resolution::Int`: Resolution for planning (hours)
- `investment_resolution::Int`: Resolution for investment decisions (hours)
- `discount_rate::Float64`: Discount rate for annualization
- `loss_of_fuel_supply_penalty::Float64`: Penalty for fuel shortfall (\$/unit)
- `coupling_slack_penalty::Float64`: Penalty for periodic-hourly coupling slack (\$/unit)
- `mode::String`: Operation mode ("development", "economic_dispatch", "unit_commitment")
- `cumulative_capacities::Dict`: Previously accumulated infrastructure investments
- `initial_storage_levels::Union{Dict{String, Vector{Float64}}, Nothing}`: Initial storage levels
- `h2_production_hourly::Union{Matrix{Float64}, Nothing}`: H2 production from electrolyzers [hour × node] (units/hr)
    Matches Python legacy power_system.h2_model.variables['h2_production'] (lines 703-707)
- `generator_rated_power::Dict{Int, Vector{Float64}}`: Rated power per node for each gen in fuel map
    Used to skip zero-capacity nodes in PE coupling
"""
struct PrimaryEnergyInput
    year::Int
    base_year::Int
    num_nodes::Int
    hours::Int
    fuels::Vector{FuelConfig}
    infrastructure::Dict{String, FuelInfrastructureConfig}
    non_electric_demand::Vector{NonElectricDemandConfig}
    transport_routes::Vector{TransportRoute}
    generator_fuel_map::Dict{Int, Tuple{String, Float64, Float64, Float64}}
    primary_energy_resolution::Int
    investment_resolution::Int
    discount_rate::Float64
    loss_of_fuel_supply_penalty::Float64
    coupling_slack_penalty::Float64
    mode::String
    cumulative_capacities::Dict{String, Any}
    initial_storage_levels::Union{Dict{String, Vector{Float64}}, Nothing}
    # Flag to skip investment variable creation when handled by MasterProblem
    # Matches Python legacy investment_from_master (lines 50, 72, 361-379)
    investment_from_master::Bool
    # H2 production from electrolyzers for Hydrogen storage balance
    # When electrolyzer_config is provided, this is IGNORED and replaced by optimization variables.
    # When no electrolyzer config, this fixed data is used as fallback.
    h2_production_hourly::Union{Matrix{Float64}, Nothing}
    # Rated power per node for generators in fuel map, used to skip zero-capacity
    # nodes in PE coupling
    generator_rated_power::Dict{Int, Vector{Float64}}
    # Electrolyzer configuration for joint optimization of H2 production
    # Matches Python legacy electrolizer_model.py HydrogenProduction class
    electrolyzer_config::Union{Nothing, ElectrolyzerConfig}
end

"""
    _auto_routes_from_distances(dist, n, fuels, infrastructure)

Generate transport routes from a legacy distance matrix, using global FuelConfig defaults.
Each non-zero distance entry (i,j) with j>i generates two unidirectional routes.
"""
function _auto_routes_from_distances(
    dist::Matrix{Float64}, n::Int,
    fuels::Vector{FuelConfig},
    infrastructure::Dict{String, FuelInfrastructureConfig}
)
    routes = TransportRoute[]
    for i in 1:n
        for j in (i+1):n
            if dist[i, j] > 0
                fparams = Dict{String, FuelRouteParams}()
                for f in fuels
                    if haskey(infrastructure, f.name)
                        inf = infrastructure[f.name]
                        fparams[f.name] = FuelRouteParams(
                            inf.transport_capacity,
                            f.transport_cost,
                            f.transport_losses
                        )
                    end
                end
                push!(routes, TransportRoute("auto_$(i)_$(j)", i, j, dist[i, j], fparams))
                push!(routes, TransportRoute("auto_$(j)_$(i)", j, i, dist[i, j], fparams))
            end
        end
    end
    return routes
end

# Backward-compatible constructor: accepts distance matrix, auto-generates routes
function PrimaryEnergyInput(
    year::Int, base_year::Int, num_nodes::Int, hours::Int,
    fuels::Vector{FuelConfig},
    infrastructure::Dict{String, FuelInfrastructureConfig},
    non_electric_demand::Vector{NonElectricDemandConfig},
    transport_distances::Matrix{Float64},  # OLD field type
    generator_fuel_map::Dict{Int, Tuple{String, Float64, Float64, Float64}},
    primary_energy_resolution::Int,
    investment_resolution::Int,
    discount_rate::Float64,
    loss_of_fuel_supply_penalty::Float64,
    coupling_slack_penalty::Float64,
    mode::String,
    cumulative_capacities::Dict{String, Any},
    initial_storage_levels::Union{Dict{String, Vector{Float64}}, Nothing},
    investment_from_master::Bool,
    h2_production_hourly::Union{Matrix{Float64}, Nothing},
    generator_rated_power::Dict{Int, Vector{Float64}},
    electrolyzer_config::Union{Nothing, ElectrolyzerConfig}
)
    routes = _auto_routes_from_distances(transport_distances, num_nodes, fuels, infrastructure)
    return PrimaryEnergyInput(
        year, base_year, num_nodes, hours, fuels, infrastructure, non_electric_demand,
        routes,
        generator_fuel_map, primary_energy_resolution, investment_resolution,
        discount_rate, loss_of_fuel_supply_penalty, coupling_slack_penalty, mode,
        cumulative_capacities, initial_storage_levels, investment_from_master,
        h2_production_hourly, generator_rated_power, electrolyzer_config
    )
end

"""
    PrimaryEnergyVariables

Container for primary energy decision variables organized by temporal scale.

# Investment Scale
- `transport_capacity_investment`: New transport capacity [fuel, inv_period, route]
- `storage_capacity_investment`: New storage capacity [fuel, inv_period, node]

# Primary Period Scale (planning resolution)
- `fuel_supply_periodic`: Fuel supply [fuel, node, period]
- `fuel_transport_periodic`: Fuel transport [fuel, route, period]
- `non_electric_consumption_periodic`: NE consumption [fuel, sector, node, period]
- `storage_level_start`: Storage at period start [fuel, node, period]
- `storage_level_end`: Storage at period end [fuel, node, period]
- `fuel_loss_of_supply_periodic`: Unmet demand penalty [fuel, node, period]
- `net_hourly_storage_change`: Net storage change for period [fuel, node, period]

# Hourly Operational Scale
- `fuel_storage_level_hourly`: Hourly storage level [fuel, node, hour]
- `fuel_storage_in_hourly`: Hourly storage inflow [fuel, node, hour]
- `fuel_storage_out_hourly`: Hourly storage outflow [fuel, node, hour]
- `fuel_for_power_hourly`: Fuel for power generation [gen_idx, node, hour]
- `non_electric_consumption_hourly`: Hourly NE consumption [fuel, sector, node, hour]
"""
mutable struct PrimaryEnergyVariables
    # Investment scale
    transport_capacity_investment::Dict{String, Matrix{VariableRef}}  # fuel => [inv_p, route]
    storage_capacity_investment::Dict{String, Matrix{VariableRef}}  # fuel => [inv_p, node]

    # Primary period scale
    fuel_supply_periodic::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    fuel_transport_periodic::Dict{String, Matrix{VariableRef}}  # fuel => [route, period]
    non_electric_consumption_periodic::Dict{Tuple{String, String}, Matrix{VariableRef}}  # (fuel, sector) => [node, period]
    storage_level_start::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    storage_level_end::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    fuel_loss_of_supply_periodic::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    fuel_excess_supply_periodic::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    net_hourly_storage_change::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]

    # Hourly operational scale
    fuel_storage_level_hourly::Dict{String, Matrix{VariableRef}}  # fuel => [node, hour+1]
    fuel_storage_in_hourly::Dict{String, Matrix{VariableRef}}  # fuel => [node, hour]
    fuel_storage_out_hourly::Dict{String, Matrix{VariableRef}}  # fuel => [node, hour]
    fuel_for_power_hourly::Dict{Int, Matrix{VariableRef}}  # gen_idx => [node, hour]
    non_electric_consumption_hourly::Dict{Tuple{String, String}, Matrix{VariableRef}}  # (fuel, sector) => [node, hour]
    fuel_loss_of_supply_hourly::Dict{String, Matrix{VariableRef}}  # fuel => [node, hour]
    primary_sector_emissions_hourly::Dict{String, Matrix{VariableRef}}  # fuel => [node, hour]
    total_primary_emissions_hourly::Matrix{VariableRef}  # [node, hour]

    # Coupling slack variables
    coupling_slack_start::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]
    coupling_slack_end::Dict{String, Matrix{VariableRef}}  # fuel => [node, period]

    # Electrolyzer variables (E1: joint H2 production optimization)
    electrolyzer_power::Union{Nothing, Matrix{VariableRef}}  # [node, hour]
    h2_production::Union{Nothing, Matrix{VariableRef}}  # [node, hour]
    electrolyzer_investment::Union{Nothing, Vector{VariableRef}}  # [node]
end

"""
    PrimaryEnergyResult

Solution output from the Primary Energy model.
"""
struct PrimaryEnergyResult
    # Investment results
    transport_investments::Dict{String, Matrix{Float64}}
    storage_investments::Dict{String, Matrix{Float64}}

    # Periodic aggregates
    total_fuel_supply::Dict{String, Vector{Float64}}  # fuel => [period]
    total_ne_demand_satisfied::Dict{String, Vector{Float64}}  # fuel => [period]
    total_loss_of_supply::Dict{String, Vector{Float64}}  # fuel => [period]

    # Final storage levels for carry-over
    final_storage_levels::Dict{String, Vector{Float64}}  # fuel => [node]

    # Per-route transport flows
    transport_flows::Dict{String, Matrix{Float64}}  # fuel => [route × period]

    # Cost breakdown
    total_fuel_cost::Float64
    total_transport_cost::Float64
    total_loss_penalty::Float64
end

# =============================================================================
# Master Problem Types
# =============================================================================

"""
    PrimaryEnergyInvestmentConfig

Configuration for primary energy infrastructure investment in Master Problem.

# Fields
- `fuel_id::String`: Fuel identifier
- `storage_invest_cost::Vector{Float64}`: Storage investment cost per node (\$/unit)
- `storage_invest_max::Vector{Float64}`: Max storage investment per node (units)
- `transport_invest_cost::Float64`: Transport investment cost (\$/unit/km)
- `transport_invest_max::Float64`: Max transport capacity investment (units/day)
"""
struct PrimaryEnergyInvestmentConfig
    fuel_id::String
    storage_invest_cost::Vector{Float64}
    storage_invest_max::Vector{Float64}
    transport_invest_cost::Float64
    transport_invest_max::Float64
end

"""
    MasterProblemInput

Input configuration for the Master Problem (capacity expansion planning).

# Fields
## Planning Horizon
- `years::Vector{Int}`: Years in the planning horizon
- `base_year::Int`: Base year for discount calculations

## System Configuration
- `system_name::String`: System identifier
- `network::NetworkConfig`: Network configuration
- `generators::Vector{GeneratorConfig}`: Generator configurations
- `batteries::Vector{BatteryConfig}`: Battery configurations

## Demand and Profiles
- `base_demand::Matrix{Float64}`: Base demand [hour, node]
- `demand_growth::Float64`: Annual demand growth rate

## Economic Parameters
- `discount_rate::Float64`: Discount rate for NPV calculations
- `max_annual_investment::Float64`: Maximum annual investment budget (\$)

## Targets
- `target_re_penetration::Float64`: Target RE penetration (final year)
- `initial_re_penetration::Float64`: Initial RE penetration (first year)

## Penalties
- `slack_penalty::Float64`: Penalty for constraint violations

## Operational Settings
- `temporal_resolution_hours::Int`: Hours per timestep (for aggregation)
- `representative_days_per_year::Int`: Number of representative days
- `min_day_separation::Int`: Minimum days between representative days

## Life Extension
- `life_extension_cost_factor::Float64`: Fraction of investment cost for annual extension
- `decommissioning_cost_factor::Float64`: Fraction of investment cost for decommissioning

## Solver
- `threads::Int`: Solver threads
- `time_limit::Float64`: Solver time limit
- `gap::Float64`: MIP gap tolerance
- `verbose::Bool`: Verbose output
"""

"""
    SystemNodeRange

Defines which buses belong to a subsystem for per-system RE constraints.
"""
struct SystemNodeRange
    name::String
    first_bus::Int      # 1-indexed
    num_buses::Int
    initial_re::Float64  # Per-system initial RE penetration
end

struct MasterProblemInput
    # Planning horizon
    years::Vector{Int}
    base_year::Int

    # System configuration
    system_name::String
    network::NetworkConfig
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}

    # Candidate technologies for investment (separate from existing units)
    technologies::Vector{TechnologyConfig}
    battery_technologies::Vector{BatteryTechnologyConfig}

    # Demand
    base_demand::Matrix{Float64}
    demand_growth::Float64

    # Economic
    discount_rate::Float64
    max_annual_investment::Float64

    # Targets
    target_re_penetration::Float64
    initial_re_penetration::Float64

    # RE increment bounds (annual min/max change in RE ratio)
    min_re_increment::Float64
    max_re_increment::Float64

    # Per-system node ranges for per-system RE constraints
    # Empty = single global system (backward compat)
    system_node_ranges::Vector{SystemNodeRange}

    # Penalties (from config, not hardcoded)
    slack_penalty::Float64              # Generic slack for budget/capacity adequacy
    loss_of_load_penalty::Float64       # $/MW not supplied (config: Loss_of_load)
    fre_penetration_loss_penalty::Float64  # $/MWh RE shortfall (config: FRE_penetration_loss)
    max_curtailment_ratio::Float64      # Max curtailment as fraction of RE generation (0.05 = 5%)
    curtailment_cost::Float64           # $/MWh penalty for curtailed RE energy
    curtailment_excess_penalty::Float64 # $/MWh penalty for curtailment exceeding ratio limit
    re_excess_penalty::Float64          # $/MWh penalty for RE generation exceeding target

    # Operational
    temporal_resolution_hours::Int
    representative_days_per_year::Int
    min_day_separation::Int
    investment_resolution_hours::Int

    # TSAM configuration
    use_tsam::Bool
    tsam_period_start_hours::Vector{Vector{Int}}     # [year_idx] => [period start hours]
    tsam_period_weights::Vector{Vector{Float64}}      # [year_idx] => [weight per period]
    tsam_chronological_order::Vector{Vector{Int}}     # [year_idx] => [period indices in chrono order]
    tsam_inter_period_linking::Bool

    # Life extension (NOTE: Julia-only feature, may be removed for Python parity)
    life_extension_cost_factor::Float64
    decommissioning_cost_factor::Float64

    # Sectoral demand for operational subproblems (M2)
    # sector_name => Matrix{Float64} (hours × nodes) - full annual demand per sector
    sectoral_demand::Dict{String, Matrix{Float64}}
    # sector_name => criticality weight (higher = more expensive to shed)
    sectoral_criticality::Dict{String, Float64}

    # Primary energy investment configs (M10)
    pe_configs::Vector{PrimaryEnergyInvestmentConfig}

    # Transport routes for primary energy (route-based fuel transport)
    transport_routes::Vector{TransportRoute}

    # Configurable parameters (previously hardcoded)
    reserve_margin::Float64             # Capacity adequacy reserve margin (e.g. 1.15 = 15% margin)
    npv_annual_return_rate::Float64     # NPV revenue estimation rate for investments
    base_lcoe::Float64                  # Base LCOE ($/MWh) for NPV revenue estimation
    max_npv_penalty_per_mw::Float64     # Cap on NPV-based penalty ($/MW)
    max_decommission_cost_per_mw::Float64  # Cap on decommissioning cost ($/MW)
    force_replacement_threshold::Float64   # NPV threshold for forced replacement ($)

    # Hours per year (indexed by year_idx; 8760 or 8784 for leap years)
    hours_per_year::Vector{Int}

    # Reserve parameters (for operational constraints in master)
    reserve_static_default_ratio::Float64   # Default static reserve as fraction of demand
    reserve_static_requirement::Dict{Int, Float64}  # Per-bus static reserve (MW)
    reserve_dynamic_requirement::Dict{Int, Float64}  # Per-bus dynamic reserve (MW)
    dynamic_reserve_contribution::Float64   # Fraction of rated_power for dynamic reserve
    loss_of_reserve_static::Float64         # Penalty $/MW for unmet static reserve
    loss_of_reserve_dynamic::Float64        # Penalty $/MW for unmet dynamic reserve

    # Inertia parameters
    inertia_limit::Float64                  # System inertia requirement (MWs)
    loss_of_inertia_penalty::Float64        # Penalty $/MWs for inertia shortfall

    # CO2
    fuel_co2::Dict{String, Float64}        # Fuel → CO2 factor (t/MWh_fuel)
    co2_cost::Float64                       # $/t CO2

    # Transmission line data (for DC power flow in master)
    transmission_lines::Vector{Tuple{Int,Int}}      # (from_bus, to_bus) per physical line
    transmission_reactances::Vector{Float64}        # Per-line reactance (pu)
    transmission_capacities::Vector{Float64}        # Per-line MW capacity
    transmission_resistances::Vector{Float64}       # Per-line resistance (pu) for PWL losses
    transmission_loss_segments::Int                  # PWL segments (0=no losses, -1=linear, N>0=PWL)

    # Solver
    solver_name::String
    threads::Int
    time_limit::Float64
    gap::Float64
    verbose::Bool
    solver_options::Dict{String, Any}

    # Bidding/offer cost curves for generators and candidate technologies
    gen_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}}
    tech_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}}
    bat_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}}

    # Loss-of-load penalty cap ($/MW per timestep) applied inside Benders
    # subproblems for numerical stability. Ignored by the monolithic solver.
    benders_lol_penalty_cap::Float64
end

# Constructor with defaults
function MasterProblemInput(;
    years::Vector{Int},
    base_year::Int = years[1],
    system_name::String = "primary",
    network::NetworkConfig,
    generators::Vector{GeneratorConfig},
    batteries::Vector{BatteryConfig},
    technologies::Vector{TechnologyConfig} = TechnologyConfig[],
    battery_technologies::Vector{BatteryTechnologyConfig} = BatteryTechnologyConfig[],
    base_demand::Matrix{Float64},
    demand_growth::Float64 = 0.02,
    discount_rate::Float64 = 0.05,
    max_annual_investment::Float64 = 1e4,
    target_re_penetration::Float64 = 1.0,
    initial_re_penetration::Float64 = 0.0,
    min_re_increment::Float64 = 0.0,
    max_re_increment::Float64 = 1.0,
    system_node_ranges::Vector{SystemNodeRange} = SystemNodeRange[],
    slack_penalty::Float64 = 1.0,
    loss_of_load_penalty::Float64 = 10.0,
    fre_penetration_loss_penalty::Float64 = 1e-4,
    max_curtailment_ratio::Float64 = 0.05,
    curtailment_cost::Float64 = 2e-5,
    curtailment_excess_penalty::Float64 = 5e-4,
    re_excess_penalty::Float64 = 1e-4,
    temporal_resolution_hours::Int = 1,
    representative_days_per_year::Int = 2,
    min_day_separation::Int = 30,
    investment_resolution_hours::Int = 8760,
    use_tsam::Bool = false,
    tsam_period_start_hours::Vector{Vector{Int}} = Vector{Int}[],
    tsam_period_weights::Vector{Vector{Float64}} = Vector{Float64}[],
    tsam_chronological_order::Vector{Vector{Int}} = Vector{Int}[],
    tsam_inter_period_linking::Bool = true,
    life_extension_cost_factor::Float64 = 0.05,
    decommissioning_cost_factor::Float64 = 0.10,
    sectoral_demand::Dict{String, Matrix{Float64}} = Dict{String, Matrix{Float64}}(),
    sectoral_criticality::Dict{String, Float64} = Dict{String, Float64}(),
    pe_configs::Vector{PrimaryEnergyInvestmentConfig} = PrimaryEnergyInvestmentConfig[],
    transport_routes::Vector{TransportRoute} = TransportRoute[],
    reserve_margin::Float64 = 1.15,
    npv_annual_return_rate::Float64 = 0.15,
    base_lcoe::Float64 = 9.3e-5,
    max_npv_penalty_per_mw::Float64 = 1.0,
    max_decommission_cost_per_mw::Float64 = 0.5,
    force_replacement_threshold::Float64 = -0.5,
    hours_per_year::Vector{Int} = [hours_in_year(y) for y in years],
    reserve_static_default_ratio::Float64 = 0.15,
    reserve_static_requirement::Dict{Int, Float64} = Dict{Int, Float64}(),
    reserve_dynamic_requirement::Dict{Int, Float64} = Dict{Int, Float64}(),
    dynamic_reserve_contribution::Float64 = 0.5,
    loss_of_reserve_static::Float64 = 0.01,
    loss_of_reserve_dynamic::Float64 = 0.01,
    inertia_limit::Float64 = 0.0,
    loss_of_inertia_penalty::Float64 = 0.01,
    fuel_co2::Dict{String, Float64} = Dict{String, Float64}(),
    co2_cost::Float64 = 0.0,
    transmission_lines::Vector{Tuple{Int,Int}} = Tuple{Int,Int}[],
    transmission_reactances::Vector{Float64} = Float64[],
    transmission_capacities::Vector{Float64} = Float64[],
    transmission_resistances::Vector{Float64} = Float64[],
    transmission_loss_segments::Int = 2,
    solver_name::String = "highs",
    threads::Int = 4,
    time_limit::Float64 = 3600.0,
    gap::Float64 = 0.01,
    verbose::Bool = false,
    solver_options::Dict{String, Any} = Dict{String, Any}(),
    gen_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}} = Dict{Int, Dict{Int, Vector{CostSegment}}}(),
    tech_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}} = Dict{Int, Dict{Int, Vector{CostSegment}}}(),
    bat_cost_curves::Dict{Int, Dict{Int, Vector{CostSegment}}} = Dict{Int, Dict{Int, Vector{CostSegment}}}(),
    benders_lol_penalty_cap::Float64 = 1000.0
)
    return MasterProblemInput(
        years, base_year,
        system_name, network, generators, batteries,
        technologies, battery_technologies,
        base_demand, demand_growth,
        discount_rate, max_annual_investment,
        target_re_penetration, initial_re_penetration,
        min_re_increment, max_re_increment,
        system_node_ranges,
        slack_penalty, loss_of_load_penalty, fre_penetration_loss_penalty,
        max_curtailment_ratio, curtailment_cost, curtailment_excess_penalty, re_excess_penalty,
        temporal_resolution_hours, representative_days_per_year, min_day_separation,
        investment_resolution_hours,
        use_tsam, tsam_period_start_hours, tsam_period_weights,
        tsam_chronological_order, tsam_inter_period_linking,
        life_extension_cost_factor, decommissioning_cost_factor,
        sectoral_demand, sectoral_criticality,
        pe_configs, transport_routes,
        reserve_margin, npv_annual_return_rate,
        base_lcoe, max_npv_penalty_per_mw, max_decommission_cost_per_mw, force_replacement_threshold,
        hours_per_year,
        reserve_static_default_ratio,
        reserve_static_requirement, reserve_dynamic_requirement,
        dynamic_reserve_contribution,
        loss_of_reserve_static, loss_of_reserve_dynamic,
        inertia_limit, loss_of_inertia_penalty,
        fuel_co2, co2_cost,
        transmission_lines, transmission_reactances, transmission_capacities,
        transmission_resistances, transmission_loss_segments,
        solver_name, threads, time_limit, gap, verbose, solver_options,
        gen_cost_curves, tech_cost_curves, bat_cost_curves,
        benders_lol_penalty_cap
    )
end

"""
    MasterProblemVariables

Container for Master Problem decision variables.

# Investment Variables (indexed by [year, tech/bat_tech, node])
- `tech_investment`: Technology capacity investment (MW)
- `bat_tech_power_investment`: Battery technology power investment (MW)
- `bat_tech_capacity_investment`: Battery technology energy investment (MWh)
- `transfer_investment`: Transmission line investment (MW)

# Life Extension Variables (indexed by [year, unit, node])
- `gen_life_extension`: Binary - extend generator life (1) or retire (0)
- `bat_life_extension`: Binary - extend battery life (1) or retire (0)

# RE Tracking (indexed by [year])
- `re_penetration_ratio`: Achieved RE penetration per year

# Operational Variables (per representative day)
- `operational_subproblems`: Dict mapping (year, day) to PowerSystem models

# Slack Variables
- `slack_re_target`: RE target slack per year
- `slack_capacity`: Capacity slack per year per node
- `slack_budget`: Budget slack per year
"""
mutable struct MasterProblemVariables
    # Technology investment variables: Dict{year, Dict{tech_idx, Vector{VariableRef}}} (per bus)
    tech_investment::Dict{Int, Dict{Int, Vector{VariableRef}}}
    bat_tech_power_investment::Dict{Int, Dict{Int, Vector{VariableRef}}}
    bat_tech_capacity_investment::Dict{Int, Dict{Int, Vector{VariableRef}}}
    transfer_investment::Dict{Int, Dict{Tuple{Int,Int}, VariableRef}}

    # Life extension: Dict{year, Dict{unit_idx, Vector{Union{VariableRef, Nothing}}}}
    gen_life_extension::Dict{Int, Dict{Int, Vector{Union{VariableRef, Nothing}}}}
    bat_life_extension::Dict{Int, Dict{Int, Vector{Union{VariableRef, Nothing}}}}

    # RE penetration per (system_idx, year_idx)
    re_penetration_ratio::Dict{Tuple{Int,Int}, VariableRef}

    # Operational subproblems: (year_idx, day_idx) => (model, vars)
    operational_subproblems::Dict{Tuple{Int,Int}, Tuple{Model, PowerSystemVariables}}

    # Operational costs per year and day
    operational_costs::Dict{Int, Vector{AffExpr}}

    # Slack variables
    slack_re_target::Dict{Int, VariableRef}
    slack_capacity::Dict{Tuple{Int,Int}, VariableRef}  # (year, node) => slack
    slack_budget::Dict{Int, VariableRef}

    # Primary energy investment (M10: wired from pe_configs)
    # fuel_id => year => Vector{VariableRef} (per node)
    fuel_storage_investment::Dict{String, Dict{Int, Vector{VariableRef}}}
    # fuel_id => year => Dict{route_index => VariableRef}
    fuel_transport_investment::Dict{String, Dict{Int, Dict{Int, VariableRef}}}

    # Inter-period SOC linking (TSAM): year_idx => (bat_idx, node_idx, period_boundary) => VariableRef
    # period_boundary = 0..K where 0 = year start, K = after last period
    inter_period_soc::Dict{Int, Dict{Tuple{Int,Int,Int}, VariableRef}}

    # Inter-period reservoir-level linking (TSAM seasonal hydro): year_idx =>
    # (gen_idx, node_idx, period_boundary) => VariableRef. Same chronological
    # chain as inter_period_soc, but tracks reservoir energy (MWh-eq) so water
    # can be carried across representative periods (e.g. filled in spring and
    # drawn down in summer) instead of being cyclic within each period.
    inter_period_reservoir::Dict{Int, Dict{Tuple{Int,Int,Int}, VariableRef}}

    # Reservoir capacity investment: year => gen_idx => Vector{VariableRef} (per node, MWh)
    reservoir_investment::Dict{Int, Dict{Int, Vector{VariableRef}}}

    # Investment period grouping: how many years per investment period
    # (derived from investment_resolution_hours ÷ 8760)
    years_per_inv_period::Int
end

"""
    MasterProblemResult

Solution output from the Master Problem.

# Fields
- `status`: Solver termination status
- `objective`: Total NPV cost (\$)
- `solve_time`: Solve time (seconds)

## Investment Decisions
- `tech_investment`: Technology investments [year, tech, node] (MW)
- `bat_tech_power_investment`: Battery technology power investments [year, bat_tech, node] (MW)
- `bat_tech_capacity_investment`: Battery technology energy investments [year, bat_tech, node] (MWh)
- `transfer_investment`: Transmission investments [year, (i,j)] (MW)

## Life Decisions
- `gen_life_extension`: Generator life extension decisions [year, gen, node] (0/1)
- `bat_life_extension`: Battery life extension decisions [year, bat, node] (0/1)

## Summary Metrics
- `total_investment_by_year`: Total investment per year (\$)
- `total_operational_cost_by_year`: Total operational cost per year (\$)
- `re_penetration_by_year`: Achieved RE penetration per year
- `cumulative_capacity_by_year`: Cumulative capacity by technology and year
"""
struct MasterProblemResult
    # Status
    status::MOI.TerminationStatusCode
    objective::Float64
    solve_time::Float64

    # Technology investment decisions (per-technology, not per-generator)
    tech_investment::Dict{Int, Dict{Int, Vector{Float64}}}           # year => tech_idx => [per-bus MW]
    bat_tech_power_investment::Dict{Int, Dict{Int, Vector{Float64}}} # year => bat_tech_idx => [per-bus MW]
    bat_tech_capacity_investment::Dict{Int, Dict{Int, Vector{Float64}}} # year => bat_tech_idx => [per-bus MWh]
    transfer_investment::Dict{Int, Dict{Tuple{Int,Int}, Float64}}

    # Life decisions (per existing unit)
    gen_life_extension::Dict{Int, Dict{Int, Vector{Float64}}}
    bat_life_extension::Dict{Int, Dict{Int, Vector{Float64}}}

    # Summary
    total_investment_by_year::Vector{Float64}
    total_operational_cost_by_year::Vector{Float64}
    re_penetration_by_year::Vector{Float64}
    re_penetration_by_system::Dict{String, Vector{Float64}}  # system_name => [per-year RE]

    # Cumulative capacities for existing units (year => unit_idx => [per-bus])
    cumulative_gen_capacity::Dict{Int, Dict{Int, Vector{Float64}}}
    cumulative_bat_capacity::Dict{Int, Dict{Int, Vector{Float64}}}
    cumulative_bat_power::Dict{Int, Dict{Int, Vector{Float64}}}

    # Cumulative capacities for technology investments (year => tech_idx => [per-bus])
    cumulative_tech_capacity::Dict{Int, Dict{Int, Vector{Float64}}}
    cumulative_bat_tech_power::Dict{Int, Dict{Int, Vector{Float64}}}
    cumulative_bat_tech_capacity::Dict{Int, Dict{Int, Vector{Float64}}}

    # Reservoir capacity investment: year => gen => nodes (MWh)
    reservoir_investment::Dict{Int, Dict{Int, Vector{Float64}}}
end

"""
    BendersResult

Output of the Benders decomposition solver for the master problem.

# Fields
- `solution`: the recovered `MasterProblemResult`
- `objective`: best upper bound found (total NPV cost, \$)
- `iterations`: number of Benders iterations performed
- `gap`: final relative optimality gap (UB - LB) / |UB|
- `lb_history`: lower bound per iteration (master objective)
- `ub_history`: upper bound per iteration (investment + subproblem cost)
- `solve_time`: total wall-clock time (seconds)
"""
struct BendersResult
    solution::MasterProblemResult
    objective::Float64
    iterations::Int
    gap::Float64
    lb_history::Vector{Float64}
    ub_history::Vector{Float64}
    solve_time::Float64
end

# =============================================================================
# Multi-System Types
# =============================================================================

"""
    InterSystemLink

Configuration for a transmission link between two systems.

# Fields
- `from_system::String`: Source system name
- `to_system::String`: Destination system name
- `from_node::Int`: Source node (1-indexed)
- `to_node::Int`: Destination node (1-indexed)
- `existing_capacity_mw::Float64`: Existing transfer capacity (MW)
- `max_investment_mw::Float64`: Maximum additional investment (MW)
- `investment_cost_per_mw::Float64`: Investment cost (\$/MW)
- `loss_factor::Float64`: Transmission loss factor (0-1)
- `distance_km::Float64`: Distance for loss calculation (km)
"""
struct InterSystemLink
    from_system::String
    to_system::String
    from_node::Int
    to_node::Int
    existing_capacity_mw::Float64
    max_investment_mw::Float64
    investment_cost_per_mw::Float64
    loss_factor::Float64           # Fallback for linear loss model
    distance_km::Float64
    cost_per_mw_km::Float64        # M4: operational flow cost ($/MW/km)
    reactance_pu::Float64          # Series reactance (p.u.) for PWL loss g_l = R/(R²+X²)
    resistance_pu::Float64         # Series resistance (p.u.) for PWL loss
end

# Backward-compatible constructor (without cost_per_mw_km, reactance_pu, resistance_pu)
function InterSystemLink(
    from_system::String, to_system::String,
    from_node::Int, to_node::Int,
    existing_capacity_mw::Float64, max_investment_mw::Float64,
    investment_cost_per_mw::Float64, loss_factor::Float64,
    distance_km::Float64
)
    return InterSystemLink(
        from_system, to_system, from_node, to_node,
        existing_capacity_mw, max_investment_mw,
        investment_cost_per_mw, loss_factor,
        distance_km, 1.0, 0.01, 0.001
    )
end

# Backward-compatible constructor (without reactance_pu, resistance_pu)
function InterSystemLink(
    from_system::String, to_system::String,
    from_node::Int, to_node::Int,
    existing_capacity_mw::Float64, max_investment_mw::Float64,
    investment_cost_per_mw::Float64, loss_factor::Float64,
    distance_km::Float64, cost_per_mw_km::Float64
)
    return InterSystemLink(
        from_system, to_system, from_node, to_node,
        existing_capacity_mw, max_investment_mw,
        investment_cost_per_mw, loss_factor,
        distance_km, cost_per_mw_km, 0.01, 0.001
    )
end

"""
    SystemConfig

Configuration for a single system in multi-system optimization.

# Fields
- `name::String`: System identifier
- `network::NetworkConfig`: Network configuration
- `generators::Vector{GeneratorConfig}`: Generator configurations
- `batteries::Vector{BatteryConfig}`: Battery configurations
- `base_demand::Matrix{Float64}`: Base demand [hour, node]
- `target_re_penetration::Float64`: System-specific RE target
- `initial_re_penetration::Float64`: Initial RE penetration
"""
struct SystemConfig
    name::String
    network::NetworkConfig
    generators::Vector{GeneratorConfig}
    batteries::Vector{BatteryConfig}
    technologies::Vector{TechnologyConfig}
    battery_technologies::Vector{BatteryTechnologyConfig}
    base_demand::Matrix{Float64}
    target_re_penetration::Float64
    initial_re_penetration::Float64
end

"""
    MultiSystemMasterInput

Input configuration for multi-system Master Problem.

# Fields
- `systems::Vector{SystemConfig}`: All system configurations
- `inter_system_links::Vector{InterSystemLink}`: Inter-system connections
- `years::Vector{Int}`: Planning horizon years
- `base_year::Int`: Base year for NPV
- `demand_growth::Float64`: Annual demand growth rate
- `discount_rate::Float64`: NPV discount rate
- `max_annual_investment::Float64`: Annual investment budget
- `slack_penalty::Float64`: Constraint violation penalty
- `temporal_resolution_hours::Int`: Temporal aggregation
- `representative_days_per_year::Int`: Representative days count
- `min_day_separation::Int`: Minimum day separation
- `life_extension_cost_factor::Float64`: Life extension cost factor
- `decommissioning_cost_factor::Float64`: Decommissioning cost factor
- `threads::Int`: Solver threads
- `time_limit::Float64`: Solver time limit
- `gap::Float64`: MIP gap
- `verbose::Bool`: Verbose output
- `hours_per_year::Vector{Int}`: Hours per year (8760 or 8784 for leap years)
"""
struct MultiSystemMasterInput
    systems::Vector{SystemConfig}
    inter_system_links::Vector{InterSystemLink}
    years::Vector{Int}
    base_year::Int
    demand_growth::Float64
    discount_rate::Float64
    max_annual_investment::Float64
    slack_penalty::Float64
    loss_of_load_penalty::Float64
    fre_penetration_loss_penalty::Float64
    max_curtailment_ratio::Float64
    temporal_resolution_hours::Int
    representative_days_per_year::Int
    min_day_separation::Int
    life_extension_cost_factor::Float64
    decommissioning_cost_factor::Float64
    solver_name::String
    threads::Int
    time_limit::Float64
    gap::Float64
    verbose::Bool
    hours_per_year::Vector{Int}
    inter_system_loss_segments::Int  # PWL segments for inter-system links (0=linear, N>0=PWL)
end

# Backward-compatible constructor (without solver_name and inter_system_loss_segments)
function MultiSystemMasterInput(
    systems, inter_system_links, years, base_year,
    demand_growth, discount_rate, max_annual_investment, slack_penalty,
    loss_of_load_penalty, fre_penetration_loss_penalty,
    max_curtailment_ratio, temporal_resolution_hours, representative_days_per_year,
    min_day_separation, life_extension_cost_factor, decommissioning_cost_factor,
    threads, time_limit, gap, verbose, hours_per_year
)
    return MultiSystemMasterInput(
        systems, inter_system_links, years, base_year,
        demand_growth, discount_rate, max_annual_investment, slack_penalty,
        loss_of_load_penalty, fre_penetration_loss_penalty,
        max_curtailment_ratio, temporal_resolution_hours, representative_days_per_year,
        min_day_separation, life_extension_cost_factor, decommissioning_cost_factor,
        "highs", threads, time_limit, gap, verbose, hours_per_year,
        2  # default inter_system_loss_segments
    )
end

# =============================================================================
# Stochastic Programming Types
# =============================================================================

"""
    ScenarioMultipliers

Cost and parameter multipliers for a scenario.

# Fields
- `invest_cost_renewables::Float64`: Multiplier for renewable investment costs
- `invest_cost_conventional::Float64`: Multiplier for conventional investment costs
- `fuel_cost::Float64`: Multiplier for fuel costs
- `maintenance_cost::Float64`: Multiplier for maintenance costs
- `invest_cost_storage::Float64`: Multiplier for storage investment costs
- `invest_cost_transmission::Float64`: Multiplier for transmission investment costs
- `discount_rate::Float64`: Scenario-specific discount rate adjustment
- `demand_growth::Float64`: Scenario-specific demand growth adjustment (scales operational costs)
- `fuel_price_growth::Float64`: Multiplier for fuel price escalation over time
- `carbon_price::Float64`: Multiplier for carbon price / CO2 cost
"""
struct ScenarioMultipliers
    invest_cost_renewables::Float64
    invest_cost_conventional::Float64
    fuel_cost::Float64
    maintenance_cost::Float64
    invest_cost_storage::Float64
    invest_cost_transmission::Float64
    discount_rate::Float64
    demand_growth::Float64
    fuel_price_growth::Float64
    carbon_price::Float64
end

# Default multipliers (all 1.0 = no change from base costs)
function ScenarioMultipliers()
    return ScenarioMultipliers(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
end

"""
    Scenario

Stochastic scenario definition.

# Fields
- `name::String`: Scenario identifier
- `probability::Float64`: Probability weight (0-1, sum to 1)
- `multipliers::ScenarioMultipliers`: Cost/parameter multipliers
"""
struct Scenario
    name::String
    probability::Float64
    multipliers::ScenarioMultipliers
end

"""
    StochasticMasterInput

Input for stochastic Master Problem with multiple scenarios.

Extends MasterProblemInput with scenario information.
"""
struct StochasticMasterInput
    base_input::MasterProblemInput
    scenarios::Vector{Scenario}
    use_stochastic::Bool
end

# =============================================================================
# Extended Master Problem Variables (Multi-System + Stochastic)
# =============================================================================

"""
    ExtendedMasterVariables

Extended variable container for multi-system and stochastic Master Problem.

Includes all base MasterProblemVariables plus:
- Inter-system investment and flow variables
- Primary energy infrastructure variables
- Per-scenario operational costs
"""
mutable struct ExtendedMasterVariables
    # Base variables (per system for multi-system)
    # system_name => base variables
    system_vars::Dict{String, MasterProblemVariables}

    # Inter-system investment: year => link_idx => VariableRef
    inter_system_investment::Dict{Int, Dict{Int, VariableRef}}

    # Primary energy investment:
    # fuel_id => year => (storage_inv per node, transport_inv per route)
    fuel_storage_investment::Dict{String, Dict{Int, Vector{VariableRef}}}
    fuel_transport_investment::Dict{String, Dict{Int, Dict{Int, VariableRef}}}

    # Stochastic: scenario_name => year => operational_costs
    scenario_operational_costs::Dict{String, Dict{Int, Vector{AffExpr}}}

    # Annual renewable generation tracking per system
    # system_name => year => total_renewable_gen expression
    annual_renewable_gen::Dict{String, Dict{Int, AffExpr}}
    annual_total_demand::Dict{String, Dict{Int, Float64}}

    # DC-OPF inter-system flow variables (replaces NTC fwd/rev model)
    # (year_idx, day_idx, link_idx, hour) => bidirectional flow (positive = from→to)
    inter_system_pf::Dict{Tuple{Int,Int,Int,Int}, VariableRef}
    # (year_idx, day_idx, link_idx, hour) => PWL loss variable
    inter_system_loss::Dict{Tuple{Int,Int,Int,Int}, VariableRef}
    # (year_idx, day_idx, link_idx, hour) => |flow| = fp + fn (for objective cost)
    inter_system_abs_flow::Dict{Tuple{Int,Int,Int,Int}, AffExpr}

    # Per-system representative day variable store for coupling
    # system_name => (year_idx, day_idx) => PowerSystemVariables reference
    system_day_vars::Dict{String, Dict{Tuple{Int,Int}, Any}}
end

"""
    ExtendedMasterResult

Extended result container for multi-system Master Problem.
"""
struct ExtendedMasterResult
    # Base result
    status::MOI.TerminationStatusCode
    objective::Float64
    solve_time::Float64

    # Per-system investment results
    system_results::Dict{String, MasterProblemResult}

    # Inter-system investments: year => link_idx => MW
    inter_system_investment::Dict{Int, Dict{Int, Float64}}

    # Inter-system DC-OPF flows: (year, day, link, hour) => MW
    inter_system_flows::Dict{Tuple{Int,Int,Int,Int}, Float64}

    # Primary energy investments
    fuel_storage_investment::Dict{String, Dict{Int, Vector{Float64}}}
    fuel_transport_investment::Dict{String, Dict{Int, Dict{Int, Float64}}}

    # Scenario-weighted costs
    expected_operational_cost_by_year::Vector{Float64}

    # NPV breakdown
    total_investment_npv::Float64
    total_operational_npv::Float64
end

# =============================================================================
# NPV Calculation Types
# =============================================================================

"""
    UnitNPV

NPV calculation result for a single unit.

# Fields
- `unit_type::String`: "generator" or "battery"
- `unit_idx::Int`: Unit index
- `node::Int`: Node index
- `system::String`: System name (for multi-system)
- `npv::Float64`: Net present value
- `remaining_lifetime::Float64`: Years remaining
- `recommend_retirement::Bool`: Whether NPV suggests early retirement
"""
struct UnitNPV
    unit_type::String
    unit_idx::Int
    node::Int
    system::String
    npv::Float64
    remaining_lifetime::Float64
    recommend_retirement::Bool
end

"""
    NPVIterationResult

Result from NPV-based iterative retirement analysis.

# Fields
- `iterations::Int`: Number of iterations performed
- `converged::Bool`: Whether iteration converged
- `final_result::MasterProblemResult`: Final solution
- `forced_retirements::Vector{UnitNPV}`: Units forced to retire
- `npv_history::Vector{Dict{String, Float64}}`: NPV per iteration
"""
struct NPVIterationResult
    iterations::Int
    converged::Bool
    final_result::Union{MasterProblemResult, ExtendedMasterResult}
    forced_retirements::Vector{UnitNPV}
    npv_history::Vector{Dict{String, Float64}}
end

# =============================================================================
# MGA/SPORES Types
# =============================================================================

"""
    MGAResult

Result container for MGA/SPORES (Modeling to Generate Alternatives /
Spatially-explicit Practically Optimal REsultS).

Contains multiple near-optimal alternatives with diverse investment patterns.

# Fields
- `alternatives::Vector{MasterProblemResult}`: All solutions (index 1 = cost-optimal)
- `num_alternatives::Int`: Total number of alternatives (including cost-optimal)
- `slack_fraction::Float64`: Near-optimal slack parameter ε
- `optimal_cost::Float64`: Cost-optimal objective value C*
- `alternative_costs::Vector{Float64}`: Actual cost of each alternative
- `diversity_objectives::Vector{Float64}`: Diversity objective for alternatives 2..K
"""
struct MGAResult
    alternatives::Vector{MasterProblemResult}
    num_alternatives::Int
    slack_fraction::Float64
    optimal_cost::Float64
    alternative_costs::Vector{Float64}
    diversity_objectives::Vector{Float64}
    # Tag for each non-optimal alternative naming the SPORES objective
    # that produced it (e.g. "hsj_diversity", "min_total_build",
    # "max_tech_equity"…). For the classical MGA loop every entry is
    # "hsj_diversity"; for SPORES runs each entry matches one of the
    # SporesObjective values in the Python schema. Length matches
    # ``diversity_objectives``; empty for back-compat with old result
    # files that predate the SPORES roadmap.
    objective_labels::Vector{String}
end

# Convenience constructor for back-compat with sites that don't pass
# labels (e.g., old test fixtures): defaults the labels to all
# "hsj_diversity", which is what every pre-SPORES caller did.
function MGAResult(
    alternatives::Vector{MasterProblemResult},
    num_alternatives::Int,
    slack_fraction::Float64,
    optimal_cost::Float64,
    alternative_costs::Vector{Float64},
    diversity_objectives::Vector{Float64},
)
    labels = fill("hsj_diversity", length(diversity_objectives))
    return MGAResult(alternatives, num_alternatives, slack_fraction,
                     optimal_cost, alternative_costs,
                     diversity_objectives, labels)
end

