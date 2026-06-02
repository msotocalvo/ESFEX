/**
 * ESFEX Map Controller
 *
 * Manages Leaflet map interactions and communicates with Python
 * via QWebChannel bridge.
 */

// ── Suppress Leaflet internal async errors (replaceChild on detached DOM) ──
window.addEventListener('unhandledrejection', function(event) {
    if (event.reason && event.reason.message &&
        event.reason.message.indexOf('replaceChild') !== -1) {
        event.preventDefault();
    }
});

// ── Global State ──────────────────────────────────────────────────
var map = null;
var bridge = null;
var drawControl = null;
var rectDrawControl = null;
var rooftopDomainLayer = null;
var rooftopResultsLayer = null;
var otecDomainLayer = null;
var otecResultsLayer = null;
var otecDevZonesLayer = null;
var windDomainLayer = null;
var windResultsLayer = null;
var windDevZonesLayer = null;
var solarPVDomainLayer = null;
var solarPVResultsLayer = null;
var solarPVDevZonesLayer = null;
var demandDomainLayer = null;
var demandClustersLayer = null;
var currentMode = 'select';   // select | add_line | draw_zone | draw_rectangle | add_*
var selectedElement = null;

// ── Context Menu State ────────────────────────────────────────────
var _ctxMenuType = null;
var _ctxMenuId = null;

// Layer groups
var layers = {
    generators: null,
    batteries: null,
    transmissionLines: null,
    transformers: null,
    electrolyzers: null,
    acdcConverters: null,
    freqConverters: null,
    developmentZones: null,
    buses: null,
    fuelEntryPoints: null,
    fuelTransport: null,
    resultsNodes: null,
    resultsFlows: null,
    background: null,
};

// Element registries (id -> layer)
var generatorMarkers = {};
var batteryMarkers = {};
var transmissionLinePolylines = {};
var transformerMarkers = {};
var zonePolygons = {};
var fuelEntryMarkers = {};
var electrolyzerMarkers = {};
var acdcConverterMarkers = {};
var freqConverterMarkers = {};
var busMarkers = {};
var fuelRoutePolylines = {};
var geoAssetLayers = {};     // asset_id -> L.geoJSON layer
var bgElements = {};         // background system elements (non-interactive)

// ── Magnetic Element Registry ────────────────────────────────────
// "type:id" -> {type, id, marker, nodeIndex}
var baseMaps = {};
var currentBaseMap = null;

var magneticElements = {};

function _registerMagnetic(type, id, marker, nodeIndex) {
    var key = type + ':' + id;
    magneticElements[key] = { type: type, id: String(id), marker: marker, nodeIndex: nodeIndex };
}

function _unregisterMagnetic(type, id) {
    var key = type + ':' + id;
    delete magneticElements[key];
}

// ── Polyline Trace State ─────────────────────────────────────────
var traceStartRef = null;       // {type, id} of start magnetic element
var traceWaypoints = [];        // [{lat, lng}, ...] intermediate clicks
var tracePolyline = null;       // L.polyline for the current trace
var traceRubberBand = null;     // L.polyline for cursor follow
var traceJustFinished = false;  // guard: prevents map click adding waypoint after finish

// ── Line-Split Snap State ────────────────────────────────────────
var _highlightedLine = null;  // {lineId, lineType, originalStyle} or null
var _SNAP_PIXELS = 15;        // proximity threshold in screen pixels

var _ELEMENT_PLACEMENT_MODES = [
    'add_generator', 'add_battery', 'add_bus', 'add_transformer',
    'add_electrolyzer', 'add_acdc_converter', 'add_freq_converter',
    'add_fuel_entry', 'add_fuel_storage', 'pick_centroid'
];

function _isElementPlacementMode(mode) {
    return _ELEMENT_PLACEMENT_MODES.indexOf(mode) >= 0;
}

// ── Performance: Canvas Rendering + LOD + Clustering ─────────────
//
// For large systems (>200 point elements) the batch loader switches to
// Canvas-backed L.circleMarker instead of DOM-based L.marker+L.divIcon.
// This renders all points on a single <canvas>, avoiding thousands of
// DOM nodes.  When the user zooms in past _LOD_ZOOM, visible elements
// are "promoted" to full DOM markers with SVG icons and drag support.
// Buses additionally use L.markerClusterGroup when their count is high.

var _canvasRenderer = null;        // L.canvas() – created lazily
var _CANVAS_THRESHOLD = 200;       // min point elements to trigger canvas mode
var _LOD_ZOOM = 15;                // zoom level for DOM detail markers
var _BUS_CLUSTER_THRESHOLD = 50;   // min buses for clustering

var _usingCanvasMode = false;      // true while batch used canvas markers
var _lodActive = false;            // true when detail DOM markers are shown
var _lodListenersAttached = false;

var _canvasMarkers = {};           // "type:id" -> L.circleMarker
var _lodDomMarkers = {};           // "type:id" -> L.marker (detail DOM)
var _lodHiddenCanvas = {};         // "type:id" -> hidden circleMarker
var _lodBatchData = null;          // original batch items for LOD rebuild
var _busClusterGroup = null;       // L.markerClusterGroup
var _busClusterAttached = false;   // guard against double-attach

// Map element type -> layer key
var _typeToLayerKey = {
    'bus': 'buses', 'generator': 'generators', 'battery': 'batteries',
    'fuel_entry': 'fuelEntryPoints', 'fuel_storage': 'fuelEntryPoints',
    'transformer': 'transformers', 'electrolyzer': 'electrolyzers',
    'acdc_converter': 'acdcConverters', 'freq_converter': 'freqConverters',
};

// Map element type -> marker registry
function _getRegistry(type) {
    var m = {
        'bus': busMarkers, 'generator': generatorMarkers,
        'battery': batteryMarkers, 'fuel_entry': fuelEntryMarkers,
        'fuel_storage': fuelStorageMarkers, 'transformer': transformerMarkers,
        'electrolyzer': electrolyzerMarkers, 'acdc_converter': acdcConverterMarkers,
        'freq_converter': freqConverterMarkers,
    };
    return m[type] || null;
}

function _getCanvasRenderer() {
    if (!_canvasRenderer) {
        _canvasRenderer = L.canvas({ padding: 0.5, tolerance: 10 });
    }
    return _canvasRenderer;
}

/** Resolve canvas circle style from a batch element descriptor. */
function _canvasStyleFor(el) {
    var colorMap = {
        'bus':            _defaultColors['bus-marker']            || '#34495e',
        'gen_Renewable':  _defaultColors['gen-marker-renewable']  || '#27ae60',
        'gen_Non-Renewable': _defaultColors['gen-marker-nonrenewable'] || '#7f8c8d',
        'battery':        _defaultColors['bat-marker']            || '#f39c12',
        'fuel_entry':     _defaultColors['fuel-marker']           || '#e74c3c',
        'fuel_storage':   _defaultColors['fuel-storage-marker']   || '#d35400',
        'transformer':    _defaultColors['transformer-marker']    || '#9b59b6',
        'electrolyzer':   _defaultColors['electrolyzer-marker']   || '#16a085',
        'acdc_converter': _defaultColors['acdc-marker']           || '#2980b9',
        'freq_converter': _defaultColors['freq-marker']           || '#8e44ad',
    };

    var key = el.type;
    if (el.type === 'generator') {
        key = (el.genType === 'Renewable') ? 'gen_Renewable' : 'gen_Non-Renewable';
    }
    var color = colorMap[key] || '#888';

    // Style overrides from batch element
    var style = el.style;
    if (typeof style === 'string') {
        try { style = JSON.parse(style); } catch(e) { style = null; }
    }
    if (style && style.color) color = style.color;

    var radius = 5;
    if (style && style.size) radius = Math.max(3, style.size / 4);
    return { color: color, radius: radius };
}

/** Create a Canvas-backed circleMarker with click/context/tooltip. */
function _addCanvasMarker(el) {
    var cs = _canvasStyleFor(el);
    var cm = L.circleMarker([el.lat, el.lng], {
        renderer: _getCanvasRenderer(),
        radius: cs.radius,
        fillColor: cs.color,
        color: '#fff',
        weight: 1,
        fillOpacity: 0.85,
        opacity: 0.9,
    });

    // Tooltip
    var tooltip = el.name || el.id;
    if (el.type === 'generator' && el.ratedPowerMw != null)
        tooltip = el.name + ' (' + el.ratedPowerMw.toFixed(0) + ' MW)';
    else if (el.type === 'battery' && el.capacityMwh != null)
        tooltip = el.name + ' (' + el.capacityMwh.toFixed(0) + ' MWh)';
    else if (el.type === 'bus' && el.voltageKv != null)
        tooltip = el.name + ' (' + el.voltageKv + ' kV)';
    else if (el.type === 'transformer' && el.ratedPowerMva != null)
        tooltip = el.name + ' (' + el.ratedPowerMva + ' MVA)';
    else if ((el.type === 'fuel_entry' || el.type === 'fuel_storage') && el.fuel)
        tooltip = el.name + ' (' + el.fuel + ')';
    else if ((el.type === 'electrolyzer' || el.type === 'acdc_converter' ||
              el.type === 'freq_converter') && el.ratedPower != null)
        tooltip = el.name + ' (' + el.ratedPower + ' MW)';

    cm.bindTooltip(tooltip, { sticky: true });
    cm._hoverLabel = tooltip;
    cm._shortLabel = el.name || el.id;
    cm._markerSize = cs.radius * 2;
    cm._cssClassHint = null;

    // Click
    cm.on('click', (function(type, id) {
        return function(e) {
            L.DomEvent.stopPropagation(e);
            if (_onMagneticClickForTrace(type, id, cm)) return;
            selectElement(type, id, cm);
        };
    })(el.type, el.id));

    // Context menu
    cm.on('contextmenu', (function(type, id) {
        return function(e) {
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e);
            _showMarkerContextMenu(type, id,
                e.originalEvent.clientX, e.originalEvent.clientY);
        };
    })(el.type, el.id));

    // No drag handler — canvas circleMarkers don't support draggable.
    // Drag is available when LOD upgrades to DOM markers (zoom >= _LOD_ZOOM).

    var storeKey = el.type + ':' + el.id;
    _canvasMarkers[storeKey] = cm;

    // Add to appropriate layer (buses may go to cluster group)
    if (el.type === 'bus' && _busClusterGroup) {
        _busClusterGroup.addLayer(cm);
    } else {
        var layerKey = _typeToLayerKey[el.type];
        if (layerKey && layers[layerKey]) cm.addTo(layers[layerKey]);
    }

    // Standard registry + magnetic
    var reg = _getRegistry(el.type);
    if (reg) reg[el.id] = cm;
    if (el.nodeIndex !== undefined) {
        _registerMagnetic(el.type, el.id, cm, el.nodeIndex);
    }
    return cm;
}

/** Batch-add items using canvas circleMarkers for point elements. */
function _addBatchItemsCanvas(items, start, end) {
    // Separate buses for potential clustering
    var busItems = [];
    var busCount = 0;
    for (var i = start; i < end; i++) {
        if (items[i].type === 'bus') busCount++;
    }
    var useBusCluster = (busCount > _BUS_CLUSTER_THRESHOLD) &&
                         (typeof L.markerClusterGroup === 'function');
    if (useBusCluster && !_busClusterGroup) {
        _busClusterGroup = L.markerClusterGroup({
            maxClusterRadius: 40,
            disableClusteringAtZoom: _LOD_ZOOM - 1,
            spiderfyOnMaxZoom: true,
            showCoverageOnHover: false,
            chunkedLoading: true,
            iconCreateFunction: function(cluster) {
                var n = cluster.getChildCount();
                var sz = n < 10 ? 28 : n < 50 ? 36 : 44;
                return L.divIcon({
                    html: '<div style="background:' +
                        (_defaultColors['bus-marker'] || '#34495e') +
                        ';color:#fff;border-radius:50%;width:' + sz +
                        'px;height:' + sz + 'px;line-height:' + sz +
                        'px;text-align:center;font-size:11px;font-weight:700;' +
                        'border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.3)">' +
                        n + '</div>',
                    className: '',
                    iconSize: [sz, sz],
                });
            },
        });
    }

    for (var i = start; i < end; i++) {
        var el = items[i];
        try {
            switch (el.type) {
                case 'line':
                    addTransmissionLine(el.id, el.coords, el.capacityMw, el.style);
                    break;
                case 'zone':
                    addDevelopmentZone(el.id, el.coords, el.name,
                        el.technology, el.color, el.opacity);
                    break;
                case 'fuel_route':
                    addFuelTransportRoute(el.id, el.coords, el.fuel,
                        el.capacity, el.style);
                    break;
                default:
                    _addCanvasMarker(el);
                    break;
            }
        } catch(err) {
            console.warn('batch(canvas): error adding ' + el.type + ' ' + el.id, err);
        }
    }

    // Note: _busClusterGroup is attached to the map by loadBatchElements
    // or _finishCanvasMode after all layers are re-attached.
}

// ── LOD: Level-of-Detail switching ───────────────────────────────

function _initLOD() {
    if (_lodListenersAttached) return;
    _lodListenersAttached = true;
    map.on('zoomend', _onLODZoomEnd);
    map.on('moveend', _onLODMoveEnd);
}

function _teardownLOD() {
    if (_lodListenersAttached) {
        map.off('zoomend', _onLODZoomEnd);
        map.off('moveend', _onLODMoveEnd);
        _lodListenersAttached = false;
    }
    _clearAllLODDetail();
    _lodBatchData = null;
}

function _onLODZoomEnd() {
    if (map.getZoom() >= _LOD_ZOOM) {
        _applyLODDetail();
    } else if (_lodActive) {
        _clearAllLODDetail();
    }
}

function _onLODMoveEnd() {
    if (_lodActive && map.getZoom() >= _LOD_ZOOM) {
        _applyLODDetail();
    }
}

/** Promote visible canvas markers to DOM markers with SVG + drag. */
function _applyLODDetail() {
    if (!_lodBatchData) return;
    var bounds = map.getBounds().pad(0.1);

    // Remove DOM markers that left the viewport
    for (var key in _lodDomMarkers) {
        if (!bounds.contains(_lodDomMarkers[key].getLatLng())) {
            _demoteLODMarker(key);
        }
    }

    // Promote visible canvas markers
    for (var i = 0; i < _lodBatchData.length; i++) {
        var el = _lodBatchData[i];
        // Skip non-point elements
        if (el.type === 'line' || el.type === 'zone' || el.type === 'fuel_route') continue;
        // Buses in cluster mode are promoted too at LOD zoom — this gives
        // them full DOM markers with drag support.  The cluster group
        // automatically unspiders at disableClusteringAtZoom, so the DOM
        // marker replaces the cluster marker cleanly.
        // (Previously buses were skipped here, removing drag support.)
        var key = el.type + ':' + el.id;
        if (_lodDomMarkers[key]) continue;  // already promoted
        if (!bounds.contains(L.latLng(el.lat, el.lng))) continue;

        _promoteLODMarker(el, key);
    }
    _lodActive = true;
}

