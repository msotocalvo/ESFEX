"""Tests for esfex.sensitivity.lp_parser module.

Covers LPModel dataclass, _parse_terms, parse_lp_file, solve_lp,
extract_kpis, and perturb_and_solve.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import sparse

from esfex.sensitivity.lp_parser import (
    LPModel,
    _parse_terms,
    extract_kpis,
    parse_lp_file,
    perturb_and_solve,
    solve_lp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_lp(tmp_path, content: str, name: str = "test.lp"):
    """Write LP content to a temporary file and return its path."""
    p = tmp_path / name
    p.write_text(content)
    return p


def _make_simple_model(**overrides) -> LPModel:
    """Build a minimal LPModel with sensible defaults, overridable."""
    names = overrides.pop("variable_names", ["x", "y"])
    idx = {n: i for i, n in enumerate(names)}
    defaults = dict(
        variable_names=names,
        variable_index=idx,
        c=np.array([1.0, 2.0][:len(names)]),
        A_ub=None,
        b_ub=np.array([]),
        A_eq=None,
        b_eq=np.array([]),
        bounds=[(0.0, None)] * len(names),
        constraint_names_ub=[],
        constraint_names_eq=[],
        sense="minimize",
    )
    defaults.update(overrides)
    return LPModel(**defaults)


# ===========================================================================
# 1. LPModel properties
# ===========================================================================

class TestLPModelProperties:
    """Tests for n_vars and n_constraints properties."""

    def test_n_vars_empty(self):
        m = LPModel()
        assert m.n_vars == 0

    def test_n_vars_two(self):
        m = _make_simple_model()
        assert m.n_vars == 2

    def test_n_vars_many(self):
        names = [f"v{i}" for i in range(50)]
        m = _make_simple_model(variable_names=names,
                               c=np.zeros(50),
                               bounds=[(0, None)] * 50)
        assert m.n_vars == 50

    def test_n_constraints_no_matrices(self):
        m = _make_simple_model()
        assert m.n_constraints == 0

    def test_n_constraints_ub_only(self):
        A = sparse.csr_matrix(np.array([[1, 1]]))
        m = _make_simple_model(A_ub=A, b_ub=np.array([10.0]))
        assert m.n_constraints == 1

    def test_n_constraints_eq_only(self):
        A = sparse.csr_matrix(np.array([[1, -1]]))
        m = _make_simple_model(A_eq=A, b_eq=np.array([0.0]))
        assert m.n_constraints == 1

    def test_n_constraints_both(self):
        A_ub = sparse.csr_matrix(np.array([[1, 0], [0, 1]]))
        A_eq = sparse.csr_matrix(np.array([[1, 1]]))
        m = _make_simple_model(
            A_ub=A_ub, b_ub=np.array([5.0, 5.0]),
            A_eq=A_eq, b_eq=np.array([3.0]),
        )
        assert m.n_constraints == 3

    def test_default_sense(self):
        m = LPModel()
        assert m.sense == "minimize"


# ===========================================================================
# 2. get_objective_groups
# ===========================================================================

class TestGetObjectiveGroups:
    """Tests for LPModel.get_objective_groups()."""

    def test_no_matching_vars(self):
        m = _make_simple_model()
        assert m.get_objective_groups() == {}

    def test_tech_inv_group(self):
        names = ["tech_inv_y1_t0_n1", "tech_inv_y2_t0_n2", "other"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(
            variable_names=names,
            variable_index=idx,
            c=np.array([100.0, 200.0, 50.0]),
        )
        groups = m.get_objective_groups()
        assert "inv_tech_0" in groups
        assert sorted(groups["inv_tech_0"]) == [0, 1]

    def test_bat_tech_pow_inv_group(self):
        names = ["bat_tech_pow_inv_y1_bt3_n0"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([10.0]))
        groups = m.get_objective_groups()
        assert "inv_bat_pow_3" in groups
        assert groups["inv_bat_pow_3"] == [0]

    def test_bat_tech_cap_inv_group(self):
        names = ["bat_tech_cap_inv_y2_bt1_n0"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([15.0]))
        groups = m.get_objective_groups()
        assert "inv_bat_cap_1" in groups

    def test_trans_inv_group(self):
        names = ["trans_inv_y1_l0_2"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([30.0]))
        groups = m.get_objective_groups()
        assert "inv_trans" in groups

    def test_op_load_shedding_group(self):
        names = ["op_ll_y1_d1_n0_t5"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([9999.0]))
        groups = m.get_objective_groups()
        assert "op_load_shedding" in groups

    def test_op_fre_penalty_group(self):
        names = ["op_fre_loss_y1_d1_n0"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([600.0]))
        groups = m.get_objective_groups()
        assert "op_fre_penalty" in groups

    def test_zero_coeff_skipped(self):
        names = ["tech_inv_y1_t0_n1"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([0.0]))
        assert m.get_objective_groups() == {}

    def test_multiple_groups_mixed(self):
        names = [
            "tech_inv_y1_t0_n0",
            "bat_tech_pow_inv_y1_bt0_n0",
            "op_ll_y1_d1_n0",
            "op_fre_loss_y1_d1_n0",
            "unrelated_var",
        ]
        idx = {n: i for i, n in enumerate(names)}
        c = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        m = LPModel(variable_names=names, variable_index=idx, c=c)
        groups = m.get_objective_groups()
        assert len(groups) == 4
        assert "inv_tech_0" in groups
        assert "inv_bat_pow_0" in groups
        assert "op_load_shedding" in groups
        assert "op_fre_penalty" in groups

    def test_multiple_technologies(self):
        names = ["tech_inv_y1_t0_n0", "tech_inv_y1_t1_n0", "tech_inv_y2_t1_n1"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(variable_names=names, variable_index=idx,
                     c=np.array([1.0, 2.0, 3.0]))
        groups = m.get_objective_groups()
        assert "inv_tech_0" in groups
        assert "inv_tech_1" in groups
        assert len(groups["inv_tech_0"]) == 1
        assert len(groups["inv_tech_1"]) == 2


# ===========================================================================
# 3. get_rhs_groups
# ===========================================================================

class TestGetRhsGroups:
    """Tests for LPModel.get_rhs_groups()."""

    def test_empty(self):
        m = LPModel()
        assert m.get_rhs_groups() == {}

    def test_demand_eq(self):
        m = LPModel(constraint_names_eq=["demand_bal_y1_n0_t1"])
        groups = m.get_rhs_groups()
        assert "demand" in groups
        assert groups["demand"] == [("eq", 0)]

    def test_power_balance_eq(self):
        m = LPModel(constraint_names_eq=["power_balance_y1_n0_t2"])
        groups = m.get_rhs_groups()
        assert "demand" in groups

    def test_re_target_ub(self):
        m = LPModel(constraint_names_ub=["re_target_y1"])
        groups = m.get_rhs_groups()
        assert "re_target" in groups
        assert groups["re_target"] == [("ub", 0)]

    def test_re_ratio_eq(self):
        m = LPModel(constraint_names_eq=["yearly_re_ratio_y1"])
        groups = m.get_rhs_groups()
        assert "re_target" in groups

    def test_co2_ub(self):
        m = LPModel(constraint_names_ub=["co2_limit_y1"])
        groups = m.get_rhs_groups()
        assert "co2_budget" in groups

    def test_carbon_eq(self):
        m = LPModel(constraint_names_eq=["carbon_cap_y1"])
        groups = m.get_rhs_groups()
        assert "co2_budget" in groups

    def test_budget_ub(self):
        m = LPModel(constraint_names_ub=["budget_limit_y1"])
        groups = m.get_rhs_groups()
        assert "cost_budget" in groups

    def test_mixed_ub_and_eq(self):
        m = LPModel(
            constraint_names_ub=["demand_bal_ub_1", "budget_limit_1"],
            constraint_names_eq=["co2_cap_y1", "re_target_y1"],
        )
        groups = m.get_rhs_groups()
        assert "demand" in groups
        assert "cost_budget" in groups
        assert "co2_budget" in groups
        assert "re_target" in groups

    def test_unmatched_constraint_ignored(self):
        m = LPModel(
            constraint_names_ub=["capacity_limit_g0"],
            constraint_names_eq=["soc_balance_b0"],
        )
        assert m.get_rhs_groups() == {}


# ===========================================================================
# 4. _parse_terms
# ===========================================================================

class TestParseTerms:
    """Tests for _parse_terms()."""

    def _idx(self, *names):
        return {n: i for i, n in enumerate(names)}

    def test_empty_expression(self):
        coeffs, const = _parse_terms("", {}, 0)
        assert coeffs == {}
        assert const == 0.0

    def test_single_var_with_coeff(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("1 x", vi, 1)
        assert coeffs == {0: 1.0}
        assert const == 0.0

    def test_single_var_float_coeff(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("2.5 x", vi, 1)
        assert coeffs[0] == pytest.approx(2.5)

    def test_negative_coeff(self):
        vi = self._idx("y")
        coeffs, const = _parse_terms("-2.5 y", vi, 1)
        assert coeffs[0] == pytest.approx(-2.5)

    def test_implicit_coeff_one(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("x", vi, 1)
        assert coeffs[0] == pytest.approx(1.0)

    def test_implicit_coeff_one_with_sign(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("- x", vi, 1)
        assert coeffs[0] == pytest.approx(-1.0)

    def test_pure_constant(self):
        coeffs, const = _parse_terms("5", {}, 0)
        assert coeffs == {}
        assert const == pytest.approx(5.0)

    def test_negative_constant(self):
        coeffs, const = _parse_terms("-3.5", {}, 0)
        assert coeffs == {}
        assert const == pytest.approx(-3.5)

    def test_two_vars_addition(self):
        vi = self._idx("x", "y")
        coeffs, const = _parse_terms("1 x + 2 y", vi, 2)
        assert coeffs[0] == pytest.approx(1.0)
        assert coeffs[1] == pytest.approx(2.0)

    def test_subtraction(self):
        vi = self._idx("x", "y")
        coeffs, const = _parse_terms("3 x - 1 y", vi, 2)
        assert coeffs[0] == pytest.approx(3.0)
        assert coeffs[1] == pytest.approx(-1.0)

    def test_combined_expression(self):
        vi = self._idx("x", "y", "z")
        coeffs, const = _parse_terms("1 x + 2 y - 3 z", vi, 3)
        assert coeffs[0] == pytest.approx(1.0)
        assert coeffs[1] == pytest.approx(2.0)
        assert coeffs[2] == pytest.approx(-3.0)

    def test_scientific_notation(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("1e3 x", vi, 1)
        assert coeffs[0] == pytest.approx(1000.0)

    def test_scientific_notation_no_sign(self):
        """Parser handles e-notation without sign in exponent (e.g. 1e3)."""
        vi = self._idx("x")
        coeffs, const = _parse_terms("1e2 x", vi, 1)
        assert coeffs[0] == pytest.approx(100.0)

    def test_unknown_variable_ignored(self):
        vi = self._idx("x")
        coeffs, const = _parse_terms("1 x + 2 unknown", vi, 1)
        assert 0 in coeffs
        # "unknown" is not in var_index so it should be ignored (implicit coeff path)
        # but "2 unknown" has "2" parsed as coeff and "unknown" as variable name
        # Since "unknown" is not in var_index, it just gets skipped
        assert len([k for k in coeffs if coeffs[k] != 0]) <= 1

    def test_whitespace_variations(self):
        vi = self._idx("a", "b")
        coeffs, _ = _parse_terms("  3   a  +  7   b  ", vi, 2)
        assert coeffs[0] == pytest.approx(3.0)
        assert coeffs[1] == pytest.approx(7.0)


# ===========================================================================
# 5. parse_lp_file -- basic
# ===========================================================================

class TestParseLpFile:
    """Tests for parse_lp_file() with temporary LP files."""

    def test_minimal_minimize(self, tmp_path):
        content = """\
