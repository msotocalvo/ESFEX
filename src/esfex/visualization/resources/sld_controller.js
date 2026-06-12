/**
 * sld_controller.js — Single-Line Diagram rendering engine (v4).
 *
 * Professional PowerFactory / ETAP style with:
 *   - DOWN direction (HV at top, LV at bottom)
 *   - Thick-line bus bars with end caps
 *   - Smart edge-to-bus face snapping (geometric, not hardcoded)
 *   - Rounded orthogonal edge corners
 *   - Solid node-group backgrounds with subtle shadows
 *   - Infinite dot-grid canvas (never floats)
 *
 * Two-pass rendering:
 *   1. ELK positions **buses** (flat graph, buses = nodes, lines = edges).
 *   2. JS manually draws equipment rows below each bus bar with vertical
 *      stubs — classic PowerFactory / ETAP style.
 *
 * Input JSON from Python:
 *   {
 *     "elkGraph":      { ... flat ELK graph ... },
 *     "busEquipment":  { "bus_0": [ {equipment}, ... ], ... },
 *     "nodeGroups":    [ { nodeId, name, busIds }, ... ],
 *     "constants":     { busH, stubLen, equipSize, equipSpacing }
 *   }
 */

// ══════════════════════════════════════════════════════════════════
// Global state
// ══════════════════════════════════════════════════════════════════

var sldBridge = null;
var svg = null;
var rootGroup = null;
var elk = null;
var currentData = null;
var currentLayouted = null;
var selectedKey = null;
var labelsVisible = true;
var _pendingLabels = [];       // edge labels drawn in a final pass (avoid all)
var zoomBehavior = null;
var diagramBounds = null;     // { x, y, w, h } after layout
var currentOpsData = null;    // latest operational snapshot (parsed JSON)
var _legendBounds = null;     // { x, y, w, h } of last drawn legend

// Element registry: "type:id" → { group, props }
var elementRegistry = {};
// Bus layout cache: bus_id → { x, y, w, h, color, ... }
var busLayout = {};

// Theme — professional palette
var T = {
    bg:             '#FFFFFF',
    groupBg:        '#F0F3F7',
    groupBorder:    '#C5CFD8',
    groupTitle:     '#4A5568',
    stubLine:       '#8B95A0',
    edgeLine:       '#6B7B8D',
    selGlow:        '#2563EB',
    labelText:      '#4A5568',
    gridDot:        '#E2E6EA',
    edgeLabelBg:    '#FFFFFF',
};


// ══════════════════════════════════════════════════════════════════
// Init
// ══════════════════════════════════════════════════════════════════

var _resizeTimer = null;
var _sldPageReady = false;  // true once QWebChannel is connected

function initSld() {
    console.log('SLD: initSld v5 — optimized rendering');
    svg = d3.select('#sld-container')
        .append('svg')
        .attr('width', '100%')
        .attr('height', '100%')
        .style('opacity', '0');  // Hidden until first layoutAndRender + fitView

    rootGroup = svg.append('g').attr('class', 'sld-root');

    // Zoom — extents are updated dynamically after each layout
    zoomBehavior = d3.zoom()
        .scaleExtent([0.1, 5])
        .on('zoom', function(event) {
            rootGroup.attr('transform', event.transform);
        });
    svg.call(zoomBehavior);

    svg.on('click', function(event) {
        if (event.target === svg.node() || event.target.tagName === 'svg') {
            clearSelection();
            if (sldBridge) sldBridge.on_element_deselected();
        }
    });

    elk = new ELK();

    // Debounced ResizeObserver — avoid rapid redraws during panel resize
    new ResizeObserver(function() {
        if (!diagramBounds || !svg) return;
        if (_resizeTimer) clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(function() {
            _resizeTimer = null;
            var cw = svg.node().clientWidth;
            var ch = svg.node().clientHeight;
            if (!cw || !ch || cw < 10 || ch < 10) return;  // container not visible yet
            var sc = Math.min(cw / diagramBounds.w, ch / diagramBounds.h);
            zoomBehavior.scaleExtent([Math.max(0.05, sc), 5]);
            // Reposition legend to new upper-right corner
            if (currentData) _drawLegend(currentData);
            // Reposition frequency gauge below legend
            if (currentOpsData) _drawFrequencyGauge(null, currentOpsData);
        }, 100);
    }).observe(document.getElementById('sld-container'));

    if (typeof QWebChannel !== 'undefined') {
        new QWebChannel(qt.webChannelTransport, function(channel) {
            sldBridge = channel.objects.sldBridge;
            _sldPageReady = true;
            sldBridge.on_sld_ready();
        });
    }
}


// ══════════════════════════════════════════════════════════════════
// Main API
// ══════════════════════════════════════════════════════════════════

function layoutAndRender(jsonStr) {
    var data;
    try { data = JSON.parse(jsonStr); } catch(e) {
        console.error('SLD: bad JSON', e); _showEmpty(true); return;
    }
    currentData = data;
    var elkGraph = data.elkGraph;
    if (!elkGraph || !elkGraph.children || elkGraph.children.length === 0) {
        _showEmpty(true); return;
    }
    _showEmpty(false);

    // Hide diagram + show spinner while ELK computes layout
    _setDiagramVisible(false);
    _showLoading(true);

    // Use setTimeout(0) to yield to browser so spinner paints before
    // synchronous layout / render runs on the JS thread.
    setTimeout(function() {
        if (elkGraph.precomputedLayout) {
            // Python already assigned x/y/sections — skip the layout
            // step entirely. This is the PowerFactory-style grid layout
            // which is O(n) and finishes before the spinner even paints.
            currentLayouted = elkGraph;
            render(data, elkGraph);
            _showLoading(false);
            _ensureFitView(function() {
                _setDiagramVisible(true);
                if (sldBridge) sldBridge.on_sld_ready();
            });
            return;
        }
        elk.layout(elkGraph).then(function(layouted) {
            currentLayouted = layouted;
            render(data, layouted);
            _showLoading(false);
            // Wait for valid container dimensions, apply fitView, then reveal
            _ensureFitView(function() {
                _setDiagramVisible(true);
                if (sldBridge) sldBridge.on_sld_ready();
            });
        }).catch(function(err) {
            console.error('SLD: ELK error', err);
            _showLoading(false);
            _setDiagramVisible(true);
        });
    }, 0);
}

/** Show or hide the SVG diagram layer. */
function _setDiagramVisible(visible) {
    if (!svg) return;
    svg.style('opacity', visible ? '1' : '0')
       .style('pointer-events', visible ? 'all' : 'none');
}

/**
 * Ensure fitView runs with valid container dimensions.
 * Retries up to 20 frames (~330ms at 60fps) if the container still
 * has zero size (Qt WebEngine hasn't painted yet).
 */
function _ensureFitView(callback, attempt) {
    attempt = attempt || 0;
    var cw = svg.node().clientWidth;
    var ch = svg.node().clientHeight;
    if ((!cw || !ch || cw < 10 || ch < 10) && attempt < 20) {
        console.log('SLD: _ensureFitView waiting, attempt=' + attempt +
                    ' cw=' + cw + ' ch=' + ch);
        requestAnimationFrame(function() { _ensureFitView(callback, attempt + 1); });
        return;
    }
    console.log('SLD: fitView ready, cw=' + cw + ' ch=' + ch +
                ' bounds=' + JSON.stringify(diagramBounds));
    fitView(false);
    if (callback) callback();
}


// ══════════════════════════════════════════════════════════════════
// Rendering (two-pass)
// ══════════════════════════════════════════════════════════════════

function render(data, layouted) {
    rootGroup.selectAll('*').remove();
    elementRegistry = {};
    busLayout = {};
    selectedKey = null;
    _obstacleGrid = {};  // Reset spatial grid for label collision avoidance
    _pendingLabels = []; // Edge labels are placed last, avoiding every element
    _darkenCache = {};   // Reset color cache

    var C = data.constants || {};
    var busH    = C.busH || 6;
    var stubLen = C.stubLen || 40;
    var eqSize  = C.equipSize || 36;
    var eqSpace = C.equipSpacing || 72;
    var busEquip = data.busEquipment || {};

    // Layer groups (draw order: grid → groups bg → edges → buses → stubs → equipment)
    var gGrid   = rootGroup.append('g').attr('class', 'layer-grid');
    var gGroups = rootGroup.append('g').attr('class', 'layer-groups');
    var gEdges  = rootGroup.append('g').attr('class', 'layer-edges');
    var gBuses  = rootGroup.append('g').attr('class', 'layer-buses');
    var gStubs  = rootGroup.append('g').attr('class', 'layer-stubs');
    var gEquip  = rootGroup.append('g').attr('class', 'layer-equip');

    // ── Pass 1: Collect bus positions from ELK layout ──
    if (layouted.children) {
        layouted.children.forEach(function(busNode) {
            var bid = busNode.id;
            var props = busNode.properties || {};
            var equips = busEquip[bid] || [];
            // Honor orientation from Python (90 = vertical bar). When
            // vertical, barLen runs along the rectangle's height; when
            // horizontal, along its width.
            var pyOrient = (props.orientation === 90) ? 90 : 0;
            var elkW = busNode.width || 200;
            var elkH = busNode.height || busH;
            busLayout[bid] = {
                x: busNode.x || 0,
                y: busNode.y || 0,
                w: elkW,
                h: busH,
                elkW: elkW,
                elkH: elkH,
                color: props.color || T.edgeLine,
                voltageKv: props.voltageKv || 0,
                label: props.label || bid,
                parentNode: props.parentNode,
                equips: equips,
                orientation: pyOrient,
                barLen: pyOrient === 90 ? elkH : elkW,
            };
        });
    }

    // ── Compute bus orientations from edge topology ──
    _computeBusOrientations(layouted, busH, eqSpace);

    // ── Fix edge endpoints: snap to visible bus bar face ──
    // When the layout is precomputed, edges already carry their final
    // start/end/bendPoints — skip the JS-side rebuild entirely. The
    // re-routing was overwriting Python's Z-shape with a midY-collapsed
    // version that piled up edges on the same horizontal line.
    if (!(currentData && currentData.elkGraph
          && currentData.elkGraph.precomputedLayout)) {
        _fixEdgeEndpoints(layouted, busH);
    }

    // ── Draw node group backgrounds ──
    var nodeGroups = data.nodeGroups || [];
    nodeGroups.forEach(function(ng) {
        _drawNodeGroup(gGroups, ng, busH, stubLen, eqSize, eqSpace);
    });

    // ── Draw inter-bus edges ──
    if (layouted.edges) {
        layouted.edges.forEach(function(edge) {
            _drawEdge(gEdges, edge, busH);
        });
    }

    // ── Draw buses + stubs + equipment ──
    Object.keys(busLayout).forEach(function(bid) {
        _drawBus(gBuses, gStubs, gEquip, bid, busH, stubLen, eqSize, eqSpace);
    });

    // ── Final pass: place edge labels now that EVERY element (bars, edges,
    //    equipment) is a registered obstacle, so labels avoid them all while
    //    still sitting on their own line. ──
    _pendingLabels.forEach(_drawPendingLabel);

    // ── Compute diagram bounds & draw grid ──
    _updateDiagramBounds(gGrid);
    // Note: fitView is called by _ensureFitView in layoutAndRender,
    // AFTER render completes and container dimensions are verified.

    // ── Fixed legend overlay (outside rootGroup — not affected by zoom) ──
    _drawLegend(data);
}


// ══════════════════════════════════════════════════════════════════
// Bus orientation — auto-detect from edge topology
// ══════════════════════════════════════════════════════════════════

/**
 * Determine whether each bus should be horizontal (0°) or vertical (90°)
 * based on the predominant direction of its connected edges.
 *
 * Rule: the bus bar should be PERPENDICULAR to the main flow of edges.
 *   - Edges mostly horizontal → vertical bus (90°)
 *   - Edges mostly vertical → horizontal bus (0°)
 *   - No edges / tie → horizontal (0°)
 */
function _computeBusOrientations(layouted, busH, eqSpace) {
    // With the deterministic PowerFactory grid layout, orientation is
    // already set per bus from Python — keep it.
    if (currentData && currentData.elkGraph
        && currentData.elkGraph.precomputedLayout) {
        return;
    }
    var edgeDirs = {};
    Object.keys(busLayout).forEach(function(bid) {
        edgeDirs[bid] = { h: 0, v: 0 };
    });

    if (layouted.edges) {
        layouted.edges.forEach(function(edge) {
            var srcId = (edge.sources && edge.sources[0]) || '';
            var tgtId = (edge.targets && edge.targets[0]) || '';
            var src = busLayout[srcId];
            var tgt = busLayout[tgtId];
            if (!src || !tgt) return;

            var dx = Math.abs((tgt.x + tgt.w / 2) - (src.x + src.w / 2));
            var dy = Math.abs(tgt.y - src.y);

            if (dx > dy) {
                if (edgeDirs[srcId]) edgeDirs[srcId].h++;
                if (edgeDirs[tgtId]) edgeDirs[tgtId].h++;
            } else {
                if (edgeDirs[srcId]) edgeDirs[srcId].v++;
                if (edgeDirs[tgtId]) edgeDirs[tgtId].v++;
            }
        });
    }

    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        var dirs = edgeDirs[bid] || { h: 0, v: 0 };

        // More horizontal edges → vertical bus (perpendicular to flow)
        if (dirs.h > dirs.v) {
            bl.orientation = 90;
            bl.barLen = bl.elkH;
        } else {
            bl.orientation = 0;
            bl.barLen = bl.elkW;
        }
    });
}


// ══════════════════════════════════════════════════════════════════
// Edge endpoint correction — rebuild clean orthogonal routes
// ══════════════════════════════════════════════════════════════════

/**
 * After ELK layout, edge section endpoints connect to the full node
 * bounding box (which includes the equipment area below the bus bar).
 *
 * Instead of patching ELK's bend points (which creates zigzags), we
 * compute the correct connection face for each bus bar and then build
 * a clean orthogonal route from scratch.
 */
