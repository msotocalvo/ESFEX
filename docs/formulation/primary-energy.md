# Primary Energy Model

The primary energy model optimizes the fuel supply chain at multiple temporal scales, coupling fuel availability with power generation. It is implemented in `primary_energy.jl`.

## Overview

The primary energy module models:

- **Fuel supply** at import/production nodes with cost and availability limits
- **Fuel transport** between nodes with capacity, losses, and investment
- **Fuel storage** with dynamic inventory management
- **Non-electric demand** for fuels outside the electricity sector
- **Coupling** with generator fuel consumption in the power system
- **Environmental emissions** from fuel combustion and non-electric consumption

## Multi-Temporal Structure

The primary energy model operates at a coarser temporal resolution than the hourly power system dispatch:

| Scale | Resolution | Variables |
|-------|-----------|-----------|
| Hourly | 1 hour | Generator fuel consumption (from power system) |
| Primary period | 24-168 hours | Fuel supply, transport, storage |
| Investment period | 1 year | Infrastructure investment |

The temporal mapping is built by `create_temporal_mapping()`:

```
Hours:              1  2  3 ... 24  25 ... 48  49 ...
Primary periods:    [--- period 1 ---] [-- period 2 --] ...
Investment periods: [---------- year 1 ----------] ...
```

The `TemporalMapping` struct stores:

- `hour_to_primary_period`: Maps each hour to its enclosing primary period
- `primary_to_investment_period`: Maps each primary period to its investment period
- `hours_in_primary_period`: Lists all hours belonging to each primary period
- `num_primary_periods` / `num_investment_periods`: Period counts

This multi-scale approach reduces the number of decision variables for slow-varying quantities (fuel supply scheduling, storage management) while preserving hourly resolution for the fast electrical dispatch coupling.

## Decision Variables

| Variable | Indices | Units | Description |
|----------|---------|-------|-------------|
| \(S_{f,n,p}\) | fuel, node, period | units/period | Fuel supply at node \(n\) |
| \(T_{f,r,p}\) | fuel, route, period | units/period | Fuel transport on route \(r\) |
| \(V^{start}_{f,n,p}\) | fuel, node, period | units | Fuel storage level at period start |
| \(V^{end}_{f,n,p}\) | fuel, node, period | units | Fuel storage level at period end |
| \(V^{hr}_{f,n,t}\) | fuel, node, hour | units | Hourly fuel storage level |
| \(I^{stor}_{f,n,ip}\) | fuel, node, inv. period | units | Storage capacity investment |
| \(I^{trans}_{f,r,ip}\) | fuel, route, inv. period | units/day | Transport capacity investment |
| \(W^{ne}_{f,s,n,p}\) | fuel, sector, node, period | units/period | Non-electric fuel consumption |
| \(W^{elec}_{f,n,t}\) | fuel, node, hour | units/hour | Fuel consumed for power generation |
| \(L^{fuel}_{f,n,p}\) | fuel, node, period | units/period | Fuel supply shortfall (penalized) |
| \(E^{fuel}_{f,n,t}\) | fuel, node, hour | tonnes CO2 | Emissions from non-electric consumption |

Built by `build_primary_energy_variables!()`.

## Fuel Supply Chain Modeling

The primary energy model captures three stages of the fuel supply chain:

### Extraction / Import (Supply)

Fuel enters the system at designated supply nodes (e.g., ports, refineries, gas wellheads). Each node has a maximum supply rate per primary period:

\[
S_{f,n,p} \leq \bar{S}_{f,n,p} + X^{supply}_{f,n,p}
\tag{PE-1}
\]

where \(\bar{S}_{f,n,p}\) is the maximum supply capacity and \(X^{supply}_{f,n,p}\) is an excess supply slack variable (penalized) that allows soft constraint violation when the supply infrastructure is insufficient. The supply limit may vary by period to capture seasonal availability (e.g., reduced LNG deliveries in winter due to shipping constraints).

#### Source Disruption

A source can be configured with a disruption window — an hour range
\([h^{start}, h^{end})\) over which its availability is scaled to a fraction
\(a \in [0,1]\) (\(a = 0\) is a full cut). The per-period supply cap is reduced
by the share of the period's hours that fall inside the window:

\[
\bar{S}^{disrupted}_{f,n,p} = \bar{S}_{f,n,p} \cdot \big( 1 - \phi_{f,p}(1 - a) \big),
\qquad
\phi_{f,p} = \frac{\left|\{ h \in p : h^{start} \le h < h^{end} \}\right|}{|p|}
\tag{PE-1b}
\]

where \(\phi_{f,p}\) is the fraction of period \(p\)'s hours under the disruption. This models a supply-side shock (a terminal or pipeline down for several days). On its own it forces the consuming node onto its tank; together with the transport lead time (PE-3b) — the next shipment still in transit — and a finite tank, it reproduces real fuel-supply stress, with the penalised shortfall curtailing generation once the tank empties.

### Transport

Fuel transport is modeled through explicit routes connecting pairs of nodes. Each route \(r\) has:

- A **from-node** and **to-node**
- A **distance** \(d_r\) (km)
- Per-fuel parameters: capacity, transport losses, and cost

Transport flow is bounded by existing capacity plus investment:

\[
T_{f,r,p} \leq \left(\bar{T}^{base}_{f,r} + C^{cumul}_{f,r} + I^{trans}_{f,r,ip}\right) \cdot \Delta_p^{days}
\tag{PE-2}
\]

where \(\bar{T}^{base}_{f,r}\) is the base daily capacity, \(C^{cumul}_{f,r}\) is cumulative capacity from prior investments, \(I^{trans}_{f,r,ip}\) is new investment in the current investment period, and \(\Delta_p^{days}\) is the number of days in primary period \(p\).

### Transport Losses

Fuel is lost during transport proportional to route distance:

\[
T^{received}_{f,r,p} = T_{f,r,p} \cdot (1 - \lambda^{trans}_f \cdot d_r / 100)
\tag{PE-3}
\]

where \(\lambda^{trans}_f\) is the loss rate per 100 km for fuel \(f\) and \(d_r\) is the route distance in km. This captures pipeline leakage, evaporation during tanker transport, and similar physical losses.

### Transport Lead Time

A plant runs from a local tank (its node storage), which the generator draws down hour by hour — so **tank → generator is instantaneous**. The supply stress lives upstream: replenishing that tank from the source takes time. Each fuel carries a lead time \(\delta_f\) in **days per 100 km** of route distance; the per-route delay in primary periods is

\[
\tau_{f,r} = \mathrm{round}\!\left( \frac{\delta_f \cdot d_r / 100}{\Delta_p} \right)
\tag{PE-3b}
\]

where \(\Delta_p\) is the period length in days. Fuel dispatched on route \(r\) at period \(p\) then arrives at the destination at period \(p + \tau_{f,r}\), so the **received inflow** at node \(n\), period \(p\), uses the *shifted* transport variable:

\[
T^{received}_{f,r,p} = T_{f,r,\,p-\tau_{f,r}} \cdot (1 - \lambda^{trans}_f \cdot d_r / 100), \qquad p - \tau_{f,r} \ge 1
\]

Shipments dispatched before the window (\(p - \tau_{f,r} < 1\)) fall outside it and are not counted in that window (a small boundary effect that storage buffers; the rolling horizon carries the rest). With \(\delta_f = 0\) the inflow reduces to PE-3 (instantaneous transport, the default). Combined with a source disruption and a finite tank, a lead time reproduces real supply stress: a cut drains the tank while the next shipment is still in transit, and if the tank empties the penalised fuel shortfall curtails generation.

### Storage

Fuel storage provides temporal flexibility between supply scheduling and consumption. Storage is modeled at two scales:

**Periodic storage balance** (primary period granularity):

\[
V^{start}_{f,n,p} + S_{f,n,p} + \sum_{r: to(r)=n} T^{received}_{f,r,p} = V^{end}_{f,n,p} + W^{ne,total}_{f,n,p} + \sum_{r: from(r)=n} T_{f,r,p} + L^{fuel}_{f,n,p} + \Delta V^{hr}_{f,n,p}
\tag{PE-4}
\]

where:

- \(W^{ne,total}_{f,n,p} = \sum_s W^{ne}_{f,s,n,p}\) is total non-electric consumption across all sectors
- \(\Delta V^{hr}_{f,n,p}\) is the net hourly storage change (coupling variable linking periodic and hourly scales)
- \(L^{fuel}_{f,n,p}\) is the fuel supply shortfall slack

