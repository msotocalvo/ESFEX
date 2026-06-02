# Element Forms

Property form reference for all element types. Forms use collapsible sections and tabs. Changes take effect immediately and reflect on the map and in the element tree.


---


## Electrical Infrastructure

### Node

Fundamental geographic locations in the power system. Each node represents a substation, load center, or generation hub. All equipment attaches to a node.

| Tab | Field | Unit | Description |
|-----|-------|------|-------------|
| **General** | Name | -- | Display name for the node (e.g., "Saint-Denis Substation") |
| | Index | -- | Auto-assigned integer index (read-only). Used as the internal identifier. |
| | Latitude / Longitude | degrees | Geographic position in WGS84. Editable manually or by dragging the node on the map. |
| | Static Reserve | MW | Minimum spinning reserve requirement at this node. Sets a floor for available generation headroom. |
| | Dynamic Reserve | MW | Frequency-response reserve requirement. Ensures fast-acting capacity is available for contingencies. |
| | Reserve Duration | hours | Duration over which reserve capacity must be sustained. Default is 1 hour. |
| | Losses | fraction | Distribution losses at this node (0.0 to 1.0). Applied as a multiplier to served demand. A value of 0.03 means 3% of generation is lost in local distribution. |
| | Transference Investment Cost | $/MW | Cost per MW to increase transfer capacity at this node. Used by the optimizer when expanding interconnections. |
| | Transference Investment Max | MW | Maximum allowable transfer capacity expansion at this node. |
| **Demand** | Demand File | path | CSV or Excel file with hourly demand data in MW. Must contain 8760 values per year (or 8784 for leap years). Use the file browser button to select. |
| | Peak Demand | MW | Auto-calculated from the demand file. Shows the maximum hourly demand value. Read-only. |
| | Total Energy | MWh | Annual energy consumption, auto-calculated by summing all hourly values. Read-only. |
| **Technologies** | (table) | -- | Per-technology maximum installable capacity at this node. Each row shows a technology name, category, existing capacity, investment cost, and maximum investment. Technologies are defined at the system level and this table controls node-specific limits. |

**Tips:**
- The demand file can be in CSV format with a single column of MW values, or an Excel file with the first column containing the data.
- If no demand file is assigned, the node is treated as a pure generation or transit node (zero demand).
- The Technologies table inherits from the system-level technology definitions. Override values here to set node-specific constraints.

### Generator

