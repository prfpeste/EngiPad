"""
Security regression tests.

Background: core/context.py originally evaluated numeric input via
sp.sympify(text, locals=env) -- which uses eval() internally. The
following input led to real code execution on the server:

    x = __import__(chr(111)+chr(115)).popen(chr(105)+chr(100)).read()
    -> returned "uid=0(root) gid=0(root) groups=0(root)"

Fix: core/context.py::eval_line() now builds expressions exclusively
via parsing.parser.parse() (our own, safe parser, no eval) +
mathlib.sympy_bridge.ast_to_sympy() (builds the SymPy tree node by
node, function calls only against a fixed whitelist). sympify()/
parse_expr() are no longer called anywhere on user input.

These tests are deliberately VERY concrete (real, historically
successful exploit payloads) rather than just checking "grammar
feature X is rejected" -- if someone in the future reintroduces an
eval()-based path (e.g. as a "quick fix" for a parser gap), this should
catch it immediately.
"""

import pytest

from core.context import EvaluationContext
from core.engine import evaluate_code


# ---------------------------------------------------------------------
# The original, actually successful exploit + variants
# ---------------------------------------------------------------------
class TestNoArbitraryCodeExecution:
    ORIGINAL_EXPLOIT = (
        "x = __import__(chr(111)+chr(115))"
        ".popen(chr(105)+chr(100)).read()"
    )

    RCE_PAYLOADS = [
        # The original exploit that actually worked during this session
        # (returned "uid=0(root)...").
        ORIGINAL_EXPLOIT,
        # Same idea with plain quotes (no chr() detour needed, in case
        # the "'" marker is ever handled differently).
        "x = __import__('os').popen('id').read()",
        # Classic Python sandbox escape via the class hierarchy.
        "x = ().__class__.__bases__[0].__subclasses__()",
        # Direct eval()/exec() call.
        "x = eval(chr(49))",
        "x = exec(chr(49))",
        # File access without import (phrased without getattr).
        "open(chr(47)+chr(101)+chr(116)+chr(99)+chr(47)+chr(112)+chr(97)"
        "+chr(115)+chr(115)+chr(119)+chr(100))",
        # Access to environment/process info.
        "x = globals()",
        "x = vars()",
        "x = getattr(chr(1), chr(2))",
    ]

    @pytest.mark.parametrize("payload", RCE_PAYLOADS)
    def test_payload_never_executes_real_python(self, payload):
        # It doesn't matter whether the line raises an error (because it
        # doesn't fit our grammar) or renders as a purely symbolic,
        # never-executed placeholder -- the only hard requirement is: no
        # trace of real code execution may show up (e.g. a UID/GID
        # output like the original exploit, or any other filesystem/
        # process content).
        results = evaluate_code(payload + "\n")
        rendered = str(results)

        assert "uid=" not in rendered
        assert "gid=" not in rendered
        assert "root" not in rendered

    def test_original_exploit_produces_no_shell_output(self):
        # The original exploit as an explicit standalone test (not just
        # parametrized) -- this is THE case that triggered this whole
        # change.
        results = evaluate_code(self.ORIGINAL_EXPLOIT + "\n")
        rendered = str(results)
        assert "uid=0(root)" not in rendered

    def test_unknown_function_becomes_inert_symbolic_placeholder(self):
        # An unknown function (not in NUMERIC_FUNCTIONS) must NEVER turn
        # into a real Python call -- it becomes a symbolic
        # sp.Function(name)(...) placeholder that never executes. This
        # is the central security invariant of the bridge (see
        # mathlib/sympy_bridge.py).
        ctx = EvaluationContext()
        _, _, _, expr_num, _, _ = ctx.eval_line("eval(1)")
        assert str(expr_num) == "eval(1)"
        assert not isinstance(expr_num, (int, float))

    def test_attribute_access_is_not_part_of_the_grammar(self):
        # "." (method call/attribute access, e.g. "().__class__") simply
        # isn't a token in the lexer -- this is the FIRST line of
        # defense, before the function whitelist even comes into play.
        # Expected: a clear error line, not a result like "1" (which
        # would be a successful .bit_length() call).
        results = evaluate_code("x = (1).bit_length()\n")
        rendered = str(results)
        assert "Error" in rendered

    def test_subscript_cannot_reach_attribute_access(self):
        # "[]" (added for picking a value out of a solve() result, e.g.
        # "sol[0]") is plain item access, never a way to reach "."
        # -- confirm the two don't combine into something exploitable.
        results = evaluate_code("x = ().__class__[0]\n")
        rendered = str(results)
        assert "Error" in rendered
        assert "uid=" not in rendered
        assert "root" not in rendered


