"""Additive unit tests maximising coverage of ``grid_mapping_builder``.

Targets the internal branches of
:mod:`esfex.visualization.workflows.grid_mapping_builder`:
fuel/technology normalization and auto-creation, every ``_create_*`` phase
helper, the public ``build_grid_from_features`` orchestrator including its
guard clauses, error paths, snap-collapse self-loops, voltage defaults and
the per-circuit line composition.

PySide6 is stubbed only when a working Qt is genuinely unavailable, mirroring
``tests/test_auto_complete.py``.  A lightweight ``MockGuiModel`` mirrors the
GuiModel mutation methods the builder calls so no live QApplication is needed.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest


class _ReqExc(Exception):
    """Stand-in for requests.RequestException (narrow, unlike bare Exception)."""

# ---------------------------------------------------------------------------
# Stub PySide6 + i18n only if a working Qt is absent.
# ---------------------------------------------------------------------------
try:
    import PySide6.QtWidgets  # noqa: F401
    _PYSIDE6_AVAILABLE = True
except Exception:
    _PYSIDE6_AVAILABLE = False

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
    _i18n = ModuleType("esfex.visualization.i18n")
    _i18n.tr = lambda key, **kw: key  # type: ignore[attr-defined]
    sys.modules.setdefault("esfex.visualization.i18n", _i18n)


from esfex.visualization.data.geo_asset_parser import ParseResult  # noqa: E402
from esfex.visualization.data.gui_model import (  # noqa: E402
    GeoPoint,
    GuiBus,
    GuiFuel,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiGeneratorInstance,
    GuiNode,
    GuiNodeDemand,
    GuiSystemState,
    GuiTechnology,
    GuiTransformer,
)
from esfex.visualization.workflows.grid_mapping_fetchers import GridFeature  # noqa: E402
from esfex.visualization.workflows import grid_mapping_builder as gmb  # noqa: E402


# ======================================================================
# Helpers
# ======================================================================


def _make_state(**overrides) -> GuiSystemState:
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
        transmission_lines=overrides.pop("transmission_lines", []),
        transformers=overrides.pop("transformers", []),
        acdc_converters=overrides.pop("acdc_converters", []),
        **overrides,
    )


class MockGuiModel:
    """Mirror the GuiModel mutation methods the builder invokes."""

    def __init__(self, state: GuiSystemState):
        self.state = state

    def add_fuel(self, fuel_id: str, name: str, **kw) -> str:
        f = GuiFuel(fuel_id=fuel_id, name=name)
        for k, v in kw.items():
            if hasattr(f, k):
                setattr(f, k, v)
        self.state.fuels[fuel_id] = f
        return fuel_id

    def add_technology(self, name="T", category="Renewable",
                       tech_id=None, **kw) -> str:
        if tech_id is None:
            tech_id = f"tech_{self.state._next_tech_id}"
            self.state._next_tech_id += 1
        t = GuiTechnology(tech_id=tech_id, name=name, category=category)
        for k, v in kw.items():
            if hasattr(t, k):
                setattr(t, k, v)
        self.state.technologies[tech_id] = t
        return tech_id

    def update_technology(self, tech_id: str, **kw) -> None:
        t = self.state.technologies.get(tech_id)
        if t is None:
            return
        for k, v in kw.items():
            if hasattr(t, k):
                setattr(t, k, v)

    def add_fuel_entry(self, name, fuels=None, node=0,
                       lat=0.0, lng=0.0, **kw) -> int:
        self.state.fuel_entry_points.append(GuiFuelEntryPoint(
            name=name, fuels=fuels or [], node=node,
            coordinate=GeoPoint(lat, lng, name),
        ))
        return len(self.state.fuel_entry_points) - 1

    def add_fuel_storage(self, name, fuel="", node=0, **kw) -> str:
        sid = f"fuel_storage_{len(self.state.fuel_storages)}"
        self.state.fuel_storages[sid] = GuiFuelStorage(
            storage_id=sid, name=name,
            fuels=[fuel] if fuel else [], node=node,
        )
        return sid


def _feat(ftype, name="F", lat=21.0, lng=-82.0, **kw) -> GridFeature:
    return GridFeature(source="osm", feature_type=ftype, name=name,
                       latitude=lat, longitude=lng, **kw)


# ======================================================================
# _normalize_fuel_key
# ======================================================================


class TestNormalizeFuelKey:
    def test_alias_lookup(self):
        assert gmb._normalize_fuel_key("Solar") == "sun"
        assert gmb._normalize_fuel_key("PV") == "sun"
        assert gmb._normalize_fuel_key("photovoltaic") == "sun"

    def test_punctuation_and_spaces_stripped(self):
        assert gmb._normalize_fuel_key("natural-gas") == "naturalgas"
        assert gmb._normalize_fuel_key("natural_gas") == "naturalgas"
        assert gmb._normalize_fuel_key("Natural Gas") == "naturalgas"

    def test_oil_variants_split(self):
        assert gmb._normalize_fuel_key("diesel") == "diesel"
        assert gmb._normalize_fuel_key("HFO") == "fuel_oil"
        assert gmb._normalize_fuel_key("mazut") == "fuel_oil"

    def test_unknown_returns_normalized_input(self):
        # Not in alias table → returns the cleaned string itself.
        assert gmb._normalize_fuel_key("Unobtanium") == "unobtanium"


# ======================================================================
# _find_existing_fuel / _find_existing_technology
# ======================================================================


class TestFindExisting:
    def test_find_fuel_by_id(self):
        state = _make_state()
        state.fuels["Sun"] = GuiFuel(fuel_id="Sun", name="Sun")
        assert gmb._find_existing_fuel(state, "sun") == "Sun"

    def test_find_fuel_by_name(self):
        state = _make_state()
        state.fuels["X1"] = GuiFuel(fuel_id="X1", name="Natural Gas")
        assert gmb._find_existing_fuel(state, "naturalgas") == "X1"

    def test_find_fuel_none(self):
        state = _make_state()
        assert gmb._find_existing_fuel(state, "coal") is None

    def test_find_technology_exact_fuel(self):
        state = _make_state()
        state.technologies["t0"] = GuiTechnology(
            tech_id="t0", name="Solar", category="Renewable", fuel="Sun")
        assert gmb._find_existing_technology(state, "Sun", "sun") == "t0"

    def test_find_technology_by_normalized_fuel(self):
        state = _make_state()
        state.technologies["t0"] = GuiTechnology(
            tech_id="t0", name="GT", category="Non-renewable",
            fuel="Natural Gas")
        # fuel_id arg doesn't match exactly, but normalized fuel does.
        assert gmb._find_existing_technology(state, "Natural_gas",
                                             "naturalgas") == "t0"

    def test_find_technology_none(self):
        state = _make_state()
        state.technologies["t0"] = GuiTechnology(
            tech_id="t0", name="GT", category="Non-renewable", fuel="")
        assert gmb._find_existing_technology(state, "Coal", "coal") is None


# ======================================================================
# _create_fuels_and_technologies
# ======================================================================


class TestCreateFuelsAndTechnologies:
    def test_creates_default_fuel_and_tech(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        gens = [_feat("generator", fuel="coal", capacity_mw=100)]
        fuel_remap, tech_remap = gmb._create_fuels_and_technologies(
            model, gens, result)
        assert fuel_remap["coal"] == "Coal"
        assert tech_remap[("Coal ST", "Coal")] is not None
        assert result.fuels_created == 1
        assert result.technologies_created == 1
        assert "Coal" in model.state.fuels

    def test_unfueled_generators_get_other_fuel_and_tech(self):
        # Generators with no detected fuel must still get a real fuel AND a
        # technology (routed through the catalog "Other" entry) — never a
        # dangling reference with technology_id=None.
        model = MockGuiModel(_make_state())
        result = ParseResult()
        gens = [
            _feat("generator", fuel="None"),
            _feat("generator", fuel=""),
        ]
        fuel_remap, tech_remap = gmb._create_fuels_and_technologies(
            model, gens, result)
        assert fuel_remap["Other"] == "Other"
        assert tech_remap[("Other", "Other")] is not None
        assert "Other" in model.state.fuels
        assert result.fuels_created == 1 and result.technologies_created == 1

    def test_none_fuel_routed_to_other(self):
        # A literal "none" fuel is treated as undetected and routed to "Other"
        # so the generator still gets a fuel + technology.
        model = MockGuiModel(_make_state())
        result = ParseResult()
        gens = [_feat("generator", fuel="none")]
        fuel_remap, tech_remap = gmb._create_fuels_and_technologies(
            model, gens, result)
        assert fuel_remap["Other"] == "Other"
        assert tech_remap[("Other", "Other")] is not None

    def test_reuses_existing_fuel(self):
        state = _make_state()
        state.fuels["Sun"] = GuiFuel(fuel_id="Sun", name="Sun")
        model = MockGuiModel(state)
        result = ParseResult()
        gens = [_feat("generator", fuel="solar")]
        fuel_remap, _ = gmb._create_fuels_and_technologies(
            model, gens, result)
        assert fuel_remap["solar"] == "Sun"
        assert result.fuels_created == 0  # reused, not created

    def test_generic_fuel_for_unknown_key(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        gens = [_feat("generator", fuel="Magic Dust")]
        fuel_remap, tech_remap = gmb._create_fuels_and_technologies(
            model, gens, result)
        # Unknown key → generic fuel id with spaces replaced.
        assert fuel_remap["Magic Dust"] == "Magic_Dust"
        assert result.fuels_created == 1
        # Unknown canonical → a generic technology is created (never None).
        assert tech_remap[("Other", "Magic_Dust")] is not None
        assert result.technologies_created == 1

    def test_existing_tech_by_name_reused(self):
        state = _make_state()
        state.fuels["Coal"] = GuiFuel(fuel_id="Coal", name="Coal")
        # Existing tech with the catalog name but an inconsistent fuel id is
        # reused (matched by name) and its fuel is made consistent.
        state.technologies["t0"] = GuiTechnology(
            tech_id="t0", name="Coal Steam Turbine", category="Non-renewable",
            fuel="coal")
        model = MockGuiModel(state)
        result = ParseResult()
        gens = [_feat("generator", fuel="coal")]
        _, tech_remap = gmb._create_fuels_and_technologies(
            model, gens, result)
        assert tech_remap[("Coal ST", "Coal")] == "t0"
        assert state.technologies["t0"].fuel == "Coal"
        assert result.technologies_created == 0


# ======================================================================
# Standard-line-type capacity / impedance (PyPSA catalog methodology)
# ======================================================================


class TestStandardLineTypeCapacity:
    @pytest.mark.parametrize("kv,expected_mw", [
        # sqrt(3) * V * I_nom(nearest type) * 0.7  (per circuit)
        (110, 98.7),
        (220, 344.1),
        (380, 1188.7),
        (500, 1819.1),
    ])
    def test_thermal_capacity(self, kv, expected_mw):
        from esfex.visualization.workflows.grid_mapping_quality import (
            estimate_line_capacity_mw,
        )
        assert estimate_line_capacity_mw(kv) == pytest.approx(expected_mw, rel=0.02)

    def test_capacity_increases_with_voltage(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            estimate_line_capacity_mw,
        )
        caps = [estimate_line_capacity_mw(v) for v in (66, 110, 220, 380, 500, 750)]
        assert caps == sorted(caps)  # strictly increasing with voltage

    def test_circuits_scale_capacity(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            estimate_line_capacity_mw,
        )
        one = estimate_line_capacity_mw(220, num_circuits=1)
        two = estimate_line_capacity_mw(220, num_circuits=2)
        assert two == pytest.approx(2 * one)

    def test_voltage_snaps_to_nearest_type(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            _nearest_std_line_type,
        )
        assert _nearest_std_line_type(400)[0] == 380.0   # 400 -> 380
        assert _nearest_std_line_type(230)[0] == 220.0   # 230 -> 220
        assert _nearest_std_line_type(345)[0] == 380.0   # 345 -> 380 (closer)

    def test_impedance_from_same_type(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            estimate_line_rxb_per_km,
        )
        r, x, b = estimate_line_rxb_per_km(380)
        assert r == pytest.approx(0.030) and x == pytest.approx(0.246)
        assert b > 0  # susceptance derived from the type capacitance


# ======================================================================
# _create_buses_from_substation
# ======================================================================


class TestCreateBusesFromSubstation:
    def test_per_voltage_creates_two_buses_and_transformer(self):
        state = _make_state(buses={})
        result = ParseResult()
        sub = _feat("substation", name="S", voltage_kv=220.0,
                    voltage_kv_secondary=110.0)
        bus_ids = gmb._create_buses_from_substation(
            state, sub, "per_voltage", 5.0, result, {}, None)
        assert len(bus_ids) == 2
        assert result.transformers_added == 1
        assert len(state.transformers) == 1

    def test_per_voltage_snap_collapse_no_transformer(self):
        # Both voltage levels snap to a single existing bus (huge snap_km
        # and an existing close bus) → no self-transformer.
        state = _make_state(buses={
            "bus0": GuiBus(bus_id="bus0", parent_node=0, voltage_kv=220.0,
                           latitude=21.0, longitude=-82.0),
        })
        result = ParseResult()
        sub = _feat("substation", name="S", voltage_kv=220.0,
                    voltage_kv_secondary=220.0)
        bus_ids = gmb._create_buses_from_substation(
            state, sub, "per_voltage", 1000.0, result, {}, force_node=0)
        assert bus_ids[0] == bus_ids[1]
        assert result.transformers_added == 0

    def test_per_substation_single_bus(self):
        state = _make_state(buses={})
        result = ParseResult()
        sub = _feat("substation", name="S", voltage_kv=220.0)
        bus_ids = gmb._create_buses_from_substation(
            state, sub, "per_substation", 5.0, result, {}, None)
        assert len(bus_ids) == 1
        assert result.transformers_added == 0

    def test_single_bus_default_voltage_when_missing(self):
        state = _make_state(buses={})
        result = ParseResult()
        sub = _feat("substation", name="", voltage_kv=0.0)
        bus_ids = gmb._create_buses_from_substation(
            state, sub, "per_substation", 5.0, result, {}, None)
        bus = state.buses[bus_ids[0]]
        assert bus.voltage_kv == 220.0


# ======================================================================
# _ensure_generator_stepup
# ======================================================================


class TestEnsureGeneratorStepup:
    def test_missing_gen_bus_returns(self):
        state = _make_state(buses={})
        result = ParseResult()
        gen = _feat("generator", name="G")
        gmb._ensure_generator_stepup(state, gen, 0, "nonexistent", result)
        assert result.transformers_added == 0

    def test_no_higher_voltage_bus_no_transformer(self):
        state = _make_state(buses={
            "bus_lo": GuiBus(bus_id="bus_lo", parent_node=0, voltage_kv=34.5,
                             latitude=21.0, longitude=-82.0),
        })
        result = ParseResult()
        gen = _feat("generator", name="G")
        gmb._ensure_generator_stepup(state, gen, 0, "bus_lo", result)
        assert result.transformers_added == 0

    def test_lower_voltage_sibling_bus_skipped(self):
        # A sibling bus at or below the generator voltage is not a step-up
        # target → exercises the ``bv <= gen_v`` continue.
        state = _make_state(buses={
            "bus_gen": GuiBus(bus_id="bus_gen", parent_node=0,
                              voltage_kv=110.0,
                              latitude=21.0, longitude=-82.0),
            "bus_low": GuiBus(bus_id="bus_low", parent_node=0,
                              voltage_kv=34.5,
                              latitude=21.01, longitude=-82.0),
        })
        result = ParseResult()
        gen = _feat("generator", name="G")
        gmb._ensure_generator_stepup(state, gen, 0, "bus_gen", result)
        # Only a lower-voltage sibling exists → no GSU created.
        assert result.transformers_added == 0

    def test_creates_gsu_to_hv_bus(self):
        state = _make_state(buses={
            "bus_lo": GuiBus(bus_id="bus_lo", parent_node=0, voltage_kv=34.5,
                             latitude=21.0, longitude=-82.0),
            "bus_hi": GuiBus(bus_id="bus_hi", parent_node=0, voltage_kv=220.0,
                             latitude=21.01, longitude=-82.0),
        })
        result = ParseResult()
        gen = _feat("generator", name="G", capacity_mw=50.0)
        gmb._ensure_generator_stepup(state, gen, 0, "bus_lo", result)
        assert result.transformers_added == 1
        tr = state.transformers[-1]
        assert tr.from_bus == "bus_hi"
        assert tr.to_bus == "bus_lo"

    def test_skips_when_transformer_already_bridges(self):
        state = _make_state(buses={
            "bus_lo": GuiBus(bus_id="bus_lo", parent_node=0, voltage_kv=34.5,
                             latitude=21.0, longitude=-82.0),
            "bus_hi": GuiBus(bus_id="bus_hi", parent_node=0, voltage_kv=220.0,
                             latitude=21.01, longitude=-82.0),
        }, transformers=[
            GuiTransformer(name="existing", from_bus="bus_lo",
                           to_bus="bus_hi"),
        ])
        result = ParseResult()
        gen = _feat("generator", name="G")
        gmb._ensure_generator_stepup(state, gen, 0, "bus_lo", result)
        assert result.transformers_added == 0

    def test_tiebreak_prefers_nearer_equal_voltage_bus(self):
        # Two HV buses at the same (higher) voltage: the farther one is
        # inserted first, the nearer one second → exercises the
        # ``bv == hv_v and d < hv_d`` proximity tie-break.
        state = _make_state(buses={
            "bus_lo": GuiBus(bus_id="bus_lo", parent_node=0, voltage_kv=34.5,
                             latitude=21.0, longitude=-82.0),
            "bus_far": GuiBus(bus_id="bus_far", parent_node=0,
                              voltage_kv=220.0,
                              latitude=22.0, longitude=-83.0),
            "bus_near": GuiBus(bus_id="bus_near", parent_node=0,
                               voltage_kv=220.0,
                               latitude=21.001, longitude=-82.001),
        })
        result = ParseResult()
        gen = _feat("generator", name="G", capacity_mw=50.0)
        gmb._ensure_generator_stepup(state, gen, 0, "bus_lo", result)
        assert result.transformers_added == 1
        tr = state.transformers[-1]
        assert tr.from_bus == "bus_near"

    def test_zero_gen_voltage_uses_default_lo(self):
        # gen bus has voltage 0 → v_lo defaults to 34.5 in the GSU.
        state = _make_state(buses={
            "bus_lo": GuiBus(bus_id="bus_lo", parent_node=0, voltage_kv=0.0,
                             latitude=21.0, longitude=-82.0),
            "bus_hi": GuiBus(bus_id="bus_hi", parent_node=0, voltage_kv=220.0,
                             latitude=21.01, longitude=-82.0),
        })
        result = ParseResult()
        gen = _feat("generator", name="G")
        gmb._ensure_generator_stepup(state, gen, 0, "bus_lo", result)
        tr = state.transformers[-1]
        assert tr.to_voltage_kv == 34.5


# ======================================================================
# _create_generator
# ======================================================================


class TestCreateGenerator:
    def test_basic_generator(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=50.0, fuel="solar")
        gmb._create_generator(state, gen, 5.0, result, {}, None)
        assert result.generators_added == 1
        inst = next(iter(state.generators.values()))
        assert inst.rated_power == 50.0

    def test_fuel_remap_applied(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=10.0, fuel="solar")
        gmb._create_generator(
            state, gen, 5.0, result, {}, None,
            fuel_remap={"solar": "Sun"}, tech_remap={("PV", "Sun"): "tech_0"})
        inst = next(iter(state.generators.values()))
        assert inst.fuel == "Sun"
        assert inst.technology_id == "tech_0"

    def test_empty_fuel_falls_back_to_other(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=10.0, fuel="")
        gmb._create_generator(state, gen, 5.0, result, {}, None)
        inst = next(iter(state.generators.values()))
        assert inst.fuel == "Other"

    def test_initial_age_from_commissioning_year(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=10.0, fuel="coal",
                    commissioning_year=2000)
        gmb._create_generator(state, gen, 5.0, result, {}, None)
        inst = next(iter(state.generators.values()))
        assert inst.initial_age > 0

    def test_no_commissioning_year_zero_age(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=10.0, fuel="coal")
        gmb._create_generator(state, gen, 5.0, result, {}, None)
        inst = next(iter(state.generators.values()))
        assert inst.initial_age == 0

    def test_gen_type_default_non_renewable(self):
        state = _make_state()
        result = ParseResult()
        gen = _feat("generator", name="G1", capacity_mw=10.0, fuel="coal",
                    gen_type="")
        gmb._create_generator(state, gen, 5.0, result, {}, None)
        inst = next(iter(state.generators.values()))
        assert inst.gen_type == "Non-renewable"


# ======================================================================
# _create_battery
# ======================================================================


class TestCreateBattery:
    def test_explicit_energy(self):
        state = _make_state()
        result = ParseResult()
        bat = _feat("battery", name="B", capacity_mw=10.0, energy_mwh=80.0)
        gmb._create_battery(state, bat, 5.0, result, {}, None)
        inst = next(iter(state.batteries.values()))
        assert inst.capacity == 80.0
        assert result.batteries_added == 1

    def test_default_4h_duration(self):
        state = _make_state()
        result = ParseResult()
        bat = _feat("battery", name="B", capacity_mw=10.0, energy_mwh=0.0)
        gmb._create_battery(state, bat, 5.0, result, {}, None)
        inst = next(iter(state.batteries.values()))
        assert inst.capacity == 40.0


# ======================================================================
# _create_line
# ======================================================================


class TestCreateLine:
    def test_no_geometry_skipped(self):
        state = _make_state()
        result = ParseResult()
        line = _feat("line", name="L", line_coords=[])
        gmb._create_line(state, line, 5.0, result, {}, None)
        assert result.lines_added == 0
        assert any("no geometry" in w for w in result.warnings)

    def test_single_point_skipped(self):
        state = _make_state()
        result = ParseResult()
        line = _feat("line", name="L", line_coords=[(21.0, -82.0)])
        gmb._create_line(state, line, 5.0, result, {}, None)
        assert result.lines_added == 0

    def test_endpoints_same_bus_skipped(self):
        # Both endpoints snap to the same existing bus.
        state = _make_state(buses={
            "bus0": GuiBus(bus_id="bus0", parent_node=0, voltage_kv=110.0,
                           latitude=21.0, longitude=-82.0),
        })
        result = ParseResult()
        line = _feat("line", name="L",
                     line_coords=[(21.0, -82.0), (21.0001, -82.0001)])
        gmb._create_line(state, line, 1000.0, result, {}, force_node=0)
        assert result.lines_added == 0
        assert any("same bus" in w for w in result.warnings)

    def test_creates_line_with_waypoints_and_impedance(self):
        state = _make_state(buses={})
        result = ParseResult()
        line = _feat("line", name="L", voltage_kv=220.0, num_circuits=2,
                     line_coords=[(21.0, -82.0), (21.3, -82.3), (21.6, -82.6)])
        gmb._create_line(state, line, 5.0, result, {}, None)
        assert result.lines_added == 1
        gl = state.transmission_lines[-1]
        assert len(gl.waypoints) == 1
        assert gl.length_km is not None and gl.length_km > 0
        assert gl.resistance_pu is not None
        assert gl.num_circuits == 2

    def test_no_voltage_leaves_impedance_none(self):
        state = _make_state(buses={})
        result = ParseResult()
        line = _feat("line", name="L", voltage_kv=0.0,
                     line_coords=[(21.0, -82.0), (22.0, -83.0)])
        gmb._create_line(state, line, 5.0, result, {}, None)
        gl = state.transmission_lines[-1]
        assert gl.voltage_kv is None
        assert gl.length_km is None
        assert gl.resistance_pu is None

    def test_explicit_capacity_scaled_by_circuits(self):
        state = _make_state(buses={})
        result = ParseResult()
        line = _feat("line", name="L", voltage_kv=220.0, capacity_mw=300.0,
                     num_circuits=3,
                     line_coords=[(21.0, -82.0), (22.0, -83.0)])
        gmb._create_line(state, line, 5.0, result, {}, None)
        gl = state.transmission_lines[-1]
        assert gl.capacity_mw == 900.0

    def test_estimated_capacity_when_unspecified(self):
        state = _make_state(buses={})
        result = ParseResult()
        line = _feat("line", name="L", voltage_kv=500.0, capacity_mw=0.0,
                     num_circuits=1,
                     line_coords=[(21.0, -82.0), (22.0, -83.0)])
        gmb._create_line(state, line, 5.0, result, {}, None)
        gl = state.transmission_lines[-1]
        # Thermal rating of the nearest (500 kV) standard type:
        # sqrt(3) * 500 * 3.0 kA * 0.7 ~= 1819 MW.
        assert gl.capacity_mw == pytest.approx(1819.1, rel=0.01)


# ======================================================================
# _create_transformer
# ======================================================================


class TestCreateTransformer:
    def test_creates_transformer(self):
        state = _make_state(buses={})
        result = ParseResult()
        tr = _feat("transformer", name="TR", voltage_kv=220.0,
                   voltage_kv_secondary=110.0, capacity_mw=200.0)
        gmb._create_transformer(state, tr, 5.0, result, {}, None)
        assert result.transformers_added == 1
        gt = state.transformers[-1]
        assert gt.from_voltage_kv == 220.0
        assert gt.to_voltage_kv == 110.0
        assert gt.rated_power_mva == 200.0

    def test_default_voltages_and_capacity(self):
        state = _make_state(buses={})
        result = ParseResult()
        tr = _feat("transformer", name="TR", voltage_kv=0.0,
                   voltage_kv_secondary=0.0, capacity_mw=0.0)
        gmb._create_transformer(state, tr, 5.0, result, {}, None)
        gt = state.transformers[-1]
        assert gt.from_voltage_kv == 220.0
        assert gt.to_voltage_kv == 110.0
        assert gt.rated_power_mva == 100.0

    def test_self_loop_skipped(self):
        # HV and LV sides snap to the same existing bus.
        state = _make_state(buses={
            "bus0": GuiBus(bus_id="bus0", parent_node=0, voltage_kv=220.0,
                           latitude=21.0, longitude=-82.0),
        })
        result = ParseResult()
        tr = _feat("transformer", name="TR", voltage_kv=220.0,
                   voltage_kv_secondary=220.0)
        gmb._create_transformer(state, tr, 1000.0, result, {}, force_node=0)
        assert result.transformers_added == 0
        assert any("self-loop" in w for w in result.warnings)


# ======================================================================
# _create_converter
# ======================================================================


class TestCreateConverter:
    def test_creates_converter(self):
        state = _make_state(buses={})
        result = ParseResult()
        conv = _feat("converter", name="C", voltage_kv=400.0,
                     capacity_mw=500.0)
        # snap_km small enough that the ~33 m DC offset does not collapse
        # onto the AC bus.
        gmb._create_converter(state, conv, 0.01, result, {}, None)
        assert result.acdc_converters_added == 1
        gc = state.acdc_converters[-1]
        assert gc.rated_power_mva == 500.0

    def test_default_voltage_and_capacity(self):
        state = _make_state(buses={})
        result = ParseResult()
        conv = _feat("converter", name="C", voltage_kv=0.0, capacity_mw=0.0)
        gmb._create_converter(state, conv, 0.01, result, {}, None)
        gc = state.acdc_converters[-1]
        assert gc.from_voltage_kv == 220.0
        assert gc.rated_power_mva == 100.0

    def test_self_loop_skipped(self):
        state = _make_state(buses={
            "bus0": GuiBus(bus_id="bus0", parent_node=0, voltage_kv=220.0,
                           current_type="AC",
                           latitude=21.0, longitude=-82.0),
        })
        result = ParseResult()
        conv = _feat("converter", name="C", voltage_kv=220.0)
        gmb._create_converter(state, conv, 1000.0, result, {}, force_node=0)
        assert result.acdc_converters_added == 0
        assert any("self-loop" in w for w in result.warnings)


# ======================================================================
# _create_fuel_entry / _create_fuel_storage
# ======================================================================


class TestCreateFuelEntry:
    def test_maps_known_fuel_to_default_id(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fe = _feat("fuel_entry", name="FE", fuel="gas")
        gmb._create_fuel_entry(model, fe, 5.0, result, {}, None)
        assert result.fuel_entries_added == 1
        entry = model.state.fuel_entry_points[-1]
        assert entry.fuels == ["Natural_gas"]

    def test_existing_fuel_preferred(self):
        state = _make_state()
        state.fuels["MyGas"] = GuiFuel(fuel_id="MyGas", name="Natural Gas")
        model = MockGuiModel(state)
        result = ParseResult()
        fe = _feat("fuel_entry", name="FE", fuel="gas")
        gmb._create_fuel_entry(model, fe, 5.0, result, {}, None)
        entry = model.state.fuel_entry_points[-1]
        assert entry.fuels == ["MyGas"]

    def test_empty_fuel_gives_no_fuels(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fe = _feat("fuel_entry", name="FE", fuel="")
        gmb._create_fuel_entry(model, fe, 5.0, result, {}, None)
        entry = model.state.fuel_entry_points[-1]
        assert entry.fuels == []

    def test_force_node_used(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fe = _feat("fuel_entry", name="FE", fuel="coal")
        gmb._create_fuel_entry(model, fe, 5.0, result, {}, force_node=0)
        entry = model.state.fuel_entry_points[-1]
        assert entry.node == 0

    def test_unknown_fuel_kept_raw(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fe = _feat("fuel_entry", name="FE", fuel="Unobtanium")
        gmb._create_fuel_entry(model, fe, 5.0, result, {}, None)
        entry = model.state.fuel_entry_points[-1]
        # Unknown canonical, no existing, not in defaults → raw kept.
        assert entry.fuels == ["Unobtanium"]


class TestCreateFuelStorage:
    def test_maps_known_fuel(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fs = _feat("fuel_storage", name="FS", fuel="diesel")
        gmb._create_fuel_storage(model, fs, 5.0, result, {}, None)
        assert result.fuel_storages_added == 1
        store = list(model.state.fuel_storages.values())[-1]
        assert store.fuels == ["Diesel"]

    def test_existing_fuel_preferred(self):
        state = _make_state()
        state.fuels["D1"] = GuiFuel(fuel_id="D1", name="Diesel")
        model = MockGuiModel(state)
        result = ParseResult()
        fs = _feat("fuel_storage", name="FS", fuel="diesel")
        gmb._create_fuel_storage(model, fs, 5.0, result, {}, None)
        store = list(model.state.fuel_storages.values())[-1]
        assert store.fuels == ["D1"]

    def test_empty_fuel(self):
        model = MockGuiModel(_make_state())
        result = ParseResult()
        fs = _feat("fuel_storage", name="FS", fuel="")
        gmb._create_fuel_storage(model, fs, 5.0, result, {}, force_node=0)
        store = list(model.state.fuel_storages.values())[-1]
        assert store.fuels == []
        assert store.node == 0


# ======================================================================
# build_grid_from_features (orchestrator)
# ======================================================================


class TestBuildGridFromFeatures:
    def test_empty_features(self):
        model = MockGuiModel(_make_state())
        result = gmb.build_grid_from_features(model, [])
        assert isinstance(result, ParseResult)
        assert result.generators_added == 0

    def test_include_filter(self):
        model = MockGuiModel(_make_state(buses={}))
        feats = [
            _feat("substation", name="S1", voltage_kv=220.0, include=False),
            _feat("generator", name="G1", capacity_mw=10.0, fuel="solar",
                  include=True),
        ]
        result = gmb.build_grid_from_features(model, feats)
        # Excluded substation not processed; generator processed.
        assert result.generators_added == 1

    def test_full_pipeline_all_types(self):
        model = MockGuiModel(_make_state(buses={}))
        feats = [
            _feat("substation", name="S1", voltage_kv=220.0,
                  voltage_kv_secondary=110.0),
            _feat("generator", name="G1", capacity_mw=50.0, fuel="solar"),
            _feat("battery", name="B1", capacity_mw=5.0),
            _feat("line", name="L1", voltage_kv=220.0,
                  line_coords=[(21.0, -82.0), (21.5, -82.5)]),
            _feat("transformer", name="TR1", voltage_kv=220.0,
                  voltage_kv_secondary=110.0),
            _feat("converter", name="C1", voltage_kv=220.0),
            _feat("fuel_entry", name="FE1", fuel="gas"),
            _feat("fuel_storage", name="FS1", fuel="diesel"),
        ]
        result = gmb.build_grid_from_features(model, feats)
        assert result.generators_added == 1
        assert result.batteries_added == 1
        assert result.lines_added == 1
        assert result.fuel_entries_added == 1
        assert result.fuel_storages_added == 1
        assert result.fuels_created >= 1

    def test_substation_exception_captured_as_warning(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))

        def _boom(*a, **kw):
            raise RuntimeError("boom-sub")

        monkeypatch.setattr(gmb, "_create_buses_from_substation", _boom)
        feats = [_feat("substation", name="BadSub", voltage_kv=220.0)]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadSub" in w and "boom-sub" in w for w in result.warnings)

    def test_generator_exception_captured_as_warning(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))

        def _boom(*a, **kw):
            raise RuntimeError("boom-gen")

        monkeypatch.setattr(gmb, "_create_generator", _boom)
        feats = [_feat("generator", name="BadGen", fuel="coal")]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadGen" in w for w in result.warnings)

    def test_battery_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_battery",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-bat")))
        feats = [_feat("battery", name="BadBat", capacity_mw=5.0)]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadBat" in w for w in result.warnings)

    def test_line_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_line",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-line")))
        feats = [_feat("line", name="BadLine",
                       line_coords=[(21.0, -82.0), (22.0, -83.0)])]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadLine" in w for w in result.warnings)

    def test_transformer_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_transformer",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-tr")))
        feats = [_feat("transformer", name="BadTR", voltage_kv=220.0)]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadTR" in w for w in result.warnings)

    def test_converter_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_converter",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-cv")))
        feats = [_feat("converter", name="BadCV", voltage_kv=220.0)]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadCV" in w for w in result.warnings)

    def test_fuel_entry_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_fuel_entry",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-fe")))
        feats = [_feat("fuel_entry", name="BadFE", fuel="gas")]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadFE" in w for w in result.warnings)

    def test_fuel_storage_exception_captured(self, monkeypatch):
        model = MockGuiModel(_make_state(buses={}))
        monkeypatch.setattr(
            gmb, "_create_fuel_storage",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-fs")))
        feats = [_feat("fuel_storage", name="BadFS", fuel="coal")]
        result = gmb.build_grid_from_features(model, feats)
        assert any("BadFS" in w for w in result.warnings)

    def test_phase9_repair_exception_captured(self, monkeypatch):
        # Force the consistency-repair import block to raise so the outer
        # try/except warning path runs.
        import esfex.visualization.workflows.grid_mapping_quality as q
        monkeypatch.setattr(
            q, "repair_fuel_consistency",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom-rep")))
        model = MockGuiModel(_make_state(buses={}))
        feats = [_feat("generator", name="G1", fuel="coal", capacity_mw=10)]
        result = gmb.build_grid_from_features(model, feats)
        assert any("consistency repair" in w for w in result.warnings)

    def test_node_coupling_warning_branch(self, monkeypatch):
        # Make repair_node_internal_coupling report coupled buses so the
        # transformers_added accumulation + warning branch runs.
        import esfex.visualization.workflows.grid_mapping_quality as q
        monkeypatch.setattr(q, "repair_fuel_consistency",
                            lambda s: {"fuels_added": 0, "techs_added": 0})
        monkeypatch.setattr(q, "repair_bus_roles_and_demand",
                            lambda s: {"buses_role_changed": 0,
                                       "nodes_redistributed": 0})
        monkeypatch.setattr(
            q, "repair_node_internal_coupling",
            lambda s: {"buses_coupled": 2, "transformers_added": 1,
                       "lines_added": 1, "nodes_restructured": 1})
        model = MockGuiModel(_make_state(buses={}))
        feats = [_feat("generator", name="G1", fuel="coal", capacity_mw=10)]
        result = gmb.build_grid_from_features(model, feats)
        assert result.transformers_added >= 1
        assert any("star-coupling" in w for w in result.warnings)

    def test_substation_with_osm_id_tracked(self):
        # Non-empty osm_id exercises the substation_buses mapping branch.
        model = MockGuiModel(_make_state(buses={}))
        feats = [_feat("substation", name="S1", voltage_kv=220.0,
                       osm_id="way/123")]
        result = gmb.build_grid_from_features(model, feats)
        assert result.buses_added >= 1

    def test_substation_without_osm_id_not_tracked(self):
        # osm_id empty → skip the substation_buses mapping branch.
        model = MockGuiModel(_make_state(buses={}))
        feats = [_feat("substation", name="S1", voltage_kv=220.0, osm_id="")]
        result = gmb.build_grid_from_features(model, feats)
        assert result.buses_added >= 1

    def test_target_node_forces_placement(self):
        nodes = [
            GuiNode(index=0, name="N0",
                    demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
            GuiNode(index=1, name="N1",
                    demand=GuiNodeDemand(peak_mw=100.0, total_mwh=800.0)),
        ]
        state = _make_state(nodes=nodes, buses={})
        model = MockGuiModel(state)
        feats = [_feat("generator", name="G1", capacity_mw=10.0, fuel="solar")]
        gmb.build_grid_from_features(model, feats, target_node=1)
        inst = next(iter(state.generators.values()))
        assert inst.node == 1


class TestOSMFetcherTiling:
    """OSM fetcher splits large regions into tiles and treats a server-side
    timeout (HTTP 200 + 'runtime error' remark) as a retryable failure rather
    than a silently empty success. Regression for the all-of-Japan Grid Builder
    timeout."""

    def _fetcher(self, bounds):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher(bounds)

    def test_large_region_tiled_and_covers_bbox(self):
        f = self._fetcher((24.0, 122.9, 45.6, 145.8))  # Japan
        tiles = f._tile_bboxes()
        assert len(tiles) > 1
        # every tile within the max span
        assert all(
            (t[2] - t[0]) <= 5.0 + 1e-9 and (t[3] - t[1]) <= 5.0 + 1e-9
            for t in tiles
        )
        # union of tiles equals the original bbox
        assert min(t[0] for t in tiles) == pytest.approx(24.0)
        assert min(t[1] for t in tiles) == pytest.approx(122.9)
        assert max(t[2] for t in tiles) == pytest.approx(45.6)
        assert max(t[3] for t in tiles) == pytest.approx(145.8)

    def test_build_query_contains_bbox_and_types(self):
        f = self._fetcher((0.0, 0.0, 1.0, 1.0))
        f.element_types = {"line", "substation"}
        q = f._build_query("0,0,1,1")
        assert "[out:json][timeout:180]" in q
        assert '"power"="line"' in q
        assert '"power"="substation"' in q
        assert "(0,0,1,1)" in q

    def test_post_query_runtime_error_is_not_silent_success(self):
        f = self._fetcher((0.0, 0.0, 1.0, 1.0))

        class _Resp:
            status_code = 200
            content = b'{"elements": [], "remark": "runtime error: Query timed out"}'
            text = ""

        class _FakeRequests:
            RequestException = _ReqExc

            def post(self, *a, **kw):
                return _Resp()

        class _FakeApi:
            def parse_json(self, content):
                raise AssertionError("parse_json called on a timeout remark")

        class _FakeTime:
            def sleep(self, *_):
                pass

        with pytest.raises(RuntimeError, match="(?i)overpass"):
            f._post_query(_FakeApi(), {}, "q", _FakeTime(), _FakeRequests())

    def test_post_query_clean_body_parses(self):
        f = self._fetcher((0.0, 0.0, 1.0, 1.0))
        sentinel = object()

        class _Resp:
            status_code = 200
            content = b'{"elements": []}'
            text = ""

        class _FakeRequests:
            RequestException = _ReqExc

            def post(self, *a, **kw):
                return _Resp()

        class _FakeApi:
            def parse_json(self, content):
                return sentinel

        class _FakeTime:
            def sleep(self, *_):
                pass

        out = f._post_query(_FakeApi(), {}, "q", _FakeTime(), _FakeRequests())
        assert out is sentinel


class TestOSMFetcher400Handling:
    """HTTP 400 from Overpass covers both genuine query-syntax errors (fatal)
    and transient dispatcher/load timeouts (retryable). Regression for the
    Grid Builder raising on a transient 400 mid-fetch."""

    def _fetcher(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher((0.0, 0.0, 1.0, 1.0))

    def _make_requests(self, body: bytes, status: int = 400):
        class _Resp:
            status_code = status
            content = body
            text = body.decode("utf-8", "replace")

        class _FakeRequests:
            RequestException = _ReqExc
            calls = 0

            def post(self, *a, **kw):
                _FakeRequests.calls += 1
                return _Resp()

        return _FakeRequests()

    class _FakeTime:
        def sleep(self, *_):
            pass

    class _NoParseApi:
        def parse_json(self, content):
            raise AssertionError("parse_json must not run on a 400")

    def test_400_is_retried_not_fatal(self):
        # Our Overpass query is machine-generated and static, so an
        # intermittent 400 means the server is busy, not a syntax error.
        # It must be retried (across mirrors), not raised on the first hit.
        f = self._fetcher()
        req = self._make_requests(
            b'<html><body><p><strong style="color:#FF0000">Error</strong>: '
            b'runtime error: Dispatcher_Client::request_read_and_idx::timeout. '
            b'The server is probably too busy.</p></body></html>'
        )
        with pytest.raises(RuntimeError, match="(?i)overpass"):
            f._post_query(self._NoParseApi(), {}, "q", self._FakeTime(), req)
        assert req.calls > 1  # retried, not fatal

    def test_overload_400_is_retried_then_raised(self):
        f = self._fetcher()
        req = self._make_requests(
            b'<html>Error: Dispatcher_Client::request_read_and_idx::timeout. '
            b'Probably the server is overloaded.</html>'
        )
        with pytest.raises(RuntimeError, match="(?i)overpass"):
            f._post_query(self._NoParseApi(), {}, "q", self._FakeTime(), req)
        # Retryable => multiple POST attempts before giving up.
        assert req.calls > 1


class TestOSMFeatureReduction:
    """Dense regions (e.g. Japan) map hundreds of thousands of rooftop PV
    panels that swamped the GUI and triggered an O(n^2) dedup freeze. They are
    dropped at filter time; dedup is spatially bucketed; the total is capped."""

    def _fetcher(self, **kw):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher((34.0, 138.0, 37.0, 141.0), **kw)

    def _gf(self, **kw):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            GridFeature,
        )
        base = dict(source="osm", name="x", latitude=35.0, longitude=139.0)
        base.update(kw)
        return GridFeature(**base)

    def test_rooftop_pv_dropped_real_kept(self):
        f = self._fetcher(min_voltage_kv=110.0)
        feats = [
            # rooftop PV: power=generator, solar, no capacity -> dropped
            self._gf(feature_type="generator", fuel="Solar", capacity_mw=0.0,
                     raw_tags={"power": "generator", "generator:source": "solar"}),
            # utility solar plant -> kept
            self._gf(feature_type="generator", name="farm", fuel="Solar",
                     capacity_mw=40.0,
                     raw_tags={"power": "plant", "plant:source": "solar"}),
            # capacity-tagged solar generator -> kept
            self._gf(feature_type="generator", name="sized", fuel="Solar",
                     capacity_mw=10.0,
                     raw_tags={"power": "generator", "generator:source": "solar"}),
            # thermal generator with no capacity (e.g. Cuba) -> kept (not solar)
            self._gf(feature_type="generator", name="thermal", fuel="Gas",
                     capacity_mw=0.0,
                     raw_tags={"power": "generator", "generator:source": "gas"}),
            self._gf(feature_type="substation", name="ss", voltage_kv=275.0),
        ]
        out = self._fetcher(min_voltage_kv=110.0)._apply_filters(feats)
        names = {x.name for x in out}
        assert "x" not in names                      # rooftop PV dropped
        assert {"farm", "sized", "thermal", "ss"} <= names

    def test_feature_cap_keeps_infra_and_largest_gens(self):
        f = self._fetcher()
        f._MAX_GUI_FEATURES = 100
        feats = [self._gf(feature_type="substation", name=f"ss{i}",
                          voltage_kv=275.0) for i in range(60)]
        feats += [self._gf(feature_type="generator", name=f"g{i}",
                           capacity_mw=float(i)) for i in range(200)]
        out = f._cap_features(feats)
        assert len(out) == 100
        # all 60 substations survive; 40 largest generators kept
        assert sum(1 for x in out if x.feature_type == "substation") == 60
        gens = [x for x in out if x.feature_type == "generator"]
        assert len(gens) == 40
        assert min(g.capacity_mw for g in gens) == 160.0  # 200-40

    def test_dedup_merges_near_and_keeps_far(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            deduplicate_features,
        )
        # three generators within 0.2km -> 1; one 10km away -> separate
        cluster = [
            self._gf(feature_type="generator", name=f"c{i}", capacity_mw=5.0,
                     latitude=35.0 + i * 0.001, longitude=139.0)
            for i in range(3)
        ]
        far = self._gf(feature_type="generator", name="far", capacity_mw=5.0,
                       latitude=35.1, longitude=139.0)
        out = deduplicate_features(cluster + [far], proximity_km=1.0)
        gens = [x for x in out if x.feature_type == "generator"]
        assert len(gens) == 2  # one merged cluster + the far one

    def test_dedup_large_dense_is_fast(self):
        import time
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            deduplicate_features,
        )
        dense = [
            self._gf(feature_type="generator", name=f"g{i}", capacity_mw=0.0,
                     latitude=35.6 + (i % 140) * 0.001,
                     longitude=139.7 + (i // 140) * 0.001)
            for i in range(15000)
        ]
        t0 = time.time()
        deduplicate_features(dense, proximity_km=1.0)
        assert time.time() - t0 < 10.0  # O(n^2) would be minutes


class TestOverpassErrorSnippet:
    def test_extracts_message_from_html(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            _overpass_error_snippet,
        )
        html = ('<html><body><p><strong style="color:#FF0000">Error</strong>: '
                'runtime error: Dispatcher_Client::request_read_and_idx::timeout.'
                ' The server is probably too busy.</p></body></html>')
        out = _overpass_error_snippet(html)
        assert "Dispatcher_Client" in out
        assert "<strong" not in out

    def test_empty_and_plain(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            _overpass_error_snippet,
        )
        assert _overpass_error_snippet("") == ""
        assert "boom" in _overpass_error_snippet("boom plain text")


class TestOSMFetcherResilience:
    """A single failing tile (e.g. road-dense, times out on every mirror) must
    not abort the whole region; only an all-tiles failure raises."""

    def _fetcher(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher((24.0, 122.9, 45.6, 145.8))  # Japan -> many tiles

    class _EmptyResult:
        nodes: list = []
        ways: list = []

    def test_one_failed_tile_is_skipped(self, monkeypatch):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda *a, **k: None)
        f = self._fetcher()
        n = len(f._tile_bboxes())
        calls = {"i": 0}

        def fake_post(api, headers, query, time, requests):
            i = calls["i"]
            calls["i"] += 1
            if i == 0:
                raise RuntimeError("Overpass busy (504)")
            return self._EmptyResult()

        monkeypatch.setattr(f, "_post_query", fake_post)
        out = f._fetch()
        assert calls["i"] == n          # every tile attempted despite tile-0 fail
        assert out == []                # empty fakes, but no exception raised

    def test_all_tiles_failed_raises(self, monkeypatch):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda *a, **k: None)
        f = self._fetcher()

        def fake_post(*a, **k):
            raise RuntimeError("Overpass busy (504)")

        monkeypatch.setattr(f, "_post_query", fake_post)
        with pytest.raises(RuntimeError, match=r"All .* tile"):
            f._fetch()


class TestOSMFetcher400Fatal:
    """A genuine query error (Overpass 'static error', e.g. an out-of-range
    bbox) must fail fast, not burn retries."""

    def _fetcher(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher((0.0, 0.0, 1.0, 1.0))

    def test_static_error_400_is_fatal_no_retry(self):
        f = self._fetcher()

        class _Resp:
            status_code = 400
            content = b""
            text = ('<p><strong style="color:#FF0000">Error</strong>: line 3: '
                    'static error: For the attribute "w" of the element '
                    '"bbox-query" the only allowed values are floats between '
                    '-180.0 and 180.0.</p>')

        class _FakeRequests:
            RequestException = _ReqExc
            calls = 0

            def post(self, *a, **k):
                _FakeRequests.calls += 1
                return _Resp()

        class _Api:
            def parse_json(self, c):
                raise AssertionError("must not parse a 400")

        class _T:
            def sleep(self, *_):
                pass

        req = _FakeRequests()
        with pytest.raises(RuntimeError, match="rejected query"):
            f._post_query(_Api(), {}, "q", _T(), req)
        assert req.calls == 1  # fatal: no retries


class TestPolygonLongitudeNormalization:
    """Leaflet can hand back longitudes outside [-180, 180] when the world map
    is panned across a copy of the globe; the domain bounds must be normalised
    so Overpass and the plant-DB filters see a valid bbox (regression: 0 plants
    + Overpass static error for a Japan polygon drawn on a wrapped map)."""

    def test_wrapped_japan_polygon_normalises_to_valid_bbox(self):
        # Same normalisation the step applies; Japan drawn at +360 longitude.
        def _norm_lng(x):
            return ((float(x) + 180.0) % 360.0) - 180.0

        ring = [[487.0, 24.0], [505.8, 24.0], [505.8, 45.6], [487.0, 45.6]]
        poly = [(max(-90.0, min(90.0, c[1])), _norm_lng(c[0])) for c in ring]
        lngs = [p[1] for p in poly]
        assert all(-180.0 <= x <= 180.0 for x in lngs)
        assert min(lngs) == pytest.approx(127.0)
        assert max(lngs) == pytest.approx(145.8)


def test_no_orphan_generators_after_build():
    """Every generator the Grid Builder creates must reference an existing
    fuel and an existing technology — including generators whose fuel was not
    detected (empty/None) or is unknown. Regression for 'generators left
    without technologies nor fuels'."""
    model = MockGuiModel(_make_state(buses={}))
    feats = [
        _feat("substation", name="S1", voltage_kv=220.0),
        _feat("generator", name="G_solar", capacity_mw=50.0, fuel="solar"),
        _feat("generator", name="G_empty", capacity_mw=20.0, fuel=""),
        _feat("generator", name="G_none", capacity_mw=8.0, fuel="None"),
        _feat("generator", name="G_unknown", capacity_mw=10.0, fuel="Magic Dust"),
    ]
    gmb.build_grid_from_features(model, feats)
    gens = list(model.state.generators.values())
    assert len(gens) == 4
    for g in gens:
        assert g.fuel and g.fuel in model.state.fuels, \
            f"{g.name}: fuel '{g.fuel}' not in fuels"
        assert g.technology_id and g.technology_id in model.state.technologies, \
            f"{g.name}: technology_id '{g.technology_id}' not in technologies"


