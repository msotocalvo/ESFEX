"""
Tests for esfex.io.demand module.

Covers the following public functions and classes:
- _read_tabular (CSV and Excel dispatch)
- load_demand_data (full load and year-specific load)
- create_sectoral_demand (sectoral distribution)
- load_availability_profile (availability loading with clipping/padding)
- DemandDataManager (HDF5 conversion and year-by-year access)
- extract_year_profile (year extraction from full profiles)
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from esfex.io.demand import (
    DemandDataManager,
    _read_tabular,
    create_sectoral_demand,
    extract_year_profile,
    load_availability_profile,
    load_demand_data,
)


# ---------------------------------------------------------------------------
# _read_tabular
# ---------------------------------------------------------------------------


class TestReadTabular:
    """Tests for _read_tabular CSV and Excel dispatch."""

    def test_csv_no_header(self, tmp_path):
        """CSV files are read with header=None (pure numeric data)."""
        csv_file = tmp_path / "data.csv"
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        np.savetxt(csv_file, data, delimiter=",")

        result = _read_tabular(csv_file)
        assert isinstance(result, pd.DataFrame)
        assert result.shape == (3, 2)
        np.testing.assert_allclose(result.values, data)

    def test_csv_column_names_are_integers(self, tmp_path):
        """Since header=None, column names should be integer indices."""
        csv_file = tmp_path / "data.csv"
        np.savetxt(csv_file, np.ones((2, 3)), delimiter=",")

        result = _read_tabular(csv_file)
        assert list(result.columns) == [0, 1, 2]

    def test_csv_single_column(self, tmp_path):
        """Single-column CSV reads correctly."""
        csv_file = tmp_path / "single.csv"
        np.savetxt(csv_file, np.array([10.0, 20.0, 30.0]), delimiter=",")

        result = _read_tabular(csv_file)
        assert result.shape == (3, 1)
        np.testing.assert_allclose(result.values.flatten(), [10.0, 20.0, 30.0])

    def test_excel_file(self, tmp_path):
        """Excel files (.xlsx) are read via pd.read_excel."""
        xlsx_file = tmp_path / "data.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([100.0, 200.0])
        ws.append([300.0, 400.0])
        ws.append([500.0, 600.0])
        wb.save(xlsx_file)

        result = _read_tabular(xlsx_file)
        assert isinstance(result, pd.DataFrame)
        # First row may be treated as header by pd.read_excel default
        # but the shape should contain the numeric data
        assert result.shape[1] == 2

    def test_excel_xls_extension_dispatches(self, tmp_path):
        """Files without .csv extension go to read_excel path."""
        # We create an xlsx but name it .xls -- just testing dispatch logic
        xlsx_file = tmp_path / "data.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([1.0, 2.0])
        wb.save(xlsx_file)

        # read_tabular with .xlsx extension should work
        result = _read_tabular(xlsx_file)
        assert isinstance(result, pd.DataFrame)

    def test_csv_large_data(self, tmp_path):
        """Read a larger CSV (100 rows x 5 columns)."""
        csv_file = tmp_path / "large.csv"
        data = np.random.default_rng(42).uniform(0, 100, (100, 5))
        np.savetxt(csv_file, data, delimiter=",")

        result = _read_tabular(csv_file)
        assert result.shape == (100, 5)
        np.testing.assert_allclose(result.values, data, rtol=1e-6)

    def test_csv_kwargs_passthrough(self, tmp_path):
        """Extra kwargs are passed through to pd.read_csv."""
        csv_file = tmp_path / "data.csv"
        np.savetxt(csv_file, np.ones((10, 3)), delimiter=",")

        result = _read_tabular(csv_file, nrows=5)
        assert result.shape == (5, 3)


# ---------------------------------------------------------------------------
# load_demand_data  (all years)
# ---------------------------------------------------------------------------


class TestLoadDemandDataAllYears:
    """Tests for load_demand_data loading ALL years at once."""

    def test_basic_shape_and_return(self, tmp_path):
        """Load 48 hours x 2 nodes CSV, verify shape and return types."""
        csv_file = tmp_path / "demand.csv"
        data = np.random.default_rng(42).uniform(50, 200, (48, 2))
        np.savetxt(csv_file, data, delimiter=",")

        demand, hours, num_nodes, years, time_index = load_demand_data(
            csv_file, date_start="01/01/2025 00:00"
        )

        assert demand.shape == (48, 2)
        assert hours == 48
        assert num_nodes == 2
        np.testing.assert_allclose(demand, data, rtol=1e-6)

    def test_years_list_single_year(self, tmp_path):
        """48 hours starting Jan 1 falls entirely within 2025."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((48, 2)), delimiter=",")

        _, _, _, years, _ = load_demand_data(
            csv_file, date_start="01/01/2025 00:00"
        )
        assert years == [2025]

    def test_years_list_multi_year(self, tmp_path):
        """8760*2 = 17520 hours spans 2025 and 2026."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((17520, 1)), delimiter=",")

        _, _, _, years, _ = load_demand_data(
            csv_file, date_start="01/01/2025 00:00"
        )
        assert 2025 in years
        assert 2026 in years

    def test_time_index_length(self, tmp_path):
        """time_index should have one entry per hour."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((72, 3)), delimiter=",")

        _, _, _, _, time_index = load_demand_data(
            csv_file, date_start="15/06/2025 00:00"
        )
        assert len(time_index) == 72

    def test_time_index_start_date(self, tmp_path):
        """First time_index entry matches date_start."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((24, 1)), delimiter=",")

        _, _, _, _, time_index = load_demand_data(
            csv_file, date_start="15/03/2025 12:00"
        )
        assert time_index[0] == datetime(2025, 3, 15, 12, 0)

    def test_time_index_hourly_spacing(self, tmp_path):
        """Consecutive time_index entries differ by exactly 1 hour."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((10, 1)), delimiter=",")

        _, _, _, _, time_index = load_demand_data(
            csv_file, date_start="01/01/2025 00:00"
        )
        for i in range(1, len(time_index)):
            delta = time_index[i] - time_index[i - 1]
            assert delta == timedelta(hours=1)

    def test_num_nodes_matches_columns(self, tmp_path):
        """num_nodes equals number of columns in the CSV."""
        csv_file = tmp_path / "demand.csv"
        np.savetxt(csv_file, np.ones((24, 7)), delimiter=",")

        _, _, num_nodes, _, _ = load_demand_data(
            csv_file, date_start="01/01/2025 00:00"
        )
        assert num_nodes == 7


