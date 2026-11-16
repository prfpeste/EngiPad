"""erf/erfc, Bessel functions and the display of compound units
(13.11.2026)."""

import pytest

from core.engine import evaluate_code
from tests.conftest import unwrap_unit_markup


def last(code):
    block = evaluate_code(code)[-1]
    return unwrap_unit_markup(block[-1].get("content", block[-1]["type"]))


def is_plot(code):
    return evaluate_code(code)[-1][-1]["type"] == "plot"


class TestErrorFunction:
    def test_values(self):
        assert last("a = erf(0.5)\n") == r"a = \operatorname{erf}\left(0.5\right) = 0.5205"
        assert last("b =: erfc(0.5)\n") == "b = 0.4795"
        assert last("c =: erf(0.3) + erfc(0.3)\n") == "c = 1"
        assert last("d =: erf(0)\n") == "d = 0"

    def test_argument_must_be_dimensionless(self):
        assert "must be dimensionless" in last("erf(2'm)\n")
        assert "must be dimensionless" in last("erfc(2'K)\n")

    def test_semi_infinite_body(self):
        # theta = erfc(x / (2 sqrt(a t))), all quantities with units
        code = (
            "λ = 0.6'W/(m*K) ; ρ = 998'kg/m^3 ; c_p = 4180'J/(kg*K)\n"
            "a = λ/(ρ*c_p) ; t = 60's ; x_1 = 5'mm\n"
            "Θ =: erfc(x_1/(2*sqrt(a*t)))\n"
        )
        assert last(code) == r"\Theta = 0.22877"

    def test_plot_without_scipy(self):
        assert is_plot("plot(erf(x), x, -2, 2)\n")
        assert is_plot("plot(1 - erfc(x/2), x, 0, 3)\n")
        assert is_plot("f(x) = erf(x)\nplot(f(x), x, 0, 1)\n")

    def test_in_nsolve_and_nintegrate(self):
        assert last("r =: nsolve(erf(x) - 0.5, x, 1)\n") == "r = 0.47694"
        assert last("r =: nintegrate(erfc(u), (u, 0, 2))\n") == "r = 0.56321"


class TestBesselFunctions:
    @pytest.mark.parametrize("line, value", [
        ("besselj(0, 0)", "1"),
        ("besselj(1, 1)", "0.44005"),
        ("bessely(1, 2)", "-0.10703"),
        ("besseli(0, 1)", "1.2661"),
        ("besselk(1, 0.5)", "1.6564"),
    ])
    def test_values(self, line, value):
        assert last(f"r =: {line}\n") == f"r = {value}"

    def test_shown_with_the_textbook_symbol(self):
        assert last("r = besselj(0, 1)\n") == r"r = J_{0}\left(1\right) = 0.7652"
        assert last("r = besselk(1, 0.5)\n").startswith(r"r = K_{1}\left(0.5\right)")

    def test_first_zero_of_j0(self):
        assert last("z =: nsolve(besselj(0, x), x, 2)\n") == "z = 2.4048"

    def test_argument_must_be_dimensionless(self):
        assert "must be dimensionless" in last("besselj(0, 2'm)\n")

    def test_plot_without_scipy(self):
        assert is_plot("plot(besselj(0, x), x, 0, 10)\n")
        assert is_plot("plot(besseli(1, x) + besselk(0, x), x, 0.1, 2)\n")

    def test_whitelist_and_toolbar(self):
        import re
        from pathlib import Path

        from mathlib.functions import (
            COMMON_EVAL_FUNCTIONS, DIMENSIONLESS_ARGUMENT_FUNCTIONS, NUMERIC_FUNCTIONS,
            RESERVED_FUNCTION_NAMES,
        )
        from mathlib.plotting import _SPECIAL_FUNCTIONS

        names = {"erf", "erfc", "besselj", "bessely", "besseli", "besselk"}
        assert names <= set(COMMON_EVAL_FUNCTIONS)
        assert names <= DIMENSIONLESS_ARGUMENT_FUNCTIONS
        # every special function the plot can resolve is a whitelisted
        # one, or one of the two fixed fluid-property functions
        # (mathlib/fluids.py, see tests/test_fluid_properties.py)
        assert set(_SPECIAL_FUNCTIONS) == names | {"engipad_fluid", "engipad_humid_air"}

        template = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        buttons = set(re.findall(r"insertSnippet\('([A-Za-z_][A-Za-z_0-9]*)\(", template))
        assert {"erf", "erfc", "besselj"} <= buttons
        assert not buttons - set(NUMERIC_FUNCTIONS) - RESERVED_FUNCTION_NAMES