# ---------------------------------------------------------------------
# The plot path (eval_plot_bound()/resolve_plot_expression()) was
# separately vulnerable -- its own regression tests.
# ---------------------------------------------------------------------
class TestPlotPathIsAlsoSafe:
    def test_malicious_plot_bound_does_not_execute(self):
        results = evaluate_code(
            "plot(x, x, __import__(chr(111)+chr(115)), 5)\n"
        )
        rendered = str(results)
        assert "uid=" not in rendered
        assert "root" not in rendered

    def test_malicious_plot_expression_does_not_execute(self):
        results = evaluate_code(
            "plot(__import__(chr(111)+chr(115)), x, -5, 5)\n"
        )
        rendered = str(results)
        assert "uid=" not in rendered
        assert "root" not in rendered


# ---------------------------------------------------------------------
# No more eval()/sympify() on user input -- static check.
# ---------------------------------------------------------------------
class TestNoSympifyOnUserInputRemains:
    def test_context_module_does_not_import_sympify_parsing(self):
        # sp.sympify()/parse_expr() must no longer be applied to RAW
        # USER TEXT anywhere in core/context.py. sp.sympify() itself may
        # of course still be importable (as a module attribute) -- what
        # matters is that it's never CALLED anymore.
        import inspect

        import core.context as context_module

        source = inspect.getsource(context_module)
        assert "sympify(" not in source
        assert "parse_expr(" not in source


# ---------------------------------------------------------------------
# lambdify(): the second way to code execution (found 12.11.2026).
#
# An unknown function name stays an inert sp.Function(name)(...)
# placeholder in the bridge -- but plot() hands its expression to
# sp.lambdify(), and so does sp.nsolve() internally. lambdify() prints
# the expression as Python source and exec()s it, and an unknown
# function is printed as a plain call "name(args)". "plot(print(x), x)"
# therefore really called print(); with NumPy names in reach (uint8,
# load, ...) a longer input could run arbitrary Python on the server.
#
# The tests use a canary: a function that is reachable as a builtin
# under a name EngiPad does not know. If anything ever calls it, user
# input reached Python again.
# ---------------------------------------------------------------------
@pytest.fixture
def canary(monkeypatch):
    import builtins

    calls = []

    def engipad_canary(*args):
        calls.append(args)
        return 1.0

    monkeypatch.setattr(builtins, "engipad_canary", engipad_canary, raising=False)
    return calls


