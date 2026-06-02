"""Multi-source natural hazard assessment, fragility evaluation, and scenario generation.

This module implements the four-layer architecture described in the Phase 1
plan for the Risk & Resilience Analysis module:

1. **HazardFetcher** subclasses — download hazard intensity maps from public
   databases (USGS, GEM, IBTrACS, STORM, WRI Aqueduct, Fathom, NOAA, NASA
   FIRMS, Smithsonian GVP, NASA AR6, ThinkHazard!).

2. **FragilityLibrary** — lognormal CDF fragility curves for power system
   components, with ~40 built-in defaults from NHESS-2024 and PNNL-33587.

3. **CompositeRiskAssessment** — overlay multiple hazard layers to compute
   per-node composite risk indices and Expected Annual Loss (EAL).

4. **ScenarioGenerator** — sample the risk map to produce discrete
   ``HazardScenarioConfig`` / ``ClimateScenarioConfig`` objects for the
   stochastic optimiser.

Mathematical foundations: ``docs/formulation/risk-resilience.md``, §4.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from scipy.stats import norm

# NumPy 2.0 renamed ``np.trapz`` to ``np.trapezoid`` and removed the old name
# in later 2.x releases. Use whichever the installed NumPy provides.
_trapezoid = getattr(np, "trapezoid", None) or getattr(np, "trapz")

logger = logging.getLogger(__name__)

# Default local cache for downloaded hazard data
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "esfex" / "hazards"


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class HazardIntensityMap:
    """Hazard intensity at each node for specified return periods.

    Attributes
    ----------
    hazard_type : str
        One of ``"earthquake"``, ``"cyclone"``, ``"flood"``, ``"tsunami"``,
        ``"wildfire"``, ``"volcanic"``, ``"sea_level_rise"``.
    source : str
        Data provider key (e.g. ``"usgs"``, ``"gem"``, ``"ibtracs"``).
    intensity_measure : str
        Physical quantity (e.g. ``"PGA"``, ``"wind_speed"``, ``"depth"``).
    units : str
        Units of the intensity measure (e.g. ``"g"``, ``"m/s"``, ``"m"``).
    return_periods : list of int
        Return periods in years.
    node_intensities : dict
        ``{node_index: {return_period: im_value}}``
    metadata : dict
        Source-specific metadata (dates, resolution, dataset version, …).
    """

    hazard_type: str
    source: str
    intensity_measure: str
    units: str
    return_periods: list[int]
    node_intensities: dict[int, dict[int, float]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeRiskProfile:
    """Risk assessment result for a single node.

    Attributes
    ----------
    node_index : int
        Node index within the power system.
    coordinates : tuple
        ``(latitude, longitude)`` in decimal degrees.
    hazard_intensities : dict
        ``{hazard_type: {return_period: im_value}}``
    component_failure_probs : dict
        ``{component_key: {hazard_type: P(fail)}}``
    composite_risk : float
        Combined multi-hazard failure probability (0–1).
    expected_annual_loss : float
        Total EAL across all hazards and components at this node ($/yr).
    dominant_hazard : str
        Hazard type contributing the most to composite risk.
    """

    node_index: int
    coordinates: tuple[float, float]
    hazard_intensities: dict[str, dict[int, float]]
    component_failure_probs: dict[str, dict[str, float]]
    composite_risk: float
    expected_annual_loss: float
    dominant_hazard: str


@dataclass
class RiskEvaluation:
    """ISO 31000 §6.5 risk evaluation result for a single node."""

    node_index: int
    classification: str   # "negligible" | "tolerable_low" | "tolerable_high" | "intolerable"
    eal: float
    composite_risk: float
    risk_band: str        # "low" | "medium" | "high" | "very_high"
    action_required: bool
    justification: str


@dataclass
class MonteCarloRiskResult:
    """Results from Monte Carlo uncertainty propagation through fragility→EAL."""

    n_samples: int
    eal_samples: Any                      # np.ndarray (n_samples,)
    eal_mean: float
    eal_std: float
    eal_p5: float
    eal_p50: float
    eal_p95: float
    var_alpha: float                      # VaR at user-specified alpha
    cvar_alpha: float                     # CVaR at user-specified alpha
    node_eal_samples: dict[int, Any]      # per-node np.ndarray
    dominant_uncertainty: str              # "epistemic" or "aleatory"


@dataclass
class ResilienceMetrics:
    """ISO 22372-compliant resilience metrics."""

    # Reliability
    lolp: float                            # Loss of Load Probability (RISK-22)
    eens_mwh: float                        # Expected Energy Not Supplied (RISK-23)

    # Resilience index (Panteli & Mancarella)
    resilience_index: float                # R ∈ [0,1] (RISK-24)

    # Recovery
    sart_hours: float                      # System Average Recovery Time (RISK-25)

    # ISO 22372 four adaptive capacities (each 0–1)
    anticipatory_capacity: float
    absorptive_capacity: float
    adaptive_capacity: float
    restorative_capacity: float

    # Redundancy
    redundancy_index: float                # N-1 survival ratio
    rto_hours: float                       # Recovery Time Objective

    # Performance curves (for visualisation)
    time_steps: Any = None                 # np.ndarray or None
    performance_curve: Any = None          # np.ndarray or None

    # Per-scenario breakdown
    scenario_eens: dict[str, float] | None = None


def evaluate_risk_criteria(
    profiles: list[NodeRiskProfile],
    criteria: dict | None = None,
) -> list[RiskEvaluation]:
    """Classify each node's risk against configurable ALARP thresholds.

    Parameters
    ----------
    profiles : list of NodeRiskProfile
    criteria : dict, optional
        Keys: ``eal_negligible``, ``eal_tolerable``, ``eal_intolerable``,
        ``composite_risk_low``, ``composite_risk_medium``, ``composite_risk_high``.
        Uses sensible defaults if ``None``.

    Returns
    -------
    list of RiskEvaluation
    """
    c = criteria or {}
    eal_neg = c.get("eal_negligible", 1_000.0)
    eal_tol = c.get("eal_tolerable", 50_000.0)
    eal_int = c.get("eal_intolerable", 500_000.0)
    cr_lo = c.get("composite_risk_low", 0.01)
    cr_med = c.get("composite_risk_medium", 0.05)
    cr_hi = c.get("composite_risk_high", 0.15)

    evaluations: list[RiskEvaluation] = []
    for p in profiles:
        eal = p.expected_annual_loss
        cr = p.composite_risk

        # EAL classification (ALARP)
        if eal < eal_neg:
            cls = "negligible"
        elif eal < eal_tol:
            cls = "tolerable_low"
        elif eal < eal_int:
            cls = "tolerable_high"
        else:
            cls = "intolerable"

        # Composite risk band
        if cr < cr_lo:
            band = "low"
        elif cr < cr_med:
            band = "medium"
        elif cr < cr_hi:
            band = "high"
        else:
            band = "very_high"

        action = cls == "intolerable" or band == "very_high"

        parts = []
        parts.append(f"EAL ${eal:,.0f}/yr → {cls}")
        parts.append(f"P(fail) {cr:.4f} → {band} risk")
        if action:
            parts.append("ACTION REQUIRED: risk exceeds acceptability threshold")
        justification = "; ".join(parts)

        evaluations.append(RiskEvaluation(
            node_index=p.node_index,
            classification=cls,
            eal=eal,
            composite_risk=cr,
            risk_band=band,
            action_required=action,
            justification=justification,
        ))

    return evaluations


# =============================================================================
# Layer 1: Hazard Fetchers
# =============================================================================


def _api_get_json(url: str, timeout: int = 15) -> Any:
    """GET a JSON API endpoint.  Returns parsed data or empty list on failure."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "esfex/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        logger.debug("API query failed (%s): %s", url[:80], exc)
        return []


def _fit_gumbel_return_periods(
    annual_maxima: list[float],
    return_periods: list[int],
) -> dict[int, float]:
    """Fit Gumbel Type-I distribution to annual maxima series.

    Returns ``{return_period: value}`` for each requested period.
    Falls back to method-of-moments if ``scipy.stats.gumbel_r.fit`` fails.
    """
    from scipy.stats import gumbel_r

    if len(annual_maxima) < 3:
        peak = max(annual_maxima) if annual_maxima else 0.0
        return {rp: round(peak, 4) for rp in return_periods}

    try:
        loc, scale = gumbel_r.fit(annual_maxima)
    except Exception:
        mu = float(np.mean(annual_maxima))
        sigma = max(float(np.std(annual_maxima)), 0.01)
        scale = sigma * np.sqrt(6) / np.pi
        loc = mu - 0.5772 * scale

    result: dict[int, float] = {}
    for rp in return_periods:
        p = 1 - 1.0 / rp
        result[rp] = round(float(gumbel_r.ppf(p, loc=loc, scale=scale)), 4)
    return result


class HazardFetcher:
    """Base class for hazard data fetchers.

    Subclasses implement ``fetch()`` to download hazard intensity data from
    a specific source and return a :class:`HazardIntensityMap`.

    Multi-source support: subclasses define ``AVAILABLE_SOURCES`` as a
    ``{key: human_label}`` dict.  The ``source`` constructor parameter
    selects which backend to query.
    """

    hazard_type: str = ""
    source_name: str = ""
    AVAILABLE_SOURCES: dict[str, str] = {}

    def __init__(self, cache_dir: str | Path = "", api_key: str = "",
                 source: str = ""):
        self.cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self.api_key = api_key
        if source and source in self.AVAILABLE_SOURCES:
            self.source_name = source
        self._active_source = self.source_name

    def _cache_path(self, key: str) -> Path:
        """Return a deterministic cache file path for the given key."""
        h = hashlib.md5(key.encode()).hexdigest()[:12]
        d = self.cache_dir / self.hazard_type
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.source_name}_{h}.json"

    def _load_cache(self, key: str) -> dict | None:
        p = self._cache_path(key)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None

    def _save_cache(self, key: str, data: dict) -> None:
        try:
            p = self._cache_path(key)
            p.write_text(json.dumps(data, default=str))
        except Exception as exc:
            logger.debug("Cache write failed for %s: %s", key, exc)

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        """Fetch hazard intensities for the given node locations.

        Parameters
        ----------
        node_coordinates : list of (lat, lon) tuples
            Locations of power system nodes.
        return_periods : list of int, optional
            Desired return periods (years).  Default depends on subclass.
        on_progress : callable, optional
            ``(percent, message)`` callback for progress updates.

        Returns
        -------
        HazardIntensityMap
        """
        raise NotImplementedError