function _fixEdgeEndpoints(layouted, busH) {
    if (!layouted.edges) return;

    // Phase 1: compute natural exit points and collect per-bus connections
    var busConns = {};  // busId → [{edge, isStart, face}]

    layouted.edges.forEach(function(edge) {
        var srcId = (edge.sources && edge.sources[0]) || '';
        var tgtId = (edge.targets && edge.targets[0]) || '';
        var src = busLayout[srcId];
        var tgt = busLayout[tgtId];
        if (!edge.sections || !src || !tgt) return;

        var srcFace = _getBusFace(src, tgt, busH);
        var tgtFace = _getBusFace(tgt, src, busH);

        // Store faces on edge for phase 3
        edge._srcFace = srcFace;
        edge._tgtFace = tgtFace;

        // Group ALL connections by bus (not by side — opposite-side
        // connections still land on the same bar and can overlap).
        // ``other`` lets Phase 2 sort connections by the location of
        // the OPPOSITE endpoint, which gives a natural left→right /
        // top→bottom ordering and reduces edge crossings.
        if (!busConns[srcId]) busConns[srcId] = [];
        busConns[srcId].push({ face: srcFace, other: tgtFace });

        if (!busConns[tgtId]) busConns[tgtId] = [];
        busConns[tgtId].push({ face: tgtFace, other: srcFace });
    });

    // Phase 2: distribute edge connections UNIFORMLY along each bar,
    // around the fixed equipment stub positions. Starting from ELK's
    // natural endpoints and just spacing them out leaves the rest of
    // the bar empty when ELK clusters multiple edges at one point.
    var minSpacing = 18;

    Object.keys(busConns).forEach(function(busId) {
        var bl = busLayout[busId];
        if (!bl) return;

        var conns = busConns[busId];
        var isVert = bl.orientation === 90;
        var axis = isVert ? 'y' : 'x';
        var barStart = isVert ? bl.y : bl.x;
        var barLen = bl.barLen;
        var margin = 15;
        var lo = barStart + margin;
        var hi = barStart + barLen - margin;

        // Sort edge connections by the position of the OTHER endpoint
        // along the bar's axis — keeps edges going "left" to the left
        // half of the bar, edges going "right" to the right half, which
        // minimizes crossings naturally.
        conns.forEach(function(c) {
            // Pick a stable secondary key from the original face
            // (whatever ELK gave us) so identical primaries stay stable.
            var other = c.other;
            var secondary = other ? other[axis] : c.face[axis];
            c._sortKey = secondary;
        });
        conns.sort(function(a, b) { return a._sortKey - b._sortKey; });

        // Equipment stub positions act as anchors that push edge
        // connections into the gaps between (and around) them.
        var equipPos = _getEquipStubPositions(bl, isVert);
        equipPos.sort(function(a, b) { return a - b; });

        // Build segments of free space along the bar (between equipment
        // anchors and the bar ends) and distribute edges proportionally
        // to segment length so the whole bar is used.
        var anchors = [lo].concat(equipPos).concat([hi]);
        var nConns = conns.length;
        if (nConns === 0) return;

        // Total free length excluding the small zone around each
        // equipment stub (we keep edges away from stubs by minSpacing).
        var segments = [];
        var totalFree = 0;
        for (var s = 0; s < anchors.length - 1; s++) {
            var a = anchors[s];
            var b = anchors[s + 1];
            // Skip the zero-length endpoint anchors at lo/hi
            var segLo = (s === 0) ? a : a + minSpacing;
            var segHi = (s === anchors.length - 2) ? b : b - minSpacing;
            if (segHi <= segLo) continue;
            segments.push({ lo: segLo, hi: segHi, len: segHi - segLo });
            totalFree += segHi - segLo;
        }
        if (segments.length === 0) {
            // Fallback: distribute uniformly across the whole bar
            segments.push({ lo: lo, hi: hi, len: hi - lo });
            totalFree = hi - lo;
        }

        // Allocate connections to segments proportionally to length
        var connIdx = 0;
        for (var sg = 0; sg < segments.length && connIdx < nConns; sg++) {
            var seg = segments[sg];
            var share = (sg === segments.length - 1)
                ? (nConns - connIdx)
                : Math.max(1, Math.round(nConns * seg.len / totalFree));
            share = Math.min(share, nConns - connIdx);
            // Place ``share`` connections evenly within the segment
            for (var k = 0; k < share; k++) {
                var t = (share === 1) ? 0.5 : (k + 0.5) / share;
                var pos = seg.lo + t * seg.len;
                conns[connIdx].face[axis] = pos;
                connIdx++;
            }
        }
        // If any connections left over (rounding), pin them at hi
        while (connIdx < nConns) {
            conns[connIdx].face[axis] = hi;
            connIdx++;
        }
    });

    // Phase 3: apply final face positions and build routes
    layouted.edges.forEach(function(edge) {
        if (!edge._srcFace || !edge._tgtFace || !edge.sections) return;
        // If Python pre-assigned a horizontal lane Y for this edge,
        // use it instead of midpoint Y so all inter-row edges in the
        // same gap don't pile onto the same horizontal line.
        var laneY = edge.properties && edge.properties.laneY;
        edge.sections.forEach(function(sec) {
            sec.startPoint = edge._srcFace;
            sec.endPoint = edge._tgtFace;
            sec.bendPoints = _buildOrthogonalRoute(
                edge._srcFace, edge._tgtFace, laneY
            );
        });
        delete edge._srcFace;
        delete edge._tgtFace;
    });
}

/** Get equipment stub positions along the bar axis for a bus. */
function _getEquipStubPositions(bl, isVert) {
    var equips = bl.equips || [];
    var nEq = equips.length;
    if (nEq === 0) return [];

    var barLen = bl.barLen;
    var eqSpace = 72;  // matches C.equipSpacing default
    var positions = [];

    if (isVert) {
        var vertSpace = Math.min(eqSpace, barLen / Math.max(nEq, 1));
        var startY = (barLen - (nEq - 1) * vertSpace) / 2;
        for (var i = 0; i < nEq; i++) {
            positions.push(bl.y + startY + i * vertSpace);
        }
    } else {
        var rowW = nEq * eqSpace;
        var startX = bl.x + (barLen - rowW) / 2 + eqSpace / 2;
        for (var i = 0; i < nEq; i++) {
            positions.push(startX + i * eqSpace);
        }
    }
    return positions;
}

/**
 * Determine the connection point on a bus bar facing toward the other bus.
 * ALWAYS exits perpendicular to the bar axis:
 *   - Horizontal bus (0°): exit from top or bottom face (vertical departure)
 *   - Vertical bus (90°): exit from left or right face (horizontal departure)
 *
 * Returns { x, y, dir } where dir is 'up'|'down'|'left'|'right'.
 */
function _getBusFace(bl, other, busH) {
    var isVert = bl.orientation === 90;
    var barLen = bl.barLen;

    // Other bus bar center
    var oVert = other.orientation === 90;
    var oBarLen = other.barLen;
    var ocx = oVert ? other.x + busH / 2 : other.x + oBarLen / 2;
    var ocy = oVert ? other.y + oBarLen / 2 : other.y + busH / 2;

    if (isVert) {
        // Vertical bar runs top→bottom: exit LEFT or RIGHT (perpendicular)
        var busCx = bl.x + busH / 2;
        var dx = ocx - busCx;
        var exitX = dx >= 0 ? bl.x + busH : bl.x;
        var exitY = _clamp(ocy, bl.y + 15, bl.y + barLen - 15);
        return { x: exitX, y: exitY, dir: dx >= 0 ? 'right' : 'left' };
    } else {
        // Horizontal bar runs left→right: exit TOP or BOTTOM (perpendicular)
        var busCy = bl.y + busH / 2;
        var dy = ocy - busCy;
        var exitX2 = _clamp(ocx, bl.x + 15, bl.x + barLen - 15);
        var exitY2 = dy >= 0 ? bl.y + busH : bl.y;
        return { x: exitX2, y: exitY2, dir: dy >= 0 ? 'down' : 'up' };
    }
}

/**
 * Build a clean orthogonal route between two connection points.
 * Uses the exit direction (dir) from each endpoint to ensure edges
 * always depart/arrive perpendicular to bus bars.
 *
 * Returns an array of bend points.
 */
function _buildOrthogonalRoute(start, end, laneY) {
    var dx = end.x - start.x;
    var dy = end.y - start.y;
    var absDx = Math.abs(dx);
    var absDy = Math.abs(dy);

    var sDir = start.dir || 'down';
    var eDir = end.dir || 'up';
    var sVert = (sDir === 'up' || sDir === 'down');
    var eVert = (eDir === 'up' || eDir === 'down');
    var stub = 35;  // minimum distance before first turn

    // Already aligned on one axis → straight line
    if (absDx < 3 && sVert && eVert) return [];
    if (absDy < 3 && !sVert && !eVert) return [];

    if (sVert && eVert) {
        // Both exit vertically → horizontal connecting segment (Z/S or U shape)
        if (sDir !== eDir) {
            // Opposite (e.g. down↔up): Z/S-shape at the assigned lane Y
            // (each edge gets a unique laneY so they don't stack).
            var midY = (typeof laneY === 'number')
                ? laneY
                : (start.y + end.y) / 2;
            return [
                { x: start.x, y: midY },
                { x: end.x, y: midY }
            ];
        } else {
            // Same direction (e.g. both down): U-shape
            var extY = (sDir === 'down')
                ? Math.max(start.y, end.y) + stub
                : Math.min(start.y, end.y) - stub;
            return [
                { x: start.x, y: extY },
                { x: end.x, y: extY }
            ];
        }
    }

    if (!sVert && !eVert) {
        // Both exit horizontally → vertical connecting segment
        if (sDir !== eDir) {
            var midX = (start.x + end.x) / 2;
            return [
                { x: midX, y: start.y },
                { x: midX, y: end.y }
            ];
        } else {
            var extX = (sDir === 'right')
                ? Math.max(start.x, end.x) + stub
                : Math.min(start.x, end.x) - stub;
            return [
                { x: extX, y: start.y },
                { x: extX, y: end.y }
            ];
        }
    }

    // Mixed: one vertical exit, one horizontal exit → L-shape (1 bend)
    if (sVert) {
        // Start goes vertical, end goes horizontal → bend at (start.x, end.y)
        return [{ x: start.x, y: end.y }];
    } else {
        // Start goes horizontal, end goes vertical → bend at (end.x, start.y)
        return [{ x: end.x, y: start.y }];
    }
}


// ══════════════════════════════════════════════════════════════════
// Drawing: Node groups (substation backgrounds)
// ══════════════════════════════════════════════════════════════════

function _drawNodeGroup(layer, ng, busH, stubLen, eqSize, eqSpace) {
    var busIds = ng.busIds || [];
    if (busIds.length === 0) return;

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    busIds.forEach(function(bid) {
        var bl = busLayout[bid];
        if (!bl) return;
        var nEq = bl.equips.length;
        var isVert = bl.orientation === 90;
        var barLen = bl.barLen;

        if (isVert) {
            // Vertical bus: bar is tall, equipment extends to the right
            var eqExtentX = nEq > 0 ? busH + stubLen + eqSize + 60 : busH + 10;
            minX = Math.min(minX, bl.x - 60);  // label space to the left
            minY = Math.min(minY, bl.y);
            maxX = Math.max(maxX, bl.x + eqExtentX);
            maxY = Math.max(maxY, bl.y + barLen);
        } else {
            // Horizontal bus: bar is wide, equipment extends below
            var eqExtentY = nEq > 0 ? busH + stubLen + eqSize + 20 : busH + 10;
            minX = Math.min(minX, bl.x);
            minY = Math.min(minY, bl.y - 18);  // label space above
            maxX = Math.max(maxX, bl.x + barLen);
            maxY = Math.max(maxY, bl.y + eqExtentY);
        }
    });
    if (minX === Infinity) return;

    var pad = 30, titleH = 28;
    var gx = minX - pad;
    var gy = minY - pad - titleH;
    var gw = maxX - minX + 2 * pad;
    var gh = maxY - minY + 2 * pad + titleH;

    // Group outline (no fill, no shadow)
    layer.append('rect')
        .attr('class', 'node-group-bg')
        .attr('x', gx).attr('y', gy)
        .attr('width', gw).attr('height', gh)
        .attr('rx', 6)
        .attr('fill', 'none')
        .attr('stroke', T.groupBorder)
        .attr('stroke-width', 1.2)
        .attr('stroke-dasharray', '6,3');

    // Title label
    var titleText = ng.name;
    layer.append('text')
        .attr('class', 'group-title')
        .attr('x', gx + 10).attr('y', gy + 16)
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '11px')
        .attr('font-weight', '700')
        .attr('letter-spacing', '0.3px')
        .attr('fill', T.groupTitle)
        .text(titleText);
}


// ══════════════════════════════════════════════════════════════════
// Drawing: Bus bars (thick-line PowerFactory style)
// ══════════════════════════════════════════════════════════════════

