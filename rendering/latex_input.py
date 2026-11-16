"""AST -> LaTeX renderer for the input side of a formula.

Mirrors parsing/parser.py: the same precedence levels are used here to
add parentheses only where they're needed for meaning (not wherever
they happened to be in the original text).

Falls back to core/formatter.py's plain-text renderer for anything
outside the supported grammar. Identifier rendering (Greek letters,
F_1 -> F_{\\text{1}} etc.) reuses the existing
mathlib.units.var_to_latex() logic rather than duplicating it.
"""

from __future__ import annotations

from mathlib.units import UNIT_DISPLAY_OVERRIDES, var_to_latex, wrap_unit_latex
from parsing.ast_nodes import (
    BinaryOp, Comparison, FunctionCall, Identifier, ListLiteral, Node, Number, Quantity,
    SheetRef, Subscript, UnaryOp,
)

# Precedence levels, identical to the grammar in parsing/parser.py.
_COMPARISON_PREC = 0
_ADDITIVE_PREC = 1
_MULTIPLICATIVE_PREC = 2
_UNARY_PREC = 3
_POWER_PREC = 4

_BINARY_PREC = {
    "+": _ADDITIVE_PREC,
    "-": _ADDITIVE_PREC,
    "*": _MULTIPLICATIVE_PREC,
    "/": _MULTIPLICATIVE_PREC,
    "^": _POWER_PREC,
}

# Known functions with their own LaTeX macro (no \left(\right) suffix needed).
_LATEX_FUNCTION_NAMES = {
    "sin": r"\sin", "cos": r"\cos", "tan": r"\tan",
    "asin": r"\arcsin", "acos": r"\arccos", "atan": r"\arctan",
    "sinh": r"\sinh", "cosh": r"\cosh", "tanh": r"\tanh",
    "exp": r"\exp", "ln": r"\ln", "log": r"\log",
    # Not real LaTeX macros of their own -- \operatorname{...} the same
    # way the generic fallback below would, just with the conventional
    # capitalized math notation (Re/Im) instead of the lowercase
    # function names "re"/"im" the user actually types.
    "re": r"\operatorname{Re}",
    "im": r"\operatorname{Im}",
    "min": r"\min", "max": r"\max",
    "erf": r"\operatorname{erf}", "erfc": r"\operatorname{erfc}",
    "sign": r"\operatorname{sgn}",
}

_BESSEL_SYMBOLS = {"besselj": "J", "bessely": "Y", "besseli": "I", "besselk": "K"}

COMPARISON_LATEX = {
    "<": "<", ">": ">", "<=": r"\le", ">=": r"\ge", "==": "=", "!=": r"\ne",
}

# Known constants that SymPy prints as plain identifiers in str(expr)
# (e.g. "pi*x", "exp(x) + I"). Without this mapping they'd appear as
# literal text "pi"/"I"/... via var_to_latex() instead of \pi, i,
# \infty, e.
_IDENTIFIER_LATEX_OVERRIDES = {
    "pi": r"\pi",
    "I": "i",
    "oo": r"\infty",
    "E": "e",
    # Internal placeholders from mathlib.units.normalize_identifiers(),
    # which replace the unicode symbols "∞" and "ⅈ" BEFORE the line
    # reaches this parser (see context.py::eval_line()).
    "__calcpad_infty__": r"\infty",
    "__calcpad_imag__": "i",
}


def to_latex(node: Node, known_vars: frozenset[str] = frozenset()) -> str:
    """Renders an AST node as a complete LaTeX expression (no outer wrapping).

    known_vars: names the user has defined as a variable themselves
    (e.g. ctx.user_vars). The constant overrides (pi, I, oo, E) are NOT
    applied for these names -- otherwise a variable "I" (e.g. electric
    current) would wrongly render as the imaginary unit "i": both cases
    produce the same text "I" via str(sympy_expr) and are textually
    indistinguishable, hence "user variable wins over constant
    override".
    """

    return _render(node, parent_prec=0, known_vars=known_vars)


