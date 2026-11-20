"""Tables, matrices with units and numerical integration (13.11.2026).

    v[1], M[2][3], M[2, 3]         indices start at 1
    vec(20, 40, 60)'degC           one unit for a whole vector/matrix
    interp(x, y, value)            linear interpolation, no extrapolation
    interp2(x, y, Z, vx, vy)       bilinear interpolation in a map
    nintegrate(f, (x, a, b))       numerical quadrature with units
    matrices are shown rounded, with their unit(s), and accept "| unit"
"""

import math
import time

import pytest
import sympy as sp

from core.engine import evaluate_code
from tests.conftest import unwrap_unit_markup


def last(code):
    block = evaluate_code(code)[-1]
    return unwrap_unit_markup(block[-1].get("content", block[-1]["type"]))


def matrix(*rows):
    body = r"\\".join(" & ".join(row) for row in rows)
    return rf"\left[\begin{{matrix}}{body}\end{{matrix}}\right]"


TABLE = (
    "T = vec(20, 40, 60)'degC\n"
    "ρ = vec(998, 992, 983)'kg/m^3\n"
)
DENSITY = r"\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"
MAP = (
    "x_k = vec(1, 2, 3) ; y_k = vec(10, 20)\n"
    "Z = mat([[1, 2], [3, 4], [5, 6]])\n"
)


class TestIndexingStartsAtOne:
    def test_vector(self):
        assert last("v = vec(5, 6, 7)\nr =: v[1]\n") == "r = 5"
        assert last("v = vec(5, 6, 7)\nr =: v[3]\n") == "r = 7"

    def test_matrix_element_both_spellings(self):
        code = "M = mat([[20, 998], [40, 992], [60, 983]])\n"
        assert last(code + "r =: M[2][2]\n") == "r = 992"
        assert last(code + "r =: M[2, 1]\n") == "r = 40"

    def test_matrix_row(self):
        assert last("M = mat([[1, 2], [3, 4]])\nr =: M[2]\n") == "r = " + matrix(["3", "4"])

    def test_row_and_column_vectors(self):
        assert last("r =: T(vec(5, 6, 7))[2]\n") == "r = 6"

    def test_solutions_of_solve(self):
        code = "sol = solve(Eq(x^2 - 4, 0), x)\n"
        assert last(code + "r =: sol[1]\n") == "r = -2"
        assert last(code + "r =: sol[2]\n") == "r = 2"

    def test_single_solution_accepts_index_one(self):
        assert last("one = solve(Eq(2*x, 4), x)\nr =: one[1]\n") == "r = 2"

    def test_element_keeps_its_unit(self):
        assert last("p = vec(1, 2)'bar\nr =: p[2] | kPa\n") == r"r = 200\,\mathrm{kPa}"

    def test_index_from_a_variable_or_formula(self):
        assert last("v = vec(5, 6, 7) ; i = 2\nr =: v[i + 1]\n") == "r = 7"

    @pytest.mark.parametrize("line, message", [
        ("v[0]", "Indices start at 1"),
        ("v[4]", "Index 4 is out of range (1 to 3)"),
        ("v[-1]", "out of range"),
        ("v[1.5]", "whole number"),
        ("v[k]", "whole number"),
        ("M[3][1]", "Index 3 is out of range (1 to 2)"),
        ("M[1][3]", "Index 3 is out of range (1 to 2)"),
        ("q[1]", "Only vectors, matrices and lists"),
    ])
    def test_errors(self, line, message):
        code = "v = vec(5, 6, 7)\nM = mat([[1, 2], [3, 4]])\n"
        assert message in last(code + line + "\n")


