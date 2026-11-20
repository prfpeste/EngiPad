"""prop() and haprop(): fluid properties from CoolProp (13.11.2026).

    rho = prop(rho, T, 20'degC, p, 1'bar, Water)
    h   = haprop(h, T, 25'degC, p, 1.01325'bar, phi, 0.6)

The whole module is skipped if CoolProp is not installed, except the
test of the message for exactly that case.
"""

import sys
import time

import pytest

from core.engine import evaluate_code
from tests.conftest import unwrap_unit_markup


def last(code):
    block = evaluate_code(code)[-1]
    return unwrap_unit_markup(block[-1].get("content", block[-1]["type"]))


def is_plot(code):
    return evaluate_code(code)[-1][-1]["type"] == "plot"


class TestWithoutCoolProp:
    def test_clear_message_and_everything_else_works(self, monkeypatch):
        # None in sys.modules makes "import CoolProp.CoolProp" fail.
        monkeypatch.setitem(sys.modules, "CoolProp", None)
        monkeypatch.setitem(sys.modules, "CoolProp.CoolProp", None)
        out = last("a = 2 + 3\nr = prop(ρ, T, 20'degC, p, 1'bar, Water)\n")
        assert "need the package CoolProp, which is not installed" in out
        assert "coolprop==6.8.0" in out
        assert "not installed" in last("haprop(h, T, 25'degC, p, 1'bar, φ, 0.6)\n")
        assert last("a = 2 + 3\n") == "a = 2 + 3 = 5"

    def test_importing_the_application_does_not_import_coolprop(self):
        import subprocess

        code = (
            "import sys, app\n"
            "from core.engine import evaluate_code\n"
            "evaluate_code('a = 2 + 3')\n"
            "print('CoolProp' in sys.modules)\n"
        )
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60
        )
        assert result.stdout.strip().endswith("False"), result.stderr[-500:]


pytest.importorskip("CoolProp")

WATER = "T, 20'degC, p, 1'bar, Water"
AIR_STATE = "T, 25'degC, p, 1.01325'bar, φ, 0.6"


