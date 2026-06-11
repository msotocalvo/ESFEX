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
) -> dict:
    """Build the SLD layout descriptor from the current system state.

    The SLD is a single, electrically faithful schematic: **every** ``GuiBus``
    renders as its own bar and **every** transformer / line / converter is its
    own connection between its two real buses. There is no level aggregation —
    buses are never pooled and parallel-looking elements are never collapsed
    into an ``N×`` symbol, so the diagram always matches the actual topology
    (each transformer visibly bridges its specific HV and LV bus).

    Parameters
    ----------
    state : GuiSystemState
        The system to render.
    theme_colors : dict, optional
        Per-element-type color overrides.
    filter_substation : int, optional
        If provided, only buses with ``parent_node == filter_substation``
        are included.

    Returns
    -------
    dict
        ``{"elkGraph": ..., "busEquipment": ..., "nodeGroups": ...,
           "constants": ...}``
    """
    colors = theme_colors or {}

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

    # ── One bar per GuiBus (no merging) ──
    bus_to_group: dict[str, str] = {}
    group_to_buses: dict[str, list[str]] = {}
    group_meta: dict[str, dict] = {}
    _node_by_idx = {n.index: n for n in state.nodes}

    for bus in state.buses.values():
        if bus.bus_id not in included_buses:
            continue
        gid = f"bus_{bus.bus_id}"
        label = bus.name or bus.bus_id
        bus_to_group[bus.bus_id] = gid
        group_to_buses.setdefault(gid, []).append(bus.bus_id)
        group_meta[gid] = {
            "parent_node": bus.parent_node,
            "voltage_kv": bus.voltage_kv,
            "name": label,
            "color": get_voltage_color(bus.voltage_kv),
            "n_buses": 1,
        }

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

    # ── One edge per physical element (no aggregation) ──
    # Every transformer / line / converter is its own connection between its
    # two real buses, so the schematic stays electrically faithful (parallel
    # elements are NOT collapsed into an N× symbol).
    valid_bus_ids = included_buses

    edge_records: list[dict] = []
    edge_count: dict[str, int] = {gid: 0 for gid in group_meta}

    def _add_edge(src_gid: str, tgt_gid: str, etype: str,
                   voltage: float, capacity: float, element_id: str) -> None:
        if src_gid == tgt_gid:
            return  # both ends on the same bus → nothing to draw
        edge_records.append({
            "src": src_gid, "tgt": tgt_gid, "etype": etype,
            "voltage": float(voltage or 0), "capacity": float(capacity or 0),
            "element_id": str(element_id),
        })
        edge_count[src_gid] += 1
        edge_count[tgt_gid] += 1

    # ── Geographic connectivity: bus --line--> transformer --line--> bus ──
    # A transformer's two windings are wired to buses by the lines that
    # TERMINATE at it (from_endpoint/to_endpoint == that transformer). Those
    # lines are the transformer's stubs — consumed here, not drawn as separate
    # transmission — and the transformer bridges the buses on their far ends.
    # (A transformer's own from_bus/to_bus fields can disagree with the drawn
    # topology, so the lines — the geographic network — are the source of truth.)
    tr_terminals: dict[str, list[str]] = {}
    consumed_lines: set[str] = set()
    for line in state.transmission_lines:
        for ep, far_bus in ((line.from_endpoint, line.to_bus),
                            (line.to_endpoint, line.from_bus)):
            if (ep and ep.element_type == "transformer"
                    and far_bus in valid_bus_ids):
                tr_terminals.setdefault(str(ep.element_id), []).append(far_bus)
                consumed_lines.add(line.line_id)

    # Transmission lines: every line that is NOT a transformer stub, bus→bus.
    for line in state.transmission_lines:
        if line.line_id in consumed_lines:
            continue
        if (line.from_bus not in valid_bus_ids
                or line.to_bus not in valid_bus_ids
                or line.from_bus == line.to_bus):
            continue
        sg = bus_to_group.get(line.from_bus)
        tg = bus_to_group.get(line.to_bus)
        if not sg or not tg:
            continue
        _add_edge(
            sg, tg, "transmission",
            line.voltage_kv or 220.0, line.capacity_mw or 0,
            line.line_id,
        )

    # Transformers: bridge the two buses wired to it via its stub lines; fall
    # back to from_bus/to_bus when the geographic stubs aren't both available.
    for i, tr in enumerate(state.transformers):
        seen: list[str] = []
        for b in tr_terminals.get(str(i), []):
            if b not in seen:
                seen.append(b)
        fb, tb = (seen[0], seen[1]) if len(seen) >= 2 else (tr.from_bus, tr.to_bus)
        if (fb not in valid_bus_ids or tb not in valid_bus_ids or fb == tb):
            continue
        sg = bus_to_group.get(fb)
        tg = bus_to_group.get(tb)
        if not sg or not tg:
            continue
        if group_meta[sg]["parent_node"] != group_meta[tg]["parent_node"]:
            continue
        _add_edge(sg, tg, "transformer", 0.0, tr.rated_power_mva or 0, i)
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
        _add_edge(
            sg, tg, "converter",
            0.0, conv.rated_power_mva or 0,
            f"acdc_{i}",
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
        _add_edge(
            sg, tg, "converter",
            0.0, conv.rated_power_mva or 0,
            f"freq_{i}",
        )

    # ── Build flat ELK graph: one bar per bus ──
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

    # ── Emit one ELK edge per physical element ──
    elk_edges: list[dict] = []
    for rec in edge_records:
        etype = rec["etype"]
        cap = rec["capacity"]
        if etype == "transmission":
            color = get_voltage_color(rec["voltage"])
            label = f"{cap:.0f} MW"
        elif etype == "transformer":
            color = colors.get("transformer", "#9B59B6")
            label = f"{cap:.0f} MVA"
        else:
            color = colors.get("acdc_converter", "#2980B9")
            label = f"{cap:.0f} MVA"
        elk_edges.append({
            "id": f"{etype}_{rec['element_id']}_{rec['src']}_{rec['tgt']}",
            "sources": [rec["src"]],
            "targets": [rec["tgt"]],
            "properties": {
                "elementType": etype,
                "elementId": rec["element_id"],
                "edgeType": etype,
                "voltageKv": rec["voltage"],
                "capacityMw": cap,
                "nCircuits": 1,
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

    # Same-row (one-voltage) edges connect different node columns; colour
    # them into lanes too so parallel circuits at the same voltage don't all
    # stack on a single Y line (the previous behaviour).
    same_row_by_row: dict[int, list[tuple[float, float, dict]]] = {}
    for edge in same_row_edges:
        src = bus_index[edge["sources"][0]]
        tgt = bus_index[edge["targets"][0]]
        sx = src["x"] + src["width"] / 2
        tx = tgt["x"] + tgt["width"] / 2
        sv = float(src["properties"].get("voltageKv", 0.0) or 0.0)
        same_row_by_row.setdefault(v_to_row[sv], []).append(
            (min(sx, tx), max(sx, tx), edge))
    same_row_lane_idx: dict[str, int] = {}
    same_row_lane_count: dict[int, int] = {}
    for i, intervals in same_row_by_row.items():
        intervals.sort(key=lambda t: t[0])
        sr_ends: list[float] = []
        for x_left, x_right, e in intervals:
            assigned = -1
            for k, end in enumerate(sr_ends):
                if end + LANE_X_MARGIN < x_left:
                    assigned = k
                    break
            if assigned == -1:
                assigned = len(sr_ends)
                sr_ends.append(x_right)
            else:
                sr_ends[assigned] = x_right
            same_row_lane_idx[e["id"]] = assigned
        same_row_lane_count[i] = len(sr_ends)

    # ── 5b. Adaptive row spacing (the refinement promised in step 5a).
    #       Grow each inter-row gap to fit BOTH the cross-row lanes passing
    #       through it and the same-row lanes that dip below the upper row,
    #       so edges stop cramming on one another (and on intermediate
    #       bars). Sparse gaps keep the compact default. Row Y is then
    #       recomputed and every bus re-placed at its new row. ──
    n_rows = len(sorted_v_list)
    cross_need = [0] * n_rows                 # cross-row lanes through gap below row i
    for (r_lo, r_hi), n_lanes in gap_lane_count.items():
        for i in range(r_lo, r_hi):           # edge spans every consecutive gap
            cross_need[i] = max(cross_need[i], n_lanes)
    gap_need = [cross_need[i] + same_row_lane_count.get(i, 0)
                for i in range(n_rows)]

    cursor_y = 0.0
    for i, v in enumerate(sorted_v_list):
        ridx = v_to_row[v]
        row_y[ridx] = cursor_y
        if i < n_rows - 1:
            gap_h = max(_ROW_SPACING_Y,
                        gap_need[ridx] * _LANE_STEP_Y + 2 * _LANE_MARGIN_Y)
            cursor_y += row_h[ridx] + gap_h
        else:
            cursor_y += row_h[ridx]

    for (node_idx, v), buses in buses_by_node_volt.items():
        _cw, _ch, placements = cell_cache[(node_idx, v)]
        y_top = row_y[v_to_row[v]]
        for _dx, dy, b in placements:
            b["y"] = y_top + dy

    # Resolve lane Y per gap. Cross-row lanes are spread across the gap but
    # start BELOW the upper row's same-row band so the two lane families
    # don't overlap.
    edge_lane_y: dict[str, float] = {}
    for (r_lo, r_hi), n_lanes in gap_lane_count.items():
        gap_top = row_y[r_lo] + row_h[r_lo]
        gap_bottom = row_y[r_hi]
        sr_band = same_row_lane_count.get(r_lo, 0) * _LANE_STEP_Y
        usable_top = gap_top + _LANE_MARGIN_Y + sr_band
        usable_h = max(_LANE_STEP_Y,
                       gap_bottom - usable_top - _LANE_MARGIN_Y)
        for x_left, x_right, e in gap_edges[(r_lo, r_hi)]:
            idx = edge_lane_idx[e["id"]]
            t = (idx + 0.5) / n_lanes if n_lanes else 0.5
            edge_lane_y[e["id"]] = usable_top + t * usable_h

    # Same-row edges: own lane band just below their row (one Y per lane).
    for i, intervals in same_row_by_row.items():
        base = row_y[i] + row_h[i] + _LANE_MARGIN_Y
        for x_left, x_right, e in intervals:
            edge_lane_y[e["id"]] = base + same_row_lane_idx[e["id"]] * _LANE_STEP_Y

    # ── 8. Emit edge sections with explicit Z-shape bend points using
    #      the assigned lane Y. JS will render these directly without
    #      re-routing (precomputedRoute flag).
    #
    # Horizontal bars: edges exit/enter from TOP or BOTTOM of the bar.
    # Bar runs at y = bus.y + busH/2 across [bus.x, bus.x + bar_len]. ──

    # ── Terminal slots: spread each bus's connections ALONG its bar instead
    #    of stacking them all at the centre. Terminals are ordered by the
    #    other endpoint's centre X, so left-going connections attach on the
    #    left of the bar and right-going on the right (fewer crossings). ──
    _TERM_PAD = 24
    bus_center_x = {gid: c["x"] + c["width"] / 2 for gid, c in bus_index.items()}
    bus_edge_list: dict[str, list[tuple[str, str]]] = {gid: [] for gid in bus_index}
    for _e in elk_edges:
        s0, t0 = _e["sources"][0], _e["targets"][0]
        if s0 in bus_edge_list:
            bus_edge_list[s0].append((_e["id"], t0))
        if t0 in bus_edge_list:
            bus_edge_list[t0].append((_e["id"], s0))
    term_x: dict[tuple[str, str], float] = {}
    for gid, lst in bus_edge_list.items():
        c = bus_index[gid]
        lst.sort(key=lambda e: bus_center_x.get(e[1], c["x"]))
        n = len(lst)
        x0 = c["x"] + _TERM_PAD
        x1 = c["x"] + c["width"] - _TERM_PAD
        if x1 <= x0:
            x0, x1 = c["x"], c["x"] + c["width"]
        for i, (eid, _other) in enumerate(lst):
            frac = (i + 0.5) / n if n else 0.5
            term_x[(gid, eid)] = x0 + frac * (x1 - x0)

    for edge in elk_edges:
        src = bus_index.get(edge["sources"][0])
        tgt = bus_index.get(edge["targets"][0])
        if not src or not tgt:
            continue
        sx = term_x.get((edge["sources"][0], edge["id"]),
                        src["x"] + src["width"] / 2)
        tx = term_x.get((edge["targets"][0], edge["id"]),
                        tgt["x"] + tgt["width"] / 2)
        # Default: exit src bottom, enter tgt top (DOWN-direction layout).
        sy = src["y"] + _BUS_H
        ty = tgt["y"]
        if src["y"] > tgt["y"]:
            # src is below tgt → swap exit/entry direction.
            sy = src["y"]
            ty = tgt["y"] + _BUS_H
        elif src["y"] == tgt["y"]:
            # Same row → both exit the bottom face and dip to the lane
            # below as a clean U (no segment crossing a bar).
            ty = tgt["y"] + _BUS_H

        sv = float(src["properties"].get("voltageKv", 0.0) or 0.0)
        tv = float(tgt["properties"].get("voltageKv", 0.0) or 0.0)
        rows_apart = abs(v_to_row.get(sv, 0) - v_to_row.get(tv, 0))

        edge["properties"]["precomputedRoute"] = True

        is_xfmr = edge["properties"].get("edgeType") == "transformer"
        if is_xfmr and rows_apart == 1:
            # Transformer between ADJACENT voltage levels: clean vertical
            # connection at one shared X inside the two bars' horizontal
            # overlap — exit the upper bar's bottom, enter the lower bar's top.
            # The symbol sits between the bars, always vertical (the JS side
            # draws stubs + windings, so no line crosses the symbol).
            # Multi-row transformers (non-adjacent levels) fall through to the
            # side-channel route below so they don't pierce intermediate bars.
            upper, lower = (src, tgt) if src["y"] < tgt["y"] else (tgt, src)
            upper_gid = (edge["sources"][0] if upper is src
                         else edge["targets"][0])
            ox0 = max(upper["x"], lower["x"])
            ox1 = min(upper["x"] + upper["width"], lower["x"] + lower["width"])
            # Use this transformer's own terminal slot (distinct per parallel
            # transformer) clamped into the bars' overlap, so multiple
            # transformers between the same two bars don't stack on one line.
            cx = term_x.get((upper_gid, edge["id"]),
                            upper["x"] + upper["width"] / 2)
            if ox1 > ox0:
                cx = min(max(cx, ox0), ox1)
            edge["properties"]["transformerVertical"] = True
            edge["sections"] = [{
                "startPoint": {"x": cx, "y": upper["y"] + _BUS_H},
                "endPoint": {"x": cx, "y": lower["y"]},
                "bendPoints": [],
            }]
            continue

        if rows_apart >= 2:
            # Multi-row edge: a straight vertical drop at sx/tx would pierce
            # the bars of the rows in between. Route the long vertical run in
            # a column GAP (between node columns), which is guaranteed clear
            # of any bar, then step horizontally in to each endpoint.
            src_node = int(src["properties"].get("parentNode", 0) or 0)
            if tx >= sx:
                channel_x = (node_x_left.get(src_node, sx)
                             + node_col_w.get(src_node, 0.0) + _COL_GAP_X / 2)
            else:
                channel_x = node_x_left.get(src_node, sx) - _COL_GAP_X / 2
            down = sy < ty
            y_src = sy + _LANE_MARGIN_Y if down else sy - _LANE_MARGIN_Y
            y_tgt = ty - _LANE_MARGIN_Y if down else ty + _LANE_MARGIN_Y
            edge["sections"] = [{
                "startPoint": {"x": sx, "y": sy},
                "endPoint": {"x": tx, "y": ty},
                "bendPoints": [
                    {"x": sx, "y": y_src},
                    {"x": channel_x, "y": y_src},
                    {"x": channel_x, "y": y_tgt},
                    {"x": tx, "y": y_tgt},
                ],
            }]
        else:
            lane = edge_lane_y.get(edge["id"], (sy + ty) / 2)
            edge["sections"] = [{
                "startPoint": {"x": sx, "y": sy},
                "endPoint": {"x": tx, "y": ty},
                "bendPoints": [
                    {"x": sx, "y": lane},
                    {"x": tx, "y": lane},
                ],
            }]