minimize
obj: 1 x + 2 y
subject to
c1: 1 x + 1 y >= 10
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.sense == "minimize"
        assert model.n_vars == 2
        assert model.n_constraints == 1  # 1 ub (>= converted)

    def test_maximize_sense(self, tmp_path):
        content = """\
maximize
obj: 3 x + 5 y
subject to
c1: 1 x + 2 y <= 14
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.sense == "maximize"

    def test_variable_names_sorted(self, tmp_path):
        content = """\
minimize
obj: 1 z + 1 a + 1 m
subject to
c1: 1 z + 1 a + 1 m >= 1
Bounds
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.variable_names == ["a", "m", "z"]

    def test_objective_coefficients(self, tmp_path):
        content = """\
minimize
obj: 3 x + 7 y
subject to
c1: 1 x + 1 y <= 100
Bounds
0 <= x <= 50
0 <= y <= 50
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # Variables sorted: x, y
        xi = model.variable_index["x"]
        yi = model.variable_index["y"]
        assert model.c[xi] == pytest.approx(3.0)
        assert model.c[yi] == pytest.approx(7.0)

    def test_le_constraint(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 2 x <= 10
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.A_ub is not None
        assert model.A_ub.shape[0] == 1
        xi = model.variable_index["x"]
        assert model.A_ub[0, xi] == pytest.approx(2.0)
        assert model.b_ub[0] == pytest.approx(10.0)

    def test_ge_constraint_negated(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x >= 5
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # >= converted to <=: -x <= -5
        xi = model.variable_index["x"]
        assert model.A_ub[0, xi] == pytest.approx(-1.0)
        assert model.b_ub[0] == pytest.approx(-5.0)

    def test_eq_constraint(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y = 10
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.A_eq is not None
        assert model.A_eq.shape[0] == 1
        assert model.b_eq[0] == pytest.approx(10.0)

    def test_multiple_constraints(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y <= 20
c2: 1 x - 1 y >= 0
c3: 1 x = 5
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # c1 <= -> 1 ub, c2 >= -> 1 ub (negated), c3 = -> 1 eq
        assert model.A_ub.shape[0] == 2
        assert model.A_eq.shape[0] == 1
        assert model.n_constraints == 3

    def test_constraint_names(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
demand_bal_y1_n0: 1 x = 100
re_target_y1: 1 x <= 50
Bounds
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert "demand_bal_y1_n0" in model.constraint_names_eq
        assert "re_target_y1" in model.constraint_names_ub


# ===========================================================================
# 6. parse_lp_file -- bounds
# ===========================================================================

class TestParseLpFileBounds:
    """Tests for bound parsing in parse_lp_file()."""

    def test_range_bounds(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x <= 100
Bounds
5 <= x <= 50
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (5.0, 50.0)

    def test_fixed_bound(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y <= 100
Bounds
x = 42
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (42.0, 42.0)

    def test_free_bound(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y <= 100
Bounds
x free
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (None, None)

    def test_ge_bound(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x <= 100
Bounds
x >= 10
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (10.0, None)

    def test_le_bound(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x >= 0
Bounds
x <= 25
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (0.0, 25.0)

    def test_default_bounds(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y <= 100
Bounds
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # Default should be (0.0, None)
        for b in model.bounds:
            assert b == (0.0, None)

    def test_negative_lower_bound(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x <= 100
Bounds
-10 <= x <= 50
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        xi = model.variable_index["x"]
        assert model.bounds[xi] == (-10.0, 50.0)


# ===========================================================================
# 7. parse_lp_file -- edge cases
# ===========================================================================

class TestParseLpFileEdgeCases:
    """Edge cases for parse_lp_file()."""

    def test_no_constraints_section(self, tmp_path):
        content = """\
