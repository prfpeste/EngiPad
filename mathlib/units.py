import re
from functools import lru_cache

import sympy as sp
from sympy import latex
from sympy.physics.units import (
    A, J, K, L, N, Pa, R, V, W, atm, bar, cd, centi, hour, kg, km,
    liter, milli, minute, mol, m, ohm, s,
    convert_to as sympy_convert_to,
)
from sympy.physics.units.systems.si import SI

VAR_SYMBOL_PREFIX = "__varsym_"
BASE_UNITS = (m, kg, s, A, K, mol, cd)

UNIT_NS = {
    "m": m,
    "mm": milli * m,
    "cm": centi * m,
    "km": km,

    "s": s,
    "ms": milli * s,
    "min": minute,
    "h": hour,

    "kg": kg,
    "g": milli * kg,
    "to": 1000 * kg,

    "N": N,
    "kN": 1000 * N,

    "J": J,
    "kJ": 1000 * J,
    "MJ": 1_000_000 * J,
    "GJ": 1_000_000_000 * J,

    "W": W,
    "kW": 1000 * W,
    "MW": 1_000_000 * W,
    "GW": 1_000_000_000 * W,

    "Pa": Pa,
    "kPa": 1000 * Pa,
    "MPa": 1_000_000 * Pa,
    "bar": bar,
    "atm": atm,

    "A": A,
    "V": V,
    "Ohm": ohm,

    "K": K,
    "degC": K,

    "mol": mol,
    "kmol": 1000 * mol,

    "cd": cd,

    "L": L,
    "liter": liter,

    "Hz": 1 / s,
    "rpm": 1 / minute,

    "deg": sp.pi / 180,
    "rad": 1,

    "R": R,

    "Ws": J,
    "Wh": 3600 * J,
    "kWh": 3_600_000 * J,
    "MWh": 3_600_000_000 * J,

    "GPa": 1_000_000_000 * Pa,

    # Moment/Torque (force * length) -- dimensionally identical to
    # energy (J = N*m), but kept as separate names/labels since
    # engineers write "kNm" for a bending moment, never "kJ" -- same
    # pattern as Ws/Wh above, which are also just J with a different
    # display label. Valid as a literal quantity unit too (e.g.
    # "12'kNm"), unlike "m^4" below which is already expressible via
    # the existing power grammar ("5'm^4") and doesn't need its own
    # entry here.
    "Nm": N * m,
    "kNm": 1000 * N * m,
}

UNIT_NAME_SET = frozenset(UNIT_NS.keys())

UNIT_VALUES = tuple(
    unit for unit in UNIT_NS.values()
    if unit != 1 and not unit.is_number
)

GREEK_VARS = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\vartheta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma",
    "Δ": r"\Delta", "Λ": r"\Lambda", "Θ": r"\Theta", "Ξ": r"\Xi",
    "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi",
    "Ω": r"\Omega",
}

