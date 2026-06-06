"""
Pydantic configuration schema for ESFEX.

Defines strongly-typed dataclass models for all configuration entities:
- ESFEXConfig: Top-level configuration container
- SystemConfig: Per-system (e.g., Cuba, Jamaica) configuration
- GeneratorConfig, BatteryConfig, NodeConfig, FuelConfig, etc.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from esfex.utils.temporal import HOURS_STD_YEAR


# =============================================================================
# BASE MODELS AND ENUMS
# =============================================================================


class FuelConfig(BaseModel):
    """Fuel definition with physical and economic properties."""

    name: str
    unit: Optional[str] = None  # None for renewable sources
    emission_factor: float = Field(ge=0, description="CO2 emissions (ton/MWh)")
    energy_content: Optional[float] = Field(None, ge=0, description="MWh per unit")
    price_base: float = Field(ge=0, description="Base price (USD/unit)")
    price_growth_rate: float = Field(default=0, description="Annual price growth rate")


class PenaltiesConfig(BaseModel):
    """Penalty costs for constraint violations."""

    loss_of_load: float = Field(default=10e6, description="$/MW not supplied")
    loss_of_reserve_static: float = Field(default=100, description="$/MW static reserve deficit")
    loss_of_reserve_dynamic: float = Field(default=100, description="$/MW dynamic reserve deficit")
    loss_of_inertia: float = Field(default=200, description="$/MW-s inertia deficit")
    transfer_margin: float = Field(default=100, description="$/MW transfer margin violation")
    max_curtailment_ratio: float = Field(default=0.05, ge=0, le=1, description="Max curtailment as fraction of RE generation (0.05 = 5%)")
    curtailment_cost: float = Field(default=20.0, ge=0, description="$/MWh penalty for curtailed RE energy")
    curtailment_excess_penalty: float = Field(default=500.0, ge=0, description="$/MWh penalty for curtailment exceeding ratio limit")
    re_excess_penalty: float = Field(default=100.0, ge=0, description="$/MWh penalty for RE generation exceeding target")
    rooftop_curtailment: float = Field(default=5, description="$/MWh rooftop curtailed")
    co2_cost: float = Field(default=10, description="$/tCO2")
    co2_budget_violation: float = Field(default=500, description="$/tCO2 over budget")
    fre_penetration_loss: float = Field(default=100, description="$/MWh RE shortfall")
    ev_loss: float = Field(default=10, description="$/MWh EV demand not met")
    loss_of_fuel_supply: float = Field(default=100, description="$/unit fuel deficit")
    coupling_slack_penalty: float = Field(default=1.0, description="$/unit PE periodic-hourly coupling slack")
    transport_congestion: float = Field(default=100, description="$/MW congestion")
    storage_violation: float = Field(default=100, description="$/MW storage violation")
    non_electric_demand_loss: float = Field(default=100, description="$/unit fuel demand unmet")
    soc_violation: float = Field(default=1e6, description="$/MWh SOC limit violation")
    delay_retirement_per_mw: float = Field(default=50000, description="$/MW delay retirement penalty")


class CO2BudgetConfig(BaseModel):
    """CO2 emissions budget configuration."""

    enabled: bool = True
    annual_budget: float = Field(default=1e6, ge=0, description="tonnes CO2 per year")


class CriticalityPenalties(BaseModel):
    """Penalties by load criticality level."""

    critical: float = 1000
    high: float = 100
    medium: float = 10
    low: float = 1


# =============================================================================
# GIS / GEOGRAPHIC CONFIGURATION
# =============================================================================


class GeoCoordinate(BaseModel):
    """Geographic coordinate (WGS84)."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: Optional[str] = None
    radius_km: float = Field(default=20.0, ge=0.1, le=500)


class BusConfig(BaseModel):
    """Electrical bus within a node.

    The `role` field declares the electrical purpose of the bus and controls
    how the optimization model treats it:

    - **connection**: Pure junction — only connects generators, batteries,
      transformers, or transmission lines. Does NOT carry demand; no
      load_shed/reserve variables are created. KCL only enforces power
      balance (Σ injections − Σ withdrawals = 0).
    - **load**: Demand-carrying bus (substation feeder). Has
      `demand_fraction > 0`; full KCL with load_shed and reserve terms.
    - **mixed**: Both connection and load (rare — e.g. industrial load
      co-located with its dedicated generator).

    Enforcing roles explicitly prevents spurious demand allocation to
    generator/transformer junction buses, which otherwise creates
    near-degenerate LPs that fail IPM convergence.
    """

    bus_id: Optional[str] = None
    name: str = ""
    parent_node: int = Field(ge=0, default=0)
    voltage_kv: float = Field(gt=0, default=220.0)
    frequency_hz: float = Field(gt=0, default=50.0)
    current_type: Literal["AC", "DC"] = "AC"
    bus_type: Literal["PQ", "PV", "slack"] = "PQ"
    role: Literal["connection", "load", "mixed"] = "load"
    demand_fraction: float = 1.0

    @model_validator(mode="after")
    def _validate_role_consistency(self) -> "BusConfig":
        if self.role == "connection" and self.demand_fraction != 0.0:
            raise ValueError(
                f"Bus {self.bus_id!r}: role='connection' requires demand_fraction=0 "
                f"(got {self.demand_fraction}). Connection buses do not carry load."
            )
        if self.role in ("load", "mixed") and self.demand_fraction < 0:
            raise ValueError(
                f"Bus {self.bus_id!r}: role={self.role!r} requires demand_fraction >= 0 "
                f"(got {self.demand_fraction})."
            )
        return self


class TransmissionLineGeo(BaseModel):
    """Geographic metadata for a transmission line."""

    line_id: Optional[str] = None
    from_node: int = Field(ge=0)
    to_node: int = Field(ge=0)
    from_bus: Optional[int] = None   # Bus index (0-indexed), preferred over from_node
    to_bus: Optional[int] = None     # Bus index (0-indexed), preferred over to_node
    capacity_mw: Optional[float] = None
    waypoints: list[GeoCoordinate] = Field(
        default_factory=list,
        description="Intermediate geographic waypoints for line routing",
    )
    voltage_kv: Optional[float] = None
    line_type: Optional[Literal["overhead", "underground", "submarine"]] = None
    # Power flow properties (per-line overrides for global DC power flow defaults)
    length_km: Optional[float] = None
    reactance_pu: Optional[float] = None
    resistance_pu: Optional[float] = None
    susceptance_pu: Optional[float] = None
    num_circuits: int = 1

    # Electrical properties
    frequency_hz: float = Field(default=50.0, gt=0, description="Operating frequency (Hz)")
    current_type: Literal["AC", "DC"] = Field(default="AC", description="Current type")

    # GUI endpoint references (preserved for round-trip spatial fidelity)
    from_endpoint_type: Optional[str] = None   # "node", "generator", "battery", etc.
    from_endpoint_id: Optional[str] = None     # element identifier
    to_endpoint_type: Optional[str] = None
    to_endpoint_id: Optional[str] = None


class TransformerConfig(BaseModel):
    """Transformer connecting different voltage levels between two nodes."""

    name: str
    from_node: int = Field(ge=0, description="HV-side node index (0-indexed)")
    to_node: int = Field(ge=0, description="LV-side node index (0-indexed)")
    from_bus: Optional[int] = None   # Bus index (0-indexed), preferred over from_node
    to_bus: Optional[int] = None     # Bus index (0-indexed), preferred over to_node
    from_voltage_kv: float = Field(gt=0)
    to_voltage_kv: float = Field(gt=0)
    rated_power_mva: float = Field(gt=0)
    impedance_pu: float = Field(default=0.1, gt=0)
    resistance_pu: Optional[float] = Field(default=None, description="Series resistance (p.u.), derived from losses if None")
    losses_fraction: float = Field(default=0.005, ge=0, le=1)

class ACDCConverterConfig(BaseModel):
    """AC/DC converter (rectifier/inverter) connecting AC and DC buses."""

    name: str
    converter_type: Literal["VSC", "LCC"] = Field(default="VSC", description="VSC or LCC topology")
    from_node: int = Field(ge=0, description="AC-side node index (0-indexed)")
    to_node: int = Field(ge=0, description="DC-side node index (0-indexed)")
    from_bus: Optional[int] = None   # Bus index (0-indexed), preferred over from_node
    to_bus: Optional[int] = None     # Bus index (0-indexed), preferred over to_node
    from_voltage_kv: float = Field(default=220.0, gt=0)
    dc_voltage_kv: float = Field(default=320.0, gt=0)
    rated_power_mva: float = Field(default=100.0, gt=0)
    min_power_mva: float = Field(default=0.0, ge=0)
    efficiency_rectify: float = Field(default=0.98, gt=0, le=1, description="AC→DC efficiency")
    efficiency_invert: float = Field(default=0.98, gt=0, le=1, description="DC→AC efficiency")
    standby_losses_mw: float = Field(default=0.5, ge=0)
    reactive_power_min_mvar: float = Field(default=-50.0, description="Min Q (MVAr)")
    reactive_power_max_mvar: float = Field(default=50.0, description="Max Q (MVAr)")
    power_factor: float = Field(default=1.0, gt=0, le=1)
    impedance_pu: float = Field(default=0.05, gt=0)
    resistance_pu: float = Field(default=0.01, ge=0)
    invest_cost: float = Field(default=0.0, ge=0, description="$/MW")
    fixed_cost: float = Field(default=0.0, ge=0, description="$/MW/year")
    variable_cost: float = Field(default=0.0, ge=0, description="$/MWh")
    invest_max_power: float = Field(default=0.0, ge=0, description="Max investment (MW)")
    life_time: int = Field(default=30, ge=1)
    initial_age: int = Field(default=0, ge=0)
    degradation_rate: float = Field(default=0.005, ge=0)


