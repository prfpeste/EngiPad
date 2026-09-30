"""
Tests for rendering/latex_input.py.

Deliberately calls parsing.parser.parse() + rendering.latex_input.to_latex()
directly instead of going through core/formatter.py::raw_expr_to_latex():
the latter catches any parser/renderer error and silently falls back to
the old regex-based path. A test running through that fallback would
stay green even if the NEW parser/renderer is broken for that case -- it
would just unknowingly return uglier legacy output. Here a regression
should show up as a test failure.
"""

from parsing.parser import parse
from rendering.latex_input import to_latex


def render(text: str, known_vars: frozenset = frozenset()) -> str:
    return to_latex(parse(text), known_vars=known_vars)


# ---------------------------------------------------------------------
# Basic operators / parenthesization
# ---------------------------------------------------------------------
class TestBasicRendering:
    def test_addition_and_multiplication(self):
        assert render("2+3*4") == r"2 + 3 \cdot 4"

    def test_subtraction_is_left_associative_no_parens_needed(self):
        assert render("a-b-c") == r"a - b - c"

    def test_subtraction_needs_parens_on_the_right(self):
        # a - (b - c) != a - b - c -- the right side MUST be parenthesized.
        assert render("a-(b-c)") == r"a - \left(b - c\right)"

    def test_division_renders_as_frac(self):
        assert render("a/b") == r"\frac{a}{b}"

    def test_nested_division_renders_as_nested_frac(self):
        # a/b/c is left-associative: (a/b)/c
        assert render("a/b/c") == r"\frac{\frac{a}{b}}{c}"

    def test_power_simple(self):
        assert render("x^2") == r"x^{2}"

    def test_power_is_right_associative_in_output(self):
        assert render("x^y^z") == r"x^{y^{z}}"

    def test_power_base_needs_parens(self):
        assert render("(a^b)^c") == r"\left(a^{b}\right)^{c}"

    def test_negative_exponent(self):
        assert render("2^-1") == r"2^{-1}"

    def test_unary_minus_binds_weaker_than_power(self):
        assert render("-x^2") == r"-x^{2}"

    def test_unary_minus_on_base_needs_parens(self):
        assert render("(-x)^2") == r"\left(-x\right)^{2}"


# ---------------------------------------------------------------------
# Function calls
# ---------------------------------------------------------------------
class TestFunctionCallRendering:
    def test_known_trig_function(self):
        assert render("sin(x)") == r"\sin\left(x\right)"

    def test_sqrt_has_its_own_macro(self):
        assert render("sqrt(x)") == r"\sqrt{x}"

    def test_unknown_function_uses_operatorname(self):
        assert render("foo(x)") == r"\operatorname{foo}\left(x\right)"

    def test_multi_arg_function(self):
        assert render("atan2(1,1)") == r"\operatorname{atan2}\left(1, 1\right)"

    def test_eq_renders_as_equation(self):
        assert render("Eq(x,5)") == "x = 5"

    def test_exp_of_single_arg_renders_as_e_power(self):
        assert render("exp(x)") == r"e^{x}"

    def test_exp_power_needs_parens(self):
        # exp(x)^2 must become "(e^x)^2", not the invalid "e^x^2".
        assert render("exp(x)^2") == r"\left(e^{x}\right)^{2}"

    def test_exp_with_more_than_one_arg_is_not_special_cased(self):
        # exp() with != 1 argument does NOT get the "e^{x}" special
        # case (which needs exactly 1 argument), but still falls back to
        # the normal \exp macro (exp is in _LATEX_FUNCTION_NAMES), not
        # the generic \operatorname{exp}.
        assert render("exp(x,y)") == r"\exp\left(x, y\right)"


# ---------------------------------------------------------------------
# Identifiers / constant collisions (pi/I/oo/E vs. user variables)
# ---------------------------------------------------------------------
class TestIdentifierRendering:
    def test_pi_constant_without_collision(self):
        assert render("pi") == r"\pi"

    def test_imaginary_unit_without_collision(self):
        assert render("I") == "i"

    def test_infinity_constant_without_collision(self):
        assert render("oo") == r"\infty"

    def test_eulers_number_without_collision(self):
        assert render("E") == "e"

    def test_user_variable_i_is_not_overridden(self):
        # A user variable "I" (e.g. electric current) must NOT appear as
        # the imaginary unit "i" when it's in known_vars.
        assert render("I", known_vars=frozenset({"I"})) == "I"

    def test_infty_placeholder_is_translated(self):
        # Internal placeholder from normalize_identifiers() (replaces
        # "∞" BEFORE parsing) must still render as \infty.
        assert render("__calcpad_infty__") == r"\infty"

    def test_imag_placeholder_is_translated(self):
        assert render("__calcpad_imag__") == "i"

    def test_subscript_identifier(self):
        # Reuses var_to_latex() here -- a spot check, not full coverage
        # (that's mathlib.units' own scope).
        assert render("F_1") == r"F_{\text{1}}"


