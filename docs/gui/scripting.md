# Scripting & Automation

Python console and multi-tab script editor for automating element creation, batch modifications, data extraction, analysis, and simulation. Full access to the GUI data model and any installed Python library.


---


## Console and Script Editor

### Python Console

Interactive REPL at the bottom-left, built on `code.InteractiveInterpreter`. Startup banner:

```
Python Console - ESFEX Studio
Available objects: model, state, config
Type help(<object>) for details.
>>>
```

#### Console Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Execute the current line (or add to multi-line buffer if incomplete) |
| `Up Arrow` | Navigate to the previous command in history |
| `Down Arrow` | Navigate to the next command in history |
| `Ctrl+C` | Cancel the current input line |
| `Tab` | Insert 4 spaces (indentation) |

#### Multi-Line Input

Incomplete statements (e.g., `for` loops, `if` blocks, function definitions) change the prompt from `>>>` to `...`. Press `Enter` on an empty line to execute.

```python
>>> for g in state.generators.values():
...     if g.rated_power > 50:
...         print(f"{g.name}: {g.rated_power} MW")
...
Solar Farm North: 100 MW
Diesel Plant A: 75 MW
```

#### Command History

Session-level command history navigable with Up/Down arrow keys. Not persisted between sessions.

### Script Editor

Bottom-right of the window. Multi-line scripting environment:

- **Multiple tabs** -- Open several scripts simultaneously. Each tab has its own undo history.
- **Syntax highlighting** -- Python keywords, builtins, strings, numbers, comments, and decorators are highlighted using the current theme's color scheme.
- **Line numbers** -- Displayed in the left gutter for easy reference.
- **Auto-indentation** -- New lines inherit the indentation of the previous line.

#### Script Editor Actions

| Button / Shortcut | Action |
|-------------------|--------|
| **Run** | Execute the current tab's script. Output appears in the console. |
| **Open** | Load a `.py` file into a new tab. |
| **Save** | Save the current tab's content to a `.py` file. |
| **New Tab** | Create a blank script tab. |
| **Close Tab** | Close the current tab (prompts to save if modified). |

Scripts execute in the same namespace as the console. Variables are shared bidirectionally.


---


## Available Objects

Four pre-loaded objects in the console/script namespace:

| Variable | Type | Description |
|----------|------|-------------|
| `model` | `GuiModel` | The central data model. Use its methods for all create/update/delete operations. Changes via model methods trigger proper UI updates (map refresh, tree sync). |
| `state` | `GuiSystemState` | The currently active system's data container. Contains dictionaries of nodes, generators, batteries, lines, etc. Updates automatically when you switch systems. |
| `config` | `ESFEXConfig` or `None` | The loaded configuration object. `None` if no YAML file has been opened. |
| `window` | `MainWindow` | The editor window instance. Provides access to the map widget, element tree, console, and all UI components. |

### The `model` Object

Primary interface for programmatic modifications. Methods ensure the map, element tree, and properties panel all update correctly.

**Important:** Always use `model.update_*()` methods. Direct attribute changes on `state` objects update data but do not refresh UI.

```python
# Correct -- triggers full UI update (map marker, tree label, form)
model.update_generator(gen_id, rated_power=200.0)

# Incorrect -- data changes but UI is stale
state.generators[gen_id].rated_power = 200.0
```

#### Key Model Methods

| Method | Description |
|--------|-------------|
| `model.add_node(name, lat, lng)` | Create a new node |
| `model.add_generator_instance(unit_key, name, gen_type, fuel, node, rated_power, ...)` | Create a new generator |
| `model.add_battery(name, node, rated_power, capacity, ...)` | Create a new battery |
| `model.add_line(from_node, to_node, capacity_mw, ...)` | Create a new transmission line |
| `model.update_generator(gen_id, **kwargs)` | Update generator properties |
| `model.update_battery(bat_id, **kwargs)` | Update battery properties |
| `model.update_node(node_idx, **kwargs)` | Update node properties |
| `model.delete_element(element_type, element_id)` | Delete an element |

### The `state` Object

Read access to the active system's data:

| Attribute | Type | Description |
|-----------|------|-------------|
| `state.nodes` | `dict[int, GuiNode]` | All nodes, keyed by index |
| `state.generators` | `dict[str, GuiGeneratorInstance]` | All generators, keyed by ID |
| `state.batteries` | `dict[str, GuiBatteryInstance]` | All batteries, keyed by ID |
| `state.transmission_lines` | `dict[str, GuiTransmissionLine]` | All lines, keyed by line_id |
| `state.transformers` | `dict[str, GuiTransformer]` | All transformers |
| `state.zones` | `dict[str, GuiZone]` | All development zones |
| `state.fuel_entries` | `dict[str, GuiFuelEntry]` | All fuel entry points |
| `state.fuel_storages` | `dict[str, GuiFuelStorage]` | All fuel storage facilities |
| `state.fuel_routes` | `dict[str, GuiFuelRoute]` | All fuel transport routes |
| `state.buses` | `dict[str, GuiBus]` | All buses |
| `state.electrolyzers` | `dict[str, GuiElectrolyzer]` | All electrolyzers |

### The `window` Object

Access to UI components for advanced scripting:

| Attribute | Type | Description |
|-----------|------|-------------|
| `window.map_widget` | `MapWidget` | The Leaflet.js map widget |
| `window.element_tree` | `ElementTreePanel` | The element tree panel |
| `window.console` | `PythonConsole` | The Python console itself |
| `window.properties_panel` | `PropertiesPanel` | The right-side properties panel |
| `window.script_editor` | `ScriptEditor` | The script editor widget |


---


## Example: System Summary

```python
print(f"System: {window._current_system_name}")
print(f"Nodes: {len(state.nodes)}")
print(f"Generators: {len(state.generators)}")
print(f"Total generation capacity: {sum(g.rated_power for g in state.generators.values()):.0f} MW")
print(f"Batteries: {len(state.batteries)}")
print(f"Total storage capacity: {sum(b.capacity for b in state.batteries.values()):.0f} MWh")
print(f"Lines: {len(state.transmission_lines)}")
print(f"Buses: {len(state.buses)}")
print(f"Fuel entries: {len(state.fuel_entries)}")
```


---


## Example: Batch Import Generators from CSV

```python
import csv

with open("generators.csv") as f:
    reader = csv.DictReader(f)
    count = 0
    for row in reader:
        gen_id = model.add_generator_instance(
            unit_key=row["name"].lower().replace(" ", "_"),
            name=row["name"],
            gen_type=row["type"],
            fuel=row["fuel"],
            node=int(row["node"]),
            rated_power=float(row["rated_power_mw"]),
        )
        # Set additional properties on the state object
        gen = state.generators[gen_id]
        gen.invest_cost = float(row.get("invest_cost", 0))
        gen.life_time = int(row.get("lifetime", 25))
        gen.fuel_cost = float(row.get("fuel_cost", 0))
        count += 1

print(f"Imported {count} generators")
```

**Expected CSV format:**
```csv
name,type,fuel,node,rated_power_mw,invest_cost,lifetime,fuel_cost
Solar Farm A,Renewable,Solar,0,50,1200,25,0
Diesel Plant B,Non-renewable,Diesel,1,20,800,30,65
Wind Farm C,Renewable,Wind,2,80,1500,20,0
```


---


## Example: Scale All Capacities

```python
FACTOR = 1.5

for gen_id, gen in state.generators.items():
    model.update_generator(gen_id, rated_power=gen.rated_power * FACTOR)

for bat_id, bat in state.batteries.items():
    model.update_battery(bat_id,
        rated_power=bat.rated_power * FACTOR,
        capacity=bat.capacity * FACTOR)

print(f"All capacities scaled by {FACTOR}x")
```


---


## Example: Create Grid from Adjacency Matrix

```python
import numpy as np
from esfex.visualization.data.gui_model import GeoPoint

# Define nodes: (latitude, longitude, name)
nodes = [
    (-21.10, 55.53, "Saint-Denis"),
    (-21.15, 55.45, "Le Port"),
    (-21.34, 55.47, "Saint-Louis"),
    (-21.28, 55.70, "Saint-Benoit"),
]

# Adjacency matrix: capacity in MW (0 = no connection)
adjacency = np.array([
    [0,   300, 0,   200],
    [300, 0,   250, 0  ],
    [0,   250, 0,   150],
    [200, 0,   150, 0  ],
])

# Create nodes
for i, (lat, lng, name) in enumerate(nodes):
    model.add_node(name=name)
    state.nodes[i].coordinate = GeoPoint(lat=lat, lng=lng, label=name)

# Create lines from upper triangle of adjacency matrix
line_count = 0
for i in range(len(nodes)):
    for j in range(i + 1, len(nodes)):
        if adjacency[i, j] > 0:
            model.add_line(from_node=i, to_node=j,
                          capacity_mw=float(adjacency[i, j]))
            line_count += 1

print(f"Created {len(nodes)} nodes and {line_count} lines")
```