PRETTY_UNITS = (
    (bar, r"\mathrm{bar}"),
    (atm, r"\mathrm{atm}"),
    (1_000_000_000 * Pa, r"\mathrm{GPa}"),
    (1_000_000 * Pa, r"\mathrm{MPa}"),
    (1000 * Pa, r"\mathrm{kPa}"),
    (Pa, r"\mathrm{Pa}"),

    (1000 * N / (centi * m)**2, r"\frac{\mathrm{kN}}{\mathrm{cm}^{2}}"),
    (1000 * N / (m**2), r"\frac{\mathrm{kN}}{\mathrm{m}^{2}}"),
    (N / (milli * m)**2, r"\frac{\mathrm{N}}{\mathrm{mm}^{2}}"),
    (N / (centi * m)**2, r"\frac{\mathrm{N}}{\mathrm{cm}^{2}}"),
    (N / (m**2), r"\frac{\mathrm{N}}{\mathrm{m}^{2}}"),

    (1000 * N, r"\mathrm{kN}"),
    (N, r"\mathrm{N}"),

    (1_000_000_000 * J, r"\mathrm{GJ}"),
    (3_600_000_000 * J, r"\mathrm{MWh}"),
    (1_000_000 * J, r"\mathrm{MJ}"),
    (1000 * J, r"\mathrm{kJ}"),
    (J, r"\mathrm{J}"),

    (1_000_000_000 * W, r"\mathrm{GW}"),
    (1_000_000 * W, r"\mathrm{MW}"),
    (1000 * W, r"\mathrm{kW}"),
    (W, r"\mathrm{W}"),

    (km, r"\mathrm{km}"),
    (m, r"\mathrm{m}"),
    ((centi * m)**2, r"\mathrm{cm}^{2}"),
    ((milli * m)**2, r"\mathrm{mm}^{2}"),
    ((centi * m)**3, r"\mathrm{cm}^{3}"),
    ((milli * m)**3, r"\mathrm{mm}^{3}"),
    (centi * m, r"\mathrm{cm}"),
    (milli * m, r"\mathrm{mm}"),

    (hour, r"\mathrm{h}"),
    (minute, r"\mathrm{min}"),
    (s, r"\mathrm{s}"),
    (milli * s, r"\mathrm{ms}"),

    (1000 * kg, r"\mathrm{t}"),
    (kg, r"\mathrm{kg}"),
    (milli * kg, r"\mathrm{g}"),

    (1000 * mol, r"\mathrm{kmol}"),
    (mol, r"\mathrm{mol}"),

    (L, r"\mathrm{L}"),
    (liter, r"\mathrm{liter}"),

    (K, r"\mathrm{K}"),
    (V, r"\mathrm{V}"),
    (A, r"\mathrm{A}"),
    (ohm, r"\mathrm{\Omega}"),
    (cd, r"\mathrm{cd}"),

    (1 / s, r"\mathrm{Hz}"),
    (1 / minute, r"\mathrm{rpm}"),

    (1000 * W / (m**2 * K), r"\frac{\mathrm{kW}}{\mathrm{m}^{2}\,\mathrm{K}}"),
    (W / (m**2 * K), r"\frac{\mathrm{W}}{\mathrm{m}^{2}\,\mathrm{K}}"),
    (1000 * W / (m * K), r"\frac{\mathrm{kW}}{\mathrm{m}\,\mathrm{K}}"),
    (W / (m * K), r"\frac{\mathrm{W}}{\mathrm{m}\,\mathrm{K}}"),
    (1000 * W / (m**2), r"\frac{\mathrm{kW}}{\mathrm{m}^{2}}"),
    (W / (m**2), r"\frac{\mathrm{W}}{\mathrm{m}^{2}}"),
    (W / m, r"\frac{\mathrm{W}}{\mathrm{m}}"),

    (1000 * J / (kg * K), r"\frac{\mathrm{kJ}}{\mathrm{kg}\,\mathrm{K}}"),
    (J / (kg * K), r"\frac{\mathrm{J}}{\mathrm{kg}\,\mathrm{K}}"),
    (1000 * J / mol, r"\frac{\mathrm{kJ}}{\mathrm{mol}}"),
    (J / mol, r"\frac{\mathrm{J}}{\mathrm{mol}}"),
    (1000 * J / kg, r"\frac{\mathrm{kJ}}{\mathrm{kg}}"),
    (J / kg, r"\frac{\mathrm{J}}{\mathrm{kg}}"),
    (3_600_000 * J / kg, r"\frac{\mathrm{kWh}}{\mathrm{kg}}"),
    (3600 * J / kg, r"\frac{\mathrm{Wh}}{\mathrm{kg}}"),
    (3_600_000 * J / (kg * K), r"\frac{\mathrm{kWh}}{\mathrm{kg}\,\mathrm{K}}"),
    (3600 * J / (kg * K), r"\frac{\mathrm{Wh}}{\mathrm{kg}\,\mathrm{K}}"),
    (1000 * J / (m**3), r"\frac{\mathrm{kJ}}{\mathrm{m}^{3}}"),
    (J / (m**3), r"\frac{\mathrm{J}}{\mathrm{m}^{3}}"),
    (3_600_000 * J / (m**3), r"\frac{\mathrm{kWh}}{\mathrm{m}^{3}}"),
    (3600 * J / (m**3), r"\frac{\mathrm{Wh}}{\mathrm{m}^{3}}"),

    (m**2 / s, r"\frac{\mathrm{m}^{2}}{\mathrm{s}}"),
    (m**2, r"\mathrm{m}^{2}"),
    (m**3, r"\mathrm{m}^{3}"),

    (sp.pi / 180, r"^\circ"),
    (1, r""),
)



