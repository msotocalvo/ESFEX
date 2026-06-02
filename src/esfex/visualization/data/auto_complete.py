"""Auto-complete network: connect disconnected equipment to the main grid.

Single-bus equipment (generators, batteries, electrolyzers):

    equip ---line1--- LV_bus ---line2--- TR ---line3--- existing_HV_bus

Creates: 1 bus (LV), 1 transformer, 3 lines.

Two-bus elements (AC/DC converters, frequency converters):

    bus_from ---line1--- converter ---line2--- bus_to

Creates: 2 connection lines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import GuiModel, GuiSystemState

# ── Constants ──────────────────────────────────────────────────────

SAFETY_FACTOR = 1.2
DEFAULT_LV_KV = 0.48
DEFAULT_CAPACITY_MW = 1.0
SPACING_DEG = 0.001  # ~110 m between pattern elements (legacy)
# Proportional placement along equip→HV axis
LV_FRACTION = 0.25   # LV bus at 25 % of the way from equipment to HV
TR_FRACTION = 0.65   # Transformer at 65 % of the way from equipment to HV
MIN_CHAIN_SPREAD = 0.003  # ~330 m minimum spread so elements stay distinct


# ── Dataclass ──────────────────────────────────────────────────────

@dataclass
class ConnectionPlan:
    isolated_bus_id: str
    target_bus_id: str
    distance_km: float
    equipment_summary: str
    total_capacity_mw: float
    transformer_capacity_mva: float
    transformer_hv_kv: float
    transformer_lv_kv: float
    line_capacity_mw: float
    reason: str = ""
    # Positions along the pattern (gen → LV → TR → HV)
    equip_lat: float = 0.0
    equip_lng: float = 0.0
    lv_lat: float = 0.0
    lv_lng: float = 0.0
    tr_lat: float = 0.0
    tr_lng: float = 0.0
    hv_lat: float = 0.0
    hv_lng: float = 0.0
    selected: bool = True
    equipment_ids: list[str] = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _direction(from_lat, from_lng, to_lat, to_lng):
    """Unit direction vector from → to (as dy, dx in degrees)."""
    dy = to_lat - from_lat
    dx = to_lng - from_lng
    d = math.sqrt(dx * dx + dy * dy) or 1e-9
    return dy / d, dx / d


# ── Equipment detection ───────────────────────────────────────────

@dataclass
class _Equip:
    etype: str
    eid: str
    bus_id: str
    node: int
    lat: float
    lng: float
    rated_mw: float
    name: str


def _find_disconnected(state: GuiSystemState) -> list[_Equip]:
    buses_with_tr: set[str] = set()
    for tr in state.transformers:
        buses_with_tr.add(tr.from_bus)
        buses_with_tr.add(tr.to_bus)
    for c in state.acdc_converters:
        buses_with_tr.add(c.from_bus)
        buses_with_tr.add(c.to_bus)
    for c in state.freq_converters:
        buses_with_tr.add(c.from_bus)
        buses_with_tr.add(c.to_bus)

    result: list[_Equip] = []
    for gid, g in state.generators.items():
        if g.bus not in buses_with_tr:
            result.append(_Equip("generator", gid, g.bus, g.node,
                                 g.latitude, g.longitude, g.rated_power, g.name))
    for bid, b in state.batteries.items():
        if b.bus not in buses_with_tr:
            result.append(_Equip("battery", bid, b.bus, b.node,
                                 b.latitude, b.longitude, b.rated_power, b.name))
    for eid, e in state.electrolyzers.items():
        if e.bus not in buses_with_tr:
            result.append(_Equip("electrolyzer", eid, e.bus, e.node,
                                 e.latitude, e.longitude, e.rated_power, e.name))
    return result


# ── Public API ─────────────────────────────────────────────────────

def plan_auto_complete(state: GuiSystemState) -> list[ConnectionPlan]:
    disconnected = _find_disconnected(state)
    if not disconnected:
        return []

    # Group by bus
    bus_groups: dict[str, list[_Equip]] = {}
    for eq in disconnected:
        bus_groups.setdefault(eq.bus_id, []).append(eq)

    plans: list[ConnectionPlan] = []

    for bus_id, group in bus_groups.items():
        bus = state.buses.get(bus_id)
        if not bus or (bus.latitude == 0.0 and bus.longitude == 0.0):
            continue

        eq_lat, eq_lng = bus.latitude, bus.longitude

        # If the equipment's own bus qualifies as HV, use it directly as
        # the target.  The chain will interpose a new LV bus + transformer
        # between the equipment and this bus, then move the equipment to
        # the LV bus.  Only search for a different bus when the equipment's
        # bus is itself low-voltage.
        if bus.voltage_kv > DEFAULT_LV_KV:
            best_id = bus_id
            best_dist = 0.0
        else:
            best_id, best_dist = _find_nearest_hv_bus(
                state, eq_lat, eq_lng,
            )
        if best_id is None:
            continue

        tgt = state.buses[best_id]

        # Direction from equipment toward HV bus (default east when same spot)
        if eq_lat == tgt.latitude and eq_lng == tgt.longitude:
            uy, ux = 0.0, 1.0
        else:
            uy, ux = _direction(eq_lat, eq_lng, tgt.latitude, tgt.longitude)

        dist_deg = math.sqrt(
            (tgt.latitude - eq_lat) ** 2 + (tgt.longitude - eq_lng) ** 2
        )

        # Place LV and TR along the equip→HV axis.
        # When far enough apart, use proportional fractions so
        #   equip --- LV (25%) --- TR (65%) --- HV (100%)
        # Otherwise fall back to fixed minimum spacing.
        if dist_deg >= 3 * MIN_CHAIN_SPREAD:
            lv_lat = eq_lat + (tgt.latitude - eq_lat) * LV_FRACTION
            lv_lng = eq_lng + (tgt.longitude - eq_lng) * LV_FRACTION
            tr_lat = eq_lat + (tgt.latitude - eq_lat) * TR_FRACTION
            tr_lng = eq_lng + (tgt.longitude - eq_lng) * TR_FRACTION
        else:
            spacing = MIN_CHAIN_SPREAD
            lv_lat = eq_lat + uy * spacing
            lv_lng = eq_lng + ux * spacing
            tr_lat = eq_lat + uy * 2 * spacing
            tr_lng = eq_lng + ux * 2 * spacing

        # Sizing
        total_mw = sum(e.rated_mw for e in group)
        if total_mw <= 0:
            total_mw = DEFAULT_CAPACITY_MW
        tr_cap = total_mw * SAFETY_FACTOR

        # Summary
        gen_n = sum(1 for e in group if e.etype == "generator")
        bat_n = sum(1 for e in group if e.etype == "battery")
        ely_n = sum(1 for e in group if e.etype == "electrolyzer")
        parts: list[str] = []
        if gen_n:
            mw = sum(e.rated_mw for e in group if e.etype == "generator")
            parts.append(f"{gen_n} gen ({mw:.0f} MW)")
        if bat_n:
            mw = sum(e.rated_mw for e in group if e.etype == "battery")
            parts.append(f"{bat_n} bat ({mw:.0f} MW)")
        if ely_n:
            parts.append(f"{ely_n} elec")
        summary = ", ".join(parts) or "equipment"

        plans.append(ConnectionPlan(
            isolated_bus_id=bus_id,
            target_bus_id=best_id,
            distance_km=best_dist,
            equipment_summary=summary,
            total_capacity_mw=total_mw,
            transformer_capacity_mva=tr_cap,
            transformer_hv_kv=tgt.voltage_kv,
            transformer_lv_kv=DEFAULT_LV_KV,
            line_capacity_mw=tr_cap,
            reason="no_transformer",
            equip_lat=eq_lat,
            equip_lng=eq_lng,
            lv_lat=lv_lat,
            lv_lng=lv_lng,
            tr_lat=tr_lat,
            tr_lng=tr_lng,
            hv_lat=tgt.latitude,
            hv_lng=tgt.longitude,
            equipment_ids=[e.eid for e in group],
        ))

    plans.sort(key=lambda p: p.distance_km)
    return plans


def verify_connection_chain(
    state: GuiSystemState,
    equipment_ids: list[str],
    lv_bus_id: str,
    tr_idx: int,
    target_bus_id: str,
) -> tuple[bool, str]:
    """Verify end-to-end connectivity: equipment → line → bus_lv → line → TR → line → bus_hv.

    Returns ``(True, "")`` if the chain is complete, or ``(False, reason)``
    describing the first broken link.
    """
    # 1. Check LV bus exists
    if lv_bus_id not in state.buses:
        return False, f"LV bus {lv_bus_id} not found"

    # 2. Check transformer exists and connects lv→hv
    if tr_idx >= len(state.transformers):
        return False, f"Transformer index {tr_idx} out of range"
    tr = state.transformers[tr_idx]
    if tr.from_bus != lv_bus_id:
        return False, f"Transformer from_bus={tr.from_bus}, expected {lv_bus_id}"
    if tr.to_bus != target_bus_id:
        return False, f"Transformer to_bus={tr.to_bus}, expected {target_bus_id}"

    # 3. Check target HV bus exists
    if target_bus_id not in state.buses:
        return False, f"HV bus {target_bus_id} not found"

    # Helper: find lines by endpoint type+id
    def _has_line(from_type: str, from_id: str, to_type: str, to_id: str) -> bool:
        for ln in state.transmission_lines:
            f_ok = (ln.from_endpoint
                    and ln.from_endpoint.element_type == from_type
                    and ln.from_endpoint.element_id == from_id)
            t_ok = (ln.to_endpoint
                    and ln.to_endpoint.element_type == to_type
                    and ln.to_endpoint.element_id == to_id)
            if f_ok and t_ok:
                return True
        return False

    tr_id_str = str(tr_idx)

    # 4. For each equipment, check line: equipment → bus_lv
    for eid in equipment_ids:
        etype: str | None = None
        if eid in state.generators:
            etype = "generator"
        elif eid in state.batteries:
            etype = "battery"
        elif eid in state.electrolyzers:
            etype = "electrolyzer"
        else:
            # Check converters (indexed by position, eid is str(index))
            try:
                idx = int(eid)
                if idx < len(state.acdc_converters):
                    etype = "acdc_converter"
                elif idx < len(state.freq_converters):
                    etype = "freq_converter"
            except (ValueError, TypeError):
                pass
        if etype is None:
            return False, f"Equipment {eid} not found in state"
        if not _has_line(etype, eid, "bus", lv_bus_id):
            return False, f"No line from {etype}:{eid} to bus:{lv_bus_id}"

    # 5. Check line: bus_lv → transformer
    if not _has_line("bus", lv_bus_id, "transformer", tr_id_str):
        return False, f"No line from bus:{lv_bus_id} to transformer:{tr_id_str}"

    # 6. Check line: transformer → bus_hv
    if not _has_line("transformer", tr_id_str, "bus", target_bus_id):
        return False, f"No line from transformer:{tr_id_str} to bus:{target_bus_id}"

    return True, ""


def _find_nearest_hv_bus(
    state: GuiSystemState,
    equip_lat: float,
    equip_lng: float,
    exclude_buses: set[str] | None = None,
) -> tuple[str | None, float]:
    """Find the nearest bus suitable as the HV end of a transformer.

    A bus qualifies if:
    - It has valid geographic coordinates (not 0, 0)
    - Its voltage is **strictly greater** than ``DEFAULT_LV_KV`` (so that
      auto-created 0.48 kV LV buses are never chosen as HV targets)
    - It is not in *exclude_buses*

    Returns ``(bus_id, distance_km)`` or ``(None, inf)`` if none found.
    """
    if exclude_buses is None:
        exclude_buses = set()
    best_id: str | None = None
    best_dist = float("inf")
    for bid, b in state.buses.items():
        if bid in exclude_buses:
            continue
        if b.latitude == 0.0 and b.longitude == 0.0:
            continue
        # Strictly greater: skip auto-created LV buses (0.48 kV)
        if b.voltage_kv <= DEFAULT_LV_KV:
            continue
        d = _haversine_km(equip_lat, equip_lng, b.latitude, b.longitude)
        if d < best_dist:
            best_dist = d
            best_id = bid
    return best_id, best_dist


def auto_connect_single_equipment(
    model: GuiModel,
    etype: str,
    eid: str,
    equip_lat: float,
    equip_lng: float,
) -> bool:
    """Create the full connection chain for one newly-placed equipment.

    For **single-bus equipment** (generator, battery, electrolyzer):

        1:equipment → 2:line → 3:bus_lv → 4:line → 5:transformer → 6:line → 7:bus_hv

    For **two-bus elements** (acdc_converter, freq_converter):

        bus_from → line → converter → line → bus_to

    Returns ``True`` if the chain was created and verified, ``False`` on
    failure (no HV bus found, etc.).
    """
    # Dispatch to converter-specific logic for two-bus elements
    if etype in ("acdc_converter", "freq_converter"):
        return _auto_connect_single_converter(model, etype, eid)

    from esfex.visualization.data.gui_model import EndpointRef

    state = model.state

    # Resolve equipment object
    if etype == "generator":
        obj = state.generators.get(eid)
    elif etype == "battery":
        obj = state.batteries.get(eid)
    elif etype == "electrolyzer":
        obj = state.electrolyzers.get(eid)
    else:
        return False
    if obj is None:
        return False

    equip_node = obj.node
    rated_mw = getattr(obj, "rated_power", 0.0) or DEFAULT_CAPACITY_MW

    # Find the nearest HV bus (> 0.48 kV).  Do NOT exclude the
    # equipment's default bus — it is the correct HV target.  The chain
    # creates a new LV bus + transformer between the equipment and that
    # bus, then moves the equipment to the new LV bus.
    target_id, dist_km = _find_nearest_hv_bus(
        state, equip_lat, equip_lng,
    )
    if target_id is None:
        return False

    tgt = state.buses[target_id]

    # Direction from equipment toward HV bus (or small offset if same spot)
    if equip_lat == tgt.latitude and equip_lng == tgt.longitude:
        uy, ux = 0.0, 1.0  # default: east
    else:
        uy, ux = _direction(equip_lat, equip_lng, tgt.latitude, tgt.longitude)

    dist_deg = math.sqrt(
        (tgt.latitude - equip_lat) ** 2 + (tgt.longitude - equip_lng) ** 2
    )

    # Place LV and TR along the equip→HV axis.
    # Proportional fractions ensure visual ordering:
    #   equip --- LV (25%) --- TR (65%) --- HV (100%)
    if dist_deg >= 3 * MIN_CHAIN_SPREAD:
        lv_lat = equip_lat + (tgt.latitude - equip_lat) * LV_FRACTION
        lv_lng = equip_lng + (tgt.longitude - equip_lng) * LV_FRACTION
        tr_lat = equip_lat + (tgt.latitude - equip_lat) * TR_FRACTION
        tr_lng = equip_lng + (tgt.longitude - equip_lng) * TR_FRACTION
    else:
        spacing = MIN_CHAIN_SPREAD
        lv_lat = equip_lat + uy * spacing
        lv_lng = equip_lng + ux * spacing
        tr_lat = equip_lat + uy * 2 * spacing
        tr_lng = equip_lng + ux * 2 * spacing

    tr_cap = rated_mw * SAFETY_FACTOR

    try:
        # ── 3. bus_lv ──
        new_lv = model.add_bus(
            parent_node=equip_node,
            name=f"LV ({getattr(obj, 'name', eid)[:25]})",
            voltage_kv=DEFAULT_LV_KV,
            latitude=lv_lat,
            longitude=lv_lng,
        )

        # ── 5. transformer ──
        tr_idx = model.add_transformer(
            name=f"TR {new_lv}\u2192{target_id}",
            from_bus=new_lv,
            to_bus=target_id,
            from_voltage_kv=DEFAULT_LV_KV,
            to_voltage_kv=tgt.voltage_kv,
            rated_power_mva=tr_cap,
            latitude=tr_lat,
            longitude=tr_lng,
        )

        # ── 2. line: equipment → bus_lv ──
        model.add_line(
            from_bus=new_lv, to_bus=new_lv,
            capacity_mw=rated_mw,
            from_endpoint=EndpointRef(etype, eid),
            to_endpoint=EndpointRef("bus", new_lv),
        )

        # ── 4. line: bus_lv → transformer ──
        model.add_line(
            from_bus=new_lv, to_bus=new_lv,
            capacity_mw=tr_cap,
            from_endpoint=EndpointRef("bus", new_lv),
            to_endpoint=EndpointRef("transformer", str(tr_idx)),
        )

        # ── 6. line: transformer → bus_hv ──
        model.add_line(
            from_bus=target_id, to_bus=target_id,
            capacity_mw=tr_cap,
            from_endpoint=EndpointRef("transformer", str(tr_idx)),
            to_endpoint=EndpointRef("bus", target_id),
        )

        # ── Move equipment to LV bus ──
        obj.bus = new_lv

        # ── Verify ──
        ok, _reason = verify_connection_chain(
            state, [eid], new_lv, tr_idx, target_id,
        )
        return ok

    except Exception:
        return False


def _auto_connect_single_converter(
    model: GuiModel,
    conv_type: str,
    conv_id: str,
) -> bool:
    """Create connection lines for a newly-placed converter.

    Pattern::

        bus:from_bus → line → converter → line → bus:to_bus

    Returns ``True`` if both lines were created successfully.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    state = model.state

    # Resolve converter object and its index
    if conv_type == "acdc_converter":
        conv_list = state.acdc_converters
    elif conv_type == "freq_converter":
        conv_list = state.freq_converters
    else:
        return False

    # Find by index (conv_id is the string index)
    try:
        conv_idx = int(conv_id)
    except (ValueError, TypeError):
        return False
    if conv_idx < 0 or conv_idx >= len(conv_list):
        return False

    conv = conv_list[conv_idx]
    conv_id_str = str(conv_idx)

    # Verify both buses exist
    if conv.from_bus not in state.buses or conv.to_bus not in state.buses:
        return False

    cap = conv.rated_power_mva or DEFAULT_CAPACITY_MW

    try:
        # Line 1: bus:from_bus → converter
        model.add_line(
            from_bus=conv.from_bus, to_bus=conv.from_bus,
            capacity_mw=cap,
            from_endpoint=EndpointRef("bus", conv.from_bus),
            to_endpoint=EndpointRef(conv_type, conv_id_str),
        )

        # Line 2: converter → bus:to_bus
        model.add_line(
            from_bus=conv.to_bus, to_bus=conv.to_bus,
            capacity_mw=cap,
            from_endpoint=EndpointRef(conv_type, conv_id_str),
            to_endpoint=EndpointRef("bus", conv.to_bus),
        )

        return True

    except Exception:
        return False


