# Development Zones

## What Are Development Zones

Geographic areas where specific technologies can be developed. ESFEX preprocesses zones into virtual nodes with transmission connections, enabling the optimizer to decide investment location and capacity while accounting for interconnection costs.

Zones capture:

- **Geographic location**: Where the resource area is (polygon boundary)
- **Resource potential**: Maximum capacity that can be installed
- **Interconnection cost**: Cost to build the transmission line and transformer connecting the zone to the grid
- **Technology specificity**: Which generation or storage technologies can be built in each zone

The optimizer jointly decides generation investments and associated interconnection costs.


---


## How Zones Work Internally

`expand_config_with_zones()` preprocesses each zone before model construction:

1. **Virtual node creation**: A new node is added to the network at the polygon centroid coordinates
2. **Virtual bus creation**: A new electrical bus is added at the virtual node (with zero demand fraction, since no load exists at the zone)
3. **Nearest bus detection**: The closest existing bus is found using Haversine distance from the zone centroid to existing node coordinates
4. **Transmission line**: A candidate transmission line is added from the nearest existing node to the zone node, with investment cost proportional to distance
5. **Generator/technology expansion**: Per-node arrays of all matching generators, batteries, and technologies are extended to include the virtual node, with appropriate investment limits
6. **Adjacency matrix expansion**: The network adjacency matrix grows from NxN to (N+Z)x(N+Z), where Z is the number of zones



---


## Zone Configuration in YAML

### Basic Example

```yaml
systems:
  my_system:
    development_zones:
      - name: southern_desert
        technology: solar_pv
        polygon:
          - latitude: 22.0
            longitude: -82.5
          - latitude: 22.0
            longitude: -82.0
          - latitude: 21.8
            longitude: -82.0
          - latitude: 21.8
            longitude: -82.5
        max_capacity_mw: 500.0
        line_cost_per_mw_km: 1500.0
        transformer_cost_per_mw: 50000.0
```

### Full Configuration Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | (required) | Unique identifier for the zone |
| `technology` | string | (required) | Technology type (e.g., "solar_pv", "Wind", "Battery") |
| `layer` | string | `"electrical"` | Layer: `"electrical"` or `"primary_energy"` |
| `polygon` | list of coordinates | (required) | Polygon boundary vertices |
| `max_capacity_mw` | float or null | `null` | Maximum installable capacity (MW). `null` = unlimited |
| `line_cost_per_mw_km` | float | `1500.0` | Transmission line cost ($/MW/km) |
| `transformer_cost_per_mw` | float | `50000.0` | Step-up transformer cost ($/MW) |
| `target_bus` | int or null | `null` | Override automatic nearest-bus detection (0-indexed bus index) |
| `allowed_generators` | list of strings or null | `null` | Explicit list of generator keys allowed in zone. `null` = match by technology name |
| `allowed_technologies` | dict or null | `null` | Technology keys mapped to per-technology max investment (MW). `null` = match by technology name |
| `exclusive` | bool | `false` | If `true`, matched technologies can ONLY invest at this zone; investment at original nodes is zeroed out |
| `notes` | string or null | `null` | Free-text notes for documentation |

### Polygon Coordinates

Each vertex is a `GeoCoordinate` (latitude -90 to 90, longitude -180 to 180, WGS84):

```yaml
polygon:
  - latitude: 23.2
    longitude: -82.5
  - latitude: 23.2
    longitude: -82.3
  - latitude: 23.0
    longitude: -82.3
  - latitude: 23.0
    longitude: -82.5
```

The polygon does not need to be closed. ESFEX uses the centroid for virtual node positioning and distance calculations.


---


## How Zones Map to Nodes in the Network

With N original nodes and Z zones, the expanded system has N+Z total nodes:

```
Original nodes:  0, 1, 2, ..., N-1
Zone nodes:      N, N+1, N+2, ..., N+Z-1
```

Each zone node is connected to its nearest existing node via a small initial connection (0.001 MW in the adjacency matrix), which triggers the creation of transmission investment variables in the optimization model.

### Example: 3-Node System with 2 Zones

```
Original system:
  Node 0 (City A) ---- Node 1 (City B) ---- Node 2 (City C)

After zone expansion:
  Node 0 ---- Node 1 ---- Node 2
    |                        |
  Node 3                  Node 4
  (Solar Zone)           (Wind Zone)
```

Connections are candidate transmission lines whose capacity is determined by the optimizer. Interconnection cost ensures transmission is built only where resource value justifies the infrastructure investment.


---


## Generator and Battery Assignment to Zones

### Automatic Technology Matching

Zones match generators/technologies using fuzzy name matching. A zone with `technology: solar_pv` matches any generator or technology whose key, name, or fuel contains "solar_pv" (case-insensitive).

