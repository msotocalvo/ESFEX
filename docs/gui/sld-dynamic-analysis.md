# Single-Line Diagram Analysis
Three levels of dynamic analysis overlaid on the Single-Line Diagram (SLD) topology, extending the base operational overlay with simulation results and post-contingency calculations.


---


## Prerequisites

1. Build your system in the Geographic View with at least one node, generators, and (optionally) transmission lines
2. Switch to the **SLD View** tab
3. Click **Load Results** and select an HDF5 results file from a completed simulation
4. The operational toolbar becomes active with year/hour controls, playback, and contingency selection


---


## Level 1: Enhanced Operational Overlay

Fields read directly from HDF5 simulation results, displayed alongside generation, demand, and price data.

### Generator Status Indicators

Each generator on the SLD shows its commitment status:

| Status | Visual | Meaning |
|--------|--------|---------|
| **Online** | Normal color + utilization bar | Generator is committed and producing |
| **Offline** | Gray circle overlay + "OFF" label | Generator is not committed for this hour |
| **Starting up** | Green upward arrow badge | Generator is starting up this hour |
| **Shutting down** | Red downward arrow badge | Generator is shutting down this hour |

Offline generators show no utilization bar, since they have zero output.

### Reserve Badges

Compact badge near each node's bus:

```
R: 15/8 MW
```

Format: `R: static/dynamic MW`. Badge color indicates adequacy:

| Color | Meaning |
|-------|---------|
| **Green** | No reserve loss -- reserves are fully met |
| **Red** | Reserve loss detected -- reserves are insufficient |

### Voltage Angle Labels

Per-bus voltage angle label:

```
\u03B8=2.3\u00B0
```

Color coding:

| Color | Range | Interpretation |
|-------|-------|----------------|
| **Green** | < 10\u00B0 | Normal operating range |
| **Yellow** | 10\u00B0--20\u00B0 | Elevated, monitor stability |
| **Red** | > 20\u00B0 | High angle, potential stability concern |

### CO\u2082 per Node

Per-node CO\u2082 emissions included in the system info bar.

### System Info Bar

Fixed bar at the bottom, extended with frequency metrics (when available):

```
Year 2030 | Hour 100 | RE: 42.0% | Demand: 300 MW | Gen: 295 MW | CO\u2082: 15.2 t | ROCOF: 0.83 Hz/s | Nadir: 49.3 Hz | H: 1250 MW\u00B7s
```


---


## Level 2: N-1 Contingency Analysis

Simulate the loss of any generator or transmission line and see the post-contingency impact on the SLD.

### Using the Contingency Combo

**Contingency** dropdown in the SLD toolbar:

```
... | Contingency: [None \u25BC] | ...
```

Populated with all N-1 contingencies sorted by impact (highest first):

- **Generator contingencies**: "Loss: gen_name (X MW)" -- loss of generation output
- **Line contingencies**: "Loss: line_name (X MW cap)" -- loss of transmission capacity

Select a contingency to overlay the post-contingency state.

### Contingency Overlay

| Element | Visual |
|---------|--------|
| **Tripped element** | Red X cross over the tripped generator or line |
| **Overloaded lines** | Thick red dashed stroke with "OVERLOAD +X%" label |
| **Load shedding** | Red "Shed: X MW" badge on affected nodes |
| **Security status** | Badge in upper right: green "N-1 SECURE" or red "N-1 INSECURE" |

Select "None" to remove the overlay and return to base operational view.

### Generator Loss Analysis

Generator trip sequence:

1. The lost generation is redistributed pro-rata among remaining generators based on their available headroom (rated capacity minus current output)
2. Renewable generators do not participate in redistribution
3. If total headroom is insufficient, load shedding is applied proportionally to demand at each node
4. The DC power flow is solved with updated injections to find new line flows
5. Lines exceeding thermal capacity are flagged as overloaded

### Line Loss Analysis

Line trip sequence:

1. The network susceptance matrix is rebuilt without the tripped line
2. New voltage angles and line flows are computed using DC power flow
3. Lines exceeding thermal capacity are flagged as overloaded
4. Generation dispatch is unchanged (only flows redistribute)


---


## Level 3: Frequency Response Gauge

Compact frequency stability gauge in the upper-right corner, computed from the center-of-inertia model.

### Frequency Gauge

Displays:

