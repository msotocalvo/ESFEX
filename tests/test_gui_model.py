"""Tests for GUI model dataclasses (no Qt/PySide6 dependency).

Only plain ``@dataclass`` classes and module-level constants are tested here.
``GuiModel(QObject)`` is intentionally excluded because it requires a running
Qt event loop.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Import helpers -- we need to stub out PySide6 so the module can be imported
# on CI machines (or anywhere PySide6 is not installed).
# ---------------------------------------------------------------------------

# Detect whether a *working* PySide6 is importable (not merely whether the
# name is already in sys.modules) so a real, functional Qt is preferred and
# the stubs below are only installed when Qt is genuinely absent.
try:
    import PySide6.QtWidgets  # noqa: F401
    _PYSIDE6_AVAILABLE = True
except Exception:
    _PYSIDE6_AVAILABLE = False

if not _PYSIDE6_AVAILABLE:
    # Create minimal stubs so ``from PySide6.QtCore import QObject, Signal``
    # succeeds at import time.
    _qtcore = ModuleType("PySide6.QtCore")
    _qtcore.QObject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})  # type: ignore[attr-defined]
    _qtcore.Signal = lambda *a, **kw: property(lambda self: None)  # type: ignore[attr-defined]
    _pyside6 = ModuleType("PySide6")
    sys.modules.setdefault("PySide6", _pyside6)
    sys.modules.setdefault("PySide6.QtCore", _qtcore)

from esfex.visualization.data.gui_model import (  # noqa: E402
    RENEWABLE_FUELS,
    EndpointRef,
    FuelEntryParams,
    FuelRouteParams,
    FuelStorageParams,
    GeoPoint,
    GuiACDCConverter,
    GuiACPowerFlow,
    GuiBatteryInstance,
    GuiBus,
    GuiDCPowerFlow,
    GuiDemandSector,
    GuiDevelopmentZone,
    GuiEVCategory,
    GuiEVConfig,
    GuiElectrolyzerInstance,
    GuiFuel,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiFuelTransportRoute,
    GuiFrequencyConverter,
    GuiGeneratorInstance,
    GuiGlobalSettings,
    GuiInterSystemLink,
    GuiInvestmentEntry,
    GuiInvestmentNodeData,
    GuiNode,
    GuiNodeDemand,
    GuiNonElectricDemand,
    GuiPenalties,
    GuiRooftopSolar,
    GuiStochasticScenario,
    GuiSystemSettings,
    GuiSystemState,
    GuiTechnology,
    GuiTransformer,
    GuiTransmissionLine,
    GuiVisualScaling,
    normalize_px,
    NodeTechnology,
    VisualStyle,
)


# ======================================================================
# Constants
# ======================================================================


class TestRenewableFuels:
    def test_is_set(self):
        assert isinstance(RENEWABLE_FUELS, set)

    def test_contains_expected_members(self):
        assert RENEWABLE_FUELS == {"Sun", "Wind", "Water", "OTEC"}

    def test_membership(self):
        assert "Sun" in RENEWABLE_FUELS
        assert "Wind" in RENEWABLE_FUELS
        assert "Water" in RENEWABLE_FUELS
        assert "OTEC" in RENEWABLE_FUELS
        assert "Coal" not in RENEWABLE_FUELS


# ======================================================================
# VisualStyle
# ======================================================================


class TestVisualStyle:
    def test_defaults_all_none(self):
        vs = VisualStyle()
        assert vs.color is None
        assert vs.size is None
        assert vs.icon_shape is None
        assert vs.opacity is None
        assert vs.width is None

    def test_custom_values(self):
        vs = VisualStyle(
            color="#3498db", size=12.0, icon_shape="circle", opacity=0.8, width=2.5
        )
        assert vs.color == "#3498db"
        assert vs.size == 12.0
        assert vs.icon_shape == "circle"
        assert vs.opacity == 0.8
        assert vs.width == 2.5


# ======================================================================
# GeoPoint
# ======================================================================


class TestGeoPoint:
    def test_creation(self):
        gp = GeoPoint(lat=48.85, lng=2.35)
        assert gp.lat == 48.85
        assert gp.lng == 2.35

    def test_default_label(self):
        gp = GeoPoint(lat=0.0, lng=0.0)
        assert gp.label == ""

    def test_custom_label(self):
        gp = GeoPoint(lat=1.0, lng=2.0, label="Paris")
        assert gp.label == "Paris"


# ======================================================================
# EndpointRef
# ======================================================================


class TestEndpointRef:
    def test_creation(self):
        ref = EndpointRef(element_type="node", element_id="0")
        assert ref.element_type == "node"
        assert ref.element_id == "0"

    def test_generator_ref(self):
        ref = EndpointRef(element_type="generator", element_id="unit_1_n0")
        assert ref.element_type == "generator"
        assert ref.element_id == "unit_1_n0"


# ======================================================================
# GuiNodeDemand
# ======================================================================


class TestGuiNodeDemand:
    def test_defaults(self):
        nd = GuiNodeDemand()
        assert nd.csv_path is None
        assert nd.data is None
        assert nd.num_hours == 0
        assert nd.peak_mw == 0.0
        assert nd.total_mwh == 0.0

    def test_custom(self):
        nd = GuiNodeDemand(csv_path="/tmp/d.csv", num_hours=8760, peak_mw=500.0)
        assert nd.csv_path == "/tmp/d.csv"
        assert nd.num_hours == 8760
        assert nd.peak_mw == 500.0


# ======================================================================
# NodeTechnology
# ======================================================================


class TestNodeTechnology:
    def test_creation(self):
        nt = NodeTechnology(name="Solar", category="generation")
        assert nt.name == "Solar"
        assert nt.category == "generation"
        assert nt.invest_cost == 0.0
        assert nt.invest_max == 0.0
        assert nt.existing_capacity == 0.0


# ======================================================================
# GuiNode
# ======================================================================


class TestGuiNode:
    def test_required_fields(self):
        n = GuiNode(index=0, name="Node A")
        assert n.index == 0
        assert n.name == "Node A"

    def test_defaults(self):
        n = GuiNode(index=1, name="N")
        assert n.reserve_static == 0.0
        assert n.reserve_dynamic == 0.0
        assert n.reserve_duration == 1
        assert n.losses == 0.0
        assert n.transference_invest_cost == 0.0
        assert n.transference_invest_max == 0.0
        assert isinstance(n.style, VisualStyle)
        assert isinstance(n.demand, GuiNodeDemand)
        assert n.technologies == []

    def test_mutable_defaults_independent(self):
        n1 = GuiNode(index=0, name="A")
        n2 = GuiNode(index=1, name="B")
        n1.technologies.append(NodeTechnology(name="X", category="gen"))
        assert len(n2.technologies) == 0


# ======================================================================
# GuiBus
# ======================================================================


class TestGuiBus:
    def test_creation_and_defaults(self):
        b = GuiBus(bus_id="bus_0")
        assert b.bus_id == "bus_0"
        assert b.name == ""
        assert b.parent_node == 0
        assert b.voltage_kv == 220.0
        assert b.frequency_hz == 50.0
        assert b.current_type == "AC"
        assert b.bus_type == "PQ"
        assert b.demand_fraction == 0.0   # default: child buses carry no demand
        assert b.latitude == 0.0
        assert b.longitude == 0.0
        assert isinstance(b.style, VisualStyle)

    def test_custom_values(self):
        b = GuiBus(bus_id="bus_5", voltage_kv=400.0, current_type="DC")
        assert b.voltage_kv == 400.0
        assert b.current_type == "DC"


# ======================================================================
# GuiGeneratorInstance
# ======================================================================


class TestGuiGeneratorInstance:
    def _make(self, **overrides) -> GuiGeneratorInstance:
        defaults = dict(
            instance_id="unit_0_bus_0",
            unit_key="unit_0",
            name="Solar PV",
            gen_type="Renewable",
            fuel="Sun",
        )
        defaults.update(overrides)
        return GuiGeneratorInstance(**defaults)

    def test_required_fields(self):
        g = self._make()
        assert g.instance_id == "unit_0_bus_0"
        assert g.unit_key == "unit_0"
        assert g.name == "Solar PV"
        assert g.gen_type == "Renewable"
        assert g.fuel == "Sun"

    def test_defaults(self):
        g = self._make()
        assert g.bus == "bus_0"
        assert g.node == 0
        assert g.reservable is True
        assert g.technology_id is None
        assert g.availability_file is None
        assert g.rated_power == 0.0
        assert g.min_power == 0.0
        assert g.life_time == 25
        assert g.initial_age == 0
        assert g.degradation_rate == 0.0
        assert g.fuel_cost == 0.0
        assert g.fixed_cost == 0.0
        assert g.maintenance_cost == 0.0
        assert g.eff_at_rated == 0.35
        assert g.eff_at_min == 0.25
        assert g.min_up == 0
        assert g.min_down == 0
        assert g.ramp_up == 0.0
        assert g.ramp_down == 0.0
        assert g.inertia == 0.0
        assert g.start_up_cost == 0.0
        assert g.decommissioning_cost == 0.0
        assert g.frequency_hz == 50.0
        assert g.current_type == "AC"
        assert isinstance(g.style, VisualStyle)
        assert g.latitude == 0.0
        assert g.longitude == 0.0

    def test_custom_rated_power(self):
        g = self._make(rated_power=100.0)
        assert g.rated_power == 100.0


# ======================================================================
# GuiBatteryInstance
# ======================================================================


class TestGuiBatteryInstance:
    def _make(self, **overrides) -> GuiBatteryInstance:
        defaults = dict(
            instance_id="bat_0_bus_0",
            unit_key="bat_0",
            name="Li-ion Battery",
        )
        defaults.update(overrides)
        return GuiBatteryInstance(**defaults)

    def test_required_fields(self):
        b = self._make()
        assert b.instance_id == "bat_0_bus_0"
        assert b.unit_key == "bat_0"
        assert b.name == "Li-ion Battery"

    def test_defaults(self):
        b = self._make()
        assert b.fuel == "None"
        assert b.bus == "bus_0"
        assert b.node == 0
        assert b.reservable is True
        assert b.spillage is True
        assert b.technology_id is None
        assert b.min_duration_hours is None
        assert b.max_duration_hours is None
        assert b.availability_file is None
        assert b.rated_power == 0.0
        assert b.capacity == 0.0
        assert b.efficiency_charge == 0.9
        assert b.efficiency_discharge == 0.9
        assert b.soc_initial == 0.5
        assert b.max_DoD == 1.0
        assert b.MaxChargePower == 0.0
        assert b.MaxDischargePower == 0.0
        assert b.life_time == 20
        assert b.initial_age == 0
        assert b.degradation_rate == 0.0
        assert b.min_power == 0.0
        assert b.min_up == 0
        assert b.min_down == 0
        assert b.ramp_up == 1.0
        assert b.ramp_down == 1.0
        assert b.eff_at_rated == 0.9
        assert b.eff_at_min == 0.9
        assert b.inertia == 0.0
        assert b.start_up_cost == 0.0
        assert b.fuel_cost == 0.0
        assert b.fixed_cost == 0.0
        assert b.maintenance_cost == 0.0
        assert b.throughput_degradation_cost == 0.0
        assert b.decommissioning_cost == 0.0
        assert b.current_type == "DC"
        assert isinstance(b.style, VisualStyle)
        assert b.latitude == 0.0
        assert b.longitude == 0.0

    def test_efficiency_override(self):
        b = self._make(efficiency_charge=0.95, efficiency_discharge=0.85)
        assert b.efficiency_charge == 0.95
        assert b.efficiency_discharge == 0.85


# ======================================================================
# GuiTransmissionLine
# ======================================================================


class TestGuiTransmissionLine:
    def test_creation_and_defaults(self):
        tl = GuiTransmissionLine(line_id="line_0")
        assert tl.line_id == "line_0"
        assert tl.from_bus == "bus_0"
        assert tl.to_bus == "bus_0"
        assert tl.from_node == 0
        assert tl.to_node == 0
        assert tl.capacity_mw == 0.0
        assert tl.voltage_kv is None
        assert tl.line_type is None
        assert tl.waypoints == []
        assert isinstance(tl.style, VisualStyle)
        assert tl.from_endpoint is None
        assert tl.to_endpoint is None
        assert tl.length_km is None
        assert tl.base_impedance is None
        assert tl.reactance_per_km is None
        assert tl.reactance_pu is None
        assert tl.resistance_pu is None
        assert tl.susceptance_pu is None
        assert tl.num_circuits == 1
        assert tl.frequency_hz == 50.0
        assert tl.current_type == "AC"

    def test_with_waypoints(self):
        pts = [GeoPoint(0.0, 0.0), GeoPoint(1.0, 1.0)]
        tl = GuiTransmissionLine(line_id="line_1", waypoints=pts, capacity_mw=500.0)
        assert len(tl.waypoints) == 2
        assert tl.capacity_mw == 500.0

    def test_with_endpoints(self):
        tl = GuiTransmissionLine(
            line_id="line_2",
            from_endpoint=EndpointRef("node", "0"),
            to_endpoint=EndpointRef("node", "1"),
        )
        assert tl.from_endpoint.element_type == "node"
        assert tl.to_endpoint.element_id == "1"


# ======================================================================
# GuiTransformer
# ======================================================================


class TestGuiTransformer:
    def test_creation_and_defaults(self):
        tr = GuiTransformer(name="TR-1")
        assert tr.name == "TR-1"
        assert tr.from_bus == "bus_0"
        assert tr.to_bus == "bus_0"
        assert tr.from_voltage_kv == 220.0
        assert tr.to_voltage_kv == 110.0
        assert tr.rated_power_mva == 100.0
        assert tr.impedance_pu == 0.1
        assert tr.losses_fraction == 0.005
        assert isinstance(tr.style, VisualStyle)
        assert tr.latitude == 0.0
        assert tr.longitude == 0.0

    def test_custom_voltages(self):
        tr = GuiTransformer(name="T2", from_voltage_kv=400.0, to_voltage_kv=220.0)
        assert tr.from_voltage_kv == 400.0
        assert tr.to_voltage_kv == 220.0


# ======================================================================
# GuiACDCConverter
# ======================================================================


class TestGuiACDCConverter:
    def test_creation_and_defaults(self):
        c = GuiACDCConverter(name="VSC-1")
        assert c.name == "VSC-1"
        assert c.converter_type == "VSC"
        assert c.from_bus == "bus_0"
        assert c.to_bus == "bus_0"
        assert c.from_voltage_kv == 220.0
        assert c.dc_voltage_kv == 320.0
        assert c.rated_power_mva == 100.0
        assert c.min_power_mva == 0.0
        assert c.efficiency_rectify == 0.98
        assert c.efficiency_invert == 0.98
        assert c.standby_losses_mw == 0.5
        assert c.reactive_power_min_mvar == -50.0
        assert c.reactive_power_max_mvar == 50.0
        assert c.power_factor == 1.0
        assert c.impedance_pu == 0.05
        assert c.resistance_pu == 0.01
        assert c.fixed_cost == 0.0
        assert c.variable_cost == 0.0
        assert c.life_time == 30
        assert c.initial_age == 0
        assert c.degradation_rate == 0.005
        assert isinstance(c.style, VisualStyle)

    def test_lcc_type(self):
        c = GuiACDCConverter(name="LCC-1", converter_type="LCC")
        assert c.converter_type == "LCC"


# ======================================================================
# GuiFrequencyConverter
# ======================================================================


class TestGuiFrequencyConverter:
    def test_creation_and_defaults(self):
        fc = GuiFrequencyConverter(name="FC-1")
        assert fc.name == "FC-1"
        assert fc.from_bus == "bus_0"
        assert fc.to_bus == "bus_0"
        assert fc.from_frequency_hz == 50.0
        assert fc.to_frequency_hz == 60.0
        assert fc.rated_power_mva == 100.0
        assert fc.min_power_mva == 0.0
        assert fc.efficiency_a_to_b == 0.98
        assert fc.efficiency_b_to_a == 0.98
        assert fc.standby_losses_mw == 0.5
        assert fc.impedance_pu == 0.05
        assert fc.resistance_pu == 0.01
        assert fc.life_time == 30
        assert fc.initial_age == 0
        assert fc.degradation_rate == 0.005

    def test_custom_frequencies(self):
        fc = GuiFrequencyConverter(
            name="FC-2", from_frequency_hz=60.0, to_frequency_hz=50.0
        )
        assert fc.from_frequency_hz == 60.0
        assert fc.to_frequency_hz == 50.0


# ======================================================================
# GuiDevelopmentZone
# ======================================================================


class TestGuiDevelopmentZone:
    def test_creation_and_defaults(self):
        dz = GuiDevelopmentZone(name="Solar Zone", technology="Solar PV")
        assert dz.name == "Solar Zone"
        assert dz.technology == "Solar PV"
        assert dz.layer == "electrical"
        assert dz.node is None
        assert dz.polygon == []
        assert dz.max_capacity_mw is None
        assert dz.notes is None
        assert isinstance(dz.style, VisualStyle)
        assert dz.line_cost_per_mw_km == 1500.0
        assert dz.transformer_cost_per_mw == 50000.0
        assert dz.target_bus_override is None
        assert dz.allowed_generators == []

    def test_with_polygon(self):
        poly = [GeoPoint(0, 0), GeoPoint(0, 1), GeoPoint(1, 1), GeoPoint(1, 0)]
        dz = GuiDevelopmentZone(
            name="Wind Zone", technology="Wind", polygon=poly, max_capacity_mw=1000.0
        )
        assert len(dz.polygon) == 4
        assert dz.max_capacity_mw == 1000.0


# ======================================================================
# FuelEntryParams
# ======================================================================


class TestFuelEntryParams:
    def test_defaults(self):
        fp = FuelEntryParams()
        assert fp.max_import_rate == 0.0
        assert fp.import_cost == 0.0

    def test_custom(self):
        fp = FuelEntryParams(max_import_rate=500.0, import_cost=10.0)
        assert fp.max_import_rate == 500.0
        assert fp.import_cost == 10.0


# ======================================================================
# GuiFuelEntryPoint
# ======================================================================


class TestGuiFuelEntryPoint:
    def test_creation_and_defaults(self):
        fe = GuiFuelEntryPoint(name="Port A")
        assert fe.name == "Port A"
        assert fe.fuels == []
        assert fe.node == 0
        assert isinstance(fe.coordinate, GeoPoint)
        assert fe.coordinate.lat == 0
        assert fe.coordinate.lng == 0
        assert fe.fuel_params == {}
        assert isinstance(fe.style, VisualStyle)

    def test_with_fuels(self):
        fe = GuiFuelEntryPoint(name="Gas Terminal", fuels=["LNG", "Diesel"])
        assert "LNG" in fe.fuels
        assert len(fe.fuels) == 2


# ======================================================================
# FuelRouteParams
# ======================================================================


class TestFuelRouteParams:
    def test_defaults(self):
        frp = FuelRouteParams()
        assert frp.capacity == 0.0
        assert frp.transport_cost == 0.0
        assert frp.losses_fraction == 0.0


# ======================================================================
# GuiFuelTransportRoute
# ======================================================================


class TestGuiFuelTransportRoute:
    def test_creation_and_defaults(self):
        fr = GuiFuelTransportRoute(route_id="fuel_route_0")
        assert fr.route_id == "fuel_route_0"
        assert fr.fuels == []
        assert fr.from_node == 0
        assert fr.to_node == 0
        assert fr.capacity == 0.0
        assert fr.transport_cost == 0.0
        assert fr.losses_fraction == 0.0
        assert fr.fuel_params == {}
        assert fr.length_km is None
        assert fr.waypoints == []
        assert fr.from_endpoint is None
        assert fr.to_endpoint is None

    def test_default_style(self):
        fr = GuiFuelTransportRoute(route_id="fuel_route_0")
        assert fr.style.color == "#c0392b"
        assert fr.style.width == 3.0


# ======================================================================
# FuelStorageParams
# ======================================================================


class TestFuelStorageParams:
    def test_defaults(self):
        sp = FuelStorageParams()
        assert sp.capacity == 0.0
        assert sp.initial_level == 0.5
        assert sp.min_level == 0.1


# ======================================================================
# GuiFuelStorage
# ======================================================================


class TestGuiFuelStorage:
    def test_creation_and_defaults(self):
        fs = GuiFuelStorage(storage_id="fuel_storage_0", name="Oil Tank")
        assert fs.storage_id == "fuel_storage_0"
        assert fs.name == "Oil Tank"
        assert fs.fuels == []
        assert fs.fuel_params == {}
        assert fs.node == 0
        assert isinstance(fs.style, VisualStyle)
        assert fs.latitude == 0.0
        assert fs.longitude == 0.0


# ======================================================================
# GuiFuel
# ======================================================================


class TestGuiFuel:
    def test_creation_and_defaults(self):
        f = GuiFuel(fuel_id="Fuel_oil", name="Fuel Oil")
        assert f.fuel_id == "Fuel_oil"
        assert f.name == "Fuel Oil"
        assert f.unit is None
        assert f.emission_factor == 0.0
        assert f.energy_content is None
        assert f.price_base == 0.0
        assert f.price_growth_rate == 0.0

    def test_renewable_fuel(self):
        f = GuiFuel(fuel_id="Sun", name="Solar")
        assert f.unit is None
        assert f.energy_content is None

    def test_with_emission_factor(self):
        f = GuiFuel(
            fuel_id="Coal",
            name="Coal",
            unit="ton",
            emission_factor=0.34,
            energy_content=8.14,
            price_base=50.0,
        )
        assert f.emission_factor == 0.34
        assert f.energy_content == 8.14
        assert f.price_base == 50.0


# ======================================================================
# GuiSystemSettings
# ======================================================================


class TestGuiSystemSettings:
    def test_defaults(self):
        ss = GuiSystemSettings()
        assert ss.demand_scale == 1.0
        assert ss.discount_rate == 0.05
        assert ss.base_lcoe == 93.0
        assert ss.target_re_penetration == 1.0
        assert ss.min_annual_increment == 0.01
        assert ss.max_annual_increment == 0.10
        assert ss.max_annual_system_cost == 20e9
        assert ss.max_npv_penalty_per_mw == 1e6
        assert ss.max_decommission_cost_per_mw == 5e5
        assert ss.force_replacement == -5e5
        assert ss.life_extension_cost_factor == 0.20
        assert ss.loss_demand_threshold == 0.05
        assert ss.inertia_limit_threshold == 0.1
        assert ss.sim_rooftop is False
        assert ss.co2_budget_enabled is True
        assert ss.co2_annual_budget == 1e6

    def test_custom(self):
        ss = GuiSystemSettings(discount_rate=0.08, target_re_penetration=0.8)
        assert ss.discount_rate == 0.08
        assert ss.target_re_penetration == 0.8


# ======================================================================
# GuiPenalties
# ======================================================================


class TestGuiPenalties:
    def test_defaults(self):
        p = GuiPenalties()
        # Default VOLL is the industry-standard $50,000/MWh, not the
        # original conservative $10M (the high value was distorting the
        # optimizer toward fictitious capacity expansion).
        assert p.loss_of_load == 50_000.0
        assert p.loss_of_reserve_static == 100.0
        assert p.loss_of_reserve_dynamic == 100.0
        assert p.loss_of_inertia == 200.0
        assert p.transfer_margin == 100.0
        assert p.curtailment == 100.0
        assert p.max_curtailment_ratio == 0.05
        assert p.rooftop_curtailment == 5.0
        assert p.co2_cost == 10.0
        assert p.co2_budget_violation == 500.0
        assert p.fre_penetration_loss == 100.0
        assert p.ev_loss == 10.0
        assert p.loss_of_fuel_supply == 100.0
        assert p.transport_congestion == 100.0
        assert p.storage_violation == 100.0
        assert p.non_electric_demand_loss == 100.0
        # Criticality penalties (single-digit relative weights, not absolute $).
        assert p.criticality_critical == 3.0
        assert p.criticality_high == 2.0
        assert p.criticality_medium == 1.0
        assert p.criticality_low == 0.5


# ======================================================================
# GuiDCPowerFlow
# ======================================================================


class TestGuiDCPowerFlow:
    def test_defaults(self):
        # enable_angle_limits was removed — DC angle limits do not bind
        # in this formulation (thermal capacity does); kept only for ACOPF.
        dc = GuiDCPowerFlow()
        assert dc.max_angle_diff_deg == 30.0
        assert dc.slack_bus == 0

    def test_custom(self):
        dc = GuiDCPowerFlow(slack_bus=2)
        assert dc.slack_bus == 2


# ======================================================================
# GuiACPowerFlow
# ======================================================================


class TestGuiACPowerFlow:
    def test_defaults(self):
        ac = GuiACPowerFlow()
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

    def test_custom(self):
        ac = GuiACPowerFlow(base_mva=200.0, voltage_min_pu=0.95, q_slack_penalty=50.0)
        assert ac.base_mva == 200.0
        assert ac.voltage_min_pu == 0.95
        assert ac.q_slack_penalty == 50.0


# ======================================================================
# GuiElectrolyzerInstance
# ======================================================================


class TestGuiElectrolyzerInstance:
    def _make(self, **overrides) -> GuiElectrolyzerInstance:
        defaults = dict(
            instance_id="elz_0_bus_0",
            unit_key="elz_0",
            name="PEM Electrolyzer",
        )
        defaults.update(overrides)
        return GuiElectrolyzerInstance(**defaults)

    def test_required_fields(self):
        e = self._make()
        assert e.instance_id == "elz_0_bus_0"
        assert e.unit_key == "elz_0"
        assert e.name == "PEM Electrolyzer"

    def test_defaults(self):
        e = self._make()
        assert e.fuel == "Hydrogen"
        assert e.technology == "PEM"
        assert e.technology_id is None
        assert e.bus == "bus_0"
        assert e.node == 0
        assert e.life_time == 20
        assert e.initial_age == 0
        assert e.degradation_rate == 0.01
        assert e.rated_power == 0.0
        assert e.min_power == 0.1
        assert e.ramp_up == 0.5
        assert e.ramp_down == 0.5
        assert e.eff_at_rated == 0.65
        assert e.eff_at_min == 0.55
        assert e.energy_per_kg_h2 == 50.0
        assert e.fixed_cost == 0.0
        assert e.variable_cost == 0.0
        assert e.water_cost == 0.001
        assert isinstance(e.style, VisualStyle)

    def test_alkaline_type(self):
        e = self._make(technology="Alkaline")
        assert e.technology == "Alkaline"


# ======================================================================
# GuiEVCategory
# ======================================================================


class TestGuiEVCategory:
    def test_creation_and_defaults(self):
        ev = GuiEVCategory(category_id="light")
        assert ev.category_id == "light"
        assert ev.battery_capacity == 50.0
        assert ev.charging_power == 7.0
        assert ev.v2g_power == 5.0
        assert ev.v2g_participation == 0.3
        assert ev.efficiency_charge == 0.9
        assert ev.efficiency_discharge == 0.9
        assert ev.min_soc == 0.2
        assert ev.max_adoption == 35.0
        assert ev.growth_rate == 0.14
        assert ev.mid_point_fraction == 0.5
        assert ev.quantity == []
        assert ev.base_pattern == []

    def test_with_quantity_list(self):
        ev = GuiEVCategory(category_id="heavy", quantity=[100, 200, 300])
        assert ev.quantity == [100, 200, 300]

    def test_with_base_pattern(self):
        pattern = [0.1] * 24
        ev = GuiEVCategory(category_id="buses", base_pattern=pattern)
        assert len(ev.base_pattern) == 24


# ======================================================================
# GuiEVConfig
# ======================================================================


class TestGuiEVConfig:
    def test_defaults(self):
        cfg = GuiEVConfig()
        assert cfg.initial_soc == []
        assert cfg.categories == {}

    def test_with_categories(self):
        cats = {
            "light": GuiEVCategory(category_id="light"),
            "heavy": GuiEVCategory(category_id="heavy"),
        }
        cfg = GuiEVConfig(categories=cats)
        assert len(cfg.categories) == 2
        assert "light" in cfg.categories
        assert cfg.categories["heavy"].category_id == "heavy"


# ======================================================================
# GuiRooftopSolar
# ======================================================================


class TestGuiRooftopSolar:
    def test_defaults(self):
        rs = GuiRooftopSolar()
        assert rs.adoption_scenario == "medium"
        assert rs.weather_variability == "normal"
        assert rs.simulation_seed == 42
        assert rs.performance_ratio == 0.75
        assert rs.degradation_rate == 0.005
        assert rs.cost_per_kw == 1200.0
        assert rs.cost_reduction_rate == 0.08
        assert rs.o_and_m_cost == 20.0
        assert rs.base_year == 2025
        assert rs.target_year == 2050
        assert rs.systems_per_node == []
        assert rs.avg_system_size == []
        assert rs.initial_adoption == []
        assert rs.max_adoption == {}
        assert rs.adoption_rates == {}

    def test_custom(self):
        rs = GuiRooftopSolar(
            adoption_scenario="high",
            max_adoption={"high": 0.8, "medium": 0.5},
        )
        assert rs.adoption_scenario == "high"
        assert rs.max_adoption["high"] == 0.8


# ======================================================================
# GuiDemandSector
# ======================================================================


class TestGuiDemandSector:
    def test_creation_and_defaults(self):
        ds = GuiDemandSector(sector_id="residential")
        assert ds.sector_id == "residential"
        assert ds.is_flexible is False
        assert ds.flexibility_ratio == 0.0
        assert ds.criticality == "medium"
        assert ds.delay_tolerance == 0
        assert ds.price_sensitivity == 0.0

    def test_flexible_sector(self):
        ds = GuiDemandSector(
            sector_id="industrial", is_flexible=True, flexibility_ratio=0.3
        )
        assert ds.is_flexible is True
        assert ds.flexibility_ratio == 0.3


# ======================================================================
# GuiNonElectricDemand
# ======================================================================


class TestGuiNonElectricDemand:
    def test_creation_and_defaults(self):
        ned = GuiNonElectricDemand(demand_id="heat_gas", fuel="Gas", unit="MMBTU")
        assert ned.demand_id == "heat_gas"
        assert ned.fuel == "Gas"
        assert ned.unit == "MMBTU"
        assert ned.is_flexible is False
        assert ned.flexibility_ratio == 0.0
        assert ned.criticality == "medium"
        assert ned.delay_tolerance == 0
        assert ned.price_sensitivity == 0.0
        assert ned.demand == []

    def test_with_demand(self):
        ned = GuiNonElectricDemand(
            demand_id="transport_diesel",
            fuel="Diesel",
            unit="kTon",
            demand=[500, 600],
        )
        assert ned.demand == [500, 600]


# ======================================================================
# GuiVisualScaling
# ======================================================================


class TestGuiVisualScaling:
    def test_defaults(self):
        vs = GuiVisualScaling()
        assert vs.marker_min_px == 6.0
        assert vs.marker_max_px == 40.0
        assert vs.marker_transform == "sqrt"
        assert vs.line_min_px == 1.5
        assert vs.line_max_px == 8.0


class TestNormalizePx:
    """Auto-fit mapping of a data range onto a pixel band."""

    def test_endpoints_map_to_band(self):
        # lo -> min_px, hi -> max_px (any transform).
        for t in ("linear", "sqrt", "log"):
            assert normalize_px(10.0, 10.0, 100.0, 6.0, 40.0, t) == pytest.approx(6.0)
            assert normalize_px(100.0, 10.0, 100.0, 6.0, 40.0, t) == pytest.approx(40.0)

    def test_monotonic_within_band(self):
        a = normalize_px(20.0, 10.0, 100.0, 6.0, 40.0, "sqrt")
        b = normalize_px(50.0, 10.0, 100.0, 6.0, 40.0, "sqrt")
        assert 6.0 < a < b < 40.0

    def test_linear_midpoint(self):
        # Linear: value halfway in range -> halfway in band.
        assert normalize_px(55.0, 10.0, 100.0, 0.0, 100.0, "linear") == pytest.approx(50.0)

    def test_sqrt_is_area_proportional(self):
        # With sqrt, normalized position tracks sqrt(value): for lo=0,
        # value at 25% of hi maps to 50% of the band.
        n = normalize_px(25.0, 0.0, 100.0, 0.0, 100.0, "sqrt")
        assert n == pytest.approx(50.0)

    def test_clamps_out_of_range(self):
        assert normalize_px(500.0, 10.0, 100.0, 6.0, 40.0, "linear") == pytest.approx(40.0)

    def test_nonpositive_and_degenerate(self):
        assert normalize_px(0.0, 10.0, 100.0, 6.0, 40.0) == 6.0      # value<=0 -> floor
        assert normalize_px(-5.0, 10.0, 100.0, 6.0, 40.0) == 6.0
        # Degenerate range (all equal) -> neutral mid-band.
        assert normalize_px(50.0, 50.0, 50.0, 6.0, 40.0) == pytest.approx(23.0)


# ======================================================================
# GuiGlobalSettings
# ======================================================================


class TestGuiGlobalSettings:
    def test_defaults(self):
        gs = GuiGlobalSettings()
        assert gs.simulation_mode == "development"
        assert gs.unit_commitment_hours == 24
        assert gs.date_start == "01/01/2025 00:00"
        assert gs.enable_primary_energy is False
        # Temporal
        assert gs.resolution_hours == 6
        assert gs.rolling_horizon_hours == 48
        assert gs.overlap_hours == 0
        assert gs.investment_resolution == 8760  # HOURS_STD_YEAR
        assert gs.primary_energy_resolution == 24
        assert gs.use_rolling_horizon is True
        # Solver
        assert gs.solver_name == "highs"
        assert gs.solver_threads == 4
        assert gs.solver_time_limit == 3600
        assert gs.solver_gap == 0.001
        assert gs.solver_verbose is False
        assert gs.solver_scale_constraints is False
        assert gs.solver_specific_options == {}
        # N1
        assert gs.n1_enabled is False
        assert gs.n1_apply_to_modes == ["unit_commitment"]
        assert gs.n1_transmission_enabled is True
        assert gs.n1_transmission_reserve_factor == 0.70
        assert gs.n1_critical_line_threshold == 0.0
        assert gs.n1_generation_enabled is True
        assert gs.n1_generation_reserve_type == "largest_unit"
        assert gs.n1_generation_reserve_percentage == 0.15
        # Master Problem
        assert gs.mp_stochastic is False
        assert gs.mp_representative_days == 5
        assert gs.mp_min_day_separation == 5
        assert gs.mp_use_tsam is False
        assert gs.mp_tsam_num_periods == 10
        assert gs.mp_tsam_method == "kmedoids"
        assert gs.mp_tsam_inter_period_linking is True
        # MGA/SPORES
        assert gs.mp_mga_enabled is False
        assert gs.mp_mga_num_alternatives == 10
        assert gs.mp_mga_slack_fraction == 0.05
        assert gs.mp_mga_investment_threshold == 0.1
        # Visual scaling
        assert isinstance(gs.visual_scaling, GuiVisualScaling)

    def test_custom_solver(self):
        gs = GuiGlobalSettings(solver_name="gurobi", solver_threads=8)
        # Both solver_name and solver_threads are re-resolved from user
        # preferences in __post_init__, so constructor arguments do not stick;
        # with no preferences set they fall back to the 'highs'/4 defaults.
        assert gs.solver_name == "highs"
        assert gs.solver_threads == 4

    def test_n1_modes_mutable_default_independent(self):
        gs1 = GuiGlobalSettings()
        gs2 = GuiGlobalSettings()
        gs1.n1_apply_to_modes.append("economic_dispatch")
        assert "economic_dispatch" not in gs2.n1_apply_to_modes


# ======================================================================
# GuiStochasticScenario
# ======================================================================


class TestGuiStochasticScenario:
    def test_creation_and_defaults(self):
        sc = GuiStochasticScenario(name="Base")
        assert sc.name == "Base"
        assert sc.probability == 0.5
        assert sc.description == ""
        assert sc.multipliers == {}

    def test_with_multipliers(self):
        sc = GuiStochasticScenario(
            name="High Cost",
            probability=0.3,
            multipliers={"fuel_cost": 1.5, "invest_cost": 1.2},
        )
        assert sc.probability == 0.3
        assert sc.multipliers["fuel_cost"] == 1.5
        assert len(sc.multipliers) == 2


# ======================================================================
# GuiInterSystemLink
# ======================================================================


class TestGuiInterSystemLink:
    def _make(self, **overrides) -> GuiInterSystemLink:
        defaults = dict(
            link_id="islink_0",
            link_type="transmission",
            from_system="sys_a",
            to_system="sys_b",
            from_node=0,
            to_node=1,
        )
        defaults.update(overrides)
        return GuiInterSystemLink(**defaults)

    def test_required_fields(self):
        lk = self._make()
        assert lk.link_id == "islink_0"
        assert lk.link_type == "transmission"
        assert lk.from_system == "sys_a"
        assert lk.to_system == "sys_b"
        assert lk.from_node == 0
        assert lk.to_node == 1

    def test_defaults(self):
        lk = self._make()
        assert lk.capacity_mw == 0.0
        assert lk.investment_cost == 0.0
        assert lk.max_investment_mw == 0.0
        assert lk.loss_factor == 0.0
        assert lk.distance_km == 0.0
        assert lk.cost_per_mw_km == 0.0
        assert lk.reactance_pu == 0.01
        assert lk.resistance_pu == 0.001
        assert lk.fuel == ""
        assert lk.waypoints == []
        assert lk.from_endpoint is None
        assert lk.to_endpoint is None

    def test_default_style(self):
        lk = self._make()
        assert lk.style.color == "#8e44ad"
        assert lk.style.width == 3.0

    def test_fuel_route_type(self):
        lk = self._make(link_type="fuel_route", fuel="LNG")
        assert lk.link_type == "fuel_route"
        assert lk.fuel == "LNG"


# ======================================================================
# GuiInvestmentNodeData
# ======================================================================


class TestGuiInvestmentNodeData:
    def test_creation_and_defaults(self):
        ind = GuiInvestmentNodeData(node_index=0)
        assert ind.node_index == 0
        assert ind.invest_cost == 0.0
        assert ind.invest_max == 0.0

    def test_custom(self):
        ind = GuiInvestmentNodeData(
            node_index=2, invest_cost=1200.0, invest_max=500.0
        )
        assert ind.node_index == 2
        assert ind.invest_cost == 1200.0
        assert ind.invest_max == 500.0


# ======================================================================
# GuiTechnology
# ======================================================================


class TestGuiTechnology:
    def test_creation_and_defaults(self):
        tech = GuiTechnology(tech_id="tech_0")
        assert tech.tech_id == "tech_0"
        assert tech.name == "New Technology"
        assert tech.category == "Renewable"
        assert tech.fuel == ""
        assert tech.life_time == 25
        assert tech.degradation_rate == 0.0
        assert tech.eff_at_rated == 0.35
        assert tech.eff_at_min == 0.25
        assert tech.invest_cost == 0.0
        assert tech.invest_max_power == 0.0
        assert tech.invest_cost_energy == 0.0
        assert tech.invest_max_capacity == 0.0
        assert isinstance(tech.style, VisualStyle)

    def test_storage_technology(self):
        tech = GuiTechnology(
            tech_id="tech_1",
            category="Storage",
            invest_cost_energy=200.0,
            invest_max_capacity=1000.0,
        )
        assert tech.category == "Storage"
        assert tech.invest_cost_energy == 200.0
        assert tech.invest_max_capacity == 1000.0


# ======================================================================
# GuiInvestmentEntry
# ======================================================================


class TestGuiInvestmentEntry:
    def test_creation_and_defaults(self):
        ie = GuiInvestmentEntry(
            entry_id="inv_0", name="Solar PV", technology_type="generator"
        )
        assert ie.entry_id == "inv_0"
        assert ie.name == "Solar PV"
        assert ie.technology_type == "generator"
        assert ie.target_key == ""
        assert ie.technology_id == ""
        assert ie.node_data == []
        assert ie.invest_cost_energy == {}
        assert ie.invest_max_capacity == {}

    def test_with_node_data(self):
        nd = [
            GuiInvestmentNodeData(node_index=0, invest_cost=1000.0, invest_max=200.0),
            GuiInvestmentNodeData(node_index=1, invest_cost=1100.0, invest_max=300.0),
        ]
        ie = GuiInvestmentEntry(
            entry_id="inv_1", name="Battery", technology_type="battery", node_data=nd
        )
        assert len(ie.node_data) == 2
        assert ie.node_data[0].invest_cost == 1000.0


# ======================================================================
# GuiSystemState
# ======================================================================


class TestGuiSystemState:
    def test_defaults(self):
        s = GuiSystemState()
        assert s.name == ""
        assert s.map_center is None
        assert s.map_zoom == 2     # world-view default
        assert s.nodes == []
        assert s.buses == {}
        assert s.generators == {}
        assert s.batteries == {}
        assert s.transmission_lines == []
        assert s.transformers == []
        assert s.acdc_converters == []
        assert s.freq_converters == []
        assert s.development_zones == []
        assert s.fuel_entry_points == []
        assert s.fuel_storages == {}
        assert s.fuel_transport_routes == []
        assert s.demand_path is None
        assert s.investment_portfolio == {}
        assert s.technologies == {}
        assert s._next_line_id == 0
        assert s._next_tech_id == 0
        assert s._next_fuel_route_id == 0
        assert s._next_bus_id == 0
        assert s._next_investment_id == 0
        # Subsystem defaults
        assert s.fuels == {}
        assert isinstance(s.settings, GuiSystemSettings)
        assert isinstance(s.penalties, GuiPenalties)
        assert isinstance(s.dc_power_flow, GuiDCPowerFlow)
        assert s.power_flow_mode == "dcopf"
        assert isinstance(s.ac_power_flow, GuiACPowerFlow)
        assert s.electrolyzers == {}
        assert isinstance(s.ev_config, GuiEVConfig)
        assert s.rooftop_solar is None
        assert s.demand_sectors == {}
        assert s.non_electric_demand == {}
        assert s.sector_distribution == {}
        assert s.raw_extras == {}

    def test_counter_fields(self):
        s = GuiSystemState(_next_line_id=5, _next_fuel_route_id=3)
        assert s._next_line_id == 5
        assert s._next_fuel_route_id == 3

    def test_mutable_defaults_independent(self):
        s1 = GuiSystemState()
        s2 = GuiSystemState()
        s1.nodes.append(GuiNode(index=0, name="A"))
        assert len(s2.nodes) == 0
        s1.generators["g1"] = GuiGeneratorInstance(
            instance_id="g1", unit_key="u1", name="G1", gen_type="Renewable", fuel="Sun"
        )
        assert len(s2.generators) == 0

    def test_full_construction(self):
        node = GuiNode(index=0, name="Main")
        gen = GuiGeneratorInstance(
            instance_id="unit_0_bus_0",
            unit_key="unit_0",
            name="Solar",
            gen_type="Renewable",
            fuel="Sun",
            rated_power=100.0,
        )
        bat = GuiBatteryInstance(
            instance_id="bat_0_bus_0",
            unit_key="bat_0",
            name="Storage",
            capacity=200.0,
        )
        line = GuiTransmissionLine(line_id="line_0", capacity_mw=500.0)

        s = GuiSystemState(
            name="TestSystem",
            map_center=GeoPoint(lat=10.0, lng=20.0),
            map_zoom=10,
            nodes=[node],
            generators={"unit_0_bus_0": gen},
            batteries={"bat_0_bus_0": bat},
            transmission_lines=[line],
            _next_line_id=1,
        )
        assert s.name == "TestSystem"
        assert s.map_center.lat == 10.0
        assert s.map_zoom == 10
        assert len(s.nodes) == 1
        assert s.nodes[0].name == "Main"
        assert s.generators["unit_0_bus_0"].rated_power == 100.0
        assert s.batteries["bat_0_bus_0"].capacity == 200.0
        assert s.transmission_lines[0].capacity_mw == 500.0
        assert s._next_line_id == 1

    def test_nested_subsystems_have_correct_types(self):
        s = GuiSystemState()
        assert isinstance(s.settings, GuiSystemSettings)
        assert s.settings.discount_rate == 0.05
        assert isinstance(s.penalties, GuiPenalties)
        assert s.penalties.loss_of_load == 50_000.0   # industry-standard VOLL
        assert isinstance(s.dc_power_flow, GuiDCPowerFlow)
        assert s.dc_power_flow.slack_bus == 0
        assert isinstance(s.ac_power_flow, GuiACPowerFlow)
        assert s.ac_power_flow.base_mva == 100.0
        assert s.power_flow_mode == "dcopf"
        assert isinstance(s.ev_config, GuiEVConfig)
        assert s.ev_config.categories == {}
