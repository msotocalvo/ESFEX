"""
Tests for esfex.models.ev module.

Covers the following public functions:
- generate_ev_profiles (S-curve fleet growth, charging demand)
- generate_v2g_availability (V2G availability profiles)
- generate_electricity_prices (synthetic price generation)
- calculate_v2g_compensation (V2G compensation rates)
- aggregate_ev_profiles (per-node aggregation)
- save_ev_profiles_hdf5 / load_ev_profiles_hdf5 (HDF5 round-trip)
"""

import os

import h5py
import numpy as np
import pandas as pd
import pytest

from esfex.models.ev import (
    aggregate_ev_profiles,
    calculate_v2g_compensation,
    generate_electricity_prices,
    generate_ev_profiles,
    generate_v2g_availability,
    load_ev_profiles_hdf5,
    save_ev_profiles_hdf5,
)
from esfex.utils.temporal import HOURS_STD_YEAR


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def seed_rng():
    """Set global numpy random seed for reproducibility."""
    np.random.seed(42)


def _make_ev_inputs(
    num_nodes=2,
    num_hours=48,
    num_categories=1,
):
    """Helper to build standard EV inputs for testing."""
    category_names = [f"cat_{i}" for i in range(num_categories)]

    ev_categories = {}
    for name in category_names:
        ev_categories[name] = {
            "charging_power": 7.0,   # kW
            "v2g_participation": 0.3,
            "v2g_power": 5.0,        # kW
            "max_adoption": 10.0,
            "growth_rate": 0.15,
            "mid_point_fraction": 0.5,
            "battery_capacity": 60.0,  # kWh, typical EV pack
        }

    ev_quantity = {}
    for name in category_names:
        ev_quantity[name] = [1000.0] * num_nodes

    # 24-hour base pattern (higher during evening)
    pattern_24 = [
        0.1, 0.1, 0.05, 0.05, 0.05, 0.1,
        0.15, 0.2, 0.15, 0.1, 0.1, 0.1,
        0.1, 0.1, 0.1, 0.15, 0.2, 0.3,
        0.5, 0.6, 0.5, 0.4, 0.3, 0.2,
    ]

    base_patterns = {name: pattern_24 for name in category_names}

    return ev_categories, ev_quantity, base_patterns, category_names


# ---------------------------------------------------------------------------
# generate_ev_profiles
# ---------------------------------------------------------------------------


