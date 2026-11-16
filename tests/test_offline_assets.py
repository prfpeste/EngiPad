"""MathJax and the web fonts are served from static/vendor: the page
must not load anything from another host (privacy: no request to a font
service or CDN; and the program has to work without internet, e.g. in
an exam VM)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//|url\(\s*["']?(https?:)?//""", re.I)


@pytest.fixture
def client():
    from app import app
    return app.test_client()


def test_page_loads_nothing_from_another_host(client):
    html = client.get("/").get_data(as_text=True)
    assert not _URL_RE.search(html)
    assert "googleapis" not in html and "jsdelivr" not in html


@pytest.mark.parametrize("name", ["main.css", "vendor/fonts/fonts.css"])
def test_stylesheets_load_nothing_from_another_host(name):
    css = (ROOT / "static" / name).read_text(encoding="utf-8")
    assert not _URL_RE.search(css)
    assert "@import" not in css


def test_mathjax_files_are_present():
    assert (VENDOR / "mathjax" / "tex-chtml.js").stat().st_size > 500_000
    for extension in ("html", "textmacros", "unicode"):
        assert (VENDOR / "mathjax" / "input" / "tex" / "extensions" / f"{extension}.js").is_file()
    fonts = list((VENDOR / "mathjax" / "output" / "chtml" / "fonts" / "woff-v2").glob("*.woff"))
    assert len(fonts) >= 20
    assert (VENDOR / "mathjax" / "LICENSE").is_file()


def test_every_font_file_of_the_stylesheet_exists():
    css = (VENDOR / "fonts" / "fonts.css").read_text(encoding="utf-8")
    names = re.findall(r"url\(([^)]+)\)", css)
    assert len(names) == 15
    for name in names:
        assert (VENDOR / "fonts" / name).is_file(), name
    for family in ("'Inter'", "'JetBrains Mono'"):
        assert family in css
    assert (VENDOR / "fonts" / "LICENSE-Inter.txt").is_file()
    assert (VENDOR / "fonts" / "LICENSE-JetBrainsMono.txt").is_file()


def test_vendor_files_are_served(client):
    for path in (
        "/static/vendor/mathjax/tex-chtml.js",
        "/static/vendor/mathjax/input/tex/extensions/html.js",
        "/static/vendor/fonts/fonts.css",
        "/static/vendor/fonts/inter-latin-400-normal.woff2",
    ):
        assert client.get(path).status_code == 200, path
