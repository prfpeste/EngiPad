"""User-defined functions, comparisons and checks (13.11.2026).

    f(x) = x^2 + 1          definition (only the formula is shown)
    y = f(2)                call
    sigma <= sigma_zul | MPa    check:  formula: 180 MPa <= 235 MPa => true
    t = if(a < b, 1, 2)     min max sign round floor ceil
"""

import pytest
import sympy as sp

from core.context import EvaluationContext, find_assignment
from core.engine import evaluate_code
from parsing.ast_nodes import Comparison, FunctionCall, Identifier, Number
from parsing.parser import parse
from tests.conftest import unwrap_unit_markup


def blocks(code):
    return [
        [unwrap_unit_markup(item.get("content", item["type"])) for item in block]
        for block in evaluate_code(code)
    ]


def last(code):
    return blocks(code)[-1][-1]


def items(code):
    """All items of all lines, in order ("a ; b" on one line is one
    merged item, joined with \\quad)."""
    return [item for block in blocks(code) for item in block]


# ---------------------------------------------------------------------
# Assignment detection: the "=" of <= >= == != is not an assignment.
# ---------------------------------------------------------------------
class TestFindAssignment:
    @pytest.mark.parametrize("text, pos", [
        ("a = 5", 2),
        ("a=5", 1),
        ("ok = a <= b", 3),
        ("ok = a == b", 3),
        ("a <= b", -1),
        ("a >= b", -1),
        ("a == b", -1),
        ("a != b", -1),
        ("a < b", -1),
        ("f(x) = x^2", 5),
        ("x + 1", -1),
        ("r = Eq(x, 2)", 2),
    ])
    def test_position(self, text, pos):
        assert find_assignment(text) == pos

    def test_old_assignments_are_unchanged(self):
        assert items("a = 5'm ; b = a*2 ; c = b | cm\n") == [
            r"a = 5\,\mathrm{m} \quad b = a \cdot 2 = 10\,\mathrm{m} \quad c = b = 1000\,\mathrm{cm}"
        ]


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------
class TestComparisonGrammar:
    def test_is_the_weakest_level(self):
        node = parse("a + 1 <= 2*b")
        assert isinstance(node, Comparison) and node.op == "<="
        assert node.left.op == "+" and node.right.op == "*"

    @pytest.mark.parametrize("text, op", [
        ("a < b", "<"), ("a > b", ">"), ("a <= b", "<="), ("a >= b", ">="),
        ("a == b", "=="), ("a != b", "!="),
        ("a ≤ b", "<="), ("a ≥ b", ">="), ("a ≠ b", "!="),
    ])
    def test_operators(self, text, op):
        assert parse(text) == Comparison(op, Identifier("a"), Identifier("b"))

    def test_allowed_as_function_argument_and_in_parentheses(self):
        node = parse("if((a < b), 1, 2)")
        assert isinstance(node, FunctionCall)
        assert node.args[0] == Comparison("<", Identifier("a"), Identifier("b"))
        assert node.args[1] == Number("1")

    def test_chain_is_rejected(self):
        with pytest.raises(ValueError, match="Chained comparisons"):
            parse("a < b < c")

    def test_single_equals_is_still_no_token(self):
        with pytest.raises(ValueError):
            parse("a = b")


