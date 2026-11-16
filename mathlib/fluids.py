"""Fluid properties from CoolProp: prop(...) and haprop(...).

    rho = prop(rho, T, 20'degC, p, 1'bar, Water)
    h   = haprop(h, T, 25'degC, p, 1'bar, phi, 0.6)        (humid air)

Same order as CoolProp's PropsSI / HAPropsSI (and its Excel add-in):
the wanted quantity first, then name and value in turn, for prop() the
fluid last. Names are written WITHOUT quotation marks (the language has
no strings); mathlib/sympy_bridge.py reads them from the syntax tree
and never evaluates them, so a variable called "T" or "p" does not get
in the way.

CoolProp is imported only when one of the two functions is actually
called (the import costs about 0.2 s, and every request runs in a
fresh process). Without the package everything else works and these
functions say what is missing.

All values go to CoolProp in SI base units and come back with their SI
unit attached. Inputs are checked against the dimension their name
asks for.

A value that is still a variable without a value (inside a function
that is plotted, solved or integrated) gives a symbolic call
engipad_fluid(...) / engipad_humid_air(...): two real sp.Function
classes whose arguments are only numbers and the variable. Names and
the fluid are passed as integer codes. NUMPY_FUNCTIONS / MPMATH_FUNCTIONS
are the matching implementations for sp.lambdify() (plot) and
sp.nsolve(): a fixed table with exactly these two names.
"""

from __future__ import annotations

import numpy as np
import sympy as sp

from mathlib.units import (
    BASE_UNITS,
    UNIT_NS,
    UNIT_VALUES,
    convert_to_cached,
    has_only_units_and_numbers,
    split_magnitude_unit,
    strip_units_for_plot,
)

COOLPROP_VERSION_HINT = "coolprop==6.8.0"

_K, _Pa, _kg, _m, _J, _W, _N, _s = (UNIT_NS[n] for n in ("K", "Pa", "kg", "m", "J", "W", "N", "s"))
_ONE = sp.Integer(1)

# Spellings that mean the same quantity.
_ALIASES = {
    "ρ": "rho", "λ": "lambda", "η": "eta", "ν": "nu", "β": "beta", "σ": "sigma",
    "φ": "phi", "ϕ": "phi", "cp": "c_p", "cv": "c_v", "P": "p",
}

# name -> (CoolProp key or None if computed here, SI unit)
FLUID_QUANTITIES = {
    "T": ("T", _K),
    "p": ("P", _Pa),
    "rho": ("D", _kg / _m**3),
    "h": ("H", _J / _kg),
    "s": ("S", _J / (_kg * _K)),
    "u": ("U", _J / _kg),
    "x": ("Q", _ONE),                       # vapour quality
    "c_p": ("C", _J / (_kg * _K)),
    "c_v": ("O", _J / (_kg * _K)),
    "lambda": ("L", _W / (_m * _K)),        # thermal conductivity
    "eta": ("V", _Pa * _s),                 # dynamic viscosity
    "nu": (None, _m**2 / _s),               # kinematic viscosity = eta / rho
    "Pr": ("PRANDTL", _ONE),
    "a": (None, _m**2 / _s),                # thermal diffusivity = lambda / (rho c_p)
    "beta": ("ISOBARIC_EXPANSION_COEFFICIENT", _ONE / _K),
    "sigma": ("I", _N / _m),                # surface tension
    "c_s": ("A", _m / _s),                  # speed of sound
}
FLUID_INPUTS = ("T", "p", "rho", "h", "s", "u", "x")

# Humid air; h, s, v and c_p are per kg of DRY air, as usual.
HUMID_AIR_QUANTITIES = {
    "T": ("T", _K),
    "p": ("P", _Pa),
    "phi": ("R", _ONE),                     # relative humidity 0..1
    "x": ("W", _ONE),                       # water content kg water / kg dry air
    "T_tau": ("D", _K),                     # dew point
    "T_f": ("B", _K),                       # wet-bulb temperature
    "h": ("H", _J / _kg),
    "s": ("S", _J / (_kg * _K)),
    "v": ("V", _m**3 / _kg),
    "c_p": ("C", _J / (_kg * _K)),
    "lambda": ("K", _W / (_m * _K)),
    "eta": ("M", _Pa * _s),
    "rho": (None, _kg / _m**3),             # (1 + x) / v
    "p_D": ("P_w", _Pa),                    # partial pressure of water vapour
}
HUMID_AIR_INPUTS = ("T", "p", "phi", "x", "T_tau", "T_f", "h")

_FLUID_NAMES = list(FLUID_QUANTITIES)
_HUMID_AIR_NAMES = list(HUMID_AIR_QUANTITIES)

# Fluids used so far in this process, for the integer code of the
# symbolic form.
_FLUIDS: list[str] = []


def _coolprop():
    try:
        import CoolProp.CoolProp as module
    except ImportError:
        raise ValueError(
            "prop() and haprop() need the package CoolProp, which is not "
            f"installed (pip install {COOLPROP_VERSION_HINT})."
        )
    return module