class TestOSMFuelDetection:
    """OSM generator fuel detection: expanded source aliases + a method
    fallback so fewer generators fall back to 'Other'."""

    def _fetcher(self):
        from esfex.visualization.workflows.grid_mapping_fetchers import (
            OSMGridFetcher,
        )
        return OSMGridFetcher((0.0, 0.0, 1.0, 1.0))

    @pytest.mark.parametrize("tags,expected", [
        ({"power": "generator", "generator:source": "natural_gas"}, "Natural Gas"),
        ({"power": "generator", "generator:source": "lng"}, "Natural Gas"),
        ({"power": "generator", "generator:source": "lignite"}, "Coal"),
        ({"power": "generator", "generator:source": "wood"}, "Biomass"),
        ({"power": "generator", "generator:source": "landfill_gas"}, "Biogas"),
        ({"power": "generator", "generator:source": "heavy_oil"}, "Fuel_oil"),
        # method fallback when source missing/unknown
        ({"power": "generator", "generator:method": "photovoltaic"}, "Solar"),
        ({"power": "generator", "generator:method": "wind_turbine"}, "Wind"),
        ({"power": "plant", "generator:method": "fission"}, "Nuclear"),
        ({"power": "generator", "generator:method": "run-of-the-river"}, "Water"),
        # genuinely unknown still maps to Other
        ({"power": "generator", "generator:source": "unobtanium"}, "Other"),
    ])
    def test_fuel_detected(self, tags, expected):
        feat = self._fetcher()._process_element(
            tags=tags, lat=0.5, lng=0.5, osm_id="node/1")
        assert feat is not None and feat.fuel == expected