class FrequencyConverterConfig(BaseModel):
    """Frequency converter connecting buses at different frequencies."""

    name: str
    from_node: int = Field(ge=0, description="Frequency-A bus (0-indexed)")
    to_node: int = Field(ge=0, description="Frequency-B bus (0-indexed)")
    from_bus: Optional[int] = None   # Bus index (0-indexed), preferred over from_node
    to_bus: Optional[int] = None     # Bus index (0-indexed), preferred over to_node
    from_frequency_hz: float = Field(default=50.0, gt=0)
    to_frequency_hz: float = Field(default=60.0, gt=0)
    rated_power_mva: float = Field(default=100.0, gt=0)
    min_power_mva: float = Field(default=0.0, ge=0)
    efficiency_a_to_b: float = Field(default=0.98, gt=0, le=1)
    efficiency_b_to_a: float = Field(default=0.98, gt=0, le=1)
    standby_losses_mw: float = Field(default=0.5, ge=0)
    reactive_power_min_mvar: float = Field(default=-50.0)
    reactive_power_max_mvar: float = Field(default=50.0)
    impedance_pu: float = Field(default=0.05, gt=0)
    resistance_pu: float = Field(default=0.01, ge=0)
    invest_cost: float = Field(default=0.0, ge=0, description="$/MW")
    fixed_cost: float = Field(default=0.0, ge=0, description="$/MW/year")
    variable_cost: float = Field(default=0.0, ge=0, description="$/MWh")
    invest_max_power: float = Field(default=0.0, ge=0, description="Max investment (MW)")
    life_time: int = Field(default=30, ge=1)
    initial_age: int = Field(default=0, ge=0)
    degradation_rate: float = Field(default=0.005, ge=0)


class DevelopmentZoneConfig(BaseModel):
    """Geographic zone where a technology can be developed."""

    name: str
    technology: str = Field(description="e.g., Solar, Wind, Battery")
    layer: Literal["electrical", "primary_energy"] = "electrical"
    polygon: list[GeoCoordinate] = Field(
        description="Vertices of the polygon boundary (closed ring)",
    )
    max_capacity_mw: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None

    # Interconnection parameters
    line_cost_per_mw_km: float = Field(
        default=1500.0, ge=0,
        description="Transmission line cost ($/MW/km)",
    )
    transformer_cost_per_mw: float = Field(
        default=50000.0, ge=0,
        description="Step-up transformer cost ($/MW)",
    )
    target_bus: Optional[int] = Field(
        default=None,
        description="Override nearest bus detection (0-indexed bus index)",
    )
    allowed_generators: Optional[list[str]] = Field(
        default=None,
        description="Generator keys allowed in zone (None = match by technology name)",
    )
    allowed_technologies: Optional[dict[str, float]] = Field(
        default=None,
        description=(
            "Technology keys mapped to max investment MW (0 = unlimited). "
            "None = match by zone.technology name/fuel."
        ),
    )
    exclusive: bool = Field(
        default=False,
        description=(
            "If True, matched technologies can ONLY invest at this zone. "
            "invest_max_power at all original nodes is set to 0 after expansion."
        ),
    )


class FuelEntryPointConfig(BaseModel):
    """Geographic entry point where fuel enters the system (port, pipeline terminal)."""

    name: str
    fuel: str = ""
    fuels: list[str] = Field(default_factory=list)
    node: int = Field(ge=0)
    coordinate: GeoCoordinate
    max_import_rate: float = Field(default=0, ge=0, description="Max import rate (units/hour)")
    import_cost: float = Field(default=0, ge=0, description="Import cost ($/unit)")
    fuel_params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _populate_fuels(self) -> FuelEntryPointConfig:
        """Ensure ``fuels`` is populated from legacy ``fuel`` field."""
        if not self.fuels and self.fuel:
            self.fuels = [self.fuel]
        if not self.fuel and self.fuels:
            self.fuel = self.fuels[0]
        return self


# =============================================================================
# NETWORK CONFIGURATION
# =============================================================================


class NodeConfig(BaseModel):
    """Network topology and reserve requirements."""

    num_nodes: Optional[int] = Field(default=None, description="Number of nodes (auto-inferred if not set)")
    nodes_connections: list[float] = Field(
        description="Flattened NxN adjacency matrix with line capacities (MW)"
    )
    reserve_static: list[float] = Field(default_factory=list, description="Static reserve requirement per node (MW)")
    reserve_dynamic: list[float] = Field(default_factory=list, description="Dynamic reserve requirement per node (MW)")
    reserve_duration: list[int] = Field(default_factory=list, description="Reserve duration per node (hours)")
    losses: list[float] = Field(default_factory=list, description="Transmission losses per node (fraction)")
    transference_invest_cost: list[float] = Field(default_factory=list, description="Transmission investment cost ($/MW)")
    transference_invest_max: list[float] = Field(default_factory=list, description="Max transmission investment (MW)")

    # GIS metadata (optional, backward-compatible)
    node_coordinates: Optional[list[GeoCoordinate]] = Field(
        default=None, description="Geographic coordinates per node (length = num_nodes)"
    )
    node_names: Optional[list[str]] = Field(
        default=None, description="Human-readable node names"
    )

    @model_validator(mode="after")
    def _set_defaults_from_num_nodes(self) -> "NodeConfig":
        """Auto-fill empty lists with defaults based on num_nodes."""
        import math
        n = self.num_nodes or int(math.sqrt(len(self.nodes_connections)))

        if not self.reserve_static:
            self.reserve_static = [0.0] * n
        if not self.reserve_dynamic:
            self.reserve_dynamic = [0.0] * n
        if not self.reserve_duration:
            self.reserve_duration = [1] * n
        if not self.losses:
            self.losses = [0.0] * n
        if not self.transference_invest_cost:
            self.transference_invest_cost = [0.0] * n
        if not self.transference_invest_max:
            self.transference_invest_max = [0.0] * n

        if self.num_nodes is None:
            self.num_nodes = n

        return self


class DCPowerFlowConfig(BaseModel):
    """DC power flow model parameters."""

    base_impedance: float = Field(default=100.0, description="Base impedance (Ohm)")
    reactance_per_km: float = Field(default=0.4, description="Line reactance (Ohm/km)")
    voltage_level_kv: float = Field(default=220.0, description="Nominal voltage (kV)")
    # NOTE: DC angle-difference limits were removed — in this DC formulation
    # they impose a non-physical pf ≤ b_line·max_angle throttle.  DC flows are
    # bounded by the explicit thermal pf ≤ line_capacity constraints.
    # max_angle_diff_deg is retained: it IS physically meaningful and
    # load-bearing in the ACOPF formulations (power_flow_mode='acopf_*').
    max_angle_diff_deg: float = Field(default=30.0, ge=0, le=90, description="Max angle diff (deg, ACOPF only)")
    slack_bus: int = Field(default=0, ge=0, description="Slack bus index (0-indexed)")

    # Transmission loss model
    loss_model: Literal["none", "linear", "pwl"] = Field(
        default="pwl",
        description="Loss model: none=lossless, linear=constant factor, pwl=piecewise linear quadratic",
    )
    pwl_loss_segments: int = Field(
        default=3, ge=1, le=10,
        description="Number of PWL segments for operational dispatch",
    )
    pwl_loss_segments_master: int = Field(
        default=2, ge=1, le=5,
        description="Number of PWL segments for master problem (fewer for performance)",
    )


class ACPowerFlowConfig(BaseModel):
    """AC power flow configuration.

    When power_flow_mode is 'dcopf_ac_verify', these settings control the
    Newton-Raphson verification step (post-DCOPF, UC mode only).

    When power_flow_mode starts with 'acopf_', base_mva and voltage limits
    are used for the ACOPF optimization formulation.
    """

    enabled: bool = False
    max_iterations: int = Field(default=50, ge=1)
    tolerance: float = Field(default=1e-6, gt=0)
    base_mva: float = Field(default=100.0, gt=0)
    voltage_min_pu: float = Field(default=0.90, gt=0, le=1.0)
    voltage_max_pu: float = Field(default=1.10, ge=1.0)
    check_hours: Literal["all", "peak", "sample"] = "peak"
    sample_count: int = Field(default=24, ge=1)
    # Default power factor for reactive limit estimation when Q limits not specified
    default_power_factor: float = Field(default=0.85, gt=0, le=1.0)
    # Load power factor for reactive demand estimation: Q_load = P_load × tan(acos(pf))
    load_power_factor: float = Field(default=0.9, gt=0, le=1.0)
    # Q slack penalty ($/MVAr) — penalizes reactive power imbalance
    q_slack_penalty: float = Field(default=100.0, ge=0)
    # Minimum reactance (p.u.) to clamp short lines/bus-ties
    min_reactance_pu: float = Field(default=0.01, gt=0)
    # Transformer tap ratio bounds — taps outside [min, max] are reset to 1.0
    tap_ratio_min: float = Field(default=0.5, gt=0)
    tap_ratio_max: float = Field(default=2.0, gt=0)
    # Q_min default ratio: Q_min = -ratio × Q_max (when Q limits not specified)
    q_min_ratio: float = Field(default=0.5, ge=0, le=1.0)


# =============================================================================
# BIDDING / OFFER CURVE CONFIGURATION
# =============================================================================


class CostCurveBlock(BaseModel):
    """A single price block in a stepwise offer curve."""

    fraction: float = Field(ge=0, le=1, description="Fraction of Pmax for this block")
    price: float = Field(ge=0, description="Marginal cost for this block ($/MWh)")


class CostCurveConfig(BaseModel):
    """Bidding/offer curve for a generator or battery at one node.

    All curve types are internally normalised to stepwise blocks by
    :func:`normalize_cost_curve`.
    """

    curve_type: Literal["flat", "linear", "stepwise", "exponential"] = "flat"

    # Flat
    flat_price: Optional[float] = Field(default=None, ge=0)

    # Stepwise
    blocks: Optional[list[CostCurveBlock]] = None

    # Linear: p(P) = price_at_zero + (price_at_max - price_at_zero) * P / Pmax
    price_at_zero: Optional[float] = Field(default=None, ge=0)
    price_at_max: Optional[float] = Field(default=None, ge=0)

    # Exponential: p(P) = base_price * exp(scale_factor * P / Pmax)
    base_price: Optional[float] = Field(default=None, ge=0)
    scale_factor: Optional[float] = Field(default=None, ge=0)

    # Number of linear segments for linear/exponential approximation
    num_segments: int = Field(default=5, ge=2, le=20)