_BARE_LITERAL_UNIT_RE = re.compile(r"[-+]?\d+(\.\d+)?'(?P<unit>.+)$")


def bare_literal_unit_key(raw_expr) -> str | None:
    """If raw_expr is JUST a numeric literal with a single unit marker
    (e.g. "110'degC", "-7'kg"), returns the unit name ("degC", "kg",
    ...), otherwise None.

    Used so that a bare literal assignment (no computation) keeps the
    ENTERED unit as the display unit automatically, instead of always
    converting to base SI (which normalize_numeric_quantity()/
    format_scalar_with_unit() would otherwise do). Only relevant when no
    explicit "|unit" was given.
    """

    if raw_expr is None:
        return None

    match = _BARE_LITERAL_UNIT_RE.fullmatch(raw_expr.strip())
    if not match:
        return None

    return match.group("unit").strip()


DESIRED_UNIT_MAP = {
    "m": (r"\mathrm{m}", m, 1),
    "mm": (r"\mathrm{mm}", m, 0.001),
    "cm": (r"\mathrm{cm}", m, 0.01),
    "km": (r"\mathrm{km}", m, 1000),
    "m^2": (r"\mathrm{m}^2", m**2, 1),
    "cm^2": (r"\mathrm{cm}^2", m**2, 0.0001),
    "mm^2": (r"\mathrm{mm}^2", m**2, 0.000001),
    "m^3": (r"\mathrm{m}^3", m**3, 1),
    "cm^3": (r"\mathrm{cm}^3", m**3, 0.000001),
    "mm^3": (r"\mathrm{mm}^3", m**3, 0.000000001),
    "L": (r"\mathrm{L}", m**3, 0.001),
    "liter": (r"\mathrm{liter}", m**3, 0.001),
    "s": (r"\mathrm{s}", s, 1),
    "ms": (r"\mathrm{ms}", s, 0.001),
    "min": (r"\mathrm{min}", s, 60),
    "h": (r"\mathrm{h}", s, 3600),
    "kg": (r"\mathrm{kg}", kg, 1),
    "g": (r"\mathrm{g}", kg, 0.001),
    "to": (r"\mathrm{t}", kg, 1000),
    "mol": (r"\mathrm{mol}", mol, 1),
    "kmol": (r"\mathrm{kmol}", mol, 1000),
    "cd": (r"\mathrm{cd}", cd, 1),
    "A": (r"\mathrm{A}", A, 1),
    "V": (r"\mathrm{V}", V, 1),
    "Ohm": (r"\mathrm{Ohm}", ohm, 1),
    "N": (r"\mathrm{N}", N, 1),
    "kN": (r"\mathrm{kN}", N, 1000),
    "N/m^2": (r"\frac{\mathrm{N}}{\mathrm{m}^{2}}", Pa, 1),
    "N/cm^2": (r"\frac{\mathrm{N}}{\mathrm{cm}^{2}}", Pa, 10_000),
    "N/mm^2": (r"\frac{\mathrm{N}}{\mathrm{mm}^{2}}", Pa, 1_000_000),
    "kN/m^2": (r"\frac{\mathrm{kN}}{\mathrm{m}^{2}}", Pa, 1000),
    "kN/cm^2": (r"\frac{\mathrm{kN}}{\mathrm{cm}^{2}}", Pa, 10_000_000),
    "J": (r"\mathrm{J}", J, 1),
    "kJ": (r"\mathrm{kJ}", J, 1000),
    "MJ": (r"\mathrm{MJ}", J, 1_000_000),
    "GJ": (r"\mathrm{GJ}", J, 1_000_000_000),
    "Ws": (r"\mathrm{Ws}", J, 1),
    "Wh": (r"\mathrm{Wh}", J, 3600),
    "kWh": (r"\mathrm{kWh}", J, 3_600_000),
    "MWh": (r"\mathrm{MWh}", J, 3_600_000_000),
    "W": (r"\mathrm{W}", W, 1),
    "kW": (r"\mathrm{kW}", W, 1000),
    "MW": (r"\mathrm{MW}", W, 1_000_000),
    "GW": (r"\mathrm{GW}", W, 1_000_000_000),
    "Pa": (r"\mathrm{Pa}", Pa, 1),
    "kPa": (r"\mathrm{kPa}", Pa, 1000),
    "MPa": (r"\mathrm{MPa}", Pa, 1_000_000),
    "GPa": (r"\mathrm{GPa}", Pa, 1_000_000_000),
    "bar": (r"\mathrm{bar}", Pa, 100_000),
    "atm": (r"\mathrm{atm}", Pa, 101325),
    "K": (r"\mathrm{K}", K, 1),
    "degC": (r"^\circ\mathrm{C}", K, 1),
    "Hz": (r"\mathrm{Hz}", 1 / s, 1),
    "rpm": (r"\mathrm{rpm}", 1 / s, sp.Rational(1, 60)),
    "deg": (r"^\circ", 1, sp.pi / 180),
    "rad": (r"\mathrm{rad}", 1, 1),
    # Moment/Torque (force * length): same dimension as J, own label
    # (see the UNIT_NS comment above for why).
    "Nm": (r"\mathrm{Nm}", J, 1),
    "kNm": (r"\mathrm{kNm}", J, 1000),
    # Second moment of area (Flaechentraegheitsmoment I), length^4.
    "m^4": (r"\mathrm{m}^4", m**4, 1),
    "cm^4": (r"\mathrm{cm}^4", m**4, 0.01**4),
    "mm^4": (r"\mathrm{mm}^4", m**4, 0.001**4),
    "W/(m^2*K)": (r"\frac{\mathrm{W}}{\mathrm{m}^{2}\,\mathrm{K}}", W / (m**2 * K), 1),
    "kW/(m^2*K)": (r"\frac{\mathrm{kW}}{\mathrm{m}^{2}\,\mathrm{K}}", W / (m**2 * K), 1000),
    "W/(m*K)": (r"\frac{\mathrm{W}}{\mathrm{m}\,\mathrm{K}}", W / (m * K), 1),
    "kW/(m*K)": (r"\frac{\mathrm{kW}}{\mathrm{m}\,\mathrm{K}}", W / (m * K), 1000),
    "W/m": (r"\frac{\mathrm{W}}{\mathrm{m}}", W / m, 1),
    "W/m^2": (r"\frac{\mathrm{W}}{\mathrm{m}^{2}}", W / (m**2), 1),
    "kW/m^2": (r"\frac{\mathrm{kW}}{\mathrm{m}^{2}}", W / (m**2), 1000),
    "J/(kg*K)": (r"\frac{\mathrm{J}}{\mathrm{kg}\,\mathrm{K}}", J / (kg * K), 1),
    "kJ/(kg*K)": (r"\frac{\mathrm{kJ}}{\mathrm{kg}\,\mathrm{K}}", J / (kg * K), 1000),
    "Wh/(kg*K)": (r"\frac{\mathrm{Wh}}{\mathrm{kg}\,\mathrm{K}}", J / (kg * K), 3600),
    "kWh/(kg*K)": (r"\frac{\mathrm{kWh}}{\mathrm{kg}\,\mathrm{K}}", J / (kg * K), 3_600_000),
    "J/kg": (r"\frac{\mathrm{J}}{\mathrm{kg}}", J / kg, 1),
    "kJ/kg": (r"\frac{\mathrm{kJ}}{\mathrm{kg}}", J / kg, 1000),
    "Wh/kg": (r"\frac{\mathrm{Wh}}{\mathrm{kg}}", J / kg, 3600),
    "kWh/kg": (r"\frac{\mathrm{kWh}}{\mathrm{kg}}", J / kg, 3_600_000),
    "J/mol": (r"\frac{\mathrm{J}}{\mathrm{mol}}", J / mol, 1),
    "kJ/mol": (r"\frac{\mathrm{kJ}}{\mathrm{mol}}", J / mol, 1000),
    "J/m^3": (r"\frac{\mathrm{J}}{\mathrm{m}^{3}}", J / (m**3), 1),
    "kJ/m^3": (r"\frac{\mathrm{kJ}}{\mathrm{m}^{3}}", J / (m**3), 1000),
    "Wh/m^3": (r"\frac{\mathrm{Wh}}{\mathrm{m}^{3}}", J / (m**3), 3600),
    "kWh/m^3": (r"\frac{\mathrm{kWh}}{\mathrm{m}^{3}}", J / (m**3), 3_600_000),
}