**Hourly storage dynamics** (used for electricity coupling):

\[
V^{hr}_{f,n,t+1} = V^{hr}_{f,n,t} \cdot \eta^{stor}_f + (\text{storage\_in}_{f,n,t} + H2^{prod}_{f,n,t}) \cdot \eta^{stor}_f - \text{storage\_out}_{f,n,t}
\tag{PE-4b}
\]

where \(\eta^{stor}_f\) is the storage round-trip efficiency and \(H2^{prod}_{f,n,t}\) is hydrogen production from electrolyzers (when applicable).

**Storage continuity** between periods:

\[
V^{start}_{f,n,p} = V^{end}_{f,n,p-1} \quad \forall f, n, p \geq 2
\tag{PE-4c}
\]

For the first period (\(p=1\)), storage is initialized from the configured initial level:

\[
V^{start}_{f,n,1} = V^{init}_f \cdot \bar{V}_{f,n}
\]

### Storage Bounds

Storage levels are bounded by capacity (existing plus investment):

\[
V^{end}_{f,n,p} \leq \bar{V}_{f,n} + C^{cumul,stor}_{f,n} + I^{stor}_{f,n,ip} \quad \forall f, n, p
\tag{PE-5a}
\]

\[
V^{start}_{f,n,p} \leq \bar{V}_{f,n} + C^{cumul,stor}_{f,n} + I^{stor}_{f,n,ip} \quad \forall f, n, p
\tag{PE-5b}
\]

**Tank safety floor (operational reserve).** The plant tank cannot be drawn below a configured minimum level \(\underline{v}_f \in [0,1]\) (a fraction of capacity) — an operational reserve that must be held back. The floor applies to the hourly level and to the period boundaries:

\[
V^{hr}_{f,n,t} \ge \underline{v}_f \cdot \bar{V}_{f,n}, \qquad
V^{end}_{f,n,p} \ge \underline{v}_f \cdot \bar{V}_{f,n}
\tag{PE-5c}
\]

With \(\underline{v}_f = 0\) the tank may be emptied. A positive floor reserves fuel even under stress: combined with a source disruption (PE-1b) and a transport lead time (PE-3b), the reserve cannot be tapped to cover the gap, so the penalised shortfall — and any consequent generation curtailment — appears sooner.

**Storage investment limits** prevent unbounded expansion:

\[
I^{stor}_{f,n,ip} \leq \bar{V}_{f,n} \cdot \xi^{stor} \quad \forall f, n, ip
\tag{PE-5c}
\]

where \(\xi^{stor}\) is the `storage_expansion_limit` parameter (e.g., 2.0 means capacity can at most triple).

## Non-Electric Fuel Demand

Non-electric demand represents fuel consumption outside the electricity sector, such as transportation, industrial heating, and cooking. It is specified per fuel, per sector, per node, and per primary period.

### Demand Satisfaction

\[
W^{ne}_{f,s,n,p} \leq D^{ne}_{f,s,n,p}
\tag{PE-6a}
\]

\[
W^{ne}_{f,s,n,p} + L^{fuel}_{f,n,p} \geq D^{ne}_{f,s,n,p}
\tag{PE-6b}
\]

The first constraint caps consumption at the demand level. The second ensures demand is met, either through actual consumption or through the shortfall slack \(L^{fuel}_{f,n,p}\) (which is penalized in the objective).

### Sector Examples

| Sector | Fuel | Typical Demand |
|--------|------|---------------|
| Transport | Diesel, Gasoline | Vehicle fleet consumption |
| Industrial | Natural Gas, Coal | Process heat and feedstock |
| Residential | LPG, Kerosene | Cooking, water heating |
| Commercial | Diesel | Backup generators, heating |

## Fuel Cost Formulation

### Variable Costs

The variable cost of fuel supply depends on the procurement price at each supply node:

\[
C^{supply} = \sum_{f,n,p} c^{supply}_{f,n} \cdot S_{f,n,p}
\]

where \(c^{supply}_{f,n}\) is the import/production cost at node \(n\) for fuel \(f\) ($/unit).

### Transport Costs

Transport costs are proportional to volume and distance:

\[
C^{transport} = \sum_{f,r,p} c^{trans}_{f,r} \cdot d_r \cdot T_{f,r,p}
\]

