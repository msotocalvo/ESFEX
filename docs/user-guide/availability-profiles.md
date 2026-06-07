# Availability Profiles

## What Are Availability Profiles?

A time series of **capacity factors** (0.0 to 1.0) describing the fraction of nameplate capacity available at each hour. A 100 MW solar PV generator with availability 0.85 at hour 12 can produce up to 85 MW.

- **0.0** means the resource is completely unavailable (e.g., solar at night, wind during calm)
- **1.0** means the generator can run at full rated power
- Intermediate values reflect partial resource availability (e.g., cloudy conditions for solar, moderate wind speeds)

Non-renewable generators (diesel, gas, biomass) implicitly use a constant availability of 1.0.


---


## File Format

| Format | Extension | Notes |
|--------|-----------|-------|
| CSV | `.csv` | No header row; pure numeric data |
| Excel | `.xlsx`, `.xls` | First row may contain headers (auto-detected) |

### CSV Structure

No header row. Each row represents one hour; each column represents one node.

```
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.05,0.04,0.06
0.15,0.12,0.18
0.35,0.30,0.40
0.55,0.50,0.60
0.75,0.72,0.78
0.85,0.80,0.90
0.90,0.87,0.92
0.88,0.85,0.91
0.80,0.76,0.84
0.60,0.55,0.65
0.35,0.30,0.40
0.10,0.08,0.12
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
0.00,0.00,0.00
```

### Tabular Layout

| | Node 0 | Node 1 | Node 2 |
|---|--------|--------|--------|
| Hour 1 | 0.00 | 0.00 | 0.00 |
| Hour 2 | 0.00 | 0.00 | 0.00 |
| ... | ... | ... | ... |
| Hour 7 | 0.15 | 0.12 | 0.18 |
| Hour 12 | 0.85 | 0.80 | 0.90 |
| Hour 18 | 0.10 | 0.08 | 0.12 |
| ... | ... | ... | ... |
| Hour 8760 | 0.00 | 0.00 | 0.00 |

- **Values**: Between 0.0 (no output) and 1.0 (full rated power)
- **Rows**: One per hour (8,760 rows for one standard year)
- **Columns**: One per node in the system
- **Formats**: CSV (`.csv`) or Excel (`.xlsx`)

---


## Example Profiles

### Solar PV Profile (Typical Day)

```
Hour,  Node 0 (South),  Node 1 (North)
  1        0.00              0.00
  2        0.00              0.00
  3        0.00              0.00
  4        0.00              0.00
  5        0.00              0.00
  6        0.02              0.01
  7        0.12              0.08
  8        0.30              0.22
  9        0.52              0.45
 10        0.70              0.62
 11        0.82              0.75
 12        0.88              0.82
 13        0.90              0.84
 14        0.85              0.80
 15        0.72              0.68
 16        0.55              0.50
 17        0.32              0.28
 18        0.10              0.07
 19        0.01              0.00
 20        0.00              0.00
 21        0.00              0.00
 22        0.00              0.00
 23        0.00              0.00
 24        0.00              0.00
```

Key characteristics:
- Zero during nighttime (typically 18:00-06:00 depending on latitude/season)
- Peak 0.80-0.95 on clear days
- Seasonal variation: longer daylight and higher peaks in summer
- Spatial variation: southern nodes may exceed northern ones (Northern Hemisphere)
- Annual average capacity factor: 0.15-0.25

### Wind Profile (Seasonal Pattern)

```
Hour,  Node 0 (Coastal),  Node 1 (Inland)
  1        0.45               0.20
  2        0.50               0.22
  3        0.48               0.18
  4        0.52               0.25
  5        0.55               0.30
  6        0.60               0.35
  7        0.58               0.32
  8        0.50               0.28
  9        0.42               0.22
 10        0.38               0.18
 11        0.35               0.15
 12        0.30               0.12
 13        0.28               0.10
 14        0.32               0.15
 15        0.40               0.20
 16        0.45               0.25
 17        0.50               0.30
 18        0.55               0.35
 19        0.58               0.38
 20        0.60               0.40
 21        0.55               0.35
 22        0.50               0.30
 23        0.48               0.25
 24        0.45               0.22
```

Key characteristics:
- No fixed diurnal pattern
- Higher hour-to-hour variability than solar
- Seasonal: winter typically windier than summer in many regions
- Coastal nodes often have higher, more consistent availability than inland
- Annual average capacity factor: 0.20-0.45 depending on location


---


## Mapping Profiles to Generators in YAML

Reference an availability profile using the `Availability` field (capital A, a YAML alias for `availability_file`):