The matching checks:
1. Generator/technology key (e.g., `solar_pv` in `generators.solar_pv`)
2. Fuel field (e.g., `fuel: Sun`)
3. Technology field (e.g., `technology: tech_solar`)

### Explicit Generator Assignment

```yaml
development_zones:
  - name: solar_zone_south
    technology: solar_pv
    allowed_generators:
      - solar_pv          # Only this specific generator
    polygon: [...]
    max_capacity_mw: 200.0
```

### Technology Assignment with Per-Technology Limits

```yaml
development_zones:
  - name: renewable_zone_east
    technology: solar_pv
    allowed_technologies:
      tech_solar: 300.0       # Up to 300 MW solar
      tech_wind: 100.0        # Up to 100 MW wind
    polygon: [...]
    max_capacity_mw: 500.0    # Overall zone limit
```

With `allowed_technologies`:
- Only listed technologies can invest at the zone node
- Each has its own maximum investment (dict value in MW)
- `max_capacity_mw` still applies as aggregate constraint

### Battery Zone Assignment

Set `technology` to `battery`, `storage`, `bess`, or `ess` to activate battery matching:

```yaml
development_zones:
  - name: storage_hub
    technology: battery
    polygon: [...]
    max_capacity_mw: 50.0
    allowed_technologies:
      lithium_ion: 50.0       # Battery technology key with limit
```

---


## Zone-Based Investment Limits and Constraints

### Capacity Limits

`max_capacity_mw` sets total installable capacity, representing land area, permitting, or environmental constraints:

```yaml
development_zones:
  - name: protected_coastal_wind
    technology: Wind
    max_capacity_mw: 50.0     # Limited by environmental permit
    polygon: [...]
```

If `null` or omitted, defaults internally to 1,000,000 MW (effectively unlimited).

### Interconnection Cost

Total interconnection cost per MW:

```
interconnection_cost = line_cost_per_mw_km * distance_km + transformer_cost_per_mw
```

`distance_km` is the Haversine distance from zone centroid to the nearest existing node.

**Example** (30 km from nearest node):
```
cost = 1500 * 30 + 50000 = 95,000 $/MW
```

### Exclusive Zones

`exclusive: true` forces investment only at the zone -- `invest_max_power` is set to 0 at all original nodes for matching technologies:

```yaml
development_zones:
  - name: only_solar_here
    technology: solar_pv
    exclusive: true
    polygon: [...]
    max_capacity_mw: 1000.0
```

Useful for modeling policies that restrict development to designated areas.

### Target Bus Override

Override automatic nearest-bus detection:

```yaml
development_zones:
  - name: offshore_wind
    technology: Wind
    target_bus: 3             # Connect to bus 3 regardless of distance
    polygon: [...]
    max_capacity_mw: 200.0
```

Useful when the optimal connection point differs from the geographically closest one (e.g., due to grid capacity or existing infrastructure).


---


## Interconnection Between Zones

Zones connect only to their nearest existing node (or `target_bus`). Direct zone-to-zone connections are not created automatically. To interconnect two zones:

1. Ensure both zones connect to the same existing node (using `target_bus`)
2. Or connect them through the existing network



---


## Multi-Zone Planning Strategies

### Strategy 1: Competing Solar Zones

Multiple solar zones let the optimizer choose the best locations:

```yaml
development_zones:
  - name: solar_south
    technology: solar_pv
    polygon: [...]  # Southern desert, high irradiance
    max_capacity_mw: 500.0
    line_cost_per_mw_km: 1500.0

  - name: solar_east
    technology: solar_pv
    polygon: [...]  # Eastern coast, moderate irradiance
    max_capacity_mw: 300.0
    line_cost_per_mw_km: 1500.0

  - name: solar_central
    technology: solar_pv
    polygon: [...]  # Near load center, lower irradiance
    max_capacity_mw: 200.0
    line_cost_per_mw_km: 1500.0
```

The optimizer invests where resource quality (availability) best offsets connection cost (distance).

### Strategy 2: Technology Diversification Zones

```yaml
development_zones:
  - name: desert_solar
    technology: solar_pv
    exclusive: true
    polygon: [...]
    max_capacity_mw: 1000.0

  - name: coastal_wind
    technology: Wind
    exclusive: true
    polygon: [...]
    max_capacity_mw: 500.0

  - name: storage_hub
    technology: battery
    polygon: [...]
    max_capacity_mw: 200.0
```

### Strategy 3: Mixed-Technology Zones

Allow multiple technologies in the same geographic area:

```yaml
development_zones:
  - name: industrial_zone
    technology: solar_pv
    allowed_technologies:
      tech_solar: 200.0
      tech_wind: 100.0
      lithium_ion: 50.0
    polygon: [...]
    max_capacity_mw: 400.0
```

---


## Example: Island Grid with 3 Development Zones

