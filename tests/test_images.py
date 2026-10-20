"""Pictures: picture("name") lines, the image section of a saved file,
the PNG checks, the LaTeX export and the web routes.

The browser side (static/pictures.js) implements the same rules as
core/images.py; TestPythonAndJavascriptAgree runs both with node on the
same inputs (skipped if node is missing).
"""

import base64
import json
import re
import shutil
import struct
import subprocess
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

import pytest

from app import create_app
from core import images as im
from core.engine import evaluate_code
from core.latex_export import build_latex_document
from core.workbook import evaluate_workbook, parse_workbook

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def make_png(width=10, height=10, rgb=(200, 30, 30)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def png_claiming(width, height) -> bytes:
    """A tiny file whose header CLAIMS a huge size (decompression bomb)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00"))
        + _chunk(b"IEND", b"")
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def contents(results):
    return [item.get("content", "") for block in results for item in block]


def items(results, kind):
    return [item for block in results for item in block if item.get("type") == kind]


@pytest.fixture
def client():
    return create_app().test_client()


# ---------------------------------------------------------------------
# PNG check
# ---------------------------------------------------------------------
class TestPngCheck:
    def test_valid_png_gives_size(self):
        assert im.png_size(make_png(30, 20)) == (30, 20)

    def test_jpeg_is_refused(self):
        with pytest.raises(ValueError, match="not a PNG"):
            im.png_size(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    def test_svg_is_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>' + b" " * 50
        with pytest.raises(ValueError, match="not a PNG"):
            im.png_size(svg)

    def test_text_with_png_name_is_refused(self):
        with pytest.raises(ValueError):
            im.png_size(b"hello" * 20)

    def test_truncated_png_is_refused(self):
        with pytest.raises(ValueError, match="incomplete"):
            im.png_size(make_png()[:-12])

    def test_decompression_bomb_header_is_refused_without_decoding(self):
        with pytest.raises(ValueError, match="too many pixels"):
            im.png_size(png_claiming(60000, 60000))

    def test_wider_than_limit_is_refused(self):
        with pytest.raises(ValueError, match="too many pixels"):
            im.png_size(png_claiming(im.MAX_PIXEL_WIDTH + 1, 10))

    def test_pixel_count_limit(self):
        with pytest.raises(ValueError, match="too many pixels"):
            im.png_size(png_claiming(1200, 2600))  # 3.12 million

    def test_zero_size_is_refused(self):
        with pytest.raises(ValueError):
            im.png_size(png_claiming(0, 10))

    def test_too_large_file_is_refused(self):
        with pytest.raises(ValueError, match="too large"):
            im.png_size(make_png(), max_bytes=20)

    def test_base64_must_be_base64(self):
        with pytest.raises(ValueError, match="Base64"):
            im.decode_png_base64("not base64 !!")

    def test_base64_roundtrip(self):
        data = make_png(5, 5)
        assert im.decode_png_base64(b64(data)) == data

    def test_huge_text_is_refused_before_decoding(self):
        with pytest.raises(ValueError, match="too large"):
            im.decode_png_base64("A" * (10 * 1024 * 1024))


# ---------------------------------------------------------------------
# Names, settings
# ---------------------------------------------------------------------
class TestNamesAndSettings:
    @pytest.mark.parametrize("name", ["balken", "Skizze_1", "_x", "a" * 40, "7"])
    def test_valid_names(self, name):
        assert im.is_valid_name(name)

    @pytest.mark.parametrize(
        "name",
        ["", "a b", "../x", "a/b", "a\\b", "a.png", 'a"b', "a}\\input{x", "ä", "a" * 41, "a;b", None, 5],
    )
    def test_invalid_names(self, name):
        assert not im.is_valid_name(name)

    @pytest.mark.parametrize("raw,expected", [("1", 1), ("2", 2), ("3", 3), (" 2 ", 2),
                                              ("4", 1), ("0", 1), ("", 1), ("x", 1), (None, 1)])
    def test_image_limit_setting(self, raw, expected):
        assert im.parse_limit_mb(raw) == expected

    def test_per_image_limit_is_a_fifth(self):
        assert im.per_image_limit(im.limit_bytes(1)) == 1024 * 1024 // 5


# ---------------------------------------------------------------------
# picture(...) syntax
# ---------------------------------------------------------------------
class TestPictureSyntax:
    def test_name_only(self):
        assert im.parse_picture_call('picture("balken")') == {
            "name": "balken", "width": None, "align": "center"}

    def test_width_and_align(self):
        spec = im.parse_picture_call('picture("b", width=300, align=left)')
        assert spec == {"name": "b", "width": 300, "align": "left"}

    def test_any_order_and_spaces(self):
        spec = im.parse_picture_call('picture( "b" ,align = right ,width = 120 )')
        assert spec == {"name": "b", "width": 120, "align": "right"}

    def test_px_suffix(self):
        assert im.parse_picture_call('picture("b", width=300px)')["width"] == 300

    def test_width_up_to_1200(self):
        assert im.parse_picture_call('picture("b", width=1200)')["width"] == 1200

    @pytest.mark.parametrize("line", [
        'picture("b", width=0)', 'picture("b", width=1201)', 'picture("b", width=50%)',
        'picture("b", width=5mm)', 'picture("b", width=-3)', 'picture("b", width=2.5)',
        'picture("b", width=)', 'picture("b", align=top)', 'picture("b", align=)',
        'picture("b", height=10)', 'picture("b", width=10, width=20)',
        'picture("b", 300)', 'picture(b)', 'picture()', 'picture("")',
        'picture("../etc/passwd")', 'picture("a b")', 'picture("a", )',
    ])
    def test_bad_calls_are_errors(self, line):
        with pytest.raises(ValueError):
            im.parse_picture_call(line)


# ---------------------------------------------------------------------
# In the evaluation
# ---------------------------------------------------------------------
class TestPictureInResults:
    def test_picture_line_gives_image_item(self):
        results = evaluate_code('picture("balken", width=300, align=right)')
        assert items(results, "image") == [
            {"type": "image", "name": "balken", "width": 300, "align": "right"}]

    def test_not_loaded_image_is_an_error_line(self):
        results = evaluate_code('picture("balken")', image_names=frozenset())
        assert not items(results, "image")
        text = " ".join(contents(results))
        assert "not loaded" in text and "line 1" in text

    def test_loaded_image_passes(self):
        results = evaluate_code('picture("balken")', image_names=frozenset({"balken"}))
        assert len(items(results, "image")) == 1

    def test_without_name_list_nothing_is_checked(self):
        assert len(items(evaluate_code('picture("x")'), "image")) == 1

    def test_error_in_syntax_is_an_error_line(self):
        results = evaluate_code('picture("b", width=5000)')
        assert "width" in " ".join(contents(results))

    def test_with_other_items_in_one_line(self):
        results = evaluate_code('a = 1 ; picture("b") ; c = 2', image_names=frozenset({"b"}))
        block = results[0]
        assert [i["type"] for i in block] == ["latex", "image", "latex"]

    def test_between_calculations_keeps_variables(self):
        results = evaluate_code('x = 2\npicture("b")\ny = x * 3\n', image_names=frozenset({"b"}))
        assert any("6" in c for c in contents(results))

    def test_commented_picture_line_is_ignored(self):
        assert not items(evaluate_code('# picture("b")'), "image")

    def test_workbook_checks_names_too(self):
        out = evaluate_workbook('##A\npicture("b")\n##B\nx = 1\n', image_names=frozenset())
        assert "not loaded" in " ".join(contents(out["results"]))


# ---------------------------------------------------------------------
# The image section of a file
# ---------------------------------------------------------------------
class TestImageSection:
    def test_without_section_text_is_unchanged(self):
        text = "a = 1\n##S2\nb = 2\n"
        assert im.split_image_section(text) == (text, {}, [])

    def test_roundtrip_single_sheet(self):
        data = make_png()
        text = im.join_image_section('picture("p")\nx = 1', {"p": data})
        code, images, warnings = im.split_image_section(text)
        assert code == 'picture("p")\nx = 1'
        assert images == {"p": data} and warnings == []

    @pytest.mark.parametrize("code", ["", "x = 1", "x = 1\n", "x = 1\n\n", "##A\na = 1\n##B\nb = 2"])
    def test_roundtrip_is_exact_for_any_code_ending(self, code):
        text = im.join_image_section(code, {"p": make_png()})
        assert im.split_image_section(text)[0] == code

    def test_repeated_save_and_open_does_not_grow(self):
        data = make_png()
        code = "x = 1\n"
        for _ in range(4):
            text = im.join_image_section(code, {"p": data})
            code, images, _w = im.split_image_section(text)
        assert code == "x = 1\n" and images == {"p": data}

    def test_two_images_keep_order(self):
        a, b = make_png(4, 4), make_png(6, 6)
        text = im.join_image_section("x = 1", {"zz": a, "aa": b})
        _code, images, _w = im.split_image_section(text)
        assert list(images) == ["zz", "aa"]

    def test_base64_lines_are_76_wide(self):
        text = im.join_image_section("x", {"p": make_png(60, 60)})
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith("##") and ln != "x"]
        assert lines and all(len(ln) <= 76 for ln in lines)

    def test_crlf_file(self):
        text = im.join_image_section("x = 1", {"p": make_png()}).replace("\n", "\r\n")
        code, images, _w = im.split_image_section(text)
        assert code == "x = 1" and list(images) == ["p"]

    def test_marker_typed_in_the_middle_is_not_an_image(self):
        text = "a = 1\n##!image foo\nb = 2\n"
        assert im.split_image_section(text) == (text, {}, [])

    def test_typed_marker_before_real_section_does_not_swallow_code(self):
        data = make_png()
        text = im.join_image_section("a = 1\n##!image foo\nb = 2", {"p": data})
        code, images, _w = im.split_image_section(text)
        assert code == "a = 1\n##!image foo\nb = 2" and list(images) == ["p"]

    def test_code_line_that_looks_like_base64_stays_code(self):
        text = im.join_image_section("a = 1\nx", {"p": make_png()})
        assert im.split_image_section(text)[0] == "a = 1\nx"

    def test_junk_before_a_block_stays_code_and_the_block_still_loads(self):
        good = make_png()
        text = "x = 1\n##!image bad\n!!!!\n##!image good\n" + b64(good) + "\n"
        # "!!!!" ends the valid tail: everything before it is just code
        code, images, warnings = im.split_image_section(text)
        assert code == "x = 1\n##!image bad\n!!!!"
        assert images == {"good": good} and warnings == []

    def test_block_with_wrong_content_is_skipped_but_file_opens(self):
        good = make_png()
        text = ("x = 1\n##!image notpng\n" + b64(b"hello world" * 10)
                + "\n##!image good\n" + b64(good) + "\n")
        code, images, warnings = im.split_image_section(text)
        assert code == "x = 1" and list(images) == ["good"]
        assert len(warnings) == 1 and "notpng" in warnings[0]

    def test_empty_block_is_skipped(self):
        text = "x = 1\n##!image e\n##!image good\n" + b64(make_png()) + "\n"
        code, images, warnings = im.split_image_section(text)
        assert list(images) == ["good"] and "e" in warnings[0]

    def test_bomb_in_file_is_skipped(self):
        text = "x\n##!image bomb\n" + b64(png_claiming(60000, 60000)) + "\n"
        _code, images, warnings = im.split_image_section(text)
        assert images == {} and "too many pixels" in warnings[0]

    def test_duplicate_name_second_is_skipped(self):
        data = make_png()
        text = "x\n##!image p\n" + b64(data) + "\n##!image p\n" + b64(make_png(3, 3)) + "\n"
        _code, images, warnings = im.split_image_section(text)
        assert images == {"p": data} and "twice" in warnings[0]

    def test_eleventh_image_is_skipped(self):
        data = make_png()
        text = "x\n" + "".join(f"##!image i{n}\n{b64(data)}\n" for n in range(11))
        _code, images, warnings = im.split_image_section(text)
        assert len(images) == 10 and "more than 10" in warnings[0]

    def test_total_size_limit(self, monkeypatch):
        data = make_png(40, 40)
        monkeypatch.setattr(im, "HARD_TOTAL_BYTES", len(data) * 2 + 1)
        monkeypatch.setattr(im, "PER_IMAGE_DIVISOR", 1)
        text = "x\n" + "".join(f"##!image i{n}\n{b64(data)}\n" for n in range(3))
        _code, images, warnings = im.split_image_section(text)
        assert len(images) == 2 and "altogether" in warnings[0]

    def test_workbook_parser_ignores_the_section(self):
        text = im.join_image_section("##A\npicture(\"p\")\n##B\nx = 1", {"p": make_png()})
        sheets = parse_workbook(text)
        assert [s.name for s in sheets] == ["A", "B"]
        assert sheets[1].code == "x = 1"

    def test_single_sheet_with_section_is_still_one_unnamed_sheet(self):
        text = im.join_image_section('x = 1\npicture("p")', {"p": make_png()})
        sheets = parse_workbook(text)
        assert len(sheets) == 1 and sheets[0].name is None
        assert "##!image" not in sheets[0].code

    def test_old_files_without_images_behave_as_before(self):
        text = "##S1\na = 1\n##S2\nb = S1::a + 1\n"
        assert [s.name for s in parse_workbook(text)] == ["S1", "S2"]

    def test_referenced_names(self):
        codes = ['picture("a")\nx = 1', '# picture("b", width=100)\npicture( "a" )', 'foo("c")']
        assert im.referenced_names(codes) == ["a", "b"]


# ---------------------------------------------------------------------
# LaTeX export
# ---------------------------------------------------------------------
class TestLatexExport:
    def _build(self, code, pictures, **kwargs):
        results = evaluate_code(code, image_names=frozenset(pictures))
        return build_latex_document(results, pictures=pictures, **kwargs)

    def test_picture_becomes_includegraphics_file(self):
        tex, files = self._build('picture("balken")', {"balken": make_png(300, 100)})
        assert "\\engipicture{79.4mm}{img_balken.png}" in tex
        assert [name for name, _ in files] == ["img_balken.png"]
        assert files[0][1] == make_png(300, 100)

    def test_default_width_is_at_most_500_px(self):
        tex, _ = self._build('picture("b")', {"b": make_png(1000, 100)})
        assert "132.3mm" in tex  # 500 px at 96 dpi

    def test_small_image_keeps_its_size(self):
        tex, _ = self._build('picture("b")', {"b": make_png(96, 50)})
        assert "25.4mm" in tex

    def test_explicit_width_in_pixels(self):
        tex, _ = self._build('picture("b", width=192)', {"b": make_png(1000, 10)})
        assert "50.8mm" in tex

    @pytest.mark.parametrize("align,env", [("left", "flushleft"), ("center", "center"),
                                           ("right", "flushright")])
    def test_alignment(self, align, env):
        tex, _ = self._build(f'picture("b", align={align})', {"b": make_png()})
        assert f"\\begin{{{env}}}\n\\engipicture" in tex and f"\\end{{{env}}}" in tex

    def test_same_picture_twice_is_one_file(self):
        tex, files = self._build('picture("b")\npicture("b", width=100)', {"b": make_png()})
        assert len(files) == 1
        assert tex.count("{img_b.png}") == 2

    def test_definition_only_when_pictures_are_used(self):
        tex, files = build_latex_document(evaluate_code("a = 1"))
        assert "engipicture" not in tex and files == []
        tex, _ = self._build('picture("b")', {"b": make_png()})
        assert "\\newcommand{\\engipicture}" in tex

    def test_missing_picture_is_an_error_not_a_crash(self):
        results = evaluate_code('picture("zz")')  # engine did not check the names
        tex, files = build_latex_document(results, pictures={})
        assert "Picture error" in tex and files == []

    def test_all_sheets_export(self):
        out = evaluate_workbook('##A\npicture("p")\n##B\npicture("p")\n',
                                all_sheets=True, image_names=frozenset({"p"}))
        tex, files = build_latex_document(None, sheets=out["all"], pictures={"p": make_png()})
        assert tex.count("{img_p.png}") == 2 and len(files) == 1

    @pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
    def test_document_compiles_with_pdflatex(self, tmp_path):
        pictures = {"wide": make_png(1200, 200), "small": make_png(40, 40, (10, 90, 200))}
        code = ('"Test"\npicture("wide")\npicture("small", align=left)\n'
                'picture("small", width=96, align=right)\nx = 2 + 3\n')
        tex, files = self._build(code, pictures)
        (tmp_path / "doc.tex").write_text(tex, encoding="utf-8")
        for name, data in files:
            (tmp_path / name).write_bytes(data)
        run = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
            cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert run.returncode == 0, run.stdout[-1500:]
        assert (tmp_path / "doc.pdf").stat().st_size > 1000


# ---------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------
def _post(client, code, **extra):
    data = {"code": code, "precision": "0.01"}
    data.update(extra)
    return client.post("/", data=data)


class TestRoutes:
    def test_page_has_button_setting_and_config(self, client):
        html = client.get("/").get_data(as_text=True)
        assert 'id="image-btn"' in html and 'data-icon="image_plus"' in html
        assert 'name="image_limit"' in html and "window.ENGIPAD_IMAGES" in html
        assert 'src="/static/pictures.js' in html

    def test_button_sits_between_plot_and_settings(self, client):
        html = client.get("/").get_data(as_text=True)
        assert (html.index('data-icon="plot"') < html.index('id="image-btn"')
                < html.index('data-icon="settings"'))

    def test_icon_file_is_served(self, client):
        assert client.get("/static/icons/image_plus.svg").status_code == 200

    def test_picture_is_rendered_as_placeholder_img(self, client):
        html = _post(client, 'picture("balken", width=300, align=left)',
                     image_names="balken").get_data(as_text=True)
        assert 'data-name="balken"' in html and "width: 300px" in html
        assert "picture-left" in html and "picture-sized" in html

    def test_picture_without_width_has_no_inline_width(self, client):
        html = _post(client, 'picture("balken")', image_names="balken").get_data(as_text=True)
        tag = re.search(r'<img class="engipad-picture[^>]*>', html).group(0)
        assert "style=" not in tag and "picture-sized" not in tag

    def test_unknown_name_is_an_error_line(self, client):
        html = _post(client, 'picture("balken")', image_names="other").get_data(as_text=True)
        assert 'data-name="balken"' not in html and "not loaded" in html

    def test_empty_name_list_means_nothing_loaded(self, client):
        html = _post(client, 'picture("balken")', image_names="").get_data(as_text=True)
        assert "not loaded" in html

    def test_without_name_field_nothing_is_checked(self, client):
        html = _post(client, 'picture("balken")').get_data(as_text=True)
        assert 'data-name="balken"' in html

    def test_invalid_names_in_list_are_ignored(self, client):
        html = _post(client, 'picture("balken")', image_names="../x,a b").get_data(as_text=True)
        assert "not loaded" in html

    @pytest.mark.parametrize("raw,selected", [("2", 2), ("3", 3), ("9", 1), ("", 1)])
    def test_image_limit_setting_is_shown_again(self, client, raw, selected):
        html = _post(client, "a = 1", image_limit=raw).get_data(as_text=True)
        assert re.search(rf'<option value="{selected}" selected>', html)

    def test_pictures_in_print_sheets(self, client):
        html = _post(client, '##A\npicture("p")\n##B\nx = 1', image_names="p",
                     all_sheets="1", active_sheet="A").get_data(as_text=True)
        assert html.count('data-name="p"') == 2  # result + print copy

    def _export(self, client, code, images=None, **extra):
        data = {"code": code, "precision": "0.01"}
        if images is not None:
            data["images"] = json.dumps({k: b64(v) if isinstance(v, bytes) else v
                                         for k, v in images.items()})
        data.update(extra)
        return client.post("/export/latex", data=data)

    def test_export_with_picture_is_a_zip(self, client):
        res = self._export(client, 'picture("p", width=192)', {"p": make_png(400, 100)})
        assert res.status_code == 200 and res.mimetype == "application/zip"
        zf = zipfile.ZipFile(BytesIO(res.data))
        assert sorted(zf.namelist()) == ["engipad_export.tex", "img_p.png"]
        assert zf.read("img_p.png") == make_png(400, 100)
        assert "{img_p.png}" in zf.read("engipad_export.tex").decode()

    def test_export_without_pictures_is_still_plain_tex(self, client):
        res = self._export(client, "a = 1")
        assert res.mimetype == "application/x-tex"

    def test_export_missing_image_shows_error_in_document(self, client):
        res = self._export(client, 'picture("p")', {})
        assert res.status_code == 200 and b"not loaded" in res.data

    @pytest.mark.parametrize("images", [
        "not json", "[1,2]", '{"p": 5}', '{"p": "!!!"}',
        json.dumps({"p": b64(b"\xff\xd8\xff" + b"0" * 100)}),
        json.dumps({"p": b64(png_claiming(60000, 60000))}),
        json.dumps({"../evil": b64(make_png())}),
        json.dumps({"a b": b64(make_png())}),
        json.dumps({"a}\\input{x": b64(make_png())}),
    ])
    def test_export_refuses_bad_image_data(self, client, images):
        res = client.post("/export/latex", data={"code": 'picture("p")', "images": images})
        assert res.status_code == 400 and b"Export failed" in res.data

    def test_export_refuses_eleven_images(self, client):
        data = {f"i{n}": b64(make_png()) for n in range(11)}
        res = client.post("/export/latex", data={"code": "a=1", "images": json.dumps(data)})
        assert res.status_code == 400

    def test_export_refuses_images_over_the_total_limit(self, client, monkeypatch):
        monkeypatch.setattr(im, "HARD_TOTAL_BYTES", len(make_png()) * 2 - 1)
        monkeypatch.setattr(im, "PER_IMAGE_DIVISOR", 1)
        data = {f"i{n}": b64(make_png()) for n in range(2)}
        res = client.post("/export/latex", data={"code": "a=1", "images": json.dumps(data)})
        assert res.status_code == 400 and b"too large" in res.data

    def test_request_body_limit(self, client):
        res = client.post("/export/latex", data={"code": "a=1", "images": "x" * (7 * 1024 * 1024)})
        assert res.status_code == 413

    def test_large_valid_export_passes_the_form_limit(self, client):
        # ~2.7 MB of Base64 in one form field: Werkzeug's default of 500 KB must not apply
        data = {f"i{n}": b64(self._noisy_png(200_000)) for n in range(5)}
        res = client.post("/export/latex", data={"code": "a=1", "images": json.dumps(data)})
        assert res.status_code == 200

    @staticmethod
    def _noisy_png(approx_bytes):
        import os
        side = 1
        while True:
            raw = b"".join(b"\x00" + os.urandom(side * 3) for _ in range(side))
            data = (b"\x89PNG\r\n\x1a\n"
                    + _chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
                    + _chunk(b"IDAT", zlib.compress(raw, 0)) + _chunk(b"IEND", b""))
            if len(data) >= approx_bytes or side >= 1000:
                return data
            side += 40


# ---------------------------------------------------------------------
# Browser code: what must (not) travel with a calculation
# ---------------------------------------------------------------------
class TestBrowserCode:
    editor = (ROOT / "static" / "editor.js").read_text(encoding="utf-8")

    def test_calculations_send_only_the_code(self):
        assert "workbookField.value = getWorkbookText();" in self.editor
        assert "workbookField.value = getFileText" not in self.editor

    def test_saved_file_and_autosave_include_images(self):
        assert re.search(r"function saveFile\(\) \{\s+const text = getFileText\(\)", self.editor)
        assert "setItem(AUTOSAVE_KEY, getFileText())" in self.editor

    def test_insert_snippet_keeps_the_undo_history(self):
        body = self.editor.split("function insertSnippet(")[1].split("\n}\n")[0]
        assert "execCommand('insertText'" in body
        assert "codeTextarea.value =" not in body

    def test_inserted_line_shows_all_options_with_their_defaults(self):
        body = self.editor.split("async function addImageFromFile(")[1].split("\n}\n")[0]
        assert "width=' + shown + ', align=' + c.defaultAlignment" in body
        assert "Math.min(info.width, c.defaultDisplayWidth)" in body

    def test_client_config_carries_the_defaults(self):
        cfg = im.client_config()
        assert cfg["defaultDisplayWidth"] == 500 and cfg["defaultAlignment"] == "center"
        # the inserted line must be valid input itself
        line = f'picture("p", width={cfg["defaultDisplayWidth"]}, align={cfg["defaultAlignment"]})'
        assert im.parse_picture_call(line)["width"] == 500

    def test_export_fills_the_image_data_only_for_the_export_button(self):
        assert "'/export/latex'" in self.editor and "export-images-field" in self.editor

    def test_live_update_refills_pictures(self):
        update = self.editor.split("function runLiveUpdate()")[1].split("function initLiveUpdate")[0]
        assert update.count("fillPictures(") == 2


# ---------------------------------------------------------------------
# Python and JavaScript implement the same rules
# ---------------------------------------------------------------------
NODE = shutil.which("node")

_NODE_RUNNER = r"""
const fs = require('fs');
const P = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
P.configure(input.config);
const out = {split: [], png: [], refs: [], join: []};
input.texts.forEach(t => {
  const r = P.splitImageSection(t);
  out.split.push({code: r.code, images: r.images.map(i => [i.name, i.b64]), warnings: r.warnings});
});
input.pngs.forEach(b => {
  try { const i = P.pngInfo(b); out.png.push([i.width, i.height]); }
  catch (e) { out.png.push(null); }
});
input.codes.forEach(c => out.refs.push(P.referencedNames(c)));
input.joins.forEach(j => out.join.push(P.joinImageSection(j.code, j.entries)));
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
class TestPythonAndJavascriptAgree:
    def _run(self, texts=(), pngs=(), codes=(), joins=()):
        payload = {"config": im.client_config(), "texts": list(texts), "pngs": list(pngs),
                   "codes": [list(c) for c in codes], "joins": list(joins)}
        run = subprocess.run(
            [NODE, "-e", _NODE_RUNNER, str(ROOT / "static" / "pictures.js")],
            input=json.dumps(payload), capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    def _texts(self):
        good, other = make_png(), make_png(7, 3, (1, 2, 3))
        return [
            "a = 1\n##S2\nb = 2\n",
            im.join_image_section("x = 1", {"p": good, "q": other}),
            im.join_image_section("x = 1\n", {"p": good}),
            im.join_image_section("", {"p": good}),
            im.join_image_section("a = 1\n##!image foo\nb = 2", {"p": good}),
            "a = 1\n##!image foo\nb = 2\n",
            im.join_image_section("x", {"p": good}).replace("\n", "\r\n"),
            "x\n##!image bad\n" + b64(b"hello" * 30) + "\n##!image ok\n" + b64(good) + "\n",
            "x\n##!image e\n##!image ok\n" + b64(good) + "\n",
            "x\n##!image bomb\n" + b64(png_claiming(60000, 60000)) + "\n",
            "x\n##!image p\n" + b64(good) + "\n##!image p\n" + b64(other) + "\n",
            "x\n" + "".join(f"##!image i{n}\n{b64(good)}\n" for n in range(12)),
            "x\n##!image " + "n" * 41 + "\n" + b64(good) + "\n",
            "x\n##!image bad name\n" + b64(good) + "\n",
            "x\n##!image trunc\n" + b64(good[:-12]) + "\n",
            "x\n##!image pad\n" + b64(good)[:-1] + "\n",
        ]

    def test_split_agrees(self):
        texts = self._texts()
        js = self._run(texts=texts)["split"]
        for text, got in zip(texts, js):
            code, images, warnings = im.split_image_section(text)
            assert got["code"] == code
            assert got["images"] == [[n, b64(d)] for n, d in images.items()]
            assert got["warnings"] == warnings, text[:60]

    def test_png_check_agrees(self):
        blobs = [make_png(), make_png(1200, 3), make_png(1, 1), png_claiming(1201, 5),
                 png_claiming(60000, 60000), png_claiming(1200, 2600), png_claiming(0, 4),
                 b"\xff\xd8\xff" + b"0" * 60, make_png()[:-12], b"hello" * 20]
        encoded = [b64(b) for b in blobs] + ["!!!!", "", "AAA"]
        js = self._run(pngs=encoded)["png"]
        for text, got in zip(encoded, js):
            try:
                try:
                    expected = list(im.png_size(im.decode_png_base64(text)))
                except ValueError:
                    expected = None
            except Exception:
                expected = None
            assert got == expected, text[:30]

    def test_referenced_names_agree(self):
        cases = [['picture("a")\nx = 1'], ['# picture("b", width=100)\npicture( "a" )', "foo"],
                 ['picture("a")', 'picture("a")', 'picture("c")'], ['picture("a b")'], ['picture(a)']]
        js = self._run(codes=cases)["refs"]
        assert js == [im.referenced_names(c) for c in cases]

    def test_join_agrees(self):
        data = {"p": make_png(80, 80), "q": make_png(5, 5)}
        cases = [
            {"code": "x = 1", "entries": [{"name": k, "b64": b64(v)} for k, v in data.items()]},
            {"code": "x = 1\n", "entries": [{"name": "p", "b64": b64(data["p"])}]},
            {"code": "x = 1", "entries": []},
        ]
        js = self._run(joins=cases)["join"]
        assert js[0] == im.join_image_section("x = 1", data)
        assert js[1] == im.join_image_section("x = 1\n", {"p": data["p"]})
        assert js[2] == "x = 1"
