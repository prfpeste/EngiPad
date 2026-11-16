"""Builds a SymPy expression from an AST node (parsing.ast_nodes, as
produced by parsing.parser.parse()) -- WITHOUT ever calling eval() or
sympy.sympify()/parse_expr() on user input.

Background: core/context.py used to evaluate numeric input via
sp.sympify(text, locals=env). sympify() is, by SymPy's own
documentation, NOT meant for untrusted input, because it uses eval()
internally. This was actually exploitable with a payload like

    x = __import__(chr(111)+chr(115)).popen(chr(105)+chr(100)).read()

(arbitrary code ran on the server). This bridge closes the hole
structurally: numbers/identifiers/operators are translated node by node
from the AST into real SymPy objects, and function calls are resolved
EXCLUSIVELY against a fixed whitelist
(mathlib.functions.NUMERIC_FUNCTIONS/DISPLAY_FUNCTIONS/PLOT_FUNCTIONS)
-- a name like "__import__" that isn't in the whitelist can never
become a real Python function call; like any unknown function name it
becomes a purely symbolic, never-executed sp.Function(name)(...)
placeholder.

`mode` controls two things (mirrors the old
EvaluationContext.make_eval_env()):
    "numeric": user variables resolve to their CURRENT VALUE (var_ns),
               functions are ACTUALLY evaluated (integrate() really
               solves, via NUMERIC_FUNCTIONS).
    "display": user variables resolve to a FRESH symbol (for the "raw
               formula" display before the "="), functions stay
               unevaluated where that makes sense (integrate() returns
               an sp.Integral(...) object, via DISPLAY_FUNCTIONS).
    "plot":    identifiers behave like "display" (user variables stay
               symbols -- a plotted function should generally stay
               expressed in terms of "x"), but uses PLOT_FUNCTIONS as
               the function whitelist.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass

import sympy as sp
from sympy.logic.boolalg import Boolean

from mathlib.functions import (
    element_at,
    DIMENSION_MATCHING_FUNCTIONS,
    DIMENSIONLESS_ARGUMENT_FUNCTIONS,
    DISPLAY_FUNCTIONS,
    NUMERIC_FUNCTIONS,
    PLOT_FUNCTIONS,
)
from mathlib.units import (
    UNIT_NS,
    check_addition_dimensions,
    compare_values,
    require_dimensionless,
    require_same_dimension,
)
from parsing.ast_nodes import (
    BinaryOp,
    Comparison,
    FunctionCall,
    Identifier,
    ListLiteral,
    Node,
    Number,
    Quantity,
    SheetRef,
    Subscript,
    UnaryOp,
)

_FUNCTION_TABLES = {
    "numeric": NUMERIC_FUNCTIONS,
    "display": DISPLAY_FUNCTIONS,
    "plot": PLOT_FUNCTIONS,
}

_DEGC_OFFSET = sp.Float("273.15")

# Resolver for sheet references ("S1::L"): a callable (sheet, name) ->
# value, provided by the workbook evaluation (core/workbook.py) via
# sheet_lookup_scope(). A context variable instead of an extra
# parameter on every recursive call; without a workbook it is None and
# every sheet reference is a clean error.
_SHEET_LOOKUP = contextvars.ContextVar("engipad_sheet_lookup", default=None)


@contextlib.contextmanager
def sheet_lookup_scope(lookup):
    token = _SHEET_LOOKUP.set(lookup)
    try:
        yield
    finally:
        _SHEET_LOOKUP.reset(token)


# User-defined functions ("f(x) = x^2 + 1"): name -> UserFunction,
# provided by core/context.py via user_functions_scope(). Like the
# sheet lookup a context variable, so the recursive calls keep their
# signature.
_USER_FUNCTIONS = contextvars.ContextVar("engipad_user_functions", default=None)
_CALL_DEPTH = contextvars.ContextVar("engipad_call_depth", default=0)
MAX_CALL_DEPTH = 30


@dataclass(frozen=True)
class UserFunction:
    """A function defined in the sheet. `body` is the PARSED formula
    (AST), never text: calling the function evaluates this tree through
    the same bridge and whitelist as any other input."""

    name: str
    params: tuple
    body: Node


@contextlib.contextmanager
def user_functions_scope(functions):
    token = _USER_FUNCTIONS.set(functions)
    try:
        yield
    finally:
        _USER_FUNCTIONS.reset(token)


def _call_user_function(function: UserFunction, args: tuple, var_ns: dict, user_vars: set, mode: str):
    if len(args) != len(function.params):
        raise ValueError(
            f"{function.name}() takes {len(function.params)} argument(s), "
            f"{len(args)} given."
        )

    if mode == "display":
        # The formula display shows the call itself ("f(2)"), not the
        # body. Never executed -- an inert placeholder like an unknown
        # function, only ever printed.
        return sp.Function(function.name)(*args)

    depth = _CALL_DEPTH.get()
    if depth >= MAX_CALL_DEPTH:
        raise ValueError(
            f"Function calls are nested too deeply at {function.name}() "
            "(functions must not call themselves)."
        )

    # The parameters hide variables of the same name; every other name
    # in the body is looked up as usual, i.e. with the value it has NOW
    # (at the time of the call). The body is always evaluated for real
    # ("numeric"), so the unit checks apply to the actual arguments and
    # nothing unevaluated is left for plot()/nsolve().
    local_ns = dict(var_ns)
    local_ns.update(zip(function.params, args))

    token = _CALL_DEPTH.set(depth + 1)
    try:
        return ast_to_sympy(function.body, local_ns, user_vars, "numeric")
    finally:
        _CALL_DEPTH.reset(token)


_DISPLAY_RELATIONS = {
    "<": sp.Lt, "<=": sp.Le, ">": sp.Gt, ">=": sp.Ge, "==": sp.Eq, "!=": sp.Ne,
}


def _eval_if(node: FunctionCall, var_ns: dict, user_vars: set, mode: str):
    """if(condition, a, b). Only the branch that applies is evaluated
    when the condition is decided (so an error in the other branch,
    e.g. a unit mismatch, does not matter). With a condition that still
    contains a variable without a value (inside a function that is
    plotted or solved) both branches are kept as a piecewise formula.
    """
    if len(node.args) != 3:
        raise ValueError("if(condition, a, b) expected: three arguments.")

    condition = ast_to_sympy(node.args[0], var_ns, user_vars, mode)

    if condition is sp.true:
        return ast_to_sympy(node.args[1], var_ns, user_vars, mode)
    if condition is sp.false:
        return ast_to_sympy(node.args[2], var_ns, user_vars, mode)

    if not isinstance(condition, Boolean):
        raise ValueError(
            "The first argument of if() must be a condition, e.g. a < b."
        )

    return sp.Piecewise(
        (ast_to_sympy(node.args[1], var_ns, user_vars, mode), condition),
        (ast_to_sympy(node.args[2], var_ns, user_vars, mode), True),
    )


# prop(output, name, value, name, value, fluid) and
# haprop(output, name, value, name, value, name, value): positions that
# hold a NAME (taken from the tree as written, never evaluated) and
# positions that hold a value.
_PROPERTY_FUNCTIONS = {
    "prop": {"count": 6, "names": (0, 1, 3, 5), "values": (2, 4)},
    "haprop": {"count": 7, "names": (0, 1, 3, 5), "values": (2, 4, 6)},
}


def _eval_property(node: FunctionCall, var_ns: dict, user_vars: set, mode: str):
    """Fluid properties (CoolProp), see mathlib/fluids.py. A special
    form like if(): the quantity names and the fluid are words, not
    expressions -- "T" stays the name "T" even if a variable T exists."""
    layout = _PROPERTY_FUNCTIONS[node.name]
    usage = (
        "prop(quantity, name, value, name, value, fluid)" if node.name == "prop"
        else "haprop(quantity, name, value, name, value, name, value)"
    )
    if len(node.args) != layout["count"]:
        raise ValueError(f"{usage} expected.")

    for position in layout["names"]:
        if not isinstance(node.args[position], Identifier):
            raise ValueError(
                f"{node.name}(): argument {position + 1} must be a name "
                f"(usage: {usage})."
            )

    values = [ast_to_sympy(node.args[i], var_ns, user_vars, mode) for i in layout["values"]]

    if mode == "display":
        # Only ever printed (the formula display comes from the tree).
        return sp.Function(node.name)(*values)

    from mathlib import fluids  # light; CoolProp itself is loaded on first use

    names = [node.args[i].name for i in layout["names"]]
    if node.name == "prop":
        return fluids.prop(names[0], names[1], values[0], names[2], values[1], names[3])
    return fluids.haprop(names[0], names[1], values[0], names[2], values[1], names[3], values[2])


def _number_to_sympy(value: str):
    # "1.5e-3" / "2e6" (scientific notation) are always floats.
    if "." in value or "e" in value or "E" in value:
        return sp.Float(value)
    return sp.Integer(value)


def ast_to_sympy(node: Node, var_ns: dict, user_vars: set, mode: str = "numeric"):
    """Converts an AST node into a SymPy expression. Raises
    ValueError/KeyError on unknown constructs (e.g. a ListLiteral
    outside a function argument) -- the caller (core/context.py) treats
    this like any other evaluation error.
    """
    if mode not in _FUNCTION_TABLES:
        raise ValueError(f"Unknown mode: {mode!r}")

    table = _FUNCTION_TABLES[mode]

    if isinstance(node, Number):
        return _number_to_sympy(node.value)

    if isinstance(node, Identifier):
        name = node.name

        if mode == "numeric":
            # var_ns starts as a copy of the function table, so a name
            # like "T" (transpose) or "det" is in it as a FUNCTION. Used
            # as a plain name without a value it is an ordinary
            # variable (e.g. a temperature T that is integrated over).
            if name in var_ns and not callable(var_ns[name]):
                return var_ns[name]
        elif name in user_vars:
            return sp.Symbol(name)

        if name in table and not callable(table[name]):
            return table[name]

        return sp.Symbol(name)

    if isinstance(node, SheetRef):
        if mode == "display":
            # Display mode shows the formula, not the value: a fresh
            # symbol whose name var_to_latex() renders as "S1::L".
            return sp.Symbol(f"{node.sheet}::{node.name}")

        lookup = _SHEET_LOOKUP.get()
        if lookup is None:
            raise ValueError(f"Sheet '{node.sheet}' does not exist.")
        return lookup(node.sheet, node.name)

    if isinstance(node, UnaryOp):
        value = ast_to_sympy(node.operand, var_ns, user_vars, mode)
        return value if node.op == "+" else -value

    if isinstance(node, BinaryOp):
        left = ast_to_sympy(node.left, var_ns, user_vars, mode)
        right = ast_to_sympy(node.right, var_ns, user_vars, mode)

        if node.op in ("+", "-"):
            # Only checked in "numeric" mode: that's the only mode
            # where left/right can actually be real quantities (a
            # number times a unit). In "display"/"plot" mode, user
            # variables are fresh symbols with no unit info at all
            # (see module docstring), so there is nothing to check --
            # and that's fine, since the numeric-mode evaluation of
            # the same line already catches a genuine unit mismatch.
            if mode == "numeric":
                check_addition_dimensions(left, right)
            return left + right if node.op == "+" else left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right
        if node.op == "^":
            # Same reasoning as for "+"/"-" above (numeric mode only):
            # the exponent of a power must be dimensionless -- the BASE
            # may carry a unit ("a^2" -> m^2), but "5'm ^ 10'kg" is
            # meaningless. A dimensionless exponent that still contains
            # unit symbols (b/d with kg and g) comes back as a number.
            if mode == "numeric":
                right = require_dimensionless(right, "Exponent")
            return left ** right

        raise ValueError(f"Unknown operator {node.op!r}")

    if isinstance(node, Comparison):
        left = ast_to_sympy(node.left, var_ns, user_vars, mode)
        right = ast_to_sympy(node.right, var_ns, user_vars, mode)

        if mode == "display":
            return _DISPLAY_RELATIONS[node.op](left, right, evaluate=False)
        return compare_values(node.op, left, right)

    if isinstance(node, FunctionCall):
        if node.name == "if":
            return _eval_if(node, var_ns, user_vars, mode)
        if node.name in _PROPERTY_FUNCTIONS:
            return _eval_property(node, var_ns, user_vars, mode)

        args = tuple(ast_to_sympy(arg, var_ns, user_vars, mode) for arg in node.args)

        user_functions = _USER_FUNCTIONS.get()
        if user_functions and node.name in user_functions:
            return _call_user_function(
                user_functions[node.name], args, var_ns, user_vars, mode
            )

        func = table.get(node.name)

        # Numeric mode only (see the "+"/"-" comment above): exp/log/
        # sin/... need dimensionless arguments, atan2 needs matching
        # ones. Only applied if the name resolves to the real whitelist
        # function (func is not None) -- an unknown name stays an inert
        # placeholder, exactly as before.
        if mode == "numeric" and func is not None and callable(func):
            if node.name in DIMENSIONLESS_ARGUMENT_FUNCTIONS:
                args = tuple(
                    require_dimensionless(arg, f"Argument of {node.name}()")
                    for arg in args
                )
            elif node.name in DIMENSION_MATCHING_FUNCTIONS:
                args = require_same_dimension(node.name, args)

        if func is not None and callable(func):
            return func(*args)

        # Unknown function name (not in the whitelist): NEVER eval(),
        # NEVER a dynamic attribute/name lookup -- just a symbolic,
        # never-executed placeholder. This is exactly what sympify()
        # already produced for an unknown function (AppliedUndef), just
        # without the eval() path to get there.
        #
        # The placeholder is only inert as long as nothing EXECUTES the
        # expression: sp.lambdify() (plot(), and sp.nsolve() internally)
        # prints it as Python source and would turn it into a real call
        # "name(args)". Both paths therefore refuse placeholders first,
        # see mathlib.functions.reject_unknown_functions().
        return sp.Function(node.name)(*args)

    if isinstance(node, ListLiteral):
        return [ast_to_sympy(item, var_ns, user_vars, mode) for item in node.items]

    if isinstance(node, Subscript):
        # Plain "[]" item access on an already-built value (a Python
        # list, sp.Matrix, etc.) -- never a dynamic attribute/name
        # lookup, so this doesn't reopen the eval()-style security hole
        # the bridge exists to close (see module docstring).
        base = ast_to_sympy(node.base, var_ns, user_vars, mode)
        index = ast_to_sympy(node.index, var_ns, user_vars, mode)
        return element_at(base, index)

    if isinstance(node, Quantity):
        magnitude = ast_to_sympy(node.magnitude, var_ns, user_vars, mode)

        if isinstance(node.unit, Identifier) and node.unit.name == "degC":
            # Affine conversion (+273.15), same formula as the old
            # text-based expand_units(), just AST-based here.
            if isinstance(magnitude, sp.MatrixBase):
                # One unit for a whole vector: vec(20, 40, 60)'degC
                return magnitude.applyfunc(
                    lambda value: (value + _DEGC_OFFSET) * UNIT_NS["K"]
                )
            return (magnitude + _DEGC_OFFSET) * UNIT_NS["K"]

        return magnitude * _unit_node_to_sympy(node.unit)

    raise ValueError(f"Unknown AST node: {node!r}")


def _unit_node_to_sympy(node: Node):
    """Like ast_to_sympy(), but ONLY for the unit subtree of a Quantity
    (see parsing.parser.parse_unit_expr()): Identifier nodes are
    resolved EXCLUSIVELY against UNIT_NS, never against user variables
    or functions -- mirrors the grammar restriction the parser already
    enforces (only known, simple unit names can appear in this part of
    the AST at all).
    """
    if isinstance(node, Identifier):
        return UNIT_NS[node.name]

    if isinstance(node, Number):
        # Only possible as an exponent (e.g. "s^-2"), see
        # parsing.parser.parse_unit_exponent().
        return _number_to_sympy(node.value)

    if isinstance(node, BinaryOp):
        left = _unit_node_to_sympy(node.left)
        right = _unit_node_to_sympy(node.right)

        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right
        if node.op == "^":
            return left ** right

        raise ValueError(f"Unexpected operator in unit expression: {node.op!r}")

    raise ValueError(f"Unexpected node in unit expression: {node!r}")