class TestOneUnitForAVector:
    def test_vector_keeps_the_typed_unit(self):
        assert last("p = vec(1, 2)'bar\n").endswith("= " + matrix(["1"], ["2"]) + r"\,\mathrm{bar}")

    def test_celsius_is_converted_per_element(self):
        out = last("T = vec(20, 40, 60)'degC\n")
        assert out.endswith("= " + matrix(["20"], ["40"], ["60"]) + r"\,^\circ\mathrm{C}")
        assert last("T = vec(20, 40)'degC\nr =: T[1]\n") == r"r = 293.15\,\mathrm{K}"

    def test_compound_unit_and_matrix(self):
        out = last("K = mat([[2, -1], [-1, 2]])'kN/mm\n")
        assert out.endswith(
            "= " + matrix(["2", "-1"], ["-1", "2"]) + r"\,\frac{\mathrm{kN}}{\mathrm{mm}}"
        )

    def test_same_values_as_units_on_every_element(self):
        a = last("v = vec(3, 4)'m\nn =: norm(v)\n")
        b = last("v = vec(3'm, 4'm)\nn =: norm(v)\n")
        assert a == b == r"n = 5\,\mathrm{m}"


class TestMatrixDisplay:
    def test_whole_numbers_without_decimals(self):
        assert last("A =: mat([[1, 2], [3, 4]])\n") == "A = " + matrix(["1", "2"], ["3", "4"])

    def test_elements_are_rounded_like_scalars(self):
        assert last("A =: mat([[1/3, 2/3]])\n") == "A = " + matrix(["0.33333", "0.66667"])
        assert last("A = mat([[1, 2], [3, 4]])\nC =: inv(A)\n") == (
            "C = " + matrix(["-2", "1"], ["1.5", "-0.5"])
        )

    def test_common_unit_is_written_once(self):
        assert last("d = vec(3, 4)'m\ne =: d / 2\n") == "e = " + matrix(["1.5"], ["2"]) + r"\,\mathrm{m}"

    def test_zero_fits_the_common_unit(self):
        assert last("F =: vec(1'kN, 0, 2'kN)\n") == (
            "F = " + matrix(["1000"], ["0"], ["2000"]) + r"\,\mathrm{N}"
        )

    def test_mixed_units_stay_at_the_elements(self):
        assert last("F =: vec(1'kN, 2'm, 0)\n") == (
            "F = " + matrix([r"1000\,\mathrm{N}"], [r"2\,\mathrm{m}"], ["0"])
        )

    def test_target_unit(self):
        assert last("p = vec(1, 2)'bar\np | kPa\n") == "p = " + matrix(["100"], ["200"]) + r"\,\mathrm{kPa}"
        assert last("K = mat([[2, -1], [-1, 2]])'kN/mm\nK | N/m\n").endswith(r"\,\frac{\mathrm{N}}{\mathrm{m}}")

    def test_wrong_target_unit(self):
        assert "not compatible with the requested unit" in last("p = vec(1, 2)'bar\np | m\n")

    def test_matrix_with_a_variable_without_a_value(self):
        # used to fail with "unhashable type: 'MutableDenseMatrix'"
        assert last("S =: mat([[a, 1], [2, 3]])\n") == "S = " + matrix(["a", "1"], ["2", "3"])
        assert "all its elements have a value" in last("S = mat([[a, 1], [2, 3]]) | m\n")

    def test_matrix_arithmetic_is_unchanged(self):
        assert last("A = mat([[1, 2], [3, 4]])\nB =: A*A\n") == "B = " + matrix(["7", "10"], ["15", "22"])
        assert last("A = mat([[1, 2], [3, 4]])\nd =: det(A)\n") == "d = -2"


