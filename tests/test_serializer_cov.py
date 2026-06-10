"""Coverage-focused unit tests for esfex.visualization.data.serializer.

These tests target the pure / self-contained helper functions and a few
config<->GUI round trips. Assertions reflect behaviour observed by reading
the module source. This file is independent of the existing
tests/test_serializer.py.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

# The serializer imports numpy at module level; numpy is a hard dep of the
# package so it should always be present, but guard defensively.
np = pytest.importorskip("numpy")

from esfex.config.schema import (
    CostCurveBlock,
    CostCurveConfig,
    ESFEXConfig,
    FuelConfig,
    MetaNetworkConfig,
    NodeConfig,
    SystemConfig,
)
from esfex.visualization.data.gui_model import (
    EndpointRef,
    FuelRouteParams,
    GeoPoint,
    GuiFuelEntryPoint,
    GuiFuelStorage,
    GuiFuelTransportRoute,
    GuiGlobalSettings,
    GuiInterSystemLink,
    GuiNode,
    GuiStochasticScenario,
    GuiSystemState,
    GuiVisualScaling,
    VisualStyle,
)
from esfex.visualization.data import serializer as S


@pytest.fixture(autouse=True)
def _no_user_pref_overrides(monkeypatch):
    """GuiGlobalSettings.__post_init__ loads ~/.config preferences which
    overwrite constructor-supplied fields. Stub it so test inputs survive.
    """
    monkeypatch.setattr(
        GuiGlobalSettings, "__post_init__", lambda self: None, raising=False
    )


# ---------------------------------------------------------------------------
# _style_to_dict / _dict_to_style
# ---------------------------------------------------------------------------

def test_style_to_dict_none_returns_none():
    assert S._style_to_dict(None) is None


def test_style_to_dict_all_unset_returns_none():
    # A VisualStyle with every field None should serialize to None.
    assert S._style_to_dict(VisualStyle()) is None


def test_style_to_dict_partial_keeps_only_set_fields():
    s = VisualStyle(color="#abcdef", width=2.5)
    out = S._style_to_dict(s)
    assert out == {"color": "#abcdef", "width": 2.5}


def test_style_to_dict_full():
    s = VisualStyle(color="#111", size=4.0, icon_shape="square",
                    opacity=0.5, width=1.0)
    out = S._style_to_dict(s)
    assert out == {
        "color": "#111", "size": 4.0, "icon_shape": "square",
        "opacity": 0.5, "width": 1.0,
    }


def test_dict_to_style_none_yields_all_none():
    s = S._dict_to_style(None)
    assert isinstance(s, VisualStyle)
    assert s.color is None and s.size is None and s.width is None
    assert s.icon_shape is None and s.opacity is None


def test_dict_to_style_roundtrip():
    original = VisualStyle(color="#xyz", size=3.0, icon_shape="diamond",
                           opacity=0.2, width=5.0)
    restored = S._dict_to_style(S._style_to_dict(original))
    assert restored == original


# ---------------------------------------------------------------------------
# _normalize_voltage_kv
# ---------------------------------------------------------------------------

def test_normalize_voltage_below_threshold_unchanged():
    assert S._normalize_voltage_kv(220.0) == 220.0
    assert S._normalize_voltage_kv(1200.0) == 1200.0  # boundary: not > 1200


def test_normalize_voltage_above_threshold_divided():
    assert S._normalize_voltage_kv(220000.0) == 220.0
    assert S._normalize_voltage_kv(1200.0001) == pytest.approx(1.2000001)


# ---------------------------------------------------------------------------
# _cost_curve_to_gui_data
# ---------------------------------------------------------------------------

def test_cost_curve_flat_returns_none():
    c = CostCurveConfig(curve_type="flat")
    assert S._cost_curve_to_gui_data(c) is None


def test_cost_curve_linear():
    c = CostCurveConfig(curve_type="linear", price_at_zero=10.0,
                        price_at_max=50.0, num_segments=7)
    out = S._cost_curve_to_gui_data(c)
    assert out == {"price_at_zero": 10.0, "price_at_max": 50.0,
                   "num_segments": 7}


def test_cost_curve_linear_none_prices_default_zero():
    # price_at_zero / price_at_max default to None -> serialized as 0.0
    c = CostCurveConfig(curve_type="linear")
    out = S._cost_curve_to_gui_data(c)
    assert out["price_at_zero"] == 0.0
    assert out["price_at_max"] == 0.0


def test_cost_curve_stepwise():
    c = CostCurveConfig(
        curve_type="stepwise",
        blocks=[CostCurveBlock(fraction=0.5, price=20.0),
                CostCurveBlock(fraction=0.5, price=40.0)],
    )
    out = S._cost_curve_to_gui_data(c)
    assert out == {"blocks": [{"fraction": 0.5, "price": 20.0},
                              {"fraction": 0.5, "price": 40.0}]}


def test_cost_curve_stepwise_empty_blocks():
    c = CostCurveConfig(curve_type="stepwise", blocks=[])
    out = S._cost_curve_to_gui_data(c)
    assert out == {"blocks": []}


def test_cost_curve_exponential_defaults():
    # base_price defaults None -> 0.0, scale_factor None -> 1.0
    c = CostCurveConfig(curve_type="exponential")
    out = S._cost_curve_to_gui_data(c)
    assert out["base_price"] == 0.0
    assert out["scale_factor"] == 1.0
    assert out["num_segments"] == c.num_segments


def test_cost_curve_exponential_values():
    c = CostCurveConfig(curve_type="exponential", base_price=5.0,
                        scale_factor=2.0, num_segments=9)
    out = S._cost_curve_to_gui_data(c)
    assert out == {"base_price": 5.0, "scale_factor": 2.0, "num_segments": 9}


# ---------------------------------------------------------------------------
# _gui_data_to_cost_curve_config
# ---------------------------------------------------------------------------

def test_gui_data_to_curve_flat_returns_none():
    assert S._gui_data_to_cost_curve_config("flat", {"x": 1}) is None


def test_gui_data_to_curve_none_data_returns_none():
    assert S._gui_data_to_cost_curve_config("linear", None) is None


def test_gui_data_to_curve_linear_with_defaults():
    cfg = S._gui_data_to_cost_curve_config("linear", {})
    assert cfg == {"curve_type": "linear", "price_at_zero": 0.0,
                   "price_at_max": 0.0, "num_segments": 5}


def test_gui_data_to_curve_linear_with_values():
    cfg = S._gui_data_to_cost_curve_config(
        "linear", {"price_at_zero": 3.0, "price_at_max": 9.0, "num_segments": 8}
    )
    assert cfg["price_at_zero"] == 3.0
    assert cfg["price_at_max"] == 9.0
    assert cfg["num_segments"] == 8


def test_gui_data_to_curve_stepwise():
    blocks = [{"fraction": 1.0, "price": 5.0}]
    cfg = S._gui_data_to_cost_curve_config("stepwise", {"blocks": blocks})
    assert cfg == {"curve_type": "stepwise", "blocks": blocks}


def test_gui_data_to_curve_stepwise_missing_blocks_default_empty():
    cfg = S._gui_data_to_cost_curve_config("stepwise", {})
    assert cfg == {"curve_type": "stepwise", "blocks": []}


def test_gui_data_to_curve_exponential():
    cfg = S._gui_data_to_cost_curve_config(
        "exponential", {"base_price": 2.0, "scale_factor": 3.0, "num_segments": 6}
    )
    assert cfg == {"curve_type": "exponential", "base_price": 2.0,
                   "scale_factor": 3.0, "num_segments": 6}


def test_gui_data_to_curve_exponential_defaults():
    cfg = S._gui_data_to_cost_curve_config("exponential", {})
    assert cfg["base_price"] == 0.0
    assert cfg["scale_factor"] == 1.0
    assert cfg["num_segments"] == 5


def test_curve_roundtrip_linear():
    c = CostCurveConfig(curve_type="linear", price_at_zero=4.0,
                        price_at_max=12.0, num_segments=5)
    gui = S._cost_curve_to_gui_data(c)
    back = S._gui_data_to_cost_curve_config("linear", gui)
    assert back["price_at_zero"] == 4.0
    assert back["price_at_max"] == 12.0


# ---------------------------------------------------------------------------
# _parse_allowed_technologies
# ---------------------------------------------------------------------------

def test_parse_allowed_tech_none():
    assert S._parse_allowed_technologies(None) == {}


def test_parse_allowed_tech_dict_coerces_types():
    out = S._parse_allowed_technologies({"Solar": 100, "Wind": "50"})
    assert out == {"Solar": 100.0, "Wind": 50.0}
    assert all(isinstance(v, float) for v in out.values())


def test_parse_allowed_tech_list_legacy():
    out = S._parse_allowed_technologies(["Solar", "Wind"])
    assert out == {"Solar": 0.0, "Wind": 0.0}


def test_parse_allowed_tech_tuple_legacy():
    out = S._parse_allowed_technologies(("A",))
    assert out == {"A": 0.0}


def test_parse_allowed_tech_unsupported_returns_empty():
    assert S._parse_allowed_technologies(42) == {}
    assert S._parse_allowed_technologies("string") == {}


# ---------------------------------------------------------------------------
# _to_native
# ---------------------------------------------------------------------------

def test_to_native_numpy_scalars():
    assert S._to_native(np.int64(7)) == 7
    assert isinstance(S._to_native(np.int64(7)), int)
    assert S._to_native(np.float64(1.5)) == 1.5
    assert isinstance(S._to_native(np.float64(1.5)), float)
    assert S._to_native(np.bool_(True)) is True
    assert isinstance(S._to_native(np.bool_(True)), bool)


def test_to_native_ndarray():
    out = S._to_native(np.array([1, 2, 3]))
    assert out == [1, 2, 3]
    assert isinstance(out, list)


def test_to_native_nested_structures():
    obj = {"a": np.int64(1), "b": [np.float64(2.0), {"c": np.array([3, 4])}]}
    out = S._to_native(obj)
    assert out == {"a": 1, "b": [2.0, {"c": [3, 4]}]}


def test_to_native_tuple_becomes_list():
    assert S._to_native((1, 2)) == [1, 2]


def test_to_native_passthrough_native():
    assert S._to_native("x") == "x"
    assert S._to_native(5) == 5
    assert S._to_native(None) is None


def test_to_native_output_is_yaml_serializable():
    obj = {"vals": np.array([1.0, 2.0]), "n": np.int64(3)}
    native = S._to_native(obj)
    dumped = yaml.dump(native)
    assert "1.0" in dumped


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------

def test_haversine_zero_distance():
    assert S._haversine_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)


def test_haversine_symmetric():
    a = S._haversine_km(0.0, 0.0, 0.0, 1.0)
    b = S._haversine_km(0.0, 1.0, 0.0, 0.0)
    assert a == pytest.approx(b)


def test_haversine_one_degree_lng_at_equator():
    # One degree of longitude at the equator ~ 111.19 km.
    d = S._haversine_km(0.0, 0.0, 0.0, 1.0)
    assert d == pytest.approx(111.19, abs=0.5)


def test_haversine_known_distance():
    # Approx distance between two well separated points is large & positive.
    d = S._haversine_km(0.0, 0.0, 10.0, 10.0)
    assert d > 1000.0


# ---------------------------------------------------------------------------
# _route_length_from_waypoints
# ---------------------------------------------------------------------------

def _route(waypoints):
    return GuiFuelTransportRoute(route_id="r", waypoints=list(waypoints))


def test_route_length_no_waypoints():
    assert S._route_length_from_waypoints(GuiSystemState(), _route([])) == 0.0


def test_route_length_single_waypoint():
    rt = _route([GeoPoint(0.0, 0.0)])
    assert S._route_length_from_waypoints(GuiSystemState(), rt) == 0.0


def test_route_length_two_waypoints_matches_haversine():
    rt = _route([GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0)])
    expected = S._haversine_km(0.0, 0.0, 0.0, 1.0)
    assert S._route_length_from_waypoints(GuiSystemState(), rt) == pytest.approx(expected)


def test_route_length_chains_segments():
    rt = _route([GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0), GeoPoint(0.0, 2.0)])
    seg = S._haversine_km(0.0, 0.0, 0.0, 1.0)
    assert S._route_length_from_waypoints(GuiSystemState(), rt) == pytest.approx(2 * seg)


# ---------------------------------------------------------------------------
# _build_fuel_transport_distances
# ---------------------------------------------------------------------------

def test_build_fuel_distances_explicit_length_priority():
    state = GuiSystemState(
        nodes=[GuiNode(index=0, name="A"), GuiNode(index=1, name="B")],
        fuel_transport_routes=[
            GuiFuelTransportRoute(route_id="r0", from_node=0, to_node=1,
                                  length_km=42.0),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    assert dist[0][1] == 42.0
    assert dist[1][0] == 42.0
    assert dist[0][0] == 0.0


def test_build_fuel_distances_haversine_fallback_from_node_centroids():
    # Use non-origin centroids: the fallback loop skips any node whose
    # centroid is exactly (0.0, 0.0), treating it as "missing".
    state = GuiSystemState(
        nodes=[
            GuiNode(index=0, name="A", centroid_lat=10.0, centroid_lng=20.0),
            GuiNode(index=1, name="B", centroid_lat=10.0, centroid_lng=21.0),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    expected = S._haversine_km(10.0, 20.0, 10.0, 21.0)
    assert dist[0][1] == pytest.approx(expected)
    assert dist[1][0] == pytest.approx(expected)


def test_build_fuel_distances_skips_zero_zero_centroids():
    # node 0 at origin (0,0) is treated as "missing" -> pair skipped,
    # so the distance between node 0 and node 1 stays 0.
    state = GuiSystemState(
        nodes=[
            GuiNode(index=0, name="A", centroid_lat=0.0, centroid_lng=0.0),
            GuiNode(index=1, name="B", centroid_lat=10.0, centroid_lng=20.0),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    assert dist[0][1] == 0.0
    assert len(dist) == 2 and len(dist[0]) == 2


def test_build_fuel_distances_uses_fuel_entry_centroid():
    state = GuiSystemState(
        nodes=[
            GuiNode(index=0, name="A", centroid_lat=0.0, centroid_lng=0.0),
            GuiNode(index=1, name="B", centroid_lat=0.0, centroid_lng=0.0),
        ],
        fuel_entry_points=[
            GuiFuelEntryPoint(name="fe0", node=0,
                              coordinate=GeoPoint(0.0, 0.0)),
            GuiFuelEntryPoint(name="fe1", node=1,
                              coordinate=GeoPoint(0.0, 2.0)),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    # node0 entry coordinate (0,0) is treated as missing -> pair skipped.
    # So distance between node0 and node1 stays 0 because node0 centroid is 0,0.
    assert dist[0][1] == 0.0


def test_build_fuel_distances_explicit_length_zero_falls_back_to_waypoints():
    state = GuiSystemState(
        nodes=[GuiNode(index=0, name="A"), GuiNode(index=1, name="B")],
        fuel_transport_routes=[
            GuiFuelTransportRoute(
                route_id="r0", from_node=0, to_node=1, length_km=0.0,
                waypoints=[GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0)],
            ),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    expected = S._haversine_km(0.0, 0.0, 0.0, 1.0)
    assert dist[0][1] == pytest.approx(expected)


def test_build_fuel_distances_ignores_self_and_out_of_range_routes():
    state = GuiSystemState(
        nodes=[GuiNode(index=0, name="A")],
        fuel_transport_routes=[
            GuiFuelTransportRoute(route_id="self", from_node=0, to_node=0,
                                  length_km=10.0),
            GuiFuelTransportRoute(route_id="oob", from_node=0, to_node=5,
                                  length_km=10.0),
        ],
    )
    dist = S._build_fuel_transport_distances(state)
    assert dist == [[0.0]]


# ---------------------------------------------------------------------------
# config_to_stochastic_scenarios / stochastic_scenarios_to_config_dict
# ---------------------------------------------------------------------------

def test_config_to_stochastic_scenarios_none_when_absent():
    # A system with no stochastic_scenarios yields an empty list.
    cfg = _minimal_config("Sys")
    assert S.config_to_stochastic_scenarios(cfg) == []


def test_stochastic_scenarios_to_dict_empty_noop():
    d = {}
    S.stochastic_scenarios_to_config_dict([], d)
    assert d == {}


def test_stochastic_scenarios_to_dict_writes():
    sc = GuiStochasticScenario(name="dry", probability=0.3,
                               description="dry year",
                               multipliers={"demand": 1.1})
    d = {}
    S.stochastic_scenarios_to_config_dict([sc], d)
    assert d["stochastic_scenarios"] == [{
        "name": "dry", "probability": 0.3, "description": "dry year",
        "multipliers": {"demand": 1.1},
    }]


# ---------------------------------------------------------------------------
# global_settings_to_config_dict & round-trip with config_to_global_settings
# ---------------------------------------------------------------------------

def test_global_settings_to_config_dict_basic_fields():
    g = GuiGlobalSettings()
    g.simulation_mode = "unit_commitment"
    g.unit_commitment_hours = 48
    g.date_start = "02/02/2030 00:00"
    g.enable_primary_energy = False
    g.console_log_level = "verbose"
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    assert cd["simulation_mode"] == "unit_commitment"
    assert cd["unit_commitment_hours"] == 48
    assert cd["date_start"] == "02/02/2030 00:00"
    assert cd["enable_primary_energy"] is False
    assert cd["logging"]["console_level"] == "verbose"
    # temporal / solver / n1_security / master_problem sub-dicts created
    assert "temporal" in cd and "solver" in cd
    assert "n1_security" in cd and "master_problem" in cd
    assert "visual_scaling" in cd and "risk" in cd


def test_global_settings_mga_classical_emits_num_alternatives():
    g = GuiGlobalSettings()
    g.mp_mga_method = "mga"
    g.mp_mga_num_alternatives = 12
    g.mp_mga_objectives = ["min_cost"]  # stale; should be ignored
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    mga = cd["master_problem"]["mga"]
    assert mga["num_alternatives"] == 12
    assert "objectives" not in mga


def test_global_settings_mga_spores_emits_objectives():
    g = GuiGlobalSettings()
    g.mp_mga_method = "spores"
    g.mp_mga_objectives = ["min_cost", "max_re"]
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    mga = cd["master_problem"]["mga"]
    assert mga["objectives"] == ["min_cost", "max_re"]
    assert "num_alternatives" not in mga


def test_global_settings_solver_options_only_when_present():
    g = GuiGlobalSettings()
    g.solver_specific_options = {}
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    assert "options" not in cd["solver"]
    g.solver_specific_options = {"presolve": "on"}
    cd2 = {}
    S.global_settings_to_config_dict(g, cd2)
    assert cd2["solver"]["options"] == {"presolve": "on"}


def test_global_settings_visual_scaling_written():
    g = GuiGlobalSettings()
    g.visual_scaling = GuiVisualScaling(marker_min_px=9.0, line_max_px=12.0)
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    assert cd["visual_scaling"]["marker_min_px"] == 9.0
    assert cd["visual_scaling"]["line_max_px"] == 12.0


def test_global_settings_setdefault_preserves_existing_logging():
    g = GuiGlobalSettings()
    g.console_log_level = "basic"
    cd = {"logging": {"file_level": "debug"}}
    S.global_settings_to_config_dict(g, cd)
    # existing key preserved, new key added
    assert cd["logging"]["file_level"] == "debug"
    assert cd["logging"]["console_level"] == "basic"


# ---------------------------------------------------------------------------
# config_to_global_settings
# ---------------------------------------------------------------------------

def _minimal_config(sys_name="Sys"):
    sys = SystemConfig(
        name=sys_name,
        nodes=NodeConfig(
            num_nodes=2,
            nodes_connections=[0.0, 100.0, 100.0, 0.0],
            reserve_static=[1.0, 1.0],
            reserve_dynamic=[1.0, 1.0],
            reserve_duration=[1, 1],
            losses=[0.0, 0.0],
        ),
        fuels={"Sun": FuelConfig(name="Sun", emission_factor=0.0,
                                 price_base=0.0)},
    )
    return ESFEXConfig(
        meta_network=MetaNetworkConfig(systems=[sys_name]),
        systems={sys_name: sys},
    )


def test_config_to_global_settings_systems_from_meta_network():
    cfg = _minimal_config("Alpha")
    g = S.config_to_global_settings(cfg)
    assert g.systems_to_simulate == ["Alpha"]


def test_config_to_global_settings_console_level_from_raw():
    cfg = _minimal_config()
    raw = {"logging": {"console_level": "trace"}}
    g = S.config_to_global_settings(cfg, raw_dict=raw)
    assert g.console_log_level == "trace"


def test_config_to_global_settings_console_level_default_basic():
    cfg = _minimal_config()
    g = S.config_to_global_settings(cfg, raw_dict={})
    assert g.console_log_level == "basic"


def test_config_to_global_settings_visual_scaling_from_raw():
    cfg = _minimal_config()
    raw = {"visual_scaling": {"marker_min_px": 11.0, "line_min_px": 2.0}}
    g = S.config_to_global_settings(cfg, raw_dict=raw)
    assert g.visual_scaling.marker_min_px == 11.0
    assert g.visual_scaling.line_min_px == 2.0


def test_global_settings_roundtrip_console_and_scaling():
    cfg = _minimal_config()
    g = S.config_to_global_settings(cfg, raw_dict={})
    g.console_log_level = "verbose"
    g.visual_scaling = GuiVisualScaling(marker_min_px=7.5)
    cd = {}
    S.global_settings_to_config_dict(g, cd)
    g2 = S.config_to_global_settings(cfg, raw_dict=cd)
    assert g2.console_log_level == "verbose"
    assert g2.visual_scaling.marker_min_px == 7.5


# ---------------------------------------------------------------------------
# config_to_gui_states
# ---------------------------------------------------------------------------

def test_config_to_gui_states_produces_state_per_system():
    cfg = _minimal_config("Beta")
    states = S.config_to_gui_states(cfg)
    assert set(states.keys()) == {"Beta"}
    st = states["Beta"]
    assert isinstance(st, GuiSystemState)
    assert len(st.nodes) == 2
    # auto buses created (one per node) when config defines none
    assert len(st.buses) == 2


# ---------------------------------------------------------------------------
# config_to_inter_system_links / inter_system_links_to_config_dict
# ---------------------------------------------------------------------------

def test_config_to_inter_system_links_no_meta_network():
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=[]), systems={})
    # meta_network present with no links -> returns []
    links = S.config_to_inter_system_links(cfg)
    assert links == []


def test_inter_system_links_to_dict_empty_noop():
    cd = {"meta_network": {}}
    S.inter_system_links_to_config_dict([], cd)
    # no links -> early return, meta_network untouched
    assert "systems_links" not in cd["meta_network"]


def test_inter_system_links_to_dict_no_meta_noop():
    cd = {}
    # meta is None -> early return, no KeyError
    S.inter_system_links_to_config_dict(
        [GuiInterSystemLink(link_id="x", link_type="transmission",
                            from_system="A", to_system="B",
                            from_node=0, to_node=1)],
        cd,
    )
    assert cd == {}


def test_inter_system_links_to_dict_writes_grouped():
    lk = GuiInterSystemLink(
        link_id="islink_0", link_type="transmission",
        from_system="A", to_system="B", from_node=0, to_node=1,
        capacity_mw=500.0, max_investment_mw=200.0, investment_cost=1000.0,
        loss_factor=0.02, distance_km=120.0, cost_per_mw_km=5.0,
        reactance_pu=0.05, resistance_pu=0.005,
        waypoints=[GeoPoint(1.0, 2.0)],
        from_endpoint=EndpointRef("node", "0"),
        to_endpoint=EndpointRef("node", "1"),
        voltage_kv=220.0, num_circuits=2,
    )
    cd = {"meta_network": {}}
    S.inter_system_links_to_config_dict([lk], cd)
    sls = cd["meta_network"]["systems_links"]
    assert len(sls) == 1
    sl = sls[0]
    assert sl["systems"] == ["A", "B"]
    assert sl["connections"] == [[0, 1]]
    assert sl["existing_capacity_MW"] == [500.0]
    assert sl["max_investment_MW"] == [200.0]
    assert sl["waypoints"] == [[{"lat": 1.0, "lng": 2.0}]]
    assert sl["endpoints"] == [[
        {"element_type": "node", "element_id": "0"},
        {"element_type": "node", "element_id": "1"},
    ]]
    assert sl["num_circuits"] == [2]
    assert sl["voltage_kv"] == [220.0]


def test_inter_system_links_roundtrip_preserves_capacity():
    lk = GuiInterSystemLink(
        link_id="islink_0", link_type="transmission",
        from_system="A", to_system="B", from_node=0, to_node=1,
        capacity_mw=333.0, distance_km=77.0,
    )
    cd = {"meta_network": {}}
    S.inter_system_links_to_config_dict([lk], cd)

    # Build a config object that exposes systems_links to parse back.
    from esfex.config.schema import (
        MetaNetworkConfig as _MN,
        SystemLinkConfig as _SL,
    )
    sl_dict = cd["meta_network"]["systems_links"][0]
    meta = _MN(systems=["A", "B"], systems_links=[_SL(**sl_dict)])

    class _Cfg:
        meta_network = meta

    parsed = S.config_to_inter_system_links(_Cfg())
    assert len(parsed) == 1
    assert parsed[0].capacity_mw == 333.0
    assert parsed[0].distance_km == 77.0
    assert parsed[0].from_node == 0
    assert parsed[0].to_node == 1


# ---------------------------------------------------------------------------
# gui_state_to_yaml end-to-end (writes a real file)
# ---------------------------------------------------------------------------

def test_gui_state_to_yaml_writes_loadable_file(tmp_path):
    cfg = _minimal_config("Gamma")
    states = S.config_to_gui_states(cfg)
    out = tmp_path / "out.yaml"
    S.gui_state_to_yaml(states, cfg, out)
    assert out.is_file()
    with open(out) as f:
        loaded = yaml.safe_load(f)
    assert "systems" in loaded
    assert "Gamma" in loaded["systems"]
    assert loaded["meta_network"]["systems"] == ["Gamma"]
