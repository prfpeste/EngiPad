"""Browser tests of the editor (undo/redo history), with a real Chromium.

They need the optional packages `playwright` and a Chromium for it
(`pip install playwright && playwright install chromium`); without them
the whole module is skipped. MathJax and web fonts come from the internet
and are blocked here -- the tests only look at the editor, not at the
typeset output.
"""

import struct
import threading
import zlib

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402


@pytest.fixture(scope="module")
def base_url():
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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


@pytest.fixture
def page(browser, base_url):
    context = browser.new_context(viewport={"width": 1300, "height": 800})
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    page.route("**/cdn.jsdelivr.net/**", lambda r: r.abort())
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base_url, wait_until="domcontentloaded")
    page.evaluate("localStorage.clear()")
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#undo-btn svg")
    page.fill("#code-textarea", "")          # start with an empty editor
    page.evaluate("resetHistory()")
    page.errors = errors
    yield page
    assert not errors, errors
    context.close()


PAUSE_MS = 900  # a bit more than HISTORY_PAUSE_MS in editor.js


def text(page):
    return page.input_value("#code-textarea")


def type_text(page, value):
    page.click("#code-textarea")
    page.keyboard.press("Control+End")
    page.keyboard.type(value)


def undo_enabled(page):
    return not page.is_disabled("#undo-btn")


def redo_enabled(page):
    return not page.is_disabled("#redo-btn")


def tiny_png(tmp_path):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes((200, 30, 30)) * 20 for _ in range(10))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 20, 10, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path = tmp_path / "small.png"
    path.write_bytes(data)
    return str(path)


class TestToolbar:
    def test_buttons_sit_between_save_and_greek_in_their_own_group(self, page):
        order = page.evaluate("""() => [...document.querySelectorAll('.col-input .toolbar > *')]
            .map(e => e.id || e.getAttribute('data-icon') || e.className
                      || e.querySelector('[data-icon]')?.getAttribute('data-icon'))""")
        start = order.index("save")
        assert order[start:start + 5] == ["save", "tool-divider", "undo-btn", "redo-btn",
                                          "tool-divider"]
        assert page.evaluate("""() => {
            const d = document.querySelector('#redo-btn').nextElementSibling.nextElementSibling;
            return !!d.querySelector('[data-icon=\\"greek\\"]'); }""")

    def test_icons_are_loaded(self, page):
        assert page.query_selector("#undo-btn svg") and page.query_selector("#redo-btn svg")

    def test_both_disabled_at_the_start(self, page):
        assert not undo_enabled(page) and not redo_enabled(page)

    def test_tooltips_name_the_shortcuts(self, page):
        assert "Ctrl+Z" in page.get_attribute("#undo-btn", "title")
        assert "Ctrl+Y" in page.get_attribute("#redo-btn", "title")

    def test_clicking_a_button_keeps_the_focus_in_the_editor(self, page):
        type_text(page, "abc")
        page.click("#undo-btn")
        assert page.evaluate("document.activeElement.id") == "code-textarea"


class TestTyping:
    def test_typing_is_one_step_and_undo_redo_walk_through_it(self, page):
        type_text(page, "abc")
        assert undo_enabled(page)
        page.click("#undo-btn")
        assert text(page) == "" and redo_enabled(page) and not undo_enabled(page)
        page.click("#redo-btn")
        assert text(page) == "abc" and not redo_enabled(page)

    def test_a_pause_ends_the_step(self, page):
        type_text(page, "one")
        page.wait_for_timeout(PAUSE_MS)
        type_text(page, " two")
        page.click("#undo-btn")
        assert text(page) == "one"
        page.click("#undo-btn")
        assert text(page) == ""

    def test_enter_is_its_own_step(self, page):
        type_text(page, "a")
        page.keyboard.press("Enter")
        page.keyboard.type("b")
        page.click("#undo-btn")
        assert text(page) == "a\n"
        page.click("#undo-btn")
        assert text(page) == "a"
        page.click("#undo-btn")
        assert text(page) == ""

    def test_changing_from_typing_to_deleting_ends_the_step(self, page):
        type_text(page, "abc")
        page.keyboard.press("Backspace")
        assert text(page) == "ab"
        page.click("#undo-btn")
        assert text(page) == "abc"

    def test_paste_is_its_own_step(self, page):
        type_text(page, "x")
        page.evaluate("""() => { const t = document.getElementById('code-textarea');
            t.focus(); t.setSelectionRange(1, 1);
            const dt = new DataTransfer(); dt.setData('text/plain', 'PASTED');
            t.dispatchEvent(new InputEvent('beforeinput', {inputType: 'insertFromPaste', data: 'PASTED', dataTransfer: dt, cancelable: true, bubbles: true}));
            t.setRangeText('PASTED', 1, 1, 'end');
            t.dispatchEvent(new InputEvent('input', {inputType: 'insertFromPaste', bubbles: true}));
        }""")
        assert text(page) == "xPASTED"
        page.click("#undo-btn")
        assert text(page) == "x"

    def test_new_typing_after_undo_ends_the_redo_branch(self, page):
        type_text(page, "abc")
        page.click("#undo-btn")
        type_text(page, "z")
        page.wait_for_timeout(PAUSE_MS)
        assert not redo_enabled(page)

    def test_cursor_is_restored(self, page):
        type_text(page, "hello")
        page.wait_for_timeout(PAUSE_MS)
        page.keyboard.press("Home")
        page.keyboard.type("XY")
        page.click("#undo-btn")
        assert text(page) == "hello"
        assert page.evaluate("document.getElementById('code-textarea').selectionStart") == 5

    def test_undo_updates_the_autosave(self, page):
        type_text(page, "abc")
        page.wait_for_timeout(PAUSE_MS)
        type_text(page, "def")
        page.click("#undo-btn")
        page.wait_for_timeout(1300)
        assert page.evaluate("localStorage.getItem('engipad_autosave_code')") == "abc"