class TestInterp:
    def test_between_two_points(self):
        assert last(TABLE + "r =: interp(T, ρ, 30'degC)\n") == "r = 995" + DENSITY

    def test_value_in_another_unit_of_the_same_dimension(self):
        assert last(TABLE + "r =: interp(T, ρ, 303.15'K)\n") == "r = 995" + DENSITY

    def test_table_points_are_returned_exactly(self):
        assert last(TABLE + "r =: interp(T, ρ, 20'degC)\n") == "r = 998" + DENSITY
        assert last(TABLE + "r =: interp(T, ρ, 60'degC)\n") == "r = 983" + DENSITY

    def test_descending_x_values(self):
        assert last("r =: interp(vec(3, 2, 1), vec(30, 20, 10), 2.5)\n") == "r = 25"

    def test_plain_numbers(self):
        assert last("r =: interp(vec(0, 10), vec(0, 5), 4)\n") == "r = 2"

    def test_table_as_columns_of_one_matrix(self):
        code = "M = mat([[20, 998], [40, 992], [60, 983]])\nMt = T(M)\n"
        assert last(code + "r =: interp(Mt[1], Mt[2], 50)\n") == "r = 987.5"

    @pytest.mark.parametrize("line, message", [
        ("interp(T, ρ, 70'degC)", "outside the table"),
        ("interp(T, ρ, 10'degC)", "not extrapolated"),
        ("interp(T, ρ, 30'm)", "incompatible units"),
        ("interp(T, ρ, 30)", "incompatible units"),
        ("interp(vec(1, 3, 2), vec(1, 2, 3), 2)", "strictly increasing or strictly decreasing"),
        ("interp(vec(1, 1, 2), vec(1, 2, 3), 1)", "strictly increasing or strictly decreasing"),
        ("interp(vec(1, 2), vec(1, 2, 3), 1.5)", "equally many"),
        ("interp(vec(1), vec(1), 1)", "at least two values"),
        ("interp(vec(1, 2), vec(1'm, 2's), 1.5)", "incompatible units"),
        ("interp(vec(1, 2), vec(a, 2), 1.5)", "must be numbers or quantities"),
        ("interp(T, ρ)", "interp(x, y, value) expected"),
    ])
    def test_errors(self, line, message):
        assert message in last(TABLE + line + "\n")

    def test_inside_a_function_and_plotted(self):
        code = (
            "ρ_f(t) = interp(vec(20, 40, 60), vec(998, 992, 983), t)\n"
            "ρ_f(30)\nplot(ρ_f(t), t, 20, 60)\n"
        )
        results = evaluate_code(code)
        assert results[1][0]["content"].endswith("= 995")
        assert results[2][0]["type"] == "plot"


class TestInterp2:
    def test_inside_a_cell(self):
        assert last(MAP + "r =: interp2(x_k, y_k, Z, 2.5, 15)\n") == "r = 4.5"

    def test_corners_and_edges(self):
        assert last(MAP + "r =: interp2(x_k, y_k, Z, 1, 10)\n") == "r = 1"
        assert last(MAP + "r =: interp2(x_k, y_k, Z, 3, 20)\n") == "r = 6"
        assert last(MAP + "r =: interp2(x_k, y_k, Z, 2, 15)\n") == "r = 3.5"

    def test_with_units(self):
        code = (
            "d = vec(10, 20)'mm ; n = vec(1000, 2000)'rpm\n"
            "P = mat([[1, 2], [3, 5]])'kW\n"
        )
        assert last(code + "r =: interp2(d, n, P, 15'mm, 1500'rpm) | kW\n") == r"r = 2.75\,\mathrm{kW}"

    @pytest.mark.parametrize("line, message", [
        ("interp2(y_k, x_k, Z, 15, 2)", "the table has 3 rows and 2 columns"),
        ("interp2(x_k, y_k, Z, 4, 15)", "outside the table"),
        ("interp2(x_k, y_k, Z, 2, 25)", "outside the table"),
        ("interp2(x_k, y_k, x_k, 2, 15)", "the table has 3 rows and 1 columns"),
        ("interp2(x_k, y_k, Z, 2)", "interp2(x, y, Z, value"),
    ])
    def test_errors(self, line, message):
        assert message in last(MAP + line + "\n")