function _drawBus(gBuses, gStubs, gEquip, bid, busH, stubLen, eqSize, eqSpace) {
    var bl = busLayout[bid];
    var equips = bl.equips;
    var nEq = equips.length;
    var isVert = bl.orientation === 90;
    var barLen = bl.barLen;

    // Register bus bar as obstacle for label collision avoidance
    _addBusObstacle(bl, busH);

    // ── Bus bar group ──
    var busG = gBuses.append('g')
        .attr('class', 'bus-bar')
        .attr('transform', 'translate(' + bl.x + ',' + bl.y + ')')
        .style('cursor', 'pointer');

    if (isVert) {
        // ═══ VERTICAL bus bar ═══
        var barX = busH / 2;

        // Border stroke (darker, wider — gives "depth" to distinguish from edges)
        busG.append('line')
            .attr('class', 'bus-bar-border')
            .attr('x1', barX).attr('y1', 0)
            .attr('x2', barX).attr('y2', barLen)
            .attr('stroke', _darken(bl.color, 0.3))
            .attr('stroke-width', 7)
            .attr('stroke-linecap', 'round');

        // Main vertical bus line
        busG.append('line')
            .attr('class', 'bus-bar-line')
            .attr('x1', barX).attr('y1', 0)
            .attr('x2', barX).attr('y2', barLen)
            .attr('stroke', bl.color)
            .attr('stroke-width', 5)
            .attr('stroke-linecap', 'round');

        // End caps
        busG.append('circle').attr('cx', barX).attr('cy', 0)
            .attr('r', 4.5).attr('fill', bl.color)
            .attr('stroke', _darken(bl.color, 0.3)).attr('stroke-width', 1);
        busG.append('circle').attr('cx', barX).attr('cy', barLen)
            .attr('r', 4.5).attr('fill', bl.color)
            .attr('stroke', _darken(bl.color, 0.3)).attr('stroke-width', 1);

        // Hit area
        busG.append('rect').attr('class', 'bus-bar-rect')
            .attr('x', -6).attr('y', -4)
            .attr('width', busH + 12).attr('height', barLen + 8)
            .attr('fill', 'transparent');

        if (labelsVisible) {
            // Voltage label — to the LEFT of vertical bar, tight to bar
            if (bl.voltageKv) {
                busG.append('text').attr('class', 'data-label')
                    .attr('x', -6).attr('y', barLen / 2 - 6)
                    .attr('text-anchor', 'end').attr('dominant-baseline', 'auto')
                    .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                    .attr('font-size', '10px').attr('font-weight', '700')
                    .attr('fill', bl.color)
                    .text(bl.voltageKv + ' kV');
            }
            // Bus name — to the LEFT, below voltage
            busG.append('text').attr('class', 'data-label')
                .attr('x', -6).attr('y', barLen / 2 + 8)
                .attr('text-anchor', 'end').attr('dominant-baseline', 'auto')
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px')
                .attr('fill', T.labelText).attr('opacity', 0.75)
                .text(bl.label);
        }

        // ── Equipment column to the RIGHT ──
        if (nEq > 0) {
            var vertSpace = Math.min(eqSpace, barLen / Math.max(nEq, 1));
            var startY = (barLen - (nEq - 1) * vertSpace) / 2;
            var stubLeft = busH;
            var eqCenterX = bl.x + stubLeft + stubLen + eqSize / 2;

            equips.forEach(function(eq, idx) {
                var cy = bl.y + startY + idx * vertSpace;

                // Horizontal stub
                gStubs.append('line')
                    .attr('x1', bl.x + stubLeft).attr('y1', cy)
                    .attr('x2', bl.x + stubLeft + stubLen).attr('y2', cy)
                    .attr('stroke', T.stubLine).attr('stroke-width', 2);

                // Connection dot at bus bar
                gStubs.append('circle')
                    .attr('cx', bl.x + stubLeft).attr('cy', cy)
                    .attr('r', 4).attr('fill', bl.color)
                    .attr('stroke', _darken(bl.color, 0.25)).attr('stroke-width', 1);

                // Equipment symbol
                var eqG = gEquip.append('g').attr('class', 'equipment')
                    .attr('transform', 'translate(' + eqCenterX + ',' + cy + ')')
                    .style('cursor', 'pointer');

                _drawSymbol(eqG, eq, eqSize, 'right');

                // Register equipment circle as obstacle
                _addObstacle(eqCenterX - eqSize / 2, cy - eqSize / 2,
                             eqCenterX + eqSize / 2, cy + eqSize / 2);

                // Sublabel to the RIGHT of symbol, collision-aware
                if (labelsVisible && eq.sublabel) {
                    var slW = eq.sublabel.length * 6 + 4;
                    var slH = 12;
                    var slPref = _placeLabel(eqCenterX + eqSize / 2 + 6 + slW / 2, cy, slW, slH);
                    eqG.append('text').attr('class', 'data-label')
                        .attr('x', slPref.x - eqCenterX).attr('y', slPref.y - cy)
                        .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                        .attr('font-size', '9px').attr('font-weight', '600')
                        .attr('fill', T.labelText).text(eq.sublabel);
                }

                _wireEquipInteraction(eqG, eq);
                elementRegistry[eq.elementType + ':' + eq.elementId] = { group: eqG, props: eq };
            });
        }

    } else {
        // ═══ HORIZONTAL bus bar ═══
        var busMidY = busH / 2;

        // Border stroke (darker, wider)
        busG.append('line').attr('class', 'bus-bar-border')
            .attr('x1', 0).attr('y1', busMidY)
            .attr('x2', barLen).attr('y2', busMidY)
            .attr('stroke', _darken(bl.color, 0.3))
            .attr('stroke-width', 7)
            .attr('stroke-linecap', 'round');

        // Main horizontal bus line
        busG.append('line').attr('class', 'bus-bar-line')
            .attr('x1', 0).attr('y1', busMidY)
            .attr('x2', barLen).attr('y2', busMidY)
            .attr('stroke', bl.color)
            .attr('stroke-width', 5)
            .attr('stroke-linecap', 'round');

        // End caps
        busG.append('circle').attr('cx', 0).attr('cy', busMidY)
            .attr('r', 4.5).attr('fill', bl.color)
            .attr('stroke', _darken(bl.color, 0.3)).attr('stroke-width', 1);
        busG.append('circle').attr('cx', barLen).attr('cy', busMidY)
            .attr('r', 4.5).attr('fill', bl.color)
            .attr('stroke', _darken(bl.color, 0.3)).attr('stroke-width', 1);

        // Hit area
        busG.append('rect').attr('class', 'bus-bar-rect')
            .attr('x', -4).attr('y', -6)
            .attr('width', barLen + 8).attr('height', busH + 12)
            .attr('fill', 'transparent');

        if (labelsVisible) {
            // Voltage label — ABOVE bus bar, tight to bar
            if (bl.voltageKv) {
                busG.append('text').attr('class', 'data-label')
                    .attr('x', 4).attr('y', -5)
                    .attr('text-anchor', 'start').attr('dominant-baseline', 'auto')
                    .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                    .attr('font-size', '10px').attr('font-weight', '700')
                    .attr('fill', bl.color)
                    .text(bl.voltageKv + ' kV');
            }
            // Bus name — ABOVE, right-aligned
            busG.append('text').attr('class', 'data-label')
                .attr('x', barLen - 4).attr('y', -5)
                .attr('text-anchor', 'end').attr('dominant-baseline', 'auto')
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px')
                .attr('fill', T.labelText).attr('opacity', 0.75)
                .text(bl.label);
        }

        // ── Equipment row BELOW bus ──
        if (nEq > 0) {
            var rowW = nEq * eqSpace;
            var startX = bl.x + (barLen - rowW) / 2 + eqSpace / 2;
            var stubTop = bl.y + busH;
            var eqCenterY = stubTop + stubLen + eqSize / 2;

            equips.forEach(function(eq, idx) {
                var cx = startX + idx * eqSpace;

                // Vertical stub
                gStubs.append('line')
                    .attr('x1', cx).attr('y1', stubTop)
                    .attr('x2', cx).attr('y2', stubTop + stubLen)
                    .attr('stroke', T.stubLine).attr('stroke-width', 2);

                // Connection dot at bus bar
                gStubs.append('circle')
                    .attr('cx', cx).attr('cy', stubTop)
                    .attr('r', 4).attr('fill', bl.color)
                    .attr('stroke', _darken(bl.color, 0.25)).attr('stroke-width', 1);

                // Equipment symbol
                var eqG = gEquip.append('g').attr('class', 'equipment')
                    .attr('transform', 'translate(' + cx + ',' + eqCenterY + ')')
                    .style('cursor', 'pointer');

                _drawSymbol(eqG, eq, eqSize, 'down');

                // Register equipment circle as obstacle
                _addObstacle(cx - eqSize / 2, eqCenterY - eqSize / 2,
                             cx + eqSize / 2, eqCenterY + eqSize / 2);

                // Sublabel BELOW symbol, collision-aware
                if (labelsVisible && eq.sublabel) {
                    var slW2 = eq.sublabel.length * 6 + 4;
                    var slH2 = 12;
                    var slPref2 = _placeLabel(cx, eqCenterY + eqSize / 2 + 14, slW2, slH2);
                    eqG.append('text').attr('class', 'data-label')
                        .attr('x', slPref2.x - cx).attr('y', slPref2.y - eqCenterY)
                        .attr('text-anchor', 'middle')
                        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                        .attr('font-size', '9px').attr('font-weight', '600')
                        .attr('fill', T.labelText).text(eq.sublabel);
                }

                _wireEquipInteraction(eqG, eq);
                elementRegistry[eq.elementType + ':' + eq.elementId] = { group: eqG, props: eq };
            });
        }
    }

    // ── Common: interaction ──
    busG.on('click', function(event) {
        event.stopPropagation();
        _selectElement('bus', bid, busG);
    }).on('mouseenter', function(event) {
        _showTooltip(event, bl.label, bl.voltageKv ? bl.voltageKv + ' kV' : '');
    }).on('mouseleave', _hideTooltip);

    elementRegistry['bus:' + bid] = { group: busG, props: bl };
}

/**
 * Draw a symbol inside an equipment group.
 * @param stubDir  'down' (horizontal bus, stub from above) or 'right' (vertical bus, stub from left)
 */
function _drawSymbol(eqG, eq, eqSize, stubDir) {
    var sym = sldSymbols[eq.symbolType];
    if (sym) {
        sym(eqG, eqSize, eq.color, stubDir || 'down');
    } else {
        eqG.append('circle').attr('r', eqSize / 2 - 2)
            .attr('fill', '#fff').attr('stroke', eq.color || '#888').attr('stroke-width', 2);
        eqG.append('text').attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-size', eqSize * 0.35).attr('fill', eq.color || '#888').text('?');
    }
}

/** Wire click/hover events for an equipment symbol. */
function _wireEquipInteraction(eqG, eq) {
    eqG.on('click', function(event) {
        event.stopPropagation();
        _selectElement(eq.elementType, eq.elementId, eqG);
    }).on('mouseenter', function(event) {
        var sub = eq.sublabel || '';
        if (eq.fuel) sub = eq.fuel + (sub ? ' \u00b7 ' + sub : '');
        _showTooltip(event, eq.label || eq.symbolType, sub);
        if (sldBridge) sldBridge.on_element_hovered(eq.elementType || '', eq.elementId || '');
    }).on('mouseleave', _hideTooltip);
}


// ══════════════════════════════════════════════════════════════════
// Drawing: Edges (transmission lines, transformers, converters)
// ══════════════════════════════════════════════════════════════════

function _edgeMidpoint(sections) {
    var pts = [];
    sections.forEach(function(sec) {
        pts.push(sec.startPoint);
        if (sec.bendPoints) sec.bendPoints.forEach(function(bp) { pts.push(bp); });
        pts.push(sec.endPoint);
    });
    if (pts.length === 0) return { x: 0, y: 0 };
    if (pts.length === 1) return pts[0];

    var totalLen = 0;
    var segs = [];
    for (var i = 1; i < pts.length; i++) {
        var dx = pts[i].x - pts[i - 1].x, dy = pts[i].y - pts[i - 1].y;
        var d = Math.sqrt(dx * dx + dy * dy);
        segs.push({ from: pts[i - 1], to: pts[i], len: d });
        totalLen += d;
    }
    var half = totalLen / 2, acc = 0;
    for (var j = 0; j < segs.length; j++) {
        if (acc + segs[j].len >= half) {
            var t = segs[j].len > 0 ? (half - acc) / segs[j].len : 0;
            return {
                x: segs[j].from.x + t * (segs[j].to.x - segs[j].from.x),
                y: segs[j].from.y + t * (segs[j].to.y - segs[j].from.y),
            };
        }
        acc += segs[j].len;
    }
    return pts[pts.length - 1];
}

/**
 * Draw one deferred edge label. Transmission lines carry their label INLINE,
 * breaking the line as ``----[Label]----`` along a horizontal stretch (an
 * opaque box hides the line behind the text); transformers/converters keep
 * their label just below the IEC symbol. Either way the label avoids every
 * element except its own line (obstacles tagged with the edge's id).
 */
function _drawPendingLabel(e) {
    var props = e.props;
    if (!props.label) return;
    var labelW = props.label.length * 6.5 + 16;
    var labelH = 18;
    var labelX, labelY, inline = false;
    var hseg = (e.edgeType === 'transmission') ? _longestHSeg(e.sections) : null;
    if (hseg && hseg.len > labelW * 0.4) {
        var p = _placeLabelInline(hseg.x0, hseg.x1, hseg.y, labelW, labelH, e.owner);
        labelX = p.x; labelY = p.y; inline = true;
    } else {
        labelX = e.mid.x;
        labelY = e.mid.y;
        // Transformer/converter labels sit BELOW their symbol, centred on the
        // element's own X so they don't reach into a neighbour.
        if (e.edgeType === 'transformer') labelY = e.mid.y + 34;
        else if (e.edgeType === 'converter') labelY = e.mid.y + 32;
        var placed = _placeLabel(labelX, labelY, labelW, labelH, e.owner);
        labelX = placed.x;
        labelY = placed.y;
    }

    e.g.append('rect')
        .attr('x', labelX - labelW / 2).attr('y', labelY - 9)
        .attr('width', labelW).attr('height', labelH).attr('rx', 4)
        .attr('fill', T.edgeLabelBg).attr('stroke', e.color)
        .attr('stroke-width', 0.8).attr('opacity', inline ? 1 : 0.95);
    e.g.append('text')
        .attr('x', labelX).attr('y', labelY + 1)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '10px').attr('font-weight', '600')
        .attr('fill', e.color).text(props.label);
}

/**
 * Longest horizontal run of an edge, as { x0, x1, y, len }, or null if the
 * edge has no horizontal segment. Used to sit a line's label cleanly ON the
 * line (``----[Label]----``) along a straight horizontal stretch.
 */
function _longestHSeg(sections) {
    var pts = [];
    sections.forEach(function(sec) {
        pts.push(sec.startPoint);
        if (sec.bendPoints) sec.bendPoints.forEach(function(bp) { pts.push(bp); });
        pts.push(sec.endPoint);
    });
    var best = null, bestLen = -1;
    for (var i = 1; i < pts.length; i++) {
        var a = pts[i - 1], b = pts[i];
        if (Math.abs(a.y - b.y) < 0.5) {
            var len = Math.abs(a.x - b.x);
            if (len > bestLen) { bestLen = len; best = { x0: a.x, x1: b.x, y: a.y, len: len }; }
        }
    }
    return best;
}

/**
 * Place a label inline ON a horizontal line segment, centred and breaking the
 * line. Tries the segment midpoint first, then slides left/right ALONG the
 * line to dodge other elements (never perpendicular, so it stays on the line).
 */
function _placeLabelInline(x0, x1, y, w, h, ignoreOwner) {
    var hw = w / 2, hh = h / 2, margin = 2;
    var lo = Math.min(x0, x1), hi = Math.max(x0, x1), midX = (lo + hi) / 2;
    var cands = [midX];
    var step = hw + 6;
    for (var d = step; d <= (hi - lo) / 2 + step; d += step) {
        cands.push(midX + d);
        cands.push(midX - d);
    }
    for (var i = 0; i < cands.length; i++) {
        var cx = cands[i];
        if (!_overlapsAny(cx - hw, y - hh, cx + hw, y + hh, margin, ignoreOwner)) {
            _addObstacle(cx - hw, y - hh, cx + hw, y + hh, 'label');
            return { x: cx, y: y };
        }
    }
    // Fallback: centre on the segment even if crowded (still on the line).
    _addObstacle(midX - hw, y - hh, midX + hw, y + hh, 'label');
    return { x: midX, y: y };
}

/**
 * Build an SVG path with rounded corners at bend points.
 */
function _buildRoundedPath(sections, radius) {
    var allPts = [];
    sections.forEach(function(sec) {
        allPts.push(sec.startPoint);
        if (sec.bendPoints) sec.bendPoints.forEach(function(bp) { allPts.push(bp); });
        allPts.push(sec.endPoint);
    });

    if (allPts.length < 2) return '';
    if (allPts.length === 2) {
        return 'M' + allPts[0].x + ',' + allPts[0].y +
               ' L' + allPts[1].x + ',' + allPts[1].y;
    }

    var d = 'M' + allPts[0].x + ',' + allPts[0].y;

    for (var i = 1; i < allPts.length - 1; i++) {
        var prev = allPts[i - 1];
        var curr = allPts[i];
        var next = allPts[i + 1];

        var dx1 = prev.x - curr.x, dy1 = prev.y - curr.y;
        var dx2 = next.x - curr.x, dy2 = next.y - curr.y;
        var d1 = Math.sqrt(dx1 * dx1 + dy1 * dy1);
        var d2 = Math.sqrt(dx2 * dx2 + dy2 * dy2);

        var r = Math.min(radius, d1 / 2, d2 / 2);
        if (r < 1) {
            d += ' L' + curr.x + ',' + curr.y;
            continue;
        }

        // Points on segments, distance r from the corner
        var p1x = curr.x + (dx1 / d1) * r;
        var p1y = curr.y + (dy1 / d1) * r;
        var p2x = curr.x + (dx2 / d2) * r;
        var p2y = curr.y + (dy2 / d2) * r;

        d += ' L' + p1x + ',' + p1y;
        d += ' Q' + curr.x + ',' + curr.y + ' ' + p2x + ',' + p2y;
    }

    d += ' L' + allPts[allPts.length - 1].x + ',' + allPts[allPts.length - 1].y;
    return d;
}

