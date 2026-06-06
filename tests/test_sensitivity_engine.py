"""
Tests for esfex.sensitivity.engine module.

Covers the following public interfaces:
- SensitivityParameter dataclass (creation, defaults, categories)
- SobolResult dataclass (creation, to_csv export, field defaults)
- SensitivityEngine class (init, problem property, n_evaluations, generate_samples,
  run_lp_analysis, run_config_analysis, _analyze)
- get_config_parameters() function
- get_lp_parameters() function (mocked LP parser)
- _apply_config_multipliers() config modification
- _run_simulation_and_extract() subprocess wrapper
- _extract_kpis_from_results() HDF5 extraction
- KPI_NAMES and CONFIG_PARAMETERS constants
- Edge cases: empty parameters, zero variance, nan/inf handling
"""

import csv
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py
import numpy as np
import pytest

from esfex.sensitivity.engine import (
    CONFIG_PARAMETERS,
    KPI_NAMES,
    SensitivityEngine,
    SensitivityParameter,
    SobolResult,
    _apply_config_multipliers,
    _extract_kpis_from_results,
    _run_simulation_and_extract,
    get_config_parameters,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_parameters(n=3, category="objective"):
    """Create a list of n SensitivityParameter objects for testing."""
    return [
        SensitivityParameter(
            name=f"param_{i}",
            key=f"key_{i}",
            lower_bound=0.5,
            upper_bound=2.0,
            category=category,
        )
        for i in range(n)
    ]


def _make_base_config():
    """Create a minimal base config dict for _apply_config_multipliers tests."""
    return {
        "systems": {
            "main": {
                "generators": [
                    {
                        "type": "solar",
                        "invest_cost": 1000.0,
                        "fuel_cost": 0.0,
                    },
                    {
                        "type": "wind",
                        "invest_cost": [1200.0, 1100.0],
                        "fuel_cost": 0.0,
                    },
                    {
                        "type": "gas",
                        "invest_cost": 800.0,
                        "fuel_cost": 50.0,
                    },
                    {
                        "type": "coal",
                        "invest_cost": [700.0, 650.0],
                        "fuel_cost": [30.0, 35.0],
                    },
                ],
                "batteries": [
                    {
                        "invest_cost_power": 500.0,
                        "invest_cost_capacity": [200.0, 180.0],
                    },
                ],
                "demand": {
                    "growth_rate": 0.02,
                },
            },
        },
    }


def _write_results_h5(directory, filename, data):
    """Write a mock HDF5 results file under directory/filename.

    ``data`` is a dict mapping dataset names to arrays, all placed
    under the ``summary_results`` group.
    """
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, filename)
    with h5py.File(filepath, "w") as f:
        grp = f.create_group("summary_results")
        for key, arr in data.items():
            grp.create_dataset(key, data=np.asarray(arr))


# ---------------------------------------------------------------------------
# SensitivityParameter
# ---------------------------------------------------------------------------


class TestSensitivityParameter:
    """Tests for the SensitivityParameter dataclass."""

    def test_creation_with_all_fields(self):
        """Creating a parameter with all fields populates correctly."""
        param = SensitivityParameter(
            name="Fuel Cost", key="fuel_cost",
            lower_bound=0.3, upper_bound=3.0, category="config",
        )
        assert param.name == "Fuel Cost"
        assert param.key == "fuel_cost"
        assert param.lower_bound == 0.3
        assert param.upper_bound == 3.0
        assert param.category == "config"

    def test_default_bounds(self):
        """Default lower_bound=0.5, upper_bound=2.0."""
        param = SensitivityParameter(name="x", key="x")
        assert param.lower_bound == 0.5
        assert param.upper_bound == 2.0

    def test_default_category(self):
        """Default category is 'objective'."""
        param = SensitivityParameter(name="x", key="x")
        assert param.category == "objective"

    def test_rhs_category(self):
        """Category can be set to 'rhs'."""
        param = SensitivityParameter(name="x", key="x", category="rhs")
        assert param.category == "rhs"

    def test_config_category(self):
        """Category can be set to 'config'."""
        param = SensitivityParameter(name="x", key="x", category="config")
        assert param.category == "config"

    def test_negative_bounds(self):
        """Negative bounds are accepted (no validation enforced)."""
        param = SensitivityParameter(name="x", key="x", lower_bound=-1.0, upper_bound=0.0)
        assert param.lower_bound == -1.0
        assert param.upper_bound == 0.0

    def test_equal_bounds(self):
        """Equal lower and upper bounds are accepted."""
        param = SensitivityParameter(name="x", key="x", lower_bound=1.0, upper_bound=1.0)
        assert param.lower_bound == param.upper_bound

    def test_name_and_key_are_required(self):
        """TypeError is raised when name or key are missing."""
        with pytest.raises(TypeError):
            SensitivityParameter(name="x")
        with pytest.raises(TypeError):
            SensitivityParameter(key="x")


# ---------------------------------------------------------------------------
# SobolResult
# ---------------------------------------------------------------------------


