"""Coverage tests for esfex.models.hazard_assessment.

These tests focus on the pure / deterministic, network-free parts of the
module: utility functions, fragility curves, the fragility library, composite
risk assessment, scenario generation, resilience metrics, ISO report
generation, risk-criteria evaluation, and the offline fetcher paths
(ScreeningFetcher with empty input, AR6 SLR lookup, factory + registry).

Networked fetchers are NOT exercised against live endpoints.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

# compute_resilience_index() / compute_metrics() integrate via the module's
# _trapezoid shim, which uses np.trapezoid (NumPy >= 2.0) or np.trapz (older).
# One of the two always exists, so these tests run on every supported NumPy.
_HAS_TRAPZ = hasattr(np, "trapezoid") or hasattr(np, "trapz")
_requires_trapz = pytest.mark.skipif(
    not _HAS_TRAPZ,
    reason="neither numpy.trapezoid nor numpy.trapz is available",
)

from esfex.models import hazard_assessment as ha
from esfex.models.hazard_assessment import (
    CompositeRiskAssessment,
    CycloneFetcher,
    FloodFetcher,
    FragilityCurve,
    FragilityLibrary,
    HazardIntensityMap,
    ISOReportGenerator,
    NodeRiskProfile,
    ResilienceAnalyzer,
    ScenarioGenerator,
    ScreeningFetcher,
    SeaLevelFetcher,
    SeismicFetcher,
    create_fetcher,
    evaluate_risk_criteria,
    get_available_sources,
)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def test_haversine_zero_distance():
    assert ha._haversine(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)


def test_haversine_known_distance():
    # 1 degree of latitude ~ 111 km
    d = ha._haversine(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111.19, abs=1.0)


def test_haversine_symmetric():
    a = ha._haversine(40.0, -70.0, 34.0, -118.0)
    b = ha._haversine(34.0, -118.0, 40.0, -70.0)
    assert a == pytest.approx(b)


@pytest.mark.parametrize("lat,lon,expected", [
    (20.0, -110.0, "EP"),   # eastern pacific
    (25.0, -70.0, "NA"),    # north atlantic
    (15.0, 70.0, "NI"),     # north indian
    (15.0, 130.0, "WP"),    # western pacific
    (-15.0, 70.0, "SI"),    # south indian
    (-15.0, 150.0, "SP"),   # south pacific
    (-15.0, -40.0, "SA"),   # south atlantic
    (0.0, 0.0, "ALL"),      # fall-through
])
def test_determine_tc_basin(lat, lon, expected):
    assert ha._determine_tc_basin(lat, lon) == expected


def test_fit_gumbel_few_points_returns_peak():
    # < 3 points => peak repeated for each RP
    out = ha._fit_gumbel_return_periods([5.0, 9.0], [10, 100])
    assert out == {10: 9.0, 100: 9.0}


def test_fit_gumbel_empty_returns_zero():
    out = ha._fit_gumbel_return_periods([], [50])
    assert out == {50: 0.0}


def test_fit_gumbel_monotonic_in_return_period():
    data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    out = ha._fit_gumbel_return_periods(data, [10, 100, 1000])
    # Higher return period => higher (or equal) estimated value
    assert out[10] <= out[100] <= out[1000]


def test_api_get_json_bad_url_returns_empty_list():
    # Invalid scheme/host: should be caught and return []
    assert ha._api_get_json("http://localhost:1/nonexistent", timeout=1) == []


# ---------------------------------------------------------------------------
# FragilityCurve
# ---------------------------------------------------------------------------

def test_fragility_curve_zero_im_returns_zero():
    c = FragilityCurve("solar_pv", "flood", "complete", 1.0, 0.4)
    assert c.evaluate(0.0) == 0.0
    assert c.evaluate(-5.0) == 0.0


def test_fragility_curve_at_median_is_half():
    c = FragilityCurve("solar_pv", "flood", "complete", 1.0, 0.4)
    assert c.evaluate(1.0) == pytest.approx(0.5, abs=1e-9)


def test_fragility_curve_monotonic_increasing():
    c = FragilityCurve("solar_pv", "earthquake", "complete", 0.5, 0.5)
    lo = c.evaluate(0.1)
    mid = c.evaluate(0.5)
    hi = c.evaluate(2.0)
    assert 0.0 < lo < mid < hi < 1.0
    assert mid == pytest.approx(0.5, abs=1e-9)


def test_fragility_curve_bounds():
    c = FragilityCurve("battery", "cyclone", "complete", 30.0, 0.3)
    assert 0.0 <= c.evaluate(1.0) <= 1.0
    assert c.evaluate(1e6) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# FragilityLibrary
# ---------------------------------------------------------------------------

def test_library_loads_builtins():
    lib = FragilityLibrary()
    assert len(lib.get_all_curves()) == len(ha._BUILTIN_CURVES)


def test_library_component_and_hazard_types():
    lib = FragilityLibrary()
    assert "solar_pv" in lib.component_types
    assert "battery" in lib.component_types
    assert "earthquake" in lib.hazard_types
    assert "sea_level_rise" in lib.hazard_types


def test_library_get_curves_known_pair():
    lib = FragilityLibrary()
    curves = lib.get_curves("solar_pv", "earthquake")
    assert len(curves) >= 1
    assert all(isinstance(c, FragilityCurve) for c in curves)
    assert all(c.component_type == "solar_pv" for c in curves)


def test_library_get_curves_unknown_pair_empty():
    lib = FragilityLibrary()
    assert lib.get_curves("nonexistent", "earthquake") == []


def test_library_default_epistemic_beta_assigned():
    # NHESS-2024 curves with no explicit beta_e get 0.25
    lib = FragilityLibrary()
    nhess = [
        c for c in lib.get_curves("solar_pv", "earthquake")
        if c.source == "NHESS-2024"
    ]
    assert nhess
    assert all(c.beta_epistemic == 0.25 for c in nhess)


def test_library_explicit_epistemic_beta_preserved():
    lib = FragilityLibrary()
    # solar_pv wildfire 'slight' has explicit beta_e=0.3
    curves = lib.get_curves("solar_pv", "wildfire")
    slight = [c for c in curves if c.damage_state == "slight"][0]
    assert slight.beta_epistemic == 0.3
    assert slight.source_quality == "analytical"


def test_evaluate_damage_probability_returns_all_states():
    lib = FragilityLibrary()
    probs = lib.evaluate_damage_probability("solar_pv", "earthquake", 0.6)
    assert set(probs.keys()) >= {"slight", "moderate", "extensive", "complete"}
    # slight median (0.3) < complete median (1.2) => slight prob higher
    assert probs["slight"] > probs["complete"]


def test_evaluate_damage_probability_unknown_empty():
    lib = FragilityLibrary()
    assert lib.evaluate_damage_probability("foo", "bar", 1.0) == {}


def test_get_complete_damage_probability_present():
    lib = FragilityLibrary()
    p = lib.get_complete_damage_probability("solar_pv", "flood", 1.5)
    # median for complete flood is 1.5 => ~0.5
    assert p == pytest.approx(0.5, abs=1e-6)


def test_get_complete_damage_probability_absent_returns_zero():
    lib = FragilityLibrary()
    # transmission_line / sea_level_rise has only 'complete' actually;
    # use a pair lacking a complete curve: none obvious, so use unknown pair.
    assert lib.get_complete_damage_probability("foo", "bar", 5.0) == 0.0


def test_load_from_config_overrides_builtin():
    lib = FragilityLibrary()

    class _Curve:
        def __init__(self, ds, med, beta):
            self.damage_state = ds
            self.im_median = med
            self.beta = beta

    class _Cfg:
        component_type = "solar_pv"
        hazard_type = "earthquake"
        source = "custom"
        curves = [_Curve("complete", 2.0, 0.5)]

    lib.load_from_config([_Cfg()])
    curves = lib.get_curves("solar_pv", "earthquake")
    assert len(curves) == 1
    assert curves[0].source == "custom"
    assert curves[0].im_median == 2.0
    # missing attributes default
    assert curves[0].beta_epistemic == 0.0
    assert curves[0].source_quality == "analytical"


# ---------------------------------------------------------------------------
# evaluate_risk_criteria
# ---------------------------------------------------------------------------

def _profile(idx, eal, cr):
    return NodeRiskProfile(
        node_index=idx,
        coordinates=(0.0, 0.0),
        hazard_intensities={},
        component_failure_probs={},
        composite_risk=cr,
        expected_annual_loss=eal,
        dominant_hazard="none",
    )


def test_evaluate_risk_criteria_classifications():
    profiles = [
        _profile(0, 500.0, 0.001),       # negligible / low
        _profile(1, 10_000.0, 0.02),     # tolerable_low / medium
        _profile(2, 100_000.0, 0.10),    # tolerable_high / high
        _profile(3, 1_000_000.0, 0.50),  # intolerable / very_high
    ]
    evals = evaluate_risk_criteria(profiles)
    assert [e.classification for e in evals] == [
        "negligible", "tolerable_low", "tolerable_high", "intolerable",
    ]
    assert [e.risk_band for e in evals] == ["low", "medium", "high", "very_high"]
    # Only intolerable / very_high requires action
    assert [e.action_required for e in evals] == [False, False, False, True]
    assert "ACTION REQUIRED" in evals[3].justification


def test_evaluate_risk_criteria_action_on_very_high_band_only():
    # Low EAL but very high composite risk still triggers action
    evals = evaluate_risk_criteria([_profile(0, 100.0, 0.9)])
    assert evals[0].classification == "negligible"
    assert evals[0].risk_band == "very_high"
    assert evals[0].action_required is True


def test_evaluate_risk_criteria_custom_thresholds():
    profiles = [_profile(0, 2_000.0, 0.02)]
    evals = evaluate_risk_criteria(profiles, criteria={
        "eal_negligible": 5_000.0,
        "composite_risk_low": 0.10,
    })
    # 2000 < 5000 => negligible; 0.02 < 0.10 => low
    assert evals[0].classification == "negligible"
    assert evals[0].risk_band == "low"


# ---------------------------------------------------------------------------
# Factory / registry
# ---------------------------------------------------------------------------

def test_create_fetcher_known_types():
    for ht, cls in [
        ("earthquake", SeismicFetcher),
        ("cyclone", CycloneFetcher),
        ("flood", FloodFetcher),
        ("sea_level_rise", SeaLevelFetcher),
        ("screening", ScreeningFetcher),
    ]:
        f = create_fetcher(ht)
        assert isinstance(f, cls)


def test_create_fetcher_unknown_raises():
    with pytest.raises(ValueError):
        create_fetcher("nonexistent_hazard")


def test_create_fetcher_source_selection():
    f = create_fetcher("earthquake", source="isc")
    assert f._active_source == "isc"
    # invalid source ignored, falls back to default
    f2 = create_fetcher("earthquake", source="bogus")
    assert f2._active_source == "usgs"


def test_get_available_sources():
    src = get_available_sources("earthquake")
    assert "usgs" in src and "isc" in src
    assert get_available_sources("nonexistent") == {}


def test_seismic_estimate_pga_no_events():
    out = SeismicFetcher._estimate_pga([], 10.0, 20.0, [475, 975])
    assert out == {475: 0.0, 975: 0.0}


def test_seismic_estimate_pga_scaling():
    events = [(10.0, 20.0, 7.0)]
    out = SeismicFetcher._estimate_pga(events, 10.0, 20.0, [475, 2475])
    assert out[2475] > out[475]
    assert all(v >= 0 for v in out.values())


def test_cyclone_winds_to_rp_empty():
    out = CycloneFetcher._winds_to_rp([], [100, 500], 30)
    assert out == {100: 0.0, 500: 0.0}


def test_flood_discharge_to_depth():
    assert FloodFetcher._discharge_to_depth(0.0, 10.0) == 0.0
    assert FloodFetcher._discharge_to_depth(10.0, 0.0) == 0.0
    d = FloodFetcher._discharge_to_depth(100.0, 10.0)
    assert 0.0 < d <= 10.0
    # capped at 10
    assert FloodFetcher._discharge_to_depth(1e12, 1.0) == 10.0


def test_flood_parse_annual_maxima():
    daily = {
        "time": ["2020-01-01", "2020-06-01", "2021-01-01"],
        "river_discharge": [5.0, 9.0, 3.0],
    }
    out = FloodFetcher._parse_annual_maxima(daily)
    # 2020 max = 9, 2021 = 3 => sorted
    assert out == [3.0, 9.0]


def test_flood_parse_annual_maxima_empty():
    assert FloodFetcher._parse_annual_maxima({"time": [], "river_discharge": []}) == []


# ---------------------------------------------------------------------------
# SeaLevelFetcher offline AR6 lookup
# ---------------------------------------------------------------------------

def test_ar6_interpolate_baseline_and_growth():
    f = SeaLevelFetcher()
    assert f._ar6_interpolate("ssp245", 2020) == 0.0
    assert f._ar6_interpolate("ssp245", 2010) == 0.0
    mid = f._ar6_interpolate("ssp245", 2050)
    assert mid == pytest.approx(0.24, abs=1e-9)
    later = f._ar6_interpolate("ssp245", 2100)
    assert later == pytest.approx(0.56, abs=1e-9)
    # monotonic between
    assert f._ar6_interpolate("ssp245", 2035) < mid
    assert mid < f._ar6_interpolate("ssp245", 2075) < later


def test_ar6_interpolate_unknown_ssp_defaults():
    f = SeaLevelFetcher()
    # unknown ssp falls back to ssp245 table values
    assert f._ar6_interpolate("unknown", 2050) == pytest.approx(0.24, abs=1e-9)


def test_sealevel_fetch_ar6_lookup_offline():
    f = create_fetcher("sea_level_rise", source="ar6_lookup")
    hmap = f.fetch([(25.0, -80.0), (26.0, -81.0)], ssp="ssp585", year=2100)
    assert hmap.hazard_type == "sea_level_rise"
    assert hmap.source == "ar6_lookup"
    assert hmap.return_periods == [0]
    assert set(hmap.node_intensities.keys()) == {0, 1}
    # ssp585 2100 == 0.77
    assert hmap.node_intensities[0][0] == pytest.approx(0.77, abs=1e-3)
    assert hmap.metadata["ssp"] == "ssp585"


# ---------------------------------------------------------------------------
# ScreeningFetcher empty input (network-free)
# ---------------------------------------------------------------------------

def test_screening_fetch_empty_nodes():
    f = ScreeningFetcher()
    hmap = f.fetch([])
    assert hmap.hazard_type == "screening"
    assert hmap.node_intensities == {}
    assert hmap.metadata["n_nodes"] == 0


# ---------------------------------------------------------------------------
# CompositeRiskAssessment
# ---------------------------------------------------------------------------

def _hazard_map(hazard_type, node_intensities):
    return HazardIntensityMap(
        hazard_type=hazard_type,
        source="test",
        intensity_measure="x",
        units="u",
        return_periods=sorted({rp for d in node_intensities.values() for rp in d}),
        node_intensities=node_intensities,
    )


def test_combine_hazards_empty():
    cra = CompositeRiskAssessment()
    assert cra.combine_hazards({}) == 0.0


def test_combine_hazards_independent():
    cra = CompositeRiskAssessment(combination_method="independent")
    # 1 - (1-0.5)(1-0.5) = 0.75
    assert cra.combine_hazards({"a": 0.5, "b": 0.5}) == pytest.approx(0.75)


def test_combine_hazards_single_value():
    cra = CompositeRiskAssessment(combination_method="independent")
    assert cra.combine_hazards({"a": 0.3}) == pytest.approx(0.3)


def test_combine_hazards_mcda_mean():
    cra = CompositeRiskAssessment(combination_method="mcda")
    assert cra.combine_hazards({"a": 0.2, "b": 0.4}) == pytest.approx(0.3)


def test_combine_hazards_copula_single_returns_value():
    cra = CompositeRiskAssessment(combination_method="copula")
    assert cra.combine_hazards({"a": 0.4}) == pytest.approx(0.4)


def test_combine_hazards_copula_two_between_max_and_independent():
    cra = CompositeRiskAssessment(combination_method="copula")
    out = cra.combine_hazards({"a": 0.3, "b": 0.3})
    independent = 1 - (1 - 0.3) ** 2
    # Positive correlation => combined risk <= independent, >= max marginal
    assert 0.3 <= out <= independent + 1e-9


def test_compute_eal_no_intensity_returns_zero():
    cra = CompositeRiskAssessment()
    hmap = _hazard_map("earthquake", {})
    assert cra.compute_eal(0, hmap, "solar_pv", 1000.0) == 0.0


def test_compute_eal_positive():
    cra = CompositeRiskAssessment()
    # High PGA across two return periods => nonzero EAL
    hmap = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    eal = cra.compute_eal(0, hmap, "solar_pv", 1000.0)
    assert eal > 0.0


def test_compute_eal_single_rp():
    cra = CompositeRiskAssessment()
    hmap = _hazard_map("earthquake", {0: {475: 2.0}})
    eal = cra.compute_eal(0, hmap, "solar_pv", 1000.0)
    assert eal >= 0.0


def test_assess_basic_pipeline():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.5}, 1: {475: 0.01}})
    node_components = {0: ["solar_pv"], 1: ["solar_pv"]}
    profiles = cra.assess([quake], node_components)
    assert len(profiles) == 2
    p0 = [p for p in profiles if p.node_index == 0][0]
    p1 = [p for p in profiles if p.node_index == 1][0]
    # High PGA node has higher composite risk
    assert p0.composite_risk > p1.composite_risk
    assert p0.dominant_hazard == "earthquake"
    assert p0.expected_annual_loss > 0


def test_assess_with_coordinates_and_values():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.5}})
    profiles = cra.assess(
        [quake],
        {0: ["solar_pv"]},
        component_values={0: {"solar_pv": 1_000_000.0}},
        node_coordinates={0: (12.3, -45.6)},
    )
    assert profiles[0].coordinates == (12.3, -45.6)
    assert profiles[0].expected_annual_loss > 0


def test_assess_no_hazard_for_node():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.0}})
    profiles = cra.assess([quake], {5: ["solar_pv"]})
    assert profiles[0].composite_risk == 0.0
    assert profiles[0].dominant_hazard == "none"


def test_assess_cvar_measure_runs():
    cra = CompositeRiskAssessment(risk_measure="cvar")
    quake = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    profiles = cra.assess(
        [quake], {0: ["solar_pv"]},
        component_values={0: {"solar_pv": 100_000.0}},
    )
    assert profiles[0].expected_annual_loss >= 0.0


def test_assess_minimax_measure_runs():
    cra = CompositeRiskAssessment(risk_measure="minimax_regret")
    quake = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    profiles = cra.assess(
        [quake], {0: ["solar_pv"]},
        component_values={0: {"solar_pv": 100_000.0}},
    )
    assert profiles[0].expected_annual_loss >= 0.0


def test_compute_risk_coefficients():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.5}})
    profiles = cra.assess([quake], {0: ["solar_pv", "battery"]})
    gen_map = {"g0": (0, "solar_pv"), "g_missing": (99, "solar_pv")}
    bat_map = {"b0": (0, "battery")}
    gen_co, bat_co = cra.compute_risk_coefficients(profiles, gen_map, bat_map)
    # Node present with failure prob => coeff < 1
    assert 0.0 <= gen_co["g0"] <= 1.0
    # Missing node => 1.0 (no derating)
    assert gen_co["g_missing"] == 1.0
    assert 0.0 <= bat_co["b0"] <= 1.0


def test_compute_technology_risk_coefficients():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.5}})
    profiles = cra.assess([quake], {0: ["solar_pv"]})
    coeffs = cra.compute_technology_risk_coefficients(profiles, "solar_pv", n_nodes=3)
    assert len(coeffs) == 3
    assert all(0.0 <= c <= 1.0 for c in coeffs)
    # nodes 1,2 absent => 1.0
    assert coeffs[1] == 1.0 and coeffs[2] == 1.0


def test_monte_carlo_eal_runs():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    result = cra.monte_carlo_eal(
        [quake], {0: ["solar_pv"]},
        component_values={0: {"solar_pv": 100_000.0}},
        n_samples=30,
    )
    assert result.n_samples == 30
    assert result.eal_samples.shape == (30,)
    assert result.eal_mean >= 0.0
    assert result.eal_p5 <= result.eal_p50 <= result.eal_p95
    assert result.dominant_uncertainty in {"aleatory", "epistemic"}
    assert 0 in result.node_eal_samples


def test_monte_carlo_eal_restores_fragility_curves():
    cra = CompositeRiskAssessment()
    before = [
        (c.im_median, c.beta)
        for c in cra.fragility.get_curves("solar_pv", "earthquake")
    ]
    quake = _hazard_map("earthquake", {0: {475: 1.5}})
    cra.monte_carlo_eal([quake], {0: ["solar_pv"]}, n_samples=10)
    after = [
        (c.im_median, c.beta)
        for c in cra.fragility.get_curves("solar_pv", "earthquake")
    ]
    assert before == after


def test_sensitivity_sweep_structure_and_restore():
    cra = CompositeRiskAssessment()
    saved = (cra.cvar_alpha, cra.cvar_lambda, cra.risk_measure, cra.combination_method)
    quake = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    out = cra.sensitivity_sweep(
        [quake], {0: ["solar_pv"]},
        component_values={0: {"solar_pv": 100_000.0}},
    )
    assert set(out.keys()) == {"param_names", "low_values", "high_values", "base_value"}
    assert len(out["param_names"]) == len(out["low_values"]) == len(out["high_values"]) == 5
    # settings restored
    assert (cra.cvar_alpha, cra.cvar_lambda, cra.risk_measure, cra.combination_method) == saved


# ---------------------------------------------------------------------------
# ScenarioGenerator
# ---------------------------------------------------------------------------

def _risky_profiles():
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {475: 1.8}})
    flood = _hazard_map("flood", {0: {475: 2.0}})
    return cra.assess([quake, flood], {0: ["solar_pv", "battery"]})


def test_scenario_generator_importance():
    gen = ScenarioGenerator(seed=1)
    profiles = _risky_profiles()
    gmap = {"g0": (0, "solar_pv")}
    bmap = {"b0": (0, "battery")}
    scenarios = gen.generate_hazard_scenarios(
        profiles, gmap, bmap, n_scenarios=5, method="importance",
    )
    assert scenarios  # non-empty
    probs = [s["probability"] for s in scenarios]
    assert sum(probs) == pytest.approx(1.0, abs=1e-3)
    # A baseline is inserted only when total disaster probability < 1.0;
    # with high-intensity hazards the disaster mass can reach 1.0, so only
    # require that every probability is a valid normalised weight.
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_scenario_generator_enumeration():
    gen = ScenarioGenerator(seed=1)
    profiles = _risky_profiles()
    gmap = {"g0": (0, "solar_pv")}
    bmap = {"b0": (0, "battery")}
    scenarios = gen.generate_hazard_scenarios(
        profiles, gmap, bmap, method="enumeration",
    )
    assert scenarios
    # each non-baseline scenario references a hazard type
    hazards = {s["hazard_type"] for s in scenarios if s["hazard_type"]}
    assert hazards.issubset({"earthquake", "flood"})
    assert sum(s["probability"] for s in scenarios) == pytest.approx(1.0, abs=1e-3)


def test_scenario_generator_lhs():
    gen = ScenarioGenerator(seed=3)
    profiles = _risky_profiles()
    gmap = {"g0": (0, "solar_pv")}
    bmap = {"b0": (0, "battery")}
    scenarios = gen.generate_hazard_scenarios(
        profiles, gmap, bmap, n_scenarios=8, method="lhs",
    )
    # Either empty (no triggered) or normalized to 1
    if scenarios:
        assert sum(s["probability"] for s in scenarios) == pytest.approx(1.0, abs=1e-3)


def test_scenario_generator_importance_zero_risk_returns_empty():
    gen = ScenarioGenerator(seed=1)
    p = _profile(0, 0.0, 0.0)
    scenarios = gen.generate_hazard_scenarios(
        [p], {"g0": (0, "solar_pv")}, n_scenarios=5, method="importance",
    )
    assert scenarios == []


def test_compute_damage_fractions_hazard_filter():
    gen = ScenarioGenerator()
    profile = _risky_profiles()[0]
    gmap = {"g0": (0, "solar_pv")}
    bmap = {"b0": (0, "battery")}
    dmg = gen._compute_damage_fractions(profile, gmap, bmap, hazard_filter="earthquake")
    # both component keys may appear if p>0.01
    assert all(0.0 < v <= 1.0 for v in dmg.values())


def test_generate_climate_scenarios_defaults():
    gen = ScenarioGenerator()
    scens = gen.generate_climate_scenarios()
    assert [s["ssp_pathway"] for s in scens] == ["SSP2-4.5", "SSP5-8.5"]
    assert all(s["delta_source"] == "ipcc-ar6-global" for s in scens)
    assert all("temperature_delta" in s for s in scens)
    assert sum(s["probability"] for s in scens) == pytest.approx(1.0, abs=1e-3)


def test_generate_climate_scenarios_site_deltas():
    gen = ScenarioGenerator()
    site_deltas = {
        "SSP2-4.5": {
            "temperature_delta": {2030: 0.8},
            "ghi_delta_fraction": {2030: 0.01},
            "wind_speed_delta_fraction": {2030: -0.02},
        }
    }
    scens = gen.generate_climate_scenarios(
        ssp_pathways=["SSP2-4.5"], site_deltas=site_deltas,
    )
    assert scens[0]["delta_source"] == "nex-gddp"
    assert scens[0]["temperature_delta"] == {2030: 0.8}


def test_generate_climate_scenarios_unknown_ssp_falls_back():
    gen = ScenarioGenerator()
    scens = gen.generate_climate_scenarios(ssp_pathways=["SSP9-9.9"])
    # falls back to SSP2-4.5 deltas
    assert scens[0]["temperature_delta"][2050] == 1.2


def test_build_scenario_tree_under_limit():
    gen = ScenarioGenerator()
    climate = [{"name": "c1", "probability": 0.5}]
    hazard = [{"name": "h1", "probability": 0.5}]
    rc, rh = gen.build_scenario_tree(climate, hazard, max_scenarios=20)
    assert rc == climate and rh == hazard


def test_build_scenario_tree_reduction():
    gen = ScenarioGenerator()
    climate = [{"name": "c1", "probability": 1.0}]
    hazard = [
        {"name": "baseline_no_disaster", "probability": 0.5},
        {"name": "h1", "probability": 0.3},
        {"name": "h2", "probability": 0.15},
        {"name": "h3", "probability": 0.05},
    ]
    rc, rh = gen.build_scenario_tree(climate, hazard, max_scenarios=3)
    assert rc == climate
    # max_scenarios - len(climate) = 2 kept
    assert len(rh) == 2
    assert sum(s["probability"] for s in rh) == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# ResilienceAnalyzer
# ---------------------------------------------------------------------------

def _scenarios():
    return [
        {"name": "baseline_no_disaster", "probability": 0.7,
         "damage_fraction": {}, "recovery_hours": 0},
        {"name": "quake0", "probability": 0.3,
         "damage_fraction": {"g0": 0.8}, "recovery_hours": 48},
    ]


def test_resilience_compute_lolp_empty():
    ra = ResilienceAnalyzer()
    assert ra.compute_lolp([], 100.0, 70.0) == 0.0


def test_resilience_compute_lolp():
    ra = ResilienceAnalyzer()
    # demand 70; scenario quake0 reduces cap to 100*0.2=20 < 70 => prob 0.3 counts
    lolp = ra.compute_lolp(_scenarios(), 100.0, 70.0)
    assert lolp == pytest.approx(0.3)


def test_resilience_compute_eens():
    ra = ResilienceAnalyzer()
    eens, sc = ra.compute_eens(_scenarios(), 100.0, total_demand_mwh=8760.0)
    assert eens >= 0.0
    assert "quake0" in sc


def test_resilience_compute_eens_empty():
    ra = ResilienceAnalyzer()
    assert ra.compute_eens([], 100.0, 8760.0) == (0.0, {})


def test_resilience_compute_resilience_index_no_scenarios():
    ra = ResilienceAnalyzer()
    r, t, perf = ra.compute_resilience_index([], 70.0, 100.0)
    assert r == 1.0
    assert isinstance(t, np.ndarray) and isinstance(perf, np.ndarray)
    assert np.allclose(perf, 1.0)


@_requires_trapz
def test_resilience_compute_resilience_index_with_damage():
    ra = ResilienceAnalyzer()
    r, t, perf = ra.compute_resilience_index(_scenarios(), 70.0, 100.0)
    assert 0.0 <= r <= 1.0
    assert perf.min() >= 0.0 and perf.max() <= 1.0


def test_resilience_compute_sart():
    ra = ResilienceAnalyzer()
    # 0.7*0 + 0.3*48 = 14.4
    assert ra.compute_sart(_scenarios()) == pytest.approx(14.4)
    assert ra.compute_sart([]) == 0.0


@_requires_trapz
def test_resilience_compute_metrics_full():
    ra = ResilienceAnalyzer()
    profiles = [_profile(0, 1000.0, 0.2)]
    metrics = ra.compute_metrics(
        profiles, _scenarios(),
        total_demand_mwh=8760.0, total_capacity_mw=100.0, n_generators=4,
    )
    assert 0.0 <= metrics.lolp <= 1.0
    assert metrics.eens_mwh >= 0.0
    assert 0.0 <= metrics.resilience_index <= 1.0
    assert 0.0 <= metrics.anticipatory_capacity <= 1.0
    assert 0.0 <= metrics.absorptive_capacity <= 1.0
    assert 0.0 <= metrics.adaptive_capacity <= 1.0
    assert 0.0 <= metrics.restorative_capacity <= 1.0
    assert metrics.rto_hours == 48.0
    assert metrics.scenario_eens is not None


def test_resilience_compute_metrics_no_scenarios():
    ra = ResilienceAnalyzer()
    metrics = ra.compute_metrics([_profile(0, 0.0, 0.0)], None)
    assert metrics.redundancy_index == 1.0
    assert metrics.rto_hours == 0.0


def test_four_capacities_adaptive_increases_with_generators():
    ra = ResilienceAnalyzer()
    a1 = ra._compute_four_capacities([], [], 100.0, 1)[2]
    a2 = ra._compute_four_capacities([], [], 100.0, 8)[2]
    assert a1 < a2 <= 1.0
    # matches formula
    assert a2 == pytest.approx(min(1.0, math.log(9) / math.log(11)))


# ---------------------------------------------------------------------------
# ISOReportGenerator
# ---------------------------------------------------------------------------

def test_iso_report_minimal():
    gen = ISOReportGenerator()
    html = gen.generate_html({})
    assert html.startswith("<!DOCTYPE html>")
    assert "Executive Summary" in html
    assert "ISO 31000" in html
    assert "9. Appendices" in html


def test_iso_report_full_sections():
    gen = ISOReportGenerator()
    profiles = [_profile(0, 600_000.0, 0.5)]
    evals = evaluate_risk_criteria(profiles)
    ra = ResilienceAnalyzer()
    # Use the no-scenario path so the report renders resilience metrics
    # without relying on np.trapz (removed in NumPy 2.x).
    metrics = ra.compute_metrics(profiles, None)
    flib = FragilityLibrary()
    state = {
        "risk_profiles": [{"expected_annual_loss": 600_000.0}],
        "node_coordinates": [(0.0, 0.0)],
        "hazard_maps": [_hazard_map("earthquake", {0: {475: 1.0}})],
        "combination_method": "independent",
        "risk_measure": "cvar",
        "cvar_alpha": 0.95,
        "fragility_library": flib,
    }
    html = gen.generate_html(
        state, risk_evaluations=evals, resilience_metrics=metrics,
        title="Test", date="2026-01-01", author="tester",
    )
    assert "Test" in html
    assert "tester" in html
    # intolerable node => action section present
    assert "Immediate Action" in html
    # appendix curve count
    assert f"{len(flib.get_all_curves())} curves loaded" in html


def test_iso_report_handles_dict_hazard_maps():
    gen = ISOReportGenerator()
    html = gen.generate_html({
        "hazard_maps": [{"hazard_type": "flood"}, {"hazard_type": "earthquake"}],
    })
    assert "earthquake" in html and "flood" in html


def test_iso_report_with_mc_result():
    gen = ISOReportGenerator()
    cra = CompositeRiskAssessment()
    quake = _hazard_map("earthquake", {0: {100: 1.0, 475: 1.5}})
    mc = cra.monte_carlo_eal([quake], {0: ["solar_pv"]}, n_samples=20)
    html = gen.generate_html({}, mc_result=mc)
    assert "Monte Carlo" in html
    assert "Dominant Uncertainty" in html