class TestTypedUnitIsKept:
    @pytest.mark.parametrize("line, shown", [
        ("q = 5'kN/m", r"q = 5\,\frac{\mathrm{kN}}{\mathrm{m}}"),
        ("c = 2'N/mm", r"c = 2\,\frac{\mathrm{N}}{\mathrm{mm}}"),
        ("ρ = 998'kg/m^3", r"\rho = 998\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"),
        ("g = 9.81'm/s^2", r"g = 9.81\,\frac{\mathrm{m}}{\mathrm{s}^{2}}"),
        ("η = 1'mPa*s", r"\eta = 1\,\mathrm{mPa}\,\mathrm{s}"),
        ("ν = 1'mm^2/s", r"\nu = 1\,\frac{\mathrm{mm}^{2}}{\mathrm{s}}"),
        ("y = 5'Ohm/K", r"y = 5\,\frac{\mathrm{\Omega}}{\mathrm{K}}"),
        # unchanged: units of the preferred list
        ("E = 210'GPa", r"E = 210\,\mathrm{GPa}"),
        ("T = 110'degC", r"T = 110\,^\circ\mathrm{C}"),
        ("L = 2500'mm", r"L = 2500\,\mathrm{mm}"),
    ])
    def test_plain_number_with_unit(self, line, shown):
        assert last(line + "\n") == shown

    def test_value_is_unchanged(self):
        assert last("q = 5'kN/m\nF =: q * 2'm | kN\n") == r"F = 10\,\mathrm{kN}"
        assert last("c = 2'N/mm ; F = 10'N\ns =: F / c | mm\n") == r"s = 5\,\mathrm{mm}"