class TestSobolResult:
    """Tests for the SobolResult dataclass."""

    def test_creation_defaults(self):
        """Default SobolResult has empty fields."""
        result = SobolResult()
        assert result.parameters == []
        assert result.kpi_names == []
        assert result.S1 == {}
        assert result.ST == {}
        assert result.S1_conf == {}
        assert result.ST_conf == {}
        assert result.n_samples == 0
        assert result.n_evaluations == 0

    def test_creation_with_data(self):
        """SobolResult stores parameters and KPI names."""
        result = SobolResult(
            parameters=["a", "b"],
            kpi_names=["cost", "load"],
            n_samples=64,
            n_evaluations=384,
        )
        assert result.parameters == ["a", "b"]
        assert result.kpi_names == ["cost", "load"]
        assert result.n_samples == 64
        assert result.n_evaluations == 384

    def test_to_csv_creates_file(self, tmp_path):
        """to_csv() creates a CSV file at the specified path."""
        result = SobolResult(
            parameters=["p1", "p2"],
            kpi_names=["kpi_a"],
            S1={"kpi_a": np.array([0.3, 0.7])},
            ST={"kpi_a": np.array([0.4, 0.8])},
            S1_conf={"kpi_a": np.array([0.01, 0.02])},
            ST_conf={"kpi_a": np.array([0.03, 0.04])},
        )
        csv_path = tmp_path / "sobol.csv"
        result.to_csv(csv_path)
        assert csv_path.exists()

    def test_to_csv_header(self, tmp_path):
        """CSV has the expected header row."""
        result = SobolResult(
            parameters=["p1"],
            kpi_names=["kpi_a"],
            S1={"kpi_a": np.array([0.5])},
            ST={"kpi_a": np.array([0.6])},
            S1_conf={"kpi_a": np.array([0.01])},
            ST_conf={"kpi_a": np.array([0.02])},
        )
        csv_path = tmp_path / "sobol.csv"
        result.to_csv(csv_path)

        with open(csv_path) as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == ["KPI", "Parameter", "S1", "S1_conf", "ST", "ST_conf"]

    def test_to_csv_content_correct(self, tmp_path):
        """CSV data rows match the Sobol indices."""
        result = SobolResult(
            parameters=["alpha", "beta"],
            kpi_names=["cost"],
            S1={"cost": np.array([0.123456, 0.654321])},
            ST={"cost": np.array([0.234567, 0.765432])},
            S1_conf={"cost": np.array([0.010000, 0.020000])},
            ST_conf={"cost": np.array([0.030000, 0.040000])},
        )
        csv_path = tmp_path / "sobol.csv"
        result.to_csv(csv_path)

        with open(csv_path) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0][0] == "cost"
        assert rows[0][1] == "alpha"
        assert float(rows[0][2]) == pytest.approx(0.123456, abs=1e-6)
        assert rows[1][1] == "beta"
        assert float(rows[1][4]) == pytest.approx(0.765432, abs=1e-6)

    def test_to_csv_multiple_kpis(self, tmp_path):
        """CSV includes rows for every (KPI, parameter) combination."""
        params = ["p1", "p2", "p3"]
        kpis = ["cost", "load"]
        result = SobolResult(
            parameters=params,
            kpi_names=kpis,
            S1={k: np.zeros(3) for k in kpis},
            ST={k: np.zeros(3) for k in kpis},
            S1_conf={k: np.zeros(3) for k in kpis},
            ST_conf={k: np.zeros(3) for k in kpis},
        )
        csv_path = tmp_path / "sobol.csv"
        result.to_csv(csv_path)

        with open(csv_path) as f:
            reader = csv.reader(f)
            next(reader)
            rows = list(reader)

        assert len(rows) == 6  # 2 KPIs x 3 params

    def test_to_csv_accepts_string_path(self, tmp_path):
        """to_csv() works with a string path."""
        result = SobolResult(
            parameters=["p1"],
            kpi_names=["k1"],
            S1={"k1": np.array([0.5])},
            ST={"k1": np.array([0.6])},
            S1_conf={"k1": np.array([0.01])},
            ST_conf={"k1": np.array([0.02])},
        )
        csv_path = str(tmp_path / "out.csv")
        result.to_csv(csv_path)
        assert os.path.exists(csv_path)

    def test_to_csv_empty_kpis(self, tmp_path):
        """to_csv() with no KPIs produces a header-only CSV."""
        result = SobolResult(parameters=["p1"], kpi_names=[])
        csv_path = tmp_path / "empty.csv"
        result.to_csv(csv_path)

        with open(csv_path) as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        assert header == ["KPI", "Parameter", "S1", "S1_conf", "ST", "ST_conf"]
        assert len(rows) == 0

    def test_default_factory_independence(self):
        """Each SobolResult instance has independent mutable defaults."""
        a = SobolResult()
        b = SobolResult()
        a.parameters.append("x")
        assert "x" not in b.parameters


# ---------------------------------------------------------------------------
# SensitivityEngine initialisation and properties
# ---------------------------------------------------------------------------


class TestSensitivityEngineInit:
    """Tests for SensitivityEngine construction, problem, and n_evaluations."""

    def test_mode_stored(self):
        """Engine stores the analysis mode."""
        engine = SensitivityEngine(mode="lp", parameters=[])
        assert engine.mode == "lp"

    def test_config_mode(self):
        """Engine stores 'config' mode."""
        engine = SensitivityEngine(mode="config", parameters=[])
        assert engine.mode == "config"

    def test_parameters_stored(self):
        """Engine stores the parameter list."""
        params = _make_parameters(2)
        engine = SensitivityEngine(mode="lp", parameters=params)
        assert engine.parameters is params
        assert len(engine.parameters) == 2

    def test_default_kpi_names(self):
        """Without kpi_names argument, defaults to KPI_NAMES."""
        engine = SensitivityEngine(mode="lp", parameters=[])
        assert engine.kpi_names == list(KPI_NAMES)

    def test_custom_kpi_names(self):
        """Custom kpi_names override the default."""
        engine = SensitivityEngine(
            mode="lp", parameters=[], kpi_names=["my_kpi"],
        )
        assert engine.kpi_names == ["my_kpi"]

    def test_default_n_base_samples(self):
        """Default n_base_samples is 128."""
        engine = SensitivityEngine(mode="lp", parameters=[])
        assert engine.n_base_samples == 128

    def test_custom_n_base_samples(self):
        """Custom n_base_samples is stored."""
        engine = SensitivityEngine(mode="lp", parameters=[], n_base_samples=64)
        assert engine.n_base_samples == 64

    def test_problem_property_structure(self):
        """problem property returns dict with num_vars, names, bounds."""
        params = _make_parameters(3)
        engine = SensitivityEngine(mode="lp", parameters=params)
        prob = engine.problem

        assert prob["num_vars"] == 3
        assert prob["names"] == ["param_0", "param_1", "param_2"]
        assert len(prob["bounds"]) == 3
        assert prob["bounds"][0] == [0.5, 2.0]

    def test_problem_with_mixed_bounds(self):
        """problem property reflects per-parameter bounds."""
        params = [
            SensitivityParameter(name="a", key="a", lower_bound=0.1, upper_bound=5.0),
            SensitivityParameter(name="b", key="b", lower_bound=0.8, upper_bound=1.2),
        ]
        engine = SensitivityEngine(mode="lp", parameters=params)
        prob = engine.problem
        assert prob["bounds"][0] == [0.1, 5.0]
        assert prob["bounds"][1] == [0.8, 1.2]

    def test_n_evaluations_formula(self):
        """n_evaluations = N * (D + 2) for Saltelli with calc_second_order=False."""
        params = _make_parameters(4)
        engine = SensitivityEngine(mode="lp", parameters=params, n_base_samples=64)
        D = 4
        N = 64
        expected = N * (D + 2)
        assert engine.n_evaluations == expected

    def test_n_evaluations_one_parameter(self):
        """n_evaluations for a single parameter."""
        params = _make_parameters(1)
        engine = SensitivityEngine(mode="lp", parameters=params, n_base_samples=32)
        assert engine.n_evaluations == 32 * (1 + 2)

    def test_n_evaluations_zero_parameters(self):
        """n_evaluations with zero parameters is N * 2."""
        engine = SensitivityEngine(mode="lp", parameters=[], n_base_samples=16)
        assert engine.n_evaluations == 16 * 2

    def test_problem_empty_parameters(self):
        """problem property with zero parameters."""
        engine = SensitivityEngine(mode="lp", parameters=[])
        prob = engine.problem
        assert prob["num_vars"] == 0
        assert prob["names"] == []
        assert prob["bounds"] == []


