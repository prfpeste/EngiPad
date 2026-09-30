"""
Tests for the LaTeX export (core/latex_export.py::build_latex_document()
and the /export/latex Flask route in app.py).

The "results" structure used as input here is exactly what
core.engine.evaluate_code() returns -- tests build it directly (small,
targeted lists of {"type": ...} dicts) instead of evaluating real code
each time, except where the full path (Flask route) is under test.
"""

import zipfile
from io import BytesIO

import pytest

from core.latex_export import build_latex_document


# ---------------------------------------------------------------------
# build_latex_document(): pure document-building logic, no Flask
# ---------------------------------------------------------------------
class TestBuildLatexDocument:
    def test_empty_results_still_produces_valid_shell(self):
        tex, images = build_latex_document([])
        assert tex.startswith(r"\documentclass")
        assert r"\begin{document}" in tex
        assert r"\end{document}" in tex
        assert images == []

    def test_preamble_defines_the_unit_size_macro(self):
        # Results contain \engiunit{...} around every unit (see
        # mathlib/units.py::wrap_unit_latex()); without this definition
        # the exported document would not compile.
        tex, _ = build_latex_document([])
        assert r"\newcommand{\engiunit}" in tex
        assert r"\usepackage{graphicx}" in tex  # provides \scalebox
        assert tex.index(r"\newcommand{\engiunit}") < tex.index(r"\begin{document}")

    def test_default_unit_size_is_a_plain_pass_through(self):
        # 100%: same size as the text -- no needless \scalebox box
        # around every unit.
        tex, _ = build_latex_document([])
        assert r"\newcommand{\engiunit}[1]{#1}" in tex
        assert r"\scalebox" not in tex

    def test_unit_scale_is_used_for_the_macro(self):
        tex, _ = build_latex_document([], unit_scale=0.75)
        assert r"\newcommand{\engiunit}[1]{\scalebox{0.75}{$\displaystyle #1$}}" in tex

    def test_unit_scale_above_one_works_too(self):
        tex, _ = build_latex_document([], unit_scale=1.5)
        assert r"\scalebox{1.5}" in tex

    @pytest.mark.parametrize("bad_scale", [0, -1, float("nan")])
    def test_invalid_unit_scale_is_rejected(self, bad_scale):
        with pytest.raises(ValueError, match="unit_scale must be positive"):
            build_latex_document([], unit_scale=bad_scale)

    def test_exactly_one_macro_definition_and_no_leftover_placeholder(self):
        tex, _ = build_latex_document([], unit_scale=0.75)
        assert tex.count(r"\newcommand{\engiunit}") == 1
        assert "ENGIUNIT_DEFINITION" not in tex

    def test_latex_item_is_wrapped_in_display_math(self):
        results = [[{"type": "latex", "content": r"a = 2 + 3 = 5"}]]
        tex, images = build_latex_document(results)
        assert r"\[ a = 2 + 3 = 5 \]" in tex
        assert images == []

    def test_multiple_items_in_one_block_each_get_their_own_display_math(self):
        results = [[
            {"type": "latex", "content": "a = 1"},
            {"type": "latex", "content": "b = 2"},
        ]]
        tex, _ = build_latex_document(results)
        assert r"\[ a = 1 \]" in tex
        assert r"\[ b = 2 \]" in tex

    def test_spacer_becomes_vspace(self):
        results = [[{"type": "spacer"}]]
        tex, _ = build_latex_document(results)
        assert r"\vspace{0.8em}" in tex

    def test_required_packages_are_present(self):
        # amsmath/amssymb for \operatorname, \left\right, etc.; graphicx
        # for \includegraphics (plots); inputenc/fontenc for accented
        # characters in text lines.
        tex, _ = build_latex_document([])
        for pkg in ("amsmath", "amssymb", "graphicx", "inputenc", "fontenc"):
            assert rf"\usepackage" in tex and pkg in tex

    def test_plot_item_produces_includegraphics_and_returns_image_bytes(self):
        png_bytes = b"\x89PNG\r\n\x1a\nfake-but-nonempty-payload"
        import base64
        data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")

        results = [[{"type": "plot", "src": data_uri}]]
        tex, images = build_latex_document(results)

        assert r"\includegraphics" in tex
        assert "plot_1.png" in tex
        assert images == [("plot_1.png", png_bytes)]

    def test_multiple_plots_get_sequential_filenames(self):
        import base64
        data_uri = "data:image/png;base64," + base64.b64encode(b"x").decode("ascii")
        results = [
            [{"type": "plot", "src": data_uri}],
            [{"type": "plot", "src": data_uri}],
        ]
        tex, images = build_latex_document(results)
        assert [name for name, _ in images] == ["plot_1.png", "plot_2.png"]
        assert "plot_1.png" in tex and "plot_2.png" in tex

    def test_malformed_plot_src_raises(self):
        results = [[{"type": "plot", "src": "not-a-data-uri"}]]
        with pytest.raises(ValueError):
            build_latex_document(results)

    def test_unknown_item_type_is_silently_skipped(self):
        # Robustness: an unknown/future item type must not crash the
        # export.
        results = [[{"type": "something_new"}]]
        tex, _ = build_latex_document(results)
        assert tex.startswith(r"\documentclass")


