# Map Editor

Central workspace for placing nodes, drawing transmission lines, positioning generators and storage, defining development zones, and visualizing simulation results. Powered by Leaflet.js embedded in a QWebEngineView.


---


## Drawing Modes

Select a mode from the toolbar and interact with the map. Single-placement actions (node, generator, battery) auto-reset to selection mode. Multi-click actions (line trace, zone polygon) stay active until completed or cancelled. Press `Escape` to cancel and return to selection mode.

### Placing Nodes

1. Click **Node** in the toolbar (or the assigned shortcut key).
2. Click on the map at the desired geographic location.
3. A circular marker appears with an auto-assigned index (0, 1, 2, ...).
4. The Properties Panel opens the Node form. Configure the node name, demand file, reserve requirements, and other parameters.
5. The mode resets to selection.

Nodes are the fundamental geographic locations. All other equipment attaches to a node. A node represents a substation, load center, or generation hub. Coordinates (latitude, longitude) are captured from the click location and stored in WGS84 format.

**Tips:**
- You can reposition a node later by dragging it in selection mode.
- When you move a node, all attached generators, batteries, fuel entries, transformers, and connected line endpoints move with it automatically.
- Node markers display their index number as a label. Rename nodes in the Properties Panel for clarity.

### Placing Generators and Batteries

1. Click **Generator** (or **Battery**) in the toolbar.
2. Click on the map near an existing node.
3. The element snaps to the nearest node automatically via the magnetic snapping system.
4. A marker appears near the parent node with a shape and color indicating its type.
5. The Properties Panel opens the corresponding form for configuration.
6. The mode resets to selection.

Marker sizes scale with rated capacity. Renewable generators (Solar, Wind, Hydro) use green markers by default; non-renewable generators (Diesel, Gas) use gray markers. Batteries use orange markers.

**Tips:**
- To change a generator or battery's parent node, edit the **Node** field in the Properties Panel.
- Multiple generators and batteries can be attached to the same node.
- Marker colors and shapes are customizable in the Appearance section of each element's form.

### Placing Transformers

1. Click **Transformer** in the toolbar.
2. Click on the map near a node.
3. The transformer snaps to the nearest node. It represents a voltage step-up or step-down connecting two buses.
4. Configure the primary and secondary voltages, rated power, and impedance in the Properties Panel.

### Placing Buses

1. Click **Bus** in the toolbar.
2. Click on the map near an existing node.
3. The bus snaps to the parent node. Buses represent electrical connection points at specific voltage levels within a node.
4. Configure voltage, frequency, current type (AC/DC), and demand fraction.

### Placing AC/DC and Frequency Converters

1. Click **AC/DC Converter** or **Freq Converter** in the toolbar.
2. Click near a node or bus.
3. The converter snaps to the nearest element. Configure its connected buses, rated power, and efficiency in the form.

### Placing Electrolyzers

1. Click **Electrolyzer** in the toolbar.
2. Click near a node or bus.
3. The electrolyzer snaps and creates a hydrogen production facility. Configure type (PEM, Alkaline, SOE), capacity, efficiency, and costs.

### Placing Fuel Infrastructure

Fuel entry points, fuel storage, and electrolyzers work the same way as generators -- select the mode, click near a node, and configure in the Properties Panel.

- **Fuel Source** -- A fuel import point (port, pipeline terminal). Configure available fuel types, import rates, costs, and supply-stress parameters (transit lead time, disruption window).
- **Fuel Storage** -- Represents a fuel storage facility. Configure capacity, initial levels, and minimum levels per fuel type.


---


## Drawing Lines

Transmission lines and fuel routes use a **polyline trace** workflow -- a multi-click process that allows you to route lines through intermediate waypoints for geographic accuracy.

### Transmission Lines

