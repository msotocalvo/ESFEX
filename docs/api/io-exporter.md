# Exporter I/O
Module: `esfex.io.exporter`

## ResultsExporter

Read-only exporter that converts existing HDF5 results files to other formats.

```python
class ResultsExporter:
    def __init__(self, results_path: Union[str, Path]) -> None
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `results_path` | `str` or `Path` | Path to an existing HDF5 results file. |

**Raises:** `FileNotFoundError` if the results file does not exist.

### Methods

#### to_csv

```python
def to_csv(self, output_dir: Union[str, Path]) -> None
```

Export all datasets from the HDF5 file as individual CSV files. Preserves group hierarchy as directory structure.

**Output structure:**

```
output_dir/
    summary/
        total_cost.csv
        gen_investment_power.csv
        ...
    year_2025_threshold_0.5/
        generation/
            Solar_PV.csv
            Diesel.csv
            ...
        battery_charge/
            Li_Ion.csv
            ...
        battery_discharge/
            Li_Ion.csv
            ...
        curtailment.csv
        electricity_prices.csv
        CO2_emissions.csv
        power_flow.csv
    year_2026_threshold_0.5/
        ...
    demand/
        ...
```

#### to_excel

```python
def to_excel(self, output_path: Union[str, Path]) -> None
```

Export results to a single Excel workbook with multiple sheets: "Summary" (summary_results group) and "Generation" (first scenario's generation data, summed across nodes).

Uses `openpyxl` engine.

#### to_json

```python
def to_json(self, output_path: Union[str, Path]) -> None
```

Export metadata and summary data to a JSON file with nested structure matching the HDF5 hierarchy. Scenario names and attributes are included; large hourly arrays are NOT exported to JSON (use CSV or HDF5 for full time-series data).

**JSON structure:**

```json
{
  "metadata": {
    "creation_date": "2026-01-15T10:30:00",
    "hours": "8760",
    "num_nodes": "4",
    "num_generators": "7",
    "num_batteries": "1"
  },
  "summary": {
    "total_cost": [1234567.0, ...],
    "gen_investment_power": [...]
  },
  "scenarios": {
    "year_2025_threshold_0.5": {
      "year": "2025",
      "threshold": "0.5",
      "objective": "5432100.0"
    }
  }
}
```

### Example

```python
from esfex.io.exporter import ResultsExporter

exporter = ResultsExporter("results/isla_juventud.h5")

# Export to CSV directory
exporter.to_csv("results/csv/")

# Export to Excel workbook
exporter.to_excel("results/report.xlsx")

# Export to JSON
exporter.to_json("results/data.json")
```

---

## export_system_results

```python
def export_system_results(
    results_dict: Dict[str, Any],
    generators: List[dict],
    batteries: List[dict],
    hours: int,
    num_nodes: int,
    output_filename: Optional[str] = None,
    output_dir: Union[str, Path] = "results",
    temporal_resolution_hours: int = 1,
) -> Path
```

Write optimization results to an HDF5 file. Called by the runner after each simulation.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results_dict` | `dict` | required | Dictionary with optimization results keyed by scenario. Keys can be `(year, threshold)` tuples or string identifiers. |
| `generators` | `list[dict]` | required | List of generator configurations (used for names and metadata). |
| `batteries` | `list[dict]` | required | List of battery configurations. |
| `hours` | `int` | required | Number of hours in the simulation. |
| `num_nodes` | `int` | required | Number of nodes. |
| `output_filename` | `str` or `None` | `None` | Output filename. If `None`, auto-generates as `results_YYYYMMDD_HHMMSS.h5`. |
| `output_dir` | `str` or `Path` | `"results"` | Output directory (created if it does not exist). |
| `temporal_resolution_hours` | `int` | `1` | Temporal resolution of the data (for metadata). |

**Returns:** `Path` to the created HDF5 file.

### HDF5 File Structure

