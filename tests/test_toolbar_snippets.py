"""Snippet buttons of the toolbar (templates/base.html) and the power
operator (20.11.2026).

insertSnippet(text, n) puts the cursor n characters BEFORE THE END of
the inserted text. Four buttons added on 20.11.2026 had n counted from
the start (cursor in "s|implify()"), eleven older ones put it inside
the name or behind a default value. Every button is checked here.

A student could not type "^" (dead key / keyboard layout gave a
look-alike character): there is now an "xʸ" button, and the lexer reads
"ˆ" (U+02C6) and "＾" (U+FF3E) as "^".
"""
import re
import threading
from pathlib import Path

import pytest

from core.engine import evaluate_code
from tests.conftest import unwrap_unit_markup

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")
SNIPPETS = re.findall(r"insertSnippet\('((?:[^'\\]|\\.)*)',\s*(\d+)\)", TEMPLATE)


def cursor_position(snippet, offset_from_end):
    return len(snippet) - int(offset_from_end)


def test_snippets_found():
    assert len(SNIPPETS) > 50


@pytest.mark.parametrize("snippet, offset", SNIPPETS, ids=[s for s, _ in SNIPPETS])
def test_cursor_lands_in_an_empty_place(snippet, offset):
    pos = cursor_position(snippet, offset)
    before, after = snippet[:pos], snippet[pos:]
    right_after_bracket = before.endswith(("(", "["))
    empty_argument = before.endswith(", ") and after[:1] in (" ", ",", ")")
    assert right_after_bracket or empty_argument, f"{before}|{after}"


def test_power_button_is_the_first_function():
    functions = TEMPLATE.split('<p class="panel-title">Functions</p>', 1)[1]
    first = re.search(r"insertSnippet\('((?:[^'\\]|\\.)*)',\s*(\d+)\)", functions)
    assert first.groups() == ("^()", "1")
    assert ">xʸ</button>" in functions.split("</button>", 1)[0] + "</button>"


def last(code):
    block = evaluate_code(code)[-1]
    return unwrap_unit_markup(block[-1].get("content", block[-1]["type"]))


@pytest.mark.parametrize("code, expected", [
    ("a = 2ˆ2\n", "a = 2^{2} = 4"),
    ("a = 2＾2\n", "a = 2^{2} = 4"),
    ("g = 9.81'm/sˆ2\n", r"g = 9.81\,\frac{\mathrm{m}}{\mathrm{s}^{2}}"),
])
def test_caret_lookalikes_are_the_power_operator(code, expected):
    assert last(code) == expected


# --- the button in a real browser ---------------------------------------
playwright_sync = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page():
    from werkzeug.serving import make_server

    from app import create_app

    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1900, "height": 900})
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
        page.wait_for_selector('button[data-icon="calc"] svg')
        yield page
        browser.close()
    server.shutdown()


def test_power_button_in_the_browser(page):
    textarea = page.locator("#code-textarea")
    textarea.fill("A = r")
    textarea.press("End")
    page.locator('button[data-icon="math_func"]').click()
    page.locator("button", has_text="xʸ").click()
    page.keyboard.type("2")
    assert textarea.input_value() == "A = r^(2)"


def test_simplify_button_in_the_browser(page):
    textarea = page.locator("#code-textarea")
    textarea.fill("g_s = ")
    textarea.press("End")
    page.locator('button[data-icon="solve_equ"]').click()
    page.locator("button", has_text="simplify").click()
    page.keyboard.type("g")
    assert textarea.input_value() == "g_s = simplify(g)"
