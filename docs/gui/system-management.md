# System Management

Multiple independent power systems within a single project. Each system represents a self-contained electrical network (island, country, regional grid) with its own nodes, generators, batteries, lines, and settings. Systems interconnect via inter-system links for cross-border power exchange.


---


## Element Tree

Hierarchical organization of all project elements:

- **Global Settings** -- Simulation-wide parameters (solver, temporal resolution, N-1 security). Always at the top, not tied to any system.
- **Stochastic Scenarios** -- Multi-scenario definitions with probability weights. Shared across all systems.
- **Inter-system Links** -- Cross-system transmission lines and fuel routes. Contains two sub-categories: Transmission Links and Fuel Route Links.
- **System 1** (bold) -- First power system, expandable into component categories.
- **System 2** (bold) -- Second power system, and so on.

### System Component Categories

Each system contains these categories (element count shown in parentheses):

| Category | Contains |
|----------|----------|
| **Nodes** | Geographic load/generation locations |
| **Generators** | All generation units (renewable and thermal) |
| **Batteries** | Energy storage systems |
| **Transmission Lines** | Electrical connections between nodes |
| **Transformers** | Voltage conversion equipment |
| **Buses** | Electrical connection points at specific voltage levels |
| **AC/DC Converters** | Interface between AC and DC buses |
| **Frequency Converters** | Interface between different frequency systems |
| **Development Zones** | Geographic areas for new capacity installation |
| **Fuel Entries** | Fuel import points |
| **Fuel Sources** | Primary energy supply definitions |
| **Fuel Storages** | Fuel stockpiling facilities |
| **Fuel Routes** | Fuel transport pathways |
| **Fuels** | Fuel type definitions (emission factors, prices) |
| **Electrolyzers** | Power-to-hydrogen conversion units |
| **EV Configuration** | Electric vehicle fleet parameters |
| **Rooftop Solar** | Distributed PV deployment settings |
| **Technologies** | Technology investment parameters |
| **Investment Portfolio** | Historical and planned investments |


---


## Creating a System

### From the Toolbar

1. Click **Add System** in the toolbar.
2. A dialog prompts you for a system name. Enter a descriptive name (e.g., "Cuba_Western_Grid", "Jamaica", "Puerto_Rico").
3. Click **OK**. A new empty system appears in the tree.
4. The new system becomes active, and all drawing mode buttons are enabled.

### From an Imported YAML

When you open a YAML configuration file (`Ctrl+O`) that contains multiple systems, each system is automatically created in the tree with all its elements populated. The map centers on the first system's elements.

**Tips:**
- System names must be unique within the project. The dialog will warn you if you enter a duplicate name.
- System names should use alphanumeric characters, underscores, and hyphens. Avoid spaces and special characters for maximum compatibility with the YAML export format.
- There is no hard limit on the number of systems, but performance may degrade with more than 20 heavily populated systems.


---


## Switching Between Systems

Only one system is displayed on the map at a time:

1. **Click on a system name** in the Element Tree. The map updates to show that system's elements.
2. The Properties Panel clears (no element selected in the new system).
3. The active system name appears in **bold** in the tree; inactive systems appear in normal weight.
4. All drawing mode actions now create elements within the newly active system.

Alternatively, right-click a system name and select **Switch To** from the context menu.

**Tips:**
- When switching systems, the map viewport (zoom level and center point) remains unchanged. This is intentional for multi-system projects where systems are geographically close.
- Inter-system links are always visible in the tree regardless of the active system.
- The Python console's `state` variable automatically updates to reference the active system's data.


---


## Renaming a System

1. Click on the system name in the tree to select it and open the System Settings form.
2. Edit the **Name** field in the Properties Panel.
3. Press Enter or click elsewhere to confirm.

The rename propagates to the Element Tree, all inter-system links referencing this system, and the Python console.


---


## Deleting a System

1. Right-click the system name in the Element Tree.
2. Select **Delete System** from the context menu.
3. A confirmation dialog appears listing:
   - The total number of elements that will be removed (nodes, generators, batteries, lines, etc.).
   - Any inter-system links involving this system.
4. Click **Yes** to confirm or **No** to cancel.

**Warning:** System deletion cannot be undone. Save before deleting. All elements within the system and all inter-system links connected to it are permanently removed.


---


## Adding Elements

Three ways to add elements to the active system:

### From the Toolbar (Map Interaction)

1. Select a drawing mode (Node, Generator, Battery, Line, etc.) from the toolbar.
2. Click on the map at the desired location.
3. The element is created in the active system and its form opens in the Properties Panel.

Primary method for elements with geographic positions: nodes, generators, batteries, fuel entries, fuel storage, lines, fuel routes, zones, transformers, buses, converters, and electrolyzers.

### From the Element Tree (Right-Click Menu)

