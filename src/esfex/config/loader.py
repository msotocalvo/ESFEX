"""
Configuration loader for ESFEX.

Loads YAML configuration files and validates them against Pydantic schemas.
Supports both single-file and multi-file configurations.
"""

import logging
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import ValidationError

log = logging.getLogger(__name__)

from esfex.config.schema import (
    BatteryConfig,
    BatteryTechnologyConfig,
    CO2BudgetConfig,
    ConversionTechnologyConfig,
    CriticalityPenalties,
    DCPowerFlowConfig,
    DemandSectorConfig,
    DevelopmentZoneConfig,
    ElectrolyzerConfig,
    EVCategoryConfig,
    FuelConfig,
    FuelEntryPointConfig,
    FuelInfrastructureConfig,
    GeoCoordinate,
    GeneratorConfig,
    MasterProblemConfig,
    MetaNetworkConfig,
    N1SecurityConfig,
    NodeConfig,
    NonElectricDemandConfig,
    PenaltiesConfig,
    PrimaryEnergySourceConfig,
    ESFEXConfig,
    RooftopSolarConfig,
    ScenarioMultipliers,
    SolverConfig,
    StochasticScenarioConfig,
    SystemConfig,
    SystemLinkConfig,
    TechnologyConfig,
    TemporalConfig,
    TransformerConfig,
    TransmissionLineGeo,
)


class ConfigLoadError(Exception):
    """Exception raised when configuration loading fails."""

    pass


def load_yaml(path: Union[str, Path]) -> dict[str, Any]:
    """
    Load a YAML file and return its contents as a dictionary.

    Args:
        path: Path to the YAML file

    Returns:
        Dictionary with YAML contents

    Raises:
        ConfigLoadError: If file cannot be read or parsed
    """
    path = Path(path)

    if not path.exists():
        raise ConfigLoadError(f"Configuration file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Invalid YAML in {path}: {e}")
    except IOError as e:
        raise ConfigLoadError(f"Cannot read {path}: {e}")

    if content is None:
        content = {}

    return content


def _convert_dc_power_flow(system_data: dict[str, Any]) -> DCPowerFlowConfig:
    """Extract DC power flow config from system data.

    Reads from nested ``dc_power_flow:`` dict first, then falls back to flat
    ``dc_*`` keys for backward compatibility.
    """
    nested = system_data.get("dc_power_flow", {})
    if not isinstance(nested, dict):
        nested = {}
    return DCPowerFlowConfig(
        base_impedance=nested.get("base_impedance",
            system_data.get("dc_base_impedance", system_data.get("DC_BASE_IMPEDANCE", 100.0))),
        reactance_per_km=nested.get("reactance_per_km",
            system_data.get("dc_reactance_per_km", system_data.get("DC_REACTANCE_PER_KM", 0.4))),
        voltage_level_kv=nested.get("voltage_level_kv",
            system_data.get("dc_voltage_level_kv", system_data.get("DC_VOLTAGE_LEVEL_KV", 220.0))),
        max_angle_diff_deg=nested.get("max_angle_diff_deg",
            system_data.get("dc_max_angle_diff_deg", system_data.get("DC_MAX_ANGLE_DIFF_DEG", 30.0))),
        slack_bus=nested.get("slack_bus",
            system_data.get("dc_slack_bus", system_data.get("DC_SLACK_BUS", 0))),
        loss_model=nested.get("loss_model", "pwl"),
        pwl_loss_segments=nested.get("pwl_loss_segments", 3),
        pwl_loss_segments_master=nested.get("pwl_loss_segments_master", 2),
    )


def _convert_fuels(fuels_data: dict[str, dict]) -> dict[str, FuelConfig]:
    """Convert fuel definitions to FuelConfig objects."""
    result = {}
    for name, data in fuels_data.items():
        result[name] = FuelConfig(
            name=data.get("name", name),
            unit=data.get("unit"),
            emission_factor=data.get("emission_factor", 0.0),
            energy_content=data.get("energy_content"),
            price_base=data.get("price_base", 0.0),
            price_growth_rate=data.get("price_growth_rate", 0.0),
        )
    return result