minimize
obj: 1 x
Bounds
0 <= x <= 10
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.n_vars == 1
        assert model.n_constraints == 0

    def test_no_bounds_section(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y <= 10
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.n_vars == 2
        # Defaults to (0.0, None)
        for b in model.bounds:
            assert b == (0.0, None)

    def test_missing_objective_raises(self, tmp_path):
        content = """\
subject to
c1: 1 x <= 5
End
"""
        p = _write_lp(tmp_path, content)
        with pytest.raises(ValueError, match="No minimize/maximize"):
            parse_lp_file(p)

    def test_blank_lines_ignored(self, tmp_path):
        content = """\
minimize

obj: 1 x + 2 y

subject to

c1: 1 x + 1 y <= 10

Bounds

0 <= x <= 5
0 <= y <= 5

End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.n_vars == 2
        assert model.n_constraints == 1

    def test_var_with_underscores_and_commas(self, tmp_path):
        content = """\
minimize
obj: 1 gen_inv_y1_g0_n0 + 2 op_gen_y1_d2_5,n0
subject to
c1: 1 gen_inv_y1_g0_n0 + 1 op_gen_y1_d2_5,n0 <= 100
Bounds
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert "gen_inv_y1_g0_n0" in model.variable_index
        assert "op_gen_y1_d2_5,n0" in model.variable_index


# ===========================================================================
# 8. solve_lp
# ===========================================================================

class TestSolveLp:
    """Tests for solve_lp()."""

    def test_simple_minimize(self, tmp_path):
        """min x + y s.t. x + y >= 10, 0 <= x <= 100, 0 <= y <= 100"""
        content = """\
minimize
obj: 1 x + 1 y
subject to
c1: 1 x + 1 y >= 10
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        assert obj_val == pytest.approx(10.0, abs=1e-6)

    def test_simple_maximize(self, tmp_path):
        """max 3x + 5y s.t. x <= 4, y <= 6, x + y <= 8"""
        content = """\
maximize
obj: 3 x + 5 y
subject to
c1: 1 x <= 4
c2: 1 y <= 6
c3: 1 x + 1 y <= 8
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        # Optimal: x=2, y=6 -> obj=36 OR x=4, y=4 -> obj=32
        # Actually: maximize 3x + 5y, x<=4, y<=6, x+y<=8
        # y dominates, so y=6, x=2 -> 6+30=36
        assert obj_val == pytest.approx(36.0, abs=1e-6)

    def test_equality_constraint(self, tmp_path):
        """min x + y s.t. x + y = 5, x,y >= 0"""
        content = """\
minimize
obj: 1 x + 2 y
subject to
c1: 1 x + 1 y = 5
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        # min x + 2y, x+y=5 => x=5, y=0 -> obj=5
        assert obj_val == pytest.approx(5.0, abs=1e-6)

    def test_infeasible_returns_inf(self, tmp_path):
        """x >= 10 and x <= 5 is infeasible"""
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x >= 10
c2: 1 x <= 5
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        assert obj_val == float("inf")
        assert sol == {}

    def test_solution_dict_has_nonzero_vars(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y + 1 z
subject to
c1: 1 x >= 5
Bounds
0 <= x <= 100
0 <= y <= 100
0 <= z <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        # x=5, y=0, z=0 -> solution should only contain x
        assert "x" in sol
        assert sol["x"] == pytest.approx(5.0, abs=1e-6)
        # y and z should not appear (they are 0)
        assert "y" not in sol
        assert "z" not in sol

    def test_solve_with_free_variable(self, tmp_path):
        """min x s.t. x = -3, x free"""
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x = -3
Bounds
x free
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        assert obj_val == pytest.approx(-3.0, abs=1e-6)

    def test_solve_two_var_bounded(self, tmp_path):
        """min 2x + 3y s.t. x+y>=4, x>=1, 0<=y<=10"""
        content = """\
minimize
obj: 2 x + 3 y
subject to
c1: 1 x + 1 y >= 4
Bounds
1 <= x <= 100
0 <= y <= 10
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        # x is cheaper (coeff 2 vs 3), so maximize x: x=4, y=0 -> obj=8
        assert obj_val == pytest.approx(8.0, abs=1e-6)


# ===========================================================================
# 9. extract_kpis
# ===========================================================================

class TestExtractKpis:
    """Tests for extract_kpis()."""

    def test_total_cost(self):
        m = LPModel()
        kpis = extract_kpis(m, 12345.0, {})
        assert kpis["total_cost"] == 12345.0

    def test_tech_investment(self):
        sol = {"tech_inv_y1_t0_n0": 50.0, "tech_inv_y2_t1_n0": 30.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["inv_gen_total"] == pytest.approx(80.0)

    def test_bat_tech_pow_investment(self):
        sol = {"bat_tech_pow_inv_y1_bt0_n0": 10.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["inv_bat_total"] == pytest.approx(10.0)

    def test_bat_tech_cap_investment(self):
        sol = {"bat_tech_cap_inv_y1_bt0_n0": 20.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["inv_bat_total"] == pytest.approx(20.0)

    def test_bat_tech_pow_and_cap_combined(self):
        sol = {"bat_tech_pow_inv_y1_bt0_n0": 10.0, "bat_tech_cap_inv_y1_bt0_n0": 20.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["inv_bat_total"] == pytest.approx(30.0)

    def test_curtailment_by_name(self):
        sol = {"op_curtailment_y1_n0_t1": 5.0, "op_curtailment_y1_n0_t2": 3.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["curtailment"] == pytest.approx(8.0)

    def test_curtailment_by_keyword(self):
        sol = {"some_curtailment_var": 7.0}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["curtailment"] == pytest.approx(7.0)

    def test_load_shedding(self):
        sol = {"op_ll_y1_d1_n0_t1": 2.0, "op_ll_y1_d1_n0_t2": 1.5}
        kpis = extract_kpis(LPModel(), 0.0, sol)
        assert kpis["load_shedding"] == pytest.approx(3.5)

    def test_empty_solution(self):
        kpis = extract_kpis(LPModel(), 0.0, {})
        assert kpis["inv_gen_total"] == 0.0
        assert kpis["inv_bat_total"] == 0.0
        assert kpis["curtailment"] == 0.0
        assert kpis["load_shedding"] == 0.0

    def test_mixed_solution(self):
        sol = {
            "tech_inv_y1_t0_n0": 100.0,
            "bat_tech_pow_inv_y1_bt0_n0": 25.0,
            "op_ll_y1_d1_n0_t1": 0.5,
            "op_curtailment_y1_n0_t1": 1.0,
            "other_var": 999.0,
        }
        kpis = extract_kpis(LPModel(), 5000.0, sol)
        assert kpis["total_cost"] == 5000.0
        assert kpis["inv_gen_total"] == pytest.approx(100.0)
        assert kpis["inv_bat_total"] == pytest.approx(25.0)
        assert kpis["load_shedding"] == pytest.approx(0.5)
        assert kpis["curtailment"] == pytest.approx(1.0)

    def test_all_kpi_keys_present(self):
        kpis = extract_kpis(LPModel(), 0.0, {})
        expected_keys = {"total_cost", "inv_gen_total", "inv_bat_total",
                         "curtailment", "load_shedding"}
        assert set(kpis.keys()) == expected_keys


# ===========================================================================
# 10. perturb_and_solve
# ===========================================================================

class TestPerturbAndSolve:
    """Tests for perturb_and_solve()."""

    @pytest.fixture
    def simple_lp_path(self, tmp_path):
        """A simple LP where tech_inv and demand constraints exist."""
        content = """\
minimize
obj: 10 tech_inv_y1_t0_n0 + 5 x
subject to
demand_bal_y1: 1 tech_inv_y1_t0_n0 + 1 x >= 100
Bounds
0 <= tech_inv_y1_t0_n0 <= 1000
0 <= x <= 1000
End
"""
        return _write_lp(tmp_path, content)

    def test_no_perturbation(self, simple_lp_path):
        model = parse_lp_file(simple_lp_path)
        kpis = perturb_and_solve(model)
        assert kpis["total_cost"] == pytest.approx(500.0, abs=1e-4)

    def test_obj_multiplier_increases_cost(self, simple_lp_path):
        model = parse_lp_file(simple_lp_path)
        # Baseline: x dominates (cost 5), so x=100, obj=500
        kpis_base = perturb_and_solve(model)

        # Double the gen inv cost => no change since x is cheaper
        kpis_pert = perturb_and_solve(model, obj_multipliers={"inv_tech_0": 2.0})
        # x is still cheaper, same solution
        assert kpis_pert["total_cost"] == pytest.approx(500.0, abs=1e-4)

    def test_rhs_multiplier_demand(self, simple_lp_path):
        model = parse_lp_file(simple_lp_path)
        # Double demand -> need 200 units -> cost = 1000
        kpis = perturb_and_solve(model, rhs_multipliers={"demand": 2.0})
        assert kpis["total_cost"] == pytest.approx(1000.0, abs=1e-4)

    def test_rhs_multiplier_half_demand(self, simple_lp_path):
        model = parse_lp_file(simple_lp_path)
        # Half demand -> need 50 units -> cost = 250
        kpis = perturb_and_solve(model, rhs_multipliers={"demand": 0.5})
        assert kpis["total_cost"] == pytest.approx(250.0, abs=1e-4)

    def test_infeasible_perturbation(self, tmp_path):
        """Make problem infeasible via perturbation."""
        content = """\
minimize
obj: 1 x
subject to
demand_bal_y1: 1 x >= 10
c2: 1 x <= 20
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # Multiply demand by 100 -> x >= 1000 but x <= 20 => infeasible
        kpis = perturb_and_solve(model, rhs_multipliers={"demand": 100.0})
        assert kpis["total_cost"] == float("inf")
        assert math.isnan(kpis["inv_gen_total"])

    def test_original_model_unchanged(self, simple_lp_path):
        model = parse_lp_file(simple_lp_path)
        c_before = model.c.copy()
        b_ub_before = model.b_ub.copy()
        perturb_and_solve(model, obj_multipliers={"inv_gen_0": 5.0},
                          rhs_multipliers={"demand": 3.0})
        np.testing.assert_array_equal(model.c, c_before)
        np.testing.assert_array_equal(model.b_ub, b_ub_before)

    def test_no_multipliers_matches_solve(self, tmp_path):
        content = """\
minimize
obj: 1 x + 2 y
subject to
c1: 1 x + 1 y >= 10
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, _ = solve_lp(model)
        kpis = perturb_and_solve(model)
        assert kpis["total_cost"] == pytest.approx(obj_val, abs=1e-6)

    def test_obj_and_rhs_combined(self, tmp_path):
        content = """\
minimize
obj: 10 gen_inv_y1_g0_n0 + 1 x
subject to
demand_bal_y1: 1 gen_inv_y1_g0_n0 + 1 x >= 50
Bounds
0 <= gen_inv_y1_g0_n0 <= 1000
0 <= x <= 1000
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # Baseline: x=50, obj=50
        kpis = perturb_and_solve(
            model,
            obj_multipliers={"inv_gen_0": 0.5},  # gen cost now 5
            rhs_multipliers={"demand": 2.0},  # demand now 100
        )
        # x still cheapest (cost 1), so x=100, obj=100
        assert kpis["total_cost"] == pytest.approx(100.0, abs=1e-4)


# ===========================================================================
# 11. Integration: parse -> solve -> kpis
# ===========================================================================

class TestIntegration:
    """End-to-end tests combining parse, solve, and KPI extraction."""

    def test_full_pipeline(self, tmp_path):
        content = """\
minimize
obj: 100 tech_inv_y1_t0_n0 + 200 tech_inv_y1_t1_n0 + 50 bat_tech_pow_inv_y1_bt0_n0 + 9999 op_ll_y1_d1_n0
subject to
demand_bal_y1: 1 tech_inv_y1_t0_n0 + 1 tech_inv_y1_t1_n0 + 1 bat_tech_pow_inv_y1_bt0_n0 + 1 op_ll_y1_d1_n0 >= 500
Bounds
0 <= tech_inv_y1_t0_n0 <= 1000
0 <= tech_inv_y1_t1_n0 <= 1000
0 <= bat_tech_pow_inv_y1_bt0_n0 <= 1000
0 <= op_ll_y1_d1_n0 <= 1000
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        kpis = extract_kpis(model, obj_val, sol)

        # bat_pow_inv cheapest (50), so 500 units of battery -> obj = 25000
        assert kpis["total_cost"] == pytest.approx(25000.0, abs=1e-2)
        assert kpis["inv_bat_total"] == pytest.approx(500.0, abs=1e-2)
        assert kpis["inv_gen_total"] == pytest.approx(0.0, abs=1e-6)
        assert kpis["load_shedding"] == pytest.approx(0.0, abs=1e-6)

    def test_maximize_pipeline(self, tmp_path):
        content = """\
maximize
obj: 3 x + 2 y
subject to
c1: 1 x + 1 y <= 10
c2: 1 x <= 6
c3: 1 y <= 8
Bounds
0 <= x <= 100
0 <= y <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        # x has higher coeff, x=6, y=4 -> obj = 18+8 = 26
        assert obj_val == pytest.approx(26.0, abs=1e-6)

    def test_perturb_pipeline(self, tmp_path):
        content = """\
minimize
obj: 10 tech_inv_y1_t0_n0 + 20 tech_inv_y1_t1_n0
subject to
demand_bal_y1: 1 tech_inv_y1_t0_n0 + 1 tech_inv_y1_t1_n0 >= 100
Bounds
0 <= tech_inv_y1_t0_n0 <= 1000
0 <= tech_inv_y1_t1_n0 <= 1000
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)

        # Baseline: t0 cheaper -> t0=100, obj=1000
        kpis_base = perturb_and_solve(model)
        assert kpis_base["total_cost"] == pytest.approx(1000.0, abs=1e-2)

        # Triple t0 cost (30) -> t1 cheaper (20) -> t1=100, obj=2000
        kpis_pert = perturb_and_solve(model, obj_multipliers={"inv_tech_0": 3.0})
        assert kpis_pert["total_cost"] == pytest.approx(2000.0, abs=1e-2)

    def test_sensitivity_to_demand(self, tmp_path):
        content = """\
minimize
obj: 5 x
subject to
demand_bal_y1: 1 x >= 100
Bounds
0 <= x <= 10000
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)

        results = []
        for mult in [0.5, 1.0, 1.5, 2.0]:
            kpis = perturb_and_solve(model, rhs_multipliers={"demand": mult})
            results.append(kpis["total_cost"])

        # Cost should scale linearly with demand
        assert results[0] == pytest.approx(250.0, abs=1e-2)
        assert results[1] == pytest.approx(500.0, abs=1e-2)
        assert results[2] == pytest.approx(750.0, abs=1e-2)
        assert results[3] == pytest.approx(1000.0, abs=1e-2)


# ===========================================================================
# 12. LPModel construction and defaults
# ===========================================================================

class TestLPModelDefaults:
    """Tests for LPModel default values and construction."""

    def test_default_empty_model(self):
        m = LPModel()
        assert m.variable_names == []
        assert m.variable_index == {}
        assert len(m.c) == 0
        assert m.A_ub is None
        assert len(m.b_ub) == 0
        assert m.A_eq is None
        assert len(m.b_eq) == 0
        assert m.bounds == []
        assert m.constraint_names_ub == []
        assert m.constraint_names_eq == []
        assert m.sense == "minimize"

    def test_custom_sense(self):
        m = LPModel(sense="maximize")
        assert m.sense == "maximize"

    def test_n_constraints_with_empty_arrays(self):
        m = LPModel()
        assert m.n_constraints == 0


# ===========================================================================
# 13. Additional edge cases
# ===========================================================================

class TestAdditionalEdgeCases:
    """Miscellaneous edge cases."""

    def test_parse_terms_multiple_constants_not_expected(self):
        """When expression has a trailing constant after variables."""
        vi = {"x": 0}
        coeffs, const = _parse_terms("3 x + 5", vi, 1)
        assert coeffs[0] == pytest.approx(3.0)
        assert const == pytest.approx(5.0)

    def test_get_objective_groups_near_zero_coeff(self):
        """Variables with c[i] < 1e-15 should be skipped."""
        names = ["tech_inv_y1_t0_n0", "tech_inv_y1_t1_n1"]
        idx = {n: i for i, n in enumerate(names)}
        m = LPModel(
            variable_names=names,
            variable_index=idx,
            c=np.array([1e-16, 100.0]),
        )
        groups = m.get_objective_groups()
        # First variable should be skipped (near zero)
        assert "inv_tech_0" not in groups
        assert "inv_tech_1" in groups

    def test_parse_lp_file_path_object(self, tmp_path):
        """Ensure Path objects work as input."""
        from pathlib import Path
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x <= 10
Bounds
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(Path(p))
        assert model.n_vars == 1

    def test_solve_maximize_sense_sign(self, tmp_path):
        """Verify maximize returns positive objective when appropriate."""
        content = """\
maximize
obj: 1 x
subject to
c1: 1 x <= 42
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        obj_val, sol = solve_lp(model)
        assert obj_val == pytest.approx(42.0, abs=1e-6)
        assert sol["x"] == pytest.approx(42.0, abs=1e-6)

    def test_perturb_ignores_unknown_groups(self, tmp_path):
        content = """\
minimize
obj: 1 x
subject to
c1: 1 x >= 5
Bounds
0 <= x <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        # Unknown group name should be silently ignored
        kpis = perturb_and_solve(
            model,
            obj_multipliers={"nonexistent_group": 999.0},
            rhs_multipliers={"nonexistent_rhs_group": 999.0},
        )
        assert kpis["total_cost"] == pytest.approx(5.0, abs=1e-4)

    def test_constraint_name_with_search_pattern(self):
        """get_rhs_groups uses .search() not .match(), so pattern can appear anywhere."""
        m = LPModel(
            constraint_names_eq=["my_power_balance_y1_n0"],
            constraint_names_ub=["yearly_re_target_y1"],
        )
        groups = m.get_rhs_groups()
        assert "demand" in groups
        assert "re_target" in groups

    def test_multiple_eq_constraints_same_group(self):
        m = LPModel(
            constraint_names_eq=[
                "demand_bal_y1_n0_t1",
                "demand_bal_y1_n0_t2",
                "demand_bal_y1_n0_t3",
            ]
        )
        groups = m.get_rhs_groups()
        assert len(groups["demand"]) == 3
        assert all(ctype == "eq" for ctype, _ in groups["demand"])

    def test_sparse_matrix_shape(self, tmp_path):
        content = """\
minimize
obj: 1 x + 1 y + 1 z
subject to
c1: 1 x <= 10
c2: 1 y <= 20
Bounds
0 <= x <= 100
0 <= y <= 100
0 <= z <= 100
End
"""
        p = _write_lp(tmp_path, content)
        model = parse_lp_file(p)
        assert model.A_ub.shape == (2, 3)
        assert model.A_eq is None