All forms of electricity production: solar PV, wind turbines, diesel engines, gas turbines, etc.

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Identity** | Name | -- | Generator name (e.g., "Solar Farm North") |
| | Type | -- | `Renewable` or `Non-renewable`. Determines how the generator counts toward RE penetration targets. |
| | Fuel | -- | Primary energy source: Solar, Wind, Water (hydro), OTEC, Diesel, Natural Gas, Fuel Oil, Biomass, Biogas, Hydrogen, Nuclear, Geothermal, etc. |
| | Node | -- | Parent node index. Can be changed by selecting a different node from the dropdown. |
| **Capacity** | Rated Power | MW | Nameplate capacity. The maximum power output under standard conditions. |
| | Minimum Power | MW | Minimum stable output. Relevant for thermal generators that cannot operate below a certain level without shutting down. Set to 0 for renewables. |
| | Availability Profile | path | CSV file with hourly capacity factors (0.0 to 1.0) for renewable generators. Each value represents the fraction of rated power available in that hour. Must contain 8760 values per year. Not used for dispatchable generators. |
| **Operating** | Efficiency at Rated | fraction | Conversion efficiency at full load (0.0 to 1.0). For thermal generators, this is the inverse of the heat rate. |
| | Efficiency at Minimum | fraction | Conversion efficiency at minimum stable output. Typically lower than efficiency at rated power. |
| | Ramp Up Rate | MW/h | Maximum rate of output increase per hour. Constrains how quickly the generator can respond to demand changes. |
| | Ramp Down Rate | MW/h | Maximum rate of output decrease per hour. |
| | Min Up Time | hours | Minimum duration the generator must remain on after starting. Only effective in unit commitment mode. |
| | Min Down Time | hours | Minimum duration the generator must remain off after shutting down. Only effective in unit commitment mode. |
| | Degradation Rate | fraction/year | Annual capacity degradation. A value of 0.005 means 0.5% capacity loss per year. Applied cumulatively over the generator's lifetime. |
| **Costs** | Fuel Cost | $/MWh | Variable fuel cost per MWh of electrical output. For renewables, this is typically 0. |
| | Cost Curve | dropdown | Marginal cost model: Flat (default), Linear, Stepwise, or Exponential. See the Cost Curve Widgets section below. |
| | Fixed Cost | $/MW/year | Annual fixed operation and maintenance cost per MW of installed capacity. Incurred regardless of output. |
| | Maintenance Cost | $/MWh | Variable operation and maintenance cost per MWh of generation. Covers wear and consumables. |
| | Start-up Cost | $ | Cost incurred each time the generator starts. Only effective in unit commitment mode. |
| | Investment Cost | $/MW | Capital cost for building new capacity of this generator type. Used by the master problem optimizer. |
| | Max Investment | MW | Maximum new capacity that the optimizer is allowed to build for this generator. Set to 0 to prevent investment. |
| **Electrical** | Inertia Constant | s | Rotational inertia contribution (H constant). Relevant for synchronous generators. Set to 0 for inverter-based resources (solar, wind, batteries). |
| | Reserve Contribution | fraction | Fraction of online capacity available for spinning reserve provision (0.0 to 1.0). |
| **Lifecycle** | Lifetime | years | Expected operational life. The optimizer retires the generator when its age exceeds this value. |
| | Initial Age | years | Current age of existing units. Used to calculate remaining useful life. New investments start at age 0. |
| **Appearance** | Color | hex | Marker color on the map (e.g., "#27AE60" for green) |
| | Icon Shape | -- | Marker shape: circle, square, diamond, triangle-up, triangle-down, hexagon, pentagon, horizontal-bar, star |
| | Size | pixels | Marker size. Auto-scales with capacity by default; override here for manual control. |
| | Opacity | 0-1 | Marker opacity. |

#### Reservoir Mode (Hydro Generators)

When the fuel type is set to **Water** (hydroelectric), additional fields appear:

| Field | Unit | Description |
|-------|------|-------------|
| Reservoir Capacity | MWh | Maximum energy storage capacity of the reservoir |
| Initial Level | fraction | Starting water level as a fraction of reservoir capacity |
| Minimum Level | fraction | Minimum allowed water level (environmental flow constraint) |
| Inflow Profile | path | CSV file with hourly water inflow in MW-equivalent |

### Battery

All forms of energy storage: lithium-ion, flow batteries, pumped hydro, compressed air, etc.

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Identity** | Name | -- | Storage system name (e.g., "Li-ion Battery Bank A") |
| | Node | -- | Location node index |
| **Capacity** | Energy Capacity | MWh | Total energy storage capacity. Determines how long the battery can discharge at rated power. |
| | Charge Power | MW | Maximum charging rate. May differ from discharge power for asymmetric storage. |
| | Discharge Power | MW | Maximum discharging rate. |
| **Efficiency** | Charge Efficiency | fraction | Charging efficiency (0.0 to 1.0). Energy lost during charging = (1 - charge_efficiency) x charge_power. |
| | Discharge Efficiency | fraction | Discharging efficiency (0.0 to 1.0). Energy delivered = stored_energy x discharge_efficiency. |
| | Self-Discharge Rate | fraction/hour | Standing energy losses per hour. A value of 0.0001 means 0.01% of stored energy lost each hour. |
| **SOC Limits** | Initial SOC | fraction | Starting state of charge (0.0 to 1.0). The optimizer enforces cyclic SOC: the battery must return to this level at the end of each dispatch window. |
| | Minimum SOC | fraction | Depth of discharge limit. Prevents the battery from discharging below this level to preserve cycle life. |
| | Maximum SOC | fraction | Upper charge limit. Some chemistries degrade faster when charged to 100%. |
| **Duration** | Minimum Duration | hours | Minimum allowed energy-to-power ratio (E/P). Constrains investment decisions. |
| | Maximum Duration | hours | Maximum allowed energy-to-power ratio. |
| **Costs** | Investment Cost (Power) | $/MW | Capital cost per MW of power capacity for new installations. |
| | Investment Cost (Capacity) | $/MWh | Capital cost per MWh of energy capacity for new installations. |
| | Maintenance Cost | $/MWh | Variable O&M cost per MWh of throughput (charge + discharge). |
| | Discharge Cost Curve | dropdown | Discharge cost model: Flat (default), Linear, Stepwise, or Exponential. See Cost Curve Widgets below. |
| | Max Investment Power | MW | Maximum new power capacity the optimizer may build. |
| | Max Investment Capacity | MWh | Maximum new energy capacity the optimizer may build. |
| **Lifecycle** | Lifetime | years | Expected operational life in years. |
| **Appearance** | Color, Icon Shape, Size | -- | Visual style on the map |

