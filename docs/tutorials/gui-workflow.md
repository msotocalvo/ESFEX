# GUI Workflow
## Prerequisites

Install ESFEX with GUI dependencies:

```bash
pip install "esfex[gui]"
```

This installs PySide6 and QWebEngine. Requires Python 3.10+ and a display of at least 1280x720 (1920x1080 recommended).

---

## Step 1: Launch the Editor

```bash
# Start with a blank project
esfex studio

# Or edit an existing configuration
esfex studio -c existing_system.yaml

# Open and auto-center on a specific system
esfex studio -c config.yaml --system "isla_juventud"
```

The editor window has three main panels:

- **Element Tree** (left, ~20% width) — hierarchical overview of all objects
- **Map / SLD View** (center, ~55% width) — interactive Leaflet.js map for placing and connecting elements
- **Properties Panel** (right, ~25% width) — editable form for the selected element

Below the map, a Python Console (left) and Script Editor (right) provide programmatic access to the data model.

---

## Step 2: Create a New System

1. Click the **Add System** button in the toolbar (it remains disabled until clicked for the first time in a blank project).
2. A name dialog appears. Enter a descriptive name, for example `Cuba_Western_Grid`. System names must be unique and should not contain spaces (use underscores instead).
3. Click **OK**. The new system appears as a bold top-level node in the Element Tree, with empty categories underneath: Nodes, Generators, Batteries, Transmission Lines, and so on.

Drawing mode buttons become enabled once a system exists.

!!! tip "Multiple Systems"
    You can create additional systems later for multi-system studies. Each system is independent and can be interconnected via inter-system links.

---

## Step 3: Place Nodes on the Map

Nodes represent substations, load centers, or any point where generation, load, or transmission converge.

1. Click the **Node** button in the toolbar to enter node placement mode. The cursor changes to a crosshair.
2. Click on the map at the location of your first substation or load center. A numbered marker appears (e.g., "Node 0").
3. Continue clicking to place additional nodes. Each receives an auto-incremented index (Node 0, Node 1, Node 2, ...).
4. When finished, press **Escape** or click the **Select** button to exit placement mode.

Place three nodes for a three-bus system.

### Configuring Node Properties

Click any node marker on the map or in the Element Tree. The Properties Panel shows:

- **Name**: Give the node a human-readable name (e.g., "Havana", "Cienfuegos", "Camaguey")
- **Coordinates**: Latitude and longitude (auto-filled from where you clicked; editable for precision)
- **Demand File**: Path to the demand data file (Excel or CSV) for this node. If demand is specified at the system level, individual node demand files are optional.

!!! tip "Map Navigation"
    Double-click any node in the Element Tree to center the map on that node. Use the mouse scroll wheel to zoom. Click and drag to pan.

---

## Step 4: Add Generators to Nodes

Generators represent power plants — both existing units and investment candidates.

1. Click the **Generator** button in the toolbar to enter generator placement mode.
2. Click on the map near a node. The generator icon snaps automatically to the nearest node (magnetic snapping).
3. The Properties Panel immediately shows the generator configuration form.

### Configuring Generator Properties

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Human-readable label | "Solar PV" |
| **Type** | `Renewable` or `Non-renewable` | Renewable |
| **Fuel** | Energy source (Solar, Wind, Diesel, Natural Gas, etc.) | Solar |
| **Rated Power (MW)** | Existing installed capacity at this node | 50.0 |
| **Investment Cost ($/MW)** | Per-MW cost for new capacity | 700000 |
| **Max Investment (MW)** | Maximum new capacity allowed at this node | 200.0 |
| **Fuel Cost ($/MWh)** | Variable fuel cost (0 for renewables) | 0.0 |
| **Fixed Cost ($/MWh)** | Fixed O&M cost | 5.0 |
| **Maintenance Cost ($/MWh)** | Variable O&M cost | 2.0 |
| **Lifetime (years)** | Expected operational lifetime | 25 |
| **Initial Age (years)** | Current age of existing unit | 0 |
| **Efficiency at Rated** | Thermal efficiency at full load | 1.0 |
| **Degradation Rate** | Annual capacity loss fraction | 0.005 |
| **Availability Profile** | Path to CSV file with hourly capacity factors | solar_profile.csv |

For a typical three-node island system, add:

- **Node 0**: Solar PV (50 MW existing, 200 MW max investment) + Diesel Generator (30 MW, no new investment)
- **Node 1**: Wind (20 MW existing, 100 MW max investment) + Diesel Generator (20 MW)
- **Node 2**: Diesel Generator (40 MW) + Gas Turbine (25 MW)