def _humid_air():
    _coolprop()
    from CoolProp.HumidAirProp import HAPropsSI
    return HAPropsSI


def _short(exc: Exception) -> str:
    text = str(exc).split(" : ")[0].split("::")[0].strip()
    return text[:200] or exc.__class__.__name__


# ---------------------------------------------------------------------
# Plain-number core (SI base units in, SI out)
# ---------------------------------------------------------------------
def fluid_value(output: str, name1: str, value1: float, name2: str, value2: float, fluid: str) -> float:
    props = _coolprop().PropsSI
    key1, key2 = FLUID_QUANTITIES[name1][0], FLUID_QUANTITIES[name2][0]

    def get(name):
        return props(FLUID_QUANTITIES[name][0], key1, value1, key2, value2, fluid)

    try:
        if output == "nu":
            result = get("eta") / get("rho")
        elif output == "a":
            result = get("lambda") / (get("rho") * get("c_p"))
        else:
            result = get(output)
    except ValueError as exc:
        raise ValueError(f"CoolProp: {_short(exc)}")

    if not np.isfinite(result):
        raise ValueError(
            f"CoolProp gives no value for '{output}' of {fluid} at this state "
            "(e.g. inside the two-phase region)."
        )
    return float(result)


def humid_air_value(output: str, name1, value1, name2, value2, name3, value3) -> float:
    ha = _humid_air()
    pairs = (
        HUMID_AIR_QUANTITIES[name1][0], value1,
        HUMID_AIR_QUANTITIES[name2][0], value2,
        HUMID_AIR_QUANTITIES[name3][0], value3,
    )
    try:
        if output == "rho":
            result = (1 + ha("W", *pairs)) / ha("V", *pairs)
        else:
            result = ha(HUMID_AIR_QUANTITIES[output][0], *pairs)
    except ValueError as exc:
        raise ValueError(f"CoolProp: {_short(exc)}")

    if not np.isfinite(result):
        raise ValueError(f"CoolProp gives no value for '{output}' of humid air at this state.")
    return float(result)


def _fluid_by_code(output, name1, value1, name2, value2, fluid):
    return fluid_value(
        _FLUID_NAMES[int(output)], _FLUID_NAMES[int(name1)], float(value1),
        _FLUID_NAMES[int(name2)], float(value2), _FLUIDS[int(fluid)],
    )


def _humid_air_by_code(output, name1, value1, name2, value2, name3, value3):
    return humid_air_value(
        _HUMID_AIR_NAMES[int(output)], _HUMID_AIR_NAMES[int(name1)], float(value1),
        _HUMID_AIR_NAMES[int(name2)], float(value2), _HUMID_AIR_NAMES[int(name3)], float(value3),
    )


# ---------------------------------------------------------------------
# Symbolic form (a value is still a variable)
# ---------------------------------------------------------------------
def _all_numbers(args) -> bool:
    return all(arg.is_number and arg.is_real for arg in args)


class engipad_fluid(sp.Function):
    """prop() with a variable among its values; all arguments are
    numbers (codes, SI values) or that variable."""

    @classmethod
    def eval(cls, *args):
        if len(args) == 6 and _all_numbers(args):
            return sp.Float(_fluid_by_code(*args))

    def _eval_evalf(self, prec):
        args = [arg.evalf(prec) for arg in self.args]
        if _all_numbers(args):
            return sp.Float(_fluid_by_code(*args))


class engipad_humid_air(sp.Function):
    @classmethod
    def eval(cls, *args):
        if len(args) == 7 and _all_numbers(args):
            return sp.Float(_humid_air_by_code(*args))

    def _eval_evalf(self, prec):
        args = [arg.evalf(prec) for arg in self.args]
        if _all_numbers(args):
            return sp.Float(_humid_air_by_code(*args))


def _nan_outside(function):
    """For plots: a state CoolProp cannot evaluate is a gap in the
    curve, not an error for the whole plot."""
    def safe(*args):
        try:
            return function(*args)
        except ValueError:
            return float("nan")
    return safe


NUMPY_FUNCTIONS = {
    "engipad_fluid": np.vectorize(_nan_outside(_fluid_by_code), otypes=[float]),
    "engipad_humid_air": np.vectorize(_nan_outside(_humid_air_by_code), otypes=[float]),
}
MPMATH_FUNCTIONS = {
    "engipad_fluid": lambda *args: _fluid_by_code(*args),
    "engipad_humid_air": lambda *args: _humid_air_by_code(*args),
}


# ---------------------------------------------------------------------
# Entry points for the bridge
# ---------------------------------------------------------------------
def _canonical(name: str, table: dict, function: str, what: str) -> str:
    key = _ALIASES.get(name, name)
    if key not in table:
        raise ValueError(
            f"{function}(): '{name}' is not a known {what}. Known: "
            + ", ".join(table) + "."
        )
    return key