# ---------------------------------------------------------------------
# User-defined functions
# ---------------------------------------------------------------------
class TestFunctionDefinition:
    def test_definition_shows_only_the_formula(self):
        assert items("f(x) = x^2 + 1\n") == [r"f\left(x\right) = x^{2} + 1"]

    def test_call_with_a_number(self):
        assert last("f(x) = x^2 + 1\ny = f(2)\n") == r"y = f\left(2\right) = 5"

    def test_several_parameters_and_units(self):
        out = last("g(a, b) = a*b\nW = g(2'm, 3'N)\n")
        assert out == r"W = g\left(2\,\mathrm{m}, 3\,\mathrm{N}\right) = 6\,\mathrm{J}"

    def test_target_unit_at_the_call(self):
        out = last("A(d) = pi*d^2/4\nA(20'mm) | mm^2\n")
        assert out.endswith(r"= 314.16\,\mathrm{mm}^2")

    def test_unit_check_runs_with_the_real_arguments(self):
        out = last("f(x) = x^2 + 1\nz = f(3'm)\n")
        assert "Cannot add/subtract incompatible units" in out

    def test_other_variables_have_their_value_at_the_time_of_the_call(self):
        assert last("a = 2\nh(x) = a*x\na = 3\nk =: h(1)\n") == "k = 3"

    def test_parameter_hides_a_variable_of_the_same_name(self):
        assert last("x = 10\nf(x) = x + 1\nk =: f(2)\n") == "k = 3"
        # ... and does not change it
        assert last("x = 10\nf(x) = x + 1\nk = f(2)\nx\n") == "x = 10"

    def test_nested_calls_and_functions_calling_functions(self):
        assert last("f(x) = x^2\ng(x) = f(x) + 1\nr =: g(f(2))\n") == "r = 17"

    def test_redefinition_replaces_the_function(self):
        assert last("f(x) = x^2\nf(x) = x^3\nr =: f(2)\n") == "r = 8"

    def test_definition_with_the_other_assignment_operators(self):
        assert items("f(x) := x^2\n") == [r"f\left(x\right) = x^{2}"]
        assert items("f(x) =: x^2\n") == [r"f\left(x\right) = x^{2}"]

    def test_greek_and_subscript_parameters(self):
        out = items("u(α, β_1) = α*β_1 ; u(2, 3)\n")[0]
        assert out.startswith(r"u\left(\alpha, \beta_{\text{1}}\right) = \alpha \cdot \beta_{\text{1}}")
        assert out.endswith("= 6")

    def test_function_is_not_stored_as_a_variable(self):
        ctx = EvaluationContext()
        ctx.eval_line("f(x) = x^2")
        assert "f" in ctx.user_functions
        assert "f(x)" not in ctx.var_ns and "f(x)" not in ctx.user_vars
        assert "f" not in ctx.user_vars

    def test_symbolic_use(self):
        assert last("f(x) = x^3\nd = diff(f(x), x)\n").endswith(r"= 3 \cdot x^{2}")
        assert last("f(x) = x^3\ns = solve(Eq(f(x), 8), x)\n").endswith("= 2")
        assert last("f(x) = x^2\ni = integrate(f(t), (t, 0, 3))\n").endswith("= 9")

    def test_vector_argument(self):
        assert last("v = vec(3, 4)\nn(a) = norm(a)\nr =: n(v)\n") == "r = 5"

    @pytest.mark.parametrize("code, message", [
        ("q(x) = q(x) + 1\n", "must not call itself"),
        ("s1(x) = s2(x)\ns2(x) = s1(x)\ns1(1)\n", "nested too deeply"),
        ("sin(x) = x\n", "built-in name"),
        ("if(x) = x\n", "built-in name"),
        ("plot(x) = x\n", "built-in name"),
        ("pi(x) = x\n", "built-in name"),
        ("c = 5\nc(x) = x\n", "already a variable"),
        ("f() = 3\n", "at least one parameter"),
        ("g(a, a) = a\n", "must be different"),
        ("h(1x) = 2\n", "not a valid parameter name"),
        ("h(a+b) = 2\n", "not a valid parameter name"),
        ("k(x) = x | m\n", "cannot have a target unit"),
        ("p(x) = x\np(1, 2)\n", "takes 1 argument(s), 2 given"),
    ])
    def test_errors(self, code, message):
        assert message in last(code)

    def test_functions_are_per_sheet(self):
        from core.workbook import evaluate_workbook

        text = "##A\nf(x) = x^2\na = f(3)\n##B\nb = f(3)\nc = A::a + 1\n"
        outcome = evaluate_workbook(text, active="B")
        shown = [item["content"] for block in outcome["results"] for item in block]
        assert shown[0].endswith(r"f{\left(3 \right)}")  # unknown in sheet B
        assert shown[1].endswith("= 10")