1. Click **Line** in the toolbar.
2. Click on the starting node. The node highlights with a pulsing effect to confirm selection.
3. A rubber-band dashed line follows your cursor from the last point.
4. Click on the map to add intermediate waypoints that shape the route (for example, following a coastline or road corridor).
5. Click on the destination node to complete the line.
6. The Properties Panel opens the Line form for configuring capacity, impedance, and voltage.
7. Press `Escape` at any point during the trace to cancel without creating the line.

**Key behaviors:**
- Both the start and end points must be magnetic elements (nodes, generators, batteries, transformers, or fuel entries).
- The rubber-band line updates in real time as you move the cursor.
- Waypoints are stored and preserved through save/load cycles.
- Multiple parallel lines between the same pair of nodes are allowed. Each is a separate element with its own capacity and impedance.

### Fuel Transport Routes

Same polyline trace workflow as transmission lines. Click **Fuel Route** in the toolbar, then trace from source to destination.

- Fuel routes display with a **dashed line style** to distinguish them visually from electrical transmission lines.
- Configure the transported fuel types, capacity, cost per unit-km, and loss fraction in the Properties Panel.

### Magnetic Snapping System

Line endpoints automatically snap to nearby magnetic elements when you click within their snap radius. The magnetic registry tracks all elements by type and ID:

| Magnetic Element Types |
|----------------------|
| Node |
| Generator |
| Battery |
| Transformer |
| Fuel Source |

When a node moves (dragged), all connected line endpoints update automatically. The snap system uses `EndpointRef(element_type, element_id)` references internally, so connections survive element repositioning.


---


## Drawing Zones

Development zones define geographic areas where new generation capacity may be installed.

1. Click **Zone** in the toolbar.
2. Click on the map to define polygon vertices. Each click adds a vertex.
3. Close the polygon by clicking near the starting point (within the snap tolerance).
4. The Properties Panel opens the Zone form. Configure the zone's technology type, maximum capacity, and interconnection cost.
5. The polygon fills with a semi-transparent color indicating the assigned technology.

**Tips:**
- Zone polygon boundaries can be edited later (see Editing Zone Boundaries below).
- Zones are assigned colors automatically based on their technology type, drawn from a predefined palette.
- Overlapping zones of different technologies are supported.


---


## Editing Elements

### Moving Elements

In selection mode (the default, or press `Escape`), drag any node to move it. The drag propagation system ensures that:

- All generators attached to the node move with it.
- All batteries attached to the node move with it.
- All fuel entries, fuel storage, and transformers at the node move with it.
- All connected transmission line and fuel route endpoints update to the new position.

Other elements (generators, batteries, fuel entries) can also be dragged individually to adjust their visual offset from the parent node, though their logical node assignment does not change.

### Editing Line Routes

1. Select a line in the tree or on the map.
2. In the Properties Panel, click the **Edit Trace** toggle button.
3. The line's waypoint vertices become visible as draggable handles.
4. Drag vertex handles to reshape the route. The line redraws in real time.
5. Click **Edit Trace** again (or press `Escape`) to confirm and exit edit mode.

### Editing Zone Boundaries

1. Select a zone in the tree or on the map.
2. In the Properties Panel, click the **Edit Polygon** toggle button.
3. The polygon's vertices become visible as draggable handles.
4. Drag vertices to reshape the boundary.
5. Click **Edit Polygon** again to confirm.


---


## Selecting Elements

- **Click** an element on the map to select it. Its property form appears in the Properties Panel and the corresponding tree item highlights.
- **Ctrl+Click** to add more elements to the selection (for batch editing of elements of the same type).
- **Click empty space** on the map or press `Escape` to deselect all elements.
- **Double-click** an item in the Element Tree to center the map on that element and zoom to an appropriate level.
- **Right-click** an element for a context menu with quick actions.

### Selection Feedback

When an element is selected:
- Its map marker or polyline receives a highlight border (selection color from the active theme).
- The corresponding item in the Element Tree scrolls into view and highlights.
- The Properties Panel switches to the appropriate form and populates it with the element's data.


