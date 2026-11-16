import mpmath
import sympy as sp
from sympy.core.function import AppliedUndef
from sympy.core.relational import Relational

from mathlib.solving import my_csolve, my_solve
from mathlib.units import (
    BASE_UNITS,
    UNIT_VALUES,
    base_magnitude,
    convert_to_cached,
    has_only_units_and_numbers,
    require_comparable,
    split_magnitude_unit,
    strip_units_for_plot,
    substitute_symbols,
)


def _iter_expressions(value):
    """All SymPy objects inside a value that may be a nested Python
    list/tuple (ListLiteral arguments) or a single expression/matrix."""
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_expressions(item)
    elif hasattr(value, "atoms"):
        yield value


def reject_unknown_functions(value, where: str) -> None:
    """Raises ValueError if `value` contains a call of a function that
    is not in the whitelist (an inert sp.Function(name)(...) placeholder,
    see mathlib/sympy_bridge.py).

    MUST be called before anything is handed to sp.lambdify() (plot())
    or to a SymPy routine that uses lambdify() internally (nsolve()):
    lambdify() prints the expression as Python source and exec()s it,
    and it prints an unknown function as a plain call "name(args)" --
    so a placeholder named like a Python builtin or a NumPy function
    (print, exec, open, load, ...) would really be CALLED there. The
    placeholder is only harmless as long as it is never executed.
    """
    names = set()
    for expr in _iter_expressions(value):
        names.update(str(call.func) for call in expr.atoms(AppliedUndef))
    if names:
        listed = ", ".join(f"'{name}'" for name in sorted(names))
        raise ValueError(f"Unknown function {listed} cannot be used in {where}.")


def _as_list(value) -> list:
    if isinstance(value, sp.MatrixBase):
        return list(value)
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _has_units(value) -> bool:
    return hasattr(value, "has") and value.has(*UNIT_VALUES)


def _plain_number_functions() -> tuple:
    from mathlib.fluids import engipad_fluid, engipad_humid_air

    return (engipad_fluid, engipad_humid_air)


def _nsolve_modules():
    """Modules for the code SymPy generates inside nsolve(): mpmath plus
    the two fluid-property functions (mathlib/fluids.py), so an equation
    may contain prop()/haprop() with the unknown among the values."""
    from mathlib.fluids import MPMATH_FUNCTIONS

    return [MPMATH_FUNCTIONS, "mpmath"]


def _nsolve(*args):
    """nsolve(equation, unknown, start) -- numeric root finding that
    also works with units.

    mpmath (behind sp.nsolve) only knows plain numbers. So everything is
    converted to SI base units and the unit symbols are dropped (same
    approach as plot(), see mathlib.units.strip_units_for_plot()), the
    equation is solved as a pure number problem, and the result gets a
    unit back: the one of the START VALUE.

        U = nsolve(Eq(exp(e*U/(k*T)), ...), U, 0.5'V)   ->  0.53 V

    The unit of the start value therefore states the unit of the
    unknown. A start value without a unit gives a plain number, exactly
    as before. The equation itself is NOT checked for consistent
    dimensions here (the unknown has no value yet, so the usual checks
    of the bridge do not apply either).

    Systems: nsolve([eq1, eq2], [x, y], [1'm, 2's]).
    """
    if len(args) == 2:
        equations, starts = args
        free = set()
        for expr in _iter_expressions(_as_list(equations)):
            free |= {s for s in expr.free_symbols if s not in UNIT_VALUES}
        if len(free) != 1:
            raise ValueError("nsolve(equation, variable, start) expected.")
        unknowns = free.pop()
    elif len(args) == 3:
        equations, unknowns, starts = args
    else:
        raise ValueError("nsolve(equation, variable, start) expected.")

    reject_unknown_functions(list(args), "nsolve()")

    is_system = isinstance(unknowns, (list, tuple, sp.MatrixBase))
    equation_list = _as_list(equations)
    unknown_list = _as_list(unknowns)
    start_list = _as_list(starts)

    for unknown in unknown_list:
        if not isinstance(unknown, sp.Symbol) or unknown in UNIT_VALUES:
            raise ValueError(
                "nsolve: the unknown must be a variable that has no value yet."
            )
    if len(unknown_list) != len(start_list):
        raise ValueError("nsolve: one start value per unknown is needed.")
    if len(equation_list) != len(unknown_list):
        raise ValueError("nsolve: as many equations as unknowns are needed.")

    # Start values -> plain number (in SI base units) + unit of the unknown.
    magnitudes = []
    units = []
    for start in start_list:
        if _has_units(start):
            magnitude, unit = split_magnitude_unit(convert_to_cached(start, BASE_UNITS))
        else:
            magnitude, unit = start, sp.Integer(1)
        if getattr(magnitude, "free_symbols", None):
            raise ValueError("nsolve: the start value must be a number.")
        magnitudes.append(magnitude)
        units.append(unit)

    with_unit = {
        unknown: unknown * unit
        for unknown, unit in zip(unknown_list, units)
        if unit != 1
    }

    plain_equations = []
    for equation in equation_list:
        if isinstance(equation, sp.Equality):
            equation = equation.lhs - equation.rhs
        elif not isinstance(equation, sp.Expr):
            raise ValueError("nsolve: the first argument must be an expression or Eq(...).")
        if with_unit:
            # Not .subs(): see mathlib.units.substitute_symbols().
            equation = substitute_symbols(equation, with_unit, _plain_number_functions())
        equation = strip_units_for_plot(equation)
        if _has_units(equation):
            raise ValueError("nsolve: the units of the equation could not be removed.")
        undefined = sorted(
            str(s) for s in equation.free_symbols if s not in unknown_list
        )
        if undefined:
            raise ValueError(
                "nsolve: variable(s) without a value: " + ", ".join(undefined) + "."
            )
        plain_equations.append(equation)

    if is_system:
        solution = sp.nsolve(plain_equations, unknown_list, magnitudes, modules=_nsolve_modules())
        return sp.Matrix([value * unit for value, unit in zip(solution, units)])

    return _solve_one(plain_equations[0], unknown_list[0], magnitudes[0]) * units[0]