class TestUnknownFunctionsNeverReachLambdify:
    CANARY_INPUTS = [
        "plot(engipad_canary(x), x)",
        "plot(sin(x) + engipad_canary(x), x, 0, 1)",
        "plot(x, x, engipad_canary(1), 5)",
        "plot(x, x, 0, engipad_canary(1))",
        "f = engipad_canary(x) + x\nplot(f, x)",
        "y = nsolve(x + engipad_canary(x), 1)",
        "y = nsolve(x + engipad_canary(x), x, 1)",
        "y = nsolve(Eq(x, engipad_canary(x)), x, 1)",
        "y = nsolve([x + engipad_canary(x), z - 1], [x, z], [1, 1])",
        "y = nsolve(x^2 - 2, x, engipad_canary(1))",
        "y = engipad_canary(2)",
        "y = integrate(engipad_canary(x), (x, 0, 1))",
        "y = sum(engipad_canary(n), (n, 0, 3))",
        "y = prod(engipad_canary(n), (n, 1, 3))",
        "y = diff(engipad_canary(x), x)",
        "y = lim(engipad_canary(x), x, 0)",
        "y = solve(Eq(engipad_canary(x), 1), x)",
        "y = mat([[engipad_canary(1), 2], [3, 4]])",
        "y = det(mat([[engipad_canary(1), 2], [3, 4]]))",
        "y := engipad_canary(x)",
    ]

    @pytest.mark.parametrize("code", CANARY_INPUTS)
    def test_unknown_function_is_never_called(self, canary, code):
        evaluate_code(code + "\n")
        assert canary == [], f"user input reached a Python call: {code!r}"

    def test_plot_names_the_unknown_function(self):
        rendered = str(evaluate_code("plot(print(x), x)\n"))
        assert "Unknown function" in rendered
        assert "print" in rendered
        assert "'type': 'plot'" not in rendered

    def test_nsolve_names_the_unknown_function(self):
        rendered = str(evaluate_code("y = nsolve(x + print(x), 1)\n"))
        assert "Unknown function" in rendered

    def test_builtin_call_through_numpy_names_does_not_run(self, tmp_path):
        # The shape of the input that created a file on the server
        # before the fix: exec(bytes(uint8(vec(<character codes>)))).
        marker = tmp_path / "marker"
        source = f"open({str(marker)!r}, 'w').write('x')"
        codes = ",".join(str(b) for b in source.encode())
        for line in (
            f"plot(exec(bytes(uint8(vec({codes})))), x)",
            f"y = nsolve(x + exec(bytes(uint8(vec({codes})))), x, 1)",
        ):
            evaluate_code(line + "\n")
            assert not marker.exists(), line[:40]

    def test_same_input_through_the_http_route(self, canary):
        from app import app

        response = app.test_client().post(
            "/", data={"code": "plot(engipad_canary(x), x)\n"}
        )
        assert response.status_code == 200
        assert "Unknown function" in response.get_data(as_text=True)
        # (The canary list of THIS process cannot see a call in the
        # child process; the response text is the check here.)

    def test_plot_variable_must_be_a_plain_name(self):
        rendered = str(evaluate_code("plot(x, x + 1, 0, 1)\n"))
        assert "must be a variable name" in rendered

    def test_plot_reports_variables_without_a_value(self):
        rendered = str(evaluate_code("plot(b*x, x)\n"))
        assert "without a value" in rendered and "b" in rendered

    def test_normal_plots_still_work(self):
        for code in (
            "plot(sin(x) + abs(x), x, 0, 5)",
            "f = x^2 - 2\nplot(f, x)",
            "L = 2'm ; F = 3'kN\nM = F*(L - x)\nplot(M, x, 0, 2)",
            "plot(sin(t), t, 0, 5)",
        ):
            rendered = str(evaluate_code(code + "\n"))
            assert "'type': 'plot'" in rendered, code

    def test_every_lambdify_call_site_is_guarded(self):
        # Static check: lambdify() may only be called in
        # mathlib/plotting.py and in _nintegrate() (mathlib/functions.py),
        # each time after reject_unknown_functions(); nsolve (which uses
        # lambdify internally) only through the wrapper.
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        for path in list(root.glob("*.py")) + [
            p for folder in ("core", "mathlib", "parsing", "rendering")
            for p in (root / folder).glob("*.py")
        ]:
            source = path.read_text(encoding="utf-8")
            if path.name == "plotting.py":
                assert source.index("reject_unknown_functions(expr_sym") < source.index("sp.lambdify(")
                continue
            if path.name == "functions.py":
                assert '"nsolve": _nsolve' in source
                # nsolve: the system case in _nsolve() and twice in
                # _solve_one(), which only _nsolve() calls -- after
                # reject_unknown_functions(list(args), "nsolve()").
                assert source.count("sp.nsolve(") == 3
                solver = source.index("def _nsolve(")
                assert source.index('reject_unknown_functions(list(args), "nsolve()")') > solver
                assert "_solve_one(" not in source[:solver]
                # lambdify: exactly once, in _nintegrate(), after its guard.
                code = source.replace("sp.lambdify()", "")  # mentions in comments
                assert code.count("sp.lambdify(") == 1
                integrate = code.index("def _nintegrate(")
                guard = code.index('reject_unknown_functions(list(args), "nintegrate()")')
                assert integrate < guard < code.index("sp.lambdify(")
                continue
            assert "lambdify(" not in source.replace("lambdify()", ""), path

    def test_module_and_symbols_are_not_reachable(self):
        from mathlib.functions import DISPLAY_FUNCTIONS, NUMERIC_FUNCTIONS, PLOT_FUNCTIONS

        for table in (NUMERIC_FUNCTIONS, DISPLAY_FUNCTIONS, PLOT_FUNCTIONS):
            assert "sp" not in table
            assert "symbols" not in table
        # "sp" used to print the path of the SymPy installation.
        assert "site-packages" not in str(evaluate_code("sp\n"))