UNIT_NAMES_REGEX = "|".join(
    sorted((re.escape(name) for name in UNIT_NS), key=len, reverse=True)
)

UNIT_PATTERN = re.compile(
    rf"(?P<num>\d+(\.\d+)?|\b\w+\b)\s*'(?P<unit>{UNIT_NAMES_REGEX})"
)


_BRACE_SUBSCRIPT_RE = re.compile(r"(\w+)_\{([^}]*)\}")
_COMMA_MARKER = "__calcpad_comma__"


def _mangle_brace_subscript(match: re.Match) -> str:
    base = match.group(1)
    content = match.group(2).replace(" ", "")
    mangled_content = content.replace(",", _COMMA_MARKER)
    return f"{base}_{mangled_content}"


def normalize_identifiers(text: str) -> str:
    # "m_{x,1}" -> "m_x__calcpad_comma__1": makes the comma safe for
    # sympify and our own parser (a comma otherwise separates function
    # arguments), without needing to support the braces themselves
    # anywhere else. var_to_latex() turns it back into a real comma for
    # display.
    text = _BRACE_SUBSCRIPT_RE.sub(_mangle_brace_subscript, text)

    return (
        text.replace("ϑ", "θ")
            .replace("∞", "__calcpad_infty__")
            .replace("ⅈ", "__calcpad_imag__")
    )


