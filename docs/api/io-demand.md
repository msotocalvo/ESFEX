# Demand I/O
Module: `esfex.io.demand`

## Functions

### load_demand_data

```python
def load_demand_data(
    file_path: Union[str, Path],
    date_start: str = "01/01/2025 00:00",
    year_to_load: Optional[int] = None
) -> Tuple[np.ndarray, int, int, List[int], List[datetime]]
```

Load demand data from an Excel (.xlsx, .xls) or CSV file.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` or `Path` | required | Path to the demand data file. |
| `date_start` | `str` | `"01/01/2025 00:00"` | Simulation start date and time in `"DD/MM/YYYY HH:MM"` format. Used to compute year boundaries and time indices. |
| `year_to_load` | `int` or `None` | `None` | If specified, loads only data for this calendar year. If `None`, loads all data in the file. |

**Returns:** A 5-tuple:

| Index | Type | Description |
|-------|------|-------------|
| 0 | `np.ndarray` | Demand array with shape `(hours, num_nodes)` in MW. |
| 1 | `int` | Total number of hours loaded. |
| 2 | `int` | Number of nodes (columns in the file). |
| 3 | `list[int]` | List of years present in the data (e.g., `[2025, 2026, ..., 2050]`). |
| 4 | `list[datetime]` | Time index -- one `datetime` object per hour. |

**Raises:**
- `FileNotFoundError` if the demand file does not exist.

**Behavior:**

- When `year_to_load=None`: reads the entire file. For CSV files, assumes no header row (pure numeric data). For Excel files, uses pandas defaults.
- When `year_to_load` is specified: calculates the row range for that year based on `date_start`, then reads only those rows. This avoids loading the full multi-year file into memory.
- Years are determined from the time span: `start_date.year` through `end_date.year`.

**Demand File Format:**

Hourly power demand values in MW. Each column represents a node. No header row for CSV files; Excel files may have headers that are auto-detected by pandas.

| Column 0 | Column 1 | Column 2 | ... |
|----------|----------|----------|-----|
| 120.5 | 85.3 | 200.1 | ... |
| 118.2 | 84.1 | 198.7 | ... |
| ... | ... | ... | ... |

For a 25-year simulation with hourly resolution, the file contains `25 * 8760 = 219,000` rows.

**Multi-Year Demand and Growth Rates:**

Demand growth is NOT applied inside `load_demand_data()`. The function loads raw data as-is. Growth rates are applied by the runner during simulation separately:

```python
# In runner._load_demand():
demand_array, hours, num_nodes, years, time_index = load_demand_data(
    config.demand_file, date_start=config.date_start
)
# Growth is applied separately per year during master problem setup
```

The master problem applies growth via the `demand_growth` parameter passed to `MasterProblemAdapter`, scaling demand per year in the Julia optimization model.

**Example:**

```python
from esfex.io.demand import load_demand_data

# Load all years
demand, hours, nodes, years, time_idx = load_demand_data(
    "data/demand.xlsx",
    date_start="01/01/2025 00:00"
)
print(f"Loaded {hours} hours, {nodes} nodes, years: {years}")

# Load only 2030
demand_2030, h, n, _, t = load_demand_data(
    "data/demand.xlsx",
    date_start="01/01/2025 00:00",
    year_to_load=2030
)
```

### create_sectoral_demand

```python
def create_sectoral_demand(
    base_demand: np.ndarray,
    sector_distribution: Dict[int, Dict[str, float]],
    sectors_list: Optional[List[str]] = None
) -> Dict[str, np.ndarray]
```

Distribute total demand into sector-specific demand arrays.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `base_demand` | `np.ndarray` | Total demand array with shape `(hours, num_nodes)`. |
| `sector_distribution` | `dict` | Per-node sectoral fractions. Format: `{node_idx: {sector_name: proportion}}`. |
| `sectors_list` | `list[str]` or `None` | Ordered list of sectors to include. If `None`, automatically detects all sector names from `sector_distribution`. |

**Returns:** Dictionary mapping sector name to demand array, each with shape `(hours, num_nodes)`.

**Normalization:**

Proportions are normalized per node to ensure `sum(sectoral_demand) == base_demand`. If proportions for a node sum to a value significantly different from 1.0, a warning is logged. If proportions sum to 0 for a node, demand is distributed equally across all sectors.

**Example:**

```python
from esfex.io.demand import create_sectoral_demand

sector_dist = {
    0: {"residential": 0.4, "commercial": 0.35, "industrial": 0.25},
    1: {"residential": 0.5, "commercial": 0.3, "industrial": 0.2},
}

