import re

import sympy as sp

from core.context import EvaluationContext, split_top_level
from core.formatter import (
    error_to_latex,
    format_computation_result,
    text_to_latex,
    merge_latex_items,
    render_plain_text_item,
)
from core.images import parse_picture_call
from mathlib.plotting import create_plot
from parsing.ast_nodes import Identifier
from parsing.parser import parse
from mathlib.units import UNIT_VALUES, normalize_identifiers, strip_units_for_plot


sp.init_printing()

DEFAULT_INPUT = (
    '"Example:"\n'
    "a = 5'm/s^2 ; m = 10'kg\n"
    "F = m * a\n"
)


# A line that looks like the start of a new statement ("name = ...",
# "name := ...", a text line, a comment, plot/picture): an expression
# with an open bracket is NOT continued into such a line, so a forgotten
# ")" stays an error of its own line instead of swallowing the lines
# below it.
_NEW_STATEMENT_RE = re.compile(
    r"""^\s*(?:
        [^\W\d]\w*(?:_\{[^}]*\})?\s*
            (?:\([^()]*\)\s*)?(?::=|=)           # assignment / f(x) = ...
      | ["'\#]                                    # text line / comment
      | (?:plot|picture)\s*\(
    )""",
    re.VERBOSE,
)


def _open_bracket_depth(text: str) -> int:
    """Number of brackets that are still open at the end of `text`
    (text inside "..." is ignored, like in split_top_level())."""
    depth = 0
    in_quote = False
    for char in text:
        if char == '"':
            in_quote = not in_quote
        elif in_quote:
            continue
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
    return depth


def _continues(text: str) -> bool:
    """True if the statement at the end of `text` goes on in the next
    line: a bracket is still open, or the line ends with a backslash.
    Text lines and comments never continue."""
    parts = split_top_level(text, ";")
    if not parts:
        return False
    last = parts[-1].strip()
    if last[:1] in ("#", '"', "'"):
        return False
    return last.endswith("\\") or _open_bracket_depth(last) > 0


def join_continued_lines(user_input: str) -> list[tuple[int, str]]:
    """Splits the input into LOGICAL lines: (number of the first
    physical line, text).

    A statement may run over several lines while a bracket is open --
    made for matrices:

        C = mat([
          [1, 2],
          [3, 4]])

    A backslash at the end of a line continues it as well. The
    continuation stops at an empty line, at the end of the input and at
    a line that starts a new statement (_NEW_STATEMENT_RE); what was
    collected so far is then evaluated as it is (and reports its syntax
    error). Lines without an open bracket are passed through unchanged,
    so every old input behaves exactly as before.
    """
    lines = user_input.splitlines()
    logical = []
    i = 0
    while i < len(lines):
        line_no = i + 1
        text = lines[i]
        i += 1
        while _continues(text):
            explicit = text.rstrip().endswith("\\")
            at_end = i >= len(lines) or not lines[i].strip()
            if at_end or (not explicit and _NEW_STATEMENT_RE.match(lines[i])):
                if explicit:
                    text = text.rstrip()[:-1]  # dangling "\", nothing follows
                break
            text = text.rstrip()
            if explicit:
                text = text[:-1].rstrip()
            text = f"{text} {lines[i].strip()}"
            i += 1
        logical.append((line_no, text))
    return logical


def _split_result_only(stripped: str) -> tuple[bool, str]:
    """"C =: expr" shows only "C = value" (no echo of the formula) --
    the counterpart of ":=" (formula only). Returns (True, "C = expr")
    for such a line, else (False, stripped)."""
    idx = stripped.find("=")
    if idx > 0 and stripped[idx + 1:idx + 2] == ":" and stripped[idx + 2:idx + 3] != ":" \
            and stripped[idx - 1] != ":" and stripped[:idx].strip():
        return True, stripped[:idx + 1] + stripped[idx + 2:]
    return False, stripped


def evaluate_code(user_input: str, rel_tol: float = 1e-4, image_names=None):
    return evaluate_lines(user_input, rel_tol, EvaluationContext(), image_names)


