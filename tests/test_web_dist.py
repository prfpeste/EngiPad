"""The built web version (tools/build_web.py) in a real Chromium, served
with the MIME types of Moodle (tools/moodle_like_server.py), once directly
and once inside an iframe of a page on another port.

Needs a built package: set ENGIPAD_WEB_DIST to the folder (dist_web).
Skipped without it or without Playwright/Chromium. Every request to a
host other than 127.0.0.1 fails the test (the page must work offline).
"""
import io
import os
import sys
import threading
import zipfile
from pathlib import Path

import pytest

DIST = os.environ.get("ENGIPAD_WEB_DIST")
pytestmark = pytest.mark.skipif(
    not DIST or not os.path.isfile(os.path.join(DIST or "", "web_manifest.json")),
    reason="set ENGIPAD_WEB_DIST to a built web version (tools/build_web.py)",
)
playwright_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from moodle_like_server import make_server  # noqa: E402
from tests.test_editor_browser import tiny_png  # noqa: E402

READY = "['ready', 'complete'].includes(document.documentElement.getAttribute('data-engipad-python'))"
COMPLETE = "document.documentElement.getAttribute('data-engipad-python') === 'complete'"
SLOW = "s = sum(1/(i^2+i^3+1), (i, 1, 200000))\n"     # many seconds, pure SymPy


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def urls(tmp_path_factory):
    package = _serve(make_server(DIST, 0))
    port = package.server_address[1]
    outer_dir = tmp_path_factory.mktemp("outer")
    (outer_dir / "index.html").write_text(
        "<!DOCTYPE html><html><body><h1>Outer page (like Moodle)</h1>"
        f'<iframe id="f" src="http://127.0.0.1:{port}/index.html" '
        'style="width:100%;height:700px;border:none" title="EngiPad"></iframe>'
        "</body></html>", encoding="utf-8")
    outer = _serve(make_server(str(outer_dir), 0))
    yield {"page": f"http://127.0.0.1:{port}/index.html",
           "outer": f"http://127.0.0.1:{outer.server_address[1]}/index.html"}
    package.shutdown()
    outer.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            try:
                b = p.chromium.launch(channel="chrome")
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no Chromium: {exc}")
        yield b
        b.close()


class Session:
    """One browser tab with EngiPad (or the outer page with it in an iframe)."""

    def __init__(self, browser, url, in_iframe=False, routes=(), context=None):
        self.context = context or browser.new_context(
            viewport={"width": 1300, "height": 800}, accept_downloads=True)
        self.page = self.context.new_page()
        self.foreign, self.errors, self.dialogs = [], [], []

        def guard(route):
            if route.request.url.startswith(("http://127.0.0.1:", "data:", "blob:")):
                route.continue_()
            else:
                self.foreign.append(route.request.url)
                route.abort()

        self.page.route("**/*", guard)
        for pattern, handler in routes:          # registered later = tried first
            self.page.route(pattern, handler)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("dialog", lambda d: (self.dialogs.append(d.message), d.accept()))
        self.page.goto(url)
        self.frame = self.page
        if in_iframe:
            self.frame = self.page.wait_for_selector("#f").content_frame()
        self.frame.wait_for_selector('button[data-icon="calc"] svg', timeout=60_000)
        # Record every computed page (the HTML from Python, before MathJax).
        self.frame.evaluate(
            "() => { window.__pages = []; const f = EngiPadEditor.applyResultPage;"
            " EngiPadEditor.applyResultPage = function (h) { window.__pages.push(h); return f(h); }; }")

    def wait_ready(self, complete=False):
        self.frame.wait_for_function(COMPLETE if complete else READY, timeout=180_000)

    def calculate(self, code, settings=None):
        """Types the code, presses Calculate, returns the computed page."""
        for field, value in (settings or {}).items():   # fields of the (closed) Settings menu
            self.frame.eval_on_selector(field, "(el, v) => { el.value = v; }", value)
        self.frame.fill("#code-textarea", code)
        count = self.frame.evaluate("window.__pages.length")
        self.frame.click('button[data-icon="calc"]')
        self.frame.wait_for_function(f"window.__pages.length > {count}", timeout=120_000)
        return self.frame.evaluate("window.__pages[window.__pages.length - 1]")

    def start_mode(self):
        return self.frame.evaluate("document.documentElement.getAttribute('data-engipad-start')")

    def state(self):
        return self.frame.evaluate("document.documentElement.getAttribute('data-engipad-python')")

    def close(self, keep_context=False):
        assert not self.foreign, f"request to another host: {self.foreign}"
        assert not self.errors, self.errors
        if keep_context:
            self.page.close()
        else:
            self.context.close()