where \(c^{trans}_{f,r}\) is the transport cost per unit per km for route \(r\).

### Fixed / Investment Costs

Investment in storage and transport infrastructure incurs annualized capital costs:

\[
C^{invest} = \sum_{f,n,ip} c^{stor,inv}_f \cdot I^{stor}_{f,n,ip} + \sum_{f,r,ip} c^{trans,inv}_f \cdot d_r \cdot I^{trans}_{f,r,ip}
\]

### Penalty Costs

Fuel supply shortfall and excess supply are penalized:

\[
C^{penalty} = \sum_{f,n,p} c^{pen,loss} \cdot L^{fuel}_{f,n,p} + \sum_{f,n,p} c^{pen,excess} \cdot X^{supply}_{f,n,p}
\]

## Multi-Fuel Systems

ESFEX supports modeling multiple fuel types simultaneously. Each fuel is configured independently with its own:

- Supply nodes and limits
- Transport routes (with per-fuel parameters on shared routes)
- Storage infrastructure
- Energy content and emission factor
- Price structure

Fuels are coupled only through:

1. **Shared transport routes**: A single physical pipeline or road may carry multiple fuels, each with distinct capacity and loss parameters (via `fuel_params` on `TransportRoute`)
2. **Generator fuel assignment**: Each generator is assigned a single fuel type; the fuel consumption is determined by the generator's heat rate/efficiency
3. **Emission budgets**: All fuel-related emissions contribute to a common CO2 budget when configured

### Fuel Configuration Example (Multi-Fuel)

```yaml
fuels:
  diesel:
    emission_factor: 0.267      # tCO2/MWh_thermal
    energy_content: 11.86       # MWh/tonne
    price: 800.0                # $/tonne
  natural_gas:
    emission_factor: 0.202      # tCO2/MWh_thermal
    energy_content: 13.9        # MWh/tonne (LNG equivalent)
    price: 350.0                # $/tonne
  hydrogen:
    emission_factor: 0.0        # Zero direct emissions
    energy_content: 33.33       # MWh/tonne
    price: 3000.0               # $/tonne (green H2)
```

## Import / Export Fuel Flows

Fuel enters and leaves the modeled system through supply nodes. The model supports:

- **Import nodes**: Ports, border crossings, or pipeline entry points where fuel enters the system at a configured import cost
- **Domestic production nodes**: Local extraction sites (e.g., gas wells, biomass processing)
- **Transit flows**: Fuel transported through the system to other nodes, potentially serving both local demand and downstream export

There is no explicit "export" variable. Instead, fuel may be transported to border nodes where non-electric demand represents contractual export obligations. This approach keeps the formulation simple while allowing transit modeling.

## Environmental Emissions from Fuel

### Non-Electric Emissions

Emissions from non-electric fuel consumption are computed hourly:

\[
E^{fuel}_{f,n,t} = W^{ne,hr}_{f,n,t} \cdot \varepsilon_f \cdot \eta^{energy}_f
\tag{PE-EM-1}
\]

where:

- \(W^{ne,hr}_{f,n,t}\) is hourly non-electric consumption
- \(\varepsilon_f\) is the emission factor (tCO2 per MWh thermal or per physical unit)
- \(\eta^{energy}_f\) is the energy content (MWh/unit); set to 1 when the emission factor is already per physical unit

If `energy_content` is greater than zero, the full conversion applies. Otherwise, the emission factor is assumed to be directly per physical unit:

\[
E^{fuel}_{f,n,t} = W^{ne,hr}_{f,n,t} \cdot \varepsilon_f
\tag{PE-EM-1b}
\]

### Total Primary Emissions

Total emissions across all fuels at each node and hour:

\[
E^{total}_{n,t} = \sum_f E^{fuel}_{f,n,t}
\tag{PE-EM-2}
\]

These emissions are tracked separately from electricity-sector CO2 emissions (which are computed in `power_system.jl`). The total system emissions for policy reporting combine both:

\[
E^{system} = \sum_{n,t} E^{total,elec}_{n,t} + \sum_{n,t} E^{total}_{n,t}
\]

### Fuel Availability Constraints

Beyond per-period supply limits (PE-1), fuel availability can be further constrained by:

1. **Annual supply caps**: Total fuel available per year across all nodes

