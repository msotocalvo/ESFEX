# Changelog

All notable changes to **ESFEX** are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-release notes are also published on the
[GitHub Releases page](https://github.com/Net-Zero-Horizon/ESFEX/releases).

## [0.1.5] — 2026-06-06

### Fixed

- **Grid Builder demand forecast not persisted (#7)** — applying the step-3
  demand forecast only stored per-node summary stats and never wrote the hourly
  series to disk nor recorded a CSV path, so the saved config carried empty
  `demand_paths` and the runner had no per-node demand. The forecast is now
  written to per-node CSV files (under a `demand/` folder next to the project)
  and wired into each node, so `demand_paths` is emitted and the runner finds
  the files.

## [0.1.4] — 2026-06-06

### Added

- **Reservoir hydropower modelling overhaul (#4)** — hydroelectric generators
  are now dispatched against an explicit water-energy budget in both the
  operational dispatch and the capacity-expansion master, instead of being
  treated as firm capacity. Five behaviours, each independently optional and
  fully wired into the Studio GUI (en/es/ja):
  - **Energy budget** — water balance (inflow, turbining, pumping, spillage,
    evaporation) modelled in the master, correcting hydro previously
    over-credited as firm MW.
  - **Minimum environmental flow** — a mandatory ecological release floor.
  - **Seasonal storage** — reservoir level chained chronologically across
    representative periods (TSAM inter-period linking), so water banked in a wet
    season is available in a later dry one.
  - **Hydraulic cascade** — an upstream reservoir's release feeds a downstream
    reservoir, with an optional travel delay.
  - **Head dependence** — a depleted reservoir delivers less peak power, via a
    linear (LP-friendly) level-dependent power limit.

### Fixed

- **Deleting a system left orphan inter-system links (#5)** — links whose
  endpoints referenced a deleted system survived in the project and were
  silently dropped by the runner. Deletion now removes every link touching the
  system.
- **Grid Builder country detection (#6)** — step 3 reverse-geocoded the
  bounding-box centroid via Nominatim, folding territories into their sovereign
  state (Puerto Rico → United States), finding only one country per region
  (Haiti was missed), and returning localized names. Detection is now offline
  and territory-aware: grid nodes are tested against bundled country polygons,
  surfacing every country the region intersects with correct ISO3 codes.

### Documentation

- New reservoir-hydropower formulation page documenting the water balance and
  all five behaviours as LP constraints; reservoir config/GUI fields and the
  constraint catalogue updated.
- README links the companion repositories and uses Harvey-ball icons in the
  feature comparison table.

## [0.1.3] — 2026-06-05

### Fixed

- **Grid Builder demand-forecast crash (#3)** — the forecast step's worker
  threads (country detection, World Bank / ERA5 fetch, ML forecast) updated Qt
  widgets directly, violating Qt's main-thread-only GUI rule and segfaulting
  (`Cannot create children for a parent that is in a different thread`). The
  heavy work still runs off the GUI thread, but every widget update is now
  marshalled to the main thread via a queued signal.

## [0.1.2] — 2026-06-05

### Added

- **Benders decomposition** as an optional master-problem solver
  (`master_problem.solver_method: monolithic | benders`): an investment-only
  master with `θ[y]` recourse variables plus per-representative-day dispatch
  subproblems and optimality cuts — beneficial for very large problems.
  Configurable (`benders_max_iterations`, `benders_tolerance`,
  `benders_lol_penalty_cap`) and selectable from the Studio. Monolithic remains
  the default.
- **OpenSSF Best Practices** badge.

### Fixed

- **Grid Builder bus-distribution step no longer freezes the UI** on
  whole-country footprint sets: classification and nearest-bus assignment run in
  a background thread, with a vectorised classifier, a single centroid pass, and
  `np.bincount` accumulation.

## [0.1.1] — 2026-06-04

### Fixed

- **Grid Builder demand forecast crash** — the per-node forecast read
  `latitude`/`longitude` on grid nodes, but `GuiNode` exposes its position as
  `centroid_lat`/`centroid_lng`.
- **Fuel-entry-point duplication crash** — duplicating a fuel entry point used
  `coordinate.latitude`/`.longitude`, but `GeoPoint` uses `lat`/`lng`.
- **GeoJSON fuel-entry import** — fuel entry points were built with invalid
  `max_import_rate`/`import_cost` keyword arguments; import parameters now pass
  through the `fuel_params` mapping.
- **GeoJSON node import** — nodes are created from `Point` features and snapped
  to the nearest existing node by great-circle (haversine) distance, instead of
  always attaching to the first node.

### Changed

- Native Julia test suite expanded from smoke tests to full unit and
  end-to-end model-solve coverage, reported to Codecov under a `julia` flag.

## [0.1.0] — 2026-06-02

First PyPI release of **ESFEX — Energy System FlEXibility**.

Hybrid Python/Julia framework for power-system capacity expansion and
operational dispatch under high renewable penetration: two-stage decomposition,
DC/AC optimal power flow, N-1 security, frequency stability, battery storage,
sector coupling (electrolyzer, primary energy, EV/V2G, rooftop solar),
MGA/SPORES, stochastic programming, Sobol sensitivity, and a GIS-based Studio.
Includes the unit-commitment load-shed fix, the test/coverage expansion, and
full packaging (CI, Apache-2.0, REUSE-compliant).

[0.1.5]: https://github.com/Net-Zero-Horizon/ESFEX/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Net-Zero-Horizon/ESFEX/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Net-Zero-Horizon/ESFEX/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Net-Zero-Horizon/ESFEX/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Net-Zero-Horizon/ESFEX/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Net-Zero-Horizon/ESFEX/releases/tag/v0.1.0