function _drawEdge(layer, edge, busH) {
    var props = edge.properties || {};
    var edgeType = props.edgeType || 'transmission';
    var color = props.color || T.edgeLine;
    var sections = edge.sections || [];
    if (sections.length === 0) return;

    // Verify connectivity
    var srcId = (edge.sources && edge.sources[0]) || '';
    var tgtId = (edge.targets && edge.targets[0]) || '';
    if (!busLayout[srcId] || !busLayout[tgtId]) return;

    var edgeOwner = edge.id || (srcId + '->' + tgtId);
    // Register this line's path as an obstacle (tagged with its own id) so
    // every OTHER label avoids it, while this line's own label may still sit
    // on it (the label pass ignores obstacles owned by its edge).
    _addEdgeObstacles(sections, edgeOwner);

    // Build SVG path with rounded corners
    var pathD = _buildRoundedPath(sections, 8);

    var g = layer.append('g').attr('class', 'edge edge-' + edgeType)
        .style('cursor', 'pointer');

    // Invisible wider hit area for easier click/hover
    g.append('path')
        .attr('class', 'edge-hit-area')
        .attr('d', pathD)
        .attr('fill', 'none')
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14);

    // Connection dots at bus bars
    var sec0 = sections[0];
    var secLast = sections[sections.length - 1];
    g.append('circle')
        .attr('cx', sec0.startPoint.x).attr('cy', sec0.startPoint.y)
        .attr('r', 5).attr('fill', color)
        .attr('stroke', _darken(color, 0.25)).attr('stroke-width', 1);
    g.append('circle')
        .attr('cx', secLast.endPoint.x).attr('cy', secLast.endPoint.y)
        .attr('r', 5).attr('fill', color)
        .attr('stroke', _darken(color, 0.25)).attr('stroke-width', 1);

    // Register connection dots as obstacles (owned by this edge)
    _addObstacle(sec0.startPoint.x - 6, sec0.startPoint.y - 6,
                 sec0.startPoint.x + 6, sec0.startPoint.y + 6, edgeOwner);
    _addObstacle(secLast.endPoint.x - 6, secLast.endPoint.y - 6,
                 secLast.endPoint.x + 6, secLast.endPoint.y + 6, edgeOwner);

    // Edge path. Vertical transformers draw their own stubs around the
    // symbol (below) so no line crosses over the windings.
    var strokeW = edgeType === 'transmission' ? 2.5 : 2;
    // Dashed for converters; dashed + red (colour set in Python) flags a
    // transmission line whose two buses are at DIFFERENT voltages (data error).
    var dash = props.voltageMismatch ? '6,4'
             : (edgeType === 'converter' ? '8,4' : 'none');
    if (!props.transformerVertical) {
        g.append('path')
            .attr('class', 'edge-path')
            .attr('d', pathD)
            .attr('fill', 'none')
            .attr('stroke', color)
            .attr('stroke-width', strokeW)
            .attr('stroke-dasharray', dash)
            .attr('stroke-linecap', 'round')
            .attr('stroke-linejoin', 'round');
    }

    // Midpoint for symbols and labels
    var mid = _edgeMidpoint(sections);

    // Transformer: two coupled windings sitting BETWEEN the two bars, always
    // vertical, with short stubs from each bar to a winding. The Python side
    // routes transformers as a clean vertical (shared X), so the symbol never
    // gets a line drawn across it.
    if (edgeType === 'transformer' && props.transformerVertical) {
        // Adjacent-level transformer: clean vertical between the two bars.
        var txx = sec0.startPoint.x;
        var yA = sec0.startPoint.y, yB = secLast.endPoint.y;
        var yTop = Math.min(yA, yB), yBot = Math.max(yA, yB);
        var tmid = (yTop + yBot) / 2;
        var r = 12, off = 8;
        var cTop = tmid - off, cBot = tmid + off;
        // Stubs: upper bar → top winding, bottom winding → lower bar.
        g.append('path').attr('class', 'edge-path')
            .attr('d', 'M' + txx + ',' + yTop + ' L' + txx + ',' + (cTop - r))
            .attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', strokeW).attr('stroke-linecap', 'round');
        g.append('path').attr('class', 'edge-path')
            .attr('d', 'M' + txx + ',' + (cBot + r) + ' L' + txx + ',' + yBot)
            .attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', strokeW).attr('stroke-linecap', 'round');
        g.append('circle').attr('cx', txx).attr('cy', cTop).attr('r', r)
            .attr('fill', T.bg).attr('stroke', color).attr('stroke-width', 2.5);
        g.append('circle').attr('cx', txx).attr('cy', cBot).attr('r', r)
            .attr('fill', T.bg).attr('stroke', color).attr('stroke-width', 2.5);
        _addObstacle(txx - r - 2, cTop - r - 2, txx + r + 2, cBot + r + 2, edgeOwner);
        mid = { x: txx, y: tmid };   // label sits beside the symbol
    } else if (edgeType === 'transformer') {
        // Non-adjacent transformer routed in a side channel: place the two
        // coupled windings on the channel's vertical run (the polyline is
        // already drawn) so the symbol sits on the line, never on a bar.
        var r2 = 11, off2 = 8;
        g.append('circle').attr('cx', mid.x).attr('cy', mid.y - off2).attr('r', r2)
            .attr('fill', T.bg).attr('stroke', color).attr('stroke-width', 2.5);
        g.append('circle').attr('cx', mid.x).attr('cy', mid.y + off2).attr('r', r2)
            .attr('fill', T.bg).attr('stroke', color).attr('stroke-width', 2.5);
        _addObstacle(mid.x - r2 - 2, mid.y - off2 - r2 - 2, mid.x + r2 + 2, mid.y + off2 + r2 + 2, edgeOwner);
    }

    // Converter IEC symbol (square with ~/= or ~/Hz)
    if (edgeType === 'converter') {
        var cs = 20;
        g.append('rect').attr('x', mid.x - cs).attr('y', mid.y - cs)
            .attr('width', cs * 2).attr('height', cs * 2).attr('rx', 3)
            .attr('fill', T.bg).attr('stroke', color).attr('stroke-width', 2);
        g.append('line').attr('x1', mid.x - cs).attr('y1', mid.y + cs)
            .attr('x2', mid.x + cs).attr('y2', mid.y - cs)
            .attr('stroke', color).attr('stroke-width', 1.2);
        var isAcdc = (props.elementType === 'acdc_converter');
        g.append('text').attr('x', mid.x - cs * 0.4).attr('y', mid.y - cs * 0.2)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-size', '14px').attr('font-weight', '700').attr('fill', color)
            .text('~');
        g.append('text').attr('x', mid.x + cs * 0.4).attr('y', mid.y + cs * 0.25)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-size', isAcdc ? '12px' : '10px').attr('font-weight', '700')
            .attr('fill', color).text(isAcdc ? '=' : 'Hz');
        // Register converter symbol as obstacle
        _addObstacle(mid.x - cs - 2, mid.y - cs - 2, mid.x + cs + 2, mid.y + cs + 2, edgeOwner);
    }

    // Defer the label to the final pass (after every bar/equipment/edge is a
    // registered obstacle) so it can avoid ALL elements while still resting on
    // its own line.
    if (labelsVisible && props.label) {
        _pendingLabels.push({
            g: g, props: props, sections: sections, mid: mid,
            edgeType: edgeType, color: color, owner: edgeOwner,
        });
    }

    // Interaction
    var typeNames = {
        'transmission': 'Line', 'transformer': 'Transformer',
        'converter': (props.elementType === 'acdc_converter' ? 'AC/DC Converter' : 'Freq. Converter'),
    };
    g.on('click', function(event) {
        event.stopPropagation();
        if (props.elementType) _selectElement(props.elementType, props.elementId, g);
    }).on('mouseenter', function(event) {
        _showTooltip(event, (typeNames[edgeType] || 'Edge') + ' ' + (props.elementId || ''), props.label || '');
    }).on('mouseleave', _hideTooltip);

    if (props.elementType) {
        elementRegistry[props.elementType + ':' + props.elementId] = { group: g, props: props };
    }
}


// ══════════════════════════════════════════════════════════════════
// Legend (fixed overlay — does not zoom/pan)
// ══════════════════════════════════════════════════════════════════

/** Readable labels for equipment symbol types. */
var _equipLabels = {
    'gen-renewable':    'Renewable Gen.',
    'gen-nonrenewable': 'Non-Renew. Gen.',
    'battery':          'Battery',
    'load':             'Load',
    'electrolyzer':     'Electrolyzer',
    'acdc-converter':   'AC/DC Converter',
    'freq-converter':   'Freq. Converter',
};

/** Readable labels and visual specs for edge types. */
var _edgeSpecs = {
    'transmission': { label: 'Transmission Line', dash: 'none', sw: 2.5, sym: null },
    'transformer':  { label: 'Transformer',       dash: 'none', sw: 2,   sym: 'trafo' },
    'converter':    { label: 'Converter',          dash: '6,3',  sw: 2,   sym: 'conv' },
};

/**
 * Draw a fixed legend in the upper-right corner of the SVG viewport.
 * Appended to `svg` (not rootGroup), so it stays in place when zooming.
 * Only shows symbols/edges/voltages that are present in the current diagram.
 */
function _drawLegend(data) {
    svg.select('.sld-legend').remove();
    if (!labelsVisible) return;

    // ── Scan diagram for present element types ──
    var equipTypes = [];
    var equipSeen = {};
    var busEquip = data.busEquipment || {};
    Object.keys(busEquip).forEach(function(bid) {
        busEquip[bid].forEach(function(eq) {
            if (!equipSeen[eq.symbolType]) {
                equipSeen[eq.symbolType] = true;
                equipTypes.push({ sym: eq.symbolType, color: eq.color || T.edgeLine });
            }
        });
    });

    var edgeTypes = [];
    var edgeSeen = {};
    var edges = (data.elkGraph && data.elkGraph.edges) || [];
    edges.forEach(function(e) {
        var p = e.properties || {};
        var et = p.edgeType || 'transmission';
        if (!edgeSeen[et]) {
            edgeSeen[et] = true;
            edgeTypes.push({ type: et, color: p.color || T.edgeLine });
        }
    });

    var voltages = [];
    var seenKv = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        if (bl.voltageKv && !seenKv[bl.voltageKv]) {
            seenKv[bl.voltageKv] = true;
            voltages.push({ kv: bl.voltageKv, color: bl.color });
        }
    });
    voltages.sort(function(a, b) { return b.kv - a.kv; });

    // Nothing to show
    if (equipTypes.length === 0 && edgeTypes.length === 0 && voltages.length === 0) return;

    // ── Layout constants ──
    var rowH = 18;
    var padX = 10, padY = 8;
    var symColW = 32;       // width reserved for symbol/swatch column
    var textColW = 100;     // width for label text
    var legendW = padX + symColW + 4 + textColW + padX;
    var cw = svg.node().clientWidth || 800;

    // Count rows: title + sections
    var nRows = 0;
    var hasEquip = equipTypes.length > 0;
    var hasEdges = edgeTypes.length > 0;
    var hasVolts = voltages.length > 0;
    if (hasEquip) nRows += 1 + equipTypes.length;   // header + items
    if (hasEdges) nRows += 1 + edgeTypes.length;
    if (hasVolts) nRows += 1 + voltages.length;

    var titleH = 20;
    var legendH = titleH + nRows * rowH + padY * 2;
    var lx = cw - legendW - 12;
    var ly = 12;

    var lg = svg.append('g').attr('class', 'sld-legend')
        .attr('transform', 'translate(' + lx + ',' + ly + ')');

    // ── Background ──
    lg.append('rect')
        .attr('x', -1).attr('y', -1)
        .attr('width', legendW + 2).attr('height', legendH + 2)
        .attr('rx', 6).attr('fill', 'rgba(0,0,0,0.03)').attr('stroke', 'none');
    lg.append('rect')
        .attr('width', legendW).attr('height', legendH)
        .attr('rx', 6)
        .attr('fill', 'rgba(255,255,255,0.92)')
        .attr('stroke', T.groupBorder)
        .attr('stroke-width', 1);

    // ── Title ──
    lg.append('text')
        .attr('x', padX).attr('y', titleH - 4)
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '10px').attr('font-weight', '700')
        .attr('fill', T.groupTitle)
        .text('Legend');

    var curY = titleH + padY;

    // ── Helper: section header ──
    function _sectionHeader(label) {
        lg.append('text')
            .attr('x', padX).attr('y', curY + 12)
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '700')
            .attr('fill', T.groupTitle).attr('opacity', 0.6)
            .attr('text-transform', 'uppercase')
            .text(label);
        curY += rowH;
    }

    // ── Section: Equipment ──
    if (hasEquip) {
        _sectionHeader('Equipment');
        equipTypes.forEach(function(et) {
            var symCx = padX + symColW / 2;
            var symCy = curY + rowH / 2;
            var symSize = 16;

            // Draw miniature symbol
            var symG = lg.append('g')
                .attr('transform', 'translate(' + symCx + ',' + symCy + ')');
            var symFn = sldSymbols[et.sym];
            if (symFn) {
                symFn(symG, symSize, et.color, 'down');
            } else {
                symG.append('circle').attr('r', symSize / 2 - 1)
                    .attr('fill', '#fff').attr('stroke', et.color).attr('stroke-width', 1.5);
            }

            // Label
            lg.append('text')
                .attr('x', padX + symColW + 4).attr('y', symCy + 3)
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px')
                .attr('fill', T.labelText)
                .text(_equipLabels[et.sym] || et.sym);

            curY += rowH;
        });
    }

    // ── Section: Connections ──
    if (hasEdges) {
        _sectionHeader('Connections');
        edgeTypes.forEach(function(et) {
            var spec = _edgeSpecs[et.type] || _edgeSpecs['transmission'];
            var lx1 = padX + 2;
            var lx2 = padX + symColW - 2;
            var lcy = curY + rowH / 2;

            // Line sample
            lg.append('line')
                .attr('x1', lx1).attr('y1', lcy)
                .attr('x2', lx2).attr('y2', lcy)
                .attr('stroke', et.color)
                .attr('stroke-width', spec.sw)
                .attr('stroke-dasharray', spec.dash)
                .attr('stroke-linecap', 'round');

            // Mini IEC symbol on the line
            var midX = (lx1 + lx2) / 2;
            if (spec.sym === 'trafo') {
                var mr = 5;
                lg.append('circle').attr('cx', midX - 3).attr('cy', lcy).attr('r', mr)
                    .attr('fill', T.bg).attr('stroke', et.color).attr('stroke-width', 1.5);
                lg.append('circle').attr('cx', midX + 3).attr('cy', lcy).attr('r', mr)
                    .attr('fill', T.bg).attr('stroke', et.color).attr('stroke-width', 1.5);
            } else if (spec.sym === 'conv') {
                var cs = 5;
                lg.append('rect')
                    .attr('x', midX - cs).attr('y', lcy - cs)
                    .attr('width', cs * 2).attr('height', cs * 2).attr('rx', 1)
                    .attr('fill', T.bg).attr('stroke', et.color).attr('stroke-width', 1.2);
            }

            // Label
            lg.append('text')
                .attr('x', padX + symColW + 4).attr('y', lcy + 3)
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px')
                .attr('fill', T.labelText)
                .text(spec.label);

            curY += rowH;
        });
    }

    // ── Section: Voltage Levels ──
    if (hasVolts) {
        _sectionHeader('Voltage Levels');
        voltages.forEach(function(v) {
            var lx1 = padX + 4;
            var lx2 = padX + symColW - 4;
            var lcy = curY + rowH / 2;

            // Thick colored swatch (bus-bar style)
            lg.append('line')
                .attr('x1', lx1).attr('y1', lcy)
                .attr('x2', lx2).attr('y2', lcy)
                .attr('stroke', v.color)
                .attr('stroke-width', 4)
                .attr('stroke-linecap', 'round');

            // Label
            lg.append('text')
                .attr('x', padX + symColW + 4).attr('y', lcy + 3)
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px').attr('font-weight', '600')
                .attr('fill', v.color)
                .text(v.kv + ' kV');

            curY += rowH;
        });
    }

    // Store legend bounds for positioning other panels below
    _legendBounds = { x: lx, y: ly, w: legendW, h: legendH };
}


