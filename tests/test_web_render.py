"""The Python side of the web version (web/engipad_web.py), in normal
CPython: it must produce the same page and the same LaTeX export as the
Flask routes for the same input -- one code base, two shells.

The pages may differ only in the lines of the shells (templates/index.html
vs templates/web.html) and in the paths (relative in the web version);
everything else, in particular the result and the print area, must be
identical.
"""
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import app as app_module
from tests.test_editor_browser import tiny_png
from web import engipad_web

ROOT = Path(__file__).resolve().parent.parent

# Lines that belong to the shells and may differ (server line, web line).
SHELL_LINES = [
    re.compile(r'<input type="checkbox" id="live-update-toggle" data-default="[01]"'),
    re.compile(r'<form (method="post" )?id="calc-form"'),
    re.compile(r'<button type="(submit|button)" (form="calc-form" formaction="/export/latex" )?'
               r'class="tool-btn" (id="export-btn" )?data-icon="export"'),
    re.compile(r"<dt>Built with</dt>"),
    re.compile(r'<div class="web-status" id="web-status"'),
    re.compile(r'<button type="button" class="tool-btn" id="exit-btn"'),
    re.compile(r'\{% if local_mode %\}|\{% endif %\}'),
    re.compile(r'<script src="static/(server_shell|web/web_shell)\.js'),
    re.compile(r"^\s*$"),
]


def _server_page(form, local=False):
    app_module.app.config["ENGIPAD_LOCAL"] = local
    try:
        client = app_module.app.test_client()
        response = client.post("/", data=form) if form is not None else client.get("/")
        return response.get_data(as_text=True)
    finally:
        app_module.app.config["ENGIPAD_LOCAL"] = False


def _differences(server_html, web_html):
    import difflib
    server_html = server_html.replace('="/static/', '="static/')
    return [
        line for line in difflib.unified_diff(server_html.splitlines(), web_html.splitlines(),
                                              lineterm="", n=0)
        if not line.startswith(("---", "+++", "@@"))
    ]


def _between(html, start, end):
    i = html.index(start)
    return html[i:html.index(end, i)]


FORMS = {
    "first view": None,
    "simple": {"code": "a = 3\nb = 4\nc = sqrt(a^2 + b^2)\n"},
    "settings": {"code": "x = 1/3\nF = 5'kN | N\n", "precision": "1", "font_size": "18",
                 "unit_font_size": "120", "timeout": "25", "image_limit": "2"},
    "invalid settings": {"code": "x = 1\n", "font_size": "abc", "timeout": "-5",
                         "precision": "<b>", "unit_font_size": "9999"},
    "sheets": {"code": "##Loads\nF = 12'kN\nL = 3'm\n##Beam\nM = Loads::F * Loads::L / 4 | kNm\n",
               "active_sheet": "Beam", "all_sheets": "1", "sheet_font_size": "70"},
    "errors and html": {"code": "a = 5'm + 3'kg\n\"<b>bold</b> & <script>x</script>\"\n# c\n"},
    "plot": {"code": "f(x) = x^2\nplot(f(x), x, 0, 2)\n"},
    "pictures": {"code": 'picture("logo", width=100, align=left)\npicture("missing")\n',
                 "image_names": "logo"},
    "functions, solve, matrix": {"code": "sol = solve(Eq(x^2 - 5, 0), x) ; x_1 = sol[1]\n"
                                         "A = mat([[1, 2], [3, 4]]) ; det(A)\nσ = 180'MPa ; σ <= 235'MPa\n"},
    "nintegrate": {"code": "q = 5'kN/m ; L = 4'm\nW = nintegrate(q*s, (s, 0'm, L)) | kNm\n"},
}


class TestSamePageAsTheServer:
    @pytest.mark.parametrize("name", list(FORMS))
    def test_only_the_shell_lines_differ(self, name):
        form = FORMS[name]
        server = _server_page(form)
        web = engipad_web.render_page(form)
        rest = [line for line in _differences(server, web)
                if not any(p.search(line[1:]) for p in SHELL_LINES)]
        assert not rest, "\n".join(rest[:20])

    @pytest.mark.parametrize("name", list(FORMS))
    def test_result_and_print_area_are_identical(self, name):
        form = FORMS[name]
        server = _server_page(form).replace('="/static/', '="static/')
        web = engipad_web.render_page(form)
        for start, end in (('<div class="output-wrap">', "</section>"),
                           ('<textarea id="code-textarea"', "</form>")):
            assert _between(server, start, end) == _between(web, start, end)

    def test_local_server_page_differs_only_by_the_quit_button(self):
        web = engipad_web.render_page(FORMS["simple"])
        rest = [line for line in _differences(_server_page(FORMS["simple"], local=True), web)
                if not any(p.search(line[1:]) for p in SHELL_LINES)]
        assert not rest, rest


class TestWebShellOfThePage:
    page = engipad_web.render_page(None)

    def test_no_server_addresses(self):
        assert 'method="post"' not in self.page
        assert "/export/latex" not in self.page
        assert "/shutdown" not in self.page
        assert 'src="/' not in self.page and 'href="/' not in self.page

    def test_web_parts_are_there(self):
        assert 'id="export-btn"' in self.page
        assert 'id="web-status"' in self.page
        assert 'src="static/web/web_shell.js' in self.page
        assert "server_shell.js" not in self.page
        assert 'id="exit-btn"' not in self.page
        assert "Python (Pyodide), SymPy" in self.page

    def test_live_update_is_on_by_default(self):
        assert 'id="live-update-toggle" data-default="1"' in self.page

    def test_autoescaping_is_on_like_in_flask(self):
        page = engipad_web.render_page({"code": '"<img src=x onerror=alert(1)>"\n',
                                        "timeout": '"><script>x</script>'})
        assert "<img src=x" not in page
        assert "<script>x</script>" not in page

    def test_image_config_json_like_flask(self):
        server = _server_page(None)
        line = re.search(r"window\.ENGIPAD_IMAGES = .*;", server).group(0)
        assert line in self.page