def _render(node: Node, parent_prec: int, known_vars: frozenset[str]) -> str:
    if isinstance(node, Number):
        mantissa, e, exponent = node.value.replace("E", "e").partition("e")
        if not e:
            return node.value
        # "1.5e-3" is shown like the typed-out "1.5*10^-3". It is a
        # product, so it needs the parentheses of one (e.g. as the base
        # of a power).
        text = rf"{mantissa} \cdot 10^{{{int(exponent)}}}"
        return _wrap_if_needed(text, _MULTIPLICATIVE_PREC, parent_prec)

    if isinstance(node, Identifier):
        if node.name in _IDENTIFIER_LATEX_OVERRIDES and node.name not in known_vars:
            return _IDENTIFIER_LATEX_OVERRIDES[node.name]
        return var_to_latex(node.name)

    if isinstance(node, SheetRef):
        return var_to_latex(f"{node.sheet}::{node.name}")

    if isinstance(node, UnaryOp):
        operand = _render(node.operand, _UNARY_PREC, known_vars)
        text = f"{node.op}{operand}"
        return _wrap_if_needed(text, _UNARY_PREC, parent_prec)

    if isinstance(node, FunctionCall):
        return _render_function_call(node, parent_prec, known_vars)

    if isinstance(node, Quantity):
        return _render_quantity(node, parent_prec, known_vars)

    if isinstance(node, Subscript):
        return _render_subscript(node, parent_prec, known_vars)

    if isinstance(node, BinaryOp):
        return _render_binary_op(node, parent_prec, known_vars)

    if isinstance(node, ListLiteral):
        # "[1, 2, 3]" / "(x, 0, 5)" as an argument, e.g. of solve() or
        # nsolve(). (A matrix typed as mat([[..], [..]]) is drawn as a
        # matrix, see _render_function_call().)
        items = ", ".join(_render(item, 0, known_vars) for item in node.items)
        return rf"\left[{items}\right]"

    if isinstance(node, Comparison):
        left = _render(node.left, _ADDITIVE_PREC, known_vars)
        right = _render(node.right, _ADDITIVE_PREC, known_vars)
        text = f"{left} {COMPARISON_LATEX[node.op]} {right}"
        return _wrap_if_needed(text, _COMPARISON_PREC, parent_prec)

    raise TypeError(f"Unknown AST node type: {type(node).__name__}")


def _render_binary_op(node: BinaryOp, parent_prec: int, known_vars: frozenset[str]) -> str:
    prec = _BINARY_PREC[node.op]

    if node.op == "^":
        # Right-associative: x^y^z == x^(y^z), so the right side needs
        # NO parentheses at equal precedence, but the left side does
        # ((a^b)^c != a^b^c).
        base = _render(node.left, prec + 1, known_vars)
        exponent = _render(node.right, 0, known_vars)
        text = f"{base}^{{{exponent}}}"
        return _wrap_if_needed(text, prec, parent_prec)

    if node.op == "/":
        # \frac{...}{...} instead of "a / b": the fraction bar already
        # groups unambiguously, so parent_prec=0 for numerator/
        # denominator (no unnecessary inner parentheses) and NO
        # _wrap_if_needed at the end (a fraction never needs outer
        # parentheses, it's already visually self-contained -- this also
        # resolves the associativity question for a/b/c ->
        # \frac{\frac{a}{b}}{c}).
        numerator = _render(node.left, 0, known_vars)
        denominator = _render(node.right, 0, known_vars)
        return rf"\frac{{{numerator}}}{{{denominator}}}"

    left = _render(node.left, prec, known_vars)

    if node.op == "-":
        # Left-associative and non-commutative: a - (b - c) != a - b - c,
        # so the right side ALWAYS needs parentheses at equal precedence.
        right = _render(node.right, prec + 1, known_vars)
    else:
        right = _render(node.right, prec, known_vars)

    if node.op == "*":
        text = f"{left} \\cdot {right}"
    else:
        text = f"{left} {node.op} {right}"

    return _wrap_if_needed(text, prec, parent_prec)


_DEGC_LATEX = UNIT_DISPLAY_OVERRIDES["degC"]