class TestKeyboard:
    def test_ctrl_z_ctrl_y_and_ctrl_shift_z(self, page):
        type_text(page, "abc")
        page.keyboard.press("Control+z")
        assert text(page) == ""
        page.keyboard.press("Control+y")
        assert text(page) == "abc"
        page.keyboard.press("Control+z")
        page.keyboard.press("Control+Shift+z")
        assert text(page) == "abc"

    def test_browser_undo_events_are_taken_over(self, page):
        type_text(page, "abc")
        page.evaluate("""() => document.getElementById('code-textarea').dispatchEvent(
            new InputEvent('beforeinput', {inputType: 'historyUndo', cancelable: true, bubbles: true}))""")
        assert text(page) == ""
        page.evaluate("""() => document.getElementById('code-textarea').dispatchEvent(
            new InputEvent('beforeinput', {inputType: 'historyRedo', cancelable: true, bubbles: true}))""")
        assert text(page) == "abc"


class TestSnippetsAndPictures:
    def test_a_snippet_is_one_step_apart_from_the_typing_before_it(self, page):
        type_text(page, "x = ")
        page.evaluate("insertSnippet('sqrt()', 1)")
        assert text(page) == "x = sqrt()"
        page.click("#undo-btn")
        assert text(page) == "x = "
        page.click("#undo-btn")
        assert text(page) == ""
        page.click("#redo-btn")
        page.click("#redo-btn")
        assert text(page) == "x = sqrt()"

    def test_picture_line_can_be_undone_and_redone_and_the_picture_stays(self, page, tmp_path):
        page.set_input_files("#image-input", tiny_png(tmp_path))
        page.wait_for_function("document.getElementById('code-textarea').value.includes('picture(')")
        assert 'picture("small", width=20, align=center)' in text(page)
        page.click("#undo-btn")
        assert "picture(" not in text(page)
        assert page.evaluate("imageStore.has('small')")
        page.click("#redo-btn")
        assert 'picture("small"' in text(page)


