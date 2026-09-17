import base64
import io

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import sympy as sp


def create_plot(expr_sym, var_symbol, x_min=-10, x_max=10, num_points=400, note=None):
    f_num = sp.lambdify(var_symbol, expr_sym, "numpy")
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