/** Create a DOM marker for an element and hide its canvas marker. */
function _promoteLODMarker(el, key) {
    // Hide canvas marker (make invisible but keep in data structures)
    var cm = _canvasMarkers[key];
    if (cm) {
        cm.setStyle({ fillOpacity: 0, opacity: 0, radius: 0 });
        _lodHiddenCanvas[key] = cm;
    }

    // Create DOM marker using existing add*Marker functions.
    // These overwrite the standard registries and magnetic entries — that's fine,
    // since the DOM marker IS the active marker in LOD mode.
    try {
        switch (el.type) {
            case 'bus':
                addBusMarker(el.id, el.lat, el.lng, el.name,
                    el.voltageKv, el.nodeIndex, el.style); break;
            case 'generator':
                addGeneratorMarker(el.id, el.lat, el.lng, el.name,
                    el.genType, el.ratedPowerMw, el.nodeIndex, el.style); break;
            case 'battery':
                addBatteryMarker(el.id, el.lat, el.lng, el.name,
                    el.capacityMwh, el.nodeIndex, el.style); break;
            case 'fuel_entry':
                addFuelEntryMarker(el.id, el.lat, el.lng, el.name,
                    el.fuel, el.maxAvailability, el.nodeIndex, el.style); break;
            case 'fuel_storage':
                addFuelStorageMarker(el.id, el.lat, el.lng, el.name,
                    el.fuel, el.capacity, el.nodeIndex, el.style); break;
            case 'transformer':
                addTransformerMarker(el.id, el.lat, el.lng, el.name,
                    el.ratedPowerMva, el.nodeIndex, el.style); break;
            case 'electrolyzer':
                addElectrolyzerMarker(el.id, el.lat, el.lng, el.name,
                    el.ratedPower, el.nodeIndex, el.style); break;
            case 'acdc_converter':
                addACDCConverterMarker(el.id, el.lat, el.lng, el.name,
                    el.ratedPower, el.nodeIndex, el.style); break;
            case 'freq_converter':
                addFreqConverterMarker(el.id, el.lat, el.lng, el.name,
                    el.ratedPower, el.nodeIndex, el.style); break;
        }
    } catch(err) {
        console.warn('LOD promote error: ' + key, err);
        return;
    }

    // Capture the DOM marker from the registry
    var reg = _getRegistry(el.type);
    if (reg && reg[el.id]) {
        _lodDomMarkers[key] = reg[el.id];
    }
}

/** Remove a DOM detail marker and restore its canvas marker. */
function _demoteLODMarker(key) {
    var dm = _lodDomMarkers[key];
    if (!dm) return;

    var sep = key.indexOf(':');
    var type = key.substring(0, sep);
    var id = key.substring(sep + 1);

    // Remove DOM marker from layer
    var layerKey = _typeToLayerKey[type];
    if (layerKey && layers[layerKey]) layers[layerKey].removeLayer(dm);

    // Restore canvas marker visibility
    var cm = _lodHiddenCanvas[key];
    if (cm) {
        var cs = _canvasStyleFor({ type: type, id: id, style: null });
        cm.setStyle({ fillOpacity: 0.85, opacity: 0.9, radius: cs.radius });

        // Restore registry + magnetic to canvas marker
        var reg = _getRegistry(type);
        if (reg) reg[id] = cm;
        var magEntry = magneticElements[type + ':' + id];
        if (magEntry) magEntry.marker = cm;
        delete _lodHiddenCanvas[key];
    }
    delete _lodDomMarkers[key];
}

/** Remove ALL LOD detail markers, reverting fully to canvas. */
function _clearAllLODDetail() {
    var keys = Object.keys(_lodDomMarkers);
    for (var i = 0; i < keys.length; i++) {
        _demoteLODMarker(keys[i]);
    }
    _lodActive = false;
}

/**
 * Project point P onto segment AB in pixel space.
 * Returns {dist, projected: L.point, t: [0..1]}.
 */
function _pointToSegmentDistance(p, a, b) {
    var dx = b.x - a.x;
    var dy = b.y - a.y;
    var lenSq = dx * dx + dy * dy;
    var t = 0;
    if (lenSq > 0) {
        t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
    }
    var proj = L.point(a.x + t * dx, a.y + t * dy);
    var ddx = p.x - proj.x;
    var ddy = p.y - proj.y;
    return { dist: Math.sqrt(ddx * ddx + ddy * ddy), projected: proj, t: t };
}

/**
 * Find the nearest transmission polyline to a latlng.
 * @param {L.LatLng} latlng
 * @returns {object|null}  {lineId, segmentIndex, projectedLatLng, distance, lineType}
 */
function _findNearestLineAny(latlng) {
    var clickPt = map.latLngToContainerPoint(latlng);
    var registry = transmissionLinePolylines;

    var best = null;
    var bestDist = Infinity;

    for (var lineId in registry) {
        var polyline = registry[lineId];
        var latlngs = polyline.getLatLngs();

        for (var i = 0; i < latlngs.length - 1; i++) {
            var segStart = map.latLngToContainerPoint(latlngs[i]);
            var segEnd = map.latLngToContainerPoint(latlngs[i + 1]);
            var result = _pointToSegmentDistance(clickPt, segStart, segEnd);

            if (result.dist < _SNAP_PIXELS && result.dist < bestDist) {
                bestDist = result.dist;
                var projectedLatLng = map.containerPointToLatLng(result.projected);
                best = {
                    lineId: lineId,
                    segmentIndex: i,
                    projectedLatLng: projectedLatLng,
                    distance: result.dist,
                    lineType: 'transmission'
                };
            }
        }
    }
    return best;
}

/**
 * Highlight the nearest line when mouse hovers during element placement mode.
 */
function _updateLineSplitHighlight(latlng) {
    if (!_isElementPlacementMode(currentMode)) {
        _clearLineSplitHighlight();
        return;
    }

    var nearest = _findNearestLineAny(latlng);

    if (nearest) {
        // Already highlighting this same line
        if (_highlightedLine && _highlightedLine.lineId === nearest.lineId) return;
        _clearLineSplitHighlight();

        var pl = transmissionLinePolylines[nearest.lineId];
        if (pl) {
            _highlightedLine = {
                lineId: nearest.lineId,
                lineType: nearest.lineType,
                originalStyle: {
                    color: pl.options.color,
                    weight: pl.options.weight,
                    opacity: pl.options.opacity
                }
            };
            pl.setStyle({ color: '#f1c40f', weight: pl.options.weight + 2, opacity: 1.0 });
        }
    } else {
        _clearLineSplitHighlight();
    }
}

function _clearLineSplitHighlight() {
    if (_highlightedLine) {
        var pl = transmissionLinePolylines[_highlightedLine.lineId];
        if (pl) pl.setStyle(_highlightedLine.originalStyle);
        _highlightedLine = null;
    }
}

/**
 * Check line proximity and call bridge for drag-onto-line.
 * Returns true if a line-split signal was sent, false otherwise.
 */
function _checkDragOntoLine(elementType, elementId, pos) {
    var nearest = _findNearestLineAny(pos);
    if (!nearest) return false;

    // Guard: don't split a line this element is already an endpoint of
    // (Python side also checks, but skip the signal for efficiency)
    bridge.on_element_dropped_on_line(
        elementType, String(elementId),
        nearest.projectedLatLng.lat, nearest.projectedLatLng.lng,
        nearest.lineType, nearest.lineId, nearest.segmentIndex
    );
    return true;
}

// ── Context Menu ──────────────────────────────────────────────────

function _showMarkerContextMenu(type, id, clientX, clientY) {
    _ctxMenuType = type;
    _ctxMenuId = id;
    var menu = document.getElementById('map-ctx-menu');
    if (!menu) return;
    // Position near cursor, avoiding viewport overflow
    var menuW = 160, menuH = 140;
    var vpW = window.innerWidth, vpH = window.innerHeight;
    var left = (clientX + menuW > vpW) ? clientX - menuW : clientX + 2;
    var top  = (clientY + menuH > vpH) ? clientY - menuH : clientY + 2;
    menu.style.left = left + 'px';
    menu.style.top  = top  + 'px';
    menu.style.display = 'block';
}

function _hideContextMenu() {
    var menu = document.getElementById('map-ctx-menu');
    if (menu) menu.style.display = 'none';
    _ctxMenuType = null;
    _ctxMenuId = null;
}

// ── Initialization ────────────────────────────────────────────────
function startApp() {
    if (typeof QWebChannel === 'undefined' || typeof qt === 'undefined') {
        setTimeout(startApp, 50);
        return;
    }
    new QWebChannel(qt.webChannelTransport, function(channel) {
        bridge = channel.objects.bridge;
        initializeMap();
        bridge.on_map_ready();
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startApp);
} else {
    startApp();
}

function initializeMap() {
    map = L.map('map', {
        center: [20, 0],
        zoom: 2,
        zoomControl: true,
    });

    // Base map layers
    baseMaps = {
        'OpenStreetMap': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }),
        'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 18, attribution: '&copy; Esri'
        }),
        'Terrain': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            maxZoom: 17, subdomains: 'abc', attribution: '&copy; OpenTopoMap'
        }),
        'Dark': L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19, subdomains: 'abcd', attribution: '&copy; CartoDB'
        }),
        // Offline: vector world countries (Natural Earth 1:110m) bundled
        // locally. No network requests, just country polygons over a
        // water-blue background. Geometry only, ~260 KB.
        'Offline': _createOfflineBaseMap()
    };
    currentBaseMap = baseMaps['OpenStreetMap'];
    currentBaseMap.addTo(map);

    // Fix black-map on resize: observe container size changes and
    // tell Leaflet to recalculate its viewport.  Debounce kept short
    // (50 ms) so users dragging the window border don't see a black
    // frame for long. The Qt-side MapWidget.resizeEvent also pokes
    // invalidateSize on a short timer in case this observer is slow
    // to wake up under heavy Qt resize traffic.
    var _resizeTimeout = null;
    new ResizeObserver(function() {
        if (_resizeTimeout) clearTimeout(_resizeTimeout);
        _resizeTimeout = setTimeout(function() {
            if (map) map.invalidateSize({animate: false});
        }, 50);
    }).observe(document.getElementById('map'));

    for (var key in layers) {
        layers[key] = L.layerGroup().addTo(map);
    }

    var drawnItems = new L.FeatureGroup().addTo(map);

    map.on(L.Draw.Event.CREATED, function(e) {
        var layer = e.layer;
        drawnItems.addLayer(layer);
        if (e.layerType === 'polygon') {
            if (currentMode === 'draw_domain_polygon') {
                bridge.on_domain_polygon_drawn(JSON.stringify(layer.toGeoJSON()));
            } else {
                bridge.on_zone_drawn(JSON.stringify(layer.toGeoJSON()));
            }
            drawnItems.removeLayer(layer);
        } else if (e.layerType === 'rectangle') {
            var bounds = layer.getBounds();
            var boundsJson = JSON.stringify({
                south: bounds.getSouth(),
                west: bounds.getWest(),
                north: bounds.getNorth(),
                east: bounds.getEast()
            });
            bridge.on_rectangle_drawn(boundsJson);
            drawnItems.removeLayer(layer);
        }
    });

    // Context menu item click handlers
    var ctxItems = document.querySelectorAll('#map-ctx-menu [data-action]');
    ctxItems.forEach(function(item) {
        item.addEventListener('click', function(e) {
            e.stopPropagation();
            var action = this.getAttribute('data-action');
            if (_ctxMenuType && _ctxMenuId && bridge) {
                bridge.on_marker_context_action(action, _ctxMenuType, _ctxMenuId);
            }
            _hideContextMenu();
        });
    });
    // Hide context menu on any document click
    document.addEventListener('click', function(e) {
        var menu = document.getElementById('map-ctx-menu');
        if (menu && !menu.contains(e.target)) {
            _hideContextMenu();
        }
    });

    map.on('click', function(e) {
        _hideContextMenu();
        if (_isElementPlacementMode(currentMode)) {
            // pick_centroid is a simple coordinate pick — skip line-split logic
            if (currentMode === 'pick_centroid') {
                bridge.on_element_placed(currentMode, e.latlng.lat, e.latlng.lng);
            // Check if clicking near an existing line → auto-split
            } else {
                var nearest = _findNearestLineAny(e.latlng);
                if (nearest) {
                    _clearLineSplitHighlight();
                    bridge.on_element_placed_on_line(
                        currentMode,
                        nearest.projectedLatLng.lat, nearest.projectedLatLng.lng,
                        nearest.lineType, nearest.lineId, nearest.segmentIndex
                    );
                } else {
                    // Normal placement
                    if (currentMode === 'add_fuel_entry') {
                        bridge.on_fuel_entry_placed(e.latlng.lat, e.latlng.lng);
                    } else {
                        bridge.on_element_placed(currentMode, e.latlng.lat, e.latlng.lng);
                    }
                }
            }
        } else if (currentMode === 'add_line') {
            _onMapClickForTrace(e);
        } else if (currentMode === 'select') {
            bridge.on_element_deselected();
            clearSelection();
        }
    });

    map.on('mousemove', function(e) {
        _updateTraceRubberBand(e.latlng);
        _updateLineSplitHighlight(e.latlng);
    });

    // ESC key cancels current mode
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            cancelPolylineTrace();
            // Explicitly disable any active L.Draw control. Without this,
            // leaflet-draw keeps its mouse handlers bound and the Python
            // side never learns the draw was cancelled.
            if (currentMode === 'draw_domain_polygon'
                && domainPolygonDrawControl) {
                domainPolygonDrawControl.disable();
            } else if (currentMode === 'draw_rectangle'
                && rectDrawControl) {
                rectDrawControl.disable();
            } else if (currentMode === 'draw_zone' && drawControl) {
                drawControl.disable();
            }
            if (currentMode !== 'select') {
                setMode('select');
                bridge.on_mode_reset();
            }
        }
    });
}

// ── Mode Management ───────────────────────────────────────────────

function setMode(mode) {
    currentMode = mode;

    // Cancel any in-progress trace when mode changes
    if (mode !== 'add_line') {
        cancelPolylineTrace();
    }

    var container = map.getContainer();
    if (mode !== 'select') {
        container.style.cursor = 'crosshair';
    } else {
        container.style.cursor = '';
    }

    // Mode indicator overlay
    var indicator = document.getElementById('mode-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'mode-indicator';
        indicator.style.cssText = 'position:absolute;top:10px;left:50%;transform:translateX(-50%);'
            + 'background:rgba(0,0,0,0.75);color:#fff;padding:6px 16px;border-radius:4px;'
            + 'font-size:13px;z-index:9999;pointer-events:none;display:none;font-family:sans-serif;';
        document.body.appendChild(indicator);
    }
    var labels = {
        'add_line': 'Click an element to start drawing a transmission line (ESC to cancel)',
        'add_generator': 'Click on map to place generators (ESC to stop)',
        'add_battery': 'Click on map to place energy storage units (ESC to stop)',
        'add_transformer': 'Click on map to place transformers (ESC to stop)',
        'add_fuel_entry': 'Click on map to place fuel entries (ESC to stop)',
        'add_fuel_storage': 'Click on map to place fuel storages (ESC to stop)',
        'add_electrolyzer': 'Click on map to place electrolyzers (ESC to stop)',
        'add_bus': 'Click on map to place buses (ESC to stop)',
        'add_acdc_converter': 'Click on map to place AC/DC converters (ESC to stop)',
        'add_freq_converter': 'Click on map to place frequency converters (ESC to stop)',
        'pick_centroid': 'Click on map to set node centroid (ESC to cancel)',
        'draw_zone': 'Click to draw a development zone polygon',
        'draw_rectangle': 'Click and drag to draw the analysis domain',
    };
    if (labels[mode]) {
        indicator.textContent = labels[mode];
        indicator.style.display = 'block';
    } else {
        indicator.style.display = 'none';
    }
}