#### Cost Curve Widgets

Generator and battery forms include a **cost curve dropdown** controlling the marginal cost model:

- **Flat** (default) -- No additional widgets. Uses flat `Fuel Cost` (generator) or `Maintenance Cost` (battery). Suitable for most renewables and simple thermal units.

- **Linear** -- Two additional fields appear:
  - `Price at Zero` ($/MWh) -- Marginal cost when output is zero (intercept).
  - `Price at Max` ($/MWh) -- Marginal cost at full rated output (slope endpoint).
  - `Segments` spinner (2--20, default 5) -- Controls the piecewise-linear (PWL) approximation resolution. More segments yield a smoother curve but increase the number of constraints in the optimization.

  The cost increases linearly from `Price at Zero` to `Price at Max` as output goes from 0 to rated power.

- **Stepwise** -- A dynamic table appears where each row defines a generation block:
  - `Fraction` -- Fraction of rated capacity for this block (e.g., 0.4 means 40% of rated power).
  - `Price` ($/MWh) -- Marginal cost for generation within this block.
  - Use the **+** button to add blocks and the **-** button to remove them.
  - **Constraint:** Fractions must sum to exactly 1.0. Prices should be non-decreasing (cheapest block first).
  - The optimizer dispatches blocks in order of increasing price, ensuring economic efficiency.

- **Exponential** -- Two fields appear:
  - `Base Price` ($/MWh) -- Cost at zero output.
  - `Scale Factor` -- Exponential growth rate.
  - `Segments` spinner -- PWL approximation resolution.
  - The cost follows the curve `base_price * exp(scale_factor * P/P_max)`, which models increasing marginal cost at high utilization levels.

### Transmission Line

Electrical power connections between nodes: overhead lines, underground cables, or submarine cables.

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Identity** | Line ID | -- | Unique string identifier (auto-assigned, e.g., "line_0") |
| | From / To | -- | Connected endpoints displayed as `type:id` references (e.g., "node:0", "node:3"). Set automatically during polyline trace. |
| | Type | -- | Construction type: Overhead, Underground, or Submarine. Affects default impedance parameters. |
| **Capacity** | Rated Capacity | MW | Maximum power transfer capacity in either direction. |
| | Voltage | kV | Operating voltage level. Used for DC power flow calculations. |
| | Circuits | -- | Number of parallel circuits. Total capacity = Rated Capacity x Circuits. |
| **Impedance** | Resistance | pu | Per-unit resistance (R). Higher values increase resistive losses. |
| | Reactance | pu | Per-unit reactance (X). Determines power flow distribution in DC-OPF. |
| | Susceptance | pu | Per-unit susceptance (B). Line charging effect. |
| | Base Impedance | ohm | Base impedance value for converting between physical ohms and per-unit. |
| **Geometry** | Length | km | Auto-calculated from the polyline coordinates (geodesic distance). Can be overridden manually. |
| **Investment** | Investment Cost | $/MW | Cost per MW for capacity expansion. |
| | Max Investment | MW | Maximum additional capacity the optimizer may build. |
| **Actions** | Edit Trace | toggle | Enable/disable polyline vertex editing mode. |

### Transformer