// ══════════════════════════════════════════════════════════════════
// IEC Symbol Library
// ══════════════════════════════════════════════════════════════════

var sldSymbols = {

    'gen-renewable': function(g, s, color) {
        var r = s / 2 - 2;
        g.append('circle').attr('class', 'symbol').attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('text').attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Arial,sans-serif').attr('font-weight', '700')
            .attr('font-size', s * 0.42).attr('fill', color).text('G');
        var w2 = r * 0.45;
        g.append('path')
            .attr('d', 'M' + (-w2) + ',' + (r * 0.55) +
                ' Q' + (-w2 / 2) + ',' + (r * 0.3) + ' 0,' + (r * 0.55) +
                ' Q' + (w2 / 2) + ',' + (r * 0.8) + ' ' + w2 + ',' + (r * 0.55))
            .attr('fill', 'none').attr('stroke', color).attr('stroke-width', 1.2).attr('opacity', 0.45);
    },

    'gen-nonrenewable': function(g, s, color) {
        var r = s / 2 - 2;
        g.append('circle').attr('class', 'symbol').attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('text').attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Arial,sans-serif').attr('font-weight', '700')
            .attr('font-size', s * 0.42).attr('fill', color).text('G');
    },

    'battery': function(g, s, color) {
        var r = s / 2 - 2;
        var lw = s * 0.32, sw = s * 0.18, gap = s * 0.07;
        g.append('circle').attr('class', 'symbol').attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('line').attr('x1', -r * 0.6).attr('y1', 0).attr('x2', -gap).attr('y2', 0)
            .attr('stroke', color).attr('stroke-width', 1.5);
        g.append('line').attr('x1', gap).attr('y1', 0).attr('x2', r * 0.6).attr('y2', 0)
            .attr('stroke', color).attr('stroke-width', 1.5);
        g.append('line').attr('x1', -gap).attr('y1', -lw).attr('x2', -gap).attr('y2', lw)
            .attr('stroke', color).attr('stroke-width', 3);
        g.append('line').attr('x1', gap).attr('y1', -sw).attr('x2', gap).attr('y2', sw)
            .attr('stroke', color).attr('stroke-width', 3);
    },

    'transformer': function(g, s, color) {
        var r = s * 0.26, off = s * 0.14;
        g.append('circle').attr('class', 'symbol').attr('cx', -off).attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('circle').attr('class', 'symbol').attr('cx', off).attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
    },

    'load': function(g, s, color, stubDir) {
        // IEC load symbol: tip at top (toward stub/bus), base at bottom (away).
        // Full-size so it connects to the stub like other symbols.
        var r = s / 2 - 2;       // same radius as generator circles
        var w2 = r * 0.65;       // half-width of base

        // Default: tip at top (-r), base at bottom (+r)  → for horizontal bus
        // For vertical bus (stub from left): rotate 90° CW → tip faces left
        var rotation = (stubDir === 'right') ? 90 : 0;
        var triG = g.append('g');
        if (rotation !== 0) triG.attr('transform', 'rotate(' + rotation + ')');

        // Base line at bottom (away from bus)
        triG.append('line')
            .attr('x1', -w2).attr('y1', r)
            .attr('x2', w2).attr('y2', r)
            .attr('stroke', color).attr('stroke-width', 2.5);

        // Filled triangle: tip at top (-r), base at bottom (+r)
        triG.append('polygon').attr('class', 'symbol')
            .attr('points', '0,' + (-r) + ' ' + (-w2) + ',' + r + ' ' + w2 + ',' + r)
            .attr('fill', color).attr('opacity', 0.8);
    },

    'electrolyzer': function(g, s, color) {
        var r = s / 2 - 2;
        g.append('circle').attr('class', 'symbol').attr('r', r)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('text').attr('text-anchor', 'middle').attr('y', -s * 0.04)
            .attr('dominant-baseline', 'central')
            .attr('font-family', 'Arial,sans-serif').attr('font-weight', '700')
            .attr('font-size', s * 0.30).attr('fill', color).text('H');
        g.append('text').attr('x', s * 0.14).attr('y', s * 0.12)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Arial,sans-serif').attr('font-weight', '700')
            .attr('font-size', s * 0.18).attr('fill', color).text('2');
    },

    'acdc-converter': function(g, s, color) {
        var h = s / 2 - 2;
        g.append('rect').attr('class', 'symbol').attr('x', -h).attr('y', -h)
            .attr('width', h * 2).attr('height', h * 2).attr('rx', 3)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('line').attr('x1', -h).attr('y1', h).attr('x2', h).attr('y2', -h)
            .attr('stroke', color).attr('stroke-width', 1.2);
        g.append('text').attr('x', -h * 0.38).attr('y', -h * 0.22)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-size', s * 0.32).attr('font-weight', '700').attr('fill', color).text('~');
        g.append('text').attr('x', h * 0.38).attr('y', h * 0.28)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-size', s * 0.26).attr('font-weight', '700').attr('fill', color).text('=');
    },

    'freq-converter': function(g, s, color) {
        var h = s / 2 - 2;
        g.append('rect').attr('class', 'symbol').attr('x', -h).attr('y', -h)
            .attr('width', h * 2).attr('height', h * 2).attr('rx', 3)
            .attr('fill', '#fff').attr('stroke', color).attr('stroke-width', 2.5);
        g.append('text').attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Arial,sans-serif').attr('font-weight', '700')
            .attr('font-size', s * 0.32).attr('fill', color).text('Hz');
    }
};


// ══════════════════════════════════════════════════════════════════
// Selection
// ══════════════════════════════════════════════════════════════════

function _selectElement(type, id, gElem) {
    clearSelection();
    selectedKey = type + ':' + id;
    gElem.classed('selected', true);
    gElem.selectAll('.symbol, .bus-bar-line, .bus-bar-rect, .edge-path')
        .attr('filter', 'drop-shadow(0 0 6px ' + T.selGlow + ')');
    if (sldBridge) sldBridge.on_element_selected(type, id);
}

function clearSelection() {
    if (selectedKey && elementRegistry[selectedKey]) {
        var e = elementRegistry[selectedKey];
        e.group.classed('selected', false);
        e.group.selectAll('.symbol, .bus-bar-line, .bus-bar-rect, .edge-path').attr('filter', null);
    }
    selectedKey = null;
}

function highlightElement(type, id) {
    clearSelection();
    var key = type + ':' + id;
    var e = elementRegistry[key];
    if (e) {
        selectedKey = key;
        e.group.classed('selected', true);
        e.group.selectAll('.symbol, .bus-bar-line, .bus-bar-rect, .edge-path')
            .attr('filter', 'drop-shadow(0 0 6px ' + T.selGlow + ')');
    }
}


// ══════════════════════════════════════════════════════════════════
// Zoom / Fit — infinite canvas, diagram fills the panel
// ══════════════════════════════════════════════════════════════════

/**
 * Compute diagram bounding box and draw an "infinite" dot grid.
 * The solid background + grid rects are very large so there are
 * never visible edges when zooming/panning.
 */
function _updateDiagramBounds(gGrid) {
    gGrid.selectAll('*').remove();
    var bb = rootGroup.node().getBBox();
    if (bb.width === 0 || bb.height === 0) {
        diagramBounds = { x: 0, y: 0, w: 100, h: 100 };
    } else {
        var pad = 60;
        diagramBounds = {
            x: bb.x - pad,
            y: bb.y - pad,
            w: bb.width + 2 * pad,
            h: bb.height + 2 * pad,
        };
    }

    // Background provided by CSS (html, body, svg all have background: #FFFFFF)

    // Permissive initial constraints — fitView() tightens scaleExtent dynamically
    zoomBehavior
        .scaleExtent([0.05, 5])
        .translateExtent([
            [diagramBounds.x - 50, diagramBounds.y - 50],
            [diagramBounds.x + diagramBounds.w + 50, diagramBounds.y + diagramBounds.h + 50],
        ]);
    svg.call(zoomBehavior);
}

/**
 * Zoom/pan so the full diagram fills the panel with padding.
 * Also updates scaleExtent so the user cannot zoom out past
 * the fit-to-diagram level.
 */
function fitView(animate) {
    if (!rootGroup || !svg || !diagramBounds) return;
    var db = diagramBounds;
    if (db.w === 0 || db.h === 0) return;

    var cw = svg.node().clientWidth;
    var ch = svg.node().clientHeight;
    // Abort if container has no dimensions yet (Qt paint pending)
    if (!cw || !ch || cw < 10 || ch < 10) return;
    var sc = Math.min(cw / db.w, ch / db.h);

    // Clamp zoom-out to this fit scale (diagram always fills panel)
    zoomBehavior.scaleExtent([Math.max(0.05, sc), 5]);

    var tx = (cw - db.w * sc) / 2 - db.x * sc;
    var ty = (ch - db.h * sc) / 2 - db.y * sc;

    var transform = d3.zoomIdentity.translate(tx, ty).scale(sc);
    if (animate === false) {
        svg.call(zoomBehavior.transform, transform);
    } else {
        svg.transition().duration(350).call(zoomBehavior.transform, transform);
    }
}


// ══════════════════════════════════════════════════════════════════
// Labels / Theme
// ══════════════════════════════════════════════════════════════════

function toggleLabels(show) {
    labelsVisible = show;
    if (currentData && currentLayouted) {
        render(currentData, currentLayouted);
        fitView(false);  // Reapply current zoom after re-render
    }
}

function updateTheme(json) {
    try {
        var c = JSON.parse(json);
        for (var k in c) if (T.hasOwnProperty(k)) T[k] = c[k];
        document.body.style.background = T.bg;
        if (currentData && currentLayouted) {
            render(currentData, currentLayouted);
            fitView(false);
        }
    } catch(e) {}
}


// ══════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════

function _clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }


// ══════════════════════════════════════════════════════════════════
// Global obstacle registry for label collision avoidance
// ══════════════════════════════════════════════════════════════════

// Spatial grid for fast obstacle collision queries
var _obstacleGrid = {};   // "gx,gy" → [{x1,y1,x2,y2}, ...]
var _GRID_SIZE = 100;     // pixels per cell

/** Register an axis-aligned rectangle as an obstacle. */
function _addObstacle(x1, y1, x2, y2, owner) {
    var ob = {
        x1: Math.min(x1, x2), y1: Math.min(y1, y2),
        x2: Math.max(x1, x2), y2: Math.max(y1, y2),
        owner: owner   // optional tag so an element's own label can ignore it
    };
    var gx0 = Math.floor(ob.x1 / _GRID_SIZE);
    var gy0 = Math.floor(ob.y1 / _GRID_SIZE);
    var gx1 = Math.floor(ob.x2 / _GRID_SIZE);
    var gy1 = Math.floor(ob.y2 / _GRID_SIZE);
    for (var gx = gx0; gx <= gx1; gx++) {
        for (var gy = gy0; gy <= gy1; gy++) {
            var k = gx + ',' + gy;
            if (!_obstacleGrid[k]) _obstacleGrid[k] = [];
            _obstacleGrid[k].push(ob);
        }
    }
}

/** Register edge path segments as thin obstacle boxes (tagged with owner). */
function _addEdgeObstacles(sections, owner) {
    var pad = 5;
    sections.forEach(function(sec) {
        var pts = [sec.startPoint];
        if (sec.bendPoints) sec.bendPoints.forEach(function(bp) { pts.push(bp); });
        pts.push(sec.endPoint);
        for (var i = 1; i < pts.length; i++) {
            _addObstacle(
                Math.min(pts[i-1].x, pts[i].x) - pad,
                Math.min(pts[i-1].y, pts[i].y) - pad,
                Math.max(pts[i-1].x, pts[i].x) + pad,
                Math.max(pts[i-1].y, pts[i].y) + pad,
                owner
            );
        }
    });
}

/** Register a bus bar (both orientations) as obstacle. */
function _addBusObstacle(bl, busH) {
    var isVert = bl.orientation === 90;
    var barLen = bl.barLen;
    if (isVert) {
        _addObstacle(bl.x - 2, bl.y - 2, bl.x + busH + 2, bl.y + barLen + 2);
    } else {
        _addObstacle(bl.x - 2, bl.y - 2, bl.x + barLen + 2, bl.y + busH + 2);
    }
}

/** Check whether a rectangle overlaps ANY registered obstacle (spatial grid).
 *  Obstacles tagged with ``ignoreOwner`` are skipped, so an element's own
 *  geometry doesn't block its own label. */
function _overlapsAny(lx1, ly1, lx2, ly2, margin, ignoreOwner) {
    var m = margin || 0;
    var gx0 = Math.floor((lx1 - m) / _GRID_SIZE);
    var gy0 = Math.floor((ly1 - m) / _GRID_SIZE);
    var gx1 = Math.floor((lx2 + m) / _GRID_SIZE);
    var gy1 = Math.floor((ly2 + m) / _GRID_SIZE);
    for (var gx = gx0; gx <= gx1; gx++) {
        for (var gy = gy0; gy <= gy1; gy++) {
            var cell = _obstacleGrid[gx + ',' + gy];
            if (!cell) continue;
            for (var i = 0; i < cell.length; i++) {
                var o = cell[i];
                if (ignoreOwner != null && o.owner === ignoreOwner) continue;
                if (lx2 + m > o.x1 && lx1 - m < o.x2 &&
                    ly2 + m > o.y1 && ly1 - m < o.y2) {
                    return true;
                }
            }
        }
    }
    return false;
}

