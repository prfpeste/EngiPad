from functools import lru_cache

import sympy as sp
import re
from sympy import latex
from sympy.physics.units import K

from mathlib.units import (
    BASE_UNITS,
    PRETTY_UNITS,
    UNIT_VALUES,
    VAR_SYMBOL_PREFIX,
    convert_to_cached,
    error_to_latex,
    format_magnitude_decimal,
    format_scalar_with_unit,
    make_safe_text_latex,
    normalize_numeric_quantity,
    split_magnitude_unit,
    unit_to_pretty_latex,
    var_to_latex,
    wrap_unit_latex,
)
from parsing.parser import parse
from rendering.latex_input import to_latex


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
    safe = make_safe_text_latex(text)
    content = rf"\textbf{{{safe}}}" if bold else rf"\text{{{safe}}}"
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
        return to_latex(ast, known_vars=frozenset(ctx.user_vars))
    except Exception:
        return expr_to_latex(expr, tuple(sorted(ctx.user_vars)))


_BARE_LITERAL_RE = re.compile(r"[-+]?\d+(\.\d+)?('.*)?$")


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


def format_computation_result(var, raw_expr, expr_sym, val, desired_unit, symbolic_only, ctx, rel_tol: float = 1e-4):
    if raw_expr and _has_symbolic_display_call(raw_expr):
        latex_expr = expr_to_latex(expr_sym, tuple(sorted(ctx.user_vars)))
    elif raw_expr:
        latex_expr = raw_expr_to_latex(raw_expr, frozenset(ctx.user_vars))
    else:
        latex_expr = expr_to_latex(expr_sym, tuple(sorted(ctx.user_vars)))

    if desired_unit is not None:
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
            mag_with_unit = rf"0\,{wrap_unit_latex(desired_latex)}"
        elif desired_latex == r"^\circ\mathrm{C}":
            converted_kelvin = convert_to_cached(val, K)
            mag_kelvin, unit_kelvin = split_magnitude_unit(converted_kelvin)
            if unit_kelvin != K:
                raise ValueError("Temperature result cannot be converted to °C.")

            mag_celsius = sp.N(mag_kelvin - 273.15)
            mag_str = format_magnitude_decimal(mag_celsius, rel_tol=rel_tol)
            mag_with_unit = rf"{mag_str}\,{wrap_unit_latex(desired_latex)}"
        else:
            converted_base = convert_to_cached(val, desired_dim_expr)
            mag_base, unit_base = split_magnitude_unit(converted_base)

            if unit_base != desired_dim_expr:
                raise ValueError("Result is not compatible with the requested unit.")

            mag_scaled = normalize_numeric_quantity(sp.N(mag_base / scale))
            mag_str = format_magnitude_decimal(mag_scaled, rel_tol=rel_tol)
            mag_with_unit = rf"{mag_str}\,{wrap_unit_latex(desired_latex)}"
    else:
        if isinstance(val, sp.MatrixBase):
            # Our AST has no matrix node type yet -- deliberately keep
            # using SymPy's own printer.
            mag_with_unit = expr_to_latex(val, tuple(sorted(ctx.user_vars)))
        elif isinstance(val, sp.Equality):
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

    if symbolic_only:
        if var is None:
            return {"type": "latex", "content": latex_expr}
        return {"type": "latex", "content": rf"{var_to_latex(var)} = {latex_expr}"}

    if var is None:
        if only_units_and_numbers_text:
            # A pure numeric literal (e.g. "110'degC") with no
            # assignment: the "raw formula" would just be a repetition
            # of the unit marker (not supported by the current parser
            # scope anyway) -- so, like the case WITH a variable, show
            # only the value, no "raw = value" repetition.
            return {"type": "latex", "content": mag_with_unit}
        return {"type": "latex", "content": rf"{latex_expr} = {mag_with_unit}"}

    var_latex = var_to_latex(var)

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
