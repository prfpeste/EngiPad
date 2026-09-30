import pytest

from core.context import EvaluationContext
from core.formatter import format_computation_result


def unwrap_unit_markup(latex: str) -> str:
    r"""Replaces every \engiunit{...} marker (see
    mathlib/units.py::wrap_unit_latex()) by its plain content.

    The marker only controls the DISPLAY SIZE of units (MathJax macro in
    templates/index.html, \newcommand in core/latex_export.py). Most
    tests in this suite are about the actual CONTENT of a result line
    (number, rounding, unit label), not its size, so eval_and_format()
    below unwraps it before returning -- otherwise every unit assertion
    would have to repeat the marker. That the marker IS emitted, on every
    path that shows a unit, is tested explicitly in
    tests/test_context_eval.py::TestUnitSizeMarker.
    """
    marker = "\\engiunit{"
    while True:
        start = latex.find(marker)
        if start == -1:
            return latex

        depth = 0
        for pos in range(start + len(marker) - 1, len(latex)):
            if latex[pos] == "{":
                depth += 1
            elif latex[pos] == "}":
                depth -= 1
                if depth == 0:
                    inner = latex[start + len(marker):pos]
                    latex = latex[:start] + inner + latex[pos + 1:]
                    break
        else:
            raise ValueError(f"Unbalanced braces in {latex!r}")


@pytest.fixture
def ctx():
    """Fresh EvaluationContext per test (no shared state between tests)."""
    return EvaluationContext()


def eval_and_format(line: str, context: EvaluationContext, symbolic_only: bool = False) -> dict:
    """Runs a single EngiPad line end to end, exactly like
    core/engine.py would for a normal line: eval_line() ->
    format_computation_result(). Returns the resulting
    {"type": "latex", "content": ...} dict.

    Deliberately NOT a test of the Flask route (see test_app.py for a
    real HTTP smoke test), but the core pipeline at the Python level:
    fast, no server, but still "real" (no mocking).
    """
    var, raw, expr_sym, expr_num, val, desired_unit = context.eval_line(line)
    result = format_computation_result(var, raw, expr_sym, val, desired_unit, symbolic_only, context)
    return {**result, "content": unwrap_unit_markup(result["content"])}


def eval_and_format_raw(line: str, context: EvaluationContext, symbolic_only: bool = False) -> dict:
    """Like eval_and_format(), but WITHOUT unwrapping the \\engiunit{}
    marker (for tests of the marker itself)."""
    var, raw, expr_sym, expr_num, val, desired_unit = context.eval_line(line)
    return format_computation_result(var, raw, expr_sym, val, desired_unit, symbolic_only, context)


def content_of(line: str, context: EvaluationContext, symbolic_only: bool = False) -> str:
    """Convenience wrapper: returns just the LaTeX content string."""
    return eval_and_format(line, context, symbolic_only)["content"]


@pytest.fixture
def run():
    """Fixture version of eval_and_format(), for tests that want to call
    the function directly as 'run(line, ctx)'."""
    return eval_and_format


@pytest.fixture
def run_content():
    """Fixture version of content_of()."""
    return content_of