/**
 * Find the best non-overlapping position for a label.
 * Tries the preferred position first, then several offsets.
 * Registers the placed label as an obstacle for subsequent labels.
 */
function _placeLabel(prefX, prefY, w, h, ignoreOwner) {
    var margin = 2;
    var hw = w / 2, hh = h / 2;

    // Try preferred position
    if (!_overlapsAny(prefX - hw, prefY - hh, prefX + hw, prefY + hh, margin, ignoreOwner)) {
        _addObstacle(prefX - hw, prefY - hh, prefX + hw, prefY + hh, 'label');
        return { x: prefX, y: prefY };
    }

    // Candidate offsets — small increments to stay close to the element
    var sy = hh + 3;   // vertical step
    var sx = hw + 3;   // horizontal step
    var offsets = [
        { dx: 0, dy: -sy },        // above
        { dx: 0, dy: sy },         // below
        { dx: sx, dy: 0 },         // right
        { dx: -sx, dy: 0 },        // left
        { dx: sx, dy: -sy },       // upper-right
        { dx: -sx, dy: -sy },      // upper-left
        { dx: sx, dy: sy },        // lower-right
        { dx: -sx, dy: sy },       // lower-left
        { dx: 0, dy: -sy * 2 },    // further above
        { dx: 0, dy: sy * 2 },     // further below
    ];

    for (var i = 0; i < offsets.length; i++) {
        var tx = prefX + offsets[i].dx;
        var ty = prefY + offsets[i].dy;
        if (!_overlapsAny(tx - hw, ty - hh, tx + hw, ty + hh, margin, ignoreOwner)) {
            _addObstacle(tx - hw, ty - hh, tx + hw, ty + hh, 'label');
            return { x: tx, y: ty };
        }
    }

    // Fallback: use preferred position
    _addObstacle(prefX - hw, prefY - hh, prefX + hw, prefY + hh, 'label');
    return { x: prefX, y: prefY };
}

/**
 * Darken a hex color by the given amount (0–1).  Memoized.
 */
var _darkenCache = {};
function _darken(hex, amount) {
    if (!hex || hex.length < 7) return hex || '#000';
    var key = hex + '|' + amount;
    if (_darkenCache[key]) return _darkenCache[key];
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    r = Math.max(0, Math.round(r * (1 - amount)));
    g = Math.max(0, Math.round(g * (1 - amount)));
    b = Math.max(0, Math.round(b * (1 - amount)));
    var result = '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
    _darkenCache[key] = result;
    return result;
}

function _showEmpty(show) {
    document.getElementById('empty-message').style.display = show ? 'block' : 'none';
    if (show) {
        var loadEl = document.getElementById('sld-loading');
        if (loadEl) loadEl.style.display = 'none';
    }
    if (show && rootGroup) rootGroup.selectAll('*').remove();
}

function _showLoading(show) {
    var el = document.getElementById('sld-loading');
    if (el) el.style.display = show ? 'block' : 'none';
}

function _showTooltip(event, label, sub) {
    var tt = document.getElementById('sld-tooltip');
    tt.querySelector('.tt-label').textContent = label;
    tt.querySelector('.tt-sub').textContent = sub || '';
    tt.style.display = 'block';
    tt.style.left = (event.clientX + 14) + 'px';
    tt.style.top = (event.clientY - 12) + 'px';
}

function _hideTooltip() {
    document.getElementById('sld-tooltip').style.display = 'none';
}

function _trunc(s, n) {
    return (!s) ? '' : (s.length > n ? s.substring(0, n - 1) + '\u2026' : s);
}


// ══════════════════════════════════════════════════════════════════
// Operational overlay — power flow, generation, prices, system info
// ══════════════════════════════════════════════════════════════════

/** Utilization color: green → yellow → red. */
function _utilColor(ratio) {
    if (ratio < 0) ratio = 0;
    if (ratio > 1) ratio = 1;
    if (ratio < 0.7) return '#27AE60';
    if (ratio < 0.9) return '#F39C12';
    return '#E74C3C';
}

/** Nodal price color: blue (low) → white (avg) → red (high). */
function _priceColor(price, minP, maxP) {
    if (maxP <= minP) return '#95A5A6';
    var t = (price - minP) / (maxP - minP);  // 0..1
    // blue → white → red  (t=0 blue, t=0.5 white, t=1 red)
    if (t < 0.5) {
        var s = t / 0.5;  // 0..1
        var r = Math.round(52 + s * (255 - 52));
        var g = Math.round(152 + s * (255 - 152));
        var b = Math.round(219 + s * (255 - 219));
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    } else {
        var s = (t - 0.5) / 0.5;
        var r = Math.round(255);
        var g = Math.round(255 - s * (255 - 74));
        var b = Math.round(255 - s * (255 - 60));
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }
}

/** Format MW value for display. */
function _fmtMW(v) {
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + ' GW';
    if (Math.abs(v) >= 1) return v.toFixed(0) + ' MW';
    return v.toFixed(1) + ' MW';
}

/**
 * Draw/update the operational overlay on the SLD.
 * Called from Python via sld_widget.update_operational_data().
 *
 * @param {string} jsonStr - JSON snapshot from SldResultsLoader.get_timestep()
 */
function updateOperationalData(jsonStr) {
    var ops;
    try { ops = JSON.parse(jsonStr); } catch(e) {
        console.error('SLD ops: bad JSON', e); return;
    }
    currentOpsData = ops;

    // Remove previous overlay
    rootGroup.selectAll('.layer-ops').remove();
    svg.selectAll('.ops-info-bar').remove();

    // Create overlay layer ON TOP of everything
    var gOps = rootGroup.append('g').attr('class', 'layer-ops');

    // ── Equipment overlays (generators, batteries, loads) ──
    _drawEquipmentOverlay(gOps, ops);

    // ── Edge flow overlays ──
    _drawEdgeFlowOverlay(gOps, ops);

    // ── Nodal price badges ──
    _drawPriceBadges(gOps, ops);

    // ── Reserve badges (Level 1) ──
    _drawReserveBadges(gOps, ops);

    // ── Voltage angle labels (Level 1) ──
    _drawVoltageAngles(gOps, ops);

    // ── AC Power Flow overlays (only when power_flow data present) ──
    _drawVoltageBadges(gOps, ops);
    _drawReactivePowerOverlay(gOps, ops);
    _drawLineLossLabels(gOps, ops);
    _drawShortCircuitBadges(gOps, ops);
    _drawPowerFlowStatusBadge(ops);

    // ── Frequency gauge (Level 3) ──
    _drawFrequencyGauge(gOps, ops);

    // ── System info bar (fixed, outside rootGroup) ──
    _drawSystemInfoBar(ops);
}

/** Remove operational overlay. */
function clearOperationalData() {
    currentOpsData = null;
    if (rootGroup) {
        rootGroup.selectAll('.layer-ops').remove();
        rootGroup.selectAll('.layer-contingency').remove();
    }
    if (svg) {
        svg.selectAll('.ops-info-bar').remove();
        svg.selectAll('.freq-gauge').remove();
        svg.selectAll('.contingency-badge').remove();
        svg.selectAll('.pf-status-badge').remove();
    }
}

/** Draw utilization bars and value labels on generators, batteries, loads. */
function _drawEquipmentOverlay(gOps, ops) {
    var gens = ops.generators || {};
    var bats = ops.batteries || {};
    var loads = ops.loads || {};

    // Iterate over registered equipment elements
    Object.keys(elementRegistry).forEach(function(key) {
        var entry = elementRegistry[key];
        var parts = key.split(':');
        var eType = parts[0];
        var eId = parts[1];

        // Find the equipment group's transform to get position
        var group = entry.group;
        if (!group || !group.node()) return;
        var transform = group.attr('transform');
        if (!transform) return;
        var match = transform.match(/translate\(\s*([\d.e+-]+)\s*,\s*([\d.e+-]+)\s*\)/);
        if (!match) return;
        var cx = parseFloat(match[1]);
        var cy = parseFloat(match[2]);

        var barData = null;   // { ratio, label, color }
        var barW = 32, barH = 5;

        if (eType === 'generator' && gens[eId]) {
            var g = gens[eId];
            var ratio = g.capacity_mw > 0 ? g.output_mw / g.capacity_mw : 0;
            barData = { ratio: ratio, label: _fmtMW(g.output_mw), color: _utilColor(ratio) };
            // Curtailment indicator
            if (g.curtailment_mw > 0.5) {
                gOps.append('circle')
                    .attr('cx', cx).attr('cy', cy).attr('r', 22)
                    .attr('fill', 'none').attr('stroke', '#E74C3C')
                    .attr('stroke-width', 2).attr('stroke-dasharray', '4,3')
                    .attr('opacity', 0.8);
            }
            // Generator status indicators (Level 1)
            if (g.status === 0) {
                // Offline: gray overlay
                gOps.append('circle')
                    .attr('cx', cx).attr('cy', cy).attr('r', 18)
                    .attr('fill', '#95A5A6').attr('opacity', 0.4);
                gOps.append('text')
                    .attr('x', cx).attr('y', cy + 1)
                    .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                    .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                    .attr('font-size', '8px').attr('font-weight', '700')
                    .attr('fill', '#2C3E50').text('OFF');
                barData = null;  // Don't draw utilization bar for offline generators
            } else if (g.is_startup) {
                // Startup: green upward arrow
                gOps.append('text')
                    .attr('x', cx + 18).attr('y', cy - 12)
                    .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                    .attr('font-size', '14px').attr('fill', '#27AE60').text('\u25B2');
            } else if (g.is_shutdown) {
                // Shutdown: red downward arrow
                gOps.append('text')
                    .attr('x', cx + 18).attr('y', cy - 12)
                    .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                    .attr('font-size', '14px').attr('fill', '#E74C3C').text('\u25BC');
            }
        } else if (eType === 'battery' && bats[eId]) {
            var b = bats[eId];
            var net = b.discharge_mw - b.charge_mw;
            var socRatio = b.capacity_mwh > 0 ? b.soc_mwh / b.capacity_mwh : 0;
            var lbl = net > 0.1 ? '+' + _fmtMW(net) : net < -0.1 ? _fmtMW(net) : 'Idle';
            barData = { ratio: socRatio, label: lbl + ' (' + (socRatio * 100).toFixed(0) + '%)', color: _utilColor(1 - socRatio) };
        } else if (eType === 'load' && loads[eId]) {
            var l = loads[eId];
            barData = { ratio: 1, label: _fmtMW(l.demand_mw), color: '#E67E22' };
            if (l.shed_mw > 0.1) {
                barData.color = '#E74C3C';
                barData.label += ' (shed: ' + _fmtMW(l.shed_mw) + ')';
            }
        }

        if (!barData) return;

        // Draw small horizontal utilization bar below equipment symbol
        var barY = cy + 22;
        gOps.append('rect')
            .attr('x', cx - barW / 2).attr('y', barY)
            .attr('width', barW).attr('height', barH).attr('rx', 2)
            .attr('fill', '#E0E0E0').attr('opacity', 0.6);
        gOps.append('rect')
            .attr('x', cx - barW / 2).attr('y', barY)
            .attr('width', barW * Math.min(barData.ratio, 1)).attr('height', barH).attr('rx', 2)
            .attr('fill', barData.color).attr('opacity', 0.85);

        // Value label below bar
        gOps.append('text')
            .attr('x', cx).attr('y', barY + barH + 11)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'auto')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '9px').attr('font-weight', '600')
            .attr('fill', barData.color)
            .text(barData.label);
    });
}

/** Draw flow arrows and utilization coloring on transmission edges. */
function _drawEdgeFlowOverlay(gOps, ops) {
    var lines = ops.lines || {};

    Object.keys(lines).forEach(function(edgeId) {
        var flow = lines[edgeId];
        var entry = elementRegistry['line:' + edgeId.replace('edge_', '')];
        if (!entry) return;

        var group = entry.group;
        if (!group || !group.node()) return;

        // Find the path element inside this edge group
        var pathEl = group.select('path.edge-path');
        if (pathEl.empty()) pathEl = group.select('path');
        if (pathEl.empty()) return;

        var cap = flow.capacity_mw || 1;
        var util = Math.abs(flow.flow_mw) / cap;
        var color = _utilColor(util);

        // Color the edge path by utilization
        pathEl.attr('stroke', color).attr('stroke-width', Math.max(2.5, 1.5 + util * 3));

        // Flow direction arrow marker at midpoint
        var pathNode = pathEl.node();
        if (pathNode && pathNode.getTotalLength) {
            var totalLen = pathNode.getTotalLength();
            var midPt = pathNode.getPointAtLength(totalLen / 2);
            var nearPt = pathNode.getPointAtLength(totalLen / 2 + 2);
            var angle = Math.atan2(nearPt.y - midPt.y, nearPt.x - midPt.x) * 180 / Math.PI;
            if (flow.flow_mw < 0) angle += 180;

            // Arrow head
            var arrSize = 7;
            gOps.append('polygon')
                .attr('points', '0,' + (-arrSize) + ' ' + (arrSize * 1.5) + ',0 0,' + arrSize)
                .attr('transform', 'translate(' + midPt.x + ',' + midPt.y + ') rotate(' + angle + ')')
                .attr('fill', color).attr('opacity', 0.9);
        }

        // Flow label near midpoint
        if (pathNode && pathNode.getTotalLength) {
            var totalLen2 = pathNode.getTotalLength();
            var lblPt = pathNode.getPointAtLength(totalLen2 * 0.4);
            var dir = flow.flow_mw >= 0 ? '\u2192' : '\u2190';
            var util = flow.utilization_pct != null ? flow.utilization_pct : 0;
            var txt = dir + ' ' + _fmtMW(Math.abs(flow.flow_mw)) + ' (' + util.toFixed(0) + '%)';

            var tw = txt.length * 5.5 + 10;
            gOps.append('rect')
                .attr('x', lblPt.x - tw / 2).attr('y', lblPt.y - 18)
                .attr('width', tw).attr('height', 16).attr('rx', 3)
                .attr('fill', 'rgba(255,255,255,0.92)').attr('stroke', color)
                .attr('stroke-width', 0.8);
            gOps.append('text')
                .attr('x', lblPt.x).attr('y', lblPt.y - 10)
                .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '9px').attr('font-weight', '600')
                .attr('fill', color).text(txt);
        }
    });
}