class TestTechnologyClassification:
    """powerplantmatching-style technology labels: generators sharing a fuel
    map to distinct technologies (CCGT vs OCGT, steam turbine vs engine, hydro
    sub-types) with realistic efficiencies."""

    @pytest.mark.parametrize("fuel,hint,cap,expected", [
        ("naturalgas", "", 300.0, "CCGT"),         # large gas -> CCGT
        ("naturalgas", "", 30.0, "OCGT"),          # small gas -> OCGT
        ("naturalgas", "OCGT", 500.0, "OCGT"),     # hint overrides capacity
        ("naturalgas", "combined cycle", 5.0, "CCGT"),
        ("coal", "", 0.0, "Coal ST"),
        ("fuel_oil", "", 0.0, "Oil ST"),
        ("diesel", "", 0.0, "Combustion Engine"),
        ("diesel", "steam turbine", 0.0, "Oil ST"),
        ("water", "pumped storage", 0.0, "Pumped Storage"),
        ("water", "run-of-river", 0.0, "Run-Of-River"),
        ("water", "", 0.0, "Reservoir"),
        ("sun", "", 0.0, "PV"),
        ("wind", "", 0.0, "Onshore Wind"),
        ("wind", "offshore", 0.0, "Offshore Wind"),
        ("nuclear", "", 0.0, "Nuclear"),
        ("magicdust", "", 0.0, "Other"),
    ])
    def test_classify(self, fuel, hint, cap, expected):
        assert gmb.classify_technology(fuel, "", hint, cap) == expected

    def test_catalog_entry_per_label(self):
        # Every label classify_technology can return has a catalog entry.
        for spec in gmb._TECH_CATALOG.values():
            assert spec["fuel"] and spec["name"] and spec["category"]
        assert gmb._TECH_CATALOG["CCGT"]["eff_at_rated"] > \
            gmb._TECH_CATALOG["OCGT"]["eff_at_rated"]