# ---------------------------------------------------------------------
# /export/latex: end to end via the Flask test client
# ---------------------------------------------------------------------
class TestExportLatexRoute:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    def test_export_without_plot_returns_plain_tex_file(self, client):
        response = client.post(
            "/export/latex", data={"code": "a = 2 + 3*4\n", "precision": "0.01"}
        )
        assert response.status_code == 200
        assert response.mimetype == "application/x-tex"
        assert "engipad_export.tex" in response.headers["Content-Disposition"]

        tex = response.get_data(as_text=True)
        assert r"\[ a = 2 + 3 \cdot 4 = 14 \]" in tex

    def test_export_with_plot_returns_zip_with_tex_and_png(self, client):
        response = client.post(
            "/export/latex",
            data={"code": "plot(sin(x), x, -5, 5)\n", "precision": "0.01"},
        )
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert "engipad_export.zip" in response.headers["Content-Disposition"]

        with zipfile.ZipFile(BytesIO(response.data)) as zf:
            names = zf.namelist()
            assert "engipad_export.tex" in names
            assert "plot_1.png" in names

            tex = zf.read("engipad_export.tex").decode("utf-8")
            assert "plot_1.png" in tex
            assert zf.read("plot_1.png").startswith(b"\x89PNG")

    def test_export_respects_the_submitted_precision(self, client):
        # Same rel_tol pass-through mechanism as for a normal computation
        # (see test_precision_setting.py) -- just making sure the export
        # route actually USES it instead of staying hardcoded at 1e-4.
        response = client.post(
            "/export/latex", data={"code": "a = 1/3\n", "precision": "1"}
        )
        tex = response.get_data(as_text=True)
        assert r"\[ a = \frac{1}{3} = 0.333 \]" in tex

    def test_export_uses_the_submitted_unit_font_size(self, client):
        response = client.post(
            "/export/latex",
            data={"code": "m = 10'kg\n", "unit_font_size": "75"},
        )
        tex = response.get_data(as_text=True)
        assert r"\scalebox{0.75}" in tex
        assert r"10\,\engiunit{\mathrm{kg}}" in tex

    def test_export_default_unit_size_is_80_percent(self, client):
        response = client.post("/export/latex", data={"code": "m = 10'kg\n"})
        tex = response.get_data(as_text=True)
        assert r"\scalebox{0.8}" in tex

    def test_export_invalid_unit_font_size_falls_back_to_default_80_percent(self, client):
        response = client.post(
            "/export/latex", data={"code": "m = 10'kg\n", "unit_font_size": "abc"}
        )
        assert r"\scalebox{0.8}" in response.get_data(as_text=True)

    def test_export_100_percent_needs_no_scaling(self, client):
        response = client.post(
            "/export/latex", data={"code": "m = 10'kg\n", "unit_font_size": "100"}
        )
        assert r"\newcommand{\engiunit}[1]{#1}" in response.get_data(as_text=True)

    def test_export_handles_error_lines_gracefully(self, client):
        # A broken line must not crash the export -- evaluate_code()
        # already catches that (error_to_latex()), the export just needs
        # to be able to include that error message too.
        response = client.post(
            "/export/latex", data={"code": "a = 1/0\n", "precision": "0.01"}
        )
        assert response.status_code == 200
        tex = response.get_data(as_text=True)
        assert r"\documentclass" in tex

    def test_export_route_is_get_form_only_no_get_method(self, client):
        response = client.get("/export/latex")
        assert response.status_code == 405