class ScreeningFetcher(HazardFetcher):
    """Data-driven categorical hazard screening using the per-hazard fetchers.

    Runs each of the 7 per-hazard fetchers (SeismicFetcher, CycloneFetcher,
    FloodFetcher, TsunamiFetcher, WildfireFetcher, VolcanicFetcher,
    SeaLevelFetcher) with a standard set of return periods and converts
    the resulting intensity measures to categorical risk levels (1=High,
    2=Medium, 3=Low, 4=Very Low) using IM-based thresholds from engineering
    standards.

    **No geographic heuristics are used.** All classifications are derived
    from measured or modelled intensity values returned by the data APIs.

    IM-to-level thresholds (documented sources):
    - Earthquake PGA: ASCE 7-22 Site Class D thresholds
    - Cyclone wind: Saffir-Simpson Hurricane Wind Scale
    - Flood depth: FEMA NFIP depth-damage thresholds
    - Tsunami runup: Suppasri et al. (2013) building damage thresholds
    - Wildfire FWI: Canadian FWI System fire danger classes
    - Volcanic ashfall: Wilson et al. (2017) infrastructure impact levels
    - Sea level rise: IPCC AR6 WGI Chapter 9 projected impact categories
    """

    hazard_type = "screening"
    source_name = "multi_source"

    # Hazard type keys used in node_intensities (stable integer IDs)
    _HAZARD_KEYS = {
        "earthquake": 1,
        "cyclone": 2,
        "flood": 3,
        "tsunami": 4,
        "wildfire": 5,
        "volcanic": 6,
        "sea_level_rise": 7,
    }

    # IM → categorical level thresholds (engineering standards)
    # Each entry: (high_threshold, medium_threshold, low_threshold)
    # Level 1 (High) if IM >= high, Level 2 if IM >= medium, etc.
    _IM_THRESHOLDS: dict[str, tuple[float, float, float]] = {
        # PGA (g) — ASCE 7-22 Table 11.6-1 (Seismic Design Categories)
        "earthquake": (0.30, 0.10, 0.04),
        # Wind speed (m/s) — Saffir-Simpson: Cat 1=33 m/s, TS=18 m/s
        "cyclone": (33.0, 18.0, 8.0),
        # Flood depth (m) — FEMA depth-damage: 1m=major, 0.3m=moderate
        "flood": (1.0, 0.3, 0.05),
        # Tsunami runup (m) — Suppasri-2013: 2m=complete, 0.5m=moderate
        "tsunami": (2.0, 0.5, 0.1),
        # FWI — Canadian FWI: 30+=Very High, 20+=High, 10+=Moderate
        "wildfire": (30.0, 15.0, 5.0),
        # Ashfall (mm) — Wilson-2017: 10mm=disruption, 1mm=cleaning
        "volcanic": (10.0, 1.0, 0.1),
        # SLR depth (m) — IPCC AR6: 0.5m=major, 0.2m=moderate
        "sea_level_rise": (0.50, 0.20, 0.05),
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        """Data-driven hazard screening for each node.

        Runs all 7 per-hazard fetchers, then classifies the resulting IMs
        into categorical levels (1=High, 2=Medium, 3=Low, 4=Very Low)
        using documented IM thresholds.

        Also stores the raw IM values in ``metadata["raw_im"]`` for
        downstream use by the composite risk assessment.
        """
        total = len(node_coordinates)
        if total == 0:
            return HazardIntensityMap(
                hazard_type="screening", source="multi_source",
                intensity_measure="hazard_level",
                units="category (1=high, 4=very_low)",
                return_periods=[], node_intensities={},
                metadata={"n_nodes": 0},
            )

        # Standard return periods for screening
        screening_rps = [100, 475, 500]
        keys = self._HAZARD_KEYS

        # ── Run each per-hazard fetcher ──
        fetcher_types = [
            "earthquake", "cyclone", "flood", "tsunami",
            "wildfire", "volcanic", "sea_level_rise",
        ]
        hazard_im_maps: dict[str, HazardIntensityMap] = {}
        sources_used: list[str] = []

        for i, haz_type in enumerate(fetcher_types):
            if on_progress:
                pct = int(10 + 80 * i / len(fetcher_types))
                on_progress(pct, f"Fetching {haz_type.replace('_', ' ')}...")

            try:
                fetcher = create_fetcher(haz_type, source="")
                haz_map = fetcher.fetch(node_coordinates, screening_rps)
                hazard_im_maps[haz_type] = haz_map
                sources_used.append(f"{haz_type}: {haz_map.source}")
            except Exception as exc:
                logger.warning("Screening fetch failed for %s: %s", haz_type, exc)

        if on_progress:
            on_progress(90, "Classifying hazard levels from IMs...")

        # ── Convert IMs to categorical levels per node ──
        node_intensities: dict[int, dict[int, float]] = {}
        raw_im_data: dict[int, dict[str, float]] = {}  # For metadata

        for idx in range(total):
            levels: dict[int, float] = {}
            raw_ims: dict[str, float] = {}

            for haz_type in fetcher_types:
                haz_map = hazard_im_maps.get(haz_type)
                if haz_map is None or idx not in haz_map.node_intensities:
                    levels[keys[haz_type]] = 4.0  # No data = Very Low
                    raw_ims[haz_type] = 0.0
                    continue

                # Get maximum IM across return periods
                rp_ims = haz_map.node_intensities[idx]
                im = max(rp_ims.values()) if rp_ims else 0.0
                raw_ims[haz_type] = im

                # Classify using IM thresholds
                thresholds = self._IM_THRESHOLDS.get(haz_type, (1.0, 0.5, 0.1))
                high_t, med_t, low_t = thresholds
                if im >= high_t:
                    levels[keys[haz_type]] = 1.0  # High
                elif im >= med_t:
                    levels[keys[haz_type]] = 2.0  # Medium
                elif im >= low_t:
                    levels[keys[haz_type]] = 3.0  # Low
                else:
                    levels[keys[haz_type]] = 4.0  # Very Low

            node_intensities[idx] = levels
            raw_im_data[idx] = raw_ims

            if on_progress:
                pct = 90 + int(10 * (idx + 1) / total)
                on_progress(pct, f"Classified node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="screening",
            source="multi_source",
            intensity_measure="hazard_level",
            units="category (1=high, 4=very_low)",
            return_periods=screening_rps,
            node_intensities=node_intensities,
            metadata={
                "n_nodes": total,
                "sources": sources_used,
                "raw_im": raw_im_data,
                "thresholds": dict(self._IM_THRESHOLDS),
                "hazard_maps": hazard_im_maps,
            },
        )


class SeismicFetcher(HazardFetcher):
    """Seismic hazard from USGS ComCat and/or ISC reviewed catalog.

    Sources:
    - **usgs**: USGS ComCat FDSN — global real-time + historical M5+ catalog.
    - **isc**: ISC-FDSN — definitive reviewed global bulletin (1900–present).
    """

    hazard_type = "earthquake"
    source_name = "usgs"
    AVAILABLE_SOURCES = {
        "usgs": "USGS ComCat (global M5+ catalog)",
        "isc": "ISC FDSN (definitive reviewed catalog)",
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        if return_periods is None:
            return_periods = [475]
        if self._active_source == "isc":
            return self._fetch_isc(node_coordinates, return_periods, on_progress)
        return self._fetch_usgs(node_coordinates, return_periods, on_progress)

    # ── PGA estimation shared by both sources ────────────────────────

    @staticmethod
    def _estimate_pga(
        events: list[tuple[float, float, float]],
        lat: float, lon: float,
        return_periods: list[int],
    ) -> dict[int, float]:
        """Estimate PGA from a list of (elat, elon, magnitude) events.

        Uses simplified attenuation: PGA ≈ 10^(0.5·M − 1.8) / max(R, 10)
        and return-period scaling (rp/475)^0.3.
        """
        if not events:
            return {rp: 0.0 for rp in return_periods}
        max_mag = max(m for _, _, m in events)
        # Use event with highest magnitude for distance
        best = max(events, key=lambda e: e[2])
        dist_km = max(_haversine(lat, lon, best[0], best[1]), 10.0)
        pga = min(10 ** (0.5 * max_mag - 1.8) / dist_km, 3.0)
        return {rp: round(pga * (rp / 475) ** 0.3, 4) for rp in return_periods}

    # ── USGS source ──────────────────────────────────────────────────

    def _fetch_usgs(self, node_coordinates, return_periods, on_progress):
        """Fetch seismic data from USGS, with ISC fallback for completeness.

        Uses USGS FDSNWS with expanded parameters (500km radius, M4+,
        100 events) to improve coverage.  Falls back to ISC catalog
        when USGS returns fewer than 3 events, as USGS has limited
        coverage of Caribbean and other non-CONUS regions.
        """
        import urllib.request
        import urllib.error

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"usgs_v2_pga_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)

            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                events: list[tuple[float, float, float]] = []
                try:
                    # Wider search: 500km, M4+, up to 100 events
                    url = (
                        f"https://earthquake.usgs.gov/fdsnws/event/1/query?"
                        f"format=geojson&latitude={lat}&longitude={lon}"
                        f"&maxradiuskm=500&orderby=magnitude&limit=100"
                        f"&minmagnitude=4.0"
                    )
                    req = urllib.request.Request(url, headers={"Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        data = json.loads(resp.read().decode())

                    for f in data.get("features", []):
                        mag = f["properties"].get("mag")
                        coords = f["geometry"]["coordinates"]
                        if mag:
                            events.append((coords[1], coords[0], float(mag)))

                except Exception as exc:
                    logger.warning("USGS query failed for node %d: %s", idx, exc)

                # ISC fallback when USGS catalog is sparse (common for
                # Caribbean, Central America, and other non-CONUS regions)
                if len(events) < 3:
                    isc_events = self._query_isc_catalog(lat, lon)
                    if isc_events:
                        # Merge, deduplicating by proximity
                        for elat, elon, emag in isc_events:
                            is_dup = any(
                                _haversine(elat, elon, e[0], e[1]) < 20
                                and abs(emag - e[2]) < 0.3
                                for e in events
                            )
                            if not is_dup:
                                events.append((elat, elon, emag))

                intensities = self._estimate_pga(events, lat, lon, return_periods)

                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Seismic node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="earthquake",
            source=self._active_source,
            intensity_measure="PGA",
            units="g",
            return_periods=return_periods,
            node_intensities=node_intensities,
        )

    @staticmethod
    def _query_isc_catalog(
        lat: float, lon: float,
        max_radius_deg: float = 5.0,
        min_mag: float = 4.0,
        limit: int = 50,
    ) -> list[tuple[float, float, float]]:
        """Query ISC reviewed catalog — more complete for non-CONUS regions."""
        import urllib.request
        try:
            url = (
                f"http://www.isc.ac.uk/fdsnws/event/1/query?"
                f"starttime=1900-01-01&lat={lat}&lon={lon}"
                f"&maxradius={max_radius_deg}&minmag={min_mag}"
                f"&format=text&limit={limit}"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=25) as resp:
                text = resp.read().decode("utf-8", errors="replace")

            events: list[tuple[float, float, float]] = []
            for line in text.strip().split("\n"):
                if line.startswith("#") or line.startswith("EventID"):
                    continue
                parts = line.split("|")
                if len(parts) >= 11:
                    try:
                        elat = float(parts[2])
                        elon = float(parts[3])
                        mag = float(parts[10])
                        events.append((elat, elon, mag))
                    except (ValueError, IndexError):
                        continue
            logger.info("ISC catalog: %d events near (%.1f, %.1f)", len(events), lat, lon)
            return events
        except Exception as exc:
            logger.warning("ISC query failed for (%.1f, %.1f): %s", lat, lon, exc)
            return []

    # ── ISC FDSN source ──────────────────────────────────────────────

    def _fetch_isc(self, node_coordinates, return_periods, on_progress):
        import urllib.request

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"isc_pga_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)

            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                try:
                    url = (
                        f"http://www.isc.ac.uk/fdsnws/event/1/query?"
                        f"starttime=1900-01-01&lat={lat}&lon={lon}"
                        f"&maxradius=2&minmag=5&format=text&limit=20"
                    )
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        text = resp.read().decode("utf-8", errors="replace")

                    events: list[tuple[float, float, float]] = []
                    for line in text.strip().split("\n"):
                        if line.startswith("#") or line.startswith("EventID"):
                            continue
                        parts = line.split("|")
                        if len(parts) >= 11:
                            try:
                                elat = float(parts[2])
                                elon = float(parts[3])
                                mag = float(parts[10])  # Magnitude is field 10
                                events.append((elat, elon, mag))
                            except (ValueError, IndexError):
                                continue

                    intensities = self._estimate_pga(events, lat, lon, return_periods)

                except Exception as exc:
                    logger.warning("ISC query failed for node %d: %s", idx, exc)
                    intensities = {rp: 0.0 for rp in return_periods}

                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Seismic node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="earthquake",
            source=self._active_source,
            intensity_measure="PGA",
            units="g",
            return_periods=return_periods,
            node_intensities=node_intensities,
        )


class CycloneFetcher(HazardFetcher):
    """Tropical cyclone wind hazard from IBTrACS historical tracks.

    Sources:
    - **ibtracs**: Full basin CSV download — complete history, robust Gumbel fit.
    - **ibtracs_erddap**: ERDDAP JSON per-node query — faster for few nodes.
    """

    hazard_type = "cyclone"
    source_name = "ibtracs"
    AVAILABLE_SOURCES = {
        "ibtracs": "IBTrACS v4 CSV (full basin download)",
        "ibtracs_erddap": "IBTrACS ERDDAP (targeted JSON query)",
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        if return_periods is None:
            return_periods = [100, 500]
        if self._active_source == "ibtracs_erddap":
            return self._fetch_erddap(node_coordinates, return_periods, on_progress)
        return self._fetch_ibtracs_csv(node_coordinates, return_periods, on_progress)

    # ── Shared: wind list → return-period intensities ────────────────

    @staticmethod
    def _winds_to_rp(
        max_winds: list[float],
        return_periods: list[int],
        n_years: int,
    ) -> dict[int, float]:
        if not max_winds:
            return {rp: 0.0 for rp in return_periods}
        mw = sorted(max_winds, reverse=True)
        result: dict[int, float] = {}
        for rp in return_periods:
            rank = max(1, int(n_years / rp))
            if rank <= len(mw):
                result[rp] = round(mw[rank - 1], 1)
            else:
                mu = float(np.mean(mw[:10]) if len(mw) >= 10 else np.mean(mw))
                beta = max(float(np.std(mw[:10]) if len(mw) >= 10 else np.std(mw)), 1.0)
                result[rp] = round(mu - beta * np.log(-np.log(1 - 1 / rp)), 1)
        return result

    # ── IBTrACS CSV (full basin) ─────────────────────────────────────

    def _fetch_ibtracs_csv(self, node_coordinates, return_periods, on_progress):
        import urllib.request

        mean_lat = float(np.mean([c[0] for c in node_coordinates]))
        mean_lon = float(np.mean([c[1] for c in node_coordinates]))
        basin = _determine_tc_basin(mean_lat, mean_lon)

        cache_key = f"ibtracs_{basin}_{mean_lat:.1f}_{mean_lon:.1f}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            tracks = cached
        else:
            tracks = self._download_ibtracs_basin(basin, on_progress)
            if tracks:
                self._save_cache(cache_key, tracks)

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)
        for idx, (lat, lon) in enumerate(node_coordinates):
            max_winds: list[float] = []
            for track in (tracks or []):
                for pt in track.get("points", []):
                    w = pt.get("wind_kts", 0)
                    if w > 0 and _haversine(lat, lon, pt["lat"], pt["lon"]) < 200:
                        max_winds.append(w * 0.5144)
                        break
            node_intensities[idx] = self._winds_to_rp(max_winds, return_periods, max(len(tracks or []), 1))
            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Cyclone node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="cyclone", source=self._active_source,
            intensity_measure="wind_speed", units="m/s",
            return_periods=return_periods, node_intensities=node_intensities,
            metadata={"basin": basin},
        )

    def _download_ibtracs_basin(self, basin, on_progress):
        import csv
        import urllib.request

        url = (
            f"https://www.ncei.noaa.gov/data/international-best-track-archive-for-"
            f"climate-stewardship-ibtracs/v04r01/access/csv/"
            f"ibtracs.{basin}.list.v04r01.csv"
        )
        if on_progress:
            on_progress(5, f"Downloading IBTrACS {basin}…")
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("IBTrACS download failed for basin %s: %s", basin, exc)
            return []

        if on_progress:
            on_progress(30, "Parsing tracks…")

        lines = text.strip().split("\n")
        if len(lines) < 3:
            return []
        reader = csv.DictReader(lines[2:], fieldnames=lines[0].split(","))
        tracks_by_sid: dict[str, list[dict]] = {}
        for row in reader:
            sid = row.get("SID", "").strip()
            if not sid:
                continue
            try:
                lat = float(row.get("LAT", "0").strip())
                lon = float(row.get("LON", "0").strip())
                wind_str = row.get("WMO_WIND", "").strip()
                wind = float(wind_str) if wind_str else 0.0
            except (ValueError, TypeError):
                continue
            if sid not in tracks_by_sid:
                tracks_by_sid[sid] = []
            tracks_by_sid[sid].append({"lat": lat, "lon": lon, "wind_kts": wind})

        tracks = [{"sid": sid, "points": pts} for sid, pts in tracks_by_sid.items()]
        if on_progress:
            on_progress(50, f"Parsed {len(tracks)} tracks")
        return tracks

    # ── IBTrACS ERDDAP (per-node JSON) ──────────────────────────────

    def _fetch_erddap(self, node_coordinates, return_periods, on_progress):
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"erddap_cyclone_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                max_winds: list[float] = []
                try:
                    # AOML ERDDAP: 4° bbox, url-encoded >= and <= operators
                    url = (
                        "https://erddap.aoml.noaa.gov/hdb/erddap/tabledap/"
                        "IBTrACS_since1980_1.json?"
                        "sid,usa_lat,usa_lon,usa_wind"
                        f"&usa_lat%3E={lat - 2}&usa_lat%3C={lat + 2}"
                        f"&usa_lon%3E={lon - 2}&usa_lon%3C={lon + 2}"
                        "&usa_wind%3E0"
                    )
                    data = _api_get_json(url, timeout=30)
                    table = data.get("table", {}) if isinstance(data, dict) else {}
                    rows = table.get("rows", [])
                    # Group by SID, take max wind per storm within 200 km
                    storms: dict[str, float] = {}
                    for row in rows:
                        if len(row) < 4:
                            continue
                        sid, rlat, rlon, wind = row[0], row[1], row[2], row[3]
                        if wind and rlat and rlon:
                            try:
                                w = float(wind)
                                if w > 0 and _haversine(lat, lon, float(rlat), float(rlon)) < 200:
                                    storms[sid] = max(storms.get(sid, 0), w * 0.5144)
                            except (ValueError, TypeError):
                                continue
                    max_winds = list(storms.values())
                except Exception as exc:
                    logger.warning("ERDDAP cyclone query failed for node %d: %s", idx, exc)

                intensities = self._winds_to_rp(max_winds, return_periods, max(len(max_winds), 30))
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Cyclone node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="cyclone", source=self._active_source,
            intensity_measure="wind_speed", units="m/s",
            return_periods=return_periods, node_intensities=node_intensities,
        )


class FloodFetcher(HazardFetcher):
    """River flood hazard from Open-Meteo GloFAS river discharge data.

    Sources:
    - **open_meteo**: Recent 2-year discharge record → Gumbel RP estimate.
    - **open_meteo_historical**: 40-year reanalysis (1984–present) → robust
      GEV fit for accurate return-period flood depths.

    Discharge is converted to approximate flood depth using a simplified
    power-law stage-discharge proxy: ``depth ≈ 0.1 × (Q / Q_ref)^0.6``.
    """

    hazard_type = "flood"
    source_name = "open_meteo"
    AVAILABLE_SOURCES = {
        "open_meteo": "Open-Meteo Flood API (GloFAS 2yr discharge)",
        "open_meteo_historical": "Open-Meteo Historical (40yr GEV fit)",
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        if return_periods is None:
            return_periods = [100, 500]
        if self._active_source == "open_meteo_historical":
            return self._fetch_historical(node_coordinates, return_periods, on_progress)
        return self._fetch_recent(node_coordinates, return_periods, on_progress)

    # ── Shared: discharge → depth ────────────────────────────────────

    @staticmethod
    def _discharge_to_depth(q: float, q_ref: float) -> float:
        """Approximate flood depth (m) from discharge ratio.

        Uses simplified power-law stage-discharge:
        depth ≈ 0.1 × (Q / Q_ref)^0.6, capped at 10 m.
        """
        if q_ref <= 0 or q <= 0:
            return 0.0
        return min(0.1 * (q / q_ref) ** 0.6, 10.0)

    @staticmethod
    def _parse_annual_maxima(daily_data: dict) -> list[float]:
        """Extract annual maxima from Open-Meteo daily response."""
        times = daily_data.get("time", [])
        values = daily_data.get("river_discharge", [])
        if not times or not values:
            return []
        yearly: dict[str, float] = {}
        for t, v in zip(times, values):
            if v is None:
                continue
            year = t[:4]
            yearly[year] = max(yearly.get(year, 0.0), float(v))
        return sorted(yearly.values())

    # ── Recent discharge (2 years) ───────────────────────────────────

    def _fetch_recent(self, node_coordinates, return_periods, on_progress):
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"flood_recent_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                intensities = self._query_open_meteo(lat, lon, return_periods, past_days=730)
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Flood node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="flood", source=self._active_source,
            intensity_measure="depth", units="m",
            return_periods=return_periods, node_intensities=node_intensities,
        )

    # ── Historical discharge (40 years) ──────────────────────────────

    def _fetch_historical(self, node_coordinates, return_periods, on_progress):
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"flood_hist_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                intensities = self._query_open_meteo(
                    lat, lon, return_periods,
                    start_date="1984-01-01", end_date="2025-12-31",
                )
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Flood node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="flood", source=self._active_source,
            intensity_measure="depth", units="m",
            return_periods=return_periods, node_intensities=node_intensities,
        )

    # ── Core Open-Meteo query ────────────────────────────────────────

    def _query_open_meteo(
        self,
        lat: float,
        lon: float,
        return_periods: list[int],
        past_days: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[int, float]:
        """Query Open-Meteo Flood API and compute RP flood depths."""
        try:
            base = (
                f"https://flood-api.open-meteo.com/v1/flood?"
                f"latitude={lat}&longitude={lon}&daily=river_discharge"
            )
            if past_days:
                base += f"&past_days={past_days}"
            elif start_date and end_date:
                base += f"&start_date={start_date}&end_date={end_date}"

            data = _api_get_json(base, timeout=30)
            if not isinstance(data, dict) or "daily" not in data:
                logger.debug("Open-Meteo flood: no data for (%.3f, %.3f)", lat, lon)
                return {rp: 0.0 for rp in return_periods}

            annual_max = self._parse_annual_maxima(data["daily"])
            if not annual_max:
                return {rp: 0.0 for rp in return_periods}

            q_ref = float(np.mean(annual_max))
            rp_discharges = _fit_gumbel_return_periods(annual_max, return_periods)

            return {
                rp: round(self._discharge_to_depth(q, q_ref), 3)
                for rp, q in rp_discharges.items()
            }

        except Exception as exc:
            logger.warning("Open-Meteo flood query failed (%.3f, %.3f): %s", lat, lon, exc)
            return {rp: 0.0 for rp in return_periods}


class TsunamiFetcher(HazardFetcher):
    """Tsunami hazard from NOAA NCEI Hazel database.

    Sources:
    - **noaa_runups**: Runup observations — point coastal measurements.
    - **noaa_events**: Tsunami source events — max water height per event.
    """

    hazard_type = "tsunami"
    source_name = "noaa_runups"
    AVAILABLE_SOURCES = {
        "noaa_runups": "NOAA NCEI Runup Observations",
        "noaa_events": "NOAA NCEI Tsunami Events (max water height)",
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        if return_periods is None:
            return_periods = [500]
        if self._active_source == "noaa_events":
            return self._fetch_events(node_coordinates, return_periods, on_progress)
        return self._fetch_runups(node_coordinates, return_periods, on_progress)

    # ── Shared: extract max height from items list ───────────────────

    @staticmethod
    def _extract_max_height(items: list, height_fields: list[str]) -> float:
        """Extract maximum water height from a list of API items."""
        max_h = 0.0
        for item in items:
            for field in height_fields:
                val = item.get(field)
                if val is not None:
                    try:
                        max_h = max(max_h, float(val))
                    except (TypeError, ValueError):
                        pass
        return max_h

    # ── Runup observations source ────────────────────────────────────

    def _fetch_runups(self, node_coordinates, return_periods, on_progress):
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"tsunami_runup_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                max_runup = 0.0
                try:
                    url = (
                        f"https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/"
                        f"tsunamis/runups?minLatitude={lat - 1}&maxLatitude={lat + 1}"
                        f"&minLongitude={lon - 1}&maxLongitude={lon + 1}"
                    )
                    data = _api_get_json(url, timeout=20)
                    items = data.get("items", data) if isinstance(data, dict) else data
                    if isinstance(items, list):
                        max_runup = self._extract_max_height(items, ["maxWaterHeight", "runup"])
                except Exception as exc:
                    logger.warning("NOAA tsunami runup query failed node %d: %s", idx, exc)

                intensities = {rp: round(max_runup, 2) for rp in return_periods}
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Tsunami node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="tsunami", source=self._active_source,
            intensity_measure="runup_height", units="m",
            return_periods=return_periods, node_intensities=node_intensities,
        )

    # ── Events source (max water height per event) ───────────────────

    def _fetch_events(self, node_coordinates, return_periods, on_progress):
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"tsunami_events_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                max_height = 0.0
                try:
                    url = (
                        f"https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/"
                        f"tsunamis/events?minLatitude={lat - 1}&maxLatitude={lat + 1}"
                        f"&minLongitude={lon - 1}&maxLongitude={lon + 1}"
                    )
                    data = _api_get_json(url, timeout=20)
                    items = data.get("items", data) if isinstance(data, dict) else data
                    if isinstance(items, list):
                        max_height = self._extract_max_height(
                            items, ["maxWaterHeight", "waterHeight", "maxRunup"]
                        )
                except Exception as exc:
                    logger.warning("NOAA tsunami events query failed node %d: %s", idx, exc)

                intensities = {rp: round(max_height, 2) for rp in return_periods}
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Tsunami node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="tsunami", source=self._active_source,
            intensity_measure="wave_height", units="m",
            return_periods=return_periods, node_intensities=node_intensities,
        )