function enablePolygonDraw() {
    if (!drawControl) {
        drawControl = new L.Draw.Polygon(map, {
            shapeOptions: { color: '#2ecc71', fillOpacity: 0.15, weight: 2 }
        });
    }
    drawControl.enable();
    currentMode = 'draw_zone';
}

function disablePolygonDraw() {
    if (drawControl) drawControl.disable();
    currentMode = 'select';
}

// ── Rectangle Drawing (Workflows) ───────────────────────────────

function enableRectangleDraw() {
    if (!rectDrawControl) {
        rectDrawControl = new L.Draw.Rectangle(map, {
            shapeOptions: { color: '#e74c3c', fillOpacity: 0.08, weight: 2, dashArray: '6,4' }
        });
    }
    rectDrawControl.enable();
    currentMode = 'draw_rectangle';
}

function disableRectangleDraw() {
    if (rectDrawControl) rectDrawControl.disable();
    currentMode = 'select';
}

// ── Domain Polygon Drawing (Grid Mapping Workflow) ──────────────

var domainPolygonDrawControl = null;
var domainPolygonLayer = null;

function enableDomainPolygonDraw() {
    if (!domainPolygonDrawControl) {
        domainPolygonDrawControl = new L.Draw.Polygon(map, {
            shapeOptions: { color: '#3498db', fillOpacity: 0.08, weight: 2, dashArray: '6,4' }
        });
    }
    domainPolygonDrawControl.enable();
    currentMode = 'draw_domain_polygon';
}

function disableDomainPolygonDraw() {
    if (domainPolygonDrawControl) domainPolygonDrawControl.disable();
    currentMode = 'select';
}

function showDomainPolygon(coordsJson) {
    clearDomainPolygon();
    var coords = typeof coordsJson === 'string' ? JSON.parse(coordsJson) : coordsJson;
    domainPolygonLayer = L.polygon(coords, {
        color: '#3498db', fillOpacity: 0.06, weight: 2, dashArray: '6,4'
    }).addTo(map);
}

function clearDomainPolygon() {
    if (domainPolygonLayer) {
        map.removeLayer(domainPolygonLayer);
        domainPolygonLayer = null;
    }
}

// ── Rooftop Analysis Layers ─────────────────────────────────────

function showRooftopDomain(south, west, north, east) {
    clearRooftopDomain();
    rooftopDomainLayer = L.rectangle(
        [[south, west], [north, east]],
        { color: '#e74c3c', fillOpacity: 0.05, weight: 2, dashArray: '8,4' }
    ).addTo(map);
}

function clearRooftopDomain() {
    if (rooftopDomainLayer) {
        map.removeLayer(rooftopDomainLayer);
        rooftopDomainLayer = null;
    }
}