Connects buses at different voltage levels within or between nodes.

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Transformer name |
| From Bus / To Bus | -- | Primary (high voltage) and secondary (low voltage) side bus IDs |
| From Voltage / To Voltage | kV | Primary and secondary voltage levels |
| Rated Power | MVA | Transformer power rating |
| Impedance | pu | Transformer series impedance in per-unit |
| No-Load Losses | fraction | Iron core losses as a fraction of rated power (constant) |
| Load Losses | fraction | Copper losses at full load as a fraction of rated power (proportional to load squared) |
| Tap Ratio | -- | Turns ratio adjustment (1.0 = nominal). Range typically 0.9-1.1. |

### Bus

Electrical connection points at specific voltage levels within a node, enabling multi-voltage substations and complex internal topologies.

| Field | Unit | Description |
|-------|------|-------------|
| Bus ID | -- | Unique identifier (auto-assigned, e.g., "bus_0") |
| Name | -- | Bus name (e.g., "110kV Busbar A") |
| Parent Node | -- | Node index this bus belongs to |
| Voltage | kV | Bus voltage level. Determines which equipment can connect directly. |
| Frequency | Hz | System frequency: 50 Hz or 60 Hz |
| Current Type | -- | AC or DC. Determines the power flow model used. |
| Demand Fraction | fraction | Share of the parent node's demand served by this bus. All bus demand fractions within a node must sum to 1.0. |
| Is Slack | boolean | Whether this bus serves as the reference (slack) bus for DC power flow angle calculations. |

### AC/DC Converter

Interfaces between AC and DC buses for HVDC transmission and DC-coupled storage.

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Converter name |
| Converter Type | -- | VSC (Voltage Source Converter) or LCC (Line-Commutated Converter). VSC supports independent P/Q control; LCC is simpler but requires reactive power support. |
| AC Bus | -- | Connected AC bus ID |
| DC Bus | -- | Connected DC bus ID |
| Rated Power | MW | Maximum power transfer capacity |
| Rectifier Efficiency | fraction | AC-to-DC conversion efficiency (0.0 to 1.0) |
| Inverter Efficiency | fraction | DC-to-AC conversion efficiency (0.0 to 1.0) |
| Reactive Power Min | MVAr | Minimum reactive power capability (negative = absorb) |
| Reactive Power Max | MVAr | Maximum reactive power capability (positive = generate) |
| Standby Losses | MW | No-load power consumption when the converter is energized but not transferring power |

### Frequency Converter

Interconnects systems operating at different frequencies (e.g., 50 Hz and 60 Hz).

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Converter name |
| From Bus / To Bus | -- | Input and output bus IDs |
| From Frequency / To Frequency | Hz | Input and output system frequencies |
| Rated Power | MW | Maximum power transfer capacity |
| Forward Efficiency | fraction | Efficiency in the from-to direction |
| Reverse Efficiency | fraction | Efficiency in the to-from direction |


---


## Fuel Infrastructure

### Fuel Entry Point

Locations where fuel enters the system: ports, pipeline terminals, LNG terminals, etc.

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Entry point name (e.g., "Port of Mariel LNG Terminal") |
| Node | -- | Associated network node index |
| Fuels | -- | Available fuel types (multi-select checkboxes). Select all fuels that can be imported at this location. |
| **Per-fuel table** | | For each selected fuel: |
| -- Max Import Rate | MW | Maximum fuel import rate in energy-equivalent terms |
| -- Import Cost | $/MWh | Cost per MWh of fuel imported at this entry point |

### Fuel Source

Primary energy supply characteristics.

| Section | Field | Description |
|---------|-------|-------------|
| **Properties** | Name | Source identification (e.g., "Caribbean Diesel Supply") |
| | Fuel Type | Single fuel type this source provides |
| **Per-node table** | Availability | Maximum extraction/import rate per node (MW) |
| | Import Cost | Cost per MWh at each node |
| | Storage Capacity | Local buffer storage capacity at each node (MWh) |
| **Transport** | Cost per km | Transport cost per MWh per km |
| | Loss Factor | Fraction of fuel lost per km of transport |

### Fuel Storage