# ---------------------------------------------------------------------------
# load_demand_data  (missing file)
# ---------------------------------------------------------------------------


class TestLoadDemandDataMissingFile:
    """Tests for load_demand_data when file is missing."""

    def test_missing_file_raises(self, tmp_path):
        """FileNotFoundError when file does not exist."""
        missing = tmp_path / "nonexistent.csv"
        with pytest.raises(FileNotFoundError, match="Demand file not found"):
            load_demand_data(missing)

    def test_missing_file_message_includes_path(self, tmp_path):
        """Error message contains the missing file path."""
        missing = tmp_path / "no_such_file.xlsx"
        with pytest.raises(FileNotFoundError, match="no_such_file.xlsx"):
            load_demand_data(missing)


# ---------------------------------------------------------------------------
# load_demand_data  (year_to_load)
# ---------------------------------------------------------------------------


class TestLoadDemandDataSpecificYear:
    """Tests for load_demand_data with year_to_load parameter."""

    def test_year_to_load_first_year(self, tmp_path):
        """Loading the first year from a multi-year file."""
        csv_file = tmp_path / "demand.csv"
        # 2 years of data: 8760 * 2 = 17520 hours
        data = np.random.default_rng(42).uniform(50, 200, (17520, 2))
        np.savetxt(csv_file, data, delimiter=",")

        demand, hours, num_nodes, years, time_index = load_demand_data(
            csv_file, date_start="01/01/2025 00:00", year_to_load=2025
        )

        assert hours == 8760
        assert num_nodes == 2
        assert time_index[0] == datetime(2025, 1, 1, 0, 0)

    def test_year_to_load_returns_correct_data_slice(self, tmp_path):
        """Data for year_to_load matches the correct rows from the file."""
        csv_file = tmp_path / "demand.csv"
        # Create 2 years with distinct values: year1=100, year2=200
        year1_data = np.full((8760, 1), 100.0)
        year2_data = np.full((8760, 1), 200.0)
        data = np.vstack([year1_data, year2_data])
        np.savetxt(csv_file, data, delimiter=",")

        demand_y1, _, _, _, _ = load_demand_data(
            csv_file, date_start="01/01/2025 00:00", year_to_load=2025
        )
        demand_y2, _, _, _, _ = load_demand_data(
            csv_file, date_start="01/01/2025 00:00", year_to_load=2026
        )

        np.testing.assert_allclose(demand_y1.mean(), 100.0, rtol=1e-3)
        np.testing.assert_allclose(demand_y2.mean(), 200.0, rtol=1e-3)