def _convert_generator(unit_key: str, data: dict[str, Any]) -> GeneratorConfig:
    """Convert a generator unit definition to GeneratorConfig."""
    kwargs: dict[str, Any] = dict(
        name=data.get("name", unit_key),
        type=data["type"],
        fuel=data.get("fuel", "None"),
        technology=data.get("technology"),
        reservable=data.get("reservable", True),
        life_time=data["life_time"],
        initial_age=data["initial_age"],
        degradation_rate=data["degradation_rate"],
        decommissioning_cost=data["decommissioning_cost"],
        rated_power=data["rated_power"],
        min_power=data["min_power"],
        min_up=data["min_up"],
        min_down=data["min_down"],
        ramp_up=data["ramp_up"],
        ramp_down=data["ramp_down"],
        eff_at_rated=data["eff_at_rated"],
        eff_at_min=data["eff_at_min"],
        inertia=data["inertia"],
        start_up_cost=data["start_up_cost"],
        fuel_cost=data["fuel_cost"],
        fixed_cost=data["fixed_cost"],
        maintenance_cost=data["maintenance_cost"],
        invest_cost=data["invest_cost"],
        invest_max_power=data["invest_max_power"],
        availability_file=data.get("Availability"),
        frequency_hz=data.get("frequency_hz", 50.0),
        current_type=data.get("current_type", "AC"),
    )
    # Pass through reservoir fields if present
    _reservoir_fields = [
        "reservoir_capacity", "reservoir_initial_level", "reservoir_min_level",
        "reservoir_max_level", "reservoir_turbine_efficiency",
        "reservoir_evaporation_rate", "reservoir_pump_capacity",
        "reservoir_pump_efficiency", "reservoir_spillage_allowed",
        "reservoir_invest_cost", "reservoir_invest_max", "reservoir_inflow_file",
    ]
    for field in _reservoir_fields:
        if field in data:
            kwargs[field] = data[field]
    return GeneratorConfig(**kwargs)


def _convert_battery(unit_key: str, data: dict[str, Any]) -> BatteryConfig:
    """Convert a storage unit definition to BatteryConfig."""
    return BatteryConfig(
        name=data.get("name", unit_key),
        type="Storage",
        fuel=data.get("fuel", "None"),
        reservable=data.get("reservable", True),
        spillage=data.get("spillage", True),
        min_duration_hours=data.get("min_duration_hours"),
        max_duration_hours=data.get("max_duration_hours"),
        life_time=data["life_time"],
        initial_age=data["initial_age"],
        degradation_rate=data["degradation_rate"],
        decommissioning_cost=data["decommissioning_cost"],
        rated_power=data["rated_power"],
        min_power=data["min_power"],
        min_up=data["min_up"],
        min_down=data["min_down"],
        ramp_up=data["ramp_up"],
        ramp_down=data["ramp_down"],
        eff_at_rated=data["eff_at_rated"],
        eff_at_min=data["eff_at_min"],
        inertia=data["inertia"],
        start_up_cost=data["start_up_cost"],
        fuel_cost=data["fuel_cost"],
        fixed_cost=data["fixed_cost"],
        maintenance_cost=data["maintenance_cost"],
        invest_cost=data["invest_cost"],
        invest_cost_energy=data.get("invest_cost_energy", data["invest_cost"]),
        invest_max_power=data["invest_max_power"],
        invest_max_capacity=data.get("invest_max_capacity", [0] * len(data["rated_power"])),
        efficiency_charge=data["efficiency_charge"],
        efficiency_discharge=data["efficiency_discharge"],
        soc_initial=data["soc_initial"],
        max_DoD=data["max_DoD"],
        capacity=data["capacity"],
        MaxChargePower=data["MaxChargePower"],
        MaxDischargePower=data["MaxDischargePower"],
        availability_file=data.get("Availability"),
        current_type=data.get("current_type", "DC"),
    )


def _convert_primary_energy_source(name: str, data: dict[str, Any]) -> PrimaryEnergySourceConfig:
    """Convert primary energy source definition."""
    return PrimaryEnergySourceConfig(
        name=data.get("name", name),
        unit=data["unit"],
        max_availability=data["max_availability"],
        import_cost=data["import_cost"],
        storage_capacity=data["storage_capacity"],
        initial_storage_level=data["initial_storage_level"],
        min_storage_level=data.get("min_storage_level", 0.1),
        storage_investment_cost=data["storage_investment_cost"],
        transport_cost=data["transport_cost"],
        transport_losses=data["transport_losses"],
        max_storage_investment_per_node=data["max_storage_investment_per_node"],
        max_transport_investment_per_arc=data["max_transport_investment_per_arc"],
    )


