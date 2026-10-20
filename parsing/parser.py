"""Recursive-descent parser for EngiPad expressions.

Builds an AST (parsing.ast_nodes) from the token list produced by
parsing.lexer. Classic precedence hierarchy (low -> high):

    additive   : term (("+" | "-") term)*
    term       : quantity (("*" | "/") quantity)*
    quantity   : unary ("'" unit_expr)?
    unary      : ("+" | "-") unary | power
    power      : postfix ("^" unary)?        # right-associative
    postfix    : primary ("[" additive "]")*  # e.g. sol[0], M[1][2]
    primary    : NUMBER
               | IDENT "::" IDENT              # sheet reference S1::L
               | IDENT ("(" (expr ("," expr)*)? ")")?
               | "(" expr ")"
               | "[" (expr ("," expr)*)? "]"

    Separate, deliberately NARROWER grammar for the unit side after "'"
    (compound units like "m^2/s", "W/(m*K)"):

    unit_expr    : unit_power (("*" | "/") unit_power)*
    unit_power   : unit_primary ("^" unit_exponent)?
    unit_exponent: ("+" | "-")? NUMBER
    unit_primary : IDENT | "(" unit_expr ")"

    "Narrower" means unit_expr only knows "*", "/", "^" and parentheses
    -- no "+"/"-", no function calls. A "+"/"-" therefore always cleanly
    ends the unit (e.g. "5'kg + 3" == Quantity(5,"kg") + 3, not
    "5 * (kg + 3)"). Every IDENT inside unit_expr must be a known,
    simple unit name (mathlib.units.UNIT_NAME_SET -- same list used for
    evaluation, single source of truth) and must not be in
    _DEFERRED_UNIT_NAMES (e.g. "degC": different semantics, handled
    separately).

Examples:
    -x^2      ==  -(x^2)          (unary binds weaker than ^)
    2^-1      ==  2^(-1)          (unary allowed on the right of ^)
    x^y^z     ==  x^(y^z)         (^ is right-associative)
    5'kg      ==  Quantity(5, kg)         (a leading sign belongs to
    -7'kg     ==  Quantity(-7, kg)         the magnitude, not the unit)
    5'm^2/s   ==  Quantity(5, m^2/s)      (compound unit)
    5'kg + 3  ==  Quantity(5, kg) + 3     ("+" ends the unit)

Raises ValueError on any syntax error or construct outside the
supported scope (";", ":=", "|" -- rejected already by the lexer, see
parsing/lexer.py). Callers (core/formatter.py, core/context.py) either
fall back to a plain-text renderer or surface the error to the user --
this parser never partially executes anything.
"""

from __future__ import annotations

