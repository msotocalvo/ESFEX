# Overview
Desktop application for building, editing, and validating power system configurations on an interactive geographic map. Built on PySide6 with an embedded Leaflet.js map for designing multi-system power networks, running simulations, and reviewing results without manual YAML editing.


---


## Installation and Launch

```bash
# Install ESFEX with GUI dependencies (PySide6, QWebEngine, Leaflet)
pip install esfex

# Start with a blank project
esfex studio

# Open an existing configuration
esfex studio -c config.yaml

# Open and auto-center on a specific system
esfex studio -c config.yaml --system "isla_juventud"
```

### System Requirements

- Python 3.10 or later
- PySide6 6.5+ (included in the core install)
- A display capable of 1280x720 or higher (1920x1080 recommended)
- Internet connection for base map tiles and workflow data fetching (offline tile caching is supported after first load)


---


## Window Layout

Three resizable columns separated by splitter handles. Each panel can be collapsed using the arrow button on its splitter handle.

```
+------------------+----------------------------+------------------+
|                  |         Toolbar            |                  |
|   Element Tree   +----------------------------+  Properties      |
|   (left panel)   |                            |  Panel           |
|                  |      Map / SLD View        |  (right panel)   |
|                  |                            |                  |
|                  +----------------------------+                  |
|                  | Console (left) | Script (R) |                  |
+------------------+----------------------------+------------------+
```

### Element Tree (Left Panel -- 20% Width)

Hierarchical overview of every object in the project:

- **Global Settings** -- Simulation-wide parameters (solver, temporal resolution, N-1 security). Always at the top.
- **Stochastic Scenarios** -- Multi-scenario definitions with probability weights and cost multipliers.
- **Inter-system Links** -- Cross-system transmission lines and fuel routes.
- **Each System** -- Shown in bold as a top-level node, expandable into component categories:
  - Nodes, Generators, Batteries, Transmission Lines, Transformers, Buses, AC/DC Converters, Frequency Converters, Development Zones, Fuel Sources, Fuel Storages, Fuel Routes, Fuels, Electrolyzers, EV Configuration, Rooftop Solar, Technologies, Investment Portfolio

Each category displays an element count in parentheses, e.g. "Generators (12)". Clicking any item selects it on the map and opens its property form. A search box at the top filters by name in real time.

### Map and SLD View (Center Panel -- 55% Width)

Two tabbed views:

- **Geographic View** -- The interactive Leaflet.js map where you place, edit, and connect elements. Supports multiple base map styles and layer filtering.
- **Single-Line Diagram (SLD)** -- An auto-generated electrical schematic of the current system, color-coded by voltage level. Useful for verifying topology without geographic context.

Below the map/SLD tabs, a horizontal splitter divides:

- **Python Console** (left, 60%) -- Interactive Python REPL with access to the data model.
- **Script Editor** (right, 40%) -- Multi-tab editor with syntax highlighting for automation scripts.

### Properties Panel (Right Panel -- 25% Width)

Editable form for the currently selected element, organized with tabs and collapsible sections. Changes reflect immediately on the map and in the tree. When no element is selected, shows a usage tip.


---


## Toolbar

Drawing modes, layer controls, and analysis actions. All drawing mode buttons are mutually exclusive (radio-group style). Toolbar icons adapt automatically to the current theme (light icons on dark themes, dark icons on light themes).

### Drawing Modes

| Button | Mode | What it does |
|--------|------|-------------|
| **Select** | `select` | Default pointer mode. Click to select, drag to move elements |
| **Line** | `add_line` | Draw a transmission line between two nodes using polyline trace |
| **Generator** | `add_generator` | Click near a node to attach a new generator |
| **Battery** | `add_battery` | Click near a node to attach a new battery |
| **Transformer** | `add_transformer` | Place a transformer near a node |
| **Bus** | `add_bus` | Place a bus near a node |
| **Zone** | `draw_zone` | Draw a polygon to define a development zone |
| **AC/DC Converter** | `add_acdc_converter` | Place an AC/DC converter near a bus |
| **Freq Converter** | `add_freq_converter` | Place a frequency converter between two buses |
| **Electrolyzer** | `add_electrolyzer` | Place an electrolyzer near a bus |
| **Fuel Source** | `add_fuel_entry` | Place a fuel source (import point) near a node |
| **Fuel Storage** | `add_fuel_storage` | Place fuel storage near a node |
| **Fuel Route** | `add_fuel_route` | Draw a fuel transport route using polyline trace |

Drawing mode buttons remain disabled until at least one system exists in the project.

### Layer and Base Map Selectors

Two dropdowns to the right of the drawing modes:

- **Layer** -- Filter visible elements: All, Electrical, Primary Energy, or Results.
- **Base Map** -- Switch the tile layer: OpenStreetMap, Satellite, Terrain, or Dark.

### System Management

| Button | What it does |
|--------|-------------|
| **Add System** | Opens a name dialog and creates a new empty power system |

### Analysis Actions

| Button | What it does |
|--------|-------------|
| **Validate** | Check the configuration for errors across all systems |
| **Run** | Launch an optimization simulation (enabled after successful validation) |
| **Sensitivity** | Open the sensitivity analysis dialog (enabled after successful simulation) |
| **Results** | Open the results viewer (enabled after successful simulation) |

Press `Escape` at any time to cancel the current drawing operation and return to selection mode.


---


## Basic Workflow

Typical session steps:

1. **Create a system** -- Click **Add System** in the toolbar and enter a descriptive name (e.g., "Cuba_Western_Grid").
2. **Place nodes** -- Switch to Node mode and click on the map at each substation or load center location. Each node gets an auto-assigned index.
3. **Add generation** -- Switch to Generator mode and click near a node. The generator snaps to the nearest node. Configure its type, fuel, capacity, and costs in the properties panel.
4. **Add storage** -- Same process with Battery mode. Set energy capacity, power ratings, and efficiency.
5. **Connect nodes** -- Use Line mode to draw transmission lines. Click the starting node, add waypoints for routing, then click the destination node.
6. **Add fuel infrastructure** -- If using fuel-based generation, place fuel entry points, storage, and transport routes.
7. **Configure system settings** -- Click a system name in the tree to set RE targets, penalties, discount rate, and other system-level parameters.
8. **Configure global settings** -- Click Global Settings to set solver choice, temporal resolution, and N-1 contingency parameters.
9. **Validate** -- Click **Validate** to run structural, electrical, demand, generation, fuel, and connectivity checks.
10. **Fix errors** -- Double-click any issue in the validation dialog to navigate to the affected element.
11. **Run simulation** -- After all errors are resolved, click **Run** to launch the optimization.
12. **Review results** -- Click **Results** to open charts and the results map layer.
13. **Save or export** -- Save includes visual layout and map positions; Export produces a clean YAML for command-line simulation.


---


## File Operations

| Shortcut | Action | Description |
|----------|--------|-------------|
| `Ctrl+O` | Open / Import | Load a YAML configuration file. The editor validates it, populates all systems, and centers the map. |
| `Ctrl+S` | Save | Save the current project including visual layout, map positions, and GUI-only metadata. |
| `Ctrl+Shift+S` | Export | Export a clean YAML suitable for `esfex run`. Visual data is stripped. |
| `Ctrl+N` | New | Start a new blank project (prompts to save unsaved changes). |

The editor also supports importing geographic asset files (GeoJSON, Shapefile, KML, KMZ, GeoPackage) via **File > Import Geographic Data**, parsed into nodes, generators, lines, and other elements using the asset import dialog.


---


## Map Layers

Three functional layers, controlled by the Layer dropdown in the toolbar:

- **Electrical** -- Nodes, generators, batteries, transmission lines, transformers, buses, AC/DC converters, frequency converters. Shown with solid-color markers and polylines.
- **Primary Energy** -- Fuel entry points, fuel storage, fuel transport routes, electrolyzers. Shown with distinct marker shapes to differentiate from electrical elements.
- **Results** -- Simulation output overlays including generation dispatch heatmaps, power flow arrows, and load shedding indicators. Only populated after a successful simulation run.

Selecting **All** shows every layer simultaneously.


---


## Themes

Available under **File > Preferences > Theme**:

- **ESFEX Light** (default) -- White surfaces with blue accent colors. Optimized for daytime use.
- **ESFEX Dark** -- Dark surfaces with muted accent colors. Reduces eye strain in low-light environments. Toolbar icons automatically invert to white.
- **ESFEX Twilight** -- A balanced mid-tone theme with warm accents. Toolbar icons also invert.

Theme changes take effect immediately without restarting. The map base layer style is independent of the application theme.


---


## Keyboard Shortcuts

### General

| Shortcut | Action |
|----------|--------|
| `Escape` | Cancel current drawing mode and return to selection, or deselect the current element |
| `Delete` | Delete the selected element (with confirmation if it has dependents) |
| `Ctrl+Z` | Undo the last action |
| `Ctrl+F` | Focus the search box in the element tree |
| `Ctrl+,` | Open the Preferences dialog |

### Element Operations

| Shortcut | Action |
|----------|--------|
| `Ctrl+C` | Copy the selected element's technical properties to the clipboard |
| `Ctrl+V` | Paste copied properties onto the selected element (same type required) |
| `Ctrl+D` | Duplicate the selected element with a new ID and offset position |

### File Operations

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open / Import YAML |
| `Ctrl+S` | Save project |
| `Ctrl+Shift+S` | Export clean YAML |
| `Ctrl+N` | New blank project |

Shortcuts are customizable via **File > Preferences > Shortcuts**.


---


## Validation

Click **Validate** in the toolbar to check the configuration before running a simulation. The validation dialog:

- Runs checks across six categories: structural integrity, electrical parameters, demand data, generation adequacy, fuel network, and connectivity.
- Groups results by severity: errors (red), warnings (yellow), and informational messages (blue).
- Double-click any issue to navigate to the affected element on the map and open its form.
- Optionally detects and removes dead-end network elements (simplification pass).
- Displays a summary count of issues by category and severity.

Fix all errors before exporting or running simulations. Warnings are advisory and do not prevent simulation execution.


---


## Python Console

Interactive access to the data model through four pre-loaded variables: `model`, `state`, `config`, and `window`. Inspect elements, modify properties programmatically, run batch operations, or launch simulations directly. See [Scripting & Automation](scripting.md) for details.


---


## Collapsible Panels

Each panel (Element Tree, Properties, Console/Script) has a collapse button on its splitter handle. Click the arrow to hide a panel and maximize the remaining space. Click again to restore.


---


## Next Steps

- [Map Editor](map-editor.md) — placing and editing elements on the map
- [Element Forms](element-forms.md) — reference for all property fields
- [System Management](system-management.md) — working with multiple systems
- [Inter-System Links](inter-system-links.md) — connecting independent power systems
- [Global Settings](global-settings.md) — simulation parameters, solver, and penalties
- [Analysis Workflows](../workflows/index.md) — resource assessment, financial analysis, and other multi-step wizards
- [Plugin Management](plugins.md) — extending the editor with plugins
- [Scripting & Automation](scripting.md) — Python console usage and script examples
