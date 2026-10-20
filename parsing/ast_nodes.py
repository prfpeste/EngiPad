from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class Number:
    """Numeric literal.

    The value is stored as a string, not a float, so no unwanted
    rounding happens while parsing. Conversion to an actual number type
    happens only where it's needed (SymPy bridge / LaTeX renderer).
    """

    value: str


@dataclass(frozen=True)
class Identifier:
    """A name: variable or constant, e.g. 'x', 'α', 'F_1'."""

    name: str


@dataclass(frozen=True)
class SheetRef:
    """A reference to a variable of ANOTHER sheet, written
    "Sheet::name" (e.g. "S1::L"). Read-only: it is resolved against the
    final value of that variable in the other sheet (see
    core/workbook.py), never assigned to.
    """

    sheet: str
    name: str


@dataclass(frozen=True)
class UnaryOp:
    """Unary operation, e.g. -x or +x."""

    op: str  # "+" or "-"
    operand: "Node"


@dataclass(frozen=True)
class BinaryOp:
    """Binary operation, e.g. a + b, a * b, a ^ b."""

    op: str  # "+", "-", "*", "/", "^"
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class FunctionCall:
    """Function call with any number of arguments, e.g. sin(x)."""

    name: str
    args: tuple["Node", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Quantity:
    """A number with a unit marker, e.g. "5'kg", "(-7)'degC" or
    "5'm^2/s" (compound unit).

    magnitude: the expression BEFORE the "'" (e.g. Number(5) or a
    UnaryOp/parenthesized expression).
    unit: the unit EXPRESSION after the "'" -- its own subtree of
    Identifier/Number/BinaryOp("*"|"/"|"^"), built by the narrow
    sub-grammar in parsing/parser.py (parse_unit_expr()). For a simple
    unit like "kg" this is just Identifier("kg").

    Deliberately a SEPARATE subtree instead of reusing the normal
    expression parser for the unit side: this way the unit side never
    touches the variable-rendering logic (var_to_latex()/known_vars
    overrides) -- a unit "kg" and a user variable of the same name can
    never collide, because the role (unit vs. variable) is already
    fixed by position in the tree (after "'" or not), not by name. For
    the same reason rendering/latex_input.py renders the unit subtree
    through its own _render_unit() function (Identifier ->
    \\mathrm{...} instead of italic), not through the normal _render().
    """

    magnitude: "Node"
    unit: "Node"


@dataclass(frozen=True)
class ListLiteral:
    """A list/row literal in square brackets, e.g. "[1, 2, 3]" or
    nested "[[1,2],[3,4]]" (matrix rows).

    Used exclusively as a function argument (e.g. "mat([[1,2],[3,4]])",
    "Matrix([[1,2],[3,5]])") -- not a standalone expression, no renderer
    support needed: if a ListLiteral shows up in a formula on the
    display side, raw_expr_to_latex() falls back to the plain-text
    renderer (see rendering/latex_input.py). For the SymPy bridge
    (mathlib/sympy_bridge.py) it becomes a (possibly nested) Python
    list, suitable for sp.Matrix()/mat().
    """

    items: tuple["Node", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Subscript:
    """Indexing/subscript access, e.g. "sol[0]" or "M[1][2]".

    base: the expression being indexed (e.g. Identifier("sol")).
    index: the index expression (e.g. Number("0")) -- any expression is
    allowed here (mirrors function-call arguments), so "a[i+1]" works
    too.

    Needed so a list result (e.g. from solve(), which can return several
    solutions) can be picked apart into individual values for further
    computation -- the same thing Python's native "sol[0]" does, just
    without ever going through eval(): the bridge (mathlib/sympy_bridge.py)
    applies this via the plain "[]" operator (base_value[index_value]) to
    an already-built value, never as a dynamic attribute/name lookup.
    """

    base: "Node"
    index: "Node"


# Union type for type annotations elsewhere (parser, renderer).
Node = Union[Number, Identifier, SheetRef, UnaryOp, BinaryOp, FunctionCall, Quantity, ListLiteral, Subscript]