class TestFunctionsInPlotAndNsolve:
    def is_plot(self, code):
        return [item for block in evaluate_code(code) for item in block][-1]["type"] == "plot"

    def test_plot_of_a_function_call(self):
        assert self.is_plot("f(x) = x^2 + 1\nplot(f(x), x, 0, 5)\n")

    def test_plot_uses_the_values_of_other_variables(self):
        assert self.is_plot("F = 3'kN ; L = 2'm\nM(s) = F*(L - s)\nplot(M(s), s, 0, 2)\n")
        # ... also written directly, without a function or a name
        assert self.is_plot("F = 3'kN ; L = 2'm\nplot(F*(L - x), x, 0, 2)\n")

    def test_plot_variable_stays_a_variable_even_if_it_has_a_value(self):
        assert self.is_plot("x = 5\nplot(x^2, x, 0, 3)\n")

    def test_plot_bounds_may_be_variables(self):
        assert self.is_plot("L = 2'm\nf(x) = x^2\nplot(f(x), x, 0, L)\n")

    def test_plot_with_if_inside_a_function(self):
        assert self.is_plot("ramp(x) = if(x < 0, 0, x)\nplot(ramp(x), x, -2, 2)\n")
        assert self.is_plot("q(x) = if(x < 1'm, 2'kN, 0'kN)\nplot(q(x), x, 0, 2)\n")

    def test_plot_value_is_right(self):
        from mathlib.units import strip_units_for_plot

        ctx = EvaluationContext()
        for line in ("F = 7.5'kN", "L = 2.5'm", "M(s) = F*(L - s)"):
            ctx.eval_line(line)
        expr = strip_units_for_plot(ctx.resolve_plot_expression("M(s)", "s"))
        assert abs(float(expr.subs(sp.Symbol("s"), 0)) - 18750.0) < 1e-6

    def test_nsolve_with_a_function(self):
        assert last("f(x) = x^2 + 1\nr =: nsolve(f(x) - 3, x, 1)\n") == "r = 1.4142"

    def test_nsolve_with_a_function_and_units(self):
        out = last("c = 2'N/mm\nF(s) = c*s\ns_1 =: nsolve(F(s) - 10'N, s, 1'mm) | mm\n")
        assert out == r"s_{\text{1}} = 5\,\mathrm{mm}"

    def test_old_style_formula_with_parentheses_in_the_name_still_plots(self):
        # Files written before 13.11.2026 used "M(x_pos) = ..." as a
        # variable NAME and plotted it by that name.
        code = "L = 2.5'm\nF = 7.5'kN\nM(x_pos) = F * (L - x_pos)\nplot(M(x_pos), x_pos, 0, 2.5)\n"
        assert self.is_plot(code)


PIPE = (
    "u(r) = u_max*(1 - r^2/R^2)\n"
    "R = 10'mm ; u_max = 0.2'm/s ; η = 1.0e-3'Pa*s\n"
    "τ(r) = -η*diff(u(r), r)\n"
)