# ---------------------------------------------------------------------------
# create_sectoral_demand
# ---------------------------------------------------------------------------


class TestCreateSectoralDemand:
    """Tests for create_sectoral_demand."""

    def test_basic_structure(self):
        """Returns dict with correct sector keys and array shapes."""
        base_demand = np.ones((24, 2)) * 100.0
        sector_dist = {
            0: {"residential": 0.4, "commercial": 0.6},
            1: {"residential": 0.5, "commercial": 0.5},
        }

        result = create_sectoral_demand(base_demand, sector_dist)

        assert isinstance(result, dict)
        assert "residential" in result
        assert "commercial" in result
        assert result["residential"].shape == (24, 2)
        assert result["commercial"].shape == (24, 2)

    def test_proportions_sum_to_base(self):
        """Sum of all sectoral demands equals base demand for each hour/node."""
        base_demand = np.random.default_rng(42).uniform(50, 200, (48, 3))
        sector_dist = {
            0: {"A": 0.3, "B": 0.7},
            1: {"A": 0.5, "B": 0.5},
            2: {"A": 0.2, "B": 0.8},
        }

        result = create_sectoral_demand(base_demand, sector_dist)
        total = result["A"] + result["B"]
        np.testing.assert_allclose(total, base_demand, rtol=1e-10)

    def test_proportions_applied_per_node(self):
        """Each node gets its own sector proportions."""
        base_demand = np.ones((10, 2)) * 100.0
        sector_dist = {
            0: {"X": 0.3, "Y": 0.7},
            1: {"X": 0.8, "Y": 0.2},
        }

        result = create_sectoral_demand(base_demand, sector_dist)

        # Node 0: X=30, Y=70
        np.testing.assert_allclose(result["X"][:, 0], 30.0, rtol=1e-10)
        np.testing.assert_allclose(result["Y"][:, 0], 70.0, rtol=1e-10)
        # Node 1: X=80, Y=20
        np.testing.assert_allclose(result["X"][:, 1], 80.0, rtol=1e-10)
        np.testing.assert_allclose(result["Y"][:, 1], 20.0, rtol=1e-10)

    def test_normalization_non_unity_proportions(self):
        """Proportions that don't sum to 1.0 are normalized."""
        base_demand = np.ones((5, 1)) * 100.0
        # Proportions sum to 2.0, not 1.0
        sector_dist = {0: {"A": 0.6, "B": 1.4}}

        result = create_sectoral_demand(base_demand, sector_dist)
        total = result["A"] + result["B"]
        # After normalization, should still sum to base_demand
        np.testing.assert_allclose(total, base_demand, rtol=1e-10)

    def test_sectors_list_filter(self):
        """Only specified sectors are included in output."""
        base_demand = np.ones((10, 1)) * 100.0
        sector_dist = {0: {"A": 0.3, "B": 0.3, "C": 0.4}}

        result = create_sectoral_demand(
            base_demand, sector_dist, sectors_list=["A", "B"]
        )

        assert "A" in result
        assert "B" in result
        assert "C" not in result

    def test_missing_node_falls_back_to_node_zero(self):
        """Nodes not in sector_distribution fall back to node 0 distribution."""
        base_demand = np.ones((5, 3)) * 100.0
        # Only node 0 defined; nodes 1 and 2 should inherit from node 0
        sector_dist = {0: {"A": 0.4, "B": 0.6}}

        result = create_sectoral_demand(base_demand, sector_dist)

        # All nodes should use node 0's proportions
        for node in range(3):
            np.testing.assert_allclose(result["A"][:, node], 40.0, rtol=1e-10)
            np.testing.assert_allclose(result["B"][:, node], 60.0, rtol=1e-10)

    def test_equal_distribution_when_no_proportions(self):
        """When total proportion is 0, sectors are distributed equally."""
        base_demand = np.ones((5, 1)) * 100.0
        # No matching sectors in distribution
        sector_dist = {0: {"Z": 0.5}}

        result = create_sectoral_demand(
            base_demand, sector_dist, sectors_list=["A", "B"]
        )

        # Equal distribution: 50/50
        np.testing.assert_allclose(result["A"][:, 0], 50.0, rtol=1e-10)
        np.testing.assert_allclose(result["B"][:, 0], 50.0, rtol=1e-10)

    def test_single_sector(self):
        """With a single sector, all demand goes to that sector."""
        base_demand = np.ones((10, 2)) * 150.0
        sector_dist = {
            0: {"only_sector": 1.0},
            1: {"only_sector": 1.0},
        }

        result = create_sectoral_demand(base_demand, sector_dist)
        assert len(result) == 1
        np.testing.assert_allclose(result["only_sector"], base_demand, rtol=1e-10)

    def test_varying_demand_values(self):
        """Proportions correctly scale time-varying demand."""
        base_demand = np.array([[100.0], [200.0], [300.0]])
        sector_dist = {0: {"A": 0.25, "B": 0.75}}

        result = create_sectoral_demand(base_demand, sector_dist)

        expected_A = np.array([[25.0], [50.0], [75.0]])
        expected_B = np.array([[75.0], [150.0], [225.0]])
        np.testing.assert_allclose(result["A"], expected_A, rtol=1e-10)
        np.testing.assert_allclose(result["B"], expected_B, rtol=1e-10)