def evaluate_lines(user_input: str, rel_tol: float, ctx: EvaluationContext, image_names=None):
    """Evaluates the lines of ONE sheet with the given context (the
    workbook evaluation in core/workbook.py passes a context that can
    resolve references to other sheets).

    image_names: names of the images that are loaded (for picture(...)
    lines). None = unknown, then picture() lines are not checked against
    a list (the image data never travels with the calculation, see
    core/images.py)."""
    results = []

    for line_no, raw_line in join_continued_lines(user_input):
        parts = split_top_level(raw_line, ";")

        if not parts:
            results.append([{"type": "spacer"}])
            continue

        block_items = []

        for part in parts:
            stripped = part.strip()

            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
                block_items.append(render_plain_text_item(stripped[1:-1], bold=True))
                continue

            if stripped.startswith("'") and stripped.endswith("'") and len(stripped) >= 2:
                block_items.append(render_plain_text_item(stripped[1:-1], bold=False))
                continue

            if stripped.startswith("picture(") and stripped.endswith(")"):
                try:
                    spec = parse_picture_call(stripped)
                    if image_names is not None and spec["name"] not in image_names:
                        raise ValueError(
                            f"Image '{spec['name']}' is not loaded. "
                            "Use the 'Insert image' button to add it."
                        )
                    block_items.append({"type": "image", **spec})
                except Exception as exc:
                    block_items.append({
                        "type": "latex",
                        "content": error_to_latex(f"Error in line {line_no}: {stripped}", exc),
                    })
                continue

            if stripped.startswith("plot(") and stripped.endswith(")"):
                try:
                    args = ctx.parse_plot_call(stripped)
                    var_name = normalize_identifiers(args[1].strip())
                    if not isinstance(parse(var_name), Identifier):
                        raise ValueError(
                            "plot(f, x, ...): the second argument must be a variable name."
                        )
                    func_expr = ctx.resolve_plot_expression(args[0], var_name)
                    var_symbol = sp.Symbol(var_name)
                    x_min = ctx.eval_plot_bound(args[2]) if len(args) >= 3 else -10
                    x_max = ctx.eval_plot_bound(args[3]) if len(args) >= 4 else 10

                    # Entdimensionieren: a formula built from unit-
                    # bearing quantities (e.g. "F * (L - x_pos)" with F
                    # in N and L in m) can't be lambdify()'d/plotted as
                    # is -- matplotlib/numpy need plain floats. See
                    # mathlib.units.strip_units_for_plot() for the
                    # reasoning; bounds are stripped the same way in
                    # case they were given with a unit too (e.g.
                    # "plot(f, x, 0'm, 2.5'm)").
                    had_units = hasattr(func_expr, "has") and func_expr.has(*UNIT_VALUES)
                    func_expr = strip_units_for_plot(func_expr)
                    x_min = strip_units_for_plot(x_min)
                    x_max = strip_units_for_plot(x_max)

                    block_items.append({
                        "type": "plot",
                        "src": create_plot(
                            func_expr, var_symbol, x_min, x_max,
                            note="values in SI base units" if had_units else None,
                        ),
                    })
                except Exception as exc:
                    block_items.append({
                        "type": "latex",
                        "content": text_to_latex(f'Error while plotting: {exc}'),
                    })
                continue

            try:
                symbolic_only = False
                line_for_eval = stripped

                result_only = False

                if ":=" in stripped:
                    symbolic_only = True
                    line_for_eval = stripped.replace(":=", "=", 1)
                else:
                    result_only, line_for_eval = _split_result_only(stripped)

                var, raw_expr, expr_sym, expr_num, val, desired_unit = ctx.eval_line(line_for_eval)

                if ctx.last_definition:
                    # "f(x) = ...": a definition shows its formula only.
                    symbolic_only, result_only = True, False

                if expr_sym is None or val is None:
                    continue

                block_items.append(
                    format_computation_result(
                        var=var,
                        raw_expr=raw_expr,
                        expr_sym=expr_sym,
                        val=val,
                        desired_unit=desired_unit,
                        symbolic_only=symbolic_only,
                        result_only=result_only,
                        ctx=ctx,
                        rel_tol=rel_tol,
                    )
                )
            except Exception as exc:
                block_items.append({
                    "type": "latex",
                    "content": error_to_latex(f"Error in line {line_no}: {stripped}", exc),
                })

        if block_items:
            results.append(merge_latex_items(block_items))

    return results