@lru_cache(maxsize=4096)
def _convert_to_cached(expr, frozen_target):
    real_target = list(frozen_target) if isinstance(frozen_target, tuple) else frozen_target
    return sympy_convert_to(expr, real_target)


def freeze_target(target):
    return tuple(target) if isinstance(target, list) else target


def convert_to_cached(expr, target):
    return _convert_to_cached(expr, freeze_target(target))


def split_magnitude_unit(expr):
    if not hasattr(expr, "has") or not expr.has(*UNIT_VALUES):
        return expr, 1

    if isinstance(expr, sp.Mul):
        mag_factors = []
        unit_factors = []
        for factor in expr.args:
            if factor.has(*UNIT_VALUES):
                unit_factors.append(factor)
            else:
                mag_factors.append(factor)
        magnitude = sp.Mul(*mag_factors) if mag_factors else 1
        unit = sp.Mul(*unit_factors) if unit_factors else 1
        return magnitude, unit

    return 1, expr


def format_magnitude_decimal(mag, rel_tol=1e-4):
    try:
        mag_num = sp.N(mag)
    except Exception:
        mag_num = mag

    if getattr(mag_num, "is_real", False):
        try:
            value = float(mag_num)

            if value == 0:
                return "0"

            absval = abs(value)
            use_sci = absval < 1e-3 or absval >= 1e4
            sig_digits = max(1, int(sp.ceiling(-sp.log(rel_tol, 10))))

            if use_sci:
                sci = f"{value:.{sig_digits - 1}e}"
                base, exp = sci.split("e")
                base = base.rstrip("0").rstrip(".")
                exp_int = int(exp)
                return rf"{base}\cdot 10^{{{exp_int}}}"

            decimals = max(0, sig_digits - 1 - int(sp.floor(sp.log(absval, 10))))
            fixed = f"{value:.{decimals}f}".rstrip("0").rstrip(".")

            return "0" if fixed in {"-0", "-0.0", ""} else fixed

        except Exception:
            pass

    return latex(mag)


