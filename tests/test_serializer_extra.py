"""Additive coverage tests for esfex.visualization.data.serializer.

These tests target branches NOT exercised by tests/test_serializer.py or
tests/test_serializer_cov.py, in particular the large config->GUI->YAML
round-trip paths in ``_system_to_gui_state`` / ``_apply_gui_state_to_dict``
and the standalone ``_load_demand_csv`` helper.

All assertions reflect behaviour observed by reading and running the module
source — no guesses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from esfex.config.schema import (
    ACDCConverterConfig,
    BatteryConfig,
    BusConfig,
    CostCurveBlock,
    CostCurveConfig,
    DemandSectorConfig,
    DevelopmentZoneConfig,
    ElectrolyzerConfig,
    EVCategoryConfig,
    FrequencyConverterConfig,
    FuelConfig,
    FuelEntryPointConfig,
    FuelInfrastructureConfig,
    GeoCoordinate,
    GeneratorConfig,
    MetaNetworkConfig,
    NodeConfig,
    NonElectricDemandConfig,
    PrimaryEnergySourceConfig,
    RooftopSolarConfig,
    ESFEXConfig,
    SystemConfig,
    TransformerConfig,
    TransmissionLineGeo,
)
from esfex.visualization.data.gui_model import (
    EndpointRef,
    GeoPoint,
    GuiGlobalSettings,
    GuiNode,
)
from esfex.visualization.data import serializer as S


@pytest.fixture(autouse=True)
def _no_user_pref_overrides(monkeypatch):
    """Stop GuiGlobalSettings.__post_init__ from loading ~/.config prefs."""
    monkeypatch.setattr(
        GuiGlobalSettings, "__post_init__", lambda self: None, raising=False
    )


# ---------------------------------------------------------------------------
# helpers for building valid per-node generator / battery arrays
# ---------------------------------------------------------------------------

def _gen_arrays(n: int, *, rated, invest_cost=None, invest_max=None):
    """Return kwargs for the per-node GeneratorConfig arrays."""
    d = dict(
        life_time=[25] * n,
        initial_age=[0] * n,
        degradation_rate=[0.01] * n,
        decommissioning_cost=[0.0] * n,
        rated_power=list(rated),
        min_power=[0.0] * n,
        min_up=[1] * n,
        min_down=[1] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.9] * n,
        eff_at_min=[0.85] * n,
        inertia=[3.0] * n,
        droop=[0.05] * n,
        governor_time_const=[5.0] * n,
        start_up_cost=[100.0] * n,
        fuel_cost=[20.0] * n,
        fixed_cost=[1.0] * n,
        maintenance_cost=[1.0] * n,
    )
    if invest_cost is not None:
        d["invest_cost"] = list(invest_cost)
    if invest_max is not None:
        d["invest_max_power"] = list(invest_max)
    return d


def _bat_arrays(n: int, *, rated, capacity, invest_cost=None, invest_max=None,
                invest_cost_energy=None, invest_max_capacity=None):
    d = dict(
        life_time=[15] * n,
        initial_age=[0] * n,
        degradation_rate=[0.01] * n,
        decommissioning_cost=[0.0] * n,
        rated_power=list(rated),
        min_power=[0.0] * n,
        min_up=[0] * n,
        min_down=[0] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.95] * n,
        eff_at_min=[0.9] * n,
        inertia=[0.0] * n,
        start_up_cost=[0.0] * n,
        fuel_cost=[0.0] * n,
        fixed_cost=[0.5] * n,
        maintenance_cost=[0.5] * n,
        efficiency_charge=[0.95] * n,
        efficiency_discharge=[0.95] * n,
        soc_initial=[0.5] * n,
        max_DoD=[0.9] * n,
        capacity=list(capacity),
        MaxChargePower=[10.0] * n,
        MaxDischargePower=[10.0] * n,
    )
    if invest_cost is not None:
        d["invest_cost"] = list(invest_cost)
    if invest_max is not None:
        d["invest_max_power"] = list(invest_max)
    if invest_cost_energy is not None:
        d["invest_cost_energy"] = list(invest_cost_energy)
    if invest_max_capacity is not None:
        d["invest_max_capacity"] = list(invest_max_capacity)
    return d


def _elec_arrays(n: int, *, rated, invest_cost, invest_max):
    return dict(
        life_time=[20] * n,
        initial_age=[0] * n,
        degradation_rate=[0.01] * n,
        rated_power=list(rated),
        min_power=[0.0] * n,
        ramp_up=[1.0] * n,
        ramp_down=[1.0] * n,
        eff_at_rated=[0.7] * n,
        eff_at_min=[0.6] * n,
        fixed_cost=[1.0] * n,
        variable_cost=[0.5] * n,
        invest_cost=list(invest_cost),
        invest_max_power=list(invest_max),
    )


def _rich_system(name="Rich"):
    """Build a 2-node SystemConfig populated with many component types."""
    n = 2
    coords = [GeoCoordinate(latitude=10.0, longitude=20.0),
              GeoCoordinate(latitude=11.0, longitude=21.0)]
    nodes = NodeConfig(
        num_nodes=n,
        nodes_connections=[0.0, 150.0, 150.0, 0.0],
        reserve_static=[5.0, 5.0],
        reserve_dynamic=[3.0, 3.0],
        reserve_duration=[2, 2],
        losses=[0.01, 0.01],
        transference_invest_cost=[1.0, 2.0, 3.0, 4.0],
        transference_invest_max=[10.0, 20.0, 30.0, 40.0],
        node_coordinates=coords,
        node_names=["North", "South"],
    )

    # Generator present at node 0, with investment at node 1, and a
    # per-node fuel cost curve (linear + stepwise).
    gen = GeneratorConfig(
        name="GasPlant",
        type="Non-renewable",
        fuel="Gas",
        technology="CCGT",
        **_gen_arrays(n, rated=[100.0, 0.0],
                      invest_cost=[0.0, 5000.0],
                      invest_max=[0.0, 50.0]),
        fuel_cost_curve=[
            CostCurveConfig(curve_type="linear", price_at_zero=10.0,
                            price_at_max=40.0, num_segments=4),
            CostCurveConfig(
                curve_type="stepwise",
                blocks=[CostCurveBlock(fraction=0.5, price=15.0),
                        CostCurveBlock(fraction=0.5, price=30.0)],
            ),
        ],
        reservoir_capacity=[1000.0, 0.0],
        reservoir_initial_level=[0.5, 0.0],
        reservoir_inflow_file="inflow.csv",
    )

    bat = BatteryConfig(
        name="BESS",
        fuel="None",
        **_bat_arrays(n, rated=[20.0, 0.0], capacity=[80.0, 0.0],
                      invest_cost=[0.0, 1000.0], invest_max=[0.0, 30.0],
                      invest_cost_energy=[0.0, 200.0],
                      invest_max_capacity=[0.0, 120.0]),
        discharge_cost_curve=[
            CostCurveConfig(curve_type="flat"),
            CostCurveConfig(curve_type="exponential", base_price=5.0,
                            scale_factor=2.0, num_segments=3),
        ],
    )

    elec = ElectrolyzerConfig(
        name="H2Plant",
        **_elec_arrays(n, rated=[15.0, 0.0],
                       invest_cost=[0.0, 3000.0], invest_max=[0.0, 25.0]),
    )

    buses = [
        BusConfig(bus_id="bus_hv0", name="HV0", parent_node=0,
                  voltage_kv=220.0, role="load", demand_fraction=1.0),
        BusConfig(bus_id="bus_lv0", name="LV0", parent_node=0,
                  voltage_kv=33.0, role="connection", demand_fraction=0.0),
        BusConfig(bus_id="bus_hv1", name="HV1", parent_node=1,
                  voltage_kv=220000.0, role="load", demand_fraction=1.0),
    ]

    transformer = TransformerConfig(
        name="T1", from_node=0, to_node=0,
        from_bus=0, to_bus=1,
        from_voltage_kv=220.0, to_voltage_kv=33.0,
        rated_power_mva=100.0, impedance_pu=0.1, losses_fraction=0.005,
    )

    acdc = ACDCConverterConfig(
        name="VSC1", from_node=0, to_node=1,
        from_voltage_kv=220.0, dc_voltage_kv=320.0, rated_power_mva=200.0,
        invest_cost=100.0, invest_max_power=50.0,
    )
    freqc = FrequencyConverterConfig(
        name="FC1", from_node=0, to_node=1,
        invest_cost=80.0, invest_max_power=40.0,
    )

    zone = DevelopmentZoneConfig(
        name="ZoneA", technology="Solar", layer="electrical",
        polygon=[GeoCoordinate(latitude=10.0, longitude=20.0),
                 GeoCoordinate(latitude=10.1, longitude=20.1),
                 GeoCoordinate(latitude=10.0, longitude=20.2)],
        max_capacity_mw=500.0, notes="sunny",
        allowed_generators=["GasPlant"],
        allowed_technologies={"Solar": 100.0},
        exclusive=True,
    )

    fuel_entry = FuelEntryPointConfig(
        name="Port", fuels=["Gas", "Diesel"], node=0,
        coordinate=GeoCoordinate(latitude=10.0, longitude=20.0),
        fuel_params={
            "Gas": {"max_import_rate": 1000.0, "import_cost": 5.0},
            "Diesel": {"max_import_rate": 500.0, "import_cost": 8.0},
        },
    )

    primary_src = PrimaryEnergySourceConfig(
        name="GasField", unit="MWh_th",
        max_availability=[10000.0, 0.0], import_cost=[3.0, 4.0],
        storage_capacity=[500.0, 0.0], initial_storage_level=[0.5, 0.0],
        min_storage_level=0.1, storage_investment_cost=100.0,
        transport_cost=0.5, transport_losses=0.02,
        max_storage_investment_per_node=1000.0,
        max_transport_investment_per_arc=2000.0,
    )

    fuel_infra = FuelInfrastructureConfig(
        transport_pipelines={
            "pipe1": {
                "route_id": "fuel_route_pipe1",
                "from_node": 0, "to_node": 1,
                "fuels": ["Gas"],
                "fuel_params": {
                    "Gas": {"capacity": 200.0, "transport_cost": 1.0,
                            "losses_fraction": 0.01},
                },
                "length_km": 50.0,
                "waypoints": [
                    {"latitude": 10.0, "longitude": 20.0},
                    {"latitude": 11.0, "longitude": 21.0},
                ],
            },
        },
        storage_facilities={
            "store1": {
                "name": "GasCavern",
                "node": 1,
                "fuel_params": {
                    "Gas": {"capacity": 300.0, "initial_level": 0.5,
                            "min_level": 0.1},
                },
            },
        },
    )

    sys = SystemConfig(
        name=name,
        nodes=nodes,
        buses=buses,
        fuels={"Gas": FuelConfig(name="Gas", emission_factor=0.2,
                                 price_base=5.0),
               "Diesel": FuelConfig(name="Diesel", emission_factor=0.3, price_base=0.0)},
        generators={"GasPlant": gen},
        batteries={"BESS": bat},
        electrolyzers={"H2Plant": elec},
        transformers=[transformer],
        acdc_converters=[acdc],
        freq_converters=[freqc],
        development_zones=[zone],
        fuel_entry_points=[fuel_entry],
        primary_energy_sources={"GasField": primary_src},
        fuel_infrastructure=fuel_infra,
        electric_demand={"residential": DemandSectorConfig(
            is_flexible=True, flexibility_ratio=0.2, criticality="high",
            delay_tolerance=3, price_sensitivity=0.1)},
        sector_distribution={0: {"residential": 1.0}, 1: {"residential": 1.0}},
        non_electric_demand={"heat": NonElectricDemandConfig(
            fuel="Gas", unit="MWh_th", demand=[100, 200])},
        ev_initial_soc=[0.5, 0.5],
        ev_categories={"car": EVCategoryConfig(
            battery_capacity=60.0, charging_power=11.0, v2g_power=7.0,
            v2g_participation=0.3, efficiency_charge=0.95,
            efficiency_discharge=0.95, min_soc=0.2)},
        ev_quantity={"car": [1000, 500]},
        base_patterns={"car": [0.1] * 24},
        rooftop_solar_config=RooftopSolarConfig(
            systems_per_node=[10, 20], avg_system_size=[5.0, 5.0],
            initial_adoption=[0.1, 0.2],
            max_adoption={"residential": 0.5},
            adoption_rates={"residential": 0.05}),
        map_center=GeoCoordinate(latitude=10.5, longitude=20.5),
        transmission_lines_geo=[
            TransmissionLineGeo(
                line_id="line_A", from_node=0, to_node=1,
                capacity_mw=150.0, voltage_kv=220000.0,
                line_type="overhead",
                waypoints=[GeoCoordinate(latitude=10.5, longitude=20.5)],
                from_endpoint_type="node", from_endpoint_id="0",
                to_endpoint_type="node", to_endpoint_id="1",
                length_km=120.0, reactance_pu=0.05, num_circuits=2,
            ),
        ],
    )
    return sys


def _rich_config(name="Rich"):
    sys = _rich_system(name)
    return ESFEXConfig(
        meta_network=MetaNetworkConfig(systems=[name]),
        systems={name: sys},
    )


# ---------------------------------------------------------------------------
# config_to_gui_states over a rich config (drives _system_to_gui_state)
# ---------------------------------------------------------------------------

def test_rich_config_to_gui_state_components():
    states = S.config_to_gui_states(_rich_config())
    st = states["Rich"]

    # nodes carry names + coordinates + reserves
    assert [nd.name for nd in st.nodes] == ["North", "South"]
    assert st.nodes[0].centroid_lat == 10.0
    assert st.nodes[0].reserve_static == 5.0

    # generator instance at node 0 only (presence via rated_power),
    # plus an investment-only instance at node 1 (invest_max_power > 0).
    assert "GasPlant_n0" in st.generators
    assert "GasPlant_n1" in st.generators
    g0 = st.generators["GasPlant_n0"]
    assert g0.gen_type == "Non-renewable"
    assert g0.rated_power == 100.0
    # linear fuel cost curve imported
    assert g0.fuel_cost_curve_type == "linear"
    assert g0.fuel_cost_curve_data["price_at_zero"] == 10.0
    # reservoir fields copied
    assert g0.reservoir_capacity == 1000.0
    assert g0.reservoir_inflow_file == "inflow.csv"

    # battery present at node 0; exponential discharge curve at node 1 is
    # not attached to n0 (flat -> None there)
    assert "BESS_n0" in st.batteries
    assert st.batteries["BESS_n0"].capacity == 80.0

    # electrolyzers present
    assert "H2Plant_n0" in st.electrolyzers
    assert "H2Plant_n1" in st.electrolyzers

    # buses: explicit 3 buses (voltage normalised: 220000 -> 220)
    assert set(st.buses.keys()) == {"bus_hv0", "bus_lv0", "bus_hv1"}
    assert st.buses["bus_hv1"].voltage_kv == 220.0

    # transformer resolved to distinct from/to buses
    assert len(st.transformers) == 1
    tr = st.transformers[0]
    assert tr.from_bus == "bus_hv0"
    assert tr.to_bus == "bus_lv0"

    # converters
    assert len(st.acdc_converters) == 1
    assert len(st.freq_converters) == 1

    # development zone
    assert len(st.development_zones) == 1
    z = st.development_zones[0]
    assert z.exclusive is True
    assert z.allowed_technologies == {"Solar": 100.0}

    # fuel entry points multi-fuel
    assert len(st.fuel_entry_points) == 1
    fe = st.fuel_entry_points[0]
    assert fe.fuels == ["Gas", "Diesel"]
    assert fe.fuel_params["Gas"].max_import_rate == 1000.0

    # fuel routes / storages
    assert len(st.fuel_transport_routes) == 1
    assert st.fuel_transport_routes[0].fuels == ["Gas"]
    assert "store1" in st.fuel_storages

    # demand sectors + non-electric + sector distribution
    assert st.demand_sectors["residential"].is_flexible is True
    assert st.non_electric_demand["heat"].demand == [100, 200]
    assert st.sector_distribution[0] == {"residential": 1.0}

    # EV
    assert "car" in st.ev_config.categories
    assert st.ev_config.categories["car"].quantity == [1000, 500]

    # rooftop solar
    assert st.rooftop_solar is not None
    assert st.rooftop_solar.systems_per_node == [10, 20]

    # transmission line (new format with line_id), voltage normalised
    assert any(ln.line_id == "line_A" for ln in st.transmission_lines)
    la = next(ln for ln in st.transmission_lines if ln.line_id == "line_A")
    assert la.voltage_kv == 220.0
    assert la.num_circuits == 2

    # map center
    assert st.map_center.lat == 10.5

    # investment portfolio built from invest fields
    assert any(e.target_key == "GasPlant" for e in st.investment_portfolio.values())
    assert any(e.target_key == "BESS" for e in st.investment_portfolio.values())
    assert any(e.target_key == "H2Plant" for e in st.investment_portfolio.values())


def test_rich_roundtrip_yaml_reloads(tmp_path):
    cfg = _rich_config("RT")
    states = S.config_to_gui_states(cfg)
    out = tmp_path / "rich.yaml"
    S.gui_state_to_yaml(states, cfg, out)
    assert out.is_file()
    loaded = yaml.safe_load(out.read_text())
    assert "RT" in loaded["systems"]
    sysd = loaded["systems"]["RT"]
    # generators serialized back
    assert "generators" in sysd
    # buses serialized
    assert "buses" in sysd

    # And the written YAML can be parsed back into an ESFEXConfig.
    cfg2 = ESFEXConfig(**loaded)
    states2 = S.config_to_gui_states(cfg2)
    assert "RT" in states2


# ---------------------------------------------------------------------------
# transformer legacy self-loop repair (from_bus == to_bus, voltages distinct)
# ---------------------------------------------------------------------------

def test_transformer_self_loop_repair_picks_sibling_bus():
    n = 2
    nodes = NodeConfig(num_nodes=n, nodes_connections=[0.0, 0.0, 0.0, 0.0])
    buses = [
        BusConfig(bus_id="b_hv", parent_node=0, voltage_kv=220.0),
        BusConfig(bus_id="b_lv", parent_node=0, voltage_kv=33.0),
    ]
    # Self-loop: both sides reference bus index 0 (b_hv), but voltages differ.
    tr = TransformerConfig(
        name="Tself", from_node=0, to_node=0, from_bus=0, to_bus=0,
        from_voltage_kv=220.0, to_voltage_kv=33.0,
        rated_power_mva=50.0,
    )
    sys = SystemConfig(name="S", nodes=nodes, buses=buses, transformers=[tr],
                       fuels={"X": FuelConfig(name="X", emission_factor=0.0, price_base=0.0)})
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=["S"]),
                      systems={"S": sys})
    st = S.config_to_gui_states(cfg)["S"]
    t = st.transformers[0]
    # Repair relocated the to-side to the 33 kV sibling bus.
    assert t.from_bus != t.to_bus
    assert t.to_bus == "b_lv"


def test_transformer_self_loop_collapsed_voltages():
    n = 1
    nodes = NodeConfig(num_nodes=n, nodes_connections=[0.0])
    buses = [
        BusConfig(bus_id="b0", parent_node=0, voltage_kv=110.0),
        BusConfig(bus_id="b1", parent_node=0, voltage_kv=10.0),
    ]
    # Both voltages collapsed (equal) AND from_bus == to_bus -> still a
    # self-loop; relocate to any other bus on the node (prefer different V).
    tr = TransformerConfig(
        name="Tc", from_node=0, to_node=0, from_bus=0, to_bus=0,
        from_voltage_kv=110.0, to_voltage_kv=110.0, rated_power_mva=20.0,
    )
    sys = SystemConfig(name="S", nodes=nodes, buses=buses, transformers=[tr],
                       fuels={"X": FuelConfig(name="X", emission_factor=0.0, price_base=0.0)})
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=["S"]),
                      systems={"S": sys})
    st = S.config_to_gui_states(cfg)["S"]
    t = st.transformers[0]
    assert t.from_bus != t.to_bus
    assert t.to_bus == "b1"


# ---------------------------------------------------------------------------
# old-format transmission lines via adjacency matrix
# ---------------------------------------------------------------------------

def test_old_format_lines_from_adjacency():
    n = 2
    nodes = NodeConfig(num_nodes=n, nodes_connections=[0.0, 90.0, 90.0, 0.0])
    # geo entry WITHOUT line_id triggers old (adjacency) path; matching geo
    # entry supplies voltage/line_type metadata.
    geo = TransmissionLineGeo(from_node=0, to_node=1, voltage_kv=132.0,
                              line_type="underground", length_km=10.0,
                              reactance_pu=0.02, num_circuits=1)
    sys = SystemConfig(name="S", nodes=nodes, transmission_lines_geo=[geo],
                       fuels={"X": FuelConfig(name="X", emission_factor=0.0, price_base=0.0)})
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=["S"]),
                      systems={"S": sys})
    st = S.config_to_gui_states(cfg)["S"]
    assert len(st.transmission_lines) == 1
    ln = st.transmission_lines[0]
    assert ln.capacity_mw == 90.0
    assert ln.voltage_kv == 132.0
    assert ln.line_type == "underground"


def test_empty_system_n0_no_crash():
    nodes = NodeConfig(num_nodes=0, nodes_connections=[])
    sys = SystemConfig(name="Empty", nodes=nodes,
                       fuels={"X": FuelConfig(name="X", emission_factor=0.0, price_base=0.0)})
    cfg = ESFEXConfig(meta_network=MetaNetworkConfig(systems=["Empty"]),
                      systems={"Empty": sys})
    st = S.config_to_gui_states(cfg)["Empty"]
    assert st.nodes == []
    assert st.transmission_lines == []


# ---------------------------------------------------------------------------
# _load_demand_csv
# ---------------------------------------------------------------------------

def _node(idx):
    return GuiNode(index=idx, name=f"Node {idx}",
                   centroid_lat=0.0, centroid_lng=0.0)


def test_load_demand_csv_none_path_noop():
    nd = _node(0)
    S._load_demand_csv(None, [nd])
    assert nd.demand.data is None


def test_load_demand_csv_missing_file_noop():
    nd = _node(0)
    S._load_demand_csv("/no/such/file_xyz.csv", [nd])
    assert nd.demand.data is None


def test_load_demand_csv_traversal_refused(tmp_path):
    nd = _node(0)
    # relative path with .. that escapes cwd -> refused, returns silently
    S._load_demand_csv("../../etc/hosts", [nd])
    assert nd.demand.data is None


def test_load_demand_csv_multicolumn(tmp_path):
    p = tmp_path / "demand.csv"
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})
    df.to_csv(p, header=False, index=False)
    nodes = [_node(0), _node(1)]
    S._load_demand_csv(str(p), nodes)
    assert nodes[0].demand.data == [1.0, 2.0, 3.0]
    assert nodes[0].demand.peak_mw == 3.0
    assert nodes[0].demand.total_mwh == 6.0
    assert nodes[1].demand.data == [10.0, 20.0, 30.0]


def test_load_demand_csv_single_column_one_node(tmp_path):
    p = tmp_path / "single.csv"
    pd.DataFrame({"x": [5.0, 7.0]}).to_csv(p, header=False, index=False)
    nd = _node(0)
    S._load_demand_csv(str(p), [nd])
    assert nd.demand.data == [5.0, 7.0]
    assert nd.demand.num_hours == 2


def test_load_demand_csv_single_column_many_nodes_refused(tmp_path):
    p = tmp_path / "single2.csv"
    pd.DataFrame({"x": [5.0, 7.0]}).to_csv(p, header=False, index=False)
    nodes = [_node(0), _node(1)]
    S._load_demand_csv(str(p), nodes)
    # refusal: no demand assigned to either node
    assert nodes[0].demand.data is None
    assert nodes[1].demand.data is None


def test_load_demand_csv_col_index_out_of_range_skipped(tmp_path):
    p = tmp_path / "demand2.csv"
    pd.DataFrame({"a": [1.0], "b": [2.0]}).to_csv(p, header=False, index=False)
    # node.index=5 exceeds the 2 columns -> that node is skipped.
    nd = _node(5)
    S._load_demand_csv(str(p), [nd])
    assert nd.demand.data is None


def test_load_demand_csv_unreadable_returns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_bytes(b"\x00\x01\x02 not,a,valid\x00csv")
    nd = _node(0)
    # pandas may or may not raise; either way it must not crash.
    S._load_demand_csv(str(p), [nd])


# ---------------------------------------------------------------------------
# config_to_inter_system_links with rich GUI-extra metadata
# ---------------------------------------------------------------------------

def test_config_to_inter_system_links_rich_metadata():
    from esfex.config.schema import (
        MetaNetworkConfig as _MN,
        SystemLinkConfig as _SL,
    )
    sl = _SL(
        systems=["A", "B"],
        connections=[[0, 1]],
        existing_capacity_MW=[400.0],
        max_investment_MW=[100.0],
        investment_cost_per_MW=[50.0],
        loss_factor=[0.03],
        distance_km=[200.0],
        cost_per_mw_km=[2.0],
        reactance_pu=[0.06],
        resistance_pu=[0.006],
        waypoints=[[{"lat": 1.0, "lng": 2.0},
                    {"lat": "bad", "lng": "wp"}]],
        endpoints=[[
            {"element_type": "node", "element_id": "0"},
            {"element_type": "bus", "element_id": "bus_3"},
        ]],
        voltage_kv=[330.0],
        line_type=["overhead"],
        length_km=[210.0],
        base_impedance=[100.0],
        reactance_per_km=[0.4],
        susceptance_pu=[0.001],
        num_circuits=[3],
        frequency_hz=[60.0],
        current_type=["DC"],
        decorative=[True],
        style=[{"color": "#ff0000", "width": 5.0, "opacity": 0.7}],
    )
    meta = _MN(systems=["A", "B"], systems_links=[sl])

    class _Cfg:
        meta_network = meta

    links = S.config_to_inter_system_links(_Cfg())
    assert len(links) == 1
    lk = links[0]
    assert lk.from_system == "A" and lk.to_system == "B"
    assert lk.capacity_mw == 400.0
    assert lk.voltage_kv == 330.0
    assert lk.num_circuits == 3
    assert lk.frequency_hz == 60.0
    assert lk.current_type == "DC"
    assert lk.decorative is True
    # one good waypoint parsed, the malformed one dropped
    assert len(lk.waypoints) == 1
    assert lk.waypoints[0].lat == 1.0
    # endpoints rehydrated
    assert lk.from_endpoint == EndpointRef("node", "0")
    assert lk.to_endpoint == EndpointRef("bus", "bus_3")
    # style overrides applied
    assert lk.style.color == "#ff0000"
    assert lk.style.width == 5.0
    assert lk.style.opacity == 0.7


def test_config_to_inter_system_links_skips_short_connection():
    from esfex.config.schema import (
        MetaNetworkConfig as _MN,
        SystemLinkConfig as _SL,
    )
    # A connection with <2 endpoints is skipped; a systems list with <2
    # systems is skipped entirely.
    req = dict(existing_capacity_MW=[1.0], max_investment_MW=[0.0],
               investment_cost_per_MW=[0.0], loss_factor=[0.0],
               distance_km=[0.0], cost_per_mw_km=[0.0])
    sl_short_sys = _SL(systems=["Solo"], connections=[[0, 1]], **req)
    sl_short_conn = _SL(systems=["A", "B"], connections=[[0]], **req)
    meta = _MN(systems=["A", "B"],
               systems_links=[sl_short_sys, sl_short_conn])

    class _Cfg:
        meta_network = meta

    links = S.config_to_inter_system_links(_Cfg())
    assert links == []


# ---------------------------------------------------------------------------
# _parse_allowed_technologies extra (already partly covered; assert dict path)
# ---------------------------------------------------------------------------

def test_parse_allowed_technologies_empty_dict():
    assert S._parse_allowed_technologies({}) == {}
