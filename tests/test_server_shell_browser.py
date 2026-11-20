"""Browser tests of the server shell (static/server_shell.js) together with
the shared editor (static/editor.js), with a real Chromium.

static/editor.js only calls EngiPadShell.calculate(); everything that is
specific to the server version lives in server_shell.js: posting the
calculation, the LaTeX export with the picture data, the Quit button.
These tests run exactly these paths. Skipped without playwright/Chromium,
like tests/test_editor_browser.py.
"""
import io
import threading
import zipfile

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from tests.test_editor_browser import tiny_png  # noqa: E402


def _serve(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def servers():
    remote = _serve(create_app())
    local_app = create_app()
    local_app.config["ENGIPAD_LOCAL"] = True
    local = _serve(local_app)
    yield {"server": f"http://127.0.0.1:{remote.server_port}/",
           "local": f"http://127.0.0.1:{local.server_port}/",
           "token": local_app.config["SHUTDOWN_TOKEN"]}
    remote.shutdown()
    local.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        yield instance
        instance.close()


def _open(browser, url, live):
    context = browser.new_context(viewport={"width": 1300, "height": 800}, accept_downloads=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(url, wait_until="domcontentloaded")
    page.evaluate("localStorage.clear(); localStorage.setItem('engipad_live_update', '%s')"
                  % ("1" if live else "0"))
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector('button[data-icon="calc"] svg')
    page.fill("#code-textarea", "")
    page.errors = errors
    return context, page


@pytest.fixture
def page(browser, servers):
    context, page = _open(browser, servers["server"], live=True)
    yield page
    assert not page.errors, page.errors
    context.close()


def _result(page):
    return page.inner_text(".output-container")


class TestCalculation:
    def test_live_update_on_enter_shows_the_result_without_reload(self, page):
        page.evaluate("window.__marker = 1")          # gone after a reload
        page.click("#code-textarea")
        page.keyboard.type("a = 6*7")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('.output-container').textContent.includes('42')")
        assert page.evaluate("window.__marker") == 1

    def test_calculate_button_posts_the_form_and_reloads(self, page):
        page.fill("#code-textarea", "b = 2^10")
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click('button[data-icon="calc"]')
        page.wait_for_function("document.querySelector('.output-container').textContent.includes('1024')")

    def test_icons_come_from_the_relative_path(self, page):
        assert page.evaluate("document.querySelectorAll('[data-icon] svg').length") > 10


class TestLatexExport:
    def test_export_without_pictures_is_a_tex_file(self, page):
        page.fill("#code-textarea", "c = 3")
        with page.expect_download() as info:
            page.click('button[data-icon="export"]')
        download = info.value
        assert download.suggested_filename == "engipad_export.tex"
        assert b"\\documentclass" in open(download.path(), "rb").read()
        assert page.input_value("#export-images-field") == ""

    def test_export_carries_the_picture_data(self, page, tmp_path):
        page.set_input_files("#image-input", tiny_png(tmp_path))
        page.wait_for_function("document.getElementById('code-textarea').value.includes('picture(')")
        with page.expect_download() as info:
            page.click('button[data-icon="export"]')
        download = info.value
        assert download.suggested_filename == "engipad_export.zip"
        names = zipfile.ZipFile(io.BytesIO(open(download.path(), "rb").read())).namelist()
        assert "img_small.png" in names
        # emptied again right after the export (a calculation carries names only)
        page.wait_for_function("document.getElementById('export-images-field').value === ''")

    def test_live_update_does_not_send_picture_data(self, page, tmp_path):
        bodies = []
        page.on("request", lambda r: bodies.append(r.post_data or "") if r.method == "POST" else None)
        page.set_input_files("#image-input", tiny_png(tmp_path))
        page.wait_for_function("document.getElementById('code-textarea').value.includes('picture(')")
        page.click("#code-textarea")
        page.keyboard.press("Control+End")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('.output-container img.engipad-picture')")
        for _ in range(50):
            if bodies:
                break
            page.wait_for_timeout(100)
        assert bodies
        for body in bodies:
            assert "image_names=small" in body
            assert "images=&" in body or body.endswith("images=")


class TestQuitButton:
    def test_no_quit_button_on_a_server(self, page):
        assert page.query_selector("#exit-btn") is None

    def test_quit_posts_the_token_and_shows_the_notice(self, browser, servers):
        context, page = _open(browser, servers["local"], live=False)
        seen = []

        def fake_shutdown(route):   # never really stop the test server
            seen.append(route.request.headers.get("x-shutdown-token"))
            route.fulfill(status=200, body="EngiPad stopped.")

        page.route("**/shutdown", fake_shutdown)
        page.click("#exit-btn")
        page.wait_for_function("document.body.textContent.includes('EngiPad has been stopped.')")
        assert seen == [servers["token"]]
        assert not page.errors, page.errors
        context.close()
