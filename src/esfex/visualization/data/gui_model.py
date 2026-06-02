"""Mutable GUI state model with Qt signals for synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from esfex.visualization.data.undo import UndoStack
from esfex.utils.temporal import HOURS_STD_YEAR


# Renewable fuels: always present, no transport/storage/entry infrastructure needed
RENEWABLE_FUELS: set[str] = {"Sun", "Wind", "Water", "OTEC"}


# ── Visual style (GUI-only; persisted via the ``_gui_styles`` block) ─


@dataclass
class VisualStyle:
    """Visual customization for map elements."""

    color: Optional[str] = None       # hex "#3498db"
    size: Optional[float] = None      # marker size in pixels
    icon_shape: Optional[str] = None  # "circle"|"square"|"diamond"|"triangle-up"|"triangle-down"|"hexagon"|"pentagon"|"horizontal-bar"|"star"
    opacity: Optional[float] = None   # 0-1 (for zones)
    width: Optional[float] = None     # stroke width (for lines)


# ── Data classes (plain state, no Qt dependency) ─────────────────


@dataclass
class GeoPoint:
    lat: float
    lng: float
    label: str = ""


@dataclass
class EndpointRef:
    """Reference to a magnetic element that a line endpoint snaps to."""

    element_type: str   # "node", "generator", "battery", "transformer", "fuel_entry"
    element_id: str     # e.g. "0" for node, "unit_1_n0" for generator


@dataclass
class GuiNodeDemand:
    """Demand data loaded from CSV for one node."""

    csv_path: Optional[str] = None
    data: Optional[list[float]] = None
    num_hours: int = 0
    peak_mw: float = 0.0
    total_mwh: float = 0.0


@dataclass
class NodeTechnology:
    """Per-node technology investment parameters."""

    name: str                         # e.g. "Solar", "Wind", "Li-ion Battery"
    category: str                     # "generation", "storage", "fuel", "transmission", "fuel_transport", "transformation"
    invest_cost: float = 0.0          # $/MW or $/MWh
    invest_max: float = 0.0           # MW or MWh
    existing_capacity: float = 0.0    # MW or MWh


@dataclass
class GuiNode:
    index: int
    name: str
    centroid_lat: float = 0.0
    centroid_lng: float = 0.0
    reserve_static: float = 0.0
    reserve_dynamic: float = 0.0
    reserve_duration: int = 1
    losses: float = 0.0
    transference_invest_cost: float = 0.0
    transference_invest_max: float = 0.0
    style: VisualStyle = field(default_factory=VisualStyle)
    demand: GuiNodeDemand = field(default_factory=GuiNodeDemand)
    technologies: list[NodeTechnology] = field(default_factory=list)


@dataclass
class GuiBus:
    """An electrical bus within a node.

    Buses are the electrical connection points where equipment attaches.
    Each bus belongs to a parent node (geographic region) and has
    electrical properties (voltage, frequency, type).

    The ``role`` field declares the bus's electrical purpose:

    - ``"connection"``: pure junction — connects generators, transformers
      or transmission lines but carries no demand. ``demand_fraction`` is
      forced to 0 and the optimizer skips load_shed/reserve variables for
      this bus.
    - ``"load"``: substation feeder serving real demand. ``demand_fraction
      > 0`` and full KCL with load_shed/reserve terms.
    - ``"mixed"``: both equipment-bearing and load-carrying (e.g.
      industrial site co-located with its dedicated generator).
    """

    bus_id: str                          # e.g., "bus_0", "bus_1"
    name: str = ""
    parent_node: int = 0                 # Node index
    voltage_kv: float = 220.0
    frequency_hz: float = 50.0
    current_type: str = "AC"             # "AC" or "DC"
    bus_type: str = "PQ"                 # "PQ", "PV", "slack" (AC PF class)
    role: str = "connection"             # "connection", "load", "mixed"
                                         # Default conservative: bus carries no
                                         # demand unless explicitly marked load
                                         # or mixed. Operational LP fragments
                                         # demand only across load/mixed buses.
    demand_fraction: float = 0.0         # Share of parent node's demand (0..1)
                                         # Sum across load+mixed buses of the
                                         # same node should equal 1.0; "connection"
                                         # buses must keep this at 0.
    latitude: float = 0.0               # Absolute map position
    longitude: float = 0.0
    style: VisualStyle = field(default_factory=VisualStyle)


@dataclass
class GuiGeneratorInstance:
    """A single generator unit at a specific node.

    On YAML export, instances sharing the same ``unit_key`` are
    aggregated back into per-node arrays.
    """

    instance_id: str       # unique, e.g. "unit_1_bus_0"
    unit_key: str          # groups instances for export, e.g. "unit_1"
    name: str
    gen_type: str          # "Renewable" or "Non-renewable"
    fuel: str
    bus: str = "bus_0"     # bus_id this generator is connected to
    node: int = 0          # DEPRECATED: kept for backward compat, derived from bus
    reservable: bool = True
    technology_id: Optional[str] = None  # references GuiTechnology.tech_id
    availability_file: Optional[str] = None
    # Scalar parameters (previously per-node arrays)
    rated_power: float = 0.0
    min_power: float = 0.0
    life_time: int = 25
    initial_age: int = 0
    degradation_rate: float = 0.0
    fuel_cost: float = 0.0
    fixed_cost: float = 0.0
    maintenance_cost: float = 0.0
    eff_at_rated: float = 0.35
    eff_at_min: float = 0.25
    min_up: int = 0
    min_down: int = 0
    ramp_up: float = 0.0
    ramp_down: float = 0.0
    inertia: float = 0.0
    droop: float = 0.05
    governor_time_const: float = 5.0
    start_up_cost: float = 0.0
    decommissioning_cost: float = 0.0
    frequency_hz: float = 50.0
    current_type: str = "AC"  # "AC", "DC", "AC_DC"
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0
    # Reservoir (optional — 0 means no reservoir)
    reservoir_capacity: float = 0.0           # MWh, 0 = no reservoir
    reservoir_initial_level: float = 0.5      # Fraction (0-1)
    reservoir_min_level: float = 0.1          # Fraction (0-1)
    reservoir_max_level: float = 1.0          # Fraction (0-1)
    reservoir_inflow_file: Optional[str] = None
    reservoir_turbine_efficiency: float = 0.9
    reservoir_evaporation_rate: float = 0.0
    reservoir_pump_capacity: float = 0.0      # MW, 0 = no pump
    reservoir_pump_efficiency: float = 0.85
    reservoir_spillage_allowed: bool = True
    reservoir_invest_cost: float = 0.0        # $/MWh
    reservoir_invest_max: float = 0.0         # MWh
    # Bidding/offer curve for fuel cost
    fuel_cost_curve_type: str = "flat"        # flat, linear, stepwise, exponential
    fuel_cost_curve_data: Optional[dict] = None
    # Risk & Resilience — geographic risk derating (0-1)
    risk_coefficient: float = 1.0


@dataclass
class GuiBatteryInstance:
    """A single battery/storage unit at a specific bus."""

    instance_id: str
    unit_key: str
    name: str
    fuel: str = "None"
    bus: str = "bus_0"     # bus_id this battery is connected to
    node: int = 0          # DEPRECATED: kept for backward compat, derived from bus
    reservable: bool = True
    spillage: bool = True
    technology_id: Optional[str] = None  # references GuiTechnology.tech_id
    min_duration_hours: Optional[int] = None
    max_duration_hours: Optional[int] = None
    availability_file: Optional[str] = None
    # Scalar parameters
    rated_power: float = 0.0
    capacity: float = 0.0
    efficiency_charge: float = 0.9
    efficiency_discharge: float = 0.9
    soc_initial: float = 0.5
    max_DoD: float = 1.0
    MaxChargePower: float = 0.0
    MaxDischargePower: float = 0.0
    life_time: int = 20
    initial_age: int = 0
    degradation_rate: float = 0.0
    min_power: float = 0.0
    min_up: int = 0
    min_down: int = 0
    ramp_up: float = 1.0
    ramp_down: float = 1.0
    eff_at_rated: float = 0.9
    eff_at_min: float = 0.9
    inertia: float = 0.0
    start_up_cost: float = 0.0
    fuel_cost: float = 0.0
    fixed_cost: float = 0.0
    maintenance_cost: float = 0.0
    throughput_degradation_cost: float = 0.0
    # Bidding/offer curve for discharge cost
    discharge_cost_curve_type: str = "flat"   # flat, linear, stepwise, exponential
    discharge_cost_curve_data: Optional[dict] = None
    decommissioning_cost: float = 0.0
    current_type: str = "DC"  # "AC" or "DC"
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0
    # Risk & Resilience — geographic risk derating (0-1)
    risk_coefficient: float = 1.0


@dataclass
class GuiTransmissionLine:
    line_id: str                                # unique, e.g. "line_0", "line_1"
    from_bus: str = "bus_0"
    to_bus: str = "bus_0"
    from_node: int = 0     # DEPRECATED: kept for backward compat, derived from bus
    to_node: int = 0       # DEPRECATED: kept for backward compat, derived from bus
    capacity_mw: float = 0.0
    voltage_kv: Optional[float] = None
    line_type: Optional[str] = None
    waypoints: list[GeoPoint] = field(default_factory=list)
    style: VisualStyle = field(default_factory=VisualStyle)
    from_endpoint: Optional[EndpointRef] = None
    to_endpoint: Optional[EndpointRef] = None
    # Power flow properties
    length_km: Optional[float] = None          # None = auto-calculate from coords
    base_impedance: Optional[float] = None     # ohm (None = system default)
    reactance_per_km: Optional[float] = None   # ohm/km (None = system default)
    reactance_pu: Optional[float] = None       # None = use global default
    resistance_pu: Optional[float] = None
    susceptance_pu: Optional[float] = None
    num_circuits: int = 1
    frequency_hz: float = 50.0
    current_type: str = "AC"  # "AC" or "DC"
    # True when this line is a purely-visual connector (bus → trafo,
    # gen → bus, etc.) — drawn on the map but NOT included in the
    # solver's electrical network. Capacity / impedance / etc. on a
    # decorative line are display-only.
    decorative: bool = False


@dataclass
class GuiTransformer:
    name: str
    from_bus: str = "bus_0"
    to_bus: str = "bus_0"
    from_node: int = 0     # DEPRECATED
    to_node: int = 0       # DEPRECATED
    from_voltage_kv: float = 220.0
    to_voltage_kv: float = 110.0
    rated_power_mva: float = 100.0
    impedance_pu: float = 0.1
    losses_fraction: float = 0.005
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0


@dataclass
class GuiACDCConverter:
    """AC/DC converter (rectifier/inverter) connecting AC and DC buses."""

    name: str
    converter_type: str = "VSC"       # "VSC" or "LCC"
    from_bus: str = "bus_0"           # AC side bus_id
    to_bus: str = "bus_0"             # DC side bus_id
    from_node: int = 0                # DEPRECATED
    to_node: int = 0                  # DEPRECATED
    from_voltage_kv: float = 220.0
    dc_voltage_kv: float = 320.0
    rated_power_mva: float = 100.0
    min_power_mva: float = 0.0
    efficiency_rectify: float = 0.98  # AC→DC
    efficiency_invert: float = 0.98   # DC→AC
    standby_losses_mw: float = 0.5
    reactive_power_min_mvar: float = -50.0
    reactive_power_max_mvar: float = 50.0
    power_factor: float = 1.0
    impedance_pu: float = 0.05
    resistance_pu: float = 0.01
    fixed_cost: float = 0.0          # $/MW/year
    variable_cost: float = 0.0       # $/MWh
    life_time: int = 30
    initial_age: int = 0
    degradation_rate: float = 0.005
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0


@dataclass
class GuiFrequencyConverter:
    """Frequency converter connecting buses at different frequencies."""

    name: str
    from_bus: str = "bus_0"
    to_bus: str = "bus_0"
    from_node: int = 0     # DEPRECATED
    to_node: int = 0       # DEPRECATED
    from_frequency_hz: float = 50.0
    to_frequency_hz: float = 60.0
    rated_power_mva: float = 100.0
    min_power_mva: float = 0.0
    efficiency_a_to_b: float = 0.98
    efficiency_b_to_a: float = 0.98
    standby_losses_mw: float = 0.5
    reactive_power_min_mvar: float = -50.0
    reactive_power_max_mvar: float = 50.0
    impedance_pu: float = 0.05
    resistance_pu: float = 0.01
    fixed_cost: float = 0.0
    variable_cost: float = 0.0
    life_time: int = 30
    initial_age: int = 0
    degradation_rate: float = 0.005
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0


@dataclass
class GuiDevelopmentZone:
    name: str
    technology: str
    layer: str = "electrical"
    node: Optional[int] = None
    polygon: list[GeoPoint] = field(default_factory=list)
    max_capacity_mw: Optional[float] = None
    notes: Optional[str] = None
    style: VisualStyle = field(default_factory=VisualStyle)
    # Interconnection parameters
    line_cost_per_mw_km: float = 1500.0
    transformer_cost_per_mw: float = 50000.0
    target_bus_override: Optional[int] = None
    allowed_generators: list[str] = field(default_factory=list)
    allowed_technologies: dict[str, float] = field(default_factory=dict)  # tech_id → max_invest_mw (0 = unlimited)
    exclusive: bool = False


@dataclass
class FuelEntryParams:
    """Per-fuel import parameters for a fuel entry point."""

    max_import_rate: float = 0.0
    import_cost: float = 0.0


@dataclass
class GuiFuelEntryPoint:
    name: str
    fuels: list[str] = field(default_factory=list)
    node: int = 0
    coordinate: GeoPoint = field(default_factory=lambda: GeoPoint(0, 0))
    fuel_params: dict[str, FuelEntryParams] = field(default_factory=dict)
    style: VisualStyle = field(default_factory=VisualStyle)


@dataclass
class GuiFuelSource:
    """System-level primary energy source configuration."""

    source_id: str                   # key, e.g. "Oil", "Gas"
    name: str
    unit: str                        # e.g. "kTon", "MMBTU"
    max_availability: list[float] = field(default_factory=list)
    import_cost: list[float] = field(default_factory=list)
    storage_capacity: list[float] = field(default_factory=list)
    initial_storage_level: list[float] = field(default_factory=list)
    min_storage_level: float = 0.1
    storage_investment_cost: float = 0.0
    transport_cost: float = 0.0
    transport_losses: float = 0.0
    max_storage_investment_per_node: float = 0.0
    max_transport_investment_per_arc: float = 0.0


@dataclass
class FuelRouteParams:
    """Per-fuel transport parameters for a fuel route."""

    capacity: float = 0.0           # transport capacity (units/hour)
    transport_cost: float = 0.0     # $/unit/km
    losses_fraction: float = 0.0    # loss fraction per 100km


@dataclass
class GuiFuelTransportRoute:
    """A fuel transport route (pipeline, shipping route) between two points."""

    route_id: str                    # unique, e.g. "fuel_route_0"
    fuels: list[str] = field(default_factory=list)
    from_node: int = 0
    to_node: int = 0
    capacity: float = 0.0           # transport capacity (units/hour) — legacy/default
    transport_cost: float = 0.0     # $/unit/km — legacy/default
    losses_fraction: float = 0.0    # loss fraction per 100km — legacy/default
    fuel_params: dict[str, FuelRouteParams] = field(default_factory=dict)
    length_km: Optional[float] = None
    waypoints: list[GeoPoint] = field(default_factory=list)
    style: VisualStyle = field(default_factory=lambda: VisualStyle(
        color="#c0392b", width=3.0,
    ))
    from_endpoint: Optional[EndpointRef] = None
    to_endpoint: Optional[EndpointRef] = None


@dataclass
class FuelStorageParams:
    """Per-fuel storage parameters."""

    capacity: float = 0.0       # storage capacity in fuel units (kTon, MMBTU, etc.)
    initial_level: float = 0.5  # fraction 0-1
    min_level: float = 0.1      # fraction 0-1


@dataclass
class GuiFuelStorage:
    """A fuel storage facility at a specific node."""

    storage_id: str              # "fuel_storage_0"
    name: str
    fuels: list[str] = field(default_factory=list)
    fuel_params: dict[str, FuelStorageParams] = field(default_factory=dict)
    node: int = 0
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0


# ── Fuel properties (FuelConfig) ──────────────────────────────────


@dataclass
class GuiFuel:
    """Physical/economic fuel properties (distinct from supply infrastructure)."""

    fuel_id: str              # key, e.g. "Fuel_oil", "Sun"
    name: str
    unit: Optional[str] = None  # None for renewables
    emission_factor: float = 0.0  # ton CO2/MWh
    energy_content: Optional[float] = None  # MWh/unit (None for renewables)
    price_base: float = 0.0      # $/unit
    price_growth_rate: float = 0.0  # annual growth rate


# ── System-level settings ─────────────────────────────────────────


@dataclass
class GuiSystemSettings:
    """Per-system simulation parameters."""

    demand_scale: float = 1.0
    discount_rate: float = 0.05
    base_lcoe: float = 93.0
    target_re_penetration: float = 1.0
    min_annual_increment: float = 0.01
    max_annual_increment: float = 0.10
    max_annual_system_cost: float = 20e9
    max_npv_penalty_per_mw: float = 1e6
    max_decommission_cost_per_mw: float = 5e5
    force_replacement: float = -5e5
    life_extension_cost_factor: float = 0.20
    loss_demand_threshold: float = 0.05
    inertia_limit_threshold: float = 0.1
    sim_rooftop: bool = False
    # CO2 budget (merged from former GuiCO2Budget)
    co2_budget_enabled: bool = True
    co2_annual_budget: float = 1e6  # tonnes/year


@dataclass
class GuiPenalties:
    """Penalty coefficients for constraint violations."""

    loss_of_load: float = 50_000.0  # $/MWh — VOLL.  Industry-standard
                                     # residential VOLL ~$5K, industrial
                                     # ~$15K-$50K, critical loads ~$100K.
                                     # Previous default (10e6) was 100-2000×
                                     # higher than any reference value and
                                     # produced absurd cost objectives that
                                     # also distorted the master investment
                                     # decision toward avoiding *any* shed.
    loss_of_reserve_static: float = 100.0
    loss_of_reserve_dynamic: float = 100.0
    loss_of_inertia: float = 200.0
    transfer_margin: float = 100.0
    curtailment: float = 100.0
    max_curtailment_ratio: float = 0.05
    curtailment_cost: float = 20.0
    curtailment_excess_penalty: float = 500.0
    re_excess_penalty: float = 100.0
    rooftop_curtailment: float = 5.0
    co2_cost: float = 10.0
    co2_budget_violation: float = 500.0
    fre_penetration_loss: float = 100.0
    ev_loss: float = 10.0
    loss_of_fuel_supply: float = 100.0
    coupling_slack_penalty: float = 1.0
    transport_congestion: float = 100.0
    storage_violation: float = 100.0
    non_electric_demand_loss: float = 100.0
    # Load criticality penalties (multipliers on VOLL per sector).
    # Real LP code multiplies these by `loss_of_load_penalty` (VOLL,
    # typically $5–50K/MWh) to compute sectoral shedding cost, so the
    # values here must be SMALL modifiers (≈1–3×), not absolute $/MWh
    # quantities.  Earlier defaults of 1, 10, 100, 1000 produced shed
    # costs of $50K–$50M per MWh, inflating reported total costs by
    # 50–1000× and distorting investment economics.
    criticality_critical: float = 3.0
    criticality_high: float = 2.0
    criticality_medium: float = 1.0
    criticality_low: float = 0.5


@dataclass
class GuiDCPowerFlow:
    """DC power flow settings (system-level angle/slack only)."""

    max_angle_diff_deg: float = 30.0
    slack_bus: int = 0


@dataclass
class GuiACPowerFlow:
    """AC power flow configuration parameters."""

    base_mva: float = 100.0
    voltage_min_pu: float = 0.90
    voltage_max_pu: float = 1.10
    default_power_factor: float = 0.85
    load_power_factor: float = 0.9
    q_slack_penalty: float = 100.0
    min_reactance_pu: float = 0.01
    tap_ratio_min: float = 0.5
    tap_ratio_max: float = 2.0
    q_min_ratio: float = 0.5


# ── Electrolyzer ──────────────────────────────────────────────────


@dataclass
class GuiElectrolyzerInstance:
    """A single electrolyzer unit at a specific bus."""

    instance_id: str
    unit_key: str
    name: str
    fuel: str = "Hydrogen"
    technology: str = "PEM"  # PEM, Alkaline, SOE
    technology_id: Optional[str] = None  # references GuiTechnology.tech_id
    bus: str = "bus_0"     # bus_id this electrolyzer is connected to
    node: int = 0          # DEPRECATED
    life_time: int = 20
    initial_age: int = 0
    degradation_rate: float = 0.01
    rated_power: float = 0.0
    min_power: float = 0.1
    ramp_up: float = 0.5
    ramp_down: float = 0.5
    eff_at_rated: float = 0.65
    eff_at_min: float = 0.55
    energy_per_kg_h2: float = 50.0
    fixed_cost: float = 0.0
    variable_cost: float = 0.0
    water_cost: float = 0.001
    style: VisualStyle = field(default_factory=VisualStyle)
    latitude: float = 0.0
    longitude: float = 0.0


# ── EV Configuration ─────────────────────────────────────────────


@dataclass
class GuiEVCategory:
    """EV fleet category (light, medium, heavy, buses)."""

    category_id: str
    battery_capacity: float = 50.0   # kWh
    charging_power: float = 7.0      # kW
    v2g_power: float = 5.0           # kW
    v2g_participation: float = 0.3
    efficiency_charge: float = 0.9
    efficiency_discharge: float = 0.9
    min_soc: float = 0.2
    max_adoption: float = 35.0
    growth_rate: float = 0.14
    mid_point_fraction: float = 0.5
    quantity: list[int] = field(default_factory=list)       # per-node
    base_pattern: list[float] = field(default_factory=list)  # 24-hour


@dataclass
class GuiEVConfig:
    """Complete EV configuration for a system."""

    initial_soc: list[float] = field(default_factory=list)  # per-node
    categories: dict[str, GuiEVCategory] = field(default_factory=dict)


# ── Rooftop Solar ─────────────────────────────────────────────────


@dataclass
class GuiRooftopSolar:
    """Rooftop solar configuration."""

    adoption_scenario: str = "medium"  # low, medium, high
    weather_variability: str = "normal"  # low, normal, high
    simulation_seed: int = 42
    performance_ratio: float = 0.75
    degradation_rate: float = 0.005
    cost_per_kw: float = 1200.0
    cost_reduction_rate: float = 0.08
    o_and_m_cost: float = 20.0
    base_year: int = 2025
    target_year: int = 2050
    systems_per_node: list[int] = field(default_factory=list)      # per-node
    avg_system_size: list[float] = field(default_factory=list)     # per-node
    initial_adoption: list[float] = field(default_factory=list)    # per-node
    max_adoption: dict[str, float] = field(default_factory=dict)   # by scenario
    adoption_rates: dict[str, float] = field(default_factory=dict)


# ── Demand Sectors ────────────────────────────────────────────────


@dataclass
class GuiDemandSector:
    """Electric demand sector configuration."""

    sector_id: str
    is_flexible: bool = False
    flexibility_ratio: float = 0.0
    criticality: str = "medium"  # "critical", "high", "medium", "low"
    delay_tolerance: int = 0
    price_sensitivity: float = 0.0


@dataclass
class GuiNonElectricDemand:
    """Non-electric fuel demand configuration."""

    demand_id: str
    fuel: str
    unit: str
    is_flexible: bool = False
    flexibility_ratio: float = 0.0
    criticality: str = "medium"  # "critical", "high", "medium", "low"
    delay_tolerance: int = 0
    price_sensitivity: float = 0.0
    demand: list[int] = field(default_factory=list)  # per-node annual


# ── Global Settings ───────────────────────────────────────────────


@dataclass
class GuiVisualScaling:
    """Global scale factors for proportional visual scaling on the map.

    Final size = max(min_px, scale * element_attribute).
    Separate scale factors for each unit domain (electrical, energy, fuel).
    Adjust scale factors to match the region being analyzed.
    """

    # Markers
    marker_min_px: float = 6.0               # floor for all markers
    electrical_marker_scale: float = 0.02    # px/MW or px/MVA (gen, elz, tr, conv)
    energy_marker_scale: float = 0.02        # px/MWh (batteries)
    fuel_marker_scale: float = 0.5           # px/fuel-unit (fuel storage, fuel entry)

    # Lines
    line_min_px: float = 1.5                 # floor for all lines
    electrical_line_scale: float = 0.005     # px/MW (transmission lines)
    fuel_line_scale: float = 0.1             # px/fuel-unit (fuel routes)


@dataclass
class GuiGlobalSettings:
    """Top-level simulation settings (not per-system).

    When created without explicit values (new project), fields are
    populated from user preferences in ``~/.config/esfex/preferences.json``
    so that Solver and Simulation defaults from Preferences take effect.
    """

    # Systems selection (empty = all systems)
    systems_to_simulate: list[str] = field(default_factory=list)

    simulation_mode: str = "development"
    unit_commitment_hours: int = 24
    # When simulation_mode='development', this toggles whether the
    # operational subproblem inside each rolling-horizon window runs
    # with binary commitment variables (UC) or as pure LP economic
    # dispatch. Mirrors ``ESFEXConfig.master_problem.use_uc_in_dispatch``.
    # Off by default — most planning runs converge faster as LP.
    mp_use_uc_in_dispatch: bool = False
    date_start: str = "01/01/2025 00:00"
    enable_primary_energy: bool = True
    console_log_level: str = "basic"  # "basic" or "high"
    # Temporal
    resolution_hours: int = 1
    rolling_horizon_hours: int = 48
    overlap_hours: int = 6
    investment_resolution: int = HOURS_STD_YEAR
    primary_energy_resolution: int = 24
    use_rolling_horizon: bool = True
    # Solver
    solver_name: str = "highs"
    solver_threads: int = 4
    solver_time_limit: int = 10800
    solver_gap: float = 0.01
    solver_verbose: bool = False
    solver_scale_constraints: bool = True
    solver_specific_options: dict[str, Any] = field(default_factory=dict)
    # N1 Security
    n1_enabled: bool = False
    n1_apply_to_modes: list[str] = field(
        default_factory=lambda: ["unit_commitment"]
    )
    n1_transmission_enabled: bool = True
    n1_transmission_reserve_factor: float = 0.70
    n1_critical_line_threshold: float = 0.0
    n1_generation_enabled: bool = True
    n1_generation_reserve_type: str = "largest_unit"
    n1_generation_reserve_percentage: float = 0.15
    n1_scopf_enabled: bool = False
    n1_scopf_max_iterations: int = 5
    n1_scopf_violation_tolerance: float = 0.01
    n1_corrective_enabled: bool = False
    n1_contingency_depth: str = "n1"
    n1_redistribution_mode: str = "pro_rata"
    n1_pi_screening_threshold: float = 0.0
    n1_transformer_contingencies: bool = False
    n1_battery_contingencies: bool = False
    # Master Problem
    mp_stochastic: bool = False
    mp_representative_days: int = 5
    mp_min_day_separation: int = 5
    mp_use_tsam: bool = False
    mp_tsam_num_periods: int = 10
    mp_tsam_method: str = "kmedoids"
    mp_tsam_inter_period_linking: bool = True
    # MGA
    mp_mga_enabled: bool = False
    # Generation method — "mga" runs the classical HSJ loop;
    # "spores" picks one objective per alternative from mp_mga_objectives.
    # Phase-1 plumbing: round-trips through YAML, but the Julia
    # implementation for "spores" is not wired yet, so the adapter
    # rejects the value at run time.
    mp_mga_method: str = "mga"
    mp_mga_objectives: list[str] = field(default_factory=list)
    mp_mga_num_alternatives: int = 10
    mp_mga_slack_fraction: float = 0.05
    mp_mga_investment_threshold: float = 0.1
    # Visual scaling
    visual_scaling: GuiVisualScaling = field(default_factory=GuiVisualScaling)
    # Risk & Resilience
    risk_enabled: bool = False
    risk_measure: str = "expected"
    risk_cvar_alpha: float = 0.95
    risk_cvar_lambda: float = 0.5
    risk_combination_method: str = "independent"
    risk_voll_residential: float = 5000.0
    risk_voll_commercial: float = 25000.0
    risk_voll_industrial: float = 15000.0
    risk_voll_critical: float = 100000.0
    risk_base_temperature: float = 18.0
    risk_heating_coefficient: float = 0.0
    risk_cooling_coefficient: float = 0.0
    risk_insurance_premium_rate: float = 0.0
    risk_monte_carlo_samples: int = 1000
    risk_monte_carlo_seed: int = 42

    # Set to True by serializer when loading from file (skip pref override)
    _loaded_from_file: bool = field(default=False, repr=False)

    def __post_init__(self):
        """Override dataclass defaults with user preferences for new projects."""
        if self._loaded_from_file:
            return
        try:
            from esfex.visualization.preferences import load_preferences, get_preference
            prefs = load_preferences()
            # Simulation defaults
            self.simulation_mode = get_preference(
                prefs, "simulation", "default_mode", self.simulation_mode)
            self.resolution_hours = get_preference(
                prefs, "simulation", "default_resolution", self.resolution_hours)
            self.rolling_horizon_hours = get_preference(
                prefs, "simulation", "default_rolling_horizon", self.rolling_horizon_hours)
            self.overlap_hours = get_preference(
                prefs, "simulation", "default_overlap", self.overlap_hours)
            self.enable_primary_energy = get_preference(
                prefs, "simulation", "default_primary_energy", self.enable_primary_energy)
            self.console_log_level = get_preference(
                prefs, "simulation", "default_log_level", self.console_log_level)
            # Solver defaults
            solver_name = get_preference(prefs, "solver", "default_solver", "HiGHS")
            self.solver_name = solver_name.lower()
            self.solver_threads = get_preference(
                prefs, "solver", "threads", self.solver_threads)
            self.solver_time_limit = get_preference(
                prefs, "solver", "time_limit", self.solver_time_limit)
            self.solver_gap = get_preference(
                prefs, "solver", "mip_gap", self.solver_gap)
            self.solver_verbose = get_preference(
                prefs, "solver", "verbose", self.solver_verbose)
            self.solver_scale_constraints = get_preference(
                prefs, "solver", "scale_constraints", self.solver_scale_constraints)
        except Exception:
            pass  # Preferences unavailable — keep dataclass defaults


@dataclass
class GuiStochasticScenario:
    """A stochastic scenario with arbitrary cost multipliers."""

    name: str
    probability: float = 0.5
    description: str = ""
    # Dynamic cost multipliers: key -> multiplier value (default 1.0)
    multipliers: dict[str, float] = field(default_factory=dict)


@dataclass
class GuiInterSystemLink:
    """An inter-system connection (transmission line or fuel route between systems)."""

    link_id: str              # "islink_0", "islink_1"
    link_type: str            # "transmission" or "fuel_route"
    from_system: str
    to_system: str
    from_node: int            # node index in from_system
    to_node: int              # node index in to_system
    capacity_mw: float = 0.0
    investment_cost: float = 0.0
    max_investment_mw: float = 0.0
    loss_factor: float = 0.0
    distance_km: float = 0.0
    cost_per_mw_km: float = 0.0
    reactance_pu: float = 0.01   # series reactance (p.u.) for DC-OPF PWL losses
    resistance_pu: float = 0.001  # series resistance (p.u.) for DC-OPF PWL losses
    fuel: str = ""            # only for fuel_route type
    waypoints: list[GeoPoint] = field(default_factory=list)
    style: VisualStyle = field(default_factory=lambda: VisualStyle(
        color="#8e44ad", width=3.0,
    ))
    from_endpoint: Optional[EndpointRef] = None
    to_endpoint: Optional[EndpointRef] = None
    # Electrical metadata for visual parity with GuiTransmissionLine.
    # These do NOT change the solver behaviour (the merged config
    # forwards distance_km/capacity/reactance_pu/resistance_pu only) —
    # they live here so the properties form can offer the same
    # editing experience as the intra-system line form.
    voltage_kv: Optional[float] = None
    line_type: Optional[str] = None     # "overhead", "underground", "submarine"
    length_km: Optional[float] = None   # alias of distance_km when shown to user
    base_impedance: Optional[float] = None
    reactance_per_km: Optional[float] = None
    susceptance_pu: Optional[float] = None
    num_circuits: int = 1
    frequency_hz: float = 50.0
    current_type: str = "AC"            # "AC" or "DC"
    decorative: bool = False


# ── Investment Portfolio ──────────────────────────────────────────


@dataclass
class GuiInvestmentNodeData:
    """Investment parameters for one technology at one node."""

    node_index: int
    invest_cost: float = 0.0      # $/MW (or $/MWh for battery energy)
    invest_max: float = 0.0       # MW (or MWh for battery energy capacity)


@dataclass
class GuiTechnology:
    """A technology definition that can be associated with generators,
    batteries, electrolyzers, and the investment portfolio."""

    tech_id: str                    # e.g. "tech_0"
    name: str = "New Technology"
    category: str = "Renewable"     # Renewable, Non-renewable, Storage, Electrolyzer
    fuel: str = ""
    life_time: int = 25
    degradation_rate: float = 0.0
    eff_at_rated: float = 0.35
    eff_at_min: float = 0.25
    invest_cost: float = 0.0       # $/MW
    invest_max_power: float = 0.0  # MW
    # Storage-specific (only when category=Storage)
    invest_cost_energy: float = 0.0   # $/MWh
    invest_max_capacity: float = 0.0  # MWh
    style: VisualStyle = field(default_factory=VisualStyle)


@dataclass
class GuiInvestmentEntry:
    """An investable technology in the portfolio."""

    entry_id: str                 # e.g. "inv_0", "inv_1"
    name: str                     # Display name, e.g. "Solar PV"
    technology_type: str           # "generator", "battery", "electrolyzer",
                                   # "acdc_converter", "freq_converter",
                                   # "transmission", "fuel_storage"
    target_key: str = ""           # Links to existing unit_key or element_id
    technology_id: str = ""         # References GuiTechnology.tech_id
    # Per-node investment data
    node_data: list[GuiInvestmentNodeData] = field(default_factory=list)
    # Battery-specific extra columns
    invest_cost_energy: dict[int, float] = field(default_factory=dict)  # node_idx → $/MWh
    invest_max_capacity: dict[int, float] = field(default_factory=dict)  # node_idx → MWh


# ── System state ─────────────────────────────────────────────────


@dataclass
class GuiSystemState:
    """Complete mutable GUI state for one power system."""

    name: str = ""
    map_center: Optional[GeoPoint] = None
    map_zoom: int = 2
    nodes: list[GuiNode] = field(default_factory=list)
    buses: dict[str, GuiBus] = field(default_factory=dict)   # bus_id → GuiBus
    generators: dict[str, GuiGeneratorInstance] = field(default_factory=dict)
    batteries: dict[str, GuiBatteryInstance] = field(default_factory=dict)
    transmission_lines: list[GuiTransmissionLine] = field(default_factory=list)
    transformers: list[GuiTransformer] = field(default_factory=list)
    acdc_converters: list[GuiACDCConverter] = field(default_factory=list)
    freq_converters: list[GuiFrequencyConverter] = field(default_factory=list)
    development_zones: list[GuiDevelopmentZone] = field(default_factory=list)
    fuel_entry_points: list[GuiFuelEntryPoint] = field(default_factory=list)
    fuel_sources: dict[str, GuiFuelSource] = field(default_factory=dict)
    fuel_storages: dict[str, GuiFuelStorage] = field(default_factory=dict)
    fuel_transport_routes: list[GuiFuelTransportRoute] = field(default_factory=list)
    demand_path: Optional[str] = None
    demand_paths: Optional[list[str]] = None
    investment_portfolio: dict[str, GuiInvestmentEntry] = field(default_factory=dict)
    technologies: dict[str, GuiTechnology] = field(default_factory=dict)
    _next_line_id: int = 0
    _next_tech_id: int = 0
    _next_fuel_route_id: int = 0
    _next_bus_id: int = 0
    _next_investment_id: int = 0
    # New subsystems
    fuels: dict[str, GuiFuel] = field(default_factory=dict)
    settings: GuiSystemSettings = field(default_factory=GuiSystemSettings)
    penalties: GuiPenalties = field(default_factory=GuiPenalties)
    power_flow_mode: str = "dcopf"
    dc_power_flow: GuiDCPowerFlow = field(default_factory=GuiDCPowerFlow)
    ac_power_flow: GuiACPowerFlow = field(default_factory=GuiACPowerFlow)
    electrolyzers: dict[str, GuiElectrolyzerInstance] = field(default_factory=dict)
    ev_config: GuiEVConfig = field(default_factory=GuiEVConfig)
    rooftop_solar: Optional[GuiRooftopSolar] = None
    demand_sectors: dict[str, GuiDemandSector] = field(default_factory=dict)
    non_electric_demand: dict[str, GuiNonElectricDemand] = field(default_factory=dict)
    sector_distribution: dict[int, dict[str, float]] = field(default_factory=dict)

    # Raw passthrough for fields not edited in the GUI
    raw_extras: dict = field(default_factory=dict)


# ── Checkpoint suspension helper ─────────────────────────────────


class _CheckpointSuspender:
    """Context manager that suppresses undo checkpoints during bulk ops."""

    def __init__(self, model: GuiModel):
        self._model = model

    def __enter__(self):
        self._model._undo_suspended = True
        return self

    def __exit__(self, *exc):
        self._model._undo_suspended = False
        # Take a single checkpoint after the bulk operation
        self._model._last_checkpoint = 0  # bypass debounce
        self._model.checkpoint()
        return False


# ── Signal-emitting model ────────────────────────────────────────


class GuiModel(QObject):
    """Central model that owns the state and emits change signals.

    All mutations should go through this class so that the map,
    tree, and properties panels stay synchronised.
    """

    # Node signals
    nodeAdded = Signal(int)           # node index
    nodeRemoved = Signal(int)
    nodeUpdated = Signal(int)

    # Bus signals
    busAdded = Signal(str)            # bus_id
    busRemoved = Signal(str)
    busUpdated = Signal(str)

    # Generator / battery signals (emit instance_id)
    generatorAdded = Signal(str)
    generatorRemoved = Signal(str)
    generatorUpdated = Signal(str)
    batteryAdded = Signal(str)
    batteryRemoved = Signal(str)
    batteryUpdated = Signal(str)

    # Line signals
    lineAdded = Signal(str)           # line_id "from-to"
    lineRemoved = Signal(str)
    lineUpdated = Signal(str)

    # Zone signals
    zoneAdded = Signal(int)           # index
    zoneRemoved = Signal(int)

    # Fuel entry signals
    fuelEntryAdded = Signal(int)
    fuelEntryRemoved = Signal(int)

    # Transformer signals
    transformerAdded = Signal(int)
    transformerRemoved = Signal(int)

    # AC/DC converter signals
    acdcConverterAdded = Signal(int)
    acdcConverterRemoved = Signal(int)

    # Frequency converter signals
    freqConverterAdded = Signal(int)
    freqConverterRemoved = Signal(int)

    # Fuel source signals
    fuelSourceAdded = Signal(str)
    fuelSourceRemoved = Signal(str)
    fuelSourceUpdated = Signal(str)

    # Fuel storage signals
    fuelStorageAdded = Signal(str)
    fuelStorageRemoved = Signal(str)
    fuelStorageUpdated = Signal(str)

    # Fuel route signals
    fuelRouteAdded = Signal(str)
    fuelRouteRemoved = Signal(str)
    fuelRouteUpdated = Signal(str)

    # Inter-system link signals
    interSystemLinkAdded = Signal(str)
    interSystemLinkRemoved = Signal(str)
    interSystemLinkUpdated = Signal(str)

    # Fuel signals (FuelConfig, not FuelSource)
    fuelAdded = Signal(str)
    fuelRemoved = Signal(str)
    fuelUpdated = Signal(str)

    # Electrolyzer signals
    electrolyzerAdded = Signal(str)
    electrolyzerRemoved = Signal(str)
    electrolyzerUpdated = Signal(str)

    # Technology signals
    technologyAdded = Signal(str)        # tech_id
    technologyRemoved = Signal(str)
    technologyUpdated = Signal(str)

    # Investment portfolio signals
    investmentEntryAdded = Signal(str)    # entry_id
    investmentEntryRemoved = Signal(str)
    investmentEntryUpdated = Signal(str)

    # Settings signals
    systemSettingsUpdated = Signal()
    globalSettingsUpdated = Signal()

    # Selection
    selectionChanged = Signal(str, str)  # element_type, element_id

    # Global
    stateLoaded = Signal()            # fired after bulk load from config
    undoChanged = Signal()            # fired when undo/redo availability changes
    # Fired when the underlying state mutated by a user action (edit,
    # undo, redo) — NOT when it changed via bulk load or clear_undo.
    # Use this to drive an unsaved-changes indicator and prompt-on-close.
    dataMutated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = GuiSystemState()
        self.global_settings = GuiGlobalSettings()
        self.stochastic_scenarios: list[GuiStochasticScenario] = []
        self._inter_system_links: list[GuiInterSystemLink] = []
        self._next_islink_id: int = 0
        from esfex.visualization.preferences import load_preferences, get_preference
        _prefs = load_preferences()
        _undo_depth = get_preference(_prefs, "general", "undo_depth", 50)
        self._undo_stack = UndoStack(max_depth=_undo_depth)
        self._undo_suspended: bool = False
        self._last_checkpoint: float = 0.0
        self._bulk_depth: int = 0
        self._bulk_dirty: bool = False

    # ------------------------------------------------------------------
    # Bulk update (suppresses stateLoaded until end)
    # ------------------------------------------------------------------

    def begin_bulk_update(self):
        """Begin a bulk operation — checkpoints and stateLoaded are deferred."""
        if self._bulk_depth == 0:
            # Take one checkpoint before the batch (respects debounce)
            # and suspend further checkpoints during the batch.
            self._undo_suspended = False
            self.checkpoint()
            self._undo_suspended = True
        self._bulk_depth += 1

    def end_bulk_update(self):
        """End a bulk operation — always emit stateLoaded for a clean rebuild.

        Granular signals during the batch provide incremental updates, but
        index-based elements (transformers, fuel entries, converters) can
        have stale indices after cascaded deletions.  The final stateLoaded
        triggers a full clear + rebuild that guarantees no orphans remain.
        """
        self._bulk_depth = max(0, self._bulk_depth - 1)
        if self._bulk_depth == 0:
            self._undo_suspended = False
            self._bulk_dirty = False
            self.stateLoaded.emit()

    def _emit_state_loaded(self):
        """Emit stateLoaded, or defer if inside a bulk update."""
        if self._bulk_depth > 0:
            self._bulk_dirty = True
        else:
            self.stateLoaded.emit()

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    def checkpoint(self):
        """Save a snapshot of the current state for undo.

        Uses time-based debouncing (300 ms) so that rapid spinbox
        changes are grouped into a single undo step.  Structural
        mutations in model methods bypass the debounce via
        ``checkpoint()``, which is acceptable because they happen
        at most once per user click.
        """
        if self._undo_suspended:
            return
        import time
        now = time.monotonic()
        if now - self._last_checkpoint < 0.3:
            return
        self._last_checkpoint = now
        self._undo_stack.push(self.state)
        self.undoChanged.emit()
        self.dataMutated.emit()

    def undo(self):
        """Restore the previous state. Returns True if successful."""
        restored = self._undo_stack.undo(self.state)
        if restored is None:
            return False
        self._undo_suspended = True
        self.load_state(restored)
        self._undo_suspended = False
        self.undoChanged.emit()
        self.dataMutated.emit()
        return True

    def redo(self):
        """Re-apply the last undone state. Returns True if successful."""
        restored = self._undo_stack.redo(self.state)
        if restored is None:
            return False
        self._undo_suspended = True
        self.load_state(restored)
        self._undo_suspended = False
        self.undoChanged.emit()
        self.dataMutated.emit()
        return True

    @property
    def can_undo(self) -> bool:
        return self._undo_stack.can_undo

    @property
    def can_redo(self) -> bool:
        return self._undo_stack.can_redo

    def clear_undo(self):
        """Clear the undo/redo history."""
        from esfex.visualization.preferences import load_preferences, get_preference
        _prefs = load_preferences()
        _undo_depth = get_preference(_prefs, "general", "undo_depth", 50)
        self._undo_stack = UndoStack(max_depth=_undo_depth)
        self.undoChanged.emit()

    def suspend_checkpoints(self):
        """Context manager to suppress undo checkpoints during bulk operations.

        Usage::

            with model.suspend_checkpoints():
                model.add_fuel(...)
                model.add_generator_instance(...)
            # A single checkpoint is taken on exit
        """
        return _CheckpointSuspender(self)

    @property
    def inter_system_links(self) -> list[GuiInterSystemLink]:
        return self._inter_system_links

    # ------------------------------------------------------------------
    # Inter-system link operations
    # ------------------------------------------------------------------

    def add_inter_system_link(
        self,
        link_type: str,
        from_system: str,
        to_system: str,
        from_node: int,
        to_node: int,
        link_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        self.checkpoint()
        if link_id is None:
            link_id = f"islink_{self._next_islink_id}"
            self._next_islink_id += 1
        link = GuiInterSystemLink(
            link_id=link_id,
            link_type=link_type,
            from_system=from_system,
            to_system=to_system,
            from_node=from_node,
            to_node=to_node,
        )
        for k, v in kwargs.items():
            if hasattr(link, k):
                setattr(link, k, v)
        self._inter_system_links.append(link)
        # Track ID counter
        if link_id.startswith("islink_"):
            try:
                num = int(link_id[7:])
                if num >= self._next_islink_id:
                    self._next_islink_id = num + 1
            except ValueError:
                pass
        self.interSystemLinkAdded.emit(link_id)
        return link_id

    def remove_inter_system_link(self, link_id: str):
        self.checkpoint()
        before = len(self._inter_system_links)
        self._inter_system_links = [
            lk for lk in self._inter_system_links if lk.link_id != link_id
        ]
        if len(self._inter_system_links) < before:
            self.interSystemLinkRemoved.emit(link_id)

    def update_inter_system_link(self, link_id: str, **kwargs):
        self.checkpoint()
        for lk in self._inter_system_links:
            if lk.link_id == link_id:
                for k, v in kwargs.items():
                    if hasattr(lk, k):
                        setattr(lk, k, v)
                self.interSystemLinkUpdated.emit(link_id)
                return

    def clear_inter_system_links(self):
        self._inter_system_links.clear()
        self._next_islink_id = 0

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(self, name: str = "") -> int:
        self.checkpoint()
        idx = len(self.state.nodes)
        if not name:
            name = f"Node {idx}"
        node = GuiNode(index=idx, name=name)
        self.state.nodes.append(node)
        self.nodeAdded.emit(idx)
        return idx

    def remove_node(self, index: int):
        self.checkpoint()
        if 0 <= index < len(self.state.nodes):
            self.state.nodes.pop(index)
            # Reindex remaining nodes
            for i, n in enumerate(self.state.nodes):
                n.index = i

            # Collect bus_ids belonging to this node
            buses_at_node = {
                bid for bid, bus in self.state.buses.items()
                if bus.parent_node == index
            }

            # Remove generators/batteries/electrolyzers at deleted buses
            # (granular signals for O(1) map/tree removal each)
            for k in [k for k, g in self.state.generators.items()
                       if g.bus in buses_at_node]:
                del self.state.generators[k]
                self.generatorRemoved.emit(k)
            for k in [k for k, b in self.state.batteries.items()
                       if b.bus in buses_at_node]:
                del self.state.batteries[k]
                self.batteryRemoved.emit(k)
            for k in [k for k, e in self.state.electrolyzers.items()
                       if e.bus in buses_at_node]:
                del self.state.electrolyzers[k]
                self.electrolyzerRemoved.emit(k)

            # Remove lines referencing deleted buses
            removed_lines = [ln.line_id for ln in self.state.transmission_lines
                             if ln.from_bus in buses_at_node
                             or ln.to_bus in buses_at_node]
            if removed_lines:
                self.state.transmission_lines = [
                    ln for ln in self.state.transmission_lines
                    if ln.from_bus not in buses_at_node
                    and ln.to_bus not in buses_at_node
                ]
                for lid in removed_lines:
                    self.lineRemoved.emit(lid)
            # Update endpoint refs pointing to nodes
            for ln in self.state.transmission_lines:
                if ln.from_endpoint and ln.from_endpoint.element_type == "node":
                    ep_idx = int(ln.from_endpoint.element_id)
                    if ep_idx > index:
                        ln.from_endpoint.element_id = str(ep_idx - 1)
                if ln.to_endpoint and ln.to_endpoint.element_type == "node":
                    ep_idx = int(ln.to_endpoint.element_id)
                    if ep_idx > index:
                        ln.to_endpoint.element_id = str(ep_idx - 1)

            # Remove transformers referencing deleted buses (reverse index)
            tr_indices = sorted(
                [i for i, tr in enumerate(self.state.transformers)
                 if tr.from_bus in buses_at_node or tr.to_bus in buses_at_node],
                reverse=True,
            )
            for idx in tr_indices:
                self.state.transformers.pop(idx)
                self.transformerRemoved.emit(idx)
            # Remove AC/DC converters (reverse index)
            acdc_indices = sorted(
                [i for i, c in enumerate(self.state.acdc_converters)
                 if c.from_bus in buses_at_node or c.to_bus in buses_at_node],
                reverse=True,
            )
            for idx in acdc_indices:
                self.state.acdc_converters.pop(idx)
                self.acdcConverterRemoved.emit(idx)
            # Remove frequency converters (reverse index)
            freq_indices = sorted(
                [i for i, c in enumerate(self.state.freq_converters)
                 if c.from_bus in buses_at_node or c.to_bus in buses_at_node],
                reverse=True,
            )
            for idx in freq_indices:
                self.state.freq_converters.pop(idx)
                self.freqConverterRemoved.emit(idx)

            # Remove fuel entries at this node (reverse index)
            fe_indices = sorted(
                [i for i, fe in enumerate(self.state.fuel_entry_points)
                 if fe.node == index],
                reverse=True,
            )
            for idx in fe_indices:
                self.state.fuel_entry_points.pop(idx)
                self.fuelEntryRemoved.emit(idx)
            for fe in self.state.fuel_entry_points:
                if fe.node > index:
                    fe.node -= 1

            # Remove fuel transport routes referencing this node
            removed_routes = [rt.route_id for rt in self.state.fuel_transport_routes
                              if rt.from_node == index or rt.to_node == index]
            if removed_routes:
                self.state.fuel_transport_routes = [
                    rt for rt in self.state.fuel_transport_routes
                    if rt.from_node != index and rt.to_node != index
                ]
                for rid in removed_routes:
                    self.fuelRouteRemoved.emit(rid)
            for rt in self.state.fuel_transport_routes:
                if rt.from_node > index:
                    rt.from_node -= 1
                if rt.to_node > index:
                    rt.to_node -= 1
                if rt.from_endpoint and rt.from_endpoint.element_type == "node":
                    ep_idx = int(rt.from_endpoint.element_id)
                    if ep_idx > index:
                        rt.from_endpoint.element_id = str(ep_idx - 1)
                if rt.to_endpoint and rt.to_endpoint.element_type == "node":
                    ep_idx = int(rt.to_endpoint.element_id)
                    if ep_idx > index:
                        rt.to_endpoint.element_id = str(ep_idx - 1)

            # Remove buses at this node (after cascaded equipment)
            for bid in buses_at_node:
                del self.state.buses[bid]
                self.busRemoved.emit(bid)

            # Reindex bus parent_node for nodes > index
            for bus in self.state.buses.values():
                if bus.parent_node > index:
                    bus.parent_node -= 1

            self.nodeRemoved.emit(index)

    def update_node(self, index: int, **kwargs):
        self.checkpoint()
        if 0 <= index < len(self.state.nodes):
            node = self.state.nodes[index]
            for k, v in kwargs.items():
                if hasattr(node, k):
                    setattr(node, k, v)
            self.nodeUpdated.emit(index)

    def get_node(self, index: int) -> Optional[GuiNode]:
        if 0 <= index < len(self.state.nodes):
            return self.state.nodes[index]
        return None

    # ------------------------------------------------------------------
    # Generator operations (instance-based)
    # ------------------------------------------------------------------

    def add_generator_instance(
        self,
        unit_key: str,
        name: str,
        gen_type: str,
        fuel: str,
        node: int = 0,
        bus: Optional[str] = None,
        **params,
    ) -> str:
        """Create a generator instance at a specific bus."""
        self.checkpoint()
        if bus is None:
            bus = self._default_bus_for_node(node)
        instance_id = self._make_instance_id(unit_key, bus, self.state.generators)
        inst = GuiGeneratorInstance(
            instance_id=instance_id,
            unit_key=unit_key,
            name=name,
            gen_type=gen_type,
            fuel=fuel,
            bus=bus,
            node=self._node_for_bus(bus),
        )
        for k, v in params.items():
            if hasattr(inst, k):
                setattr(inst, k, v)
        self.state.generators[instance_id] = inst
        self.generatorAdded.emit(instance_id)
        return instance_id

    def remove_generator(self, instance_id: str):
        self.checkpoint()
        if instance_id in self.state.generators:
            del self.state.generators[instance_id]
            self.generatorRemoved.emit(instance_id)

    def update_generator(self, instance_id: str, **kwargs):
        self.checkpoint()
        if instance_id in self.state.generators:
            inst = self.state.generators[instance_id]
            for k, v in kwargs.items():
                if hasattr(inst, k):
                    setattr(inst, k, v)
            self.generatorUpdated.emit(instance_id)

    # ------------------------------------------------------------------
    # Battery operations (instance-based)
    # ------------------------------------------------------------------

    def add_battery_instance(
        self,
        unit_key: str,
        name: str,
        node: int = 0,
        bus: Optional[str] = None,
        **params,
    ) -> str:
        """Create a battery instance at a specific bus."""
        self.checkpoint()
        if bus is None:
            bus = self._default_bus_for_node(node)
        instance_id = self._make_instance_id(unit_key, bus, self.state.batteries)
        inst = GuiBatteryInstance(
            instance_id=instance_id,
            unit_key=unit_key,
            name=name,
            bus=bus,
            node=self._node_for_bus(bus),
        )
        for k, v in params.items():
            if hasattr(inst, k):
                setattr(inst, k, v)
        self.state.batteries[instance_id] = inst
        self.batteryAdded.emit(instance_id)
        return instance_id

    def remove_battery(self, instance_id: str):
        self.checkpoint()
        if instance_id in self.state.batteries:
            del self.state.batteries[instance_id]
            self.batteryRemoved.emit(instance_id)

    def update_battery(self, instance_id: str, **kwargs):
        self.checkpoint()
        if instance_id in self.state.batteries:
            inst = self.state.batteries[instance_id]
            for k, v in kwargs.items():
                if hasattr(inst, k):
                    setattr(inst, k, v)
            self.batteryUpdated.emit(instance_id)

    # ------------------------------------------------------------------
    # Line operations
    # ------------------------------------------------------------------

    def add_line(
        self,
        from_node: int = 0,
        to_node: int = 0,
        capacity_mw: float = 100.0,
        line_id: Optional[str] = None,
        waypoints: Optional[list[GeoPoint]] = None,
        from_endpoint: Optional[EndpointRef] = None,
        to_endpoint: Optional[EndpointRef] = None,
        from_bus: Optional[str] = None,
        to_bus: Optional[str] = None,
    ) -> str:
        if line_id is None:
            line_id = f"line_{self.state._next_line_id}"
            self.state._next_line_id += 1
        # Resolve buses from endpoint refs first (exact bus), then fallback
        self.checkpoint()
        if from_bus is None and from_endpoint is not None:
            from_bus = self.resolve_endpoint_bus(from_endpoint)
        if from_bus is None:
            from_bus = self._default_bus_for_node(from_node)
        if to_bus is None and to_endpoint is not None:
            to_bus = self.resolve_endpoint_bus(to_endpoint)
        if to_bus is None:
            to_bus = self._default_bus_for_node(to_node)
        if from_endpoint is None:
            from_endpoint = EndpointRef("node", str(self._node_for_bus(from_bus)))
        if to_endpoint is None:
            to_endpoint = EndpointRef("node", str(self._node_for_bus(to_bus)))
        line = GuiTransmissionLine(
            line_id=line_id,
            from_bus=from_bus,
            to_bus=to_bus,
            from_node=self._node_for_bus(from_bus),
            to_node=self._node_for_bus(to_bus),
            capacity_mw=capacity_mw,
            waypoints=waypoints or [],
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
        )
        self.state.transmission_lines.append(line)
        self.lineAdded.emit(line.line_id)
        return line.line_id

    def remove_line(self, line_id: str):
        self.checkpoint()
        before = len(self.state.transmission_lines)
        self.state.transmission_lines = [
            ln for ln in self.state.transmission_lines
            if ln.line_id != line_id
        ]
        if len(self.state.transmission_lines) < before:
            self.lineRemoved.emit(line_id)

    def update_line(self, line_id: str, **kwargs):
        self.checkpoint()
        for ln in self.state.transmission_lines:
            if ln.line_id == line_id:
                for k, v in kwargs.items():
                    if hasattr(ln, k):
                        setattr(ln, k, v)
                self.lineUpdated.emit(line_id)
                return

    def resolve_endpoint_node(self, ref: EndpointRef, state=None) -> Optional[int]:
        """Resolve an EndpointRef to a node index within ``state``.

        ``state`` defaults to the currently-active system state. Pass an
        explicit ``GuiSystemState`` to resolve against a different system
        (used by main_window when drawing inter-system links: each
        endpoint may live in a different ``_all_states`` entry).
        """
        st = state if state is not None else self.state
        etype, eid = ref.element_type, ref.element_id
        if etype == "node":
            idx = int(eid)
            if 0 <= idx < len(st.nodes):
                return idx
            return None
        if etype == "bus":
            bus = st.buses.get(eid)
            return bus.parent_node if bus else None

        def _node_for_bus_in(bus_id: str) -> int:
            bus = st.buses.get(bus_id)
            return bus.parent_node if bus else 0

        if etype == "generator":
            inst = st.generators.get(eid)
            return _node_for_bus_in(inst.bus) if inst else None
        if etype == "battery":
            inst = st.batteries.get(eid)
            return _node_for_bus_in(inst.bus) if inst else None
        if etype == "electrolyzer":
            inst = st.electrolyzers.get(eid)
            return _node_for_bus_in(inst.bus) if inst else None
        if etype == "transformer":
            # eid is the list index (str), not the name
            try:
                idx = int(eid)
                if idx < len(st.transformers):
                    return _node_for_bus_in(st.transformers[idx].from_bus)
            except (ValueError, IndexError):
                pass
            # Fallback: match by name
            for tr in st.transformers:
                if tr.name == eid:
                    return _node_for_bus_in(tr.from_bus)
            return None
        if etype == "acdc_converter":
            try:
                idx = int(eid)
                if idx < len(st.acdc_converters):
                    return _node_for_bus_in(st.acdc_converters[idx].from_bus)
            except (ValueError, IndexError):
                pass
            return None
        if etype == "freq_converter":
            try:
                idx = int(eid)
                if idx < len(st.freq_converters):
                    return _node_for_bus_in(st.freq_converters[idx].from_bus)
            except (ValueError, IndexError):
                pass
            return None
        if etype == "fuel_entry":
            try:
                idx = int(eid)
                if idx < len(st.fuel_entry_points):
                    return st.fuel_entry_points[idx].node
            except (ValueError, IndexError):
                pass
            for i, fe in enumerate(st.fuel_entry_points):
                if fe.name == eid:
                    return fe.node
            return None
        if etype == "fuel_storage":
            inst = st.fuel_storages.get(eid)
            return inst.node if inst else None
        return None

    def resolve_endpoint_bus(self, ref: EndpointRef) -> Optional[str]:
        """Resolve an EndpointRef to its specific bus_id.

        Unlike ``_default_bus_for_node`` this returns the *exact* bus that
        the endpoint element is connected to, avoiding wrong-voltage
        inheritance when a node hosts multiple buses at different voltages.
        """
        etype, eid = ref.element_type, ref.element_id
        if etype == "bus":
            return eid if eid in self.state.buses else None
        if etype == "generator":
            inst = self.state.generators.get(eid)
            return inst.bus if inst and inst.bus in self.state.buses else None
        if etype == "battery":
            inst = self.state.batteries.get(eid)
            return inst.bus if inst and inst.bus in self.state.buses else None
        if etype == "electrolyzer":
            inst = self.state.electrolyzers.get(eid)
            return inst.bus if inst and inst.bus in self.state.buses else None
        if etype == "transformer":
            try:
                idx = int(eid)
                if idx < len(self.state.transformers):
                    return self.state.transformers[idx].from_bus
            except (ValueError, IndexError):
                pass
            for tr in self.state.transformers:
                if tr.name == eid:
                    return tr.from_bus
            return None
        if etype == "acdc_converter":
            try:
                idx = int(eid)
                if idx < len(self.state.acdc_converters):
                    return self.state.acdc_converters[idx].from_bus
            except (ValueError, IndexError):
                pass
            return None
        if etype == "freq_converter":
            try:
                idx = int(eid)
                if idx < len(self.state.freq_converters):
                    return self.state.freq_converters[idx].from_bus
            except (ValueError, IndexError):
                pass
            return None
        if etype == "node":
            try:
                node_idx = int(eid)
            except ValueError:
                return None
            return self._default_bus_for_node(node_idx)
        return None

    def get_connected_elements(
        self, element_type: str, element_id: str
    ) -> dict[str, list[tuple[str, str, str]]]:
        """Return elements connected to a given element via transmission lines.

        Args:
            element_type: Type of element (e.g., "transformer", "acdc_converter")
            element_id: ID of element (index as string for indexed elements)

        Returns:
            Dictionary with "from" and "to" keys, each containing list of tuples:
            [(connected_element_type, connected_element_id, line_id), ...]

        Example:
            >>> connections = model.get_connected_elements("transformer", "0")
            >>> connections["from"]
            [("generator", "unit_1_bus_0", "line_0"), ("bus", "bus_1", "line_3")]
        """
        connections: dict[str, list[tuple[str, str, str]]] = {"from": [], "to": []}

        for ln in self.state.transmission_lines:
            # Check if line originates from this element
            if (
                ln.from_endpoint
                and ln.from_endpoint.element_type == element_type
                and ln.from_endpoint.element_id == element_id
            ):
                if ln.to_endpoint:
                    connections["from"].append(
                        (
                            ln.to_endpoint.element_type,
                            ln.to_endpoint.element_id,
                            ln.line_id,
                        )
                    )

            # Check if line terminates at this element
            if (
                ln.to_endpoint
                and ln.to_endpoint.element_type == element_type
                and ln.to_endpoint.element_id == element_id
            ):
                if ln.from_endpoint:
                    connections["to"].append(
                        (
                            ln.from_endpoint.element_type,
                            ln.from_endpoint.element_id,
                            ln.line_id,
                        )
                    )

        return connections

    def format_connected_element(self, elem_type: str, elem_id: str) -> str:
        """Format an element reference as human-readable string.

        Args:
            elem_type: Element type (e.g., "generator", "bus", "transformer")
            elem_id: Element ID (instance_id for dict-stored, index for list-stored)

        Returns:
            Formatted string like "Generator: Coal Plant #1" or "Bus: Main Bus"

        Example:
            >>> model.format_connected_element("generator", "unit_1_bus_0")
            "Generator: Coal Plant #1"
            >>> model.format_connected_element("bus", "bus_0")
            "Bus: Main Bus"
        """
        if elem_type == "node":
            try:
                idx = int(elem_id)
                if idx < len(self.state.nodes):
                    return f"Node: {self.state.nodes[idx].name}"
            except ValueError:
                pass
            return f"Node {elem_id}"

        if elem_type == "bus":
            bus = self.state.buses.get(elem_id)
            return f"Bus: {bus.name}" if bus else f"Bus {elem_id}"

        if elem_type == "generator":
            gen = self.state.generators.get(elem_id)
            return f"Generator: {gen.name}" if gen else f"Generator {elem_id}"

        if elem_type == "battery":
            bat = self.state.batteries.get(elem_id)
            return f"Battery: {bat.name}" if bat else f"Battery {elem_id}"

        if elem_type == "electrolyzer":
            elz = self.state.electrolyzers.get(elem_id)
            return f"Electrolyzer: {elz.name}" if elz else f"Electrolyzer {elem_id}"

        if elem_type == "transformer":
            try:
                idx = int(elem_id)
                if idx < len(self.state.transformers):
                    return f"Transformer: {self.state.transformers[idx].name}"
            except ValueError:
                pass
            return f"Transformer {elem_id}"

        if elem_type == "acdc_converter":
            try:
                idx = int(elem_id)
                if idx < len(self.state.acdc_converters):
                    return f"AC/DC Converter: {self.state.acdc_converters[idx].name}"
            except ValueError:
                pass
            return f"AC/DC Converter {elem_id}"

        if elem_type == "freq_converter":
            try:
                idx = int(elem_id)
                if idx < len(self.state.freq_converters):
                    return f"Freq. Converter: {self.state.freq_converters[idx].name}"
            except ValueError:
                pass
            return f"Freq. Converter {elem_id}"

        if elem_type == "fuel_entry":
            try:
                idx = int(elem_id)
                if idx < len(self.state.fuel_entry_points):
                    return f"Fuel Entry: {self.state.fuel_entry_points[idx].name}"
            except ValueError:
                pass
            return f"Fuel Entry {elem_id}"

        if elem_type == "fuel_storage":
            fs = self.state.fuel_storages.get(elem_id)
            return f"Fuel Storage: {fs.name}" if fs else f"Fuel Storage {elem_id}"

        return f"{elem_type.capitalize()} {elem_id}"

    def resolve_element_voltage(self, element_type: str, element_id: str) -> float | None:
        """Return the voltage (kV) of a connected element, or None."""
        if element_type == "bus":
            bus = self.state.buses.get(element_id)
            return bus.voltage_kv if bus else None

        if element_type == "generator":
            gen = self.state.generators.get(element_id)
            if gen:
                bus = self.state.buses.get(gen.bus)
                if bus:
                    return bus.voltage_kv
            return None

        if element_type == "battery":
            bat = self.state.batteries.get(element_id)
            if bat:
                bus = self.state.buses.get(bat.bus)
                if bus:
                    return bus.voltage_kv
            return None

        if element_type == "electrolyzer":
            elz = self.state.electrolyzers.get(element_id)
            if elz:
                bus = self.state.buses.get(elz.bus)
                if bus:
                    return bus.voltage_kv
            return None

        if element_type == "node":
            try:
                node_idx = int(element_id)
            except ValueError:
                return None
            # Node may have multiple buses; return None to avoid ambiguity
            buses = [b for b in self.state.buses.values()
                     if b.parent_node == node_idx]
            if len(buses) == 1:
                return buses[0].voltage_kv
            return None

        if element_type == "transformer":
            try:
                idx = int(element_id)
                if idx < len(self.state.transformers):
                    t = self.state.transformers[idx]
                    return max(t.from_voltage_kv, t.to_voltage_kv)
            except (ValueError, IndexError):
                pass
            return None

        return None

    def resolve_transformer_side_voltages(
        self, transformer_idx: int
    ) -> tuple[float | None, float | None]:
        """Resolve voltages on each side of a transformer from connected elements.

        Returns (from_side_kv, to_side_kv).
        """
        connections = self.get_connected_elements("transformer", str(transformer_idx))

        def _max_voltage(side_connections: list) -> float | None:
            voltages = []
            for et, eid, _lid in side_connections:
                v = self.resolve_element_voltage(et, eid)
                if v is not None:
                    voltages.append(v)
            return max(voltages) if voltages else None

        from_kv = _max_voltage(connections["from"])
        to_kv = _max_voltage(connections["to"])
        return from_kv, to_kv

    # ------------------------------------------------------------------
    # Electrical property propagation
    # ------------------------------------------------------------------

    def propagate_bus_properties(self, bus_id: str) -> None:
        """Propagate electrical properties from a bus to all connected equipment.

        Bus is the source of truth for voltage_kv, frequency_hz, current_type.
        """
        bus = self.state.buses.get(bus_id)
        if not bus:
            return

        # Generators connected to this bus
        for gen in self.state.generators.values():
            if gen.bus == bus_id:
                gen.frequency_hz = bus.frequency_hz
                gen.current_type = bus.current_type

        # Batteries connected to this bus
        for bat in self.state.batteries.values():
            if bat.bus == bus_id:
                bat.current_type = bus.current_type

        # Transmission lines referencing this bus
        for line in self.state.transmission_lines:
            if line.from_bus == bus_id or line.to_bus == bus_id:
                self._propagate_line_properties(line)

        # Transformers: from_bus or to_bus → voltage
        for tr in self.state.transformers:
            if tr.from_bus == bus_id:
                tr.from_voltage_kv = bus.voltage_kv
            if tr.to_bus == bus_id:
                tr.to_voltage_kv = bus.voltage_kv

        # AC/DC converters: from_bus (AC side) or to_bus (DC side) → voltage
        for conv in self.state.acdc_converters:
            if conv.from_bus == bus_id:
                conv.from_voltage_kv = bus.voltage_kv
            if conv.to_bus == bus_id:
                conv.dc_voltage_kv = bus.voltage_kv

        # Frequency converters: from_bus or to_bus → frequency
        for fc in self.state.freq_converters:
            if fc.from_bus == bus_id:
                fc.from_frequency_hz = bus.frequency_hz
            if fc.to_bus == bus_id:
                fc.to_frequency_hz = bus.frequency_hz

    def _propagate_line_properties(self, line) -> None:
        """Propagate bus properties to a transmission line from its endpoint buses."""
        from_bus = self.state.buses.get(line.from_bus)
        to_bus = self.state.buses.get(line.to_bus)
        src = from_bus or to_bus
        if src:
            line.voltage_kv = src.voltage_kv
            line.frequency_hz = src.frequency_hz
            line.current_type = src.current_type

    def propagate_bus_to_element(self, element_type: str, element_id: str) -> None:
        """Update a single element's electrical properties from its assigned bus."""
        if element_type == "generator":
            gen = self.state.generators.get(element_id)
            if gen:
                bus = self.state.buses.get(gen.bus)
                if bus:
                    gen.frequency_hz = bus.frequency_hz
                    gen.current_type = bus.current_type
        elif element_type == "battery":
            bat = self.state.batteries.get(element_id)
            if bat:
                bus = self.state.buses.get(bat.bus)
                if bus:
                    bat.current_type = bus.current_type
        elif element_type == "transmission_line":
            for line in self.state.transmission_lines:
                if line.line_id == element_id:
                    self._propagate_line_properties(line)
                    break
        elif element_type == "transformer":
            try:
                idx = int(element_id)
                if idx < len(self.state.transformers):
                    tr = self.state.transformers[idx]
                    from_bus = self.state.buses.get(tr.from_bus)
                    to_bus = self.state.buses.get(tr.to_bus)
                    if from_bus:
                        tr.from_voltage_kv = from_bus.voltage_kv
                    if to_bus:
                        tr.to_voltage_kv = to_bus.voltage_kv
            except (ValueError, IndexError):
                pass
        elif element_type == "acdc_converter":
            try:
                idx = int(element_id)
                if idx < len(self.state.acdc_converters):
                    conv = self.state.acdc_converters[idx]
                    from_bus = self.state.buses.get(conv.from_bus)
                    to_bus = self.state.buses.get(conv.to_bus)
                    if from_bus:
                        conv.from_voltage_kv = from_bus.voltage_kv
                    if to_bus:
                        conv.dc_voltage_kv = to_bus.voltage_kv
            except (ValueError, IndexError):
                pass
        elif element_type == "freq_converter":
            try:
                idx = int(element_id)
                if idx < len(self.state.freq_converters):
                    fc = self.state.freq_converters[idx]
                    from_bus = self.state.buses.get(fc.from_bus)
                    to_bus = self.state.buses.get(fc.to_bus)
                    if from_bus:
                        fc.from_frequency_hz = from_bus.frequency_hz
                    if to_bus:
                        fc.to_frequency_hz = to_bus.frequency_hz
            except (ValueError, IndexError):
                pass

    # ------------------------------------------------------------------
    # Zone operations
    # ------------------------------------------------------------------

    def add_zone(self, name: str, technology: str, polygon: list[GeoPoint],
                 layer: str = "electrical", max_capacity_mw: float | None = None,
                 node: int | None = None) -> int:
        self.checkpoint()
        zone = GuiDevelopmentZone(
            name=name, technology=technology, layer=layer,
            node=node, polygon=polygon, max_capacity_mw=max_capacity_mw,
        )
        self.state.development_zones.append(zone)
        idx = len(self.state.development_zones) - 1
        self.zoneAdded.emit(idx)
        return idx

    def remove_zone(self, index: int):
        self.checkpoint()
        if 0 <= index < len(self.state.development_zones):
            self.state.development_zones.pop(index)
            self.zoneRemoved.emit(index)

    # ------------------------------------------------------------------
    # Fuel entry operations
    # ------------------------------------------------------------------

    def add_fuel_entry(self, name: str, fuels: list[str] | None = None,
                       node: int = 0,
                       lat: float = 0.0, lng: float = 0.0, **kwargs) -> int:
        self.checkpoint()
        entry = GuiFuelEntryPoint(
            name=name, fuels=fuels or [], node=node,
            coordinate=GeoPoint(lat, lng, name),
            **kwargs,
        )
        self.state.fuel_entry_points.append(entry)
        idx = len(self.state.fuel_entry_points) - 1
        self.fuelEntryAdded.emit(idx)
        return idx

    def remove_fuel_entry(self, index: int):
        """Remove a fuel entry point by its list index."""
        self.checkpoint()
        if 0 <= index < len(self.state.fuel_entry_points):
            self.state.fuel_entry_points.pop(index)
            self.fuelEntryRemoved.emit(index)

    # ------------------------------------------------------------------
    # Transformer operations
    # ------------------------------------------------------------------

    def add_transformer(
        self, name: str, from_node: int = 0, to_node: int = -1,
        from_bus: Optional[str] = None, to_bus: Optional[str] = None,
        **kwargs,
    ) -> int:
        self.checkpoint()
        if from_bus is None:
            from_bus = self._default_bus_for_node(from_node)
        if to_bus is None:
            if to_node < 0:
                to_bus = from_bus
            else:
                to_bus = self._default_bus_for_node(to_node)
        tr = GuiTransformer(
            name=name,
            from_bus=from_bus, to_bus=to_bus,
            from_node=self._node_for_bus(from_bus),
            to_node=self._node_for_bus(to_bus),
            **kwargs,
        )
        self.state.transformers.append(tr)
        idx = len(self.state.transformers) - 1
        self.transformerAdded.emit(idx)
        return idx

    def remove_transformer(self, index: int):
        """Remove a transformer by its list index."""
        self.checkpoint()
        if 0 <= index < len(self.state.transformers):
            self.state.transformers.pop(index)
            self.transformerRemoved.emit(index)

    # ------------------------------------------------------------------
    # AC/DC converter operations
    # ------------------------------------------------------------------

    def add_acdc_converter(
        self, name: str, from_node: int = 0, to_node: int = 0,
        from_bus: Optional[str] = None, to_bus: Optional[str] = None,
        **kwargs,
    ) -> int:
        self.checkpoint()
        if from_bus is None:
            from_bus = self._default_bus_for_node(from_node)
        if to_bus is None:
            to_bus = self._default_bus_for_node(to_node)
        conv = GuiACDCConverter(
            name=name,
            from_bus=from_bus, to_bus=to_bus,
            from_node=self._node_for_bus(from_bus),
            to_node=self._node_for_bus(to_bus),
        )
        for k, v in kwargs.items():
            if hasattr(conv, k):
                setattr(conv, k, v)
        self.state.acdc_converters.append(conv)
        idx = len(self.state.acdc_converters) - 1
        self.acdcConverterAdded.emit(idx)
        return idx

    def remove_acdc_converter(self, index: int):
        self.checkpoint()
        if 0 <= index < len(self.state.acdc_converters):
            self.state.acdc_converters.pop(index)
            self.acdcConverterRemoved.emit(index)

    def update_acdc_converter(self, index: int, **kwargs):
        self.checkpoint()
        if 0 <= index < len(self.state.acdc_converters):
            conv = self.state.acdc_converters[index]
            for k, v in kwargs.items():
                if hasattr(conv, k):
                    setattr(conv, k, v)

    # ------------------------------------------------------------------
    # Frequency converter operations
    # ------------------------------------------------------------------

    def add_freq_converter(
        self, name: str, from_node: int = 0, to_node: int = 0,
        from_bus: Optional[str] = None, to_bus: Optional[str] = None,
        **kwargs,
    ) -> int:
        self.checkpoint()
        if from_bus is None:
            from_bus = self._default_bus_for_node(from_node)
        if to_bus is None:
            to_bus = self._default_bus_for_node(to_node)
        conv = GuiFrequencyConverter(
            name=name,
            from_bus=from_bus, to_bus=to_bus,
            from_node=self._node_for_bus(from_bus),
            to_node=self._node_for_bus(to_bus),
        )
        for k, v in kwargs.items():
            if hasattr(conv, k):
                setattr(conv, k, v)
        self.state.freq_converters.append(conv)
        idx = len(self.state.freq_converters) - 1
        self.freqConverterAdded.emit(idx)
        return idx

    def remove_freq_converter(self, index: int):
        self.checkpoint()
        if 0 <= index < len(self.state.freq_converters):
            self.state.freq_converters.pop(index)
            self.freqConverterRemoved.emit(index)

    def update_freq_converter(self, index: int, **kwargs):
        self.checkpoint()
        if 0 <= index < len(self.state.freq_converters):
            conv = self.state.freq_converters[index]
            for k, v in kwargs.items():
                if hasattr(conv, k):
                    setattr(conv, k, v)

    # ------------------------------------------------------------------
    # Fuel source operations
    # ------------------------------------------------------------------

    def add_fuel_source(self, source_id: str, name: str, unit: str,
                        num_nodes: int = 0, **kwargs) -> str:
        self.checkpoint()
        source = GuiFuelSource(
            source_id=source_id, name=name, unit=unit,
            max_availability=[0.0] * num_nodes,
            import_cost=[0.0] * num_nodes,
            storage_capacity=[0.0] * num_nodes,
            initial_storage_level=[0.5] * num_nodes,
        )
        for k, v in kwargs.items():
            if hasattr(source, k):
                setattr(source, k, v)
        self.state.fuel_sources[source_id] = source
        self.fuelSourceAdded.emit(source_id)
        return source_id

    def remove_fuel_source(self, source_id: str):
        self.checkpoint()
        if source_id in self.state.fuel_sources:
            del self.state.fuel_sources[source_id]
            self.fuelSourceRemoved.emit(source_id)

    def update_fuel_source(self, source_id: str, **kwargs):
        self.checkpoint()
        if source_id in self.state.fuel_sources:
            src = self.state.fuel_sources[source_id]
            for k, v in kwargs.items():
                if hasattr(src, k):
                    setattr(src, k, v)
            self.fuelSourceUpdated.emit(source_id)

    # ------------------------------------------------------------------
    # Fuel storage operations
    # ------------------------------------------------------------------

    def add_fuel_storage(
        self,
        name: str,
        fuel: str = "",
        node: int = 0,
        storage_id: Optional[str] = None,
        **params,
    ) -> str:
        self.checkpoint()
        if storage_id is None:
            idx = len(self.state.fuel_storages)
            storage_id = f"fuel_storage_{idx}"
            while storage_id in self.state.fuel_storages:
                idx += 1
                storage_id = f"fuel_storage_{idx}"
        fuels = [fuel] if fuel else []
        fuel_params = {fuel: FuelStorageParams()} if fuel else {}
        inst = GuiFuelStorage(
            storage_id=storage_id, name=name, fuels=fuels,
            fuel_params=fuel_params, node=node,
        )
        for k, v in params.items():
            if hasattr(inst, k):
                setattr(inst, k, v)
        self.state.fuel_storages[storage_id] = inst
        self.fuelStorageAdded.emit(storage_id)
        return storage_id

    def remove_fuel_storage(self, storage_id: str):
        self.checkpoint()
        if storage_id in self.state.fuel_storages:
            del self.state.fuel_storages[storage_id]
            self.fuelStorageRemoved.emit(storage_id)

    def update_fuel_storage(self, storage_id: str, **kwargs):
        self.checkpoint()
        if storage_id in self.state.fuel_storages:
            inst = self.state.fuel_storages[storage_id]
            for k, v in kwargs.items():
                if hasattr(inst, k):
                    setattr(inst, k, v)
            self.fuelStorageUpdated.emit(storage_id)

    # ------------------------------------------------------------------
    # Fuel transport route operations
    # ------------------------------------------------------------------

    def add_fuel_route(
        self,
        from_node: int,
        to_node: int,
        fuels: list[str] | None = None,
        capacity: float = 0.0,
        route_id: Optional[str] = None,
        waypoints: Optional[list[GeoPoint]] = None,
        from_endpoint: Optional[EndpointRef] = None,
        to_endpoint: Optional[EndpointRef] = None,
    ) -> str:
        self.checkpoint()
        if route_id is None:
            route_id = f"fuel_route_{self.state._next_fuel_route_id}"
            self.state._next_fuel_route_id += 1
        if from_endpoint is None:
            from_endpoint = EndpointRef("node", str(from_node))
        if to_endpoint is None:
            to_endpoint = EndpointRef("node", str(to_node))
        route = GuiFuelTransportRoute(
            route_id=route_id,
            fuels=fuels or [],
            from_node=from_node,
            to_node=to_node,
            capacity=capacity,
            waypoints=waypoints or [],
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
        )
        self.state.fuel_transport_routes.append(route)
        self.fuelRouteAdded.emit(route.route_id)
        return route.route_id

    def remove_fuel_route(self, route_id: str):
        self.checkpoint()
        before = len(self.state.fuel_transport_routes)
        self.state.fuel_transport_routes = [
            r for r in self.state.fuel_transport_routes if r.route_id != route_id
        ]
        if len(self.state.fuel_transport_routes) < before:
            self.fuelRouteRemoved.emit(route_id)

    def update_fuel_route(self, route_id: str, **kwargs):
        self.checkpoint()
        for rt in self.state.fuel_transport_routes:
            if rt.route_id == route_id:
                for k, v in kwargs.items():
                    if hasattr(rt, k):
                        setattr(rt, k, v)
                self.fuelRouteUpdated.emit(route_id)
                return

    # ------------------------------------------------------------------
    # Fuel operations (FuelConfig, not FuelSource)
    # ------------------------------------------------------------------

    def add_fuel(self, fuel_id: str, name: str, **kwargs) -> str:
        self.checkpoint()
        fuel = GuiFuel(fuel_id=fuel_id, name=name)
        for k, v in kwargs.items():
            if hasattr(fuel, k):
                setattr(fuel, k, v)
        self.state.fuels[fuel_id] = fuel
        self.fuelAdded.emit(fuel_id)
        return fuel_id

    def remove_fuel(self, fuel_id: str):
        self.checkpoint()
        if fuel_id in self.state.fuels:
            del self.state.fuels[fuel_id]
            self.fuelRemoved.emit(fuel_id)

    def update_fuel(self, fuel_id: str, **kwargs):
        self.checkpoint()
        if fuel_id in self.state.fuels:
            fuel = self.state.fuels[fuel_id]
            for k, v in kwargs.items():
                if hasattr(fuel, k):
                    setattr(fuel, k, v)
            self.fuelUpdated.emit(fuel_id)

    # ------------------------------------------------------------------
    # Electrolyzer operations (instance-based)
    # ------------------------------------------------------------------

    def add_electrolyzer_instance(
        self, unit_key: str, name: str, node: int = 0,
        bus: Optional[str] = None, **params,
    ) -> str:
        self.checkpoint()
        if bus is None:
            bus = self._default_bus_for_node(node)
        instance_id = self._make_instance_id(
            unit_key, bus, self.state.electrolyzers,
        )
        inst = GuiElectrolyzerInstance(
            instance_id=instance_id, unit_key=unit_key, name=name,
            bus=bus, node=self._node_for_bus(bus),
        )
        for k, v in params.items():
            if hasattr(inst, k):
                setattr(inst, k, v)
        self.state.electrolyzers[instance_id] = inst
        self.electrolyzerAdded.emit(instance_id)
        return instance_id

    def remove_electrolyzer(self, instance_id: str):
        self.checkpoint()
        if instance_id in self.state.electrolyzers:
            del self.state.electrolyzers[instance_id]
            self.electrolyzerRemoved.emit(instance_id)

    def update_electrolyzer(self, instance_id: str, **kwargs):
        self.checkpoint()
        if instance_id in self.state.electrolyzers:
            inst = self.state.electrolyzers[instance_id]
            for k, v in kwargs.items():
                if hasattr(inst, k):
                    setattr(inst, k, v)
            self.electrolyzerUpdated.emit(instance_id)

    # ------------------------------------------------------------------
    # Technology operations
    # ------------------------------------------------------------------

    def add_technology(
        self,
        name: str = "New Technology",
        category: str = "Renewable",
        tech_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        self.checkpoint()
        if tech_id is None:
            tech_id = f"tech_{self.state._next_tech_id}"
            self.state._next_tech_id += 1
        tech = GuiTechnology(tech_id=tech_id, name=name, category=category)
        for k, v in kwargs.items():
            if hasattr(tech, k):
                setattr(tech, k, v)
        self.state.technologies[tech_id] = tech
        if tech_id.startswith("tech_"):
            try:
                num = int(tech_id[5:])
                if num >= self.state._next_tech_id:
                    self.state._next_tech_id = num + 1
            except ValueError:
                pass
        self.technologyAdded.emit(tech_id)
        return tech_id

    def remove_technology(self, tech_id: str):
        self.checkpoint()
        if tech_id in self.state.technologies:
            del self.state.technologies[tech_id]
            self.technologyRemoved.emit(tech_id)

    def update_technology(self, tech_id: str, **kwargs):
        self.checkpoint()
        if tech_id in self.state.technologies:
            tech = self.state.technologies[tech_id]
            for k, v in kwargs.items():
                if hasattr(tech, k):
                    setattr(tech, k, v)
            self.technologyUpdated.emit(tech_id)

    # ------------------------------------------------------------------
    # Investment portfolio operations
    # ------------------------------------------------------------------

    def add_investment_entry(
        self,
        name: str,
        technology_type: str,
        target_key: str = "",
        entry_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        self.checkpoint()
        if entry_id is None:
            entry_id = f"inv_{self.state._next_investment_id}"
            self.state._next_investment_id += 1
        entry = GuiInvestmentEntry(
            entry_id=entry_id,
            name=name,
            technology_type=technology_type,
            target_key=target_key,
        )
        for k, v in kwargs.items():
            if hasattr(entry, k):
                setattr(entry, k, v)
        self.state.investment_portfolio[entry_id] = entry
        if entry_id.startswith("inv_"):
            try:
                num = int(entry_id[4:])
                if num >= self.state._next_investment_id:
                    self.state._next_investment_id = num + 1
            except ValueError:
                pass
        self.investmentEntryAdded.emit(entry_id)
        return entry_id

    def remove_investment_entry(self, entry_id: str):
        self.checkpoint()
        if entry_id in self.state.investment_portfolio:
            del self.state.investment_portfolio[entry_id]
            self.investmentEntryRemoved.emit(entry_id)

    def update_investment_entry(self, entry_id: str, **kwargs):
        self.checkpoint()
        if entry_id in self.state.investment_portfolio:
            entry = self.state.investment_portfolio[entry_id]
            for k, v in kwargs.items():
                if hasattr(entry, k):
                    setattr(entry, k, v)
            self.investmentEntryUpdated.emit(entry_id)

    # ------------------------------------------------------------------
    # Bulk load
    # ------------------------------------------------------------------

    def load_state(self, state: GuiSystemState):
        """Replace the entire state and emit stateLoaded."""
        self.state = state
        # Ensure line counter is at least past any existing line IDs
        for ln in self.state.transmission_lines:
            if ln.line_id.startswith("line_"):
                try:
                    num = int(ln.line_id[5:])
                    if num >= self.state._next_line_id:
                        self.state._next_line_id = num + 1
                except ValueError:
                    pass
        # Ensure fuel route counter is at least past any existing route IDs
        for rt in self.state.fuel_transport_routes:
            if rt.route_id.startswith("fuel_route_"):
                try:
                    num = int(rt.route_id[11:])
                    if num >= self.state._next_fuel_route_id:
                        self.state._next_fuel_route_id = num + 1
                except ValueError:
                    pass
        # Ensure bus counter is at least past any existing bus IDs
        for bid in self.state.buses:
            if bid.startswith("bus_"):
                try:
                    num = int(bid[4:])
                    if num >= self.state._next_bus_id:
                        self.state._next_bus_id = num + 1
                except ValueError:
                    pass
        # Auto-create default buses if none exist (backward compat)
        if not self.state.buses and self.state.nodes:
            for node in self.state.nodes:
                bus_id = f"bus_{self.state._next_bus_id}"
                self.state._next_bus_id += 1
                lat = 0.0
                lng = 0.0
                self.state.buses[bus_id] = GuiBus(
                    bus_id=bus_id,
                    name=f"Bus {node.index}",
                    parent_node=node.index,
                    demand_fraction=1.0,
                    latitude=lat,
                    longitude=lng,
                )
        # Ensure renewable fuels always exist
        self._ensure_renewable_fuels()
        self.stateLoaded.emit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_renewable_fuels(self):
        """Add default renewable fuels if not already present."""
        for fid in RENEWABLE_FUELS:
            if fid not in self.state.fuels:
                self.state.fuels[fid] = GuiFuel(fuel_id=fid, name=fid)

    @staticmethod
    def _make_instance_id(unit_key: str, bus_or_node, existing: dict) -> str:
        """Generate a unique instance_id like 'unit_1_bus_0'."""
        candidate = f"{unit_key}_{bus_or_node}"
        if candidate not in existing:
            return candidate
        seq = 2
        while f"{candidate}_{seq}" in existing:
            seq += 1
        return f"{candidate}_{seq}"

    def _default_bus_for_node(self, node_index: int) -> str:
        """Return the first bus belonging to the given node, or 'bus_0' fallback."""
        for bid, bus in self.state.buses.items():
            if bus.parent_node == node_index:
                return bid
        # Fallback: return first bus if any
        if self.state.buses:
            return next(iter(self.state.buses))
        return "bus_0"

    def _node_for_bus(self, bus_id: str) -> int:
        """Return the parent node index for a bus_id."""
        bus = self.state.buses.get(bus_id)
        return bus.parent_node if bus else 0

    def get_buses_for_node(self, node_index: int) -> list[GuiBus]:
        """Return all buses belonging to a node."""
        return [b for b in self.state.buses.values() if b.parent_node == node_index]

    # ------------------------------------------------------------------
    # Bus operations
    # ------------------------------------------------------------------

    def add_bus(
        self,
        parent_node: int = 0,
        name: str = "",
        bus_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Create a new bus at a node."""
        self.checkpoint()
        if bus_id is None:
            bus_id = f"bus_{self.state._next_bus_id}"
            self.state._next_bus_id += 1
        if not name:
            name = bus_id.replace("_", " ").title()
        bus = GuiBus(bus_id=bus_id, name=name, parent_node=parent_node)
        for k, v in kwargs.items():
            if hasattr(bus, k):
                setattr(bus, k, v)
        self.state.buses[bus_id] = bus
        # Track ID counter
        if bus_id.startswith("bus_"):
            try:
                num = int(bus_id[4:])
                if num >= self.state._next_bus_id:
                    self.state._next_bus_id = num + 1
            except ValueError:
                pass
        self.busAdded.emit(bus_id)
        return bus_id

    def remove_bus(self, bus_id: str):
        """Remove a bus and all equipment connected to it."""
        self.checkpoint()
        if bus_id not in self.state.buses:
            return
        # Remove generators on this bus (granular signals)
        for k in [k for k, g in self.state.generators.items() if g.bus == bus_id]:
            del self.state.generators[k]
            self.generatorRemoved.emit(k)
        # Remove batteries on this bus
        for k in [k for k, b in self.state.batteries.items() if b.bus == bus_id]:
            del self.state.batteries[k]
            self.batteryRemoved.emit(k)
        # Remove electrolyzers on this bus
        for k in [k for k, e in self.state.electrolyzers.items() if e.bus == bus_id]:
            del self.state.electrolyzers[k]
            self.electrolyzerRemoved.emit(k)
        # Remove lines referencing this bus (collect IDs first)
        removed_lines = [ln.line_id for ln in self.state.transmission_lines
                         if ln.from_bus == bus_id or ln.to_bus == bus_id]
        if removed_lines:
            self.state.transmission_lines = [
                ln for ln in self.state.transmission_lines
                if ln.from_bus != bus_id and ln.to_bus != bus_id
            ]
            for lid in removed_lines:
                self.lineRemoved.emit(lid)
        # Remove transformers referencing this bus (reverse index order)
        tr_indices = sorted(
            [i for i, tr in enumerate(self.state.transformers)
             if tr.from_bus == bus_id or tr.to_bus == bus_id],
            reverse=True,
        )
        for idx in tr_indices:
            self.state.transformers.pop(idx)
            self.transformerRemoved.emit(idx)
        # Remove AC/DC converters referencing this bus (reverse index order)
        acdc_indices = sorted(
            [i for i, c in enumerate(self.state.acdc_converters)
             if c.from_bus == bus_id or c.to_bus == bus_id],
            reverse=True,
        )
        for idx in acdc_indices:
            self.state.acdc_converters.pop(idx)
            self.acdcConverterRemoved.emit(idx)
        # Remove frequency converters referencing this bus (reverse index order)
        freq_indices = sorted(
            [i for i, c in enumerate(self.state.freq_converters)
             if c.from_bus == bus_id or c.to_bus == bus_id],
            reverse=True,
        )
        for idx in freq_indices:
            self.state.freq_converters.pop(idx)
            self.freqConverterRemoved.emit(idx)
        del self.state.buses[bus_id]
        self.busRemoved.emit(bus_id)

    def update_bus(self, bus_id: str, **kwargs):
        """Update bus properties."""
        self.checkpoint()
        if bus_id in self.state.buses:
            bus = self.state.buses[bus_id]
            for k, v in kwargs.items():
                if hasattr(bus, k):
                    setattr(bus, k, v)
            self.busUpdated.emit(bus_id)
