"""Convert a GuiSystemState into an SLD layout descriptor.

Instead of a deep ELK compound hierarchy (which produces poor results for
power-system single-line diagrams), this module uses a **two-pass** strategy:

1. Build a **flat** ELK graph where each *bus* is a node and each
   *transmission line / transformer / converter* is an edge.  ELK's layered
   algorithm then positions the buses cleanly.

2. Attach **equipment lists** to each bus as metadata.  The JavaScript
   renderer manually arranges equipment in an evenly-spaced row below each
   bus bar and draws vertical stubs — giving the classic PowerFactory look.

The output is a single JSON dict with two top-level keys:

- ``"elkGraph"`` — the flat ELK graph (buses + inter-bus edges)
- ``"busEquipment"`` — ``{ bus_id: [ {equipment metadata}, ... ] }``
- ``"nodeGroups"`` — ``[ { nodeId, name, busIds: [...] }, ... ]``
"""

from __future__ import annotations

import logging
from typing import Any

from esfex.visualization.data.gui_model import GuiSystemState
from esfex.visualization.sld.voltage_colors import get_voltage_color

log = logging.getLogger(__name__)

# ── Sizing constants ──
# Bus bars are drawn HORIZONTAL (with merge-by-substation collapsing
# many GuiBus into one wide bar per voltage level).
_BUS_H = 6               # Bus bar thickness
_BUS_MIN_LEN = 200       # Minimum bus bar length (horizontal extent)
_BUS_PER_EQUIP = 70      # Extra bar length per attached equipment slot
_BUS_PER_EDGE = 28       # Extra bar length per inter-bus terminal slot
_BUS_LABEL_PAD = 24      # Bottom/top label margin
_EQUIP_SIZE = 36         # Symbol diameter
_EQUIP_SPACING = 70      # Spacing between equipment slots along the bar
_STUB_LEN = 36           # Vertical stub length (bar → equipment center)

# ── Deterministic layout constants (PowerFactory-style grid) ──
_ROW_SPACING_Y = 280     # Minimum vertical distance between voltage rows
_COL_GAP_X = 100         # Horizontal gap between adjacent node columns
_INTRA_NODE_GAP_X = 24   # Gap between adjacent bars at the same
                         # (substation, voltage) — only used when a
                         # node has multiple groups at one voltage
                         # (rare with merge enabled).
_LANE_STEP_Y = 14        # Vertical spacing between adjacent edge lanes
                         # within a row gap (each edge gets its own lane Y
                         # for the horizontal segment, eliminating overlap)
_LANE_MARGIN_Y = 30      # Margin from row edges to first/last lane