def _render_quantity(node: Quantity, parent_prec: int, known_vars: frozenset[str]) -> str:
    # magnitude: the "'" visually binds the unit like a multiplication
    # (mag\,unit), so magnitude needs the same parenthesization as a
    # multiplication operand -- otherwise e.g. "(3+4)'kg" (parentheses
    # are transparent in the AST, parse_unary() returns the
    # BinaryOp("+",...) directly) would render as "3 + 4\,\mathrm{kg}",
    # which looks like "3 + (4 kg)" instead of "(3+4) kg".
    magnitude = _render(node.magnitude, _MULTIPLICATIVE_PREC, known_vars)

    if isinstance(node.unit, Identifier) and node.unit.name == "degC":
        # "^\circ\mathrm{C}" instead of "\mathrm{degC}", consistent with
        # mathlib.units.DESIRED_UNIT_MAP["degC"].
        text = rf"{magnitude}\,{wrap_unit_latex(_DEGC_LATEX)}"
    else:
        # Unit rendered through its OWN function, not the normal
        # _render(): the role "unit, not variable" is already fixed by
        # position in the tree (right of "'"), not by name -- see the
        # Quantity docstring in parsing/ast_nodes.py.
        unit_latex = _render_unit(node.unit, 0)
        text = rf"{magnitude}\,{wrap_unit_latex(unit_latex)}"

    # Treated like a unary expression: as the base of a power (e.g.
    # "(5'kg)^2") the WHOLE quantity must be parenthesized, otherwise
    # "^{2}" would visually attach only to the unit ("5 kg^2" looks like
    # "5 * kg^2", not "(5 kg)^2"). As a factor in a multiplication or a
    # term in a sum, no parentheses are needed ("5'kg * 3" stays
    # "5\,kg \cdot 3").
    return _wrap_if_needed(text, _UNARY_PREC, parent_prec)


def _render_subscript(node: Subscript, parent_prec: int, known_vars: frozenset[str]) -> str:
    # Postfix, binds as tightly as a primary -- e.g. a low-precedence
    # base like "(a+b)[0]" still needs its own parentheses, hence
    # rendering the base at _POWER_PREC.
    base = _render(node.base, _POWER_PREC, known_vars)
    index = _render(node.index, 0, known_vars)
    text = f"{base}[{index}]"
    return _wrap_if_needed(text, _POWER_PREC, parent_prec)


def _render_unit(node: Node, parent_prec: int) -> str:
    """Renders the unit subtree of a Quantity.

    Separate from _render(): the unit subtree only contains
    Identifier/Number/BinaryOp("*"|"/"|"^") (see
    parsing/parser.py::parse_unit_expr()) and needs different rules
    than normal expressions:
    - Identifier -> upright (\\mathrm{...}) instead of italic like a
      variable (never through var_to_latex()/known_vars, see the
      Quantity docstring).
    - "*" between units -> thin space ("\\,") instead of "\\cdot",
      consistent with mathlib.units.PRETTY_UNITS/DESIRED_UNIT_MAP
      (e.g. "kg\\,m" instead of "kg \\cdot m").
    - "/" and "^" as usual, via \\frac{{...}}{{...}} and ^{{...}}.
    """

    if isinstance(node, Identifier):
        if node.name in UNIT_DISPLAY_OVERRIDES:
            return UNIT_DISPLAY_OVERRIDES[node.name]
        safe_name = node.name.replace("\\", r"\\").replace("_", r"\_")
        return rf"\mathrm{{{safe_name}}}"

    if isinstance(node, Number):
        return node.value

    if isinstance(node, BinaryOp):
        prec = _BINARY_PREC[node.op]

        if node.op == "^":
            base = _render_unit(node.left, prec + 1)
            if base.startswith("^"):
                # "^\circ" as a power base would give a double
                # superscript ("^\circ^{2}") -- brace it.
                base = "{" + base + "}"
            exponent = _render_unit(node.right, 0)
            text = f"{base}^{{{exponent}}}"
            return _wrap_if_needed(text, prec, parent_prec)

        if node.op == "/":
            numerator = _render_unit(node.left, 0)
            denominator = _render_unit(node.right, 0)
            return rf"\frac{{{numerator}}}{{{denominator}}}"

        # "*"
        left = _render_unit(node.left, prec)
        right = _render_unit(node.right, prec)
        text = rf"{left}\,{right}"
        return _wrap_if_needed(text, prec, parent_prec)

    raise TypeError(f"Unexpected node type in unit expression: {type(node).__name__}")