# ---------------------------------------------------------------------------
# SensitivityEngine.generate_samples
# ---------------------------------------------------------------------------


class TestGenerateSamples:
    """Tests for SensitivityEngine.generate_samples (mocked SALib)."""

    def test_generate_samples_calls_sobol(self):
        """generate_samples delegates to SALib.sample.sobol."""
        params = _make_parameters(2)
        engine = SensitivityEngine(mode="lp", parameters=params, n_base_samples=8)

        expected_shape = (8 * (2 * 2 + 2), 2)  # (48, 2)
        mock_array = np.random.rand(*expected_shape)

        mock_sobol_sample = MagicMock()
        mock_sobol_sample.sample.return_value = mock_array

        with patch.dict("sys.modules", {"SALib.sample.sobol": mock_sobol_sample}):
            result = engine.generate_samples()

        mock_sobol_sample.sample.assert_called_once()
        call_args = mock_sobol_sample.sample.call_args
        assert call_args[0][1] == 8  # n_base_samples
        assert call_args[1]["calc_second_order"] is False
        np.testing.assert_array_equal(result, mock_array)

    def test_generate_samples_shape(self):
        """generate_samples returns array of shape (N*(2D+2), D)."""
        params = _make_parameters(3)
        engine = SensitivityEngine(mode="lp", parameters=params, n_base_samples=4)

        n_eval = 4 * (2 * 3 + 2)
        mock_array = np.random.rand(n_eval, 3)

        mock_sobol_sample = MagicMock()
        mock_sobol_sample.sample.return_value = mock_array

        with patch.dict("sys.modules", {"SALib.sample.sobol": mock_sobol_sample}):
            result = engine.generate_samples()

        assert result.shape == (n_eval, 3)


# ---------------------------------------------------------------------------
# SensitivityEngine._analyze
# ---------------------------------------------------------------------------