```yaml
systems:
  my_system:
    generators:
      solar_pv:
        name: Solar PV
        type: Renewable
        fuel: Sun
        rated_power: [50.0, 30.0]
        Availability: data/profile_sun_1.xlsx
        # ... other fields ...

      wind_farm:
        name: Wind Farm
        type: Renewable
        fuel: Wind
        rated_power: [20.0, 0.0]
        Availability: data/profile_wind_1.xlsx
        # ... other fields ...

      diesel_gen:
        name: Diesel Generator
        type: Non-renewable
        fuel: Diesel
        rated_power: [15.0, 10.0]
        # No Availability field -- implicitly 1.0 at all hours
        # ... other fields ...

    technologies:
      tech_solar:
        name: Solar PV Investment
        type: Renewable
        fuel: Sun
        invest_cost: [900000.0]
        invest_max_power: [500.0]
        Availability: data/profile_sun_1.xlsx
        # ... other fields ...
```

### Path Resolution

- **Relative path**: Resolved relative to the YAML config file directory
- **Absolute path**: Full filesystem path

If relative resolution fails, ESFEX attempts the value as an absolute path before raising an error.

### Which Generators Need Profiles?

| Generator Type | Availability Profile Required? | Typical Values |
|---------------|-------------------------------|----------------|
| Solar PV | Yes | 0.0-0.90, diurnal pattern |
| Wind | Yes | 0.0-0.95, weather-driven |
| Hydroelectric (run-of-river) | Yes | 0.2-0.8, seasonal |
| Biomass | Optional | 0.85-0.95 (planned outages) |
| Diesel / Gas / Fuel Oil | No | Implicit 1.0 |
| OTEC | Optional | 0.90-0.95 (nearly constant) |
| Batteries / Storage | No | Not applicable |

---


## Temporal Resolution

### Hourly Profiles (Default)

Profiles are expected at hourly resolution (8,760 rows per year).

### Sub-Hourly Aggregation

At coarser temporal resolution (`temporal.resolution_hours`), ESFEX aggregates using the **mean** within each time block. With `resolution_hours: 6`, 8,760 hourly values become 1,460 six-hour averages.

```yaml
temporal:
  resolution_hours: 6      # Each time step = 6 hours
  rolling_horizon_hours: 48
  overlap_hours: 6
```

### Resolution Mismatch Handling

- **Fewer rows than expected**: Tiled (repeated cyclically) to fill the required duration
- **More rows than expected**: Sliced from the beginning


---


## Multi-Year Profiles

Availability files should contain data for all years concatenated row-by-row:

| Simulation Duration | Rows Required |
|-------------------|---------------|
| 1 year | 8,760 |
| 5 years | 43,800 |
| 10 years | 87,600 |
| 25 years | 219,000 |

If the file contains fewer rows than required, ESFEX tiles it cyclically. Year-specific data is recommended for studies where inter-annual variability matters.


---


## How Availability Is Used in the Optimization

The availability factor constrains renewable generator maximum output at each time step:

$$P_{g,n,t} \leq (\bar{P}_{g,n} + I_{g,n}) \times \alpha_{g,t,n}$$

where:
- $P_{g,n,t}$ is the power output of generator $g$ at node $n$ in hour $t$
- $\bar{P}_{g,n}$ is the existing rated (installed) capacity
- $I_{g,n}$ is the investment capacity added by the optimizer
- $\alpha_{g,t,n}$ is the availability factor from the profile

The optimizer can **curtail** (produce less than available) but never exceed the availability-limited maximum.


---


## Generating Profiles from Weather Data

### Using ESFEX Built-in Workflows

The workflow dependencies (`pvlib`, `atlite`, `geopandas`, …) are part of
the core install — `pip install esfex` is all you need.

- **Solar PV**: Uses `pvlib` with ERA5 reanalysis data to compute hourly capacity factors. Accounts for panel orientation, temperature losses, and inverter efficiency.
- **Wind**: Uses `atlite` with ERA5 or MERRA-2 data to compute hourly capacity factors. Accounts for hub height, power curve, and air density corrections.

See [Analysis Workflows](../workflows/index.md) for detailed instructions.

### Using External Tools

You can also generate profiles externally and save them in the required format:

1. **Renewables.ninja** (https://www.renewables.ninja/): Web interface for solar and wind capacity factors from ERA5/MERRA-2. Download as CSV, reformat to match the required column structure (one column per node).

2. **PVLIB Python** (https://pvlib-python.readthedocs.io/): Python library for solar PV modeling. Example:
   ```python
   import pvlib
   import pandas as pd

   location = pvlib.location.Location(23.0, -82.5, tz='America/Havana')
   times = pd.date_range('2025-01-01', periods=8760, freq='h')
   clearsky = location.get_clearsky(times)
   system = pvlib.pvsystem.PVSystem(
       surface_tilt=23, surface_azimuth=180,
       module_parameters={'pdc0': 1, 'gamma_pdc': -0.004},
       inverter_parameters={'pdc0': 1.1, 'eta_inv_nom': 0.96},
   )
   mc = pvlib.modelchain.ModelChain(system, location)
   mc.run_model(clearsky)
   capacity_factors = mc.results.ac.clip(0, 1)
   capacity_factors.to_csv('profile_sun_1.csv', header=False, index=False)
   ```

3. **Atlite** (https://atlite.readthedocs.io/): Python library for wind and solar resource assessment at scale. Supports ERA5, MERRA-2, and SARAH datasets.

### Quality Checks

Verify generated profiles meet these criteria:
- All values are between 0.0 and 1.0
- Solar profiles have zero values at night (check sunrise/sunset times for the location)
- Annual capacity factors are reasonable for the technology and location
- No extended periods of constant values (which may indicate data errors)
- The correct number of rows for the simulation period

---


## Validation Rules

1. **Value range**: All values are clipped to [0.0, 1.0]. Values outside this range are silently clamped.

2. **Column count**: If the file has fewer columns than the number of nodes in the system, extra columns are padded with 1.0 (full availability). If the file has more columns than nodes, extra columns are truncated.

3. **Row count**: The profile must have at least enough rows for one simulation year at the configured resolution. For multi-year simulations, short profiles are tiled cyclically.

4. **File existence**: If a generator specifies an `Availability` file that does not exist, ESFEX logs a warning and falls back to a default profile of 1.0 for all hours. This fallback prevents simulation failure but may produce unrealistic results for renewable generators.

5. **Data type**: All values must be numeric. Non-numeric cells will cause an error during loading.

---


## Profile Caching

### How Caching Works

1. **Startup preloading**: The `_preload_availability_profiles()` method runs once during simulation initialization. It loads **all** availability files referenced by generators and technologies into an in-memory dictionary.

2. **File-level deduplication**: If multiple generators reference the same physical file (e.g., all solar PV generators sharing `profile_sun_1.xlsx`), the file is loaded only once. Subsequent generators reuse the same NumPy array.

3. **Cache key**: The cache is keyed by generator/technology name. The underlying file is resolved to an absolute path for deduplication.

4. **Operational windows**: During the rolling horizon dispatch, each operational window retrieves the appropriate slice of the cached profile instead of re-reading files from disk.

### Performance Impact

| Scenario | Without Cache | With Cache |
|----------|--------------|------------|
| 25 years, 10 generators, 208 windows/year | ~52,000 file reads | 10 file reads (at startup) |
| Load time per file | ~50 ms | 0 ms (cached) |
| Total I/O time | ~43 minutes | ~0.5 seconds |

### Zone Expansion

When development zones are used, cached profiles are extended to include virtual zone nodes. Each zone copies the availability profile of the nearest existing node.


---


## Common Errors and Troubleshooting

### File Not Found

```
WARNING - Availability file not found: data/profile_sun_1.xlsx, using default 1.0
```

**Cause**: The path specified in `Availability` does not resolve to an existing file.
**Solution**: Verify the path is correct relative to the YAML config file directory. Check for typos and ensure the file exists.

### Column Count Mismatch

```
WARNING - Availability file data/profile_sun_1.xlsx has 3 columns, expected 5. Adjusting...
```

**Cause**: The profile file has a different number of columns than the system has nodes.
**Solution**: Regenerate the profile with the correct number of columns, or let ESFEX auto-adjust (padding with 1.0 or truncating). Note that padded columns mean those nodes will have full availability, which may not be physically realistic.

### Unrealistic Results (High Curtailment or Infeasibility)

If the simulation shows excessive curtailment or unexpected infeasibility:
- Check that solar profiles are not accidentally used for wind generators (and vice versa)
- Verify that the temporal resolution in the config matches the profile's intended resolution
- Confirm that the profile covers enough hours for the simulation period

### Zero-Energy Renewable Output

If renewable generators produce zero energy despite having rated capacity:
- Check that the `Availability` field is correctly specified (capital A)
- Verify the profile file is not empty or filled with zeros
- Check the logs for loading errors or fallback warnings

### Performance Issues

If simulation startup takes too long:
- Large Excel files load slower than CSV. Convert `.xlsx` files to `.csv` for faster I/O
- Profiles with millions of rows (long multi-year studies) consume significant memory. Ensure sufficient RAM is available
- The cache holds all profiles in memory simultaneously. For very large systems (many generators, many nodes, many years), memory usage can be substantial