---


## Copy and Paste

Copy technical properties between elements of the same type:

1. Select an element, press `Ctrl+C`. A confirmation message appears in the status bar.
2. Select another element of the same type, press `Ctrl+V`.

**What is copied:** All technical parameters -- capacity, costs, efficiency, fuel type, impedance, etc.

**What is NOT copied:** Position (latitude/longitude), ID, node assignment, and visual style. This ensures each element retains its unique identity and location.

Useful for creating multiple generators with identical specifications at different nodes, or replicating battery configurations across a network.


---


## Context Menu

Right-click an element on the map or in the Element Tree for a context menu:

| Action | Description |
|--------|-------------|
| **Delete** | Remove the element (with dependent confirmation if needed) |
| **Duplicate** | Create a copy with a new ID and offset position |
| **Copy Properties** | Copy technical parameters to clipboard |
| **Paste Properties** | Apply copied parameters (same type required) |
| **Center Map** | Pan and zoom the map to center on this element |
| **Edit Trace** | (Lines only) Toggle polyline vertex editing |
| **Edit Polygon** | (Zones only) Toggle polygon vertex editing |


---


## Map Controls

### Navigation

| Action | How |
|--------|-----|
| Zoom in/out | Scroll wheel, or double-click to zoom in |
| Pan | Click and drag on empty map space |
| Fit all elements | Click the **Fit Bounds** button in the map controls |
| Center on element | Double-click an item in the Element Tree |

### Base Maps

Switch between base map tile layers using the **Base Map** dropdown in the toolbar:

| Base Map | Description |
|----------|-------------|
| **OpenStreetMap** | Standard street map with labels. Good for urban grid design. |
| **Satellite** | Aerial/satellite imagery. Useful for identifying terrain, coastlines, and existing infrastructure. |
| **Terrain** | Topographic map with elevation contours and hillshading. Helpful for wind and solar resource assessment. |
| **Dark** | Minimalist dark-background map. Reduces visual clutter and works well with the Dark and Twilight themes. |

Base map selection is independent of the application theme and persists across sessions.


---


## Layer Filtering

The **Layer** dropdown controls element category visibility on the map:

| Layer | Visible Elements |
|-------|-----------------|
| **All** | Every element across all categories |
| **Electrical** | Nodes, generators, batteries, lines, transformers, buses, converters |
| **Primary Energy** | Fuel entries, fuel storage, fuel routes, electrolyzers |
| **Results** | Simulation output overlays (generation heatmaps, power flow arrows, load shedding) |

Layer filtering only affects map visibility. The Element Tree always shows all elements regardless of the layer filter.


---


## Coordinate System

All geographic positions use the **WGS84** coordinate reference system (EPSG:4326) with latitude and longitude in decimal degrees. This is the same system used by GPS devices, Google Maps, and OpenStreetMap.

- Latitude: positive values are north of the equator, negative values are south.
- Longitude: positive values are east of the prime meridian, negative values are west.

Coordinates are preserved through save/load cycles and exported accurately in the YAML configuration. When importing geographic data (GeoJSON, Shapefile, KML), the editor reprojects to WGS84 automatically if the source uses a different CRS.


---


## Element Visibility and Scaling

- Generator and battery markers scale in size proportional to their rated capacity, making it easy to identify large vs. small installations at a glance.
- Transmission line widths scale with rated capacity.
- Zone polygons use semi-transparent fills with technology-specific colors.
- All markers include tooltip text showing the element name and key parameters on hover.


---


## Performance Considerations

- For systems with more than 500 elements, consider using layer filtering to reduce rendering load.
- Satellite base maps consume more bandwidth than vector tile layers.
- Zoom to a specific area before placing elements to ensure precise geographic positioning.
- The map caches tiles locally after first load, so subsequent sessions load faster even with intermittent connectivity.
