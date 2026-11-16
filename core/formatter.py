from functools import lru_cache

import sympy as sp
import re
from sympy.core.relational import Relational
from sympy import latex
from sympy.physics.units import K
from sympy.physics.units.systems.si import SI

from mathlib.units import (
    BASE_UNITS,
    PRETTY_UNITS,
    UNIT_VALUES,
    VAR_SYMBOL_PREFIX,
    convert_to_cached,
    error_to_latex,
    format_magnitude_decimal,
    format_scalar_with_unit,
    scalar_parts,
    make_safe_text_latex,
    text_to_latex,
    normalize_numeric_quantity,
    split_magnitude_unit,
    unit_to_pretty_latex,
    var_to_latex,
    wrap_unit_latex,
)
from parsing.ast_nodes import ListLiteral
from parsing.parser import parse
from rendering.latex_input import COMPARISON_LATEX, to_latex


@lru_cache(maxsize=4096)
def expr_to_latex(expr, user_vars_key=()):
    symbol_names = {}
    free_symbols = getattr(expr, "free_symbols", set())

    for sym in free_symbols:
        if sym in UNIT_VALUES:
            continue

        sym_name = str(sym)
        if sym_name.startswith(VAR_SYMBOL_PREFIX):
            sym_name = sym_name[len(VAR_SYMBOL_PREFIX):]

        symbol_names[sym] = var_to_latex(sym_name)

    for name in user_vars_key:
        sym = sp.Symbol(name)
        if sym not in UNIT_VALUES:
            symbol_names[sym] = var_to_latex(name)

    return latex(
        expr,
        mul_symbol="dot",
        symbol_names=symbol_names,
    ).replace(r"\frac{d}{d x}", r"\frac{d}{dx}")


def render_plain_text_item(text: str, bold: bool = False):
    content = text_to_latex(text, "textbf" if bold else "text")
    return {
        "type": "latex",
        "content": content,
    }


def _raw_expr_to_latex_legacy(raw_expr: str) -> str:
    """Plain-text/regex fallback for raw input -> LaTeX.

    Used for anything our AST parser (parsing/parser.py) doesn't cover:
    the unit marker "'", ";" separators, ":=", "|unit", and the
    comparison operators ">=" / "<=".
    """

    latex_text = raw_expr.strip()

    latex_text = latex_text.replace("\\", r"\\")
    latex_text = latex_text.replace(">=", r"\ge ")
    latex_text = latex_text.replace("<=", r"\le ")

    # multiplication
    latex_text = latex_text.replace("*", r" \cdot ")

    # powers: x^2 -> x^{2}, (x+1)^3 -> (x+1)^{3}
    latex_text = re.sub(r'([A-Za-z0-9\)\]])\^([A-Za-z0-9\(\[]+)', r'\1^{\2}', latex_text)

    # escape underscores without breaking LaTeX commands
    latex_text = latex_text.replace("_", r"\_")

    latex_text = re.sub(r'\s+', ' ', latex_text).strip()

    return latex_text



def raw_expr_to_latex(raw_expr: str, known_vars: frozenset = frozenset()) -> str:
    """Converts the raw input (right-hand side of "=") to LaTeX.

    Tries the AST parser first; falls back to the plain-text renderer
    for anything outside its scope, so existing input keeps rendering
    the same way it always has.

    known_vars: variable names the user has defined (ctx.user_vars),
    passed through to the renderer so e.g. a user's own variable "I"
    doesn't collide with the imaginary unit (see
    rendering/latex_input.py::to_latex).
    """

    if raw_expr is None:
        return ""

    stripped = raw_expr.strip()

    try:
        ast = parse(stripped)
        return to_latex(ast, known_vars=known_vars)
    except Exception:
        return _raw_expr_to_latex_legacy(stripped)


