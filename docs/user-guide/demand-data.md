# Demand Data

## File Formats

### Excel (.xlsx)

Single worksheet with pure numeric data:

| | Column 0 | Column 1 | Column 2 |
|---|--------|--------|--------|
| Row 1 | 150.5 | 80.2 | 45.0 |
| Row 2 | 148.3 | 79.1 | 44.5 |
| Row 3 | 145.0 | 77.5 | 43.8 |
| ... | ... | ... | ... |
| Row 8760 | 155.0 | 82.0 | 46.0 |

### CSV (.csv)

Same structure, comma-separated, no header row:

```csv
150.5,80.2,45.0
148.3,79.1,44.5
145.0,77.5,43.8
...
155.0,82.0,46.0
```

### Format Rules

| Rule | Description |
|------|-------------|
| **Rows** | One per time step (typically 8,760 rows per year at hourly resolution) |
| **Columns** | One per node, matching the number of nodes in the system configuration |
| **Values** | Electrical demand in MW (positive real numbers) |
| **No header** | Data starts from the first row; no column headers or index column |
| **No timestamps** | Time alignment is determined by `date_start` in the configuration |

### Example: 3-Node Hourly CSV

```csv
120.5,65.3,38.2
118.0,63.1,36.9
115.2,61.0,35.5
113.0,59.8,34.2
112.5,58.5,33.8
114.0,60.0,34.5
125.0,68.0,40.0
145.0,78.5,46.2
160.0,86.0,51.0
165.0,89.0,53.0
168.0,90.5,54.2
170.0,91.0,55.0
172.0,92.0,55.5
170.5,91.5,55.0
168.0,90.0,54.0
165.0,88.5,53.0
162.0,87.0,52.0
158.0,85.0,50.5
150.0,81.0,48.0
145.0,78.0,46.0
140.0,75.5,44.5
135.0,73.0,43.0
128.0,69.0,41.0
122.0,66.0,39.0
```

---


## Multi-Year Demand

Provide all years in a single file by vertically concatenating hourly data.

### Row Counts

| Duration | Standard Year (8,760 h) | Leap Year (8,784 h) |
|----------|------------------------|---------------------|
| 1 year | 8,760 rows | 8,784 rows |
| 5 years | 43,800 rows | varies per year |
| 10 years | 87,600 rows | varies per year |
| 25 years | 219,000 rows | varies per year |

`date_start` determines row-to-year mapping:

```yaml
date_start: "01/01/2025 00:00"
```

Year boundaries are detected automatically from the start date, accounting for leap years.

### Single-Year Files with Demand Growth

If the file contains only one year but the simulation spans multiple years, the growth rate projects future demand:

```yaml
systems:
  my_system:
    demand_path: demand_2025.xlsx
    demand_growth: 0.02               # 2% annual growth
```

Year `y` demand:

```
D(y) = D_base * (1 + demand_growth)^(y - 1)
```

Example with 170 MW base peak and 2% growth:

| Year | Peak Demand (MW) |
|------|-----------------|
| 1 | 170.0 |
| 5 | 184.1 |
| 10 | 203.0 |
| 15 | 224.1 |
| 20 | 247.4 |
| 25 | 273.2 |

---


## Demand Scaling

`demand_scale` applies uniformly at load time, before growth:

```yaml
systems:
  my_system:
    demand_scale: 1.05    # 5% increase over file values
```

Useful for sensitivity analysis without modifying the original data file.


---


## Sectoral Distribution

Total demand decomposes into sectors with per-sector criticality for differentiated load shedding.

### Configuration

```yaml
electric_demand:
  residential:
    criticality: 0.7       # 0 = fully flexible, 1 = critical
    flexibility: 0.3       # Fraction of demand that can be shifted
  industrial:
    criticality: 0.9       # Higher criticality = shed last
    flexibility: 0.1
  commercial:
    criticality: 0.5
    flexibility: 0.5

sector_distribution:
  0:                        # Node 0
    residential: 0.40       # 40% residential
    industrial: 0.35        # 35% industrial
    commercial: 0.25        # 25% commercial
  1:                        # Node 1
    residential: 0.50
    industrial: 0.30
    commercial: 0.20
  2:                        # Node 2
    residential: 0.45
    industrial: 0.25
    commercial: 0.30
```

### Validation Rules