```yaml
systems:
  island_system:
    name: Island Grid
    nodes:
      num_nodes: 3
      nodes_connections: [0, 50, 0, 50, 0, 30, 0, 30, 0]
      node_coordinates:
        - latitude: 22.4
          longitude: -79.9
        - latitude: 22.1
          longitude: -79.6
        - latitude: 21.8
          longitude: -79.3
      node_names: [city_north, city_center, city_south]

    generators:
      diesel_gen:
        name: Diesel Plant
        type: Non-renewable
        fuel: Diesel
        rated_power: [30.0, 20.0, 10.0]
        # ... other fields ...

      existing_solar:
        name: Existing Solar
        type: Renewable
        fuel: Sun
        rated_power: [5.0, 0.0, 0.0]
        Availability: data/profile_sun_1.xlsx
        # ... other fields ...

    technologies:
      tech_solar:
        name: Solar PV Investment
        type: Renewable
        fuel: Sun
        invest_cost: [900000.0, 900000.0, 900000.0]
        invest_max_power: [100.0, 100.0, 100.0]
        Availability: data/profile_sun_1.xlsx
        lifetime: 25
        # ... other fields ...

      tech_wind:
        name: Wind Investment
        type: Renewable
        fuel: Wind
        invest_cost: [1200000.0, 1200000.0, 1200000.0]
        invest_max_power: [50.0, 50.0, 50.0]
        Availability: data/profile_wind_1.xlsx
        lifetime: 25
        # ... other fields ...

    battery_technologies:
      lithium_ion:
        name: Li-ion Battery
        invest_cost_power: [400000.0, 400000.0, 400000.0]
        invest_cost_energy: [200000.0, 200000.0, 200000.0]
        invest_max_power: [50.0, 50.0, 50.0]
        invest_max_capacity: [200.0, 200.0, 200.0]
        lifetime: 15
        # ... other fields ...

    development_zones:
      - name: desert_solar
        technology: solar_pv
        polygon:
          - latitude: 22.6
            longitude: -80.2
          - latitude: 22.6
            longitude: -79.8
          - latitude: 22.3
            longitude: -79.8
          - latitude: 22.3
            longitude: -80.2
        max_capacity_mw: 500.0
        line_cost_per_mw_km: 1500.0
        transformer_cost_per_mw: 50000.0
        allowed_technologies:
          tech_solar: 500.0
        notes: "Desert area 20 km NW of city_north with high irradiance"

      - name: coastal_wind
        technology: Wind
        polygon:
          - latitude: 21.6
            longitude: -79.5
          - latitude: 21.6
            longitude: -79.1
          - latitude: 21.4
            longitude: -79.1
          - latitude: 21.4
            longitude: -79.5
        max_capacity_mw: 200.0
        line_cost_per_mw_km: 2000.0
        transformer_cost_per_mw: 75000.0
        allowed_technologies:
          tech_wind: 200.0
        notes: "Coastal area south of city_south with strong trade winds"

      - name: storage_hub
        technology: battery
        polygon:
          - latitude: 22.2
            longitude: -79.7
          - latitude: 22.2
            longitude: -79.5
          - latitude: 22.0
            longitude: -79.5
          - latitude: 22.0
            longitude: -79.7
        max_capacity_mw: 100.0
        line_cost_per_mw_km: 1000.0
        transformer_cost_per_mw: 30000.0
        notes: "Industrial area near city_center for battery storage"
```

After zone expansion, the system grows from 3 nodes to 6 nodes:
- Nodes 0-2: Original city nodes
- Node 3: `desert_solar` virtual node (connected to nearest bus, likely node 0)
- Node 4: `coastal_wind` virtual node (connected to nearest bus, likely node 2)
- Node 5: `storage_hub` virtual node (connected to nearest bus, likely node 1)

---


## Availability Profiles for Zone Nodes

ESFEX copies the availability profile from the nearest existing node to each zone's virtual node. For more accurate representation, provide profile files with enough columns to cover zone nodes.


---


## Zone-Level Results Analysis

Zone investments appear in results with virtual node indices. To trace back to zones:

1. **Investment decisions**: Look for generation investments at node indices >= N (the original node count). For example, `gen_investment_power_solar_pv_3` indicates solar investment at virtual node 3 (the first zone).

2. **Transmission investments**: The transmission capacity from the zone node to its connection node shows the interconnection capacity the optimizer chose to build.

3. **Generation output**: Virtual node generation output in the HDF5 results represents the zone's energy contribution to the system.

4. **Zone mapping log**: ESFEX logs detailed zone mapping information at startup:
   ```
   Zone 'desert_solar' (solar_pv): virtual node 3, bus 3 -> nearest bus 0
   (node 0, 25.3 km), interconnection cost $87950/MW,
   matched gens: ['existing_solar'], matched techs: ['tech_solar']
   ```

The log shows zone name, virtual node/bus indices, nearest connection point, interconnection cost, and matched generators/technologies.