def _solve_one(equation, unknown, start):
    try:
        return sp.nsolve(equation, unknown, start, modules=_nsolve_modules())
    except ValueError as exc:
        if not equation.has(*_plain_number_functions()):
            raise
        first_error = exc

    # A fluid property is only accurate to about 12 digits. mpmath's
    # final check wants the residual below 1e-15 ABSOLUTE, which a
    # pressure in Pa can never reach. Solve again without that check
    # and judge the residual relative to the size of the terms.
    try:
        root = sp.nsolve(equation, unknown, start, modules=_nsolve_modules(), verify=False)
        residual = abs(sp.N(equation.subs(unknown, root)))
        scale = max(
            (abs(sp.N(term.subs(unknown, root))) for term in sp.Add.make_args(equation)),
            default=sp.Integer(1),
        )
        if residual.is_number and residual <= 1e-9 * max(scale, 1e-300):
            return root
    except Exception:
        pass
    raise first_error


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


def _flatten_values(args) -> list:
    values = []
    for arg in args:
        if isinstance(arg, sp.MatrixBase):
            values.extend(arg)
        elif isinstance(arg, (list, tuple)):
            values.extend(_flatten_values(arg))
        else:
            values.append(arg)
    return values


def _extreme(name, largest, symbolic, args):
    """min()/max() of numbers or quantities (also of the elements of a
    vector). Quantities need the same dimension; the result is the
    chosen ARGUMENT, so it keeps its unit (max(900'mm, 1'm) -> 1 m)."""
    values = _flatten_values(args)
    if not values:
        raise ValueError(f"{name}() needs at least one value.")

    if all(has_only_units_and_numbers(v) for v in values):
        require_comparable(f"{name}(): cannot compare", values)
        sizes = [base_magnitude(v, f"Argument of {name}()") for v in values]
        pick = max if largest else min
        return values[sizes.index(pick(sizes))]

    if any(_has_units(v) for v in values):
        raise ValueError(
            f"{name}() with units needs a value for every variable."
        )
    return symbolic(*values)


def _min(*args):
    return _extreme("min", False, sp.Min, args)


def _max(*args):
    return _extreme("max", True, sp.Max, args)


def _sign(value):
    if has_only_units_and_numbers(value):
        return sp.sign(sp.Float(base_magnitude(value, "Argument of sign()")))
    if _has_units(value):
        raise ValueError("sign() with units needs a value for every variable.")
    return sp.sign(value)