class TestAnalyze:
    """Tests for SensitivityEngine._analyze (Sobol index computation)."""

    def _mock_sobol_analyze(self, mock_si=None):
        """Create a mock for SALib.analyze.sobol.analyze function.

        The engine does ``from SALib.analyze import sobol`` then calls
        ``sobol.analyze(...)``.  We need ``SALib.analyze`` to expose a
        ``sobol`` attribute that has an ``analyze`` method.
        """
        mock_sobol = MagicMock()
        if mock_si is not None:
            mock_sobol.analyze.return_value = mock_si
        mock_salib_analyze = MagicMock()
        mock_salib_analyze.sobol = mock_sobol
        return mock_salib_analyze, mock_sobol

    def test_analyze_returns_sobol_result(self):
        """_analyze returns a SobolResult with correct metadata."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evaluations = {"cost": list(np.random.rand(n_eval))}

        mock_si = {
            "S1": np.array([0.4, 0.6]),
            "ST": np.array([0.5, 0.7]),
            "S1_conf": np.array([0.01, 0.02]),
            "ST_conf": np.array([0.03, 0.04]),
        }
        mock_parent, mock_sobol = self._mock_sobol_analyze(mock_si)

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        assert isinstance(result, SobolResult)
        assert result.parameters == ["param_0", "param_1"]
        assert result.kpi_names == ["cost"]
        assert result.n_samples == 8
        assert result.n_evaluations == n_eval

    def test_analyze_stores_indices(self):
        """_analyze populates S1, ST, S1_conf, ST_conf for each KPI."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost", "load"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evaluations = {
            "cost": list(np.random.rand(n_eval)),
            "load": list(np.random.rand(n_eval)),
        }

        mock_si = {
            "S1": np.array([0.3, 0.7]),
            "ST": np.array([0.4, 0.8]),
            "S1_conf": np.array([0.01, 0.02]),
            "ST_conf": np.array([0.03, 0.04]),
        }
        mock_parent, mock_sobol = self._mock_sobol_analyze(mock_si)

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        for kpi in ["cost", "load"]:
            assert kpi in result.S1
            assert kpi in result.ST
            np.testing.assert_array_equal(result.S1[kpi], np.array([0.3, 0.7]))

    def test_analyze_zero_variance_returns_zeros(self):
        """When all evaluations are identical, S1/ST are zero arrays."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evaluations = {"cost": [42.0] * n_eval}

        mock_parent, mock_sobol = self._mock_sobol_analyze()

        # sobol.analyze should NOT be called for zero-variance KPIs
        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        mock_sobol.analyze.assert_not_called()
        np.testing.assert_array_equal(result.S1["cost"], np.zeros(2))
        np.testing.assert_array_equal(result.ST["cost"], np.zeros(2))
        np.testing.assert_array_equal(result.S1_conf["cost"], np.zeros(2))
        np.testing.assert_array_equal(result.ST_conf["cost"], np.zeros(2))

    def test_analyze_inf_values_replaced(self):
        """Inf values in evaluations are replaced before SALib call."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evals = list(np.random.rand(n_eval) * 100)
        evals[0] = float("inf")
        evals[5] = float("-inf")
        evaluations = {"cost": evals}

        mock_si = {
            "S1": np.array([0.5, 0.5]),
            "ST": np.array([0.6, 0.6]),
            "S1_conf": np.array([0.01, 0.01]),
            "ST_conf": np.array([0.02, 0.02]),
        }
        mock_parent, mock_sobol = self._mock_sobol_analyze(mock_si)

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        # Should succeed without errors (inf replaced)
        assert "cost" in result.S1

    def test_analyze_nan_values_replaced(self):
        """NaN values in evaluations are replaced before SALib call."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evals = list(np.random.rand(n_eval) * 100)
        evals[3] = float("nan")
        evaluations = {"cost": evals}

        mock_si = {
            "S1": np.array([0.5, 0.5]),
            "ST": np.array([0.6, 0.6]),
            "S1_conf": np.array([0.01, 0.01]),
            "ST_conf": np.array([0.02, 0.02]),
        }
        mock_parent, mock_sobol = self._mock_sobol_analyze(mock_si)

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        assert "cost" in result.S1

    def test_analyze_all_nan_gives_zeros(self):
        """All NaN evaluations produce zero Sobol indices (zero variance)."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evaluations = {"cost": [float("nan")] * n_eval}

        mock_parent, mock_sobol = self._mock_sobol_analyze()

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        # All nan → finite_mask all False → max_finite fallback 1e12
        # All replaced with same value → zero variance → zeros
        mock_sobol.analyze.assert_not_called()
        np.testing.assert_array_equal(result.S1["cost"], np.zeros(2))

    def test_analyze_multiple_kpis_mixed_variance(self):
        """One KPI with variance, another constant: both handled correctly."""
        params = _make_parameters(2)
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost", "flat"],
            n_base_samples=8,
        )

        n_eval = 8 * (2 * 2 + 2)
        samples = np.random.rand(n_eval, 2)
        evaluations = {
            "cost": list(np.random.rand(n_eval) * 1000),
            "flat": [100.0] * n_eval,
        }

        mock_si = {
            "S1": np.array([0.4, 0.6]),
            "ST": np.array([0.5, 0.7]),
            "S1_conf": np.array([0.01, 0.02]),
            "ST_conf": np.array([0.03, 0.04]),
        }
        mock_parent, mock_sobol = self._mock_sobol_analyze(mock_si)

        with patch.dict("sys.modules", {"SALib.analyze": mock_parent,
                                         "SALib.analyze.sobol": mock_sobol}):
            result = engine._analyze(samples, evaluations, n_eval)

        # "cost" should use SALib results
        np.testing.assert_array_equal(result.S1["cost"], np.array([0.4, 0.6]))
        # "flat" should be all zeros (zero variance)
        np.testing.assert_array_equal(result.S1["flat"], np.zeros(2))


# ---------------------------------------------------------------------------
# KPI_NAMES and CONFIG_PARAMETERS constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_kpi_names_is_list(self):
        """KPI_NAMES is a list of strings."""
        assert isinstance(KPI_NAMES, list)
        assert all(isinstance(k, str) for k in KPI_NAMES)

    def test_kpi_names_contains_expected(self):
        """KPI_NAMES contains the expected KPI identifiers."""
        expected = {"total_cost", "inv_gen_total", "inv_bat_total",
                    "curtailment", "load_shedding"}
        assert set(KPI_NAMES) == expected

    def test_config_parameters_is_list(self):
        """CONFIG_PARAMETERS is a list of tuples."""
        assert isinstance(CONFIG_PARAMETERS, list)
        assert all(isinstance(item, tuple) for item in CONFIG_PARAMETERS)

    def test_config_parameters_tuple_structure(self):
        """Each CONFIG_PARAMETERS entry is (key, display_name, (low, high))."""
        for item in CONFIG_PARAMETERS:
            assert len(item) == 3
            key, display, bounds = item
            assert isinstance(key, str)
            assert isinstance(display, str)
            assert isinstance(bounds, tuple) and len(bounds) == 2
            assert bounds[0] < bounds[1]

    def test_config_parameters_count(self):
        """CONFIG_PARAMETERS has 9 entries."""
        assert len(CONFIG_PARAMETERS) == 9


# ---------------------------------------------------------------------------
# get_config_parameters()
# ---------------------------------------------------------------------------


class TestGetConfigParameters:
    """Tests for get_config_parameters() function."""

    def test_returns_list_of_sensitivity_parameters(self):
        """Returns a list of SensitivityParameter objects."""
        params = get_config_parameters()
        assert isinstance(params, list)
        assert all(isinstance(p, SensitivityParameter) for p in params)

    def test_count_matches_config_parameters(self):
        """Number of returned parameters matches CONFIG_PARAMETERS."""
        params = get_config_parameters()
        assert len(params) == len(CONFIG_PARAMETERS)

    def test_all_category_config(self):
        """All returned parameters have category='config'."""
        params = get_config_parameters()
        for p in params:
            assert p.category == "config"

    def test_keys_match_config_parameters(self):
        """Parameter keys match the first element of CONFIG_PARAMETERS tuples."""
        params = get_config_parameters()
        expected_keys = [item[0] for item in CONFIG_PARAMETERS]
        actual_keys = [p.key for p in params]
        assert actual_keys == expected_keys

    def test_names_match_config_parameters(self):
        """Parameter names match the second element (display name)."""
        params = get_config_parameters()
        expected_names = [item[1] for item in CONFIG_PARAMETERS]
        actual_names = [p.name for p in params]
        assert actual_names == expected_names

    def test_bounds_match_config_parameters(self):
        """Parameter bounds match CONFIG_PARAMETERS tuples."""
        params = get_config_parameters()
        for p, (_, _, bounds) in zip(params, CONFIG_PARAMETERS):
            assert p.lower_bound == bounds[0]
            assert p.upper_bound == bounds[1]

    def test_fuel_cost_upper_bound_is_three(self):
        """fuel_cost parameter has upper_bound=3.0 (wider range)."""
        params = get_config_parameters()
        fuel_cost = [p for p in params if p.key == "fuel_cost"]
        assert len(fuel_cost) == 1
        assert fuel_cost[0].upper_bound == 3.0

    def test_carbon_price_lower_bound_is_zero(self):
        """carbon_price parameter allows a lower_bound of 0.0."""
        params = get_config_parameters()
        carbon = [p for p in params if p.key == "carbon_price"]
        assert len(carbon) == 1
        assert carbon[0].lower_bound == 0.0