def _render_function_call(node: FunctionCall, parent_prec: int, known_vars: frozenset[str]) -> str:
    if node.name == "Eq" and len(node.args) == 2:
        # Eq(a, b) should look like a handwritten equation "a = b", not
        # a generic function call \operatorname{Eq}(a, b).
        left = _render(node.args[0], 0, known_vars)
        right = _render(node.args[1], 0, known_vars)
        return f"{left} = {right}"

    if node.name == "exp" and len(node.args) == 1:
        # exp(x) rendered as "e^{x}" instead of "\exp(x)". Needs the same
        # parenthesization as a real power node (see _render_binary_op,
        # the "^" case), since the result itself contains a "^": e.g.
        # exp(x)^2 would otherwise become the invalid double-superscript
        # "e^{x}^{2}".
        exponent = _render(node.args[0], 0, known_vars)
        text = f"e^{{{exponent}}}"
        return _wrap_if_needed(text, _POWER_PREC, parent_prec)

    if (
        node.name == "nintegrate" and len(node.args) == 2
        and isinstance(node.args[1], ListLiteral) and len(node.args[1].items) == 3
    ):
        # nintegrate(f, (x, a, b)) like a handwritten definite integral.
        variable, lower, upper = (_render(item, 0, known_vars) for item in node.args[1].items)
        integrand = _render(node.args[0], _MULTIPLICATIVE_PREC, known_vars)
        text = rf"\int_{{{lower}}}^{{{upper}}} {integrand}\,\mathrm{{d}}{variable}"
        return _wrap_if_needed(text, _ADDITIVE_PREC, parent_prec)

    if node.name in ("mat", "Matrix") and len(node.args) == 1 and isinstance(node.args[0], ListLiteral):
        # mat([[1, 2], [3, 4]]) is drawn as the matrix it is; a flat
        # list mat([1, 2, 3]) is a column, as SymPy builds it.
        rows = node.args[0].items
        if rows and all(isinstance(row, ListLiteral) for row in rows):
            lines = [" & ".join(_render(cell, 0, known_vars) for cell in row.items) for row in rows]
        else:
            lines = [_render(cell, 0, known_vars) for cell in rows]
        body = r"\\".join(lines)
        return rf"\left[\begin{{matrix}}{body}\end{{matrix}}\right]"

    if node.name in ("prop", "haprop"):
        # The fluid is a name, not a variable: upright ("Water").
        rendered = [_render(arg, 0, known_vars) for arg in node.args]
        if node.name == "prop" and len(node.args) == 6 and isinstance(node.args[5], Identifier):
            rendered[5] = rf"\mathrm{{{node.args[5].name}}}"
        return rf"\operatorname{{{node.name}}}\left({', '.join(rendered)}\right)"

    args_latex = ", ".join(_render(arg, 0, known_vars) for arg in node.args)

    if node.name == "abs" and len(node.args) == 1:
        # |x| like in a handwritten formula (the result side already
        # looks like this: SymPy prints Abs() with bars too).
        return rf"\left|{args_latex}\right|"

    if node.name == "sqrt" and len(node.args) == 1:
        return rf"\sqrt{{{args_latex}}}"

    if node.name in _BESSEL_SYMBOLS and len(node.args) == 2:
        # besselj(n, x) as J_n(x), like in the textbooks.
        order = _render(node.args[0], 0, known_vars)
        argument = _render(node.args[1], 0, known_vars)
        return rf"{_BESSEL_SYMBOLS[node.name]}_{{{order}}}\left({argument}\right)"

    if node.name == "floor" and len(node.args) == 1:
        return rf"\left\lfloor {args_latex} \right\rfloor"

    if node.name == "ceil" and len(node.args) == 1:
        return rf"\left\lceil {args_latex} \right\rceil"

    if f"{node.name}()" in known_vars:
        # A function the user defined ("f(x) = ..."): italic like a
        # variable, f(x), not upright like a built-in operator name.
        # core/context.py::known_names() lists functions as "name()".
        return rf"{var_to_latex(node.name)}\left({args_latex}\right)"

    if node.name in _LATEX_FUNCTION_NAMES:
        return rf"{_LATEX_FUNCTION_NAMES[node.name]}\left({args_latex}\right)"

    safe_name = node.name.replace("\\", r"\\").replace("_", r"\_")
    return rf"\operatorname{{{safe_name}}}\left({args_latex}\right)"


def _wrap_if_needed(text: str, prec: int, parent_prec: int) -> str:
    if prec < parent_prec:
        return rf"\left({text}\right)"
    return text
