"""
Tests for the generated unit families in mathlib/units.py
(SI prefixes + additional derived SI units).

The expected factors below are written down independently of the
generator, so a wrong exponent or base unit in _UNIT_FAMILIES fails here.
"""

import pytest
import sympy as sp

from core.context import EvaluationContext
from mathlib.units import (
    DESIRED_UNIT_MAP,
    UNIT_DISPLAY_OVERRIDES,
    UNIT_NS,
    convert_to_cached,
    BASE_UNITS,
)
from rendering.latex_input import to_latex
from parsing.parser import parse
from tests.conftest import eval_and_format

# name -> (factor, reference unit it is a multiple of)
_EXPECTED = {
    # length / time / mass
    "nm": (sp.Rational(1, 10**9), "m"), "um": (sp.Rational(1, 10**6), "m"),
    "dm": (sp.Rational(1, 10), "m"),
    "us": (sp.Rational(1, 10**6), "s"), "ns": (sp.Rational(1, 10**9), "s"),
    "d": (86400, "s"),
    "mg": (sp.Rational(1, 10**6), "kg"), "ug": (sp.Rational(1, 10**9), "kg"),
    # force / energy / power / pressure
    "mN": (sp.Rational(1, 1000), "N"), "MN": (10**6, "N"), "GN": (10**9, "N"),
    "mJ": (sp.Rational(1, 1000), "J"), "TJ": (10**12, "J"),
    "GWh": (3_600_000_000_000, "J"), "TWh": (3_600_000_000_000_000, "J"),
    "uW": (sp.Rational(1, 10**6), "W"), "mW": (sp.Rational(1, 1000), "W"),
    "TW": (10**12, "W"),
    "mPa": (sp.Rational(1, 1000), "Pa"), "hPa": (100, "Pa"),
    "mbar": (100, "Pa"),
    "MNm": (10**6, "J"), "Nmm": (sp.Rational(1, 1000), "J"),
    # electrical
    "uA": (sp.Rational(1, 10**6), "A"), "mA": (sp.Rational(1, 1000), "A"),
    "kA": (1000, "A"),
    "uV": (sp.Rational(1, 10**6), "V"), "mV": (sp.Rational(1, 1000), "V"),
    "kV": (1000, "V"), "MV": (10**6, "V"),
    "mOhm": (sp.Rational(1, 1000), "Ohm"), "kOhm": (1000, "Ohm"),
    "MOhm": (10**6, "Ohm"), "GOhm": (10**9, "Ohm"),
    "kHz": (1000, "Hz"), "MHz": (10**6, "Hz"), "GHz": (10**9, "Hz"),
    # amount / volume
    "mmol": (sp.Rational(1, 1000), "mol"), "umol": (sp.Rational(1, 10**6), "mol"),
    "mL": (sp.Rational(1, 1000), "L"),
    # additional derived SI units (reference: their SI base composition)
    "mF": (sp.Rational(1, 1000), "F"), "uF": (sp.Rational(1, 10**6), "F"),
    "nF": (sp.Rational(1, 10**9), "F"), "pF": (sp.Rational(1, 10**12), "F"),
    "mH": (sp.Rational(1, 1000), "H"), "uH": (sp.Rational(1, 10**6), "H"),
    "mC": (sp.Rational(1, 1000), "C"), "uC": (sp.Rational(1, 10**6), "C"),
    "Ah": (3600, "C"), "mAh": (sp.Rational(36, 10), "C"),
    "mS": (sp.Rational(1, 1000), "S"),
    "mT": (sp.Rational(1, 1000), "T"), "uT": (sp.Rational(1, 10**6), "T"),
}

# Reference units that are themselves new; expressed in base SI.
_BASE_SI = {
    "F": "s^4*A^2/(kg*m^2)", "H": "kg*m^2/(s^2*A^2)", "C": "A*s",
    "S": "s^3*A^2/(kg*m^2)", "T": "kg/(s^2*A)", "Wb": "kg*m^2/(s^2*A)",
}


def _si(unit_expr):
    return sp.simplify(convert_to_cached(unit_expr, BASE_UNITS))


def _reference(ref):
    if ref in UNIT_NS:
        return UNIT_NS[ref]
    if ref in _BASE_SI:
        from sympy.physics.units import ampere, kilogram, meter, second
        return sp.sympify(
            _BASE_SI[ref],
            locals={"A": ampere, "kg": kilogram, "m": meter, "s": second},
        )
    raise KeyError(ref)


class TestGeneratedUnitValues:
    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_value_in_si(self, name):
        factor, ref = _EXPECTED[name]
        assert _si(UNIT_NS[name]) == sp.simplify(factor * _si(_reference(ref)))

    @pytest.mark.parametrize("name", ["F", "H", "C", "S", "T", "Wb"])
    def test_new_derived_base_units_in_si(self, name):
        assert _si(UNIT_NS[name]) == sp.simplify(_si(_reference(name)))

    @pytest.mark.parametrize("micro", ["um", "µm", "μm"])
    def test_all_micro_spellings_are_equal(self, micro):
        assert UNIT_NS[micro] == UNIT_NS["um"]


class TestGeneratedUnitDisplay:
    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_every_new_unit_is_a_valid_target_unit(self, name):
        ctx = EvaluationContext()
        content = eval_and_format(f"x = 1'{name} | {name}", ctx)["content"]
        assert content.startswith("x = 1")
        assert "Unknown" not in content

    def test_desired_unit_result_values(self):
        ctx = EvaluationContext()
        assert eval_and_format("x = 3'TW + 2'mW | TW", ctx)["content"] == (
            r"x = 3\,\mathrm{TW}"
        )
        assert eval_and_format("y = 2'TJ | GJ", ctx)["content"] == (
            r"y = 2000\,\mathrm{GJ}"
        )
        assert eval_and_format("z = 2'Ah | C", ctx)["content"] == (
            r"z = 7200\,\mathrm{C}"
        )

    def test_micro_and_ohm_symbols_in_result(self):
        ctx = EvaluationContext()
        assert eval_and_format("a = 3'mH | uH", ctx)["content"] == (
            r"a = 3000\,\mu\mathrm{H}"
        )
        assert eval_and_format("b = 2'kOhm | kOhm", ctx)["content"] == (
            r"b = 2\,\mathrm{k\Omega}"
        )

    def test_input_echo_uses_display_symbols(self):
        assert to_latex(parse("5'uA")) == r"5\,\engiunit{\mu\mathrm{A}}"
        assert to_latex(parse("5'µm")) == r"5\,\engiunit{\mu\mathrm{m}}"
        assert to_latex(parse("2'kOhm")) == r"2\,\engiunit{\mathrm{k\Omega}}"
        assert to_latex(parse("2'mA")) == r"2\,\engiunit{\mathrm{mA}}"

    def test_computed_derived_units_get_their_name(self):
        ctx = EvaluationContext()
        # 5 A * 3 Ohm -> V (existing), 2 mA * 3 h -> C (new)
        assert eval_and_format("q = 2'mA * 3'h", ctx)["content"].endswith(
            r"21.6\,\mathrm{C}"
        )


class TestUnitTableConsistency:
    def test_every_unit_has_a_desired_unit_entry(self):
        missing = set(UNIT_NS) - set(DESIRED_UNIT_MAP)
        # "R" is the gas constant (a value, not a display unit).
        assert missing <= {"R"}

    def test_display_overrides_match_desired_unit_latex(self):
        for name, latex in UNIT_DISPLAY_OVERRIDES.items():
            if name in DESIRED_UNIT_MAP:
                assert DESIRED_UNIT_MAP[name][0] == latex
