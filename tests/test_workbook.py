"""Workbooks: several sheets in one text, references "Sheet::name",
lazy evaluation, display, export, and the web routes."""

import pytest

from core.latex_export import build_latex_document
from core.workbook import (
    Sheet,
    evaluate_workbook,
    is_valid_sheet_name,
    parse_workbook,
    serialize_workbook,
)
from parsing.ast_nodes import BinaryOp, Identifier, SheetRef
from parsing.lexer import DCOLON, tokenize
from parsing.parser import parse


def _contents(results):
    return [item.get("content", "") for block in results for item in block]


class TestSheetReferenceSyntax:
    def test_double_colon_is_one_token(self):
        types = [t.type for t in tokenize("S1::L")]
        assert DCOLON in types

    def test_single_colon_is_still_rejected(self):
        with pytest.raises(ValueError):
            tokenize("a:b")

    def test_parses_to_sheet_ref(self):
        node = parse("S1::L / c")
        assert node == BinaryOp("/", SheetRef("S1", "L"), Identifier("c"))

    def test_dot_is_still_not_a_token(self):
        # The security property of the lexer: no attribute access.
        with pytest.raises(ValueError):
            tokenize("S1.L")


class TestWorkbookText:
    def test_plain_text_is_one_unnamed_sheet(self):
        assert parse_workbook("a = 1\nb = 2") == [Sheet(None, "a = 1\nb = 2")]

    def test_markers_make_named_sheets(self):
        sheets = parse_workbook("##A\nx = 1\n##B\ny = 2\nz = 3")
        assert sheets == [Sheet("A", "x = 1"), Sheet("B", "y = 2\nz = 3")]

    def test_space_after_hashes_is_allowed(self):
        assert [s.name for s in parse_workbook("## A\n1\n##  B  \n2")] == ["A", "B"]

    def test_marker_only_counts_on_the_first_line(self):
        # An old single-sheet file with a "##Results" comment inside is
        # NOT turned into a workbook.
        text = "x = 1\n##Results\ny = 2"
        assert parse_workbook(text) == [Sheet(None, text)]

    def test_comment_with_spaces_is_no_marker(self):
        text = "## Title of the page\nx = 1"
        assert parse_workbook(text) == [Sheet(None, text)]

    def test_leading_blank_lines_are_ignored(self):
        assert [s.name for s in parse_workbook("\n\n##A\n1")] == ["A"]

    def test_duplicate_names_raise(self):
        with pytest.raises(ValueError, match="Duplicate"):
            parse_workbook("##A\n1\n##A\n2")

    def test_round_trip(self):
        text = "##A\nx = 1\n##B\ny = 2"
        assert serialize_workbook(parse_workbook(text)) == text

    def test_single_unnamed_sheet_serializes_without_marker(self):
        assert serialize_workbook([Sheet(None, "a = 1")]) == "a = 1"

    def test_sheet_name_rules(self):
        assert is_valid_sheet_name("S1") and is_valid_sheet_name("Basis_2")
        assert not is_valid_sheet_name("1S") and not is_valid_sheet_name("a b")


class TestEvaluation:
    BOOK = (
        "##NameA\nL = 5'm\nc = 20'm/s\nt = L/c\n"
        "##NameB\nt = NameA::L/NameA::c\n"
    )

    def test_single_sheet_is_unchanged(self):
        out = evaluate_workbook("a = 2\nb = a * 3")
        assert out["names"] == [] and out["active"] is None
        assert "6" in _contents(out["results"])[1]

    def test_reference_value_with_unit(self):
        out = evaluate_workbook(self.BOOK, active="NameB")
        text = _contents(out["results"])[0]
        assert out["active"] == "NameB"
        assert "0.25" in text and r"\mathrm{s}" in text

    def test_reference_is_shown_with_smaller_prefix_macro(self):
        text = _contents(evaluate_workbook(self.BOOK, active="NameB")["results"])[0]
        assert r"\engisheet{\mathrm{NameA}{::}}L" in text

    def test_unknown_active_sheet_falls_back_to_first(self):
        assert evaluate_workbook(self.BOOK, active="Nope")["active"] == "NameA"

    def test_all_sheets_option(self):
        out = evaluate_workbook(self.BOOK, active="NameA", all_sheets=True)
        assert [name for name, _ in out["all"]] == ["NameA", "NameB"]
        assert evaluate_workbook(self.BOOK)["all"] is None

    def test_order_follows_references_not_position(self):
        # S1 uses S2, which is written AFTER it.
        book = "##S1\nx = S2::y * 2\n##S2\ny = 21"
        text = _contents(evaluate_workbook(book, active="S1")["results"])[0]
        assert "42" in text

    def test_reference_uses_final_value(self):
        book = "##A\nx = 1\nx = 5\n##B\ny = A::x"
        assert "5" in _contents(evaluate_workbook(book, active="B")["results"])[0]

    def test_own_sheet_reference_works(self):
        book = "##A\nx = 4\ny = A::x + 1\n##B\nz = 1"
        assert "5" in _contents(evaluate_workbook(book, active="A")["results"])[1]

    def test_missing_variable_is_an_error_line(self):
        book = "##A\nx = 1\n##B\ny = A::q"
        text = _contents(evaluate_workbook(book, active="B")["results"])[0]
        assert "not defined in sheet" in text

    def test_missing_sheet_is_an_error_line(self):
        book = "##A\nx = Z::q"
        assert "does not exist" in _contents(evaluate_workbook(book)["results"])[0]

    def test_reference_without_workbook_is_an_error(self):
        assert "does not exist" in _contents(evaluate_workbook("x = S1::L")["results"])[0]

    def test_assignment_to_another_sheet_is_rejected(self):
        book = "##A\nx = 1\n##B\nA::x = 3"
        assert "read-only" in _contents(evaluate_workbook(book, active="B")["results"])[0]

    def test_circular_reference_is_reported(self):
        book = "##A\nx = B::y\n##B\ny = A::x"
        text = _contents(evaluate_workbook(book, active="A")["results"])[0]
        assert "Circular reference" in text

    def test_duplicate_names_give_an_error_block(self):
        out = evaluate_workbook("##A\n1\n##A\n2")
        assert "Duplicate" in _contents(out["results"])[0]

    def test_units_still_checked_across_sheets(self):
        book = "##A\nm = 3'kg\n##B\nx = A::m + 2'm"
        assert "incompatible" in _contents(evaluate_workbook(book, active="B")["results"])[0]

    def test_sheet_names_may_equal_unit_names(self):
        book = "##m\nv = 2'm\n##kg\nw = m::v * 3"
        assert "6" in _contents(evaluate_workbook(book, active="kg")["results"])[0]