---


## Example: Color Generators by Fuel Type

```python
from esfex.visualization.data.gui_model import VisualStyle

COLORS = {
    "Solar": "#f39c12",    # Orange
    "Wind": "#3498db",     # Blue
    "Diesel": "#7f8c8d",   # Gray
    "Gas": "#e67e22",      # Dark orange
    "Hydro": "#1abc9c",    # Teal
    "Biomass": "#27ae60",  # Green
    "Nuclear": "#8e44ad",  # Purple
    "Hydrogen": "#2ecc71", # Light green
}

for gen_id, gen in state.generators.items():
    color = COLORS.get(gen.fuel, "#95a5a6")
    size = max(8, gen.rated_power * 0.05)  # Scale marker with capacity
    gen.style = VisualStyle(color=color, size=size)
    model.update_generator(gen_id)

print(f"Colored {len(state.generators)} generators")
```


---


## Example: Export System Data to Excel

```python
import pandas as pd

# Generator data
gen_data = [{
    "Name": g.name,
    "Type": g.gen_type,
    "Fuel": g.fuel,
    "Node": g.node,
    "Rated Power (MW)": g.rated_power,
    "Fuel Cost ($/MWh)": g.fuel_cost,
    "Investment Cost ($/MW)": g.invest_cost,
    "Lifetime (years)": g.life_time,
} for g in state.generators.values()]

# Battery data
bat_data = [{
    "Name": b.name,
    "Node": b.node,
    "Power (MW)": b.rated_power,
    "Capacity (MWh)": b.capacity,
    "Charge Eff.": b.charge_efficiency,
    "Discharge Eff.": b.discharge_efficiency,
} for b in state.batteries.values()]

# Line data
line_data = [{
    "Line ID": l.line_id,
    "From": l.from_ref.element_id if l.from_ref else "?",
    "To": l.to_ref.element_id if l.to_ref else "?",
    "Capacity (MW)": l.capacity_mw,
    "Voltage (kV)": l.voltage_kv,
} for l in state.transmission_lines.values()]

with pd.ExcelWriter("system_export.xlsx") as writer:
    pd.DataFrame(gen_data).to_excel(writer, sheet_name="Generators", index=False)
    pd.DataFrame(bat_data).to_excel(writer, sheet_name="Batteries", index=False)
    pd.DataFrame(line_data).to_excel(writer, sheet_name="Lines", index=False)

print("Exported to system_export.xlsx")
```


---


## Example: Find Undersized Lines

```python
THRESHOLD_MW = 100  # Flag lines below this capacity

undersized = []
for line_id, line in state.transmission_lines.items():
    if line.capacity_mw < THRESHOLD_MW:
        undersized.append((line_id, line.capacity_mw))

if undersized:
    print(f"Found {len(undersized)} lines below {THRESHOLD_MW} MW:")
    for lid, cap in sorted(undersized, key=lambda x: x[1]):
        print(f"  {lid}: {cap:.0f} MW")
else:
    print(f"All lines are at or above {THRESHOLD_MW} MW")
```


---


## Example: Compute Generation Mix

```python
from collections import defaultdict

capacity_by_fuel = defaultdict(float)
for gen in state.generators.values():
    capacity_by_fuel[gen.fuel] += gen.rated_power

total = sum(capacity_by_fuel.values())
print(f"\nGeneration Mix ({total:.0f} MW total)")
print("-" * 40)
for fuel, cap in sorted(capacity_by_fuel.items(), key=lambda x: -x[1]):
    pct = cap / total * 100 if total > 0 else 0
    bar = "#" * int(pct / 2)
    print(f"  {fuel:15s} {cap:8.1f} MW ({pct:5.1f}%) {bar}")
```


---


## Example: Run Simulation from the GUI