class TestCalculusOverAParameter:
    """A2 (20.11.2026): a parameter that is the VARIABLE of diff/
    integrate/lim/sum/prod in the body ("τ(r) = -η*diff(u(r), r)") used
    to get the argument first, so diff() was asked for a derivative with
    respect to "0.01 m". Now: derive with a placeholder symbol, then put
    the argument in (mathlib/sympy_bridge.py::_call_user_function)."""

    def test_wall_shear_stress(self):
        assert last(PIPE + "τ_w = τ(R) | Pa\n") == r"\tau_{\text{w}} = \tau\left(R\right) = 0.04\,\mathrm{Pa}"

    def test_derivative_keeps_sign_and_unit(self):
        # -40 per second; shown as Hz until the 1/s display (C2) is decided
        out = last(PIPE + "du(r) = diff(u(r), r)\ndu(R)\n")
        assert out.split(" = ")[-1] in (r"-40\,\mathrm{Hz}", r"-40\,\frac{1}{\mathrm{s}}")

    def test_parameter_as_variable_of_diff(self):
        assert last("q(y) = diff(y^3, y)\nq(2)\n").endswith("= 12")

    def test_parameter_as_bound_of_integrate(self):
        assert last("F(b) = integrate(x^2, (x, 0, b))\nF(3)\n").endswith("= 9")

    def test_parameter_as_variable_of_integrate(self):
        assert last("A(x) = integrate(2*x, x)\nA(3)\n").endswith("= 9")

    def test_parameter_as_variable_of_sum_and_lim(self):
        assert last("H(k) = sum(k^2, (k, 1, 4))\nH(7)\n").endswith("= 30")
        assert last("N(x) = lim(sin(x)/x, x, 0)\nN(5)\n").endswith("= 1")

    def test_parameters_named_like_units(self):
        code = "b = 4'm/s^2\nv(s) = diff(b*s^2/2, s)\nm(m) = diff(b*m^2, m)\n"
        assert last(code + "v(3's)\n").endswith(r"= 12\,\frac{\mathrm{m}}{\mathrm{s}}")
        assert last(code + "m(2's)\n").endswith(r"= 16\,\frac{\mathrm{m}}{\mathrm{s}}")

    def test_function_calling_such_a_function(self):
        assert last(PIPE + "τ2(r) = 2*τ(r)\nτ2(R) | Pa\n").endswith(r"= 0.08\,\mathrm{Pa}")

    def test_plot(self):
        code = PIPE + "plot(τ(r), r, 0, 0.01)\n"
        assert [item for block in evaluate_code(code) for item in block][-1]["type"] == "plot"

    def test_nsolve_in_the_body_keeps_working(self):
        code = (
            "k = 2'W/(m*K)\n"
            "T_w(d) = nsolve(k*(x - 20'degC)/d - 500'W/m^2, x, 300'K)\n"
            "T_w(0.1'm) | degC\n"
        )
        assert last(code).endswith(r"= 45\,^\circ\mathrm{C}")

    @pytest.mark.parametrize("code, message", [
        ("f(x) = diff(x^2, x) + 1'kg\nf(2'm)\n", "Cannot add/subtract incompatible units"),
        ("g(x) = exp(diff(x^2, x))\ng(2'm)\n", "must be dimensionless"),
    ])
    def test_unit_checks_after_inserting_the_argument(self, code, message):
        assert message in last(code)

    def test_unknown_function_in_the_body_is_still_refused(self):
        code = "w(x) = diff(unknownfn(x), x)\nplot(w(x), x, 0, 1)\n"
        assert "Unknown function 'unknownfn'" in last(code)

    def test_only_variables_of_calculus_take_the_new_way(self):
        from mathlib.sympy_bridge import _calculus_variables

        def variables(text):
            return _calculus_variables(parse(text))

        assert variables("-η*diff(u(r), r)") == {"r"}
        assert variables("integrate(x^2, (x, 0, b))") == {"x"}
        assert variables("lim(f(x), x, a)") == {"x"}
        assert variables("nsolve(x - d, x, 1)") == frozenset()


# ---------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------
STRESS = "σ = 180'MPa ; σ_zul = 235'MPa\n"


class TestComparisons:
    def test_check_line_with_target_unit(self):
        assert last(STRESS + "σ <= σ_zul | MPa\n") == (
            r"\sigma \le \sigma_{\text{zul}}:\quad 180\,\mathrm{MPa} \le 235\,\mathrm{MPa}"
            r" \;\Rightarrow\; \text{true}"
        )

    def test_check_line_without_target_unit_uses_the_normal_result_unit(self):
        out = last(STRESS + "σ > σ_zul\n")
        assert r"1.8\cdot 10^{8}\,\mathrm{Pa} > 2.35\cdot 10^{8}\,\mathrm{Pa}" in out
        assert out.endswith(r"\text{false}")

    def test_unicode_operators(self):
        assert last(STRESS + "σ ≤ σ_zul | MPa\n") == last(STRESS + "σ <= σ_zul | MPa\n")

    def test_values_are_left_out_if_they_only_repeat_the_formula(self):
        assert last("3 < 5\n") == r"3 < 5 \;\Rightarrow\; \text{true}"

    def test_different_units_of_one_dimension(self):
        assert last("900'mm < 1'm\n").endswith(r"\text{true}")
        assert last("1'kN > 999'N\n").endswith(r"\text{true}")
        assert last("20'degC > 290'K\n").endswith(r"\text{true}")

    def test_different_dimensions_are_an_error(self):
        assert "Cannot compare incompatible units" in last("5'm < 3'kg\n")
        assert "Cannot compare incompatible units" in last("5'm < 3\n")

    def test_plain_zero_compares_with_any_quantity(self):
        assert last("1'bar > 0\n").endswith(r"\text{true}")
        assert last("0 < -1'm\n").endswith(r"\text{false}")

    @pytest.mark.parametrize("line, verdict", [
        ("a == 3", "true"), ("a != 3", "false"), ("a >= 3", "true"),
        ("a <= 3", "true"), ("a > 3", "false"), ("a < 3", "false"),
        ("0.1 + 0.2 == 0.3", "true"), ("1/3*3 == 1", "true"),
        ("1 == 1 + 1e-9", "false"),
    ])
    def test_equality_and_rounding_noise(self, line, verdict):
        assert last(f"a = 3\n{line}\n").endswith(rf"\text{{{verdict}}}")

    def test_result_can_be_stored(self):
        assert items(STRESS + "ok = σ <= σ_zul\nok\n")[1:] == [
            r"ok = \left(\sigma \le \sigma_{\text{zul}}\right) = \text{true}",
            r"ok = \text{true}",
        ]
        assert last("ok =: 3 > 5\n") == r"ok = \text{false}"

    def test_target_unit_on_a_stored_result_is_an_error(self):
        assert "has no unit" in last(STRESS + "ok = σ <= σ_zul | MPa\n")

    def test_wrong_target_unit_is_an_error(self):
        assert "not compatible with the requested unit" in last(STRESS + "σ <= σ_zul | m\n")

    def test_undecided_comparison_is_shown_once(self):
        assert last("y <= 3\n") == r"y \le 3"

    def test_only_scalars_can_be_compared(self):
        assert "Only numbers and quantities" in last("v = vec(1, 2)\nv < 3\n")

    def test_percent_as_target_unit(self):
        out = last(STRESS + "η = σ / σ_zul | %\n")
        assert out == r"\eta = \frac{\sigma}{\sigma_{\text{zul}}} = 76.596\,\%"
        assert "not compatible" in last("L = 2'm\nL | %\n")