def build_elk_graph(
    state: GuiSystemState,
    theme_colors: dict | None = None,
    filter_substation: int | None = None,
    merge_level: int = 1,
) -> dict:
    """Build the SLD layout descriptor from the current system state.

    Parameters
    ----------
    state : GuiSystemState
        The system to render.
    theme_colors : dict, optional
        Per-element-type color overrides.
    filter_substation : int, optional
        If provided, only buses with ``parent_node == filter_substation``
        are included.
    merge_level : int
        Aggregation level:

        * ``0`` — no merge: each ``GuiBus`` renders as its own bar.
        * ``1`` — merge by ``(substation, voltage)``: all sub-buses at a
          given voltage in a given substation collapse into one bar
          (default — best general-purpose level).
        * ``2`` — merge by substation: a single bar per substation with
          all voltages combined; only inter-substation edges remain.

    Returns
    -------
    dict
        ``{"elkGraph": ..., "busEquipment": ..., "nodeGroups": ...,
           "constants": ...}``
    """
    colors = theme_colors or {}
    merge_level = max(0, min(2, int(merge_level)))

    # ── Map bus_id → parent_node for topology validation ──
    if filter_substation is not None:
        included_buses = {
            bus.bus_id for bus in state.buses.values()
            if bus.parent_node == filter_substation
        }
    else:
        included_buses = set(state.buses.keys())
    bus_to_node: dict[str, int] = {
        bus.bus_id: bus.parent_node for bus in state.buses.values()
        if bus.bus_id in included_buses
    }

    # ── Group buses according to merge_level ──
    # Each visual "bus bar" is determined by the merge level:
    #   level 0: one bar per GuiBus (full detail).
    #   level 1: one bar per (substation, voltage) — feeders pooled.
    #   level 2: one bar per substation — voltages pooled too.
    bus_to_group: dict[str, str] = {}
    group_to_buses: dict[str, list[str]] = {}
    group_meta: dict[str, dict] = {}
    _node_by_idx = {n.index: n for n in state.nodes}

    # Pre-compute primary voltage per substation (highest voltage).
    sub_primary_v: dict[int, float] = {}
    for bus in state.buses.values():
        if bus.bus_id not in included_buses:
            continue
        cur = sub_primary_v.get(bus.parent_node, 0.0)
        if bus.voltage_kv > cur:
            sub_primary_v[bus.parent_node] = bus.voltage_kv

    for bus in state.buses.values():
        if bus.bus_id not in included_buses:
            continue
        node = _node_by_idx.get(bus.parent_node)
        n_name = node.name if node and node.name else f"Node {bus.parent_node}"
        if merge_level == 0:
            gid = f"bus_{bus.bus_id}"
            v_layout = bus.voltage_kv
            label = bus.name or bus.bus_id
        elif merge_level == 1:
            gid = f"bar_{bus.parent_node}_{int(round(bus.voltage_kv * 10))}"
            v_layout = bus.voltage_kv
            label = f"{n_name} {bus.voltage_kv:g} kV"
        else:  # merge_level == 2
            gid = f"sub_{bus.parent_node}"
            # Use primary voltage for layout positioning + color
            v_layout = sub_primary_v.get(bus.parent_node, bus.voltage_kv)
            label = n_name
        bus_to_group[bus.bus_id] = gid
        group_to_buses.setdefault(gid, []).append(bus.bus_id)
        if gid not in group_meta:
            group_meta[gid] = {
                "parent_node": bus.parent_node,
                "voltage_kv": v_layout,
                "name": label,
                "color": get_voltage_color(v_layout),
                "n_buses": 0,
            }
        group_meta[gid]["n_buses"] += 1

    # ── Collect equipment per merged bar (group_id) ──
    bus_equipment: dict[str, list[dict]] = {gid: [] for gid in group_meta}

    # Generators
    for gen in state.generators.values():
        gid = bus_to_group.get(gen.bus)
        if gid is None:
            continue
        sym = "gen-renewable" if gen.gen_type == "Renewable" else "gen-nonrenewable"
        color = colors.get(sym, "#27AE60" if sym == "gen-renewable" else "#7F8C8D")
        sublabel = f"{gen.rated_power:.0f} MW" if gen.rated_power else ""
        bus_equipment[gid].append({
            "elementType": "generator",
            "elementId": gen.instance_id,
            "label": gen.name,
            "sublabel": sublabel,
            "symbolType": sym,
            "color": color,
            "fuel": gen.fuel,
        })

    # Batteries
    for bat in state.batteries.values():
        gid = bus_to_group.get(bat.bus)
        if gid is None:
            continue
        color = colors.get("battery", "#F39C12")
        sublabel = f"{bat.capacity:.0f} MWh" if bat.capacity else ""
        bus_equipment[gid].append({
            "elementType": "battery",
            "elementId": bat.instance_id,
            "label": bat.name,
            "sublabel": sublabel,
            "symbolType": "battery",
            "color": color,
        })

    # Electrolyzers
    for el in state.electrolyzers.values():
        gid = bus_to_group.get(el.bus)
        if gid is None:
            continue
        color = colors.get("electrolyzer", "#16A085")
        sublabel = f"{el.rated_power:.0f} MW" if el.rated_power else ""
        bus_equipment[gid].append({
            "elementType": "electrolyzer",
            "elementId": el.instance_id,
            "label": el.name,
            "sublabel": sublabel,
            "symbolType": "electrolyzer",
            "color": color,
        })

    # Demand loads — attach the node's load to the LOWEST-voltage merged
    # bar of that node (where distribution feeders connect in real grids).
    node_buses: dict[int, list[str]] = {}
    for bus in state.buses.values():
        node_buses.setdefault(bus.parent_node, []).append(bus.bus_id)
    for node in state.nodes:
        node_buses.setdefault(node.index, [])
    for node in state.nodes:
        if not (node.demand and (node.demand.peak_mw or 0) > 0):
            continue
        # Find lowest-voltage group for this node
        node_groups = [
            (gid, m) for gid, m in group_meta.items()
            if m["parent_node"] == node.index
        ]
        if not node_groups:
            continue
        node_groups.sort(key=lambda x: x[1]["voltage_kv"])
        target_gid = node_groups[0][0]
        bus_equipment[target_gid].append({
            "elementType": "load",
            "elementId": f"load_node_{node.index}",
            "label": f"Load {node.name}",
            "sublabel": f"{node.demand.peak_mw:.0f} MW",
            "symbolType": "load",
            "color": colors.get("load", "#E67E22"),
        })

    # ── Aggregate inter-group edges and count per group ──
    # Multiple parallel circuits between the same two merged bars are
    # collapsed into ONE edge with summed capacity (one per edge type),
    # matching the PowerFactory convention where you see a single thick
    # tie-line between two substations annotated with N×.
    valid_bus_ids = included_buses
    _wiring_types = {
        "transformer", "generator", "battery", "electrolyzer",
        "acdc_converter", "freq_converter", "fuel_entry", "fuel_storage",
    }

    # key: (group_id_a, group_id_b sorted, edge_type) → aggregate dict
    agg_edges: dict[tuple[str, str, str], dict] = {}
    edge_count: dict[str, int] = {gid: 0 for gid in group_meta}

    def _aggregate(src_gid: str, tgt_gid: str, etype: str,
                    voltage: float, capacity: float, name: str) -> None:
        if src_gid == tgt_gid:
            return  # intra-group: same logical bar
        a, b = (src_gid, tgt_gid) if src_gid < tgt_gid else (tgt_gid, src_gid)
        key = (a, b, etype)
        agg = agg_edges.get(key)
        if agg is None:
            agg_edges[key] = {
                "n": 1,
                "total_mw": float(capacity or 0),
                "voltage": float(voltage or 0),
                "first_name": name,
            }
            edge_count[src_gid] += 1
            edge_count[tgt_gid] += 1
        else:
            agg["n"] += 1
            agg["total_mw"] += float(capacity or 0)
            if voltage and not agg["voltage"]:
                agg["voltage"] = float(voltage)

    for line in state.transmission_lines:
        if (line.from_bus not in valid_bus_ids
                or line.to_bus not in valid_bus_ids
                or line.from_bus == line.to_bus):
            continue
        from_ep = line.from_endpoint
        to_ep = line.to_endpoint
        if from_ep and from_ep.element_type in _wiring_types:
            continue
        if to_ep and to_ep.element_type in _wiring_types:
            continue
        sg = bus_to_group.get(line.from_bus)
        tg = bus_to_group.get(line.to_bus)
        if not sg or not tg:
            continue
        _aggregate(
            sg, tg, "transmission",
            line.voltage_kv or 220.0, line.capacity_mw or 0,
            line.line_id,
        )
    for i, tr in enumerate(state.transformers):
        if (tr.from_bus not in valid_bus_ids
                or tr.to_bus not in valid_bus_ids
                or tr.from_bus == tr.to_bus):
            continue
        sg = bus_to_group.get(tr.from_bus)
        tg = bus_to_group.get(tr.to_bus)
        if not sg or not tg:
            continue
        # Same-substation guard
        if group_meta[sg]["parent_node"] != group_meta[tg]["parent_node"]:
            continue
        _aggregate(
            sg, tg, "transformer",
            0.0, tr.rated_power_mva or 0,
            tr.name or f"Transformer {i}",
        )
    for i, conv in enumerate(state.acdc_converters):
        if (conv.from_bus not in valid_bus_ids
                or conv.to_bus not in valid_bus_ids
                or conv.from_bus == conv.to_bus):
            continue
        sg = bus_to_group.get(conv.from_bus)
        tg = bus_to_group.get(conv.to_bus)
        if not sg or not tg:
            continue
        if group_meta[sg]["parent_node"] != group_meta[tg]["parent_node"]:
            continue
        _aggregate(
            sg, tg, "converter",
            0.0, conv.rated_power_mva or 0,
            f"ACDC {i}",
        )
    for i, conv in enumerate(state.freq_converters):
        if (conv.from_bus not in valid_bus_ids
                or conv.to_bus not in valid_bus_ids
                or conv.from_bus == conv.to_bus):
            continue
        sg = bus_to_group.get(conv.from_bus)
        tg = bus_to_group.get(conv.to_bus)
        if not sg or not tg:
            continue
        if group_meta[sg]["parent_node"] != group_meta[tg]["parent_node"]:
            continue
        _aggregate(
            sg, tg, "converter",
            0.0, conv.rated_power_mva or 0,
            f"Freq {i}",
        )

    # ── Build flat ELK graph: ONE merged bar per (substation, voltage) ──
    # Horizontal bar: width = bar_len, height = bar + stub + equipment.
    elk_children: list[dict] = []
    for gid, meta in group_meta.items():
        n_eq = len(bus_equipment.get(gid, []))
        n_edges = edge_count.get(gid, 0)
        bar_len = max(
            _BUS_MIN_LEN,
            n_eq * _BUS_PER_EQUIP,
            n_edges * _BUS_PER_EDGE,
        )
        equip_extent = _STUB_LEN + _EQUIP_SIZE + 24 if n_eq > 0 else 12
        bus_w = bar_len + 40   # margin for end labels
        bus_h = _BUS_H + equip_extent + _BUS_LABEL_PAD
        elk_children.append({
            "id": gid,
            "width": bus_w,
            "height": bus_h,
            "properties": {
                "elementType": "bus",
                "elementId": gid,
                "voltageKv": meta["voltage_kv"],
                "color": meta["color"],
                "label": meta["name"],
                "parentNode": meta["parent_node"],
                "edgeCount": n_edges,
                "nMergedBuses": meta["n_buses"],
                "orientation": 0,  # horizontal bar
            },
        })

    # ── Emit aggregated inter-group edges ──
    # One ELK edge per (group_a, group_b, edge_type). Multiple parallel
    # circuits collapse into one tie-line annotated with N× — matching
    # the PowerFactory convention.
    elk_edges: list[dict] = []
    for (g_a, g_b, etype), agg in agg_edges.items():
        if etype == "transmission":
            color = get_voltage_color(agg["voltage"])
            label = (
                f"{agg['n']}× {agg['total_mw']:.0f} MW"
                if agg["n"] > 1
                else f"{agg['total_mw']:.0f} MW"
            )
        elif etype == "transformer":
            color = colors.get("transformer", "#9B59B6")
            label = (
                f"{agg['n']}× {agg['total_mw']:.0f} MVA"
                if agg["n"] > 1
                else f"{agg['total_mw']:.0f} MVA"
            )
        else:
            color = colors.get("acdc_converter", "#2980B9")
            label = f"{agg['total_mw']:.0f} MVA"
        elk_edges.append({
            "id": f"agg_{g_a}_{g_b}_{etype}",
            "sources": [g_a],
            "targets": [g_b],
            "properties": {
                "elementType": etype,
                "elementId": f"{g_a}_{g_b}_{etype}",
                "edgeType": etype,
                "voltageKv": agg["voltage"],
                "capacityMw": agg["total_mw"],
                "nCircuits": agg["n"],
                "color": color,
                "label": label,
            },
        })

    # ── Node groups (for visual background grouping) ──
    # Each visual rectangle wraps all merged bars belonging to one
    # geographic node (substation), regardless of voltage.
    node_to_groups: dict[int, list[str]] = {}
    for gid, meta in group_meta.items():
        node_to_groups.setdefault(meta["parent_node"], []).append(gid)
    groups: list[dict] = []
    for node in state.nodes:
        bus_ids = node_to_groups.get(node.index, [])
        if bus_ids:
            groups.append({
                "nodeId": node.index,
                "name": node.name or f"Node {node.index}",
                "busIds": bus_ids,
            })

    # ── PowerFactory-style deterministic layout ──
    # Replace the generic ELK layered algorithm (NP-hard, slow at >300
    # nodes) with an O(n) grid: rows = voltage levels (HV at top, LV at
    # bottom), columns = geographic nodes (left-to-right by node index).
    # Each bus sits at the (voltage row, node column) intersection.
    _apply_grid_layout(elk_children, elk_edges, state)
    elk_graph: dict[str, Any] = {
        "id": "root",
        "children": elk_children,
        "edges": elk_edges,
        # Tell the JS side to skip elk.layout() — positions are final.
        "precomputedLayout": True,
    }

    log.info(
        "SLD graph: %d buses, %d edges (%d lines input, %d transformers, "
        "%d acdc, %d freq converters)",
        len(elk_children), len(elk_edges),
        len(state.transmission_lines), len(state.transformers),
        len(state.acdc_converters), len(state.freq_converters),
    )
    for edge in elk_edges:
        p = edge.get("properties", {})
        log.info(
            "  edge %s: %s → %s  [%s, %s]",
            edge["id"], edge["sources"][0], edge["targets"][0],
            p.get("edgeType", "?"), p.get("label", ""),
        )

    return {
        "elkGraph": elk_graph,
        "busEquipment": bus_equipment,
        "nodeGroups": groups,
        "constants": {
            "busH": _BUS_H,
            "stubLen": _STUB_LEN,
            "equipSize": _EQUIP_SIZE,
            "equipSpacing": _EQUIP_SPACING,
        },
    }