function showRooftopResults(geojsonStr) {
    clearRooftopResults();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    rooftopResultsLayer = L.geoJSON(geojson, {
        style: function(feature) {
            var val = feature.properties.specific_yield || 0;
            // Color scale: low (blue) -> mid (yellow) -> high (red)
            var ratio = Math.min(val / 1800, 1.0);  // normalize to ~1800 kWh/kWp max
            var r = Math.round(255 * ratio);
            var g = Math.round(255 * (1 - Math.abs(ratio - 0.5) * 2));
            var b = Math.round(255 * (1 - ratio));
            return {
                fillColor: 'rgb(' + r + ',' + g + ',' + b + ')',
                fillOpacity: 0.7,
                weight: 0.5,
                color: '#333',
                opacity: 0.6
            };
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>Building</b><br>'
                + 'Capacity: ' + (p.capacity_kw || 0).toFixed(1) + ' kWp<br>'
                + 'Annual yield: ' + (p.annual_kwh || 0).toFixed(0) + ' kWh<br>'
                + 'Specific yield: ' + (p.specific_yield || 0).toFixed(0) + ' kWh/kWp<br>'
                + 'Roof area: ' + (p.usable_roof_area || 0).toFixed(0) + ' m²';
            if (p.shading_loss !== undefined) {
                tip += '<br>Shading loss: ' + (p.shading_loss * 100).toFixed(1) + '%';
            }
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearRooftopResults() {
    if (rooftopResultsLayer) {
        map.removeLayer(rooftopResultsLayer);
        rooftopResultsLayer = null;
    }
}

// ── OTEC Analysis Layers ────────────────────────────────────────

function showOTECDomain(south, west, north, east) {
    clearOTECDomain();
    otecDomainLayer = L.rectangle(
        [[south, west], [north, east]],
        { color: '#2980b9', fillOpacity: 0.05, weight: 2, dashArray: '8,4' }
    ).addTo(map);
}

function clearOTECDomain() {
    if (otecDomainLayer) {
        map.removeLayer(otecDomainLayer);
        otecDomainLayer = null;
    }
}

function showOTECResults(geojsonStr) {
    clearOTECResults();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    otecResultsLayer = L.geoJSON(geojson, {
        pointToLayer: function(feature, latlng) {
            var lcoe = feature.properties.lcoe || 999;
            // Color scale: green (low LCOE) -> yellow -> red (high LCOE)
            // Typical OTEC LCOE range: 0.05 - 0.40 $/kWh
            var ratio = Math.min(Math.max((lcoe - 0.05) / 0.35, 0), 1.0);
            var r = Math.round(255 * ratio);
            var g = Math.round(255 * (1 - ratio));
            var b = 50;
            var color = 'rgb(' + r + ',' + g + ',' + b + ')';

            return L.circleMarker(latlng, {
                radius: 6,
                fillColor: color,
                color: '#333',
                weight: 1,
                fillOpacity: 0.85
            });
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>OTEC Site</b><br>'
                + 'LCOE: <b>' + (p.lcoe ? p.lcoe.toFixed(3) : '?') + ' $/kWh</b><br>'
                + 'Net Power: ' + (p.net_power ? (p.net_power/1000).toFixed(1) : '?') + ' MW<br>'
                + 'Cap. Factor: ' + (p.capacity_factor ? (p.capacity_factor*100).toFixed(1) : '?') + '%<br>'
                + 'T<sub>warm</sub>: ' + (p.t_warm ? p.t_warm.toFixed(1) : '?') + ' °C<br>'
                + 'T<sub>cold</sub>: ' + (p.t_cold ? p.t_cold.toFixed(1) : '?') + ' °C<br>'
                + 'ΔT: ' + (p.delta_t ? p.delta_t.toFixed(1) : '?') + ' °C<br>'
                + 'Depth: ' + (p.depth ? p.depth.toFixed(0) : '?') + ' m';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearOTECResults() {
    if (otecResultsLayer) {
        map.removeLayer(otecResultsLayer);
        otecResultsLayer = null;
    }
}

function showOTECDevZones(geojsonStr) {
    clearOTECDevZones();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    otecDevZonesLayer = L.geoJSON(geojson, {
        style: function(feature) {
            return {
                fillColor: '#27ae60',
                fillOpacity: 0.20,
                color: '#1e8449',
                weight: 2,
                dashArray: '5,3'
            };
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>' + (p.zone_id || 'OTEC Zone') + '</b><br>'
                + 'Area: ' + (p.area_km2 ? p.area_km2.toFixed(1) : '?') + ' km²<br>'
                + 'Sites: ' + (p.num_sites || '?') + '<br>'
                + 'Avg LCOE: ' + (p.avg_lcoe ? p.avg_lcoe.toFixed(3) : '?') + ' $/kWh<br>'
                + 'Min LCOE: ' + (p.min_lcoe ? p.min_lcoe.toFixed(3) : '?') + ' $/kWh<br>'
                + 'Capacity: ' + (p.total_capacity_mw ? p.total_capacity_mw.toFixed(1) : '?') + ' MW';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearOTECDevZones() {
    if (otecDevZonesLayer) {
        map.removeLayer(otecDevZonesLayer);
        otecDevZonesLayer = null;
    }
}

// ── Wind Assessment Layers ──────────────────────────────────────

function showWindDomain(south, west, north, east) {
    clearWindDomain();
    windDomainLayer = L.rectangle(
        [[south, west], [north, east]],
        { color: '#8e44ad', fillOpacity: 0.05, weight: 2, dashArray: '8,4' }
    ).addTo(map);
}

function clearWindDomain() {
    if (windDomainLayer) {
        map.removeLayer(windDomainLayer);
        windDomainLayer = null;
    }
}

function showWindResults(geojsonStr) {
    clearWindResults();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    windResultsLayer = L.geoJSON(geojson, {
        pointToLayer: function(feature, latlng) {
            var score = feature.properties.mcda_score || 0;
            // Color scale: blue (low) -> green (mid) -> red (high score = best)
            var ratio = Math.min(Math.max(score, 0), 1.0);
            var r, g, b;
            if (ratio < 0.5) {
                r = Math.round(50 + 160 * (ratio / 0.5));
                g = Math.round(100 + 155 * (ratio / 0.5));
                b = Math.round(200 * (1 - ratio / 0.5));
            } else {
                r = Math.round(210 + 45 * ((ratio - 0.5) / 0.5));
                g = Math.round(255 * (1 - (ratio - 0.5) / 0.5));
                b = 30;
            }
            var color = 'rgb(' + r + ',' + g + ',' + b + ')';

            return L.circleMarker(latlng, {
                radius: 6,
                fillColor: color,
                color: '#333',
                weight: 1,
                fillOpacity: 0.85
            });
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>Wind Site</b><br>'
                + 'MCDA Score: <b>' + (p.mcda_score != null ? p.mcda_score.toFixed(3) : '?') + '</b><br>'
                + 'Cap. Factor: ' + (p.capacity_factor != null ? (p.capacity_factor*100).toFixed(1) : '?') + '%<br>'
                + 'Elevation: ' + (p.elevation != null ? p.elevation.toFixed(0) : '?') + ' m<br>'
                + 'Slope: ' + (p.slope != null ? p.slope.toFixed(1) : '?') + '°<br>'
                + 'LULC Score: ' + (p.lulc_score != null ? p.lulc_score.toFixed(2) : '?') + '<br>'
                + 'Dist. to Grid: ' + (p.dist_grid_km != null ? p.dist_grid_km.toFixed(1) : '?') + ' km';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearWindResults() {
    if (windResultsLayer) {
        map.removeLayer(windResultsLayer);
        windResultsLayer = null;
    }
}

function showWindDevZones(geojsonStr) {
    clearWindDevZones();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    windDevZonesLayer = L.geoJSON(geojson, {
        style: function(feature) {
            return {
                fillColor: '#2980b9',
                fillOpacity: 0.20,
                color: '#1a5276',
                weight: 2,
                dashArray: '5,3'
            };
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>' + (p.zone_id || 'Wind Zone') + '</b><br>'
                + 'Area: ' + (p.area_km2 ? p.area_km2.toFixed(1) : '?') + ' km²<br>'
                + 'Sites: ' + (p.num_sites || '?') + '<br>'
                + 'Avg CF: ' + (p.avg_cf ? (p.avg_cf*100).toFixed(1) : '?') + '%<br>'
                + 'Avg MCDA: ' + (p.avg_mcda ? p.avg_mcda.toFixed(3) : '?') + '<br>'
                + 'Capacity: ' + (p.total_capacity_mw ? p.total_capacity_mw.toFixed(1) : '?') + ' MW';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearWindDevZones() {
    if (windDevZonesLayer) {
        map.removeLayer(windDevZonesLayer);
        windDevZonesLayer = null;
    }
}

// ── Solar PV Assessment Layers ─────────────────────────────────

function showSolarPVDomain(south, west, north, east) {
    clearSolarPVDomain();
    solarPVDomainLayer = L.rectangle(
        [[south, west], [north, east]],
        { color: '#e67e22', fillOpacity: 0.05, weight: 2, dashArray: '8,4' }
    ).addTo(map);
}

function clearSolarPVDomain() {
    if (solarPVDomainLayer) {
        map.removeLayer(solarPVDomainLayer);
        solarPVDomainLayer = null;
    }
}

function showSolarPVResults(geojsonStr) {
    clearSolarPVResults();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    solarPVResultsLayer = L.geoJSON(geojson, {
        pointToLayer: function(feature, latlng) {
            var score = feature.properties.mcda_score || 0;
            // Color scale: yellow (low) -> orange (mid) -> red (high score = best)
            var ratio = Math.min(Math.max(score, 0), 1.0);
            var r, g, b;
            if (ratio < 0.5) {
                r = Math.round(255);
                g = Math.round(230 - 80 * (ratio / 0.5));
                b = Math.round(50 * (1 - ratio / 0.5));
            } else {
                r = Math.round(255 - 55 * ((ratio - 0.5) / 0.5));
                g = Math.round(150 - 120 * ((ratio - 0.5) / 0.5));
                b = Math.round(0);
            }
            var color = 'rgb(' + r + ',' + g + ',' + b + ')';

            return L.circleMarker(latlng, {
                radius: 6,
                fillColor: color,
                color: '#333',
                weight: 1,
                fillOpacity: 0.85
            });
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>Solar PV Site</b><br>'
                + 'MCDA Score: <b>' + (p.mcda_score != null ? p.mcda_score.toFixed(3) : '?') + '</b><br>'
                + 'Cap. Factor: ' + (p.capacity_factor != null ? (p.capacity_factor*100).toFixed(1) : '?') + '%<br>'
                + 'GHI: ' + (p.ghi_kwh_m2 != null ? p.ghi_kwh_m2.toFixed(0) : '?') + ' kWh/m²/yr<br>'
                + 'Elevation: ' + (p.elevation != null ? p.elevation.toFixed(0) : '?') + ' m<br>'
                + 'Slope: ' + (p.slope != null ? p.slope.toFixed(1) : '?') + '°<br>'
                + 'LULC Score: ' + (p.lulc_score != null ? p.lulc_score.toFixed(2) : '?') + '<br>'
                + 'Dist. to Grid: ' + (p.dist_grid_km != null ? p.dist_grid_km.toFixed(1) : '?') + ' km';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearSolarPVResults() {
    if (solarPVResultsLayer) {
        map.removeLayer(solarPVResultsLayer);
        solarPVResultsLayer = null;
    }
}

function showSolarPVDevZones(geojsonStr) {
    clearSolarPVDevZones();
    var geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
    solarPVDevZonesLayer = L.geoJSON(geojson, {
        style: function(feature) {
            return {
                fillColor: '#e67e22',
                fillOpacity: 0.20,
                color: '#a04000',
                weight: 2,
                dashArray: '5,3'
            };
        },
        onEachFeature: function(feature, layer) {
            var p = feature.properties;
            var tip = '<b>' + (p.zone_id || 'Solar PV Zone') + '</b><br>'
                + 'Area: ' + (p.area_km2 ? p.area_km2.toFixed(1) : '?') + ' km²<br>'
                + 'Sites: ' + (p.num_sites || '?') + '<br>'
                + 'Avg CF: ' + (p.avg_cf ? (p.avg_cf*100).toFixed(1) : '?') + '%<br>'
                + 'Avg MCDA: ' + (p.avg_mcda ? p.avg_mcda.toFixed(3) : '?') + '<br>'
                + 'Capacity: ' + (p.total_capacity_mw ? p.total_capacity_mw.toFixed(1) : '?') + ' MW';
            layer.bindPopup(tip);
        }
    }).addTo(map);
}

function clearSolarPVDevZones() {
    if (solarPVDevZonesLayer) {
        map.removeLayer(solarPVDevZonesLayer);
        solarPVDevZonesLayer = null;
    }
}

// ── Demand Distribution Layers ─────────────────────────────────

function showDemandDomain(south, west, north, east) {
    clearDemandDomain();
    demandDomainLayer = L.rectangle(
        [[south, west], [north, east]],
        { color: '#8e44ad', fillOpacity: 0.05, weight: 2, dashArray: '8,4' }
    ).addTo(map);
}

function clearDemandDomain() {
    if (demandDomainLayer) {
        map.removeLayer(demandDomainLayer);
        demandDomainLayer = null;
    }
}

function showDemandClusters(pointsJson) {
    clearDemandClusters();
    var points = typeof pointsJson === 'string' ? JSON.parse(pointsJson) : pointsJson;
    demandClustersLayer = L.layerGroup();
    for (var i = 0; i < points.length; i++) {
        var p = points[i];
        L.circleMarker([p.lat, p.lng], {
            radius: 4,
            fillColor: p.color,
            color: '#333',
            weight: 0.5,
            fillOpacity: 0.8
        }).bindPopup(
            '<b>Cluster ' + p.cluster_id + '</b>'
        ).addTo(demandClustersLayer);
    }
    demandClustersLayer.addTo(map);
}

function clearDemandClusters() {
    if (demandClustersLayer) {
        map.closePopup();
        map.removeLayer(demandClustersLayer);
        demandClustersLayer = null;
    }
}

// ── Snap Detection ───────────────────────────────────────────────

function _findNearestMagnetic(latlng, excludeKey) {
    var snapPixels = 20;
    var best = null;
    var bestDist = Infinity;
    var clickPt = map.latLngToContainerPoint(latlng);

    for (var key in magneticElements) {
        if (key === excludeKey) continue;
        var elem = magneticElements[key];
        if (!elem.marker) continue;
        var pos = elem.marker.getLatLng();
        var pt = map.latLngToContainerPoint(pos);
        var dx = pt.x - clickPt.x;
        var dy = pt.y - clickPt.y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < snapPixels && dist < bestDist) {
            bestDist = dist;
            best = elem;
        }
    }
    return best;
}

// ── Polyline Trace Drawing ───────────────────────────────────────

// Element types allowed as endpoints for each trace mode
var _ELECTRICAL_TYPES = ['bus', 'generator', 'battery', 'transformer',
                         'electrolyzer', 'acdc_converter', 'freq_converter'];
var _FUEL_TYPES = ['fuel_entry', 'fuel_storage'];

function _onMagneticClickForTrace(elemType, elemId, marker) {
    if (currentMode !== 'add_line' && currentMode !== 'add_fuel_route') return false;

    // Filter: only allow appropriate element types per mode
    if (currentMode === 'add_line' && _ELECTRICAL_TYPES.indexOf(elemType) < 0) return false;
    if (currentMode === 'add_fuel_route' && _FUEL_TYPES.indexOf(elemType) < 0) return false;

    if (!traceStartRef) {
        // Start trace — set guard to prevent map click from adding spurious waypoint
        traceJustFinished = true;  // reuse flag: prevents next map click
        startPolylineTrace(elemType, elemId, marker);
        return true;
    } else {
        // Finish trace
        var startKey = traceStartRef.type + ':' + traceStartRef.id;
        var endKey = elemType + ':' + elemId;
        if (startKey === endKey) return true; // same element, ignore
        finishPolylineTrace(elemType, elemId, marker);
        return true;
    }
}

function startPolylineTrace(elemType, elemId, marker) {
    traceStartRef = { type: elemType, id: String(elemId) };
    traceWaypoints = [];

    // Highlight start marker
    if (marker && marker._icon) {
        L.DomUtil.addClass(marker._icon, 'marker-selected');
    } else if (marker && marker.setStyle) {
        marker._preSelectStyle = { weight: marker.options.weight, opacity: marker.options.opacity };
        marker.setStyle({ weight: 4, opacity: 0.9 });
    }

    // Create trace polyline
    var startPos = marker.getLatLng();
    tracePolyline = L.polyline([startPos], {
        color: '#e67e22',
        weight: 3,
        dashArray: '8, 6',
        opacity: 0.8,
    }).addTo(map);

    // Rubber band
    traceRubberBand = L.polyline([startPos, startPos], {
        color: '#e67e22',
        weight: 2,
        dashArray: '4, 4',
        opacity: 0.5,
    }).addTo(map);

    // Update mode indicator
    var indicator = document.getElementById('mode-indicator');
    if (indicator) {
        indicator.textContent = 'Click to add waypoints, click a magnetic element to finish (ESC to cancel)';
    }
}

function _onMapClickForTrace(e) {
    if (!traceStartRef) return; // no trace in progress, ignore
    // Guard: if trace was just finished by a magnetic click, skip this map click
    if (traceJustFinished) { traceJustFinished = false; return; }
    // Add waypoint
    addTraceWaypoint(e.latlng.lat, e.latlng.lng);
}

function addTraceWaypoint(lat, lng) {
    if (!traceStartRef || !tracePolyline) return;
    traceWaypoints.push({ lat: lat, lng: lng });
    tracePolyline.addLatLng([lat, lng]);
}

function finishPolylineTrace(endType, endId, endMarker) {
    if (!traceStartRef || !tracePolyline) return;

    // Set guard before cleanup to prevent map click from adding a spurious waypoint
    traceJustFinished = true;

    // Build waypoints JSON
    var waypointsJson = JSON.stringify(traceWaypoints);

    // Notify Python (dispatch based on current mode)
    if (currentMode === 'add_fuel_route') {
        bridge.on_fuel_route_trace_completed(
            traceStartRef.type, traceStartRef.id,
            endType, String(endId),
            waypointsJson
        );
    } else {
        bridge.on_polyline_trace_completed(
            traceStartRef.type, traceStartRef.id,
            endType, String(endId),
            waypointsJson
        );
    }

    // Clean up trace visuals
    _cleanupTrace();
}

function cancelPolylineTrace() {
    _cleanupTrace();
}

function _cleanupTrace() {
    // Un-highlight start marker
    if (traceStartRef) {
        var startKey = traceStartRef.type + ':' + traceStartRef.id;
        var startElem = magneticElements[startKey];
        if (startElem && startElem.marker) {
            if (startElem.marker._icon) {
                L.DomUtil.removeClass(startElem.marker._icon, 'marker-selected');
            } else if (startElem.marker.setStyle && startElem.marker._preSelectStyle) {
                startElem.marker.setStyle(startElem.marker._preSelectStyle);
                delete startElem.marker._preSelectStyle;
            }
        }
    }

    if (tracePolyline) {
        map.removeLayer(tracePolyline);
        tracePolyline = null;
    }
    if (traceRubberBand) {
        map.removeLayer(traceRubberBand);
        traceRubberBand = null;
    }
    traceStartRef = null;
    traceWaypoints = [];

    // Reset mode indicator if still in add_line mode
    if (currentMode === 'add_line') {
        var indicator = document.getElementById('mode-indicator');
        if (indicator) {
            indicator.textContent = 'Click a magnetic element to start drawing a line (ESC to cancel)';
        }
    }
}

function _updateTraceRubberBand(latlng) {
    if (!traceRubberBand || !traceStartRef) return;
    // Last fixed point
    var lastPt;
    if (traceWaypoints.length > 0) {
        var wp = traceWaypoints[traceWaypoints.length - 1];
        lastPt = L.latLng(wp.lat, wp.lng);
    } else {
        var startKey = traceStartRef.type + ':' + traceStartRef.id;
        var startElem = magneticElements[startKey];
        if (startElem && startElem.marker) {
            lastPt = startElem.marker.getLatLng();
        } else {
            return;
        }
    }
    traceRubberBand.setLatLngs([lastPt, latlng]);
}

// ── Selection ─────────────────────────────────────────────────────

function clearSelection() {
    if (selectedElement) {
        var el = selectedElement;
        selectedElement = null;
        if (el.marker && el.marker._icon) {
            L.DomUtil.removeClass(el.marker._icon, 'marker-selected');
        } else if (el.marker && el.marker.setStyle && el.marker._preSelectStyle) {
            el.marker.setStyle(el.marker._preSelectStyle);
            delete el.marker._preSelectStyle;
        }
    }
}

function selectElement(type, id, marker) {
    clearSelection();
    selectedElement = { type: type, id: id, marker: marker };
    if (marker && marker._icon) {
        L.DomUtil.addClass(marker._icon, 'marker-selected');
    } else if (marker && marker.setStyle) {
        marker._preSelectStyle = { weight: marker.options.weight, opacity: marker.options.opacity };
        marker.setStyle({ weight: 4, opacity: 0.9 });
    }
    bridge.on_element_selected(type, String(id));
}

// ── Style Helpers ─────────────────────────────────────────────────
//
// Flat, minimalist markers following electrical schematic symbology.
// Each element category has its own dedicated SVG renderer in
// _markerRenderers. Shapes are clean outlines with a colored fill,
// no gradients, no bevels.

// Fallback defaults — overwritten by theme.py via generate_map_js_colors()
var _defaultColors = {
    'gen-marker-renewable': '#27ae60',
    'gen-marker-nonrenewable': '#7f8c8d',
    'bat-marker': '#f39c12',
    'fuel-marker': '#e74c3c',
    'transformer-marker': '#9b59b6',
    'fuel-storage-marker': '#d35400',
    'electrolyzer-marker': '#16a085',
    'acdc-marker': '#2980b9',
    'freq-marker': '#8e44ad',
    'bus-marker': '#34495e'
};

// Labels toggle state
var _labelsVisible = false;

// ── Per-category SVG renderers ────────────────────────────────────
//
// Each renderer returns a complete SVG string for the given size and
// fill color.  The approach: white background circle/shape + colored
// stroke + category symbol inside (letter, schematic, etc.).

var _markerRenderers = {

    // Generator: circle with letter "G"
    'gen-marker-renewable': function(s, color) {
        var h = s/2, r = h - 1.5;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+h+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+h+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.5)+'" fill="'+color+'">G</text>'
            + '</svg>';
    },

    'gen-marker-nonrenewable': function(s, color) {
        var h = s/2, r = h - 1.5;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+h+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+h+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.5)+'" fill="'+color+'">G</text>'
            + '</svg>';
    },

    // Battery: standard schematic symbol (two parallel plates, one wider)
    'bat-marker': function(s, color) {
        var h = s/2;
        var lw = s*0.38, sw = s*0.22;  // long plate, short plate widths (half)
        var gap = s*0.08;              // gap between plates
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+h+'" cy="'+h+'" r="'+(h-1.5)+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            // leads
            + '<line x1="'+(s*0.18)+'" y1="'+h+'" x2="'+(h-gap)+'" y2="'+h+'" stroke="'+color+'" stroke-width="1.5"/>'
            + '<line x1="'+(h+gap)+'" y1="'+h+'" x2="'+(s*0.82)+'" y2="'+h+'" stroke="'+color+'" stroke-width="1.5"/>'
            // long plate (left)
            + '<line x1="'+(h-gap)+'" y1="'+(h-lw)+'" x2="'+(h-gap)+'" y2="'+(h+lw)+'" stroke="'+color+'" stroke-width="2"/>'
            // short plate (right)
            + '<line x1="'+(h+gap)+'" y1="'+(h-sw)+'" x2="'+(h+gap)+'" y2="'+(h+sw)+'" stroke="'+color+'" stroke-width="2"/>'
            + '</svg>';
    },

    // Busbar: horizontal thick bar
    'bus-marker': function(s, color) {
        var h = s/2;
        var barH = s * 0.2;
        var pad = s * 0.12;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<rect x="'+pad+'" y="'+(h - barH/2)+'" width="'+(s - 2*pad)+'" height="'+barH+'"'
            + ' rx="2" fill="'+color+'" stroke="'+color+'" stroke-width="0.5"/>'
            + '</svg>';
    },

    // Transformer: two overlapping circles (standard IEC symbol)
    'transformer-marker': function(s, color) {
        var h = s/2;
        var r = s * 0.26;
        var offset = s * 0.14;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+(h-offset)+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<circle cx="'+(h+offset)+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '</svg>';
    },

    // Fuel entry: circle with flame-like "F"
    'fuel-marker': function(s, color) {
        var h = s/2, r = h - 1.5;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+h+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+h+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.5)+'" fill="'+color+'">F</text>'
            + '</svg>';
    },

    // Fuel storage: rounded rectangle (tank)
    'fuel-storage-marker': function(s, color) {
        var h = s/2;
        var pad = s*0.15;
        var w = s - 2*pad, th = s*0.55;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<rect x="'+pad+'" y="'+(h-th/2)+'" width="'+w+'" height="'+th+'" rx="'+(th*0.3)+'"'
            + ' fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+h+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.32)+'" fill="'+color+'">FS</text>'
            + '</svg>';
    },

    // Electrolyzer: circle with "H₂"
    'electrolyzer-marker': function(s, color) {
        var h = s/2, r = h - 1.5;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<circle cx="'+h+'" cy="'+h+'" r="'+r+'" fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+(h-s*0.02)+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.35)+'" fill="'+color+'">H</text>'
            + '<text x="'+(h+s*0.18)+'" y="'+(h+s*0.18)+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.22)+'" fill="'+color+'">2</text>'
            + '</svg>';
    },

    // AC/DC converter: square with "~=" symbol
    'acdc-marker': function(s, color) {
        var h = s/2;
        var pad = s*0.1;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<rect x="'+pad+'" y="'+pad+'" width="'+(s-2*pad)+'" height="'+(s-2*pad)+'" rx="3"'
            + ' fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            // Diagonal divider
            + '<line x1="'+pad+'" y1="'+(s-pad)+'" x2="'+(s-pad)+'" y2="'+pad+'" stroke="'+color+'" stroke-width="1.2"/>'
            // "~" top-left
            + '<text x="'+(h-s*0.12)+'" y="'+(h-s*0.12)+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.35)+'" fill="'+color+'">~</text>'
            // "=" bottom-right
            + '<text x="'+(h+s*0.12)+'" y="'+(h+s*0.14)+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.3)+'" fill="'+color+'">=</text>'
            + '</svg>';
    },

    // Frequency converter: square with "Hz"
    'freq-marker': function(s, color) {
        var h = s/2;
        var pad = s*0.1;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'" viewBox="0 0 '+s+' '+s+'"'
            + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'
            + '<rect x="'+pad+'" y="'+pad+'" width="'+(s-2*pad)+'" height="'+(s-2*pad)+'" rx="3"'
            + ' fill="#fff" stroke="'+color+'" stroke-width="2"/>'
            + '<text x="'+h+'" y="'+h+'" text-anchor="middle" dominant-baseline="central"'
            + ' font-family="Arial,sans-serif" font-weight="700" font-size="'+(s*0.35)+'" fill="'+color+'">Hz</text>'
            + '</svg>';
    }
};

// Legacy shape renderer (used by VisualStyleWidget custom shapes)
function _svgShape(shape, s, fillColor, borderColor) {
    var bc = borderColor || fillColor;
    var bw = 2;
    var half = s / 2;
    var svg;

    switch (shape) {
        case 'circle':
            var r = half - bw;
            svg = '<circle cx="'+half+'" cy="'+half+'" r="'+r+'" fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'"/>';
            break;
        case 'square':
            svg = '<rect x="'+bw/2+'" y="'+bw/2+'" width="'+(s-bw)+'" height="'+(s-bw)+'" rx="3"'
                + ' fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'"/>';
            break;
        case 'diamond':
            svg = '<rect x="'+bw/2+'" y="'+bw/2+'" width="'+(s-bw)+'" height="'+(s-bw)+'" rx="2"'
                + ' fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'"'
                + ' transform="rotate(45 '+half+' '+half+')"/>';
            break;
        case 'triangle-up': {
            var m = bw/2;
            var pts = ''+half+','+m+' '+(s-m)+','+(s-m)+' '+m+','+(s-m);
            svg = '<polygon points="'+pts+'" fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'" stroke-linejoin="round"/>';
            break;
        }
        case 'triangle-down': {
            var m = bw/2;
            var pts = ''+m+','+m+' '+(s-m)+','+m+' '+half+','+(s-m);
            svg = '<polygon points="'+pts+'" fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'" stroke-linejoin="round"/>';
            break;
        }
        case 'hexagon': {
            var pts = [];
            for (var i = 0; i < 6; i++) {
                var angle = Math.PI/6 + i*Math.PI/3;
                var r = half - bw;
                pts.push((half+r*Math.cos(angle)).toFixed(1)+','+(half+r*Math.sin(angle)).toFixed(1));
            }
            svg = '<polygon points="'+pts.join(' ')+'" fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'" stroke-linejoin="round"/>';
            break;
        }
        case 'horizontal-bar':
            var h2 = s * 0.55; var y = (s-h2)/2;
            svg = '<rect x="'+bw/2+'" y="'+y+'" width="'+(s-bw)+'" height="'+h2+'" rx="'+(h2/2)+'"'
                + ' fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'"/>';
            break;
        default:
            var r = half - bw;
            svg = '<circle cx="'+half+'" cy="'+half+'" r="'+r+'" fill="#fff" stroke="'+bc+'" stroke-width="'+bw+'"/>';
    }

    return '<svg xmlns="http://www.w3.org/2000/svg" width="'+s+'" height="'+s+'"'
         + ' viewBox="0 0 '+s+' '+s+'"'
         + ' style="filter:drop-shadow(0 1px 2px rgba(0,0,0,0.3))">'+svg+'</svg>';
}

// Kept for compatibility with VisualStyleWidget
var _defaultShapes = {
    'gen-marker-renewable': 'circle',
    'gen-marker-nonrenewable': 'circle',
    'bat-marker': 'circle',
    'fuel-marker': 'circle',
    'transformer-marker': 'circle',
    'fuel-storage-marker': 'horizontal-bar',
    'electrolyzer-marker': 'circle',
    'acdc-marker': 'square',
    'freq-marker': 'square',
    'bus-marker': 'horizontal-bar'
};

function _makeIcon(cssClass, size, style) {
    var s = (style && style.size) ? style.size : size;
    var color = (style && style.color) ? style.color : _defaultColors[cssClass] || '#888';

    // Use custom shape if user overrode it in VisualStyleWidget
    if (style && style.shape) {
        var html = _svgShape(style.shape, s, color);
        return L.divIcon({ className: '', html: html, iconSize: [s, s], iconAnchor: [s/2, s/2] });
    }

    // Use category-specific electrical schematic renderer
    var renderer = _markerRenderers[cssClass];
    if (renderer) {
        var html = renderer(s, color);
        return L.divIcon({ className: '', html: html, iconSize: [s, s], iconAnchor: [s/2, s/2] });
    }

    // Fallback to generic shape
    var shape = _defaultShapes[cssClass] || 'circle';
    var html = _svgShape(shape, s, color);
    return L.divIcon({ className: '', html: html, iconSize: [s, s], iconAnchor: [s/2, s/2] });
}

// ── Marker Labels ─────────────────────────────────────────────────

function _bindMarkerTooltip(marker, hoverLabel, shortLabel, size) {
    marker._hoverLabel = hoverLabel;
    marker._shortLabel = shortLabel;
    marker._markerSize = size;
    if (_labelsVisible) {
        marker.bindTooltip(shortLabel, {
            permanent: true, direction: 'bottom',
            offset: [0, size / 2 + 2],
            className: 'marker-label'
        });
    } else {
        marker.bindTooltip(hoverLabel, { sticky: true });
    }
}

function toggleMarkerLabels(show) {
    _labelsVisible = !!show;
    var registries = [
        generatorMarkers, batteryMarkers, fuelEntryMarkers,
        transformerMarkers, fuelStorageMarkers, electrolyzerMarkers,
        acdcConverterMarkers, freqConverterMarkers, busMarkers
    ];
    for (var ri = 0; ri < registries.length; ri++) {
        var reg = registries[ri];
        for (var id in reg) {
            if (!reg.hasOwnProperty(id)) continue;
            var m = reg[id];
            m.unbindTooltip();
            // Canvas circleMarkers: always hover-only (permanent labels
            // on canvas create DOM divs, defeating the purpose)
            if (m instanceof L.CircleMarker && !(m instanceof L.Marker)) {
                if (m._hoverLabel) m.bindTooltip(m._hoverLabel, { sticky: true });
            } else if (_labelsVisible && m._shortLabel) {
                m.bindTooltip(m._shortLabel, {
                    permanent: true, direction: 'bottom',
                    offset: [0, (m._markerSize || 14) / 2 + 2],
                    className: 'marker-label'
                });
            } else if (m._hoverLabel) {
                m.bindTooltip(m._hoverLabel, { sticky: true });
            }
        }
    }
}

// ── Dynamic Style & Position Updates ──────────────────────────────

// Map element type to their default CSS class for style resolution
var _elementTypeCssClass = {
    'generator': 'gen-marker-renewable',  // overridden below for non-renewable
    'battery': 'bat-marker',
    'fuel_entry': 'fuel-marker',
    'transformer': 'transformer-marker',
    'fuel_storage': 'fuel-storage-marker',
    'electrolyzer': 'electrolyzer-marker',
    'acdc_converter': 'acdc-marker',
    'freq_converter': 'freq-marker',
    'bus': 'bus-marker'
};

function updateMarkerStyle(elementType, elementId, styleJson) {
    var style = (typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson;

    var marker = null;
    if (elementType === 'generator') marker = generatorMarkers[elementId];
    else if (elementType === 'battery') marker = batteryMarkers[elementId];
    else if (elementType === 'fuel_entry') marker = fuelEntryMarkers[elementId];
    else if (elementType === 'transformer') marker = transformerMarkers[elementId];
    else if (elementType === 'fuel_storage') marker = fuelStorageMarkers[elementId];
    else if (elementType === 'electrolyzer') marker = electrolyzerMarkers[elementId];
    else if (elementType === 'acdc_converter') marker = acdcConverterMarkers[elementId];
    else if (elementType === 'freq_converter') marker = freqConverterMarkers[elementId];
    else if (elementType === 'bus') marker = busMarkers[elementId];
    if (!marker) return;

    // Canvas circleMarker — update fill/radius directly
    if (marker instanceof L.CircleMarker && !(marker instanceof L.Marker)) {
        var opts = {};
        if (style.color) opts.fillColor = style.color;
        if (style.size) opts.radius = Math.max(3, style.size / 4);
        marker.setStyle(opts);
        return;
    }

    // Resolve original CSS class for this element (preserves default shape/color)
    var cssClass = marker._cssClassHint || _elementTypeCssClass[elementType] || '';
    var s = style.size || 14;
    marker.setIcon(_makeIcon(cssClass, s, style));
}

function updateTransmissionLineStyle(lineId, styleJson) {
    var style = (typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson;
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    var opts = {};
    if (style.color) opts.color = style.color;
    if (typeof style.width === 'number' && style.width > 0) opts.weight = style.width;
    if (style.opacity !== undefined && style.opacity !== null) {
        opts.opacity = style.opacity;
        pl._baseOpacity = style.opacity;
    }
    pl.setStyle(opts);
    // Halo follows color so its (very faint) visual hint matches the
    // line; weight stays at the click-capture width.
    if (pl._haloPolyline && opts.color) {
        pl._haloPolyline.setStyle({ color: opts.color });
    }
}

function updateZoneStyle(zoneId, styleJson) {
    var style = (typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson;
    var pg = zonePolygons[zoneId];
    if (!pg) return;
    var opts = {};
    if (style.color) opts.color = style.color;
    if (style.opacity !== undefined && style.opacity !== null) opts.fillOpacity = style.opacity;
    pg.setStyle(opts);
}

function updateMarkerPosition(elementType, elementId, lat, lng) {
    var marker = null;
    if (elementType === 'generator') marker = generatorMarkers[elementId];
    else if (elementType === 'battery') marker = batteryMarkers[elementId];
    else if (elementType === 'fuel_entry') marker = fuelEntryMarkers[elementId];
    else if (elementType === 'transformer') marker = transformerMarkers[elementId];
    else if (elementType === 'fuel_storage') marker = fuelStorageMarkers[elementId];
    else if (elementType === 'electrolyzer') marker = electrolyzerMarkers[elementId];
    else if (elementType === 'acdc_converter') marker = acdcConverterMarkers[elementId];
    else if (elementType === 'freq_converter') marker = freqConverterMarkers[elementId];
    else if (elementType === 'bus') marker = busMarkers[elementId];
    if (marker) marker.setLatLng([lat, lng]);
}

function updateMarkerTooltip(type, id, text) {
    var reg = {generator: generatorMarkers, battery: batteryMarkers,
               fuel_entry: fuelEntryMarkers, transformer: transformerMarkers,
               fuel_storage: fuelStorageMarkers, electrolyzer: electrolyzerMarkers,
               acdc_converter: acdcConverterMarkers, freq_converter: freqConverterMarkers,
               bus: busMarkers};
    var store = reg[type];
    if (store && store[id]) {
        var m = store[id];
        m._hoverLabel = text;
        // Extract short label (name part before parenthesis)
        var paren = text.indexOf(' (');
        m._shortLabel = paren > 0 ? text.substring(0, paren) : text;
        m.setTooltipContent(_labelsVisible ? m._shortLabel : text);
    }
}

function updateLineTooltip(lineId, text) {
    var pl = transmissionLinePolylines[lineId];
    if (pl) pl.setTooltipContent(text);
}

function updateFuelRouteTooltip(routeId, text) {
    var pl = fuelRoutePolylines[routeId];
    if (pl) pl.setTooltipContent(text);
}

// ── Line Operations ───────────────────────────────────────────────

// Width (in pixels) of the invisible click-capture halo behind each
// transmission line.  At 14 px the line is comfortable to hit even
// when the visible polyline is only 1.5 px thin or the segment is
// extremely short (e.g. bus → transformer at 20 m).  The halo is
// transparent (opacity 0.001 — keeps Leaflet listening for hits;
// truly opacity:0 sometimes drops events on certain renderers).
var _LINE_HALO_WIDTH = 14;

function addTransmissionLine(lineId, coordsJson, capacityMw, styleJson) {
    var coords = (typeof coordsJson === 'string') ? JSON.parse(coordsJson) : coordsJson;
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var weight = (style && typeof style.width === 'number' && style.width > 0)
        ? style.width
        : 2.5;
    var color = (style && style.color) ? style.color : '#e67e22';
    var opacity = (style && style.opacity !== undefined) ? style.opacity : 0.7;

    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    if (latlngs.length < 2) return;

    // Visible line — purely decorative; pointer events disabled so
    // the halo is the only event target.
    var polyline = L.polyline(latlngs, {
        color: color, weight: weight, opacity: opacity,
        interactive: false,
    });
    polyline.addTo(layers.transmissionLines);

    // Invisible click-capture halo — wider, owns the tooltip and
    // click / context-menu handlers.  Hovering it bumps the visible
    // line opacity so the user gets feedback that they've found the
    // hit area.
    var halo = L.polyline(latlngs, {
        color: color, weight: _LINE_HALO_WIDTH, opacity: 0.001,
        lineCap: 'round', interactive: true, bubblingMouseEvents: false,
    });
    halo.bindTooltip(lineId + ': ' + capacityMw.toFixed(0) + ' MW', { sticky: true });
    halo.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        selectElement('line', lineId, null);
    });
    halo.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('line', lineId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    halo.on('mouseover', function() {
        polyline.setStyle({ opacity: Math.min(1.0, opacity + 0.3) });
    });
    halo.on('mouseout', function() {
        polyline.setStyle({ opacity: opacity });
    });
    halo.addTo(layers.transmissionLines);

    // Track both so removal / endpoint updates apply to both at once.
    polyline._haloPolyline = halo;
    polyline._baseOpacity = opacity;
    transmissionLinePolylines[lineId] = polyline;
}

function removeTransmissionLine(lineId) {
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    if (pl._haloPolyline) layers.transmissionLines.removeLayer(pl._haloPolyline);
    layers.transmissionLines.removeLayer(pl);
    delete transmissionLinePolylines[lineId];
}

function updateTransmissionLineEndpoint(lineId, endpointIndex, lat, lng) {
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    var latlngs = pl.getLatLngs();
    if (endpointIndex === 0) {
        latlngs[0] = L.latLng(lat, lng);
    } else {
        latlngs[latlngs.length - 1] = L.latLng(lat, lng);
    }
    pl.setLatLngs(latlngs);
    if (pl._haloPolyline) pl._haloPolyline.setLatLngs(latlngs);
}

function updateTransmissionLineCoords(lineId, coordsJson) {
    var coords = (typeof coordsJson === 'string') ? JSON.parse(coordsJson) : coordsJson;
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    pl.setLatLngs(latlngs);
    if (pl._haloPolyline) pl._haloPolyline.setLatLngs(latlngs);
}

// ── Generator Operations ──────────────────────────────────────────

function addGeneratorMarker(genKey, lat, lng, name, genType, ratedPowerMw, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var defaultClass = genType === 'Renewable' ? 'gen-marker-renewable' : 'gen-marker-nonrenewable';
    var size = (style && style.size) ? style.size : Math.max(18, Math.min(30, 18 + ratedPowerMw / 200));
    var icon = _makeIcon(defaultClass, size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 500 });
    _bindMarkerTooltip(marker, name + ' (' + ratedPowerMw.toFixed(0) + ' MW)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('generator', genKey, marker)) return;
        selectElement('generator', genKey, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('generator', genKey, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('generator', genKey, pos)) {
            bridge.on_element_dragged('generator', String(genKey), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = defaultClass;
    marker.addTo(layers.generators);
    generatorMarkers[genKey] = marker;
    _registerMagnetic('generator', genKey, marker, nodeIndex);
}

function removeGeneratorMarker(genKey) {
    var m = generatorMarkers[genKey];
    if (m) { layers.generators.removeLayer(m); delete generatorMarkers[genKey]; }
    _unregisterMagnetic('generator', genKey);
}

// ── Battery Operations ────────────────────────────────────────────

function addBatteryMarker(batKey, lat, lng, name, capacityMwh, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(18, Math.min(30, 18 + capacityMwh / 2000));
    var icon = _makeIcon('bat-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 400 });
    _bindMarkerTooltip(marker, name + ' (' + capacityMwh.toFixed(0) + ' MWh)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('battery', batKey, marker)) return;
        selectElement('battery', batKey, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('battery', batKey, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('battery', batKey, pos)) {
            bridge.on_element_dragged('battery', String(batKey), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'bat-marker';
    marker.addTo(layers.batteries);
    batteryMarkers[batKey] = marker;
    _registerMagnetic('battery', batKey, marker, nodeIndex);
}

function removeBatteryMarker(batKey) {
    var m = batteryMarkers[batKey];
    if (m) { layers.batteries.removeLayer(m); delete batteryMarkers[batKey]; }
    _unregisterMagnetic('battery', batKey);
}

// ── Development Zone Operations ───────────────────────────────────

function addDevelopmentZone(zoneId, coords, name, technology, color, opacity) {
    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    var fillOp = (opacity !== undefined && opacity !== null) ? opacity : 0.15;
    var polygon = L.polygon(latlngs, { color: color || '#2ecc71', fillOpacity: fillOp, weight: 2 });
    polygon.bindTooltip(name + ' (' + technology + ')', { sticky: true });
    polygon.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        selectElement('zone', zoneId, null);
    });
    polygon.addTo(layers.developmentZones);
    zonePolygons[zoneId] = polygon;
}

function updateZonePolygon(zoneId, coordsJson) {
    var coords = (typeof coordsJson === 'string') ? JSON.parse(coordsJson) : coordsJson;
    var pg = zonePolygons[zoneId];
    if (!pg) return;
    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    pg.setLatLngs(latlngs);
}

function enableZoneEditing(zoneId) {
    var pg = zonePolygons[zoneId];
    if (!pg) return;
    pg.editing.enable();
    pg.on('edit', function() {
        var latlngs = pg.getLatLngs()[0];
        var coords = latlngs.map(function(ll) { return [ll.lat, ll.lng]; });
        bridge.on_zone_edited(zoneId, JSON.stringify(coords));
    });
}

function disableZoneEditing(zoneId) {
    var pg = zonePolygons[zoneId];
    if (!pg) return;
    pg.editing.disable();
    pg.off('edit');
}

// ── Polyline Trace Editing ──────────────────────────────────────

function enableLineEditing(lineId) {
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    // Hide the halo so its 14 px hit area doesn't capture clicks
    // intended for the vertex / mid-edge edit handles. Also flip the
    // visible polyline to interactive so Leaflet.Editable can attach.
    if (pl._haloPolyline) {
        layers.transmissionLines.removeLayer(pl._haloPolyline);
    }
    pl.options.interactive = true;
    if (pl._path) pl._path.style.pointerEvents = 'visiblePainted';
    pl.editing.enable();
    pl.on('edit', function() {
        var latlngs = pl.getLatLngs();
        var coords = latlngs.map(function(ll) { return [ll.lat, ll.lng]; });
        bridge.on_line_edited(lineId, JSON.stringify(coords));
    });
}

function disableLineEditing(lineId) {
    var pl = transmissionLinePolylines[lineId];
    if (!pl) return;
    pl.editing.disable();
    pl.off('edit');
    // Restore halo and revert the visible line to non-interactive.
    pl.options.interactive = false;
    if (pl._path) pl._path.style.pointerEvents = 'none';
    if (pl._haloPolyline) {
        // Re-sync halo geometry in case the user moved vertices.
        pl._haloPolyline.setLatLngs(pl.getLatLngs());
        pl._haloPolyline.addTo(layers.transmissionLines);
    }
}

function enableFuelRouteEditing(routeId) {
    var pl = fuelRoutePolylines[routeId];
    if (!pl) return;
    if (pl._haloPolyline) layers.fuelTransport.removeLayer(pl._haloPolyline);
    pl.options.interactive = true;
    if (pl._path) pl._path.style.pointerEvents = 'visiblePainted';
    pl.editing.enable();
    pl.on('edit', function() {
        var latlngs = pl.getLatLngs();
        var coords = latlngs.map(function(ll) { return [ll.lat, ll.lng]; });
        bridge.on_fuel_route_edited(routeId, JSON.stringify(coords));
    });
}

function disableFuelRouteEditing(routeId) {
    var pl = fuelRoutePolylines[routeId];
    if (!pl) return;
    pl.editing.disable();
    pl.off('edit');
    pl.options.interactive = false;
    if (pl._path) pl._path.style.pointerEvents = 'none';
    if (pl._haloPolyline) {
        pl._haloPolyline.setLatLngs(pl.getLatLngs());
        pl._haloPolyline.addTo(layers.fuelTransport);
    }
}

// ── Base Map Switching ──────────────────────────────────────────

// Offline base map: rendered from a bundled Natural Earth 1:110m
// countries GeoJSON. No tile servers, no network. The geometry loads
// once on first activation and is cached in ``_offlineGeoCache``.
var _offlineGeoCache = null;
var _offlineGeoLoading = null;

function _createOfflineBaseMap() {
    // LayerGroup acts as a host: the GeoJSON sub-layer is injected
    // (and reused) when the user switches to "Offline".
    var group = L.layerGroup();
    group._isOffline = true;
    return group;
}

function _ensureOfflineGeoJSON(group) {
    // Dedicated pane below the default overlayPane (zIndex 400) so the
    // country polygons never paint over markers, lines or zones — they
    // behave like a tile layer would.
    if (!map.getPane('offlineBasePane')) {
        var pane = map.createPane('offlineBasePane');
        pane.style.zIndex = 200;  // same band as tilePane
        pane.style.pointerEvents = 'none';
    }
    if (_offlineGeoCache) {
        if (!group.hasLayer(_offlineGeoCache)) {
            group.addLayer(_offlineGeoCache);
        }
        return Promise.resolve(_offlineGeoCache);
    }
    if (_offlineGeoLoading) return _offlineGeoLoading;
    _offlineGeoLoading = fetch('world_countries.geojson')
        .then(function(r) { return r.json(); })
        .then(function(geo) {
            _offlineGeoCache = L.geoJSON(geo, {
                pane: 'offlineBasePane',
                style: {
                    color: '#666',          // border colour
                    weight: 0.5,
                    fillColor: '#e8e4d8',   // land beige
                    fillOpacity: 1.0,
                },
                interactive: false,
            });
            group.addLayer(_offlineGeoCache);
            return _offlineGeoCache;
        })
        .catch(function(err) {
            console.error('Offline base map load failed:', err);
        })
        .finally(function() { _offlineGeoLoading = null; });
    return _offlineGeoLoading;
}

function _applyOfflineBackground(active) {
    var el = map && map.getContainer();
    if (!el) return;
    if (active) {
        el.dataset.prevBg = el.style.background || '';
        el.style.background = '#aad3df';  // ocean blue
    } else if (el.dataset.prevBg !== undefined) {
        el.style.background = el.dataset.prevBg;
        delete el.dataset.prevBg;
    }
}

function setBaseMap(name) {
    if (currentBaseMap) {
        if (currentBaseMap._isOffline) _applyOfflineBackground(false);
        map.removeLayer(currentBaseMap);
    }
    if (baseMaps[name]) {
        currentBaseMap = baseMaps[name];
        currentBaseMap.addTo(map);
        if (currentBaseMap._isOffline) {
            _applyOfflineBackground(true);
            _ensureOfflineGeoJSON(currentBaseMap);
        }
    }
}

function removeDevelopmentZone(zoneId) {
    var pg = zonePolygons[zoneId];
    if (pg) {
        if (pg.editing) pg.editing.disable();
        layers.developmentZones.removeLayer(pg);
        delete zonePolygons[zoneId];
    }
}

// ── Fuel Entry Point Operations ───────────────────────────────────

function addFuelEntryMarker(entryId, lat, lng, name, fuel, maxAvailability, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + (maxAvailability || 0) / 100));
    var icon = _makeIcon('fuel-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 300 });
    _bindMarkerTooltip(marker, name + ' (' + fuel + ')', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('fuel_entry', entryId, marker)) return;
        selectElement('fuel_entry', entryId, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('fuel_entry', entryId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('fuel_entry', entryId, pos)) {
            bridge.on_element_dragged('fuel_entry', String(entryId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'fuel-marker';
    marker.addTo(layers.fuelEntryPoints);
    fuelEntryMarkers[entryId] = marker;
    _registerMagnetic('fuel_entry', entryId, marker, nodeIndex);
}

function removeFuelEntryMarker(entryId) {
    var m = fuelEntryMarkers[entryId];
    if (m) { layers.fuelEntryPoints.removeLayer(m); delete fuelEntryMarkers[entryId]; }
    _unregisterMagnetic('fuel_entry', entryId);
}

// ── Transformer Operations ────────────────────────────────────────

function addTransformerMarker(trId, lat, lng, name, ratedPowerMva, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + (ratedPowerMva || 0) / 100));
    var icon = _makeIcon('transformer-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 600 });
    _bindMarkerTooltip(marker, name, name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('transformer', trId, marker)) return;
        selectElement('transformer', trId, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('transformer', trId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('transformer', trId, pos)) {
            bridge.on_element_dragged('transformer', String(trId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'transformer-marker';
    marker.addTo(layers.transformers);
    transformerMarkers[trId] = marker;
    _registerMagnetic('transformer', trId, marker, nodeIndex);
}

function removeTransformerMarker(trId) {
    var m = transformerMarkers[trId];
    if (m) { layers.transformers.removeLayer(m); delete transformerMarkers[trId]; }
    _unregisterMagnetic('transformer', trId);
}

// ── Fuel Storage Marker Operations ───────────────────────────────

var fuelStorageMarkers = {};

function addFuelStorageMarker(storageId, lat, lng, name, fuel, capacity, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + (capacity || 0) / 100));
    var icon = _makeIcon('fuel-storage-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 300 });
    _bindMarkerTooltip(marker, name + ' (' + fuel + ')', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('fuel_storage', storageId, marker)) return;
        selectElement('fuel_storage', storageId, null);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('fuel_storage', storageId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('fuel_storage', storageId, pos)) {
            bridge.on_element_dragged('fuel_storage', String(storageId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'fuel-storage-marker';
    marker.addTo(layers.fuelEntryPoints);
    fuelStorageMarkers[storageId] = marker;
    _registerMagnetic('fuel_storage', storageId, marker, nodeIndex);
}

function removeFuelStorageMarker(storageId) {
    var m = fuelStorageMarkers[storageId];
    if (m) { layers.fuelEntryPoints.removeLayer(m); delete fuelStorageMarkers[storageId]; }
    _unregisterMagnetic('fuel_storage', storageId);
}

// ── Electrolyzer Marker Operations ──────────────────────────────

function addElectrolyzerMarker(elId, lat, lng, name, ratedPower, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + ratedPower / 100));
    var icon = _makeIcon('electrolyzer-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 500 });
    _bindMarkerTooltip(marker, name + ' (' + ratedPower + ' MW)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('electrolyzer', elId, marker)) return;
        selectElement('electrolyzer', elId, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('electrolyzer', elId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('electrolyzer', elId, pos)) {
            bridge.on_element_dragged('electrolyzer', String(elId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'electrolyzer-marker';
    marker.addTo(layers.electrolyzers);
    electrolyzerMarkers[elId] = marker;
    _registerMagnetic('electrolyzer', elId, marker, nodeIndex);
}

function removeElectrolyzerMarker(elId) {
    var m = electrolyzerMarkers[elId];
    if (m) { layers.electrolyzers.removeLayer(m); delete electrolyzerMarkers[elId]; }
    _unregisterMagnetic('electrolyzer', elId);
}

// ── AC/DC Converter Marker Operations ───────────────────────────

function addACDCConverterMarker(convId, lat, lng, name, ratedPower, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + ratedPower / 100));
    var icon = _makeIcon('acdc-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 500 });
    _bindMarkerTooltip(marker, name + ' (' + ratedPower + ' MVA)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('acdc_converter', convId, marker)) return;
        selectElement('acdc_converter', String(convId), marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('acdc_converter', String(convId), e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('acdc_converter', convId, pos)) {
            bridge.on_element_dragged('acdc_converter', String(convId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'acdc-marker';
    marker.addTo(layers.acdcConverters);
    acdcConverterMarkers[convId] = marker;
    _registerMagnetic('acdc_converter', convId, marker, nodeIndex);
}

function removeACDCConverterMarker(convId) {
    var m = acdcConverterMarkers[convId];
    if (m) { layers.acdcConverters.removeLayer(m); delete acdcConverterMarkers[convId]; }
    _unregisterMagnetic('acdc_converter', convId);
}

// ── Frequency Converter Marker Operations ───────────────────────

function addFreqConverterMarker(convId, lat, lng, name, ratedPower, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : Math.max(16, Math.min(28, 16 + ratedPower / 100));
    var icon = _makeIcon('freq-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 500 });
    _bindMarkerTooltip(marker, name + ' (' + ratedPower + ' MVA)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('freq_converter', convId, marker)) return;
        selectElement('freq_converter', String(convId), marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('freq_converter', String(convId), e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('freq_converter', convId, pos)) {
            bridge.on_element_dragged('freq_converter', String(convId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'freq-marker';
    marker.addTo(layers.freqConverters);
    freqConverterMarkers[convId] = marker;
    _registerMagnetic('freq_converter', convId, marker, nodeIndex);
}

function removeFreqConverterMarker(convId) {
    var m = freqConverterMarkers[convId];
    if (m) { layers.freqConverters.removeLayer(m); delete freqConverterMarkers[convId]; }
    _unregisterMagnetic('freq_converter', convId);
}

// ── Bus Marker Operations ────────────────────────────────────────

function addBusMarker(busId, lat, lng, name, voltageKv, nodeIndex, styleJson) {
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var size = (style && style.size) ? style.size : 16;
    var icon = _makeIcon('bus-marker', size, style);

    var marker = L.marker([lat, lng], { icon: icon, draggable: true, zIndexOffset: 450 });
    _bindMarkerTooltip(marker, name + ' (' + voltageKv + ' kV)', name, size);
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (_onMagneticClickForTrace('bus', busId, marker)) return;
        selectElement('bus', busId, marker);
    });
    marker.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('bus', busId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    marker.on('dragend', function(e) {
        var pos = e.target.getLatLng();
        if (!_checkDragOntoLine('bus', busId, pos)) {
            bridge.on_element_dragged('bus', String(busId), pos.lat, pos.lng);
        }
    });
    marker._cssClassHint = 'bus-marker';
    marker.addTo(layers.buses);
    busMarkers[busId] = marker;
    _registerMagnetic('bus', busId, marker, nodeIndex);
}

function removeBusMarker(busId) {
    var m = busMarkers[busId];
    if (m) { layers.buses.removeLayer(m); delete busMarkers[busId]; }
    _unregisterMagnetic('bus', busId);
}

function addFuelTransportRoute(routeId, coordsJson, fuel, capacity, styleJson) {
    var coords = (typeof coordsJson === 'string') ? JSON.parse(coordsJson) : coordsJson;
    var style = styleJson ? ((typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson) : null;
    var weight = (style && typeof style.width === 'number' && style.width > 0)
        ? style.width
        : 2.5;
    var color = (style && style.color) ? style.color : '#c0392b';
    var opacity = (style && style.opacity !== undefined) ? style.opacity : 0.7;

    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    if (latlngs.length < 2) return;

    var polyline = L.polyline(latlngs, {
        color: color, weight: weight, opacity: opacity,
        dashArray: '10, 6', interactive: false,
    });
    polyline.addTo(layers.fuelTransport);

    // Same halo pattern as transmission lines.
    var halo = L.polyline(latlngs, {
        color: color, weight: _LINE_HALO_WIDTH, opacity: 0.001,
        lineCap: 'round', interactive: true, bubblingMouseEvents: false,
    });
    var label = routeId + ': ' + fuel + ' (' + capacity.toFixed(0) + ')';
    halo.bindTooltip(label, { sticky: true });
    halo.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        selectElement('fuel_route', routeId, null);
    });
    halo.on('contextmenu', function(e) {
        L.DomEvent.stopPropagation(e);
        L.DomEvent.preventDefault(e);
        _showMarkerContextMenu('fuel_route', routeId, e.originalEvent.clientX, e.originalEvent.clientY);
    });
    halo.on('mouseover', function() {
        polyline.setStyle({ opacity: Math.min(1.0, opacity + 0.3) });
    });
    halo.on('mouseout', function() {
        polyline.setStyle({ opacity: opacity });
    });
    halo.addTo(layers.fuelTransport);

    polyline._haloPolyline = halo;
    polyline._baseOpacity = opacity;
    fuelRoutePolylines[routeId] = polyline;
}

function removeFuelTransportRoute(routeId) {
    var pl = fuelRoutePolylines[routeId];
    if (!pl) return;
    if (pl._haloPolyline) layers.fuelTransport.removeLayer(pl._haloPolyline);
    layers.fuelTransport.removeLayer(pl);
    delete fuelRoutePolylines[routeId];
}

function updateFuelTransportRouteCoords(routeId, coordsJson) {
    var coords = (typeof coordsJson === 'string') ? JSON.parse(coordsJson) : coordsJson;
    var pl = fuelRoutePolylines[routeId];
    if (!pl) return;
    var latlngs = coords.map(function(c) { return [c[0], c[1]]; });
    pl.setLatLngs(latlngs);
    if (pl._haloPolyline) pl._haloPolyline.setLatLngs(latlngs);
}

function updateFuelTransportRouteStyle(routeId, styleJson) {
    var style = (typeof styleJson === 'string') ? JSON.parse(styleJson) : styleJson;
    var pl = fuelRoutePolylines[routeId];
    if (!pl) return;
    var opts = {};
    if (style.color) opts.color = style.color;
    if (typeof style.width === 'number' && style.width > 0) opts.weight = style.width;
    if (style.opacity !== undefined && style.opacity !== null) {
        opts.opacity = style.opacity;
        pl._baseOpacity = style.opacity;
    }
    pl.setStyle(opts);
    if (pl._haloPolyline && opts.color) {
        pl._haloPolyline.setStyle({ color: opts.color });
    }
}

// ── Layer Visibility ──────────────────────────────────────────────

function setLayerVisibility(layerName, visible) {
    var group = layers[layerName];
    if (!group) return;
    if (visible) map.addLayer(group); else map.removeLayer(group);
}

function showElectricalLayer() {
    setLayerVisibility('generators', true);
    setLayerVisibility('batteries', true);
    setLayerVisibility('transmissionLines', true);
    setLayerVisibility('transformers', true);
    setLayerVisibility('electrolyzers', true);
    setLayerVisibility('acdcConverters', true);
    setLayerVisibility('freqConverters', true);
    setLayerVisibility('buses', true);
    setLayerVisibility('fuelEntryPoints', false);
    setLayerVisibility('developmentZones', true);
    setLayerVisibility('resultsNodes', true);
    setLayerVisibility('resultsFlows', true);
    setLayerVisibility('background', true);
}

function showPrimaryEnergyLayer() {
    setLayerVisibility('generators', false);
    setLayerVisibility('batteries', false);
    setLayerVisibility('transmissionLines', false);
    setLayerVisibility('transformers', false);
    setLayerVisibility('electrolyzers', false);
    setLayerVisibility('acdcConverters', false);
    setLayerVisibility('freqConverters', false);
    setLayerVisibility('buses', false);
    setLayerVisibility('fuelEntryPoints', true);
    setLayerVisibility('developmentZones', true);
    setLayerVisibility('resultsNodes', false);
    setLayerVisibility('resultsFlows', false);
    setLayerVisibility('background', true);
}

function showAllLayers() {
    for (var key in layers) setLayerVisibility(key, true);
}

function showResultsLayer() {
    setLayerVisibility('generators', false);
    setLayerVisibility('batteries', false);
    setLayerVisibility('transmissionLines', false);
    setLayerVisibility('transformers', false);
    setLayerVisibility('electrolyzers', false);
    setLayerVisibility('acdcConverters', false);
    setLayerVisibility('freqConverters', false);
    setLayerVisibility('fuelEntryPoints', false);
    setLayerVisibility('developmentZones', false);
    setLayerVisibility('buses', false);
    setLayerVisibility('resultsNodes', true);
    setLayerVisibility('resultsFlows', true);
    setLayerVisibility('background', false);
}

// ── Map View ──────────────────────────────────────────────────────

function setMapView(lat, lng, zoom) { map.setView([lat, lng], zoom); }
function fitBounds(south, west, north, east) { map.fitBounds([[south, west], [north, east]]); }

// Re-key an index-based marker registry after a deletion.
// Rebuilds the registry so keys become sequential 0,1,2,...
// Also updates the magnetic element registry entries.
function reindexMarkerRegistry(registryName) {
    var registries = {
        transformer: transformerMarkers,
        fuel_entry: fuelEntryMarkers,
        acdc_converter: acdcConverterMarkers,
        freq_converter: freqConverterMarkers,
    };
    var reg = registries[registryName];
    if (!reg) return;

    // Collect existing entries sorted by their old numeric key
    var entries = [];
    for (var oldKey in reg) {
        entries.push({oldKey: oldKey, marker: reg[oldKey]});
    }
    entries.sort(function(a, b) { return parseInt(a.oldKey) - parseInt(b.oldKey); });

    // Clear and rebuild with sequential keys
    for (var k in reg) delete reg[k];
    for (var i = 0; i < entries.length; i++) {
        var newKey = String(i);
        reg[newKey] = entries[i].marker;
        // Update magnetic registry
        if (entries[i].oldKey !== newKey) {
            var oldMagKey = registryName + ':' + entries[i].oldKey;
            var newMagKey = registryName + ':' + newKey;
            if (magneticElements[oldMagKey]) {
                magneticElements[newMagKey] = magneticElements[oldMagKey];
                magneticElements[newMagKey].id = newKey;
                delete magneticElements[oldMagKey];
            }
        }
    }
}

/** Set canvas mode flag for chunked loading (called before detachLayers). */
function _setCanvasMode(totalItems) {
    _usingCanvasMode = (totalItems > _CANVAS_THRESHOLD);
    if (_usingCanvasMode) {
        _lodBatchData = [];  // will accumulate items from chunks
    }
}

/** Finalize canvas mode after chunked loading (called after reattachLayers). */
function _finishCanvasMode() {
    if (_usingCanvasMode && _busClusterGroup && !_busClusterAttached) {
        // Add cluster to the buses layer group so it respects layer visibility
        if (layers.buses) {
            _busClusterGroup.addTo(layers.buses);
        } else {
            _busClusterGroup.addTo(map);
        }
        _busClusterAttached = true;
    }
    if (_usingCanvasMode) {
        _initLOD();
    }
}

/** Detach all layer groups from the map so adds don't trigger DOM reflow. */
function detachLayers() {
    map.closePopup();
    for (var key in layers) {
        if (layers[key]) map.removeLayer(layers[key]);
    }
    // Cluster will be re-attached to layers.buses on reattach
    _busClusterAttached = false;
}

/** Re-attach all layer groups to the map (single DOM reflow). */
function reattachLayers() {
    for (var key in layers) {
        if (layers[key]) layers[key].addTo(map);
    }
}

/** Parse JSON and add items without detach/reattach (caller manages that). */
function addBatchItemsFromJson(jsonStr) {
    var items;
    try { items = JSON.parse(jsonStr); } catch(e) {
        console.error('addBatchItemsFromJson: bad JSON', e);
        return;
    }
    if (_usingCanvasMode) {
        // Accumulate items for LOD detail recreation
        if (_lodBatchData) {
            for (var i = 0; i < items.length; i++) _lodBatchData.push(items[i]);
        }
        _addBatchItemsCanvas(items, 0, items.length);
    } else {
        _addBatchItems(items, 0, items.length);
    }
}

function clearAllLayers() {
    map.closePopup();

    // Teardown LOD + canvas state
    _teardownLOD();
    _canvasMarkers = {};
    _usingCanvasMode = false;
    if (_busClusterGroup) {
        map.removeLayer(_busClusterGroup);
        _busClusterGroup = null;
    }

    for (var key in layers) {
        if (layers[key]) layers[key].clearLayers();
    }
    generatorMarkers = {};
    batteryMarkers = {};
    transmissionLinePolylines = {};
    transformerMarkers = {};
    zonePolygons = {};
    fuelEntryMarkers = {};
    fuelRoutePolylines = {};
    fuelStorageMarkers = {};
    electrolyzerMarkers = {};
    acdcConverterMarkers = {};
    freqConverterMarkers = {};
    busMarkers = {};
    bgElements = {};
    magneticElements = {};
    cancelPolylineTrace();
    clearDemandDomain();
    clearDemandClusters();
}

/**
 * Batch-add all map elements in a single JS execution context.
 * Avoids N individual Qt→JS IPC crossings for large systems.
 *
 * Performance: temporarily detaches all layer groups from the map so that
 * marker.addTo(layerGroup) does NOT trigger individual DOM insertions.
 * All layers are re-attached at the end in one paint cycle.
 *
 * For very large systems (>200 elements), uses chunked requestAnimationFrame
 * processing to avoid blocking the UI thread.
 *
 * @param {string} jsonStr - JSON array of element descriptors.
 */
function loadBatchElements(jsonStr) {
    var items;
    try { items = JSON.parse(jsonStr); } catch(e) {
        console.error('loadBatchElements: bad JSON', e);
        return;
    }

    // Count point elements to decide rendering strategy
    var pointCount = 0;
    for (var i = 0; i < items.length; i++) {
        var t = items[i].type;
        if (t !== 'line' && t !== 'zone' && t !== 'fuel_route') pointCount++;
    }
    _usingCanvasMode = (pointCount > _CANVAS_THRESHOLD);

    // Close popups and detach all layer groups to prevent per-marker DOM reflow
    map.closePopup();
    for (var key in layers) {
        if (layers[key]) map.removeLayer(layers[key]);
    }

    if (_usingCanvasMode) {
        _lodBatchData = items;  // store for LOD detail recreation
        _addBatchItemsCanvas(items, 0, items.length);
    } else {
        _addBatchItems(items, 0, items.length);
    }

    // Re-attach all layer groups — single DOM reflow
    for (var key in layers) {
        if (layers[key]) layers[key].addTo(map);
    }

    // Set up canvas mode features
    if (_usingCanvasMode) {
        if (_busClusterGroup && !_busClusterAttached) {
            if (layers.buses) {
                _busClusterGroup.addTo(layers.buses);
            } else {
                _busClusterGroup.addTo(map);
            }
            _busClusterAttached = true;
        }
        _initLOD();
    }
}

function _addBatchItems(items, start, end) {
    for (var i = start; i < end; i++) {
        var el = items[i];
        try {
            switch (el.type) {
                case 'bus':
                    addBusMarker(el.id, el.lat, el.lng, el.name,
                        el.voltageKv, el.nodeIndex, el.style); break;
                case 'generator':
                    addGeneratorMarker(el.id, el.lat, el.lng, el.name,
                        el.genType, el.ratedPowerMw, el.nodeIndex, el.style); break;
                case 'battery':
                    addBatteryMarker(el.id, el.lat, el.lng, el.name,
                        el.capacityMwh, el.nodeIndex, el.style); break;
                case 'line':
                    addTransmissionLine(el.id, el.coords, el.capacityMw, el.style); break;
                case 'zone':
                    addDevelopmentZone(el.id, el.coords, el.name,
                        el.technology, el.color, el.opacity); break;
                case 'fuel_entry':
                    addFuelEntryMarker(el.id, el.lat, el.lng, el.name,
                        el.fuel, el.maxAvailability, el.nodeIndex, el.style); break;
                case 'fuel_storage':
                    addFuelStorageMarker(el.id, el.lat, el.lng, el.name,
                        el.fuel, el.capacity, el.nodeIndex, el.style); break;
                case 'fuel_route':
                    addFuelTransportRoute(el.id, el.coords, el.fuel,
                        el.capacity, el.style); break;
                case 'transformer':
                    addTransformerMarker(el.id, el.lat, el.lng, el.name,
                        el.ratedPowerMva, el.nodeIndex, el.style); break;
                case 'electrolyzer':
                    addElectrolyzerMarker(el.id, el.lat, el.lng, el.name,
                        el.ratedPower, el.nodeIndex, el.style); break;
                case 'acdc_converter':
                    addACDCConverterMarker(el.id, el.lat, el.lng, el.name,
                        el.ratedPower, el.nodeIndex, el.style); break;
                case 'freq_converter':
                    addFreqConverterMarker(el.id, el.lat, el.lng, el.name,
                        el.ratedPower, el.nodeIndex, el.style); break;
            }
        } catch(err) {
            console.warn('loadBatchElements: error adding ' + el.type + ' ' + el.id, err);
        }
    }
}

// ── Geo Assets (reference overlays) ──────────────────────────────

function addGeoAsset(assetId, geojsonData, name, color) {
    if (geoAssetLayers[assetId]) {
        map.removeLayer(geoAssetLayers[assetId]);
    }
    var layer = L.geoJSON(geojsonData, {
        style: function() {
            return {
                color: color || '#e67e22',
                weight: 2,
                opacity: 0.7,
                fillOpacity: 0.15
            };
        },
        pointToLayer: function(feature, latlng) {
            return L.circleMarker(latlng, {
                radius: 5,
                color: color || '#e67e22',
                weight: 2,
                opacity: 0.7,
                fillOpacity: 0.3
            });
        },
        onEachFeature: function(feature, layer) {
            var props = feature.properties || {};
            var tooltipText = props.name || props.NAME || props.id || name || assetId;
            layer.bindTooltip(tooltipText, {sticky: true});
        }
    });
    layer.addTo(map);
    geoAssetLayers[assetId] = layer;
}

function removeGeoAsset(assetId) {
    if (geoAssetLayers[assetId]) {
        map.removeLayer(geoAssetLayers[assetId]);
        delete geoAssetLayers[assetId];
    }
}

function setGeoAssetVisible(assetId, visible) {
    var layer = geoAssetLayers[assetId];
    if (!layer) return;
    if (visible) {
        if (!map.hasLayer(layer)) map.addLayer(layer);
    } else {
        if (map.hasLayer(layer)) map.removeLayer(layer);
    }
}

// ── Results Overlay ──────────────────────────────────────────────

var _resultsLegend = null;

function clearResultsLayer() {
    if (layers.resultsNodes) layers.resultsNodes.clearLayers();
    if (layers.resultsFlows) layers.resultsFlows.clearLayers();
    removeResultsLegend();
}

function clearResultsNodes() {
    if (layers.resultsNodes) layers.resultsNodes.clearLayers();
}

function clearResultsFlows() {
    if (layers.resultsFlows) layers.resultsFlows.clearLayers();
}

function addResultsNodeCircles(dataJson) {
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    if (!layers.resultsNodes) return;
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var circle = L.circleMarker([d.lat, d.lng], {
            radius: Math.max(4, Math.min(40, d.radius || 10)),
            fillColor: d.color || '#3498db',
            color: '#fff',
            weight: 2,
            fillOpacity: 0.75,
        });
        var tip = d.label || '';
        if (d.value !== undefined) tip += ': ' + d.value.toFixed(2);
        if (tip) circle.bindTooltip(tip, { sticky: true });
        circle.addTo(layers.resultsNodes);
    }
}

function addResultsLineOverlay(dataJson) {
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    if (!layers.resultsFlows) return;
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var latlngs = d.coords.map(function(c) { return [c[0], c[1]]; });
        if (latlngs.length < 2) continue;
        var poly = L.polyline(latlngs, {
            color: d.color || '#e74c3c',
            weight: Math.max(2, Math.min(12, d.weight || 3)),
            opacity: 0.8,
        });
        var tip = d.label || '';
        if (d.value !== undefined) tip += ': ' + d.value.toFixed(2);
        if (tip) poly.bindTooltip(tip, { sticky: true });
        poly.addTo(layers.resultsFlows);
    }
}

function addResultsLegend(title, minVal, maxVal, colorMin, colorMax) {
    removeResultsLegend();
    var legend = L.control({ position: 'bottomright' });
    legend.onAdd = function() {
        var div = L.DomUtil.create('div', 'results-legend');
        div.innerHTML =
            '<div style="font-weight:bold;margin-bottom:4px">' + title + '</div>'
            + '<div style="display:flex;align-items:center;gap:6px">'
            + '<span>' + minVal.toFixed(1) + '</span>'
            + '<div style="width:120px;height:14px;border-radius:3px;'
            + 'background:linear-gradient(to right,' + colorMin + ',' + colorMax + ')"></div>'
            + '<span>' + maxVal.toFixed(1) + '</span>'
            + '</div>';
        return div;
    };
    legend.addTo(map);
    _resultsLegend = legend;
}

function removeResultsLegend() {
    if (_resultsLegend) {
        map.removeControl(_resultsLegend);
        _resultsLegend = null;
    }
}

// ── Pie Chart Results ────────────────────────────────────────────

function _createPieSVG(segments, size) {
    /* segments = [{value, color, label}, ...] — returns SVG string */
    var total = 0;
    for (var i = 0; i < segments.length; i++) total += segments[i].value;
    if (total <= 0) return '';

    var r = size / 2;
    var cx = r, cy = r;
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + size
        + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">';

    // Drop shadow
    svg += '<defs><filter id="ps"><feDropShadow dx="0" dy="1" stdDeviation="2" flood-opacity="0.3"/></filter></defs>';
    svg += '<g filter="url(#ps)">';

    // Single-segment special case: full circle
    if (segments.length === 1) {
        svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r
            + '" fill="' + segments[0].color + '" stroke="#fff" stroke-width="1.5"/>';
        svg += '</g></svg>';
        return svg;
    }

    var angle = -Math.PI / 2;
    for (var i = 0; i < segments.length; i++) {
        var frac = segments[i].value / total;
        if (frac < 0.001) continue;
        var delta = frac * 2 * Math.PI;
        var x1 = cx + r * Math.cos(angle);
        var y1 = cy + r * Math.sin(angle);
        var x2 = cx + r * Math.cos(angle + delta);
        var y2 = cy + r * Math.sin(angle + delta);
        var large = delta > Math.PI ? 1 : 0;
        svg += '<path d="M ' + cx + ' ' + cy
            + ' L ' + x1.toFixed(2) + ' ' + y1.toFixed(2)
            + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 '
            + x2.toFixed(2) + ' ' + y2.toFixed(2) + ' Z"'
            + ' fill="' + segments[i].color + '"'
            + ' stroke="#fff" stroke-width="1"/>';
        angle += delta;
    }
    svg += '</g></svg>';
    return svg;
}

function _buildPieTooltip(title, segments) {
    /* Build HTML tooltip table for a pie chart */
    var total = 0;
    for (var i = 0; i < segments.length; i++) total += segments[i].value;
    var html = '<div style="font-weight:bold;margin-bottom:4px">' + title + '</div>';
    html += '<table style="border-collapse:collapse;font-size:11px">';
    for (var i = 0; i < segments.length; i++) {
        var s = segments[i];
        if (s.value < 0.01) continue;
        var pct = total > 0 ? (s.value / total * 100).toFixed(1) : '0.0';
        html += '<tr>'
            + '<td style="padding:1px 4px"><span style="display:inline-block;width:10px;height:10px;'
            + 'background:' + s.color + ';border-radius:2px;margin-right:4px"></span></td>'
            + '<td style="padding:1px 4px">' + s.label + '</td>'
            + '<td style="padding:1px 4px;text-align:right">' + s.value.toFixed(1) + ' MW</td>'
            + '<td style="padding:1px 4px;text-align:right;color:#888">' + pct + '%</td>'
            + '</tr>';
    }
    html += '<tr style="border-top:1px solid #ccc;font-weight:bold">'
        + '<td></td><td style="padding:2px 4px">Total</td>'
        + '<td style="padding:2px 4px;text-align:right">' + total.toFixed(1) + ' MW</td>'
        + '<td></td></tr>';
    html += '</table>';
    return html;
}

function addResultsPieCharts(dataJson) {
    /* dataJson = [{lat, lng, segments: [{value, color, label},...], size, title}, ...]
       Renders SVG pie chart markers on the results layer. */
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    if (!layers.resultsNodes) return;
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var segs = d.segments || [];
        if (segs.length === 0) continue;
        var sz = Math.max(20, Math.min(80, d.size || 40));
        var svgHtml = _createPieSVG(segs, sz);
        if (!svgHtml) continue;
        var icon = L.divIcon({
            className: 'result-pie-icon',
            html: svgHtml,
            iconSize: [sz, sz],
            iconAnchor: [sz / 2, sz / 2],
        });
        var marker = L.marker([d.lat, d.lng], {
            icon: icon,
            interactive: true,
            zIndexOffset: 800,
        });
        var tip = _buildPieTooltip(d.title || 'Generation', segs);
        marker.bindTooltip(tip, { sticky: false, direction: 'top', offset: [0, -sz / 2] });
        marker.addTo(layers.resultsNodes);
    }
}

function addResultsPieLegend(title, entriesJson) {
    /* entriesJson = [{label, color}, ...] — categorical legend for pie charts */
    removeResultsLegend();
    var entries = (typeof entriesJson === 'string') ? JSON.parse(entriesJson) : entriesJson;
    var legend = L.control({ position: 'bottomright' });
    legend.onAdd = function() {
        var div = L.DomUtil.create('div', 'results-legend');
        var html = '<div style="font-weight:bold;margin-bottom:4px">' + title + '</div>';
        for (var i = 0; i < entries.length; i++) {
            html += '<div style="display:flex;align-items:center;gap:4px;margin:2px 0">'
                + '<span style="display:inline-block;width:12px;height:12px;border-radius:2px;'
                + 'background:' + entries[i].color + '"></span>'
                + '<span style="font-size:11px">' + entries[i].label + '</span>'
                + '</div>';
        }
        div.innerHTML = html;
        return div;
    };
    legend.addTo(map);
    _resultsLegend = legend;
}

// ── Directional Flow Lines ───────────────────────────────────────

function addResultsFlowLines(dataJson) {
    /* dataJson = [{coords, weight, color, label, value, arrowDir}, ...]
       Like addResultsLineOverlay but with arrow head decorations. */
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    if (!layers.resultsFlows) return;
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var latlngs = d.coords.map(function(c) { return [c[0], c[1]]; });
        if (latlngs.length < 2) continue;
        var w = Math.max(2, Math.min(12, d.weight || 3));
        var poly = L.polyline(latlngs, {
            color: d.color || '#e74c3c',
            weight: w,
            opacity: 0.8,
        });
        var tip = d.label || '';
        if (d.value !== undefined) tip += ': ' + d.value.toFixed(1) + ' MW';
        if (tip) poly.bindTooltip(tip, { sticky: true });
        poly.addTo(layers.resultsFlows);

        // Arrow head at midpoint
        if (latlngs.length >= 2) {
            var mid = [(latlngs[0][0] + latlngs[1][0]) / 2,
                       (latlngs[0][1] + latlngs[1][1]) / 2];
            var dx = latlngs[1][1] - latlngs[0][1];
            var dy = latlngs[1][0] - latlngs[0][0];
            var angleDeg = Math.atan2(dx, dy) * 180 / Math.PI;
            var arrowSize = Math.max(8, w * 2);
            var arrowSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + arrowSize
                + '" height="' + arrowSize + '" viewBox="0 0 20 20">'
                + '<polygon points="10,2 18,18 10,14 2,18" fill="' + (d.color || '#e74c3c')
                + '" stroke="#fff" stroke-width="1" transform="rotate(' + angleDeg + ' 10 10)"/>'
                + '</svg>';
            var arrowIcon = L.divIcon({
                className: 'result-flow-arrow',
                html: arrowSvg,
                iconSize: [arrowSize, arrowSize],
                iconAnchor: [arrowSize / 2, arrowSize / 2],
            });
            L.marker(mid, { icon: arrowIcon, interactive: false }).addTo(layers.resultsFlows);
        }
    }
}

// ══════════════════════════════════════════════════════════════════
// Risk & Resilience Layer
// ══════════════════════════════════════════════════════════════════

var _riskLayerGroup = null;
var _riskLegend = null;

function _ensureRiskLayer() {
    if (!_riskLayerGroup) {
        _riskLayerGroup = L.layerGroup().addTo(map);
    }
    return _riskLayerGroup;
}

function clearRiskLayer() {
    if (_riskLayerGroup) {
        _riskLayerGroup.clearLayers();
    }
    if (_riskLegend) {
        map.removeControl(_riskLegend);
        _riskLegend = null;
    }
}

function addRiskCircles(dataJson) {
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    var layer = _ensureRiskLayer();
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var ri = d.risk_index || 0;
        var color;
        if (ri < 0.1) color = '#27ae60';
        else if (ri < 0.3) color = '#f1c40f';
        else if (ri < 0.6) color = '#e67e22';
        else color = '#e74c3c';

        var circle = L.circleMarker([d.lat, d.lng], {
            radius: d.radius || 10,
            fillColor: color,
            color: '#2c3e50',
            weight: 2,
            opacity: 0.9,
            fillOpacity: 0.6,
        });
        if (d.tooltip) {
            circle.bindTooltip(d.tooltip, { sticky: true });
        }
        if (d.label) {
            circle.bindPopup('<b>' + d.label + '</b><br/>Risk: ' + ri.toFixed(3));
        }
        circle.addTo(layer);
    }
}

function addHazardZones(dataJson) {
    var data = (typeof dataJson === 'string') ? JSON.parse(dataJson) : dataJson;
    var layer = _ensureRiskLayer();
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var polygon = L.polygon(d.coords, {
            color: d.color || '#e74c3c',
            fillColor: d.color || '#e74c3c',
            fillOpacity: d.opacity || 0.2,
            weight: 1,
        });
        if (d.label) {
            polygon.bindTooltip(d.label, { sticky: true });
        }
        polygon.addTo(layer);
    }
}

function addRiskLegend(title, entriesJson) {
    if (_riskLegend) {
        map.removeControl(_riskLegend);
    }
    var entries = (typeof entriesJson === 'string') ? JSON.parse(entriesJson) : entriesJson;
    var legend = L.control({ position: 'bottomleft' });
    legend.onAdd = function() {
        var div = L.DomUtil.create('div', 'risk-legend');
        div.style.cssText = 'background:white;padding:8px 12px;border-radius:4px;box-shadow:0 1px 5px rgba(0,0,0,0.3);font-size:12px';
        var html = '<div style="font-weight:bold;margin-bottom:4px">' + title + '</div>';
        for (var i = 0; i < entries.length; i++) {
            html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0">'
                + '<div style="width:14px;height:14px;border-radius:2px;background:'
                + entries[i].color + '"></div>'
                + '<span>' + entries[i].label + '</span></div>';
        }
        div.innerHTML = html;
        return div;
    };
    legend.addTo(map);
    _riskLegend = legend;
}
