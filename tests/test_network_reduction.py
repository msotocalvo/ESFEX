"""Unit tests for the topology reduction module.

The tests use small synthetic systems where the correct reduction is
easy to compute by hand.  Each test asserts both structural properties
(number of buses/lines after reduction) and numerical equivalence of
the ``ResultExpander`` output.
"""

from __future__ import annotations

import numpy as np
import pytest

from esfex.config.schema import BusConfig, SystemConfig, TransmissionLineGeo
from esfex.topology import reduce_network, ResultExpander
from esfex.topology.reduction_map import ReductionMap


def _make_buses(roles: list[str], dfs: list[float]) -> list[BusConfig]:
    """Build a BusConfig list from roles + demand_fractions."""
    out = []
    for i, (role, df) in enumerate(zip(roles, dfs)):
        out.append(BusConfig(
            bus_id=f"bus_{i}",
            parent_node=0,
            voltage_kv=110.0,
            demand_fraction=df,
            role=role,
        ))
    return out


def _make_line(
    line_id: str, from_bus: int, to_bus: int,
    x: float = 0.1, r: float = 0.01, cap: float = 100.0,
    length: float = 10.0,
) -> TransmissionLineGeo:
    return TransmissionLineGeo(
        line_id=line_id,
        from_node=0, to_node=0,
        from_bus=from_bus, to_bus=to_bus,
        reactance_pu=x, resistance_pu=r,
        capacity_mw=cap, length_km=length, num_circuits=1,
    )


def _minimal_system(
    buses: list[BusConfig],
    lines: list[TransmissionLineGeo],
) -> SystemConfig:
    """Build the smallest valid SystemConfig for reduction testing."""
    from esfex.config.schema import NodeConfig
    nodes_connections = [1.0]  # 1×1 connectivity
    return SystemConfig(
        name="TestSystem",
        nodes=NodeConfig(
            num_nodes=1,
            nodes_connections=nodes_connections,
            reserve_static=[0.0],
            reserve_dynamic=[0.0],
            reserve_duration=[1],
            losses=[0.0],
            transference_invest_cost=[0.0],
            transference_invest_max=[0.0],
            node_names=["N0"],
        ),
        generators={},
        batteries={},
        technologies={},
        battery_technologies={},
        buses=buses,
        transmission_lines_geo=lines,
        transformers=[],
        acdc_converters=[],
        freq_converters=[],
    )


# ════════════════════════════════════════════════════════════════════════
# Test 1: Leaf pruning
# ════════════════════════════════════════════════════════════════════════

def test_prune_single_leaf():
    """A → B where B is a passive connection bus gets pruned."""
    buses = _make_buses(["load", "connection"], [1.0, 0.0])
    lines = [_make_line("L_AB", 0, 1, x=0.1, cap=50.0)]
    sys = _minimal_system(buses, lines)

    reduced, rm = reduce_network(sys)

    assert rm.n_reduced_buses == 1
    assert rm.n_reduced_lines == 0  # the leaf's line also goes
    assert 1 in rm.pruned_buses
    assert rm.pruned_buses[1].angle_source_bus == 0


def test_prune_chain_of_leaves():
    """A (load) — B (conn) — C (conn).  B and C both become leaves in turn."""
    buses = _make_buses(["load", "connection", "connection"], [1.0, 0.0, 0.0])
    lines = [
        _make_line("L_AB", 0, 1),
        _make_line("L_BC", 1, 2),
    ]
    sys = _minimal_system(buses, lines)

    reduced, rm = reduce_network(sys)

    # C is a leaf → pruned.  Then B becomes a leaf → pruned.  Only A remains.
    assert rm.n_reduced_buses == 1
    assert rm.n_reduced_lines == 0
    assert 1 in rm.pruned_buses
    assert 2 in rm.pruned_buses


# ════════════════════════════════════════════════════════════════════════
# Test 2: Parallel merge
# ════════════════════════════════════════════════════════════════════════