sectoral = create_sectoral_demand(demand_array, sector_dist)
# sectoral["residential"].shape == (hours, num_nodes)
# sectoral["commercial"].shape == (hours, num_nodes)
# sectoral["industrial"].shape == (hours, num_nodes)
```

### load_availability_profile

```python
def load_availability_profile(
    file_path: Union[str, Path],
    temporal_resolution_hours: int = 1,
    num_nodes: Optional[int] = None,
) -> np.ndarray
```

Load a generator availability profile from an Excel or CSV file.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` or `Path` | required | Path to the availability data file. |
| `temporal_resolution_hours` | `int` | `1` | Target temporal resolution. If > 1, the hourly data is aggregated using MEAN (appropriate for capacity factors). |
| `num_nodes` | `int` or `None` | `None` | Expected number of nodes. If provided, pads with 1.0 or truncates to match. |

**Returns:** Array of shape `(hours, nodes)` with values clipped to [0, 1].

**Behavior:**

- If the file does not exist, logs a warning and returns an array of ones (full availability).
- If the file has fewer columns than `num_nodes`, pads with 1.0 (full availability for missing nodes).
- If the file has more columns than `num_nodes`, truncates to the expected number.
- Values are clipped to [0, 1] after loading.
- If `temporal_resolution_hours > 1`, applies `aggregate_to_resolution()` using MEAN aggregation.

**Availability File Format:**

Same structure as demand files: one column per node, one row per hour, values between 0 and 1.

| Node 0 | Node 1 | Node 2 |
|--------|--------|--------|
| 0.0 | 0.0 | 0.0 |
| 0.0 | 0.0 | 0.0 |
| 0.15 | 0.12 | 0.18 |
| 0.45 | 0.40 | 0.50 |
| 0.82 | 0.75 | 0.88 |
| ... | ... | ... |

### extract_year_profile

```python
def extract_year_profile(
    full_profile: Union[pd.DataFrame, np.ndarray],
    time_index: List[datetime],
    hours: int,
) -> np.ndarray
```

Extract profile data for a specific year from a full multi-year profile.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `full_profile` | `DataFrame` or `ndarray` | Full multi-year profile data. |
| `time_index` | `list[datetime]` | Time index for the target year. |
| `hours` | `int` | Number of hours to extract. |

**Returns:** Profile array for the year with shape `(hours, ...)`.

For DataFrames, filters by `index.year == time_index[0].year`. For NumPy arrays, takes the first `hours` rows.

---

## DemandDataManager

Converts large Excel/CSV demand files to HDF5 format for fast year-by-year access.

```python
class DemandDataManager:
    def __init__(
        self,
        excel_path: Union[str, Path],
        date_start: str = "01/01/2025 00:00",
        time_step: int = 1,
    )
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `excel_path` | `str` or `Path` | required | Path to the Excel or CSV demand file. |
| `date_start` | `str` | `"01/01/2025 00:00"` | Simulation start date. |
| `time_step` | `int` | `1` | Time step in hours. |

### prepare_hdf5_storage

```python
def prepare_hdf5_storage(self) -> Path
```

Convert the Excel/CSV file to an HDF5 file with year-indexed access.

**Returns:** Path to the created HDF5 temporary file.

The HDF5 file contains:
- `demand` dataset: Full demand array with gzip compression.
- `year_index/` group: Per-year `[start_idx, end_idx]` arrays for fast slicing.
- Metadata attributes: `total_hours`, `num_nodes`, `start_date`, `end_date`, `start_year`, `end_year`, `time_step`.

### load_year_data

```python
def load_year_data(self, year: int) -> Tuple[np.ndarray, int, int, List[datetime]]
```

Load data for a specific year from the prepared HDF5 file.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `year` | `int` | Year to load. |

**Returns:** A 4-tuple of `(demand_array, hours_in_year, num_nodes, time_index)`.

**Raises:** `ValueError` if HDF5 is not prepared or the year is not found.

### cleanup

```python
def cleanup(self) -> None
```

Remove the temporary HDF5 file.

**Example:**

```python
from esfex.io.demand import DemandDataManager

mgr = DemandDataManager("data/demand_25years.xlsx", date_start="01/01/2025 00:00")
hdf5_path = mgr.prepare_hdf5_storage()

for year in range(2025, 2050):
    demand, hours, nodes, time_idx = mgr.load_year_data(year)
    print(f"Year {year}: {hours} hours, peak={demand.max():.1f} MW")

mgr.cleanup()
```