def apply_auto_complete(
    model: GuiModel,
    plans: list[ConnectionPlan],
) -> int:
    """Apply the fixed pattern per plan:

    gen ---line1--- LV_bus ---line2--- TR ---line3--- existing_HV_bus

    Elements 2–6 (line1, LV_bus, line2, transformer, line3) are created
    automatically.  Equipment is reassigned to the LV bus only after all
    intermediate elements are successfully created.
    """
    from esfex.visualization.data.gui_model import EndpointRef

    count = 0
    for plan in plans:
        if not plan.selected:
            continue
        if plan.target_bus_id not in model.state.buses:
            continue

        # Equipment node
        equip_node = 0
        for eid in plan.equipment_ids:
            obj = (model.state.generators.get(eid)
                   or model.state.batteries.get(eid)
                   or model.state.electrolyzers.get(eid))
            if obj:
                equip_node = obj.node
                break
            # Check converters (indexed by position)
            try:
                idx = int(eid)
                if idx < len(model.state.acdc_converters):
                    equip_node = 0  # converters don't have a node field
                    break
                if idx < len(model.state.freq_converters):
                    equip_node = 0
                    break
            except (ValueError, TypeError):
                pass

        try:
            # ── 1. Create LV bus ──
            new_lv = model.add_bus(
                parent_node=equip_node,
                name=f"Auto LV ({plan.equipment_summary[:25]})",
                voltage_kv=plan.transformer_lv_kv,
                latitude=plan.lv_lat,
                longitude=plan.lv_lng,
            )

            # ── 2. Create transformer (LV bus → existing HV bus) ──
            tr_idx = model.add_transformer(
                name=f"TR {new_lv}\u2192{plan.target_bus_id}",
                from_bus=new_lv,
                to_bus=plan.target_bus_id,
                from_voltage_kv=plan.transformer_lv_kv,
                to_voltage_kv=plan.transformer_hv_kv,
                rated_power_mva=plan.transformer_capacity_mva,
                latitude=plan.tr_lat,
                longitude=plan.tr_lng,
            )

            # ── 3. Three lines (created BEFORE moving equipment) ──

            # Line 1: equipment → LV bus
            for eid in plan.equipment_ids:
                if eid in model.state.generators:
                    etype = "generator"
                    eq = model.state.generators[eid]
                elif eid in model.state.batteries:
                    etype = "battery"
                    eq = model.state.batteries[eid]
                elif eid in model.state.electrolyzers:
                    etype = "electrolyzer"
                    eq = model.state.electrolyzers[eid]
                else:
                    continue
                if eq.latitude or eq.longitude:
                    model.add_line(
                        from_bus=new_lv, to_bus=new_lv,
                        capacity_mw=eq.rated_power or DEFAULT_CAPACITY_MW,
                        from_endpoint=EndpointRef(etype, eid),
                        to_endpoint=EndpointRef("bus", new_lv),
                    )

            # Line 2: LV bus → transformer
            model.add_line(
                from_bus=new_lv, to_bus=new_lv,
                capacity_mw=plan.line_capacity_mw,
                from_endpoint=EndpointRef("bus", new_lv),
                to_endpoint=EndpointRef("transformer", str(tr_idx)),
            )

            # Line 3: transformer → existing HV bus
            model.add_line(
                from_bus=plan.target_bus_id, to_bus=plan.target_bus_id,
                capacity_mw=plan.line_capacity_mw,
                from_endpoint=EndpointRef("transformer", str(tr_idx)),
                to_endpoint=EndpointRef("bus", plan.target_bus_id),
            )

            # ── 4. Move equipment to LV bus (after all elements created) ──
            for eid in plan.equipment_ids:
                if eid in model.state.generators:
                    model.state.generators[eid].bus = new_lv
                elif eid in model.state.batteries:
                    model.state.batteries[eid].bus = new_lv
                elif eid in model.state.electrolyzers:
                    model.state.electrolyzers[eid].bus = new_lv

            # ── 5. Verify the complete chain ──
            verify_connection_chain(
                model.state, plan.equipment_ids, new_lv,
                tr_idx, plan.target_bus_id,
            )

        except Exception:
            continue

        count += 1

    return count