# ---------------------------------------------------------------------
# mathlib.units.make_safe_text_latex(): LaTeX special characters in
# free text (comment lines, error messages) must be escaped, otherwise
# a real pdflatex run breaks (MathJax in the browser is more lenient
# and didn't surface the bug).
# ---------------------------------------------------------------------
class TestTextEscapingForRealLatexCompilation:
    def test_percent_sign_is_escaped(self):
        from mathlib.units import make_safe_text_latex
        assert make_safe_text_latex("50% off") == r"50\% off"

    def test_all_special_characters_are_escaped(self):
        from mathlib.units import make_safe_text_latex
        expected = {
            "%": r"\%",
            "#": r"\#",
            "$": r"\$",
            "{": r"\{",
            "}": r"\}",
            "&": r"\&",
            "_": r"\_",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
            "\\": r"\textbackslash{}",
        }
        for char, escaped in expected.items():
            assert make_safe_text_latex(char) == escaped

    def test_percent_survives_a_real_pdflatex_compile(self, tmp_path):
        # The actual proof: text with "%" (and the other special
        # characters) must survive a REAL pdflatex run, not just the
        # escaping logic in isolation.
        import shutil
        import subprocess

        if shutil.which("pdflatex") is None:
            pytest.skip("pdflatex not installed")

        from core.engine import evaluate_code
        from core.latex_export import build_latex_document

        results = evaluate_code('"50% off & tax #1 $ {test} ~x^y"\na = 1+1\n')
        tex, _ = build_latex_document(results)

        tex_file = tmp_path / "doc.tex"
        tex_file.write_text(tex, encoding="utf-8")

        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_file.name],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stdout[-2000:]
        assert (tmp_path / "doc.pdf").exists()


# ---------------------------------------------------------------------
# Units are shown smaller (\engiunit macro): the exported document must
# still compile with a REAL pdflatex run -- for simple units, fractions,
# exponents and the degree signs, which are the structurally different
# cases (a plain macro test wouldn't catch e.g. a "^" without base).
# ---------------------------------------------------------------------
class TestUnitSizeMacroCompilesWithPdflatex:
    @pytest.mark.parametrize("unit_scale", [1.0, 0.75, 1.5])
    def test_document_with_various_units_compiles(self, tmp_path, unit_scale):
        import shutil
        import subprocess

        if shutil.which("pdflatex") is None:
            pytest.skip("pdflatex not installed")

        from core.engine import evaluate_code

        results = evaluate_code(
            "F = 7.5'kN | N\n"
            "L = 2.5'm\n"
            "M = F * L | kNm\n"
            "A = 3'm^2\n"
            "k = 5'W/(m^2*K)\n"
            "T = 110'degC\n"
            "phi = 30'deg\n"
            "w = 0'J/kg\n"
            "x = a^0.5\n"
            "a = 5'm\n"
            "y = a^0.5\n"
        )
        tex, _ = build_latex_document(results, unit_scale=unit_scale)
        assert r"\engiunit{" in tex

        tex_file = tmp_path / "doc.tex"
        tex_file.write_text(tex, encoding="utf-8")

        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_file.name],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stdout[-2000:]
        assert (tmp_path / "doc.pdf").exists()
