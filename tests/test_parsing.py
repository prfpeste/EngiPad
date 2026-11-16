"""
Tests for parsing/lexer.py + parsing/parser.py.

Deliberately WITHOUT going through core/formatter.py::raw_expr_to_latex():
that has a try/except fallback to the old regex path, which would
silently swallow a real parser error (the test would stay green despite
a broken parser, just with uglier legacy output). Here parsing.parser.parse()
is called directly -- a ValueError shows up as a test failure, not as a
quiet fallback.

The AST nodes in parsing/ast_nodes.py are @dataclass(frozen=True) with
an auto-generated __eq__ -- so expected trees can be compared directly
via "==" without manually walking the nodes.
"""

import pytest

from parsing.ast_nodes import BinaryOp, FunctionCall, Identifier, ListLiteral, Number, Quantity, Subscript, UnaryOp
from parsing.lexer import (
    APOSTROPHE, CARET, COMMA, EOF, IDENT, LPAREN, MINUS, NUMBER,
    PLUS, RPAREN, SLASH, STAR, tokenize,
)
from parsing.parser import parse


# ---------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------
class TestLexer:
    def test_simple_number(self):
        tokens = tokenize("42")
        assert [t.type for t in tokens] == [NUMBER, EOF]
        assert tokens[0].value == "42"

    def test_decimal_number(self):
        tokens = tokenize("3.14")
        assert tokens[0].type == NUMBER
        assert tokens[0].value == "3.14"

    def test_leading_dot_number(self):
        # ".5" is recognized as a number (ch=="." and the next character
        # is a digit), not as the start of an identifier.
        tokens = tokenize(".5")
        assert tokens[0].type == NUMBER
        assert tokens[0].value == ".5"

    def test_identifier_with_underscore_and_digits(self):
        tokens = tokenize("F_1")
        assert tokens[0].type == IDENT
        assert tokens[0].value == "F_1"

    def test_greek_identifier(self):
        tokens = tokenize("α")
        assert tokens[0].type == IDENT
        assert tokens[0].value == "α"

    def test_all_single_char_operators(self):
        tokens = tokenize("+-*/^(),'")
        types = [t.type for t in tokens]
        assert types == [
            PLUS, MINUS, STAR, SLASH, CARET, LPAREN, RPAREN, COMMA,
            APOSTROPHE, EOF,
        ]

    def test_whitespace_is_ignored(self):
        tokens = tokenize("  2   +   3  ")
        assert [t.type for t in tokens] == [NUMBER, PLUS, NUMBER, EOF]

    def test_unknown_character_raises(self):
        # ";", ":=", "|" are deliberately NOT part of the lexer's scope
        # -- must raise a ValueError for the fallback-aware caller,
        # never a silent/wrong token.
        for bad in [";", ":", "|"]:
            with pytest.raises(ValueError):
                tokenize(bad)

    def test_token_positions_are_tracked(self):
        tokens = tokenize("a + b")
        assert tokens[0].pos == 0
        assert tokens[1].pos == 2
        assert tokens[2].pos == 4


# ---------------------------------------------------------------------
# Basic precedence/associativity
# ---------------------------------------------------------------------
class TestParserPrecedence:
    def test_multiplication_binds_tighter_than_addition(self):
        ast = parse("2+3*4")
        assert ast == BinaryOp("+", Number("2"), BinaryOp("*", Number("3"), Number("4")))

    def test_division_binds_tighter_than_subtraction(self):
        ast = parse("10-6/2")
        assert ast == BinaryOp("-", Number("10"), BinaryOp("/", Number("6"), Number("2")))

    def test_addition_is_left_associative(self):
        ast = parse("a+b+c")
        assert ast == BinaryOp(
            "+", BinaryOp("+", Identifier("a"), Identifier("b")), Identifier("c")
        )

    def test_power_is_right_associative(self):
        ast = parse("x^y^z")
        assert ast == BinaryOp(
            "^", Identifier("x"), BinaryOp("^", Identifier("y"), Identifier("z"))
        )

    def test_unary_minus_binds_weaker_than_power(self):
        # -x^2 == -(x^2), NOT (-x)^2
        ast = parse("-x^2")
        assert ast == UnaryOp("-", BinaryOp("^", Identifier("x"), Number("2")))

    def test_parenthesized_base_changes_meaning(self):
        # (-x)^2 -- parentheses are "transparent" in the AST (no node of
        # their own), but they change WHAT sits at which spot in the tree.
        ast = parse("(-x)^2")
        assert ast == BinaryOp("^", UnaryOp("-", Identifier("x")), Number("2"))

    def test_negative_exponent(self):
        # 2^-1 == 2^(-1): unary is allowed on the right side of ^.
        ast = parse("2^-1")
        assert ast == BinaryOp("^", Number("2"), UnaryOp("-", Number("1")))

    def test_parentheses_are_transparent_in_ast(self):
        ast = parse("(2+3)*4")
        assert ast == BinaryOp("*", BinaryOp("+", Number("2"), Number("3")), Number("4"))