class TestIf:
    def test_picks_a_branch(self):
        assert last(STRESS + "t =: if(σ <= σ_zul, 10'mm, 12'mm) | mm\n") == r"t = 10\,\mathrm{mm}"
        assert last(STRESS + "t =: if(σ > σ_zul, 10'mm, 12'mm) | mm\n") == r"t = 12\,\mathrm{mm}"

    def test_only_the_chosen_branch_is_evaluated(self):
        # the other branch would be a unit error
        assert last("a = 1\nr =: if(a > 0, 5, 1'm + 1'kg)\n") == "r = 5"

    def test_stored_condition(self):
        assert last("ok = 3 < 5\nr =: if(ok, 1, 2)\n") == "r = 1"

    def test_in_a_function(self):
        out = items("ramp(x) = if(x < 0, 0, x)\nramp(-2) ; ramp(3)\n")[-1]
        assert out == r"ramp\left(-2\right) = 0 \quad ramp\left(3\right) = 3"

    def test_condition_without_a_value_gives_a_piecewise_formula(self):
        assert r"\begin{cases}" in last("z = if(y < 3, 1, 2)\n")

    @pytest.mark.parametrize("code, message", [
        ("if(3, 1, 2)\n", "must be a condition"),
        ("if(1 < 2, 1)\n", "if(condition, a, b) expected"),
    ])
    def test_errors(self, code, message):
        assert message in last(code)