Each new generator appears under the **Generators** category in the Element Tree.

### Cost Curves (Advanced)

For generators with non-linear cost characteristics, expand the **Cost Curve** section in the Properties Panel. You can choose from four curve types:

- **Flat**: Constant marginal cost at all output levels
- **Linear**: Price ramps from `price_at_zero` to `price_at_max`
- **Stepwise**: Piecewise blocks (e.g., first 40% at $45/MWh, next 30% at $55/MWh, last 30% at $75/MWh)
- **Exponential**: `price(P) = base_price * exp(scale_factor * P/P_max)`

---

## Step 5: Add Batteries

Battery storage follows the same placement pattern as generators.

1. Click the **Battery** button in the toolbar.
2. Click near a node on the map. The battery icon snaps to the nearest node.
3. Configure in the Properties Panel:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Human-readable label | "Li-Ion 4h" |
| **Energy Capacity (MWh)** | Total storage capacity | 0 (no existing) |
| **Max Charge Power (MW)** | Maximum charging rate | 0 |
| **Max Discharge Power (MW)** | Maximum discharging rate | 0 |
| **Charge Efficiency** | Round-trip charge efficiency | 0.95 |
| **Discharge Efficiency** | Round-trip discharge efficiency | 0.95 |
| **SOC Min / Max** | State of charge limits (fraction) | 0.10 / 0.95 |
| **SOC Initial** | Starting state of charge | 0.50 |
| **Invest Cost Power ($/MW)** | Cost for power capacity | 180000 |
| **Invest Cost Energy ($/MWh)** | Cost for energy capacity | 120000 |
| **Max Investment Power (MW)** | Maximum power investment | 100 |
| **Max Investment Energy (MWh)** | Maximum energy investment | 400 |
| **Min/Max Duration (hours)** | E/P ratio bounds | 2.0 / 8.0 |
| **Lifetime (years)** | Expected lifetime | 15 |

!!! note "Battery SOC Cyclic Constraint"
    ESFEX enforces `SOC(t_last) == SOC(t_initial)` at the end of each operational day. This prevents the optimizer from treating batteries as free energy sources.

---

## Step 6: Draw Transmission Lines with Polyline Traces

1. Click the **Line** button in the toolbar to enter line drawing mode.
2. Click on the **starting node** (the source). A dashed rubber-band line appears from that node to your cursor.
3. Click on the map at **intermediate waypoints** to route the line geographically (along roads, around obstacles, etc.). Each click adds a waypoint to the polyline.
4. Click on the **destination node** to finish the line. The polyline trace is completed and appears as a solid line on the map.
5. Press **Escape** at any time to cancel the current trace and discard all waypoints.

### Configuring Line Properties

Click a drawn line on the map or in the Element Tree:

| Field | Description | Example |
|-------|-------------|---------|
| **Capacity (MW)** | Thermal transfer capacity | 100.0 |
| **Reactance (p.u.)** | Per-unit reactance (for DC power flow) | 0.05 |
| **Resistance (p.u.)** | Per-unit resistance | 0.01 |
| **Length (km)** | Physical line length (auto-calculated from waypoints) | 200.0 |
| **Voltage (kV)** | Nominal voltage level | 220.0 |

Draw three lines to create a fully connected network:

- Node 0 to Node 1: 100 MW capacity
- Node 1 to Node 2: 50 MW capacity
- Node 0 to Node 2: 75 MW capacity (optional direct link)

!!! tip "Parallel Lines"
    You can draw multiple lines between the same pair of nodes. The serializer automatically sums their capacities in the adjacency matrix. Each line retains its own `line_id` and individual capacity.

---

## Step 7: Add Fuel Entry Points

Fuel entry points define where fuel enters the network (required for diesel, natural gas, etc.).

1. Click the **Fuel Entry** button in the toolbar.
2. Click near the node where fuel is imported (e.g., a port node).
3. Configure in the Properties Panel:

| Field | Description | Example |
|-------|-------------|---------|
| **Fuels** | List of fuel types available at this point | [diesel] |
| **Max Import Rate** | Maximum fuel import rate (tonnes/period) | 500.0 |
| **Import Cost** | Landed cost per tonne | 850.0 |

Fuel entry points are only relevant when `enable_primary_energy: true`.

---

## Step 8: Configure System-Level Settings

Click the system name in the Element Tree. The Properties Panel shows:

### General Settings