def _convert_system(system_data: dict[str, Any]) -> SystemConfig:
    """Convert a system dictionary to SystemConfig."""
    # Extract generators and batteries
    generators = {}
    batteries = {}

    # Look for unit_* and bat_* keys (legacy format)
    _valid_gen_types = {"Renewable", "Non-renewable", "Electrolyzer"}
    for key, value in list(system_data.items()):
        if (key.startswith("unit_") or key.startswith("bat_")) and isinstance(value, dict):
            unit_type = value.get("type", "Unknown")
            if unit_type == "Storage":
                batteries[key] = _convert_battery(key, value)
            elif unit_type in _valid_gen_types:
                generators[key] = _convert_generator(key, value)
            else:
                # Sanitize unknown types (e.g. "Thermal") to "Non-renewable"
                log.warning("Generator %s has unknown type '%s', treating as Non-renewable", key, unit_type)
                value["type"] = "Non-renewable"
                generators[key] = _convert_generator(key, value)
            # Remove from system_data to avoid passing to SystemConfig
            del system_data[key]

    # Handle pre-converted generators/batteries dictionaries
    if "generators" in system_data and isinstance(system_data["generators"], dict):
        for key, value in system_data["generators"].items():
            if isinstance(value, dict):
                # Sanitize unknown generator types
                gt = value.get("type", "Non-renewable")
                if gt not in ("Renewable", "Non-renewable", "Storage", "Electrolyzer"):
                    log.warning("Generator %s has unknown type '%s', treating as Non-renewable", key, gt)
                    value["type"] = "Non-renewable"
                generators[key] = _convert_generator(key, value)
        del system_data["generators"]

    if "batteries" in system_data and isinstance(system_data["batteries"], dict):
        for key, value in system_data["batteries"].items():
            if isinstance(value, dict):
                batteries[key] = _convert_battery(key, value)
        del system_data["batteries"]

    # Convert fuels
    if "fuels" in system_data and isinstance(system_data["fuels"], dict):
        system_data["fuels"] = _convert_fuels(system_data["fuels"])

    # Convert DC power flow
    system_data["dc_power_flow"] = _convert_dc_power_flow(system_data)

    # Convert primary energy sources
    if "primary_energy_sources" in system_data:
        pes = {}
        for name, data in system_data["primary_energy_sources"].items():
            if isinstance(data, dict):
                pes[name] = _convert_primary_energy_source(name, data)
        system_data["primary_energy_sources"] = pes

    # Convert nodes
    if "nodes" in system_data and isinstance(system_data["nodes"], dict):
        system_data["nodes"] = NodeConfig(**system_data["nodes"])

    # Convert penalties
    if "penalties" in system_data and isinstance(system_data["penalties"], dict):
        # Build case-insensitive lookup: normalize all keys to lowercase
        pen_raw = system_data["penalties"]
        pen_data = {k.lower(): v for k, v in pen_raw.items()}
        system_data["penalties"] = PenaltiesConfig(
            loss_of_load=pen_data.get("loss_of_load", 10e6),
            loss_of_reserve_static=pen_data.get("loss_of_reserve_static", 100),
            loss_of_reserve_dynamic=pen_data.get("loss_of_reserve_dynamic", 100),
            loss_of_inertia=pen_data.get("loss_of_inertia", 200),
            transfer_margin=pen_data.get("transfermargin", pen_data.get("transfer_margin", 100)),
            curtailment=pen_data.get("curtailment", 100),
            curtailment_cost=pen_data.get("curtailment_cost", 20.0),
            curtailment_excess_penalty=pen_data.get("curtailment_excess_penalty", 500.0),
            re_excess_penalty=pen_data.get("re_excess_penalty", 100.0),
            max_curtailment_ratio=pen_data.get("max_curtailment_ratio", 0.05),
            rooftop_curtailment=pen_data.get("rooftop_curtailment", 5),
            co2_cost=pen_data.get("co2_cost", 10),
            co2_budget_violation=pen_data.get("co2_budget_violation", 500),
            fre_penetration_loss=pen_data.get("fre_penetration_loss", 100),
            ev_loss=pen_data.get("ev_loss", 10),
            loss_of_fuel_supply=pen_data.get("loss_of_fuel_supply", 100),
            transport_congestion=pen_data.get("transport_congestion", 100),
            storage_violation=pen_data.get("storage_violation", 100),
            non_electric_demand_loss=pen_data.get("non_electric_demand_loss", 100),
        )

    # Convert CO2 budget
    if "co2_budget" in system_data and isinstance(system_data["co2_budget"], dict):
        system_data["co2_budget"] = CO2BudgetConfig(**system_data["co2_budget"])

    # Convert electric demand sectors
    if "electric_demand" in system_data and isinstance(system_data["electric_demand"], dict):
        ed = {}
        for sector, data in system_data["electric_demand"].items():
            if isinstance(data, dict):
                ed[sector] = DemandSectorConfig(**data)
        system_data["electric_demand"] = ed

    # Convert EV categories
    if "ev_categories" in system_data and isinstance(system_data["ev_categories"], dict):
        ev = {}
        for cat, data in system_data["ev_categories"].items():
            if isinstance(data, dict):
                ev[cat] = EVCategoryConfig(**data)
        system_data["ev_categories"] = ev

    # Convert rooftop solar config
    if "rooftop_solar_config" in system_data and isinstance(system_data["rooftop_solar_config"], dict):
        system_data["rooftop_solar_config"] = RooftopSolarConfig(**system_data["rooftop_solar_config"])

    # Convert stochastic scenarios
    if "stochastic_scenarios" in system_data and isinstance(system_data["stochastic_scenarios"], list):
        scenarios = []
        for sc in system_data["stochastic_scenarios"]:
            if isinstance(sc, dict):
                if "multipliers" in sc and isinstance(sc["multipliers"], dict):
                    sc["multipliers"] = ScenarioMultipliers(**sc["multipliers"])
                scenarios.append(StochasticScenarioConfig(**sc))
        system_data["stochastic_scenarios"] = scenarios

    # Convert candidate technologies for new investment
    if "technologies" in system_data and isinstance(system_data["technologies"], dict):
        techs = {}
        for key, data in system_data["technologies"].items():
            if isinstance(data, dict):
                if "name" not in data:
                    data["name"] = key
                techs[key] = TechnologyConfig(**data)
        system_data["technologies"] = techs

    if "battery_technologies" in system_data and isinstance(system_data["battery_technologies"], dict):
        bat_techs = {}
        for key, data in system_data["battery_technologies"].items():
            if isinstance(data, dict):
                if "name" not in data:
                    data["name"] = key
                bat_techs[key] = BatteryTechnologyConfig(**data)
        system_data["battery_technologies"] = bat_techs

    # Remove DC_ prefixed keys (already converted into dc_power_flow)
    # Keep dc_power_flow itself (now a DCPowerFlowConfig object)
    keys_to_remove = [k for k in system_data.keys()
                       if (k.startswith("DC_") or k.startswith("dc_")) and k != "dc_power_flow"]
    for k in keys_to_remove:
        del system_data[k]

    # Convert electrolyzer(s) to dict format
    electrolyzers = {}
    # Handle singular "electrolyzer" key (legacy / GUI-generated)
    if "electrolyzer" in system_data:
        el_data = system_data.pop("electrolyzer")
        if isinstance(el_data, dict):
            electrolyzers["electrolyzer"] = ElectrolyzerConfig(**el_data)
    # Handle plural "electrolyzers" dict
    if "electrolyzers" in system_data:
        el_dict = system_data.pop("electrolyzers")
        if isinstance(el_dict, dict):
            for key, val in el_dict.items():
                if isinstance(val, dict):
                    electrolyzers[key] = ElectrolyzerConfig(**val)
                elif isinstance(val, ElectrolyzerConfig):
                    electrolyzers[key] = val

    # Sanitize bus current_type before Pydantic validation
    if "buses" in system_data and isinstance(system_data["buses"], list):
        for bus_data in system_data["buses"]:
            if isinstance(bus_data, dict):
                ct = bus_data.get("current_type")
                if ct is not None and ct not in ("AC", "DC"):
                    log.warning("Bus has invalid current_type '%s', defaulting to AC", ct)
                    bus_data["current_type"] = "AC"

    # === Link gen.fuel → fuels[fuel].price_base when gen.fuel_cost is missing ===
    # Generators in the YAML often declare `fuel: Gas/Fuel_oil/Diesel/...` without
    # an explicit `fuel_cost`. The legacy loader leaves fuel_cost = [0.0]*N in that
    # case, so the operational LP sees thermal generation as free and never
    # dispatches investment renewables that have a nonzero marginal cost. Compute
    # fuel_cost = price_base / energy_content / efficiency from the fuels block
    # (same formula already in runner._fuel_based_cost, applied post-hoc for LCOE).
    _fuels_dict = system_data.get("fuels", {})
    _techs_dict = system_data.get("technologies", {})
    # Lazard 2024 LCOE 17.0 midpoints minus typical fuel cost — the non-fuel
    # portion (capex amortized + fixed + var O&M). Without this thermals look
    # artificially cheap (fuel only ~$16-32/MWh) compared to renewable LCOE
    # ($34+) and the LP never dispatches/invests in renewables.
    _NON_FUEL_LCOE = {
        "Gas": 60.0,        # CCGT $76 − $16 fuel
        "Fuel_oil": 55.0,   # Steam HFO $80 − $27
        "Oil": 55.0,
        "Diesel": 60.0,     # Steam diesel (peakers are higher but rare here)
        "Biomass": 20.0,    # Mostly fuel; add modest
    }

    if isinstance(_fuels_dict, dict) and _fuels_dict:
        _linked_fuel = 0
        _linked_maint = 0
        _silent_freebies: list[str] = []  # non-renewable gens that end up at $0/MWh
        for _gk, _g in generators.items():
            if getattr(_g, "type", "") == "Renewable":
                continue  # Sun/Wind/Water have no fuel cost
            _fuel_name = getattr(_g, "fuel", None) or "None"
            # Flag the silent-failure mode: a non-renewable generator with
            # no fuel reference ends up at fuel_cost=0 AND 0 emissions in the
            # LP (no key in fuel_co2). The LP then dispatches it as free with
            # zero CO2 — invisible bug at solve time. Loud warning here so
            # the user sees it before solving.
            if _fuel_name in ("None", "", None):
                _fc_now = list(getattr(_g, "fuel_cost", None) or [])
                if not _fc_now or max(_fc_now) <= 0:
                    _silent_freebies.append(getattr(_g, "name", _gk))
                continue
            _fuel = _fuels_dict.get(_fuel_name)
            if _fuel is None:
                _fc_now = list(getattr(_g, "fuel_cost", None) or [])
                if not _fc_now or max(_fc_now) <= 0:
                    _silent_freebies.append(
                        f"{getattr(_g, 'name', _gk)} (fuel={_fuel_name!r} not in fuels)"
                    )
                continue

            # --- Part 1: compute fuel_cost from fuels block if missing ---
            _fc_existing = list(getattr(_g, "fuel_cost", None) or [])
            if not _fc_existing or max(_fc_existing) <= 0:
                _price = float(getattr(_fuel, "price_base", 0.0) or 0.0)
                _energy = float(getattr(_fuel, "energy_content", 0.0) or 0.0)
                if _price > 0 and _energy > 0:
                    _eff_list = list(getattr(_g, "eff_at_rated", None) or [])
                    _tech_eff = 0.0
                    _tech_id = getattr(_g, "technology", None)
                    if _tech_id and isinstance(_techs_dict, dict):
                        _tech = _techs_dict.get(_tech_id)
                        if _tech is not None:
                            _te = list(getattr(_tech, "eff_at_rated", None) or [])
                            if _te and _te[0] > 0:
                                _tech_eff = float(_te[0])
                    _rp = list(getattr(_g, "rated_power", None) or [])
                    _n = max(len(_rp), len(_fc_existing), 1)
                    _new_fc = []
                    for _i in range(_n):
                        _eff = float(_eff_list[_i]) if _i < len(_eff_list) and _eff_list[_i] > 0 else _tech_eff
                        _new_fc.append(_price / _energy / _eff if _eff > 0 else 0.0)
                    try:
                        _g.fuel_cost = _new_fc
                        _linked_fuel += 1
                    except Exception as _exc:
                        log.warning("Cannot set fuel_cost on %s: %s", _g.name, _exc)

            # --- Part 2: add non-fuel LCOE component to maintenance_cost ---
            # Applied REGARDLESS of whether fuel_cost was just set or already
            # present — the non-fuel portion is separate from fuel cost.
            # Only adds where fuel_cost > 0 (active nodes), not where padding
            # leaves 0.
            _adder = _NON_FUEL_LCOE.get(_fuel_name, 0.0)
            if _adder <= 0:
                continue
            _existing_maint = list(getattr(_g, "maintenance_cost", None) or [])
            if _existing_maint and max(_existing_maint) >= _adder * 0.5:
                continue  # Already has substantial maintenance — don't double up
            _fc_now = list(getattr(_g, "fuel_cost", None) or [])
            _n_m = max(len(_existing_maint), len(_fc_now), 1)
            _new_maint = []
            for _i in range(_n_m):
                _e = _existing_maint[_i] if _i < len(_existing_maint) else 0.0
                _has_fuel = (_i < len(_fc_now) and _fc_now[_i] > 0)
                _new_maint.append(_e + (_adder if _has_fuel else 0.0))
            try:
                _g.maintenance_cost = _new_maint
                _linked_maint += 1
            except Exception as _exc:
                log.warning("Cannot set maintenance_cost on %s: %s", _g.name, _exc)

        if _linked_fuel > 0 or _linked_maint > 0:
            log.info(
                "Loader: linked fuel_cost for %d gens, added non-fuel LCOE for %d gens (Lazard 2024)",
                _linked_fuel, _linked_maint,
            )
        if _silent_freebies:
            log.warning(
                "Non-renewable generators with no valid fuel — LP would dispatch "
                "them as free with 0 emissions (silent bug). Affected (%d): %s",
                len(_silent_freebies),
                ", ".join(_silent_freebies[:10]) + (
                    f" … (+{len(_silent_freebies)-10} more)" if len(_silent_freebies) > 10 else ""
                ),
            )

    return SystemConfig(
        generators=generators,
        batteries=batteries,
        electrolyzers=electrolyzers,
        **system_data,
    )