class TestMinMaxRounding:
    def test_min_max_keep_the_unit_of_the_chosen_argument(self):
        assert last("m =: min(900'mm, 1'm) | mm\n") == r"m = 900\,\mathrm{mm}"
        assert last("m =: max(900'mm, 1'm)\n") == r"m = 1\,\mathrm{m}"

    def test_min_max_of_numbers_and_vectors(self):
        assert last("m =: max(2, 7, 5)\n") == "m = 7"
        assert last("m =: min(vec(4, 1, 9))\n") == "m = 1"

    def test_min_max_need_one_dimension(self):
        assert "cannot compare incompatible units" in last("min(1'm, 2's)\n")

    def test_min_max_symbolic(self):
        assert r"\operatorname{Max}" in last("max(1, y)\n") or r"\max" in last("max(1, y)\n")

    def test_input_display(self):
        assert last("m = max(2, 7)\n") == r"m = \max\left(2, 7\right) = 7"
        assert last("m = floor(3.7)\n") == r"m = \left\lfloor 3.7 \right\rfloor = 3"
        assert last("m = ceil(3.2)\n") == r"m = \left\lceil 3.2 \right\rceil = 4"

    @pytest.mark.parametrize("line, result", [
        ("round(2.5)", "3"), ("round(-2.5)", "-3"), ("round(2.4)", "2"),
        ("round(3.14159, 2)", "3.14"), ("round(1234.5, -2)", "1200"),
        ("floor(-3.2)", "-4"), ("ceil(-3.2)", "-3"),
        ("sign(-3'm)", "-1"), ("sign(2)", "1"), ("sign(0)", "0"),
    ])
    def test_rounding_and_sign(self, line, result):
        assert last(f"r =: {line}\n") == f"r = {result}"

    def test_rounding_needs_a_plain_number(self):
        assert "must be dimensionless" in last("round(5.4'm)\n")
        assert last("L = 5.4'm\nr =: round(L / 1'm) * 1'm\n") == r"r = 5\,\mathrm{m}"

    def test_all_new_functions_are_in_the_whitelist(self):
        from mathlib.functions import COMMON_EVAL_FUNCTIONS, DIMENSIONLESS_ARGUMENT_FUNCTIONS

        for name in ("min", "max", "sign", "round", "floor", "ceil"):
            assert callable(COMMON_EVAL_FUNCTIONS[name])
        assert {"round", "floor", "ceil"} <= DIMENSIONLESS_ARGUMENT_FUNCTIONS

    def test_toolbar_buttons_insert_whitelisted_functions(self):
        import re
        from pathlib import Path

        from mathlib.functions import NUMERIC_FUNCTIONS, RESERVED_FUNCTION_NAMES

        template = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")  # toolbar: shared page
        names = set(re.findall(r"insertSnippet\('([A-Za-z_][A-Za-z_0-9]*)\(", template))
        assert {"min", "max", "round", "if"} <= names
        unknown = names - set(NUMERIC_FUNCTIONS) - RESERVED_FUNCTION_NAMES
        assert not unknown, unknown


class TestSolveKeepsSymbolicSolutions:
    """20.11.2026: solve() kept only solutions SymPy KNEW to be real.
    With a variable without a value in the solution (F/m, sqrt(a)),
    is_real is None, so every symbolic solution was dropped:
    solve(Eq(F, m*a), a) gave []. Now only solutions that are certainly
    not real are dropped (mathlib/solving.py); csolve keeps all."""

    @pytest.mark.parametrize("code, expected", [
        ("solve(Eq(F, m*a), a)\n", r"= \frac{F}{m}"),
        ("solve(Eq(a*x, b), x)\n", r"= \frac{b}{a}"),
        ("solve(Eq(x^2, a), x)\n", r"= \left[ - \sqrt{a}, \  \sqrt{a}\right]"),
        ("solve([Eq(x + y, a), Eq(x - y, b)], [x, y])\n",
         r"= \left\{ x : \frac{a}{2} + \frac{b}{2}, \  y : \frac{a}{2} - \frac{b}{2}\right\}"),
    ])
    def test_symbolic_solutions(self, code, expected):
        assert last(code).endswith(expected)

    def test_overshoot_time_of_a_step_response(self):
        code = (
            "h(t) = K*(1 - exp(-δ*t)*(cos(ω_d*t) + δ/ω_d*sin(ω_d*t)))\n"
            "g_s = simplify(diff(h(t), t))\n"
            "t_p = solve(Eq(g_s, 0), t)\n"
            "t_max = t_p[2]\n"
        )
        assert r"\left[ 0, \  \frac{\pi}{" in items(code)[-2]
        assert r"\pi" in last(code)

    def test_numeric_cases_unchanged(self):
        assert last("solve(Eq(x^2, 5), x)\n").endswith(r"= \left[ - \sqrt{5}, \  \sqrt{5}\right]")
        assert last("solve(Eq(x^3, 1), x)\n").endswith("= 1")
        assert last("m = 2'kg\nsolve(Eq(10'N, m*b), b)\n").endswith(r"= 5\,\frac{\mathrm{m}}{\mathrm{s}^{2}}")

    def test_complex_solutions_only_with_csolve(self):
        assert last("solve(Eq(x^2, -4), x)\n").endswith(r"= \left[ \right]")
        assert last("csolve(Eq(x^2, -4), x)\n").endswith(r"= \left[ - 2 i, \  2 i\right]")