def _apply_grid_layout(
    elk_children: list[dict],
    elk_edges: list[dict],
    state: GuiSystemState,
) -> None:
    """In-place: assign x/y/sections using a voltage-row × node-column grid.

    Buses are placed at the intersection of their voltage row (HV at the
    top, LV at the bottom) and their parent-node column (left-to-right
    by ``node.index``). Equipment columns hanging below each bus need
    vertical clearance — row spacing scales with the tallest bus per row.
    Edges get simple orthogonal routing with two bend points; the JS side
    then spreads incoming connections along each bar in Phase 2.
    """
    # ── 1. Group buses by (parent_node, voltage_kv) ──
    buses_by_node_volt: dict[tuple[int, float], list[dict]] = {}
    voltages: set[float] = set()
    nodes_seen: set[int] = set()
    for child in elk_children:
        props = child["properties"]
        v = float(props.get("voltageKv", 0.0) or 0.0)
        n = int(props.get("parentNode", 0) or 0)
        buses_by_node_volt.setdefault((n, v), []).append(child)
        voltages.add(v)
        nodes_seen.add(n)

    # Voltage rows — descending so HV is on top
    sorted_voltages = sorted(voltages, reverse=True)
    v_to_row = {v: i for i, v in enumerate(sorted_voltages)}

    # Node columns — preserve state.nodes order; append any orphaned IDs
    state_node_order = [n.index for n in state.nodes if n.index in nodes_seen]
    for n in nodes_seen:
        if n not in state_node_order:
            state_node_order.append(n)

    import math

    # ── 2. For each (node, voltage) cell with multiple buses, lay them
    #      out in an internal sub-grid (≈ sqrt(N) wide) so they don't
    #      stretch into a single huge horizontal stripe. We pre-compute
    #      each cell's bounding box.
    def _cell_layout(buses: list[dict]) -> tuple[float, float, list[tuple[float, float, dict]]]:
        """Return (cell_w, cell_h, [(dx, dy, bus_dict), ...])."""
        n = len(buses)
        if n == 0:
            return 0.0, 0.0, []
        # Square-ish sub-grid; cap at 8 cols so very crowded substations
        # still render in a reasonable aspect ratio.
        cols = min(max(1, int(math.ceil(math.sqrt(n)))), 8)
        rows = int(math.ceil(n / cols))
        # Per-row metrics
        sub_row_h: list[float] = []
        sub_row_w: list[float] = []
        for r in range(rows):
            chunk = buses[r * cols:(r + 1) * cols]
            sub_row_h.append(max(b["height"] for b in chunk))
            sub_row_w.append(
                sum(b["width"] for b in chunk)
                + _INTRA_NODE_GAP_X * (len(chunk) - 1)
            )
        cell_w = max(sub_row_w)
        cell_h = sum(sub_row_h) + _INTRA_NODE_GAP_X * (rows - 1)
        # Place each bus relative to (0, 0) at the cell's top-left
        placements: list[tuple[float, float, dict]] = []
        y_cursor = 0.0
        for r in range(rows):
            chunk = buses[r * cols:(r + 1) * cols]
            row_w = sub_row_w[r]
            x_cursor = (cell_w - row_w) / 2  # center each sub-row
            for b in chunk:
                placements.append((x_cursor, y_cursor, b))
                x_cursor += b["width"] + _INTRA_NODE_GAP_X
            y_cursor += sub_row_h[r] + _INTRA_NODE_GAP_X
        return cell_w, cell_h, placements

    # Build a cache of cell layouts keyed by (node_idx, voltage)
    cell_cache: dict[tuple[int, float], tuple[float, float, list]] = {}
    for key, buses in buses_by_node_volt.items():
        cell_cache[key] = _cell_layout(buses)

    # Per-node column width = max cell width across the node's voltages
    node_col_w: dict[int, float] = {}
    for node_idx in state_node_order:
        max_w = float(_BUS_LABEL_PAD + _BUS_H + 12)
        for v in sorted_voltages:
            cell = cell_cache.get((node_idx, v))
            if cell and cell[0] > max_w:
                max_w = cell[0]
        node_col_w[node_idx] = max_w

    # Per-row height = max cell height in that row
    row_h: dict[int, float] = {}
    for v in sorted_voltages:
        max_h = 0.0
        for node_idx in state_node_order:
            cell = cell_cache.get((node_idx, v))
            if cell and cell[1] > max_h:
                max_h = cell[1]
        row_h[v_to_row[v]] = max_h

    # ── 4. Compute X cursor per node column ──
    node_x_left: dict[int, float] = {}
    cursor_x = 0.0
    for node_idx in state_node_order:
        node_x_left[node_idx] = cursor_x
        cursor_x += node_col_w[node_idx] + _COL_GAP_X

    # ── 5a. Compute Y per voltage row with a placeholder gap height; we
    #       refine in 5b once we know how many lanes each gap needs (after
    #       interval-coloring the edges, which requires final bus X). ──
    row_y: dict[int, float] = {}
    cursor_y = 0.0
    sorted_v_list = list(sorted_voltages)
    for i, v in enumerate(sorted_v_list):
        ridx = v_to_row[v]
        row_y[ridx] = cursor_y
        if i < len(sorted_v_list) - 1:
            cursor_y += row_h[ridx] + _ROW_SPACING_Y
        else:
            cursor_y += row_h[ridx]

    # ── 6. Place each bus from its cell layout, centering the cell
    #      within the node column at the row's Y. ──
    for (node_idx, v), buses in buses_by_node_volt.items():
        col_left = node_x_left[node_idx]
        col_w = node_col_w[node_idx]
        cell_w, cell_h, placements = cell_cache[(node_idx, v)]
        y_top = row_y[v_to_row[v]]
        x_origin = col_left + (col_w - cell_w) / 2
        for dx, dy, b in placements:
            b["x"] = x_origin + dx
            b["y"] = y_top + dy

    # ── 7. Edge routing with per-edge lane assignment.
    #      Each inter-row edge gets its own horizontal lane Y in the
    #      gap between rows, assigned via greedy interval-coloring so
    #      edges with non-overlapping X-ranges share lanes (keeping the
    #      number of lanes low). Each edge exits its src bus's bottom
    #      face, drops to the lane, traverses horizontally, and climbs
    #      back up to the tgt bus's top face. ──
    bus_index = {c["id"]: c for c in elk_children}
    LANE_X_MARGIN = 16

    gap_edges: dict[tuple[int, int], list[tuple[float, float, dict]]] = {}
    same_row_edges: list[dict] = []
    for edge in elk_edges:
        src = bus_index.get(edge["sources"][0])
        tgt = bus_index.get(edge["targets"][0])
        if not src or not tgt:
            continue
        sv = float(src["properties"].get("voltageKv", 0.0) or 0.0)
        tv = float(tgt["properties"].get("voltageKv", 0.0) or 0.0)
        sx = src["x"] + src["width"] / 2
        tx = tgt["x"] + tgt["width"] / 2
        x_left = min(sx, tx)
        x_right = max(sx, tx)
        if sv == tv:
            same_row_edges.append(edge)
            continue
        r_lo = min(v_to_row[sv], v_to_row[tv])
        r_hi = max(v_to_row[sv], v_to_row[tv])
        gap_edges.setdefault((r_lo, r_hi), []).append((x_left, x_right, edge))

    # Greedy interval coloring per gap
    edge_lane_idx: dict[str, int] = {}
    gap_lane_count: dict[tuple[int, int], int] = {}
    for (r_lo, r_hi), intervals in gap_edges.items():
        intervals.sort(key=lambda t: t[0])
        lane_ends: list[float] = []
        for x_left, x_right, e in intervals:
            assigned = -1
            for i, end in enumerate(lane_ends):
                if end + LANE_X_MARGIN < x_left:
                    assigned = i
                    break
            if assigned == -1:
                assigned = len(lane_ends)
                lane_ends.append(x_right)
            else:
                lane_ends[assigned] = x_right
            edge_lane_idx[e["id"]] = assigned
        gap_lane_count[(r_lo, r_hi)] = len(lane_ends)

    # Resolve lane Y per gap
    edge_lane_y: dict[str, float] = {}
    for (r_lo, r_hi), n_lanes in gap_lane_count.items():
        gap_top = row_y[r_lo] + row_h[r_lo]
        gap_bottom = row_y[r_hi]
        usable_top = gap_top + _LANE_MARGIN_Y
        usable_h = max(_LANE_STEP_Y, gap_bottom - gap_top - 2 * _LANE_MARGIN_Y)
        for x_left, x_right, e in gap_edges[(r_lo, r_hi)]:
            idx = edge_lane_idx[e["id"]]
            t = (idx + 0.5) / n_lanes if n_lanes else 0.5
            edge_lane_y[e["id"]] = usable_top + t * usable_h

    # Same-row edges: dip below the row
    for edge in same_row_edges:
        src = bus_index[edge["sources"][0]]
        edge_lane_y[edge["id"]] = src["y"] + src["height"] + _LANE_MARGIN_Y

    # ── 8. Emit edge sections with explicit Z-shape bend points using
    #      the assigned lane Y. JS will render these directly without
    #      re-routing (precomputedRoute flag).
    #
    # Horizontal bars: edges exit/enter from TOP or BOTTOM of the bar.
    # Bar runs at y = bus.y + busH/2 across [bus.x, bus.x + bar_len]. ──
    for edge in elk_edges:
        src = bus_index.get(edge["sources"][0])
        tgt = bus_index.get(edge["targets"][0])
        if not src or not tgt:
            continue
        sx = src["x"] + src["width"] / 2
        tx = tgt["x"] + tgt["width"] / 2
        # Default: exit src bottom, enter tgt top (DOWN-direction layout).
        sy = src["y"] + _BUS_H
        ty = tgt["y"]
        # If src is below tgt, swap exit/entry direction.
        if src["y"] > tgt["y"]:
            sy = src["y"]
            ty = tgt["y"] + _BUS_H
        lane = edge_lane_y.get(edge["id"], (sy + ty) / 2)
        edge["properties"]["precomputedRoute"] = True
        edge["sections"] = [{
            "startPoint": {"x": sx, "y": sy},
            "endPoint": {"x": tx, "y": ty},
            "bendPoints": [
                {"x": sx, "y": lane},
                {"x": tx, "y": lane},
            ],
        }]
