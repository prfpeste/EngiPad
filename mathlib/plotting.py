import base64
import io

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import mpmath
import sympy as sp

from mathlib.fluids import NUMPY_FUNCTIONS as FLUID_FUNCTIONS
from mathlib.functions import reject_unknown_functions


def _elementwise(function, order_first=False):
    """A scalar mpmath function as a NumPy function. mpmath comes with
    SymPy, so the plot needs no SciPy for these."""
    if order_first:
        scalar = lambda order, value: float(function(float(order), float(value)))
    else:
        scalar = lambda value: float(function(float(value)))
    return np.vectorize(scalar, otypes=[float])


# NumPy itself has no erf/Bessel functions. lambdify() prints them as
# plain calls "erf(x)", "besselj(n, x)", which are resolved here. A
# FIXED table of our own functions -- user input cannot add a name to
# it (unknown functions are refused before lambdify, see create_plot).
_SPECIAL_FUNCTIONS = {
    **FLUID_FUNCTIONS,   # engipad_fluid, engipad_humid_air (prop/haprop)
    "erf": _elementwise(mpmath.erf),
    "erfc": _elementwise(mpmath.erfc),
    "besselj": _elementwise(mpmath.besselj, order_first=True),
    "bessely": _elementwise(mpmath.bessely, order_first=True),
    "besseli": _elementwise(mpmath.besseli, order_first=True),
    "besselk": _elementwise(mpmath.besselk, order_first=True),
}


def create_plot(expr_sym, var_symbol, x_min=-10, x_max=10, num_points=400, note=None):
    # Security: lambdify() turns the expression into Python source and
    # runs it. Nothing may reach it that is not a plain formula in the
    # plot variable -- see mathlib.functions.reject_unknown_functions().
    reject_unknown_functions(expr_sym, "plot()")
    if not isinstance(var_symbol, sp.Symbol) or not str(var_symbol).isidentifier():
        raise ValueError("plot(f, x, ...): the second argument must be a variable name.")
    undefined = sorted(
        str(s) for s in getattr(expr_sym, "free_symbols", set()) if s != var_symbol
    )
    if undefined:
        raise ValueError(
            "variable(s) without a value in the plotted formula: "
            + ", ".join(undefined)
            + "."
        )

    f_num = sp.lambdify(var_symbol, expr_sym, [_SPECIAL_FUNCTIONS, "numpy"])
    xs = np.linspace(float(x_min), float(x_max), num_points)
    ys = f_num(xs)

    fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
    ax.plot(xs, ys)
    ax.grid(True)
    ax.set_xlabel(str(var_symbol))
    ylabel = f"f({var_symbol})"
    if note:
        # e.g. the formula contained unit-bearing quantities (N, m, ...)
        # that were stripped before plotting -- see
        # mathlib.units.strip_units_for_plot(). Values are correct
        # numerically (SI base units throughout), just not re-labelled
        # with a specific unit name (see that function's docstring for
        # why that's deliberately not attempted).
        ylabel += f"  [{note}]"
    ax.set_ylabel(ylabel)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)

    img_base64 = base64.b64encode(buf.read()).decode("ascii")
    return "data:image/png;base64," + img_base64