| Field | Description | Example |
|-------|-------------|---------|
| **Demand File** | Path to system-wide demand data | demand.xlsx |
| **Demand Scale** | Multiplier applied to all demand | 1.0 |
| **Demand Growth** | Annual growth rate | 0.02 (2%) |
| **Discount Rate** | For NPV calculations | 0.05 (5%) |

### RE Targets

| Field | Description | Example |
|-------|-------------|---------|
| **Target RE Penetration** | Final-year RE fraction target | 0.80 (80%) |
| **Initial RE Penetration** | Starting RE fraction (0 = auto-calculate) | 0.0 |
| **Min Annual Increment** | Minimum yearly RE increase | 0.02 |
| **Max Annual Increment** | Maximum yearly RE increase | 0.10 |

### Penalties

| Field | Description | Example |
|-------|-------------|---------|
| **Loss of Load** | VOLL in $/MW (must be very high) | 10000000.0 |
| **Curtailment** | Cost of curtailing RE ($/MWh) | 100.0 |
| **Max Curtailment Ratio** | Max fraction of RE that can be curtailed | 0.05 |
| **CO2 Cost** | Carbon price ($/tCO2) | 10.0 |

### CO2 Budget

| Field | Description | Example |
|-------|-------------|---------|
| **Enabled** | Activate CO2 budget constraint | true |
| **Annual Budget** | Maximum CO2 emissions per year (tonnes) | 500000.0 |

### DC Power Flow (Advanced)

| Field | Description | Example |
|-------|-------------|---------|
| **Enabled** | Use linearized power flow (vs. transport model) | true |
| **Base Impedance** | System base impedance (Ohms) | 100.0 |
| **Max Angle Difference** | Maximum voltage angle difference (degrees) | 30.0 |

---

## Step 9: Configure Global Settings

Click **Global Settings** at the top of the Element Tree. These apply across all systems:

### Simulation

| Field | Description | Options |
|-------|-------------|---------|
| **Mode** | Simulation type | `development` (LP) or `unit_commitment` (MIP) |
| **Date Start** | Simulation start date | 01/01/2025 00:00 |

### Solver

| Field | Description | Example |
|-------|-------------|---------|
| **Solver** | Optimization solver | highs, gurobi, cplex, cbc, glpk |
| **Threads** | Parallel solver threads | 4 |
| **Time Limit** | Max solve time (seconds) | 3600 |
| **MIP Gap** | Optimality gap for MIP | 0.01 |

### Temporal Resolution

| Field | Description | Example |
|-------|-------------|---------|
| **Resolution (hours)** | Time step size | 1 |
| **Rolling Horizon** | Enable rolling horizon dispatch | true |
| **Window Size (hours)** | Rolling horizon window | 48 |
| **Overlap (hours)** | Window overlap for continuity | 6 |

### Master Problem

| Field | Description | Example |
|-------|-------------|---------|
| **Representative Days** | Days per year for operational validation | 5 |
| **Min Day Separation** | Minimum days between representatives | 7 |
| **MGA Enabled** | Near-optimal alternative exploration | false |

### N-1 Security

| Field | Description | Example |
|-------|-------------|---------|
| **Enabled** | Activate contingency analysis | false |
| **Transmission** | Transmission contingency | false |
| **Generation** | Generation contingency | false |

---

## Step 10: Add More Systems (Multi-System Workflow)

1. Click **Add System** in the toolbar. Enter a name (e.g., "Isla_Juventud").
2. Switch between systems by clicking their names in the Element Tree.
3. Place nodes, generators, batteries, and lines following the same workflow.

### Connecting Systems with Inter-System Links

1. Click **Inter-system Links** in the Element Tree.
2. In the Properties Panel, configure the connection:

| Field | Description | Example |
|-------|-------------|---------|
| **Systems** | Pair of systems to connect | [Cuba_Western_Grid, Isla_Juventud] |
| **From Nodes** | Node indices in system A | [2] |
| **To Nodes** | Node indices in system B | [0] |
| **Capacity (MW)** | Existing interconnection capacity | 50.0 |
| **Invest Cost ($/MW)** | Cost for capacity expansion | 500000.0 |
| **Max Investment (MW)** | Maximum expansion | 200.0 |

---

## Step 11: Validate the Configuration

1. Click **Validate** in the toolbar.
2. The validation dialog runs checks across six categories:
   - **Structural integrity**: Node count, index consistency
   - **Electrical parameters**: Generator/battery parameter ranges, efficiency bounds
   - **Demand data**: File existence, format, row count
   - **Generation adequacy**: Sufficient capacity to meet peak demand
   - **Fuel network**: Fuel entry points connected to generators that need them
   - **Connectivity**: All nodes reachable, no isolated equipment