# ---------------------------------------------------------------------
# Quantity rendering
# ---------------------------------------------------------------------
class TestQuantityRendering:
    def test_simple_unit(self):
        assert render("5'kg") == r"5\,\engiunit{\mathrm{kg}}"

    def test_negative_magnitude(self):
        assert render("-7'kg") == r"-7\,\engiunit{\mathrm{kg}}"

    def test_parenthesized_magnitude_keeps_its_parens(self):
        # "(3+4)'kg" must NOT lose the parentheses around "3+4".
        assert render("(3+4)'kg") == r"\left(3 + 4\right)\,\engiunit{\mathrm{kg}}"

    def test_compound_unit_division(self):
        assert render("5'm^2/s") == r"5\,\engiunit{\frac{\mathrm{m}^{2}}{\mathrm{s}}}"

    def test_compound_unit_with_parens(self):
        assert render("1'W/(m*K)") == r"1\,\engiunit{\frac{\mathrm{W}}{\mathrm{m}\,\mathrm{K}}}"

    def test_quantity_as_power_base_needs_parens(self):
        # "(5'kg)^2" -- the WHOLE quantity must be parenthesized.
        assert render("(5'kg)^2") == r"\left(5\,\engiunit{\mathrm{kg}}\right)^{2}"

    def test_quantity_as_multiplication_factor_needs_no_parens(self):
        assert render("5'kg * 3") == r"5\,\engiunit{\mathrm{kg}} \cdot 3"

    def test_two_separate_quantities_multiplied(self):
        # "2'kg * 3'm" -- two separate quantities.
        assert render("2'kg * 3'm") == r"2\,\engiunit{\mathrm{kg}} \cdot 3\,\engiunit{\mathrm{m}}"

    def test_degc_uses_degree_celsius_symbol(self):
        # "^\circ\mathrm{C}" instead of "\mathrm{degC}".
        assert render("20'degC") == r"20\,\engiunit{^\circ\mathrm{C}}"

    def test_degc_negative(self):
        assert render("-7'degC") == r"-7\,\engiunit{^\circ\mathrm{C}}"

    def test_unit_multiplication_uses_thin_space_not_cdot(self):
        # Within the unit side: "*" -> "\," (PRETTY_UNITS convention),
        # NOT "\cdot" like in normal expressions.
        assert render("1'kg*m") == r"1\,\engiunit{\mathrm{kg}\,\mathrm{m}}"


# ---------------------------------------------------------------------
# Subscript / indexing
# ---------------------------------------------------------------------
class TestSubscriptRendering:
    def test_simple_index(self):
        assert render("sol[0]") == r"sol[0]"

    def test_chained_index(self):
        assert render("M[1][2]") == r"M[1][2]"

    def test_low_precedence_base_gets_parens(self):
        assert render("(a+b)[0]") == r"\left(a + b\right)[0]"

    def test_power_of_indexed_value(self):
        # The whole "sol[0]" is the base of "^", so it gets wrapped --
        # a bit more verbose than strictly necessary (the brackets
        # already group it), but unambiguous.
        assert render("sol[0]^2") == r"\left(sol[0]\right)^{2}"


# ---------------------------------------------------------------------
# Unit display overrides: input echo must show the same symbol as the
# result ("Ohm" typed -> Omega shown, like degC -> ^\circ C)
# ---------------------------------------------------------------------
class TestUnitDisplayOverrides:
    def test_ohm_shown_as_omega(self):
        assert render("100'Ohm") == r"100\,\engiunit{\mathrm{\Omega}}"

    def test_ohm_in_compound_unit(self):
        assert render("0.04'Ohm/K") == (
            r"0.04\,\engiunit{\frac{\mathrm{\Omega}}{\mathrm{K}}}"
        )

    def test_tonne_shown_as_t(self):
        assert render("3'to") == r"3\,\engiunit{\mathrm{t}}"

    def test_deg_shown_as_degree_sign(self):
        assert render("30'deg") == r"30\,^\circ"

    def test_deg_power_has_no_double_superscript(self):
        assert render("5'deg^2") == r"5\,\engiunit{{^\circ}^{2}}"

    def test_input_and_result_overrides_agree(self):
        from mathlib.units import DESIRED_UNIT_MAP, UNIT_DISPLAY_OVERRIDES

        for name, latex in UNIT_DISPLAY_OVERRIDES.items():
            if name in DESIRED_UNIT_MAP:
                assert DESIRED_UNIT_MAP[name][0] == latex