def normalize_cost_curve(
    curve: CostCurveConfig,
    fallback_price: float = 0.0,
) -> list[CostCurveBlock]:
    """Convert any :class:`CostCurveConfig` to a list of stepwise blocks.

    Parameters
    ----------
    curve:
        The curve configuration to normalise.
    fallback_price:
        Scalar fuel_cost used when ``curve_type == "flat"`` and
        ``flat_price`` is not set.

    Returns
    -------
    list[CostCurveBlock]
        Always non-empty, fractions sum to 1.0.
    """
    import math

    ct = curve.curve_type

    if ct == "flat":
        p = curve.flat_price if curve.flat_price is not None else fallback_price
        return [CostCurveBlock(fraction=1.0, price=p)]

    if ct == "stepwise":
        if not curve.blocks:
            return [CostCurveBlock(fraction=1.0, price=fallback_price)]
        # Validate: fractions must sum ~1.0
        total = sum(b.fraction for b in curve.blocks)
        if abs(total - 1.0) > 0.01:
            # Auto-normalise fractions
            blocks = [
                CostCurveBlock(fraction=b.fraction / total, price=b.price)
                for b in curve.blocks
            ]
        else:
            blocks = list(curve.blocks)
        return blocks

    if ct == "linear":
        p0 = curve.price_at_zero if curve.price_at_zero is not None else fallback_price
        p1 = curve.price_at_max if curve.price_at_max is not None else fallback_price
        n = curve.num_segments
        frac = 1.0 / n
        blocks = []
        for k in range(n):
            # Marginal cost = average price over the segment interval
            lo = k / n
            hi = (k + 1) / n
            mid = (lo + hi) / 2
            seg_price = p0 + (p1 - p0) * mid
            blocks.append(CostCurveBlock(fraction=frac, price=round(seg_price, 6)))
        return blocks

    if ct == "exponential":
        bp = curve.base_price if curve.base_price is not None else fallback_price
        sf = curve.scale_factor if curve.scale_factor is not None else 1.0
        n = curve.num_segments
        frac = 1.0 / n
        blocks = []
        for k in range(n):
            lo = k / n
            hi = (k + 1) / n
            mid = (lo + hi) / 2
            seg_price = bp * math.exp(sf * mid)
            blocks.append(CostCurveBlock(fraction=frac, price=round(seg_price, 6)))
        return blocks

    # Fallback
    return [CostCurveBlock(fraction=1.0, price=fallback_price)]


# =============================================================================
# GENERATOR AND STORAGE CONFIGURATION
# =============================================================================


class GeneratorConfig(BaseModel):
    """Generator unit configuration."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: Literal["Renewable", "Non-renewable", "Storage", "Electrolyzer"]
    fuel: str
    technology: Optional[str] = None
    reservable: bool = True

    # Per-node arrays (length = num_nodes)
    life_time: list[int] = Field(description="Lifetime per node (years)")
    initial_age: list[int] = Field(description="Initial age per node (years)")
    degradation_rate: list[float] = Field(description="Degradation rate per node (%/year)")
    decommissioning_cost: list[float] = Field(description="Decommissioning cost per node ($/MW)")
    rated_power: list[float] = Field(description="Installed capacity per node (MW)")
    min_power: list[float] = Field(description="Minimum output fraction per node")
    min_up: list[int] = Field(description="Minimum up time per node (hours)")
    min_down: list[int] = Field(description="Minimum down time per node (hours)")
    ramp_up: list[float] = Field(description="Ramp up rate per node (pu/min)")
    ramp_down: list[float] = Field(description="Ramp down rate per node (pu/min)")
    eff_at_rated: list[float] = Field(description="Efficiency at rated power per node")
    eff_at_min: list[float] = Field(description="Efficiency at minimum power per node")
    inertia: list[float] = Field(description="Inertia constant H per node (s)")
    droop: list[float] = Field(default_factory=lambda: [0.05], description="Governor droop characteristic per node (pu), e.g. 0.05 = 5%")
    governor_time_const: list[float] = Field(default_factory=lambda: [5.0], description="Governor time constant per node (seconds)")
    start_up_cost: list[float] = Field(description="Startup cost per node ($)")
    fuel_cost: list[float] = Field(description="Fuel cost per node ($/MWh)")
    fuel_cost_curve: Optional[list[CostCurveConfig]] = Field(
        default=None,
        description="Per-node bidding/offer curve for fuel cost. Overrides flat fuel_cost when present.",
    )
    fixed_cost: list[float] = Field(description="Fixed O&M cost per node ($/MWh)")
    maintenance_cost: list[float] = Field(description="Maintenance cost per node ($/MWh)")
    invest_cost: list[float] = Field(default_factory=lambda: [0.0], description="Investment cost per node ($/MW) [DEPRECATED: use technologies section]")
    invest_max_power: list[float] = Field(default_factory=lambda: [0.0], description="Max investment per node (MW) [DEPRECATED: use technologies section]")

    # Electrical properties
    frequency_hz: float = Field(default=50.0, gt=0, description="Operating frequency (Hz)")
    current_type: Literal["AC", "DC", "AC_DC"] = Field(default="AC", description="Current type")

    # Reactive power limits for ACOPF (optional, per node)
    # If empty, estimated from power_factor: Q_max = P_rated × tan(acos(pf))
    q_max_mvar: list[float] = Field(default_factory=list, description="Max reactive power per node (MVAr)")
    q_min_mvar: list[float] = Field(default_factory=list, description="Min reactive power per node (MVAr)")
    power_factor: float = Field(default=0.85, gt=0, le=1, description="Power factor for Q limit estimation when q_max/q_min not specified")

    # Availability profile (file path or null for default 1.0)
    availability_file: Optional[str] = Field(None, alias="Availability")

    # Physical bus anchoring. `bus_index` is a single global bus index
    # (legacy / single-bus units). `bus_id_per_node` maps node_idx → the
    # physical bus_id where this unit's capacity at that node connects —
    # required for multi-node fleets (each per-node piece sits at its own
    # real bus). When present it is authoritative; the operational DC-OPF
    # injects each node's capacity at its true physical bus (no node
    # aggregation / placement heuristics).
    bus_index: Optional[int] = Field(None, description="Global 0-based bus index (single-bus units)")
    bus_id_per_node: Optional[dict[int, str]] = Field(
        None, description="node_idx → physical bus_id for this unit's per-node capacity")

    # Reservoir hydroelectric (optional — empty lists = no reservoir)
    reservoir_capacity: list[float] = Field(default_factory=list, description="Reservoir capacity per node (MWh)")
    reservoir_initial_level: list[float] = Field(default_factory=list, description="Initial level fraction (0-1)")
    reservoir_min_level: list[float] = Field(default_factory=list, description="Min level fraction (0-1)")
    reservoir_max_level: list[float] = Field(default_factory=list, description="Max level fraction (0-1)")
    reservoir_inflow_file: Optional[str] = Field(None, description="Inflow time series file (MW-eq)")
    reservoir_turbine_efficiency: list[float] = Field(default_factory=list, description="Turbine efficiency (0-1)")
    reservoir_evaporation_rate: list[float] = Field(default_factory=list, description="Hourly evaporation rate")
    reservoir_pump_capacity: list[float] = Field(default_factory=list, description="Pump capacity per node (MW)")
    reservoir_pump_efficiency: list[float] = Field(default_factory=list, description="Pump efficiency (0-1)")
    reservoir_spillage_allowed: bool = Field(True, description="Allow reservoir spillage")
    reservoir_invest_cost: list[float] = Field(default_factory=list, description="Reservoir invest cost ($/MWh)")
    reservoir_invest_max: list[float] = Field(default_factory=list, description="Max reservoir expansion (MWh)")
    reservoir_min_release: list[float] = Field(
        default_factory=list,
        description="Mandatory minimum reservoir release per node (MW-eq) — "
        "ecological / minimum environmental flow. 0 = none.")

    cascade_downstream: str = Field(
        default="",
        description="Name of the downstream reservoir generator this unit "
        "discharges into (hydraulic cascade). Empty = terminal reservoir.")
    cascade_delay_hours: int = Field(
        default=0,
        ge=0,
        description="Water travel time (hours) before this unit's release "
        "reaches its cascade_downstream reservoir.")

    reservoir_head_min_factor: list[float] = Field(
        default_factory=list,
        description="Per-node turbine power-availability factor at the minimum "
        "reservoir level (0-1]. 1.0 = no head effect; below 1.0 the available "
        "power scales linearly with the fill level (head dependence).")

    risk_coefficient: list[float] = Field(
        default_factory=lambda: [1.0],
        description="Per-node geographic risk derating factor (0-1). "
        "Computed from hazard exposure and component fragility. "
        "1.0 = no risk derating; 0.82 = 18% capacity derating.",
    )


class BatteryConfig(BaseModel):
    """Battery/storage unit configuration."""

    name: str
    type: Literal["Storage"] = "Storage"
    fuel: str = "None"
    technology: Optional[str] = None
    reservable: bool = True
    spillage: bool = True
    min_duration_hours: Optional[int] = None
    max_duration_hours: Optional[int] = None

    # Per-node arrays
    life_time: list[int]
    initial_age: list[int]
    degradation_rate: list[float]
    decommissioning_cost: list[float]
    rated_power: list[float] = Field(description="Power rating per node (MW)")
    min_power: list[float]
    min_up: list[int]
    min_down: list[int]
    ramp_up: list[float]
    ramp_down: list[float]
    eff_at_rated: list[float]
    eff_at_min: list[float]
    inertia: list[float]
    start_up_cost: list[float]
    fuel_cost: list[float]
    fixed_cost: list[float]
    maintenance_cost: list[float]
    throughput_degradation_cost: Optional[list[float]] = Field(
        default=None,
        description="Degradation cost per MWh discharged ($/MWh). Represents battery wear from cycling. "
        "Note: a strictly positive value (e.g. 1.0) is required to prevent the LP from choosing "
        "degenerate dispatch where charge=discharge simultaneously every timestep (which produces "
        "a flat SOC and meaningless cycling activity)."
    )
    discharge_cost_curve: Optional[list[CostCurveConfig]] = Field(
        default=None,
        description="Per-node discharge cost curve. Overrides flat throughput_degradation_cost when present.",
    )
    invest_cost: list[float] = Field(default_factory=lambda: [0.0], description="Power investment cost ($/MW) [DEPRECATED: use battery_technologies]")
    invest_cost_energy: list[float] = Field(default_factory=lambda: [0.0], description="Energy investment cost ($/MWh) [DEPRECATED: use battery_technologies]")
    invest_max_power: list[float] = Field(default_factory=lambda: [0.0], description="Max power investment (MW) [DEPRECATED: use battery_technologies]")
    invest_max_capacity: list[float] = Field(default_factory=lambda: [0.0], description="Max capacity investment (MWh) [DEPRECATED: use battery_technologies]")

    # Storage-specific parameters
    efficiency_charge: list[float]
    efficiency_discharge: list[float]
    soc_initial: list[float] = Field(description="Initial SOC fraction per node")
    max_DoD: list[float] = Field(description="Max depth of discharge per node")
    capacity: list[float] = Field(description="Energy capacity per node (MWh)")
    MaxChargePower: list[float] = Field(description="Max charge power per node (MW)")
    MaxDischargePower: list[float] = Field(description="Max discharge power per node (MW)")

    # Electrical properties
    current_type: Literal["AC", "DC"] = Field(default="DC", description="Current type")

    availability_file: Optional[str] = Field(None, alias="Availability")

    # Physical bus anchoring (see GeneratorConfig for semantics).
    bus_index: Optional[int] = Field(None, description="Global 0-based bus index (single-bus units)")
    bus_id_per_node: Optional[dict[int, str]] = Field(
        None, description="node_idx → physical bus_id for this unit's per-node capacity")

    risk_coefficient: list[float] = Field(
        default_factory=lambda: [1.0],
        description="Per-node geographic risk derating factor (0-1). "
        "Computed from hazard exposure and component fragility. "
        "1.0 = no risk derating; 0.82 = 18% capacity derating.",
    )


class TechnologyConfig(BaseModel):
    """Candidate technology for new generation investment.

    Technologies define what CAN be built (investment candidates).
    Unlike generators (existing physical units), technologies represent
    the option to build new capacity of a given type.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    type: Literal["Renewable", "Non-renewable"]
    fuel: str
    invest_cost: list[float] = Field(description="Investment cost per node ($/MW)")
    invest_max_power: list[float] = Field(description="Max investment per node (MW)")
    availability_file: Optional[str] = Field(None, alias="Availability")
    eff_at_rated: list[float] = Field(description="Efficiency at rated power per node")
    degradation_rate: list[float] = Field(description="Degradation rate per node (%/year)")
    lifetime: int = Field(description="Economic lifetime (years)")
    min_output: list[float] = Field(default_factory=lambda: [0.0], description="Min output fraction per node")
    ramp_up: list[float] = Field(default_factory=lambda: [1.0], description="Ramp up rate per node (pu/min)")
    ramp_down: list[float] = Field(default_factory=lambda: [1.0], description="Ramp down rate per node (pu/min)")
    fuel_cost: list[float] = Field(default_factory=lambda: [0.0], description="Fuel cost per node ($/MWh)")
    fuel_cost_curve: Optional[list[CostCurveConfig]] = Field(
        default=None,
        description="Per-node bidding/offer curve for fuel cost. Overrides flat fuel_cost when present.",
    )
    fixed_cost: list[float] = Field(default_factory=lambda: [0.0], description="Fixed O&M cost per node ($/MWh)")
    maintenance_cost: list[float] = Field(default_factory=lambda: [0.0], description="Maintenance cost per node ($/MWh)")
    inertia: list[float] = Field(default_factory=lambda: [0.0], description="Inertia constant H per node (s)")
    droop: list[float] = Field(default_factory=lambda: [0.05], description="Governor droop characteristic per node (pu), e.g. 0.05 = 5%")
    governor_time_const: list[float] = Field(default_factory=lambda: [5.0], description="Governor time constant per node (seconds)")
    start_up_cost: list[float] = Field(default_factory=lambda: [0.0], description="Startup cost per node ($)")
    eff_at_min: list[float] = Field(default_factory=lambda: [0.0], description="Efficiency at min power per node")
    min_up: list[int] = Field(default_factory=lambda: [0], description="Min up time per node (hours)")
    min_down: list[int] = Field(default_factory=lambda: [0], description="Min down time per node (hours)")
    frequency_hz: float = Field(default=50.0, gt=0, description="Operating frequency (Hz)")
    current_type: Literal["AC", "DC", "AC_DC"] = Field(default="AC", description="Current type")
    reservable: bool = True
    decommissioning_cost: list[float] = Field(default_factory=lambda: [0.0], description="Decommissioning cost ($/MW)")
    risk_coefficient: list[float] = Field(
        default_factory=lambda: [1.0],
        description="Per-node geographic risk derating (0-1). Reduces effective capacity "
        "credit to account for hazard exposure. Computed from node hazard intensity "
        "and component fragility via the Risk Workbench.",
    )
    color: Optional[str] = Field(default=None, description="Display color hex code (e.g. '#FF0000') for results visualization")