@pytest.fixture
def session(browser, urls):
    s = Session(browser, urls["page"])
    yield s
    s.close()


def _result_area(html):
    return html.split('<div class="output-container"', 1)[1].split("</section>", 1)[0]


def _formulas(area):
    import re
    return re.findall(r"<div>\$\$ (.*?) \$\$</div>", area, re.S)


class TestStart:
    def test_page_is_there_at_once_and_python_gets_ready(self, session):
        # the editor works while Python is still loading
        session.frame.fill("#code-textarea", "a = 1")
        session.wait_ready()
        session.wait_ready(complete=True)
        status = session.frame.locator("#web-status")
        assert not status.is_visible()

    def test_no_server_parts_on_the_page(self, session):
        assert session.frame.locator("#exit-btn").count() == 0
        assert session.frame.evaluate("typeof EngiPadShell.calculate") == "function"


class TestCalculations:
    SHEETS = [
        ("u(r) = u_max*(1 - r^2/R^2) ; R = 10'mm ; u_max = 0.2'm/s\n"
         "V__dot = integrate(2*π*r*u(r), (r, 0'm, R)) | L/min\n", "1.885"),
        ("c_p = prop(c_p, T, 45'degC, p, 1'bar, Water) | kJ/(kg*K)\n", "4.1801"),
        ("h = haprop(h, T, 25'degC, p, 1.01325'bar, φ, 0.6) | kJ/kg\n", "55.574"),
        ("K = 2 ; T = 0.5's ; D = 0.3 ; ω_0 = 1/T | rad/s ; δ = D*ω_0 | rad/s\n"
         "ω_d = ω_0*sqrt(1 - D^2) | rad/s\n"
         "h_2(t) = K*(1 - exp(-δ*t)*(cos(ω_d*t) + δ/ω_d*sin(ω_d*t)))\n"
         "ISE = nintegrate((K - h_2(t))^2, (t, 0's, 10's))\n", "2.2667"),
        ("T = 300.15'K ; e = 1.602*10^-19'C ; k = 1.38*10^-23'J/K\ni_k = 200 ; i_s = 9*10^-9\n"
         "U_max = nsolve(Eq(exp(e*U_max/(k*T)), (1+i_k/i_s)/(1+e*U_max/(k*T))), U_max, 0.5'V) | mV\n",
         "536.37"),
        ("A = mat([[2, -1], [-1, 2]]) ; ev = eigenvals(A)\n"
         "sol = solve(Eq(x^2 - 5, 0), x) ; x_1 = sol[1]\n", "\\sqrt{5}"),
        ("\"<b>Text</b> & more\" ; a = 5'm + 3'kg\n", "incompatible units"),
    ]

    def test_example_sheets_give_the_same_formulas_as_cpython(self, session):
        """Every formula of the browser page equals the one this CPython
        computes with the same engine (CoolProp 7.2 in the browser vs 6.8
        here agree far beyond the displayed digits)."""
        from web import engipad_web
        session.wait_ready(complete=True)
        for code, expected in self.SHEETS:
            area = _result_area(session.calculate(code))
            assert expected in area, (code, area[:1500])
            here = _result_area(engipad_web.render_page({"code": code, "image_names": ""}))
            assert _formulas(area) == _formulas(here), code
            if "incompatible" not in expected:
                assert "Error" not in area, (code, area[:1500])

    def test_plot_is_a_png(self, session):
        session.wait_ready()
        html = session.calculate("plot(sin(x), x, 0, 2*pi)\n")
        assert '<img src="data:image/png;base64,' in _result_area(html)
        session.frame.wait_for_selector(".output-container img[alt='Plot']")

    def test_result_is_typeset_without_reload(self, session):
        session.wait_ready()
        session.frame.evaluate("window.__marker = 1")
        session.calculate("F = 12'kN ; a = abs(-10)\n")
        session.frame.wait_for_function(
            "document.querySelectorAll('.col-result mjx-container').length >= 1")
        assert session.frame.evaluate("window.__marker") == 1     # no page reload

    def test_live_update_on_enter(self, session):
        session.wait_ready()
        session.frame.evaluate("document.getElementById('live-update-toggle').checked")
        session.frame.click("#code-textarea")
        session.frame.fill("#code-textarea", "")
        session.page.keyboard.type("b = 6*7")
        session.page.keyboard.press("Enter")
        session.frame.wait_for_function(
            "document.querySelector('.col-result').textContent.includes('42')", timeout=60_000)

    def test_workbook_with_two_sheets(self, session, tmp_path):
        session.wait_ready()
        f = tmp_path / "book.txt"
        f.write_text("##Loads\nF = 12'kN\nL = 3'm\n##Beam\nM = Loads::F * Loads::L / 4 | kNm\n",
                     encoding="utf-8")
        session.frame.set_input_files("#file-input", str(f))
        session.frame.wait_for_selector("#sheet-tabs >> text=Beam")
        session.frame.click("#sheet-tabs >> text=Beam")
        count = session.frame.evaluate("window.__pages.length")
        session.frame.click('button[data-icon="calc"]')
        session.frame.wait_for_function(f"window.__pages.length > {count}")
        html = session.frame.evaluate("window.__pages[window.__pages.length - 1]")
        assert "= 9\\," in _result_area(html)