def _sympy_value_to_latex_via_own_parser(expr, ctx) -> str:
    """Renders a SymPy result value through our OWN parser/renderer.

    SymPy only supplies the value here (as an object); the presentation
    (parentheses, \\cdot spacing, powers, identifiers) comes entirely
    from parsing/parser.py + rendering/latex_input.py -- same as the
    input side left of "=". SymPy's own latex() printer is no longer
    used for this case.

    str(expr) already gives a "clean" representation (e.g. "0.5*x**3 +
    4*x**2 - 3*x + 5", no unnecessary decimals) that only needs
    "**" -> "^" to become compatible with our lexer.

    Falls back to SymPy's own latex() route (expr_to_latex) if str(expr)
    contains something our AST doesn't (yet) model -- e.g. scientific
    notation ("1.0e-5"), sums, integrals, or other SymPy-specific
    constructs.
    """

    try:
        text = str(expr).replace("**", "^")
        ast = parse(text)
        if _has_list_literal(ast):
            # str() of a SymPy construct with tuple arguments
            # (Piecewise((..), (..)), Sum(f, (i, 0, n)), Integral, ...):
            # SymPy's own printer knows how to draw these.
            raise ValueError("SymPy construct")
        return to_latex(ast, known_vars=frozenset(ctx.user_vars))
    except Exception:
        return expr_to_latex(expr, tuple(sorted(ctx.user_vars)))


def _has_list_literal(node) -> bool:
    if isinstance(node, ListLiteral):
        return True
    for name in getattr(node, "__dataclass_fields__", ()):
        value = getattr(node, name)
        for child in (value if isinstance(value, tuple) else (value,)):
            if hasattr(child, "__dataclass_fields__") and _has_list_literal(child):
                return True
    return False