class TestSheets:
    def _two_sheets(self, page):
        type_text(page, "a = 5")
        page.evaluate("addSheet()")
        type_text(page, "b = S1::a + 1")
        page.wait_for_timeout(PAUSE_MS)

    def _names(self, page):
        return page.evaluate("sheets.map(s => s.name)")

    def test_adding_a_sheet_can_be_undone_back_to_a_single_sheet(self, page):
        type_text(page, "a = 5")
        page.evaluate("addSheet()")
        assert self._names(page) == ["S1", "S2"]
        page.click("#undo-btn")
        assert self._names(page) == [None] and text(page) == "a = 5"
        assert page.evaluate("document.querySelectorAll('.sheet-tab').length") <= 1
        page.click("#redo-btn")
        assert self._names(page) == ["S1", "S2"]

    def test_rename_is_undone_including_the_references_in_other_sheets(self, page):
        self._two_sheets(page)
        page.evaluate("renameSheet(0, 'Loads')")
        assert self._names(page) == ["Loads", "S2"]
        assert page.evaluate("sheets[1].code") == "b = Loads::a + 1"
        page.click("#undo-btn")
        assert self._names(page) == ["S1", "S2"]
        assert page.evaluate("sheets[1].code") == "b = S1::a + 1"
        page.click("#redo-btn")
        assert page.evaluate("sheets[1].code") == "b = Loads::a + 1"

    def test_deleted_sheet_comes_back(self, page):
        self._two_sheets(page)
        page.evaluate("deleteSheet(1)")
        assert self._names(page) == [None]
        page.click("#undo-btn")
        assert self._names(page) == ["S1", "S2"]
        assert page.evaluate("sheets[1].code") == "b = S1::a + 1"
        assert page.evaluate("activeSheet") == 1

    def test_switching_sheets_is_no_step_but_undo_shows_the_edited_sheet(self, page):
        self._two_sheets(page)
        page.evaluate("switchToSheet(0)")
        assert page.evaluate("activeSheet") == 0
        page.click("#undo-btn")        # undoes the typing in S2 ...
        assert page.evaluate("activeSheet") == 1    # ... and shows S2
        assert text(page) == ""
        assert page.evaluate("sheets[0].code") == "a = 5"

    def test_typing_in_two_sheets_is_undone_sheet_by_sheet(self, page):
        self._two_sheets(page)
        page.evaluate("switchToSheet(0)")
        type_text(page, " + 1")
        page.wait_for_timeout(PAUSE_MS)
        page.click("#undo-btn")
        assert page.evaluate("[activeSheet, sheets[0].code, sheets[1].code]") == [
            0, "a = 5", "b = S1::a + 1"]


class TestOpenAndLimits:
    def test_opening_a_file_starts_a_new_history(self, page, tmp_path):
        type_text(page, "old")
        page.wait_for_timeout(PAUSE_MS)
        path = tmp_path / "f.txt"
        path.write_text("x = 1\ny = 2", encoding="utf-8")
        page.set_input_files("#file-input", str(path))
        page.wait_for_function("document.getElementById('code-textarea').value.startsWith('x = 1')")
        assert not undo_enabled(page) and not redo_enabled(page)
        type_text(page, "!")
        page.click("#undo-btn")
        assert text(page) == "x = 1\ny = 2"

    def test_history_is_limited(self, page):
        page.evaluate("""() => { for (let i = 0; i < 260; i++) {
            document.getElementById('code-textarea').value = 'v' + i; commitHistory(); } }""")
        assert page.evaluate("historySteps.length") == 200
        assert page.evaluate("historyIndex") == 199

    def test_identical_state_adds_no_step(self, page):
        type_text(page, "abc")
        page.wait_for_timeout(PAUSE_MS)
        before = page.evaluate("historySteps.length")
        page.evaluate("commitHistory(); commitHistory()")
        assert page.evaluate("historySteps.length") == before

    def test_history_is_not_part_of_the_saved_text(self, page):
        type_text(page, "abc")
        page.wait_for_timeout(PAUSE_MS)
        assert page.evaluate("getFileText()") == "abc"


class TestSurvivesCalculate:
    def _calculate(self, page):
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.click('button[data-icon="calc"]')
        page.wait_for_selector("#undo-btn svg")

    def test_history_survives_the_page_reload_of_calculate(self, page):
        type_text(page, "a = 1")
        page.wait_for_timeout(PAUSE_MS)
        type_text(page, "\nb = 2")
        page.wait_for_timeout(PAUSE_MS)
        self._calculate(page)
        assert text(page).startswith("a = 1") and undo_enabled(page)
        page.click("#undo-btn")
        assert text(page) == "a = 1\n"
        page.click("#undo-btn")
        assert text(page) == "a = 1"
        page.click("#undo-btn")
        assert text(page) == ""
        for _ in range(3):
            page.click("#redo-btn")
        assert text(page) == "a = 1\nb = 2"

    def test_redo_survives_too(self, page):
        type_text(page, "a = 1")
        page.wait_for_timeout(PAUSE_MS)
        page.click("#undo-btn")
        self._calculate(page)
        assert redo_enabled(page)
        page.click("#redo-btn")
        assert text(page) == "a = 1"

    def test_a_stale_history_is_dropped(self, page):
        type_text(page, "a = 1")
        page.wait_for_timeout(PAUSE_MS)
        page.evaluate("""() => { resetHistory();
            sessionStorage.setItem('engipad_history', JSON.stringify({steps: [
              {sheets: [{name: null, code: 'zzz'}], active: 0, selStart: 0, selEnd: 0}], index: 0}));
            restoreHistoryAfterReload(); }""")
        assert not undo_enabled(page)
        assert page.evaluate("sessionStorage.getItem('engipad_history')") is None