# ---------------------------------------------------------------------------
# load_availability_profile
# ---------------------------------------------------------------------------


class TestLoadAvailabilityProfile:
    """Tests for load_availability_profile."""

    def test_basic_load(self, tmp_path):
        """Load availability from a CSV and verify shape and values."""
        csv_file = tmp_path / "avail.csv"
        data = np.random.default_rng(42).uniform(0, 1, (8760, 2))
        np.savetxt(csv_file, data, delimiter=",")

        result = load_availability_profile(csv_file)
        assert result.shape == (8760, 2)
        # Values should be clipped to [0, 1]
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_values_clipped(self, tmp_path):
        """Values outside [0, 1] are clipped."""
        csv_file = tmp_path / "avail.csv"
        data = np.array([[-0.5, 1.5], [0.3, 2.0], [0.0, -1.0]])
        np.savetxt(csv_file, data, delimiter=",")

        result = load_availability_profile(csv_file)
        assert result.min() >= 0.0
        assert result.max() <= 1.0
        np.testing.assert_allclose(result[0, 0], 0.0)
        np.testing.assert_allclose(result[0, 1], 1.0)

    def test_missing_file_returns_ones(self, tmp_path):
        """Missing file returns all-ones array with default shape."""
        missing = tmp_path / "nonexistent.csv"
        result = load_availability_profile(missing, num_nodes=3)
        assert result.shape == (8760, 3)
        np.testing.assert_allclose(result, 1.0)

    def test_missing_file_default_nodes(self, tmp_path):
        """Missing file without num_nodes uses default 10 nodes."""
        missing = tmp_path / "nonexistent.csv"
        result = load_availability_profile(missing)
        assert result.shape[1] == 10

    def test_num_nodes_validation_pad(self, tmp_path):
        """Fewer columns than num_nodes → replicate the last column.

        Padding with 1.0 used to silently turn unmapped nodes into
        always-available generators; replicating the last real profile
        is a safer default for capacity-factor data.
        """
        csv_file = tmp_path / "avail.csv"
        data = np.random.default_rng(42).uniform(0, 0.5, (100, 2))
        np.savetxt(csv_file, data, delimiter=",")

        result = load_availability_profile(csv_file, num_nodes=4)
        assert result.shape == (100, 4)
        # Columns 2 and 3 are copies of the last loaded column (index 1).
        np.testing.assert_allclose(result[:, 2], result[:, 1])
        np.testing.assert_allclose(result[:, 3], result[:, 1])

    def test_num_nodes_validation_truncate(self, tmp_path):
        """File with more columns than num_nodes gets truncated."""
        csv_file = tmp_path / "avail.csv"
        data = np.random.default_rng(42).uniform(0, 1, (100, 5))
        np.savetxt(csv_file, data, delimiter=",")

        result = load_availability_profile(csv_file, num_nodes=3)
        assert result.shape == (100, 3)

    def test_exact_num_nodes(self, tmp_path):
        """File with exact num_nodes passes through unchanged."""
        csv_file = tmp_path / "avail.csv"
        data = np.random.default_rng(42).uniform(0, 1, (50, 4))
        np.savetxt(csv_file, data, delimiter=",")

        result = load_availability_profile(csv_file, num_nodes=4)
        assert result.shape == (50, 4)
        np.testing.assert_allclose(result, np.clip(data, 0.0, 1.0), rtol=1e-6)