# ---------------------------------------------------------------------------
# _apply_config_multipliers()
# ---------------------------------------------------------------------------


class TestApplyConfigMultipliers:
    """Tests for _apply_config_multipliers() function."""

    def test_returns_new_dict(self):
        """Result is a deep copy; original config is not modified."""
        base = _make_base_config()
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        original_cost = base["systems"]["main"]["generators"][0]["invest_cost"]
        result = _apply_config_multipliers(base, params, sample)

        assert result is not base
        assert base["systems"]["main"]["generators"][0]["invest_cost"] == original_cost

    def test_renewable_scalar_cost_multiplied(self):
        """Solar/wind/pv generators with scalar invest_cost are multiplied."""
        base = _make_base_config()
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        solar = result["systems"]["main"]["generators"][0]
        assert solar["invest_cost"] == pytest.approx(1000.0 * 1.5)

    def test_renewable_list_cost_multiplied(self):
        """Generators with list invest_cost have each element multiplied."""
        base = _make_base_config()
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([2.0])

        result = _apply_config_multipliers(base, params, sample)
        wind = result["systems"]["main"]["generators"][1]
        assert wind["invest_cost"] == [2400.0, 2200.0]

    def test_conventional_cost_multiplied(self):
        """Non-renewable generators use invest_cost_conventional multiplier."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Conv", key="invest_cost_conventional",
                                       category="config")]
        sample = np.array([1.8])

        result = _apply_config_multipliers(base, params, sample)
        gas = result["systems"]["main"]["generators"][2]
        assert gas["invest_cost"] == pytest.approx(800.0 * 1.8)

    def test_conventional_list_cost_multiplied(self):
        """Conventional generator with list invest_cost is element-wise multiplied."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Conv", key="invest_cost_conventional",
                                       category="config")]
        sample = np.array([0.5])

        result = _apply_config_multipliers(base, params, sample)
        coal = result["systems"]["main"]["generators"][3]
        assert coal["invest_cost"] == [350.0, 325.0]

    def test_fuel_cost_scalar_multiplied(self):
        """fuel_cost multiplier affects scalar fuel_cost in generators."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Fuel", key="fuel_cost",
                                       category="config")]
        sample = np.array([2.0])

        result = _apply_config_multipliers(base, params, sample)
        gas = result["systems"]["main"]["generators"][2]
        assert gas["fuel_cost"] == pytest.approx(100.0)

    def test_fuel_cost_list_multiplied(self):
        """fuel_cost multiplier affects list fuel_cost element-wise."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Fuel", key="fuel_cost",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        coal = result["systems"]["main"]["generators"][3]
        assert coal["fuel_cost"] == [45.0, 52.5]

    def test_storage_power_cost_multiplied(self):
        """invest_cost_storage affects battery invest_cost_power (scalar)."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Stor", key="invest_cost_storage",
                                       category="config")]
        sample = np.array([1.2])

        result = _apply_config_multipliers(base, params, sample)
        bat = result["systems"]["main"]["batteries"][0]
        assert bat["invest_cost_power"] == pytest.approx(500.0 * 1.2)

    def test_storage_capacity_cost_multiplied(self):
        """invest_cost_storage affects battery invest_cost_capacity (list)."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Stor", key="invest_cost_storage",
                                       category="config")]
        sample = np.array([2.0])

        result = _apply_config_multipliers(base, params, sample)
        bat = result["systems"]["main"]["batteries"][0]
        assert bat["invest_cost_capacity"] == [400.0, 360.0]

    def test_demand_growth_multiplied(self):
        """demand_growth multiplier scales demand.growth_rate."""
        base = _make_base_config()
        params = [SensitivityParameter(name="Demand", key="demand_growth",
                                       category="config")]
        sample = np.array([1.3])

        result = _apply_config_multipliers(base, params, sample)
        growth = result["systems"]["main"]["demand"]["growth_rate"]
        assert growth == pytest.approx(0.02 * 1.3)

    def test_multiple_multipliers_applied(self):
        """Multiple parameters are applied simultaneously."""
        base = _make_base_config()
        params = [
            SensitivityParameter(name="RE", key="invest_cost_renewables", category="config"),
            SensitivityParameter(name="Fuel", key="fuel_cost", category="config"),
            SensitivityParameter(name="Demand", key="demand_growth", category="config"),
        ]
        sample = np.array([1.5, 2.0, 0.8])

        result = _apply_config_multipliers(base, params, sample)

        solar = result["systems"]["main"]["generators"][0]
        assert solar["invest_cost"] == pytest.approx(1500.0)

        gas = result["systems"]["main"]["generators"][2]
        assert gas["fuel_cost"] == pytest.approx(100.0)

        growth = result["systems"]["main"]["demand"]["growth_rate"]
        assert growth == pytest.approx(0.02 * 0.8)

    def test_no_matching_multipliers_unchanged(self):
        """Parameters with keys absent from config leave it unchanged."""
        base = _make_base_config()
        params = [SensitivityParameter(name="X", key="nonexistent_key",
                                       category="config")]
        sample = np.array([5.0])

        result = _apply_config_multipliers(base, params, sample)
        solar = result["systems"]["main"]["generators"][0]
        assert solar["invest_cost"] == 1000.0

    def test_empty_parameters_unchanged(self):
        """Empty parameter list returns config unchanged (but deep copied)."""
        base = _make_base_config()
        result = _apply_config_multipliers(base, [], np.array([]))

        assert result == base
        assert result is not base

    def test_no_systems_key(self):
        """Config without 'systems' key does not raise."""
        base = {"meta": {"name": "test"}}
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        assert result == base

    def test_generators_not_list_skipped(self):
        """If generators is not a list, it is skipped without error."""
        base = {
            "systems": {
                "main": {
                    "generators": "invalid_value",
                },
            },
        }
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["main"]["generators"] == "invalid_value"

    def test_batteries_not_list_skipped(self):
        """If batteries is not a list, storage multiplier is skipped."""
        base = {
            "systems": {
                "main": {
                    "batteries": "invalid",
                },
            },
        }
        params = [SensitivityParameter(name="Stor", key="invest_cost_storage",
                                       category="config")]
        sample = np.array([2.0])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["main"]["batteries"] == "invalid"

    def test_demand_not_dict_skipped(self):
        """If demand config is not a dict, demand_growth is skipped."""
        base = {
            "systems": {
                "main": {
                    "demand": "not_a_dict",
                },
            },
        }
        params = [SensitivityParameter(name="D", key="demand_growth",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["main"]["demand"] == "not_a_dict"

    def test_system_value_not_dict_skipped(self):
        """If a system's value is not a dict, it is skipped."""
        base = {
            "systems": {
                "main": "not_a_dict",
            },
        }
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["main"] == "not_a_dict"

    def test_generator_not_dict_skipped(self):
        """Non-dict entries in generators list are skipped."""
        base = {
            "systems": {
                "main": {
                    "generators": ["not_a_dict", {"type": "solar", "invest_cost": 100.0}],
                },
            },
        }
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([2.0])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["main"]["generators"][0] == "not_a_dict"
        assert result["systems"]["main"]["generators"][1]["invest_cost"] == pytest.approx(200.0)

    def test_multiple_systems(self):
        """Multipliers are applied to every system in the config."""
        base = {
            "systems": {
                "north": {
                    "generators": [{"type": "solar", "invest_cost": 500.0}],
                },
                "south": {
                    "generators": [{"type": "wind", "invest_cost": 700.0}],
                },
            },
        }
        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        sample = np.array([1.5])

        result = _apply_config_multipliers(base, params, sample)
        assert result["systems"]["north"]["generators"][0]["invest_cost"] == pytest.approx(750.0)
        assert result["systems"]["south"]["generators"][0]["invest_cost"] == pytest.approx(1050.0)


# ---------------------------------------------------------------------------
# _extract_kpis_from_results()
# ---------------------------------------------------------------------------


class TestExtractKpisFromResults:
    """Tests for _extract_kpis_from_results() with real temp HDF5 files."""

    def test_no_h5_files_returns_nan(self, tmp_path):
        """Empty directory produces all-NaN KPIs."""
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert set(kpis.keys()) == set(KPI_NAMES)
        for v in kpis.values():
            assert np.isnan(v)

    def test_loose_filename_pattern_accepted(self, tmp_path):
        """The extractor falls back to *.h5 so any HDF5 in the dir is read."""
        _write_results_h5(str(tmp_path), "output.h5", {"total_cost": [100.0]})
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(100.0)

    def test_single_file_total_cost(self, tmp_path):
        """total_cost is extracted from a single results file."""
        _write_results_h5(str(tmp_path), "results_sys1.h5", {
            "total_cost": [1000.0, 2000.0],
        })
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(3000.0)

    def test_single_file_extractable_kpis(self, tmp_path):
        """total_cost, load_shedding (from loss_of_load) and curtailment
        (from detailed_results year groups) come out of a single file.
        inv_gen/inv_bat are NOT sourced from the H5 (runner doesn't export
        a scalar MW-invested summary) and stay NaN."""
        _write_results_h5(str(tmp_path), "results_main.h5", {
            "total_cost": [1000.0],
            "loss_of_load": [5.0],
        })
        filepath = os.path.join(str(tmp_path), "results_main.h5")
        with h5py.File(filepath, "a") as f:
            det = f.create_group("detailed_results")
            y = det.create_group("year_2025_threshold_0")
            y.create_dataset("curtailment", data=np.array([10.0, 20.0]))

        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(1000.0)
        assert kpis["load_shedding"] == pytest.approx(5.0)
        assert kpis["curtailment"] == pytest.approx(30.0)
        assert np.isnan(kpis["inv_gen_total"])
        assert np.isnan(kpis["inv_bat_total"])

    def test_multiple_files_summed(self, tmp_path):
        """total_cost across multiple results files is summed."""
        _write_results_h5(str(tmp_path), "results_north.h5", {
            "total_cost": [1000.0],
        })
        _write_results_h5(str(tmp_path), "results_south.h5", {
            "total_cost": [2000.0],
        })
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(3000.0)

    def test_missing_dataset_keeps_nan(self, tmp_path):
        """When summary_results has total_cost only, load_shedding stays NaN;
        when detailed_results is absent, curtailment stays NaN; inv_gen/inv_bat
        are never extracted from the H5."""
        _write_results_h5(str(tmp_path), "results_sys.h5", {
            "total_cost": [500.0],
        })
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(500.0)
        # load_shedding stayed NaN initially, but found_summary=True flips it to 0.
        assert kpis["load_shedding"] == pytest.approx(0.0)
        # Curtailment requires detailed_results/year_*/curtailment; absent → NaN.
        assert np.isnan(kpis["curtailment"])
        assert np.isnan(kpis["inv_gen_total"])
        assert np.isnan(kpis["inv_bat_total"])

    def test_no_summary_results_group(self, tmp_path):
        """File without 'summary_results' group leaves all KPIs NaN."""
        filepath = str(tmp_path / "results_bad.h5")
        with h5py.File(filepath, "w") as f:
            f.create_dataset("other_data", data=[1, 2, 3])

        kpis = _extract_kpis_from_results(str(tmp_path))
        # No summary_results → found_summary stays False → KPIs stay NaN.
        assert np.isnan(kpis["total_cost"])
        assert np.isnan(kpis["load_shedding"])

    def test_multidimensional_dataset(self, tmp_path):
        """Multi-dimensional datasets are summed correctly."""
        data = np.array([[10.0, 20.0], [30.0, 40.0]])  # shape (2,2)
        _write_results_h5(str(tmp_path), "results_sys.h5", {
            "total_cost": data,
        })
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(100.0)

    def test_zero_values(self, tmp_path):
        """All-zero summary_results yields zero extractable KPIs (inv stays NaN)."""
        _write_results_h5(str(tmp_path), "results_sys.h5", {
            "total_cost": [0.0],
            "loss_of_load": [0.0],
        })
        kpis = _extract_kpis_from_results(str(tmp_path))
        assert kpis["total_cost"] == pytest.approx(0.0)
        assert kpis["load_shedding"] == pytest.approx(0.0)
        # inv_gen_total / inv_bat_total / curtailment are not in summary_results
        assert np.isnan(kpis["inv_gen_total"])
        assert np.isnan(kpis["inv_bat_total"])
        assert np.isnan(kpis["curtailment"])


# ---------------------------------------------------------------------------
# _run_simulation_and_extract()
# ---------------------------------------------------------------------------


class TestRunSimulationAndExtract:
    """Tests for _run_simulation_and_extract() with mocked subprocess."""

    def test_success_calls_extract(self, tmp_path):
        """On successful subprocess run, KPIs are extracted from output dir."""
        config_path = str(tmp_path / "config.yaml")
        output_dir = str(tmp_path / "output")

        # Create a mock results file in the output dir
        os.makedirs(output_dir, exist_ok=True)
        _write_results_h5(output_dir, "results_sys.h5", {
            "total_cost": [5000.0],
        })

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("esfex.sensitivity.engine.subprocess.run", return_value=mock_result):
            kpis = _run_simulation_and_extract(config_path, output_dir)

        assert kpis["total_cost"] == pytest.approx(5000.0)

    def test_failure_returns_nan(self, tmp_path):
        """Non-zero returncode produces all-NaN KPIs."""
        config_path = str(tmp_path / "config.yaml")
        output_dir = str(tmp_path / "output")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Some error occurred"

        with patch("esfex.sensitivity.engine.subprocess.run", return_value=mock_result):
            kpis = _run_simulation_and_extract(config_path, output_dir)

        for v in kpis.values():
            assert np.isnan(v)

    def test_timeout_returns_nan(self, tmp_path):
        """subprocess.TimeoutExpired produces all-NaN KPIs."""
        import subprocess as sp

        config_path = str(tmp_path / "config.yaml")
        output_dir = str(tmp_path / "output")

        with patch("esfex.sensitivity.engine.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="test", timeout=3600)):
            kpis = _run_simulation_and_extract(config_path, output_dir)

        for v in kpis.values():
            assert np.isnan(v)

    def test_exception_returns_nan(self, tmp_path):
        """Generic exception produces all-NaN KPIs."""
        config_path = str(tmp_path / "config.yaml")
        output_dir = str(tmp_path / "output")

        with patch("esfex.sensitivity.engine.subprocess.run",
                   side_effect=OSError("Cannot execute")):
            kpis = _run_simulation_and_extract(config_path, output_dir)

        for v in kpis.values():
            assert np.isnan(v)


# ---------------------------------------------------------------------------
# get_lp_parameters() (mocked LP parser)
# ---------------------------------------------------------------------------


class TestGetLpParameters:
    """Tests for get_lp_parameters() with mocked lp_parser."""

    def test_returns_list_of_parameters(self):
        """Returns a list of SensitivityParameter objects."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"gen_cost", "bat_cost"}
        mock_model.get_rhs_groups.return_value = {"demand_limit"}

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        assert isinstance(params, list)
        assert all(isinstance(p, SensitivityParameter) for p in params)

    def test_objective_parameters_have_correct_category(self):
        """Parameters from objective groups have category='objective'."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"gen_cost"}
        mock_model.get_rhs_groups.return_value = set()

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        assert len(params) == 1
        assert params[0].category == "objective"
        assert params[0].key == "gen_cost"
        assert params[0].lower_bound == 0.5
        assert params[0].upper_bound == 2.0

    def test_rhs_parameters_have_correct_bounds(self):
        """Parameters from RHS groups have bounds (0.8, 1.5)."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = set()
        mock_model.get_rhs_groups.return_value = {"capacity_limit"}

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        assert len(params) == 1
        assert params[0].category == "rhs"
        assert params[0].lower_bound == 0.8
        assert params[0].upper_bound == 1.5

    def test_display_name_formatting(self):
        """Parameter names are title-cased with underscores replaced by spaces."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"gen_inv_cost"}
        mock_model.get_rhs_groups.return_value = set()

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        assert params[0].name == "Gen Inv Cost"

    def test_sorted_output(self):
        """Parameters are sorted by group name within each category."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"z_cost", "a_cost", "m_cost"}
        mock_model.get_rhs_groups.return_value = {"z_rhs", "a_rhs"}

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        obj_keys = [p.key for p in params if p.category == "objective"]
        rhs_keys = [p.key for p in params if p.category == "rhs"]
        assert obj_keys == sorted(obj_keys)
        assert rhs_keys == sorted(rhs_keys)

    def test_objective_before_rhs(self):
        """Objective parameters appear before RHS parameters."""
        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"gen_cost"}
        mock_model.get_rhs_groups.return_value = {"demand_rhs"}

        from esfex.sensitivity.engine import get_lp_parameters
        with patch("esfex.sensitivity.lp_parser.parse_lp_file", return_value=mock_model):
            params = get_lp_parameters("dummy.lp")

        categories = [p.category for p in params]
        assert categories == ["objective", "rhs"]


# ---------------------------------------------------------------------------
# SensitivityEngine.run_lp_analysis (mocked)
# ---------------------------------------------------------------------------


class TestRunLpAnalysis:
    """Tests for SensitivityEngine.run_lp_analysis with mocked LP parser."""

    def test_calls_progress_callback(self):
        """Progress callback is called during LP analysis."""
        params = _make_parameters(1, category="objective")
        engine = SensitivityEngine(
            mode="lp", parameters=params, kpi_names=["cost"],
            n_base_samples=4,
        )

        n_eval = 4 * (2 * 1 + 2)
        mock_samples = np.random.rand(n_eval, 1)

        mock_model = MagicMock()
        mock_model.get_objective_groups.return_value = {"key_0"}
        mock_model.get_rhs_groups.return_value = set()

        progress_calls = []

        def progress_cb(current, total, msg):
            progress_calls.append((current, total, msg))

        mock_si = {
            "S1": np.array([0.5]),
            "ST": np.array([0.6]),
            "S1_conf": np.array([0.01]),
            "ST_conf": np.array([0.02]),
        }

        mock_sobol_sample = MagicMock()
        mock_sobol_sample.sample.return_value = mock_samples

        mock_sobol = MagicMock()
        mock_sobol.analyze.return_value = mock_si
        mock_salib_analyze = MagicMock()
        mock_salib_analyze.sobol = mock_sobol

        # Mock the lp_parser module functions
        mock_lp_parser = MagicMock()
        mock_lp_parser.parse_lp_file.return_value = mock_model
        mock_lp_parser.perturb_and_solve.return_value = {"cost": 100.0}

        with patch.dict("sys.modules", {
            "SALib.sample.sobol": mock_sobol_sample,
            "SALib.analyze": mock_salib_analyze,
            "SALib.analyze.sobol": mock_sobol,
            "esfex.sensitivity.lp_parser": mock_lp_parser,
        }):
            engine.run_lp_analysis("dummy.lp", progress_callback=progress_cb)

        # Should have initial call + one per evaluation
        assert len(progress_calls) == n_eval + 1
        assert progress_calls[0][0] == 0  # initial
        assert progress_calls[-1][0] == n_eval


# ---------------------------------------------------------------------------
# SensitivityEngine.run_config_analysis (mocked)
# ---------------------------------------------------------------------------


class TestRunConfigAnalysis:
    """Tests for SensitivityEngine.run_config_analysis with mocked deps."""

    def _setup_mocks(self, n_params, n_base_samples, mock_si):
        """Create standard mocks for SALib modules."""
        n_eval = n_base_samples * (2 * n_params + 2)
        mock_samples = np.random.rand(n_eval, n_params)

        mock_sobol_sample = MagicMock()
        mock_sobol_sample.sample.return_value = mock_samples

        mock_sobol = MagicMock()
        mock_sobol.analyze.return_value = mock_si

        mock_salib_analyze = MagicMock()
        mock_salib_analyze.sobol = mock_sobol

        return n_eval, mock_sobol_sample, mock_salib_analyze, mock_sobol

    def test_creates_temp_configs(self, tmp_path):
        """run_config_analysis creates temporary YAML config files."""
        import yaml

        base_config = _make_base_config()
        config_path = tmp_path / "base.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(base_config, f)

        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        engine = SensitivityEngine(
            mode="config", parameters=params, kpi_names=["total_cost"],
            n_base_samples=4,
        )

        mock_si = {
            "S1": np.array([0.5]),
            "ST": np.array([0.6]),
            "S1_conf": np.array([0.01]),
            "ST_conf": np.array([0.02]),
        }
        n_eval, mock_sobol_sample, mock_salib_analyze, mock_sobol = \
            self._setup_mocks(1, 4, mock_si)

        # Return the unmodified base_config to avoid numpy float serialization issues
        with patch.dict("sys.modules", {
                 "SALib.sample.sobol": mock_sobol_sample,
                 "SALib.analyze": mock_salib_analyze,
                 "SALib.analyze.sobol": mock_sobol,
             }), \
             patch("esfex.sensitivity.engine._apply_config_multipliers",
                   return_value=base_config), \
             patch("esfex.sensitivity.engine._run_simulation_and_extract",
                   return_value={"total_cost": 1000.0}):

            result = engine.run_config_analysis(
                str(config_path), str(tmp_path / "output"),
            )

        assert isinstance(result, SobolResult)
        assert result.n_evaluations == n_eval

    def test_config_analysis_calls_simulation(self, tmp_path):
        """Each sample row triggers a simulation call."""
        import yaml

        base_config = _make_base_config()
        config_path = tmp_path / "base.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump(base_config, f)

        params = [SensitivityParameter(name="RE", key="invest_cost_renewables",
                                       category="config")]
        engine = SensitivityEngine(
            mode="config", parameters=params, kpi_names=["total_cost"],
            n_base_samples=4,
        )

        mock_si = {
            "S1": np.array([0.5]),
            "ST": np.array([0.6]),
            "S1_conf": np.array([0.01]),
            "ST_conf": np.array([0.02]),
        }
        n_eval, mock_sobol_sample, mock_salib_analyze, mock_sobol = \
            self._setup_mocks(1, 4, mock_si)

        with patch.dict("sys.modules", {
                 "SALib.sample.sobol": mock_sobol_sample,
                 "SALib.analyze": mock_salib_analyze,
                 "SALib.analyze.sobol": mock_sobol,
             }), \
             patch("esfex.sensitivity.engine._apply_config_multipliers",
                   return_value=base_config), \
             patch("esfex.sensitivity.engine._run_simulation_and_extract",
                   return_value={"total_cost": 1000.0}) as mock_run:

            engine.run_config_analysis(
                str(config_path), str(tmp_path / "output"),
            )

        assert mock_run.call_count == n_eval