class BatteryTechnologyConfig(BaseModel):
    """Candidate technology for new battery/storage investment."""

    name: str
    invest_cost_power: list[float] = Field(description="Power investment cost per node ($/MW)")
    invest_cost_energy: list[float] = Field(description="Energy investment cost per node ($/MWh)")
    invest_max_power: list[float] = Field(description="Max power investment per node (MW)")
    invest_max_capacity: list[float] = Field(description="Max capacity investment per node (MWh)")
    min_duration_hours: float = Field(default=1.0, ge=0, description="Min energy-to-power ratio (hours)")
    max_duration_hours: float = Field(default=24.0, ge=0, description="Max energy-to-power ratio (hours)")
    efficiency_charge: list[float] = Field(description="Charge efficiency per node (0-1)")
    efficiency_discharge: list[float] = Field(description="Discharge efficiency per node (0-1)")
    degradation_rate: list[float] = Field(description="Degradation rate per node (%/year)")
    lifetime: int = Field(description="Economic lifetime (years)")
    soc_initial: list[float] = Field(default_factory=lambda: [0.5], description="Initial SOC fraction per node")
    max_DoD: list[float] = Field(default_factory=lambda: [0.9], description="Max depth of discharge per node")
    maintenance_cost: list[float] = Field(default_factory=lambda: [0.0], description="Maintenance cost per node ($/MWh)")
    inertia: list[float] = Field(default_factory=lambda: [0.0], description="Inertia constant per node (s)")
    throughput_degradation_cost: list[float] = Field(
        default_factory=lambda: [1.0],
        description="Cycling wear cost ($/MWh discharged). A strictly positive value is required: "
        "with 0, the LP is free to dispatch charge=discharge simultaneously every hour with no "
        "energy net change, producing a degenerate flat-SOC solution. $1/MWh is typical for Li-ion."
    )
    spillage: bool = True
    current_type: Literal["AC", "DC"] = Field(default="DC", description="Current type")
    decommissioning_cost: list[float] = Field(default_factory=lambda: [0.0], description="Decommissioning cost ($/MW)")
    risk_coefficient: list[float] = Field(
        default_factory=lambda: [1.0],
        description="Per-node geographic risk derating (0-1). Reduces effective capacity "
        "credit to account for hazard exposure.",
    )
    color: Optional[str] = Field(default=None, description="Display color hex code (e.g. '#FF0000') for results visualization")


class ElectrolyzerConfig(BaseModel):
    """Electrolyzer configuration for hydrogen production."""

    name: str
    type: Literal["Electrolyzer"] = "Electrolyzer"
    fuel: str = "Hydrogen"
    technology: Literal["PEM", "Alkaline", "SOE"] = "PEM"

    life_time: list[int]
    initial_age: list[int]
    degradation_rate: list[float]
    rated_power: list[float]
    min_power: list[float]
    ramp_up: list[float]
    ramp_down: list[float]
    eff_at_rated: list[float]
    eff_at_min: list[float]
    energy_per_kg_h2: float = Field(default=50.0, description="kWh/kg H2")
    fixed_cost: list[float]
    variable_cost: list[float]
    water_cost: float = Field(default=0.001, description="$/kg H2")
    invest_cost: list[float]
    invest_max_power: list[float]


# =============================================================================
# PRIMARY ENERGY CONFIGURATION
# =============================================================================


class PrimaryEnergySourceConfig(BaseModel):
    """Primary energy source (fuel) supply configuration."""

    name: str
    unit: str
    max_availability: list[float] = Field(description="Max availability per node (unit/year)")
    import_cost: list[float] = Field(description="Import cost per node ($/unit)")
    storage_capacity: list[float] = Field(description="Storage capacity per node (units)")
    initial_storage_level: list[float] = Field(description="Initial storage fraction per node")
    min_storage_level: float = Field(default=0.1, ge=0, le=1)
    storage_investment_cost: float = Field(description="$/unit storage capacity")
    transport_cost: float = Field(description="$/unit/km transport cost")
    transport_losses: float = Field(description="Loss fraction per 100km")
    max_storage_investment_per_node: float
    max_transport_investment_per_arc: float


class ConversionTechnologyConfig(BaseModel):
    """Fuel-to-electricity conversion technology mapping."""

    fuel: str
    efficiency: Optional[float] = None
    units: dict[str, float] = Field(description="Unit key -> fraction mapping")


class FuelInfrastructureConfig(BaseModel):
    """Fuel infrastructure investment parameters."""

    transport_pipelines: dict[str, dict[str, Any]] = {}
    storage_facilities: dict[str, dict[str, Any]] = {}


# =============================================================================
# DEMAND CONFIGURATION
# =============================================================================


class DemandSectorConfig(BaseModel):
    """Electric demand sector configuration."""

    is_flexible: bool = False
    flexibility_ratio: float = Field(default=0.0, ge=0, le=1)
    criticality: Literal["critical", "high", "medium", "low"] = "medium"
    delay_tolerance: int = Field(default=0, ge=0, description="Max delay hours")
    price_sensitivity: float = Field(default=0.0, ge=0, le=1)


class NonElectricDemandConfig(BaseModel):
    """Non-electric fuel demand configuration."""

    fuel: str
    unit: str
    is_flexible: bool = False
    flexibility_ratio: float = 0.0
    criticality: Literal["critical", "high", "medium", "low"] = "medium"
    delay_tolerance: int = 0
    price_sensitivity: float = 0.0
    demand: list[int] = Field(description="Annual demand per node (units)")


# =============================================================================
# EV AND ROOFTOP SOLAR CONFIGURATION
# =============================================================================