class TestProp:
    @pytest.mark.parametrize("line, shown", [
        (f"prop(ρ, {WATER})", r"998.21\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"),
        (f"prop(rho, {WATER})", r"998.21\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"),
        (f"prop(c_p, {WATER}) | kJ/(kg*K)", r"4.1841\,\frac{\mathrm{kJ}}{\mathrm{kg}\,\mathrm{K}}"),
        (f"prop(cp, {WATER}) | kJ/(kg*K)", r"4.1841\,\frac{\mathrm{kJ}}{\mathrm{kg}\,\mathrm{K}}"),
        (f"prop(λ, {WATER})", r"0.59801\,\frac{\mathrm{W}}{\mathrm{m}\,\mathrm{K}}"),
        (f"prop(η, {WATER}) | mPa*s", r"1.0016\,\mathrm{mPa}\,\mathrm{s}"),
        (f"prop(ν, {WATER}) | mm^2/s", r"1.0034\,\frac{\mathrm{mm}^{2}}{\mathrm{s}}"),
        (f"prop(Pr, {WATER})", "7.0078"),
        ("prop(Pr, T, 300'K, p, 1'bar, Air)", "0.70705"),
        ("prop(c_s, T, 300'K, p, 1'bar, Air)", r"347.32\,\frac{\mathrm{m}}{\mathrm{s}}"),
        # saturation: pressure and vapour quality x
        ("prop(h, p, 1'bar, x, 0, Water) | kJ/kg", r"417.5\,\frac{\mathrm{kJ}}{\mathrm{kg}}"),
        ("prop(h, p, 1'bar, x, 1, Water) | kJ/kg", r"2674.9\,\frac{\mathrm{kJ}}{\mathrm{kg}}"),
        ("prop(T, p, 1'bar, x, 0, Water) | degC", r"99.606\,^\circ\mathrm{C}"),
        ("prop(p, T, 100'degC, x, 0, Water) | bar", r"1.0142\,\mathrm{bar}"),
        ("prop(σ, T, 20'degC, x, 0, Water)", r"0.072817\,\frac{\mathrm{N}}{\mathrm{m}}"),
        # other input pairs
        ("prop(T, h, 300'kJ/kg, p, 1'bar, Water) | degC", r"71.641\,^\circ\mathrm{C}"),
    ])
    def test_values(self, line, shown):
        assert last(f"r =: {line}\n") == f"r = {shown}"

    def test_derived_quantities_are_consistent(self):
        code = (
            f"λ = prop(λ, {WATER}) ; ρ = prop(ρ, {WATER}) ; c = prop(c_p, {WATER})\n"
            f"η = prop(η, {WATER}) ; ν = prop(ν, {WATER}) ; a = prop(a, {WATER})\n"
        )
        assert last(code + "ν == η/ρ\n").endswith(r"\text{true}")
        assert last(code + "a == λ/(ρ*c)\n").endswith(r"\text{true}")
        assert last(code + f"prop(Pr, {WATER}) == ν/a\n").endswith(r"\text{true}")

    def test_formula_display(self):
        assert last(f"r = prop(ρ, {WATER})\n").startswith(
            r"r = \operatorname{prop}\left(\rho, T, 20\,^\circ\mathrm{C}, p, 1\,\mathrm{bar}, \mathrm{Water}\right) = 998.21"
        )

    def test_variables_as_values_and_names_stay_names(self):
        # variables called T and p exist; in the NAME positions the
        # words T and p still mean temperature and pressure
        code = "T = 300'K ; p = 2'bar ; T_1 = 350'K\n"
        assert last(code + "r =: prop(ρ, T, T, p, p, Air)\n") == r"r = 2.3239\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"
        assert last(code + "r =: prop(ρ, T, T_1, p, p, Air)\n") == r"r = 1.9906\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"
        assert last(code + "r =: prop(ρ, p, 2*p/2, T, T_1 - 50'K, Air)\n") == r"r = 2.3239\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"

    def test_result_is_a_normal_quantity(self):
        code = f"ρ = prop(ρ, {WATER}) ; V = 2'L\n"
        assert last(code + "m =: ρ*V | kg\n") == r"m = 1.9964\,\mathrm{kg}"

    @pytest.mark.parametrize("line, message", [
        ("prop(ρ, T, 20, p, 1'bar, Water)", "the value of T must be a quantity with a matching unit"),
        ("prop(ρ, T, 20'degC, p, 1'm, Water)", "the value of p must be a quantity"),
        ("prop(h, p, 1'bar, x, 0.5'm, Water)", "the value of x must be a plain number"),
        ("prop(ρ, T, 20'degC, T, 30'degC, Water)", "two DIFFERENT input quantities"),
        ("prop(ρ, T, 20'degC, p, 1'bar, Wasser)", "'Wasser' is not a fluid CoolProp knows"),
        ("prop(κ, T, 20'degC, p, 1'bar, Water)", "is not a known quantity"),
        ("prop(ρ, λ, 0.6'W/(m*K), p, 1'bar, Water)", "is not a known input quantity"),
        ("prop(ρ, 5, 20'degC, p, 1'bar, Water)", "argument 2 must be a name"),
        ("prop(ρ, T, 20'degC, p, 1'bar)", "prop(quantity, name, value, name, value, fluid) expected"),
        ("prop(ρ, T, 100'K, p, 1'bar, Water)", "CoolProp"),
        ("prop(ρ, T, vec(1, 2), p, 1'bar, Water)", "must be a number or quantity"),
        ("prop(a, b) = a", "built-in name"),
    ])
    def test_errors(self, line, message):
        assert message in last(line + "\n")


class TestHumidAir:
    @pytest.mark.parametrize("line, shown", [
        (f"haprop(h, {AIR_STATE}) | kJ/kg", r"55.574\,\frac{\mathrm{kJ}}{\mathrm{kg}}"),
        (f"haprop(x, {AIR_STATE})", "0.011949"),
        (f"haprop(T_tau, {AIR_STATE}) | degC", r"16.704\,^\circ\mathrm{C}"),
        (f"haprop(T_f, {AIR_STATE}) | degC", r"19.467\,^\circ\mathrm{C}"),
        (f"haprop(v, {AIR_STATE})", r"0.86053\,\frac{\mathrm{m}^{3}}{\mathrm{kg}}"),
        ("haprop(φ, T, 25'degC, p, 1.01325'bar, x, 0.011949)", "0.6"),
    ])
    def test_values(self, line, shown):
        assert last(f"r =: {line}\n") == f"r = {shown}"

    def test_round_trip_through_the_dew_point(self):
        code = f"T_d = haprop(T_tau, {AIR_STATE})\n"
        assert last(code + "r =: haprop(phi, T, 25'degC, p, 1.01325'bar, T_tau, T_d)\n") == "r = 0.6"

    def test_density_from_water_content_and_volume(self):
        code = f"x = haprop(x, {AIR_STATE}) ; v = haprop(v, {AIR_STATE}) ; ρ = haprop(ρ, {AIR_STATE})\n"
        assert last(code + "ρ == (1 + x)/v\n").endswith(r"\text{true}")

    @pytest.mark.parametrize("line, message", [
        ("haprop(h, T, 25'degC, p, 1'bar, φ)", "haprop(quantity, name, value, name, value, name, value) expected"),
        ("haprop(h, T, 25, p, 1'bar, φ, 0.6)", "the value of T must be a quantity"),
        ("haprop(h, T, 25'degC, p, 1'bar, φ, 0.6'm)", "the value of φ must be a plain number"),
        ("haprop(h, T, 25'degC, p, 1'bar, T, 20'degC)", "three DIFFERENT input quantities"),
        ("haprop(ρ_x, T, 25'degC, p, 1'bar, φ, 0.6)", "is not a known quantity"),
    ])
    def test_errors(self, line, message):
        assert message in last(line + "\n")