# ---------------------------------------------------------------------
# Function calls
# ---------------------------------------------------------------------
class TestParserFunctionCalls:
    def test_single_argument(self):
        ast = parse("sin(x)")
        assert ast == FunctionCall("sin", (Identifier("x"),))

    def test_multiple_arguments(self):
        ast = parse("atan2(1,1)")
        assert ast == FunctionCall("atan2", (Number("1"), Number("1")))

    def test_no_arguments(self):
        ast = parse("f()")
        assert ast == FunctionCall("f", ())

    def test_nested_function_calls(self):
        ast = parse("sin(cos(x))")
        assert ast == FunctionCall("sin", (FunctionCall("cos", (Identifier("x"),)),))

    def test_expression_as_argument(self):
        ast = parse("sqrt(x^2+1)")
        assert ast == FunctionCall(
            "sqrt",
            (BinaryOp("+", BinaryOp("^", Identifier("x"), Number("2")), Number("1")),),
        )


# ---------------------------------------------------------------------
# Quantity grammar (unit marker "'")
# ---------------------------------------------------------------------
class TestParserQuantity:
    def test_simple_unit(self):
        ast = parse("5'kg")
        assert ast == Quantity(Number("5"), Identifier("kg"))

    def test_unary_minus_belongs_to_magnitude(self):
        # -7'kg == Quantity(-7, kg), not -(7'kg) -- quantity calls
        # parse_unary() BEFORE the "'", so the sign is part of the
        # magnitude.
        ast = parse("-7'kg")
        assert ast == Quantity(UnaryOp("-", Number("7")), Identifier("kg"))

    def test_parenthesized_magnitude(self):
        ast = parse("(3+4)'kg")
        assert ast == Quantity(BinaryOp("+", Number("3"), Number("4")), Identifier("kg"))

    def test_compound_unit_division(self):
        # compound units like "m^2/s"
        ast = parse("5'm^2/s")
        assert ast == Quantity(
            Number("5"),
            BinaryOp(
                "/",
                BinaryOp("^", Identifier("m"), Number("2")),
                Identifier("s"),
            ),
        )

    def test_compound_unit_with_parentheses(self):
        ast = parse("1'W/(m*K)")
        assert ast == Quantity(
            Number("1"),
            BinaryOp("/", Identifier("W"), BinaryOp("*", Identifier("m"), Identifier("K"))),
        )

    def test_two_separate_quantities_multiplied(self):
        # "2'kg * 3'm" -- TWO separate quantities, not one compound unit
        # "kg*3". _unit_continues_after_op() must recognize that no
        # further unit follows "*" here.
        ast = parse("2'kg * 3'm")
        assert ast == BinaryOp(
            "*",
            Quantity(Number("2"), Identifier("kg")),
            Quantity(Number("3"), Identifier("m")),
        )

    def test_plus_ends_the_unit(self):
        # "5'kg + 3" == Quantity(5,kg) + 3, "+"/"-" are NOT part of the
        # narrow unit_expr grammar.
        ast = parse("5'kg + 3")
        assert ast == BinaryOp("+", Quantity(Number("5"), Identifier("kg")), Number("3"))

    def test_degc_simple(self):
        ast = parse("20'degC")
        assert ast == Quantity(Number("20"), Identifier("degC"))

    def test_degc_negative(self):
        ast = parse("-7'degC")
        assert ast == Quantity(UnaryOp("-", Number("7")), Identifier("degC"))

    def test_degc_cannot_combine_with_further_units(self):
        # degC is deliberately NEVER part of a compound unit.
        with pytest.raises(ValueError):
            parse("110'degC/s")

    def test_unknown_unit_name_raises(self):
        with pytest.raises(ValueError):
            parse("5'banana")

    def test_parenthesis_with_a_number_after_a_unit_is_a_new_factor(self):
        # Until 13.11.2026 "(" after "*" or "/" always continued the
        # unit, so these were errors ("Unexpected token NUMBER in unit
        # expression"). A unit can only start with a name, so a
        # parenthesis that starts with a number is an ordinary factor.
        node = parse("5'kg/(3'm)")
        assert isinstance(node, BinaryOp) and node.op == "/"
        assert node.left == Quantity(Number("5"), Identifier("kg"))
        assert node.right == Quantity(Number("3"), Identifier("m"))

        node = parse("2'kg*(0.1'm)^2")
        assert isinstance(node, BinaryOp) and node.op == "*"
        assert node.left == Quantity(Number("2"), Identifier("kg"))

    def test_parenthesis_with_a_unit_name_still_continues_the_unit(self):
        node = parse("5'W/(m*K)")
        assert isinstance(node, Quantity)
        node = parse("5'W/((m)*K)")
        assert isinstance(node, Quantity)