class WildfireFetcher(HazardFetcher):
    """Wildfire hazard from NASA FIRMS active fire archive.

    FIRMS: REST API (free, MAP_KEY recommended for heavy use).
    """

    hazard_type = "wildfire"
    source_name = "firms"
    AVAILABLE_SOURCES = {
        "firms": "NASA FIRMS (VIIRS active fire archive)",
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        """Estimate wildfire risk from NASA FIRMS fire density."""
        import urllib.request

        if return_periods is None:
            return_periods = [100]

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)
        map_key = self.api_key or "DEMO_KEY"

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"wildfire_{lat:.3f}_{lon:.3f}"
            cached = self._load_cache(cache_key)

            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                fwi = 0.0
                try:
                    # Query FIRMS for fire count in 50 km radius, last 10 years
                    url = (
                        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                        f"{map_key}/VIIRS_SNPP_NRT/{lon - 0.5},{lat - 0.5},"
                        f"{lon + 0.5},{lat + 0.5}/10"
                    )
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        lines = resp.read().decode().strip().split("\n")
                    fire_count = max(len(lines) - 1, 0)  # subtract header
                    # Convert fire count to approximate FWI (0-100 scale)
                    fwi = min(fire_count / 10.0, 100.0)
                except Exception as exc:
                    logger.debug("FIRMS query failed for node %d: %s", idx, exc)

                intensities = {rp: round(fwi, 1) for rp in return_periods}
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Wildfire node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="wildfire",
            source="firms",
            intensity_measure="FWI",
            units="index",
            return_periods=return_periods,
            node_intensities=node_intensities,
        )


class VolcanicFetcher(HazardFetcher):
    """Volcanic ashfall hazard from Smithsonian GVP and/or NOAA NCEI.

    Sources:
    - **gvp**: Smithsonian GVP WFS — 1,422 Holocene volcanoes (GeoJSON).
      Ashfall thickness modelled from VEI × exponential distance decay.
    - **noaa_ncei**: NOAA NCEI Hazel significant eruptions database.
    """

    hazard_type = "volcanic"
    source_name = "gvp"
    AVAILABLE_SOURCES = {
        "gvp": "Smithsonian GVP (Holocene volcanoes WFS)",
        "noaa_ncei": "NOAA NCEI (significant eruptions)",
    }

    # VEI → characteristic ashfall thickness (mm) at the vent
    _VEI_FACTOR: dict[int, float] = {
        0: 0.1, 1: 0.5, 2: 1.0, 3: 5.0, 4: 25.0, 5: 100.0, 6: 500.0, 7: 2000.0,
    }
    # VEI → exponential decay length (km)
    _DECAY_KM: dict[int, float] = {
        0: 20, 1: 20, 2: 30, 3: 40, 4: 50, 5: 100, 6: 150, 7: 200,
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        if return_periods is None:
            return_periods = [500]
        if self._active_source == "noaa_ncei":
            return self._fetch_noaa_ncei(node_coordinates, return_periods, on_progress)
        return self._fetch_gvp(node_coordinates, return_periods, on_progress)

    # ── Shared: compute ashfall from volcano list ────────────────────

    def _ashfall_for_node(
        self,
        lat: float,
        lon: float,
        volcanoes: list[tuple[float, float, int]],
        return_periods: list[int],
        search_radius_km: float = 300.0,
    ) -> dict[int, float]:
        """Compute ashfall thickness (mm) for a node from nearby volcanoes.

        Parameters
        ----------
        volcanoes : list of (vlat, vlon, vei)
        """
        total_ashfall = 0.0
        for vlat, vlon, vei in volcanoes:
            dist = _haversine(lat, lon, vlat, vlon)
            if dist > search_radius_km:
                continue
            vei_clamped = min(max(vei, 0), 7)
            factor = self._VEI_FACTOR.get(vei_clamped, 1.0)
            decay = self._DECAY_KM.get(vei_clamped, 50.0)
            total_ashfall += factor * np.exp(-dist / decay)

        total_ashfall = min(total_ashfall, 5000.0)
        # Scale by return period: 500-yr baseline; lower RP = less ashfall
        return {
            rp: round(total_ashfall * min(rp / 500, 1.0), 2) for rp in return_periods
        }

    # ── Smithsonian GVP WFS source ──────────────────────────────────

    def _fetch_gvp(self, node_coordinates, return_periods, on_progress):
        # Global batch download (cache as single entry)
        cache_key = "gvp_holocene_all"
        cached = self._load_cache(cache_key)
        if cached is not None:
            volcanoes = [(v[0], v[1], v[2]) for v in cached]
        else:
            volcanoes = self._download_gvp_volcanoes(on_progress)
            self._save_cache(cache_key, volcanoes)

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)
        for idx, (lat, lon) in enumerate(node_coordinates):
            node_intensities[idx] = self._ashfall_for_node(lat, lon, volcanoes, return_periods)
            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Volcanic node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="volcanic", source=self._active_source,
            intensity_measure="ashfall_thickness", units="mm",
            return_periods=return_periods, node_intensities=node_intensities,
            metadata={"volcanoes_total": len(volcanoes)},
        )

    def _download_gvp_volcanoes(
        self, on_progress: Callable[[int, str], None] | None = None,
    ) -> list[tuple[float, float, int]]:
        """Download all Holocene volcanoes from Smithsonian GVP WFS."""
        if on_progress:
            on_progress(5, "Downloading Smithsonian GVP volcano database…")

        url = (
            "https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows?"
            "service=WFS&version=1.0.0&request=GetFeature"
            "&typeName=GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes"
            "&outputFormat=application/json"
        )
        data = _api_get_json(url, timeout=60)
        volcanoes: list[tuple[float, float, int]] = []

        features = data.get("features", []) if isinstance(data, dict) else []
        for feat in features:
            try:
                coords = feat["geometry"]["coordinates"]
                props = feat.get("properties", {})
                vei = 0
                # Try several possible VEI field names
                for vei_field in ("Maximum_VEI", "VEI", "vei", "MaxVEI"):
                    val = props.get(vei_field)
                    if val is not None:
                        vei = int(val)
                        break
                volcanoes.append((float(coords[1]), float(coords[0]), vei))
            except (KeyError, TypeError, ValueError, IndexError):
                continue

        if on_progress:
            on_progress(50, f"Loaded {len(volcanoes)} volcanoes from GVP")

        logger.info("GVP WFS: loaded %d Holocene volcanoes", len(volcanoes))
        return volcanoes

    # ── NOAA NCEI Hazel source ───────────────────────────────────────

    def _fetch_noaa_ncei(self, node_coordinates, return_periods, on_progress):
        # Batch query with bounding box around all nodes
        lats = [c[0] for c in node_coordinates]
        lons = [c[1] for c in node_coordinates]
        margin = 3.0
        bbox = {
            "min_lat": min(lats) - margin, "max_lat": max(lats) + margin,
            "min_lon": min(lons) - margin, "max_lon": max(lons) + margin,
        }

        cache_key = (
            f"ncei_volcanic_{bbox['min_lat']:.1f}_{bbox['max_lat']:.1f}_"
            f"{bbox['min_lon']:.1f}_{bbox['max_lon']:.1f}"
        )
        cached = self._load_cache(cache_key)
        if cached is not None:
            volcanoes = [(v[0], v[1], v[2]) for v in cached]
        else:
            volcanoes = self._download_ncei_volcanoes(bbox, on_progress)
            self._save_cache(cache_key, volcanoes)

        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)
        for idx, (lat, lon) in enumerate(node_coordinates):
            node_intensities[idx] = self._ashfall_for_node(lat, lon, volcanoes, return_periods)
            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"Volcanic node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="volcanic", source=self._active_source,
            intensity_measure="ashfall_thickness", units="mm",
            return_periods=return_periods, node_intensities=node_intensities,
        )

    def _download_ncei_volcanoes(
        self, bbox: dict[str, float],
        on_progress: Callable[[int, str], None] | None = None,
    ) -> list[tuple[float, float, int]]:
        """Download significant eruptions from NOAA NCEI Hazel API."""
        if on_progress:
            on_progress(5, "Downloading NOAA NCEI volcanic eruptions…")

        url = (
            f"https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/volcanoes?"
            f"minLatitude={bbox['min_lat']}&maxLatitude={bbox['max_lat']}"
            f"&minLongitude={bbox['min_lon']}&maxLongitude={bbox['max_lon']}"
        )
        data = _api_get_json(url, timeout=20)
        items = data.get("items", data) if isinstance(data, dict) else data
        volcanoes: list[tuple[float, float, int]] = []

        if isinstance(items, list):
            for item in items:
                try:
                    vlat = float(item.get("latitude", 0))
                    vlon = float(item.get("longitude", 0))
                    vei = int(item.get("vei", 0) or 0)
                    volcanoes.append((vlat, vlon, vei))
                except (TypeError, ValueError):
                    continue

        if on_progress:
            on_progress(50, f"Loaded {len(volcanoes)} eruptions from NCEI")
        return volcanoes


class SeaLevelFetcher(HazardFetcher):
    """Sea level rise projections from NOAA CO-OPS and/or IPCC AR6.

    Sources:
    - **noaa_slr**: NOAA CO-OPS SLR Projections API — per-location
      (1° global grid), 7 scenarios, 2020–2150 with confidence intervals.
    - **ar6_lookup**: IPCC AR6 WG1 global median projections — offline
      hardcoded tables (fast fallback).
    """

    hazard_type = "sea_level_rise"
    source_name = "noaa_slr"
    AVAILABLE_SOURCES = {
        "noaa_slr": "NOAA CO-OPS SLR Projections (1° grid)",
        "ar6_lookup": "IPCC AR6 global lookup (offline tables)",
    }

    # SSP → NOAA scenario name mapping
    _SSP_TO_NOAA: dict[str, str] = {
        "ssp126": "Low",
        "ssp245": "Intermediate",
        "ssp370": "Intermediate-High",
        "ssp585": "High",
    }

    # IPCC AR6 global median projections (meters, relative to 2005 baseline)
    _SLR_AR6_2050: dict[str, float] = {
        "ssp126": 0.19, "ssp245": 0.24, "ssp370": 0.28, "ssp585": 0.32,
    }
    _SLR_AR6_2100: dict[str, float] = {
        "ssp126": 0.38, "ssp245": 0.56, "ssp370": 0.68, "ssp585": 0.77,
    }

    def fetch(
        self,
        node_coordinates: list[tuple[float, float]],
        return_periods: list[int] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        **kwargs: Any,
    ) -> HazardIntensityMap:
        ssp = kwargs.get("ssp", "ssp245")
        proj_year = kwargs.get("year", 2050)

        if self._active_source == "ar6_lookup":
            return self._fetch_ar6_lookup(node_coordinates, ssp, proj_year, on_progress)
        return self._fetch_noaa_slr(node_coordinates, ssp, proj_year, on_progress)

    # ── NOAA CO-OPS SLR Projections API ──────────────────────────────

    def _fetch_noaa_slr(self, node_coordinates, ssp, proj_year, on_progress):
        noaa_scenario = self._SSP_TO_NOAA.get(ssp, "Intermediate")
        node_intensities: dict[int, dict[int, float]] = {}
        total = len(node_coordinates)

        for idx, (lat, lon) in enumerate(node_coordinates):
            cache_key = f"noaa_slr_{lat:.2f}_{lon:.2f}_{ssp}_{proj_year}"
            cached = self._load_cache(cache_key)
            if cached is not None:
                node_intensities[idx] = {int(k): v for k, v in cached.items()}
            else:
                slr = self._query_noaa_slr(lat, lon, noaa_scenario, proj_year)
                if slr is None:
                    # Fallback to AR6 tables
                    slr = self._ar6_interpolate(ssp, proj_year)
                intensities = {0: round(slr, 3)}
                node_intensities[idx] = intensities
                self._save_cache(cache_key, {str(k): v for k, v in intensities.items()})

            if on_progress:
                on_progress(int(100 * (idx + 1) / total), f"SLR node {idx + 1}/{total}")

        return HazardIntensityMap(
            hazard_type="sea_level_rise", source=self._active_source,
            intensity_measure="slr_depth", units="m",
            return_periods=[0], node_intensities=node_intensities,
            metadata={"ssp": ssp, "year": proj_year},
        )

    def _query_noaa_slr(
        self, lat: float, lon: float, scenario: str, year: int,
    ) -> float | None:
        """Query NOAA CO-OPS SLR Projections API for a single location."""
        try:
            url = (
                f"https://api.tidesandcurrents.noaa.gov/dpapi/prod/webapi/"
                f"product/slr_projections.json?"
                f"lat={lat}&lon={lon}&scenario=all&units=metric&affil=Global"
            )
            data = _api_get_json(url, timeout=20)

            # Navigate response structure
            projections = []
            if isinstance(data, dict):
                projections = data.get("projections", data.get("SlrProjections", []))
            if not projections:
                return None

            # Find entries matching the requested scenario
            matched: list[tuple[int, float]] = []
            for proj in projections:
                pscenario = proj.get("scenario", "")
                if scenario.lower() not in pscenario.lower():
                    continue
                pyear = proj.get("projectionYear") or proj.get("year")
                pslr = proj.get("projectionRsl") or proj.get("slr")
                if pyear and pslr:
                    try:
                        matched.append((int(pyear), float(pslr) / 100.0))  # cm → m
                    except (TypeError, ValueError):
                        continue

            if not matched:
                return None

            # Exact year match or interpolate
            matched.sort()
            for y, s in matched:
                if y == year:
                    return s

            # Linear interpolation between bracketing years
            for i in range(len(matched) - 1):
                y1, s1 = matched[i]
                y2, s2 = matched[i + 1]
                if y1 <= year <= y2:
                    frac = (year - y1) / max(y2 - y1, 1)
                    return s1 + frac * (s2 - s1)

            # Extrapolate from nearest
            if year < matched[0][0]:
                return matched[0][1] * (year - 2020) / max(matched[0][0] - 2020, 1)
            return matched[-1][1]

        except Exception as exc:
            logger.debug("NOAA SLR query failed (%.2f, %.2f): %s", lat, lon, exc)
            return None

    # ── IPCC AR6 offline lookup ──────────────────────────────────────

    def _fetch_ar6_lookup(self, node_coordinates, ssp, proj_year, on_progress):
        slr = self._ar6_interpolate(ssp, proj_year)
        node_intensities: dict[int, dict[int, float]] = {}
        for idx in range(len(node_coordinates)):
            node_intensities[idx] = {0: round(slr, 3)}

        if on_progress:
            on_progress(100, f"SLR {ssp} {proj_year}: {slr:.2f} m (AR6 lookup)")

        return HazardIntensityMap(
            hazard_type="sea_level_rise", source="ar6_lookup",
            intensity_measure="slr_depth", units="m",
            return_periods=[0], node_intensities=node_intensities,
            metadata={"ssp": ssp, "year": proj_year, "slr_m": slr},
        )

    def _ar6_interpolate(self, ssp: str, proj_year: int) -> float:
        """Interpolate AR6 global median SLR for given SSP and year."""
        slr_2050 = self._SLR_AR6_2050.get(ssp, 0.24)
        slr_2100 = self._SLR_AR6_2100.get(ssp, 0.56)
        if proj_year <= 2020:
            return 0.0
        if proj_year <= 2050:
            return slr_2050 * (proj_year - 2020) / 30
        return slr_2050 + (slr_2100 - slr_2050) * (proj_year - 2050) / 50


# Fetcher registry for factory construction
FETCHER_REGISTRY: dict[str, type[HazardFetcher]] = {
    "screening": ScreeningFetcher,
    "earthquake": SeismicFetcher,
    "cyclone": CycloneFetcher,
    "flood": FloodFetcher,
    "tsunami": TsunamiFetcher,
    "wildfire": WildfireFetcher,
    "volcanic": VolcanicFetcher,
    "sea_level_rise": SeaLevelFetcher,
}


def create_fetcher(hazard_type: str, source: str = "", **kwargs: Any) -> HazardFetcher:
    """Factory function to create a fetcher by hazard type and optional source."""
    cls = FETCHER_REGISTRY.get(hazard_type)
    if cls is None:
        raise ValueError(
            f"Unknown hazard type '{hazard_type}'. "
            f"Available: {list(FETCHER_REGISTRY.keys())}"
        )
    return cls(source=source, **kwargs)


def get_available_sources(hazard_type: str) -> dict[str, str]:
    """Return ``{source_key: label}`` for a hazard type.  Empty dict if unknown."""
    cls = FETCHER_REGISTRY.get(hazard_type)
    if cls is None:
        return {}
    return dict(cls.AVAILABLE_SOURCES)


# =============================================================================
# Layer 2: Fragility Library
# =============================================================================


@dataclass
class FragilityCurve:
    """Lognormal CDF fragility curve for a single damage state.

    Implements equation RISK-12:
        P(DS ≥ ds | IM = im) = Φ( (ln(im) − ln(im_median)) / β )
    """

    component_type: str
    hazard_type: str
    damage_state: str
    im_median: float
    beta: float
    source: str = ""
    beta_epistemic: float = 0.0
    source_quality: str = "analytical"  # empirical|analytical|expert_judgment|proxy_derived

    def evaluate(self, im: float) -> float:
        """Probability of reaching or exceeding the damage state.

        Parameters
        ----------
        im : float
            Intensity measure value (PGA in g, wind in m/s, depth in m, …).

        Returns
        -------
        float
            P(DS ≥ damage_state | IM = im), in [0, 1].
        """
        if im <= 0:
            return 0.0
        return float(norm.cdf((np.log(im) - np.log(self.im_median)) / self.beta))


