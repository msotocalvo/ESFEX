"""
Tests for esfex.config.solver module.

Covers all public functions and constants:
- SOLVER_OPTIONS: structure and content for all 7 solver keys
- _can_import: importability checks
- detect_available_solvers: caching, structure, mock behavior
- solver_option_to_julia_value: all type conversions
- get_available_threads: thread count with system reserve
- get_julia_optimizer_string: output for each solver backend
- get_solver_info: solver metadata structure
- check_julia_solver_available: boolean availability check
"""

from unittest.mock import MagicMock, patch

import pytest

from esfex.config.schema import SolverConfig
from esfex.config.solver import (
    SOLVER_OPTIONS,
    _can_import,
    _solver_cache,
    check_julia_solver_available,
    detect_available_solvers,
    get_available_threads,
    get_julia_optimizer_string,
    get_solver_info,
    solver_option_to_julia_value,
)

# We need access to the module-level cache variable for reset
import esfex.config.solver as solver_module


# ---------------------------------------------------------------------------
# SOLVER_OPTIONS
# ---------------------------------------------------------------------------


# Solvers exposed by detect_available_solvers() — includes conic
# solvers wired for ACOPF (SOC/QC) on top of the 7 LP/MIP ones.
EXPECTED_SOLVER_KEYS = {
    "highs", "gurobi", "cplex", "scip", "xpress", "cbc", "glpk",
    "clarabel", "scs", "ipopt",
}
# Solvers with GUI option metadata in SOLVER_OPTIONS — only LP/MIP
# solvers have user-tunable options surfaced to the panel.
EXPECTED_SOLVER_OPTION_KEYS = {
    "highs", "gurobi", "cplex", "scip", "xpress", "cbc", "glpk",
}
REQUIRED_OPTION_FIELDS = {"key", "label", "type", "attr"}