class TestNintegrate:
    def test_plain_number(self):
        assert last("r = nintegrate(x^2, (x, 0, 3))\n") == r"r = \int_{0}^{3} x^{2}\,\mathrm{d}x = 9"

    def test_no_closed_form_needed(self):
        assert last("r =: nintegrate(exp(-t^2), (t, 0, 2))\n") == "r = 0.88208"

    def test_units_of_limits_and_integrand(self):
        code = "q = 5'kN/m ; L = 4'm\n"
        assert last(code + "F =: nintegrate(q, (s, 0'm, L)) | kN\n") == r"F = 20\,\mathrm{kN}"
        assert last(code + "W =: nintegrate(q*s, (s, 0'm, L)) | kNm\n") == r"W = 40\,\mathrm{kNm}"

    def test_limits_in_another_unit(self):
        assert last("c = 2'N/mm\nE =: nintegrate(c*u, (u, 0'mm, 10'mm)) | J\n") == r"E = 0.1\,\mathrm{J}"

    def test_reversed_limits(self):
        assert last("r =: nintegrate(x^2, (x, 3, 0))\n") == "r = -9"

    def test_kinks_and_jumps_are_integrated_exactly_and_quickly(self):
        code = "ρ_f(t) = interp(vec(20, 40, 60), vec(998, 992, 983), t)\n"
        start = time.time()
        assert last(code + "r =: nintegrate(ρ_f(t), (t, 20, 60))\n") == r"r = 3.965\cdot 10^{4}"
        assert last("r(x) = if(x < 1, 0, 1)\ns =: nintegrate(r(x), (x, 0, 3))\n") == "s = 2"
        assert time.time() - start < 3

    def test_step_load_with_units(self):
        code = "q(s) = if(s < 1'm, 2'kN/m, 5'kN/m)\n"
        assert last(code + "F =: nintegrate(q(s), (s, 0'm, 3'm)) | kN\n") == r"F = 12\,\mathrm{kN}"

    @pytest.mark.parametrize("line, message", [
        ("nintegrate(b*x, (x, 0, 1))", "without a value: b"),
        ("x = 2\nnintegrate(x^2, (x, 0, 1))", "must be a variable that has no value yet"),
        ("nintegrate(x^2, (x, 0, k))", "limits must be numbers or quantities"),
        ("nintegrate(x^2, (x, 1'm, 2's))", "incompatible units"),
        ("nintegrate(x^2, x)", "nintegrate(f, (x, a, b)) expected"),
        ("nintegrate(unknownfn(x), (x, 0, 1))", "Unknown function"),
    ])
    def test_errors(self, line, message):
        assert message in last(line + "\n")


class TestVariablesNamedLikeUnits:
    """A SymPy unit keeps its abbreviation as a Symbol inside (second:
    "s", meter: "m", ampere: "A", ...). Replacing a user variable of
    that name with expr.subs() rewrote the unit itself. nsolve() and
    nintegrate() use mathlib.units.substitute_symbols() instead."""

    def test_substitute_symbols_leaves_units_alone(self):
        from mathlib.units import UNIT_NS, substitute_symbols

        s = sp.Symbol("s")
        expr = 5 * UNIT_NS["m"] / UNIT_NS["s"] ** 2 * s
        assert substitute_symbols(expr, {s: sp.Integer(3)}) == 15 * UNIT_NS["m"] / UNIT_NS["s"] ** 2
        # the trap this guards against:
        assert expr.subs(s, 3) != 15 * UNIT_NS["m"] / UNIT_NS["s"] ** 2

    def test_nintegrate_over_a_variable_named_s(self):
        code = "q = 5'kN/m ; L = 4'm\n"
        assert last(code + "F =: nintegrate(q, (s, 0'm, L)) | kN\n") == r"F = 20\,\mathrm{kN}"

    def test_nsolve_for_variables_named_like_units(self):
        code = "M = 2'kg ; g = 9.81'm/s^2\n"
        assert last(code + "h =: nsolve(Eq(M*g*m, 100'J), m, 1'm)\n") == r"h = 5.0968\,\mathrm{m}"
        assert last(code + "t =: nsolve(Eq(g*s^2/2, 10'm), s, 1's)\n") == r"t = 1.4278\,\mathrm{s}"
        assert last("I = 3'A\nR =: nsolve(Eq(A*I, 12'V), A, 1'Ohm) | Ohm\n") == r"R = 4\,\mathrm{\Omega}"