class EVCategoryConfig(BaseModel):
    """Electric vehicle category configuration."""

    battery_capacity: float = Field(description="kWh")
    charging_power: float = Field(description="kW per vehicle")
    v2g_power: float = Field(description="kW per vehicle for V2G")
    v2g_participation: float = Field(ge=0, le=1)
    efficiency_charge: float = Field(ge=0, le=1)
    efficiency_discharge: float = Field(ge=0, le=1)
    min_soc: float = Field(ge=0, le=1)
    max_adoption: float = Field(default=35.0)
    growth_rate: float = Field(default=0.14)
    mid_point_fraction: float = Field(default=0.5, ge=0, le=1)
    daily_energy_kwh: Optional[float] = Field(
        default=None,
        description="Real daily charging energy per vehicle (kWh/day). The "
        "availability pattern is normalized so its daily integral equals this "
        "value, preventing the pattern from being read as continuous power "
        "draw. If None, defaults to battery_capacity × 0.12 (~12% daily "
        "depth-of-discharge).",
    )


class RooftopSolarConfig(BaseModel):
    """Rooftop solar simulation configuration."""

    adoption_scenario: Literal["low", "medium", "high"] = "medium"
    weather_variability: Literal["low", "normal", "high"] = "normal"
    simulation_seed: int = 42
    systems_per_node: list[int]
    avg_system_size: list[float] = Field(description="kW per system")
    performance_ratio: float = Field(default=0.75, ge=0, le=1)
    degradation_rate: float = Field(default=0.005, ge=0)
    cost_per_kw: float = 1200
    cost_reduction_rate: float = 0.08
    o_and_m_cost: float = 20
    base_year: int = 2025
    target_year: int = 2050
    initial_adoption: list[float]
    max_adoption: dict[str, float]
    adoption_rates: dict[str, float]


# =============================================================================
# STOCHASTIC SCENARIO CONFIGURATION
# =============================================================================


class ScenarioMultipliers(BaseModel):
    """Cost and demand multipliers for stochastic scenarios."""

    invest_cost_renewables: float = 1.0
    invest_cost_storage: float = 1.0
    invest_cost_conventional: float = 1.0
    invest_cost_transmission: float = 1.0
    fuel_cost: float = 1.0
    maintenance_cost: float = 1.0
    discount_rate: float = 1.0
    demand_growth: float = 1.0
    fuel_price_growth: float = 1.0
    carbon_price: float = 1.0


class StochasticScenarioConfig(BaseModel):
    """Stochastic programming scenario definition."""

    name: str
    probability: float = Field(ge=0, le=1)
    description: str = ""
    multipliers: ScenarioMultipliers = ScenarioMultipliers()


# =============================================================================
# RISK & RESILIENCE CONFIGURATION
# =============================================================================


class ClimateScenarioConfig(BaseModel):
    """Climate projection scenario (SSP pathway).

    Defines how renewable availability and demand change under a specific
    climate projection.  Each scenario carries a probability weight so that
    the stochastic master problem can optimise across multiple futures.
    """

    name: str
    probability: float = Field(ge=0, le=1)
    ssp_pathway: Literal["SSP1-2.6", "SSP2-4.5", "SSP3-7.0", "SSP5-8.5"] = "SSP2-4.5"
    gcm_model: str = Field(default="", description="GCM model name, e.g. 'ACCESS-CM2'")
    availability_suffix: str = Field(
        default="",
        description="Suffix appended to availability profile filenames "
        "(e.g. '_ssp245' → profile_sun_1_ssp245.csv)",
    )
    demand_scale: dict[int, float] = Field(
        default_factory=dict,
        description="Year → demand multiplier (e.g. {2040: 1.05, 2050: 1.12})",
    )
    temperature_delta: dict[int, float] = Field(
        default_factory=dict,
        description="Year → ΔT from baseline in °C (e.g. {2040: 0.8, 2050: 1.5})",
    )
    ghi_delta_fraction: dict[int, float] = Field(
        default_factory=dict,
        description="Year → fractional change in GHI (e.g. {2050: -0.02} = −2%)",
    )
    wind_speed_delta_fraction: dict[int, float] = Field(
        default_factory=dict,
        description="Year → fractional change in wind speed (e.g. {2050: -0.05} = −5%)",
    )


class FragilityCurveConfig(BaseModel):
    """Lognormal fragility curve parameters for a single damage state.

    The probability of reaching or exceeding the damage state given
    intensity measure *im* is:  P = Φ( (ln(im) − ln(im_median)) / beta )
    """

    damage_state: Literal["slight", "moderate", "extensive", "complete"] = "complete"
    im_median: float = Field(gt=0, description="Median intensity measure for this damage state")
    beta: float = Field(gt=0, le=2.0, description="Log-standard deviation (aleatory)")
    beta_epistemic: float = Field(
        default=0.0, ge=0, le=2.0,
        description="Epistemic uncertainty (β_u); total β = sqrt(β² + β_u²)",
    )
    source_quality: Literal[
        "empirical", "analytical", "expert_judgment", "proxy_derived",
    ] = Field(default="analytical", description="Quality classification of fragility source")
    description: str = ""


class ComponentFragilityConfig(BaseModel):
    """Fragility assignment for a component type under a specific hazard."""

    component_type: Literal[
        "solar_pv",
        "wind_turbine",
        "diesel_gen",
        "gas_turbine",
        "substation",
        "transmission_line",
        "battery",
        "transformer",
        "hydroelectric",
        "biomass",
        "otec",
        "electrolyzer",
    ]
    hazard_type: Literal[
        "earthquake",
        "cyclone",
        "flood",
        "tsunami",
        "wildfire",
        "volcanic",
        "sea_level_rise",
    ]
    curves: list[FragilityCurveConfig]
    source: str = Field(default="", description="Literature source, e.g. 'NHESS-2024'")


class HazardScenarioConfig(BaseModel):
    """Discrete disaster scenario for stochastic optimisation.

    Each scenario describes a single event (e.g. a 500-year tsunami at
    a specific location) together with the damage it inflicts on system
    components and the time required to restore service.
    """

    name: str
    probability: float = Field(ge=0, le=1, description="Annual occurrence probability")
    hazard_type: str = Field(default="", description="earthquake, cyclone, flood, …")
    year_of_occurrence: int = Field(
        default=0,
        description="Planning year when event strikes (0 = any year / steady-state)",
    )
    affected_nodes: list[int] = Field(default_factory=list)
    damage_fraction: dict[str, float] = Field(
        default_factory=dict,
        description="gen_key or bat_key → fraction of capacity lost (0-1)",
    )
    recovery_hours: int = Field(
        default=8760, ge=0,
        description="Hours to fully restore damaged capacity",
    )
    intensity_measure: float = Field(
        default=0.0,
        description="Event intensity (PGA in g, wind in m/s, flood depth in m, …)",
    )
    description: str = ""


class HazardDataSourceConfig(BaseModel):
    """Configuration for a hazard data source / API endpoint."""

    hazard_type: str = Field(description="earthquake, cyclone, flood, …")
    source: str = Field(
        description="Data provider key: usgs, gem, ibtracs, storm, "
        "fathom, wri_aqueduct, noaa_tsunami, firms, gvp, nasa_slr, thinkhazard",
    )
    return_periods: list[int] = Field(
        default=[100, 500],
        description="Return periods of interest (years)",
    )
    enabled: bool = True
    api_key: str = Field(default="", description="API key if required by the source")
    cache_dir: str = Field(default="", description="Local cache directory override")
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific options (e.g. dataset version, resolution)",
    )


class VOLLConfig(BaseModel):
    """Value of Lost Load by demand sector (USD/MWh)."""

    residential: float = Field(default=5_000.0, ge=0, description="$/MWh")
    commercial: float = Field(default=25_000.0, ge=0, description="$/MWh")
    industrial: float = Field(default=15_000.0, ge=0, description="$/MWh")
    critical: float = Field(default=100_000.0, ge=0, description="$/MWh")


class RiskCriteriaConfig(BaseModel):
    """Configurable risk acceptability thresholds per ISO 31000 §6.5.

    Implements the ALARP (As Low As Reasonably Practicable) framework:
      negligible  → no action needed
      tolerable   → ALARP zone — monitor, apply cost-benefit reduction
      intolerable → action required regardless of cost
    """

    eal_negligible: float = Field(
        default=1_000.0, ge=0,
        description="EAL below this ($/yr) is negligible risk",
    )
    eal_tolerable: float = Field(
        default=50_000.0, ge=0,
        description="EAL below this ($/yr) is tolerable (ALARP zone)",
    )
    eal_intolerable: float = Field(
        default=500_000.0, ge=0,
        description="EAL above this ($/yr) is intolerable — action required",
    )
    composite_risk_low: float = Field(default=0.01, ge=0, le=1)
    composite_risk_medium: float = Field(default=0.05, ge=0, le=1)
    composite_risk_high: float = Field(default=0.15, ge=0, le=1)