/** Draw nodal price badges near buses. */
function _drawPriceBadges(gOps, ops) {
    var nodes = ops.nodes || {};
    var prices = [];
    Object.keys(nodes).forEach(function(k) {
        var p = nodes[k].price;
        if (p !== undefined && p !== null) prices.push(p);
    });
    if (prices.length === 0) return;
    var minP = Math.min.apply(null, prices);
    var maxP = Math.max.apply(null, prices);
    if (minP === maxP && minP === 0) return;  // Skip when all prices are zero

    // Map node_index → first bus_id for that node
    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        var ni = bl.parentNode;
        if (ni !== undefined && !nodeToBus[ni]) {
            nodeToBus[ni] = bid;
        }
    });

    Object.keys(nodes).forEach(function(nodeIdx) {
        var price = nodes[nodeIdx].price;
        if (price === undefined || price === null) return;
        var bid = nodeToBus[nodeIdx];
        if (!bid || !busLayout[bid]) return;
        var bl = busLayout[bid];

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x - 12;
            py = bl.y - 14;
        } else {
            px = bl.x + bl.barLen + 10;
            py = bl.y - 2;
        }

        var bgColor = _priceColor(price, minP, maxP);
        var txtColor = (price > (minP + maxP) * 0.6) ? '#FFF' : '#2C3E50';
        var label = '$' + price.toFixed(1);
        var tw = label.length * 6 + 8;

        gOps.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 8)
            .attr('width', tw).attr('height', 16).attr('rx', 3)
            .attr('fill', bgColor).attr('stroke', _darken(bgColor, 0.2))
            .attr('stroke-width', 0.6).attr('opacity', 0.9);
        gOps.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '9px').attr('font-weight', '700')
            .attr('fill', txtColor).text(label);
    });
}

/** Draw reserve badges near each node's bus (Level 1). */
function _drawReserveBadges(gOps, ops) {
    var nodes = ops.nodes || {};

    // Map node_index → first bus_id
    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        var ni = bl.parentNode;
        if (ni !== undefined && !nodeToBus[ni]) {
            nodeToBus[ni] = bid;
        }
    });

    Object.keys(nodes).forEach(function(nodeIdx) {
        var nd = nodes[nodeIdx];
        var resStatic = nd.reserve_static_mw || 0;
        var resDynamic = nd.reserve_dynamic_mw || 0;
        if (resStatic === 0 && resDynamic === 0) return;

        var bid = nodeToBus[nodeIdx];
        if (!bid || !busLayout[bid]) return;
        var bl = busLayout[bid];

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x + 14;
            py = bl.y + bl.barLen + 8;
        } else {
            px = bl.x - 14;
            py = bl.y + 14;
        }

        var hasLoss = (nd.reserve_static_loss_mw || 0) > 0.1 || (nd.reserve_dynamic_loss_mw || 0) > 0.1;
        var bgColor = hasLoss ? '#E74C3C' : '#27AE60';
        var label = 'R: ' + resStatic.toFixed(0) + '/' + resDynamic.toFixed(0) + ' MW';
        var tw = label.length * 5 + 10;

        gOps.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 7)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', bgColor).attr('opacity', 0.85);
        gOps.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '600')
            .attr('fill', '#FFF').text(label);
    });
}

/** Draw voltage angle labels near each bus (Level 1). */
function _drawVoltageAngles(gOps, ops) {
    var nodes = ops.nodes || {};

    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        var ni = bl.parentNode;
        if (ni !== undefined && !nodeToBus[ni]) {
            nodeToBus[ni] = bid;
        }
    });

    Object.keys(nodes).forEach(function(nodeIdx) {
        var nd = nodes[nodeIdx];
        var angle = nd.voltage_angle_deg;
        if (angle === undefined || angle === 0) return;

        var bid = nodeToBus[nodeIdx];
        if (!bid || !busLayout[bid]) return;
        var bl = busLayout[bid];

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x + bl.barLen + 8;
            py = bl.y + 10;
        } else {
            px = bl.x + bl.barLen / 2;
            py = bl.y - 12;
        }

        var absAngle = Math.abs(angle);
        var color = absAngle < 10 ? '#27AE60' : absAngle < 20 ? '#F39C12' : '#E74C3C';
        var label = '\u03B8=' + angle.toFixed(1) + '\u00B0';

        gOps.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '500')
            .attr('fill', color).text(label);
    });
}

/** Draw compact frequency gauge in upper-right corner (Level 3). */
function _drawFrequencyGauge(gOps, ops) {
    var freq = (ops.system || {}).frequency;
    svg.selectAll('.freq-gauge').remove();
    if (!freq) return;

    // ── Position below the legend, same x and width ──
    var cw = svg.node().clientWidth || 800;
    var padX = 10, padY = 8;
    var gw, gx, gy;
    if (_legendBounds) {
        gw = _legendBounds.w;
        gx = _legendBounds.x;
        gy = _legendBounds.y + _legendBounds.h + 8;  // 8px gap below legend
    } else {
        gw = 156;
        gx = cw - gw - 12;
        gy = 12;
    }

    // Adapt ranges to system nominal frequency
    var fNom = freq.f_nom_hz || 50.0;
    var fMin = fNom - 2.0;
    var fMax = fNom + 2.0;
    var nadirColor = freq.is_stable ? '#27AE60' : '#E74C3C';
    var rocofColor = freq.rocof_ok ? '#27AE60' : '#E74C3C';

    // ── Layout ──
    var rowH = 18;
    var titleH = 20;
    var barSectionH = 32;  // bar + nadir label + scale labels
    var nMetricRows = 3;   // ROCOF, H/ΔP, Nadir/Status
    var gh = titleH + barSectionH + nMetricRows * rowH + padY * 2;

    var fg = svg.append('g').attr('class', 'freq-gauge')
        .attr('transform', 'translate(' + gx + ',' + gy + ')');

    // ── Background (matches legend style) ──
    fg.append('rect')
        .attr('x', -1).attr('y', -1)
        .attr('width', gw + 2).attr('height', gh + 2)
        .attr('rx', 6).attr('fill', 'rgba(0,0,0,0.03)').attr('stroke', 'none');
    fg.append('rect')
        .attr('width', gw).attr('height', gh)
        .attr('rx', 6)
        .attr('fill', 'rgba(255,255,255,0.92)')
        .attr('stroke', freq.is_stable ? T.groupBorder : '#E74C3C')
        .attr('stroke-width', 1);

    // ── Title ──
    fg.append('text')
        .attr('x', padX).attr('y', titleH - 4)
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '10px').attr('font-weight', '700')
        .attr('fill', T.groupTitle)
        .text('Frequency (' + fNom.toFixed(0) + ' Hz)');

    // ── Status indicator on title row ──
    var statusLabel = freq.is_stable ? 'STABLE' : 'UNSTABLE';
    fg.append('text')
        .attr('x', gw - padX).attr('y', titleH - 4)
        .attr('text-anchor', 'end')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '8px').attr('font-weight', '700')
        .attr('fill', nadirColor)
        .text(statusLabel);

    // ── Horizontal bar gauge ──
    var barX = padX;
    var barY = titleH + padY;
    var barW = gw - padX * 2;
    var barH = 8;

    fg.append('rect')
        .attr('x', barX).attr('y', barY)
        .attr('width', barW).attr('height', barH).attr('rx', 2)
        .attr('fill', '#E2E6EA');

    // Color zones adapted to fNom
    var zones = [
        { from: fMin, to: fNom - 1, color: '#E74C3C' },
        { from: fNom - 1, to: fNom - 0.5, color: '#F39C12' },
        { from: fNom - 0.5, to: fNom + 0.5, color: '#27AE60' },
        { from: fNom + 0.5, to: fNom + 1, color: '#F39C12' },
        { from: fNom + 1, to: fMax, color: '#E74C3C' }
    ];
    zones.forEach(function(z) {
        var x1 = barX + (z.from - fMin) / (fMax - fMin) * barW;
        var x2 = barX + (z.to - fMin) / (fMax - fMin) * barW;
        fg.append('rect')
            .attr('x', x1).attr('y', barY)
            .attr('width', x2 - x1).attr('height', barH)
            .attr('fill', z.color).attr('opacity', 0.5);
    });

    // Nadir needle
    var nadirPos = (freq.nadir_hz - fMin) / (fMax - fMin);
    nadirPos = Math.max(0, Math.min(1, nadirPos));
    var needleX = barX + nadirPos * barW;
    fg.append('line')
        .attr('x1', needleX).attr('y1', barY - 2)
        .attr('x2', needleX).attr('y2', barY + barH + 2)
        .attr('stroke', T.groupTitle).attr('stroke-width', 2);

    // Scale labels
    fg.append('text')
        .attr('x', barX).attr('y', barY - 3)
        .attr('text-anchor', 'start')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '7px').attr('fill', T.labelText).text(fMin.toFixed(0));
    fg.append('text')
        .attr('x', barX + barW).attr('y', barY - 3)
        .attr('text-anchor', 'end')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '7px').attr('fill', T.labelText).text(fMax.toFixed(0));

    // Nadir value below bar
    fg.append('text')
        .attr('x', needleX).attr('y', barY + barH + 10)
        .attr('text-anchor', 'middle')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '8px').attr('font-weight', '600')
        .attr('fill', nadirColor)
        .text('Nadir: ' + freq.nadir_hz.toFixed(2) + ' Hz');

    // ── Metric rows (below bar section) ──
    var curY = titleH + barSectionH + padY;

    // Row 1: ROCOF
    fg.append('text')
        .attr('x', padX).attr('y', curY + rowH / 2 + 1)
        .attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('fill', T.labelText)
        .text('ROCOF');
    fg.append('text')
        .attr('x', gw - padX).attr('y', curY + rowH / 2 + 1)
        .attr('text-anchor', 'end').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('font-weight', '600')
        .attr('fill', rocofColor)
        .text(freq.rocof_hz_s.toFixed(2) + ' Hz/s');
    curY += rowH;

    // Row 2: System inertia
    fg.append('text')
        .attr('x', padX).attr('y', curY + rowH / 2 + 1)
        .attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('fill', T.labelText)
        .text('Inertia H');
    fg.append('text')
        .attr('x', gw - padX).attr('y', curY + rowH / 2 + 1)
        .attr('text-anchor', 'end').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('font-weight', '600')
        .attr('fill', T.labelText)
        .text(freq.h_total_mws.toFixed(0) + ' MW\u00B7s');
    curY += rowH;

    // Row 3: Power imbalance
    fg.append('text')
        .attr('x', padX).attr('y', curY + rowH / 2 + 1)
        .attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('fill', T.labelText)
        .text('\u0394P loss');
    fg.append('text')
        .attr('x', gw - padX).attr('y', curY + rowH / 2 + 1)
        .attr('text-anchor', 'end').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '9px').attr('font-weight', '600')
        .attr('fill', T.labelText)
        .text(freq.delta_p_mw.toFixed(0) + ' MW');
}

/** Update SLD with contingency analysis results (Level 2).
 *  Called from Python via sld_widget.update_contingency_data().
 *  @param {string} jsonStr - JSON from ContingencyResult
 */
function updateContingencyData(jsonStr) {
    var ctg;
    try { ctg = JSON.parse(jsonStr); } catch(e) {
        console.error('SLD contingency: bad JSON', e); return;
    }

    // Remove previous contingency overlay
    rootGroup.selectAll('.layer-contingency').remove();
    svg.selectAll('.contingency-badge').remove();

    var gCtg = rootGroup.append('g').attr('class', 'layer-contingency');

    // ── Mark the tripped element ──
    var tripKey = null;
    if (ctg.contingency_type === 'generator') {
        tripKey = 'generator:' + ctg.element_id;
    } else if (ctg.contingency_type === 'line') {
        tripKey = 'line:edge_' + ctg.element_id;
    }

    if (tripKey && elementRegistry[tripKey]) {
        var tripEntry = elementRegistry[tripKey];
        var tripGroup = tripEntry.group;
        if (tripGroup && tripGroup.node()) {
            var transform = tripGroup.attr('transform');
            var match = transform ? transform.match(/translate\(\s*([\d.e+-]+)\s*,\s*([\d.e+-]+)\s*\)/) : null;
            if (match) {
                var tx = parseFloat(match[1]);
                var ty = parseFloat(match[2]);
                // Red X cross over tripped element
                gCtg.append('line')
                    .attr('x1', tx - 14).attr('y1', ty - 14)
                    .attr('x2', tx + 14).attr('y2', ty + 14)
                    .attr('stroke', '#E74C3C').attr('stroke-width', 3).attr('opacity', 0.9);
                gCtg.append('line')
                    .attr('x1', tx + 14).attr('y1', ty - 14)
                    .attr('x2', tx - 14).attr('y2', ty + 14)
                    .attr('stroke', '#E74C3C').attr('stroke-width', 3).attr('opacity', 0.9);
            }
        }
    }

    // ── Highlight overloaded lines ──
    var overloaded = ctg.overloaded_lines || [];
    overloaded.forEach(function(ol) {
        var lineKey = 'line:' + ol.edge_id.replace('edge_', '');
        var entry = elementRegistry[lineKey];
        if (!entry) return;
        var group = entry.group;
        if (!group || !group.node()) return;

        var pathEl = group.select('path.edge-path');
        if (pathEl.empty()) pathEl = group.select('path');
        if (pathEl.empty()) return;

        // Thick red stroke for overloaded lines
        pathEl.attr('stroke', '#E74C3C').attr('stroke-width', 5)
            .attr('stroke-dasharray', '8,4');

        // Overload label
        var pathNode = pathEl.node();
        if (pathNode && pathNode.getTotalLength) {
            var totalLen = pathNode.getTotalLength();
            var lblPt = pathNode.getPointAtLength(totalLen * 0.6);
            var olLabel = 'OVERLOAD +' + ol.overload_pct.toFixed(0) + '%';
            var tw = olLabel.length * 5.5 + 10;

            gCtg.append('rect')
                .attr('x', lblPt.x - tw / 2).attr('y', lblPt.y - 20)
                .attr('width', tw).attr('height', 16).attr('rx', 3)
                .attr('fill', '#E74C3C').attr('opacity', 0.9);
            gCtg.append('text')
                .attr('x', lblPt.x).attr('y', lblPt.y - 12)
                .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
                .attr('font-family', 'Segoe UI, Roboto, sans-serif')
                .attr('font-size', '8px').attr('font-weight', '700')
                .attr('fill', '#FFF').text(olLabel);
        }
    });

    // ── Load shedding badges ──
    var loadShed = ctg.load_shed_mw || {};
    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        if (bl.parentNode !== undefined && !nodeToBus[bl.parentNode]) {
            nodeToBus[bl.parentNode] = bid;
        }
    });

    Object.keys(loadShed).forEach(function(nodeIdx) {
        var shedMW = loadShed[nodeIdx];
        if (shedMW < 0.1) return;
        var bid = nodeToBus[nodeIdx];
        if (!bid || !busLayout[bid]) return;
        var bl = busLayout[bid];

        var px = bl.x + bl.barLen / 2;
        var py = bl.y + 24;
        var label = 'Shed: ' + _fmtMW(shedMW);
        var tw = label.length * 5.5 + 10;

        gCtg.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 7)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', '#E74C3C').attr('opacity', 0.9);
        gCtg.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '700')
            .attr('fill', '#FFF').text(label);
    });

    // ── Voltage violations from AC contingency ──
    var voltViol = ctg.voltage_violations || [];
    voltViol.forEach(function(vv) {
        var bid = vv.bus_id;
        var bl = busLayout[bid];
        if (!bl) return;

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x - 14;
            py = bl.y - 22;
        } else {
            px = bl.x + bl.barLen / 2;
            py = bl.y - 20;
        }

        var label = 'V=' + vv.vm_pu.toFixed(3) + ' pu';
        var tw = label.length * 5.5 + 8;
        var color = '#E74C3C';

        gCtg.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 7)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', color).attr('opacity', 0.9);
        gCtg.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '700')
            .attr('fill', '#FFF').text(label);
    });

    // ── Security status badge (center-top) ──
    var badgeG = svg.append('g').attr('class', 'contingency-badge');
    var cw = svg.node().clientWidth || 800;

    // Build detail lines
    var descText = ctg.element_description || '';
    var detailLines = [];
    var overloaded = ctg.overloaded_lines || [];
    var totalShed = ctg.total_load_shed_mw || 0;
    var voltViol = ctg.voltage_violations || [];

    if (overloaded.length > 0) {
        var worstOL = overloaded.reduce(function(a, b) {
            return (a.overload_pct || 0) > (b.overload_pct || 0) ? a : b;
        });
        detailLines.push('Overloads: ' + overloaded.length + ' line(s), worst +'
            + (worstOL.overload_pct || 0).toFixed(0) + '%'
            + (worstOL.line_id ? ' (' + worstOL.line_id + ')' : ''));
    }
    if (totalShed > 0.1) {
        detailLines.push('Load shedding: ' + totalShed.toFixed(1) + ' MW');
    }
    if (voltViol.length > 0) {
        var worstV = voltViol.reduce(function(a, b) {
            return Math.abs(a.vm_pu - 1.0) > Math.abs(b.vm_pu - 1.0) ? a : b;
        });
        detailLines.push('V violations: ' + voltViol.length + ' bus(es), worst '
            + worstV.vm_pu.toFixed(3) + ' pu (' + worstV.bus_id + ')');
    }
    // Integrated N-1 assessment fields (frequency, severity)
    if (ctg.has_frequency_violation) {
        var freqLine = 'Frequency: ROCOF=' + (ctg.rocof_hz_per_s || 0).toFixed(2) + ' Hz/s'
            + ', Nadir=' + (ctg.nadir_hz || 0).toFixed(2) + ' Hz';
        detailLines.push(freqLine);
    }
    if (ctg.severity_score !== undefined && ctg.severity_score > 0) {
        var bindStr = ctg.binding_constraint ? ' [' + ctg.binding_constraint + ']' : '';
        detailLines.push('Severity: ' + ctg.severity_score.toFixed(1) + bindStr);
    }
    // Use integrated is_n1_secure if present, otherwise fall back to is_secure
    var isSecure = (ctg.is_n1_secure !== undefined) ? ctg.is_n1_secure : ctg.is_secure;
    if (isSecure) {
        detailLines.push('No overloads, no load shedding');
    }

    var statusColor = isSecure ? '#27AE60' : '#E74C3C';
    var statusText = isSecure ? 'N-1 SECURE' : 'N-1 INSECURE';
    var bw = 340;
    var lineH = 13;
    var bh = 32 + detailLines.length * lineH;
    var bx = (cw - bw) / 2;  // centered horizontally
    var by = 8;               // near top

    badgeG.append('rect')
        .attr('x', bx).attr('y', by)
        .attr('width', bw).attr('height', bh).attr('rx', 6)
        .attr('fill', 'rgba(44,62,80,0.94)')
        .attr('stroke', statusColor).attr('stroke-width', 2);

    // Status title
    badgeG.append('text')
        .attr('x', bx + bw / 2).attr('y', by + 13)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '11px').attr('font-weight', '700')
        .attr('fill', statusColor).text(statusText + '  \u2014  ' + descText);

    // Detail lines
    for (var di = 0; di < detailLines.length; di++) {
        var dColor = isSecure ? '#95A5A6' : '#F5B7B1';
        badgeG.append('text')
            .attr('x', bx + 12).attr('y', by + 27 + di * lineH)
            .attr('text-anchor', 'start').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '9px').attr('fill', dColor)
            .text(detailLines[di]);
    }
}