Tank farms, gas holders, and other fuel stockpiling facilities.

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Facility name |
| Node | -- | Location node index |
| Fuels | -- | Stored fuel types (multi-select) |
| **Per-fuel table** | | For each selected fuel: |
| -- Capacity | MWh | Maximum storage capacity in energy-equivalent terms |
| -- Initial Level | MWh | Starting fuel inventory |
| -- Minimum Level | MWh | Minimum required fuel reserve (strategic reserve floor) |

### Fuel Transport Route

Pipelines, shipping lanes, and other fuel delivery pathways between nodes.

| Field | Unit | Description |
|-------|------|-------------|
| From / To | -- | Source and destination endpoint references |
| Fuels | -- | Transported fuels (multi-select) |
| **Per-fuel table** | | For each selected fuel: |
| -- Capacity | MW | Maximum transport rate |
| -- Cost | $/MWh/km | Distance-dependent transport cost |
| -- Loss Fraction | fraction/100km | Fuel lost during transport per 100 km |
| Edit Trace | toggle | Enable polyline vertex editing for route geometry |

### Fuel Properties

System-level fuel definitions setting global parameters for each fuel type.

| Field | Unit | Description |
|-------|------|-------------|
| Fuel Name | -- | Identifier (e.g., Diesel, Natural Gas, Hydrogen, Ammonia) |
| Emission Factor | tCO2/MWh | CO2 emissions per unit of energy content. Set to 0 for zero-carbon fuels (hydrogen from electrolysis, ammonia). |
| Energy Content | MWh/unit | Energy density. Left blank for renewable sources (Solar, Wind, Water, OTEC) which have no fuel consumption. |
| Price Base | $/MWh | Base fuel price at the start of the simulation horizon |
| Price Growth Rate | %/year | Annual price escalation rate. A value of 2.0 means fuel price increases 2% per year. |

### Electrolyzer

Converts electricity to hydrogen, enabling power-to-gas coupling.

| Section | Field | Unit | Description |
|---------|-------|------|-------------|
| **Identity** | Name | -- | Electrolyzer name |
| | Type | -- | Technology: PEM (Proton Exchange Membrane), Alkaline, or SOE (Solid Oxide Electrolyzer). Each has different efficiency, cost, and ramp characteristics. |
| | Bus | -- | Connected electrical bus ID |
| **Capacity** | Rated Power | MW | Maximum electrical input power |
| | Minimum Power | MW | Minimum stable operating point |
| **Efficiency** | At Rated Load | fraction | Conversion efficiency at full power (electricity to hydrogen energy content) |
| | At Minimum Load | fraction | Efficiency at minimum operating point. Typically lower than rated efficiency for PEM and Alkaline. |
| **Economics** | Investment Cost | $/MW | Capital cost for new electrolyzer capacity |
| | Maintenance Cost | $/MWh | Variable O&M cost per MWh of electricity consumed |
| | Water Cost | $/MWh | Cost of water consumption per MWh of electricity consumed |
| **Lifecycle** | Lifetime | years | Expected operational life before stack replacement |
| | Degradation Rate | %/year | Annual efficiency degradation |
| **Operating** | Ramp Up Rate | MW/h | Maximum power increase per hour |
| | Ramp Down Rate | MW/h | Maximum power decrease per hour |


---


## Development Zone

Geographic areas where the optimizer may install new generation capacity.

| Field | Unit | Description |
|-------|------|-------------|
| Name | -- | Zone name (e.g., "Northern Solar Zone") |
| Technology | -- | Generation technology permitted: Solar PV, Wind (onshore/offshore), etc. |
| Max Capacity | MW | Maximum total installable capacity within this zone |
| Interconnection Cost | $ | Fixed cost for grid connection infrastructure |
| Allowed Generators | -- | Generator types permitted in this zone (checkboxes). Restricts which technologies the optimizer considers. |
| Interconnection Node | -- | Node to which zone generation connects |
| Edit Polygon | toggle | Enable boundary vertex editing mode |


---


## EV Configuration

Per-system electric vehicle configuration with four tabs.

### Categories Tab

Define EV types with their electrical characteristics:

| Field | Unit | Description |
|-------|------|-------------|
| Category Name | -- | EV type (e.g., "Light Vehicle", "Bus", "Truck", "Motorcycle") |
| Battery Capacity | kWh | Onboard battery capacity per vehicle |
| Charging Power | kW | Maximum charging rate per vehicle |
| Discharge Power | kW | Maximum V2G discharge rate per vehicle (0 if V2G not supported) |
| V2G Participation | fraction | Fraction of vehicles in this category that participate in vehicle-to-grid services |
| Charging Efficiency | fraction | Charger efficiency (AC-to-battery) |
| Discharge Efficiency | fraction | V2G discharge efficiency (battery-to-AC) |

### Initial SOC Tab

Per-node initial state of charge for each EV category. A table with nodes as rows and categories as columns. Values are fractions (0.0 to 1.0).

### Quantities Tab

Per-node vehicle count for each EV category. A table with nodes as rows and categories as columns. Integer values representing the number of vehicles at each location.

### Patterns Tab

24-hour charging pattern templates that define when vehicles are available for charging/discharging:

| Field | Description |
|-------|-------------|
| Pattern Name | Template name (e.g., "Residential Overnight", "Workplace Daytime") |
| Hourly Availability | 24 values (one per hour) representing the fraction of vehicles available for charging in each hour (0.0 to 1.0) |
| Category Assignment | Which EV categories use this pattern |


---


## Rooftop Solar

Per-system distributed rooftop PV configuration.

| Section | Field | Description |
|---------|-------|-------------|
| **Settings** | Adoption Scenario | Low, Medium, or High growth curve. Controls the S-curve adoption trajectory over the planning horizon. |
| | Weather Variability | Factor for inter-annual solar resource variation |
| **Performance** | Performance Ratio | System-level efficiency factor (typically 0.75-0.85) |
| | Degradation Rate | Annual panel degradation (%/year) |
| | Inverter Efficiency | DC-to-AC conversion efficiency |
| | Cost per kW | Installation cost per kW of rooftop PV |
| | O&M Cost per kW/year | Annual maintenance cost |
| **Per-node table** | Number of Systems | Total rooftop PV installations at this node |
| | Average Size | kW per installation |
| | Adoption Rate | Current adoption fraction (0.0 to 1.0) |
| | Max Adoption | Maximum possible adoption fraction |
| **Adoption Limits** | Low Scenario Max | Maximum adoption under the low growth scenario |
| | Medium Scenario Max | Maximum adoption under the medium growth scenario |
| | High Scenario Max | Maximum adoption under the high growth scenario |


---


## Multi-Select Editing

Selecting multiple elements of the same type (`Ctrl+Click` in the tree or on the map) enters batch-edit mode:

- Fields with identical values across all selected elements show that value normally.
- Fields with different values across the selection show **"Mixed"** in a gray italic font.
- Changing a field applies the new value to **all** selected elements simultaneously.
- Fields that are inherently unique (ID, name, position) cannot be batch-edited and appear disabled.

Useful for:
- Setting uniform fuel costs across all diesel generators.
- Updating investment costs for all batteries simultaneously.
- Applying a common availability profile to all solar generators.

Use `Shift+Click` in the Element Tree for range selection (all items between the last selected and the clicked item).


---


## Form Validation

Immediate visual feedback to catch errors early:

- **Red border** -- Invalid value (negative capacity, efficiency outside 0-1 range, missing required field). A tooltip explains the issue.
- **Yellow border** -- Unusual but technically valid value (very high cost, zero efficiency, capacity exceeding 10 GW). A tooltip suggests reviewing.
- **Green check** -- Value passes all validation rules.
- **Tooltips** -- Hover over any field label for a description of the parameter and its acceptable range.

Validation runs on every keystroke or value change.


---


## Deleting Elements

Deleting an element with dependents (e.g., a node with attached generators, a bus with connected lines) triggers a confirmation dialog listing:

- The element being deleted.
- All dependent elements that will be removed (generators, batteries, lines referencing the node, etc.).
- Any inter-system links that reference the deleted element.

The deletion is atomic: either all elements are removed, or none are (if cancelled).