def has_only_units_and_numbers(expr):
    free_symbols = getattr(expr, "free_symbols", set())
    return all(sym in UNIT_VALUES for sym in free_symbols)


def strip_units_for_plot(expr):
    """Reduces an expression that may contain unit-bearing quantities
    (e.g. built from Quantity symbols like m, kg, N, ...) down to a
    plain numeric SymPy expression, so it can be lambdify()'d/plotted.

    Converts to SI base units first (so e.g. a value built from "kN"
    becomes a numeric multiple of the base kg*m/s**2 combination),
    then replaces the SEVEN ATOMIC base-unit symbols (BASE_UNITS: m,
    kg, s, A, K, mol, cd) with 1 -- NOT the full UNIT_VALUES list of
    43+ derived unit objects (kN, km, g, mm, ...). Substituting the
    full derived list in one combined .subs() call is unsafe here:
    SymPy resolves a dict-subs as a simultaneous replacement using its
    own internal ordering, and several derived unit objects are
    themselves compound expressions in terms of the same base symbols
    (e.g. "km" is literally 1000*m) -- mixing those into the same
    substitution as the bare "m -> 1" rule was observed to silently
    multiply results by spurious powers of 1000 (verified with a
    18750 - 7500*x_pos case turning into 18750000 - 7500000*x_pos).
    Since convert_to_cached(expr, BASE_UNITS) already guarantees every
    unit in the result is expressed purely via the 7 base symbols, only
    those need to be (and safely can be) substituted.

    The plotted variable itself (kept as the one remaining free
    symbol) is always treated as a plain, unitless number expressed in
    whatever base unit the rest of the formula implies -- matches how
    "plot(f(x), x, 0, 2.5)" is meant to be read: x runs from 0 to 2.5
    in that implied base unit (e.g. meters for a formula built from
    lengths in meters).

    Deliberately does NOT attempt to also report back which unit that
    was (e.g. for an axis label): for the common case where the plot
    variable is combined with a real quantity via "+"/"-" (e.g.
    "F * (L - x)"), that combination is only dimensionally consistent
    AFTER this substitution, not before -- so there is no well-formed
    single "unit" to read off beforehand without already assuming the
    same base-unit convention. Left as a known, deliberate limit (the
    plot's y-axis is labelled generically instead, see
    mathlib/plotting.py).
    """
    try:
        expr_base = convert_to_cached(expr, BASE_UNITS)
    except Exception:
        expr_base = expr

    if not (hasattr(expr_base, "has") and expr_base.has(*UNIT_VALUES)):
        return expr_base

    return expr_base.subs({unit: 1 for unit in BASE_UNITS})


def check_addition_dimensions(left, right):
    """Raises ValueError if `left` and `right` are both fully known
    quantities (has_only_units_and_numbers(), i.e. no still-unassigned
    free variable on either side) but have physically incompatible
    dimensions, e.g. 10'kg + 5'm/s^2.

    Deliberately does NOT fire when either side still contains a real
    free symbol (an unassigned variable): a formula like "F := m + a"
    written before m/a are assigned is a supported, purely symbolic use
    case (see mathlib/sympy_bridge.py's symbolic-placeholder handling)
    and must keep working.

    Additions across different but COMPATIBLE units (e.g. 5'kg +
    200'g, both mass) are unaffected: SymPy already merges same-symbol
    unit factors into a single term while building the Add (see
    normalize_numeric_quantity()), so those never reach this check as
    separate terms in the first place -- only genuinely incompatible
    dimensions survive as a multi-term Add.
    """
    if not (has_only_units_and_numbers(left) and has_only_units_and_numbers(right)):
        return

    dim_left = SI.get_dimensional_expr(left)
    dim_right = SI.get_dimensional_expr(right)

    if dim_left != dim_right:
        raise ValueError(
            f"Cannot add/subtract incompatible units "
            f"(dimension [{dim_left}] vs [{dim_right}])"
        )