/** Remove contingency overlay. */
function clearContingencyData() {
    if (rootGroup) rootGroup.selectAll('.layer-contingency').remove();
    if (svg) {
        svg.selectAll('.contingency-badge').remove();
    }
}

/** Draw voltage magnitude badges at each bus (AC PF). */
function _drawVoltageBadges(gOps, ops) {
    var nodes = ops.nodes || {};
    var pf = (ops.system || {}).power_flow;
    if (!pf) return;  // Only draw when AC PF data present

    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        if (bl.parentNode !== undefined && !nodeToBus[bl.parentNode]) {
            nodeToBus[bl.parentNode] = bid;
        }
    });

    Object.keys(nodes).forEach(function(nodeIdx) {
        var nd = nodes[nodeIdx];
        var vm = nd.vm_pu;
        if (vm === undefined || vm === 1.0) return;  // Skip default/unset

        var bid = nodeToBus[nodeIdx];
        if (!bid || !busLayout[bid]) return;
        var bl = busLayout[bid];

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x - 12;
            py = bl.y + bl.barLen + 22;
        } else {
            px = bl.x + bl.barLen + 10;
            py = bl.y + 14;
        }

        // Color by voltage range
        var color;
        if (vm >= 0.95 && vm <= 1.05) color = '#27AE60';       // green: normal
        else if (vm >= 0.90 && vm <= 1.10) color = '#F39C12';   // yellow: warning
        else color = '#E74C3C';                                   // red: violation

        var label = 'V=' + vm.toFixed(3) + ' pu';
        var tw = label.length * 5.5 + 8;

        gOps.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 7)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', color).attr('opacity', 0.85);
        gOps.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '600')
            .attr('fill', '#FFF').text(label);
    });
}

/** Draw reactive power labels on transmission lines (AC PF). */
function _drawReactivePowerOverlay(gOps, ops) {
    var lines = ops.lines || {};
    var pf = (ops.system || {}).power_flow;
    if (!pf) return;

    Object.keys(lines).forEach(function(edgeId) {
        var flow = lines[edgeId];
        var qMvar = flow.q_from_mvar;
        if (qMvar === undefined || qMvar === 0) return;

        var entry = elementRegistry['line:' + edgeId.replace('edge_', '')];
        if (!entry) return;
        var group = entry.group;
        if (!group || !group.node()) return;

        var pathEl = group.select('path.edge-path');
        if (pathEl.empty()) pathEl = group.select('path');
        if (pathEl.empty()) return;

        var pathNode = pathEl.node();
        if (!pathNode || !pathNode.getTotalLength) return;

        var totalLen = pathNode.getTotalLength();
        var lblPt = pathNode.getPointAtLength(totalLen * 0.7);
        var label = 'Q: ' + qMvar.toFixed(1) + ' Mvar';
        var tw = label.length * 5.5 + 8;
        var color = '#9B59B6';  // purple

        gOps.append('rect')
            .attr('x', lblPt.x - tw / 2).attr('y', lblPt.y + 4)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', 'rgba(255,255,255,0.92)').attr('stroke', color)
            .attr('stroke-width', 0.8);
        gOps.append('text')
            .attr('x', lblPt.x).attr('y', lblPt.y + 11)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '600')
            .attr('fill', color).text(label);
    });
}

/** Draw line loss labels on transmission lines (AC PF). */
function _drawLineLossLabels(gOps, ops) {
    var lines = ops.lines || {};
    var pf = (ops.system || {}).power_flow;
    if (!pf) return;

    Object.keys(lines).forEach(function(edgeId) {
        var flow = lines[edgeId];
        var pLoss = flow.p_loss_mw;
        if (pLoss === undefined || pLoss < 0.01) return;

        var entry = elementRegistry['line:' + edgeId.replace('edge_', '')];
        if (!entry) return;
        var group = entry.group;
        if (!group || !group.node()) return;

        var pathEl = group.select('path.edge-path');
        if (pathEl.empty()) pathEl = group.select('path');
        if (pathEl.empty()) return;

        var pathNode = pathEl.node();
        if (!pathNode || !pathNode.getTotalLength) return;

        var totalLen = pathNode.getTotalLength();
        var lblPt = pathNode.getPointAtLength(totalLen * 0.55);
        var label = 'Loss: ' + pLoss.toFixed(2) + ' MW';
        var tw = label.length * 5.5 + 8;
        var color = '#E67E22';  // orange

        gOps.append('rect')
            .attr('x', lblPt.x - tw / 2).attr('y', lblPt.y - 30)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', 'rgba(255,255,255,0.92)').attr('stroke', color)
            .attr('stroke-width', 0.8);
        gOps.append('text')
            .attr('x', lblPt.x).attr('y', lblPt.y - 23)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '600')
            .attr('fill', color).text(label);
    });
}

/** Draw short-circuit power badges at each bus. */
function _drawShortCircuitBadges(gOps, ops) {
    var sc = (ops.system || {}).short_circuit;
    if (!sc || !sc.sk_mva) return;

    var nodeToBus = {};
    Object.keys(busLayout).forEach(function(bid) {
        var bl = busLayout[bid];
        if (bl.parentNode !== undefined && !nodeToBus[bl.parentNode]) {
            nodeToBus[bl.parentNode] = bid;
        }
    });

    // Map bus_id to node index for SC data
    Object.keys(sc.sk_mva).forEach(function(busId) {
        var skMva = sc.sk_mva[busId];
        // Find which node this bus belongs to
        var bl = busLayout[busId];
        if (!bl) return;

        var px, py;
        if (bl.orientation === 90) {
            px = bl.x + bl.barLen + 10;
            py = bl.y + bl.barLen / 2;
        } else {
            px = bl.x + bl.barLen / 2;
            py = bl.y + 28;
        }

        // Color by SC power level
        var color;
        if (skMva > 500) color = '#27AE60';       // strong grid
        else if (skMva > 100) color = '#F39C12';   // medium
        else color = '#E74C3C';                     // weak grid

        var label = 'Sk: ' + skMva.toFixed(0) + ' MVA';
        var tw = label.length * 5.5 + 8;

        gOps.append('rect')
            .attr('x', px - tw / 2).attr('y', py - 7)
            .attr('width', tw).attr('height', 14).attr('rx', 3)
            .attr('fill', color).attr('opacity', 0.85);
        gOps.append('text')
            .attr('x', px).attr('y', py)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('font-weight', '700')
            .attr('fill', '#FFF').text(label);
    });
}

/** Draw AC power flow status badge in upper-left corner. */
function _drawPowerFlowStatusBadge(ops) {
    var pf = (ops.system || {}).power_flow;
    if (!pf) return;

    svg.selectAll('.pf-status-badge').remove();
    var gBadge = svg.append('g').attr('class', 'pf-status-badge');

    var bx = 10;
    var by = 12;
    var bw = 200;
    var bh = 36;

    var statusColor = pf.converged ? '#27AE60' : '#E74C3C';
    var statusText = pf.converged
        ? 'AC PF: Converged (' + (pf.iterations || 0) + ' iter)'
        : 'AC PF: DIVERGED';
    var detailItems = [];
    if (pf.total_losses_mw > 0) detailItems.push('Losses: ' + pf.total_losses_mw.toFixed(2) + ' MW');
    var nViol = (pf.voltage_violations || []).length;
    if (nViol > 0) detailItems.push('V violations: ' + nViol);
    var detailText = detailItems.join(' | ');

    gBadge.append('rect')
        .attr('x', bx).attr('y', by)
        .attr('width', bw).attr('height', bh).attr('rx', 6)
        .attr('fill', 'rgba(44,62,80,0.92)')
        .attr('stroke', statusColor).attr('stroke-width', 1.5);
    gBadge.append('text')
        .attr('x', bx + bw / 2).attr('y', by + 13)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '10px').attr('font-weight', '700')
        .attr('fill', statusColor).text(statusText);
    if (detailText) {
        gBadge.append('text')
            .attr('x', bx + bw / 2).attr('y', by + 26)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('font-family', 'Segoe UI, Roboto, sans-serif')
            .attr('font-size', '8px').attr('fill', '#BDC3C7')
            .text(detailText);
    }
}

/** Draw fixed system info bar at bottom of SVG. */
function _drawSystemInfoBar(ops) {
    svg.selectAll('.ops-info-bar').remove();
    var sys = ops.system || {};
    if (!sys.year && !sys.hour) return;

    var cw = svg.node().clientWidth || 800;
    var ch = svg.node().clientHeight || 600;

    var items = [];
    if (sys.year) items.push('Year ' + sys.year);
    if (sys.hour !== undefined) items.push('Hour ' + sys.hour);
    if (sys.re_penetration) items.push('RE: ' + (sys.re_penetration * 100).toFixed(1) + '%');
    if (sys.total_demand_mw) items.push('Demand: ' + _fmtMW(sys.total_demand_mw));
    if (sys.total_gen_mw) items.push('Gen: ' + _fmtMW(sys.total_gen_mw));
    if (sys.co2_tons) items.push('CO\u2082: ' + sys.co2_tons.toFixed(1) + ' t');

    // AC Power flow metrics
    var pf = sys.power_flow;
    if (pf && pf.converged) {
        if (pf.total_losses_mw > 0) items.push('Losses: ' + pf.total_losses_mw.toFixed(2) + ' MW');
        var nViol = (pf.voltage_violations || []).length;
        if (nViol > 0) items.push('V viol: ' + nViol);
    }

    // Frequency metrics (Level 3)
    var freq = sys.frequency;
    if (freq) {
        if (freq.rocof_hz_s !== undefined) items.push('ROCOF: ' + freq.rocof_hz_s.toFixed(2) + ' Hz/s');
        if (freq.nadir_hz !== undefined) items.push('Nadir: ' + freq.nadir_hz.toFixed(2) + ' Hz');
        if (freq.h_total_mws !== undefined) items.push('H: ' + freq.h_total_mws.toFixed(0) + ' MW\u00B7s');
    }

    var text = items.join('  |  ');
    var barW = text.length * 7 + 40;
    var barH = 26;
    var bx = (cw - barW) / 2;
    var by = ch - barH - 8;

    var barG = svg.append('g').attr('class', 'ops-info-bar');
    barG.append('rect')
        .attr('x', bx).attr('y', by)
        .attr('width', barW).attr('height', barH).attr('rx', 6)
        .attr('fill', 'rgba(44, 62, 80, 0.88)')
        .attr('stroke', 'rgba(255,255,255,0.3)').attr('stroke-width', 0.5);
    barG.append('text')
        .attr('x', bx + barW / 2).attr('y', by + barH / 2)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
        .attr('font-family', 'Segoe UI, Roboto, sans-serif')
        .attr('font-size', '11px').attr('font-weight', '500')
        .attr('fill', '#ECF0F1').text(text);
}

/** Serialize current SVG and send to Python bridge for export. */
function exportSvg() {
    if (!svg) return;
    var svgNode = svg.node();
    var serializer = new XMLSerializer();
    var markup = serializer.serializeToString(svgNode);
    if (sldBridge) sldBridge.on_svg_exported(markup);
}


// ══════════════════════════════════════════════════════════════════
// Startup
// ══════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', initSld);