class RiskConfig(BaseModel):
    """Risk & Resilience Analysis configuration.

    When ``enabled`` is *False* (default), the entire risk module is skipped
    and no additional variables or constraints are added to the optimisation.
    """

    enabled: bool = False

    # ── Risk measure for optimisation ──────────────────────────────────
    risk_measure: Literal["expected", "cvar", "minimax_regret"] = Field(
        default="expected",
        description="Objective risk measure: expected (risk-neutral), "
        "cvar (Conditional Value-at-Risk), or minimax_regret",
    )
    cvar_alpha: float = Field(
        default=0.95, gt=0, lt=1,
        description="CVaR confidence level (0.95 → worst 5% of scenarios)",
    )
    cvar_lambda: float = Field(
        default=0.5, ge=0, le=1,
        description="Risk-aversion weight: 0 = risk-neutral, 1 = pure CVaR",
    )

    # ── Climate scenarios ──────────────────────────────────────────────
    climate_scenarios: list[ClimateScenarioConfig] = Field(default_factory=list)

    # ── Hazard scenarios ───────────────────────────────────────────────
    hazard_scenarios: list[HazardScenarioConfig] = Field(default_factory=list)

    # ── Hazard data sources (for automated fetching) ───────────────────
    hazard_data_sources: list[HazardDataSourceConfig] = Field(default_factory=list)

    # ── Fragility library overrides ────────────────────────────────────
    fragility_curves: list[ComponentFragilityConfig] = Field(default_factory=list)

    # ── Multi-hazard combination ───────────────────────────────────────
    combination_method: Literal["independent", "copula", "mcda"] = Field(
        default="independent",
        description="Method for combining multi-hazard failure probabilities",
    )

    # ── Risk acceptability criteria (ISO 31000 §6.5, ALARP) ──────────
    risk_criteria: RiskCriteriaConfig = Field(default_factory=RiskCriteriaConfig)

    # ── Value of Lost Load ─────────────────────────────────────────────
    voll: VOLLConfig = Field(default_factory=VOLLConfig)

    # ── Temperature-dependent demand ───────────────────────────────────
    demand_base_temperature: float = Field(
        default=24.0,
        description="Base temperature for HDD/CDD calculation (°C). "
        "Default 24°C is appropriate for tropical SIDS; use 18°C for temperate regions.",
    )
    demand_heating_coefficient: float = Field(
        default=0.5, ge=0,
        description="Heating demand sensitivity (%/°C below base temperature). "
        "Refs: Sailor & Munoz (1997). Typical 0.5 for tropical SIDS.",
    )
    demand_cooling_coefficient: float = Field(
        default=2.5, ge=0,
        description="Cooling demand sensitivity (%/°C above base temperature). "
        "Refs: Lam et al. (2018), IRENA (2019). Typical 2-4 for Caribbean.",
    )

    # ── Insurance ──────────────────────────────────────────────────────
    insurance_premium_rate: float = Field(
        default=0.0, ge=0,
        description="Annual insurance premium as fraction of asset replacement value",
    )

    # ── Post-optimisation Monte Carlo ──────────────────────────────────
    monte_carlo_samples: int = Field(default=1000, ge=100, le=100_000)
    monte_carlo_seed: int = Field(default=42, ge=0)

    @model_validator(mode="after")
    def _validate_climate_probabilities(self) -> "RiskConfig":
        """Ensure climate scenario probabilities sum to ~1.0 when present."""
        probs = [s.probability for s in self.climate_scenarios]
        if probs and abs(sum(probs) - 1.0) > 0.01:
            raise ValueError(
                f"Climate scenario probabilities must sum to 1.0, "
                f"got {sum(probs):.4f} from {len(probs)} scenarios"
            )
        return self


# =============================================================================
# SOLVER AND TEMPORAL CONFIGURATION
# =============================================================================


class SolverConfig(BaseModel):
    """Optimization solver configuration."""

    name: Literal[
        # LP / MIP solvers
        "highs", "cbc", "glpk", "gurobi", "cplex", "scip", "xpress",
        # Conic / nonlinear solvers required by the ACOPF formulations
        # (acopf_soc, acopf_qc, acopf_sdp need a conic backend;
        # acopf_polar/acopf_rect need Ipopt).
        "clarabel", "scs", "ipopt",
    ] = "highs"
    threads: int = Field(default=4, ge=1)
    time_limit: int = Field(default=10800, ge=0, description="Seconds (0 = unlimited)")
    gap: float = Field(default=0.01, ge=0, le=1, description="MIP optimality gap")
    verbose: bool = False
    scale_constraints: bool = True
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Solver-specific options (e.g. presolve, lp_method)",
    )


class TemporalConfig(BaseModel):
    """Temporal resolution and simulation settings."""

    resolution_hours: int = Field(default=1, ge=1, description="Hourly resolution")
    rolling_horizon_hours: int = Field(default=48, ge=1)
    overlap_hours: int = Field(default=6, ge=0)
    investment_resolution: int = Field(default=HOURS_STD_YEAR, description="Hours per investment period")
    primary_energy_resolution: int = Field(default=24, description="Hours")
    use_rolling_horizon: bool = True


class NetworkReductionConfig(BaseModel):
    """Internal (algebraic) network reduction for DCOPF performance.

    When enabled, the bus-level transmission topology is reduced in-memory
    before model construction via local transformations (parallel line
    merge, leaf pruning of passive stub buses, series collapse of passive
    degree-2 buses).  The original topology is preserved via a
    :class:`ReductionMap` so LP results (flows, angles, prices) are
    expanded back to every original bus and line after solving.

    All transformations preserve DC power flow *exactly*; they are not
    approximations.  Equipment (generators, batteries, transformers,
    converters) and per-node data are untouched.
    """

    enabled: bool = Field(
        default=False,
        description="Enable internal network reduction (Phase 1 + 2a)",
    )
    kron_deg3: bool = Field(
        default=False,
        description=(
            "Phase 2b: star-mesh (Kron) elimination for degree-3 non-protected "
            "junctions.  Keeps line count unchanged per elimination; may reduce "
            "lines further via subsequent parallel merges.  Skipped for degree ≥ 4 "
            "because the mesh size grows quadratically."
        ),
    )


class N1SecurityConfig(BaseModel):
    """N-1 security criteria configuration."""

    # N-1 is opt-in (enabled=False by default).  When enabled it applies to
    # BOTH development and unit_commitment: in development it should shape a
    # reliable investment plan; in unit_commitment it reports the fixed
    # fleet's security deficit.  The constraint is SOFT (penalised
    # shortfall), so including either mode can never make the LP infeasible.
    enabled: bool = False
    apply_to_modes: list[str] = Field(default=["development", "unit_commitment"])

    # Transmission N-1
    transmission_enabled: bool = True
    transmission_reserve_factor: float = Field(default=0.70, ge=0, le=1)
    critical_line_threshold: float = Field(default=0.0, ge=0)

    # Generation N-1
    generation_enabled: bool = True
    generation_reserve_type: Literal["largest_unit", "percentage"] = "largest_unit"
    generation_reserve_percentage: float = Field(default=0.15, ge=0, le=1)

    # SCOPF (Security-Constrained OPF) — iterative approach
    scopf_enabled: bool = Field(
        default=False,
        description=(
            "Use iterative SCOPF instead of preventive N-1 reserves. "
            "SCOPF adds post-contingency flow constraints using LODF "
            "factors, only for contingencies that cause violations."
        ),
    )
    scopf_max_iterations: int = Field(default=5, ge=1, le=20)
    scopf_violation_tolerance: float = Field(default=0.01, ge=0)

    # Corrective actions
    corrective_enabled: bool = Field(
        default=False,
        description=(
            "Allow corrective post-contingency actions (battery response, "
            "re-dispatch) instead of purely preventive reserves."
        ),
    )

    # N-k depth and analysis options
    contingency_depth: Literal["n1", "n1_1"] = Field(
        default="n1",
        description=(
            "Contingency analysis depth. 'n1' = single element outage, "
            "'n1_1' = sequential double outage (N-1-1)."
        ),
    )
    redistribution_mode: Literal["pro_rata", "droop"] = Field(
        default="pro_rata",
        description=(
            "How generation is redistributed after a contingency. "
            "'pro_rata' distributes proportionally to capacity, "
            "'droop' uses governor droop response characteristics."
        ),
    )
    pi_screening_threshold: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Performance Index threshold for contingency screening. "
            "0 = no screening (analyze all contingencies). Higher values "
            "filter out low-impact contingencies."
        ),
    )
    transformer_contingencies: bool = Field(
        default=False,
        description="Include transformer outages in N-1 contingency analysis.",
    )
    battery_contingencies: bool = Field(
        default=False,
        description=(
            "Include battery/storage outages in N-1 contingency analysis. "
            "Discharging batteries act as generation loss."
        ),
    )


class SporesObjective(str, Enum):
    """SPORES objective functions for near-optimal alternative generation.

    Each value labels a distinct objective that can be applied (one per
    alternative) under the same cost-slack envelope. The semantics:

    - ``hsj_diversity``: classical MGA Hop-Skip-Jump — maximise distance
      from previous alternatives by penalising techs/nodes already used.
      This is what the runner does today and what the ``mga`` method
      uses internally.
    - ``min_total_build``: minimise the sum of all new investments
      (MW + MWh) → "smallest near-optimal portfolio".
    - ``max_tech_equity``: equalise investment magnitudes across
      technologies (Gini-min over per-tech totals).
    - ``max_regional_equity``: equalise investment magnitudes across
      nodes (Gini-min over per-node totals) — the *spatially explicit*
      objective at the heart of SPORES.
    - ``evolutionary_dist``: maximise Euclidean distance in the
      (tech × node) decision vector from the cost-optimal solution.

    These are the canonical objectives from Lombardi et al. 2020
    (Calliope SPORES) plus the HSJ baseline kept for back-compat. They
    are added to the schema in Phase 1 of the SPORES roadmap; the Julia
    objective functions themselves arrive in Phase 2.
    """

    HSJ_DIVERSITY       = "hsj_diversity"
    MIN_TOTAL_BUILD     = "min_total_build"
    MAX_TECH_EQUITY     = "max_tech_equity"
    MAX_REGIONAL_EQUITY = "max_regional_equity"
    EVOLUTIONARY_DIST   = "evolutionary_dist"