# ---------------------------------------------------------------------
# HTML: the result column must show user text, never interpret it.
# ---------------------------------------------------------------------
class TestResultColumnEscapesHtml:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    @pytest.mark.parametrize("code", [
        '"<img src=x onerror=alert(1)>"',
        "'<img src=x onerror=alert(1)>'",
        "<img src=x onerror=alert(1)> = 5",
        "a = <img src=x onerror=alert(1)>",
        "a := <img src=x onerror=alert(1)>",
        "a = 5 | <img src=x onerror=alert(1)>",
        "plot(<img src=x onerror=alert(1)>, x)",
    ])
    def test_markup_from_the_input_arrives_escaped(self, client, code):
        body = client.post("/", data={"code": code + "\n"}).get_data(as_text=True)
        results = body.split('id="code-textarea"')[-1]
        assert "<img src=x" not in results
        assert "&lt;img src=x" in results

    def test_comparison_signs_in_a_comment_survive(self, client):
        body = client.post(
            "/", data={"code": '"Check: a<b and c>d"\n'}
        ).get_data(as_text=True)
        assert "a&lt;b and c&gt;d" in body

    def test_matrix_ampersands_are_escaped_once(self, client):
        body = client.post(
            "/", data={"code": "M = mat([[1,2],[3,4]])\n"}
        ).get_data(as_text=True)
        assert "1 &amp; 2" in body
        assert "&amp;amp;" not in body

    def test_backslash_in_a_variable_name_is_plain_text(self):
        # MathJax's html extension knows \href{javascript:...}{...}; the
        # left side of "=" is raw user text.
        rendered = evaluate_code("\\href{javascript:alert(1)}{x} = 5\n")[0][0]["content"]
        assert "\\href" not in rendered
        assert "textbackslash" in rendered

    def test_template_does_not_mark_results_as_safe(self):
        from pathlib import Path

        # every page template (base.html holds the result column)
        template = "".join(
            path.read_text(encoding="utf-8")
            for path in (Path(__file__).resolve().parent.parent / "templates").glob("*.html")
        )
        assert "render_blocks" in template
        assert "item.content | safe" not in template
        assert "item.content|safe" not in template