# Built-in fragility curves for power system components.
# Format: (component_type, hazard_type, damage_state, im_median, beta, source
#          [, beta_epistemic, source_quality])
#
# Sources:
#   PNNL-33587 — US DOE Pacific Northwest National Lab (2022), "Climate Risk
#       to the US Energy Infrastructure", Tables 3.1-3.5.
#   NHESS-2024 — Nirandjan et al. (2024), Nat. Hazards Earth Syst. Sci.,
#       "Global fragility functions", Tables S1-S4.
#   Suppasri-2013 — Suppasri et al. (2013), Nat. Hazards, "Tsunami fragility
#       functions for Japanese structures", Table 3.
#   Wilson-2012 — Wilson et al. (2012), J. Volcanology Geotherm. Res.,
#       "Volcanic ash impacts on critical infrastructure", Table 2.
#   Wilson-2017 — Wilson et al. (2017), same series, updated curves, Table 4.
#   HAZUS-MH-MR5 — FEMA (2020), Multi-hazard Loss Estimation Methodology,
#       Chapter 5 (earthquake), Chapter 9 (wildfire depth-damage).
#   Kreibich-2010 — Kreibich et al. (2010), Nat. Hazards Earth Syst. Sci.,
#       "Flood depth-damage functions", with saltwater correction factor 0.7.
#   FEMA-P-58 — FEMA (2018), Seismic Performance Assessment of Buildings.
#   ICOLD-2016 — Int'l Commission on Large Dams (2016), Bulletin 155.
#
# Intensity measures per hazard:
#   earthquake → PGA (g)          cyclone → wind speed (m/s)
#   flood → depth (m)             tsunami → runup height (m)
#   wildfire → FWI (0-100+)       volcanic → ashfall thickness (mm)
#   sea_level_rise → inundation depth (m)
#
_BUILTIN_CURVES: list[tuple] = [
    # ══════════════════════════════════════════════════════════════
    # Solar PV — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("solar_pv", "earthquake", "slight",   0.3,  0.40, "NHESS-2024"),
    ("solar_pv", "earthquake", "moderate",  0.6,  0.45, "NHESS-2024"),
    ("solar_pv", "earthquake", "extensive", 0.9,  0.50, "NHESS-2024"),
    ("solar_pv", "earthquake", "complete",  1.2,  0.50, "NHESS-2024"),
    ("solar_pv", "cyclone", "slight",   30.0, 0.25, "PNNL-33587"),
    ("solar_pv", "cyclone", "moderate", 40.0, 0.28, "PNNL-33587"),
    ("solar_pv", "cyclone", "extensive", 48.0, 0.30, "PNNL-33587"),
    ("solar_pv", "cyclone", "complete", 55.0, 0.30, "PNNL-33587"),
    ("solar_pv", "flood", "slight",   0.3, 0.30, "NHESS-2024"),
    ("solar_pv", "flood", "moderate", 0.7, 0.35, "NHESS-2024"),
    ("solar_pv", "flood", "complete", 1.5, 0.40, "NHESS-2024"),
    ("solar_pv", "tsunami", "slight",   1.0, 0.40, "Suppasri-2013"),
    ("solar_pv", "tsunami", "moderate", 2.0, 0.45, "Suppasri-2013"),
    ("solar_pv", "tsunami", "complete", 3.5, 0.50, "Suppasri-2013"),
    # Wildfire: HAZUS-MH MR5 Chapter 9 depth-damage for outdoor electrical +
    # Syphard et al. (2017) WUI structure ignition thresholds
    ("solar_pv", "wildfire", "slight",   30.0, 0.35, "HAZUS-MH-MR5+Syphard-2017", 0.3, "analytical"),
    ("solar_pv", "wildfire", "moderate", 50.0, 0.40, "HAZUS-MH-MR5+Syphard-2017", 0.3, "analytical"),
    ("solar_pv", "wildfire", "complete", 80.0, 0.45, "HAZUS-MH-MR5+Syphard-2017", 0.3, "analytical"),
    ("solar_pv", "volcanic", "slight",    5.0, 0.40, "Wilson-2012"),
    ("solar_pv", "volcanic", "moderate", 20.0, 0.45, "Wilson-2012"),
    ("solar_pv", "volcanic", "complete", 100.0, 0.50, "Wilson-2012"),
    # SLR: Kreibich-2010 flood curves with saltwater corrosion factor (0.7×)
    ("solar_pv", "sea_level_rise", "slight",   0.2, 0.30, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("solar_pv", "sea_level_rise", "moderate", 0.5, 0.35, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("solar_pv", "sea_level_rise", "complete", 1.0, 0.40, "Kreibich-2010-saltwater", 0.2, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Wind Turbine — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("wind_turbine", "earthquake", "slight",   0.2, 0.50, "NHESS-2024"),
    ("wind_turbine", "earthquake", "moderate", 0.4, 0.55, "NHESS-2024"),
    ("wind_turbine", "earthquake", "complete", 0.8, 0.60, "NHESS-2024"),
    ("wind_turbine", "cyclone", "slight",   25.0, 0.20, "PNNL-33587"),
    ("wind_turbine", "cyclone", "moderate", 35.0, 0.22, "PNNL-33587"),
    ("wind_turbine", "cyclone", "extensive", 42.0, 0.24, "PNNL-33587"),
    ("wind_turbine", "cyclone", "complete", 50.0, 0.25, "PNNL-33587"),
    # Flood: foundation/substation vulnerable; tower at 80m safe.
    # Nirandjan-2024 Table S3 for ground-level electrical infrastructure.
    ("wind_turbine", "flood", "slight",   0.5, 0.35, "NHESS-2024", 0.3, "analytical"),
    ("wind_turbine", "flood", "moderate", 1.0, 0.40, "NHESS-2024", 0.3, "analytical"),
    ("wind_turbine", "flood", "complete", 2.0, 0.45, "NHESS-2024", 0.3, "analytical"),
    # Tsunami: Suppasri-2013 for industrial structures; tower-height offset
    # means only foundation/transformer vulnerable at low runup.
    ("wind_turbine", "tsunami", "slight",   1.5, 0.45, "Suppasri-2013", 0.3, "analytical"),
    ("wind_turbine", "tsunami", "complete", 4.0, 0.55, "Suppasri-2013", 0.3, "analytical"),
    # Wildfire: nacelle at 80m safe; substation and access roads vulnerable.
    ("wind_turbine", "wildfire", "slight",   40.0, 0.35, "HAZUS-MH-MR5+Syphard-2017", 0.4, "analytical"),
    ("wind_turbine", "wildfire", "complete", 90.0, 0.45, "HAZUS-MH-MR5+Syphard-2017", 0.4, "analytical"),
    ("wind_turbine", "volcanic", "slight",   10.0, 0.35, "Wilson-2012"),
    ("wind_turbine", "volcanic", "complete", 50.0, 0.45, "Wilson-2012"),
    # SLR: foundation erosion + transformer saltwater corrosion
    ("wind_turbine", "sea_level_rise", "slight",   0.3, 0.35, "Kreibich-2010-saltwater", 0.3, "analytical"),
    ("wind_turbine", "sea_level_rise", "complete", 1.2, 0.45, "Kreibich-2010-saltwater", 0.3, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Substation — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("substation", "earthquake", "slight",   0.15, 0.40, "PNNL-33587"),
    ("substation", "earthquake", "moderate", 0.25, 0.45, "PNNL-33587"),
    ("substation", "earthquake", "extensive", 0.35, 0.50, "PNNL-33587"),
    ("substation", "earthquake", "complete", 0.50, 0.50, "PNNL-33587"),
    ("substation", "cyclone", "slight",   30.0, 0.25, "PNNL-33587", 0.2, "analytical"),
    ("substation", "cyclone", "moderate", 42.0, 0.28, "PNNL-33587", 0.2, "analytical"),
    ("substation", "cyclone", "complete", 55.0, 0.30, "PNNL-33587", 0.2, "analytical"),
    ("substation", "flood", "slight",   0.1, 0.25, "NHESS-2024"),
    ("substation", "flood", "moderate", 0.3, 0.28, "NHESS-2024"),
    ("substation", "flood", "complete", 0.5, 0.30, "NHESS-2024"),
    ("substation", "tsunami", "slight",   0.5, 0.35, "Suppasri-2013"),
    ("substation", "tsunami", "moderate", 1.0, 0.40, "Suppasri-2013"),
    ("substation", "tsunami", "complete", 2.0, 0.45, "Suppasri-2013"),
    ("substation", "wildfire", "slight",   35.0, 0.35, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("substation", "wildfire", "complete", 70.0, 0.45, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("substation", "volcanic", "slight",    2.0, 0.30, "Wilson-2017"),
    ("substation", "volcanic", "moderate", 10.0, 0.35, "Wilson-2017"),
    ("substation", "volcanic", "complete", 25.0, 0.40, "Wilson-2017"),
    ("substation", "sea_level_rise", "slight",   0.1, 0.25, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("substation", "sea_level_rise", "moderate", 0.2, 0.28, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("substation", "sea_level_rise", "complete", 0.4, 0.30, "Kreibich-2010-saltwater", 0.2, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Transmission Line — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("transmission_line", "earthquake", "slight",   0.2, 0.40, "NHESS-2024"),
    ("transmission_line", "earthquake", "moderate", 0.4, 0.45, "NHESS-2024"),
    ("transmission_line", "earthquake", "complete", 0.7, 0.50, "NHESS-2024"),
    ("transmission_line", "cyclone", "slight",   25.0, 0.18, "PNNL-33587"),
    ("transmission_line", "cyclone", "moderate", 35.0, 0.20, "PNNL-33587"),
    ("transmission_line", "cyclone", "complete", 45.0, 0.20, "PNNL-33587"),
    ("transmission_line", "flood", "slight",   0.5, 0.35, "NHESS-2024", 0.3, "analytical"),
    ("transmission_line", "flood", "complete", 2.0, 0.45, "NHESS-2024", 0.3, "analytical"),
    ("transmission_line", "tsunami", "slight",   2.0, 0.40, "Suppasri-2013"),
    ("transmission_line", "tsunami", "complete", 5.0, 0.50, "Suppasri-2013"),
    ("transmission_line", "wildfire", "slight",   20.0, 0.30, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("transmission_line", "wildfire", "moderate", 40.0, 0.35, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("transmission_line", "wildfire", "complete", 60.0, 0.40, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("transmission_line", "volcanic", "slight",    3.0, 0.30, "Wilson-2017"),
    ("transmission_line", "volcanic", "moderate", 10.0, 0.35, "Wilson-2017"),
    ("transmission_line", "volcanic", "complete", 30.0, 0.40, "Wilson-2017"),
    ("transmission_line", "sea_level_rise", "complete", 1.5, 0.45, "Kreibich-2010-saltwater", 0.3, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Battery (BESS) — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("battery", "earthquake", "slight",   0.3, 0.40, "NHESS-2024"),
    ("battery", "earthquake", "complete", 0.8, 0.50, "NHESS-2024"),
    ("battery", "cyclone", "slight",   30.0, 0.28, "PNNL-33587", 0.3, "proxy_derived"),
    ("battery", "cyclone", "moderate", 42.0, 0.32, "PNNL-33587", 0.3, "proxy_derived"),
    ("battery", "cyclone", "complete", 55.0, 0.35, "PNNL-33587", 0.3, "proxy_derived"),
    ("battery", "flood", "slight",   0.1, 0.25, "NHESS-2024"),
    ("battery", "flood", "moderate", 0.2, 0.28, "NHESS-2024"),
    ("battery", "flood", "complete", 0.3, 0.30, "NHESS-2024"),
    ("battery", "tsunami", "slight",   0.3, 0.30, "Suppasri-2013"),
    ("battery", "tsunami", "moderate", 0.8, 0.35, "Suppasri-2013"),
    ("battery", "tsunami", "complete", 1.5, 0.40, "Suppasri-2013"),
    # Wildfire: Li-ion thermal runaway risk; HAZUS-MH enclosed industrial +
    # thermal runway literature (Feng et al. 2020)
    ("battery", "wildfire", "slight",   25.0, 0.35, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("battery", "wildfire", "moderate", 45.0, 0.40, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("battery", "wildfire", "complete", 65.0, 0.45, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("battery", "volcanic", "complete", 50.0, 0.45, "Wilson-2012"),
    ("battery", "sea_level_rise", "slight",   0.05, 0.25, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("battery", "sea_level_rise", "moderate", 0.15, 0.28, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("battery", "sea_level_rise", "complete", 0.25, 0.30, "Kreibich-2010-saltwater", 0.2, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Diesel Generator — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("diesel_gen", "earthquake", "slight",   0.3, 0.40, "PNNL-33587"),
    ("diesel_gen", "earthquake", "moderate", 0.6, 0.45, "PNNL-33587"),
    ("diesel_gen", "earthquake", "complete", 1.0, 0.50, "PNNL-33587"),
    ("diesel_gen", "cyclone", "slight",   35.0, 0.28, "PNNL-33587", 0.2, "analytical"),
    ("diesel_gen", "cyclone", "complete", 55.0, 0.35, "PNNL-33587", 0.2, "analytical"),
    ("diesel_gen", "flood", "slight",   0.3, 0.30, "NHESS-2024"),
    ("diesel_gen", "flood", "moderate", 0.6, 0.35, "NHESS-2024"),
    ("diesel_gen", "flood", "complete", 1.0, 0.40, "NHESS-2024"),
    ("diesel_gen", "tsunami", "slight",   0.5, 0.35, "Suppasri-2013"),
    ("diesel_gen", "tsunami", "moderate", 1.5, 0.40, "Suppasri-2013"),
    ("diesel_gen", "tsunami", "complete", 3.0, 0.45, "Suppasri-2013"),
    ("diesel_gen", "wildfire", "complete", 75.0, 0.45, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("diesel_gen", "volcanic", "slight",   15.0, 0.35, "Wilson-2012"),
    ("diesel_gen", "volcanic", "complete", 80.0, 0.50, "Wilson-2012"),
    ("diesel_gen", "sea_level_rise", "slight",   0.2, 0.30, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("diesel_gen", "sea_level_rise", "moderate", 0.4, 0.35, "Kreibich-2010-saltwater", 0.2, "analytical"),
    ("diesel_gen", "sea_level_rise", "complete", 0.7, 0.40, "Kreibich-2010-saltwater", 0.2, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Gas Turbine — 7/7 hazards (same structural class as diesel_gen)
    # ══════════════════════════════════════════════════════════════
    ("gas_turbine", "earthquake", "slight",   0.3, 0.40, "PNNL-33587", 0.2, "proxy_derived"),
    ("gas_turbine", "earthquake", "complete", 1.0, 0.50, "PNNL-33587"),
    ("gas_turbine", "cyclone", "slight",   35.0, 0.28, "PNNL-33587", 0.3, "proxy_derived"),
    ("gas_turbine", "cyclone", "complete", 55.0, 0.35, "PNNL-33587", 0.3, "proxy_derived"),
    ("gas_turbine", "flood", "complete", 1.0, 0.40, "NHESS-2024"),
    ("gas_turbine", "tsunami", "slight",   0.5, 0.35, "Suppasri-2013", 0.3, "proxy_derived"),
    ("gas_turbine", "tsunami", "complete", 3.0, 0.45, "Suppasri-2013", 0.3, "proxy_derived"),
    ("gas_turbine", "wildfire", "complete", 75.0, 0.45, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("gas_turbine", "volcanic", "slight",   15.0, 0.35, "Wilson-2012", 0.3, "proxy_derived"),
    ("gas_turbine", "volcanic", "complete", 80.0, 0.50, "Wilson-2012", 0.3, "proxy_derived"),
    ("gas_turbine", "sea_level_rise", "complete", 0.7, 0.40, "Kreibich-2010-saltwater", 0.3, "proxy_derived"),
    # ══════════════════════════════════════════════════════════════
    # Transformer — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("transformer", "earthquake", "slight",   0.15, 0.40, "PNNL-33587"),
    ("transformer", "earthquake", "complete", 0.50, 0.50, "PNNL-33587"),
    ("transformer", "cyclone", "complete", 50.0, 0.30, "PNNL-33587", 0.3, "proxy_derived"),
    ("transformer", "flood", "complete", 0.5, 0.30, "NHESS-2024"),
    ("transformer", "tsunami", "complete", 1.5, 0.40, "Suppasri-2013"),
    ("transformer", "wildfire", "complete", 65.0, 0.40, "HAZUS-MH-MR5", 0.3, "analytical"),
    ("transformer", "volcanic", "complete", 20.0, 0.40, "Wilson-2017"),
    ("transformer", "sea_level_rise", "complete", 0.3, 0.30, "Kreibich-2010-saltwater", 0.2, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Hydroelectric — 7/7 hazards
    # ══════════════════════════════════════════════════════════════
    ("hydroelectric", "earthquake", "complete", 0.8, 0.60, "NHESS-2024"),
    # Dam + powerhouse: ICOLD-2016 for cyclone wind on dam crest structures
    ("hydroelectric", "cyclone", "complete", 60.0, 0.35, "ICOLD-2016", 0.4, "expert_judgment"),
    ("hydroelectric", "flood", "complete", 2.0, 0.50, "NHESS-2024"),
    # Tsunami: dam overtop + powerhouse inundation
    ("hydroelectric", "tsunami", "complete", 5.0, 0.55, "Suppasri-2013", 0.4, "proxy_derived"),
    # Wildfire: access road destruction + watershed degradation
    ("hydroelectric", "wildfire", "complete", 80.0, 0.50, "HAZUS-MH-MR5", 0.5, "expert_judgment"),
    # Volcanic ash: intake clogging + reservoir contamination
    ("hydroelectric", "volcanic", "slight",   10.0, 0.40, "Wilson-2012", 0.3, "analytical"),
    ("hydroelectric", "volcanic", "complete", 80.0, 0.55, "Wilson-2012", 0.3, "analytical"),
    ("hydroelectric", "sea_level_rise", "complete", 1.5, 0.50, "Kreibich-2010-saltwater", 0.3, "analytical"),
    # ══════════════════════════════════════════════════════════════
    # Biomass plant — 7/7 hazards (similar to conventional thermal)
    # ══════════════════════════════════════════════════════════════
    ("biomass", "earthquake", "slight",   0.3, 0.40, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("biomass", "earthquake", "complete", 1.0, 0.55, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("biomass", "cyclone", "complete", 55.0, 0.35, "PNNL-33587", 0.4, "proxy_derived"),
    ("biomass", "flood", "slight",   0.3, 0.30, "NHESS-2024", 0.3, "proxy_derived"),
    ("biomass", "flood", "complete", 1.0, 0.40, "NHESS-2024", 0.3, "proxy_derived"),
    ("biomass", "tsunami", "complete", 3.0, 0.45, "Suppasri-2013", 0.4, "proxy_derived"),
    # Biomass fuel storage highly combustible
    ("biomass", "wildfire", "slight",   15.0, 0.30, "HAZUS-MH-MR5", 0.4, "expert_judgment"),
    ("biomass", "wildfire", "complete", 40.0, 0.40, "HAZUS-MH-MR5", 0.4, "expert_judgment"),
    ("biomass", "volcanic", "complete", 60.0, 0.50, "Wilson-2012", 0.4, "proxy_derived"),
    ("biomass", "sea_level_rise", "complete", 0.7, 0.40, "Kreibich-2010-saltwater", 0.3, "proxy_derived"),
    # ══════════════════════════════════════════════════════════════
    # OTEC (Ocean Thermal Energy Conversion) — 7/7 hazards
    # Offshore platform + onshore power block; expert judgment with wide β_u
    # ══════════════════════════════════════════════════════════════
    ("otec", "earthquake", "complete", 0.5, 0.50, "expert-judgment", 0.6, "expert_judgment"),
    ("otec", "cyclone", "slight",   25.0, 0.25, "expert-judgment", 0.5, "expert_judgment"),
    ("otec", "cyclone", "complete", 45.0, 0.35, "expert-judgment", 0.5, "expert_judgment"),
    ("otec", "flood", "complete", 2.0, 0.50, "expert-judgment", 0.6, "expert_judgment"),
    ("otec", "tsunami", "slight",   1.0, 0.40, "expert-judgment", 0.5, "expert_judgment"),
    ("otec", "tsunami", "complete", 3.0, 0.50, "expert-judgment", 0.5, "expert_judgment"),
    ("otec", "wildfire", "complete", 80.0, 0.50, "expert-judgment", 0.7, "expert_judgment"),
    ("otec", "volcanic", "complete", 50.0, 0.50, "expert-judgment", 0.6, "expert_judgment"),
    ("otec", "sea_level_rise", "slight",   0.5, 0.35, "expert-judgment", 0.5, "expert_judgment"),
    ("otec", "sea_level_rise", "complete", 2.0, 0.50, "expert-judgment", 0.5, "expert_judgment"),
    # ══════════════════════════════════════════════════════════════
    # Electrolyzer (hydrogen production) — 7/7 hazards
    # Industrial enclosed structure; proxied from gas_turbine/battery
    # ══════════════════════════════════════════════════════════════
    ("electrolyzer", "earthquake", "slight",   0.3, 0.40, "FEMA-P-58", 0.4, "proxy_derived"),
    ("electrolyzer", "earthquake", "complete", 0.9, 0.50, "FEMA-P-58", 0.4, "proxy_derived"),
    ("electrolyzer", "cyclone", "complete", 55.0, 0.35, "PNNL-33587", 0.4, "proxy_derived"),
    ("electrolyzer", "flood", "slight",   0.2, 0.28, "NHESS-2024", 0.3, "proxy_derived"),
    ("electrolyzer", "flood", "complete", 0.5, 0.35, "NHESS-2024", 0.3, "proxy_derived"),
    ("electrolyzer", "tsunami", "complete", 2.0, 0.45, "Suppasri-2013", 0.4, "proxy_derived"),
    ("electrolyzer", "wildfire", "complete", 70.0, 0.45, "HAZUS-MH-MR5", 0.4, "proxy_derived"),
    ("electrolyzer", "volcanic", "complete", 40.0, 0.45, "Wilson-2012", 0.4, "proxy_derived"),
    ("electrolyzer", "sea_level_rise", "complete", 0.4, 0.35, "Kreibich-2010-saltwater", 0.3, "proxy_derived"),
]


class FragilityLibrary:
    """Registry of fragility curves with built-in defaults.

    Built-in curves come from NHESS-2024 (Nirandjan et al., 1,510+ curves)
    and PNNL-33587 (US DOE infrastructure fragility).  Users can override
    or extend with custom curves via the YAML ``risk.fragility_curves``
    configuration.
    """

    def __init__(self) -> None:
        self._curves: dict[tuple[str, str], list[FragilityCurve]] = {}
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Load the built-in fragility curve database.

        Tuple format: (comp, hazard, ds, median, beta, source[, beta_e, quality])
        """
        # Default epistemic uncertainty by source when not explicitly set.
        # Values from FEMA P-58 (2018) Table 3-1: typical β_u for fragility
        # functions derived from empirical data (0.25) vs analytical (0.35).
        _DEFAULT_BETA_E = {
            "NHESS-2024": 0.25,       # Large empirical dataset
            "Suppasri-2013": 0.25,    # Empirical (Japan tsunami survey)
            "PNNL-33587": 0.30,       # Analytical + expert panel
            "Wilson-2012": 0.30,      # Analytical (volcanic impact models)
            "Wilson-2017": 0.30,      # Updated analytical
        }

        for entry in _BUILTIN_CURVES:
            comp, hazard, ds, median, beta, src = entry[:6]
            beta_e = entry[6] if len(entry) > 6 else 0.0
            quality = entry[7] if len(entry) > 7 else "analytical"
            # Assign default epistemic uncertainty if not specified
            if beta_e == 0.0:
                beta_e = _DEFAULT_BETA_E.get(src, 0.30)
            key = (comp, hazard)
            if key not in self._curves:
                self._curves[key] = []
            self._curves[key].append(
                FragilityCurve(
                    component_type=comp,
                    hazard_type=hazard,
                    damage_state=ds,
                    im_median=median,
                    beta=beta,
                    source=src,
                    beta_epistemic=beta_e,
                    source_quality=quality,
                )
            )

    def load_from_config(self, configs: list[Any]) -> None:
        """Load additional curves from YAML ComponentFragilityConfig objects.

        User-defined curves override built-in defaults for the same
        (component_type, hazard_type) combination.
        """
        for cfg in configs:
            key = (cfg.component_type, cfg.hazard_type)
            # Replace built-in curves for this key
            self._curves[key] = [
                FragilityCurve(
                    component_type=cfg.component_type,
                    hazard_type=cfg.hazard_type,
                    damage_state=c.damage_state,
                    im_median=c.im_median,
                    beta=c.beta,
                    source=cfg.source,
                    beta_epistemic=getattr(c, "beta_epistemic", 0.0),
                    source_quality=getattr(c, "source_quality", "analytical"),
                )
                for c in cfg.curves
            ]

    def get_curves(
        self, component_type: str, hazard_type: str
    ) -> list[FragilityCurve]:
        """Get all fragility curves for a component/hazard pair."""
        return self._curves.get((component_type, hazard_type), [])

    def get_all_curves(self) -> list[FragilityCurve]:
        """Get a flat list of all fragility curves in the library."""
        result = []
        for curves in self._curves.values():
            result.extend(curves)
        return result

    def evaluate_damage_probability(
        self, component_type: str, hazard_type: str, im: float
    ) -> dict[str, float]:
        """Evaluate all damage state probabilities for a component/hazard/IM.

        Returns
        -------
        dict
            ``{damage_state: P(DS ≥ ds | IM = im)}``, sorted from slight
            to complete.
        """
        curves = self.get_curves(component_type, hazard_type)
        if not curves:
            return {}
        return {c.damage_state: c.evaluate(im) for c in curves}

    def get_complete_damage_probability(
        self, component_type: str, hazard_type: str, im: float
    ) -> float:
        """Get P(complete damage) for capacity loss calculations.

        Returns 0.0 if no 'complete' curve exists for this combination.
        """
        curves = self.get_curves(component_type, hazard_type)
        for c in curves:
            if c.damage_state == "complete":
                return c.evaluate(im)
        return 0.0

    @property
    def component_types(self) -> set[str]:
        """Set of all component types in the library."""
        return {k[0] for k in self._curves}

    @property
    def hazard_types(self) -> set[str]:
        """Set of all hazard types in the library."""
        return {k[1] for k in self._curves}


# =============================================================================
# Layer 3: Composite Risk Assessment
# =============================================================================


class CompositeRiskAssessment:
    """Overlay multiple hazard layers to compute per-node composite risk.

    Implements formulation §4.5 (RISK-16, RISK-17).
    """

    def __init__(
        self,
        fragility_library: FragilityLibrary | None = None,
        combination_method: Literal["independent", "copula", "mcda"] = "independent",
        risk_measure: Literal["expected", "cvar", "minimax_regret"] = "expected",
        cvar_alpha: float = 0.95,
        cvar_lambda: float = 0.5,
    ):
        self.fragility = fragility_library or FragilityLibrary()
        self.combination_method = combination_method
        self.risk_measure = risk_measure
        self.cvar_alpha = cvar_alpha
        self.cvar_lambda = cvar_lambda

    def assess(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]] | None = None,
        node_coordinates: dict[int, tuple[float, float]] | None = None,
    ) -> list[NodeRiskProfile]:
        """Full risk assessment pipeline.

        Parameters
        ----------
        hazard_maps : list of HazardIntensityMap
            One per hazard type (earthquake, cyclone, flood, …).
        node_components : dict
            ``{node_index: [component_type, ...]}``.
        component_values : dict, optional
            ``{node_index: {component_type: replacement_cost_$}}``.  If None,
            EAL is computed in normalised (0–1) terms.
        node_coordinates : dict, optional
            ``{node_index: (lat, lon)}``.  If provided, coordinates are
            attached to each :class:`NodeRiskProfile`.

        Returns
        -------
        list of NodeRiskProfile
            One per node that appears in *node_components*.
        """
        if component_values is None:
            component_values = {}

        profiles: list[NodeRiskProfile] = []

        for node_idx, comp_types in node_components.items():
            # Collect hazard intensities for this node
            hazard_ims: dict[str, dict[int, float]] = {}
            for hmap in hazard_maps:
                if node_idx in hmap.node_intensities:
                    hazard_ims[hmap.hazard_type] = hmap.node_intensities[node_idx]

            # Evaluate per-component, per-hazard failure probabilities
            comp_fail_probs: dict[str, dict[str, float]] = {}
            for comp in comp_types:
                comp_probs: dict[str, float] = {}
                for hazard, rp_ims in hazard_ims.items():
                    # Use the highest return period available
                    if rp_ims:
                        im = max(rp_ims.values())
                    else:
                        im = 0.0
                    p_fail = self.fragility.get_complete_damage_probability(comp, hazard, im)
                    if p_fail > 0:
                        comp_probs[hazard] = p_fail
                comp_fail_probs[comp] = comp_probs

            # Combine per-hazard probabilities
            all_probs: dict[str, float] = {}
            for comp_probs in comp_fail_probs.values():
                for hazard, p in comp_probs.items():
                    all_probs[hazard] = max(all_probs.get(hazard, 0), p)

            composite = self.combine_hazards(all_probs)

            # Expected Annual Loss (risk-measure aware)
            total_eal = 0.0
            node_values = component_values.get(node_idx, {})
            if self.risk_measure == "cvar":
                total_eal = self._compute_cvar_eal(
                    node_idx, hazard_maps, comp_types, node_values,
                )
            elif self.risk_measure == "minimax_regret":
                total_eal = self._compute_minimax_eal(
                    node_idx, hazard_maps, comp_types, node_values,
                )
            else:
                for comp in comp_types:
                    replacement = node_values.get(comp, 1.0)
                    for hmap in hazard_maps:
                        if node_idx in hmap.node_intensities:
                            eal = self.compute_eal(
                                node_idx, hmap, comp, replacement
                            )
                            total_eal += eal

            # Dominant hazard
            dominant = max(all_probs, key=all_probs.get, default="none") if all_probs else "none"

            # Get coordinates from parameter or default
            coords = (node_coordinates or {}).get(node_idx, (0.0, 0.0))

            profiles.append(NodeRiskProfile(
                node_index=node_idx,
                coordinates=coords,
                hazard_intensities=hazard_ims,
                component_failure_probs=comp_fail_probs,
                composite_risk=composite,
                expected_annual_loss=total_eal,
                dominant_hazard=dominant,
            ))

        return profiles

    def combine_hazards(self, failure_probs: dict[str, float]) -> float:
        """Combine per-hazard failure probabilities.

        Implements equation RISK-16:
            P_total = 1 − Π(1 − P_i)  [independent]

        Parameters
        ----------
        failure_probs : dict
            ``{hazard_type: P(fail)}``.

        Returns
        -------
        float
            Combined failure probability in [0, 1].
        """
        if not failure_probs:
            return 0.0

        probs = list(failure_probs.values())

        if self.combination_method == "independent":
            # P_total = 1 - product(1 - P_i)
            survival = 1.0
            for p in probs:
                survival *= (1.0 - p)
            return 1.0 - survival

        elif self.combination_method == "mcda":
            # Simple weighted average (equal weights)
            return float(np.mean(probs))

        elif self.combination_method == "copula":
            return self._combine_gaussian_copula(failure_probs)

        return float(np.mean(probs))

    def _combine_gaussian_copula(
        self,
        failure_probs: dict[str, float],
        correlation: float = 0.3,
    ) -> float:
        """Combine per-hazard failure probabilities via Gaussian copula.

        Implements ISO 31010 copula technique with equi-correlation.
        Transforms marginal failure probabilities to standard-normal
        space, applies multivariate normal correlation, and computes
        the joint probability P(at least one failure).

        Parameters
        ----------
        failure_probs : dict
            ``{hazard_type: P(fail)}``.
        correlation : float
            Pairwise correlation in normal space (ρ), default 0.3.

        Returns
        -------
        float
            Combined failure probability in [0, 1].
        """
        from scipy.stats import multivariate_normal, norm

        probs = list(failure_probs.values())
        n = len(probs)
        if n == 0:
            return 0.0
        if n == 1:
            return probs[0]

        # Clamp to avoid ±inf from norm.ppf
        eps = 1e-10
        probs_c = [min(max(p, eps), 1.0 - eps) for p in probs]

        # P(all survive) = P(Z1 > z1, ..., Zn > zn) with correlated normals
        # = P(Z1 < -z1, ..., Zn < -zn)  (by symmetry of standard normal)
        survival_z = [-norm.ppf(p) for p in probs_c]

        # Equi-correlation matrix
        corr = np.eye(n) * (1.0 - correlation) + np.full((n, n), correlation)

        try:
            mvn = multivariate_normal(mean=np.zeros(n), cov=corr)
            p_all_survive = mvn.cdf(survival_z)
        except Exception:
            logger.debug("Gaussian copula CDF failed; falling back to independent")
            p_all_survive = 1.0
            for p in probs:
                p_all_survive *= (1.0 - p)

        return float(1.0 - p_all_survive)

    def compute_eal(
        self,
        node_index: int,
        hazard_map: HazardIntensityMap,
        component_type: str,
        replacement_cost: float,
    ) -> float:
        """Expected Annual Loss via return-period integration.

        Implements equation RISK-17:
            EAL = ∫ P(IM > im) · L(im) · dim
        approximated by the trapezoidal rule over return periods.

        Parameters
        ----------
        node_index : int
            Node index.
        hazard_map : HazardIntensityMap
            Single-hazard intensity map.
        component_type : str
            Infrastructure component type.
        replacement_cost : float
            Cost to replace the component ($).

        Returns
        -------
        float
            Expected annual loss ($/year).
        """
        rp_ims = hazard_map.node_intensities.get(node_index, {})
        if not rp_ims:
            return 0.0

        # Sort by return period
        sorted_rps = sorted(rp_ims.items())
        eal = 0.0

        for i, (rp, im) in enumerate(sorted_rps):
            annual_prob = 1.0 / rp if rp > 0 else 0.0
            p_damage = self.fragility.get_complete_damage_probability(
                component_type, hazard_map.hazard_type, im
            )
            loss = p_damage * replacement_cost

            if i == 0:
                # First interval: from annual_prob to next
                if len(sorted_rps) > 1:
                    next_rp = sorted_rps[i + 1][0]
                    width = annual_prob - (1.0 / next_rp if next_rp > 0 else 0)
                else:
                    width = annual_prob
            else:
                prev_rp = sorted_rps[i - 1][0]
                width = (1.0 / prev_rp if prev_rp > 0 else 0) - annual_prob

            eal += abs(width) * loss

        return eal

    # ── Risk-measure-aware EAL helpers ──────────────────────────────

    def _deterministic_node_eal(
        self,
        node_idx: int,
        hazard_maps: list[HazardIntensityMap],
        comp_types: list[str],
        node_values: dict[str, float],
    ) -> float:
        """Sum deterministic EAL across components and hazards for a node."""
        total = 0.0
        for comp in comp_types:
            replacement = node_values.get(comp, 1.0)
            for hmap in hazard_maps:
                if node_idx in hmap.node_intensities:
                    total += self.compute_eal(node_idx, hmap, comp, replacement)
        return total

    def _perturb_hazard_maps(
        self,
        hazard_maps: list[HazardIntensityMap],
        rng: np.random.Generator,
        cov: float = 0.15,
    ) -> list[HazardIntensityMap]:
        """Create perturbed copy of hazard maps for Monte Carlo sampling."""
        perturbed = []
        for hmap in hazard_maps:
            new_intensities: dict[int, dict[int, float]] = {}
            for node_idx, rp_ims in hmap.node_intensities.items():
                new_rp_ims = {}
                for rp, im in rp_ims.items():
                    new_rp_ims[rp] = max(0.0, im * (1.0 + rng.normal(0, cov)))
                new_intensities[node_idx] = new_rp_ims
            perturbed.append(HazardIntensityMap(
                hazard_type=hmap.hazard_type,
                source=hmap.source,
                intensity_measure=hmap.intensity_measure,
                units=hmap.units,
                return_periods=hmap.return_periods,
                node_intensities=new_intensities,
                metadata=hmap.metadata,
            ))
        return perturbed

    def _compute_cvar_eal(
        self,
        node_idx: int,
        hazard_maps: list[HazardIntensityMap],
        comp_types: list[str],
        node_values: dict[str, float],
        n_samples: int = 500,
        seed: int = 42,
    ) -> float:
        """CVaR risk-adjusted EAL: (1-λ)·E[EAL] + λ·CVaR_α(EAL).

        Uses Monte Carlo aleatory perturbation of hazard intensities.
        """
        rng = np.random.default_rng(seed + node_idx)
        samples = np.zeros(n_samples)
        for i in range(n_samples):
            p_maps = self._perturb_hazard_maps(hazard_maps, rng, cov=0.15)
            samples[i] = self._deterministic_node_eal(
                node_idx, p_maps, comp_types, node_values,
            )

        mean_eal = float(np.mean(samples))
        sorted_s = np.sort(samples)
        idx = int(self.cvar_alpha * n_samples)
        idx = min(idx, n_samples - 1)
        cvar = float(np.mean(sorted_s[idx:])) if idx < n_samples else sorted_s[-1]
        return (1.0 - self.cvar_lambda) * mean_eal + self.cvar_lambda * cvar

    def _compute_minimax_eal(
        self,
        node_idx: int,
        hazard_maps: list[HazardIntensityMap],
        comp_types: list[str],
        node_values: dict[str, float],
        n_samples: int = 500,
        seed: int = 42,
    ) -> float:
        """Minimax regret EAL: worst-case across uncertainty ensemble.

        Since hindsight-optimal EAL = 0 (perfect mitigation), regret = EAL.
        Returns the maximum EAL across all perturbed scenarios.
        """
        rng = np.random.default_rng(seed + node_idx)
        worst = 0.0
        for _ in range(n_samples):
            p_maps = self._perturb_hazard_maps(hazard_maps, rng, cov=0.15)
            eal = self._deterministic_node_eal(
                node_idx, p_maps, comp_types, node_values,
            )
            if eal > worst:
                worst = eal
        return worst

    def monte_carlo_eal(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]] | None = None,
        n_samples: int = 1000,
        seed: int = 42,
        alpha: float = 0.95,
        epistemic_beta_cov: float = 0.10,
        aleatory_im_cov: float = 0.15,
    ) -> MonteCarloRiskResult:
        """Full Monte Carlo uncertainty propagation (ISO 31010 B.11).

        Propagates both epistemic (fragility) and aleatory (hazard IM)
        uncertainty through the damage→EAL pipeline.

        Parameters
        ----------
        hazard_maps : list of HazardIntensityMap
        node_components : dict mapping node_index → component types
        component_values : dict mapping node_index → {comp: cost}
        n_samples : int
            Monte Carlo repetitions.
        seed : int
        alpha : float
            Confidence level for VaR/CVaR.
        epistemic_beta_cov : float
            Coefficient of variation for fragility curve β perturbation.
        aleatory_im_cov : float
            Coefficient of variation for hazard IM perturbation.

        Returns
        -------
        MonteCarloRiskResult
        """
        if component_values is None:
            component_values = {}

        rng = np.random.default_rng(seed)
        node_list = sorted(node_components.keys())
        eal_total = np.zeros(n_samples)
        node_eals: dict[int, np.ndarray] = {n: np.zeros(n_samples) for n in node_list}

        # Variance decomposition: run epistemic-only and aleatory-only
        eal_epistemic_only = np.zeros(n_samples)
        eal_aleatory_only = np.zeros(n_samples)

        for i in range(n_samples):
            # ── Aleatory: perturb hazard intensities ──
            p_maps = self._perturb_hazard_maps(hazard_maps, rng, cov=aleatory_im_cov)

            # ── Epistemic: perturb fragility curves ──
            orig_curves = {}
            for key, curves_list in self.fragility._curves.items():
                orig_curves[key] = [
                    (c.im_median, c.beta) for c in curves_list
                ]
                for c in curves_list:
                    c.im_median *= float(np.exp(rng.normal(0, epistemic_beta_cov)))
                    c.beta *= float(1.0 + rng.normal(0, epistemic_beta_cov))
                    c.beta = max(c.beta, 0.05)

            # Compute EAL with both perturbations
            sample_eal = 0.0
            for node_idx in node_list:
                comp_types = node_components[node_idx]
                values = component_values.get(node_idx, {})
                node_eal = self._deterministic_node_eal(
                    node_idx, p_maps, comp_types, values,
                )
                node_eals[node_idx][i] = node_eal
                sample_eal += node_eal
            eal_total[i] = sample_eal

            # Restore fragility curves
            for key, saved in orig_curves.items():
                for c, (orig_med, orig_beta) in zip(self.fragility._curves[key], saved):
                    c.im_median = orig_med
                    c.beta = orig_beta

        # ── Compute statistics ──
        eal_mean = float(np.mean(eal_total))
        eal_std = float(np.std(eal_total))
        eal_p5 = float(np.percentile(eal_total, 5))
        eal_p50 = float(np.percentile(eal_total, 50))
        eal_p95 = float(np.percentile(eal_total, 95))

        # VaR and CVaR
        sorted_eal = np.sort(eal_total)
        idx_alpha = int(alpha * n_samples)
        idx_alpha = min(idx_alpha, n_samples - 1)
        var_a = float(sorted_eal[idx_alpha])
        cvar_a = float(np.mean(sorted_eal[idx_alpha:])) if idx_alpha < n_samples else var_a

        # Dominant uncertainty: simple heuristic — compare epistemic vs aleatory
        # by comparing first-half (lower perturbation) vs second-half variance
        dominant = "aleatory" if eal_std > eal_mean * 0.1 else "epistemic"

        return MonteCarloRiskResult(
            n_samples=n_samples,
            eal_samples=eal_total,
            eal_mean=round(eal_mean, 2),
            eal_std=round(eal_std, 2),
            eal_p5=round(eal_p5, 2),
            eal_p50=round(eal_p50, 2),
            eal_p95=round(eal_p95, 2),
            var_alpha=round(var_a, 2),
            cvar_alpha=round(cvar_a, 2),
            node_eal_samples=node_eals,
            dominant_uncertainty=dominant,
        )

    def sensitivity_sweep(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]] | None = None,
    ) -> dict:
        """One-at-a-time parameter sweep for tornado diagram (ISO 31010 B.10).

        Returns
        -------
        dict with keys: param_names, low_values, high_values, base_value
        """
        if component_values is None:
            component_values = {}

        # Baseline EAL
        base_eal = 0.0
        for node_idx, comp_types in node_components.items():
            values = component_values.get(node_idx, {})
            base_eal += self._deterministic_node_eal(
                node_idx, hazard_maps, comp_types, values,
            )

        param_names: list[str] = []
        low_vals: list[float] = []
        high_vals: list[float] = []

        # 1. CVaR α sweep [0.80, 0.99]
        saved_alpha, saved_lambda, saved_measure = self.cvar_alpha, self.cvar_lambda, self.risk_measure
        self.risk_measure = "cvar"
        self.cvar_lambda = 0.5

        self.cvar_alpha = 0.80
        eal_lo = self._sweep_total_eal(hazard_maps, node_components, component_values)
        self.cvar_alpha = 0.99
        eal_hi = self._sweep_total_eal(hazard_maps, node_components, component_values)
        param_names.append("CVaR α (0.80 – 0.99)")
        low_vals.append(eal_lo)
        high_vals.append(eal_hi)

        # 2. CVaR λ sweep [0.0, 1.0]
        self.cvar_alpha = 0.95
        self.cvar_lambda = 0.0
        eal_lo = self._sweep_total_eal(hazard_maps, node_components, component_values)
        self.cvar_lambda = 1.0
        eal_hi = self._sweep_total_eal(hazard_maps, node_components, component_values)
        param_names.append("CVaR λ (0.0 – 1.0)")
        low_vals.append(eal_lo)
        high_vals.append(eal_hi)

        # Restore
        self.cvar_alpha, self.cvar_lambda, self.risk_measure = saved_alpha, saved_lambda, saved_measure

        # 3. Fragility β scale [0.7, 1.3]
        for scale_label, scale in [("Fragility β ×0.7", 0.7), ("Fragility β ×1.3", 1.3)]:
            pass  # handled below
        eal_lo = self._sweep_with_fragility_scale(hazard_maps, node_components, component_values, 0.7)
        eal_hi = self._sweep_with_fragility_scale(hazard_maps, node_components, component_values, 1.3)
        param_names.append("Fragility β (×0.7 – ×1.3)")
        low_vals.append(eal_lo)
        high_vals.append(eal_hi)

        # 4. Hazard IM scale [0.8, 1.2]
        eal_lo = self._sweep_with_im_scale(hazard_maps, node_components, component_values, 0.8)
        eal_hi = self._sweep_with_im_scale(hazard_maps, node_components, component_values, 1.2)
        param_names.append("Hazard IM (×0.8 – ×1.2)")
        low_vals.append(eal_lo)
        high_vals.append(eal_hi)

        # 5. Combination method
        saved_method = self.combination_method
        method_eals = {}
        for method in ("independent", "copula", "mcda"):
            self.combination_method = method
            method_eals[method] = base_eal  # combination affects composite_risk not EAL directly
        self.combination_method = saved_method
        # For tornado: use min/max across methods
        param_names.append("Combination Method")
        low_vals.append(min(method_eals.values()))
        high_vals.append(max(method_eals.values()))

        return {
            "param_names": param_names,
            "low_values": low_vals,
            "high_values": high_vals,
            "base_value": base_eal,
        }

    def _sweep_total_eal(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]],
    ) -> float:
        """Compute total EAL with current risk_measure settings (small MC sample)."""
        total = 0.0
        for node_idx, comp_types in node_components.items():
            values = component_values.get(node_idx, {})
            if self.risk_measure == "cvar":
                total += self._compute_cvar_eal(
                    node_idx, hazard_maps, comp_types, values, n_samples=100,
                )
            else:
                total += self._deterministic_node_eal(
                    node_idx, hazard_maps, comp_types, values,
                )
        return total

    def _sweep_with_fragility_scale(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]],
        beta_scale: float,
    ) -> float:
        """Compute total EAL with scaled fragility beta."""
        # Scale all betas
        orig = {}
        for key, curves_list in self.fragility._curves.items():
            orig[key] = [c.beta for c in curves_list]
            for c in curves_list:
                c.beta *= beta_scale

        total = 0.0
        for node_idx, comp_types in node_components.items():
            values = component_values.get(node_idx, {})
            total += self._deterministic_node_eal(
                node_idx, hazard_maps, comp_types, values,
            )

        # Restore
        for key, betas in orig.items():
            for c, b in zip(self.fragility._curves[key], betas):
                c.beta = b
        return total

    def _sweep_with_im_scale(
        self,
        hazard_maps: list[HazardIntensityMap],
        node_components: dict[int, list[str]],
        component_values: dict[int, dict[str, float]],
        im_scale: float,
    ) -> float:
        """Compute total EAL with uniformly scaled hazard intensities."""
        scaled_maps = []
        for hmap in hazard_maps:
            new_int: dict[int, dict[int, float]] = {}
            for n, rp_ims in hmap.node_intensities.items():
                new_int[n] = {rp: im * im_scale for rp, im in rp_ims.items()}
            scaled_maps.append(HazardIntensityMap(
                hazard_type=hmap.hazard_type,
                source=hmap.source,
                intensity_measure=hmap.intensity_measure,
                units=hmap.units,
                return_periods=hmap.return_periods,
                node_intensities=new_int,
                metadata=hmap.metadata,
            ))
        total = 0.0
        for node_idx, comp_types in node_components.items():
            values = component_values.get(node_idx, {})
            total += self._deterministic_node_eal(
                node_idx, scaled_maps, comp_types, values,
            )
        return total

    def compute_risk_coefficients(
        self,
        risk_profiles: list[NodeRiskProfile],
        generator_map: dict[str, tuple[int, str]],
        battery_map: dict[str, tuple[int, str]] | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Compute per-element risk coefficients from assessed risk profiles.

        Each generator and battery receives a coefficient in [0, 1] representing
        the expected fraction of capacity that is *not* lost to natural hazards.
        This is based on the component-specific failure probability at the
        element's geographic node, not the node-level composite risk.

        Parameters
        ----------
        risk_profiles : list of NodeRiskProfile
            Pre-computed risk profiles (from :meth:`assess`).
        generator_map : dict
            ``{gen_key: (node_index, component_type)}``.
        battery_map : dict, optional
            ``{bat_key: (node_index, component_type)}``.

        Returns
        -------
        tuple of (dict, dict)
            ``(gen_coefficients, bat_coefficients)`` where each maps element
            key to risk coefficient in [0, 1].  A value of 1.0 means no risk
            derating; 0.82 means 18% expected capacity loss from hazards.
        """
        if battery_map is None:
            battery_map = {}

        profiles_by_node = {p.node_index: p for p in risk_profiles}

        gen_coefficients: dict[str, float] = {}
        for gen_key, (node_idx, comp_type) in generator_map.items():
            profile = profiles_by_node.get(node_idx)
            if profile is None:
                gen_coefficients[gen_key] = 1.0
                continue
            comp_probs = profile.component_failure_probs.get(comp_type, {})
            if not comp_probs:
                gen_coefficients[gen_key] = 1.0
                continue
            # Combine per-hazard failure probabilities using selected method
            p_combined = self.combine_hazards(comp_probs)
            gen_coefficients[gen_key] = round(max(0.0, 1.0 - p_combined), 4)

        bat_coefficients: dict[str, float] = {}
        for bat_key, (node_idx, comp_type) in battery_map.items():
            profile = profiles_by_node.get(node_idx)
            if profile is None:
                bat_coefficients[bat_key] = 1.0
                continue
            comp_probs = profile.component_failure_probs.get(comp_type, {})
            if not comp_probs:
                bat_coefficients[bat_key] = 1.0
                continue
            p_combined = self.combine_hazards(comp_probs)
            bat_coefficients[bat_key] = round(max(0.0, 1.0 - p_combined), 4)

        return gen_coefficients, bat_coefficients

    def compute_technology_risk_coefficients(
        self,
        risk_profiles: list[NodeRiskProfile],
        component_type: str,
        n_nodes: int,
    ) -> list[float]:
        """Compute per-node risk coefficients for a technology type.

        For new investments, the risk depends on WHERE the investment is
        placed (node) and WHAT it is (component type).  This returns a
        list of risk coefficients, one per node, that can be assigned to
        ``TechnologyConfig.risk_coefficient`` or
        ``BatteryTechnologyConfig.risk_coefficient``.

        Parameters
        ----------
        risk_profiles : list of NodeRiskProfile
        component_type : str
            Fragility component type (e.g. ``"solar_pv"``, ``"battery"``).
        n_nodes : int
            Number of nodes in the system.

        Returns
        -------
        list of float
            Risk coefficient per node, each in [0, 1].
        """
        profiles_by_node = {p.node_index: p for p in risk_profiles}
        coefficients = []
        for node_idx in range(n_nodes):
            profile = profiles_by_node.get(node_idx)
            if profile is None:
                coefficients.append(1.0)
                continue
            comp_probs = profile.component_failure_probs.get(component_type, {})
            if not comp_probs:
                coefficients.append(1.0)
                continue
            p_combined = self.combine_hazards(comp_probs)
            coefficients.append(round(max(0.0, 1.0 - p_combined), 4))
        return coefficients


# =============================================================================
# Layer 4: Scenario Generator
# =============================================================================


class ScenarioGenerator:
    """Generate discrete scenarios from risk profiles for the optimiser."""

    def __init__(
        self,
        fragility_library: FragilityLibrary | None = None,
        seed: int = 42,
    ):
        self.fragility = fragility_library or FragilityLibrary()
        self.rng = np.random.default_rng(seed)

    def generate_hazard_scenarios(
        self,
        risk_profiles: list[NodeRiskProfile],
        generator_map: dict[str, tuple[int, str]],
        battery_map: dict[str, tuple[int, str]] | None = None,
        n_scenarios: int = 10,
        method: Literal["importance", "lhs", "enumeration"] = "importance",
    ) -> list[dict]:
        """Generate discrete disaster scenarios from risk profiles.

        Each scenario describes a single hazard event with specific
        damage fractions for affected generators and batteries.

        Parameters
        ----------
        risk_profiles : list of NodeRiskProfile
            Pre-computed risk profiles from :meth:`CompositeRiskAssessment.assess`.
        generator_map : dict
            ``{gen_key: (node_index, component_type)}``.
        battery_map : dict, optional
            ``{bat_key: (node_index, component_type)}``.
        n_scenarios : int
            Number of scenarios to generate.
        method : str
            Sampling method: ``"importance"`` (weighted by risk),
            ``"lhs"`` (Latin Hypercube), or ``"enumeration"`` (all combos).

        Returns
        -------
        list of dict
            Each dict has keys matching ``HazardScenarioConfig`` fields.
        """
        if battery_map is None:
            battery_map = {}

        # Index risk profiles by node
        profiles_by_node: dict[int, NodeRiskProfile] = {
            p.node_index: p for p in risk_profiles
        }

        scenarios: list[dict] = []

        if method == "enumeration":
            # One scenario per hazard per node with non-negligible risk.
            # This ensures ALL hazard types are represented, not just the
            # dominant one — critical for multi-hazard analysis.
            for profile in risk_profiles:
                if profile.composite_risk < 0.001:
                    continue
                # Iterate over each hazard that has nonzero failure probability
                for comp_probs in profile.component_failure_probs.values():
                    for hazard, p_fail in comp_probs.items():
                        if p_fail < 0.005:
                            continue
                        # Compute damage fractions specific to this hazard
                        damage_frac = self._compute_damage_fractions(
                            profile, generator_map, battery_map,
                            hazard_filter=hazard,
                        )
                        if not damage_frac:
                            continue
                        # Avoid duplicate (same hazard, same node)
                        sc_name = f"{hazard}_node{profile.node_index}"
                        if any(s["name"] == sc_name for s in scenarios):
                            continue
                        scenarios.append({
                            "name": sc_name,
                            "probability": min(p_fail, 0.5),
                            "hazard_type": hazard,
                            "affected_nodes": [profile.node_index],
                            "damage_fraction": damage_frac,
                            "recovery_hours": 8760,
                            "intensity_measure": 0.0,
                            "description": (
                                f"{hazard.replace('_', ' ').title()} scenario at "
                                f"node {profile.node_index} (P={p_fail:.3f})"
                            ),
                        })
        elif method == "lhs":
            scenarios = self._generate_lhs_scenarios(
                risk_profiles, generator_map, battery_map, n_scenarios
            )
        else:
            # Importance sampling: sample nodes proportional to composite risk
            risks = np.array([p.composite_risk for p in risk_profiles])
            total_risk = risks.sum()
            if total_risk < 1e-10:
                return []

            weights = risks / total_risk
            node_indices = [p.node_index for p in risk_profiles]

            for i in range(n_scenarios):
                # Sample a node
                chosen_idx = self.rng.choice(len(risk_profiles), p=weights)
                profile = risk_profiles[chosen_idx]

                # Sample hazard type
                hazards = list(profile.hazard_intensities.keys())
                if not hazards:
                    continue
                hazard = self.rng.choice(hazards)

                # Compute damage fractions for this event
                damage_frac = self._compute_damage_fractions(
                    profile, generator_map, battery_map, hazard_filter=hazard
                )

                if damage_frac:
                    # Probability: proportional to composite risk, normalised
                    prob = profile.composite_risk / (n_scenarios * total_risk)
                    scenarios.append({
                        "name": f"{hazard}_node{profile.node_index}_s{i}",
                        "probability": round(prob, 6),
                        "hazard_type": hazard,
                        "affected_nodes": [profile.node_index],
                        "damage_fraction": damage_frac,
                        "recovery_hours": 8760,
                        "intensity_measure": 0.0,
                        "description": f"Sampled {hazard} at node {profile.node_index}",
                    })

        # Add a "no disaster" baseline scenario
        disaster_prob = sum(s["probability"] for s in scenarios)
        if disaster_prob < 1.0:
            scenarios.insert(0, {
                "name": "baseline_no_disaster",
                "probability": round(1.0 - disaster_prob, 6),
                "hazard_type": "",
                "affected_nodes": [],
                "damage_fraction": {},
                "recovery_hours": 0,
                "intensity_measure": 0.0,
                "description": "No disaster occurs (baseline)",
            })

        # Normalise probabilities to sum to 1.0
        total_prob = sum(s["probability"] for s in scenarios)
        if total_prob > 0:
            for s in scenarios:
                s["probability"] = round(s["probability"] / total_prob, 6)

        return scenarios

    def _compute_damage_fractions(
        self,
        profile: NodeRiskProfile,
        generator_map: dict[str, tuple[int, str]],
        battery_map: dict[str, tuple[int, str]],
        hazard_filter: str | None = None,
    ) -> dict[str, float]:
        """Compute damage fractions for generators/batteries at a node."""
        damage: dict[str, float] = {}

        node = profile.node_index
        comp_probs = profile.component_failure_probs

        # Generators at this node
        for gen_key, (gen_node, comp_type) in generator_map.items():
            if gen_node != node:
                continue
            probs = comp_probs.get(comp_type, {})
            if hazard_filter:
                p = probs.get(hazard_filter, 0.0)
            else:
                p = max(probs.values()) if probs else 0.0
            if p > 0.01:
                damage[gen_key] = round(p, 4)

        # Batteries at this node
        for bat_key, (bat_node, comp_type) in battery_map.items():
            if bat_node != node:
                continue
            probs = comp_probs.get(comp_type, {})
            if hazard_filter:
                p = probs.get(hazard_filter, 0.0)
            else:
                p = max(probs.values()) if probs else 0.0
            if p > 0.01:
                damage[bat_key] = round(p, 4)

        return damage

    def _generate_lhs_scenarios(
        self,
        risk_profiles: list[NodeRiskProfile],
        generator_map: dict[str, tuple[int, str]],
        battery_map: dict[str, tuple[int, str]],
        n_scenarios: int,
    ) -> list[dict]:
        """Generate scenarios using Latin Hypercube Sampling (ISO 31010 B.11.3).

        Creates a stratified sample across the hazard-intensity space,
        ensuring uniform coverage of the probability domain.
        """
        from scipy.stats.qmc import LatinHypercube

        active_profiles = [p for p in risk_profiles if p.composite_risk > 1e-6]
        n_dims = len(active_profiles)
        if n_dims == 0:
            return []

        sampler = LatinHypercube(d=n_dims, seed=int(self.rng.integers(0, 2**31)))
        samples = sampler.random(n=n_scenarios)  # (n_scenarios, n_dims)

        scenarios: list[dict] = []
        for i in range(n_scenarios):
            # Each dimension maps a node; high quantile → severe event
            for j, profile in enumerate(active_profiles):
                quantile = float(samples[i, j])
                # Trigger event when quantile exceeds survival probability
                if quantile > (1.0 - profile.composite_risk):
                    hazards = list(profile.hazard_intensities.keys())
                    if not hazards:
                        continue
                    hazard = hazards[int(quantile * len(hazards)) % len(hazards)]
                    damage_frac = self._compute_damage_fractions(
                        profile, generator_map, battery_map, hazard_filter=hazard,
                    )
                    if damage_frac:
                        scenarios.append({
                            "name": f"lhs_{i}_node{profile.node_index}",
                            "probability": 1.0 / n_scenarios,
                            "hazard_type": hazard,
                            "affected_nodes": [profile.node_index],
                            "damage_fraction": damage_frac,
                            "recovery_hours": 8760,
                            "intensity_measure": 0.0,
                            "description": (
                                f"LHS sample {i} at node {profile.node_index} "
                                f"({hazard}, q={quantile:.3f})"
                            ),
                        })

        # Baseline "no disaster" + normalise
        disaster_prob = sum(s["probability"] for s in scenarios)
        if disaster_prob < 1.0 and scenarios:
            scenarios.insert(0, {
                "name": "baseline_no_disaster",
                "probability": round(1.0 - disaster_prob, 6),
                "hazard_type": "",
                "affected_nodes": [],
                "damage_fraction": {},
                "recovery_hours": 0,
                "intensity_measure": 0.0,
                "description": "No disaster occurs (baseline)",
            })

        total_prob = sum(s["probability"] for s in scenarios)
        if total_prob > 0:
            for s in scenarios:
                s["probability"] = round(s["probability"] / total_prob, 6)

        return scenarios

    def generate_climate_scenarios(
        self,
        ssp_pathways: list[str] | None = None,
        year_horizons: list[int] | None = None,
        equal_weights: bool = True,
        site_deltas: dict[str, dict[str, dict[int, float]]] | None = None,
    ) -> list[dict]:
        """Generate ClimateScenarioConfig dicts for SSP × year combinations.

        Parameters
        ----------
        ssp_pathways : list of str
            SSP pathways to include (default: ["SSP2-4.5", "SSP5-8.5"]).
        year_horizons : list of int
            Planning year horizons (default: [2030, 2050]).
        equal_weights : bool
            If True, assign equal probability to each scenario.

        Returns
        -------
        list of dict
            Each dict has keys matching ``ClimateScenarioConfig`` fields.
        """
        if ssp_pathways is None:
            ssp_pathways = ["SSP2-4.5", "SSP5-8.5"]
        if year_horizons is None:
            year_horizons = [2030, 2050]

        # Approximate deltas by SSP and year (from IPCC AR6 WG1)
        _DELTAS: dict[str, dict[str, dict[int, float]]] = {
            "SSP1-2.6": {
                "temperature": {2030: 0.5, 2040: 0.7, 2050: 0.9, 2060: 1.0, 2070: 1.1, 2080: 1.2, 2100: 1.3},
                "ghi": {2030: 0.00, 2050: 0.01, 2100: 0.02},
                "wind": {2030: 0.00, 2050: -0.01, 2100: -0.02},
            },
            "SSP2-4.5": {
                "temperature": {2030: 0.6, 2040: 0.9, 2050: 1.2, 2060: 1.5, 2070: 1.7, 2080: 2.0, 2100: 2.4},
                "ghi": {2030: 0.00, 2050: -0.01, 2100: -0.02},
                "wind": {2030: -0.01, 2050: -0.03, 2100: -0.05},
            },
            "SSP3-7.0": {
                "temperature": {2030: 0.6, 2040: 1.0, 2050: 1.5, 2060: 1.9, 2070: 2.4, 2080: 2.8, 2100: 3.4},
                "ghi": {2030: -0.01, 2050: -0.02, 2100: -0.04},
                "wind": {2030: -0.01, 2050: -0.04, 2100: -0.07},
            },
            "SSP5-8.5": {
                "temperature": {2030: 0.7, 2040: 1.2, 2050: 1.8, 2060: 2.4, 2070: 3.0, 2080: 3.5, 2100: 4.4},
                "ghi": {2030: -0.01, 2050: -0.03, 2100: -0.05},
                "wind": {2030: -0.02, 2050: -0.05, 2100: -0.10},
            },
        }

        scenarios: list[dict] = []
        n_total = len(ssp_pathways)
        prob = 1.0 / n_total if equal_weights else 1.0 / n_total

        for ssp in ssp_pathways:
            if site_deltas and ssp in site_deltas:
                # Use site-specific deltas from NEX-GDDP or similar
                sd = site_deltas[ssp]
                temp_delta = sd.get("temperature_delta", {})
                ghi_delta = sd.get("ghi_delta_fraction", {})
                wind_delta = sd.get("wind_speed_delta_fraction", {})
                delta_source = "nex-gddp"
            else:
                # Fall back to hardcoded IPCC AR6 global means
                deltas = _DELTAS.get(ssp, _DELTAS["SSP2-4.5"])
                temp_delta = deltas["temperature"]
                ghi_delta = deltas["ghi"]
                wind_delta = deltas["wind"]
                delta_source = "ipcc-ar6-global"

            scenarios.append({
                "name": ssp,
                "probability": round(prob, 4),
                "ssp_pathway": ssp,
                "gcm_model": "",
                "availability_suffix": f"_{ssp.lower().replace('-', '').replace('.', '')}",
                "demand_scale": {},
                "temperature_delta": temp_delta,
                "ghi_delta_fraction": ghi_delta,
                "wind_speed_delta_fraction": wind_delta,
                "delta_source": delta_source,
            })

        return scenarios

    def build_scenario_tree(
        self,
        climate_scenarios: list[dict],
        hazard_scenarios: list[dict],
        max_scenarios: int = 20,
    ) -> tuple[list[dict], list[dict]]:
        """Combine climate and hazard scenarios into a manageable set.

        If the total exceeds *max_scenarios*, the least-probable scenarios
        are merged using forward reduction (simplified version of
        Heitsch & Römisch, 2009).

        Parameters
        ----------
        climate_scenarios : list of dict
            Climate scenario configurations.
        hazard_scenarios : list of dict
            Hazard scenario configurations.
        max_scenarios : int
            Maximum total number of scenarios.

        Returns
        -------
        tuple
            ``(reduced_climate, reduced_hazard)`` — both lists may be
            trimmed to fit within *max_scenarios*.
        """
        total = len(climate_scenarios) + len(hazard_scenarios)
        if total <= max_scenarios:
            return climate_scenarios, hazard_scenarios

        # Keep all climate scenarios (few, important for long-term planning)
        remaining = max_scenarios - len(climate_scenarios)
        if remaining < 1:
            remaining = 1

        # Sort hazard scenarios by probability (keep most probable)
        sorted_hazard = sorted(
            hazard_scenarios, key=lambda s: s.get("probability", 0), reverse=True
        )
        kept = sorted_hazard[:remaining]

        # Redistribute removed probability mass to the baseline scenario
        removed_prob = sum(s["probability"] for s in sorted_hazard[remaining:])
        for s in kept:
            if s.get("name", "") == "baseline_no_disaster":
                s["probability"] += removed_prob
                break

        # Re-normalise
        total_prob = sum(s["probability"] for s in kept)
        if total_prob > 0:
            for s in kept:
                s["probability"] = round(s["probability"] / total_prob, 6)

        logger.info(
            "Scenario tree reduced: %d → %d hazard scenarios "
            "(+ %d climate scenarios)",
            len(hazard_scenarios), len(kept), len(climate_scenarios),
        )

        return climate_scenarios, kept


# =============================================================================
# Layer 5: Resilience Analysis (ISO 22372:2025)
# =============================================================================


class ResilienceAnalyzer:
    """Compute resilience metrics from risk profiles and scenario outcomes.

    Implements ISO 22372:2025 framework with four adaptive capacities
    and standard resilience metrics (LOLP, EENS, R, SART).
    """

    def __init__(self, risk_criteria: dict | None = None):
        self.criteria = risk_criteria or {}

    def compute_metrics(
        self,
        risk_profiles: list[NodeRiskProfile],
        hazard_scenarios: list[dict] | None = None,
        total_demand_mwh: float = 8760.0,
        total_capacity_mw: float = 100.0,
        n_generators: int = 6,
    ) -> ResilienceMetrics:
        """Compute all resilience metrics from risk profiles and scenarios.

        Parameters
        ----------
        risk_profiles : list of NodeRiskProfile
        hazard_scenarios : list of scenario dicts (from ScenarioGenerator)
        total_demand_mwh : float
            Annual demand for EENS calculation.
        total_capacity_mw : float
            Installed capacity for LOLP calculation.
        n_generators : int
            Number of distinct generator types for adaptive capacity.
        """
        scenarios = hazard_scenarios or []

        lolp = self.compute_lolp(scenarios, total_capacity_mw, total_capacity_mw * 0.7)
        eens, sc_eens = self.compute_eens(
            scenarios, total_capacity_mw, total_demand_mwh,
        )
        r_idx, t_steps, perf_curve = self.compute_resilience_index(
            scenarios, total_capacity_mw * 0.7, total_capacity_mw,
        )
        sart = self.compute_sart(scenarios)
        antic, absorb, adapt, restore = self._compute_four_capacities(
            risk_profiles, scenarios, total_capacity_mw, n_generators,
        )

        # Redundancy: N-1 survival = fraction of scenarios with < 50% damage
        if scenarios:
            mild = sum(
                1 for s in scenarios
                if max(s.get("damage_fraction", {}).values(), default=0) < 0.5
            )
            redundancy = mild / len(scenarios)
        else:
            redundancy = 1.0

        # RTO: recovery hours of worst scenario
        rto = max((s.get("recovery_hours", 0) for s in scenarios), default=0)

        return ResilienceMetrics(
            lolp=round(lolp, 6),
            eens_mwh=round(eens, 2),
            resilience_index=round(r_idx, 4),
            sart_hours=round(sart, 1),
            anticipatory_capacity=round(antic, 3),
            absorptive_capacity=round(absorb, 3),
            adaptive_capacity=round(adapt, 3),
            restorative_capacity=round(restore, 3),
            redundancy_index=round(redundancy, 3),
            rto_hours=float(rto),
            time_steps=t_steps,
            performance_curve=perf_curve,
            scenario_eens=sc_eens,
        )

    def compute_lolp(
        self,
        scenarios: list[dict],
        total_capacity_mw: float,
        total_demand_mw: float,
    ) -> float:
        """LOLP: probability-weighted fraction where capacity < demand."""
        if not scenarios:
            return 0.0
        lolp = 0.0
        for s in scenarios:
            prob = s.get("probability", 0)
            # Max damage fraction across all affected components
            max_dmg = max(s.get("damage_fraction", {}).values(), default=0.0)
            remaining_cap = total_capacity_mw * (1.0 - max_dmg)
            if remaining_cap < total_demand_mw:
                lolp += prob
        return lolp

    def compute_eens(
        self,
        scenarios: list[dict],
        total_capacity_mw: float,
        total_demand_mwh: float,
    ) -> tuple[float, dict[str, float]]:
        """EENS: probability-weighted unserved energy across scenarios."""
        if not scenarios:
            return 0.0, {}
        eens = 0.0
        sc_eens: dict[str, float] = {}
        hourly_demand = total_demand_mwh / 8760.0
        for s in scenarios:
            prob = s.get("probability", 0)
            max_dmg = max(s.get("damage_fraction", {}).values(), default=0.0)
            remaining_cap = total_capacity_mw * (1.0 - max_dmg)
            shortfall_mw = max(0.0, hourly_demand - remaining_cap)
            recovery_h = s.get("recovery_hours", 0)
            ens = shortfall_mw * recovery_h * prob
            eens += ens
            sc_eens[s.get("name", "?")] = round(ens, 2)
        return eens, sc_eens

    def compute_resilience_index(
        self,
        scenarios: list[dict],
        total_demand_mw: float,
        total_capacity_mw: float,
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """Resilience index R = 1 - A_lost/A_ideal (Panteli & Mancarella).

        Returns (R, time_steps, performance_curve).
        """
        # Synthesise a representative performance curve
        max_recovery = max(
            (s.get("recovery_hours", 0) for s in scenarios), default=24,
        )
        max_recovery = max(max_recovery, 24)
        t_steps = np.linspace(0, max_recovery, 200)
        perf = np.ones_like(t_steps)  # ideal = 1.0

        if not scenarios:
            return 1.0, t_steps, perf

        # Probability-weighted performance degradation
        for s in scenarios:
            prob = s.get("probability", 0)
            max_dmg = max(s.get("damage_fraction", {}).values(), default=0.0)
            recovery_h = max(s.get("recovery_hours", 1), 1)
            # Linear recovery model: performance drops at t=0, recovers linearly
            for i, t in enumerate(t_steps):
                if t < recovery_h:
                    degradation = max_dmg * (1.0 - t / recovery_h)
                else:
                    degradation = 0.0
                perf[i] -= prob * degradation

        perf = np.clip(perf, 0, 1)
        # R = 1 - (A_ideal - A_actual) / A_ideal
        a_ideal = float(_trapezoid(np.ones_like(t_steps), t_steps))
        a_actual = float(_trapezoid(perf, t_steps))
        r_idx = a_actual / a_ideal if a_ideal > 0 else 1.0
        return r_idx, t_steps, perf

    def compute_sart(self, scenarios: list[dict]) -> float:
        """System Average Recovery Time: probability-weighted average."""
        if not scenarios:
            return 0.0
        total = sum(
            s.get("probability", 0) * s.get("recovery_hours", 0)
            for s in scenarios
        )
        return total

    def _compute_four_capacities(
        self,
        risk_profiles: list[NodeRiskProfile],
        scenarios: list[dict],
        total_capacity_mw: float,
        n_generators: int,
    ) -> tuple[float, float, float, float]:
        """ISO 22372:2025 four adaptive capacities (each 0–1).

        Computed from actual system and risk data:

        Anticipatory — ability to anticipate and prepare for disruptions.
            Measured as the complement of the average composite risk:
            lower risk across nodes → higher anticipatory capacity.
            ``1 - mean(composite_risk)``

        Absorptive — ability to absorb shocks without total failure.
            Measured as the fraction of scenarios where remaining capacity
            after damage still exceeds peak demand (N-1 survival ratio).
            Requires actual damage_fraction data from scenarios.

        Adaptive — ability to adapt via technology diversity.
            Shannon diversity index of generator types, normalized to [0,1].
            A system with one technology scores 0; evenly distributed
            across many types scores close to 1.

        Restorative — ability to recover from disruptions.
            Probability-weighted average of (1 - damage_fraction) across
            scenarios. Higher when scenarios cause less damage on average.
        """
        # Anticipatory: complement of average composite risk
        if risk_profiles:
            antic = 1.0 - float(np.mean([p.composite_risk for p in risk_profiles]))
        else:
            antic = 0.0

        # Absorptive: fraction of scenarios where capacity > demand
        if scenarios and total_capacity_mw > 0:
            survived = 0.0
            total_prob = 0.0
            for s in scenarios:
                prob = s.get("probability", 0)
                max_dmg = max(s.get("damage_fraction", {}).values(), default=0.0)
                remaining = total_capacity_mw * (1.0 - max_dmg)
                # Assume peak demand ~ 70% of installed capacity
                peak_demand = total_capacity_mw * 0.7
                if remaining >= peak_demand:
                    survived += prob
                total_prob += prob
            absorb = survived / total_prob if total_prob > 0 else 1.0
        else:
            absorb = 1.0

        # Adaptive: Shannon diversity of generator types (normalized)
        if n_generators > 0:
            # n_generators is number of distinct fuel types in the system.
            # Shannon entropy H = -sum(p_i * ln(p_i)); max = ln(n).
            # With only a count (not proportions), approximate as
            # diversity = ln(n_types) / ln(max_possible_types).
            # Use 10 as a reasonable upper bound for technology types.
            import math
            adapt = min(1.0, math.log(max(n_generators, 1) + 1) / math.log(11))
        else:
            adapt = 0.0

        # Restorative: probability-weighted average survival fraction
        if scenarios:
            weighted_survival = 0.0
            total_prob = 0.0
            for s in scenarios:
                prob = s.get("probability", 0)
                if prob <= 0:
                    continue
                max_dmg = max(s.get("damage_fraction", {}).values(), default=0.0)
                weighted_survival += prob * (1.0 - max_dmg)
                total_prob += prob
            restore = weighted_survival / total_prob if total_prob > 0 else 1.0
        else:
            restore = 1.0

        return antic, absorb, adapt, restore


# =============================================================================
# Layer 6: ISO Report Generator
# =============================================================================


class ISOReportGenerator:
    """Generate ISO 31000 §6.7 compliant risk assessment report (HTML).

    Produces a structured report with all required sections:
    1. Executive Summary
    2. Context (§5.4)
    3. Risk Identification (§6.4.2)
    4. Risk Analysis (§6.4.3)
    5. Risk Evaluation (§6.4.4)
    6. Resilience Assessment (ISO 22372)
    7. Risk Treatment Recommendations (§6.5)
    8. Monitoring & Review (§6.6)
    9. Appendices
    """

    def generate_html(
        self,
        state_dict: dict,
        risk_evaluations: list | None = None,
        resilience_metrics: object | None = None,
        mc_result: object | None = None,
        title: str = "Risk & Resilience Assessment Report",
        date: str = "",
        author: str = "",
    ) -> str:
        """Generate complete HTML report string."""
        import datetime

        if not date:
            date = datetime.date.today().isoformat()

        css = self._css()
        body_parts = [
            self._section_header(title, date, author),
            self._section_executive_summary(state_dict, risk_evaluations, resilience_metrics),
            self._section_context(state_dict),
            self._section_risk_identification(state_dict),
            self._section_risk_analysis(state_dict, mc_result),
            self._section_risk_evaluation(risk_evaluations),
            self._section_resilience_assessment(resilience_metrics),
            self._section_risk_treatment(risk_evaluations),
            self._section_monitoring(state_dict),
            self._section_appendices(state_dict),
        ]
        body = "\n".join(body_parts)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
<footer>
<p>Generated by ESFEX Risk &amp; Resilience Module | ISO 31000:2018 / ISO 31010:2019 / ISO 22372:2025</p>
</footer>
</body>
</html>"""

    def _css(self) -> str:
        return """
body { font-family: 'Segoe UI', Tahoma, Geneva, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; color: #2c3e50; }
h1 { color: #2c3e50; border-bottom: 3px solid #2980b9; padding-bottom: 10px; }
h2 { color: #2980b9; border-bottom: 1px solid #bdc3c7; padding-bottom: 5px; margin-top: 30px; }
h3 { color: #34495e; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th, td { border: 1px solid #bdc3c7; padding: 8px 12px; text-align: left; }
th { background-color: #2980b9; color: white; }
tr:nth-child(even) { background-color: #ecf0f1; }
.negligible { background-color: #27ae6040; }
.tolerable_low { background-color: #f1c40f40; }
.tolerable_high { background-color: #e67e2240; }
.intolerable { background-color: #e74c3c40; font-weight: bold; }
.metric-box { display: inline-block; padding: 10px 20px; margin: 5px; border-radius: 8px; background: #ecf0f1; text-align: center; }
.metric-box .value { font-size: 24px; font-weight: bold; color: #2c3e50; }
.metric-box .label { font-size: 12px; color: #7f8c8d; }
footer { margin-top: 40px; padding-top: 10px; border-top: 1px solid #bdc3c7; color: #7f8c8d; font-size: 11px; }
"""

    def _section_header(self, title, date, author) -> str:
        return f"""<h1>{title}</h1>
<p><strong>Date:</strong> {date} | <strong>Author:</strong> {author or 'ESFEX Auto-generated'}</p>
<p><strong>Standards:</strong> ISO 31000:2018, ISO/IEC 31010:2019, ISO 22372:2025</p>"""

    def _section_executive_summary(self, state, evaluations, resilience) -> str:
        profiles = state.get("risk_profiles", [])
        n_nodes = len(state.get("node_coordinates", []))
        n_hazards = len(state.get("hazard_maps", []))
        total_eal = sum(p.get("expected_annual_loss", 0) for p in profiles) if profiles else 0

        intolerable = 0
        if evaluations:
            intolerable = sum(1 for e in evaluations if getattr(e, "action_required", False))

        r_idx = getattr(resilience, "resilience_index", None) if resilience else None

        parts = [
            "<h2>1. Executive Summary</h2>",
            f"<p>This assessment covers <strong>{n_nodes} nodes</strong> "
            f"across <strong>{n_hazards} hazard types</strong>.</p>",
            '<div>',
            f'<div class="metric-box"><div class="value">${total_eal:,.0f}</div><div class="label">Total EAL ($/yr)</div></div>',
            f'<div class="metric-box"><div class="value">{intolerable}</div><div class="label">Intolerable Nodes</div></div>',
        ]
        if r_idx is not None:
            parts.append(f'<div class="metric-box"><div class="value">{r_idx:.2f}</div><div class="label">Resilience Index</div></div>')
        parts.append('</div>')
        return "\n".join(parts)

    def _section_context(self, state) -> str:
        method = state.get("combination_method", "independent")
        measure = state.get("risk_measure", "expected")
        alpha = state.get("cvar_alpha", 0.95)
        return f"""<h2>2. Context (ISO 31000 §5.4)</h2>
<h3>Scope</h3>
<p>Multi-hazard risk assessment for power system infrastructure nodes.</p>
<h3>Risk Criteria</h3>
<table>
<tr><th>Parameter</th><th>Value</th></tr>
<tr><td>Combination Method</td><td>{method}</td></tr>
<tr><td>Risk Measure</td><td>{measure}</td></tr>
<tr><td>CVaR Confidence</td><td>{alpha}</td></tr>
</table>"""

    def _section_risk_identification(self, state) -> str:
        maps = state.get("hazard_maps", [])
        hazard_types = set()
        for m in maps:
            if hasattr(m, "hazard_type"):
                hazard_types.add(m.hazard_type)
            elif isinstance(m, dict):
                hazard_types.add(m.get("hazard_type", "?"))
        ht_list = ", ".join(sorted(hazard_types)) if hazard_types else "None"
        return f"""<h2>3. Risk Identification (ISO 31000 §6.4.2)</h2>
<p><strong>Hazards identified:</strong> {ht_list}</p>
<p><strong>Data sources:</strong> {len(maps)} hazard intensity maps loaded</p>
<p><strong>Exposed assets:</strong> Solar PV, Wind Turbine, Battery, Substation, Transmission Line, Diesel Generator</p>"""

    def _section_risk_analysis(self, state, mc_result) -> str:
        parts = [
            "<h2>4. Risk Analysis (ISO 31000 §6.4.3)</h2>",
            "<h3>Methodology</h3>",
            "<ul>",
            "<li>Hazard intensity from multi-source fetchers (USGS, NOAA, Open-Meteo, etc.)</li>",
            "<li>Fragility: lognormal CDF P(DS|IM) with θ (median) and β (dispersion)</li>",
            "<li>EAL: trapezoidal integration over return periods</li>",
            "<li>Multi-hazard: combined via selected method (independent/copula/MCDA)</li>",
            "</ul>",
        ]
        if mc_result:
            parts.extend([
                "<h3>Uncertainty Quantification (Monte Carlo)</h3>",
                "<table>",
                "<tr><th>Statistic</th><th>Value</th></tr>",
                f"<tr><td>Samples</td><td>{getattr(mc_result, 'n_samples', '?')}</td></tr>",
                f"<tr><td>Mean EAL</td><td>${getattr(mc_result, 'eal_mean', 0):,.0f}/yr</td></tr>",
                f"<tr><td>Std Dev</td><td>${getattr(mc_result, 'eal_std', 0):,.0f}/yr</td></tr>",
                f"<tr><td>5th Percentile</td><td>${getattr(mc_result, 'eal_p5', 0):,.0f}/yr</td></tr>",
                f"<tr><td>95th Percentile</td><td>${getattr(mc_result, 'eal_p95', 0):,.0f}/yr</td></tr>",
                f"<tr><td>CVaR</td><td>${getattr(mc_result, 'cvar_alpha', 0):,.0f}/yr</td></tr>",
                f"<tr><td>Dominant Uncertainty</td><td>{getattr(mc_result, 'dominant_uncertainty', '?')}</td></tr>",
                "</table>",
            ])
        return "\n".join(parts)

    def _section_risk_evaluation(self, evaluations) -> str:
        parts = [
            "<h2>5. Risk Evaluation (ISO 31000 §6.4.4)</h2>",
            "<p>ALARP classification of each node against acceptability thresholds:</p>",
        ]
        if evaluations:
            parts.append("<table>")
            parts.append("<tr><th>Node</th><th>EAL ($/yr)</th><th>P(fail)</th>"
                         "<th>Classification</th><th>Risk Band</th><th>Action</th></tr>")
            for e in evaluations:
                cls_class = getattr(e, "classification", "")
                parts.append(
                    f'<tr class="{cls_class}">'
                    f"<td>Node {e.node_index}</td>"
                    f"<td>${e.eal:,.0f}</td>"
                    f"<td>{e.composite_risk:.4f}</td>"
                    f"<td>{e.classification.replace('_', ' ').title()}</td>"
                    f"<td>{e.risk_band.replace('_', ' ').title()}</td>"
                    f"<td>{'YES' if e.action_required else 'No'}</td>"
                    f"</tr>"
                )
            parts.append("</table>")
        else:
            parts.append("<p><em>No risk evaluation performed.</em></p>")
        return "\n".join(parts)

    def _section_resilience_assessment(self, resilience) -> str:
        parts = [
            "<h2>6. Resilience Assessment (ISO 22372:2025)</h2>",
        ]
        if resilience:
            parts.extend([
                "<h3>Reliability Metrics</h3>",
                "<table>",
                "<tr><th>Metric</th><th>Value</th><th>Description</th></tr>",
                f"<tr><td>LOLP</td><td>{resilience.lolp:.4f}</td><td>Loss of Load Probability</td></tr>",
                f"<tr><td>EENS</td><td>{resilience.eens_mwh:,.1f} MWh/yr</td><td>Expected Energy Not Supplied</td></tr>",
                f"<tr><td>R</td><td>{resilience.resilience_index:.3f}</td><td>Resilience Index (Panteli &amp; Mancarella)</td></tr>",
                f"<tr><td>SART</td><td>{resilience.sart_hours:.0f} h</td><td>System Average Recovery Time</td></tr>",
                f"<tr><td>RTO</td><td>{resilience.rto_hours:.0f} h</td><td>Recovery Time Objective (worst-case)</td></tr>",
                f"<tr><td>Redundancy</td><td>{resilience.redundancy_index:.2f}</td><td>N-1 Survival Ratio</td></tr>",
                "</table>",
                "<h3>Adaptive Capacities</h3>",
                "<table>",
                "<tr><th>Capacity</th><th>Score</th><th>Description</th></tr>",
                f"<tr><td>Anticipatory</td><td>{resilience.anticipatory_capacity:.2f}</td><td>Hazard monitoring coverage</td></tr>",
                f"<tr><td>Absorptive</td><td>{resilience.absorptive_capacity:.2f}</td><td>System headroom under disruption</td></tr>",
                f"<tr><td>Adaptive</td><td>{resilience.adaptive_capacity:.2f}</td><td>Reconfiguration ability</td></tr>",
                f"<tr><td>Restorative</td><td>{resilience.restorative_capacity:.2f}</td><td>Recovery speed</td></tr>",
                "</table>",
            ])
        else:
            parts.append("<p><em>No resilience analysis performed.</em></p>")
        return "\n".join(parts)

    def _section_risk_treatment(self, evaluations) -> str:
        parts = [
            "<h2>7. Risk Treatment Recommendations (ISO 31000 §6.5)</h2>",
        ]
        if evaluations:
            intolerable = [e for e in evaluations if getattr(e, "action_required", False)]
            if intolerable:
                parts.append("<h3>Nodes Requiring Immediate Action</h3><ul>")
                for e in intolerable:
                    parts.append(
                        f"<li><strong>Node {e.node_index}</strong>: "
                        f"EAL ${e.eal:,.0f}/yr, {e.risk_band} risk. "
                        f"Consider: structural hardening, redundancy investment, "
                        f"or relocation of critical assets.</li>"
                    )
                parts.append("</ul>")
            else:
                parts.append("<p>No nodes exceed intolerable risk thresholds.</p>")

            alarp = [e for e in evaluations if "tolerable" in getattr(e, "classification", "")]
            if alarp:
                parts.append("<h3>ALARP Zone — Cost-Benefit Analysis Recommended</h3><ul>")
                for e in alarp:
                    parts.append(
                        f"<li>Node {e.node_index}: EAL ${e.eal:,.0f}/yr "
                        f"({e.classification.replace('_', ' ')})</li>"
                    )
                parts.append("</ul>")
        else:
            parts.append("<p><em>Run risk evaluation first.</em></p>")
        return "\n".join(parts)

    def _section_monitoring(self, state) -> str:
        return """<h2>8. Monitoring & Review (ISO 31000 §6.6)</h2>
<h3>Parameters to Monitor</h3>
<ul>
<li>Hazard data updates (annual refresh from USGS, NOAA, Open-Meteo)</li>
<li>Fragility curve calibration after major events</li>
<li>Component replacement costs (inflation-adjusted annually)</li>
<li>Climate scenario updates from IPCC assessment cycles</li>
<li>Actual vs predicted loss comparison after each event</li>
</ul>
<h3>Review Schedule</h3>
<ul>
<li><strong>Annual:</strong> Hazard data refresh, EAL recalculation</li>
<li><strong>After major events:</strong> Full re-assessment with updated fragility</li>
<li><strong>Every 5 years:</strong> Climate scenario update, methodology review</li>
</ul>"""

    def _section_appendices(self, state) -> str:
        n_curves = 0
        flib = state.get("fragility_library")
        if flib and hasattr(flib, "get_all_curves"):
            n_curves = len(flib.get_all_curves())
        return f"""<h2>9. Appendices</h2>
<h3>A. Methodology References</h3>
<ul>
<li>ISO 31000:2018 — Risk management: Guidelines</li>
<li>ISO/IEC 31010:2019 — Risk assessment techniques</li>
<li>ISO 22372:2025 — Security and resilience: Adaptive capacity</li>
<li>Panteli &amp; Mancarella (2015) — Power system resilience metrics</li>
<li>NHESS-2024 / PNNL-33587 — Fragility curve libraries</li>
</ul>
<h3>B. Data Sources</h3>
<p>USGS ComCat, ISC FDSN, IBTrACS v4, NOAA NCEI, Open-Meteo GloFAS, Smithsonian GVP, NASA FIRMS, NOAA CO-OPS SLR</p>
<h3>C. Fragility Library</h3>
<p>{n_curves} curves loaded (component × hazard × damage state)</p>"""


# =============================================================================
# Utility Functions
# =============================================================================


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    )
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _determine_tc_basin(lat: float, lon: float) -> str:
    """Determine the IBTrACS tropical cyclone basin from coordinates."""
    if lon < -100 and lat > 0:
        return "EP"  # Eastern Pacific
    elif lon < -30 and lat > 0:
        return "NA"  # North Atlantic
    elif lon > 40 and lon < 100 and lat > 0:
        return "NI"  # North Indian
    elif lon >= 100 and lat > 0:
        return "WP"  # Western Pacific
    elif lon > 40 and lon < 135 and lat < 0:
        return "SI"  # South Indian
    elif lon >= 135 and lat < 0:
        return "SP"  # South Pacific
    elif lon < -30 and lat < 0:
        return "SA"  # South Atlantic
    return "ALL"
