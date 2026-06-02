"""
Tests for esfex.io.exporter module.

Covers ResultsExporter (CSV, Excel, JSON exports) and the standalone
export_system_results / read_results functions using temporary HDF5 files.
"""

import json
from pathlib import Path
from typing import Any, Dict

import h5py
import numpy as np
import pandas as pd
import pytest

from esfex.io.exporter import (
    ResultsExporter,
    export_system_results,
    read_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_minimal_hdf5(path: Path, **kwargs) -> Path:
    """Create a minimal HDF5 results file suitable for testing.

    Keyword arguments let callers add extra content:
        with_summary  -- bool, add summary_results group (default True)
        with_detailed -- bool, add detailed_results with hourly data (default False)
        with_demand   -- bool, add demand group (default False)
        metadata      -- dict, extra file-level attrs
    """
    with_summary = kwargs.get("with_summary", True)
    with_detailed = kwargs.get("with_detailed", False)
    with_demand = kwargs.get("with_demand", False)
    metadata = kwargs.get("metadata", {})

    with h5py.File(path, "w") as f:
        # Metadata
        f.attrs["creation_date"] = "2026-02-21T00:00:00"
        f.attrs["hours"] = 24
        f.attrs["num_nodes"] = 2
        for k, v in metadata.items():
            f.attrs[k] = v

        if with_summary:
            grp = f.create_group("summary_results")
            grp.create_dataset("total_cost", data=np.array([1e6, 2e6]))
            grp.create_dataset("re_penetration", data=np.array([0.3, 0.45]))

        if with_detailed:
            det = f.create_group("detailed_results")
            scenario = det.create_group("year_2025_threshold_0_5")
            scenario.attrs["objective"] = 1e6
            scenario.attrs["year"] = 2025

            hourly = scenario.create_group("hourly_data")
            gen_grp = hourly.create_group("generation")
            gen_grp.create_dataset("Gas", data=np.random.rand(2, 24))
            gen_grp.create_dataset("Solar", data=np.random.rand(2, 24))
            hourly.create_dataset("curtailment", data=np.random.rand(2, 24))

        if with_demand:
            dem = f.create_group("demand")
            dem.create_dataset("base_demand", data=np.random.rand(24, 2) * 100)

    return path


def _create_rich_hdf5(path: Path) -> Path:
    """Create a more complete HDF5 file with multiple scenarios."""
    with h5py.File(path, "w") as f:
        f.attrs["creation_date"] = "2026-02-21T12:00:00"
        f.attrs["hours"] = 48
        f.attrs["num_nodes"] = 2

        # Summary
        grp = f.create_group("summary_results")
        grp.create_dataset("total_cost", data=np.array([1e6, 2e6, 3e6]))
        grp.create_dataset("emissions", data=np.array([500.0, 400.0, 300.0]))

        # Two detailed scenarios
        det = f.create_group("detailed_results")
        for year in [2025, 2026]:
            sc = det.create_group(f"year_{year}")
            sc.attrs["objective"] = float(year * 1000)
            sc.attrs["year"] = year
            hourly = sc.create_group("hourly_data")
            gen = hourly.create_group("generation")
            gen.create_dataset("Gas", data=np.ones((2, 48)) * 50)
            gen.create_dataset("Solar", data=np.ones((2, 48)) * 30)

        # Demand
        dem = f.create_group("demand")
        dem.create_dataset("base", data=np.ones((48, 2)) * 100)

    return path


# ---------------------------------------------------------------------------
# Tests: ResultsExporter.__init__
# ---------------------------------------------------------------------------

class TestResultsExporterInit:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Results file not found"):
            ResultsExporter(tmp_path / "does_not_exist.h5")

    def test_valid_file_accepted(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5")
        exporter = ResultsExporter(h5_path)
        assert exporter.results_path == h5_path

    def test_string_path_converted(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5")
        exporter = ResultsExporter(str(h5_path))
        assert isinstance(exporter.results_path, Path)

    def test_results_path_attribute(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5")
        exporter = ResultsExporter(h5_path)
        assert exporter.results_path.exists()


# ---------------------------------------------------------------------------
# Tests: to_csv
# ---------------------------------------------------------------------------

class TestToCsv:
    def test_summary_exported(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5", with_summary=True)
        exporter = ResultsExporter(h5_path)
        csv_dir = tmp_path / "csv_output"
        exporter.to_csv(csv_dir)

        summary_dir = csv_dir / "summary"
        assert summary_dir.exists()
        assert (summary_dir / "total_cost.csv").exists()
        assert (summary_dir / "re_penetration.csv").exists()

    def test_csv_content_matches(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5", with_summary=True)
        exporter = ResultsExporter(h5_path)
        csv_dir = tmp_path / "csv_output"
        exporter.to_csv(csv_dir)

        df = pd.read_csv(csv_dir / "summary" / "total_cost.csv")
        np.testing.assert_array_almost_equal(df.values.flatten(), [1e6, 2e6])

    def test_detailed_results_exported(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_summary=True, with_detailed=True
        )
        exporter = ResultsExporter(h5_path)
        csv_dir = tmp_path / "csv_output"
        exporter.to_csv(csv_dir)

        scenario_dir = csv_dir / "year_2025_threshold_0_5"
        assert scenario_dir.exists()
        # Should have generation subdir and curtailment csv
        gen_dir = scenario_dir / "generation"
        assert gen_dir.exists()
        assert (gen_dir / "Gas.csv").exists()
        assert (gen_dir / "Solar.csv").exists()
        assert (scenario_dir / "curtailment.csv").exists()

    def test_demand_exported(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_summary=True, with_demand=True
        )
        exporter = ResultsExporter(h5_path)
        csv_dir = tmp_path / "csv_output"
        exporter.to_csv(csv_dir)

        demand_dir = csv_dir / "demand"
        assert demand_dir.exists()
        assert (demand_dir / "base_demand.csv").exists()

    def test_output_dir_created(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5")
        exporter = ResultsExporter(h5_path)
        nested_dir = tmp_path / "deep" / "nested" / "csv"
        exporter.to_csv(nested_dir)
        assert nested_dir.exists()

    def test_empty_hdf5_no_crash(self, tmp_path):
        """An HDF5 file with no groups should export without errors."""
        h5_path = tmp_path / "empty.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["creation_date"] = "test"
        exporter = ResultsExporter(h5_path)
        csv_dir = tmp_path / "csv_out"
        exporter.to_csv(csv_dir)  # should not raise
        assert csv_dir.exists()


# ---------------------------------------------------------------------------
# Tests: to_json
# ---------------------------------------------------------------------------

class TestToJson:
    def test_basic_export(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5",
            with_summary=True,
            metadata={"test_key": "test_value"},
        )
        exporter = ResultsExporter(h5_path)
        json_path = tmp_path / "results.json"
        exporter.to_json(json_path)

        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "metadata" in data
        assert "summary" in data

    def test_metadata_exported(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5",
            metadata={"custom_attr": "hello"},
        )
        exporter = ResultsExporter(h5_path)
        json_path = tmp_path / "results.json"
        exporter.to_json(json_path)

        data = json.loads(json_path.read_text())
        assert data["metadata"]["custom_attr"] == "hello"

    def test_summary_values_in_json(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5", with_summary=True)
        exporter = ResultsExporter(h5_path)
        json_path = tmp_path / "results.json"
        exporter.to_json(json_path)

        data = json.loads(json_path.read_text())
        assert data["summary"]["total_cost"] == [1e6, 2e6]

    def test_scenario_attrs_in_json(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_detailed=True
        )
        exporter = ResultsExporter(h5_path)
        json_path = tmp_path / "results.json"
        exporter.to_json(json_path)

        data = json.loads(json_path.read_text())
        assert "scenarios" in data
        scenario_key = "year_2025_threshold_0_5"
        assert scenario_key in data["scenarios"]

    def test_json_is_valid(self, tmp_path):
        h5_path = _create_rich_hdf5(tmp_path / "rich.h5")
        exporter = ResultsExporter(h5_path)
        json_path = tmp_path / "out.json"
        exporter.to_json(json_path)

        # Should parse without error
        data = json.loads(json_path.read_text())
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Tests: to_excel
# ---------------------------------------------------------------------------

class TestToExcel:
    def test_summary_sheet_created(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5", with_summary=True)
        exporter = ResultsExporter(h5_path)
        xlsx_path = tmp_path / "results.xlsx"
        exporter.to_excel(xlsx_path)

        assert xlsx_path.exists()
        xls = pd.ExcelFile(xlsx_path)
        assert "Summary" in xls.sheet_names

    def test_generation_sheet_created(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_summary=True, with_detailed=True
        )
        exporter = ResultsExporter(h5_path)
        xlsx_path = tmp_path / "results.xlsx"
        exporter.to_excel(xlsx_path)

        xls = pd.ExcelFile(xlsx_path)
        assert "Generation" in xls.sheet_names

    def test_summary_data_matches(self, tmp_path):
        h5_path = _create_minimal_hdf5(tmp_path / "results.h5", with_summary=True)
        exporter = ResultsExporter(h5_path)
        xlsx_path = tmp_path / "results.xlsx"
        exporter.to_excel(xlsx_path)

        df = pd.read_excel(xlsx_path, sheet_name="Summary")
        assert "total_cost" in df.columns
        np.testing.assert_array_almost_equal(
            df["total_cost"].values, [1e6, 2e6]
        )


# ---------------------------------------------------------------------------
# Tests: export_system_results (standalone function)
# ---------------------------------------------------------------------------

class TestExportSystemResults:
    def test_creates_hdf5_file(self, tmp_path):
        results = {}
        generators = [{"name": "Gas"}, {"name": "Solar"}]
        batteries = [{"name": "Li-ion"}]
        path = export_system_results(
            results, generators, batteries,
            hours=24, num_nodes=2,
            output_filename="test.h5", output_dir=tmp_path,
        )
        assert path.exists()
        assert path.suffix == ".h5"

    def test_metadata_stored(self, tmp_path):
        path = export_system_results(
            {}, [{"name": "G1"}], [{"name": "B1"}],
            hours=48, num_nodes=3,
            output_filename="meta_test.h5", output_dir=tmp_path,
        )
        with h5py.File(path, "r") as f:
            assert f.attrs["hours"] == 48
            assert f.attrs["num_nodes"] == 3
            assert f.attrs["num_generators"] == 1
            assert f.attrs["num_batteries"] == 1

    def test_auto_generated_filename(self, tmp_path):
        path = export_system_results(
            {}, [], [], hours=24, num_nodes=1,
            output_dir=tmp_path,
        )
        assert path.name.startswith("results_")
        assert path.suffix == ".h5"

    def test_scenario_data_stored(self, tmp_path):
        results = {
            ("2025", "0.5"): {
                "objective": 1e6,
                "total_generation": 5e5,
                "hourly_data": {
                    "generation": {
                        "Gas": np.ones((2, 24)) * 50,
                    }
                },
            }
        }
        path = export_system_results(
            results, [{"name": "Gas"}], [],
            hours=24, num_nodes=2,
            output_filename="scenario.h5", output_dir=tmp_path,
        )
        with h5py.File(path, "r") as f:
            assert "detailed_results" in f
            scenarios = list(f["detailed_results"].keys())
            assert len(scenarios) == 1

    def test_string_scenario_key(self, tmp_path):
        results = {"base_case": {"objective": 1000.0}}
        path = export_system_results(
            results, [], [], hours=24, num_nodes=1,
            output_filename="str_key.h5", output_dir=tmp_path,
        )
        with h5py.File(path, "r") as f:
            assert "base_case" in f["detailed_results"]


# ---------------------------------------------------------------------------
# Tests: read_results (standalone function)
# ---------------------------------------------------------------------------

class TestReadResults:
    def test_reads_metadata(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5",
            metadata={"hours": 24, "num_nodes": 2},
        )
        data = read_results(h5_path)
        assert "metadata" in data
        assert data["metadata"]["hours"] == 24

    def test_reads_scenarios(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_detailed=True
        )
        data = read_results(h5_path)
        assert "scenarios" in data
        assert "year_2025_threshold_0_5" in data["scenarios"]

    def test_scenario_hourly_data(self, tmp_path):
        h5_path = _create_minimal_hdf5(
            tmp_path / "results.h5", with_detailed=True
        )
        data = read_results(h5_path)
        sc = data["scenarios"]["year_2025_threshold_0_5"]
        assert "hourly_data" in sc
        assert "generation" in sc["hourly_data"]
        assert "Gas" in sc["hourly_data"]["generation"]

    def test_roundtrip_export_read(self, tmp_path):
        """Export results then read them back."""
        gen_data = np.random.rand(2, 24)
        results = {
            "test_scenario": {
                "objective": 42.0,
                "hourly_data": {
                    "generation": {"TestGen": gen_data},
                },
            }
        }
        path = export_system_results(
            results, [{"name": "TestGen"}], [],
            hours=24, num_nodes=2,
            output_filename="roundtrip.h5", output_dir=tmp_path,
        )
        data = read_results(path)
        read_gen = data["scenarios"]["test_scenario"]["hourly_data"]["generation"]["TestGen"]
        np.testing.assert_array_almost_equal(read_gen, gen_data, decimal=5)

    def test_empty_hdf5(self, tmp_path):
        h5_path = tmp_path / "empty.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["info"] = "empty"
        data = read_results(h5_path)
        assert data["metadata"]["info"] == "empty"
        assert data["scenarios"] == {}


# =====================================================================
# Reservoir HDF5 export
# =====================================================================


class TestReservoirHDF5:
    """Tests for reservoir datasets in HDF5 output."""

    def test_reservoir_groups_in_hdf5(self, tmp_path):
        """Reservoir level/spillage/pump groups should exist in HDF5."""
        h5_path = tmp_path / "reservoir.h5"
        with h5py.File(h5_path, "w") as f:
            f.attrs["creation_date"] = "2026-02-21T00:00:00"
            f.attrs["hours"] = 24
            f.attrs["num_nodes"] = 2
            det = f.create_group("detailed_results")
            scenario = det.create_group("year_2030_threshold_0")
            scenario.attrs["year"] = 2030

            # Add reservoir groups (as runner.py creates them)
            res_level = scenario.create_group("reservoir_level")
            res_level.attrs["description"] = "Reservoir water level [nodes x hours+1] per gen"
            ds = res_level.create_dataset("Hydro", data=np.random.rand(2, 25))
            ds.attrs["units"] = "MWh-eq"

            res_spill = scenario.create_group("reservoir_spillage")
            res_spill.attrs["description"] = "Reservoir spillage [nodes x hours] per gen"
            ds = res_spill.create_dataset("Hydro", data=np.random.rand(2, 24))
            ds.attrs["units"] = "MW-eq"

            res_pump = scenario.create_group("reservoir_pump")
            res_pump.attrs["description"] = "Reservoir pump power [nodes x hours] per gen"
            ds = res_pump.create_dataset("Hydro", data=np.random.rand(2, 24))
            ds.attrs["units"] = "MW-eq"

        # Read back and verify
        data = read_results(h5_path)
        scenario_data = data["scenarios"]["year_2030_threshold_0"]
        hourly = scenario_data["hourly_data"]
        assert "reservoir_level" in hourly
        assert "Hydro" in hourly["reservoir_level"]
        assert hourly["reservoir_level"]["Hydro"].shape == (2, 25)
        assert "reservoir_spillage" in hourly
        assert "Hydro" in hourly["reservoir_spillage"]
        assert hourly["reservoir_spillage"]["Hydro"].shape == (2, 24)
        assert "reservoir_pump" in hourly
        assert "Hydro" in hourly["reservoir_pump"]
        assert hourly["reservoir_pump"]["Hydro"].shape == (2, 24)

    def test_reservoir_not_present_for_non_reservoir_system(self, tmp_path):
        """Systems without reservoirs should not have reservoir groups."""
        h5_path = tmp_path / "no_reservoir.h5"
        _create_minimal_hdf5(h5_path, with_detailed=True)
        data = read_results(h5_path)
        scenario_data = list(data["scenarios"].values())[0]
        hourly = scenario_data["hourly_data"]
        assert "reservoir_level" not in hourly
        assert "reservoir_spillage" not in hourly
        assert "reservoir_pump" not in hourly