3. Results are grouped by severity:
   - **Errors** (red) — must be fixed before running
   - **Warnings** (yellow) — advisory, do not prevent simulation
   - **Info** (blue) — informational messages

4. Double-click any issue to navigate directly to the affected element on the map.

Fix all errors before proceeding. Common errors: mismatched array lengths (per-node arrays must have exactly `num_nodes` entries), missing demand files, and generators with zero rated power and zero max investment.

---

## Step 12: Run the Simulation from the GUI

1. Click **Run** in the toolbar. The Run button is only enabled after a successful validation.
2. The Simulation Dialog opens with:
   - A progress bar tracking year-by-year and window-by-window progress
   - A live log viewer showing solver output
   - Cancel and Close buttons

3. The simulation runs `esfex run` as a subprocess. Progress is parsed from log messages (e.g., "Year 2025 (1/25)", "Window 3/208").
4. When complete, the dialog shows "Optimization completed successfully!" and the Close button becomes enabled.

Alternatively, run from the CLI after exporting:

```bash
esfex run -c my_system.yaml --years 25 -v
```

---

## Step 13: View Results

1. Click **Results** in the toolbar. The Results panel opens with:
   - **Summary table**: Total cost, RE penetration, emissions by year
   - **Generation dispatch charts**: Stacked area charts showing hourly generation mix
   - **Investment timeline**: Bar charts showing capacity additions by technology and year
   - **Power flow maps**: Arrows on the map showing transmission flows (available in the Results layer)

2. Switch to the **Results** layer in the Layer dropdown to see power flow arrows, generation heatmaps, and load shedding indicators overlaid on the map.

3. Click **Sensitivity** in the toolbar (available after simulation) to launch a Sobol sensitivity analysis on the solved model.

### Accessing Results Programmatically

Results are stored in HDF5 format. Use the Python Console:

```python
import h5py

with h5py.File("results/results_Cuba_Western_Grid.h5", "r") as f:
    print(list(f["summary_results"].keys()))
    costs = f["summary_results/total_cost"][:]
    re_pen = f["summary_results/re_penetration"][:]
    for y, (c, r) in enumerate(zip(costs, re_pen)):
        print(f"Year {y+1}: Cost=${c:,.0f}, RE={r:.1%}")
```

---

## Step 14: Save and Export

| Action | Shortcut | Format | Includes |
|--------|----------|--------|----------|
| **Save** | `Ctrl+S` | YAML + GUI metadata | Visual layout, map positions, marker styles, editor state |
| **Export** | `Ctrl+Shift+S` | Clean YAML | Only optimization-relevant data, suitable for `esfex run` |

The saved file can be reopened with full visual fidelity. The exported file is a minimal YAML the CLI can process directly.

---

## Keyboard Shortcuts Reference

| Shortcut | Action |
|----------|--------|
| `Escape` | Cancel drawing mode / deselect element |
| `Delete` | Delete selected element |
| `Ctrl+Z` | Undo last action |
| `Ctrl+C` | Copy element properties |
| `Ctrl+V` | Paste properties onto selected element |
| `Ctrl+D` | Duplicate element |
| `Ctrl+F` | Focus search box in Element Tree |
| `Ctrl+O` | Open / Import YAML |
| `Ctrl+S` | Save project |
| `Ctrl+Shift+S` | Export clean YAML |
| `Ctrl+N` | New blank project |
| `Ctrl+,` | Open Preferences |

---

## Tips and Best Practices

- **Drag nodes** to reposition them. Attached generators, batteries, fuel entries, and line endpoints move automatically.
- **Double-click** any element in the Element Tree to center the map on it.
- **Ctrl+Click** to select multiple elements for batch editing (e.g., set the same fuel cost for all diesel generators).
- Use the **Appearance** section in property forms to customize marker colors, icon sizes, and line widths.
- Switch visual themes under **File > Preferences > Theme**: Light (default), Dark, or Twilight.
- Import geographic data (GeoJSON, Shapefile, KML) via **File > Import Geographic Data** to quickly populate nodes and lines from existing GIS datasets.
- Use the **Python Console** for batch operations: `for g in state.generators: g.fuel_cost = [85.0] * len(g.fuel_cost)`.
- Use **Dry Run** mode (`esfex run -c config.yaml --dry-run`) to validate and preview the simulation plan without solving.