# ---------------------------------------------------------------------
# User-defined functions (13.11.2026) must not open a way around the
# lambdify guard: the body of a function is a parsed formula that goes
# through the same bridge and whitelist, and plot()/nsolve() see the
# expanded formula.
# ---------------------------------------------------------------------
class TestUserFunctionsKeepTheGuard:
    CANARY_INPUTS = [
        "f(x) = engipad_canary(x)\ny = f(2)",
        "f(x) = engipad_canary(x)\nplot(f(x), x, 0, 1)",
        "f(x) = x + engipad_canary(x)\ny = nsolve(f(x), x, 1)",
        "f(x) = if(x < 0, engipad_canary(x), x)\nplot(f(x), x, -1, 1)",
        "f(x) = x\nplot(f(engipad_canary(x)), x, 0, 1)",
        "f(x) = x\nplot(f(x), x, engipad_canary(0), 1)",
        "y = if(engipad_canary(1) < 2, 1, 2)",
        "y = min(engipad_canary(1), 2)",
        "y = round(engipad_canary(1))",
        "y = nintegrate(engipad_canary(x), (x, 0, 1))",
        "y = nintegrate(x, (x, 0, engipad_canary(1)))",
        "f(x) = engipad_canary(x)\ny = nintegrate(f(x), (x, 0, 1))",
        "y = interp(vec(1, 2), vec(engipad_canary(1), 2), 1.5)",
        "y = interp(vec(1, 2), vec(1, 2), engipad_canary(1))",
        "f(x) = interp(vec(1, 2), vec(1, 2), engipad_canary(x))\nplot(f(x), x, 1, 2)",
        "y = interp2(vec(1, 2), vec(1, 2), mat([[1, 2], [3, 4]]), engipad_canary(1), 1)",
        "v = vec(1, 2)\ny = v[engipad_canary(1)]",
        "engipad_canary(1) < 2",
    ]

    @pytest.mark.parametrize("code", CANARY_INPUTS)
    def test_unknown_function_inside_is_never_called(self, canary, code):
        evaluate_code(code + "\n")
        assert canary == [], f"user input reached a Python call: {code!r}"

    def test_a_user_function_named_like_a_python_builtin_is_only_its_formula(self, tmp_path):
        # "exec" / "print" / "open" are ordinary free names in EngiPad.
        # Defining them as a function must never reach the Python
        # builtin of that name: the call is replaced by the body BEFORE
        # anything is handed to lambdify().
        marker = tmp_path / "marker"
        source = f"open({str(marker)!r}, 'w').write('x')"
        codes = ",".join(str(b) for b in source.encode())
        code = (
            "exec(a) = a\n"
            "bytes(a) = a\n"
            "print(a) = a + 1\n"
            f"plot(exec(bytes(x)) + print(x), x, 0, 1)\n"
            f"y = nsolve(exec(x) - 1, x, 2)\n"
            f"z = exec(bytes(uint8(vec({codes}))))\n"
            f"plot(exec(bytes(uint8(vec({codes})))), x)\n"
        )
        rendered = str(evaluate_code(code))
        assert not marker.exists()
        assert "Unknown function 'uint8'" in rendered  # the last plot is refused
        assert rendered.count("'type': 'plot'") == 1   # the first one is a normal plot

    def test_placeholder_of_an_undefined_function_is_still_refused(self):
        rendered = str(evaluate_code("plot(g(x), x)\ny = nsolve(g(x), x, 1)\n"))
        assert rendered.count("Unknown function") == 2

    def test_function_body_is_stored_as_a_tree_not_as_text(self):
        from core.context import EvaluationContext
        from mathlib.sympy_bridge import UserFunction

        ctx = EvaluationContext()
        ctx.eval_line("f(x) = x^2 + 1")
        function = ctx.user_functions["f"]
        assert isinstance(function, UserFunction)
        assert not isinstance(function.body, str)

    def test_deep_nesting_is_stopped(self):
        lines = ["f0(x) = x"] + [f"f{i}(x) = f{i-1}(x)" for i in range(1, 60)]
        rendered = str(evaluate_code("\n".join(lines) + "\ny = f59(1)\n"))
        assert "nested too deeply" in rendered

    def test_comparison_operators_arrive_escaped_in_the_page(self):
        from app import app

        body = app.test_client().post(
            "/", data={"code": "a = 1\na < 2\n"}
        ).get_data(as_text=True)
        results = body.split('id="code-textarea"')[-1]
        assert "a &lt; 2" in results
        assert "a < 2" not in results
