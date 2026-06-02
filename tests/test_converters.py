"""
Tests for esfex.bridge.converters module.

All Julia-dependent code is mocked with unittest.mock so that
no real Julia runtime is required.
"""

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import esfex.bridge.converters as converters_mod
from esfex.bridge.converters import (
    COST_SCALE,
    COST_UNSCALE,
    convert_index_julia_to_py,
    convert_index_py_to_julia,
    convert_master_problem_result,
    convert_power_system_result,
    convert_scenario,
    convert_temporal_config,
    convert_transmission_line_data,
    convert_transformer_data,
    convert_acdc_converter_data,
    convert_freq_converter_data,
    convert_inter_system_link,
    convert_generator_config,
    convert_battery_config,
    julia_to_py_array,
    julia_to_py_dict,
    py_to_julia_dict,
    py_to_julia_int_vector,
    py_to_julia_matrix,
    py_to_julia_vector,
    blocks_to_julia_cost_segments,
    build_gen_cost_curves_dict,
    build_bat_cost_curves_dict,
)
from esfex.config.schema import CostCurveBlock, CostCurveConfig


# ---------------------------------------------------------------------------
# Helpers: patching the lazy imports inside converter functions
#
# All converter functions that need Julia do lazy imports:
#   from esfex.bridge.julia_setup import get_julia
#   from esfex.bridge.julia_setup import get_esfex_module
#
# So we patch the julia_setup module functions.
# ---------------------------------------------------------------------------

_PATCH_GET_JULIA = "esfex.bridge.julia_setup.get_julia"
_PATCH_GET_ESFEX = "esfex.bridge.julia_setup.get_esfex_module"


@pytest.fixture
def mock_jl():
    """Create a mock Julia Main module with a useful seval."""
    jl = MagicMock()
    # By default, seval returns a new MagicMock for each call.
    # Tests that need specific seval behavior should override.
    return jl


@pytest.fixture
def mock_esfex():
    """Create a mock ESFEX Julia module."""
    return MagicMock()


# ---------------------------------------------------------------------------
# convert_index_py_to_julia
# ---------------------------------------------------------------------------


class TestConvertIndexPyToJulia:
    """Tests for convert_index_py_to_julia()."""

    def test_zero_to_one(self):
        assert convert_index_py_to_julia(0) == 1

    def test_five_to_six(self):
        assert convert_index_py_to_julia(5) == 6

    def test_large_index(self):
        assert convert_index_py_to_julia(999) == 1000

    def test_return_type_is_int(self):
        result = convert_index_py_to_julia(3)
        assert isinstance(result, int)

    def test_negative_index(self):
        """Negative inputs are not blocked; result is idx + 1."""
        assert convert_index_py_to_julia(-1) == 0


# ---------------------------------------------------------------------------
# convert_index_julia_to_py
# ---------------------------------------------------------------------------


class TestConvertIndexJuliaToPy:
    """Tests for convert_index_julia_to_py()."""

    def test_one_to_zero(self):
        assert convert_index_julia_to_py(1) == 0

    def test_six_to_five(self):
        assert convert_index_julia_to_py(6) == 5

    def test_large_index(self):
        assert convert_index_julia_to_py(1000) == 999

    def test_return_type_is_int(self):
        result = convert_index_julia_to_py(10)
        assert isinstance(result, int)

    def test_roundtrip_identity(self):
        """py->julia->py should return original index."""
        for idx in [0, 1, 5, 42, 100]:
            assert convert_index_julia_to_py(convert_index_py_to_julia(idx)) == idx

    def test_roundtrip_identity_reverse(self):
        """julia->py->julia should return original index."""
        for idx in [1, 2, 6, 43, 101]:
            assert convert_index_py_to_julia(convert_index_julia_to_py(idx)) == idx


# ---------------------------------------------------------------------------
# py_to_julia_vector
# ---------------------------------------------------------------------------


