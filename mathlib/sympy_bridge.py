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

import sympy as sp

from mathlib.functions import DISPLAY_FUNCTIONS, NUMERIC_FUNCTIONS, PLOT_FUNCTIONS
from mathlib.units import UNIT_NS, check_addition_dimensions
from parsing.ast_nodes import (
    BinaryOp,
    FunctionCall,
    Identifier,
    ListLiteral,
    Node,
    Number,
    Quantity,
    Subscript,
    UnaryOp,
)

_FUNCTION_TABLES = {
    "numeric": NUMERIC_FUNCTIONS,
    "display": DISPLAY_FUNCTIONS,
    "plot": PLOT_FUNCTIONS,
}

_DEGC_OFFSET = sp.Float("273.15")


def _number_to_sympy(value: str):
    return sp.Float(value) if "." in value else sp.Integer(value)


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
            if name in var_ns:
                return var_ns[name]
        elif name in user_vars:
            return sp.Symbol(name)

        if name in table and not callable(table[name]):
            return table[name]

        return sp.Symbol(name)

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
            return left ** right

        raise ValueError(f"Unknown operator {node.op!r}")

    if isinstance(node, FunctionCall):
        args = tuple(ast_to_sympy(arg, var_ns, user_vars, mode) for arg in node.args)
        func = table.get(node.name)

        if func is not None and callable(func):
            return func(*args)

        # Unknown function name (not in the whitelist): NEVER eval(),
        # NEVER a dynamic attribute/name lookup -- just a symbolic,
        # never-executed placeholder. This is exactly what sympify()
        # already produced for an unknown function (AppliedUndef), just
        # without the eval() path to get there.
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
        return base[index]

    if isinstance(node, Quantity):
        magnitude = ast_to_sympy(node.magnitude, var_ns, user_vars, mode)

        if isinstance(node.unit, Identifier) and node.unit.name == "degC":
            # Affine conversion (+273.15), same formula as the old
            # text-based expand_units(), just AST-based here.
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