def normalize_numeric_quantity(expr):
    try:
        if has_only_units_and_numbers(expr):
            try:
                expr = convert_to_cached(expr, BASE_UNITS)
            except Exception:
                pass

            try:
                return sp.N(sp.simplify(expr))
            except Exception:
                return sp.N(expr)
    except Exception:
        pass

    return expr


def unit_to_pretty_latex(unit):
    for candidate, latex_str in PRETTY_UNITS:
        try:
            ratio = convert_to_cached(unit / candidate, BASE_UNITS)
            if not ratio.has(*UNIT_VALUES) and ratio == 1:
                return latex_str
        except Exception:
            continue

    try:
        unit_base = convert_to_cached(unit, BASE_UNITS)
    except Exception:
        unit_base = unit

    return latex(unit_base)


def format_scalar_with_unit(expr, rel_tol=1e-4):
    try:
        value_for_output = convert_to_cached(expr, BASE_UNITS)
        value_for_output = normalize_numeric_quantity(value_for_output)
    except Exception:
        value_for_output = expr

    mag, unit = split_magnitude_unit(value_for_output)
    mag_str = format_magnitude_decimal(mag, rel_tol=rel_tol)

    try:
        unit_simpl = convert_to_cached(unit, BASE_UNITS)
    except Exception:
        unit_simpl = unit

    if unit == 1 or not getattr(unit_simpl, "has", lambda *args: False)(*UNIT_VALUES):
        return mag_str

    return rf"{mag_str}\,{unit_to_pretty_latex(unit)}"


def _base_symbol_to_latex(base: str) -> str:
    """Renders a single base symbol (no subscript) to LaTeX: a leading
    "Δ" combined with a Greek letter (e.g. "Δω" -> "\\Delta\\omega"),
    a plain Greek letter (GREEK_VARS), or an untouched passthrough for
    anything else (e.g. "Q" stays "Q"). Factored out of var_to_latex()
    so the accent handling below (see _ACCENT_RE) can render an
    accent's base symbol with exactly the same rules as a normal,
    subscript-only variable's base.
    """
    if base.startswith("Δ") and base[1:] in GREEK_VARS:
        return r"\Delta" + GREEK_VARS[base[1:]]
    return GREEK_VARS.get(base, base)


def _render_subscript(base_latex: str, index: str) -> str:
    """Given an already-rendered base symbol's LaTeX and the raw
    (already normalize_identifiers()'d) subscript text after the
    first "_", renders the full "base_{subscript}" LaTeX.

    Handles: a Greek-letter subscript (F_ω), a comma-subscript coming
    from "name_{a,b}" input (see normalize_identifiers()/
    _COMMA_MARKER), a nested "base_sub_more" subscript, and the plain
    text fallback. Factored out of var_to_latex() so the accent
    handling below (Q__dot_max -> "\\dot{Q}_{\\text{max}}") can reuse
    the exact same subscript rules on an already-accented base instead
    of duplicating this logic.
    """
    if index in GREEK_VARS:
        return rf"{base_latex}{GREEK_VARS[index]}"

    if _COMMA_MARKER in index:
        # Originally came from "name_{a,b}" syntax (see
        # normalize_identifiers()). The whole remainder is treated as
        # ONE flat text index -- the generic nesting logic below would
        # get confused by the underscores inside the marker itself.
        restored = index.replace(_COMMA_MARKER, ",")
        safe_index = restored.replace("\\", r"\\").replace("_", r"\_")
        return rf"{base_latex}_{{\text{{{safe_index}}}}}"

    if "_" in index:
        index_base, index_suffix = index.split("_", 1)
        index_base = normalize_identifiers(index_base)
        index_base_latex = GREEK_VARS.get(index_base, index_base)
        safe_suffix = index_suffix.replace("\\", r"\\").replace("_", r"\_")
        return rf"{base_latex}{index_base_latex}_{{\text{{{safe_suffix}}}}}"

    safe_index = index.replace("\\", r"\\").replace("_", r"\_")
    return rf"{base_latex}_{{\text{{{safe_index}}}}}"