def _magnitude(latex, unit):
    """Number in front of "\\,<unit>" in a result line."""
    head = latex.split(" = ")[-1]
    assert head.endswith(unit), latex
    return float(head[: -len(unit)])


class TestNsolveSmallStartValues:
    """A1 (20.11.2026): with ONE start value mpmath's secant method takes
    x0 + 0.25 as second point, a fixed step in SI base units. For unknowns
    that are small in SI (cm, mV, uF) that point lies where the function is
    flat, and nsolve reported "Could not find root". _solve_one() now gives
    a second point 0.1 % next to the start value."""

    def test_heat_conduction_depth_in_cm(self):
        code = (
            "a = 0.69'mm^2/s ; T_S = 80'degC ; T_0 = 20'degC\n"
            "z_1 =: nsolve(T_S - (T_S - T_0)*erf(z/(2*sqrt(a*1'h))) - 30'degC, z, 5'cm)\n"
        )
        assert _magnitude(last(code), r"\,\mathrm{m}") == pytest.approx(0.09748, rel=1e-3)

    def test_start_value_in_millivolt(self):
        code = (
            "U_T = 25.85'mV ; I_S = 1e-12'A\n"
            "u_D =: nsolve(I_S*(exp(u/U_T) - 1) - 1'mA, u, 500'mV) | mV\n"
        )
        expected = 25.85 * math.log(1e-3 / 1e-12 + 1)
        assert _magnitude(last(code), r"\,\mathrm{mV}") == pytest.approx(expected, rel=1e-4)

    def test_start_value_in_microfarad(self):
        code = (
            "U_0 = 5'V ; R = 1'kOhm ; t = 1'ms\n"
            "C_1 =: nsolve(U_0*exp(-t/(R*C_x)) - 1.84'V, C_x, 2'uF) | uF\n"
        )
        expected = 1 / math.log(5 / 1.84)
        assert _magnitude(last(code), r"\,\mu\mathrm{F}") == pytest.approx(expected, rel=1e-4)

    def test_start_value_zero_keeps_the_default_step(self):
        assert last("r =: nsolve(x^2 - 2, x, 0)\n") == "r = 1.4142"

    def test_negative_start_value(self):
        assert _magnitude(last("z_2 =: nsolve(z^3 + 8'cm^3, z, -1'cm) | cm\n"), r"\,\mathrm{cm}") == pytest.approx(-2, rel=1e-6)

    def test_second_start_point(self):
        from mathlib.functions import _secant_start

        assert _secant_start(0) == 0
        assert _secant_start(sp.Integer(0)) == 0
        first, second = _secant_start(sp.Float("0.05"))
        assert first == sp.Float("0.05")
        assert float(second) == pytest.approx(0.05005)


class TestFunctionNamesAsPlainVariables:
    def test_name_of_a_builtin_function_without_a_value_is_a_variable(self):
        # "T" is also transpose(); without a value it used to resolve to
        # that function object.
        code = "ρ_f(T) = interp(vec(20, 40, 60), vec(998, 992, 983), T)\n"
        assert last(code + "r =: nintegrate(ρ_f(T), (T, 20, 60))\n") == r"r = 3.965\cdot 10^{4}"
        assert last("f = T^2 + 1\n") == "f = T^{2} + 1 = T^{2} + 1"

    def test_the_functions_themselves_still_work(self):
        assert last("A = mat([[1, 2], [3, 4]])\nB =: T(A)\n") == "B = " + matrix(["1", "3"], ["2", "4"])


class TestWhitelistAndToolbar:
    def test_new_functions_are_whitelisted(self):
        from mathlib.functions import COMMON_EVAL_FUNCTIONS

        for name in ("interp", "interp2", "nintegrate"):
            assert callable(COMMON_EVAL_FUNCTIONS[name])

    def test_toolbar_has_the_new_snippets(self):
        from pathlib import Path

        template = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")  # toolbar: shared page
        for snippet in ("nintegrate( , (x, , ))", "interp( , , )", "interp2( , , , , )"):
            assert f"insertSnippet('{snippet}'" in template