_BARE_LITERAL_RE = re.compile(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?('.*)?$")


def _is_bare_literal_input(raw_expr: str) -> bool:
    """True if the raw input is JUST a number (optionally with a unit
    suffix like "5'kg" or "1.5'W/(m*K)") -- i.e. not a real formula
    result worth showing redundantly (e.g. "F = 5 kg = 5 kg").

    Deliberately text-based rather than checking the built SymPy
    expression: a call like "solve(Eq(...), x)" is ACTUALLY EXECUTED
    while building the display form, and can produce a result with no
    free symbols (e.g. an empty list) even though the input was a real,
    display-worthy formula. A plain text check on the RAW input
    sidesteps that whole class of issue.

    Known limitation: "5'kg + 3'kg" (two unit-bearing literals with no
    assignment in between) would also be classified as "just a number",
    since the unit suffix allows arbitrary text (needed for compound
    units like "W/(m*K)"). Very rare in practice (variables are normally
    used for this), not handled further.
    """

    return bool(_BARE_LITERAL_RE.fullmatch(raw_expr.strip()))


_SYMBOLIC_DISPLAY_CALL_RE = re.compile(
    r"\b(?:diff|integrate|sum|summe|prod|produkt|lim)\s*\("
)


def _has_symbolic_display_call(raw_expr) -> bool:
    """True if raw_expr contains a call to diff/integrate/sum/summe/
    prod/produkt/lim.

    For these functions, core/context.py::eval_line() (via
    DISPLAY_FUNCTIONS) already builds an unevaluated SymPy object
    (Derivative/Integral/Sum/Product/Limit) -- expr_sym. SymPy's own
    latex() printer (expr_to_latex()) already renders that correctly
    with proper symbols (integral sign, sum sign, d/dx notation, lim
    with an arrow). Our own AST parser/renderer doesn't have special
    handling for these (falls back to generic \\operatorname{...}(...))
    and can't parse the tuple syntax ("(x, 0, 5)") as a function
    argument at all. So for lines containing one of these calls, we
    deliberately use expr_sym directly instead of our own parser.
    """

    return raw_expr is not None and bool(_SYMBOLIC_DISPLAY_CALL_RE.search(raw_expr))


def _split_quantity_and_free_symbol_factors(val):
    """Splits a Mul expression into a "quantity" part (number(s) *
    unit(s)) and the remaining factors that contain a real free symbol
    (e.g. an undefined variable like "B").

    Only meant for the flat case (a single top-level sp.Mul) -- anything
    else (Add, Pow with a mixed base, etc.) is passed through unchanged
    via quantity_factors=[val], so format_scalar_with_unit() handles it
    as before (a known, deliberately unaddressed limit for more complex
    mixed expressions).
    """
    if not isinstance(val, sp.Mul):
        return [val], []

    quantity_factors = []
    symbol_factors = []

    for factor in val.args:
        if factor.has(*UNIT_VALUES) or not factor.free_symbols:
            quantity_factors.append(factor)
        else:
            symbol_factors.append(factor)

    return quantity_factors, symbol_factors


def _format_value_with_unit_and_symbols(val, ctx, rel_tol: float = 1e-4) -> str:
    """Renders a value that contains BOTH a unit AND a real free symbol
    (e.g. "2*meter*B" if "B" was never assigned).

    format_scalar_with_unit()/split_magnitude_unit() assume everything
    without a unit is a plain number -- a real free symbol used to land
    in the "magnitude" bucket by mistake and got rendered via SymPy's
    own latex() printer (e.g. "2.0 B" instead of "2 B" or "2 \\cdot B"),
    without the \\cdot separator used elsewhere and without the 0.01%
    rounding.

    Fix: number+unit(s) and real symbol factors are rendered separately
    (the former still via format_scalar_with_unit(), so rounding/unit
    pretty-printing is preserved; the latter via our own AST renderer,
    so e.g. exponents/identifiers look the same as everywhere else) and
    joined back together with \\cdot.
    """
    quantity_factors, symbol_factors = _split_quantity_and_free_symbol_factors(val)

    if not symbol_factors:
        # No real free symbols involved -- unchanged prior behavior.
        return format_scalar_with_unit(val, rel_tol=rel_tol)

    quantity_part = sp.Mul(*quantity_factors) if quantity_factors else sp.Integer(1)
    parts = []

    if quantity_part != 1:
        parts.append(format_scalar_with_unit(quantity_part, rel_tol=rel_tol))

    for factor in symbol_factors:
        parts.append(_sympy_value_to_latex_via_own_parser(factor, ctx))

    return r" \cdot ".join(parts) if parts else format_scalar_with_unit(val, rel_tol=rel_tol)


COMPARISON_TEXT_RE = re.compile(r"<|>|==|!=|\u2264|\u2265|\u2260")


def _same_formula(a: str, b: str) -> bool:
    strip = lambda t: re.sub(r"\s+", "", t)
    return strip(a) == strip(b)


def _lhs_to_latex(var, ctx) -> str:
    """Left side of "=": a variable name, or the signature of a
    user-defined function ("f(x)", "g(a, b)")."""
    if ctx.is_function_signature(var):
        return raw_expr_to_latex(var, ctx.known_names())
    return var_to_latex(var)


def _incompatible_unit_message(val, desired_dim_expr) -> str:
    """Says WHAT does not fit: the dimension of the result and the one
    of the unit that was asked for (e.g. a moment typed as N/mm^2 and
    then divided by a section modulus is not a stress)."""
    message = "Result is not compatible with the requested unit"
    try:
        have = SI.get_dimensional_expr(val)
        want = SI.get_dimensional_expr(desired_dim_expr)
        return f"{message} (the result has dimension [{have}], the unit [{want}])."
    except Exception:
        return message + "."


def _desired_parts(val, desired_unit, rel_tol):
    """(LaTeX of the number, LaTeX of the unit) of `val` converted to
    the unit requested with "| unit"."""
    desired_latex, desired_dim_expr, scale = desired_unit

    if val == 0:
        # A pure zero carries no unit information in SymPy's unit
        # system anymore: "0 * unit" simplifies immediately to plain
        # "0" (the unit "falls out"). The usual compatibility/
        # conversion check would ALWAYS fail here, even though "0"
        # means the same trivial thing in any unit -- relevant e.g.
        # for thermodynamic cycles where a step has exactly 0
        # work/heat. degC is deliberately excluded: it has an
        # AFFINE zero point (0 K != 0 degC), so a unitless "0" would
        # be ambiguous there.
        if desired_latex == r"^\circ\mathrm{C}":
            raise ValueError(
                "Cannot display a unitless zero as °C (affine offset)."
            )
        return "0", desired_latex

    if desired_latex == r"^\circ\mathrm{C}":
        converted_kelvin = convert_to_cached(val, K)
        mag_kelvin, unit_kelvin = split_magnitude_unit(converted_kelvin)
        if unit_kelvin != K:
            raise ValueError("Temperature result cannot be converted to °C.")

        mag_celsius = sp.N(mag_kelvin - 273.15)
        return format_magnitude_decimal(mag_celsius, rel_tol=rel_tol), desired_latex

    converted_base = convert_to_cached(val, desired_dim_expr)
    mag_base, unit_base = split_magnitude_unit(converted_base)

    if unit_base != desired_dim_expr:
        raise ValueError(_incompatible_unit_message(val, desired_dim_expr))

    mag_scaled = normalize_numeric_quantity(sp.N(mag_base / scale))
    return format_magnitude_decimal(mag_scaled, rel_tol=rel_tol), desired_latex


def _value_in_desired_unit(val, desired_unit, rel_tol):
    """LaTeX of `val` converted to the unit requested with "| unit"."""
    mag_str, unit_latex = _desired_parts(val, desired_unit, rel_tol)
    return rf"{mag_str}\,{wrap_unit_latex(unit_latex)}"


def _matrix_to_latex(val, desired_unit, ctx, rel_tol):
    """A vector/matrix of numbers or quantities.

    Every element is rounded like a scalar result. If all elements
    share one unit, it is written once behind the matrix; otherwise
    each element carries its own. With "| unit" every element is
    converted (and checked) like a scalar.

    A matrix that still contains a variable without a value keeps
    SymPy's own printer, as before.
    """
    elements = list(val)

    if not all(
        isinstance(e, sp.Expr) and all(s in UNIT_VALUES for s in e.free_symbols)
        for e in elements
    ):
        if desired_unit is not None:
            raise ValueError(
                "A matrix can only be converted to a unit if all its elements have a value."
            )
        # (an immutable copy: expr_to_latex() caches by its argument)
        return expr_to_latex(sp.ImmutableMatrix(val), tuple(sorted(ctx.user_vars)))

    if desired_unit is not None:
        parts = [_desired_parts(e, desired_unit, rel_tol) for e in elements]
    else:
        parts = [scalar_parts(e, rel_tol=rel_tol) for e in elements]

    # A zero has no unit of its own ("0 m" is stored as plain 0); it
    # fits whatever the other elements have.
    units = {unit for element, (_, unit) in zip(elements, parts) if element != 0}
    common = units.pop() if len(units) == 1 else ""

    cells = []
    for element, (mag_str, unit) in zip(elements, parts):
        if common or not unit or element == 0:
            cells.append(mag_str)
        else:
            cells.append(rf"{mag_str}\,{wrap_unit_latex(unit)}")

    rows, cols = val.shape
    body = r"\\".join(
        " & ".join(cells[r * cols:(r + 1) * cols]) for r in range(rows)
    )
    text = rf"\left[\begin{{matrix}}{body}\end{{matrix}}\right]"
    if common:
        text += rf"\,{wrap_unit_latex(common)}"
    return text


def format_computation_result(var, raw_expr, expr_sym, val, desired_unit, symbolic_only, ctx, rel_tol: float = 1e-4, result_only: bool = False):
    if raw_expr and _has_symbolic_display_call(raw_expr):
        latex_expr = expr_to_latex(expr_sym, tuple(sorted(ctx.user_vars)))
    elif raw_expr:
        latex_expr = raw_expr_to_latex(raw_expr, ctx.known_names())
    else:
        latex_expr = expr_to_latex(expr_sym, tuple(sorted(ctx.user_vars)))

    is_verdict = val is sp.true or val is sp.false
    comparison = ctx.last_comparison if (is_verdict and var is None) else None

    if is_verdict:
        # Result of a comparison. A target unit only makes sense for the
        # two compared values (see below), not for true/false itself.
        if desired_unit is not None and comparison is None:
            raise ValueError("A comparison result (true/false) has no unit.")
        mag_with_unit = r"\text{true}" if val is sp.true else r"\text{false}"
    elif isinstance(val, sp.MatrixBase):
        mag_with_unit = _matrix_to_latex(val, desired_unit, ctx, rel_tol)
    elif desired_unit is not None:
        mag_with_unit = _value_in_desired_unit(val, desired_unit, rel_tol)
    else:
        if isinstance(val, sp.Equality):
            # Same reason: no Equality node type in our AST yet.
            mag_with_unit = expr_to_latex(val, tuple(sorted(ctx.user_vars)))
        elif hasattr(val, "has") and val.has(*UNIT_VALUES):
            mag_with_unit = _format_value_with_unit_and_symbols(val, ctx, rel_tol=rel_tol)
        else:
            val_for_output = normalize_numeric_quantity(val)
            val_is_plain_number = not getattr(val_for_output, "free_symbols", set())

            if val_is_plain_number:
                # Result is a fully computed number (regardless of
                # whether the INPUT contained literal units or
                # variables) -> always via format_scalar_with_unit(), so
                # format_magnitude_decimal()'s rounding (rel_tol) kicks
                # in.
                mag_with_unit = format_scalar_with_unit(val_for_output, rel_tol=rel_tol)
            else:
                mag_with_unit = _sympy_value_to_latex_via_own_parser(val_for_output, ctx)

    only_units_and_numbers_text = raw_expr is not None and _is_bare_literal_input(raw_expr)

    if comparison is not None:
        # A check written as its own line, e.g. "sigma <= sigma_zul | MPa":
        #   formula:  180 MPa <= 235 MPa  =>  true
        # The two values are shown in the requested unit, or like any
        # other result without one. Left out if they would only repeat
        # the formula ("3 < 5").
        op, left, right = comparison
        sides = []
        for side in (left, right):
            if desired_unit is not None:
                sides.append(_value_in_desired_unit(side, desired_unit, rel_tol))
            else:
                sides.append(format_scalar_with_unit(normalize_numeric_quantity(side), rel_tol=rel_tol))
        values = f"{sides[0]} {COMPARISON_LATEX[op]} {sides[1]}"
        verdict = rf"\;\Rightarrow\; {mag_with_unit}"
        if _same_formula(values, latex_expr):
            return {"type": "latex", "content": rf"{latex_expr} {verdict}"}
        return {"type": "latex", "content": rf"{latex_expr}:\quad {values} {verdict}"}

    if var is not None and not symbolic_only and isinstance(val, sp.MatrixBase) \
            and _same_formula(latex_expr, mag_with_unit):
        # A matrix typed in directly, mat([[1, 2], [3, 4]]): the input
        # already looks exactly like the result, show it once.
        return {"type": "latex", "content": rf"{_lhs_to_latex(var, ctx)} = {mag_with_unit}"}

    if var is None and isinstance(val, Relational) and _same_formula(latex_expr, mag_with_unit):
        # A comparison that cannot be decided yet (a variable has no
        # value): show it once instead of "y <= 3 = y <= 3".
        return {"type": "latex", "content": latex_expr}

    if is_verdict and var is not None and not symbolic_only and raw_expr is not None \
            and COMPARISON_TEXT_RE.search(raw_expr):
        # "ok = a <= b": parentheses, so the line does not read like a
        # chain "ok = a <= b = true".
        latex_expr = rf"\left({latex_expr}\right)"

    if result_only and var is not None:
        # "C =: expr": only "C = value", without the echo of the formula
        # (e.g. a large matrix typed over several lines).
        return {"type": "latex", "content": rf"{_lhs_to_latex(var, ctx)} = {mag_with_unit}"}

    if symbolic_only:
        if var is None:
            return {"type": "latex", "content": latex_expr}
        return {"type": "latex", "content": rf"{_lhs_to_latex(var, ctx)} = {latex_expr}"}

    if var is None:
        if only_units_and_numbers_text:
            # A pure numeric literal (e.g. "110'degC") with no
            # assignment: the "raw formula" would just be a repetition
            # of the unit marker (not supported by the current parser
            # scope anyway) -- so, like the case WITH a variable, show
            # only the value, no "raw = value" repetition.
            return {"type": "latex", "content": mag_with_unit}
        return {"type": "latex", "content": rf"{latex_expr} = {mag_with_unit}"}

    var_latex = _lhs_to_latex(var, ctx)

    if only_units_and_numbers_text:
        return {"type": "latex", "content": rf"{var_latex} = {mag_with_unit}"}

    return {"type": "latex", "content": rf"{var_latex} = {latex_expr} = {mag_with_unit}"}


def merge_latex_items(block_items):
    merged = []
    current_parts = []

    for item in block_items:
        if item["type"] == "latex":
            current_parts.append(item["content"])
            continue

        if current_parts:
            merged.append({"type": "latex", "content": r" \quad ".join(current_parts)})
            current_parts = []

        merged.append(item)

    if current_parts:
        merged.append({"type": "latex", "content": r" \quad ".join(current_parts)})

    return merged