# Accent notation: "Q__dot" -> \dot{Q}, "Q__bar" -> \bar{Q}, etc. --
# double underscore (never a single one, which is already the existing
# subscript separator: "Q_dot" keeps meaning a literal text subscript
# "dot", completely unchanged) directly after the base symbol, BEFORE
# any subscript. Combines with everything a plain variable already
# supports: Greek base symbols ("ω__dot" -> \dot{\omega}), and any
# subscript form including the "name_{a,b}" comma syntax
# ("Q__dot_{1,x}" -> \dot{Q}_{\text{1,x}}", since
# normalize_identifiers() mangles the "_{...}" part into the
# _COMMA_MARKER form -- see _BRACE_SUBSCRIPT_RE -- long before this
# regex ever runs, so "__dot_{1,x}" and "__dot_1_x" both arrive here
# already in the same plain-underscore shape).
#
# Purely a rendering-layer feature (var_to_latex() is the single,
# central place every identifier -- LHS variable name or a reference
# inside a formula -- goes through, see rendering/latex_input.py). No
# lexer/parser changes needed: "Q__dot" is already a perfectly valid
# identifier (double underscore is just two underscore characters),
# so it works as a real, reusable SymPy variable name from the start
# -- unlike typing raw LaTeX like "\dot{Q}" directly (see the git
# history around this feature for why that never actually worked).
ACCENT_MACROS = {
    "dot": r"\dot",
    "bar": r"\bar",
    "hat": r"\hat",
    "tilde": r"\tilde",
}

_ACCENT_RE = re.compile(
    r"^(?P<base>[^_]+)__(?P<accent>" + "|".join(ACCENT_MACROS) + r")(?P<rest>_.*)?$"
)


def var_to_latex(var_name: str) -> str:
    if var_name.startswith(VAR_SYMBOL_PREFIX):
        var_name = var_name[len(VAR_SYMBOL_PREFIX):]

    var_name = normalize_identifiers(var_name)

    accent_match = _ACCENT_RE.match(var_name)
    if accent_match:
        base = normalize_identifiers(accent_match.group("base"))
        accent_latex = ACCENT_MACROS[accent_match.group("accent")]
        accented_base = rf"{accent_latex}{{{_base_symbol_to_latex(base)}}}"

        rest = accent_match.group("rest")
        if not rest:
            return accented_base

        index = normalize_identifiers(rest[1:])
        return _render_subscript(accented_base, index)

    if var_name.startswith("Δ") and "_" not in var_name:
        base = var_name[1:]
        if base in GREEK_VARS:
            return r"\Delta" + GREEK_VARS[base]

    if "_" not in var_name:
        return GREEK_VARS.get(var_name, var_name)

    base, index = var_name.split("_", 1)
    base = normalize_identifiers(base)
    index = normalize_identifiers(index)
    base_latex = _base_symbol_to_latex(base)

    return _render_subscript(base_latex, index)


def is_dimensionless_number(val):
    try:
        val_simpl = convert_to_cached(val, BASE_UNITS)
    except Exception:
        val_simpl = val

    if isinstance(val_simpl, (list, tuple, dict)):
        return None

    mag, unit = split_magnitude_unit(val_simpl)
    if unit != 1:
        return None
    if bool(getattr(mag, "is_number", False)):
        return sp.N(mag)
    return None


_LATEX_TEXT_ESCAPES = {
    # Order/completeness matters: a per-character mapping (instead of
    # several .replace() calls in sequence) avoids a "\" newly inserted
    # by an earlier substitution getting escaped again itself. Without
    # this complete escaping, a comment like "50% off" would break the
    # whole LaTeX export (real pdflatex treats "%" as a comment
    # character -- MathJax in the browser is more lenient and displayed
    # the text correctly anyway, which is why the bug wasn't visible
    # just by looking at it in the browser).
    "\\": r"\textbackslash{}",
    "%": r"\%",
    "#": r"\#",
    "$": r"\$",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def make_safe_text_latex(text: str) -> str:
    return "".join(_LATEX_TEXT_ESCAPES.get(ch, ch) for ch in text)


def error_to_latex(prefix: str, exc: Exception) -> str:
    safe = make_safe_text_latex(f"{prefix} -- {exc}")
    return rf"\text{{{safe}}}"
