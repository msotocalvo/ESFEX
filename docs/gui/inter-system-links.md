# Inter-System Links

Connections between two independent power systems for power transfer or fuel transport: submarine cables, HVDC interconnectors, international pipelines, and cross-border fuel shipping routes. Links appear under the **Inter-system Links** node in the Element Tree at the project level.


---


## Concepts

In multi-system projects, each system is solved independently unless inter-system links exist. Links enable:

- **Resource sharing** -- A system with surplus renewable generation can export to a neighboring system experiencing peak demand.
- **Reliability improvement** -- Interconnection provides backup capacity and reduces the need for local reserve margins.
- **Economic optimization** -- The optimizer can trade power between systems to minimize total cost.
- **Fuel supply chains** -- Cross-border pipelines or LNG shipping routes connect fuel producers with consumers.

### Link Ownership

Jointly owned by both connected systems. Links appear once in the **Inter-system Links** section, not duplicated in each system's element list. Deleting either connected system automatically removes associated links.


---


## Link Types

### Transmission Links

**Bidirectional** electrical power exchange between systems. Typical physical realizations:

- Submarine HVDC cables between islands.
- Overhead AC/DC transmission lines between mainland grids.
- Back-to-back converter stations at frequency boundaries.

Displayed as **solid purple polylines** with arrow markers indicating the defined direction. Actual power flow may be in either direction.

### Fuel Route Links

**Directional** fuel transport between systems. Typical physical realizations:

- Natural gas pipelines.
- LNG shipping routes.
- Hydrogen pipeline corridors.
- Ammonia tanker routes.

Displayed as **dashed purple polylines**, visually distinguished from electrical transmission links.


---


## Creating Links

### From the Element Tree

1. Expand the **Inter-system Links** node in the tree.
2. Right-click **Transmission Links** (or **Fuel Routes**).
3. Select **Add New**.
4. In the Properties Panel form that opens:
   - Select the **From System** and **From Node** using the dropdown menus.
   - Select the **To System** and **To Node**.
   - Set the capacity, costs, loss parameters, and (for fuel routes) the fuel type.
5. The link appears on the map as a straight line between the two endpoints.

### From the Toolbar (Visual Drawing)

For links requiring geographic routing (curved paths around islands, along coastlines):

1. Ensure at least two systems exist in the project.
2. Select the **Inter-system Line** drawing mode from the toolbar (or the equivalent for fuel routes).
3. Click on a node in the current (active) system. It highlights to confirm selection.
4. Click on the map to add intermediate waypoints for geographic routing.
5. Switch to the target system by clicking its name in the Element Tree.
6. Click on the destination node in the target system to complete the link.
7. The Properties Panel opens the link form for parameter configuration.

**Tips:**
- Add as many waypoints as needed to accurately represent a submarine cable route or pipeline path.
- Press `Escape` at any point during the trace to cancel.
- Waypoints can be edited later using the **Edit Trace** toggle.


---


## Transmission Link Properties

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Endpoints** | From System | -- | Source system name (dropdown) |
| | From Node | -- | Node index in the source system (dropdown, filtered by selected system) |
| | To System | -- | Destination system name (dropdown) |
| | To Node | -- | Node index in the destination system (dropdown) |
| **Capacity** | Existing Capacity | MW | Current transfer capacity of the interconnection. Set to 0 if no interconnection currently exists and the link represents a potential investment. |
| | Investment Cost | $/MW | Cost per MW for building new transfer capacity. Used by the master problem optimizer. |
| | Max Investment | MW | Maximum new capacity the optimizer may build. Set to 0 to represent a fixed link with no expansion. |
| **Losses** | Loss Factor | fraction | Fraction of power lost during transfer (0.0 to 1.0). A value of 0.03 means 3% of power is lost in transit. Accounts for converter losses, cable resistance, and other transmission losses. |
| **Distance** | Distance | km | Physical length of the interconnection. Auto-calculated from the polyline coordinates if waypoints are present. Can be overridden manually for routes where the cable/pipeline length differs significantly from the straight-line distance. |
| | Cost per MW-km | $/MW/km | Distance-dependent cost component. Total investment cost = Investment Cost x MW + Cost per MW-km x MW x km. This captures the fact that longer links are more expensive per MW. |
| **Actions** | Edit Trace | toggle | Enable polyline vertex editing for the link route |

### Net Transfer Capacity (NTC)

Effective transfer capacity = sum of all parallel link capacities minus losses. The optimizer respects NTC limits in both directions:

- **Forward NTC** = Sum of (capacity x (1 - loss_factor)) for all links from System A to System B.
- **Reverse NTC** = Same sum applied in the reverse direction (same links, same losses).

Multiple parallel links between the same system pair are additive, enabling phased interconnection expansion.


---