class TestCompoundUnitsOfResults:
    SETUP = (
        "α = 25'W/(m^2*K) ; λ = 0.6'W/(m*K) ; c_p = 4180'J/(kg*K)\n"
        "A = 2'm^2 ; d = 0.1'm ; ρ = 998'kg/m^3 ; w = 2'm/s ; g = 9.81'm/s^2\n"
        "q = 5'kN/m ; η = 1'mPa*s\n"
    )

    @pytest.mark.parametrize("line, shown", [
        ("r =: d/(λ*A)", r"r = 0.083333\,\frac{\mathrm{K}}{\mathrm{W}}"),
        ("r =: d/λ", r"r = 0.16667\,\frac{\mathrm{m}^{2}\,\mathrm{K}}{\mathrm{W}}"),
        ("r =: α*A", r"r = 50\,\frac{\mathrm{W}}{\mathrm{K}}"),
        ("r =: ρ*2", r"r = 1996\,\frac{\mathrm{kg}}{\mathrm{m}^{3}}"),
        ("r =: w*2", r"r = 4\,\frac{\mathrm{m}}{\mathrm{s}}"),
        ("r =: g*2", r"r = 19.62\,\frac{\mathrm{m}}{\mathrm{s}^{2}}"),
        ("r =: ρ*w*A", r"r = 3992\,\frac{\mathrm{kg}}{\mathrm{s}}"),
        ("r =: w*A", r"r = 4\,\frac{\mathrm{m}^{3}}{\mathrm{s}}"),
        ("r =: q*2", r"r = 1\cdot 10^{4}\,\frac{\mathrm{N}}{\mathrm{m}}"),
        ("r =: η*2", r"r = 0.002\,\mathrm{Pa}\,\mathrm{s}"),
        ("r =: ρ*g", r"r = 9790.4\,\frac{\mathrm{N}}{\mathrm{m}^{3}}"),
        ("r =: 1/300'K", r"r = 0.0033333\,\frac{1}{\mathrm{K}}"),
        ("r =: 2'kg*(0.1'm)^2", r"r = 0.02\,\mathrm{kg}\,\mathrm{m}^{2}"),
        ("r =: w*2'kg", r"r = 4\,\mathrm{N}\,\mathrm{s}"),
        ("r =: 3'cm^4 * 2", r"r = 6\cdot 10^{-8}\,\mathrm{m}^{4}"),
        # already had an entry, must stay as they were
        ("r =: α*A*30'K", r"r = 1500\,\mathrm{W}"),
        ("r =: α*30'K", r"r = 750\,\frac{\mathrm{W}}{\mathrm{m}^{2}}"),
        ("r =: λ/d", r"r = 6\,\frac{\mathrm{W}}{\mathrm{m}^{2}\,\mathrm{K}}"),
        ("r =: w^2/2", r"r = 2\,\frac{\mathrm{J}}{\mathrm{kg}}"),
        ("r =: q*2'm", r"r = 1\cdot 10^{4}\,\mathrm{N}"),
        ("r =: ρ*g*d", r"r = 979.04\,\mathrm{Pa}"),
    ])
    def test_result_unit(self, line, shown):
        assert last(self.SETUP + line + "\n") == shown

    def test_unit_without_any_entry_in_table_style(self):
        assert last("z = 1'kg/(m^4*s^2)\nr =: z*2\n") == (
            r"r = 2\,\frac{\mathrm{kg}}{\mathrm{m}^{4}\,\mathrm{s}^{2}}"
        )
        assert last("r =: sqrt(2'm)\n") == r"r = 1.4142\,\mathrm{m}^{0.5}"

    def test_new_entries_do_not_displace_old_ones(self):
        # Every unit that had an entry before 13.11.2026 still gets it:
        # the new entries were appended, and the first match wins.
        from mathlib import units

        seen = {}
        for candidate, latex_str in units.PRETTY_UNITS:
            signature = units._unit_signature(candidate)
            key = (signature[0], float(signature[1]))
            seen.setdefault(key, latex_str)
        for candidate, latex_str in units.PRETTY_UNITS:
            signature = units._unit_signature(candidate)
            first = seen[(signature[0], float(signature[1]))]
            assert units.unit_to_pretty_latex(candidate) == first


class TestIncompatibleTargetUnitMessage:
    def test_names_both_dimensions(self):
        out = last("M_max = 100'N/mm^2 ; W_x = 200'cm^3\nσ = M_max / W_x | MPa\n")
        assert "not compatible with the requested unit" in out
        assert "the result has dimension [mass/(length**4*time**2)]" in out
        assert "the unit [pressure]" in out

    def test_simple_case(self):
        out = last("L = 2'm\nL | s\n")
        assert "the result has dimension [length], the unit [time]" in out


class TestParenthesisAfterUnit:
    def test_number_in_parentheses_is_a_new_factor(self):
        assert last("J =: 2'kg*(0.1'm)^2 | kg*m^2\n") == r"J = 0.02\,\mathrm{kg}\,\mathrm{m}^{2}"
        assert last("n =: 3'kg*(2 + 1)\n") == r"n = 9\,\mathrm{kg}"
        assert last("v =: 6'm/(2's)\n") == r"v = 3\,\frac{\mathrm{m}}{\mathrm{s}}"

    def test_unit_in_parentheses_is_unchanged(self):
        assert last("k = 5'W/(m*K)\n") == r"k = 5\,\frac{\mathrm{W}}{\mathrm{m}\,\mathrm{K}}"
        assert last("c = 4.18'kJ/(kg*K) | J/(kg*K)\n").endswith(r"4180\,\frac{\mathrm{J}}{\mathrm{kg}\,\mathrm{K}}")
