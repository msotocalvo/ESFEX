# Demand Distribution

Spatial distribution of node-level electric demand among busbars using building footprint data and clustering analysis. Access via **Workflows > Demand Distribution**.

The wizard takes a node's total demand (MW) and distributes it proportionally across the node's buses based on the geographic density and type of buildings surrounding each bus. It downloads building footprints from open data sources, classifies them by type (residential, commercial, industrial), groups them into spatial clusters, and maps those clusters to buses. Only the `demand_fraction` attribute on each bus is updated -- no elements are created or removed.

The wizard supports **multiple nodes across multiple systems** in a single run and is organized as a single-phase, five-step workflow:

1. **Target Selection** -- Choose which nodes to process.
2. **Domain & Building Fetch** -- Define the geographic area and download building footprints.
3. **Building Classification** -- Assign demand weights based on building type and size.
4. **Spatial Clustering** -- Group buildings into demand zones.
5. **Review & Apply** -- Map clusters to buses and commit the new fractions.

All computations use the `esfex.visualization.workflows.demand_analysis` engine, which can also be used independently for scripting -- see [Scripting](#scripting).


---


### Step 1: Target Selection

Select the nodes whose demand should be distributed among their buses.

**Hierarchical tree:**

A checkbox tree displays systems as top-level items and their nodes as children. Check the nodes you want to process.

| Column | Description |
|--------|-------------|
| Node | Node name and index |
| Peak Demand | Peak demand (MW) from the node's demand profile |
| Bus Count | Number of buses attached to this node |

Nodes with fewer than 2 buses are disabled -- distribution requires at least 2 destinations. When a system-level checkbox is toggled, all eligible child nodes follow.

**Summary panel:**

Below the tree, a summary lists each selected node with its peak demand and bus count, giving a quick confirmation of the processing scope before proceeding.

**Tips:**

- Use **Select All** to check every eligible node, or **Clear All** to start over.
- Selecting nodes across multiple systems is fully supported. Each node's demand fractions are computed and applied independently.
- Nodes with only one bus already receive 100% of the node's demand and cannot benefit from redistribution.


### Step 2: Domain & Building Fetch

Define the geographic area that covers the selected nodes and download building footprint data.

**Domain definition:**

Two methods are available:

- **Draw on map** -- Click "Draw Rectangle", then click and drag on the map. The wizard minimizes during drawing and restores when the rectangle is complete. Coordinates populate automatically.
- **Manual entry** -- Enter bounding box coordinates directly: South latitude, North latitude, West longitude, East longitude. Click "Apply Coords" to register the domain.

The approximate area of the domain (in km^2) is displayed using the projection:

\[
A \approx (\Delta\phi \times 111.32) \times (\Delta\lambda \times 111.32 \times \cos\phi_{\text{mid}}) \tag{DD-1}
\]

where \(\Delta\phi\) and \(\Delta\lambda\) are the latitude and longitude spans in degrees, and \(\phi_{\text{mid}}\) is the mid-latitude. This is a flat-Earth approximation suitable for domains up to a few hundred kilometers.

**Building data sources:**

| Source | Description |
|--------|-------------|
| **Overture Maps** | Open dataset with global building footprints. Best coverage in urban areas. |
| **Microsoft ML Buildings** | Machine learning-derived footprints from satellite imagery. 1.2 billion buildings worldwide. |
| **Google Open Buildings** | Google's ML-derived dataset. Best coverage in Africa, South/Southeast Asia, and Latin America. |

Click **Fetch** to download building footprints within the bounding box. A progress bar and status label show download progress. When complete, the map previews the downloaded buildings as small polygons.

**Output:** A GeoDataFrame with columns `geometry` (building polygon) and `footprint_area_m2` (footprint area in square meters).

**Tips:**

- Choose a domain that covers all buses of the selected nodes with some margin. Buildings outside the domain are not counted.
- The best data source depends on the region. Try Overture Maps first for global coverage; switch to Google Open Buildings for developing regions.
- Large domains with dense urban areas may return hundreds of thousands of buildings. This is expected and the clustering step handles it efficiently.


### Step 3: Building Classification

Assign relative demand weights to each building based on its footprint area, using configurable classification rules.

**Classification rules table:**

Each row defines a building type with its area range and demand weight density:

| Column | Description |
|--------|-------------|
| Name | Type label (e.g. "Residential", "Commercial") |
| Area Min (m^2) | Minimum footprint area for this type |
| Area Max (m^2) | Maximum footprint area for this type (use \(\infty\) for no upper bound) |
| Weight Density (/m^2) | Demand weight per square meter of footprint |
| Color | Display color for map visualization |

**Default rules:**

| Type | Area Range (m^2) | Weight Density (/m^2) | Color | Rationale |
|------|------------------|-----------------------|-------|-----------|
| Residential | 30 -- 300 | 0.05 | Blue | Houses and apartments. Low per-area demand. |
| Commercial | 300 -- 2,000 | 0.08 | Orange | Offices, shops, hotels. Moderate demand intensity. |
| Industrial | 2,000+ | 0.12 | Red | Factories, warehouses. High demand intensity. |

**Matching logic:** Rules are evaluated in table order. The first rule whose area range contains the building's footprint area wins. Buildings that match no rule receive the **fallback weight density** (default 0.03 /m^2).

The demand weight for each building is computed as:

\[
w_b = A_b \times d_r \tag{DD-2}
\]

where \(A_b\) is the building's footprint area (m^2) and \(d_r\) is the weight density for the matching rule \(r\). For unmatched buildings, \(d_r\) equals the fallback weight density.

**Optional floor-based classification:** If the building data includes a `num_floors` column, rules can additionally filter by floor count (`min_floors`, `max_floors`). When a building has floor data and a rule specifies a floor range, both area and floor criteria must be satisfied.

Click **Classify** to apply the rules. A preview shows per-type statistics: building count, total area, and total weight.

**Tips:**

- Weights are **relative**, not absolute. They proportion each node's actual demand across buses. Doubling all weight densities has no effect on the final fractions.
- Adjust area thresholds to match local building stock. In dense urban areas, apartments may exceed 300 m^2 but should still be classified as residential.
- Add custom types (e.g. "Government", "Agricultural") with their own area ranges and weights for more granular control.
- The fallback weight density (default 0.03 /m^2) ensures that small or unusual buildings are not ignored entirely.


### Step 4: Spatial Clustering

Group the classified buildings into spatial clusters that will be mapped to buses.

**Algorithm selection:**

| Algorithm | Parameters | Description |
|-----------|-----------|-------------|
| **DBSCAN** | `eps` (50--5,000 m, default 500), `min_samples` (1--100, default 5) | Density-based clustering. Automatically determines the number of clusters based on building density. Noise points (buildings too far from any cluster) are assigned to the nearest cluster. Good for irregular building distributions. |
| **K-Means** | `n_clusters` (2--50, default = max bus count) | Partition-based clustering. Produces exactly the specified number of clusters by minimizing within-cluster distance. Best when building density is relatively uniform. Uses `n_init=10` for stability. |
| **Agglomerative** | `n_clusters` (2--50, default = max bus count), `linkage` (ward / complete / average) | Hierarchical clustering. Merges buildings bottom-up using the selected linkage criterion. Ward linkage minimizes variance; complete linkage uses maximum pairwise distance; average linkage uses mean pairwise distance. |

**Workflow:**

1. **UTM projection** -- Building centroids are projected from geographic coordinates (WGS84) to the local UTM zone so that distances are measured in meters.
2. **Clustering** -- The selected algorithm assigns each building to a cluster.
3. **Centroid computation** -- Each cluster's centroid is computed in UTM and back-projected to WGS84 for map display.
4. **Demand fraction computation** -- The demand fraction for each cluster is the ratio of its total demand weight to the global total:

\[
f_c = \frac{\displaystyle\sum_{b \in c} w_b}{\displaystyle\sum_{b} w_b} \tag{DD-3}
\]

5. **Normalization** -- Fractions are normalized so they sum exactly to 1.0:

\[
\sum_{c} f_c = 1 \tag{DD-4}
\]

Click **Run Clustering** to execute. A progress bar tracks the computation (projection, clustering, statistics). When complete, the results table shows per-cluster statistics:

| Column | Description |
|--------|-------------|
| Cluster ID | Numeric identifier |
| Building Count | Number of buildings in the cluster |
| Total Weight | Sum of demand weights |
| Demand Fraction | Proportion of total demand assigned to this cluster |
| Color | Display color for map visualization |

The map displays cluster centroids as colored markers for visual verification.

**Tips:**

- The default cluster count is set to the maximum bus count across all selected nodes. Adjust it if you want finer or coarser groupings.
- For DBSCAN, increase `eps` (the neighborhood radius) if the algorithm produces too many small clusters, or decrease it to split large clusters.
- K-Means works best when you know in advance how many demand zones you want. It always produces exactly `n_clusters` clusters.
- Agglomerative clustering with ward linkage tends to produce compact, evenly sized clusters, similar to K-Means but based on hierarchical merging.


### Step 5: Review & Apply

Review the mapping between spatial clusters and buses, then commit the new demand fractions to the model.

**Assignment logic:**

For each target node, the wizard maps clusters to buses as follows:

1. Clusters are sorted by demand fraction in **descending** order (heaviest cluster first).
2. Buses are sorted by bus ID in **ascending** order.
3. Clusters are mapped one-to-one to buses in order.
4. **If there are more clusters than buses:** All remaining clusters are aggregated into the last bus. Their demand fractions are summed.
5. **If there are fewer clusters than buses:** Extra buses receive a fraction of 0.
6. **Normalization:** All fractions for a node are normalized to sum exactly to 1.0.

**Assignment table:**

| Column | Description |
|--------|-------------|
| System | System name |
| Node | Node name |
| Bus ID | Bus identifier |
| Bus Name | Bus display name |
| Cluster | Mapped cluster (or "--" if no cluster is assigned) |
| Old Fraction | Current demand fraction before the wizard |
| New Fraction | Proposed demand fraction from clustering |

Below the table, a per-node sum verification line confirms that \(\Sigma = 1.0000\) for each node.

**Actions:**

| Button | Description |
|--------|-------------|
| **Apply** | Write the new demand fractions to the model. For the current system, fractions are updated through the model API (triggering proper UI refresh). For other systems, fractions are updated directly in the system state. |
| **Export CSV** | Save the full assignment table to a CSV file. Columns: `system_name`, `node_name`, `bus_id`, `bus_name`, `old_fraction`, `new_fraction`. Available after applying. |

**Tips:**

- Review the old vs. new fractions before applying. Large changes may indicate that the classification rules or clustering parameters need adjustment.
- The CSV export is useful for documentation and for comparing different clustering configurations.
- After applying, you can re-run the wizard with different parameters. The old fractions shown will reflect the most recently applied values.


---


## Mathematical Formulations


### Domain Area Approximation

\[
A \approx (\Delta\phi \times 111.32) \times (\Delta\lambda \times 111.32 \times \cos\phi_{\text{mid}}) \tag{DD-1}
\]

| Symbol | Description |
|--------|-------------|
| \(\Delta\phi\) | Latitude span (degrees) |
| \(\Delta\lambda\) | Longitude span (degrees) |
| \(\phi_{\text{mid}}\) | Mid-latitude of the bounding box |
| 111.32 | Approximate km per degree of latitude (or longitude at the equator) |

This flat-Earth approximation is accurate to within a few percent for domains smaller than a few hundred kilometers at mid-latitudes.


### Building Demand Weight

\[
w_b = A_b \times d_r \tag{DD-2}
\]

| Symbol | Description |
|--------|-------------|
| \(w_b\) | Demand weight of building \(b\) (dimensionless relative value) |
| \(A_b\) | Footprint area of building \(b\) (m^2) |
| \(d_r\) | Weight density of classification rule \(r\) that matches building \(b\) (/m^2) |

The weight is proportional to the building's footprint area, scaled by a type-dependent density that reflects the expected demand intensity. For buildings matching no rule, \(d_r\) falls back to a configurable default (0.03 /m^2).


### Cluster Demand Fraction

\[
f_c = \frac{\displaystyle\sum_{b \in c} w_b}{\displaystyle\sum_{b} w_b} \tag{DD-3}
\]

| Symbol | Description |
|--------|-------------|
| \(f_c\) | Demand fraction assigned to cluster \(c\) |
| \(w_b\) | Demand weight of building \(b\) |
| \(c\) | Set of buildings assigned to cluster \(c\) |

The fraction represents the proportion of total demand weight concentrated in the cluster. After normalization, all fractions sum to 1.0.


### Normalization Constraint

\[
\sum_{c} f_c = 1 \tag{DD-4}
\]

Ensures that the demand fractions form a valid probability distribution. Applied at two stages: once after clustering (across all clusters), and once after bus assignment (per node).


### DBSCAN Clustering

\[
N_\varepsilon(p) = \{ q \in B : \|p - q\| \leq \varepsilon \}, \quad \text{core point if } |N_\varepsilon(p)| \geq \text{min\_samples} \tag{DD-5}
\]

| Symbol | Description |
|--------|-------------|
| \(N_\varepsilon(p)\) | \(\varepsilon\)-neighborhood of point \(p\) |
| \(B\) | Set of all building centroids |
| \(\varepsilon\) | Neighborhood radius (meters, parameter `eps`) |
| \(\text{min\_samples}\) | Minimum number of points to form a core point |

Buildings in the \(\varepsilon\)-neighborhood of a core point belong to the same cluster. Buildings that are not core points and not in any core point's neighborhood are classified as noise and are reassigned to the nearest cluster via nearest-neighbor lookup.


### K-Means Objective

\[
\min \sum_{c=1}^{K} \sum_{b \in c} \| \mathbf{x}_b - \boldsymbol{\mu}_c \|^2 \tag{DD-6}
\]

| Symbol | Description |
|--------|-------------|
| \(K\) | Number of clusters (parameter `n_clusters`) |
| \(\mathbf{x}_b\) | UTM coordinates of building \(b\)'s centroid |
| \(\boldsymbol{\mu}_c\) | Centroid of cluster \(c\) |

The algorithm iteratively assigns buildings to the nearest centroid and recomputes centroids until convergence. Uses 10 random initializations (`n_init=10`) and selects the result with the lowest objective value.


---


## Scripting

All wizard computations are available as Python functions for batch processing and Jupyter notebooks:

```python
import geopandas as gpd
from esfex.visualization.workflows.demand_analysis import (
    BuildingTypeRule,
    DEFAULT_RULES,
    ClusteringWorker,
    classify_buildings,
    compute_classification_summary,
)

# 1. Load building footprints (e.g. from a GeoJSON file)
buildings = gpd.read_file("buildings.geojson")
# Must have 'footprint_area_m2' column; compute if missing:
buildings["footprint_area_m2"] = buildings.geometry.area  # if already projected

# 2. Classify buildings using default rules
classified = classify_buildings(buildings, DEFAULT_RULES, fallback_weight_per_m2=0.03)
print(compute_classification_summary(classified))

# 3. Custom rules
custom_rules = [
    BuildingTypeRule(name="Small Residential", area_min_m2=20, area_max_m2=150,
                     weight_per_m2=0.04, color="#3498db"),
    BuildingTypeRule(name="Large Residential", area_min_m2=150, area_max_m2=400,
                     weight_per_m2=0.06, color="#2ecc71"),
    BuildingTypeRule(name="Commercial", area_min_m2=400, area_max_m2=2000,
                     weight_per_m2=0.10, color="#e67e22"),
    BuildingTypeRule(name="Industrial", area_min_m2=2000, area_max_m2=float("inf"),
                     weight_per_m2=0.15, color="#e74c3c"),
]
classified_custom = classify_buildings(buildings, custom_rules, fallback_weight_per_m2=0.02)

# 4. Run clustering (synchronous usage outside the GUI)
import numpy as np
from sklearn.cluster import KMeans

gdf = classified_custom.copy()
utm_crs = gdf.estimate_utm_crs()
gdf_utm = gdf.to_crs(utm_crs)
coords = np.column_stack([gdf_utm.geometry.centroid.x, gdf_utm.geometry.centroid.y])

km = KMeans(n_clusters=5, n_init=10, random_state=42)
gdf["cluster_id"] = km.fit_predict(coords)

# 5. Compute demand fractions per cluster
total_weight = gdf["demand_weight"].sum()
fractions = gdf.groupby("cluster_id")["demand_weight"].sum() / total_weight
fractions = fractions / fractions.sum()  # normalize to exactly 1.0

for cid, frac in fractions.items():
    print(f"  Cluster {cid}: {frac:.4f} ({frac*100:.1f}%)")
```

See the [Demand Analysis API](../api/workflows-demand-analysis.md) for full parameter documentation.
