"""core/request_options.py: the form reading, the page values and the
LaTeX export shared by the server version (app.py) and the web version.

Most of the behaviour is covered through the Flask routes elsewhere
(test_precision_setting, test_timeout_setting, test_latex_export,
test_images, ...); this file tests the module directly, without Flask,
the way the web version uses it.
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from core import request_options as options

ROOT = Path(__file__).resolve().parent.parent


def _evaluate_recorder(outcome=None):
    calls = []

    def evaluate(*args):
        calls.append(args)
        return outcome or {"names": [], "active": None,
                           "results": [[{"type": "latex", "content": "a = 1"}]], "all": None}
    evaluate.calls = calls
    return evaluate


class TestRunsWithoutServerModules:
    def test_import_needs_neither_flask_nor_multiprocessing(self):
        # The web version (Pyodide in the browser) has neither.
        code = (
            "import sys\n"
            "for name in ('flask', 'werkzeug', 'multiprocessing'):\n"
            "    sys.modules[name] = None\n"
            "from core import request_options as o\n"
            "ctx = o.page_context({'code': 'a = 2*3'}, lambda *a: __import__('core.workbook', fromlist=['x'])"
            ".evaluate_workbook(a[0], rel_tol=a[1]), default_timeout=10, local_mode=False, live_default=True)\n"
            "print(ctx['results'][0][0]['content'])\n"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert "a = 2 \\cdot 3 = 6" in out.stdout

    def test_safe_runner_takes_the_default_timeout_from_here(self):
        from core import safe_runner
        assert safe_runner.DEFAULT_TIMEOUT_SECONDS == options.DEFAULT_TIMEOUT_SECONDS == 10


class TestPageContext:
    def test_first_view_shows_the_defaults_and_computes_nothing(self):
        from core.engine import DEFAULT_INPUT
        evaluate = _evaluate_recorder()
        ctx = options.page_context(None, evaluate, default_timeout=10, local_mode=False,
                                   live_default=False, shutdown_token="secret")
        assert evaluate.calls == []
        assert ctx["code"] == DEFAULT_INPUT
        assert ctx["results"] == []
        assert ctx["timeout"] == "10"
        assert ctx["precision"] == options.DEFAULT_PRECISION
        assert ctx["shutdown_token"] == ""          # only for the local version
        assert ctx["local_mode"] is False and ctx["live_default"] is False

    def test_form_values_reach_evaluate_and_the_page(self):
        evaluate = _evaluate_recorder()
        form = {"code": "x = 1", "precision": "1", "timeout": "25", "active_sheet": "B",
                "all_sheets": "1", "image_names": "logo, bad name,ok_2", "font_size": "99",
                "unit_font_size": "abc", "sheet_font_size": "70"}
        ctx = options.page_context(form, evaluate, default_timeout=10, local_mode=True,
                                   live_default=True, shutdown_token="secret")
        (code, rel_tol, timeout_raw, active, all_sheets, image_names), = evaluate.calls
        assert (code, rel_tol, timeout_raw, active, all_sheets) == ("x = 1", 0.01, "25", "B", True)
        assert image_names == frozenset({"logo", "ok_2"})
        assert ctx["timeout"] == "25"
        assert ctx["font_size"] == "99" and ctx["font_size_px"] == 40          # raw text stays, value clamped
        assert ctx["unit_font_size"] == "abc" and ctx["unit_font_size_pct"] == 80
        assert ctx["sheet_font_size_pct"] == 70
        assert ctx["shutdown_token"] == "secret"

    def test_empty_timeout_field_shows_the_default(self):
        ctx = options.page_context({"code": "x = 1", "timeout": ""}, _evaluate_recorder(),
                                   default_timeout=7, local_mode=False, live_default=False)
        assert ctx["timeout"] == "7"


class TestFailures:
    def test_timeout_message_with_and_without_the_hint(self):
        assert options.timeout_message(5) == (
            "Timeout: computation took longer than 5s and was aborted. "
            "You can increase the timeout in Settings (max. 30s).")
        assert "increase" not in options.timeout_message(30)

    def test_failure_outcome_keeps_the_sheet_tabs(self):
        outcome = options.failure_outcome("##A\nx = 1\n##B\ny = 2\n", "B", "Timeout: x")
        assert outcome["names"] == ["A", "B"] and outcome["active"] == "B"
        assert "Timeout" in outcome["results"][0][0]["content"]
        assert options.failure_outcome("x = 1\n", None, "m")["names"] == []

    @pytest.mark.parametrize("raw, expected", [
        ("5", 5), ("0", None), ("-3", None), ("abc", None), ("", None), ("nan", None),
        ("inf", None), ("99", 30), ("0.4", 1), ("12.6", 13),
    ])
    def test_timeout_field(self, raw, expected):
        assert options.parse_timeout_seconds(raw) == expected

    def test_timeout_upper_limit_can_be_given(self):
        assert options.parse_timeout_seconds("20", max_seconds=12) == 12


class TestLatexExport:
    def _real_evaluate(self, user_input, rel_tol, timeout_raw, active, all_sheets, image_names):
        from core.workbook import evaluate_workbook
        return evaluate_workbook(user_input, rel_tol=rel_tol, active=active,
                                 all_sheets=all_sheets, image_names=image_names)

    def test_plain_sheet_is_a_tex_file(self):
        name, data, mimetype = options.latex_export({"code": "a = 3\n"}, self._real_evaluate)
        assert (name, mimetype) == ("engipad_export.tex", "application/x-tex")
        assert data.startswith(b"\\documentclass")

    def test_plot_makes_a_zip(self):
        name, data, mimetype = options.latex_export({"code": "plot(x^2, x, 0, 1)\n"}, self._real_evaluate)
        assert (name, mimetype) == ("engipad_export.zip", "application/zip")
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert "engipad_export.tex" in names and "plot_1.png" in names

    def test_sheet_name_goes_into_the_file_name(self):
        name, _, _ = options.latex_export({"code": "##Beam_1\nx = 1\n"}, self._real_evaluate)
        assert name == "engipad_Beam_1.tex"
        name, _, _ = options.latex_export({"code": "##A\nx = 1\n##B\ny = 2\n", "all_sheets": "1"},
                                          self._real_evaluate)
        assert name == "engipad_workbook.tex"

    def test_broken_image_data_is_an_export_error(self):
        with pytest.raises(options.ExportError, match="could not be read"):
            options.latex_export({"code": "a = 1\n", "images": "not json"}, self._real_evaluate)