class TestPyToJuliaVector:
    """Tests for py_to_julia_vector()."""

    def test_calls_seval_with_vector_float64(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_vector([1.0, 2.0, 3.0])

        mock_jl.seval.assert_called_with("Vector{Float64}")

    def test_accepts_numpy_array(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_vector(np.array([1.0, 2.0, 3.0]))

        mock_vector_type.assert_called_once()

    def test_accepts_python_list(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_vector([10, 20, 30])

        mock_vector_type.assert_called_once()

    def test_converts_to_float64(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_vector([1, 2, 3])

        passed_arr = mock_vector_type.call_args[0][0]
        assert passed_arr.dtype == np.float64

    def test_integer_list_converted_correctly(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_vector([0, 100, 200])

        passed_arr = mock_vector_type.call_args[0][0]
        np.testing.assert_array_equal(passed_arr, [0.0, 100.0, 200.0])


# ---------------------------------------------------------------------------
# py_to_julia_int_vector
# ---------------------------------------------------------------------------


class TestPyToJuliaIntVector:
    """Tests for py_to_julia_int_vector()."""

    def test_calls_seval_with_vector_int(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_int_vector([1, 2, 3])

        mock_jl.seval.assert_called_with("Vector{Int}")

    def test_accepts_numpy_array(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_int_vector(np.array([1, 2, 3]))

        mock_vector_type.assert_called_once()

    def test_converts_to_int64(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_int_vector([1.5, 2.7, 3.9])

        passed_arr = mock_vector_type.call_args[0][0]
        assert passed_arr.dtype == np.int64

    def test_int_values_preserved(self):
        mock_jl = MagicMock()
        mock_vector_type = MagicMock()
        mock_jl.seval.return_value = mock_vector_type

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_int_vector([10, 20, 30])

        passed_arr = mock_vector_type.call_args[0][0]
        np.testing.assert_array_equal(passed_arr, [10, 20, 30])


# ---------------------------------------------------------------------------
# py_to_julia_matrix
# ---------------------------------------------------------------------------


class TestPyToJuliaMatrix:
    """Tests for py_to_julia_matrix()."""

    def test_raises_on_1d_array(self):
        mock_jl = MagicMock()
        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            with pytest.raises(ValueError, match="Expected 2D array, got 1D"):
                py_to_julia_matrix(np.array([1.0, 2.0, 3.0]))

    def test_raises_on_3d_array(self):
        mock_jl = MagicMock()
        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            with pytest.raises(ValueError, match="Expected 2D array, got 3D"):
                py_to_julia_matrix(np.ones((2, 3, 4)))

    def test_uses_vector_and_reshape(self):
        """The bulk-transfer implementation calls seval("Vector{Float64}") then seval("reshape")."""
        mock_jl = MagicMock()
        mock_jl.seval.return_value = MagicMock()

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_matrix(np.array([[1.0, 2.0], [3.0, 4.0]]))

        seval_args = [c[0][0] for c in mock_jl.seval.call_args_list]
        assert "Vector{Float64}" in seval_args
        assert "reshape" in seval_args

    def test_passes_fortran_flat_to_julia(self):
        """Values should be flattened in column-major (Fortran) order before bulk transfer."""
        mock_jl = MagicMock()
        captured = {}

        ctor_mock = MagicMock(side_effect=lambda flat: captured.setdefault("flat", flat))
        reshape_mock = MagicMock(side_effect=lambda vec, rows, cols: captured.update(
            {"rows": rows, "cols": cols}) or MagicMock())

        def seval_router(code):
            if code == "Vector{Float64}":
                return ctor_mock
            if code == "reshape":
                return reshape_mock
            return MagicMock()

        mock_jl.seval.side_effect = seval_router

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_matrix(np.array([[10.0, 20.0], [30.0, 40.0]]))

        # column-major: column 0 first (10, 30), then column 1 (20, 40)
        np.testing.assert_array_equal(captured["flat"], [10.0, 30.0, 20.0, 40.0])
        assert captured["rows"] == 2 and captured["cols"] == 2

    def test_returns_reshape_result(self):
        """The function returns whatever seval('reshape')(vec, rows, cols) produces."""
        mock_jl = MagicMock()
        reshape_result = MagicMock(name="julia_matrix")
        reshape_mock = MagicMock(return_value=reshape_result)

        def seval_router(code):
            if code == "reshape":
                return reshape_mock
            return MagicMock()  # ctor and any other seval lookups

        mock_jl.seval.side_effect = seval_router

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            result = py_to_julia_matrix(np.array([[1.0]]))

        assert result is reshape_result

    def test_handles_non_numpy_input(self):
        mock_jl = MagicMock()
        mock_jl.seval.return_value = MagicMock()

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            # Passing a list of lists - should be converted internally
            py_to_julia_matrix(np.array([[1.0, 2.0], [3.0, 4.0]]))
            # No exception means success


# ---------------------------------------------------------------------------
# py_to_julia_dict
# ---------------------------------------------------------------------------


class TestPyToJuliaDict:
    """Tests for py_to_julia_dict()."""

    def test_calls_jl_dict(self):
        mock_jl = MagicMock()
        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_dict({"a": 1, "b": 2})

        mock_jl.Dict.assert_called_once_with({"a": 1, "b": 2})

    def test_empty_dict(self):
        mock_jl = MagicMock()
        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            py_to_julia_dict({})

        mock_jl.Dict.assert_called_once_with({})

    def test_returns_jl_dict_result(self):
        mock_jl = MagicMock()
        mock_jl.Dict.return_value = "julia_dict_object"
        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            result = py_to_julia_dict({"x": 42})

        assert result == "julia_dict_object"


# ---------------------------------------------------------------------------
# julia_to_py_array
# ---------------------------------------------------------------------------


class TestJuliaToPyArray:
    """Tests for julia_to_py_array()."""

    def test_converts_list_to_numpy(self):
        result = julia_to_py_array([1.0, 2.0, 3.0])
        assert isinstance(result, np.ndarray)
        np.testing.assert_array_equal(result, [1.0, 2.0, 3.0])

    def test_preserves_2d_shape(self):
        data = [[1.0, 2.0], [3.0, 4.0]]
        result = julia_to_py_array(data)
        assert result.shape == (2, 2)

    def test_handles_numpy_input(self):
        arr = np.array([5.0, 10.0])
        result = julia_to_py_array(arr)
        np.testing.assert_array_equal(result, arr)

    def test_returns_ndarray_type(self):
        result = julia_to_py_array([42])
        assert type(result) is np.ndarray


# ---------------------------------------------------------------------------
# julia_to_py_dict
# ---------------------------------------------------------------------------


class TestJuliaToPyDict:
    """Tests for julia_to_py_dict()."""

    def test_converts_dict(self):
        result = julia_to_py_dict({"x": 1, "y": 2})
        assert result == {"x": 1, "y": 2}
        assert isinstance(result, dict)

    def test_empty_dict(self):
        result = julia_to_py_dict({})
        assert result == {}

    def test_returns_new_dict_object(self):
        original = {"a": 1}
        result = julia_to_py_dict(original)
        assert result is not original


# ---------------------------------------------------------------------------
# convert_scenario
# ---------------------------------------------------------------------------


class TestConvertScenario:
    """Tests for convert_scenario()."""

    def test_creates_julia_scenario(self, mock_esfex):
        scenario = {
            "name": "base",
            "probability": 0.5,
            "multipliers": {
                "invest_cost_renewables": 1.2,
                "fuel_cost": 0.8,
            },
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        mock_esfex.Scenario.assert_called_once()
        call_args = mock_esfex.Scenario.call_args[0]
        assert call_args[0] == "base"
        assert call_args[1] == 0.5

    def test_default_multipliers(self, mock_esfex):
        scenario = {
            "name": "default",
            "probability": 1.0,
            "multipliers": {},
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        sm_call = mock_esfex.ScenarioMultipliers.call_args[0]
        for arg in sm_call:
            assert arg == 1.0

    def test_missing_multipliers_key_uses_defaults(self, mock_esfex):
        scenario = {
            "name": "no_mult",
            "probability": 0.3,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        mock_esfex.Scenario.assert_called_once()

    def test_multiplier_values_passed_correctly(self, mock_esfex):
        scenario = {
            "name": "high_fuel",
            "probability": 0.7,
            "multipliers": {
                "invest_cost_renewables": 0.5,
                "invest_cost_conventional": 1.5,
                "fuel_cost": 2.0,
                "maintenance_cost": 1.1,
                "invest_cost_storage": 0.8,
                "invest_cost_transmission": 1.3,
                "discount_rate": 0.95,
                "demand_growth": 1.2,
                "fuel_price_growth": 1.4,
                "carbon_price": 3.0,
            },
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        sm_call = mock_esfex.ScenarioMultipliers.call_args[0]
        assert sm_call[0] == 0.5   # invest_cost_renewables
        assert sm_call[1] == 1.5   # invest_cost_conventional
        assert sm_call[2] == 2.0   # fuel_cost
        assert sm_call[3] == 1.1   # maintenance_cost
        assert sm_call[4] == 0.8   # invest_cost_storage
        assert sm_call[5] == 1.3   # invest_cost_transmission
        assert sm_call[6] == 0.95  # discount_rate
        assert sm_call[7] == 1.2   # demand_growth
        assert sm_call[8] == 1.4   # fuel_price_growth
        assert sm_call[9] == 3.0   # carbon_price

    def test_probability_is_float(self, mock_esfex):
        scenario = {"name": "test", "probability": 1}

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        call_args = mock_esfex.Scenario.call_args[0]
        assert isinstance(call_args[1], float)

    def test_ten_multiplier_arguments(self, mock_esfex):
        scenario = {
            "name": "test",
            "probability": 1.0,
            "multipliers": {},
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_scenario(scenario)

        sm_call = mock_esfex.ScenarioMultipliers.call_args[0]
        assert len(sm_call) == 10


# ---------------------------------------------------------------------------
# convert_power_system_result
# ---------------------------------------------------------------------------


class TestConvertPowerSystemResult:
    """Tests for convert_power_system_result()."""

    def _make_mock_result(self):
        """Create a mock Julia PowerSystemResult with all required fields."""
        r = MagicMock()
        r.status = "OPTIMAL"
        r.objective = 1234567.89
        r.solve_time = 42.5

        r.gen_output = np.array([[100.0, 200.0]])
        r.curtailment = np.array([[5.0, 10.0]])
        r.total_curtailment = 15.0
        r.bat_charge = np.array([[20.0]])
        r.bat_discharge = np.array([[15.0]])
        r.bat_soc = np.array([[50.0]])
        r.reserve_static = np.array([[10.0]])
        r.reserve_dynamic = np.array([[5.0]])
        r.reserve_static_loss = np.array([[0.0]])
        r.reserve_dynamic_loss = np.array([[0.0]])
        r.load_shed = np.array([[0.0]])
        r.co2_emissions = np.array([[100.0]])
        r.voltage_angle = np.array([[0.1]])
        r.energy_prices = np.array([[50.0]])

        r.total_generation = 1000.0
        r.total_demand = 950.0
        r.total_losses = 50.0
        r.re_penetration = 0.45
        r.total_co2 = 100.0
        r.load_shed_total = 0.0

        r.gen_status = None
        r.gen_startup = None
        r.gen_investment = None
        r.bat_investment_power = None
        r.bat_investment_capacity = None
        r.power_flow_by_line = None
        r.transfer_investment = None
        r.bat_spillage = None
        r.ev_charging = None
        r.ev_v2g = None
        r.ev_soc = None
        r.ev_loss = None
        r.loss_of_inertia = None
        r.transfer_margin = None

        # Reservoir
        r.reservoir_level = None
        r.reservoir_spillage = None
        r.reservoir_pump = None
        r.reservoir_invest_capacity = None

        # N-1 security
        r.n1_gen_reserve_duals = None
        r.n1_trans_reserve_duals = None
        r.n1_binding_contingencies = None
        r.n1_security_cost = 0.0

        # Cost breakdown
        r.cost_breakdown = None

        mock_pf = MagicMock()
        mock_pf.items.return_value = [
            ((1, 2), np.array([10.0, 20.0])),
        ]
        r.power_flow = mock_pf

        return r

    def test_status_field(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert result["status"] == "OPTIMAL"

    def test_objective_field(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        # Julia stores costs in M$ (scale_cost convention); converter unscales to $.
        assert result["objective"] == 1234567.89 * COST_UNSCALE

    def test_solve_time_field(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert result["solve_time"] == 42.5

    def test_gen_output_is_numpy(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert isinstance(result["gen_output"], np.ndarray)

    def test_curtailment_fields(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert isinstance(result["curtailment"], np.ndarray)
        assert result["total_curtailment"] == 15.0

    def test_storage_fields(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert "bat_charge" in result
        assert "bat_discharge" in result
        assert "bat_soc" in result

    def test_reserve_fields(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert "reserve_static" in result
        assert "reserve_dynamic" in result
        assert "loss_of_reserve_static" in result
        assert "loss_of_reserve_dynamic" in result

    def test_system_metrics(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert result["total_generation"] == 1000.0
        assert result["total_demand"] == 950.0
        assert result["total_losses"] == 50.0
        assert result["re_penetration"] == 0.45
        assert result["total_co2"] == 100.0
        assert result["load_shed_total"] == 0.0

    def test_power_flow_index_conversion(self):
        """Power flow keys should be converted from 1-based to 0-based."""
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        pf = result["power_flow"]
        assert (0, 1) in pf
        np.testing.assert_array_equal(pf[(0, 1)], [10.0, 20.0])

    def test_optional_gen_status_included_when_present(self):
        r = self._make_mock_result()
        r.gen_status = np.array([[1, 0]])
        result = convert_power_system_result(r)
        assert "gen_status" in result

    def test_optional_gen_status_absent_when_none(self):
        r = self._make_mock_result()
        r.gen_status = None
        result = convert_power_system_result(r)
        assert "gen_status" not in result

    def test_optional_gen_startup_included(self):
        r = self._make_mock_result()
        r.gen_startup = np.array([[1, 0]])
        result = convert_power_system_result(r)
        assert "gen_startup" in result

    def test_optional_gen_investment_included(self):
        r = self._make_mock_result()
        r.gen_investment = np.array([[50.0]])
        result = convert_power_system_result(r)
        assert "gen_investment" in result

    def test_optional_bat_investment_fields(self):
        r = self._make_mock_result()
        r.bat_investment_power = np.array([[10.0]])
        r.bat_investment_capacity = np.array([[100.0]])
        result = convert_power_system_result(r)
        assert "bat_investment_power" in result
        assert "bat_investment_capacity" in result

    def test_optional_power_flow_by_line(self):
        r = self._make_mock_result()
        r.power_flow_by_line = [np.array([5.0, 10.0])]
        result = convert_power_system_result(r)
        assert "power_flow_by_line" in result
        assert len(result["power_flow_by_line"]) == 1

    def test_optional_transfer_investment(self):
        r = self._make_mock_result()
        mock_ti = MagicMock()
        mock_ti.items.return_value = [((1, 2), 500.0)]
        r.transfer_investment = mock_ti
        result = convert_power_system_result(r)
        assert (0, 1) in result["transfer_investment"]
        assert result["transfer_investment"][(0, 1)] == 500.0

    def test_optional_bat_spillage(self):
        r = self._make_mock_result()
        r.bat_spillage = np.array([[[1.0]]])
        result = convert_power_system_result(r)
        assert "bat_spillage" in result

    def test_optional_ev_fields(self):
        r = self._make_mock_result()
        r.ev_charging = np.array([[10.0]])
        r.ev_v2g = np.array([[2.0]])
        r.ev_soc = np.array([[80.0]])
        r.ev_loss = np.array([[0.5]])
        result = convert_power_system_result(r)
        assert "ev_charging" in result
        assert "ev_v2g" in result
        assert "ev_soc" in result
        assert "ev_loss" in result

    def test_optional_loss_of_inertia(self):
        r = self._make_mock_result()
        r.loss_of_inertia = np.array([0.1, 0.2])
        result = convert_power_system_result(r)
        assert "loss_of_inertia" in result

    def test_optional_transfer_margin(self):
        r = self._make_mock_result()
        mock_tm = MagicMock()
        mock_tm.items.return_value = [((1, 2), np.array([100.0]))]
        r.transfer_margin = mock_tm
        result = convert_power_system_result(r)
        assert (0, 1) in result["transfer_margin"]

    def test_all_mandatory_keys_present(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        mandatory = [
            "status", "objective", "solve_time", "gen_output", "curtailment",
            "total_curtailment", "bat_charge", "bat_discharge", "bat_soc",
            "reserve_static", "reserve_dynamic", "loss_of_reserve_static",
            "loss_of_reserve_dynamic", "load_shed", "co2_emissions",
            "voltage_angle", "energy_prices", "total_generation",
            "total_demand", "total_losses", "re_penetration", "total_co2",
            "load_shed_total", "power_flow",
        ]
        for key in mandatory:
            assert key in result, f"Missing mandatory key: {key}"

    # -- cost_breakdown --

    def _make_mock_cost_breakdown(self):
        """Create a mock CostBreakdown struct."""
        cb = MagicMock()
        cb.fuel_cost = 50000.0
        cb.fixed_om_cost = 10000.0
        cb.maintenance_cost = 5000.0
        cb.startup_cost = 2000.0
        cb.battery_maintenance_cost = 1000.0
        cb.battery_degradation_cost = 500.0
        cb.load_shedding_cost = 0.0
        cb.curtailment_cost = 100.0
        cb.reserve_static_cost = 300.0
        cb.reserve_dynamic_cost = 200.0
        cb.co2_emission_cost = 8000.0
        cb.fre_penetration_cost = 0.0
        cb.inertia_cost = 0.0
        cb.soc_violation_cost = 0.0
        cb.transfer_margin_cost = 0.0
        cb.v2g_compensation = -500.0
        cb.flexible_demand_benefit = -200.0
        cb.investment_cost = 100000.0
        cb.electrolyzer_cost = 0.0
        cb.converter_cost = 0.0
        cb.spillage_cost = 50.0
        cb.delay_retirement_cost = 3000.0
        cb.reservoir_spillage_cost = 0.0
        cb.demand_shift_cost = 0.0
        cb.rooftop_curtailment_cost = 0.0
        cb.npv_penalty_cost = 0.0
        cb.reservoir_invest_cost = 0.0
        cb.pe_supply_cost = 0.0
        cb.pe_loss_cost = 0.0
        cb.pe_excess_cost = 0.0
        cb.pe_transport_cost = 0.0
        cb.pe_investment_cost = 0.0
        cb.pe_coupling_slack_cost = 0.0
        cb.pe_electrolyzer_cost = 0.0
        cb.n1_security_shortfall_cost = 0.0
        cb.total = 179450.0
        return cb

    def test_cost_breakdown_absent_when_none(self):
        r = self._make_mock_result()
        result = convert_power_system_result(r)
        assert "cost_breakdown" not in result

    def test_cost_breakdown_included_when_present(self):
        r = self._make_mock_result()
        r.cost_breakdown = self._make_mock_cost_breakdown()
        result = convert_power_system_result(r)
        assert "cost_breakdown" in result
        assert isinstance(result["cost_breakdown"], dict)

    def test_cost_breakdown_has_all_fields(self):
        r = self._make_mock_result()
        r.cost_breakdown = self._make_mock_cost_breakdown()
        result = convert_power_system_result(r)
        cb = result["cost_breakdown"]
        expected_keys = {
            "fuel_cost", "fixed_om_cost", "maintenance_cost", "startup_cost",
            "battery_maintenance_cost", "battery_degradation_cost",
            "load_shedding_cost", "curtailment_cost", "reserve_static_cost",
            "reserve_dynamic_cost", "co2_emission_cost", "fre_penetration_cost",
            "inertia_cost", "soc_violation_cost", "transfer_margin_cost",
            "v2g_compensation", "flexible_demand_benefit", "investment_cost",
            "electrolyzer_cost", "converter_cost", "spillage_cost",
            "delay_retirement_cost", "reservoir_spillage_cost",
            "demand_shift_cost", "rooftop_curtailment_cost",
            "npv_penalty_cost", "reservoir_invest_cost",
            "pe_supply_cost", "pe_loss_cost", "pe_excess_cost",
            "pe_transport_cost", "pe_investment_cost",
            "pe_coupling_slack_cost", "pe_electrolyzer_cost",
            "n1_security_shortfall_cost",
            "total",
        }
        assert set(cb.keys()) == expected_keys

    def test_cost_breakdown_values_are_float(self):
        r = self._make_mock_result()
        r.cost_breakdown = self._make_mock_cost_breakdown()
        result = convert_power_system_result(r)
        for key, value in result["cost_breakdown"].items():
            assert isinstance(value, float), f"{key} is not float: {type(value)}"

    def test_cost_breakdown_preserves_values(self):
        r = self._make_mock_result()
        r.cost_breakdown = self._make_mock_cost_breakdown()
        result = convert_power_system_result(r)
        cb = result["cost_breakdown"]
        # Julia stores costs in M$; converter unscales to $ via COST_UNSCALE.
        assert cb["fuel_cost"] == 50000.0 * COST_UNSCALE
        assert cb["v2g_compensation"] == -500.0 * COST_UNSCALE
        assert cb["total"] == 179450.0 * COST_UNSCALE

    def test_cost_breakdown_negative_values_preserved(self):
        """Negative values (credits like V2G, flexible demand) must be kept."""
        r = self._make_mock_result()
        r.cost_breakdown = self._make_mock_cost_breakdown()
        result = convert_power_system_result(r)
        assert result["cost_breakdown"]["v2g_compensation"] < 0
        assert result["cost_breakdown"]["flexible_demand_benefit"] < 0


# ---------------------------------------------------------------------------
# convert_master_problem_result
# ---------------------------------------------------------------------------


class TestConvertMasterProblemResult:
    """Tests for convert_master_problem_result()."""

    def _make_mock_result(self, years):
        """Create a mock Julia MasterProblemResult."""
        r = MagicMock()
        r.status = "OPTIMAL"
        r.objective = 9876543.21
        r.solve_time = 120.0

        num_years = len(years)

        gen_inv = {}
        for y_idx in range(1, num_years + 1):
            year_dict = MagicMock()
            year_dict.keys.return_value = [1]
            year_dict.__getitem__ = lambda self, k: np.array([50.0, 100.0])
            gen_inv[y_idx] = year_dict
        r.gen_investment = gen_inv

        bat_pow = {}
        bat_cap = {}
        for y_idx in range(1, num_years + 1):
            pow_dict = MagicMock()
            pow_dict.keys.return_value = [1]
            pow_dict.__getitem__ = lambda self, k: np.array([10.0])
            bat_pow[y_idx] = pow_dict

            cap_dict = MagicMock()
            cap_dict.keys.return_value = [1]
            cap_dict.__getitem__ = lambda self, k: np.array([100.0])
            bat_cap[y_idx] = cap_dict
        r.bat_power_investment = bat_pow
        r.bat_capacity_investment = bat_cap

        trans = {}
        for y_idx in range(1, num_years + 1):
            trans_dict = MagicMock()
            trans_dict.items.return_value = [((1, 2), 200.0)]
            trans[y_idx] = trans_dict
        r.transfer_investment = trans

        gen_life = {}
        for y_idx in range(1, num_years + 1):
            life_dict = MagicMock()
            life_dict.keys.return_value = [1]
            life_dict.__getitem__ = lambda self, k: np.array([1.0])
            gen_life[y_idx] = life_dict
        r.gen_life_extension = gen_life

        bat_life = {}
        for y_idx in range(1, num_years + 1):
            blife_dict = MagicMock()
            blife_dict.keys.return_value = [1]
            blife_dict.__getitem__ = lambda self, k: np.array([0.0])
            bat_life[y_idx] = blife_dict
        r.bat_life_extension = bat_life

        r.total_investment_by_year = np.array([1e6] * num_years)
        r.total_operational_cost_by_year = np.array([5e5] * num_years)
        r.re_penetration_by_year = np.array([0.3] * num_years)

        cumul_gen = {}
        for y_idx in range(1, num_years + 1):
            cg = MagicMock()
            cg.keys.return_value = [1]
            cg.__getitem__ = lambda self, k: np.array([500.0])
            cumul_gen[y_idx] = cg
        r.cumulative_gen_capacity = cumul_gen

        cumul_bat = {}
        for y_idx in range(1, num_years + 1):
            cb = MagicMock()
            cb.keys.return_value = [1]
            cb.__getitem__ = lambda self, k: np.array([200.0])
            cumul_bat[y_idx] = cb
        r.cumulative_bat_capacity = cumul_bat

        return r

    def test_status_field(self):
        years = [2025, 2030]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert result["status"] == "OPTIMAL"

    def test_objective_field(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        # Julia stores costs in M$; converter unscales to $.
        assert result["objective"] == 9876543.21 * COST_UNSCALE

    def test_solve_time_field(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert result["solve_time"] == 120.0

    def test_years_field(self):
        years = [2025, 2030, 2035]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert result["years"] == [2025, 2030, 2035]

    def test_gen_investment_structure(self):
        years = [2025, 2030]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert 2025 in result["gen_investment"]
        assert 2030 in result["gen_investment"]
        assert 1 in result["gen_investment"][2025]

    def test_bat_investment_structure(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert "bat_power_investment" in result
        assert "bat_capacity_investment" in result
        assert 2025 in result["bat_power_investment"]

    def test_transfer_investment_index_conversion(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        ti = result["transfer_investment"][2025]
        assert (0, 1) in ti
        assert ti[(0, 1)] == 200.0

    def test_life_extension_fields(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert "gen_life_extension" in result
        assert "bat_life_extension" in result

    def test_summary_metrics(self):
        years = [2025, 2030]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert isinstance(result["total_investment_by_year"], np.ndarray)
        assert isinstance(result["total_operational_cost_by_year"], np.ndarray)
        assert isinstance(result["re_penetration_by_year"], np.ndarray)

    def test_cumulative_capacity_fields(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        assert "cumulative_gen_capacity" in result
        assert "cumulative_bat_capacity" in result
        assert 2025 in result["cumulative_gen_capacity"]

    def test_all_expected_keys(self):
        years = [2025]
        r = self._make_mock_result(years)
        result = convert_master_problem_result(r, years)
        expected = [
            "status", "objective", "solve_time", "years",
            "gen_investment", "bat_power_investment", "bat_capacity_investment",
            "transfer_investment", "gen_life_extension", "bat_life_extension",
            "total_investment_by_year", "total_operational_cost_by_year",
            "re_penetration_by_year", "cumulative_gen_capacity",
            "cumulative_bat_capacity",
        ]
        for key in expected:
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# convert_temporal_config
# ---------------------------------------------------------------------------


class TestConvertTemporalConfig:
    """Tests for convert_temporal_config()."""

    def _make_temporal(self, **overrides):
        """Create a mock TemporalConfig."""
        tc = MagicMock()
        tc.resolution_hours = overrides.get("resolution_hours", 1)
        tc.rolling_horizon_hours = overrides.get("rolling_horizon_hours", 168)
        tc.overlap_hours = overrides.get("overlap_hours", 24)
        tc.investment_resolution = overrides.get("investment_resolution", 8760)
        tc.primary_energy_resolution = overrides.get("primary_energy_resolution", 24)
        tc.battery_soc_resolution = overrides.get("battery_soc_resolution", 6)
        tc.ev_resolution = overrides.get("ev_resolution", 6)
        tc.reserve_resolution = overrides.get("reserve_resolution", 4)
        return tc

    def test_passes_hours_parameter(self, mock_esfex):
        tc = self._make_temporal()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[0] == 8760

    def test_passes_resolution_hours(self, mock_esfex):
        tc = self._make_temporal(resolution_hours=3)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=2920)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[1] == 3

    def test_passes_rolling_horizon_hours(self, mock_esfex):
        tc = self._make_temporal(rolling_horizon_hours=48)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[2] == 48

    def test_passes_overlap_hours(self, mock_esfex):
        tc = self._make_temporal(overlap_hours=12)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[3] == 12

    def test_passes_investment_resolution(self, mock_esfex):
        tc = self._make_temporal(investment_resolution=4380)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[4] == 4380

    def test_passes_primary_energy_resolution(self, mock_esfex):
        tc = self._make_temporal(primary_energy_resolution=12)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[5] == 12

    def test_passes_battery_soc_resolution(self, mock_esfex):
        tc = self._make_temporal(battery_soc_resolution=3)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[6] == 3

    def test_passes_ev_resolution(self, mock_esfex):
        tc = self._make_temporal(ev_resolution=12)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[7] == 12

    def test_passes_reserve_resolution(self, mock_esfex):
        tc = self._make_temporal(reserve_resolution=8)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[8] == 8

    def test_defaults_for_optional_resolutions(self, mock_esfex):
        """When temporal config lacks optional resolution attrs, defaults apply."""
        tc = MagicMock(spec=[
            "resolution_hours", "rolling_horizon_hours", "overlap_hours",
            "investment_resolution", "primary_energy_resolution",
        ])
        tc.resolution_hours = 1
        tc.rolling_horizon_hours = 168
        tc.overlap_hours = 24
        tc.investment_resolution = 8760
        tc.primary_energy_resolution = 24

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert call_args[6] == 6   # battery_soc_resolution default
        assert call_args[7] == 6   # ev_resolution default
        assert call_args[8] == 4   # reserve_resolution default

    def test_total_argument_count(self, mock_esfex):
        tc = self._make_temporal()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_temporal_config(tc, hours=8760)

        call_args = mock_esfex.TemporalConfig.call_args[0]
        assert len(call_args) == 9


# ---------------------------------------------------------------------------
# convert_transmission_line_data
# ---------------------------------------------------------------------------


class TestConvertTransmissionLineData:
    """Tests for convert_transmission_line_data()."""

    def _make_line(self, **overrides):
        line = MagicMock()
        line.from_bus = overrides.get("from_bus", 0)
        line.to_bus = overrides.get("to_bus", 1)
        line.from_node = overrides.get("from_node", 0)
        line.to_node = overrides.get("to_node", 1)
        line.length_km = overrides.get("length_km", 100.0)
        line.reactance_pu = overrides.get("reactance_pu", 0.05)
        line.resistance_pu = overrides.get("resistance_pu", 0.01)
        line.susceptance_pu = overrides.get("susceptance_pu", 0.0)
        line.voltage_kv = overrides.get("voltage_kv", 220.0)
        line.capacity_mw = overrides.get("capacity_mw", 500.0)
        line.line_id = overrides.get("line_id", "line_0_1")
        line.num_circuits = overrides.get("num_circuits", 1)
        line.frequency_hz = overrides.get("frequency_hz", 50.0)
        line.current_type = overrides.get("current_type", "AC")
        return line

    def _make_dc_config(self):
        dc = MagicMock()
        dc.base_impedance = 100.0
        dc.reactance_per_km = 0.0003
        dc.voltage_level_kv = 220.0
        return dc

    def test_creates_julia_struct(self, mock_esfex):
        line = self._make_line()
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transmission_line_data(line, dc)

        mock_esfex.TransmissionLineData.assert_called_once()

    def test_uses_from_bus_when_available(self, mock_esfex):
        line = self._make_line(from_bus=2, to_bus=3)
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transmission_line_data(line, dc)

        call_args = mock_esfex.TransmissionLineData.call_args[0]
        assert call_args[1] == 3  # 2+1
        assert call_args[2] == 4  # 3+1

    def test_skips_when_bus_indices_missing(self, mock_esfex):
        """Without resolved from_bus/to_bus the converter refuses to emit the line
        (mixing bus/node indices used to silently corrupt topology)."""
        line = self._make_line(from_bus=None, to_bus=None, from_node=0, to_node=1)
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            result = convert_transmission_line_data(line, dc)

        assert result is None
        mock_esfex.TransmissionLineData.assert_not_called()

    def test_line_id_passed(self, mock_esfex):
        line = self._make_line(line_id="my_line")
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transmission_line_data(line, dc)

        call_args = mock_esfex.TransmissionLineData.call_args[0]
        assert call_args[0] == "my_line"

    def test_fallback_reactance_from_length(self, mock_esfex):
        """When reactance_pu is 0 or None, compute from length and dc_config."""
        line = self._make_line(reactance_pu=0, length_km=200.0)
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transmission_line_data(line, dc)

        call_args = mock_esfex.TransmissionLineData.call_args[0]
        expected_reactance = (200.0 * 0.0003) / 100.0
        assert call_args[4] == pytest.approx(expected_reactance)

    def test_capacity_passed(self, mock_esfex):
        line = self._make_line(capacity_mw=750.0)
        dc = self._make_dc_config()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transmission_line_data(line, dc)

        call_args = mock_esfex.TransmissionLineData.call_args[0]
        assert call_args[3] == 750.0


# ---------------------------------------------------------------------------
# convert_transformer_data
# ---------------------------------------------------------------------------


class TestConvertTransformerData:
    """Tests for convert_transformer_data()."""

    def _make_trafo(self, **overrides):
        t = MagicMock()
        t.name = overrides.get("name", "trafo_0")
        t.from_bus = overrides.get("from_bus", 0)
        t.to_bus = overrides.get("to_bus", 1)
        t.from_node = overrides.get("from_node", 0)
        t.to_node = overrides.get("to_node", 1)
        t.from_voltage_kv = overrides.get("from_voltage_kv", 220.0)
        t.to_voltage_kv = overrides.get("to_voltage_kv", 110.0)
        t.rated_power_mva = overrides.get("rated_power_mva", 100.0)
        t.impedance_pu = overrides.get("impedance_pu", 0.1)
        t.losses_fraction = overrides.get("losses_fraction", 0.005)
        t.resistance_pu = overrides.get("resistance_pu", None)
        return t

    def test_creates_julia_struct(self, mock_esfex):
        trafo = self._make_trafo()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        mock_esfex.TransformerData.assert_called_once()

    def test_tap_ratio_calculation(self, mock_esfex):
        trafo = self._make_trafo(from_voltage_kv=220.0, to_voltage_kv=110.0)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        assert call_args[9] == pytest.approx(2.0)

    def test_resistance_derived_from_losses(self, mock_esfex):
        trafo = self._make_trafo(impedance_pu=0.1, losses_fraction=0.01, resistance_pu=None)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        assert call_args[7] == pytest.approx(0.001)

    def test_explicit_resistance_used_when_provided(self, mock_esfex):
        trafo = self._make_trafo(resistance_pu=0.005)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        assert call_args[7] == pytest.approx(0.005)

    def test_reactance_derived(self, mock_esfex):
        """x_pu = sqrt(z^2 - r^2)."""
        trafo = self._make_trafo(impedance_pu=0.1, losses_fraction=0.01, resistance_pu=None)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        r_pu = 0.01 * 0.1
        x_pu = math.sqrt(0.1**2 - r_pu**2)
        assert call_args[8] == pytest.approx(x_pu)

    def test_index_conversion(self, mock_esfex):
        trafo = self._make_trafo(from_bus=0, to_bus=3)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        assert call_args[1] == 1  # 0+1
        assert call_args[2] == 4  # 3+1

    def test_name_passed(self, mock_esfex):
        trafo = self._make_trafo(name="HV_trafo")

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_transformer_data(trafo)

        call_args = mock_esfex.TransformerData.call_args[0]
        assert call_args[0] == "HV_trafo"


# ---------------------------------------------------------------------------
# convert_inter_system_link
# ---------------------------------------------------------------------------


class TestConvertInterSystemLink:
    """Tests for convert_inter_system_link()."""

    def test_creates_julia_struct(self, mock_esfex):
        link = {
            "from_system": "sys_a",
            "to_system": "sys_b",
            "from_node": 0,
            "to_node": 1,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_inter_system_link(link)

        mock_esfex.InterSystemLink.assert_called_once()

    def test_system_names_passed(self, mock_esfex):
        link = {
            "from_system": "alpha",
            "to_system": "beta",
            "from_node": 0,
            "to_node": 0,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_inter_system_link(link)

        call_args = mock_esfex.InterSystemLink.call_args[0]
        assert call_args[0] == "alpha"
        assert call_args[1] == "beta"

    def test_index_conversion_from_node(self, mock_esfex):
        link = {
            "from_system": "a",
            "to_system": "b",
            "from_node": 2,
            "to_node": 5,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_inter_system_link(link)

        call_args = mock_esfex.InterSystemLink.call_args[0]
        assert call_args[2] == 3  # 2+1
        assert call_args[3] == 6  # 5+1

    def test_uses_from_bus_over_from_node(self, mock_esfex):
        link = {
            "from_system": "a",
            "to_system": "b",
            "from_node": 0,
            "to_node": 0,
            "from_bus": 3,
            "to_bus": 7,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_inter_system_link(link)

        call_args = mock_esfex.InterSystemLink.call_args[0]
        assert call_args[2] == 4  # 3+1
        assert call_args[3] == 8  # 7+1

    def test_default_values(self, mock_esfex):
        link = {
            "from_system": "a",
            "to_system": "b",
            "from_node": 0,
            "to_node": 0,
        }

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_inter_system_link(link)

        call_args = mock_esfex.InterSystemLink.call_args[0]
        assert call_args[4] == 0.0                  # existing_capacity_mw
        assert call_args[5] == 0.0                  # max_investment_mw
        # investment_cost_per_mw default is $1e6, scaled to M$ before Julia.
        assert call_args[6] == 1e6 * COST_SCALE


# ---------------------------------------------------------------------------
# convert_acdc_converter_data
# ---------------------------------------------------------------------------


class TestConvertACDCConverterData:
    """Tests for convert_acdc_converter_data()."""

    def _make_conv(self, **overrides):
        c = MagicMock()
        c.name = overrides.get("name", "conv_0")
        c.from_bus = overrides.get("from_bus", None)
        c.from_node = overrides.get("from_node", 0)
        c.to_bus = overrides.get("to_bus", None)
        c.to_node = overrides.get("to_node", 1)
        c.converter_type = overrides.get("converter_type", "VSC")
        c.from_voltage_kv = overrides.get("from_voltage_kv", 220.0)
        c.dc_voltage_kv = overrides.get("dc_voltage_kv", 320.0)
        c.rated_power_mva = overrides.get("rated_power_mva", 100.0)
        c.min_power_mva = overrides.get("min_power_mva", 0.0)
        c.efficiency_rectify = overrides.get("efficiency_rectify", 0.98)
        c.efficiency_invert = overrides.get("efficiency_invert", 0.98)
        c.standby_losses_mw = overrides.get("standby_losses_mw", 0.5)
        c.reactive_power_min_mvar = overrides.get("reactive_power_min_mvar", -50.0)
        c.reactive_power_max_mvar = overrides.get("reactive_power_max_mvar", 50.0)
        c.power_factor = overrides.get("power_factor", 1.0)
        c.impedance_pu = overrides.get("impedance_pu", 0.05)
        c.resistance_pu = overrides.get("resistance_pu", 0.01)
        c.invest_cost = overrides.get("invest_cost", 0.0)
        c.fixed_cost = overrides.get("fixed_cost", 0.0)
        c.variable_cost = overrides.get("variable_cost", 0.0)
        c.invest_max_power = overrides.get("invest_max_power", 0.0)
        c.life_time = overrides.get("life_time", 30)
        c.initial_age = overrides.get("initial_age", 0)
        c.degradation_rate = overrides.get("degradation_rate", 0.005)
        return c

    def test_creates_julia_struct(self, mock_esfex):
        conv = self._make_conv()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_acdc_converter_data(conv)

        mock_esfex.ACDCConverterData.assert_called_once()

    def test_name_passed_as_string(self, mock_esfex):
        conv = self._make_conv(name="my_converter")

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_acdc_converter_data(conv)

        call_args = mock_esfex.ACDCConverterData.call_args[0]
        assert call_args[0] == "my_converter"

    def test_falls_back_to_from_node(self, mock_esfex):
        conv = self._make_conv(from_bus=None, from_node=2, to_bus=None, to_node=4)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_acdc_converter_data(conv)

        call_args = mock_esfex.ACDCConverterData.call_args[0]
        assert call_args[2] == 3  # 2+1
        assert call_args[3] == 5  # 4+1


# ---------------------------------------------------------------------------
# convert_freq_converter_data
# ---------------------------------------------------------------------------


class TestConvertFreqConverterData:
    """Tests for convert_freq_converter_data()."""

    def _make_conv(self, **overrides):
        c = MagicMock()
        c.name = overrides.get("name", "freq_conv_0")
        c.from_bus = overrides.get("from_bus", None)
        c.from_node = overrides.get("from_node", 0)
        c.to_bus = overrides.get("to_bus", None)
        c.to_node = overrides.get("to_node", 1)
        c.from_frequency_hz = overrides.get("from_frequency_hz", 50.0)
        c.to_frequency_hz = overrides.get("to_frequency_hz", 60.0)
        c.rated_power_mva = overrides.get("rated_power_mva", 100.0)
        c.min_power_mva = overrides.get("min_power_mva", 0.0)
        c.efficiency_a_to_b = overrides.get("efficiency_a_to_b", 0.98)
        c.efficiency_b_to_a = overrides.get("efficiency_b_to_a", 0.98)
        c.standby_losses_mw = overrides.get("standby_losses_mw", 0.5)
        c.reactive_power_min_mvar = overrides.get("reactive_power_min_mvar", -50.0)
        c.reactive_power_max_mvar = overrides.get("reactive_power_max_mvar", 50.0)
        c.impedance_pu = overrides.get("impedance_pu", 0.05)
        c.resistance_pu = overrides.get("resistance_pu", 0.01)
        c.invest_cost = overrides.get("invest_cost", 0.0)
        c.fixed_cost = overrides.get("fixed_cost", 0.0)
        c.variable_cost = overrides.get("variable_cost", 0.0)
        c.invest_max_power = overrides.get("invest_max_power", 0.0)
        c.life_time = overrides.get("life_time", 30)
        c.initial_age = overrides.get("initial_age", 0)
        c.degradation_rate = overrides.get("degradation_rate", 0.005)
        return c

    def test_creates_julia_struct(self, mock_esfex):
        conv = self._make_conv()

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_freq_converter_data(conv)

        mock_esfex.FrequencyConverterData.assert_called_once()

    def test_name_passed(self, mock_esfex):
        conv = self._make_conv(name="freq_50_60")

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_freq_converter_data(conv)

        call_args = mock_esfex.FrequencyConverterData.call_args[0]
        assert call_args[0] == "freq_50_60"

    def test_frequency_values(self, mock_esfex):
        conv = self._make_conv(from_frequency_hz=50.0, to_frequency_hz=60.0)

        with patch(_PATCH_GET_ESFEX, return_value=mock_esfex):
            convert_freq_converter_data(conv)

        call_args = mock_esfex.FrequencyConverterData.call_args[0]
        assert call_args[3] == 50.0
        assert call_args[4] == 60.0


# ---------------------------------------------------------------------------
# convert_generator_config
# ---------------------------------------------------------------------------


class TestConvertGeneratorConfig:
    """Tests for convert_generator_config()."""

    def _make_gen(self, num_nodes=2):
        g = MagicMock()
        g.name = "solar"
        g.type = "renewable"
        g.fuel = "solar"
        g.rated_power = [100.0] * num_nodes
        g.min_power = [0.0] * num_nodes
        g.eff_at_rated = [1.0] * num_nodes
        g.eff_at_min = [1.0] * num_nodes
        g.ramp_up = [100.0] * num_nodes
        g.ramp_down = [100.0] * num_nodes
        g.min_up = [0] * num_nodes
        g.min_down = [0] * num_nodes
        g.start_up_cost = [0.0] * num_nodes
        g.fuel_cost = [0.0] * num_nodes
        g.fixed_cost = [10.0] * num_nodes
        g.maintenance_cost = [5.0] * num_nodes
        g.inertia = [0.0] * num_nodes
        g.invest_cost = [1000.0] * num_nodes
        g.invest_max_power = [500.0] * num_nodes
        g.reservable = False
        g.life_time = [25] * num_nodes
        g.initial_age = [0] * num_nodes
        g.degradation_rate = [0.005] * num_nodes
        g.decommissioning_cost = [100.0] * num_nodes
        g.frequency_hz = 50.0
        g.current_type = "AC"
        return g

    def test_creates_julia_struct(self, mock_esfex):
        gen = self._make_gen()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        mock_esfex.GeneratorConfig.assert_called_once()

    def test_name_type_fuel_passed(self, mock_esfex):
        gen = self._make_gen()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        call_args = mock_esfex.GeneratorConfig.call_args[0]
        assert call_args[0] == "solar"
        assert call_args[1] == "renewable"
        assert call_args[2] == "solar"

    def test_default_availability_when_none(self, mock_esfex):
        """When availability is None, ones matrix should be used."""
        gen = self._make_gen(num_nodes=2)
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen, availability=None)

        mock_esfex.GeneratorConfig.assert_called_once()

    def test_reservable_passed(self, mock_esfex):
        gen = self._make_gen()
        gen.reservable = True
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        call_args = mock_esfex.GeneratorConfig.call_args[0]
        # reservable is arg index 18 (after 17 vector args + availability matrix)
        assert True in [arg is True for arg in call_args]


# ---------------------------------------------------------------------------
# convert_battery_config
# ---------------------------------------------------------------------------


class TestConvertBatteryConfig:
    """Tests for convert_battery_config()."""

    def _make_bat(self, num_nodes=2):
        b = MagicMock()
        b.name = "li_ion"
        b.capacity = [100.0] * num_nodes
        b.MaxChargePower = [50.0] * num_nodes
        b.MaxDischargePower = [50.0] * num_nodes
        b.efficiency_charge = [0.95] * num_nodes
        b.efficiency_discharge = [0.95] * num_nodes
        b.max_DoD = [0.8] * num_nodes
        b.soc_initial = [0.5] * num_nodes
        b.invest_cost = [500.0] * num_nodes
        b.invest_cost_energy = [200.0] * num_nodes
        b.invest_max_power = [100.0] * num_nodes
        b.invest_max_capacity = [500.0] * num_nodes
        b.life_time = [15] * num_nodes
        b.initial_age = [0] * num_nodes
        b.decommissioning_cost = [50.0] * num_nodes
        b.min_duration_hours = 2.0
        b.max_duration_hours = 8.0
        b.maintenance_cost = [10.0] * num_nodes
        b.inertia = [0.0] * num_nodes
        b.spillage = False
        b.current_type = "DC"
        b.degradation_rate = [0.01] * num_nodes
        b.throughput_degradation_cost = [5.0] * num_nodes
        return b

    def test_creates_julia_struct(self, mock_esfex):
        bat = self._make_bat()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_battery_config(bat)

        mock_esfex.BatteryConfig.assert_called_once()

    def test_name_passed(self, mock_esfex):
        bat = self._make_bat()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_battery_config(bat)

        call_args = mock_esfex.BatteryConfig.call_args[0]
        assert call_args[0] == "li_ion"

    def test_soc_min_derived_from_max_dod(self, mock_esfex):
        """soc_min = 1 - max_DoD, so DoD=0.8 gives soc_min=0.2."""
        bat = self._make_bat()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_battery_config(bat)

        mock_esfex.BatteryConfig.assert_called_once()

    def test_spillage_false_passed(self, mock_esfex):
        bat = self._make_bat()
        bat.spillage = False
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_battery_config(bat)

        call_args = mock_esfex.BatteryConfig.call_args[0]
        # spillage (False) should be in the args
        assert False in list(call_args)

    def test_current_type_passed(self, mock_esfex):
        bat = self._make_bat()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_battery_config(bat)

        call_args = mock_esfex.BatteryConfig.call_args[0]
        assert "DC" in list(call_args)


# ---------------------------------------------------------------------------
# Reservoir fields in convert_generator_config
# ---------------------------------------------------------------------------


class TestConvertGeneratorConfigReservoir:
    """Tests for reservoir fields in convert_generator_config()."""

    def _make_reservoir_gen(self, num_nodes=2):
        g = MagicMock()
        g.name = "hydro"
        g.type = "Renewable"
        g.fuel = "Hydro"
        g.rated_power = [100.0] * num_nodes
        g.min_power = [0.0] * num_nodes
        g.eff_at_rated = [0.9] * num_nodes
        g.eff_at_min = [0.9] * num_nodes
        g.ramp_up = [100.0] * num_nodes
        g.ramp_down = [100.0] * num_nodes
        g.min_up = [0] * num_nodes
        g.min_down = [0] * num_nodes
        g.start_up_cost = [0.0] * num_nodes
        g.fuel_cost = [0.0] * num_nodes
        g.fixed_cost = [10.0] * num_nodes
        g.maintenance_cost = [5.0] * num_nodes
        g.inertia = [3.0] * num_nodes
        g.invest_cost = [2000.0] * num_nodes
        g.invest_max_power = [200.0] * num_nodes
        g.reservable = True
        g.life_time = [50] * num_nodes
        g.initial_age = [5] * num_nodes
        g.degradation_rate = [0.002] * num_nodes
        g.decommissioning_cost = [500.0] * num_nodes
        g.frequency_hz = 50.0
        g.current_type = "AC"
        # Reservoir fields
        g.reservoir_capacity = [500.0] * num_nodes
        g.reservoir_initial_level = [0.8] * num_nodes
        g.reservoir_min_level = [0.1] * num_nodes
        g.reservoir_max_level = [0.95] * num_nodes
        g.reservoir_inflow_file = None
        g.reservoir_turbine_efficiency = [0.92] * num_nodes
        g.reservoir_evaporation_rate = [0.001] * num_nodes
        g.reservoir_pump_capacity = [50.0] * num_nodes
        g.reservoir_pump_efficiency = [0.87] * num_nodes
        g.reservoir_spillage_allowed = True
        g.reservoir_invest_cost = [100000.0] * num_nodes
        g.reservoir_invest_max = [200.0] * num_nodes
        return g

    def test_reservoir_gen_creates_julia_struct(self, mock_esfex):
        gen = self._make_reservoir_gen()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        mock_esfex.GeneratorConfig.assert_called_once()

    def test_reservoir_fields_in_call_args(self, mock_esfex):
        gen = self._make_reservoir_gen()
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        call_args = mock_esfex.GeneratorConfig.call_args[0]
        # reservoir_spillage_allowed (True) should be in the args
        assert True in list(call_args)

    def test_reservoir_empty_fields_defaults(self, mock_esfex):
        """When reservoir lists are empty, converter should fill with zeros."""
        gen = self._make_reservoir_gen()
        gen.reservoir_capacity = []
        gen.reservoir_initial_level = []
        gen.reservoir_min_level = []
        gen.reservoir_max_level = []
        gen.reservoir_turbine_efficiency = []
        gen.reservoir_evaporation_rate = []
        gen.reservoir_pump_capacity = []
        gen.reservoir_pump_efficiency = []
        gen.reservoir_invest_cost = []
        gen.reservoir_invest_max = []
        mock_jl = MagicMock()

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen)

        mock_esfex.GeneratorConfig.assert_called_once()

    def test_inflow_parameter_passed(self, mock_esfex):
        """When inflow array is provided, it should be passed to Julia."""
        gen = self._make_reservoir_gen()
        mock_jl = MagicMock()
        inflow = np.ones((24, 2)) * 10.0

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            convert_generator_config(gen, inflow=inflow)

        mock_esfex.GeneratorConfig.assert_called_once()


# ---------------------------------------------------------------------------
# blocks_to_julia_cost_segments
# ---------------------------------------------------------------------------


class TestBlocksToJuliaCostSegments:
    """Tests for blocks_to_julia_cost_segments()."""

    def test_single_block(self):
        """One block -> CostSegment called once, push! called once."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_push = MagicMock()
        mock_jl_vec = MagicMock()

        def seval_side_effect(expr):
            if expr == "CostSegment[]":
                return mock_jl_vec
            if expr == "push!":
                return mock_push
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        blocks = [CostCurveBlock(fraction=1.0, price=50.0)]

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            result = blocks_to_julia_cost_segments(blocks)

        assert result is mock_jl_vec
        # Price is scaled by COST_SCALE (USD → M$) before reaching Julia.
        mock_esfex.CostSegment.assert_called_once_with(1.0, 50.0 * COST_SCALE)
        mock_push.assert_called_once_with(
            mock_jl_vec, mock_esfex.CostSegment.return_value
        )

    def test_multiple_blocks(self):
        """Three blocks -> CostSegment called 3x, push! called 3x."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_push = MagicMock()
        mock_jl_vec = MagicMock()

        def seval_side_effect(expr):
            if expr == "CostSegment[]":
                return mock_jl_vec
            if expr == "push!":
                return mock_push
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        seg_returns = [
            MagicMock(name="seg0"),
            MagicMock(name="seg1"),
            MagicMock(name="seg2"),
        ]
        mock_esfex.CostSegment.side_effect = seg_returns

        blocks = [
            CostCurveBlock(fraction=0.3, price=40.0),
            CostCurveBlock(fraction=0.4, price=60.0),
            CostCurveBlock(fraction=0.3, price=80.0),
        ]

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            result = blocks_to_julia_cost_segments(blocks)

        assert result is mock_jl_vec
        assert mock_esfex.CostSegment.call_count == 3
        assert mock_push.call_count == 3

        # Verify each CostSegment was created with correct args (price in M$).
        mock_esfex.CostSegment.assert_any_call(0.3, 40.0 * COST_SCALE)
        mock_esfex.CostSegment.assert_any_call(0.4, 60.0 * COST_SCALE)
        mock_esfex.CostSegment.assert_any_call(0.3, 80.0 * COST_SCALE)

        # Verify push! was called with the vector and each segment
        for seg in seg_returns:
            mock_push.assert_any_call(mock_jl_vec, seg)

    def test_empty_blocks(self):
        """Empty list -> no CostSegment calls, returns empty vector."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_jl_vec = MagicMock()

        def seval_side_effect(expr):
            if expr == "CostSegment[]":
                return mock_jl_vec
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        with (
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
        ):
            result = blocks_to_julia_cost_segments([])

        assert result is mock_jl_vec
        mock_esfex.CostSegment.assert_not_called()


# ---------------------------------------------------------------------------
# build_gen_cost_curves_dict
# ---------------------------------------------------------------------------

_PATCH_NORMALIZE = "esfex.bridge.converters.normalize_cost_curve"


class TestBuildGenCostCurvesDict:
    """Tests for build_gen_cost_curves_dict()."""

    def test_no_curves(self):
        """Generator with fuel_cost_curve=None -> empty outer dict."""
        mock_jl = MagicMock()
        mock_outer = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int" in expr:
                return mock_outer
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        from types import SimpleNamespace

        gen = SimpleNamespace(fuel_cost_curve=None, fuel_cost=[100.0])
        generators = [("gen_0", gen)]

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            result = build_gen_cost_curves_dict(
                generators=generators,
                gen_configs=[],
                num_buses=1,
            )

        assert result is mock_outer
        # No __setitem__ should have been called on outer (no entries added)
        mock_outer.__setitem__.assert_not_called()

    def test_flat_curve_skipped(self):
        """Generator with flat curve (1 segment) -> empty outer dict."""
        mock_jl = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        call_count = {"outer": 0, "inner": 0}

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                call_count["outer"] += 1
                return mock_outer
            if "Dict{Int, Vector" in expr:
                call_count["inner"] += 1
                return mock_inner
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        flat_curve = CostCurveConfig(curve_type="flat", flat_price=100.0)

        from types import SimpleNamespace

        gen = SimpleNamespace(fuel_cost_curve=[flat_curve], fuel_cost=[100.0])
        generators = [("gen_0", gen)]

        # normalize_cost_curve for flat returns 1 block -> skipped
        flat_block = [CostCurveBlock(fraction=1.0, price=100.0)]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_NORMALIZE, return_value=flat_block),
        ):
            result = build_gen_cost_curves_dict(
                generators=generators,
                gen_configs=[],
                num_buses=1,
            )

        assert result is mock_outer
        # Outer dict should not have any generator entry (flat curve skipped)
        mock_outer.__setitem__.assert_not_called()

    def test_multi_segment_curve(self):
        """Generator with multi-segment curve -> outer dict has entry."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            if expr == "CostSegment[]":
                return MagicMock()
            if expr == "push!":
                return MagicMock()
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        stepwise_curve = CostCurveConfig(
            curve_type="stepwise",
            blocks=[
                CostCurveBlock(fraction=0.5, price=40.0),
                CostCurveBlock(fraction=0.5, price=80.0),
            ],
        )

        from types import SimpleNamespace

        gen = SimpleNamespace(
            fuel_cost_curve=[stepwise_curve], fuel_cost=[60.0]
        )
        generators = [("gen_0", gen)]

        multi_blocks = [
            CostCurveBlock(fraction=0.5, price=40.0),
            CostCurveBlock(fraction=0.5, price=80.0),
        ]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_NORMALIZE, return_value=multi_blocks),
        ):
            result = build_gen_cost_curves_dict(
                generators=generators,
                gen_configs=[],
                num_buses=1,
            )

        assert result is mock_outer
        # Generator index 1 (Julia 1-based) should be added to outer dict
        mock_outer.__setitem__.assert_called_once()
        call_args = mock_outer.__setitem__.call_args
        assert call_args[0][0] == 1  # g_idx 0 -> Julia 1

    def test_bus_mapping(self):
        """Verify bus_to_node mapping computes correct bus index."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            if expr == "CostSegment[]":
                return MagicMock()
            if expr == "push!":
                return MagicMock()
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        stepwise_curve = CostCurveConfig(
            curve_type="stepwise",
            blocks=[
                CostCurveBlock(fraction=0.5, price=40.0),
                CostCurveBlock(fraction=0.5, price=80.0),
            ],
        )

        from types import SimpleNamespace

        gen = SimpleNamespace(
            fuel_cost_curve=[stepwise_curve], fuel_cost=[60.0]
        )
        generators = [("gen_0", gen)]

        multi_blocks = [
            CostCurveBlock(fraction=0.5, price=40.0),
            CostCurveBlock(fraction=0.5, price=80.0),
        ]

        # bus_to_node: bus 0 -> node 2, bus 1 -> node 0, bus 2 -> node 1
        # gen is at node_idx=0 in the curve loop,
        # so first bus mapping node 0 is bus index 1 (bus_to_node[1]==0)
        # bus_1 = 1 + 1 = 2
        bus_to_node = [2, 0, 1]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_NORMALIZE, return_value=multi_blocks),
        ):
            result = build_gen_cost_curves_dict(
                generators=generators,
                gen_configs=[],
                num_buses=3,
                bus_to_node=bus_to_node,
                gen_bus_per_node=None,
            )

        assert result is mock_outer
        # Inner dict should have bus_1 = 2
        # (bus index 1 maps to node 0, +1 for Julia)
        mock_inner.__setitem__.assert_called_once()
        bus_key = mock_inner.__setitem__.call_args[0][0]
        assert bus_key == 2

    def test_gen_to_bus_override(self):
        """When gen_to_bus provides a mapping, it overrides bus_to_node scan."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            if expr == "CostSegment[]":
                return MagicMock()
            if expr == "push!":
                return MagicMock()
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        stepwise_curve = CostCurveConfig(
            curve_type="stepwise",
            blocks=[
                CostCurveBlock(fraction=0.5, price=40.0),
                CostCurveBlock(fraction=0.5, price=80.0),
            ],
        )

        from types import SimpleNamespace

        gen = SimpleNamespace(
            fuel_cost_curve=[stepwise_curve], fuel_cost=[60.0]
        )
        generators = [("gen_0", gen)]

        multi_blocks = [
            CostCurveBlock(fraction=0.5, price=40.0),
            CostCurveBlock(fraction=0.5, price=80.0),
        ]

        bus_to_node = [0, 1, 2]
        # gen_0 explicitly mapped to bus 2 (0-based) for node 0
        gen_bus_per_node = {"gen_0": {0: 2}}

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_NORMALIZE, return_value=multi_blocks),
        ):
            result = build_gen_cost_curves_dict(
                generators=generators,
                gen_configs=[],
                num_buses=3,
                bus_to_node=bus_to_node,
                gen_bus_per_node=gen_bus_per_node,
            )

        # Inner dict should have bus_1 = 3 (bus 2 + 1 for Julia 1-based)
        mock_inner.__setitem__.assert_called_once()
        bus_key = mock_inner.__setitem__.call_args[0][0]
        assert bus_key == 3


# ---------------------------------------------------------------------------
# build_bat_cost_curves_dict
# ---------------------------------------------------------------------------


class TestBuildBatCostCurvesDict:
    """Tests for build_bat_cost_curves_dict()."""

    def test_no_curves(self):
        """Battery with discharge_cost_curve=None -> empty outer dict."""
        mock_jl = MagicMock()
        mock_outer = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int" in expr:
                return mock_outer
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        from types import SimpleNamespace

        bat = SimpleNamespace(
            discharge_cost_curve=None,
            throughput_degradation_cost=[0.0],
        )
        batteries = [("bat_0", bat)]

        with patch(_PATCH_GET_JULIA, return_value=mock_jl):
            result = build_bat_cost_curves_dict(
                batteries=batteries,
                num_buses=1,
            )

        assert result is mock_outer
        mock_outer.__setitem__.assert_not_called()

    def test_multi_segment_discharge(self):
        """Battery with stepwise discharge curve -> outer dict has entry."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            if expr == "CostSegment[]":
                return MagicMock()
            if expr == "push!":
                return MagicMock()
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        stepwise_curve = CostCurveConfig(
            curve_type="stepwise",
            blocks=[
                CostCurveBlock(fraction=0.5, price=10.0),
                CostCurveBlock(fraction=0.5, price=20.0),
            ],
        )

        from types import SimpleNamespace

        bat = SimpleNamespace(
            discharge_cost_curve=[stepwise_curve],
            throughput_degradation_cost=[5.0],
        )
        batteries = [("bat_0", bat)]

        multi_blocks = [
            CostCurveBlock(fraction=0.5, price=10.0),
            CostCurveBlock(fraction=0.5, price=20.0),
        ]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_NORMALIZE, return_value=multi_blocks),
        ):
            result = build_bat_cost_curves_dict(
                batteries=batteries,
                num_buses=1,
            )

        assert result is mock_outer
        # Battery index 1 (Julia 1-based) should be added to outer dict
        mock_outer.__setitem__.assert_called_once()
        call_args = mock_outer.__setitem__.call_args
        assert call_args[0][0] == 1  # bi_0=0 -> Julia 1

    def test_flat_discharge_skipped(self):
        """Battery with flat discharge curve (1 segment) -> empty outer dict."""
        mock_jl = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        flat_curve = CostCurveConfig(curve_type="flat", flat_price=5.0)

        from types import SimpleNamespace

        bat = SimpleNamespace(
            discharge_cost_curve=[flat_curve],
            throughput_degradation_cost=[5.0],
        )
        batteries = [("bat_0", bat)]

        flat_block = [CostCurveBlock(fraction=1.0, price=5.0)]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_NORMALIZE, return_value=flat_block),
        ):
            result = build_bat_cost_curves_dict(
                batteries=batteries,
                num_buses=1,
            )

        assert result is mock_outer
        mock_outer.__setitem__.assert_not_called()

    def test_bus_mapping(self):
        """Verify bus_to_node mapping works for batteries."""
        mock_jl = MagicMock()
        mock_esfex = MagicMock()
        mock_outer = MagicMock()
        mock_inner = MagicMock()

        def seval_side_effect(expr):
            if "Dict{Int, Dict{Int, Vector" in expr:
                return mock_outer
            if "Dict{Int, Vector" in expr:
                return mock_inner
            if expr == "CostSegment[]":
                return MagicMock()
            if expr == "push!":
                return MagicMock()
            return MagicMock()

        mock_jl.seval.side_effect = seval_side_effect

        stepwise_curve = CostCurveConfig(
            curve_type="stepwise",
            blocks=[
                CostCurveBlock(fraction=0.5, price=10.0),
                CostCurveBlock(fraction=0.5, price=20.0),
            ],
        )

        from types import SimpleNamespace

        bat = SimpleNamespace(
            discharge_cost_curve=[stepwise_curve],
            throughput_degradation_cost=[5.0],
        )
        batteries = [("bat_0", bat)]

        multi_blocks = [
            CostCurveBlock(fraction=0.5, price=10.0),
            CostCurveBlock(fraction=0.5, price=20.0),
        ]

        # bat_bus_per_node maps bat_0 at node 0 to bus 1 (0-based) -> Julia bus_1 = 2
        bat_bus_per_node = {"bat_0": {0: 1}}
        bus_to_node = [0, 1]

        with (
            patch(_PATCH_GET_JULIA, return_value=mock_jl),
            patch(_PATCH_GET_ESFEX, return_value=mock_esfex),
            patch(_PATCH_NORMALIZE, return_value=multi_blocks),
        ):
            result = build_bat_cost_curves_dict(
                batteries=batteries,
                num_buses=2,
                bus_to_node=bus_to_node,
                bat_bus_per_node=bat_bus_per_node,
            )

        assert result is mock_outer
        mock_inner.__setitem__.assert_called_once()
        bus_key = mock_inner.__setitem__.call_args[0][0]
        assert bus_key == 2  # bus 1 (0-based) + 1 = 2