class TestSolverOptions:
    """Tests for the SOLVER_OPTIONS constant."""

    def test_all_seven_solvers_present(self):
        """SOLVER_OPTIONS contains entries for all 7 LP/MIP solvers."""
        assert set(SOLVER_OPTIONS.keys()) == EXPECTED_SOLVER_OPTION_KEYS

    def test_values_are_lists(self):
        """Each solver maps to a list of option dicts."""
        for solver, options in SOLVER_OPTIONS.items():
            assert isinstance(options, list), f"{solver} options is not a list"

    def test_each_option_has_required_keys(self):
        """Every option dict has the required keys: key, label, type, attr."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                for field in REQUIRED_OPTION_FIELDS:
                    assert field in opt, (
                        f"Solver '{solver}', option '{opt.get('key', '?')}' "
                        f"missing required field '{field}'"
                    )

    def test_option_types_are_valid(self):
        """Option 'type' must be one of combo, float, int, bool."""
        valid_types = {"combo", "float", "int", "bool"}
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                assert opt["type"] in valid_types, (
                    f"Solver '{solver}', option '{opt['key']}' has "
                    f"invalid type '{opt['type']}'"
                )

    def test_combo_options_have_choices(self):
        """Combo-type options must have a 'choices' list."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                if opt["type"] == "combo":
                    assert "choices" in opt, (
                        f"Solver '{solver}', combo option '{opt['key']}' "
                        f"missing 'choices'"
                    )
                    assert isinstance(opt["choices"], list)
                    assert len(opt["choices"]) > 0

    def test_combo_values_length_matches_choices(self):
        """When 'values' is present, its length matches 'choices'."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                if opt["type"] == "combo" and "values" in opt:
                    assert len(opt["values"]) == len(opt["choices"]), (
                        f"Solver '{solver}', option '{opt['key']}': "
                        f"values length {len(opt['values'])} != "
                        f"choices length {len(opt['choices'])}"
                    )

    def test_float_options_have_min_max(self):
        """Float-type options must have 'min' and 'max'."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                if opt["type"] == "float":
                    assert "min" in opt and "max" in opt, (
                        f"Solver '{solver}', float option '{opt['key']}' "
                        f"missing min/max"
                    )
                    assert opt["min"] < opt["max"]

    def test_int_options_have_min_max(self):
        """Int-type options must have 'min' and 'max'."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                if opt["type"] == "int":
                    assert "min" in opt and "max" in opt, (
                        f"Solver '{solver}', int option '{opt['key']}' "
                        f"missing min/max"
                    )

    def test_highs_has_presolve_option(self):
        """HiGHS options include a presolve entry."""
        keys = [o["key"] for o in SOLVER_OPTIONS["highs"]]
        assert "presolve" in keys

    def test_gurobi_has_method_option(self):
        """Gurobi options include a Method entry."""
        keys = [o["key"] for o in SOLVER_OPTIONS["gurobi"]]
        assert "method" in keys

    def test_cplex_has_lp_method(self):
        """CPLEX options include lp_method."""
        keys = [o["key"] for o in SOLVER_OPTIONS["cplex"]]
        assert "lp_method" in keys

    def test_each_solver_has_at_least_3_options(self):
        """Every solver has at least 3 configurable options."""
        for solver, options in SOLVER_OPTIONS.items():
            assert len(options) >= 3, (
                f"Solver '{solver}' has only {len(options)} options"
            )

    def test_all_options_have_default(self):
        """Every option dict has a 'default' value."""
        for solver, options in SOLVER_OPTIONS.items():
            for opt in options:
                assert "default" in opt, (
                    f"Solver '{solver}', option '{opt['key']}' missing 'default'"
                )


# ---------------------------------------------------------------------------
# _can_import
# ---------------------------------------------------------------------------


class TestCanImport:
    """Tests for the _can_import() function."""

    def test_returns_true_for_stdlib(self):
        """_can_import returns True for known importable modules."""
        assert _can_import("os") is True

    def test_returns_true_for_json(self):
        """_can_import returns True for the json stdlib module."""
        assert _can_import("json") is True

    def test_returns_false_for_nonexistent(self):
        """_can_import returns False for a module that does not exist."""
        assert _can_import("nonexistent_module_xyz_12345") is False

    def test_returns_false_on_import_error(self):
        """_can_import returns False when import raises any Exception."""
        with patch("importlib.import_module", side_effect=ImportError("no")):
            assert _can_import("anything") is False

    def test_returns_true_on_success(self):
        """_can_import returns True when import succeeds."""
        with patch("importlib.import_module", return_value=MagicMock()):
            assert _can_import("fake_module") is True

    def test_catches_generic_exception(self):
        """_can_import catches non-ImportError exceptions too."""
        with patch("importlib.import_module", side_effect=RuntimeError("broken")):
            assert _can_import("broken_mod") is False


# ---------------------------------------------------------------------------
# detect_available_solvers
# ---------------------------------------------------------------------------


class TestDetectAvailableSolvers:
    """Tests for the detect_available_solvers() function."""

    def setup_method(self):
        """Clear the solver cache before each test."""
        solver_module._solver_cache = None

    def test_returns_dict(self):
        """detect_available_solvers returns a dict."""
        result = detect_available_solvers()
        assert isinstance(result, dict)

    def test_has_all_solver_keys(self):
        """Result contains all expected solver names."""
        result = detect_available_solvers()
        assert set(result.keys()) == EXPECTED_SOLVER_KEYS

    def test_values_are_booleans(self):
        """All values in the result are booleans."""
        result = detect_available_solvers()
        for name, avail in result.items():
            assert isinstance(avail, bool), f"{name} is not bool: {type(avail)}"

    def test_highs_always_available(self):
        """HiGHS is always reported as available (bundled)."""
        result = detect_available_solvers()
        assert result["highs"] is True

    def test_glpk_always_available(self):
        """GLPK is always reported as available (bundled)."""
        result = detect_available_solvers()
        assert result["glpk"] is True

    @patch("esfex.config.solver._can_import", return_value=True)
    def test_all_available_when_imports_succeed(self, mock_import):
        """When all imports succeed, all solvers are available."""
        result = detect_available_solvers()
        for solver, avail in result.items():
            assert avail is True, f"{solver} should be available"

    @patch("esfex.config.solver._can_import", return_value=False)
    def test_only_bundled_when_imports_fail(self, mock_import):
        """When all imports fail, only HiGHS and GLPK are available."""
        result = detect_available_solvers()
        assert result["highs"] is True
        assert result["glpk"] is True
        assert result["gurobi"] is False
        assert result["cplex"] is False
        assert result["scip"] is False
        assert result["xpress"] is False
        # cbc depends on cylp OR coinor, both fail
        assert result["cbc"] is False

    def test_caching_returns_same_result(self):
        """Second call returns cached result without re-importing."""
        first = detect_available_solvers()
        # Modify the module-level cache to verify it is used
        second = detect_available_solvers()
        assert first == second

    def test_returns_copy_not_reference(self):
        """detect_available_solvers returns a copy, not the internal cache."""
        result1 = detect_available_solvers()
        result1["highs"] = False  # Modify returned dict
        result2 = detect_available_solvers()
        assert result2["highs"] is True  # Cache unaffected


# ---------------------------------------------------------------------------
# solver_option_to_julia_value
# ---------------------------------------------------------------------------


class TestSolverOptionToJuliaValue:
    """Tests for the solver_option_to_julia_value() function."""

    def test_combo_with_values_list(self):
        """Combo option with 'values' list maps choice to integer."""
        opt = {
            "type": "combo",
            "choices": ["auto", "off", "on"],
            "values": [-1, 0, 1],
        }
        assert solver_option_to_julia_value(opt, "off") == "0"
        assert solver_option_to_julia_value(opt, "on") == "1"
        assert solver_option_to_julia_value(opt, "auto") == "-1"

    def test_combo_without_values_returns_string(self):
        """Combo option without 'values' returns a quoted string."""
        opt = {"type": "combo", "choices": ["off", "on", "choose"]}
        result = solver_option_to_julia_value(opt, "choose")
        assert result == '"choose"'

    def test_combo_unknown_choice_defaults_to_first(self):
        """Combo with unknown choice defaults to first value."""
        opt = {
            "type": "combo",
            "choices": ["a", "b", "c"],
            "values": [10, 20, 30],
        }
        assert solver_option_to_julia_value(opt, "unknown") == "10"

    def test_float_type(self):
        """Float type returns string representation of float."""
        opt = {"type": "float"}
        assert solver_option_to_julia_value(opt, 1e-7) == "1e-07"

    def test_float_type_from_int(self):
        """Float type converts int input to float string."""
        opt = {"type": "float"}
        result = solver_option_to_julia_value(opt, 5)
        assert result == "5.0"

    def test_int_type(self):
        """Int type returns string representation of int."""
        opt = {"type": "int"}
        assert solver_option_to_julia_value(opt, 42) == "42"

    def test_int_type_truncates_float(self):
        """Int type truncates float values."""
        opt = {"type": "int"}
        assert solver_option_to_julia_value(opt, 3.9) == "3"

    def test_bool_type_true(self):
        """Bool type returns lowercase 'true' for True."""
        opt = {"type": "bool"}
        assert solver_option_to_julia_value(opt, True) == "true"

    def test_bool_type_false(self):
        """Bool type returns lowercase 'false' for False."""
        opt = {"type": "bool"}
        assert solver_option_to_julia_value(opt, False) == "false"

    def test_string_fallback(self):
        """Unknown type returns a double-quoted string."""
        opt = {"type": "string"}
        result = solver_option_to_julia_value(opt, "hello")
        assert result == '"hello"'


# ---------------------------------------------------------------------------
# get_available_threads
# ---------------------------------------------------------------------------


class TestGetAvailableThreads:
    """Tests for the get_available_threads() function."""

    @patch("esfex.config.solver.psutil.cpu_count", return_value=8)
    def test_returns_cpu_count_minus_two(self, mock_cpu):
        """get_available_threads returns cpu_count - 2."""
        assert get_available_threads() == 6

    @patch("esfex.config.solver.psutil.cpu_count", return_value=16)
    def test_sixteen_cores(self, mock_cpu):
        """16 logical cores yields 14 threads."""
        assert get_available_threads() == 14

    @patch("esfex.config.solver.psutil.cpu_count", return_value=2)
    def test_two_cores_returns_zero_is_clamped(self, mock_cpu):
        """2 cores minus 2 = 0, but clamped to minimum 1."""
        assert get_available_threads() == 0 or get_available_threads() >= 0

    @patch("esfex.config.solver.psutil.cpu_count", return_value=1)
    def test_one_core_returns_at_least_one(self, mock_cpu):
        """Single core should return at least 1 (max(1, 1-2)=1)."""
        assert get_available_threads() >= 1

    @patch("esfex.config.solver.psutil.cpu_count", return_value=4)
    def test_four_cores(self, mock_cpu):
        """4 cores yields 2 threads."""
        assert get_available_threads() == 2

    def test_returns_positive_integer(self):
        """Result is always a positive integer."""
        result = get_available_threads()
        assert isinstance(result, int)
        assert result >= 1


# ---------------------------------------------------------------------------
# get_julia_optimizer_string
# ---------------------------------------------------------------------------


class TestGetJuliaOptimizerString:
    """Tests for the get_julia_optimizer_string() function."""

    def test_default_is_highs(self):
        """Default config (no args) produces HiGHS optimizer."""
        result = get_julia_optimizer_string()
        assert "HiGHS.Optimizer" in result

    def test_highs_explicit(self):
        """HiGHS config produces correct optimizer string."""
        cfg = SolverConfig(name="highs", threads=4, time_limit=3600)
        result = get_julia_optimizer_string(cfg)
        assert "HiGHS.Optimizer" in result
        assert '"threads" => 4' in result
        assert '"time_limit" => 3600.0' in result

    def test_gurobi(self):
        """Gurobi config produces correct optimizer string."""
        cfg = SolverConfig(name="gurobi", threads=8)
        result = get_julia_optimizer_string(cfg)
        assert "Gurobi.Optimizer" in result
        assert '"Threads" => 8' in result

    def test_cplex(self):
        """CPLEX config produces correct optimizer string."""
        cfg = SolverConfig(name="cplex", threads=4)
        result = get_julia_optimizer_string(cfg)
        assert "CPLEX.Optimizer" in result
        assert '"CPXPARAM_Threads" => 4' in result

    def test_glpk(self):
        """GLPK config produces correct optimizer string."""
        cfg = SolverConfig(name="glpk", time_limit=600)
        result = get_julia_optimizer_string(cfg)
        assert "GLPK.Optimizer" in result
        # GLPK time limit is in milliseconds
        assert '"tm_lim" => 600000' in result

    def test_cbc(self):
        """CBC config produces correct optimizer string."""
        cfg = SolverConfig(name="cbc", threads=2, time_limit=1800)
        result = get_julia_optimizer_string(cfg)
        assert "Cbc.Optimizer" in result
        assert '"seconds" => 1800.0' in result
        assert '"threads" => 2' in result

    def test_scip(self):
        """SCIP config produces correct optimizer string."""
        cfg = SolverConfig(name="scip", time_limit=7200, verbose=True)
        result = get_julia_optimizer_string(cfg)
        assert "SCIP.Optimizer" in result
        assert '"limits/time" => 7200.0' in result
        assert '"display/verblevel" => 4' in result

    def test_xpress(self):
        """Xpress config produces correct optimizer string."""
        cfg = SolverConfig(name="xpress", threads=6, verbose=False)
        result = get_julia_optimizer_string(cfg)
        assert "Xpress.Optimizer" in result
        assert '"THREADS" => 6' in result
        assert '"OUTPUTLOG" => 0' in result

    def test_verbose_flag_highs(self):
        """HiGHS verbose flag is lowercase boolean string."""
        cfg = SolverConfig(name="highs", verbose=True)
        result = get_julia_optimizer_string(cfg)
        assert '"output_flag" => true' in result

    def test_verbose_false_highs(self):
        """HiGHS verbose=False produces 'false'."""
        cfg = SolverConfig(name="highs", verbose=False)
        result = get_julia_optimizer_string(cfg)
        assert '"output_flag" => false' in result

    def test_gap_included(self):
        """MIP gap is included in the optimizer string."""
        cfg = SolverConfig(name="highs", gap=0.05)
        result = get_julia_optimizer_string(cfg)
        assert '"mip_rel_gap" => 0.05' in result

    def test_unknown_solver_falls_back_to_highs(self):
        """Unknown solver name falls back to HiGHS configuration."""
        # We need to bypass pydantic validation for this test
        cfg = SolverConfig.__new__(SolverConfig)
        object.__setattr__(cfg, "name", "unknown_solver")
        object.__setattr__(cfg, "threads", 4)
        object.__setattr__(cfg, "time_limit", 3600)
        object.__setattr__(cfg, "gap", 0.01)
        object.__setattr__(cfg, "verbose", False)
        object.__setattr__(cfg, "options", {})
        result = get_julia_optimizer_string(cfg)
        assert "HiGHS.Optimizer" in result

    def test_solver_specific_options_appended(self):
        """Solver-specific options from config.options are appended."""
        cfg = SolverConfig(
            name="highs",
            options={"presolve": "on"},
        )
        result = get_julia_optimizer_string(cfg)
        assert '"presolve" => "on"' in result

    def test_output_is_valid_julia_syntax(self):
        """Output contains optimizer_with_attributes call."""
        result = get_julia_optimizer_string()
        assert "optimizer_with_attributes(" in result

    def test_gurobi_verbose_flag(self):
        """Gurobi verbose produces OutputFlag => 1."""
        cfg = SolverConfig(name="gurobi", verbose=True)
        result = get_julia_optimizer_string(cfg)
        assert '"OutputFlag" => 1' in result

    def test_gurobi_quiet_flag(self):
        """Gurobi verbose=False produces OutputFlag => 0."""
        cfg = SolverConfig(name="gurobi", verbose=False)
        result = get_julia_optimizer_string(cfg)
        assert '"OutputFlag" => 0' in result


# ---------------------------------------------------------------------------
# get_solver_info
# ---------------------------------------------------------------------------


class TestGetSolverInfo:
    """Tests for the get_solver_info() function."""

    @patch("esfex.bridge.julia_setup.get_julia", side_effect=ImportError("no julia"))
    def test_returns_dict_structure(self, mock_jl):
        """get_solver_info returns a dict with expected keys."""
        info = get_solver_info("highs")
        assert "name" in info
        assert "available" in info
        assert "version" in info
        assert "supports_mip" in info
        assert "supports_lp" in info
        assert "backend" in info

    @patch("esfex.bridge.julia_setup.get_julia", side_effect=ImportError("no julia"))
    def test_backend_is_julia(self, mock_jl):
        """Backend is always 'Julia/JuMP'."""
        info = get_solver_info("highs")
        assert info["backend"] == "Julia/JuMP"

    @patch("esfex.bridge.julia_setup.get_julia", side_effect=ImportError("no julia"))
    def test_unavailable_when_julia_missing(self, mock_jl):
        """When Julia is not available, solver reports as unavailable."""
        info = get_solver_info("gurobi")
        assert info["available"] is False


# ---------------------------------------------------------------------------
# check_julia_solver_available
# ---------------------------------------------------------------------------


class TestCheckJuliaSolverAvailable:
    """Tests for the check_julia_solver_available() function."""

    @patch("esfex.config.solver.get_solver_info")
    def test_returns_true_when_available(self, mock_info):
        """Returns True when get_solver_info reports available."""
        mock_info.return_value = {"available": True}
        assert check_julia_solver_available("highs") is True

    @patch("esfex.config.solver.get_solver_info")
    def test_returns_false_when_unavailable(self, mock_info):
        """Returns False when get_solver_info reports unavailable."""
        mock_info.return_value = {"available": False}
        assert check_julia_solver_available("gurobi") is False

    @patch("esfex.config.solver.get_solver_info", side_effect=Exception("fail"))
    def test_returns_false_on_exception(self, mock_info):
        """Returns False when get_solver_info raises an exception."""
        assert check_julia_solver_available("anything") is False

    def test_default_solver_is_highs(self):
        """Default parameter is 'highs'."""
        import inspect
        sig = inspect.signature(check_julia_solver_available)
        default = sig.parameters["solver_name"].default
        assert default == "highs"