def load_config(path: Union[str, Path]) -> ESFEXConfig:
    """
    Load and validate a ESFEX configuration file.

    Supports both single-file configurations (with embedded systems)
    and references to external system files.

    Args:
        path: Path to the main YAML configuration file

    Returns:
        Validated ESFEXConfig object

    Raises:
        ConfigLoadError: If loading or validation fails
    """
    path = Path(path)
    raw_config = load_yaml(path)

    try:
        # Check if systems are embedded or need to be loaded from separate files
        if "systems" in raw_config and isinstance(raw_config["systems"], dict):
            # Systems are embedded - convert each one
            converted_systems = {}
            for sys_name, sys_data in raw_config["systems"].items():
                if isinstance(sys_data, str):
                    # It's a file path reference
                    sys_path = path.parent / sys_data
                    sys_data = load_yaml(sys_path)
                converted_systems[sys_name] = _convert_system(sys_data)
            raw_config["systems"] = converted_systems

        # Convert temporal config
        if "temporal" in raw_config and isinstance(raw_config["temporal"], dict):
            raw_config["temporal"] = TemporalConfig(**raw_config["temporal"])

        # Convert solver config
        if "solver" in raw_config and isinstance(raw_config["solver"], dict):
            raw_config["solver"] = SolverConfig(**raw_config["solver"])

        # Convert N-1 security config
        if "n1_security" in raw_config and isinstance(raw_config["n1_security"], dict):
            raw_config["n1_security"] = N1SecurityConfig(**raw_config["n1_security"])

        # Convert master problem config
        if "master_problem" in raw_config and isinstance(raw_config["master_problem"], dict):
            raw_config["master_problem"] = MasterProblemConfig(**raw_config["master_problem"])

        # Convert meta_network config
        if "meta_network" in raw_config and isinstance(raw_config["meta_network"], dict):
            mn = raw_config["meta_network"]
            if "systems_links" in mn and isinstance(mn["systems_links"], list):
                links = []
                for link_data in mn["systems_links"]:
                    if isinstance(link_data, dict):
                        links.append(SystemLinkConfig(**link_data))
                mn["systems_links"] = links
            raw_config["meta_network"] = MetaNetworkConfig(**mn)

        # Validate with Pydantic
        config = ESFEXConfig(**raw_config)
        return config

    except ValidationError as e:
        raise ConfigLoadError(f"Configuration validation failed:\n{e}")
    except Exception as e:
        raise ConfigLoadError(f"Failed to load configuration: {e}")


def load_system_config(path: Union[str, Path]) -> SystemConfig:
    """
    Load a single system configuration file.

    Useful for loading individual system configs without the full
    ESFEXConfig wrapper.

    Args:
        path: Path to the system YAML file

    Returns:
        Validated SystemConfig object
    """
    raw_data = load_yaml(path)

    try:
        return _convert_system(raw_data)
    except ValidationError as e:
        raise ConfigLoadError(f"System configuration validation failed:\n{e}")