def _input_number(function: str, name: str, value, unit):
    """SI number (or SI expression in the free variable) of one input,
    after checking that it has the dimension its name asks for."""
    if not isinstance(value, sp.Expr) or isinstance(value, sp.MatrixBase):
        raise ValueError(f"{function}(): the value of {name} must be a number or quantity.")

    if not has_only_units_and_numbers(value):
        # A variable without a value: read in SI base units, like every
        # plot variable.
        return strip_units_for_plot(value)

    magnitude, _ = split_magnitude_unit(convert_to_cached(value, BASE_UNITS))
    # An exact 0 has lost its unit ("0 Pa" is stored as plain 0).
    if value != 0 and convert_to_cached(value / unit, BASE_UNITS).has(*UNIT_VALUES):
        wanted = "a plain number" if unit == 1 else "a quantity with a matching unit"
        raise ValueError(
            f"{function}(): the value of {name} must be {wanted} "
            f"(e.g. T in K or degC, p in Pa or bar)."
        )
    number = sp.N(magnitude)
    if not number.is_real:
        raise ValueError(f"{function}(): the value of {name} must be a real number.")
    return number


def _base(unit):
    """The unit written with SI base units. The symbolic form is
    multiplied with THIS, because strip_units_for_plot() cannot convert
    a derived unit (Pa, J) that stands next to a function call."""
    return unit if unit == 1 else convert_to_cached(unit, BASE_UNITS)


def prop(output_name: str, name1: str, value1, name2: str, value2, fluid: str):
    output = _canonical(output_name, FLUID_QUANTITIES, "prop", "quantity")
    in1 = _canonical(name1, {k: FLUID_QUANTITIES[k] for k in FLUID_INPUTS}, "prop", "input quantity")
    in2 = _canonical(name2, {k: FLUID_QUANTITIES[k] for k in FLUID_INPUTS}, "prop", "input quantity")
    if in1 == in2:
        raise ValueError("prop(): two DIFFERENT input quantities are needed.")

    number1 = _input_number("prop", name1, value1, FLUID_QUANTITIES[in1][1])
    number2 = _input_number("prop", name2, value2, FLUID_QUANTITIES[in2][1])
    unit = FLUID_QUANTITIES[output][1]

    if fluid not in _FLUIDS:
        try:
            _coolprop().PropsSI("M", fluid)
        except ValueError as exc:
            if str(exc).startswith("prop() and haprop() need"):
                raise
            raise ValueError(
                f"prop(): '{fluid}' is not a fluid CoolProp knows. Use the English "
                "CoolProp names without special characters, e.g. Water, Air, "
                "Nitrogen, CO2, Ammonia, R134a, nButane."
            )
        _FLUIDS.append(fluid)

    if number1.is_number and number2.is_number:
        return sp.Float(fluid_value(output, in1, float(number1), in2, float(number2), fluid)) * unit

    return engipad_fluid(
        _FLUID_NAMES.index(output), _FLUID_NAMES.index(in1), number1,
        _FLUID_NAMES.index(in2), number2, _FLUIDS.index(fluid),
    ) * _base(unit)


def haprop(output_name: str, name1, value1, name2, value2, name3, value3):
    inputs = {k: HUMID_AIR_QUANTITIES[k] for k in HUMID_AIR_INPUTS}
    output = _canonical(output_name, HUMID_AIR_QUANTITIES, "haprop", "quantity")
    names = [_canonical(n, inputs, "haprop", "input quantity") for n in (name1, name2, name3)]
    if len(set(names)) != 3:
        raise ValueError("haprop(): three DIFFERENT input quantities are needed.")

    numbers = [
        _input_number("haprop", shown, value, HUMID_AIR_QUANTITIES[name][1])
        for shown, name, value in zip((name1, name2, name3), names, (value1, value2, value3))
    ]
    unit = HUMID_AIR_QUANTITIES[output][1]

    if all(number.is_number for number in numbers):
        result = humid_air_value(
            output, names[0], float(numbers[0]), names[1], float(numbers[1]),
            names[2], float(numbers[2]),
        )
        return sp.Float(result) * unit

    _humid_air()
    codes = [_HUMID_AIR_NAMES.index(name) for name in names]
    return engipad_humid_air(
        _HUMID_AIR_NAMES.index(output), codes[0], numbers[0], codes[1], numbers[1],
        codes[2], numbers[2],
    ) * _base(unit)


def wrap_coolprop_error(function):
    """CoolProp raises ValueError itself for an unknown fluid etc.; keep
    the message short."""
    def wrapped(*args):
        try:
            return function(*args)
        except ValueError as exc:
            text = str(exc)
            if text.startswith(("prop()", "haprop()", "CoolProp")):
                raise
            raise ValueError(f"CoolProp: {_short(exc)}")
    return wrapped


prop = wrap_coolprop_error(prop)
haprop = wrap_coolprop_error(haprop)