1. Right-click a category heading (e.g., "Generators", "Fuels") under the active system.
2. Select **Add New** from the context menu.
3. A new element is created with default values and its form opens.

Convenient for non-spatial elements (fuels, technologies, stochastic scenarios) or for pre-configuring elements before map placement.

### From the Menu Bar

Use **Edit > Add** for elements not tied to a single geographic location: fuels, technologies, and stochastic scenarios.


---


## Deleting Elements

| Method | How |
|--------|-----|
| **Keyboard** | Select an element and press `Delete` |
| **Context menu** | Right-click an element and select **Delete** |
| **Properties Panel** | Click the delete button at the bottom of the element's form |

Deleting an element with dependents (e.g., a node with attached generators and batteries) triggers a confirmation dialog listing every dependent element. The deletion is atomic.

**Cascade rules:**
- Deleting a **node** removes all generators, batteries, fuel entries, fuel storage, transformers, buses, and converters attached to it, plus all lines and fuel routes with an endpoint at that node.
- Deleting a **bus** removes all equipment connected to that bus (converters, transformers referencing it).
- Deleting a **system** removes all its elements and any inter-system links referencing it.


---


## Duplicating Elements

1. Select an element in the tree or on the map.
2. Right-click > **Duplicate**, or press `Ctrl+D`.

The duplicate receives:
- A new unique ID.
- The same technical parameters (capacity, costs, efficiency, etc.).
- A slightly offset geographic position (so it does not overlap the original on the map).
- The same node assignment (for generators, batteries, etc.).

Useful for quickly creating multiple similar elements, e.g., duplicating a 50 MW solar generator to create several identical installations.


---


## Copy / Paste Properties

Transfer technical parameters between elements of the same type:

1. Select the source element and press `Ctrl+C`. A status message confirms the copy.
2. Select the target element (must be the same type, e.g., generator-to-generator).
3. Press `Ctrl+V`.

**What is transferred:** All technical properties (capacity, costs, efficiency, impedance, fuel type, etc.).

**What is preserved on the target:** Position, ID, name, node assignment, and visual style.

Useful for applying a reference element's parameters to several other elements.


---


## Search and Filtering

### Tree Search

The search box at the top of the Element Tree filters elements by name in real time. Only matching elements are displayed; categories with no matching children collapse automatically.

- Clear the search box (or press `Escape` while it is focused) to restore the full tree.
- Search is case-insensitive.
- Search applies across all systems simultaneously.

### Tree Navigation

- **Expand All / Collapse All**: Right-click any category heading and select the appropriate option.
- **Double-click** an element to center the map on it and zoom to an appropriate level.


---


## Multi-Selection and Batch Editing

### Selecting Multiple Elements

| Method | How |
|--------|-----|
| **Ctrl+Click** | Add individual elements to the selection |
| **Shift+Click** | Select a range of elements in the tree (from last selected to clicked item) |

### Batch Edit Behavior

When multiple elements of the same type are selected, the Properties Panel enters batch-edit mode:

- Fields with identical values across all selected elements display that value normally.
- Fields with differing values display **"Mixed"** in gray italic.
- Changing any field applies the new value to all selected elements.
- Unique fields (ID, name, position) are disabled in batch mode.

### Practical Uses

- Set uniform investment cost across all solar generators.
- Apply the same efficiency to all batteries.
- Update fuel cost for all diesel generators simultaneously.
- Change the lifetime of all generators of a specific type.


---


## Context Menus

### On an Element

| Action | Description |
|--------|-------------|
| Delete | Remove this element (with cascade confirmation) |
| Duplicate | Create a copy with new ID and offset position |
| Copy Properties | Copy technical parameters to clipboard |
| Paste Properties | Apply clipboard parameters (same type required) |
| Center Map | Pan and zoom the map to this element |
| Edit Trace | (Lines/routes only) Toggle polyline vertex editing |
| Edit Polygon | (Zones only) Toggle polygon vertex editing |

### On a Category

| Action | Description |
|--------|-------------|
| Add New | Create a new element of this type |
| Expand All | Expand all items under this category |
| Collapse All | Collapse all items under this category |

### On a System

| Action | Description |
|--------|-------------|
| Switch To | Make this the active system |
| Rename | Open the rename field |
| Delete System | Remove the system and all its elements |
| Add Node | Quick-add a node to this system |


---


## Import and Export Per-System

### Exporting a Single System

To export only one system from a multi-system project:

1. Open the export dialog (`Ctrl+Shift+S`).
2. Select the system(s) to include in the export.
3. Choose the output path and click **Export**.

The exported YAML contains only the selected systems and any inter-system links between them.

### Importing into an Existing Project

When you open a YAML file, all systems in that file are loaded. If the project already has systems with the same names, a conflict dialog offers three options:

- **Replace** -- Overwrite the existing system with the imported data.
- **Rename** -- Import with a new name (a suffix is appended).
- **Skip** -- Do not import the conflicting system.
