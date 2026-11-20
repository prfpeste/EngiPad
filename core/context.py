import contextlib
import re
from dataclasses import dataclass, field, fields

import sympy as sp

from mathlib.functions import NUMERIC_FUNCTIONS, RESERVED_FUNCTION_NAMES
from mathlib.sympy_bridge import (
    UserFunction,
    _unit_node_to_sympy,
    assumptions_scope,
    ast_to_sympy,
    sheet_lookup_scope,
    user_functions_scope,
)
from mathlib.units import (
    BASE_UNITS,
    DESIRED_UNIT_MAP,
    bare_literal_unit_key,
    compare_values,
    convert_to_cached,
    is_dimensionless_number,
    normalize_identifiers,
    normalize_numeric_quantity,
    split_magnitude_unit,
    substitute_symbols,
)
from parsing.ast_nodes import BinaryOp, Comparison, FunctionCall, Identifier, Number, Quantity
from parsing.parser import parse
from rendering.latex_input import _render_unit, to_latex


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

    A reciprocal unit is written with a 1 in the numerator: "1/s",
    "1/min", "1/K", "1/(m*K)". The unit grammar has no numbers, so the
    part after "1/" is read as a unit and then inverted.
    """
    reciprocal = _RECIPROCAL_UNIT_RE.match(text)
    if reciprocal:
        try:
            latex, base, scale = build_desired_unit(reciprocal.group(1))
        except ValueError as exc:
            if "°C" in str(exc):
                raise
            raise ValueError(f"Unknown desired unit '{text}'") from None
        return rf"\frac{{1}}{{{latex}}}", 1 / base, 1 / scale

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


def find_assignment(text: str) -> int:
    """Position of the "=" that makes `text` an assignment, or -1.

    The "=" inside the comparison operators <= >= == != is not an
    assignment ("a <= b" is an expression, "ok = a <= b" assigns at the
    first "="). ":=" and "=:" are also skipped here; core/engine.py
    turns them into a plain "=" before a line gets here.
    """
    i = 0
    while i < len(text):
        if text[i] == "=":
            if text[i + 1:i + 2] == "=":
                i += 2
                continue
            if i > 0 and text[i - 1] in "<>!:":
                i += 1
                continue
            if text[i + 1:i + 2] == ":":
                i += 1
                continue
            return i
        i += 1
    return -1


_IDENT = r"[^\W\d]\w*"
# "| 1/s", "| 1/(m*K)": the part after "1/" must not start with another
# "1/" (no "1/1/s").
_RECIPROCAL_UNIT_RE = re.compile(r"^\s*1\s*/\s*(?!1\s*/)(\S.*?)\s*$")
_FUNCTION_SIGNATURE_RE = re.compile(rf"^({_IDENT})\s*\(([^()]*)\)$")
_IDENT_RE = re.compile(rf"^{_IDENT}$")


def _called_names(node) -> set:
    """Names of all functions called anywhere in an AST."""
    names = set()
    if isinstance(node, FunctionCall):
        names.add(node.name)
    for f in (fields(node) if hasattr(node, "__dataclass_fields__") else ()):
        value = getattr(node, f.name)
        for child in (value if isinstance(value, tuple) else (value,)):
            if hasattr(child, "__dataclass_fields__"):
                names |= _called_names(child)
    return names


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


def _with_symbols_replaced(value, mapping):
    """`value` with the symbols in `mapping` replaced -- for a stored
    value of any kind (expression, matrix, list, ...). Units are left
    alone (substitute_symbols); functions and other non-SymPy entries of
    the namespace come back unchanged."""
    if isinstance(value, sp.MatrixBase):
        return value.applyfunc(lambda element: _with_symbols_replaced(element, mapping))
    if isinstance(value, (list, tuple)):
        return type(value)(_with_symbols_replaced(item, mapping) for item in value)
    if isinstance(value, dict):
        return {key: _with_symbols_replaced(item, mapping) for key, item in value.items()}
    if isinstance(value, sp.Basic):
        if not (value.free_symbols & set(mapping)):
            return value
        return substitute_symbols(value, mapping)
    return value


@dataclass
class EvaluationContext:
    var_ns: dict = field(default_factory=lambda: dict(NUMERIC_FUNCTIONS))
    user_vars: set = field(default_factory=set)
    sym_exprs: dict = field(default_factory=dict)
    # (sheet, name) -> value of another sheet's variable; set by
    # core/workbook.py. None outside a workbook.
    sheet_lookup: object = None
    # Functions defined in this sheet: name -> UserFunction.
    user_functions: dict = field(default_factory=dict)
    # What the line evaluated LAST was, for the output formatting
    # (core/engine.py, core/formatter.py):
    #   last_definition: True for "f(x) = ..." (only the formula is shown)
    #   last_comparison: (op, left value, right value) if the line was a
    #                    comparison of two known quantities at top level
    last_definition: bool = False
    last_comparison: object = None
    #   last_assumption: LaTeX of an "assume(T > 0)" line, else None
    last_assumption: object = None
    # "assume(T > 0)" in this sheet: name -> SymPy assumptions.
    assumptions: dict = field(default_factory=dict)

    @contextlib.contextmanager
    def _scope(self):
        with sheet_lookup_scope(self.sheet_lookup), \
                user_functions_scope(self.user_functions), \
                assumptions_scope(self.assumptions):
            yield

    def variable_symbol(self, name: str):
        """Symbol for a variable without a value, with its assumptions."""
        return sp.Symbol(name, **self.assumptions.get(name, {}))

    def eval_line(self, line: str):
        self.last_definition = False
        self.last_comparison = None
        self.last_assumption = None
        with self._scope():
            return self._eval_line(line)

    def known_names(self) -> frozenset:
        """Names the user has defined (variables and functions), for
        the formula display."""
        # A function "f" is listed as "f()": the renderer can tell it
        # from a variable of the same name as a built-in function.
        return frozenset(self.user_vars) | frozenset(
            f"{name}()" for name in self.user_functions
        )

    def is_function_signature(self, text: str) -> bool:
        match = _FUNCTION_SIGNATURE_RE.match(text.strip())
        return bool(match) and match.group(1) in self.user_functions

    def _define_function(self, name: str, params_text: str, rhs_raw: str):
        """"f(x) = x^2 + 1" / "g(a, b) = a*b": stores the parsed formula.
        Nothing is computed here; the body is evaluated on every call,
        with the values the other variables have at that moment."""
        params = [p.strip() for p in params_text.split(",")]
        if params == [""]:
            raise ValueError(f"{name}() needs at least one parameter.")
        for param in params:
            if not _IDENT_RE.match(param):
                raise ValueError(
                    f"'{param}' is not a valid parameter name in the definition of {name}()."
                )
        if len(set(params)) != len(params):
            raise ValueError(f"Parameter names of {name}() must be different.")
        if name in NUMERIC_FUNCTIONS or name in RESERVED_FUNCTION_NAMES:
            raise ValueError(
                f"'{name}' is a built-in name and cannot be defined as a function."
            )
        if name in self.user_vars:
            raise ValueError(
                f"'{name}' is already a variable and cannot be defined as a function."
            )
        if "|" in rhs_raw:
            raise ValueError(
                "A function definition cannot have a target unit; "
                "use '| unit' where the function is called."
            )

        body = parse(rhs_raw)
        if name in _called_names(body):
            raise ValueError(f"{name}() must not call itself.")

        self.user_functions[name] = UserFunction(name, tuple(params), body)

        # Only for the display of the definition line: the formula with
        # all names as symbols.
        local_vars = set(self.user_vars) | set(params)
        try:
            shown = ast_to_sympy(body, self.var_ns, local_vars, mode="display")
        except Exception:
            shown = sp.Symbol(name)

        self.last_definition = True
        signature = f"{name}({', '.join(params)})"
        return signature, rhs_raw, shown, shown, shown, None

    def _typed_unit_of_bare_literal(self, text: str):
        """A plain number with a unit ("q = 5'kN/m", "c = 2'N/mm") keeps
        the unit that was typed -- any unit, not only the ones of the
        preferred list (which used to turn 5 kN/m into 5000 kg/s^2)."""
        unit_text = bare_literal_unit_key(text)
        if unit_text is None:
            return None
        if unit_text in DESIRED_UNIT_MAP:
            return DESIRED_UNIT_MAP[unit_text]
        try:
            return build_desired_unit(unit_text)
        except Exception:
            return None

    def _typed_unit_of_matrix_literal(self, ast, text: str):
        """"T = vec(20, 40, 60)'degC" / "K = mat([[...]])'kN/mm": a
        vector or matrix typed with ONE unit for all elements keeps that
        unit in the output, like a plain "110'degC" does."""
        if not (
            isinstance(ast, Quantity)
            and isinstance(ast.magnitude, FunctionCall)
            and ast.magnitude.name in ("vec", "mat", "Matrix")
        ):
            return None
        unit_text = text.rsplit("'", 1)[1].strip()
        try:
            return DESIRED_UNIT_MAP.get(unit_text) or build_desired_unit(unit_text)
        except Exception:
            return None

    def _evaluate(self, ast):
        """Numeric value of a parsed line. A comparison at top level is
        evaluated here (not in the bridge) so both sides are available
        for the output ("180 MPa <= 235 MPa")."""
        if isinstance(ast, Comparison):
            left = ast_to_sympy(ast.left, self.var_ns, self.user_vars, mode="numeric")
            right = ast_to_sympy(ast.right, self.var_ns, self.user_vars, mode="numeric")
            result = compare_values(ast.op, left, right)
            if result in (sp.true, sp.false):
                self.last_comparison = (ast.op, left, right)
            return result
        return ast_to_sympy(ast, self.var_ns, self.user_vars, mode="numeric")

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

        eq_pos = find_assignment(line)

        if eq_pos >= 0:
            var_part, rhs_part = line[:eq_pos], line[eq_pos + 1:]
            var_name = var_part.strip()

            signature = _FUNCTION_SIGNATURE_RE.match(var_name)
            if signature:
                return self._define_function(
                    signature.group(1), signature.group(2), rhs_part.strip()
                )

            if "::" in var_name:
                raise ValueError(
                    f"Cannot assign to '{var_name}': variables of another "
                    f"sheet are read-only."
                )
            rhs_core, desired_unit = self.parse_assignment(rhs_part.strip())

            if desired_unit is None:
                desired_unit = self._typed_unit_of_bare_literal(rhs_core)

            ast = parse(rhs_core)
            if desired_unit is None:
                desired_unit = self._typed_unit_of_matrix_literal(ast, rhs_core)
            expr_num = self._evaluate(ast)

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

            if expr_num in (sp.true, sp.false):
                # Result of a comparison: stored as it is.
                value, stored_value = expr_num, None
            else:
                value = normalize_numeric_quantity(expr_num)
                stored_value = is_dimensionless_number(value)

            self.var_ns[var_name] = value if stored_value is None else stored_value
            self.user_vars.add(var_name)
            self.sym_exprs[var_name] = expr_num

            return var_name, rhs_core, expr_disp, expr_num, value, desired_unit

        line_core, desired_unit = self.parse_assignment(line)

        ast = parse(line_core)
        if isinstance(ast, FunctionCall) and ast.name == "assume":
            if desired_unit is not None:
                raise ValueError("assume(...) cannot have a target unit.")
            self._assume(ast)
            return None, None, None, None, None, None
        expr_num = self._evaluate(ast)
        if expr_num in (sp.true, sp.false):
            value = expr_num
        else:
            value = normalize_numeric_quantity(expr_num)

        try:
            expr_disp = ast_to_sympy(ast, self.var_ns, self.user_vars, mode="display")
        except Exception:
            expr_disp = expr_num

        if desired_unit is None:
            desired_unit = self._typed_unit_of_bare_literal(line_core)

        return None, line_core, expr_disp, expr_num, value, desired_unit

    def _assume(self, ast):
        """assume(T > 0) or assume(T > 0, ω_d > 0): the variables are
        positive from this line on. Only "> 0" (decided 20.11.2026): it
        is what integrals, sqrt and solve need.

        For SymPy a positive T is a different symbol than a plain T.
        Values stored in earlier lines that still contain the plain T
        are switched to the positive one, otherwise "f - T^2" with
        f = T^2 from before would not be 0.
        """
        if not ast.args:
            raise ValueError("assume(T > 0) expected: at least one condition.")
        names = []
        for condition in ast.args:
            if not (
                isinstance(condition, Comparison)
                and condition.op == ">"
                and isinstance(condition.left, Identifier)
                and isinstance(condition.right, Number)
                and float(condition.right.value) == 0
            ):
                raise ValueError(
                    "assume() only accepts conditions of the form name > 0, "
                    "e.g. assume(T > 0)."
                )
            name = condition.left.name
            if name in self.user_vars:
                raise ValueError(
                    f"{name} already has a value; assume() is only for "
                    "variables without a value."
                )
            if name in self.user_functions:
                raise ValueError(f"{name} is a function, not a variable.")
            names.append(name)

        mapping = {}
        for name in names:
            plain = self.variable_symbol(name)
            self.assumptions[name] = {"positive": True}
            assumed = self.variable_symbol(name)
            if plain != assumed:
                mapping[plain] = assumed
        if mapping:
            for store in (self.var_ns, self.sym_exprs):
                for key, value in list(store.items()):
                    store[key] = _with_symbols_replaced(value, mapping)

        self.last_assumption = r"\text{assume: }\ " + r",\ ".join(
            to_latex(condition, self.known_names()) for condition in ast.args
        )

    def parse_plot_call(self, stripped: str):
        inner = stripped[5:-1]
        args = split_top_level(normalize_identifiers(inner), ",")
        if len(args) < 2:
            raise ValueError("plot(f, x, [xmin, xmax]) expected.")
        return args

    def eval_plot_bound(self, expr: str):
        # Evaluated for real, so a bound may be a variable or a function
        # call ("plot(f(x), x, 0, L)").
        ast = parse(normalize_identifiers(expr.strip()))
        with self._scope():
            return sp.N(ast_to_sympy(ast, self.var_ns, self.user_vars, mode="numeric"))

    def resolve_plot_expression(self, expr_text: str, var_name: str = None):
        """The formula to plot as a SymPy expression in the plot
        variable.

        Evaluated like any other line (variables are replaced by their
        values, user-defined functions are expanded), with ONE
        exception: the plot variable itself stays a symbol, even if a
        variable of that name has a value. So "plot(F*(L - x), x)" and
        "plot(f(x), x)" work directly.
        """
        expr_text = normalize_identifiers(expr_text.strip())

        if expr_text in self.sym_exprs:
            return self.sym_exprs[expr_text]

        ast = parse(expr_text)
        namespace = self.var_ns
        if var_name is not None:
            namespace = dict(self.var_ns)
            namespace[var_name] = self.variable_symbol(var_name)

        with self._scope():
            return ast_to_sympy(ast, namespace, self.user_vars, mode="numeric")