class TestBackgroundLoading:
    def test_plot_and_prop_right_after_ready_wait_for_their_packages(self, session):
        session.wait_ready()
        assert session.state() == "ready"        # background not finished yet
        html = session.calculate("ρ = prop(ρ, T, 20'degC, p, 1'bar, Water)\nplot(x^2, x, 0, 1)\n")
        area = _result_area(html)
        assert "998.21" in area and "data:image/png" in area, area[:1500]
        assert "still loading" not in area


class TestTimeout:
    def test_stopped_after_the_timeout_and_calculating_again(self, session):
        session.wait_ready()
        html = session.calculate(SLOW, settings={"#timeout-input": "1"})
        assert "Timeout: computation took longer than 1s and was aborted." in _result_area(html)
        session.wait_ready()
        html = session.calculate("z = 2^8\n", settings={"#timeout-input": "10"})
        assert "= 256" in _result_area(html)

    def test_loading_a_package_does_not_count_into_the_timeout(self, session):
        session.wait_ready()
        if session.state() == "complete":
            pytest.skip("background loading already finished")
        # CoolProp takes several seconds to load; the timeout is 2 s
        html = session.calculate("c = prop(c_p, T, 300'K, p, 1'bar, Air)\n",
                                 settings={"#timeout-input": "2"})
        assert "Timeout" not in _result_area(html)


class TestExportAndPictures:
    def _export(self, session):
        with session.page.expect_download() as info:
            session.frame.click("#export-btn")
        d = info.value
        return d.suggested_filename, Path(d.path()).read_bytes()

    def test_tex_export(self, session):
        session.wait_ready()
        session.frame.fill("#code-textarea", "a = 3\nb = a^2\n")
        name, data = self._export(session)
        assert name == "engipad_export.tex"
        assert data.startswith(b"\\documentclass")

    def test_zip_export_with_plot_and_picture(self, session, tmp_path):
        session.wait_ready()
        session.frame.fill("#code-textarea", "")
        session.frame.set_input_files("#image-input", tiny_png(tmp_path))
        session.frame.wait_for_function("document.getElementById('code-textarea').value.includes('picture(')")
        session.frame.fill("#code-textarea", 'picture("small")\nplot(x, x, 0, 1)\n')
        html = session.calculate(session.frame.input_value("#code-textarea"))
        assert 'data-name="small"' in html
        session.frame.wait_for_function(
            "document.querySelector('.output-container img.engipad-picture').src.startsWith('data:image/png')")
        name, data = self._export(session)
        assert name == "engipad_export.zip"
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        assert {"engipad_export.tex", "plot_1.png", "img_small.png"} <= set(names)