# ---------------------------------------------------------------------
# List/tuple literals (for matrices and sum/integrate bounds)
# ---------------------------------------------------------------------
class TestParserListLiterals:
    def test_bracket_list(self):
        ast = parse("[1,2,3]")
        assert ast == ListLiteral((Number("1"), Number("2"), Number("3")))

    def test_nested_bracket_list(self):
        ast = parse("[[1,2],[3,4]]")
        assert ast == ListLiteral((
            ListLiteral((Number("1"), Number("2"))),
            ListLiteral((Number("3"), Number("4"))),
        ))

    def test_empty_bracket_list(self):
        assert parse("[]") == ListLiteral(())

    def test_paren_comma_list_becomes_list_literal(self):
        ast = parse("(i,1,5)")
        assert ast == ListLiteral((Identifier("i"), Number("1"), Number("5")))

    def test_plain_parenthesized_expression_stays_transparent(self):
        # WITHOUT a comma, "(" stays a plain grouping -- NO ListLiteral,
        # unchanged behavior.
        ast = parse("(2+3)*4")
        assert ast == BinaryOp("*", BinaryOp("+", Number("2"), Number("3")), Number("4"))

    def test_list_literal_as_function_argument(self):
        ast = parse("mat([[1,2],[3,4]])")
        assert ast == FunctionCall("mat", (
            ListLiteral((
                ListLiteral((Number("1"), Number("2"))),
                ListLiteral((Number("3"), Number("4"))),
            )),
        ))


# ---------------------------------------------------------------------
# Subscript / indexing (e.g. sol[0], picking a single value out of a
# solve() result)
# ---------------------------------------------------------------------
class TestParserSubscript:
    def test_simple_index(self):
        ast = parse("sol[0]")
        assert ast == Subscript(Identifier("sol"), Number("0"))

    def test_chained_index(self):
        ast = parse("M[1][2]")
        assert ast == Subscript(
            Subscript(Identifier("M"), Number("1")), Number("2")
        )

    def test_expression_as_index(self):
        ast = parse("sol[i+1]")
        assert ast == Subscript(
            Identifier("sol"), BinaryOp("+", Identifier("i"), Number("1"))
        )

    def test_index_after_function_call(self):
        ast = parse("solve(x)[0]")
        assert ast == Subscript(FunctionCall("solve", (Identifier("x"),)), Number("0"))

    def test_power_of_indexed_value(self):
        # "sol[0]^2" == "(sol[0])^2" -- the "^" must not reach inside
        # the index.
        ast = parse("sol[0]^2")
        assert ast == BinaryOp("^", Subscript(Identifier("sol"), Number("0")), Number("2"))


# ---------------------------------------------------------------------
# Error cases / deliberately out of scope
# ---------------------------------------------------------------------
class TestParserErrors:
    def test_trailing_garbage_raises(self):
        with pytest.raises(ValueError):
            parse("2 + 3 )")

    def test_unbalanced_parenthesis_raises(self):
        with pytest.raises(ValueError):
            parse("(2 + 3")

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            parse("")

    def test_semicolon_raises(self):
        with pytest.raises(ValueError):
            parse("a = 1 ; b = 2")

    def test_walrus_assignment_raises(self):
        with pytest.raises(ValueError):
            parse("f := x^2")

    def test_pipe_desired_unit_raises(self):
        with pytest.raises(ValueError):
            parse("F | kN")
