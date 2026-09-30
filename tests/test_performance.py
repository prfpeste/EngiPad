"""
Regression tests for the performance work (v1.1.4 -> optimisation session).

Timing assertions use generous limits (the measured old behaviour was
5-7 s, the new one ~0.2 s on the development machine), so they only
fail on a real regression, not on a slow CI machine.
"""

import time

import pytest
import sympy as sp
from sympy.physics.units import kelvin, kilogram, meter, second, joule, watt

from core.context import EvaluationContext
from mathlib.units import (
    BASE_UNITS,
    convert_to_cached,
    has_only_units_and_numbers,
    normalize_numeric_quantity,
)
from tests.conftest import eval_and_format

# Same structure as the exercise that ran into the 10 s timeout
# ("Lernuebung 3.1", plate solution as a series).
_SETUP = ["th_S = 50'degC", "th_0 = 800'degC", "Fo = 0.5", "xi = 0.8"]
_SUM_LINE = (
    "th = th_S + (th_0 - th_S)*sum(4*(-1)^n/((2*n+1)*pi)"
    "*exp(-((2*n+1)*pi/2)^2*Fo)*cos((2*n+1)*pi/2*xi),(n,0,10)) | degC"
)


def _reference_normalize(expr):
    """The ORIGINAL implementation (before the fast path): simplify()
    first, then N()."""
    if has_only_units_and_numbers(expr):
        try:
            expr = convert_to_cached(expr, BASE_UNITS)
        except Exception:
            pass
        try:
            return sp.N(sp.simplify(expr))
        except Exception:
            return sp.N(expr)
    return expr


def _equal(a, b, tol=1e-9):
    if a == b:
        return True
    try:
        return abs(sp.N(a - b)) <= tol * max(1, abs(sp.N(b)))
    except Exception:
        return sp.simplify(a - b) == 0


class TestNormalizeNumericQuantityFastPath:
    @pytest.mark.parametrize("expr", [
        3 * meter,
        sp.Rational(1, 3) * meter,
        sp.sqrt(2) * kilogram,
        2 * meter / meter,
        5 * joule / (kilogram * kelvin),
        sp.pi * watt,
        (3 * meter) * (4 * meter),
        sp.exp(-2) * kilogram * meter / second**2,
        sp.sin(sp.Rational(1, 3)) * meter,
        4 * (-1)**3 / (7 * sp.pi) * sp.exp(-sp.Rational(9, 4)) * kelvin,
        sp.Float("1.5") * meter + sp.Float("0.5") * meter,
        sp.Integer(0) * meter,
        sp.I * meter,
        (1 + 2 * sp.I) * kilogram,
        sp.sqrt(-1) * meter,
    ])
    def test_same_result_as_the_original_implementation(self, expr):
        assert _equal(normalize_numeric_quantity(expr), _reference_normalize(expr))

    def test_non_unit_expressions_are_returned_untouched(self):
        x = sp.Symbol("x")
        assert normalize_numeric_quantity(x + 1) == x + 1

    def test_mixed_unit_sum_keeps_the_original_path(self):
        # m + s is dimensionally meaningless but must not crash or
        # change behaviour.
        expr = 3 * meter + 2 * second
        assert _equal(normalize_numeric_quantity(expr), _reference_normalize(expr))


class TestSlowInputsStayFast:
    def test_long_trig_exp_sum_is_fast(self):
        ctx = EvaluationContext()
        for line in _SETUP:
            ctx.eval_line(line)

        start = time.perf_counter()
        content = eval_and_format(_SUM_LINE, ctx)["content"]
        elapsed = time.perf_counter() - start

        assert r"^\circ\mathrm{C}" in content
        assert elapsed < 1.5, f"sum line took {elapsed:.1f}s (was ~2.5s before the fast path)"

    def test_result_value_of_the_sum_is_correct(self):
        # Independent check: same series computed with plain floats.
        import math

        fo, xi = 0.5, 0.8
        series = sum(
            4 * (-1) ** n / ((2 * n + 1) * math.pi)
            * math.exp(-(((2 * n + 1) * math.pi / 2) ** 2) * fo)
            * math.cos((2 * n + 1) * math.pi / 2 * xi)
            for n in range(11)
        )
        expected = 50 + 750 * series

        ctx = EvaluationContext()
        for line in _SETUP:
            ctx.eval_line(line)
        content = eval_and_format(_SUM_LINE, ctx)["content"]
        shown = float(content.split("=")[-1].split("\\,")[0].strip())
        assert shown == pytest.approx(expected, rel=1e-3)


# ---------------------------------------------------------------------
# unit_to_pretty_latex(): fast (dimension, scale) lookup vs. the
# original one-convert_to-per-entry search
# ---------------------------------------------------------------------
from sympy.physics.units import ampere, hertz, newton, ohm, volt  # noqa: E402

import mathlib.units as units_mod  # noqa: E402

_LOOKUP_UNITS = {
    "N": newton,
    "kN": 1000 * newton,
    "kg m/s^2": kilogram * meter / second**2,
    "W/(m K)": watt / (meter * kelvin),
    "J/(kg K)": joule / (kilogram * kelvin),
    "1/s": 1 / second,
    "Hz": hertz,
    "m^2 float": meter**sp.Float(2.0),
    "A s": ampere * second,
    "V/A": volt / ampere,
    "Ohm": ohm,
    "mA": sp.Rational(1, 1000) * ampere,
    "kOhm": 1000 * ohm,
    "degree": sp.pi / 180,
    "dimensionless": sp.Integer(1),
    "m^3": meter**3,
    "no entry: Ohm/K": ohm / kelvin,
    "no entry: kg m/s": kilogram * meter / second,
}


class TestPrettyUnitLookup:
    @pytest.mark.parametrize("name", sorted(_LOOKUP_UNITS))
    def test_fast_lookup_matches_the_original_search(self, name):
        unit = _LOOKUP_UNITS[name]
        assert units_mod._find_pretty_latex_fast(unit) == units_mod._find_pretty_latex_slow(unit)

    def test_first_entry_wins_like_before(self):
        # Same value under two names (hPa == mbar): PRETTY_UNITS order
        # decides, exactly as in the original loop.
        unit = 100 * units_mod.Pa
        assert units_mod._find_pretty_latex_fast(unit) == units_mod._find_pretty_latex_slow(unit)

    def test_unit_without_entry_falls_back_to_base_units(self):
        unit = ohm / kelvin
        expected = units_mod.latex(convert_to_cached(unit, BASE_UNITS))
        assert units_mod.unit_to_pretty_latex(unit) == expected

    def test_many_lookups_are_fast(self):
        start = time.perf_counter()
        for unit in _LOOKUP_UNITS.values():
            units_mod.unit_to_pretty_latex(unit)
        elapsed = time.perf_counter() - start
        # Original search: ~0.3-1 s PER unit on a cold process.
        assert elapsed < 3.0, f"{len(_LOOKUP_UNITS)} lookups took {elapsed:.1f}s"

    def test_index_covers_every_entry(self):
        index = units_mod._pretty_index()
        assert sum(len(group) for group in index.values()) == len(units_mod.PRETTY_UNITS)