def _round(value, digits=0):
    """round(x) / round(x, n): to n decimal places, halves away from
    zero (2.5 -> 3, -2.5 -> -3) as in a hand calculation -- not
    Python's "round half to even"."""
    digits = sp.nsimplify(digits)
    if not digits.is_Integer:
        raise ValueError("round(x, n): n must be a whole number.")
    scale = sp.Integer(10) ** digits
    return sp.sign(value) * sp.floor(sp.Abs(value) * scale + sp.Rational(1, 2)) / scale


def element_at(base, index):
    """base[index] for the "[]" of the language. Counting starts at 1,
    as in mathematics: v[1] is the first element of a vector, M[2][3]
    (or M[2, 3]) the element in row 2, column 3, sol[1] the first of
    several solutions.

    A matrix with several rows AND columns gives its row (a row
    vector), so a second index picks the column. A plain value accepts
    the index 1 (solve() returns a single solution without a list).
    Plain item access on a value that is already built -- never a name
    or attribute lookup.
    """
    if not getattr(index, "is_number", False) or not index.is_real \
            or float(index) != int(float(index)):
        raise ValueError("An index must be a whole number.")
    position = int(float(index))

    if isinstance(base, sp.MatrixBase):
        rows, cols = base.shape
        if rows > 1 and cols > 1:
            _check_index(position, rows)
            return base[position - 1, :]
        items = list(base)
    elif isinstance(base, (list, tuple, sp.Tuple)):
        items = list(base)
    elif isinstance(base, sp.Expr) and not base.free_symbols - set(UNIT_VALUES):
        items = [base]
    else:
        raise ValueError(
            "Only vectors, matrices and lists of solutions can be indexed."
        )

    _check_index(position, len(items))
    return items[position - 1]


def _check_index(position: int, count: int) -> None:
    if position == 0:
        raise ValueError("Indices start at 1 (the first element is [1]).")
    if position < 0 or position > count:
        raise ValueError(f"Index {position} is out of range (1 to {count}).")


def _table_axis(name, values, what):
    """Values of one table axis (or of the table itself) and their
    plain numbers in SI base units."""
    items = _flatten_values([values])
    if len(items) < 2:
        raise ValueError(f"{name}(): {what} need at least two values.")
    for item in items:
        if not isinstance(item, sp.Expr) or not has_only_units_and_numbers(item):
            raise ValueError(f"{name}(): {what} must be numbers or quantities.")
    require_comparable(f"{name}(): {what} have", items)
    return items, [base_magnitude(item, f"{name}(): a table value") for item in items]


def _check_monotonic(name, what, numbers) -> None:
    pairs = list(zip(numbers, numbers[1:]))
    if not (all(b > a for a, b in pairs) or all(b < a for a, b in pairs)):
        raise ValueError(
            f"{name}(): {what} must be strictly increasing or strictly decreasing."
        )


def _locate(name, numbers, value, axis_items):
    """Index k of the interval [numbers[k], numbers[k+1]] that contains
    `value` and the relative position t (0..1) in it. Outside the table
    is an error: extrapolating silently is too dangerous."""
    if not isinstance(value, sp.Expr) or not has_only_units_and_numbers(value):
        raise ValueError(f"{name}(): the value to look up must be a number or quantity.")
    require_comparable(f"{name}(): table and value have", [*axis_items, value])
    number = base_magnitude(value, f"{name}(): the value to look up")

    low, high = min(numbers), max(numbers)
    slack = 1e-12 * max(abs(low), abs(high), 1e-300)
    if number < low - slack or number > high + slack:
        raise ValueError(
            f"{name}(): the value is outside the table "
            f"({low:g} to {high:g} in SI base units, value {number:g}). "
            "Values outside the table are not extrapolated."
        )
    number = min(max(number, low), high)

    for k, (a, b) in enumerate(zip(numbers, numbers[1:])):
        if min(a, b) <= number <= max(a, b):
            return k, (number - a) / (b - a)
    raise ValueError(f"{name}(): the value could not be located in the table.")