```python
from esfex import load_config
from esfex.runner import Orchestrator
from esfex.visualization.data.serializer import gui_state_to_yaml
import tempfile, os

# Export current state to a temporary YAML
tmp = tempfile.mkdtemp()
yaml_path = os.path.join(tmp, "gui_export.yaml")
gui_state_to_yaml(
    model._all_states, config, yaml_path,
    global_settings=model._global_settings,
    stochastic_scenarios=model._stochastic_scenarios,
)

# Load and run
cfg = load_config(yaml_path)
orch = Orchestrator(cfg, config_path=yaml_path)
results = orch.run(years=5, start_year=2025)

# Display summary
for yr in results:
    print(f"Year {yr.year}: Cost=${yr.objective:,.0f}  RE={yr.re_penetration:.1%}")
```


---


## Example: Bulk Update from External Database

```python
import sqlite3

conn = sqlite3.connect("plant_database.db")
cursor = conn.execute("""
    SELECT name, fuel_cost, efficiency, lifetime
    FROM generators
    WHERE country = 'Cuba'
""")

updated = 0
for db_name, fuel_cost, efficiency, lifetime in cursor:
    # Find matching generator by name
    for gen_id, gen in state.generators.items():
        if gen.name.lower() == db_name.lower():
            model.update_generator(gen_id,
                fuel_cost=fuel_cost,
                efficiency_rated=efficiency,
                life_time=lifetime)
            updated += 1
            break

conn.close()
print(f"Updated {updated} generators from database")
```


---


## Example: Inject Variables for Interactive Exploration

```python
# In a script: compute something complex
import numpy as np

capacities = np.array([g.rated_power for g in state.generators.values()])
names = [g.name for g in state.generators.values()]
fuels = [g.fuel for g in state.generators.values()]

# Make results available in the console
window.console.update_namespace(
    capacities=capacities,
    gen_names=names,
    gen_fuels=fuels,
)

print("Variables 'capacities', 'gen_names', 'gen_fuels' are now available in the console")
```

After running, `capacities.mean()` or `capacities.max()` are available directly in the console.


---


## Example: Generate a Matplotlib Chart

```python
import matplotlib.pyplot as plt
from collections import defaultdict

capacity_by_fuel = defaultdict(float)
for gen in state.generators.values():
    capacity_by_fuel[gen.fuel] += gen.rated_power

fuels = list(capacity_by_fuel.keys())
caps = [capacity_by_fuel[f] for f in fuels]

fig, ax = plt.subplots(figsize=(10, 5))
ax.barh(fuels, caps, color=["#f39c12", "#3498db", "#7f8c8d", "#27ae60", "#e67e22"][:len(fuels)])
ax.set_xlabel("Installed Capacity (MW)")
ax.set_title(f"Generation Capacity - {window._current_system_name}")
plt.tight_layout()
plt.savefig("capacity_chart.png", dpi=150)
plt.show()
print("Chart saved to capacity_chart.png")
```


---


## Tips and Best Practices

- **Save before running destructive scripts.** Bulk deletes, capacity scaling, and network restructuring cannot be undone across script boundaries.
- **Use `model.update_*()` methods** for all modifications. Direct attribute changes on `state` objects will not refresh the UI.
- **Use `window.console.update_namespace(my_var=data)`** to store intermediate results for later interactive exploration in the console.
- **Any installed Python library** is available: `numpy`, `pandas`, `matplotlib`, `scipy`, `networkx`, `geopandas`.
- **`state` updates automatically** on system switch. For multi-system scripts, access `model._all_states` directly.
- **Print progress** for long-running scripts to monitor execution.
- **Test on a small subset first** before applying to the full system.


---


## Error Handling

Exceptions print the full traceback in the console. The editor remains functional.

```python
# The console shows the full traceback:
# Traceback (most recent call last):
#   File "<script>", line 5, in <module>
#     model.update_generator("nonexistent_id", rated_power=100)
# KeyError: 'nonexistent_id'
```

For batch modifications, wrap operations in try/except to continue past individual failures:

```python
errors = []
for gen_id, gen in state.generators.items():
    try:
        model.update_generator(gen_id, rated_power=gen.rated_power * 1.5)
    except Exception as e:
        errors.append((gen_id, str(e)))

if errors:
    print(f"Completed with {len(errors)} errors:")
    for gid, msg in errors:
        print(f"  {gid}: {msg}")
else:
    print("All generators updated successfully")
```


---


## Subprocess Execution

External command execution via `QProcess` integration. Used internally by Run and Sensitivity actions; also available in scripts for invoking external tools.
