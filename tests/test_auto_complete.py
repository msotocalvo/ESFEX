"""Comprehensive tests for auto_complete and grid_mapping_steps helpers.

Covers:
  - esfex.visualization.data.auto_complete (ConnectionPlan, plan/apply,
    single-equipment and converter auto-connect)
  - Private helper functions in esfex.visualization.workflows.grid_mapping_steps
    (_build_bus_adjacency, _find_connected_components, _bus_has_any_equipment,
    _check_connectivity, _check_voltage_consistency, _audit_all_equipment,
    _audit_all_transformers, _audit_all_converters, iterative_auto_connect)

PySide6 and i18n are stubbed so the tests run on headless CI.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Stub PySide6 + i18n before any visualization imports
# ---------------------------------------------------------------------------

_PYSIDE6_AVAILABLE = "PySide6" in sys.modules or "PySide6.QtCore" in sys.modules

if not _PYSIDE6_AVAILABLE:
    _qtcore = ModuleType("PySide6.QtCore")
    _qtcore.QObject = type(  # type: ignore[attr-defined]
        "QObject", (), {"__init__": lambda self, *a, **kw: None},
    )
    _qtcore.Signal = lambda *a, **kw: property(lambda self: None)  # type: ignore[attr-defined]
    _qtcore.Qt = type(  # type: ignore[attr-defined]
        "Qt", (), {"AlignmentFlag": type("AF", (), {"AlignCenter": 0})},
    )()
    _qtcore.QThread = type(  # type: ignore[attr-defined]
        "QThread", (), {"__init__": lambda self, *a, **kw: None},
    )

    _pyside6 = ModuleType("PySide6")
    sys.modules.setdefault("PySide6", _pyside6)
    sys.modules.setdefault("PySide6.QtCore", _qtcore)

    # QtWidgets stub (grid_mapping_steps imports many widgets)
    _qtwidgets = ModuleType("PySide6.QtWidgets")
    for _w in [
        "QWidget", "QVBoxLayout", "QHBoxLayout", "QFormLayout",
        "QGroupBox", "QLabel", "QPushButton", "QSpinBox", "QDoubleSpinBox",
        "QCheckBox", "QComboBox", "QTextEdit", "QProgressBar", "QScrollArea",
        "QMessageBox", "QInputDialog", "QHeaderView", "QRadioButton",
        "QTableWidget", "QTableWidgetItem",
    ]:
        setattr(
            _qtwidgets, _w,
            type(_w, (), {"__init__": lambda self, *a, **kw: None}),
        )
    sys.modules.setdefault("PySide6.QtWidgets", _qtwidgets)

    # i18n stub
    _i18n = ModuleType("esfex.visualization.i18n")
    _i18n.tr = lambda key, **kw: key  # type: ignore[attr-defined]
    sys.modules.setdefault("esfex.visualization.i18n", _i18n)


# ---------------------------------------------------------------------------
# Now safe to import visualization modules
# ---------------------------------------------------------------------------

from esfex.visualization.data.gui_model import (  # noqa: E402
    EndpointRef,
    GuiACDCConverter,
    GuiBatteryInstance,
    GuiBus,
    GuiElectrolyzerInstance,
    GuiFrequencyConverter,
    GuiGeneratorInstance,
    GuiNode,
    GuiNodeDemand,
    GuiSystemState,
    GuiTransformer,
    GuiTransmissionLine,
)
from esfex.visualization.data.auto_complete import (  # noqa: E402
    ConnectionPlan,
    DEFAULT_LV_KV,
    SAFETY_FACTOR,
    _find_disconnected,
    _find_nearest_hv_bus,
    auto_connect_single_equipment,
    _auto_connect_single_converter,
    apply_auto_complete,
    plan_auto_complete,
    verify_connection_chain,
)
from esfex.visualization.workflows.grid_mapping_steps import (  # noqa: E402
    _build_bus_adjacency,
    _find_connected_components,
    _bus_has_any_equipment,
    _check_connectivity,
    _check_voltage_consistency,
    _audit_all_equipment,
    _audit_all_transformers,
    _audit_all_converters,
    iterative_auto_connect,
)


# ======================================================================
# Helpers
# ======================================================================


def _make_state(**overrides) -> GuiSystemState:
    """Return a minimal valid GuiSystemState (no equipment by default)."""
    nodes = overrides.pop("nodes", [
        GuiNode(index=0, name="North",
                demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
    ])
    buses = overrides.pop("buses", {
        "bus_hv": GuiBus(
            bus_id="bus_hv", name="HV Bus", parent_node=0,
            voltage_kv=110.0, latitude=21.0, longitude=-82.0,
        ),
    })
    return GuiSystemState(
        name="test_system",
        nodes=nodes,
        buses=buses,
        generators=overrides.pop("generators", {}),
        batteries=overrides.pop("batteries", {}),
        electrolyzers=overrides.pop("electrolyzers", {}),
        transmission_lines=overrides.pop("transmission_lines", []),
        transformers=overrides.pop("transformers", []),
        acdc_converters=overrides.pop("acdc_converters", []),
        freq_converters=overrides.pop("freq_converters", []),
        **overrides,
    )


class MockGuiModel:
    """Lightweight model mock that mirrors GuiModel mutation methods."""

    def __init__(self, state: GuiSystemState):
        self.state = state
        self._next_bus = 100
        self._next_tr = len(state.transformers)

    def add_bus(
        self, parent_node: int, name: str,
        voltage_kv: float = 220.0,
        latitude: float = 0.0, longitude: float = 0.0,
    ) -> str:
        bid = f"bus_{self._next_bus}"
        self._next_bus += 1
        self.state.buses[bid] = GuiBus(
            bus_id=bid, name=name, parent_node=parent_node,
            voltage_kv=voltage_kv, latitude=latitude, longitude=longitude,
        )
        return bid

    def add_transformer(
        self, name: str,
        from_bus: str = "bus_0", to_bus: str = "bus_0",
        from_voltage_kv: float = 220.0, to_voltage_kv: float = 110.0,
        rated_power_mva: float = 100.0,
        latitude: float = 0.0, longitude: float = 0.0,
    ) -> int:
        tr = GuiTransformer(
            name=name, from_bus=from_bus, to_bus=to_bus,
            from_voltage_kv=from_voltage_kv, to_voltage_kv=to_voltage_kv,
            rated_power_mva=rated_power_mva,
            latitude=latitude, longitude=longitude,
        )
        self.state.transformers.append(tr)
        idx = len(self.state.transformers) - 1
        return idx

    def add_line(
        self,
        from_bus: str = "bus_0", to_bus: str = "bus_0",
        capacity_mw: float = 100.0,
        from_endpoint: Optional[EndpointRef] = None,
        to_endpoint: Optional[EndpointRef] = None,
    ) -> str:
        lid = f"line_{self.state._next_line_id}"
        self.state._next_line_id += 1
        self.state.transmission_lines.append(GuiTransmissionLine(
            line_id=lid, from_bus=from_bus, to_bus=to_bus,
            capacity_mw=capacity_mw,
            from_endpoint=from_endpoint, to_endpoint=to_endpoint,
        ))
        return lid

    def remove_line(self, line_id: str):
        self.state.transmission_lines = [
            ln for ln in self.state.transmission_lines if ln.line_id != line_id
        ]

    def remove_bus(self, bus_id: str):
        if bus_id in self.state.buses:
            del self.state.buses[bus_id]

    # auto_complete's apply_auto_complete calls model.state, which is fine,
    # but verify_connection_chain is called with model.state directly.
    def stateLoaded(self):
        pass


# ======================================================================
# TestConnectionPlan
# ======================================================================


class TestConnectionPlan:
    """Tests for the ConnectionPlan dataclass."""

    def test_creation_with_required_fields(self):
        plan = ConnectionPlan(
            isolated_bus_id="bus_lv_0",
            target_bus_id="bus_hv",
            distance_km=5.0,
            equipment_summary="1 gen (10 MW)",
            total_capacity_mw=10.0,
            transformer_capacity_mva=12.0,
            transformer_hv_kv=110.0,
            transformer_lv_kv=0.48,
            line_capacity_mw=12.0,
        )
        assert plan.isolated_bus_id == "bus_lv_0"
        assert plan.target_bus_id == "bus_hv"
        assert plan.distance_km == 5.0
        assert plan.equipment_summary == "1 gen (10 MW)"

    def test_default_values(self):
        plan = ConnectionPlan(
            isolated_bus_id="b1", target_bus_id="b2",
            distance_km=0.0, equipment_summary="",
            total_capacity_mw=0.0, transformer_capacity_mva=0.0,
            transformer_hv_kv=0.0, transformer_lv_kv=0.0,
            line_capacity_mw=0.0,
        )
        assert plan.reason == ""
        assert plan.selected is True
        assert plan.equip_lat == 0.0
        assert plan.equip_lng == 0.0
        assert plan.lv_lat == 0.0
        assert plan.lv_lng == 0.0

    def test_equipment_ids_default_empty_list(self):
        plan = ConnectionPlan(
            isolated_bus_id="b1", target_bus_id="b2",
            distance_km=0.0, equipment_summary="",
            total_capacity_mw=0.0, transformer_capacity_mva=0.0,
            transformer_hv_kv=0.0, transformer_lv_kv=0.0,
            line_capacity_mw=0.0,
        )
        assert plan.equipment_ids == []

    def test_equipment_ids_with_values(self):
        plan = ConnectionPlan(
            isolated_bus_id="b1", target_bus_id="b2",
            distance_km=0.0, equipment_summary="",
            total_capacity_mw=0.0, transformer_capacity_mva=0.0,
            transformer_hv_kv=0.0, transformer_lv_kv=0.0,
            line_capacity_mw=0.0,
            equipment_ids=["gen_0", "bat_1"],
        )
        assert plan.equipment_ids == ["gen_0", "bat_1"]


# ======================================================================
# TestFindDisconnected
# ======================================================================


class TestFindDisconnected:
    """Tests for _find_disconnected."""

    def test_empty_state_returns_nothing(self):
        state = _make_state()
        result = _find_disconnected(state)
        assert result == []

    def test_generator_on_bus_with_transformer_not_disconnected(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
            transformers=[
                GuiTransformer(name="TR0", from_bus="bus_lv", to_bus="bus_hv"),
            ],
        )
        result = _find_disconnected(state)
        assert len(result) == 0

    def test_generator_on_bus_without_transformer_is_disconnected(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        result = _find_disconnected(state)
        assert len(result) == 1
        assert result[0].etype == "generator"
        assert result[0].eid == "gen_0"

    def test_battery_on_bus_without_transformer_is_disconnected(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
            },
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="LiIon",
                    bus="bus_lv", node=0, rated_power=5.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        result = _find_disconnected(state)
        assert len(result) == 1
        assert result[0].etype == "battery"

    def test_electrolyzer_on_bus_without_transformer_is_disconnected(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
            },
            electrolyzers={
                "ely_0": GuiElectrolyzerInstance(
                    instance_id="ely_0", unit_key="e0", name="PEM",
                    bus="bus_lv", node=0, rated_power=2.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        result = _find_disconnected(state)
        assert len(result) == 1
        assert result[0].etype == "electrolyzer"

    def test_equipment_on_bus_with_acdc_converter_not_disconnected(self):
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_dc": GuiBus(bus_id="bus_dc", voltage_kv=320.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_ac", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
            acdc_converters=[
                GuiACDCConverter(
                    name="Conv0", from_bus="bus_ac", to_bus="bus_dc",
                ),
            ],
        )
        result = _find_disconnected(state)
        assert len(result) == 0

    def test_equipment_on_bus_with_freq_converter_not_disconnected(self):
        state = _make_state(
            buses={
                "bus_50": GuiBus(bus_id="bus_50", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_60": GuiBus(bus_id="bus_60", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Wind",
                    gen_type="Renewable", fuel="Wind",
                    bus="bus_50", node=0, rated_power=20.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
            freq_converters=[
                GuiFrequencyConverter(
                    name="FC0", from_bus="bus_50", to_bus="bus_60",
                ),
            ],
        )
        result = _find_disconnected(state)
        assert len(result) == 0


# ======================================================================
# TestFindNearestHVBus
# ======================================================================


class TestFindNearestHVBus:
    """Tests for _find_nearest_hv_bus."""

    def test_finds_nearest_hv_bus(self):
        state = _make_state(
            buses={
                "bus_hv1": GuiBus(bus_id="bus_hv1", voltage_kv=110.0,
                                  latitude=21.0, longitude=-82.0),
                "bus_hv2": GuiBus(bus_id="bus_hv2", voltage_kv=220.0,
                                  latitude=22.0, longitude=-82.0),
            },
        )
        bid, dist = _find_nearest_hv_bus(state, 21.05, -82.0)
        assert bid == "bus_hv1"
        assert dist < 10.0  # should be ~5.5 km

    def test_skips_lv_buses(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=22.0, longitude=-82.0),
            },
        )
        bid, _ = _find_nearest_hv_bus(state, 21.0, -82.0)
        # Should skip bus_lv (0.48 kV <= DEFAULT_LV_KV) and return bus_hv
        assert bid == "bus_hv"

    def test_skips_buses_at_zero_zero(self):
        state = _make_state(
            buses={
                "bus_null": GuiBus(bus_id="bus_null", voltage_kv=110.0,
                                   latitude=0.0, longitude=0.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
        )
        bid, _ = _find_nearest_hv_bus(state, 21.05, -82.0)
        assert bid == "bus_hv"

    def test_returns_none_when_no_valid_bus(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
            },
        )
        bid, dist = _find_nearest_hv_bus(state, 21.0, -82.0)
        assert bid is None
        assert dist == float("inf")

    def test_exclude_buses_parameter(self):
        state = _make_state(
            buses={
                "bus_hv1": GuiBus(bus_id="bus_hv1", voltage_kv=110.0,
                                  latitude=21.0, longitude=-82.0),
                "bus_hv2": GuiBus(bus_id="bus_hv2", voltage_kv=220.0,
                                  latitude=22.0, longitude=-82.0),
            },
        )
        bid, _ = _find_nearest_hv_bus(
            state, 21.0, -82.0, exclude_buses={"bus_hv1"},
        )
        assert bid == "bus_hv2"


# ======================================================================
# TestPlanAutoComplete
# ======================================================================


class TestPlanAutoComplete:
    """Tests for plan_auto_complete."""

    def test_empty_state_returns_empty_plans(self):
        state = _make_state()
        plans = plan_auto_complete(state)
        assert plans == []

    def test_single_disconnected_generator_returns_one_plan(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        assert len(plans) == 1

    def test_plan_has_correct_fields(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        plan = plans[0]
        assert plan.isolated_bus_id == "bus_lv"
        assert plan.target_bus_id == "bus_hv"
        assert plan.transformer_hv_kv == 110.0
        assert plan.transformer_lv_kv == DEFAULT_LV_KV
        assert plan.total_capacity_mw == 10.0
        assert plan.transformer_capacity_mva == pytest.approx(10.0 * SAFETY_FACTOR)
        assert plan.equipment_ids == ["gen_0"]

    def test_multiple_equipment_on_same_bus_grouped_into_one_plan(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="LiIon",
                    bus="bus_lv", node=0, rated_power=5.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        assert len(plans) == 1
        assert set(plans[0].equipment_ids) == {"gen_0", "bat_0"}
        assert plans[0].total_capacity_mw == 15.0

    def test_generator_on_hv_bus_uses_own_bus_as_target(self):
        """Equipment on an HV bus gets a new LV bus + TR chain to itself."""
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_hv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        assert len(plans) == 1
        assert plans[0].target_bus_id == "bus_hv"
        assert plans[0].distance_km == 0.0


# ======================================================================
# TestAutoConnectSingleEquipment
# ======================================================================


class TestAutoConnectSingleEquipment:
    """Tests for auto_connect_single_equipment."""

    def _state_with_gen(self):
        return _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_hv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )

    def test_connects_generator_with_chain(self):
        state = self._state_with_gen()
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "generator", "gen_0", 21.0, -82.0,
        )
        assert result is True
        # Should have created 1 LV bus
        assert len(state.buses) > 1
        # Should have created 1 transformer
        assert len(state.transformers) == 1
        # Should have created 3 lines
        assert len(state.transmission_lines) == 3

    def test_connects_battery(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="LiIon",
                    bus="bus_hv", node=0, rated_power=5.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "battery", "bat_0", 21.0, -82.0,
        )
        assert result is True
        assert len(state.transformers) == 1
        assert len(state.transmission_lines) == 3

    def test_connects_electrolyzer(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
            electrolyzers={
                "ely_0": GuiElectrolyzerInstance(
                    instance_id="ely_0", unit_key="e0", name="PEM",
                    bus="bus_hv", node=0, rated_power=2.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "electrolyzer", "ely_0", 21.0, -82.0,
        )
        assert result is True

    def test_returns_false_for_unknown_type(self):
        state = self._state_with_gen()
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "unknown_type", "gen_0", 21.0, -82.0,
        )
        assert result is False

    def test_returns_false_when_no_hv_bus_available(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "generator", "gen_0", 21.0, -82.0,
        )
        assert result is False

    def test_returns_false_for_nonexistent_equipment(self):
        state = self._state_with_gen()
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "generator", "gen_99", 21.0, -82.0,
        )
        assert result is False

    def test_equipment_moved_to_lv_bus(self):
        state = self._state_with_gen()
        model = MockGuiModel(state)
        auto_connect_single_equipment(
            model, "generator", "gen_0", 21.0, -82.0,
        )
        gen = state.generators["gen_0"]
        # Equipment should now be on the new LV bus, not bus_hv
        assert gen.bus != "bus_hv"
        assert gen.bus.startswith("bus_")


# ======================================================================
# TestAutoConnectSingleConverter
# ======================================================================


class TestAutoConnectSingleConverter:
    """Tests for _auto_connect_single_converter."""

    def test_creates_2_lines_for_acdc_converter(self):
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_dc": GuiBus(bus_id="bus_dc", voltage_kv=320.0,
                                 latitude=21.1, longitude=-82.0),
            },
            acdc_converters=[
                GuiACDCConverter(
                    name="Conv0", from_bus="bus_ac", to_bus="bus_dc",
                    rated_power_mva=50.0,
                ),
            ],
        )
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "acdc_converter", "0")
        assert result is True
        assert len(state.transmission_lines) == 2

    def test_creates_2_lines_for_freq_converter(self):
        state = _make_state(
            buses={
                "bus_50": GuiBus(bus_id="bus_50", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_60": GuiBus(bus_id="bus_60", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            freq_converters=[
                GuiFrequencyConverter(
                    name="FC0", from_bus="bus_50", to_bus="bus_60",
                    rated_power_mva=30.0,
                ),
            ],
        )
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "freq_converter", "0")
        assert result is True
        assert len(state.transmission_lines) == 2

    def test_returns_false_for_invalid_converter_index(self):
        state = _make_state(
            acdc_converters=[
                GuiACDCConverter(name="Conv0", from_bus="bus_hv", to_bus="bus_hv"),
            ],
        )
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "acdc_converter", "99")
        assert result is False

    def test_returns_false_when_buses_dont_exist(self):
        state = _make_state(
            buses={},
            acdc_converters=[
                GuiACDCConverter(
                    name="Conv0", from_bus="bus_missing1", to_bus="bus_missing2",
                ),
            ],
        )
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "acdc_converter", "0")
        assert result is False

    def test_returns_false_for_invalid_type(self):
        state = _make_state()
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "invalid_type", "0")
        assert result is False

    def test_returns_false_for_non_integer_id(self):
        state = _make_state(
            acdc_converters=[
                GuiACDCConverter(name="Conv0", from_bus="bus_hv", to_bus="bus_hv"),
            ],
        )
        model = MockGuiModel(state)
        result = _auto_connect_single_converter(model, "acdc_converter", "abc")
        assert result is False


# ======================================================================
# TestApplyAutoComplete
# ======================================================================


class TestApplyAutoComplete:
    """Tests for apply_auto_complete."""

    def _state_with_disconnected_gen(self):
        return _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=10.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )

    def test_applies_plan_and_creates_chain_elements(self):
        state = self._state_with_disconnected_gen()
        model = MockGuiModel(state)
        plans = plan_auto_complete(state)
        assert len(plans) == 1
        count = apply_auto_complete(model, plans)
        assert count == 1
        # Should have created new LV bus, transformer, lines
        assert len(state.transformers) == 1
        assert len(state.transmission_lines) >= 2  # at least equip line + LV-TR + TR-HV

    def test_skips_unselected_plans(self):
        state = self._state_with_disconnected_gen()
        model = MockGuiModel(state)
        plans = plan_auto_complete(state)
        plans[0].selected = False
        count = apply_auto_complete(model, plans)
        assert count == 0
        assert len(state.transformers) == 0

    def test_moves_equipment_to_lv_bus(self):
        state = self._state_with_disconnected_gen()
        model = MockGuiModel(state)
        plans = plan_auto_complete(state)
        apply_auto_complete(model, plans)
        gen = state.generators["gen_0"]
        # Equipment should have been moved to the new LV bus
        assert gen.bus != "bus_lv"

    def test_empty_plans_returns_zero(self):
        state = self._state_with_disconnected_gen()
        model = MockGuiModel(state)
        count = apply_auto_complete(model, [])
        assert count == 0


# ======================================================================
# TestBuildBusAdjacency (grid_mapping_steps)
# ======================================================================


class TestBuildBusAdjacency:
    """Tests for _build_bus_adjacency."""

    def test_lines_create_adjacency(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert "bus_b" in adj["bus_a"]
        assert "bus_a" in adj["bus_b"]

    def test_transformers_create_adjacency(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
            },
            transformers=[
                GuiTransformer(name="TR0", from_bus="bus_a", to_bus="bus_b"),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert "bus_b" in adj["bus_a"]
        assert "bus_a" in adj["bus_b"]

    def test_acdc_converters_create_adjacency(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
            },
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_a", to_bus="bus_b"),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert "bus_b" in adj["bus_a"]

    def test_freq_converters_create_adjacency(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
            },
            freq_converters=[
                GuiFrequencyConverter(name="FC0", from_bus="bus_a", to_bus="bus_b"),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert "bus_b" in adj["bus_a"]

    def test_empty_state(self):
        state = _make_state(buses={})
        adj = _build_bus_adjacency(state)
        assert adj == {}

    def test_bus_without_connections_has_empty_neighbors(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
            },
        )
        adj = _build_bus_adjacency(state)
        assert adj["bus_a"] == set()
        assert adj["bus_b"] == set()


# ======================================================================
# TestFindConnectedComponents (grid_mapping_steps)
# ======================================================================


class TestFindConnectedComponents:
    """Tests for _find_connected_components."""

    def test_single_connected_component(self):
        adj = {
            "a": {"b"},
            "b": {"a", "c"},
            "c": {"b"},
        }
        comps = _find_connected_components(adj)
        assert len(comps) == 1
        assert comps[0] == {"a", "b", "c"}

    def test_two_isolated_components(self):
        adj = {
            "a": {"b"},
            "b": {"a"},
            "c": {"d"},
            "d": {"c"},
        }
        comps = _find_connected_components(adj)
        assert len(comps) == 2
        comp_sets = [frozenset(c) for c in comps]
        assert frozenset({"a", "b"}) in comp_sets
        assert frozenset({"c", "d"}) in comp_sets

    def test_empty_graph(self):
        adj: dict[str, set[str]] = {}
        comps = _find_connected_components(adj)
        assert comps == []

    def test_single_isolated_nodes(self):
        adj = {
            "a": set(),
            "b": set(),
        }
        comps = _find_connected_components(adj)
        assert len(comps) == 2

    def test_three_components(self):
        adj = {
            "a": {"b"},
            "b": {"a"},
            "c": set(),
            "d": {"e"},
            "e": {"d"},
        }
        comps = _find_connected_components(adj)
        assert len(comps) == 3


# ======================================================================
# TestBusHasAnyEquipment (grid_mapping_steps)
# ======================================================================


class TestBusHasAnyEquipment:
    """Tests for _bus_has_any_equipment."""

    def test_bus_with_generator(self):
        state = _make_state(
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_hv", node=0,
                ),
            },
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_bus_with_battery(self):
        state = _make_state(
            batteries={
                "bat_0": GuiBatteryInstance(
                    instance_id="bat_0", unit_key="b0", name="LiIon",
                    bus="bus_hv", node=0,
                ),
            },
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_bus_with_electrolyzer(self):
        state = _make_state(
            electrolyzers={
                "ely_0": GuiElectrolyzerInstance(
                    instance_id="ely_0", unit_key="e0", name="PEM",
                    bus="bus_hv", node=0,
                ),
            },
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_bus_with_transformer(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0),
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48),
            },
            transformers=[
                GuiTransformer(name="TR0", from_bus="bus_lv", to_bus="bus_hv"),
            ],
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_bus_with_acdc_converter(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv"),
                "bus_dc": GuiBus(bus_id="bus_dc"),
            },
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_hv", to_bus="bus_dc"),
            ],
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_bus_with_freq_converter(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv"),
                "bus_60": GuiBus(bus_id="bus_60"),
            },
            freq_converters=[
                GuiFrequencyConverter(name="FC0", from_bus="bus_hv", to_bus="bus_60"),
            ],
        )
        assert _bus_has_any_equipment(state, "bus_hv") is True

    def test_empty_bus(self):
        state = _make_state()
        assert _bus_has_any_equipment(state, "bus_hv") is False


# ======================================================================
# TestCheckConnectivity (grid_mapping_steps)
# ======================================================================


class TestCheckConnectivity:
    """Tests for _check_connectivity."""

    def test_fully_connected_no_issues(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", latitude=21.0, longitude=-82.0),
                "bus_b": GuiBus(bus_id="bus_b", latitude=21.1, longitude=-82.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _check_connectivity(state)
        assert issues == []

    def test_isolated_component_with_generator(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", latitude=21.0, longitude=-82.0),
                "bus_b": GuiBus(bus_id="bus_b", latitude=21.1, longitude=-82.0),
                "bus_iso": GuiBus(bus_id="bus_iso", latitude=22.0, longitude=-82.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_iso", node=0, rated_power=10.0,
                ),
            },
        )
        issues = _check_connectivity(state)
        assert len(issues) == 1
        assert issues[0]["type"] == "disconnected"
        assert "bus_iso" in issues[0]["component"]
        # Equipment list should include the generator
        equip_types = [et for et, _, _ in issues[0]["equipment"]]
        assert "generator" in equip_types

    def test_isolated_component_with_converter(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
                "bus_iso1": GuiBus(bus_id="bus_iso1"),
                "bus_iso2": GuiBus(bus_id="bus_iso2"),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_iso1", to_bus="bus_iso2"),
            ],
        )
        issues = _check_connectivity(state)
        assert len(issues) == 1
        equip_types = [et for et, _, _ in issues[0]["equipment"]]
        assert "acdc_converter" in equip_types

    def test_isolated_empty_component_no_equipment(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
                "bus_iso": GuiBus(bus_id="bus_iso"),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _check_connectivity(state)
        assert len(issues) == 1
        assert issues[0]["equipment"] == []

    def test_single_bus_no_issues(self):
        state = _make_state(
            buses={"bus_a": GuiBus(bus_id="bus_a")},
        )
        issues = _check_connectivity(state)
        assert issues == []


# ======================================================================
# TestCheckVoltageConsistency (grid_mapping_steps)
# ======================================================================


class TestCheckVoltageConsistency:
    """Tests for _check_voltage_consistency."""

    def test_same_voltage_lines_no_issues(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", voltage_kv=110.0),
                "bus_b": GuiBus(bus_id="bus_b", voltage_kv=110.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_a"),
                    to_endpoint=EndpointRef("bus", "bus_b"),
                ),
            ],
        )
        issues = _check_voltage_consistency(state)
        assert issues == []

    def test_high_ratio_voltage_mismatch(self):
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=220.0),
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=33.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_hv", to_bus="bus_lv",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_hv"),
                    to_endpoint=EndpointRef("bus", "bus_lv"),
                ),
            ],
        )
        issues = _check_voltage_consistency(state)
        assert len(issues) == 1
        assert issues[0]["type"] == "voltage_mismatch"
        assert issues[0]["line_id"] == "line_0"

    def test_equipment_lines_exempt(self):
        """Lines with non-bus endpoints are not checked."""
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=220.0),
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_lv", to_bus="bus_lv",
                    capacity_mw=10.0,
                    from_endpoint=EndpointRef("generator", "gen_0"),
                    to_endpoint=EndpointRef("bus", "bus_lv"),
                ),
            ],
        )
        issues = _check_voltage_consistency(state)
        assert issues == []

    def test_lines_without_endpoints_exempt(self):
        """Lines without endpoint refs are not checked."""
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=220.0),
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_hv", to_bus="bus_lv",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _check_voltage_consistency(state)
        assert issues == []

    def test_ratio_below_threshold_no_issue(self):
        """Voltage ratio of 1.4 (< 1.5 threshold) should be fine."""
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", voltage_kv=110.0),
                "bus_b": GuiBus(bus_id="bus_b", voltage_kv=80.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_a"),
                    to_endpoint=EndpointRef("bus", "bus_b"),
                ),
            ],
        )
        # 110/80 = 1.375 < 1.5
        issues = _check_voltage_consistency(state)
        assert issues == []

    def test_custom_ratio_threshold(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", voltage_kv=110.0),
                "bus_b": GuiBus(bus_id="bus_b", voltage_kv=80.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_a"),
                    to_endpoint=EndpointRef("bus", "bus_b"),
                ),
            ],
        )
        # With a lower threshold (1.2), 110/80=1.375 should be flagged
        issues = _check_voltage_consistency(state, ratio_threshold=1.2)
        assert len(issues) == 1


# ======================================================================
# TestAuditAllEquipment (grid_mapping_steps)
# ======================================================================


def _make_fully_chained_state():
    """Return a state where gen_0 has the full chain:
    gen_0 --line--> bus_lv --line--> TR --line--> bus_hv
    with bus_lv and bus_hv connected via transformer.
    """
    return _make_state(
        buses={
            "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                             latitude=21.0, longitude=-82.0),
            "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                             latitude=21.1, longitude=-82.0),
        },
        generators={
            "gen_0": GuiGeneratorInstance(
                instance_id="gen_0", unit_key="u0", name="Solar",
                gen_type="Renewable", fuel="Sun",
                bus="bus_lv", node=0, rated_power=10.0,
                latitude=21.0, longitude=-82.0,
            ),
        },
        transformers=[
            GuiTransformer(
                name="TR0", from_bus="bus_lv", to_bus="bus_hv",
                from_voltage_kv=0.48, to_voltage_kv=110.0,
            ),
        ],
        transmission_lines=[
            # Line: gen_0 -> bus_lv
            GuiTransmissionLine(
                line_id="line_0", from_bus="bus_lv", to_bus="bus_lv",
                capacity_mw=10.0,
                from_endpoint=EndpointRef("generator", "gen_0"),
                to_endpoint=EndpointRef("bus", "bus_lv"),
            ),
            # Line: bus_lv -> transformer 0
            GuiTransmissionLine(
                line_id="line_1", from_bus="bus_lv", to_bus="bus_lv",
                capacity_mw=12.0,
                from_endpoint=EndpointRef("bus", "bus_lv"),
                to_endpoint=EndpointRef("transformer", "0"),
            ),
            # Line: transformer 0 -> bus_hv
            GuiTransmissionLine(
                line_id="line_2", from_bus="bus_hv", to_bus="bus_hv",
                capacity_mw=12.0,
                from_endpoint=EndpointRef("transformer", "0"),
                to_endpoint=EndpointRef("bus", "bus_hv"),
            ),
        ],
    )


class TestAuditAllEquipment:
    """Tests for _audit_all_equipment."""

    def test_fully_chained_equipment(self):
        state = _make_fully_chained_state()
        audits = _audit_all_equipment(state)
        assert len(audits) == 1
        assert audits[0]["chain_complete"] is True
        assert audits[0]["failure_reason"] == ""

    def test_missing_connection_line(self):
        state = _make_fully_chained_state()
        # Remove the equipment-to-bus line
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id != "line_0"
        ]
        audits = _audit_all_equipment(state)
        assert len(audits) == 1
        assert audits[0]["chain_complete"] is False
        assert "no connection line" in audits[0]["failure_reason"]

    def test_missing_transformer(self):
        state = _make_fully_chained_state()
        # Remove the transformer
        state.transformers = []
        audits = _audit_all_equipment(state)
        assert len(audits) == 1
        assert audits[0]["chain_complete"] is False

    def test_missing_lv_to_transformer_line(self):
        state = _make_fully_chained_state()
        # Remove the bus_lv -> transformer line
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id != "line_1"
        ]
        audits = _audit_all_equipment(state)
        assert len(audits) == 1
        assert audits[0]["chain_complete"] is False

    def test_missing_transformer_to_hv_line(self):
        state = _make_fully_chained_state()
        # Remove the transformer -> bus_hv line
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id != "line_2"
        ]
        audits = _audit_all_equipment(state)
        assert len(audits) == 1
        assert audits[0]["chain_complete"] is False

    def test_empty_state_no_audits(self):
        state = _make_state()
        audits = _audit_all_equipment(state)
        assert audits == []


# ======================================================================
# TestAuditAllTransformers (grid_mapping_steps)
# ======================================================================


class TestAuditAllTransformers:
    """Tests for _audit_all_transformers."""

    def test_transformer_with_both_lines_ok(self):
        state = _make_fully_chained_state()
        audits = _audit_all_transformers(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is True
        assert audits[0]["missing_sides"] == []

    def test_transformer_missing_from_side(self):
        state = _make_fully_chained_state()
        # Remove the from-side line (bus_lv -> transformer)
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id != "line_1"
        ]
        audits = _audit_all_transformers(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is False
        assert "from" in audits[0]["missing_sides"]

    def test_transformer_missing_to_side(self):
        state = _make_fully_chained_state()
        # Remove the to-side line (transformer -> bus_hv)
        state.transmission_lines = [
            ln for ln in state.transmission_lines if ln.line_id != "line_2"
        ]
        audits = _audit_all_transformers(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is False
        assert "to" in audits[0]["missing_sides"]

    def test_transformer_missing_both(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0),
            },
            transformers=[
                GuiTransformer(name="TR0", from_bus="bus_lv", to_bus="bus_hv"),
            ],
        )
        audits = _audit_all_transformers(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is False
        assert "from" in audits[0]["missing_sides"]
        assert "to" in audits[0]["missing_sides"]

    def test_no_transformers_empty_result(self):
        state = _make_state()
        audits = _audit_all_transformers(state)
        assert audits == []


# ======================================================================
# TestAuditAllConverters (grid_mapping_steps)
# ======================================================================


class TestAuditAllConverters:
    """Tests for _audit_all_converters."""

    def test_acdc_converter_with_both_lines_ok(self):
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac"),
                "bus_dc": GuiBus(bus_id="bus_dc"),
            },
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_ac", to_bus="bus_dc"),
            ],
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_ac", to_bus="bus_ac",
                    capacity_mw=50.0,
                    from_endpoint=EndpointRef("bus", "bus_ac"),
                    to_endpoint=EndpointRef("acdc_converter", "0"),
                ),
                GuiTransmissionLine(
                    line_id="line_1", from_bus="bus_dc", to_bus="bus_dc",
                    capacity_mw=50.0,
                    from_endpoint=EndpointRef("acdc_converter", "0"),
                    to_endpoint=EndpointRef("bus", "bus_dc"),
                ),
            ],
        )
        audits = _audit_all_converters(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is True

    def test_acdc_converter_missing_from_side(self):
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac"),
                "bus_dc": GuiBus(bus_id="bus_dc"),
            },
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_ac", to_bus="bus_dc"),
            ],
            transmission_lines=[
                # Only the to-side line
                GuiTransmissionLine(
                    line_id="line_1", from_bus="bus_dc", to_bus="bus_dc",
                    capacity_mw=50.0,
                    from_endpoint=EndpointRef("acdc_converter", "0"),
                    to_endpoint=EndpointRef("bus", "bus_dc"),
                ),
            ],
        )
        audits = _audit_all_converters(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is False
        assert "from" in audits[0]["missing_sides"]

    def test_freq_converter_with_both_lines_ok(self):
        state = _make_state(
            buses={
                "bus_50": GuiBus(bus_id="bus_50"),
                "bus_60": GuiBus(bus_id="bus_60"),
            },
            freq_converters=[
                GuiFrequencyConverter(name="FC0", from_bus="bus_50", to_bus="bus_60"),
            ],
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_50", to_bus="bus_50",
                    capacity_mw=30.0,
                    from_endpoint=EndpointRef("bus", "bus_50"),
                    to_endpoint=EndpointRef("freq_converter", "0"),
                ),
                GuiTransmissionLine(
                    line_id="line_1", from_bus="bus_60", to_bus="bus_60",
                    capacity_mw=30.0,
                    from_endpoint=EndpointRef("freq_converter", "0"),
                    to_endpoint=EndpointRef("bus", "bus_60"),
                ),
            ],
        )
        audits = _audit_all_converters(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is True

    def test_freq_converter_missing_both(self):
        state = _make_state(
            buses={
                "bus_50": GuiBus(bus_id="bus_50"),
                "bus_60": GuiBus(bus_id="bus_60"),
            },
            freq_converters=[
                GuiFrequencyConverter(name="FC0", from_bus="bus_50", to_bus="bus_60"),
            ],
        )
        audits = _audit_all_converters(state)
        assert len(audits) == 1
        assert audits[0]["ok"] is False
        assert "from" in audits[0]["missing_sides"]
        assert "to" in audits[0]["missing_sides"]

    def test_no_converters_empty_result(self):
        state = _make_state()
        audits = _audit_all_converters(state)
        assert audits == []

    def test_multiple_converters_audited(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
                "bus_c": GuiBus(bus_id="bus_c"),
            },
            acdc_converters=[
                GuiACDCConverter(name="C0", from_bus="bus_a", to_bus="bus_b"),
            ],
            freq_converters=[
                GuiFrequencyConverter(name="FC0", from_bus="bus_b", to_bus="bus_c"),
            ],
        )
        audits = _audit_all_converters(state)
        assert len(audits) == 2
        conv_types = {a["conv_type"] for a in audits}
        assert "acdc_converter" in conv_types
        assert "freq_converter" in conv_types


# ======================================================================
# TestIterativeAutoConnect (grid_mapping_steps)
# ======================================================================


class TestIterativeAutoConnect:
    """Tests for iterative_auto_connect."""

    def test_already_connected_network_zero_created(self):
        state = _make_fully_chained_state()
        model = MockGuiModel(state)
        total, log = iterative_auto_connect(model, state)
        assert total == 0
        # Log should mention convergence
        assert any("converged" in line.lower() or "passed" in line.lower()
                    for line in log)

    def test_disconnected_generator_creates_chain(self):
        """A generator on an isolated LV bus should get auto-connected."""
        state = _make_state(
            buses={
                "bus_main1": GuiBus(bus_id="bus_main1", voltage_kv=110.0,
                                    latitude=21.0, longitude=-82.0),
                "bus_main2": GuiBus(bus_id="bus_main2", voltage_kv=110.0,
                                    latitude=21.1, longitude=-82.0),
                "bus_iso": GuiBus(bus_id="bus_iso", voltage_kv=0.48,
                                  latitude=21.05, longitude=-82.05),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_main1", to_bus="bus_main2",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_main1"),
                    to_endpoint=EndpointRef("bus", "bus_main2"),
                ),
            ],
            generators={
                "gen_iso": GuiGeneratorInstance(
                    instance_id="gen_iso", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_iso", node=0, rated_power=10.0,
                    latitude=21.05, longitude=-82.05,
                ),
            },
        )
        model = MockGuiModel(state)
        total, log = iterative_auto_connect(model, state)
        assert total > 0
        # Should have created bus + transformer + lines
        assert len(state.transformers) >= 1

    def test_converter_missing_lines_creates_lines(self):
        """A converter with missing connection lines should get them."""
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_dc": GuiBus(bus_id="bus_dc", voltage_kv=320.0,
                                 latitude=21.1, longitude=-82.0),
            },
            acdc_converters=[
                GuiACDCConverter(
                    name="C0", from_bus="bus_ac", to_bus="bus_dc",
                    rated_power_mva=50.0,
                ),
            ],
        )
        model = MockGuiModel(state)
        total, log = iterative_auto_connect(model, state)
        # Should have created 2 connection lines for the converter
        assert total >= 2
        conv_audits = _audit_all_converters(state)
        assert all(a["ok"] for a in conv_audits)

    def test_voltage_mismatch_creates_transformer_chain(self):
        """A bus-to-bus line crossing voltage levels should be fixed."""
        state = _make_state(
            buses={
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=220.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_mv": GuiBus(bus_id="bus_mv", voltage_kv=33.0,
                                 latitude=21.1, longitude=-82.0),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_bad", from_bus="bus_hv", to_bus="bus_mv",
                    capacity_mw=100.0,
                    from_endpoint=EndpointRef("bus", "bus_hv"),
                    to_endpoint=EndpointRef("bus", "bus_mv"),
                ),
            ],
        )
        model = MockGuiModel(state)
        total, log = iterative_auto_connect(model, state)
        # The direct line should have been replaced with a transformer chain
        assert total > 0
        assert len(state.transformers) >= 1
        # The original bad line should be gone
        remaining_ids = {ln.line_id for ln in state.transmission_lines}
        assert "line_bad" not in remaining_ids

    def test_max_iterations_respected(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a", voltage_kv=110.0,
                                latitude=21.0, longitude=-82.0),
            },
        )
        model = MockGuiModel(state)
        total, log = iterative_auto_connect(model, state, max_iterations=1)
        # Should finish within 1 iteration
        assert any("1" in line for line in log if "Iteration" in line)


# ======================================================================
# TestVerifyConnectionChain
# ======================================================================


class TestVerifyConnectionChain:
    """Tests for verify_connection_chain."""

    def test_complete_chain_verifies_ok(self):
        state = _make_fully_chained_state()
        ok, reason = verify_connection_chain(
            state, ["gen_0"], "bus_lv", 0, "bus_hv",
        )
        assert ok is True
        assert reason == ""

    def test_missing_lv_bus_fails(self):
        state = _make_fully_chained_state()
        ok, reason = verify_connection_chain(
            state, ["gen_0"], "bus_nonexistent", 0, "bus_hv",
        )
        assert ok is False
        assert "not found" in reason

    def test_bad_transformer_index_fails(self):
        state = _make_fully_chained_state()
        ok, reason = verify_connection_chain(
            state, ["gen_0"], "bus_lv", 99, "bus_hv",
        )
        assert ok is False
        assert "out of range" in reason

    def test_transformer_from_bus_mismatch_fails(self):
        state = _make_fully_chained_state()
        # Call with wrong lv_bus_id
        ok, reason = verify_connection_chain(
            state, ["gen_0"], "bus_hv", 0, "bus_hv",
        )
        assert ok is False
        assert "from_bus" in reason

    def test_equipment_not_found_fails(self):
        state = _make_fully_chained_state()
        ok, reason = verify_connection_chain(
            state, ["nonexistent_gen"], "bus_lv", 0, "bus_hv",
        )
        assert ok is False
        assert "not found" in reason


# ======================================================================
# Additional edge-case tests
# ======================================================================


class TestAutoConnectDispatchToConverter:
    """Verify auto_connect_single_equipment dispatches converters correctly."""

    def test_acdc_converter_dispatched(self):
        state = _make_state(
            buses={
                "bus_ac": GuiBus(bus_id="bus_ac", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_dc": GuiBus(bus_id="bus_dc", voltage_kv=320.0,
                                 latitude=21.1, longitude=-82.0),
            },
            acdc_converters=[
                GuiACDCConverter(
                    name="Conv0", from_bus="bus_ac", to_bus="bus_dc",
                    rated_power_mva=50.0,
                ),
            ],
        )
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "acdc_converter", "0", 21.0, -82.0,
        )
        assert result is True
        assert len(state.transmission_lines) == 2

    def test_freq_converter_dispatched(self):
        state = _make_state(
            buses={
                "bus_50": GuiBus(bus_id="bus_50", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
                "bus_60": GuiBus(bus_id="bus_60", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            freq_converters=[
                GuiFrequencyConverter(
                    name="FC0", from_bus="bus_50", to_bus="bus_60",
                    rated_power_mva=30.0,
                ),
            ],
        )
        model = MockGuiModel(state)
        result = auto_connect_single_equipment(
            model, "freq_converter", "0", 21.0, -82.0,
        )
        assert result is True
        assert len(state.transmission_lines) == 2


class TestPlanAutoCompleteEdgeCases:
    """Additional edge cases for plan_auto_complete."""

    def test_bus_at_zero_zero_skipped(self):
        state = _make_state(
            buses={
                "bus_null": GuiBus(bus_id="bus_null", voltage_kv=0.48,
                                   latitude=0.0, longitude=0.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.0, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_null", node=0, rated_power=10.0,
                    latitude=0.0, longitude=0.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        # bus_null is at (0,0) so it should be skipped
        assert len(plans) == 0

    def test_zero_rated_power_uses_default(self):
        state = _make_state(
            buses={
                "bus_lv": GuiBus(bus_id="bus_lv", voltage_kv=0.48,
                                 latitude=21.0, longitude=-82.0),
                "bus_hv": GuiBus(bus_id="bus_hv", voltage_kv=110.0,
                                 latitude=21.1, longitude=-82.0),
            },
            generators={
                "gen_0": GuiGeneratorInstance(
                    instance_id="gen_0", unit_key="u0", name="Solar",
                    gen_type="Renewable", fuel="Sun",
                    bus="bus_lv", node=0, rated_power=0.0,
                    latitude=21.0, longitude=-82.0,
                ),
            },
        )
        plans = plan_auto_complete(state)
        assert len(plans) == 1
        # With 0 MW equipment, total_capacity_mw should use DEFAULT_CAPACITY_MW
        from esfex.visualization.data.auto_complete import DEFAULT_CAPACITY_MW
        assert plans[0].total_capacity_mw == DEFAULT_CAPACITY_MW


class TestBuildBusAdjacencyEdgeCases:
    """Edge cases for _build_bus_adjacency."""

    def test_line_referencing_nonexistent_bus_ignored(self):
        state = _make_state(
            buses={"bus_a": GuiBus(bus_id="bus_a")},
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_missing",
                    capacity_mw=100.0,
                ),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert adj["bus_a"] == set()

    def test_transformer_referencing_nonexistent_bus_ignored(self):
        state = _make_state(
            buses={"bus_a": GuiBus(bus_id="bus_a")},
            transformers=[
                GuiTransformer(name="TR0", from_bus="bus_a", to_bus="bus_missing"),
            ],
        )
        adj = _build_bus_adjacency(state)
        assert adj["bus_a"] == set()


class TestCheckConnectivityEdgeCases:
    """Edge cases for _check_connectivity."""

    def test_three_components_two_isolated(self):
        state = _make_state(
            buses={
                "bus_a": GuiBus(bus_id="bus_a"),
                "bus_b": GuiBus(bus_id="bus_b"),
                "bus_c": GuiBus(bus_id="bus_c"),
                "bus_d": GuiBus(bus_id="bus_d"),
            },
            transmission_lines=[
                GuiTransmissionLine(
                    line_id="line_0", from_bus="bus_a", to_bus="bus_b",
                    capacity_mw=100.0,
                ),
            ],
        )
        issues = _check_connectivity(state)
        # bus_a-bus_b is main, bus_c and bus_d are isolated
        assert len(issues) == 2

    def test_empty_buses_no_issues(self):
        state = _make_state(buses={})
        issues = _check_connectivity(state)
        assert issues == []