def _interp(*args):
    """interp(x, y, value): linear interpolation in a table given as
    two vectors of the same length. With units; x must be strictly
    increasing or decreasing; a value outside the table is an error.

    If `value` is a variable without a value (inside a function that is
    plotted or solved), the result is the piecewise linear formula in
    that variable, read in SI base units like every plot variable.
    """
    if len(args) != 3:
        raise ValueError("interp(x, y, value) expected.")
    xs, x_numbers = _table_axis("interp", args[0], "the x values")
    ys, _ = _table_axis("interp", args[1], "the y values")
    value = args[2]

    if len(xs) != len(ys):
        raise ValueError(
            f"interp(): x has {len(xs)} values, y has {len(ys)}; they must be equally many."
        )
    _check_monotonic("interp", "the x values", x_numbers)

    if isinstance(value, sp.Expr) and not has_only_units_and_numbers(value):
        if _has_units(value):
            raise ValueError("interp() with units needs a value for every variable.")
        pieces = []
        for k, (a, b) in enumerate(zip(x_numbers, x_numbers[1:])):
            line = ys[k] + (ys[k + 1] - ys[k]) * (value - a) / (b - a)
            pieces.append((line, sp.And(value >= min(a, b), value <= max(a, b))))
        return sp.Piecewise(*pieces)

    k, t = _locate("interp", x_numbers, value, xs)
    if t == 0:
        return ys[k]
    if t == 1:
        return ys[k + 1]
    return ys[k] + (ys[k + 1] - ys[k]) * sp.Float(t)


def _interp2(*args):
    """interp2(x, y, Z, value_x, value_y): bilinear interpolation in a
    two-dimensional table (a map). Z is a matrix whose ROWS belong to
    the x values and whose COLUMNS belong to the y values:
    Z[i][j] is the table value at (x[i], y[j])."""
    if len(args) != 5:
        raise ValueError("interp2(x, y, Z, value_x, value_y) expected.")
    xs, x_numbers = _table_axis("interp2", args[0], "the x values")
    ys, y_numbers = _table_axis("interp2", args[1], "the y values")
    table = args[2]
    if isinstance(table, (list, tuple)):
        table = sp.Matrix(table)
    if not isinstance(table, sp.MatrixBase):
        raise ValueError("interp2(): the third argument must be a matrix.")
    if table.shape != (len(xs), len(ys)):
        raise ValueError(
            f"interp2(): the table has {table.shape[0]} rows and {table.shape[1]} columns, "
            f"but x has {len(xs)} values (rows) and y has {len(ys)} values (columns)."
        )
    _table_axis("interp2", table, "the table values")
    _check_monotonic("interp2", "the x values", x_numbers)
    _check_monotonic("interp2", "the y values", y_numbers)

    i, tx = _locate("interp2", x_numbers, args[3], xs)
    j, ty = _locate("interp2", y_numbers, args[4], ys)
    tx, ty = sp.Float(tx), sp.Float(ty)

    return (
        table[i, j] * (1 - tx) * (1 - ty)
        + table[i + 1, j] * tx * (1 - ty)
        + table[i, j + 1] * (1 - tx) * ty
        + table[i + 1, j + 1] * tx * ty
    )