class MGAConfig(BaseModel):
    """Configuration for near-optimal alternative generation.

    Two methods share most settings (cost slack ε, threshold) but
    differ in *how* alternatives are produced:

    - ``method="mga"`` (default, what the runner does today): one
      objective family — the classical MGA Hop-Skip-Jump diversity
      penalty — applied ``num_alternatives`` times sequentially.
    - ``method="spores"`` (Lombardi 2020 style): a *menu* of distinct
      objectives — see :class:`SporesObjective`. The runner solves
      one alternative per objective listed in ``objectives``.
      ``num_alternatives`` is ignored in this mode (it equals
      ``len(objectives)``).

    Phase 1 of the roadmap only introduces the schema fields; the
    Julia objective library and the bridge to dispatch on
    ``objectives`` arrive in Phases 2-4.
    """

    enabled: bool = Field(
        default=False,
        description="Enable MGA / SPORES to generate diverse near-optimal alternatives",
    )
    method: Literal["mga", "spores"] = Field(
        default="mga",
        description=(
            "Generation method. 'mga' runs the classical Hop-Skip-Jump "
            "loop num_alternatives times. 'spores' solves one alternative "
            "per objective listed in 'objectives'."
        ),
    )
    objectives: list[SporesObjective] = Field(
        default_factory=list,
        description=(
            "Objective menu for method='spores'. Each entry produces one "
            "alternative under the cost slack. Ignored when method='mga'."
        ),
    )
    num_alternatives: int = Field(
        default=10,
        ge=1,
        le=100,
        description=(
            "MGA only: number of diversity alternatives to generate "
            "(excludes cost-optimal). Ignored when method='spores' — "
            "len(objectives) drives the count there."
        ),
    )
    slack_fraction: float = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description="Near-optimal slack ε (e.g., 0.05 = allow up to 5% cost increase)",
    )
    investment_threshold: float = Field(
        default=0.1,
        ge=0.0,
        description="MW threshold to count as 'invested' for diversity scoring",
    )

    @model_validator(mode="after")
    def _check_method_consistency(self) -> "MGAConfig":
        """Surface a clear error when the method and objectives disagree.

        We don't auto-coerce: silently ignoring user-provided fields
        masks intent bugs in YAML configs. With method='spores' we
        require a non-empty objective list; with method='mga' we
        complain if the user populated 'objectives' (probably meant to
        switch method)."""
        if not self.enabled:
            return self
        if self.method == "spores" and not self.objectives:
            raise ValueError(
                "MGAConfig: method='spores' requires a non-empty "
                "'objectives' list. Choose at least one of: "
                + ", ".join(o.value for o in SporesObjective)
            )
        if self.method == "mga" and self.objectives:
            raise ValueError(
                "MGAConfig: 'objectives' is only valid with "
                "method='spores'. Either switch method to 'spores' or "
                "remove the 'objectives' entry."
            )
        return self


class MasterProblemConfig(BaseModel):
    """Master problem (capacity expansion) settings."""

    stochastic: bool = False
    representative_days: int = Field(default=5, ge=1)
    min_day_separation: int = Field(default=5, ge=1)

    # Master-problem solver method.
    #   "monolithic" — single model coupling investment + representative-day
    #                  operations (default).
    #   "benders"    — Benders decomposition: investment-only master with theta[y]
    #                  recourse and per-representative-day dispatch subproblems.
    #                  Beneficial for very large problems.
    solver_method: Literal["monolithic", "benders"] = Field(
        default="monolithic",
        description="Master-problem solver: 'monolithic' or 'benders'.",
    )
    benders_max_iterations: int = Field(
        default=50, ge=1,
        description="Maximum Benders iterations (benders solver only).",
    )
    benders_tolerance: float = Field(
        default=1e-4, gt=0.0,
        description="Relative optimality-gap tolerance for Benders convergence.",
    )
    benders_lol_penalty_cap: float = Field(
        default=1000.0, ge=0.0,
        description="Cap on the loss-of-load penalty ($/MW per timestep) inside "
        "Benders subproblems for numerical stability (0 disables the cap).",
    )

    # TSAM (Time-Series Aggregation Method)
    use_tsam: bool = Field(
        default=False,
        description="Enable TSAM clustering for representative period selection. "
        "When true, representative_days and min_day_separation are ignored.",
    )
    tsam_num_periods: int = Field(
        default=10, ge=2, le=365,
        description="Number of representative periods for TSAM clustering.",
    )
    tsam_method: Literal["kmedoids", "kmeans"] = Field(
        default="kmedoids",
        description="Clustering method for TSAM.",
    )
    tsam_inter_period_linking: bool = Field(
        default=True,
        description="Enable inter-period SOC linking for seasonal storage representation.",
    )

    # Operational dispatch mode
    use_uc_in_dispatch: bool = Field(
        default=False,
        description="Use unit commitment (binary gen_status, startup costs, "
        "min up/down times) in operational dispatch windows during "
        "development mode. Slower but more realistic operational costs.",
    )

    # Planning mode
    planning_mode: Literal["perfect_foresight", "myopic"] = Field(
        default="perfect_foresight",
        description="Investment planning mode. 'perfect_foresight' solves all years "
        "simultaneously (knows future costs, demand, and targets). 'myopic' solves "
        "year by year sequentially (only uses information available at decision time).",
    )

    # MGA
    mga: MGAConfig = Field(
        default_factory=MGAConfig,
        description="MGA near-optimal alternative generation settings",
    )


# =============================================================================
# SYSTEM CONFIGURATION
# =============================================================================


class SystemConfig(BaseModel):
    """Complete configuration for a single power system."""

    name: str
    demand_path: Optional[str] = None
    demand_paths: Optional[list[str]] = Field(
        default=None,
        description="Per-node demand file paths (one CSV per node). "
                    "Takes precedence over demand_path when set.",
    )
    demand_scale: float = Field(default=1.0, gt=0)

    # System parameters
    loss_demand_threshold: float = Field(default=0.05, ge=0, le=1, alias="LOSS_DEMAND_TRHESHOLD")
    life_extension_cost_factor: float = Field(default=0.20, ge=0)
    sim_rooftop: bool = Field(default=False, alias="SIM_ROOFTOP")
    target_re_penetration: float = Field(default=1.0, ge=0, le=1, alias="TARGET_RE_PENETRATION")
    min_annual_increment: float = Field(default=0.01, ge=0, alias="MIN_ANNUAL_INCREMENT")
    max_annual_increment: float = Field(default=0.10, ge=0, alias="MAX_ANNUAL_INCREMENT")
    max_annual_system_cost: float = Field(default=20e9, alias="MAX_ANNUAL_SYSTEM_COST")
    max_npv_penalty_per_mw: float = Field(default=1e6, alias="MAX_NPV_PENALTY_PER_MW")
    max_decommission_cost_per_mw: float = Field(default=5e5, alias="MAX_DECOMMISSION_COST_PER_MW")
    force_replacement: float = Field(default=-5e5, alias="FORCE_REPLACEMENT")
    discount_rate: float = Field(default=0.05, ge=0, le=1)
    base_lcoe: float = Field(default=93.0, ge=0)
    inertia_limit_threshold: float = Field(default=0.1, ge=0, alias="INERTIA_LIMIT_THRESHOLD")

    # Frequency stability analysis parameters
    load_damping: float = Field(default=0.01, ge=0, description="Load damping coefficient D (pu): fraction of demand that reduces per Hz deviation")
    frequency_nominal: float = Field(default=50.0, gt=0, description="Nominal system frequency (Hz)")
    rocof_limit: float = Field(default=2.0, gt=0, description="Maximum allowable ROCOF before protection trips (Hz/s)")
    frequency_nadir_limit: float = Field(default=49.0, gt=0, description="Minimum allowable frequency before UFLS (Hz)")

    # Configurable optimization parameters (previously hardcoded in Julia)
    soc_end_tolerance: float = Field(default=0.05, ge=0, le=0.5, description="Battery end-of-horizon SOC tolerance (±fraction)")
    min_cycling_ratio: float = Field(default=0.8, ge=0, le=1, description="Min battery cycling as fraction of capacity")
    min_cycling_period_days: float = Field(default=7.0, gt=0, description="Period for min cycling calculation (days)")
    reserve_static_default_ratio: float = Field(default=0.15, ge=0, le=1, description="Default static reserve as fraction of demand")
    flexible_demand_benefit_ratio: float = Field(default=0.5, ge=0, le=1, description="Fraction of price for flexible demand benefit")
    demand_shift_cost_rate: float = Field(default=0.1, ge=0, description="Cost rate per hour of demand shift distance")
    dynamic_reserve_contribution: float = Field(default=0.5, ge=0, le=1, description="Fraction of rated_power for dynamic reserve")
    reserve_margin: float = Field(default=1.15, ge=1.0, description="Capacity adequacy reserve margin (e.g. 1.15 = 15%)")
    npv_annual_return_rate: float = Field(default=0.15, ge=0, le=1, description="NPV revenue estimation rate for investments")

    # Power flow mode selection
    power_flow_mode: Literal[
        "dcopf",           # Pure DC optimal power flow (default)
        "dcopf_ac_verify", # DCOPF + Newton-Raphson AC verification (post-hoc)
        "acopf_soc",       # AC-OPF: Second-Order Cone relaxation (convex, HiGHS)
        "acopf_qc",        # AC-OPF: Quadratic Convex relaxation (tighter, HiGHS)
        "acopf_sdp",       # AC-OPF: Semidefinite Programming relaxation (tightest convex, SCS/Mosek)
        "acopf_polar",     # AC-OPF: Polar NLP exact formulation (Ipopt)
        "acopf_rect",      # AC-OPF: Rectangular NLP exact formulation (Ipopt)
    ] = Field(default="dcopf", description="Power flow formulation for operational dispatch")

    # DC power flow
    dc_power_flow: DCPowerFlowConfig = DCPowerFlowConfig()

    # AC power flow configuration (verification and ACOPF parameters)
    ac_power_flow: ACPowerFlowConfig = ACPowerFlowConfig()

    # Network
    nodes: NodeConfig
    buses: list[BusConfig] = []
    fuel_transport_distances: list[list[float]] = []
    fuel_transport_routes: list[dict] = Field(default_factory=list)

    # Fuels and penalties
    fuels: dict[str, FuelConfig] = {}
    penalties: PenaltiesConfig = PenaltiesConfig()
    co2_budget: CO2BudgetConfig = CO2BudgetConfig()
    criticality_penalties: CriticalityPenalties = CriticalityPenalties()

    # Demand
    electric_demand: dict[str, DemandSectorConfig] = {}
    sector_distribution: dict[int, dict[str, float]] = {}
    non_electric_demand: dict[str, NonElectricDemandConfig] = {}

    # Primary energy
    primary_energy_sources: dict[str, PrimaryEnergySourceConfig] = {}
    conversion_technologies: dict[str, ConversionTechnologyConfig] = {}
    non_electric_demand_growth: dict[str, float] = {}
    seasonal_factors: dict[str, dict[str, list[float]]] = {}
    fuel_infrastructure: FuelInfrastructureConfig = FuelInfrastructureConfig()

    # Generators (stored by unit key)
    generators: dict[str, GeneratorConfig] = {}

    # Batteries/storage (stored by unit key)
    batteries: dict[str, BatteryConfig] = {}

    # Candidate technologies for new investment
    technologies: dict[str, TechnologyConfig] = {}
    battery_technologies: dict[str, BatteryTechnologyConfig] = {}

    # Electrolyzers (stored by unit key)
    electrolyzers: dict[str, ElectrolyzerConfig] = {}

    # EV configuration
    ev_initial_soc: list[float] = Field(default=[], alias="EV_initial_soc")
    ev_categories: dict[str, EVCategoryConfig] = {}
    ev_quantity: dict[str, list[int]] = {}
    base_patterns: dict[str, list[float]] = {}

    # Rooftop solar
    rooftop_solar_config: Optional[RooftopSolarConfig] = None
    rooftop_max_potential: list[float] = []
    rooftop_solar_emission_reduction: float = 0.7

    # Stochastic scenarios
    stochastic_scenarios: list[StochasticScenarioConfig] = []

    # GIS metadata (optional, backward-compatible)
    map_center: Optional[GeoCoordinate] = Field(
        default=None, description="Map center for GUI visualization"
    )
    map_zoom: Optional[int] = Field(
        default=None, ge=1, le=20, description="Default map zoom level"
    )
    transmission_lines_geo: list[TransmissionLineGeo] = Field(
        default_factory=list, description="Geographic line routing metadata"
    )
    transformers: list[TransformerConfig] = Field(
        default_factory=list, description="Transformer definitions"
    )
    acdc_converters: list[ACDCConverterConfig] = Field(
        default_factory=list, description="AC/DC converter definitions"
    )
    freq_converters: list[FrequencyConverterConfig] = Field(
        default_factory=list, description="Frequency converter definitions"
    )
    development_zones: list[DevelopmentZoneConfig] = Field(
        default_factory=list, description="Technology development zones"
    )
    fuel_entry_points: list[FuelEntryPointConfig] = Field(
        default_factory=list, description="Fuel import entry points"
    )

    # GUI-only: equipment layout offsets for spatial round-trip fidelity
    gui_layout: Optional[dict[str, Any]] = Field(
        default=None, alias="_gui_layout",
        description="Per-instance equipment positions (latitude, longitude)"
    )

    # GUI-only: per-element visual styles (color/size/shape/opacity/width)
    gui_styles: Optional[dict[str, Any]] = Field(
        default=None, alias="_gui_styles",
        description="Per-element visual customization for the map editor"
    )

    # GUI-only: technology definitions for the editor
    gui_technologies: Optional[dict[str, Any]] = Field(
        default=None, alias="_technologies",
        description="Technology definitions for the Studio"
    )

    model_config = {"populate_by_name": True}

    @property
    def num_nodes(self) -> int:
        """Get number of nodes from network configuration."""
        return self.nodes.num_nodes

    @property
    def num_buses(self) -> int:
        return len(self.buses) if self.buses else self.num_nodes

    @model_validator(mode='after')
    def _ensure_buses(self) -> 'SystemConfig':
        """Auto-create one bus per node if no buses specified."""
        if not self.buses and self.nodes and self.nodes.num_nodes:
            n = self.nodes.num_nodes
            self.buses = [
                BusConfig(
                    bus_id=f"bus_{i}",
                    name=f"Bus {i}",
                    parent_node=i,
                    demand_fraction=1.0,
                )
                for i in range(n)
            ]
        return self

    @model_validator(mode='after')
    def _validate_unique_bus_ids(self) -> 'SystemConfig':
        """Bus IDs must be unique within a system.

        After config_to_gui_states populates `state.buses` as a dict
        keyed by bus_id, duplicates collapse silently (last wins) — so
        any duplicate in the source yaml causes invisible data loss
        (equipment attached to the dropped bus loses its reference).
        Catch it at load time before the dict shadowing happens.
        """
        seen: dict[str, int] = {}
        for i, b in enumerate(self.buses):
            if b.bus_id is None:
                continue
            if b.bus_id in seen:
                raise ValueError(
                    f"Duplicate bus_id {b.bus_id!r}: appears at positions "
                    f"{seen[b.bus_id]} and {i} in buses list. "
                    "bus_id must be unique within a system."
                )
            seen[b.bus_id] = i
        return self

    @model_validator(mode='after')
    def _validate_stochastic_probabilities(self) -> 'SystemConfig':
        """Validate that stochastic scenario probabilities sum to ~1.0."""
        if self.stochastic_scenarios:
            total = sum(sc.probability for sc in self.stochastic_scenarios)
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"Stochastic scenario probabilities must sum to 1.0, "
                    f"got {total:.4f} from {len(self.stochastic_scenarios)} scenarios"
                )
        return self


