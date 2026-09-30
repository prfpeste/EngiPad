import sympy as sp

from mathlib.solving import my_csolve, my_solve


def _log_base_10(x, b=10, evaluate=None):
    return sp.log(x, b)


def _vec(*args):
    return sp.Matrix(args)


def _transpose(matrix):
    return matrix.T


def _det(matrix):
    return matrix.det()


def _inv(matrix):
    return matrix.inv()


def _dot(a, b):
    return a.dot(b)


def _cross(a, b):
    return a.cross(b)


def _norm(a):
    return a.norm()


def _solve_linear(A, b):
    return A.LUsolve(b)


def _rank(A):
    return sp.Integer(A.rank())


def _trace(A):
    return A.trace()


COMMON_EVAL_FUNCTIONS = {
    "sp": sp,
    "π": sp.pi,
    "pi": sp.pi,
    "__calcpad_infty__": sp.oo,
    "__calcpad_imag__": sp.I,
    "abs": sp.Abs,
    "re": sp.re,
    "im": sp.im,
    "conjugate": sp.conjugate,
    "arg": sp.arg,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "atan2": sp.atan2,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "asinh": sp.asinh,
    "acosh": sp.acosh,
    "atanh": sp.atanh,
    "exp": sp.exp,
    "ln": sp.log,
    "log": _log_base_10,
    "sqrt": sp.sqrt,
    "symbols": sp.symbols,
    "Eq": sp.Eq,
    "solve": my_solve,
    "csolve": my_csolve,
    "nsolve": sp.nsolve,
    "vec": _vec,
    "mat": sp.Matrix,
    "Matrix": sp.Matrix,
    "eye": sp.eye,
    "zeros": sp.zeros,
    "ones": sp.ones,
    "det": _det,
    "inv": _inv,
    "T": _transpose,
    "dot": _dot,
    "cross": _cross,
    "norm": _norm,
    "solve_linear": _solve_linear,
    "eigenvals": lambda A: A.eigenvals(),
    "eigenvects": lambda A: A.eigenvects(),
    "rank": _rank,
    "trace": _trace,
}

# Functions whose argument(s) must be dimensionless: exp(10 kg) or
# log(10 kg) are physically meaningless (see
# mathlib/units.py::require_dimensionless()). Checked by
# mathlib/sympy_bridge.py in "numeric" mode only. NOT listed on purpose:
# sqrt/abs/re/im/conjugate/arg (fine with units, e.g. sqrt(9 m^2) = 3 m)
# and atan2 (arguments need the SAME dimension, not dimensionless --
# see DIMENSION_MATCHING_FUNCTIONS).
# Keep in sync with COMMON_EVAL_FUNCTIONS when adding new
# transcendental functions there.
DIMENSIONLESS_ARGUMENT_FUNCTIONS = frozenset({
    "exp", "ln", "log",
    "sin", "cos", "tan",
    "asin", "acos", "atan",
    "sinh", "cosh", "tanh",
    "asinh", "acosh", "atanh",
})

# Functions whose arguments may carry a unit but must all have the same
# dimension (a ratio of them is formed internally).
DIMENSION_MATCHING_FUNCTIONS = frozenset({"atan2"})

NUMERIC_FUNCTIONS = {
    **COMMON_EVAL_FUNCTIONS,
    "integrate": sp.integrate,
    "diff": sp.diff,
    "sum": sp.summation,
    "summe": sp.summation,
    "prod": sp.product,
    "produkt": sp.product,
    "lim": sp.limit,
    "x": sp.Symbol("x"),
    "y": sp.Symbol("y"),
    "t_sym": sp.Symbol("t"),
}

DISPLAY_FUNCTIONS = {
    **COMMON_EVAL_FUNCTIONS,
    "integrate": sp.Integral,
    "diff": sp.Derivative,
    "sum": sp.Sum,
    "summe": sp.Sum,
    "prod": sp.Product,
    "produkt": sp.Product,
    "lim": sp.Limit,
    "x": sp.Symbol("x"),
    "y": sp.Symbol("y"),
    "t_sym": sp.Symbol("t"),
}

PLOT_FUNCTIONS = {
    **COMMON_EVAL_FUNCTIONS,
    "integrate": sp.Integral,
    "diff": sp.Derivative,
    "sum": sp.Sum,
    "summe": sp.Sum,
    "prod": sp.Product,
    "produkt": sp.Product,
    "lim": sp.Limit,
    "x": sp.Symbol("x"),
    "y": sp.Symbol("y"),
    "t_sym": sp.Symbol("t"),
}