def _nintegrate(*args):
    """nintegrate(f, (x, a, b)): definite integral by numerical
    quadrature -- for integrands integrate() cannot solve in closed
    form or only very slowly. Works with units like nsolve(): limits
    and integrand are converted to SI base units and integrated as
    plain numbers; the result gets the unit "integrand times variable".
    The integrand itself is not checked for consistent dimensions."""
    if len(args) != 2 or not isinstance(args[1], (list, tuple)) or len(args[1]) != 3:
        raise ValueError("nintegrate(f, (x, a, b)) expected.")
    integrand, (variable, lower, upper) = args
    reject_unknown_functions(list(args), "nintegrate()")

    if not isinstance(variable, sp.Symbol) or variable in UNIT_VALUES:
        raise ValueError(
            "nintegrate: the integration variable must be a variable that has no value yet."
        )
    if not isinstance(integrand, sp.Expr) or isinstance(integrand, sp.MatrixBase):
        raise ValueError("nintegrate: the first argument must be a formula.")
    for limit in (lower, upper):
        if not isinstance(limit, sp.Expr) or not has_only_units_and_numbers(limit):
            raise ValueError("nintegrate: the limits must be numbers or quantities.")
    require_comparable("nintegrate(): the limits have", (lower, upper))

    a = base_magnitude(lower, "A limit of nintegrate()")
    b = base_magnitude(upper, "A limit of nintegrate()")

    variable_unit = sp.Integer(1)
    for limit in (lower, upper):
        if _has_units(limit):
            _, variable_unit = split_magnitude_unit(convert_to_cached(limit, BASE_UNITS))
            break

    # Not .subs(): see mathlib.units.substitute_symbols().
    if variable_unit != 1:
        integrand = substitute_symbols(
            integrand, {variable: variable * variable_unit}, _plain_number_functions()
        )

    # Unit of the integrand: taken from its value at a point inside the
    # interval (the first one where it is not zero).
    integrand_unit = sp.Integer(1)
    if _has_units(integrand):
        for fraction in (0.5, 0.317, 0.823, 0.0, 1.0):
            sample = substitute_symbols(
                integrand, {variable: sp.Float(a + fraction * (b - a))}
            )
            try:
                magnitude, unit = split_magnitude_unit(
                    convert_to_cached(sp.N(sample), BASE_UNITS)
                )
            except Exception:
                continue
            if getattr(magnitude, "is_number", False) and magnitude != 0 and magnitude.is_finite:
                integrand_unit = unit
                break

    plain = strip_units_for_plot(integrand)
    if _has_units(plain):
        raise ValueError("nintegrate: the units of the integrand could not be removed.")
    undefined = sorted(str(s) for s in plain.free_symbols if s != variable)
    if undefined:
        raise ValueError(
            "nintegrate: variable(s) without a value: " + ", ".join(undefined) + "."
        )

    # A table (interp) or an if() in the integrand has kinks or jumps
    # at known places. Quadrature over such a point is slow and
    # inaccurate, so the interval is split there and the parts are
    # added up.
    low, high = min(a, b), max(a, b)
    breaks = set()
    for relation in plain.atoms(Relational):
        for side, other in ((relation.lhs, relation.rhs), (relation.rhs, relation.lhs)):
            if side == variable and other.is_number and other.is_real:
                point = float(other)
                if low < point < high:
                    breaks.add(point)
    points = [low, *sorted(breaks), high]

    # mpmath quadrature on the compiled integrand. (SymPy's own
    # Integral.evalf() evaluates the expression tree point by point and
    # took ten seconds for an integrand with a fluid property.)
    # lambdify() is safe here: unknown functions were refused above, and
    # the only names beyond mpmath's own are the two fixed fluid
    # functions.
    compiled = sp.lambdify(variable, plain, _nsolve_modules())
    try:
        value = mpmath.quad(compiled, points)
    except Exception as exc:
        raise ValueError(f"nintegrate: the integral could not be evaluated numerically ({exc}).")
    if not mpmath.isfinite(value) or mpmath.im(value) != 0:
        raise ValueError("nintegrate: the integral has no finite real value.")

    result = sp.Float(float(mpmath.re(value)))
    if a > b:
        result = -result
    return result * integrand_unit * variable_unit


COMMON_EVAL_FUNCTIONS = {
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
    # Error function (transient heat conduction in a semi-infinite
    # body) and Bessel functions (fins, cylinders): J_n, Y_n and the
    # modified I_n, K_n, written besselj(n, x) etc.
    "erf": sp.erf,
    "erfc": sp.erfc,
    "besselj": sp.besselj,
    "bessely": sp.bessely,
    "besseli": sp.besseli,
    "besselk": sp.besselk,
    "min": _min,
    "max": _max,
    "sign": _sign,
    "round": _round,
    "floor": sp.floor,
    "ceil": sp.ceiling,
    "Eq": sp.Eq,
    "solve": my_solve,
    "csolve": my_csolve,
    "nsolve": _nsolve,
    "nintegrate": _nintegrate,
    "interp": _interp,
    "interp2": _interp2,
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
    # Rounding a quantity would depend on its unit (1.4 m or 1400 mm?),
    # so these take plain numbers: round(L / 1'mm) * 1'mm.
    "round", "floor", "ceil",
    "erf", "erfc", "besselj", "bessely", "besseli", "besselk",
})

# Names that are handled by the bridge itself (mathlib/sympy_bridge.py)
# or by core/engine.py and therefore are not in the tables above, but
# are just as reserved as the built-in functions.
RESERVED_FUNCTION_NAMES = frozenset({"if", "plot", "picture", "prop", "haprop"})

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
