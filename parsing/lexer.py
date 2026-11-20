"""Lexer (tokenizer) for the EngiPad expression parser.

Scope:
    Numbers (incl. scientific notation "1.5e-3"), identifiers (incl. unicode like Greek letters and
    underscore subscripts), operators + - * / ^, parentheses (),
    brackets [], comma (function arguments), unit marker ' (simple
    units like "kg", "m" as well as compound units like "m^2/s" --
    see parsing/parser.py for the full unit grammar).

"::" (sheet reference "Sheet::variable") is one token of its own; a
single ":" is still rejected.

Comparison operators: < > <= >= == != (also typed as the single
characters ≤ ≥ ≠). A single "=" is NOT a token: the assignment is
split off before the parser runs (core/context.py::find_assignment()).

Deliberately NOT included: ";" separators, ":=", "|unit". These
characters raise a ValueError here, so callers (core/formatter.py) can
cleanly fall back to a plain-text renderer for anything outside this
grammar's scope.
"""

from __future__ import annotations

from dataclasses import dataclass


# Token types as plain string constants (no enum needed for this scope).
NUMBER = "NUMBER"
IDENT = "IDENT"
PLUS = "PLUS"
MINUS = "MINUS"
STAR = "STAR"
SLASH = "SLASH"
CARET = "CARET"
LPAREN = "LPAREN"
RPAREN = "RPAREN"
LBRACKET = "LBRACKET"
RBRACKET = "RBRACKET"
COMMA = "COMMA"
APOSTROPHE = "APOSTROPHE"
DCOLON = "DCOLON"  # "::" -- sheet reference, e.g. S1::L
COMPARE = "COMPARE"  # value: "<", ">", "<=", ">=", "==" or "!="
EOF = "EOF"

# Name of the percent unit ("40'%"), an IDENT token after the "'".
PERCENT = "%"

_SINGLE_CHAR_TOKENS = {
    "+": PLUS,
    "-": MINUS,
    "*": STAR,
    "/": SLASH,
    "^": CARET,
    "(": LPAREN,
    ")": RPAREN,
    "[": LBRACKET,
    "]": RBRACKET,
    ",": COMMA,
    "'": APOSTROPHE,
}


_CARET_LOOKALIKES = frozenset({"\u02c6", "\uff3e"})

_TWO_CHAR_COMPARISONS = frozenset({"<=", ">=", "==", "!="})
_ONE_CHAR_COMPARISONS = {
    "<": "<",
    ">": ">",
    "\u2264": "<=",
    "\u2265": ">=",
    "\u2260": "!=",
}


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    pos: int  # position in the source text, for error messages


def _is_ident_start(ch: str) -> bool:
    return (ch.isalpha() or ch == "_") and ch not in _CARET_LOOKALIKES


def _is_ident_continue(ch: str) -> bool:
    return (ch.isalnum() or ch == "_") and ch not in _CARET_LOOKALIKES


def tokenize(text: str) -> list[Token]:
    """Splits an expression string into a list of tokens (EOF-terminated).

    Raises ValueError on unknown characters (e.g. ; : |), so the caller
    can fall back to a different code path.
    """

    tokens: list[Token] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch.isspace():
            i += 1
            continue

        # "^" typed with a dead key or another keyboard layout can come
        # out as a look-alike: "ˆ" (U+02C6, which Python even counts as
        # a letter, so "2ˆ2" became the name "ˆ2") or the full-width
        # "＾" (U+FF3E). Both mean the power operator.
        if ch in _CARET_LOOKALIKES:
            tokens.append(Token(CARET, "^", i))
            i += 1
            continue

        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            start = i
            seen_dot = False

            while i < n and (text[i].isdigit() or (text[i] == "." and not seen_dot)):
                if text[i] == ".":
                    seen_dot = True
                i += 1

            # Scientific notation: "1.5e-3", "2E6". Only taken as an
            # exponent if digits really follow the "e" (optionally
            # after a sign) -- "2e" or "3e-x" stay what they were (a
            # number followed by an identifier, i.e. a syntax error).
            if i < n and text[i] in "eE":
                j = i + 1
                if j < n and text[j] in "+-":
                    j += 1
                if j < n and text[j].isdigit():
                    while j < n and text[j].isdigit():
                        j += 1
                    i = j

            tokens.append(Token(NUMBER, text[start:i], start))
            continue

        if _is_ident_start(ch):
            start = i
            i += 1

            while i < n and _is_ident_continue(text[i]):
                i += 1

            tokens.append(Token(IDENT, text[start:i], start))
            continue

        if ch == ":" and text[i + 1:i + 2] == ":":
            tokens.append(Token(DCOLON, "::", i))
            i += 2
            continue

        two = text[i:i + 2]
        if two in _TWO_CHAR_COMPARISONS:
            tokens.append(Token(COMPARE, two, i))
            i += 2
            continue

        if ch in _ONE_CHAR_COMPARISONS:
            tokens.append(Token(COMPARE, _ONE_CHAR_COMPARISONS[ch], i))
            i += 1
            continue

        if ch in _SINGLE_CHAR_TOKENS:
            tokens.append(Token(_SINGLE_CHAR_TOKENS[ch], ch, i))
            i += 1
            continue

        # Percent as an input unit, "40'%": only right after the unit
        # marker. Anywhere else "%" stays an unexpected character (no
        # modulo operator, no variable named "%").
        if ch == "%" and tokens and tokens[-1].type == APOSTROPHE:
            tokens.append(Token(IDENT, PERCENT, i))
            i += 1
            continue

        raise ValueError(f"Unexpected character '{ch}' at position {i}.")

    tokens.append(Token(EOF, "", n))
    return tokens