def test_build_creates_distinct_gas_technologies():
    """A large and a small gas plant must end up on CCGT and OCGT
    respectively — not collapsed into one gas technology."""
    model = MockGuiModel(_make_state(buses={}))
    feats = [
        _feat("substation", name="S1", voltage_kv=220.0),
        _feat("generator", name="BigGas", capacity_mw=400.0, fuel="gas"),
        _feat("generator", name="Peaker", capacity_mw=40.0, fuel="gas"),
        _feat("generator", name="CoalU", capacity_mw=600.0, fuel="coal"),
    ]
    gmb.build_grid_from_features(model, feats)
    techs = {t.name for t in model.state.technologies.values()}
    assert "CCGT" in techs and "OCGT" in techs and "Coal Steam Turbine" in techs
    by_name = {g.name: g for g in model.state.generators.values()}
    ccgt = next(t.tech_id for t in model.state.technologies.values()
                if t.name == "CCGT")
    ocgt = next(t.tech_id for t in model.state.technologies.values()
                if t.name == "OCGT")
    assert by_name["BigGas"].technology_id == ccgt
    assert by_name["Peaker"].technology_id == ocgt


class TestNodeOperationalDefaults:
    """The build must leave nodes operationally complete: non-zero reserves
    and transmission losses (a Grid-Builder network used to ship reserve=0,
    losses=0 — no security margin, lossless grid)."""

    def _state_with_gens(self, peak=0.0):
        from esfex.visualization.data.gui_model import (
            GuiGeneratorInstance, GuiNode, GuiNodeDemand, GuiSystemState,
        )
        st = GuiSystemState(name="S", nodes=[GuiNode(index=0, name="N0")])
        st.nodes[0].demand = GuiNodeDemand(peak_mw=peak)
        st.generators = {
            "g0": GuiGeneratorInstance(instance_id="g0", unit_key="u",
                                       name="G0", gen_type="Non-renewable",
                                       fuel="Gas", node=0, rated_power=300.0),
            "g1": GuiGeneratorInstance(instance_id="g1", unit_key="u",
                                       name="G1", gen_type="Non-renewable",
                                       fuel="Coal", node=0, rated_power=600.0),
        }
        return st

    def test_capacity_basis_when_no_demand(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            apply_node_operational_defaults,
        )
        st = self._state_with_gens(peak=0.0)
        ch = apply_node_operational_defaults(st)
        n = st.nodes[0]
        assert n.losses == 0.02
        assert n.reserve_static == pytest.approx(90.0)   # 10% of 900 MW cap
        assert n.reserve_dynamic == pytest.approx(45.0)  # 5% of 900 MW cap
        assert n.reserve_duration >= 1
        assert ch["reserves"] == 1 and ch["losses"] == 1

    def test_demand_basis_preferred(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            apply_node_operational_defaults,
        )
        st = self._state_with_gens(peak=200.0)  # demand beats capacity
        apply_node_operational_defaults(st)
        n = st.nodes[0]
        assert n.reserve_static == pytest.approx(20.0)   # 10% of 200 MW peak
        assert n.reserve_dynamic == pytest.approx(10.0)  # 5% of 200 MW peak

    def test_explicit_values_preserved(self):
        from esfex.visualization.workflows.grid_mapping_quality import (
            apply_node_operational_defaults,
        )
        st = self._state_with_gens(peak=200.0)
        st.nodes[0].reserve_static = 50.0
        st.nodes[0].reserve_dynamic = 7.0
        st.nodes[0].losses = 0.05
        ch = apply_node_operational_defaults(st)
        n = st.nodes[0]
        assert n.reserve_static == 50.0 and n.losses == 0.05
        assert ch["reserves"] == 0 and ch["losses"] == 0


def test_build_fills_node_reserves_and_losses():
    model = MockGuiModel(_make_state(buses={}))
    feats = [
        _feat("substation", name="S1", voltage_kv=220.0),
        _feat("generator", name="G", capacity_mw=500.0, fuel="gas"),
    ]
    gmb.build_grid_from_features(model, feats)
    for n in model.state.nodes:
        assert n.losses > 0, f"node {n.index} has zero losses"
        assert n.reserve_static > 0 and n.reserve_dynamic > 0, \
            f"node {n.index} has zero reserves"