# =============================================================================
# META-NETWORK CONFIGURATION
# =============================================================================


class SystemLinkConfig(BaseModel):
    """Inter-system transmission link configuration."""

    systems: list[str] = Field(description="Pair of connected systems")
    connections: list[list[int]] = Field(description="Node pairs [[n1_sys1, n1_sys2], ...]")
    existing_capacity_mw: list[float] = Field(alias="existing_capacity_MW")
    max_investment_mw: list[float] = Field(alias="max_investment_MW")
    investment_cost_per_mw: list[float] = Field(alias="investment_cost_per_MW")
    loss_factor: list[float]
    distance_km: list[float]
    cost_per_mw_km: list[float]
    reactance_pu: list[float] = Field(
        default_factory=list,
        description="Series reactance per link (p.u.) for DC-OPF PWL losses",
    )
    resistance_pu: list[float] = Field(
        default_factory=list,
        description="Series resistance per link (p.u.) for DC-OPF PWL losses",
    )
    # GUI-only metadata: per-link map geometry. The solver does not
    # consume these, but the GUI needs them to redraw the polyline after
    # a save→load round-trip.
    #   waypoints[i]: list of {"lat": float, "lng": float} dicts for link i
    #   endpoints[i]: [from_endpoint_dict, to_endpoint_dict] where each
    #                 dict is {"element_type": str, "element_id": str}
    waypoints: list[list[dict]] = Field(
        default_factory=list,
        description="GUI-only: per-link waypoints (list of {lat, lng}) for map polyline",
    )
    endpoints: list[list[dict]] = Field(
        default_factory=list,
        description="GUI-only: per-link [from, to] endpoint refs "
                    "({element_type, element_id}) for stable map redraw",
    )
    # GUI-only metadata: extra electrical properties the link form
    # exposes for parity with LineForm. The solver still consumes only
    # the canonical fields above (capacity, distance, reactance_pu,
    # resistance_pu) — these are persisted so the properties form
    # round-trips visually.
    voltage_kv: list[float] = Field(default_factory=list)
    line_type: list[str] = Field(default_factory=list)
    length_km: list[float] = Field(default_factory=list)
    base_impedance: list[float] = Field(default_factory=list)
    reactance_per_km: list[float] = Field(default_factory=list)
    susceptance_pu: list[float] = Field(default_factory=list)
    num_circuits: list[int] = Field(default_factory=list)
    frequency_hz: list[float] = Field(default_factory=list)
    current_type: list[str] = Field(default_factory=list)
    decorative: list[bool] = Field(default_factory=list)
    # Visual style (color/width/opacity) per link
    style: list[dict] = Field(
        default_factory=list,
        description="GUI-only: per-link {color, width, opacity} for the map polyline",
    )

    model_config = {"populate_by_name": True}


class LoggingConfig(BaseModel):
    """Console / file logging configuration for ESFEX.

    ``console_level`` controls how chatty the run console is:

    * ``"basic"`` — milestones only (Year completed, Step N, etc.) plus
      WARNING and above. Designed for the GUI run console to avoid
      flooding the user with per-bus / per-window updates.
    * ``"verbose"`` — every INFO record.
    * ``"debug"`` — every record, including DEBUG.

    The on-disk log file always records DEBUG independent of this
    setting (see :func:`esfex.logging_config.setup_file_logging`).
    """

    console_level: Literal["basic", "verbose", "debug"] = "basic"


class MetaNetworkConfig(BaseModel):
    """Multi-system network configuration."""

    systems: list[str] = Field(description="List of system names to include")
    systems_links: list[SystemLinkConfig] = []
    dynamic_transfer_pricing: bool = True
    inter_system_loss_segments: int = Field(
        default=2,
        ge=0,
        le=5,
        description="PWL segments for inter-system link losses (0=linear fallback)",
    )


# =============================================================================
# TOP-LEVEL CONFIGURATION
# =============================================================================


class ESFEXConfig(BaseModel):
    """
    Top-level ESFEX configuration.

    Contains all global settings and system definitions.
    """

    # Simulation mode
    simulation_mode: Literal[
        "development",       # Full capacity expansion (master) + operations
        "unit_commitment",   # Skip master, run operations with binary commitment
        "economic_dispatch", # Skip master, run pure LP/conic operations (ACOPF studies)
    ] = "development"
    unit_commitment_hours: int = Field(default=24, ge=1)
    date_start: str = "01/01/2025 00:00"

    # Temporal settings
    temporal: TemporalConfig = TemporalConfig()

    # Solver settings
    solver: SolverConfig = SolverConfig()

    # Internal network reduction (DCOPF speedup, fully reversible)
    network_reduction: NetworkReductionConfig = NetworkReductionConfig()

    # N-1 security
    n1_security: N1SecurityConfig = N1SecurityConfig()

    # Master problem settings
    master_problem: MasterProblemConfig = MasterProblemConfig()

    # Enable primary energy modeling
    enable_primary_energy: bool = True

    # Multi-system network
    meta_network: MetaNetworkConfig

    # System definitions (by name)
    systems: dict[str, SystemConfig]

    # Plugin-specific configuration (keyed by plugin name)
    plugins: dict[str, Any] = Field(default_factory=dict)

    # Risk & Resilience Analysis
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Console / file logging verbosity
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def validate_systems_exist(self) -> "ESFEXConfig":
        """Validate that all systems referenced in meta_network exist."""
        for system_name in self.meta_network.systems:
            if system_name not in self.systems:
                raise ValueError(
                    f"System '{system_name}' referenced in meta_network but not defined in systems"
                )
        return self

    @property
    def primary_system(self) -> SystemConfig:
        """Get the first/primary system configuration."""
        first_name = self.meta_network.systems[0]
        return self.systems[first_name]

    def get_system(self, name: str) -> SystemConfig:
        """Get a specific system configuration by name."""
        if name not in self.systems:
            raise KeyError(f"System '{name}' not found. Available: {list(self.systems.keys())}")
        return self.systems[name]