## Fuel Route Link Properties

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Endpoints** | From System | -- | Source system name |
| | From Node | -- | Node index in the source system |
| | To System | -- | Destination system name |
| | To Node | -- | Node index in the destination system |
| **Fuel** | Fuel Type | -- | The fuel being transported (dropdown: Natural Gas, Hydrogen, Ammonia, Diesel, Fuel Oil, etc.) |
| **Capacity** | Capacity | MW | Maximum transport rate in energy-equivalent terms |
| **Losses** | Loss Factor | fraction/100km | Fraction of fuel lost per 100 km of transport distance. Models pipeline leakage, boil-off (LNG), or evaporation. |
| **Costs** | Cost per MW-km | $/MWh/km | Transport cost per unit of energy per km of distance |
| **Distance** | Distance | km | Route length (auto-calculated from polyline or entered manually) |
| **Actions** | Edit Trace | toggle | Enable polyline vertex editing |


---


## Managing Links

### Selecting and Editing

- Click a link in the Element Tree to select it. Its form appears in the Properties Panel and the link highlights on the map.
- Edit any field in the form. Changes are reflected immediately on the map (e.g., capacity changes update the line width).

### Editing Link Routes

1. Select the link.
2. Click **Edit Trace** in the Properties Panel.
3. Waypoint vertices become visible as draggable handles.
4. Drag handles to reshape the route.
5. Click **Edit Trace** again to confirm.

### Deleting Links

- Select a link and press `Delete`.
- Right-click a link in the tree and select **Delete**.
- Links are automatically removed when either connected system is deleted.

### Viewing All Links

Expand the **Inter-system Links** node in the tree to see all links organized by type:

```
Inter-system Links
  Transmission Links (3)
    Link: Cuba -> Jamaica (500 MW)
    Link: Cuba -> Cayman (200 MW)
    Link: Jamaica -> Haiti (300 MW)
  Fuel Routes (1)
    Route: Trinidad -> Jamaica (Natural Gas, 1000 MW)
```


---


## DC-OPF Across Systems

When multiple systems are interconnected via transmission links with DC power flow enabled, the optimizer solves a coupled optimal power flow respecting:

1. **Capacity constraints** -- Power flow on each link is bounded by its NTC.
2. **Loss modeling** -- Transfer losses reduce the power received by the importing system.
3. **Angle constraints** (if enabled) -- Voltage angle differences between the connected nodes are bounded by the maximum angle difference setting.
4. **Investment decisions** -- The master problem can invest in new link capacity up to the Max Investment limit, weighing interconnection costs against generation investment alternatives.

The coupled solution minimizes total system cost across all interconnected systems, accounting for transfer costs, losses, and interconnection investment.


---


## Constraints and Validation

| Rule | Description |
|------|-------------|
| **No self-links** | A system cannot link to itself. Use intra-system transmission lines instead. |
| **Valid endpoints** | Both endpoint systems must exist in the project. |
| **Valid nodes** | Node indices must be valid within their respective systems. |
| **Positive capacity** | Existing capacity plus max investment must be greater than zero (otherwise the link serves no purpose). |
| **Non-negative losses** | Loss factor must be between 0.0 and 1.0. |
| **Parallel links allowed** | Multiple links between the same pair of systems are permitted and their capacities are additive. |
| **Fuel type required** | Fuel route links must have a fuel type assigned. |

The validation dialog checks all links against these rules and reports violations.


---


## Map Display and Visual Style

| Link Type | Line Style | Color | Markers |
|-----------|-----------|-------|---------|
| Transmission | Solid | Purple | Arrow markers at midpoint and endpoint |
| Fuel Route | Dashed | Purple | Arrow markers at midpoint |

Both link types support complex geographic paths with intermediate waypoints, preserved through save/load cycles and exported as coordinate arrays in YAML.

Line width scales with capacity:
- Links under 100 MW: thin line (2px).
- Links 100-500 MW: medium line (3px).
- Links over 500 MW: thick line (4px).


---


## Workflow Example: Connecting Two Island Systems

1. Create two systems: "Cuba" and "Jamaica".
2. Add nodes to each system representing their respective substations.
3. In the Element Tree, right-click **Inter-system Links > Transmission Links > Add New**.
4. Set From System = "Cuba", From Node = 3 (a coastal substation).
5. Set To System = "Jamaica", To Node = 0 (the nearest Jamaican substation).
6. Set Existing Capacity = 0 MW (no current interconnection).
7. Set Investment Cost = 2000 $/MW, Max Investment = 500 MW.
8. Set Loss Factor = 0.04 (4% for a ~200 km submarine cable).
9. Set Distance = 200 km, Cost per MW-km = 5 $/MW/km.
10. Click **Edit Trace** and add waypoints to route the cable around the Cayman Trench.
11. Validate the project. The optimizer will now consider building this interconnection.