class TestSameExportAsTheServer:
    def _server(self, form):
        response = app_module.app.test_client().post("/export/latex", data=form)
        return response

    def _same_files(self, a, b):
        za, zb = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
        assert za.namelist() == zb.namelist()
        for n in za.namelist():
            assert za.read(n) == zb.read(n), n

    @pytest.mark.parametrize("form", [
        {"code": "a = 3\nb = a^2\n"},
        {"code": "plot(x^2, x, 0, 1)\n"},
        {"code": "##A\nx = 1\n##B\ny = A::x\n", "all_sheets": "1", "unit_font_size": "100"},
        {"code": "##S 1\nx = 1\n", "active_sheet": "S 1", "sheet_font_size": "80"},
    ])
    def test_same_file(self, form):
        server = self._server(form)
        name, data, mimetype = engipad_web.export_latex(form)
        assert server.headers["Content-Disposition"] == f"attachment; filename={name}"
        assert server.mimetype == mimetype
        if mimetype == "application/zip":
            self._same_files(server.data, data)
        else:
            assert server.data == data

    def test_same_file_with_a_picture(self, tmp_path):
        import base64
        png = base64.b64encode(Path(tiny_png(tmp_path)).read_bytes()).decode()
        form = {"code": 'picture("small")\n', "images": json.dumps({"small": png})}
        name, data, _ = engipad_web.export_latex(form)
        assert name == "engipad_export.zip"
        self._same_files(self._server(form).data, data)

    def test_invalid_images_give_the_same_message(self):
        form = {"code": "a = 1\n", "images": "not json"}
        server = self._server(form)
        assert server.status_code == 400
        assert engipad_web.export(form) == {"error": server.get_data(as_text=True)}


class TestTimeout:
    @pytest.mark.parametrize("raw, seconds", [("", 10), ("3", 3), ("99", 30), ("abc", 10)])
    def test_timeout_seconds(self, raw, seconds):
        assert engipad_web.timeout_seconds({"code": "", "timeout": raw}) == seconds

    def test_timeout_page_shows_the_server_message_and_keeps_the_tabs(self):
        form = {"code": "##A\nx = 1\n##B\ny = 2\n", "active_sheet": "B", "timeout": "4"}
        page = engipad_web.timeout_page(form)
        assert "Timeout: computation took longer than 4s and was aborted." in page
        assert "max. 30s" in page
        assert 'data-active="B"' in page


# The guard must be tested in a fresh interpreter: in this one NumPy and
# matplotlib are imported already (and stay there for the other tests).
GUARD_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from web import package_guard, engipad_web
package_guard.install()
steps = []
def run(code):
    r = engipad_web.calculate({"code": code})
    return r.get("need") or ("plot" if "data:image/png" in r["html"] else "page")
steps.append(run("a = 2*3\n"))                                   # no package needed
steps.append(run("plot(x^2, x, 0, 1)\n"))                        # needs matplotlib
package_guard.mark_available("matplotlib")
steps.append(run("plot(x^2, x, 0, 1)\n"))                        # NumPy is part of it
package_guard.mark_available("numpy")
steps.append(run("plot(x^2, x, 0, 1)\n"))
steps.append(run("q = nintegrate(t^2, (t, 0, 1))\n"))           # NumPy is there now
steps.append(engipad_web.export({"code": "rho = prop(rho, T, 300'K, p, 1'bar, Water)\n"}).get("need"))
print(json.dumps(steps))
'''


class TestPackageGuard:
    def test_calculation_reports_the_missing_group_and_works_once_it_is_there(self):
        out = subprocess.run([sys.executable, "-c", GUARD_SCRIPT, str(ROOT)],
                             capture_output=True, text=True, cwd=ROOT)
        assert out.returncode == 0, out.stderr
        steps = json.loads(out.stdout.strip().splitlines()[-1])
        assert steps[0] == "page"
        assert steps[1] == ["matplotlib"]
        assert steps[2] == ["numpy"]
        assert steps[3] == "plot"
        assert steps[4] == "page"
        assert steps[5] == ["coolprop"]

    def test_unknown_group_is_refused(self):
        from web import package_guard
        with pytest.raises(ValueError):
            package_guard.mark_available("scipy")

    def test_server_and_desktop_never_install_the_guard(self):
        for path in [ROOT / "app.py", ROOT / "run.py", *(ROOT / "core").glob("*.py"),
                     *(ROOT / "mathlib").glob("*.py")]:
            assert "package_guard" not in path.read_text(encoding="utf-8"), path


class TestRunsWithoutFlask:
    def test_web_module_needs_neither_flask_nor_multiprocessing(self):
        code = (
            "import sys\n"
            "for name in ('flask', 'werkzeug', 'multiprocessing'):\n"
            "    sys.modules[name] = None\n"
            "from web import engipad_web\n"
            "page = engipad_web.render_page({'code': 'a = 6*7'})\n"
            "print('= 42' in page)\n"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "True"
