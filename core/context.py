from dataclasses import dataclass, field

import sympy as sp

from mathlib.functions import NUMERIC_FUNCTIONS
from mathlib.sympy_bridge import _unit_node_to_sympy, ast_to_sympy, sheet_lookup_scope
from mathlib.units import (
    BASE_UNITS,
    DESIRED_UNIT_MAP,
    bare_literal_unit_key,
    convert_to_cached,
    is_dimensionless_number,
    normalize_identifiers,
    normalize_numeric_quantity,
    split_magnitude_unit,
)
from parsing.ast_nodes import BinaryOp, Identifier, Quantity
from parsing.parser import parse
from rendering.latex_input import _render_unit


def _unit_names_in(node) -> set:
    if isinstance(node, Identifier):
        return {node.name}
    if isinstance(node, BinaryOp):
        return _unit_names_in(node.left) | _unit_names_in(node.right)
    return set()


def build_desired_unit(text: str):
    """Builds a DESIRED_UNIT_MAP-style entry (latex, SI unit expression,
    scale) for a compound unit that is NOT in the fixed table, e.g.
    "g/h", "kJ/K", "to/d", "mm/s^2".

    The text is parsed with the same unit grammar as the unit after
    "'" (only known unit names, * / ^ and parentheses), the factor and
    dimension are derived from the individual unit definitions in
    UNIT_NS, and the LaTeX comes from the renderer of the input echo, so
    nothing has to be listed per combination.
    """
    try:
        node = parse(f"1'{text}")
    except Exception:
        raise ValueError(f"Unknown desired unit '{text}'")
    if not isinstance(node, Quantity):
        raise ValueError(f"Unknown desired unit '{text}'")

    unit_node = node.unit
    if "degC" in _unit_names_in(unit_node):
        raise ValueError(
            "°C cannot be used inside a compound unit (affine offset)."
        )

    try:
        unit_expr = _unit_node_to_sympy(unit_node)
        converted = convert_to_cached(unit_expr, BASE_UNITS)
    except Exception:
        raise ValueError(f"Unknown desired unit '{text}'")

    scale, base = split_magnitude_unit(converted)
    return _render_unit(unit_node, 0), base, scale


def split_top_level(text: str, delimiter: str) -> list[str]:
    parts = []
    current = []
    depth = 0
    quote = None

    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue

        if char == '"':
            current.append(char)
            quote = char
            continue

        if char in "([{":
            depth += 1
            current.append(char)
            continue

        if char in ")]}":
            depth = max(0, depth - 1)
            current.append(char)
            continue

        if char == delimiter and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    return parts


@dataclass
class EvaluationContext:
    var_ns: dict = field(default_factory=lambda: dict(NUMERIC_FUNCTIONS))
    user_vars: set = field(default_factory=set)
    sym_exprs: dict = field(default_factory=dict)
    # (sheet, name) -> value of another sheet's variable; set by
    # core/workbook.py. None outside a workbook.
    sheet_lookup: object = None

    def eval_line(self, line: str):
        with sheet_lookup_scope(self.sheet_lookup):
            return self._eval_line(line)

    def parse_assignment(self, rhs_raw: str):
        desired_unit = None

        if "|" in rhs_raw:
            rhs_core, desired_name = rhs_raw.split("|", 1)
            rhs_core = rhs_core.strip()
            desired_name = desired_name.strip()

            if desired_name:
                if desired_name in DESIRED_UNIT_MAP:
                    desired_unit = DESIRED_UNIT_MAP[desired_name]
                else:
                    desired_unit = build_desired_unit(desired_name)

            return rhs_core, desired_unit

        return rhs_raw, desired_unit

    def _eval_line(self, line: str):
        line = normalize_identifiers(line.strip())
        if not line:
            return None, None, None, None, None, None

        if "=" in line:
            var_part, rhs_part = line.split("=", 1)
            var_name = var_part.strip()
            if "::" in var_name:
                raise ValueError(
                    f"Cannot assign to '{var_name}': variables of another "
                    f"sheet are read-only."
                )
            rhs_core, desired_unit = self.parse_assignment(rhs_part.strip())

            if desired_unit is None:
                bare_unit_name = bare_literal_unit_key(rhs_core)
                if bare_unit_name is not None and bare_unit_name in DESIRED_UNIT_MAP:
                    desired_unit = DESIRED_UNIT_MAP[bare_unit_name]

            ast = parse(rhs_core)
            expr_num = ast_to_sympy(ast, self.var_ns, self.user_vars, mode="numeric")

            try:
                # Display mode can fail where numeric mode doesn't:
                # matrix methods like dot()/det()/inv() expect real
                # sp.Matrix objects, but display mode only provides fresh
                # symbols for user variables (see mathlib.sympy_bridge --
                # deliberate, so the "raw formula" shows variable names,
                # not their value). In that case just reuse the already-
                # computed value for display too, instead of failing the
                # whole line.
                expr_disp = ast_to_sympy(ast, self.var_ns, self.user_vars, mode="display")
            except Exception:
                expr_disp = expr_num

            value = normalize_numeric_quantity(expr_num)
            stored_value = is_dimensionless_number(value)

            self.var_ns[var_name] = value if stored_value is None else stored_value
            self.user_vars.add(var_name)
            self.sym_exprs[var_name] = expr_num

            return var_name, rhs_core, expr_disp, expr_num, value, desired_unit

        line_core, desired_unit = self.parse_assignment(line)

        ast = parse(line_core)
        expr_num = ast_to_sympy(ast, self.var_ns, self.user_vars, mode="numeric")
        value = normalize_numeric_quantity(expr_num)

        try:
            expr_disp = ast_to_sympy(ast, self.var_ns, self.user_vars, mode="display")
        except Exception:
            expr_disp = expr_num

        if desired_unit is None:
            bare_unit_name = bare_literal_unit_key(line_core)
            if bare_unit_name is not None and bare_unit_name in DESIRED_UNIT_MAP:
                desired_unit = DESIRED_UNIT_MAP[bare_unit_name]

        return None, line_core, expr_disp, expr_num, value, desired_unit

    def parse_plot_call(self, stripped: str):
        inner = stripped[5:-1]
        args = split_top_level(normalize_identifiers(inner), ",")
        if len(args) < 2:
            raise ValueError("plot(f, x, [xmin, xmax]) expected.")
        return args

    def eval_plot_bound(self, expr: str):
        ast = parse(normalize_identifiers(expr.strip()))
        with sheet_lookup_scope(self.sheet_lookup):
            return sp.N(ast_to_sympy(ast, self.var_ns, self.user_vars, mode="plot"))

    def resolve_plot_expression(self, expr_text: str):
        expr_text = normalize_identifiers(expr_text.strip())

        if expr_text in self.sym_exprs:
            return self.sym_exprs[expr_text]

        ast = parse(expr_text)
        with sheet_lookup_scope(self.sheet_lookup):
            return ast_to_sympy(ast, self.var_ns, self.user_vars, mode="plot")