```
results_20260215_143000.h5
    [attrs]
        creation_date: "2026-02-15T14:30:00"
        hours: 8760
        num_nodes: 4
        temporal_resolution_hours: 1
        num_generators: 7
        num_batteries: 1
        generator_names: ["Solar_PV", "Wind", "Diesel", ...]
        battery_names: ["Li_Ion"]

    detailed_results/
        year_2025_threshold_0.5/
            [attrs]
                year: 2025
                threshold: 0.5
                objective: 5432100.0
                total_generation: 1234000.0
                renewable_generation: 987000.0
                renewable_penetration: 0.80
                co2_emissions: 12345.0

            hourly_data/
                generation/
                    Solar_PV  [nodes x hours]  (units: MW)
                    Wind      [nodes x hours]  (units: MW)
                    Diesel    [nodes x hours]  (units: MW)
                    ...

                battery_charge/
                    Li_Ion    [nodes x hours]  (units: MW)

                battery_discharge/
                    Li_Ion    [nodes x hours]  (units: MW)

                curtailment          [nodes x hours]  (units: MW)
                electricity_prices   [hours]           (units: $/MWh)
                nodal_electricity_prices [nodes x hours] (units: $/MWh)
                CO2_emissions        [hours]           (units: tonnes)
                power_flow           [from x to x hours] (units: MW)
                EV_charging          [nodes x hours]  (units: MW)
                EV_V2G              [nodes x hours]  (units: MW)
                loss_of_inertia      [hours]          (units: GW*s)

                capacity_factor/
                    Solar_PV  [1]  (dimensionless)
                    Wind      [1]  (dimensionless)
                    ...

                lcoe/
                    Solar_PV  [1]  (USD/MWh)
                    Diesel    [1]  (USD/MWh)
                    ...

                vallcoe/
                    Solar_PV  [1]  (USD/MWh)
                    ...

                technology_selling_prices/
                    Solar_PV/
                        [attrs] total_generation, total_revenue,
                                average_selling_price, technology_type
                        prices_weights [N x 3]  (price, generation, timestep)
                    ...
```

All time-series datasets use gzip compression and chunked storage for efficient access.

---

## Derived Metrics

Derived metrics computed by the runner's `_compute_derived_metrics()` method:

| Metric | Description | Formula |
|--------|-------------|---------|
| Capacity Factor | Fraction of maximum possible output | `total_generation / (rated_power * hours)` |
| LCOE | Levelized cost of energy | `(fixed_cost + fuel_cost + maintenance) / total_generation` |
| VALLCOE | Value-adjusted LCOE | `LCOE - (revenue_from_selling_price / total_generation)` |
| Technology Selling Price | Revenue-weighted average price | `sum(price * generation) / sum(generation)` |

Stored as per-generator datasets in the `capacity_factor/`, `lcoe/`, `vallcoe/`, and `technology_selling_prices/` groups.

---

## read_results

```python
def read_results(results_path: Union[str, Path]) -> Dict[str, Any]
```

Read results from an HDF5 file back into Python.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `results_path` | `str` or `Path` | Path to HDF5 results file. |

**Returns:** Dictionary with structure:

```python
{
    "metadata": {
        "creation_date": "2026-02-15T14:30:00",
        "hours": 8760,
        "num_nodes": 4,
        ...
    },
    "scenarios": {
        "year_2025_threshold_0.5": {
            "attrs": {"year": 2025, "threshold": 0.5, "objective": 5432100.0},
            "hourly_data": {
                "generation": {"Solar_PV": np.ndarray, "Diesel": np.ndarray, ...},
                "battery_charge": {"Li_Ion": np.ndarray},
                "curtailment": np.ndarray,
                "electricity_prices": np.ndarray,
                ...
            }
        }
    }
}
```

Handles both HDF5 formats:
- Incremental format: data directly in scenario group.
- One-shot format: data nested in `hourly_data/` subgroup.

**Example:**

```python
from esfex.io.exporter import read_results

results = read_results("results/isla_juventud.h5")
gen_data = results["scenarios"]["year_2030_threshold_0.5"]["hourly_data"]["generation"]
solar_output = gen_data["Solar_PV"]  # np.ndarray shape (nodes, hours)
print(f"Peak solar: {solar_output.max():.1f} MW")
```