class TestInFunctionsPlotsAndSolvers:
    def test_property_as_a_function_of_temperature(self):
        code = "ρ_T(t) = prop(ρ, T, t, p, 1'bar, Water)\n"
        assert last(code + "r =: ρ_T(300'K)\n") == r"r = 996.56\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"
        assert is_plot(code + "plot(ρ_T(t), t, 280, 370)\n")

    def test_plot_of_a_saturation_curve_with_gap_outside_the_range(self):
        # above the critical point CoolProp has no value: a gap, not an error
        assert is_plot("p_v(t) = prop(p, T, t, x, 0, Water)\nplot(p_v(t), t, 300, 700)\n")

    def test_plot_of_humid_air(self):
        assert is_plot("x_f(f) = haprop(x, T, 25'degC, p, 1'bar, φ, f)\nplot(x_f(f), f, 0.1, 1)\n")

    def test_nsolve_agrees_with_the_direct_call(self):
        solved = last("r =: nsolve(prop(p, T, t, x, 0, Water) - 2'bar, t, 380'K) | degC\n")
        direct = last("r =: prop(T, p, 2'bar, x, 0, Water) | degC\n")
        assert solved == direct == r"r = 120.21\,^\circ\mathrm{C}"

    def test_nsolve_with_humid_air(self):
        code = f"T_d = haprop(T_tau, {AIR_STATE})\n"
        out = last(code + "f =: nsolve(haprop(T_tau, T, 25'degC, p, 1.01325'bar, φ, f) - T_d, f, 0.5)\n")
        assert out == "f = 0.6"

    def test_nintegrate_agrees_with_the_enthalpy_difference(self):
        integral = last(
            "r =: nintegrate(prop(c_p, T, t, p, 1'bar, Water), (t, 20'degC, 80'degC)) | kJ/kg\n"
        )
        difference = last(
            "r =: prop(h, T, 80'degC, p, 1'bar, Water) - prop(h, T, 20'degC, p, 1'bar, Water) | kJ/kg\n"
        )
        assert integral == difference == r"r = 251.05\,\frac{\mathrm{kJ}}{\mathrm{kg}}"

    def test_integration_and_solving_are_fast(self):
        start = time.time()
        last("r =: nintegrate(prop(c_p, T, t, p, 1'bar, Water), (t, 20'degC, 80'degC))\n")
        last("r =: nsolve(prop(p, T, t, x, 0, Water) - 2'bar, t, 380'K)\n")
        assert time.time() - start < 3


class TestFixedFunctionTables:
    def test_only_the_two_fluid_functions_are_added_to_generated_code(self):
        from mathlib import fluids

        assert set(fluids.NUMPY_FUNCTIONS) == {"engipad_fluid", "engipad_humid_air"}
        assert set(fluids.MPMATH_FUNCTIONS) == {"engipad_fluid", "engipad_humid_air"}

    def test_names_are_never_evaluated(self):
        # "Water" and "T" defined as variables or functions change nothing
        code = "Water = 5 ; f(x) = x\n"
        assert last(code + f"r =: prop(ρ, {WATER})\n").startswith("r = 998.21")

    def test_unknown_function_in_a_value_is_refused_in_plots(self):
        rendered = str(evaluate_code("g(t) = prop(ρ, T, nope(t), p, 1'bar, Water)\nplot(g(t), t, 300, 350)\n"))
        assert "Unknown function 'nope'" in rendered

    def test_toolbar_snippets(self):
        from pathlib import Path

        template = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")  # toolbar: shared page
        assert "insertSnippet('prop( , T, , p, , Water)'" in template
        assert "insertSnippet('haprop( , T, , p, , φ, )'" in template
