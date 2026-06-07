"""QWebEngineView wrapper that hosts the Leaflet.js map."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from esfex.visualization.bridge.channel import setup_channel
from esfex.visualization.bridge.js_bridge import MapBridge

if TYPE_CHECKING:
    from esfex.visualization.data.gui_model import VisualStyle

_RESOURCES_DIR = Path(__file__).parent / "resources"


def _style_to_dict(style: Optional[VisualStyle]) -> dict | None:
    """Convert a VisualStyle to a plain dict (None if empty)."""
    if style is None:
        return None
    d: dict = {}
    if style.color:
        d["color"] = style.color
    if style.size is not None:
        d["size"] = style.size
    if style.icon_shape:
        d["shape"] = style.icon_shape
    if style.opacity is not None:
        d["opacity"] = style.opacity
    if style.width is not None:
        d["width"] = style.width
    return d or None


def _style_json(style: Optional[VisualStyle]) -> str:
    """Serialize a VisualStyle to a JSON string for JavaScript."""
    d = _style_to_dict(style)
    if d is None:
        return "null"
    return json.dumps(d)


def _js_arg(value) -> str:
    """Serialize *value* to a JavaScript literal safe for ``runJavaScript``.

    Use this anywhere an f-string interpolates a Python value into a JS
    snippet. ``json.dumps`` produces a JS-compatible literal (proper
    string escaping for ``\\``, ``'``, ``"``, newlines and Unicode line
    terminators) for str / int / float / bool / list / dict / None,
    closing the XSS vector that earlier f-string interpolation of bus
    ids like ``"bus'); alert(1); //"`` opened up.

    Usage::

        self._run_js(f"removeBus({_js_arg(bus_id)})")
    """
    return json.dumps(value)


class MapWidget(QWebEngineView):
    """Interactive map based on Leaflet.js embedded in a QWebEngineView."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge: MapBridge = setup_channel(self)
        self._crash_recovered = False

        # Set User-Agent so tile servers (OSM, Carto) don't block requests
        profile = self.page().profile()
        profile.setHttpUserAgent(
            "Mozilla/5.0 (ESFEX Power System Planner) "
            "AppleWebKit/537.36 (KHTML, like Gecko) QtWebEngine"
        )

        settings = self.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        # Needed so leaflet.html can fetch() sibling assets like the
        # bundled ``world_countries.geojson`` used by the offline base map.
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )

        html_path = _RESOURCES_DIR / "leaflet.html"
        self._html_url = QUrl.fromLocalFile(str(html_path))
        self.loadFinished.connect(self._inject_theme)
        self.page().renderProcessTerminated.connect(self._on_render_crash)
        self.load(self._html_url)

    def _on_render_crash(self, termination_type, exit_code):
        """Handle WebEngine render process crash — reload the map."""
        import logging
        logging.getLogger(__name__).warning(
            "Map render process terminated (type=%s, code=%s). Reloading...",
            termination_type, exit_code,
        )
        self._crash_recovered = True
        self.load(self._html_url)

    def resizeEvent(self, event):
        """Belt-and-braces fix for the black-map-on-resize bug.

        The map_controller.js side already has a ResizeObserver on the
        #map div that calls ``map.invalidateSize()`` (debounced).  That
        is the primary fix, but two things can still leave a black
        frame:

        1. Qt may not deliver paint events to QWebEngineView while a
           Qt-side resize is in flight, so the GL frame buffer keeps
           showing stale (and after the swap, black) pixels until the
           next paint cycle.  Forcing ``self.update()`` schedules one.
        2. The ResizeObserver fires inside the page, but during the
           split second before its 50 ms debounce fires the user sees
           the stale Leaflet viewport.  Issuing ``invalidateSize`` from
           Python with a small delay collapses that gap when the
           ResizeObserver hasn't run yet.
        """
        super().resizeEvent(event)
        self.update()
        from PySide6.QtCore import QTimer
        # Two passes: one short (catches the visible black frame
        # quickly) and one longer (after Qt finishes the resize burst
        # if the user is drag-resizing).
        QTimer.singleShot(30, self._invalidate_map_size)
        QTimer.singleShot(150, self._invalidate_map_size)

    def _invalidate_map_size(self):
        """Tell Leaflet that its container's size changed.

        Guarded with ``typeof map !== 'undefined'`` because the
        leaflet.html page may not have finished loading yet — calling
        this during the initial load window would otherwise throw
        ReferenceError, which would be logged by Qt's JS error sink.
        """
        # Guard on the *method*, not just on ``map`` being truthy: a
        # browser exposes the ``<div id="map">`` element as the global
        # ``map`` until map_controller.js has run and shadowed it with the
        # Leaflet instance. During that window ``typeof map !== 'undefined'``
        # is true but ``map.invalidateSize`` is undefined → the
        # "map.invalidateSize is not a function" TypeError in the JS sink.
        self._run_js(
            "if (typeof map !== 'undefined' && map && "
            "typeof map.invalidateSize === 'function') "
            "map.invalidateSize({animate: false});"
        )

    def _inject_theme(self, ok: bool):
        """Inject theme CSS and color palette into the Leaflet page."""
        if not ok:
            return
        from esfex.visualization.theme import generate_map_css, generate_map_js_colors
        css = generate_map_css().replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(
            f"var s=document.createElement('style');"
            f"s.id='esfex-theme';"
            f"s.textContent='{css}';"
            f"document.head.appendChild(s);"
        )
        self._run_js(generate_map_js_colors())
        self._apply_map_preferences()
        # _crash_recovered stays True so main_window can detect and rebuild

    def _apply_map_preferences(self):
        """Apply map preferences (center, zoom, basemap) on first load."""
        from esfex.visualization.preferences import load_preferences, get_preference
        prefs = load_preferences()
        lat = get_preference(prefs, "map", "default_lat", 22.0)
        lng = get_preference(prefs, "map", "default_lng", -79.0)
        zoom = get_preference(prefs, "map", "default_zoom", 7)
        basemap = get_preference(prefs, "map", "default_basemap", "OpenStreetMap")
        self._run_js(f"setMapView({lat}, {lng}, {zoom})")
        self.set_base_map(basemap)


    # ------------------------------------------------------------------
    # Python -> JavaScript helpers
    # ------------------------------------------------------------------

    def _run_js(self, script: str):
        self.page().runJavaScript(script)

    # Map view
    def set_map_view(self, lat: float, lng: float, zoom: int):
        self._run_js(f"setMapView({lat}, {lng}, {zoom})")

    def get_visible_center(self) -> tuple[float, float] | None:
        """Return the current map center as (lat, lng), or None if unavailable."""
        # Use synchronous JS evaluation via a blocking event loop
        from PySide6.QtCore import QEventLoop, QTimer
        result = [None]

        def callback(val):
            result[0] = val
            loop.quit()

        loop = QEventLoop()
        self.page().runJavaScript(
            "(function(){ var c = map.getCenter(); return [c.lat, c.lng]; })()",
            callback,
        )
        QTimer.singleShot(500, loop.quit)  # Timeout after 500ms
        loop.exec()
        if isinstance(result[0], list) and len(result[0]) == 2:
            return (result[0][0], result[0][1])
        return None

    def get_visible_bounds(self) -> tuple[float, float, float, float] | None:
        """Return (south, west, north, east) of the current map viewport.

        Uses the QWebChannel bridge (reliable) instead of runJavaScript
        callbacks (which silently fail in some PySide6 builds).
        """
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

        result = [None]

        def _on_bounds(south, west, north, east):
            result[0] = (south, west, north, east)
            loop.quit()

        loop = QEventLoop()
        self.bridge.boundsReady.connect(_on_bounds)
        self._run_js(
            "(function(){"
            "  if (typeof map === 'undefined') return;"
            "  var b = map.getBounds();"
            "  bridge.on_bounds_ready(b.getSouth(), b.getWest(),"
            "                         b.getNorth(), b.getEast());"
            "})()"
        )
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        self.bridge.boundsReady.disconnect(_on_bounds)
        return result[0]

    def fit_bounds(self, south: float, west: float, north: float, east: float):
        self._run_js(f"fitBounds({south}, {west}, {north}, {east})")

    # Mode
    def set_mode(self, mode: str):
        self._run_js(f"setMode({_js_arg(mode)})")

    def enable_polygon_draw(self):
        self._run_js("enablePolygonDraw()")

    def disable_polygon_draw(self):
        self._run_js("disablePolygonDraw()")

    # ------------------------------------------------------------------
    # Transmission lines
    # ------------------------------------------------------------------

    def add_transmission_line(
        self, line_id: str,
        coords: list[tuple[float, float]],
        capacity_mw: float,
        style: Optional[VisualStyle] = None,
    ):
        sj = _style_json(style)
        coords_js = json.dumps(coords)
        self._run_js(
            f"addTransmissionLine({_js_arg(line_id)}, {coords_js}, {capacity_mw}, {sj})"
        )

    def remove_transmission_line(self, line_id: str):
        self._run_js(f"removeTransmissionLine({_js_arg(line_id)})")

    def update_line_endpoint(
        self, line_id: str, endpoint_index: int, lat: float, lng: float,
    ):
        self._run_js(
            f"updateTransmissionLineEndpoint({_js_arg(line_id)}, {endpoint_index}, {lat}, {lng})"
        )

    def update_line_coords(self, line_id: str, coords: list[tuple[float, float]]):
        coords_js = json.dumps(coords)
        self._run_js(f"updateTransmissionLineCoords({_js_arg(line_id)}, {coords_js})")

    def enable_line_editing(self, line_id: str):
        self._run_js(f"enableLineEditing({_js_arg(line_id)})")

    def disable_line_editing(self, line_id: str):
        self._run_js(f"disableLineEditing({_js_arg(line_id)})")

    # ------------------------------------------------------------------
    # Fuel transport routes
    # ------------------------------------------------------------------

    def add_fuel_transport_route(
        self, route_id: str, coords: list[tuple[float, float]],
        fuel: str, capacity: float,
        style: Optional[VisualStyle] = None,
    ):
        sj = _style_json(style)
        coords_js = json.dumps(coords)
        safe_fuel = fuel.replace("'", "\\'")
        self._run_js(
            f"addFuelTransportRoute({_js_arg(route_id)}, {coords_js}, "
            f"'{safe_fuel}', {capacity}, {sj})"
        )

    def remove_fuel_transport_route(self, route_id: str):
        self._run_js(f"removeFuelTransportRoute({_js_arg(route_id)})")

    def update_fuel_route_coords(
        self, route_id: str, coords: list[tuple[float, float]],
    ):
        coords_js = json.dumps(coords)
        self._run_js(f"updateFuelTransportRouteCoords({_js_arg(route_id)}, {coords_js})")

    def update_fuel_route_style(self, route_id: str, style: VisualStyle):
        sj = _style_json(style)
        if sj == "null":
            return
        self._run_js(f"updateFuelTransportRouteStyle({_js_arg(route_id)}, {sj})")

    def enable_fuel_route_editing(self, route_id: str):
        self._run_js(f"enableFuelRouteEditing({_js_arg(route_id)})")

    def disable_fuel_route_editing(self, route_id: str):
        self._run_js(f"disableFuelRouteEditing({_js_arg(route_id)})")

    # ------------------------------------------------------------------
    # Generators
    # ------------------------------------------------------------------

    def add_generator_marker(
        self, gen_key: str,
        lat: float, lng: float,
        name: str, gen_type: str, rated_power_mw: float,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addGeneratorMarker({_js_arg(gen_key)}, {lat}, {lng}, '{safe_name}', "
            f"'{gen_type}', {rated_power_mw}, {node_index}, {sj})"
        )

    def remove_generator_marker(self, gen_key: str):
        self._run_js(f"removeGeneratorMarker({_js_arg(gen_key)})")

    # ------------------------------------------------------------------
    # Batteries
    # ------------------------------------------------------------------

    def add_battery_marker(
        self, bat_key: str,
        lat: float, lng: float,
        name: str, capacity_mwh: float,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addBatteryMarker({_js_arg(bat_key)}, {lat}, {lng}, '{safe_name}', "
            f"{capacity_mwh}, {node_index}, {sj})"
        )

    def remove_battery_marker(self, bat_key: str):
        self._run_js(f"removeBatteryMarker({_js_arg(bat_key)})")

    # ------------------------------------------------------------------
    # Development zones
    # ------------------------------------------------------------------

    def add_development_zone(
        self, zone_id: str,
        coords: list[tuple[float, float]],
        name: str, technology: str,
        color: str = "#2ecc71",
        opacity: float | None = None,
    ):
        coords_js = json.dumps(coords)
        safe_name = name.replace("'", "\\'")
        op_arg = "null" if opacity is None else str(opacity)
        self._run_js(
            f"addDevelopmentZone({_js_arg(zone_id)}, {coords_js}, '{safe_name}', "
            f"'{technology}', '{color}', {op_arg})"
        )

    def update_zone_polygon(
        self, zone_id: str, coords: list[tuple[float, float]],
    ):
        coords_js = json.dumps(coords)
        self._run_js(f"updateZonePolygon({_js_arg(zone_id)}, {coords_js})")

    def enable_zone_editing(self, zone_id: str):
        self._run_js(f"enableZoneEditing({_js_arg(zone_id)})")

    def disable_zone_editing(self, zone_id: str):
        self._run_js(f"disableZoneEditing({_js_arg(zone_id)})")

    def remove_development_zone(self, zone_id: str):
        self._run_js(f"removeDevelopmentZone({_js_arg(zone_id)})")

    # ------------------------------------------------------------------
    # Fuel entry points
    # ------------------------------------------------------------------

    def add_fuel_entry_marker(
        self, entry_id: str,
        lat: float, lng: float,
        name: str, fuel: str,
        max_availability: float = 0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addFuelEntryMarker({_js_arg(entry_id)}, {lat}, {lng}, '{safe_name}', "
            f"'{fuel}', {max_availability}, {node_index}, {sj})"
        )

    def remove_fuel_entry_marker(self, entry_id: str):
        self._run_js(f"removeFuelEntryMarker({_js_arg(entry_id)})")

    # ------------------------------------------------------------------
    # Fuel storage
    # ------------------------------------------------------------------

    def add_fuel_storage_marker(
        self, storage_id: str,
        lat: float, lng: float,
        name: str, fuel: str,
        capacity: float = 0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        safe_fuel = fuel.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addFuelStorageMarker({_js_arg(storage_id)}, {lat}, {lng}, '{safe_name}', "
            f"'{safe_fuel}', {capacity}, {node_index}, {sj})"
        )

    def remove_fuel_storage_marker(self, storage_id: str):
        self._run_js(f"removeFuelStorageMarker({_js_arg(storage_id)})")

    # ------------------------------------------------------------------
    # Transformers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Buses
    # ------------------------------------------------------------------

    def add_bus_marker(
        self, bus_id: str, lat: float, lng: float, name: str,
        voltage_kv: float = 220.0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addBusMarker({_js_arg(bus_id)}, {lat}, {lng}, '{safe_name}', "
            f"{voltage_kv}, {node_index}, {sj})"
        )

    def remove_bus_marker(self, bus_id: str):
        self._run_js(f"removeBusMarker({_js_arg(bus_id)})")

    # ------------------------------------------------------------------
    # Transformers
    # ------------------------------------------------------------------

    def add_transformer_marker(
        self, tr_id: str, lat: float, lng: float, name: str,
        rated_power_mva: float = 0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addTransformerMarker({_js_arg(tr_id)}, {lat}, {lng}, '{safe_name}', "
            f"{rated_power_mva}, {node_index}, {sj})"
        )

    def remove_transformer_marker(self, tr_id: str):
        self._run_js(f"removeTransformerMarker({_js_arg(tr_id)})")

    def reindex_marker_registry(self, registry_name: str):
        """Re-key an index-based marker registry after a deletion."""
        self._run_js(f"reindexMarkerRegistry({_js_arg(registry_name)})")

    # ------------------------------------------------------------------
    # Electrolyzers
    # ------------------------------------------------------------------

    def add_electrolyzer_marker(
        self, el_id: str, lat: float, lng: float, name: str,
        rated_power: float = 0.0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addElectrolyzerMarker({_js_arg(el_id)}, {lat}, {lng}, '{safe_name}', "
            f"{rated_power}, {node_index}, {sj})"
        )

    def remove_electrolyzer_marker(self, el_id: str):
        self._run_js(f"removeElectrolyzerMarker({_js_arg(el_id)})")

    # ------------------------------------------------------------------
    # AC/DC Converters
    # ------------------------------------------------------------------

    def add_acdc_converter_marker(
        self, conv_id: str, lat: float, lng: float, name: str,
        rated_power: float = 0.0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addACDCConverterMarker({_js_arg(conv_id)}, {lat}, {lng}, '{safe_name}', "
            f"{rated_power}, {node_index}, {sj})"
        )

    def remove_acdc_converter_marker(self, conv_id: str):
        self._run_js(f"removeACDCConverterMarker({_js_arg(conv_id)})")

    # ------------------------------------------------------------------
    # Frequency Converters
    # ------------------------------------------------------------------

    def add_freq_converter_marker(
        self, conv_id: str, lat: float, lng: float, name: str,
        rated_power: float = 0.0,
        node_index: int = 0,
        style: Optional[VisualStyle] = None,
    ):
        safe_name = name.replace("'", "\\'")
        sj = _style_json(style)
        self._run_js(
            f"addFreqConverterMarker({_js_arg(conv_id)}, {lat}, {lng}, '{safe_name}', "
            f"{rated_power}, {node_index}, {sj})"
        )

    def remove_freq_converter_marker(self, conv_id: str):
        self._run_js(f"removeFreqConverterMarker({_js_arg(conv_id)})")

    # ------------------------------------------------------------------
    # Dynamic style updates
    # ------------------------------------------------------------------

    def update_marker_style(
        self, element_type: str, element_id: str, style: VisualStyle
    ):
        sj = _style_json(style)
        if sj == "null":
            return
        self._run_js(
            f"updateMarkerStyle('{element_type}', '{element_id}', {sj})"
        )

    def update_marker_tooltip(
        self, marker_type: str, marker_id: str, text: str
    ):
        self._run_js(
            f"updateMarkerTooltip({_js_arg(marker_type)}, "
            f"{_js_arg(marker_id)}, {_js_arg(text)})"
        )

    def update_line_tooltip(self, line_id: str, text: str):
        self._run_js(f"updateLineTooltip({_js_arg(line_id)}, {_js_arg(text)})")

    def update_fuel_route_tooltip(self, route_id: str, text: str):
        self._run_js(
            f"updateFuelRouteTooltip({_js_arg(route_id)}, {_js_arg(text)})"
        )

    def update_line_style(self, line_id: str, style: VisualStyle):
        sj = _style_json(style)
        if sj == "null":
            return
        self._run_js(f"updateTransmissionLineStyle({_js_arg(line_id)}, {sj})")

    def update_zone_style(self, zone_id: str, style: VisualStyle):
        sj = _style_json(style)
        if sj == "null":
            return
        self._run_js(f"updateZoneStyle({_js_arg(zone_id)}, {sj})")

    def update_marker_position(
        self, element_type: str, element_id: str, lat: float, lng: float
    ):
        self._run_js(
            f"updateMarkerPosition('{element_type}', '{element_id}', {lat}, {lng})"
        )

    # ------------------------------------------------------------------
    # Layer visibility
    # ------------------------------------------------------------------

    def show_electrical_layer(self):
        self._run_js("showElectricalLayer()")

    def show_primary_energy_layer(self):
        self._run_js("showPrimaryEnergyLayer()")

    def show_all_layers(self):
        self._run_js("showAllLayers()")

    # ------------------------------------------------------------------
    # Base map
    # ------------------------------------------------------------------

    def toggle_labels(self, show: bool):
        self._run_js(f"toggleMarkerLabels({'true' if show else 'false'})")

    def set_base_map(self, name: str):
        self._run_js(f"setBaseMap({_js_arg(name)})")

    # ------------------------------------------------------------------
    # Geo assets (reference overlays)
    # ------------------------------------------------------------------

    def add_geo_asset(self, asset_id: str, geojson_str: str, name: str,
                      color: str = "#e67e22"):
        safe_name = name.replace("'", "\\'")
        safe_color = color.replace("'", "\\'")
        self._run_js(
            f"addGeoAsset({_js_arg(asset_id)}, {geojson_str}, '{safe_name}', '{safe_color}')"
        )

    def remove_geo_asset(self, asset_id: str):
        self._run_js(f"removeGeoAsset({_js_arg(asset_id)})")

    def set_geo_asset_visible(self, asset_id: str, visible: bool):
        vis = "true" if visible else "false"
        self._run_js(f"setGeoAssetVisible({_js_arg(asset_id)}, {vis})")

    # ------------------------------------------------------------------
    # Rectangle drawing (workflows)
    # ------------------------------------------------------------------

    def enable_rectangle_draw(self):
        self._run_js("enableRectangleDraw()")

    def disable_rectangle_draw(self):
        self._run_js("disableRectangleDraw()")

    def enable_domain_polygon_draw(self):
        self._run_js("enableDomainPolygonDraw()")

    def disable_domain_polygon_draw(self):
        self._run_js("disableDomainPolygonDraw()")

    def show_domain_polygon(self, coords: list[tuple[float, float]]):
        import json
        self._run_js(f"showDomainPolygon({json.dumps(coords)})")

    def clear_domain_polygon(self):
        self._run_js("clearDomainPolygon()")

    def install_draw_cancel_handler(self, step, draw_button):
        """Wire ESC-cancel recovery for a workflow step's draw button.

        When the user presses ESC mid-draw, leaflet-draw releases its
        handlers (JS side) and the bridge fires ``modeReset``. Without
        this handler, the workflow's draw button stays disabled and the
        wizard window — if minimized — stays minimized, leaving the user
        with no way to retry. Pass the step widget and its draw button
        and we'll re-enable it and restore the wizard on cancel.
        """
        def _on_cancel():
            try:
                draw_button.setEnabled(True)
            except Exception:
                return
            wizard = step.window()
            if wizard and wizard.isMinimized():
                wizard.showNormal()
                wizard.raise_()
                wizard.activateWindow()
        self.bridge.modeReset.connect(_on_cancel)

    # ------------------------------------------------------------------
    # Rooftop analysis layers (workflows)
    # ------------------------------------------------------------------

    def show_rooftop_domain(self, south: float, west: float,
                            north: float, east: float):
        self._run_js(f"showRooftopDomain({south},{west},{north},{east})")

    def clear_rooftop_domain(self):
        self._run_js("clearRooftopDomain()")

    def show_rooftop_results(self, geojson_str: str):
        self._run_js(f"showRooftopResults({geojson_str})")

    def clear_rooftop_results(self):
        self._run_js("clearRooftopResults()")

    # ------------------------------------------------------------------
    # OTEC analysis layers (workflows)
    # ------------------------------------------------------------------

    def show_otec_domain(self, south: float, west: float,
                         north: float, east: float):
        self._run_js(f"showOTECDomain({south},{west},{north},{east})")

    def clear_otec_domain(self):
        self._run_js("clearOTECDomain()")

    def show_otec_results(self, geojson_str: str):
        self._run_js(f"showOTECResults({geojson_str})")

    def clear_otec_results(self):
        self._run_js("clearOTECResults()")

    def show_otec_dev_zones(self, geojson_str: str):
        self._run_js(f"showOTECDevZones({geojson_str})")

    def clear_otec_dev_zones(self):
        self._run_js("clearOTECDevZones()")

    # ------------------------------------------------------------------
    # Wind assessment layers (workflows)
    # ------------------------------------------------------------------

    def show_wind_domain(self, south: float, west: float,
                         north: float, east: float):
        self._run_js(f"showWindDomain({south},{west},{north},{east})")

    def clear_wind_domain(self):
        self._run_js("clearWindDomain()")

    def show_wind_results(self, geojson_str: str):
        self._run_js(f"showWindResults({geojson_str})")

    def clear_wind_results(self):
        self._run_js("clearWindResults()")

    def show_wind_dev_zones(self, geojson_str: str):
        self._run_js(f"showWindDevZones({geojson_str})")

    def clear_wind_dev_zones(self):
        self._run_js("clearWindDevZones()")

    # ------------------------------------------------------------------
    # Solar PV assessment layers (workflows)
    # ------------------------------------------------------------------

    def show_solar_pv_domain(self, south: float, west: float,
                             north: float, east: float):
        self._run_js(f"showSolarPVDomain({south},{west},{north},{east})")

    def clear_solar_pv_domain(self):
        self._run_js("clearSolarPVDomain()")

    def show_solar_pv_results(self, geojson_str: str):
        self._run_js(f"showSolarPVResults({geojson_str})")

    def clear_solar_pv_results(self):
        self._run_js("clearSolarPVResults()")

    def show_solar_pv_dev_zones(self, geojson_str: str):
        self._run_js(f"showSolarPVDevZones({geojson_str})")

    def clear_solar_pv_dev_zones(self):
        self._run_js("clearSolarPVDevZones()")

    # ------------------------------------------------------------------
    # Demand distribution layers (workflows)
    # ------------------------------------------------------------------

    def show_demand_domain(self, south: float, west: float,
                           north: float, east: float):
        self._run_js(f"showDemandDomain({south},{west},{north},{east})")

    def clear_demand_domain(self):
        self._run_js("clearDemandDomain()")

    def show_demand_clusters(self, points: list[dict]):
        """Show building cluster markers on the map.

        Parameters
        ----------
        points : list[dict]
            Each dict has keys: lat, lng, cluster_id, color.
        """
        import json
        data = json.dumps(points)
        self._run_js(f"showDemandClusters({data})")

    def clear_demand_clusters(self):
        self._run_js("clearDemandClusters()")

    def load_batch(self, elements_json: str):
        """Send all map elements in a single IPC call.

        Parameters
        ----------
        elements_json : str
            JSON array of element descriptors produced by
            ``_build_batch_elements()``.  Each item must have a ``type``
            key plus the fields expected by the corresponding JS
            ``add*`` function (see ``loadBatchElements`` in
            ``map_controller.js``).
        """
        # Use json.dumps to safely embed the JSON string in JS.
        # This is faster than 3x .replace() on large payloads and
        # handles all edge cases (backslashes, quotes, newlines).
        import json
        self._run_js(f"loadBatchElements({json.dumps(elements_json)})")

    def set_canvas_mode(self, total_items: int):
        """Tell JS to use canvas rendering if element count exceeds threshold."""
        self._run_js(f"_setCanvasMode({total_items})")

    def detach_layers(self):
        """Detach all layer groups from the map (no DOM reflow during adds)."""
        self._run_js("detachLayers()")

    def load_batch_raw(self, elements_json: str):
        """Add elements without detach/reattach (caller manages that)."""
        import json
        self._run_js(f"addBatchItemsFromJson({json.dumps(elements_json)})")

    def reattach_layers(self):
        """Re-attach all layer groups to the map (single DOM reflow)."""
        self._run_js("reattachLayers()")

    def clear_all(self):
        self._run_js("clearAllLayers()")

    # ------------------------------------------------------------------
    # Results layer
    # ------------------------------------------------------------------

    def show_results_layer(self):
        """Switch map to results-only visibility."""
        self._run_js("showResultsLayer()")

    def clear_results(self):
        """Clear all results overlays and legend."""
        self._run_js("clearResultsLayer()")

    def clear_results_nodes(self):
        """Clear only node-level results (circles, pie charts)."""
        self._run_js("clearResultsNodes()")

    def clear_results_flows(self):
        """Clear only flow-level results (lines, arrows)."""
        self._run_js("clearResultsFlows()")

    def add_results_pie_charts(self, data: list[dict]):
        """Render pie chart markers on the results layer.

        data: [{lat, lng, segments: [{value, color, label},...], size, title}, ...]
        """
        import json
        self._run_js(f"addResultsPieCharts({json.dumps(data)})")

    def add_results_pie_legend(self, title: str, entries: list[dict]):
        """Show categorical legend for pie charts.

        entries: [{label, color}, ...]
        """
        import json
        self._run_js(
            f"addResultsPieLegend({json.dumps(title)}, "
            f"{json.dumps(entries)})"
        )

    def add_results_flow_lines(self, data: list[dict]):
        """Render directional power flow lines on the results layer.

        data: [{coords: [[lat,lng],...], weight, color, label, value}, ...]
        """
        import json
        self._run_js(f"addResultsFlowLines({json.dumps(data)})")

    def add_results_node_circles(self, data: list[dict]):
        """Render proportional circles on the results layer.

        data: [{lat, lng, radius, color, label, value}, ...]
        """
        import json
        self._run_js(f"addResultsNodeCircles({json.dumps(data)})")

    def add_results_legend(self, title: str, min_val: float, max_val: float,
                           color_min: str, color_max: str):
        """Show gradient legend for results."""
        import json
        self._run_js(
            f"addResultsLegend({json.dumps(title)}, {min_val}, {max_val}, "
            f"{json.dumps(color_min)}, {json.dumps(color_max)})"
        )

    # ------------------------------------------------------------------
    # Risk & Resilience map layers
    # ------------------------------------------------------------------

    def add_risk_circles(self, data: list[dict]):
        """Render proportional circles coloured by composite risk index.

        data: [{lat, lng, radius, risk_index, label, tooltip}, ...]
        """
        import json
        self._run_js(f"addRiskCircles({json.dumps(data)})")

    def clear_risk_layer(self):
        """Remove all risk overlay elements from the map."""
        self._run_js("clearRiskLayer()")

    def add_hazard_zones(self, data: list[dict]):
        """Draw semi-transparent hazard intensity zones.

        data: [{coords: [[lat,lng],...], color, opacity, label, hazard_type}, ...]
        """
        import json
        self._run_js(f"addHazardZones({json.dumps(data)})")

    def add_risk_legend(self, title: str, entries: list[dict]):
        """Show categorical risk legend.

        entries: [{label, color}, ...]
        """
        import json
        self._run_js(
            f"addRiskLegend({json.dumps(title)}, {json.dumps(entries)})"
        )

    def capture_screenshot(self, save_path: str) -> bool:
        """Capture the current map view as an image file."""
        pixmap = self.grab()
        return pixmap.save(save_path)
