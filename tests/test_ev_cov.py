"""Coverage tests for esfex.models.ev."""
import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

from esfex.models import ev
from esfex.utils.temporal import HOURS_STD_YEAR


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def make_categories():
    return {
        "car": {
            "charging_power": 7.0,
            "v2g_participation": 0.3,
            "v2g_power": 5.0,
            "battery_capacity": 50.0,  # kWh
        },
        "truck": {
            "charging_power": 50.0,
            "v2g_participation": 0.1,
            "v2g_power": 20.0,
            "battery_capacity": 200.0,
            "daily_energy_kwh": 100.0,
            "max_adoption": 10.0,
            "growth_rate": 0.2,
            "mid_point_fraction": 0.3,
        },
    }


def flat_patterns():
    # 24-hour all-ones pattern (sum = 24)
    return {"car": [1.0] * 24, "truck": [1.0] * 24}


def make_quantity(num_nodes):
    return {
        "car": [100.0] * num_nodes,
        "truck": [10.0] * num_nodes,
    }


@pytest.fixture(autouse=True)
def _seed():
    np.random.seed(12345)
    yield


# --------------------------------------------------------------------------
# generate_ev_profiles
# --------------------------------------------------------------------------
def test_generate_ev_profiles_columns_and_shape():
    cats = make_categories()
    num_nodes, num_hours = 2, 48
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, make_quantity(num_nodes), flat_patterns()
    )
    assert isinstance(df, pd.DataFrame)
    assert len(df) == num_hours
    expected_cols = [
        "Node_1_car", "Node_1_truck", "Node_2_car", "Node_2_truck",
    ]
    assert list(df.columns) == expected_cols
    assert (df.values >= 0).all()


def test_generate_ev_profiles_daily_energy_default_uses_battery():
    # car has no daily_energy_kwh -> battery_capacity * 0.12
    # With flat pattern of ones (sum 24), per_vehicle_hour_mwh =
    # (battery*0.12 / 24 / 1000). With noise ~0 the column sums to roughly
    # daily energy * fleet over each day. We check positivity + scale.
    cats = make_categories()
    num_nodes, num_hours = 1, 24
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, make_quantity(num_nodes), flat_patterns()
    )
    # daily_energy_kwh for car = 50 * 0.12 = 6 kWh
    # per_vehicle_hour_mwh = 6 / 24 / 1000 = 0.00025 MWh
    # with value~1 and 100 vehicles (growth_factor at base year), total per
    # hour ~ 100 * 0.00025 * growth. Just assert finite & positive.
    col = df["Node_1_car"]
    assert col.notna().all()
    assert col.sum() > 0


def test_generate_ev_profiles_zero_pattern_sum_yields_zero():
    cats = make_categories()
    patterns = {"car": [0.0] * 24, "truck": [0.0] * 24}
    num_nodes, num_hours = 1, 24
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, make_quantity(num_nodes), patterns
    )
    # pattern_daily_sum == 0 -> per_vehicle_hour_mwh = 0 -> all zeros.
    # base_value 0 plus tiny noise clamped, multiplied by 0 -> 0.
    assert (df.values == 0).all()


def test_generate_ev_profiles_missing_node_quantity_zero():
    # ev_quantity list shorter than num_nodes -> 0 vehicles for missing node
    cats = make_categories()
    quantity = {"car": [100.0], "truck": [10.0]}  # only node 0
    num_nodes, num_hours = 2, 24
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, quantity, flat_patterns()
    )
    # Node_2 columns should be all zero (no vehicles)
    assert (df["Node_2_car"].values == 0).all()
    assert (df["Node_2_truck"].values == 0).all()
    # Node_1 should have positive
    assert df["Node_1_car"].sum() > 0


def test_generate_ev_profiles_growth_increases_over_years():
    # Compare hour 0 (year 0) vs an hour in a later year. Use no noise by
    # setting pattern values such that clamping dominates? Easier: seed fixed
    # and compare averages over a day in year 0 vs year 5.
    cats = {"car": make_categories()["car"]}
    cats["car"]["max_adoption"] = 30.0
    cats["car"]["growth_rate"] = 0.5
    cats["car"]["mid_point_fraction"] = 0.5
    patterns = {"car": [1.0] * 24}
    quantity = {"car": [1000.0]}
    num_nodes = 1
    num_hours = 6 * HOURS_STD_YEAR + 24
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, quantity, patterns,
        base_year=2025, target_year=2055,
    )
    col = df["Node_1_car"].values
    year0_mean = col[0:24].mean()
    year6_mean = col[6 * HOURS_STD_YEAR: 6 * HOURS_STD_YEAR + 24].mean()
    assert year6_mean > year0_mean


# --------------------------------------------------------------------------
# generate_v2g_availability
# --------------------------------------------------------------------------
def test_generate_v2g_availability_shape_and_columns():
    cats = make_categories()
    num_nodes, num_hours = 2, 24
    df = ev.generate_v2g_availability(
        num_nodes, num_hours, cats, make_quantity(num_nodes), flat_patterns()
    )
    assert list(df.columns) == [
        "Node_1_car", "Node_1_truck", "Node_2_car", "Node_2_truck",
    ]
    assert len(df) == num_hours
    assert (df.values >= 0).all()


def test_generate_v2g_availability_zero_participation_zero_output():
    cats = make_categories()
    cats["car"]["v2g_participation"] = 0.0
    cats["truck"]["v2g_participation"] = 0.0
    num_nodes, num_hours = 1, 24
    df = ev.generate_v2g_availability(
        num_nodes, num_hours, cats, make_quantity(num_nodes), flat_patterns()
    )
    assert (df.values == 0).all()