def test_parallel_merge_two_equal_lines():
    """Two identical lines A↔B in parallel: x_eq = x/2, cap_eq = 2*cap."""
    buses = _make_buses(["load", "load"], [0.5, 0.5])
    lines = [
        _make_line("L1", 0, 1, x=0.2, cap=100.0),
        _make_line("L2", 0, 1, x=0.2, cap=100.0),
    ]
    sys = _minimal_system(buses, lines)

    reduced, rm = reduce_network(sys)

    assert rm.n_reduced_buses == 2
    assert rm.n_reduced_lines == 1
    merged = rm.merged_lines[0]
    # Parallel: x = 1 / (1/0.2 + 1/0.2) = 0.1
    assert merged.reactance_pu == pytest.approx(0.1)
    # Binding-constraint capacity: each line carries share=0.5 of total,
    # so cap_eq = min(cap_i / share_i) = 100/0.5 = 200 for both members.
    assert merged.capacity_mw == pytest.approx(200.0)
    # Flow shares should sum to 1.0 (admittance conservation)
    total_share = sum(r.flow_share for r in merged.originals)
    assert total_share == pytest.approx(1.0)


def test_parallel_merge_capacity_proportional_admittance():
    """Binding capacity = min(cap_i / share_i), not Σ cap_i."""
    buses = _make_buses(["load", "load"], [0.5, 0.5])
    # L1: x=0.1 (y=10, share=10/15=2/3), cap=60 → binding=60/(2/3)=90
    # L2: x=0.2 (y=5,  share=5/15=1/3),  cap=50 → binding=50/(1/3)=150
    # Merged cap = min(90, 150) = 90 (L1 binds first)
    lines = [
        _make_line("L1", 0, 1, x=0.1, cap=60.0),
        _make_line("L2", 0, 1, x=0.2, cap=50.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)
    merged = rm.merged_lines[0]
    assert merged.capacity_mw == pytest.approx(90.0)
    # Verify: at cap_eq=90, L1 carries 90*(2/3)=60 (its cap ✓),
    #                       L2 carries 90*(1/3)=30 (< its 50 cap ✓)


def test_parallel_merge_flow_expansion():
    """Verify flow split is admittance-proportional."""
    buses = _make_buses(["load", "load"], [0.5, 0.5])
    # x1=0.1 (y=10), x2=0.2 (y=5): total y=15, shares 2/3 and 1/3
    lines = [
        _make_line("L1", 0, 1, x=0.1, cap=200.0),
        _make_line("L2", 0, 1, x=0.2, cap=100.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    # Suppose the reduced line carries 60 MW from bus 0 → bus 1
    reduced_flow = np.array([60.0])  # shape (1,)
    expander = ResultExpander(rm)
    expanded = expander.expand_line_flows(reduced_flow, axis=0)

    # L1 should get 60 * 2/3 = 40, L2 should get 60 * 1/3 = 20
    assert expanded.shape == (2,)
    assert expanded[0] == pytest.approx(40.0)
    assert expanded[1] == pytest.approx(20.0)


# ════════════════════════════════════════════════════════════════════════
# Test 3: Series collapse
# ════════════════════════════════════════════════════════════════════════

def test_series_collapse_three_buses():
    """A (load) — B (conn) — C (load), B gets eliminated."""
    buses = _make_buses(["load", "connection", "load"], [0.5, 0.0, 0.5])
    lines = [
        _make_line("L_AB", 0, 1, x=0.1, r=0.01, cap=80.0),
        _make_line("L_BC", 1, 2, x=0.15, r=0.02, cap=120.0),
    ]
    sys = _minimal_system(buses, lines)

    reduced, rm = reduce_network(sys)

    # B is eliminated; A and C remain.  One merged line.
    assert rm.n_reduced_buses == 2
    assert rm.n_reduced_lines == 1
    merged = rm.merged_lines[0]
    assert merged.reactance_pu == pytest.approx(0.25)   # x_AB + x_BC
    assert merged.resistance_pu == pytest.approx(0.03)
    assert merged.capacity_mw == pytest.approx(80.0)    # bottleneck
    assert len(merged.originals) == 2

    # Flow on both originals equals the merged flow
    for ref in merged.originals:
        assert ref.flow_share == pytest.approx(1.0)

    # Verify angle reconstruction
    # Suppose θ_A = 0.10 rad, θ_C = 0.04 rad (reduced solution)
    reduced_angles = np.array([0.10, 0.04])
    expander = ResultExpander(rm)
    expanded = expander.expand_voltage_angles(reduced_angles, axis=0)
    assert expanded.shape == (3,)
    assert expanded[0] == pytest.approx(0.10)
    assert expanded[2] == pytest.approx(0.04)
    # θ_B should be between θ_A and θ_C, weighted by reactance fractions
    # frac_from = x_AB / x_total = 0.1/0.25 = 0.4
    # θ_B = θ_A * (1 - 0.4) + θ_C * 0.4 = 0.10*0.6 + 0.04*0.4 = 0.076
    assert expanded[1] == pytest.approx(0.076)


def test_series_collapse_chain_four_buses():
    """A (load) — B (conn) — C (conn) — D (load).  Both B and C eliminated."""
    buses = _make_buses(
        ["load", "connection", "connection", "load"],
        [0.5, 0.0, 0.0, 0.5]
    )
    lines = [
        _make_line("L_AB", 0, 1, x=0.1, cap=100.0),
        _make_line("L_BC", 1, 2, x=0.1, cap=80.0),
        _make_line("L_CD", 2, 3, x=0.1, cap=90.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    assert rm.n_reduced_buses == 2
    assert rm.n_reduced_lines == 1
    merged = rm.merged_lines[0]
    assert merged.reactance_pu == pytest.approx(0.3)
    assert merged.capacity_mw == pytest.approx(80.0)
    assert len(merged.originals) == 3


# ════════════════════════════════════════════════════════════════════════
# Test 4: Retain protected buses
# ════════════════════════════════════════════════════════════════════════

def test_retain_load_bus_even_if_degree_2():
    """A degree-2 load bus must NOT be collapsed."""
    buses = _make_buses(["load", "mixed", "load"], [0.3, 0.4, 0.3])
    lines = [
        _make_line("L_AB", 0, 1),
        _make_line("L_BC", 1, 2),
    ]
    sys = _minimal_system(buses, lines)

    reduced, rm = reduce_network(sys)

    # Nothing should be reduced — all three buses have load/mixed roles
    assert rm.n_reduced_buses == 3
    assert rm.n_reduced_lines == 2


# ════════════════════════════════════════════════════════════════════════
# Test 5: Leaf expansion preserves bus angle
# ════════════════════════════════════════════════════════════════════════

def test_leaf_pruning_angle_expansion():
    """Pruned leaf inherits its neighbour's angle exactly."""
    buses = _make_buses(["load", "connection"], [1.0, 0.0])
    lines = [_make_line("L_AB", 0, 1)]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    reduced_angles = np.array([0.123])
    expander = ResultExpander(rm)
    expanded = expander.expand_voltage_angles(reduced_angles)
    assert expanded.shape == (2,)
    assert expanded[0] == pytest.approx(0.123)
    assert expanded[1] == pytest.approx(0.123)  # leaf inherits


# ════════════════════════════════════════════════════════════════════════
# Test 6: Leaf line flow should be zero
# ════════════════════════════════════════════════════════════════════════

def test_leaf_line_flow_is_zero():
    """The line attached to a pruned leaf must report zero flow."""
    buses = _make_buses(["load", "connection"], [1.0, 0.0])
    lines = [_make_line("L_AB", 0, 1)]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    # With 0 reduced lines, the reduced flow array is empty
    reduced_flow = np.zeros(0)
    expander = ResultExpander(rm)
    expanded = expander.expand_line_flows(reduced_flow, axis=0)
    assert expanded.shape == (1,)
    assert expanded[0] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════════
# Test 7: No-op when there's nothing to reduce
# ════════════════════════════════════════════════════════════════════════

def test_no_reduction_when_all_protected():
    """A cycle of three load buses: nothing to reduce."""
    buses = _make_buses(["load", "load", "load"], [1/3, 1/3, 1/3])
    lines = [
        _make_line("L_01", 0, 1),
        _make_line("L_12", 1, 2),
        _make_line("L_20", 2, 0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)
    assert rm.n_reduced_buses == 3
    assert rm.n_reduced_lines == 3


# ════════════════════════════════════════════════════════════════════════
# Test 8: Combined transformations (realistic minichain)
# ════════════════════════════════════════════════════════════════════════

def test_combined_transformations():
    """Realistic scenario: leaf + series collapse + parallel merge."""
    # Topology:
    #   [0:load] ═ [1:conn] ══ [2:load]
    #              │
    #             [3:conn]    (leaf off bus 1)
    # Two parallel lines between 0-1; single 1-2; leaf line 1-3.
    buses = _make_buses(
        ["load", "connection", "load", "connection"],
        [0.4, 0.0, 0.6, 0.0]
    )
    lines = [
        _make_line("L_01a", 0, 1, x=0.2, cap=100.0),
        _make_line("L_01b", 0, 1, x=0.2, cap=100.0),   # parallel
        _make_line("L_12", 1, 2, x=0.15, cap=80.0),
        _make_line("L_13_leaf", 1, 3, x=0.3, cap=20.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    # Expected: bus 3 pruned; buses 0,1,2 have load role or are endpoint.
    # But bus 1 is connection and after leaf pruning becomes degree-2 (two
    # lines: the parallel-merged to 0 and the one to 2) → series collapse.
    # After parallel merge on (0,1): two→one.  Then leaf pruning on 3.
    # Then bus 1 is degree-2 connection → series collapsed.
    #
    # Final: 2 buses (0, 2), 1 line (merged 0↔2 with x = 0.1 + 0.15 = 0.25,
    # capacity = min(200, 80) = 80).
    assert rm.n_reduced_buses == 2
    assert rm.n_reduced_lines == 1
    merged = rm.merged_lines[0]
    assert merged.capacity_mw == pytest.approx(80.0)
    assert merged.reactance_pu == pytest.approx(0.25)
    # Three originals (L_01a, L_01b, L_12) — the leaf L_13_leaf is NOT in
    # any merged line (it's ghost-mapped to zero flow).
    assert len(merged.originals) == 3

    # Expand a hypothetical 50 MW flow on the merged line
    reduced_flow = np.array([50.0])
    expander = ResultExpander(rm)
    expanded = expander.expand_line_flows(reduced_flow, axis=0)
    assert expanded.shape == (4,)
    # L_01a and L_01b each carry half of 50 = 25 (equal impedances)
    assert expanded[0] == pytest.approx(25.0)
    assert expanded[1] == pytest.approx(25.0)
    # L_12 carries all 50 MW (series, no split)
    assert expanded[2] == pytest.approx(50.0)
    # Leaf line carries 0
    assert expanded[3] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════════
# Test 9: Identity expansion (no reduction)
# ════════════════════════════════════════════════════════════════════════

def test_transformer_absorbed_in_series():
    """A transformer between two passive buses is absorbed into the chain."""
    from esfex.config.schema import TransformerConfig

    buses = _make_buses(
        ["load", "connection", "connection", "load"],
        [0.5, 0.0, 0.0, 0.5],
    )
    # Topology: 0 ━━line━━ 1 ━transformer━ 2 ━━line━━ 3
    lines = [
        _make_line("L_01", 0, 1, x=0.1, cap=80.0),
        _make_line("L_23", 2, 3, x=0.1, cap=80.0),
    ]
    transformers = [
        TransformerConfig(
            name="T_12",
            from_node=0, to_node=0,
            from_bus=1, to_bus=2,
            from_voltage_kv=110, to_voltage_kv=33,
            rated_power_mva=100.0, impedance_pu=0.05,
            resistance_pu=0.005,
        ),
    ]
    from esfex.config.schema import NodeConfig, SystemConfig
    sys = SystemConfig(
        name="TestSys",
        nodes=NodeConfig(
            num_nodes=1, nodes_connections=[1.0],
            reserve_static=[0.0], reserve_dynamic=[0.0],
            reserve_duration=[1], losses=[0.0],
            transference_invest_cost=[0.0], transference_invest_max=[0.0],
            node_names=["N0"],
        ),
        generators={}, batteries={}, technologies={}, battery_technologies={},
        buses=buses,
        transmission_lines_geo=lines,
        transformers=transformers,
        acdc_converters=[], freq_converters=[],
    )
    reduced, rm = reduce_network(sys)

    # Expect buses 1 and 2 (passive) to be series-collapsed → 2 retained,
    # single merged edge between bus 0 and bus 3, transformer absorbed.
    assert rm.n_reduced_buses == 2
    assert rm.n_reduced_lines == 1
    assert len(reduced.transformers) == 0  # fully absorbed
    assert getattr(rm, "n_absorbed_transformers", 0) == 1

    merged = rm.merged_lines[0]
    # Transformer impedance |z|=0.05, r=0.005 → x = sqrt(0.05²−0.005²) ≈ 0.04975
    # Total reactance: 0.1 + 0.04975 + 0.1 ≈ 0.24975
    import math as _m
    expected_x = 0.1 + _m.sqrt(0.05**2 - 0.005**2) + 0.1
    assert merged.reactance_pu == pytest.approx(expected_x)
    # Capacity is bottleneck: min(80, 100, 80) = 80
    assert merged.capacity_mw == pytest.approx(80.0)
    # Only 2 line originals (transformer is tracked separately)
    assert len(merged.originals) == 2

    # Expand a 40 MW flow across the merged edge:
    # Julia outputs 1 entry (reduced); expander produces n_lines+n_tf = 3 entries
    reduced_flow = np.array([40.0])  # (reduced_lines=1,)
    expander = ResultExpander(rm)
    expanded = expander.expand_line_plus_transformer_array(reduced_flow, axis=0)
    # Ordering: [L_01, L_23, T_12]
    assert expanded.shape == (3,)
    assert expanded[0] == pytest.approx(40.0)  # L_01
    assert expanded[1] == pytest.approx(40.0)  # L_23
    assert expanded[2] == pytest.approx(40.0)  # T_12


def test_loss_conductance_preserved_in_series():
    """g_eq = g1 + g2 for series; verifies loss-equivalent resistance is set.

    Uses R/X ≈ 0.02 (typical HV transmission ratio) where the
    representability constraint ``g_target ≤ 1/(2·x_eq)`` is comfortably met.
    """
    buses = _make_buses(["load", "connection", "load"], [0.5, 0.0, 0.5])
    # Realistic HV line params: R/X ≈ 0.02
    lines = [
        _make_line("L_AB", 0, 1, x=0.10, r=0.002, cap=80.0),
        _make_line("L_BC", 1, 2, x=0.10, r=0.002, cap=80.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    g_target = (0.002 / (0.002**2 + 0.10**2)) + (0.002 / (0.002**2 + 0.10**2))
    red_line = reduced.transmission_lines_geo[0]
    r = red_line.resistance_pu
    x = red_line.reactance_pu
    g_actual = r / (r * r + x * x)
    assert g_actual == pytest.approx(g_target, rel=1e-6)


def test_loss_conductance_preserved_in_parallel():
    """Σ g_i · share_i² for parallel; resistance set to match."""
    buses = _make_buses(["load", "load"], [0.5, 0.5])
    # Realistic HV line params: R/X ≈ 0.02
    lines = [
        _make_line("L1", 0, 1, x=0.10, r=0.002, cap=200.0),
        _make_line("L2", 0, 1, x=0.20, r=0.004, cap=100.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    g1 = 0.002 / (0.002**2 + 0.10**2)
    g2 = 0.004 / (0.004**2 + 0.20**2)
    share1 = (1 / 0.10) / (1/0.10 + 1/0.20)
    share2 = (1 / 0.20) / (1/0.10 + 1/0.20)
    g_target = g1 * share1**2 + g2 * share2**2

    red_line = reduced.transmission_lines_geo[0]
    r = red_line.resistance_pu
    x = red_line.reactance_pu
    g_actual = r / (r * r + x * x)
    assert g_actual == pytest.approx(g_target, rel=1e-6)


def test_loss_conductance_numerical_precision_small_gx():
    """For g·X ≪ 1 the naive formula loses precision; conjugate form must hold."""
    from esfex.topology.network_reducer import _resistance_for_target_conductance
    # Realistic merged-edge values from IslaJuventud (transformer parallel-merged
    # with lossless line).  ε = 4·g²·X² ≈ 5e-11.
    g_target = 4.13e-4
    x_eq = 0.00909
    r = _resistance_for_target_conductance(g_target, x_eq)
    g_actual = r / (r * r + x_eq * x_eq)
    # Must reproduce g_target to high precision (NOT collapse to 0)
    assert g_actual == pytest.approx(g_target, rel=1e-9)


def test_loss_conductance_clamped_when_exceeds_max():
    """When g_target > 1/(2·X) (lossy short lines), clamp to physical limit."""
    from esfex.topology.network_reducer import _resistance_for_target_conductance
    # X = 0.1 → max representable g = 1/(2·0.1) = 5.0 (achieved at R=X)
    # Try g_target = 10 (impossible)
    r = _resistance_for_target_conductance(g_target=10.0, x_eq=0.1)
    g_actual = r / (r * r + 0.1**2)
    assert g_actual == pytest.approx(5.0, rel=1e-9)
    # Apex of R/(R²+X²) is at R=X
    assert r == pytest.approx(0.1)


def test_kron_deg3_eliminates_junction():
    """A degree-3 non-protected bus is eliminated via star-mesh (Kron)."""
    #    A ─── B ─── C       A ─── C (fictitious)
    #          │        →    │     │
    #          D             D ────┘ (fictitious)
    buses = _make_buses(
        ["load", "connection", "load", "load"],
        [0.4, 0.0, 0.3, 0.3],
    )
    lines = [
        _make_line("L_AB", 0, 1, x=0.1, cap=100.0),
        _make_line("L_BC", 1, 2, x=0.1, cap=100.0),
        _make_line("L_BD", 1, 3, x=0.2, cap=100.0),
    ]
    sys = _minimal_system(buses, lines)
    # Default reduce (no Kron): bus 1 is degree-3 connection, not reduced
    reduced_nokron, rm_nokron = reduce_network(sys)
    assert rm_nokron.n_reduced_buses == 4
    assert rm_nokron.n_reduced_lines == 3

    # With Kron enabled: bus 1 eliminated, 3 fictitious edges created
    reduced, rm = reduce_network(sys, kron_deg3=True)
    assert rm.n_reduced_buses == 3
    assert rm.n_reduced_lines == 3  # 3 star edges → 3 mesh edges


def test_kron_deg3_angle_recovery():
    """Kron-eliminated bus recovers its angle via admittance-weighted average."""
    buses = _make_buses(
        ["load", "connection", "load", "load"],
        [0.4, 0.0, 0.3, 0.3],
    )
    lines = [
        # y₁=10, y₂=10, y₃=5 → Y=25
        _make_line("L_AB", 0, 1, x=0.1, cap=100.0),
        _make_line("L_BC", 1, 2, x=0.1, cap=100.0),
        _make_line("L_BD", 1, 3, x=0.2, cap=100.0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys, kron_deg3=True)

    # Retained order: A(0), C(2), D(3) → reduced indices {0, 1, 2}
    # Test: reduced angles θ_A=0.1, θ_C=0.05, θ_D=0.02
    reduced_angles = np.array([0.10, 0.05, 0.02])
    expander = ResultExpander(rm)
    expanded = expander.expand_voltage_angles(reduced_angles)

    # Check θ_B via admittance-weighted average:
    #   θ_B = (10·θ_A + 10·θ_C + 5·θ_D) / 25
    #       = (10·0.1 + 10·0.05 + 5·0.02) / 25
    #       = (1.0 + 0.5 + 0.1) / 25 = 0.064
    assert expanded.shape == (4,)
    assert expanded[0] == pytest.approx(0.10)
    assert expanded[1] == pytest.approx(0.064)  # bus B (eliminated)
    assert expanded[2] == pytest.approx(0.05)
    assert expanded[3] == pytest.approx(0.02)


def test_kron_skipped_for_degree4_hub():
    """Degree-4+ buses are NOT Kron-eliminated (mesh explosion guard)."""
    # A - B - C  with B also connected to D and E
    buses = _make_buses(
        ["load", "connection", "load", "load", "load"],
        [0.2, 0.0, 0.2, 0.2, 0.4],
    )
    lines = [
        _make_line("L_AB", 0, 1),
        _make_line("L_BC", 1, 2),
        _make_line("L_BD", 1, 3),
        _make_line("L_BE", 1, 4),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys, kron_deg3=True)
    # Bus 1 is degree-4 → not eliminated
    assert rm.n_reduced_buses == 5
    assert rm.n_reduced_lines == 4


def test_expansion_identity_when_no_reduction():
    """If nothing was reduced, expansion is the identity."""
    buses = _make_buses(["load", "load", "load"], [1/3, 1/3, 1/3])
    lines = [
        _make_line("L_01", 0, 1),
        _make_line("L_12", 1, 2),
        _make_line("L_20", 2, 0),
    ]
    sys = _minimal_system(buses, lines)
    reduced, rm = reduce_network(sys)

    angles = np.array([0.1, -0.05, 0.02])
    expanded = ResultExpander(rm).expand_voltage_angles(angles)
    np.testing.assert_allclose(expanded, angles)

    flows = np.array([10.0, -5.0, 7.5])
    expanded_f = ResultExpander(rm).expand_line_flows(flows)
    np.testing.assert_allclose(expanded_f, flows)
