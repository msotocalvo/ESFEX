"""
Tests for esfex.utils.temporal module.

Covers all public functions and the HOURS_STD_YEAR constant:
- aggregate_to_resolution (mean aggregation)
- aggregate_demand_to_resolution (max aggregation)
- validate_hourly_data
- get_aggregated_timesteps
- get_hours_per_year
- hours_for_year
- calculate_rolling_horizon_windows
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from esfex.utils.temporal import (
    HOURS_STD_YEAR,
    aggregate_demand_to_resolution,
    aggregate_to_resolution,
    calculate_rolling_horizon_windows,
    get_aggregated_timesteps,
    get_hours_per_year,
    hours_for_year,
    validate_hourly_data,
)


# ---------------------------------------------------------------------------
# HOURS_STD_YEAR constant
# ---------------------------------------------------------------------------


class TestHoursStdYear:
    """Tests for the HOURS_STD_YEAR constant."""

    def test_value(self):
        assert HOURS_STD_YEAR == 8760

    def test_type(self):
        assert isinstance(HOURS_STD_YEAR, int)

    def test_equals_365_times_24(self):
        assert HOURS_STD_YEAR == 365 * 24


# ---------------------------------------------------------------------------
# aggregate_to_resolution  (mean aggregation)
# ---------------------------------------------------------------------------


class TestAggregateToResolution:
    """Tests for aggregate_to_resolution (mean aggregation)."""

    # --- 1D numpy array ---

    def test_1d_target_hours_1_noop(self):
        """target_hours=1 returns data unchanged (identity)."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        result = aggregate_to_resolution(data, target_hours=1)
        np.testing.assert_array_equal(result, data)

    def test_1d_mean_aggregation_3h(self):
        """Mean of each 3-hour block."""
        data = np.array([100.0, 110.0, 105.0, 120.0, 115.0, 125.0])
        result = aggregate_to_resolution(data, target_hours=3)
        expected = np.array([105.0, 120.0])
        np.testing.assert_allclose(result, expected)

    def test_1d_mean_aggregation_2h(self):
        data = np.array([10.0, 20.0, 30.0, 40.0])
        result = aggregate_to_resolution(data, target_hours=2)
        expected = np.array([15.0, 35.0])
        np.testing.assert_allclose(result, expected)

    def test_1d_mean_aggregation_6h(self):
        data = np.arange(1.0, 13.0)  # [1..12]
        result = aggregate_to_resolution(data, target_hours=6)
        # mean([1..6])=3.5, mean([7..12])=9.5
        expected = np.array([3.5, 9.5])
        np.testing.assert_allclose(result, expected)

    def test_1d_truncation_non_divisible(self):
        """Excess timesteps are silently truncated (no warning for mean variant)."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])  # length 5, target 3
        result = aggregate_to_resolution(data, target_hours=3)
        # Only first 3 used: mean([1,2,3]) = 2.0
        expected = np.array([2.0])
        np.testing.assert_allclose(result, expected)

    def test_1d_full_year(self):
        """Aggregation of a full standard year to 3-hourly."""
        data = np.ones(HOURS_STD_YEAR)
        result = aggregate_to_resolution(data, target_hours=3)
        assert result.shape == (2920,)
        np.testing.assert_allclose(result, 1.0)

    # --- 2D numpy array ---

    def test_2d_mean_aggregation(self):
        """2D array: timesteps x nodes."""
        data = np.array([
            [10.0, 20.0],
            [30.0, 40.0],
            [50.0, 60.0],
            [70.0, 80.0],
        ])
        result = aggregate_to_resolution(data, target_hours=2)
        expected = np.array([
            [20.0, 30.0],  # mean([10,30]), mean([20,40])
            [60.0, 70.0],  # mean([50,70]), mean([60,80])
        ])
        np.testing.assert_allclose(result, expected)

    def test_2d_target_hours_1_noop(self):
        data = np.arange(12).reshape(4, 3).astype(float)
        result = aggregate_to_resolution(data, target_hours=1)
        np.testing.assert_array_equal(result, data)

    def test_2d_truncation(self):
        """2D with non-divisible length truncates excess rows."""
        data = np.ones((7, 2))
        result = aggregate_to_resolution(data, target_hours=3)
        # 7 // 3 = 2, uses first 6 rows
        assert result.shape == (2, 2)

    # --- DataFrame ---

    def test_dataframe_aggregation(self):
        df = pd.DataFrame(
            {"node_0": [10.0, 20.0, 30.0, 40.0], "node_1": [1.0, 2.0, 3.0, 4.0]},
        )
        result = aggregate_to_resolution(df, target_hours=2)
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["node_0", "node_1"]
        np.testing.assert_allclose(result["node_0"].values, [15.0, 35.0])
        np.testing.assert_allclose(result["node_1"].values, [1.5, 3.5])

    def test_dataframe_preserves_index_name(self):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0]})
        df.index.name = "hour"
        result = aggregate_to_resolution(df, target_hours=2)
        assert result.index.name == "hour"

    def test_dataframe_target_hours_1_returns_dataframe(self):
        df = pd.DataFrame({"x": [1.0, 2.0]})
        result = aggregate_to_resolution(df, target_hours=1)
        assert isinstance(result, pd.DataFrame)

    # --- Error handling ---

    def test_raises_type_error_for_float_target(self):
        data = np.array([1.0, 2.0, 3.0])
        with pytest.raises(TypeError, match="target_hours must be integer"):
            aggregate_to_resolution(data, target_hours=2.5)

    def test_raises_type_error_for_string_target(self):
        data = np.array([1.0, 2.0, 3.0])
        with pytest.raises(TypeError, match="target_hours must be integer"):
            aggregate_to_resolution(data, target_hours="3")

    def test_raises_value_error_for_zero_target(self):
        data = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="target_hours must be positive"):
            aggregate_to_resolution(data, target_hours=0)

    def test_raises_value_error_for_negative_target(self):
        data = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="target_hours must be positive"):
            aggregate_to_resolution(data, target_hours=-3)

    def test_accepts_numpy_integer(self):
        """np.int64 etc. should be accepted as target_hours."""
        data = np.array([1.0, 2.0, 3.0, 4.0])
        result = aggregate_to_resolution(data, target_hours=np.int64(2))
        np.testing.assert_allclose(result, [1.5, 3.5])


# ---------------------------------------------------------------------------
# aggregate_demand_to_resolution  (MAX aggregation)
# ---------------------------------------------------------------------------


class TestAggregateDemandToResolution:
    """Tests for aggregate_demand_to_resolution (mean — preserves annual energy
    once the LP multiplies MW × target_hours back to MWh)."""

    def test_1d_mean_aggregation(self):
        """Each block returns its mean (energy-preserving)."""
        data = np.array([100.0, 150.0, 120.0, 180.0, 140.0, 110.0])
        result = aggregate_demand_to_resolution(data, target_hours=3)
        expected = np.array([(100+150+120)/3, (180+140+110)/3])
        np.testing.assert_allclose(result, expected)

    def test_demand_and_availability_aggregators_now_agree(self):
        """Both use mean — demand aggregator switched away from MAX
        (which inflated capacity-adequacy needs and dropped ~40% of energy)."""
        data = np.array([10.0, 50.0, 20.0, 30.0])
        result_demand = aggregate_demand_to_resolution(data, target_hours=2)
        result_avail = aggregate_to_resolution(data, target_hours=2)
        np.testing.assert_allclose(result_demand, [30.0, 25.0])
        np.testing.assert_allclose(result_avail, [30.0, 25.0])

    def test_1d_target_hours_1_noop(self):
        data = np.array([5.0, 10.0, 15.0])
        result = aggregate_demand_to_resolution(data, target_hours=1)
        np.testing.assert_array_equal(result, data)

    def test_1d_mean_aggregation_6h(self):
        data = np.array([100.0, 150.0, 120.0, 180.0, 140.0, 110.0])
        result = aggregate_demand_to_resolution(data, target_hours=6)
        expected = np.array([data.mean()])
        np.testing.assert_allclose(result, expected)

    def test_2d_mean_aggregation(self):
        data = np.array([
            [10.0, 90.0],
            [30.0, 40.0],
            [50.0, 60.0],
            [20.0, 80.0],
        ])
        result = aggregate_demand_to_resolution(data, target_hours=2)
        expected = np.array([
            [20.0, 65.0],  # mean([10,30]), mean([90,40])
            [35.0, 70.0],  # mean([50,20]), mean([60,80])
        ])
        np.testing.assert_allclose(result, expected)

    def test_truncation_emits_warning(self):
        """Non-divisible length triggers UserWarning."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])  # 5 not divisible by 3
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = aggregate_demand_to_resolution(data, target_hours=3)
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "not divisible" in str(w[0].message)
            assert "2 timesteps will be truncated" in str(w[0].message)
        # Only first 3 used: mean([1,2,3]) = 2
        np.testing.assert_allclose(result, [2.0])

    def test_no_warning_when_divisible(self):
        """Divisible length does not trigger a warning."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            aggregate_demand_to_resolution(data, target_hours=3)
            truncation_warnings = [x for x in w if "not divisible" in str(x.message)]
            assert len(truncation_warnings) == 0

    def test_dataframe_mean_aggregation(self):
        df = pd.DataFrame({"A": [5.0, 15.0, 10.0, 20.0]})
        result = aggregate_demand_to_resolution(df, target_hours=2)
        assert isinstance(result, pd.DataFrame)
        np.testing.assert_allclose(result["A"].values, [10.0, 15.0])

    def test_dataframe_preserves_columns(self):
        df = pd.DataFrame({"load_a": [1.0, 2.0], "load_b": [3.0, 4.0]})
        result = aggregate_demand_to_resolution(df, target_hours=2)
        assert list(result.columns) == ["load_a", "load_b"]

    def test_raises_type_error_for_float_target(self):
        data = np.array([1.0, 2.0])
        with pytest.raises(TypeError, match="target_hours must be integer"):
            aggregate_demand_to_resolution(data, target_hours=1.5)

    def test_raises_value_error_for_negative_target(self):
        data = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="target_hours must be positive"):
            aggregate_demand_to_resolution(data, target_hours=-1)


# ---------------------------------------------------------------------------
# validate_hourly_data
# ---------------------------------------------------------------------------


class TestValidateHourlyData:
    """Tests for validate_hourly_data."""

    def test_correct_length_passes(self):
        data = np.zeros(HOURS_STD_YEAR)
        assert validate_hourly_data(data) is True

    def test_correct_length_custom_hours(self):
        data = np.zeros(8784)
        assert validate_hourly_data(data, expected_hours=8784) is True

    def test_wrong_length_raises(self):
        data = np.zeros(100)
        with pytest.raises(ValueError, match="does not match expected 8760 hours"):
            validate_hourly_data(data)

    def test_wrong_length_custom_expected(self):
        data = np.zeros(8760)
        with pytest.raises(ValueError, match="does not match expected 8784 hours"):
            validate_hourly_data(data, expected_hours=8784)

    def test_error_message_contains_data_name(self):
        data = np.zeros(10)
        with pytest.raises(ValueError, match="solar_profile"):
            validate_hourly_data(data, data_name="solar_profile")

    def test_2d_array_uses_first_axis(self):
        """Validation checks shape[0], so 2D arrays work too."""
        data = np.zeros((HOURS_STD_YEAR, 3))
        assert validate_hourly_data(data) is True

    def test_2d_array_wrong_length_raises(self):
        data = np.zeros((100, 3))
        with pytest.raises(ValueError):
            validate_hourly_data(data)


# ---------------------------------------------------------------------------
# get_aggregated_timesteps
# ---------------------------------------------------------------------------


class TestGetAggregatedTimesteps:
    """Tests for get_aggregated_timesteps."""

    def test_hourly_noop(self):
        assert get_aggregated_timesteps(8760, 1) == 8760

    def test_3_hourly(self):
        assert get_aggregated_timesteps(8760, 3) == 2920

    def test_6_hourly(self):
        assert get_aggregated_timesteps(8760, 6) == 1460

    def test_12_hourly(self):
        assert get_aggregated_timesteps(8760, 12) == 730

    def test_24_hourly(self):
        assert get_aggregated_timesteps(8760, 24) == 365

    def test_leap_year_24h(self):
        assert get_aggregated_timesteps(8784, 24) == 366

    def test_non_divisible_truncates(self):
        # 8760 // 7 = 1251 (with remainder 3)
        assert get_aggregated_timesteps(8760, 7) == 1251

    def test_small_values(self):
        assert get_aggregated_timesteps(10, 3) == 3


# ---------------------------------------------------------------------------
# get_hours_per_year
# ---------------------------------------------------------------------------


class TestGetHoursPerYear:
    """Tests for get_hours_per_year."""

    def test_standard_year(self):
        assert get_hours_per_year(leap_year=False) == 8760

    def test_leap_year(self):
        assert get_hours_per_year(leap_year=True) == 8784

    def test_default_is_standard(self):
        assert get_hours_per_year() == 8760

    def test_difference_is_24(self):
        diff = get_hours_per_year(leap_year=True) - get_hours_per_year(leap_year=False)
        assert diff == 24


# ---------------------------------------------------------------------------
# hours_for_year
# ---------------------------------------------------------------------------


class TestHoursForYear:
    """Tests for hours_for_year (calendar-year-specific)."""

    def test_leap_year_2024(self):
        assert hours_for_year(2024) == 8784

    def test_leap_year_2000(self):
        """2000 is a leap year (divisible by 400)."""
        assert hours_for_year(2000) == 8784

    def test_non_leap_year_1900(self):
        """1900 is NOT a leap year (divisible by 100 but not 400)."""
        assert hours_for_year(1900) == 8760

    def test_non_leap_year_2025(self):
        assert hours_for_year(2025) == 8760

    def test_non_leap_year_2023(self):
        assert hours_for_year(2023) == 8760

    def test_leap_year_2028(self):
        assert hours_for_year(2028) == 8784

    def test_consistency_with_get_hours_per_year(self):
        """hours_for_year and get_hours_per_year should agree."""
        import calendar
        for yr in [2020, 2021, 2022, 2023, 2024]:
            is_leap = calendar.isleap(yr)
            assert hours_for_year(yr) == get_hours_per_year(leap_year=is_leap)


# ---------------------------------------------------------------------------
# calculate_rolling_horizon_windows
# ---------------------------------------------------------------------------


class TestCalculateRollingHorizonWindows:
    """Tests for calculate_rolling_horizon_windows."""

    def test_basic_no_overlap(self):
        """Non-overlapping windows divide evenly."""
        windows = calculate_rolling_horizon_windows(
            total_hours=100, window_hours=25, overlap_hours=0,
        )
        assert windows == [(0, 25), (25, 50), (50, 75), (75, 100)]

    def test_basic_with_overlap(self):
        """Overlapping windows with step = window - overlap."""
        windows = calculate_rolling_horizon_windows(
            total_hours=100, window_hours=40, overlap_hours=10,
        )
        # effective step = 30
        # (0,40), (30,70), (60,100)
        assert windows == [(0, 40), (30, 70), (60, 100)]

    def test_last_window_shorter(self):
        """Last window may be shorter if total_hours is not evenly covered."""
        windows = calculate_rolling_horizon_windows(
            total_hours=50, window_hours=24, overlap_hours=0,
        )
        # (0,24), (24,48), (48,50) -- last window only 2 hours
        assert windows == [(0, 24), (24, 48), (48, 50)]
        # Last window is shorter
        last_start, last_end = windows[-1]
        assert (last_end - last_start) < 24

    def test_last_window_shorter_with_overlap(self):
        windows = calculate_rolling_horizon_windows(
            total_hours=50, window_hours=24, overlap_hours=4,
        )
        # effective step = 20
        # (0,24), (20,44), (40,50)
        assert windows == [(0, 24), (20, 44), (40, 50)]
        last_start, last_end = windows[-1]
        assert (last_end - last_start) == 10  # shorter than 24

    def test_overlap_equals_window_raises(self):
        with pytest.raises(ValueError, match="overlap_hours must be less than window_hours"):
            calculate_rolling_horizon_windows(
                total_hours=100, window_hours=24, overlap_hours=24,
            )

    def test_overlap_greater_than_window_raises(self):
        with pytest.raises(ValueError, match="overlap_hours must be less than window_hours"):
            calculate_rolling_horizon_windows(
                total_hours=100, window_hours=24, overlap_hours=30,
            )

    def test_full_coverage(self):
        """All hours from 0 to total_hours are covered by at least one window."""
        total = 8760
        windows = calculate_rolling_horizon_windows(
            total_hours=total, window_hours=168, overlap_hours=24,
        )
        covered = set()
        for start, end in windows:
            covered.update(range(start, end))
        assert covered == set(range(total))

    def test_windows_start_monotonically_increasing(self):
        windows = calculate_rolling_horizon_windows(
            total_hours=500, window_hours=100, overlap_hours=20,
        )
        starts = [w[0] for w in windows]
        assert starts == sorted(starts)
        # Strictly increasing
        assert len(set(starts)) == len(starts)

    def test_single_window_covers_all(self):
        """When window >= total, a single window is returned."""
        windows = calculate_rolling_horizon_windows(
            total_hours=50, window_hours=100, overlap_hours=0,
        )
        assert len(windows) == 1
        assert windows[0] == (0, 50)

    def test_zero_overlap(self):
        windows = calculate_rolling_horizon_windows(
            total_hours=48, window_hours=24, overlap_hours=0,
        )
        assert windows == [(0, 24), (24, 48)]

    def test_yearly_weekly_windows(self):
        """Realistic case: yearly horizon with weekly windows and 1-day overlap."""
        windows = calculate_rolling_horizon_windows(
            total_hours=8760, window_hours=168, overlap_hours=24,
        )
        # effective step = 144
        # Number of windows: ceil(8760 / 144) approximately
        assert len(windows) > 50
        # First window
        assert windows[0] == (0, 168)
        # Last window ends at 8760
        assert windows[-1][1] == 8760

    def test_return_type_is_list_of_tuples(self):
        windows = calculate_rolling_horizon_windows(
            total_hours=100, window_hours=50, overlap_hours=0,
        )
        assert isinstance(windows, list)
        for w in windows:
            assert isinstance(w, tuple)
            assert len(w) == 2
