import sympy as sp

from core.context import EvaluationContext, split_top_level
from core.formatter import (
    error_to_latex,
    format_computation_result,
    make_safe_text_latex,
    merge_latex_items,
    render_plain_text_item,
)
from mathlib.plotting import create_plot
from mathlib.units import UNIT_VALUES, normalize_identifiers, strip_units_for_plot


sp.init_printing()

DEFAULT_INPUT = (
    '"Example:"\n'
    "a = 5'm/s^2 ; m = 10'kg\n"
    "F = m * a\n"
)


def evaluate_code(user_input: str, rel_tol: float = 1e-4):
    ctx = EvaluationContext()
    results = []

    for line_no, raw_line in enumerate(user_input.splitlines(), start=1):
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

            if stripped.startswith("plot(") and stripped.endswith(")"):
                try:
                    args = ctx.parse_plot_call(stripped)
                    func_expr = ctx.resolve_plot_expression(args[0])
                    var_symbol = sp.Symbol(normalize_identifiers(args[1]))
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
                        "content": rf"\text{{{make_safe_text_latex(f'Error while plotting: {exc}')}}}",
                    })
                continue

            try:
                symbolic_only = False
                line_for_eval = stripped

                if ":=" in stripped:
                    symbolic_only = True
                    line_for_eval = stripped.replace(":=", "=", 1)

                var, raw_expr, expr_sym, expr_num, val, desired_unit = ctx.eval_line(line_for_eval)

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
