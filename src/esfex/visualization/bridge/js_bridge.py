"""Python object exposed to JavaScript via QWebChannel."""

from PySide6.QtCore import QObject, Signal, Slot


class MapBridge(QObject):
    """Bridge between Leaflet.js map and Python GUI model.

    Exposed to JavaScript as ``window.bridge`` via QWebChannel.
    JS calls ``@Slot`` methods; Python emits ``Signal``s to propagate changes.
    """

    # Signals emitted toward the Python GUI
    mapReady = Signal()
    lineDrawn = Signal(str)                         # GeoJSON string
    zoneDrawn = Signal(str)                         # GeoJSON polygon string
    elementSelected = Signal(str, str)              # element_type, element_id
    elementDeselected = Signal()
    drawModeChanged = Signal(str)                   # mode name
    fuelEntryPlaced = Signal(float, float)          # lat, lng
    elementPlaced = Signal(str, float, float)        # mode, lat, lng
    elementDragged = Signal(str, str, float, float) # type, id, lat, lng
    polylineTraceCompleted = Signal(str, str, str, str, str)
    # from_type, from_id, to_type, to_id, waypoints_json
    fuelRouteTraceCompleted = Signal(str, str, str, str, str)
    # from_type, from_id, to_type, to_id, waypoints_json
    zoneEdited = Signal(str, str)  # zone_id, coords_json
    lineEdited = Signal(str, str)  # line_id, coords_json
    fuelRouteEdited = Signal(str, str)  # route_id, coords_json
    rectangleDrawn = Signal(str)  # JSON: {"south":..,"west":..,"north":..,"east":..}
    domainPolygonDrawn = Signal(str)  # GeoJSON polygon for grid mapping domain
    modeReset = Signal()  # ESC pressed in JS, request toolbar reset
    elementPlacedOnLine = Signal(str, float, float, str, str, int)
    # mode, lat, lng, line_type, line_id, segment_index
    elementDroppedOnLine = Signal(str, str, float, float, str, str, int)
    # element_type, element_id, lat, lng, line_type, line_id, segment_index
    markerContextAction = Signal(str, str, str)
    # action ("duplicate"|"copy"|"paste"|"delete"), element_type, element_id

    # ------------------------------------------------------------------
    # Slots callable from JavaScript
    # ------------------------------------------------------------------

    @Slot()
    def on_map_ready(self):
        """Called once Leaflet has fully initialised."""
        self.mapReady.emit()

    @Slot(str)
    def on_line_drawn(self, geojson: str):
        self.lineDrawn.emit(geojson)

    @Slot(str)
    def on_zone_drawn(self, geojson: str):
        self.zoneDrawn.emit(geojson)

    @Slot(str, str)
    def on_element_selected(self, element_type: str, element_id: str):
        self.elementSelected.emit(element_type, element_id)

    @Slot()
    def on_element_deselected(self):
        self.elementDeselected.emit()

    @Slot(float, float)
    def on_fuel_entry_placed(self, lat: float, lng: float):
        """Called when user clicks the map in add_fuel_entry mode."""
        self.fuelEntryPlaced.emit(lat, lng)

    @Slot(str, float, float)
    def on_element_placed(self, mode: str, lat: float, lng: float):
        """Called when user clicks map in an add_* element placement mode."""
        self.elementPlaced.emit(mode, lat, lng)

    @Slot(str, str, float, float)
    def on_element_dragged(self, element_type: str, element_id: str,
                           lat: float, lng: float):
        """Called when a draggable marker (gen/bat/fuel/transformer) is moved."""
        self.elementDragged.emit(element_type, element_id, lat, lng)

    @Slot(str, str, str, str, str)
    def on_polyline_trace_completed(self, from_type: str, from_id: str,
                                     to_type: str, to_id: str,
                                     waypoints_json: str):
        """Called when a polyline trace is finished (endpoint to endpoint)."""
        self.polylineTraceCompleted.emit(from_type, from_id, to_type, to_id, waypoints_json)

    @Slot(str, str, str, str, str)
    def on_fuel_route_trace_completed(self, from_type: str, from_id: str,
                                       to_type: str, to_id: str,
                                       waypoints_json: str):
        """Called when a fuel route polyline trace is finished."""
        self.fuelRouteTraceCompleted.emit(from_type, from_id, to_type, to_id, waypoints_json)

    @Slot(str, str)
    def on_zone_edited(self, zone_id: str, coords_json: str):
        """Called when polygon vertices are moved via Leaflet.Draw editing."""
        self.zoneEdited.emit(zone_id, coords_json)

    @Slot(str, str)
    def on_line_edited(self, line_id: str, coords_json: str):
        """Called when transmission line vertices are moved via editing."""
        self.lineEdited.emit(line_id, coords_json)

    @Slot(str, str)
    def on_fuel_route_edited(self, route_id: str, coords_json: str):
        """Called when fuel route vertices are moved via editing."""
        self.fuelRouteEdited.emit(route_id, coords_json)

    @Slot(str)
    def on_rectangle_drawn(self, bounds_json: str):
        """Called when a rectangle is drawn on the map (workflows)."""
        self.rectangleDrawn.emit(bounds_json)

    @Slot(str)
    def on_domain_polygon_drawn(self, geojson: str):
        """Called when a domain polygon is drawn (grid mapping workflow)."""
        self.domainPolygonDrawn.emit(geojson)

    @Slot()
    def on_mode_reset(self):
        """Called when ESC is pressed in JS to cancel the current mode."""
        self.modeReset.emit()

    @Slot(str, float, float, str, str, int)
    def on_element_placed_on_line(self, mode: str, lat: float, lng: float,
                                   line_type: str, line_id: str,
                                   segment_index: int):
        """Called when user places a new element on an existing line/route."""
        self.elementPlacedOnLine.emit(
            mode, lat, lng, line_type, line_id, segment_index
        )

    @Slot(str, str, float, float, str, str, int)
    def on_element_dropped_on_line(self, element_type: str, element_id: str,
                                    lat: float, lng: float,
                                    line_type: str, line_id: str,
                                    segment_index: int):
        """Called when an existing element is dragged onto a line/route."""
        self.elementDroppedOnLine.emit(
            element_type, element_id, lat, lng,
            line_type, line_id, segment_index
        )

    @Slot(str, str, str)
    def on_marker_context_action(self, action: str, element_type: str, element_id: str):
        """Called when the user selects an action from a marker's right-click context menu."""
        self.markerContextAction.emit(action, element_type, element_id)

    # --- Bounds retrieval (used by screenshot) ---
    boundsReady = Signal(float, float, float, float)  # south, west, north, east

    @Slot(float, float, float, float)
    def on_bounds_ready(self, south: float, west: float, north: float, east: float):
        """Called from JS with the current map viewport bounds."""
        self.boundsReady.emit(south, west, north, east)