class TestGenerateEvProfiles:
    """Tests for generate_ev_profiles."""

    def test_output_is_dataframe(self):
        """Return type is pd.DataFrame."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=48)
        result = generate_ev_profiles(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert isinstance(result, pd.DataFrame)

    def test_output_shape(self):
        """DataFrame has correct number of rows and columns."""
        cats, qty, pats, cat_names = _make_ev_inputs(num_nodes=2, num_hours=48)
        result = generate_ev_profiles(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        # columns: 2 nodes x 1 category = 2
        assert result.shape == (48, 2)

    def test_column_names_format(self):
        """Column names follow 'Node_{n}_{category}' pattern."""
        cats, qty, pats, cat_names = _make_ev_inputs(num_nodes=2, num_hours=24)
        result = generate_ev_profiles(
            num_nodes=2, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        for col in result.columns:
            assert col.startswith("Node_")
            parts = col.split("_")
            assert len(parts) >= 3

    def test_non_negative_values(self):
        """All charging values are non-negative."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=48)
        result = generate_ev_profiles(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert (result.values >= 0).all()

    def test_multi_category_columns(self):
        """With 2 categories and 3 nodes, expect 6 columns."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=3, num_hours=24, num_categories=2,
        )
        result = generate_ev_profiles(
            num_nodes=3, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert result.shape[1] == 6  # 3 nodes x 2 categories

    def test_s_curve_growth_increases_over_years(self):
        """Fleet growth via S-curve means later years have higher demand."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=1, num_hours=HOURS_STD_YEAR * 2,
        )
        result = generate_ev_profiles(
            num_nodes=1, num_hours=HOURS_STD_YEAR * 2,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
            base_year=2025, target_year=2050,
        )

        col = result.columns[0]
        year1_mean = result[col].iloc[:HOURS_STD_YEAR].mean()
        year2_mean = result[col].iloc[HOURS_STD_YEAR:].mean()
        assert year2_mean > year1_mean

    def test_zero_initial_quantity(self):
        """Zero initial vehicles produce zero charging demand."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=24)
        qty[list(qty.keys())[0]] = [0.0]

        result = generate_ev_profiles(
            num_nodes=1, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        np.testing.assert_allclose(result.values, 0.0, atol=1e-15)

    def test_single_node_single_hour(self):
        """Edge case: 1 node, 1 hour produces valid output."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=1)
        result = generate_ev_profiles(
            num_nodes=1, num_hours=1,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert result.shape == (1, 1)
        assert result.values[0, 0] >= 0

    def test_custom_growth_parameters(self):
        """Custom max_adoption and growth_rate are used."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=48)
        cats["cat_0"]["max_adoption"] = 1.0  # very low growth
        cats["cat_0"]["growth_rate"] = 0.01  # very slow

        result = generate_ev_profiles(
            num_nodes=1, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        # Should produce valid output even with extreme parameters
        assert (result.values >= 0).all()


# ---------------------------------------------------------------------------
# generate_v2g_availability
# ---------------------------------------------------------------------------


class TestGenerateV2gAvailability:
    """Tests for generate_v2g_availability."""

    def test_output_is_dataframe(self):
        """Return type is pd.DataFrame."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=48)
        result = generate_v2g_availability(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert isinstance(result, pd.DataFrame)

    def test_output_shape(self):
        """Shape matches nodes x categories by hours."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=3, num_hours=72, num_categories=2,
        )
        result = generate_v2g_availability(
            num_nodes=3, num_hours=72,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert result.shape == (72, 6)  # 3 nodes x 2 categories

    def test_non_negative_values(self):
        """All V2G availability values are non-negative."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=48)
        result = generate_v2g_availability(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert (result.values >= 0).all()

    def test_v2g_less_than_or_equal_to_charging(self):
        """V2G availability should generally be less than charging demand.

        V2G is scaled by v2g_participation (0.3) and v2g_power (5 kW)
        while charging uses charging_power (7 kW). So V2G < charging
        at the same base pattern value when participation < 1.
        """
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=48)
        # Use same seed for both
        np.random.seed(42)
        charging = generate_ev_profiles(
            num_nodes=1, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        np.random.seed(42)
        v2g = generate_v2g_availability(
            num_nodes=1, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        # V2G power factor: 0.3 * 5 / 7 = ~0.214 of charging
        # Due to noise differences, just check v2g mean is lower
        assert v2g.values.mean() < charging.values.mean()

    def test_zero_v2g_participation(self):
        """Zero v2g_participation produces zero V2G availability."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=24)
        cats["cat_0"]["v2g_participation"] = 0.0

        result = generate_v2g_availability(
            num_nodes=1, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        np.testing.assert_allclose(result.values, 0.0, atol=1e-15)

    def test_s_curve_growth_in_v2g(self):
        """V2G availability grows with S-curve fleet growth."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=1, num_hours=HOURS_STD_YEAR * 2,
        )
        result = generate_v2g_availability(
            num_nodes=1, num_hours=HOURS_STD_YEAR * 2,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
            base_year=2025, target_year=2050,
        )

        col = result.columns[0]
        year1_mean = result[col].iloc[:HOURS_STD_YEAR].mean()
        year2_mean = result[col].iloc[HOURS_STD_YEAR:].mean()
        assert year2_mean > year1_mean


# ---------------------------------------------------------------------------
# generate_electricity_prices
# ---------------------------------------------------------------------------


class TestGenerateElectricityPrices:
    """Tests for generate_electricity_prices."""

    def test_output_shape(self):
        """Output length matches num_hours."""
        result = generate_electricity_prices(num_hours=24)
        assert result.shape == (24,)

    def test_prices_positive(self):
        """Prices should be mostly positive (base is 50+ with small noise)."""
        result = generate_electricity_prices(num_hours=24)
        assert (result > 0).all()

    def test_custom_num_hours(self):
        """Non-default number of hours works."""
        result = generate_electricity_prices(num_hours=48)
        assert result.shape == (48,)

    def test_output_type(self):
        """Output is a numpy array."""
        result = generate_electricity_prices()
        assert isinstance(result, np.ndarray)


# ---------------------------------------------------------------------------
# calculate_v2g_compensation
# ---------------------------------------------------------------------------


class TestCalculateV2gCompensation:
    """Tests for calculate_v2g_compensation."""

    def test_compensation_is_85_percent(self):
        """Compensation is 85% of electricity price."""
        prices = np.array([100.0, 200.0, 50.0])
        result = calculate_v2g_compensation(prices)
        expected = np.array([85.0, 170.0, 42.5])
        np.testing.assert_allclose(result, expected)

    def test_output_shape_matches_input(self):
        """Output has same shape as input."""
        prices = np.random.default_rng(42).uniform(50, 200, 48)
        result = calculate_v2g_compensation(prices)
        assert result.shape == prices.shape

    def test_compensation_less_than_price(self):
        """Compensation is always less than the electricity price."""
        prices = np.array([10.0, 100.0, 1000.0])
        result = calculate_v2g_compensation(prices)
        assert (result < prices).all()


# ---------------------------------------------------------------------------
# aggregate_ev_profiles
# ---------------------------------------------------------------------------


class TestAggregateEvProfiles:
    """Tests for aggregate_ev_profiles."""

    def test_basic_aggregation_shape(self):
        """Aggregated output has shape (hours, num_nodes)."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=2, num_hours=48, num_categories=2,
        )
        profiles = generate_ev_profiles(
            num_nodes=2, num_hours=48,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )

        result = aggregate_ev_profiles(profiles, num_nodes=2)
        assert result.shape == (48, 2)

    def test_aggregation_sums_categories(self):
        """Aggregation sums across categories for each node."""
        # Build a simple DataFrame manually
        df = pd.DataFrame({
            "Node_1_A": [10.0, 20.0],
            "Node_1_B": [5.0, 10.0],
            "Node_2_A": [3.0, 6.0],
            "Node_2_B": [7.0, 14.0],
        })

        result = aggregate_ev_profiles(df, num_nodes=2)
        expected = np.array([
            [15.0, 10.0],  # Node_1: 10+5, Node_2: 3+7
            [30.0, 20.0],  # Node_1: 20+10, Node_2: 6+14
        ])
        np.testing.assert_allclose(result, expected)

    def test_single_category_identity(self):
        """With 1 category, aggregation equals original values."""
        df = pd.DataFrame({
            "Node_1_cat": [100.0, 200.0],
            "Node_2_cat": [300.0, 400.0],
        })

        result = aggregate_ev_profiles(df, num_nodes=2)
        expected = np.array([[100.0, 300.0], [200.0, 400.0]])
        np.testing.assert_allclose(result, expected)

    def test_non_negative_output(self):
        """Aggregated output is non-negative when inputs are non-negative."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=3, num_hours=24, num_categories=2,
        )
        profiles = generate_ev_profiles(
            num_nodes=3, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )

        result = aggregate_ev_profiles(profiles, num_nodes=3)
        assert (result >= 0).all()

    def test_no_matching_columns_returns_zeros(self):
        """When no columns match a node, that node column is all zeros."""
        df = pd.DataFrame({
            "Node_1_A": [10.0, 20.0],
        })

        result = aggregate_ev_profiles(df, num_nodes=3)
        assert result.shape == (2, 3)
        # Node 2 and Node 3 have no columns, should be zero
        np.testing.assert_allclose(result[:, 1], 0.0)
        np.testing.assert_allclose(result[:, 2], 0.0)


# ---------------------------------------------------------------------------
# save_ev_profiles_hdf5 / load_ev_profiles_hdf5  (round-trip)
# ---------------------------------------------------------------------------


class TestEvProfilesHdf5RoundTrip:
    """Tests for HDF5 save and load round-trip."""

    def _make_sample_profiles(self, num_hours=48, num_nodes=2):
        """Create sample charging and V2G DataFrames."""
        cats, qty, pats, _ = _make_ev_inputs(
            num_nodes=num_nodes, num_hours=num_hours,
        )
        charging = generate_ev_profiles(
            num_nodes=num_nodes, num_hours=num_hours,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        v2g = generate_v2g_availability(
            num_nodes=num_nodes, num_hours=num_hours,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        return charging, v2g

    def test_save_creates_file(self, tmp_path):
        """save_ev_profiles_hdf5 creates an HDF5 file."""
        charging, v2g = self._make_sample_profiles()
        filepath = str(tmp_path / "ev_profiles.h5")

        result_path = save_ev_profiles_hdf5(charging, v2g, filepath=filepath)
        assert os.path.exists(result_path)

    def test_save_auto_generates_path(self):
        """Without filepath argument, a path is auto-generated."""
        charging, v2g = self._make_sample_profiles()
        result_path = save_ev_profiles_hdf5(charging, v2g)
        assert os.path.exists(result_path)
        # Clean up
        os.remove(result_path)

    def test_round_trip_charging_data(self, tmp_path):
        """Charging data survives save/load round-trip."""
        charging, v2g = self._make_sample_profiles()
        filepath = str(tmp_path / "ev_profiles.h5")

        save_ev_profiles_hdf5(charging, v2g, filepath=filepath)
        loaded_charging, _ = load_ev_profiles_hdf5(filepath)

        np.testing.assert_allclose(
            loaded_charging.values, charging.values, rtol=1e-6
        )

    def test_round_trip_v2g_data(self, tmp_path):
        """V2G data survives save/load round-trip."""
        charging, v2g = self._make_sample_profiles()
        filepath = str(tmp_path / "ev_profiles.h5")

        save_ev_profiles_hdf5(charging, v2g, filepath=filepath)
        _, loaded_v2g = load_ev_profiles_hdf5(filepath)

        np.testing.assert_allclose(
            loaded_v2g.values, v2g.values, rtol=1e-6
        )

    def test_round_trip_column_names(self, tmp_path):
        """Column names are preserved through save/load."""
        charging, v2g = self._make_sample_profiles()
        filepath = str(tmp_path / "ev_profiles.h5")

        save_ev_profiles_hdf5(charging, v2g, filepath=filepath)
        loaded_charging, loaded_v2g = load_ev_profiles_hdf5(filepath)

        assert list(loaded_charging.columns) == list(charging.columns)
        assert list(loaded_v2g.columns) == list(v2g.columns)

    def test_round_trip_shape(self, tmp_path):
        """Shape is preserved through save/load."""
        charging, v2g = self._make_sample_profiles(num_hours=72, num_nodes=3)
        filepath = str(tmp_path / "ev_profiles.h5")

        save_ev_profiles_hdf5(charging, v2g, filepath=filepath)
        loaded_charging, loaded_v2g = load_ev_profiles_hdf5(filepath)

        assert loaded_charging.shape == charging.shape
        assert loaded_v2g.shape == v2g.shape

    def test_hdf5_file_structure(self, tmp_path):
        """HDF5 file has expected groups and datasets."""
        charging, v2g = self._make_sample_profiles()
        filepath = str(tmp_path / "ev_profiles.h5")

        save_ev_profiles_hdf5(charging, v2g, filepath=filepath)

        with h5py.File(filepath, "r") as f:
            assert "charging" in f
            assert "v2g" in f
            assert "data" in f["charging"]
            assert "columns" in f["charging"]
            assert "index" in f["charging"]
            assert "data" in f["v2g"]
            assert "columns" in f["v2g"]
            assert "index" in f["v2g"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests across multiple EV functions."""

    def test_single_node_profiles(self):
        """Single node produces valid output for all profile functions."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=1, num_hours=24)

        charging = generate_ev_profiles(
            num_nodes=1, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        v2g = generate_v2g_availability(
            num_nodes=1, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )

        assert charging.shape == (24, 1)
        assert v2g.shape == (24, 1)

    def test_single_hour_profiles(self):
        """Single hour produces valid 1-row output."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=1)

        charging = generate_ev_profiles(
            num_nodes=2, num_hours=1,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        assert charging.shape == (1, 2)

    def test_zero_initial_quantity_all_nodes(self):
        """Zero vehicles at all nodes produces zero demand everywhere."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=3, num_hours=24)
        for key in qty:
            qty[key] = [0.0, 0.0, 0.0]

        charging = generate_ev_profiles(
            num_nodes=3, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        np.testing.assert_allclose(charging.values, 0.0, atol=1e-15)

    def test_zero_quantity_aggregation_is_zero(self):
        """Aggregated profiles are zero when input profiles are zero."""
        cats, qty, pats, _ = _make_ev_inputs(num_nodes=2, num_hours=24)
        for key in qty:
            qty[key] = [0.0, 0.0]

        profiles = generate_ev_profiles(
            num_nodes=2, num_hours=24,
            ev_categories=cats, ev_quantity=qty, base_patterns=pats,
        )
        aggregated = aggregate_ev_profiles(profiles, num_nodes=2)
        np.testing.assert_allclose(aggregated, 0.0, atol=1e-15)