from mathlib.units import UNIT_NAME_SET
from parsing.ast_nodes import (
    BinaryOp,
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
from parsing.lexer import (
    APOSTROPHE,
    CARET,
    COMMA,
    DCOLON,
    EOF,
    IDENT,
    LBRACKET,
    LPAREN,
    MINUS,
    NUMBER,
    PLUS,
    RBRACKET,
    RPAREN,
    SLASH,
    STAR,
    Token,
    tokenize,
)

# Units that ARE in UNIT_NAME_SET (needed for evaluation) but have
# different semantics than a plain scale factor, so they're deliberately
# excluded from this grammar level:
# - "degC": affine conversion (+273.15), its own display symbol
#   ("^\circ\mathrm{C}" instead of "\mathrm{degC}").
_DEFERRED_UNIT_NAMES = frozenset({"degC"})


class Parser:
    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, token_type: str) -> Token:
        tok = self._peek()
        if tok.type != token_type:
            raise ValueError(
                f"Expected '{token_type}', got '{tok.type}' ({tok.value!r}) "
                f"at position {tok.pos}."
            )
        return self._advance()

    # --- Precedence levels, low (weak) to high (strong) ---

    def parse_additive(self) -> Node:
        node = self.parse_term()

        while self._peek().type in (PLUS, MINUS):
            op_tok = self._advance()
            right = self.parse_term()
            node = BinaryOp(op_tok.value, node, right)

        return node

    def parse_term(self) -> Node:
        node = self.parse_quantity()

        while self._peek().type in (STAR, SLASH):
            op_tok = self._advance()
            right = self.parse_quantity()
            node = BinaryOp(op_tok.value, node, right)

        return node

    def parse_quantity(self) -> Node:
        node = self.parse_unary()

        if self._peek().type == APOSTROPHE:
            self._advance()

            if self._peek().type == IDENT and self._peek().value == "degC":
                # degC is not a pure scale-factor unit (affine
                # conversion, +273.15) -- handled as its own case rather
                # than inside parse_unit_expr(). It stays in
                # _DEFERRED_UNIT_NAMES so it can never appear as part of
                # a compound unit expression (e.g. "degC/s").
                self._advance()

                if self._peek().type in (STAR, SLASH, CARET):
                    # e.g. "110'degC/s" would otherwise be parsed as
                    # "Quantity(110, degC) / s" -- misleading, since
                    # evaluation treats degC as a synonym for K (without
                    # +273.15) in that position. Reject cleanly instead
                    # of implying a meaning that doesn't hold.
                    raise ValueError(
                        "degC cannot be combined with further units."
                    )

                node = Quantity(node, Identifier("degC"))
            else:
                unit_node = self.parse_unit_expr()
                node = Quantity(node, unit_node)

        return node

    # --- Narrow sub-grammar for the unit side after "'" (compound
    # units). Deliberately separate from the normal expression grammar
    # (parse_term/parse_power/parse_primary): only "*", "/", "^" and
    # parentheses -- no "+"/"-", no function calls. Every identifier
    # must be a known, simple unit name (UNIT_NAME_SET).

    def parse_unit_expr(self) -> Node:
        node = self.parse_unit_power()

        while self._peek().type in (STAR, SLASH) and self._unit_continues_after_op():
            op_tok = self._advance()
            right = self.parse_unit_power()
            node = BinaryOp(op_tok.value, node, right)

        return node

    def _unit_continues_after_op(self) -> bool:
        # Looks one token past the current "*"/"/" to check whether
        # another unit factor actually follows (IDENT or "(") rather
        # than e.g. a NEW, separate Quantity (NUMBER). Without this
        # check, "2'kg * 3'm" (two separate quantities) would wrongly
        # try to read "kg * 3" as ONE compound unit and fail on the
        # number.
        lookahead_pos = self._pos + 1
        lookahead = self._tokens[lookahead_pos] if lookahead_pos < len(self._tokens) else self._tokens[-1]
        return lookahead.type in (IDENT, LPAREN)

    def parse_unit_power(self) -> Node:
        base = self.parse_unit_primary()

        if self._peek().type == CARET:
            self._advance()
            exponent = self.parse_unit_exponent()
            return BinaryOp("^", base, exponent)

        return base

    def parse_unit_exponent(self) -> Node:
        # Integer exponents only, e.g. "m^2", "s^-2" -- no identifiers
        # or expressions as a unit exponent.
        sign = ""
        if self._peek().type in (PLUS, MINUS):
            sign = self._advance().value

        num_tok = self._expect(NUMBER)
        return Number(sign + num_tok.value)

    def parse_unit_primary(self) -> Node:
        tok = self._peek()

        if tok.type == IDENT:
            self._advance()

            if tok.value not in UNIT_NAME_SET or tok.value in _DEFERRED_UNIT_NAMES:
                raise ValueError(
                    f"Unknown or not-yet-supported unit '{tok.value}' "
                    f"at position {tok.pos}."
                )

            return Identifier(tok.value)

        if tok.type == LPAREN:
            self._advance()
            node = self.parse_unit_expr()
            self._expect(RPAREN)
            return node

        raise ValueError(
            f"Unexpected token '{tok.type}' ({tok.value!r}) in unit "
            f"expression at position {tok.pos}."
        )

    def parse_unary(self) -> Node:
        if self._peek().type in (PLUS, MINUS):
            op_tok = self._advance()
            operand = self.parse_unary()
            return UnaryOp(op_tok.value, operand)

        return self.parse_power()

    def parse_power(self) -> Node:
        base = self.parse_postfix()

        if self._peek().type == CARET:
            self._advance()
            # Right-associative: the exponent may itself be unary/power,
            # e.g. 2^-1 or x^y^z.
            exponent = self.parse_unary()
            return BinaryOp("^", base, exponent)

        return base

    def parse_postfix(self) -> Node:
        # Postfix indexing, e.g. "sol[0]" or "M[1][2]" (repeatable, so
        # a chained "[i][j]" works too). Deliberately its own level
        # between primary and power: binds as tightly as a primary
        # itself, and "sol[0]^2" should mean "(sol[0])^2", not
        # "sol[0^2]" -- the "[" only ever attaches to what was just
        # parsed, never reaches into the exponent.
        node = self.parse_primary()

        while self._peek().type == LBRACKET:
            self._advance()
            index = self.parse_additive()
            self._expect(RBRACKET)
            node = Subscript(node, index)

        return node

    def parse_primary(self) -> Node:
        tok = self._peek()

        if tok.type == NUMBER:
            self._advance()
            return Number(tok.value)

        if tok.type == IDENT:
            self._advance()

            if self._peek().type == DCOLON:
                # Sheet reference "Sheet::name" (read-only, see
                # parsing.ast_nodes.SheetRef).
                self._advance()
                name_tok = self._expect(IDENT)
                return SheetRef(tok.value, name_tok.value)

            if self._peek().type == LPAREN:
                self._advance()
                args = self.parse_arg_list()
                self._expect(RPAREN)
                return FunctionCall(tok.value, tuple(args))

            return Identifier(tok.value)

        if tok.type == LPAREN:
            self._advance()
            first = self.parse_additive()

            if self._peek().type == COMMA:
                # Parenthesized comma list, e.g. "(i, 1, 5)" in
                # sum(i, (i,1,5)) or "(x, 0, π)" in integrate(f, (x,0,π))
                # -- SymPy's convention for (variable, lower, upper).
                # Reuses ListLiteral (SymPy accepts both list and tuple
                # here) instead of a dedicated tuple node type. A plain
                # "(a+b)" WITHOUT a comma stays transparent (see below)
                # -- only a comma switches to list semantics.
                items = [first]
                while self._peek().type == COMMA:
                    self._advance()
                    items.append(self.parse_additive())
                self._expect(RPAREN)
                return ListLiteral(tuple(items))

            self._expect(RPAREN)
            return first

        if tok.type == LBRACKET:
            return self.parse_list_literal()

        raise ValueError(
            f"Unexpected token '{tok.type}' ({tok.value!r}) at position {tok.pos}."
        )

    def parse_list_literal(self) -> Node:
        # List literal, e.g. "[1, 2, 3]" or nested "[[1,2],[3,4]]"
        # (matrix rows) -- used exclusively as a function argument
        # (mat(...), Matrix(...)).
        self._expect(LBRACKET)
        items: list[Node] = []

        if self._peek().type != RBRACKET:
            items.append(self.parse_additive())

            while self._peek().type == COMMA:
                self._advance()
                items.append(self.parse_additive())

        self._expect(RBRACKET)
        return ListLiteral(tuple(items))

    def parse_arg_list(self) -> list[Node]:
        args: list[Node] = []

        if self._peek().type == RPAREN:
            return args

        args.append(self.parse_additive())

        while self._peek().type == COMMA:
            self._advance()
            args.append(self.parse_additive())

        return args


def parse(text: str) -> Node:
    """Parses an expression string to an AST.

    Raises ValueError on syntax errors or unsupported constructs.
    """

    tokens = tokenize(text)
    parser = Parser(tokens)
    node = parser.parse_additive()

    trailing = parser._peek()
    if trailing.type != EOF:
        raise ValueError(
            f"Unexpected token '{trailing.type}' ({trailing.value!r}) "
            f"after end of expression, position {trailing.pos}."
        )

    return node