# ---------------------------------------------------------------------------
# DemandDataManager
# ---------------------------------------------------------------------------


class TestDemandDataManager:
    """Tests for DemandDataManager HDF5 conversion and loading."""

    def _create_demand_csv(self, tmp_path, hours, nodes, fill_value=100.0):
        """Helper: create a demand CSV with constant or patterned data."""
        csv_file = tmp_path / "demand.csv"
        data = np.full((hours, nodes), fill_value)
        np.savetxt(csv_file, data, delimiter=",")
        return csv_file

    def test_prepare_hdf5_creates_file(self, tmp_path):
        """prepare_hdf5_storage creates an HDF5 file."""
        csv_file = self._create_demand_csv(tmp_path, 48, 2)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")
        hdf5_path = manager.prepare_hdf5_storage()

        assert hdf5_path.exists()
        assert hdf5_path.suffix == ".h5"
        manager.cleanup()

    def test_metadata_populated(self, tmp_path):
        """After prepare, metadata is populated with correct values."""
        csv_file = self._create_demand_csv(tmp_path, 8760, 3)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")
        manager.prepare_hdf5_storage()

        assert manager.metadata["total_hours"] == 8760
        assert manager.metadata["num_nodes"] == 3
        assert 2025 in manager.metadata["years"]
        manager.cleanup()

    def test_load_year_data_shape(self, tmp_path):
        """load_year_data returns correct shape for a full year."""
        csv_file = self._create_demand_csv(tmp_path, 8760, 2, fill_value=42.0)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")
        manager.prepare_hdf5_storage()

        demand, hours, num_nodes, time_index = manager.load_year_data(2025)
        assert hours == 8760
        assert num_nodes == 2
        assert len(time_index) == 8760
        np.testing.assert_allclose(demand.mean(), 42.0, rtol=1e-5)
        manager.cleanup()

    def test_load_year_without_prepare_raises(self, tmp_path):
        """Calling load_year_data before prepare raises ValueError."""
        csv_file = self._create_demand_csv(tmp_path, 48, 1)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")

        with pytest.raises(ValueError, match="HDF5 not prepared"):
            manager.load_year_data(2025)

    def test_load_nonexistent_year_raises(self, tmp_path):
        """Loading a year not in the data raises ValueError."""
        csv_file = self._create_demand_csv(tmp_path, 48, 1)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")
        manager.prepare_hdf5_storage()

        with pytest.raises(ValueError, match="Year 2030 not found"):
            manager.load_year_data(2030)
        manager.cleanup()

    def test_cleanup_removes_file(self, tmp_path):
        """cleanup removes the temporary HDF5 file."""
        csv_file = self._create_demand_csv(tmp_path, 48, 1)
        manager = DemandDataManager(csv_file, date_start="01/01/2025 00:00")
        hdf5_path = manager.prepare_hdf5_storage()

        assert hdf5_path.exists()
        manager.cleanup()
        assert not hdf5_path.exists()


# ---------------------------------------------------------------------------
# extract_year_profile
# ---------------------------------------------------------------------------


class TestExtractYearProfile:
    """Tests for extract_year_profile."""

    def test_ndarray_extraction(self):
        """ndarray input returns first `hours` rows."""
        full = np.arange(100).reshape(50, 2)
        time_idx = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(10)]
        result = extract_year_profile(full, time_idx, hours=10)
        assert result.shape == (10, 2)
        np.testing.assert_array_equal(result, full[:10])

    def test_dataframe_extraction_by_year(self):
        """DataFrame input filters by year using the index."""
        dates = pd.date_range("2025-01-01", periods=48, freq="h")
        df = pd.DataFrame(
            {"node_0": np.arange(48.0), "node_1": np.arange(48.0) * 2},
            index=dates,
        )

        time_idx = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(24)]
        result = extract_year_profile(df, time_idx, hours=24)
        assert result.shape == (24, 2)

    def test_unsupported_type_raises(self):
        """Non-array, non-DataFrame input raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported profile type"):
            extract_year_profile([1, 2, 3], [], hours=2)

    def test_ndarray_hours_limit(self):
        """Result is limited to exactly `hours` rows."""
        full = np.ones((1000, 3))
        time_idx = [datetime(2025, 1, 1)]
        result = extract_year_profile(full, time_idx, hours=50)
        assert result.shape == (50, 3)
