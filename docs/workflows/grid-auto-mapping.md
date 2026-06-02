# Grid Auto-Mapping

Automated power grid construction from open geospatial databases. Access via **Workflows > Grid Auto-Mapping**.

The wizard builds a complete power system network — buses, generators, batteries, transmission lines, transformers, converters, and fuel infrastructure — by querying publicly available geographic databases and applying spatial clustering, deduplication, and connectivity algorithms. Starting from a user-drawn polygon on the map, it fetches real-world infrastructure data, normalizes it into a unified intermediate format, creates network elements with automatic node placement, and validates the resulting topology.

The wizard is organized in a single phase of five steps:

1. **Region Definition** — draw the study area polygon on the map.
2. **Sources & Fetch** — select databases, set filters, and download infrastructure data.
3. **Review** — inspect, filter, and toggle fetched features before import.
4. **Build Network** — create nodes via spatial clustering and map features to network elements.
5. **Connect & Simplify** — auto-connect isolated components, route fuel, and clean up topology.

All spatial computations use the `esfex.visualization.workflows.grid_mapping_clustering`, `grid_mapping_fetchers`, and `grid_mapping_builder` modules, which can also be called from scripts — see [Scripting](#scripting).


---


### Step 1: Region Definition

Define the geographic study area by drawing a polygon on the interactive map.

**Drawing the polygon:**

Click on the map to place vertices. Close the polygon by clicking on or near the first vertex. The wizard minimizes automatically while drawing so the full map is visible, and restores itself when the polygon is complete.

Once drawn, the wizard displays:

- **Vertex list** — coordinates of each vertex (up to 10 shown, remainder summarized).
- **Bounding box** — the rectangular envelope `(south, west)` to `(north, east)` used for API queries.
- **Approximate area** — computed via the Shoelace formula on latitude/longitude coordinates projected to kilometers (see [GM-4](#gm-4-shoelace-polygon-area)).

The polygon can be redrawn at any time by clicking "Draw Polygon on Map" again.

**How it works:**

APIs require rectangular bounding boxes, so the wizard extracts the polygon's envelope for queries. After download, a point-in-polygon filter (ray-casting algorithm) clips results to the actual polygon boundary. This means irregular shapes — island coastlines, administrative borders, river basins — all work correctly.

**Tips:**

- Use a polygon that follows natural or administrative boundaries rather than a simple rectangle. The polygon clip removes features outside the boundary even if they fall within the bounding box.
- Larger regions produce more features and longer download times. For country-scale studies, consider splitting into sub-regions.
- The area estimate uses a cosine-latitude projection (approximate). For precise areas near the poles, verify independently.


### Step 2: Sources & Fetch

Select which geospatial databases to query, configure filters, and download infrastructure data.

This step combines source configuration and data fetching into a single interface. Configure all settings first, then click **Fetch Data** to launch parallel downloads.

**Data sources:**

| Source | Default | Description |
|--------|---------|-------------|
| **OpenStreetMap (Overpass API)** | Enabled | Crowd-sourced geographic data. Provides substations, generators, transmission lines, transformers, AC/DC converters, energy storage, fuel entry points, and fuel storage. The most comprehensive source for network topology. |
| **GEM Global Power Plants (2025)** | Enabled | Global Energy Monitor database (February 2025 update). Power plants with capacity, fuel type, commissioning year, owner, and technology sub-type. More recent than WRI. |
| **WRI Global Power Plant Database** | Disabled | World Resources Institute database (~30,000 plants worldwide). Power plants with capacity and fuel type. Useful as a cross-reference when GEM coverage is incomplete. |
| **GridFinder (Predicted Grid Routes)** | Disabled | ML-predicted transmission line routes derived from nighttime satellite imagery (Zenodo dataset). Useful for regions with sparse OSM mapping. Supports GeoJSON and GeoPackage formats. |

**Filters:**

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| Minimum voltage | 10 -- 750 kV | 110 kV | Substations and lines below this voltage are excluded. Set to 110 kV for transmission-only studies, or 33 kV to include sub-transmission. |
| Minimum generator capacity | 0 -- 10,000 MW | 1 MW | Generators with known capacity below this threshold are excluded. Generators with unknown capacity (0 MW in the database) are always included. |
| Bus snap threshold | 0.1 -- 100 km | 5 km | Distance threshold for snapping new elements to existing buses during network construction (Step 4). Elements closer than this distance to an existing bus share that bus rather than creating a new one. |

**Element types to import:**

Eight independently selectable element categories:

| Element Type | Default | OSM Tags |
|--------------|---------|----------|
| Substations / Buses | Enabled | `power=substation` |
| Generators / Power Plants | Enabled | `power=generator`, `power=plant` |
| Transmission Lines | Enabled | `power=line`, `power=cable` |
| Transformers | Enabled | `power=transformer` |
| Energy Storage | Enabled | `power=storage` |
| AC/DC Converters | Enabled | `power=converter` |
| Fuel Entry Points | Disabled | `industrial=refinery`, `industrial=fuel_depot`, `man_made=oil_terminal`, fuel-cargo ports |
| Fuel Storage | Disabled | `man_made=storage_tank` (fuel content), `industrial=tank_farm` |

**Bus creation strategy:**

| Strategy | Description |
|----------|-------------|
| **One bus per voltage level** (recommended) | Multi-voltage substations produce one bus per voltage level, connected by auto-created transformers. Yields a more realistic network topology. |
| **One bus per substation** | Each substation becomes a single bus at its highest voltage. Simpler model with fewer elements. |

**Fetching:**

Click **Fetch Data** to launch parallel download threads for all enabled sources. Each source displays its own progress bar and status message. Failed downloads are reported but do not block other sources. The button changes to **Re-fetch Data** after completion, allowing re-runs with different settings.

After all sources complete, the wizard automatically:

1. **Clips to polygon** — removes features outside the drawn polygon (APIs use the bounding box, which is larger).
2. **Deduplicates** — merges duplicate features across sources (see [Deduplication Algorithm](#deduplication-algorithm)).
3. **Summarizes** — displays counts by feature type.

**Tips:**

- Enable both OSM and GEM for the most complete picture. OSM provides network topology (substations, lines), while GEM provides accurate generator capacities and fuel types.
- Enable GridFinder only for regions with known gaps in OSM transmission line coverage (parts of sub-Saharan Africa, Southeast Asia).
- Enable fuel entry and fuel storage only if you plan to model fuel supply chains. These elements require the road network, which increases download size.
- The Overpass API has rate limits. For very large regions, the query may time out — try reducing the region size or increasing the minimum voltage filter.


### Step 3: Review

Inspect all fetched features in a sortable table and selectively include or exclude individual items before building the network.

**Feature table columns:**

| Column | Description |
|--------|-------------|
| Include | Checkbox to include or exclude each feature from network construction |
| Source | Database that provided this feature (OSM, GEM, WRI, GridFinder) |
| Type | Feature type: substation, generator, battery, line, transformer, converter, fuel\_entry, fuel\_storage |
| Name | Feature name from the source database |
| Voltage (kV) | Operating voltage. Multi-voltage substations show both levels (e.g., "220 / 110") |
| Capacity (MW) | Rated capacity in MW (generators, batteries) or MVA (transformers, converters) |
| Fuel | Fuel type for generators (Solar, Wind, Natural Gas, etc.) |

**Filtering:**

A dropdown filter restricts the table to a single feature type (Substations, Generators, Batteries, Lines, Transformers, Converters, Fuel Entries, or Fuel Storage). Select "All Types" to show everything.

**Bulk actions:**

- **Select All** — checks all visible (non-hidden) features.
- **Deselect All** — unchecks all visible features.

A summary label shows the count of selected features out of the total.

**Tips:**

- Uncheck features that appear to be data errors (e.g., a generator with implausible capacity or location).
- Use the type filter to review one category at a time. For example, filter to "Generators" and verify fuel types before building.
- Road features (used internally for fuel routing) are hidden from the table and cannot be toggled.


### Step 4: Build Network

Create nodes via spatial clustering and map the reviewed features into the active system as buses, generators, batteries, transmission lines, transformers, converters, and fuel infrastructure.

**Target system:**

Select an existing system from the dropdown, or click **New System** to create a new one. The wizard switches the editor to the selected system before building.

**Automatic node placement (optional):**

When enabled (default), the wizard determines optimal node locations via K-means clustering before placing any elements.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Minimum nodes | 1 -- 100 | 2 | Lower bound on the number of nodes to create |
| Maximum nodes | 1 -- 200 | 20 | Upper bound on the number of nodes to create |

**Clustering criteria:**

One or more criteria can be selected. When multiple criteria are active, each runs independently and the candidate positions are merged via a consensus K-means pass (pool all candidate centroids, re-cluster to the mean of the individual k values).

| Criterion | Method | Description |
|-----------|--------|-------------|
| **Infrastructure Density** | K-means on infrastructure positions | Clusters the positions of substations, generators, batteries, transformers, converters, and fuel entries. Places nodes where physical infrastructure is concentrated. Default criterion. |
| **Demand Proxy (Building Footprints)** | Fetch + cluster building centroids | Downloads building footprints from Overture Maps, Microsoft ML Buildings, or Google Open Buildings (tried in that order). Clusters by building density to approximate demand hotspots. Requires an additional download. Falls back to Infrastructure Density if building data is unavailable. |
| **Regional Balance (Uniform Coverage)** | Grid-seeded K-means | Seeds initial cluster centroids on a regular spatial grid covering the study area, then runs K-means. Ensures even geographic coverage regardless of infrastructure density. Good for planning studies that require uniform spatial resolution. |

The optimal number of clusters is determined automatically via the elbow method (see [GM-3](#gm-3-elbow-method-knee-detection)). All clustering operates on coordinates projected to approximate kilometers using a cosine-latitude correction (see [GM-5](#gm-5-utm-approximation-for-metric-distances)).

When automatic node placement is disabled, fetched elements are assigned to the nearest existing node in the target system. At least one node must already exist.

**Network building pipeline:**

After nodes are created (or if using existing nodes), the wizard executes an eight-phase build pipeline:

| Phase | Input Features | Created Elements |
|-------|---------------|-----------------|
| 1. Substations | `substation` | Buses (one per voltage level or per substation, depending on strategy). Auto-creates transformers between voltage levels in per-voltage mode. |
| 1.5. Fuels & Technologies | All generator fuels | `GuiFuel` and `GuiTechnology` objects with sensible defaults (see [Fuel & Technology Mapping](#fuel--technology-mapping)). |
| 2. Generators | `generator` | `GuiGeneratorInstance` with mapped fuel, technology, capacity, and initial age (from commissioning year). |
| 3. Batteries | `battery` | `GuiBatteryInstance` with power rating and energy capacity (explicit or 4-hour default duration). |
| 4. Lines | `line` | `GuiTransmissionLine` with waypoint geometry. Endpoints snapped to nearest buses. Capacity from explicit tags or SIL estimate. Self-loop lines (both endpoints snap to same bus) are skipped. |
| 5. Transformers | `transformer` | `GuiTransformer` with HV and LV bus creation. |
| 6. Converters | `converter` | `GuiACDCConverter` with AC-side and DC-side bus creation. |
| 7. Fuel Entries | `fuel_entry` | Fuel entry points with mapped fuel type. |
| 8. Fuel Storage | `fuel_storage` | Fuel storage facilities with mapped fuel type. |

Each element is snapped to the nearest existing bus within the snap threshold. If no bus exists within range, a new bus is created at the element's geographic location.

**Tips:**

- Start with the default Infrastructure Density criterion. Add Demand Proxy or Regional Balance only if the default placement is unsatisfactory.
- For island systems, set minimum nodes to 1. For large interconnected grids, increase maximum nodes to 30--50.
- Review the build log in the result panel. Warnings indicate features that could not be placed (e.g., lines with no geometry).


### Step 5: Connect & Simplify

Three optional post-processing actions to refine the auto-generated network. Each action is independent and can be run in any order, though the recommended sequence is: Auto-Connect first, then Simplify, then Auto-Route Fuel (if fuel infrastructure was fetched).

#### 1. Auto-Connect

Iterative connectivity analysis with electrical validation. The algorithm runs multiple passes, each building a fresh bus adjacency graph and checking every element for a complete connection chain.

**Per-iteration phases:**

| Phase | Check | Fix |
|-------|-------|-----|
| 1--2 | Voltage mismatches between connected buses | Replace direct lines with transformer chains |
| 3--4 | Disconnected network components (BFS on bus adjacency graph) | Bridge isolated components to the main network via new transmission lines |
| 5--6 | Transformers missing connection lines to their buses | Create missing connection lines |
| 7--8 | AC/DC and frequency converters missing connection lines | Create missing connection lines |
| 9--10 | Equipment (generators, batteries, electrolyzers) without complete chains | Create LV bus, transformer, and connection lines |
| 11 | Transformer voltages inconsistent with their bus voltages | Synchronize |

The loop converges when an iteration produces zero new elements, or when the maximum iteration count is reached. After convergence, a final verification re-audits all elements and reports any remaining failures.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Max iterations | 1 -- 100 | 20 | Maximum number of check-and-fix passes. The loop stops early when no issues remain. |
| Voltage mismatch ratio | 1.1 -- 10.0 | 1.5 | Bus-to-bus lines whose endpoint voltage ratio exceeds this threshold are replaced with a transformer chain. |
| Max interconnection distance | 10 -- 10,000 km | 100 km | Maximum distance for bridging isolated components. Components farther than this from the main network are left as independent local networks (e.g., offshore platforms, remote islands). |
| LV bus voltage | 0.1 -- 33 kV | 0.48 kV | Voltage level assigned to auto-created low-voltage buses in equipment connection chains. |

#### 2. Auto-Route Fuel

Connects fuel entry points to fuel storage facilities via road-network routing.

The algorithm builds a graph from OSM road segments (motorways, trunk roads, primary and secondary roads fetched during Step 2) and uses Dijkstra's shortest-path algorithm to find routes between each fuel storage facility and its nearest fuel entry point of matching fuel type.

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| Max route distance | 10 -- 5,000 km | 200 km | Maximum road-network distance for fuel routes. Storage beyond this distance is left unconnected. |

Fuel routing requires that fuel entry points, fuel storage, and road data were all fetched in Step 2. If any of these are missing, no routes are created.

#### 3. Simplify & Aggregate

Two-phase cleanup of the auto-generated network:

**Phase 1 (always runs): Network cleanup**

- Removes empty isolated buses (degree 0 — no connected lines, no equipment).
- Removes self-loop lines (both endpoints on the same bus).
- Removes duplicate lines between the same bus pair (merged into one with summed capacity).

**Phase 2 (optional): Infrastructure aggregation**

Merges similar generators and batteries (same fuel and technology) into aggregate units. Four scope levels:

| Scope | Description |
|-------|-------------|
| **Clean Up Only** | Phase 1 only — no aggregation |
| **Aggregate by Bus** | Merge units on the same bus |
| **Aggregate by Circuit** | Merge units across buses in the same connected component |
| **Aggregate by Node** | Merge units across all buses within a node |

The wizard first runs an analysis pass that displays merge groups in a tree view (group description, unit count, total MW, and reduction count). Select individual groups via checkboxes before applying.

**Tips:**

- Run Auto-Connect first to establish full connectivity, then Simplify to clean artifacts created during auto-connection.
- Use "Aggregate by Bus" (default) for a moderate reduction. Use "Aggregate by Node" for maximum simplification.
- After applying aggregations, click Analyze again to check for further merge opportunities (new groups may emerge after prior merges).


---


## Data Sources


### GridFeature Schema

All fetchers normalize raw source data into a unified `GridFeature` dataclass before any processing:

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Origin database: `"osm"`, `"wri"`, `"gem"`, or `"gridfinder"` |
| `feature_type` | str | Element category: `"substation"`, `"generator"`, `"line"`, `"transformer"`, `"battery"`, `"converter"`, `"fuel_entry"`, `"fuel_storage"`, `"road"` |
| `name` | str | Feature name from the source database |
| `latitude` | float | WGS-84 latitude (decimal degrees) |
| `longitude` | float | WGS-84 longitude (decimal degrees) |
| `voltage_kv` | float | Primary voltage in kV (0 = unknown) |
| `voltage_kv_secondary` | float | Secondary voltage for multi-voltage substations (0 = single voltage) |
| `capacity_mw` | float | Rated capacity in MW or MVA (0 = unknown) |
| `frequency_hz` | float | System frequency in Hz (default 50) |
| `current_type` | str | `"AC"`, `"DC"`, or `"AC_DC"` |
| `fuel` | str | Fuel type for generators (e.g., `"Solar"`, `"Natural Gas"`, `"Diesel"`) |
| `gen_type` | str | `"Renewable"`, `"Non-renewable"`, or `"Storage"` |
| `energy_mwh` | float | Energy capacity for batteries in MWh (0 = unknown, builder defaults to 4h duration) |
| `line_coords` | list | Line geometry as `[(lat, lng), ...]` waypoints |
| `num_circuits` | int | Number of parallel circuits (lines only, default 1) |
| `operator` | str | Plant owner or grid operator |
| `commissioning_year` | int | Year commissioned (0 = unknown) |
| `technology` | str | Technology sub-type (e.g., `"CCGT"`, `"Onshore"`, `"Offshore"`) |
| `raw_tags` | dict | Raw source tags for debugging |
| `osm_id` | str | OpenStreetMap element ID (e.g., `"node/123456"`) |
| `include` | bool | User toggle from the Review step (default `True`) |


### Fuel & Technology Mapping

The builder automatically creates fuel and technology objects for each unique fuel type found in the imported generators. A two-level alias system normalizes variant names to canonical keys:

**Fuel alias table (selected entries):**

| Input variants | Canonical key | Created fuel |
|---------------|---------------|-------------|
| solar, solarpv, pv, photovoltaic, sun | `sun` | Sun |
| wind, eolica, eolico | `wind` | Wind |
| hydro, hydroelectric, hydropower, water | `water` | Water |
| naturalgas, gas, ng, gasnatural | `naturalgas` | Natural Gas |
| oil, fueloil, hfo, diesel, gasoil | `diesel` | Diesel |
| coal, carbon, petcoke | `coal` | Coal |
| nuclear, uranium | `nuclear` | Nuclear |
| biomass, biomasa | `biomass` | Biomass |
| biogas | `biogas` | Biogas |
| waste, residuos | `waste` | Waste |
| geothermal, geotermica | `geothermal` | Geothermal |

**Default technology parameters:**

| Canonical Key | Technology Name | Category | Efficiency (rated) | Efficiency (min) | Lifetime (years) |
|--------------|----------------|----------|-------------------|------------------|------------------|
| `sun` | Solar PV | Renewable | 1.00 | 1.00 | 25 |
| `wind` | Wind Turbine | Renewable | 1.00 | 1.00 | 25 |
| `water` | Hydroelectric | Renewable | 0.90 | 0.85 | 50 |
| `geothermal` | Geothermal | Renewable | 0.90 | 0.85 | 30 |
| `naturalgas` | Gas Turbine | Non-renewable | 0.45 | 0.30 | 30 |
| `coal` | Coal Plant | Non-renewable | 0.38 | 0.28 | 40 |
| `diesel` | Diesel Generator | Non-renewable | 0.40 | 0.25 | 25 |
| `nuclear` | Nuclear Plant | Non-renewable | 0.33 | 0.33 | 60 |
| `biomass` | Biomass Plant | Non-renewable | 0.30 | 0.20 | 25 |
| `biogas` | Biogas Generator | Non-renewable | 0.35 | 0.25 | 20 |
| `waste` | Waste-to-Energy | Non-renewable | 0.25 | 0.18 | 25 |

**Default fuel parameters:**

| Canonical Key | Unit | Emission Factor (tCO2/unit) | Energy Content (MWh/unit) | Base Price ($/unit) |
|--------------|------|----------------------------|--------------------------|-------------------|
| `naturalgas` | MMBTU | 0.202 | 0.293 | 4.0 |
| `coal` | kTon | 0.341 | 8.14 | 60.0 |
| `diesel` | kTon | 0.267 | 11.63 | 600.0 |
| `nuclear` | kgU | 0.0 | 45,000.0 | 0.006 |
| `biomass` | kTon | 0.0 | 4.5 | 40.0 |
| `biogas` | Nm3 | 0.0 | 0.006 | 0.3 |
| `waste` | kTon | 0.33 | 3.0 | -20.0 |

Renewable fuels (sun, wind, water, geothermal) have no unit, no emission factor, and zero price — they represent always-available resources.

If a fuel or technology already exists in the system with a matching canonical key, the existing object is reused rather than creating a duplicate.


### Deduplication Algorithm

When multiple sources report the same physical asset, the wizard merges duplicates to avoid double-counting.

**Generator deduplication:**

1. Sort generators by source priority: OSM (best geolocation) > GEM (newest data) > WRI (oldest).
2. For each pair of generators within the proximity threshold (default 1.0 km, computed via Haversine distance — see [GM-1](#gm-1-haversine-distance)):
   - If the capacity difference is within the tolerance (default 20%): mark the lower-priority record as a duplicate.
   - Merge metadata: fill missing fields (name, fuel, operator, commissioning year, technology) from the duplicate into the primary record.
3. The primary record is kept; the duplicate is discarded.

**Line deduplication:**

1. OSM lines are always kept.
2. GridFinder lines are checked against OSM lines: if both endpoints of a GridFinder line are within the proximity threshold of an OSM line's endpoints (in either direction), the GridFinder line is discarded.

**Other element types** (substations, transformers, converters, storage) are not deduplicated because they are sourced exclusively from OSM.


### Line Capacity Estimation

When a transmission line has no explicit capacity tag in the source data, the builder estimates capacity from the voltage level using typical Surge Impedance Loading (SIL) values:

| Voltage (kV) | Estimated Capacity (MW per circuit) |
|--------------|-----------------------------------|
| >= 500 | 2,000 |
| >= 345 | 1,000 |
| >= 220 | 500 |
| >= 110 | 200 |
| >= 33 | 50 |
| < 33 | 10 |

The effective capacity is multiplied by the number of circuits (from the `circuits` or `cables` tag, default 1).


---


## Mathematical Formulations


### GM-1: Haversine Distance

The great-circle distance between two points on the Earth's surface, used for proximity calculations throughout the wizard (snapping, deduplication, interconnection):

\[
d = 2R \arctan2\!\left(\sqrt{a},\; \sqrt{1-a}\right) \tag{GM-1}
\]

where

\[
a = \sin^2\!\left(\frac{\Delta\varphi}{2}\right) + \cos\varphi_1 \cos\varphi_2 \sin^2\!\left(\frac{\Delta\lambda}{2}\right)
\]

| Symbol | Description |
|--------|-------------|
| \(R\) | Earth's mean radius (6,371 km) |
| \(\varphi_1, \varphi_2\) | Latitudes of the two points (radians) |
| \(\lambda_1, \lambda_2\) | Longitudes of the two points (radians) |
| \(\Delta\varphi\) | \(\varphi_2 - \varphi_1\) |
| \(\Delta\lambda\) | \(\lambda_2 - \lambda_1\) |

The Haversine formula assumes a spherical Earth. For the distance scales in this wizard (typically 0.1 -- 100 km), the error relative to an ellipsoidal calculation is negligible (< 0.3%).


### GM-2: K-Means Clustering Objective

Node placement minimizes the within-cluster sum of squared distances:

\[
J = \sum_{c=1}^{k} \sum_{\mathbf{x}_i \in C_c} \|\mathbf{x}_i - \boldsymbol{\mu}_c\|^2 \tag{GM-2}
\]

| Symbol | Description |
|--------|-------------|
| \(k\) | Number of clusters (nodes to create) |
| \(C_c\) | Set of points assigned to cluster \(c\) |
| \(\mathbf{x}_i\) | Position of infrastructure feature \(i\) (in projected km) |
| \(\boldsymbol{\mu}_c\) | Centroid of cluster \(c\) |

The algorithm uses k-means++ initialization (10 restarts, random state 42) for the Infrastructure Density and Demand Proxy criteria. The Regional Balance criterion uses a regular grid as the initial centroid seed instead.


### GM-3: Elbow Method (Knee Detection)

The optimal number of clusters \(k^*\) is determined by finding the "knee" in the inertia curve — the point of diminishing returns where adding more clusters yields minimal improvement:

\[
k^* = \arg\max_{k \in [k_{\min},\, k_{\max}]} \; d_\perp(k) \tag{GM-3}
\]

where \(d_\perp(k)\) is the perpendicular distance from the point \((k,\, J(k))\) to the line connecting \((k_{\min},\, J(k_{\min}))\) and \((k_{\max},\, J(k_{\max}))\):

\[
d_\perp(k) = \frac{|\hat{x}_k \cdot \Delta \hat{y} - \hat{y}_k \cdot \Delta \hat{x}|}{\sqrt{\Delta\hat{x}^2 + \Delta\hat{y}^2}}
\]

Both axes are normalized to \([0, 1]\) before computing distances so that the number of clusters and the inertia value contribute equally:

\[
\hat{x}_k = \frac{k - k_{\min}}{k_{\max} - k_{\min}}, \qquad \hat{y}_k = \frac{J(k) - J(k_{\max})}{J(k_{\min}) - J(k_{\max})}
\]

This method avoids the low-\(k\) bias of the silhouette score and provides a robust, parameter-free selection of the cluster count.


### GM-4: Shoelace Polygon Area

The approximate area of the user-drawn polygon, used for display purposes in Step 1:

\[
A = \frac{1}{2} \left|\sum_{i=0}^{n-1} \left(x_i y_{i+1} - x_{i+1} y_i\right)\right| \tag{GM-4}
\]

where indices are taken modulo \(n\), and the coordinates are projected to approximate kilometers:

\[
x_i = \varphi_i \times 111.32 \;\text{km}, \qquad y_i = \lambda_i \times 111.32 \times \cos\bar{\varphi} \;\text{km}
\]

| Symbol | Description |
|--------|-------------|
| \(\varphi_i, \lambda_i\) | Latitude and longitude of vertex \(i\) (decimal degrees) |
| \(\bar{\varphi}\) | Mean latitude of all vertices |
| \(n\) | Number of polygon vertices |

The factor 111.32 km/degree converts degrees to approximate distances. The cosine correction accounts for the convergence of meridians at higher latitudes.


### GM-5: UTM Approximation for Metric Distances

All clustering computations project geographic coordinates to approximate Cartesian kilometers before running K-means, so that distances in both axes are comparable:

\[
x_{\text{km}} = \varphi \times 111.32, \qquad y_{\text{km}} = \lambda \times 111.32 \times \cos\left(\frac{\pi}{180}\bar{\varphi}\right) \tag{GM-5}
\]

| Symbol | Description |
|--------|-------------|
| \(\varphi\) | Latitude (decimal degrees) |
| \(\lambda\) | Longitude (decimal degrees) |
| \(\bar{\varphi}\) | Mean latitude of all points in the dataset |

This is equivalent to a simplified UTM projection centered at the mean latitude. For study areas spanning less than 5 degrees of latitude (roughly 550 km), the approximation error is below 0.5%.

After clustering, centroids are back-projected to geographic coordinates by dividing by the same scale factors.


---


## Scripting

The grid mapping modules can be used independently for batch processing and automated workflows:

```python
from esfex.visualization.workflows.grid_mapping_fetchers import (
    GridFeature,
    OSMGridFetcher,
    GEMGridFetcher,
    WRIGridFetcher,
    deduplicate_features,
    filter_features_by_polygon,
)
from esfex.visualization.workflows.grid_mapping_clustering import (
    NodeClusteringWorker,
    _determine_optimal_k,
    _extract_infrastructure_points,
    _project_to_km,
    _run_kmeans,
)
from esfex.visualization.workflows.grid_mapping_builder import (
    build_grid_from_features,
)

# Define study area (south, west, north, east)
bounds = (21.4, -83.0, 23.3, -79.5)  # Cuba

# Polygon vertices [(lat, lng), ...]
polygon = [
    (21.5, -83.0), (23.2, -83.0),
    (23.3, -79.5), (21.4, -79.5),
]

# Fetch features (outside Qt, call _fetch() directly)
osm = OSMGridFetcher(bounds, min_voltage_kv=110, min_capacity_mw=1.0)
features = osm._fetch()

# Filter and deduplicate
features = filter_features_by_polygon(features, polygon)
features = deduplicate_features(features, proximity_km=1.0)

# Inspect results
for f in features:
    print(f"{f.feature_type:15s} {f.name:40s} "
          f"{f.capacity_mw:8.1f} MW  {f.fuel}")

# Determine optimal node count from infrastructure positions
import numpy as np
pts = _extract_infrastructure_points(features)
pts_km = _project_to_km(pts)
k = _determine_optimal_k(pts_km, min_k=2, max_k=20)
print(f"Optimal k = {k}")

# Run K-means
labels, centroids = _run_kmeans(pts_km, k)
print(f"Cluster centroids (km): {centroids}")
```

The fetcher classes inherit from `QThread` and emit Qt signals for GUI progress. When used in scripts without a Qt event loop, call the `_fetch()` method directly instead of `start()`.

For building the network, instantiate a `GuiModel` and `GuiSystemState` from `esfex.visualization.data.gui_model`, then call `build_grid_from_features()`:

```python
from esfex.visualization.data.gui_model import GuiModel, GuiSystemState

# Create a model with a blank system
state = GuiSystemState(name="scripted_grid")
model = GuiModel(state)

# Add nodes (from clustering results)
for i, (cy, cx) in enumerate(centroids):
    import math
    mid_lat = float(np.mean(pts[:, 0]))
    cos_lat = math.cos(math.radians(mid_lat))
    lat = cy / 111.32
    lng = cx / (111.32 * cos_lat)
    idx = model.add_node(f"Node {i}")
    model.update_node(idx, centroid_lat=lat, centroid_lng=lng)

# Build network
result = build_grid_from_features(
    model=model,
    features=features,
    bus_strategy="per_voltage",
    snap_threshold_km=5.0,
)
print(result.summary())
```
