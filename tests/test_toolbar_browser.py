"""Browser tests of the input toolbar (templates/base.html, static/editor.js),
shared by all versions: the "More" menu for buttons that do not fit, the
"New calculation" button and the start with an empty sheet.

Run against the Flask app (the server shell); skipped without
playwright/Chromium, like tests/test_editor_browser.py.
"""
import threading

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from tests.test_editor_browser import tiny_png  # noqa: E402

COLLAPSIBLE = ["greek", "math_func", "solve_equ", "matrix", "plot", "image_plus", "settings"]


@pytest.fixture(scope="module")
def base_url():
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        yield instance
        instance.close()


def _open(browser, url, width=1800, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.errors = []
    page.dialogs = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector('button[data-icon="calc"] svg')
    return context, page


@pytest.fixture
def make_page(browser, base_url):
    opened = []

    def make(width=1800, height=900):
        context, page = _open(browser, base_url, width, height)
        opened.append((context, page))
        return page
    yield make
    for context, page in opened:
        assert not page.errors, page.errors
        context.close()


def collapsed(page):
    """data-icon of the buttons that are in the "More" menu now."""
    return page.evaluate("""() => [...document.querySelectorAll('.toolbar-more-panel > [data-collapsible]')]
        .map(e => e.getAttribute('data-icon') || e.querySelector('[data-icon]').getAttribute('data-icon'))""")


def more_visible(page):
    return page.evaluate("!document.querySelector('[data-toolbar-more]').hidden")


class TestOverflowMenu:
    def test_wide_window_shows_every_button_and_no_menu(self, make_page):
        page = make_page(width=1900)
        assert collapsed(page) == []
        assert not more_visible(page)

    def test_narrow_column_moves_buttons_from_the_right_into_the_menu(self, make_page):
        page = make_page(width=1000)
        moved = collapsed(page)
        assert moved and more_visible(page)
        assert moved == COLLAPSIBLE[len(COLLAPSIBLE) - len(moved):]    # from the right, in order
        # what stays in the toolbar fits into the header
        assert page.evaluate("""() => { const t = document.querySelector('.col-input .toolbar');
            return t.scrollWidth <= t.closest('.col-header').clientWidth; }""")
        # New ... Calculate never move
        for sel in ("#new-calc-btn", "#undo-btn", "#redo-btn", 'button[data-icon="calc"]'):
            assert page.is_visible(sel)

    def test_menu_shows_the_buttons_one_below_the_other(self, make_page):
        page = make_page(width=1000)
        page.click('[data-toolbar-more] > [data-trigger]')
        boxes = page.evaluate("""() => [...document.querySelectorAll('.toolbar-more-panel > [data-collapsible]')]
            .map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top]; })""")
        assert len(boxes) >= 2
        assert all(b[1] > a[1] for a, b in zip(boxes, boxes[1:]))     # below each other
        assert len({round(b[0]) for b in boxes}) == 1                   # in one column

    def test_a_menu_in_the_menu_opens_and_inserts(self, make_page):
        page = make_page(width=760 + 300)   # side by side, narrow input column
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_function("document.querySelectorAll('.toolbar-more-panel > [data-collapsible]').length >= 5")
        page.click('[data-toolbar-more] > [data-trigger]')
        page.click('.toolbar-more-panel [data-icon="greek"]')
        assert page.is_visible("text=Greek letters & symbols")
        assert page.evaluate("document.querySelector('[data-toolbar-more]').hasAttribute('data-open')")
        page.click(".toolbar-more-panel .chip-grid >> text=β")
        assert "β" in page.input_value("#code-textarea")
        assert not page.evaluate("document.querySelector('[data-toolbar-more]').hasAttribute('data-open')")

    def test_settings_in_the_menu_still_work(self, make_page):
        page = make_page(width=1000)
        assert "settings" in collapsed(page)
        page.click('[data-toolbar-more] > [data-trigger]')
        page.click('.toolbar-more-panel [data-icon="settings"]')
        page.fill("#timeout-input", "7")
        assert page.input_value("#timeout-input") == "7"
        assert page.evaluate("document.querySelector('[data-toolbar-more]').hasAttribute('data-open')")

    def test_image_button_in_the_menu_opens_the_file_chooser(self, make_page):
        page = make_page(width=1000)
        assert "image_plus" in collapsed(page)
        page.click('[data-toolbar-more] > [data-trigger]')
        with page.expect_file_chooser():
            page.click('.toolbar-more-panel [data-icon="image_plus"]')

    def test_buttons_come_back_when_there_is_room_again(self, make_page):
        page = make_page(width=1000)
        assert collapsed(page)
        page.set_viewport_size({"width": 1900, "height": 900})
        page.wait_for_function("document.querySelector('[data-toolbar-more]').hidden")
        assert collapsed(page) == []
        order = page.evaluate("""() => [...document.querySelectorAll('.col-input .toolbar > [data-collapsible]')]
            .map(e => e.getAttribute('data-icon') || e.querySelector('[data-icon]').getAttribute('data-icon'))""")
        assert order == COLLAPSIBLE

    def test_no_divider_at_the_end_of_the_visible_buttons(self, make_page):
        page = make_page(width=1000)
        last = page.evaluate("""() => { const kids = [...document.querySelector('.col-input .toolbar').children]
            .filter(e => getComputedStyle(e).display !== 'none' && !e.hidden && e.type !== 'file');
            return kids[kids.length - 2].className; }""")      # the last one is the More button
        assert "tool-divider" not in last


class TestNewCalculation:
    def test_starts_with_an_empty_sheet(self, make_page):
        page = make_page()
        assert page.input_value("#code-textarea") == ""

    def test_reload_brings_the_calculation_back(self, make_page):
        # not embedded in a task (server, desktop, direct call): as always
        page = make_page()
        page.fill("#code-textarea", "old = 1")
        page.reload(wait_until="domcontentloaded")      # the backup is written on unload
        page.wait_for_selector('button[data-icon="calc"] svg')
        assert page.input_value("#code-textarea") == "old = 1"

    def test_new_asks_discards_everything_and_undo_brings_it_back(self, make_page, tmp_path):
        page = make_page()
        book = tmp_path / "book.txt"
        book.write_text("##A\nx = 1\n##B\ny = A::x\n", encoding="utf-8")
        page.set_input_files("#file-input", str(book))
        page.wait_for_selector("#sheet-tabs >> text=B")
        page.click('button[data-icon="calc"]')
        page.wait_for_selector(".output-container .formula-block")
        messages = []
        page.once("dialog", lambda d: (messages.append(d.message), d.accept()))
        page.click("#new-calc-btn")
        assert messages and "new calculation" in messages[0].lower()
        assert page.input_value("#code-textarea") == ""
        assert page.evaluate("document.querySelectorAll('#sheet-tabs .sheet-tab').length") <= 1
        assert page.evaluate("document.querySelector('.output-container').children.length") == 0
        page.click("#undo-btn")
        page.wait_for_selector("#sheet-tabs >> text=B")

    def test_cancel_keeps_the_calculation(self, make_page):
        page = make_page()
        page.fill("#code-textarea", "keep = 1")
        page.once("dialog", lambda d: d.dismiss())
        page.click("#new-calc-btn")
        assert page.input_value("#code-textarea") == "keep = 1"

    def test_empty_sheet_needs_no_question(self, make_page):
        page = make_page()
        asked = []
        page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        page.click("#new-calc-btn")
        assert asked == []

    def test_server_calculate_keeps_the_pictures(self, make_page, tmp_path):
        # Calculate reloads the page in the server version; the pictures come
        # back from the browser's backup although the start is empty now.
        page = make_page()
        page.set_input_files("#image-input", tiny_png(tmp_path))
        page.wait_for_function("document.getElementById('code-textarea').value.includes('picture(')")
        page.wait_for_timeout(1200)       # autosave runs after a pause
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click('button[data-icon="calc"]')
        page.wait_for_function(
            "document.querySelector('.output-container img.engipad-picture')?.src.startsWith('data:image/png')")


def _autosaved(frame):
    frame.evaluate("window.EngiPadEditor.saveAutosave(false)")


class TestOneBackupPerTask:
    """Embedded in a Moodle question, each question of an attempt keeps its
    own backup: "Check" reloads the page and brings the student's
    calculation back, another question or a new attempt starts empty."""

    @pytest.fixture
    def moodle(self, browser, base_url):
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))

        def quiz_page(route):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(route.request.url).query)
            usage, slot = q["attempt"][0], q["slot"][0]
            route.fulfill(content_type="text/html", body=(
                "<!DOCTYPE html><html><body><div class='que'"
                f" id='question-{usage}-{slot}'><div class='content'><p>Question {slot}</p>"
                "<iframe id='f' src='/' style='width:1300px;height:700px;border:none'></iframe>"
                "</div></div></body></html>"))

        page.route("**/mod/quiz/attempt.php*", quiz_page)

        def open_question(usage, slot):
            page.goto(f"{base_url}mod/quiz/attempt.php?attempt={usage}&slot={slot}")
            frame = page.wait_for_selector("#f").content_frame()
            frame.wait_for_selector('button[data-icon="calc"] svg')
            return frame

        yield open_question
        assert not page.errors, page.errors
        context.close()

    def test_check_brings_the_own_calculation_back(self, moodle):
        frame = moodle(77, 3)
        assert frame.input_value("#code-textarea") == ""
        frame.fill("#code-textarea", "s = 55'km/h * 130's | m")
        _autosaved(frame)
        frame = moodle(77, 3)                         # "Check": the page is reloaded
        assert frame.input_value("#code-textarea") == "s = 55'km/h * 130's | m"

    def test_other_question_and_new_attempt_start_empty(self, moodle):
        frame = moodle(77, 3)
        frame.fill("#code-textarea", "a = 1")
        _autosaved(frame)
        assert moodle(77, 4).input_value("#code-textarea") == ""      # next question
        assert moodle(78, 3).input_value("#code-textarea") == ""      # new attempt
        assert moodle(77, 3).input_value("#code-textarea") == "a = 1"  # back again

    def test_not_mixed_with_the_backup_outside_moodle(self, moodle, base_url):
        frame = moodle(77, 3)
        frame.evaluate("localStorage.setItem('engipad_autosave_code', 'outside = 1')")
        frame = moodle(77, 5)
        assert frame.input_value("#code-textarea") == ""

    def test_only_the_last_20_tasks_are_kept(self, moodle):
        frame = moodle(1, 1)
        frame.evaluate("""() => {
            const index = {};
            for (let i = 0; i < 20; i++) {
                const key = 'engipad_autosave_code::moodle:question-old-' + i;
                localStorage.setItem(key, 'x = ' + i);
                index[key] = 1000 + i;            // older than now
            }
            localStorage.setItem('engipad_autosave_tasks', JSON.stringify(index));
        }""")
        frame.fill("#code-textarea", "new = 1")
        _autosaved(frame)
        index = frame.evaluate("JSON.parse(localStorage.getItem('engipad_autosave_tasks'))")
        assert len(index) == 20
        assert "engipad_autosave_code::moodle:question-1-1" in index
        assert frame.evaluate("localStorage.getItem('engipad_autosave_code::moodle:question-old-0')") is None
        assert frame.evaluate("localStorage.getItem('engipad_autosave_code::moodle:question-old-19')") == "x = 19"

    def test_task_parameter_in_the_address(self, make_page, base_url):
        page = make_page()
        page.goto(base_url + "?task=a")
        page.wait_for_selector('button[data-icon="calc"] svg')
        page.fill("#code-textarea", "in_a = 1")
        _autosaved(page)
        page.goto(base_url + "?task=b")
        page.wait_for_selector('button[data-icon="calc"] svg')
        assert page.input_value("#code-textarea") == ""
        page.goto(base_url + "?task=a")
        page.wait_for_selector('button[data-icon="calc"] svg')
        assert page.input_value("#code-textarea") == "in_a = 1"
