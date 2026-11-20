"""assume(T > 0) (C1, 20.11.2026).

Without assumptions SymPy must allow every symbol to be complex:
integrate(K*exp(-t/T), (t, 0, oo)) is then a case distinction
("K*T for |arg(T)| < pi/2, otherwise ..."), sqrt(T^2) stays as it is.
"assume(T > 0)" on a line of its own makes T positive from that line
on (only "> 0", decided by the user). For SymPy a positive T is a
DIFFERENT symbol than a plain T: every variable symbol is made by
mathlib.sympy_bridge.variable_symbol(), and values stored before the
assume() line are switched to the positive symbol
(core/context.py::EvaluationContext._assume).
"""

from pathlib import Path

import pytest

from core.engine import evaluate_code
from core.workbook import evaluate_workbook
from tests.conftest import unwrap_unit_markup


def contents(code):
    return [
        unwrap_unit_markup(item.get("content", item["type"]))
        for block in evaluate_code(code)
        for item in block
    ]


def last(code):
    return contents(code)[-1]


STEP = "h_1(t) = K*(1 - exp(-t/T))\nintegrate(K - h_1(t), (t, 0, ∞))\n"


class TestEffect:
    def test_integral_of_a_step_response(self):
        assert last("assume(T > 0)\n" + STEP).endswith(r"\, dt = K \cdot T")

    def test_without_assume_nothing_changes(self):
        out = last(STEP)
        assert r"\begin{cases}" in out and r"\arg" in out

    def test_solve_and_sqrt(self):
        assert last("assume(T > 0)\nsolve(Eq(T^2, 4), T)\n").endswith("= 2")
        assert last("assume(T > 0)\nsqrt(T^2)\n") == r"\sqrt{T^{2}} = T"
        assert last("sqrt(T^2)\n") == r"\sqrt{T^{2}} = \sqrt{T^{2}}"

    def test_complex_solutions_disappear(self):
        assert last("solve(Eq(x^2 + a^2, 0), x)\n").endswith(r"= \left[ - i a, \  i a\right]")
        assert last("assume(a > 0)\nsolve(Eq(x^2 + a^2, 0), x)\n").endswith(r"= \left[ \right]")

    def test_several_conditions_in_one_line(self):
        out = contents("assume(T > 0, ω_d > 0)\nsqrt(T^2*ω_d^2)\n")
        assert out[0] == r"\text{assume: }\ T > 0,\ \omega_{\text{d}} > 0"
        assert out[-1].endswith(r"= T \cdot \omega_{\text{d}}")

    def test_display_of_the_line(self):
        assert contents("assume(T > 0)\n") == [r"\text{assume: }\ T > 0"]


class TestOrderOfLines:
    def test_values_stored_before_are_switched(self):
        out = contents("f = T^2\nassume(T > 0)\ng = f - T^2\nsqrt(f)\n")
        assert out[2] == r"g = f - T^{2} = 0"
        assert out[3] == r"\sqrt{f} = T"

    def test_vector_stored_before(self):
        assert last("v = vec(T, 2*T)\nassume(T > 0)\nsqrt(v[1]^2)\n").endswith("= T")

    def test_lines_before_assume_are_not_affected(self):
        out = contents("sqrt(T^2)\nassume(T > 0)\nsqrt(T^2)\n")
        assert out[0] == r"\sqrt{T^{2}} = \sqrt{T^{2}}"
        assert out[-1] == r"\sqrt{T^{2}} = T"

    def test_value_given_later_wins(self):
        assert last("assume(T > 0)\nT = 5'K\nT*2\n").endswith(r"= 10\,\mathrm{K}")


class TestUnitsAndOtherFeatures:
    def test_variables_named_like_units(self):
        # the unit abbreviations s and K are symbols inside the units
        assert last("assume(s > 0)\nv = 3'm/s\nsqrt(s^2)*v\n").endswith(
            r"= 3\,\frac{\mathrm{m}}{\mathrm{s}} \cdot s"
        )
        assert last("K_1 = 5'K\nf = K*K_1\nassume(K > 0)\nf/K\n").endswith(r"= 5\,\mathrm{K}")

    def test_own_function_with_calculus(self):
        code = "assume(r > 0)\nu(r) = 2*r\nU(r) = integrate(u(r)*exp(-r), (r, 0, ∞))\nU(1)\n"
        assert last(code).endswith("= 2")

    def test_plot_and_nsolve(self):
        assert last("assume(x > 0)\nplot(sqrt(x^2), x, -1, 1)\n") == "plot"
        assert last("assume(T > 0)\nr =: nsolve(T^2 - 2, T, 1)\n") == "r = 1.4142"

    def test_each_sheet_has_its_own_assumptions(self):
        text = "##A\nassume(T > 0)\nsqrt(T^2)\n##B\nsqrt(T^2)\n"
        results = evaluate_workbook(text, 1e-4, None, True, None)
        sheets = {
            name: [unwrap_unit_markup(item["content"]) for block in blocks for item in block]
            for name, blocks in results["all"]
        }
        assert sheets["A"][-1] == r"\sqrt{T^{2}} = T"
        assert sheets["B"] == [r"\sqrt{T^{2}} = \sqrt{T^{2}}"]


class TestErrors:
    @pytest.mark.parametrize("code, message", [
        ("assume(T >= 0)\n", "only accepts conditions of the form name > 0"),
        ("assume(T > 1)\n", "only accepts conditions of the form name > 0"),
        ("assume(T < 0)\n", "only accepts conditions of the form name > 0"),
        ("assume(2*T > 0)\n", "only accepts conditions of the form name > 0"),
        ("assume()\n", "at least one condition"),
        ("T = 3\nassume(T > 0)\n", "T already has a value"),
        ("f(x) = x^2\nassume(f > 0)\n", "f is a function"),
        ("a = assume(T > 0)\n", "must stand on a line of its own"),
        ("assume(T > 0) | K\n", "cannot have a target unit"),
        ("assume(x) = x\n", "built-in name"),
    ])
    def test_messages(self, code, message):
        assert message in last(code)


def test_toolbar_button():
    template = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert "insertSnippet('assume( > 0)'" in template