| Rule | Description |
|------|-------------|
| Fractions must sum to 1.0 per node | A tolerance of 0.01 is applied; if the sum deviates, proportions are normalized automatically |
| All sectors defined in `electric_demand` should appear in `sector_distribution` | Missing sectors receive zero allocation |
| Node indices must be valid | If a node index in `sector_distribution` does not exist, node 0 proportions are used as fallback |
| `criticality` range | Must be between 0.0 and 1.0 |
| `flexibility` range | Must be between 0.0 and 1.0 |

### How Sectoral Demand Is Used

1. **Priority-based load shedding**: High-criticality sectors (industrial) are preserved; low-criticality (commercial lighting) shed first
2. **Demand flexibility**: Flexible fractions can be time-shifted by the optimizer, acting as virtual storage
3. **Reporting**: Unserved energy broken down by sector


---


## EV Demand Integration

EV charging demand is generated separately using an S-curve growth model.

### When EV Optimization Is Enabled

The optimizer decides charging and V2G schedules. EV demand is NOT added to `total_demand` (avoiding double-counting); EV constraints are included directly in the model.

### When EV Optimization Is Disabled

EV charging demand is added to base demand:

```
total_demand = base_demand + ev_charging_demand
```

Profile generation depends on fleet size (`ev_quantity`), battery specs (`ev_categories`), driving patterns (`base_patterns`), and an S-curve growth model. See [EV Model](../formulation/ev-model.md) for details.


---


## Python API

### Loading Demand Data

```python
from esfex.io.demand import load_demand_data

# Load all years from the file
demand, hours, num_nodes, years, time_index = load_demand_data(
    "demand.xlsx",
    date_start="01/01/2025 00:00"
)
print(f"Shape: {demand.shape}")          # (219000, 3) for 25 years, 3 nodes
print(f"Years: {years}")                 # [2025, 2026, ..., 2049]
print(f"Peak demand: {demand.max():.1f} MW")

# Load only a specific year (memory efficient)
demand_2030, hours, num_nodes, years, time_index = load_demand_data(
    "demand.xlsx",
    date_start="01/01/2025 00:00",
    year_to_load=2030
)
print(f"Year 2030 shape: {demand_2030.shape}")  # (8760, 3)
```

### Creating Sectoral Demand

```python
from esfex.io.demand import create_sectoral_demand

sector_distribution = {
    0: {"residential": 0.40, "industrial": 0.35, "commercial": 0.25},
    1: {"residential": 0.50, "industrial": 0.30, "commercial": 0.20},
}

sectoral = create_sectoral_demand(demand, sector_distribution)
# sectoral = {"residential": array(8760, 2), "industrial": array(8760, 2), ...}

for sector, data in sectoral.items():
    print(f"{sector}: mean={data.mean():.1f} MW, peak={data.max():.1f} MW")
```

### Using DemandDataManager for Large Files

Converts Excel to HDF5 for faster year-by-year random access:

```python
from esfex.io.demand import DemandDataManager

mgr = DemandDataManager("demand.xlsx", date_start="01/01/2025 00:00")
mgr.prepare_hdf5_storage()              # One-time conversion

# Fast year-by-year loading
for year in range(2025, 2050):
    demand, hours, num_nodes, time_idx = mgr.load_year_data(year)
    print(f"{year}: {hours} hours, peak={demand.max():.1f} MW")

mgr.cleanup()                           # Remove temporary HDF5 file
```

---


## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `FileNotFoundError: Demand file not found` | The `demand_path` does not point to a valid file | Check the file path; it is relative to the YAML config directory |
| `Shape mismatch: expected N columns` | Number of columns in demand file does not match `num_nodes` | Ensure the demand file has exactly one column per node |
| `Empty demand file` | The file exists but contains no data | Check that the file is not empty and is in the correct format |
| `Sector proportions sum to X` | `sector_distribution` fractions do not sum to 1.0 | Adjust fractions; a warning is logged and values are normalized |

### Best Practices

1. **Consistency**: Ensure demand file resolution matches `temporal.resolution_hours`. If your demand file is hourly and `resolution_hours` is 1, no aggregation is needed. If using sub-hourly demand (e.g., 15-minute), set `resolution_hours` accordingly.

2. **Units**: All demand values must be in MW (megawatts), not kW or GW.

3. **Missing data**: Do not leave cells empty or use placeholder values like `-1`. All values must be non-negative real numbers.

4. **File size**: For 25-year simulations with many nodes, Excel files can become slow to load. Consider using CSV format, which loads significantly faster for large files.