\[
\sum_{n,p \in \mathcal{P}_y} S_{f,n,p} \leq \bar{S}^{annual}_{f,y}
\]

2. **Seasonal supply profiles**: Maximum supply rates that vary by season (e.g., reduced gas availability in winter heating season)

3. **Import dependency limits**: Policy constraints limiting the fraction of total fuel that can be imported (as opposed to domestically produced)

These constraints are configured through the primary energy sources YAML section and enforced within the primary period constraint loop.

## Objective Terms

\[
C^{PE} = C^{supply} + C^{transport} + C^{invest} + C^{penalty}
\tag{PE-OBJ}
\]

Expanded:

\[
C^{PE} = \sum_{f,n,p} c^{supply}_f \cdot S_{f,n,p} + \sum_{f,r,p} c^{trans}_f \cdot d_r \cdot T_{f,r,p} + \sum_{f,n,ip} c^{stor,inv}_f \cdot I^{stor}_{f,n,ip} + \sum_{f,r,ip} c^{trans,inv}_f \cdot d_r \cdot I^{trans}_{f,r,ip} + \sum_{f,n,p} c^{penalty} \cdot L^{fuel}_{f,n,p}
\tag{PE-OBJ-FULL}
\]

| Term | Description |
|------|-------------|
| Supply cost | Fuel procurement at import nodes |
| Transport cost | Operating cost proportional to distance |
| Storage investment | Annualized infrastructure cost |
| Transport investment | Annualized route capacity cost |
| Shortfall penalty | Penalty for unmet non-electric fuel demand |

## Power System Coupling

The primary energy model is coupled to the power system through fuel consumption:

\[
W_{f,n,p}^{elec} = \sum_{g \in \mathcal{G}_f} \sum_{t \in p} \frac{P_{g,n,t}}{\eta_{g,n}} \cdot \Delta t
\tag{PE-COUPLE}
\]

where \(\mathcal{G}_f\) is the set of generators using fuel \(f\) and \(\eta_{g,n}\) is the generator efficiency.

At the hourly scale, the coupling is implemented through:

\[
W^{elec,hr}_{f,n,t} = \sum_{g \in \mathcal{G}_f} \frac{P_{g,n,t}}{\eta_{g,n}}
\tag{PE-COUPLE-HR}
\]

Coupling slack variables (\(\text{slack}^{start}\) and \(\text{slack}^{end}\)) are included to handle numerical discrepancies between the periodic fuel balance and the aggregated hourly consumption. These are penalized to keep them near zero.

Implemented in `couple_primary_energy_to_power_system!()`.

## Configuration

```yaml
enable_primary_energy: true

systems:
  my_system:
    fuels:
      diesel:
        emission_factor: 0.267      # tCO2/MWh
        energy_content: 11.86       # MWh/tonne
        price: 800.0                # $/tonne

    primary_energy_sources:
      diesel_import:
        fuel: diesel
        node: 0
        max_supply: 1000.0          # tonnes/period
        import_cost: 800.0          # $/tonne
        storage_capacity: 5000.0    # tonnes
        storage_min: 500.0          # tonnes (safety stock)

    primary_energy_infrastructure:
      diesel:
        storage_efficiency: 0.98    # Round-trip efficiency
        storage_expansion_limit: 2.0  # Max 2x base capacity expansion

    transport_routes:
      - from_node: 0
        to_node: 1
        distance_km: 150.0
        fuel_params:
          diesel:
            capacity: 200.0         # tonnes/day
            transport_losses: 0.5   # % per 100 km
            transport_cost: 10.0    # $/tonne/km

    non_electric_demand:
      transport_diesel:
        fuel: diesel
        sector: transport
        demand_per_node: [50.0]     # tonnes/period per node
```

---

## References

The multi-commodity network flow formulation for fuel supply chains follows standard LP transport models as described in Wood et al. [**[31]**](../reference/bibliography.md#ref31). The coupling between fuel logistics and generator dispatch is a feature shared with TIMES [**[16]**](../reference/bibliography.md#ref16), though ESFEX operates at chronological hourly resolution rather than time slices. IRENA cost benchmarks [**[48]**](../reference/bibliography.md#ref48) provide default fuel and infrastructure cost assumptions.

See the [full bibliography](../reference/bibliography.md) for complete citation details.