class TestStartWithoutSnapshot:
    """Where the memory snapshot cannot be restored (Safari/WebKit: every
    browser on iPad and iPhone), Python is started normally instead."""

    def test_snapshot_is_used_normally(self, session):
        session.wait_ready()
        assert session.start_mode() == "snapshot"

    def test_unusable_snapshot_falls_back_and_is_remembered(self, browser, urls):
        import gzip
        requests = []

        def broken_snapshot(route):
            requests.append(route.request.url)
            route.fulfill(status=200, body=gzip.compress(bytes(4096)),
                          headers={"Content-Type": "application/g-zip"})

        first = Session(browser, urls["page"], routes=[("**/snapshot.bin.gz", broken_snapshot)])
        first.wait_ready()
        assert first.start_mode() == "normal"
        assert len(requests) == 1
        html = first.calculate("ρ = prop(ρ, T, 20'degC, p, 1'bar, Water)\nplot(x^2, x, 0, 1)\n"
                               "V = integrate(2*x, (x, 0, 3))\n")
        area = _result_area(html)
        assert "998.21" in area and "data:image/png" in area and "= 9" in area, area[:1500]
        built = first.frame.evaluate("localStorage.getItem('engipad_web_no_snapshot')")
        assert built
        first.close(keep_context=True)

        # next visit in the same browser: no second attempt with the snapshot
        second = Session(browser, urls["page"], routes=[("**/snapshot.bin.gz", broken_snapshot)],
                         context=first.context)
        second.wait_ready()
        assert second.start_mode() == "normal"
        assert len(requests) == 1
        second.close()

    def test_normal_start_with_the_parameter_and_restart_after_timeout(self, browser, urls):
        s = Session(browser, urls["page"] + "?snapshot=0")
        try:
            s.wait_ready()
            assert s.start_mode() == "normal"
            html = s.calculate(SLOW, settings={"#timeout-input": "1"})
            assert "Timeout: computation took longer than 1s" in _result_area(html)
            s.wait_ready()
            assert s.start_mode() == "normal"
            html = s.calculate("z = 2^8\n", settings={"#timeout-input": "10"})
            assert "= 256" in _result_area(html)
        finally:
            s.close()


def test_check_in_a_moodle_question_brings_work_and_result_back(browser, urls):
    """Moodle's "Check" reloads the quiz page with the iframe: the student's
    calculation of THIS question comes back and is calculated again; the
    next question starts empty."""
    base = urls["page"].rsplit("/", 1)[0]
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def quiz_page(route):
        slot = route.request.url.rsplit("slot=", 1)[1]
        route.fulfill(content_type="text/html", body=(
            f"<!DOCTYPE html><html><body><div class='que' id='question-501-{slot}'>"
            "<iframe id='f' src='/index.html' style='width:1300px;height:700px;border:none'></iframe>"
            "</div></body></html>"))

    page.route("**/mod/quiz/attempt.php*", quiz_page)

    def question(slot):
        page.goto(f"{base}/mod/quiz/attempt.php?attempt=501&slot={slot}")
        frame = page.wait_for_selector("#f").content_frame()
        frame.wait_for_function(READY, timeout=180_000)
        return frame

    frame = question(1)
    assert frame.input_value("#code-textarea") == ""
    frame.fill("#code-textarea", "s = 55'km/h * 130's | m\n")
    frame.click('button[data-icon="calc"]')
    frame.wait_for_function("document.querySelector('.col-result').textContent.includes('1986.1')")
    frame = question(1)                                   # "Check"
    assert "55'km/h" in frame.input_value("#code-textarea")
    frame.wait_for_function("document.querySelector('.col-result').textContent.includes('1986.1')",
                            timeout=60_000)               # calculated again by itself
    assert question(2).input_value("#code-textarea") == ""
    assert not errors, errors
    context.close()


def test_inside_an_iframe_of_another_origin(browser, urls):
    s = Session(browser, urls["outer"], in_iframe=True)
    try:
        s.wait_ready()
        html = s.calculate("a = 5'm/s^2 ; m = 10'kg ; F = m * a\n")
        assert "50\\," in _result_area(html)
        assert s.frame.evaluate("window.self !== window.top")
    finally:
        s.close()