1. **Frequency bar** (48--52 Hz): Color-coded zones with a needle at the predicted nadir
   - Red zones: < 49 Hz and > 51 Hz
   - Yellow zones: 49--49.5 Hz and 50.5--51 Hz
   - Green zone: 49.5--50.5 Hz

2. **Metrics**:
   - ROCOF (Hz/s) -- colored green if within limits, red if exceeded
   - System inertia H (MW\u00B7s) and power imbalance \u0394P (MW)

3. **Border color**: Green if the system is frequency-stable (nadir above limit), red if unstable

Automatically uses the largest online generator's output as the worst-case N-1 power imbalance.

### Interpretation

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| ROCOF | < 1.0 Hz/s | 1.0--2.0 Hz/s | > 2.0 Hz/s |
| Nadir | > 49.5 Hz | 49.0--49.5 Hz | < 49.0 Hz |
| Steady-state | > 49.5 Hz | 49.0--49.5 Hz | < 49.0 Hz |

High renewable penetration reduces system inertia (lower H), leading to higher ROCOF and lower nadir values.


---


## Configuration

### Generator Parameters

Per-generator droop and governor parameters in YAML:

```yaml
generators:
  unit_0:
    name: Diesel
    type: Non-renewable
    inertia: [5.0]              # Inertia constant H (seconds) -- already existing
    droop: [0.05]               # Governor droop R (pu), 5% is typical
    governor_time_const: [5.0]  # Governor time constant T_g (seconds)
    # ... other parameters
```

### System Parameters

System-level frequency parameters:

```yaml
systems:
  my_system:
    load_damping: 0.01            # Load damping D (pu), 1% typical
    frequency_nominal: 50.0       # Nominal frequency (Hz)
    rocof_limit: 2.0              # Max ROCOF threshold (Hz/s)
    frequency_nadir_limit: 49.0   # Min frequency threshold (Hz)
```

All parameters have sensible defaults. See [Frequency Stability](../formulation/frequency-stability.md) for the mathematical formulation.


---


## Playback

All overlays update automatically during animated playback. Use play/pause and speed selector:

| Speed | Interval | Use case |
|-------|----------|----------|
| 1x | 1 s/hour | Detailed inspection |
| 2x | 500 ms/hour | Normal review |
| 5x | 200 ms/hour | Quick scan |
| 10x | 100 ms/hour | Overview |

The contingency overlay is static -- shows the post-contingency state for the selected hour only.


---


## Real-Time Analysis Mode

Runs frequency and contingency analysis directly from the editor state without simulation results.

### Activating Analysis Mode

1. Switch to the **SLD View** tab
2. Click the **Analysis** toggle button in the toolbar
3. A dispatch scenario panel appears on the right side of the SLD

### Dispatch Scenario Panel

Two editable tables:

**Generator Dispatch Table:**

| Generator | Rated (MW) | Output (MW) | On |
|-----------|-----------|-------------|-----|
| Diesel_0  | 100       | [80.0]      | [✓] |
| Solar_1   | 200       | [150.0]     | [✓] |

- Output: Adjustable spinbox (0 to rated power)
- Status: On/Off checkbox per generator
- Quick-set buttons: "All On", "100%", "80%"

**Demand Table:**

| Node   | Demand (MW) |
|--------|-------------|
| Node 0 | [120.0]     |
| Node 1 | [180.0]     |

- "Balance Demand" button sets each node's demand equal to its generation

### Real-Time Updates

Changes trigger (after 300 ms debounce):

1. A snapshot is built from the scenario
2. The `FrequencyAnalyzer` computes ROCOF, nadir, steady-state using the largest online generator's output as the worst-case contingency
3. The SLD overlays (frequency gauge, info bar, generation/demand indicators) update immediately
4. If a contingency is selected in the combo, the post-contingency overlay updates too

### Generator Parameters

Uses parameters from the editor's property panel:

- **Inertia H (s)**: Already existing field
- **Droop R (pu)**: Governor droop characteristic (default 0.05 = 5%)
- **Governor T (s)**: Governor time constant (default 5.0 s)

Located in the Electrical group of the generator property editor.

### Switching Between Modes

- Analysis and Load Results modes are independent
- Year/hour/play controls disabled during analysis
- "Clear" exits analysis mode and clears overlays
- Unchecking "Analysis" returns to normal mode, preserving HDF5 results