def test_generate_v2g_availability_missing_node_quantity_zero():
    cats = make_categories()
    quantity = {"car": [50.0], "truck": [5.0]}
    num_nodes, num_hours = 2, 24
    df = ev.generate_v2g_availability(
        num_nodes, num_hours, cats, quantity, flat_patterns()
    )
    assert (df["Node_2_car"].values == 0).all()


def test_generate_v2g_availability_scales_with_v2g_power():
    cats = make_categories()
    base = ev.generate_v2g_availability(
        1, 24, cats, {"car": [100.0], "truck": [0.0]}, flat_patterns()
    )
    cats2 = make_categories()
    cats2["car"]["v2g_power"] = cats["car"]["v2g_power"] * 2
    np.random.seed(12345)
    doubled = ev.generate_v2g_availability(
        1, 24, cats2, {"car": [100.0], "truck": [0.0]}, flat_patterns()
    )
    # Doubling v2g_power doubles availability (same seed, same noise sequence)
    np.random.seed(12345)
    base2 = ev.generate_v2g_availability(
        1, 24, cats, {"car": [100.0], "truck": [0.0]}, flat_patterns()
    )
    assert np.allclose(doubled["Node_1_car"].values,
                       2 * base2["Node_1_car"].values)


# --------------------------------------------------------------------------
# generate_electricity_prices
# --------------------------------------------------------------------------
def test_generate_electricity_prices_default():
    prices = ev.generate_electricity_prices()
    assert isinstance(prices, np.ndarray)
    assert prices.shape == (24,)
    # Base range 50-200 plus noise; should be comfortably in this band
    assert prices.min() > 30
    assert prices.max() < 230


def test_generate_electricity_prices_custom_length():
    prices = ev.generate_electricity_prices(num_hours=48)
    assert prices.shape == (48,)


# --------------------------------------------------------------------------
# calculate_v2g_compensation
# --------------------------------------------------------------------------
def test_calculate_v2g_compensation():
    prices = np.array([100.0, 200.0, 0.0])
    comp = ev.calculate_v2g_compensation(prices)
    assert np.allclose(comp, prices * 0.85)


# --------------------------------------------------------------------------
# aggregate_ev_profiles
# --------------------------------------------------------------------------
def test_aggregate_ev_profiles_sums_categories():
    cats = make_categories()
    num_nodes, num_hours = 2, 10
    df = ev.generate_ev_profiles(
        num_nodes, num_hours, cats, make_quantity(num_nodes), flat_patterns()
    )
    agg = ev.aggregate_ev_profiles(df, num_nodes)
    assert agg.shape == (num_hours, num_nodes)
    # Node 0 aggregate equals sum of its two category columns
    expected_node0 = (df["Node_1_car"] + df["Node_1_truck"]).values
    assert np.allclose(agg[:, 0], expected_node0)


def test_aggregate_ev_profiles_node_without_columns_is_zero():
    # Build a df with only node 1 columns but request 2 nodes
    df = pd.DataFrame(
        {"Node_1_car": [1.0, 2.0, 3.0]},
    )
    agg = ev.aggregate_ev_profiles(df, num_nodes=2)
    assert agg.shape == (3, 2)
    assert np.allclose(agg[:, 0], [1.0, 2.0, 3.0])
    assert np.allclose(agg[:, 1], [0.0, 0.0, 0.0])


def test_aggregate_ev_profiles_prefix_exact_match():
    # Ensure Node_1 prefix does not accidentally swallow Node_10
    df = pd.DataFrame(
        {
            "Node_1_car": [1.0],
            "Node_10_car": [99.0],
        }
    )
    agg = ev.aggregate_ev_profiles(df, num_nodes=1)
    # Only Node_1_ should match for node 0
    assert agg[0, 0] == 1.0


# --------------------------------------------------------------------------
# save / load HDF5 round-trip
# --------------------------------------------------------------------------
def test_save_and_load_hdf5_roundtrip(tmp_path):
    cats = make_categories()
    charging = ev.generate_ev_profiles(
        1, 12, cats, make_quantity(1), flat_patterns()
    )
    v2g = ev.generate_v2g_availability(
        1, 12, cats, make_quantity(1), flat_patterns()
    )
    fp = str(tmp_path / "profiles.h5")
    returned = ev.save_ev_profiles_hdf5(charging, v2g, filepath=fp)
    assert returned == fp
    assert os.path.exists(fp)

    loaded_charging, loaded_v2g = ev.load_ev_profiles_hdf5(fp)
    assert isinstance(loaded_charging, pd.DataFrame)
    assert isinstance(loaded_v2g, pd.DataFrame)
    assert list(loaded_charging.columns) == list(charging.columns)
    assert list(loaded_v2g.columns) == list(v2g.columns)
    assert np.allclose(loaded_charging.values, charging.values)
    assert np.allclose(loaded_v2g.values, v2g.values)


def test_save_hdf5_autogenerates_path():
    cats = make_categories()
    charging = ev.generate_ev_profiles(
        1, 4, cats, make_quantity(1), flat_patterns()
    )
    v2g = ev.generate_v2g_availability(
        1, 4, cats, make_quantity(1), flat_patterns()
    )
    fp = ev.save_ev_profiles_hdf5(charging, v2g, filepath=None)
    try:
        assert fp.endswith(".h5")
        assert "ev_profiles_" in os.path.basename(fp)
        assert os.path.exists(fp)
        lc, lv = ev.load_ev_profiles_hdf5(fp)
        assert np.allclose(lc.values, charging.values)
    finally:
        if os.path.exists(fp):
            os.remove(fp)