class TestLatexExport:
    RESULTS = [[{"type": "latex", "content": "a = 1"}]]

    def test_single_sheet_document_has_no_header(self):
        tex, _ = build_latex_document(self.RESULTS)
        assert "fancyhdr" not in tex and r"\markboth" not in tex

    def test_sheet_name_goes_into_the_page_header(self):
        tex, _ = build_latex_document(self.RESULTS, sheet_name="Basis")
        assert r"\usepackage{fancyhdr}" in tex
        assert r"\fancyhead[L]{\textbf{\leftmark}}" in tex
        assert r"\markboth{Basis}{Basis}" in tex
        assert tex.index("fancyhdr") < tex.index(r"\begin{document}")

    def test_sheet_name_is_escaped(self):
        tex, _ = build_latex_document(self.RESULTS, sheet_name="my_sheet")
        assert r"\markboth{my\_sheet}{my\_sheet}" in tex

    def test_all_sheets_start_on_new_pages(self):
        tex, _ = build_latex_document(
            None, sheets=[("A", self.RESULTS), ("B", self.RESULTS), ("C", self.RESULTS)]
        )
        assert tex.count(r"\markboth") == 3
        assert tex.count(r"\clearpage") == 2
        assert tex.index(r"\markboth{A}") < tex.index(r"\markboth{B}") < tex.index(r"\markboth{C}")

    def test_prefix_macro_is_defined_with_the_given_scale(self):
        tex, _ = build_latex_document(self.RESULTS, sheet_scale=0.6)
        assert r"\newcommand{\engisheet}[1]{\scalebox{0.6}{$#1$}}" in tex

    def test_prefix_macro_at_100_percent_is_plain(self):
        tex, _ = build_latex_document(self.RESULTS, sheet_scale=1)
        assert r"\newcommand{\engisheet}[1]{#1}" in tex


@pytest.fixture
def client():
    from app import app
    return app.test_client()


BOOK = "##NameA\nL = 5'm\nc = 20'm/s\n##NameB\nt = NameA::L/NameA::c\n"


class TestRoutes:
    def test_post_shows_only_the_active_sheet(self, client):
        html = client.post("/", data={"code": BOOK, "active_sheet": "NameB"}).get_data(as_text=True)
        assert "engisheet" in html and "0.25" in html
        assert 'class="print-sheet"' not in html

    def test_all_sheets_adds_print_sections(self, client):
        html = client.post(
            "/", data={"code": BOOK, "active_sheet": "NameB", "all_sheets": "1"}
        ).get_data(as_text=True)
        assert html.count('class="print-sheet"') == 2

    def test_sheet_name_is_in_the_print_header(self, client):
        html = client.post("/", data={"code": BOOK, "active_sheet": "NameB"}).get_data(as_text=True)
        assert 'id="print-sheet-title">NameB<' in html

    def test_single_sheet_page_has_empty_print_header(self, client):
        html = client.post("/", data={"code": "a = 1"}).get_data(as_text=True)
        assert 'class="print-head is-empty"' in html

    def test_prefix_size_setting_reaches_the_page(self, client):
        html = client.post("/", data={"code": BOOK, "sheet_font_size": "65"}).get_data(as_text=True)
        assert "--sheet-scale: 65%" in html

    def test_invalid_prefix_size_falls_back_to_default(self, client):
        html = client.post("/", data={"code": BOOK, "sheet_font_size": "abc"}).get_data(as_text=True)
        assert "--sheet-scale: 50%" in html

    def test_export_active_sheet(self, client):
        resp = client.post("/export/latex", data={"code": BOOK, "active_sheet": "NameB"})
        assert "engipad_NameB.tex" in resp.headers["Content-Disposition"]
        assert r"\markboth{NameB}{NameB}" in resp.get_data(as_text=True)

    def test_export_all_sheets(self, client):
        resp = client.post(
            "/export/latex", data={"code": BOOK, "active_sheet": "NameB", "all_sheets": "1"}
        )
        tex = resp.get_data(as_text=True)
        assert "engipad_workbook.tex" in resp.headers["Content-Disposition"]
        assert r"\markboth{NameA}" in tex and r"\markboth{NameB}" in tex

    def test_export_single_sheet_is_as_before(self, client):
        resp = client.post("/export/latex", data={"code": "a = 1"})
        assert "engipad_export.tex" in resp.headers["Content-Disposition"]
        assert "fancyhdr" not in resp.get_data(as_text=True)
