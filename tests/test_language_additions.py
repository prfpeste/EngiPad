"""Language additions of 12.11.2026:

- a statement may continue over several lines while a bracket is open
  (or after a backslash at the end of a line),
- "name =: expr" shows only "name = value",
- abs(x) is shown as |x|,
- nsolve() works with units (the start value carries the unit of the
  unknown).
"""

import pytest

from core.engine import evaluate_code, join_continued_lines
from tests.conftest import unwrap_unit_markup


def contents(code):
    return [
        unwrap_unit_markup(item["content"])
        for block in evaluate_code(code)
        for item in block
        if item["type"] == "latex"
    ]


MATRIX = (
    r"\left[\begin{matrix}0.7 & 0.3\\0.3 & 0.7\end{matrix}\right]"
)  # unchanged by the matrix formatting of 13.11.2026


class TestLineContinuation:
    def test_open_bracket_continues_on_the_next_lines(self):
        out = contents("ν = 0.3\nC = mat([\n  [1-ν, ν],\n  [ν, 1-ν]])\nd = 2\n")
        assert len(out) == 3
        assert out[1].endswith("= " + MATRIX)
        assert out[2] == "d = 2"

    def test_backslash_at_the_line_end_is_accepted_too(self):
        out = contents("ν = 0.3\nC = mat([\\\n[1-ν, ν],\\\n[ν, 1-ν]])\n")
        assert out[1].endswith("= " + MATRIX)

    def test_backslash_continues_without_a_bracket(self):
        assert contents("e = 5 + \\\n    6\n") == ["e = 5 + 6 = 11"]

    def test_same_result_as_on_one_line(self):
        one = contents("M = mat([[1, 2], [3, 4]]) ; d = det(M)\n")
        many = contents("M = mat([\n[1, 2],\n[3, 4]]) ; d = det(M)\n")
        assert one[-1].split(r"\quad")[-1] == many[-1].split(r"\quad")[-1]

    def test_function_arguments_may_be_split(self):
        out = contents("s = sum(i,\n  (i, 1, 4))\n")
        assert out[0].endswith("= 10")

    def test_forgotten_bracket_does_not_swallow_the_next_statement(self):
        out = contents("a = (1 + 2\nc = 3\nd = c + 1\n")
        assert "Error in line 1" in out[0]
        assert out[1:] == ["c = 3", "d = c + 1 = 4"]

    def test_continuation_stops_at_an_empty_line(self):
        out = contents("a = (1 +\n\nc = 3\n")
        assert "Error in line 1" in out[0]
        assert out[-1] == "c = 3"

    def test_continuation_stops_at_the_end_of_the_input(self):
        out = contents("a = mat([\n[1, 2],")
        assert len(out) == 1 and "Error in line 1" in out[0]

    def test_error_line_number_is_the_first_line_of_the_statement(self):
        out = contents("a = 1\n\nb = mat([\n[1, 2],\n[3, 4]) + q)\n")
        assert "Error in line 3" in out[-1]

    def test_text_lines_and_comments_never_continue(self):
        out = contents("'see (above'\n\"Title (1\"\n# note (x\na = 1 ; 'remark [1'\nb = 2\n")
        assert out[-1] == "b = 2"
        assert len(out) == 4

    def test_plain_lines_are_passed_through_unchanged(self):
        code = "a = 1\n\n  b = 2  \n'text  '\n"
        assert join_continued_lines(code) == [
            (1, "a = 1"), (2, ""), (3, "  b = 2  "), (4, "'text  '"),
        ]

    def test_blank_lines_still_give_spacers(self):
        blocks = evaluate_code("a = 1\n\nb = 2\n")
        assert [b[0]["type"] for b in blocks] == ["latex", "spacer", "latex"]

    def test_works_inside_a_workbook(self):
        from core.workbook import evaluate_workbook

        text = "##A\nM = mat([\n[1, 2],\n[3, 4]])\nd = det(M)\n##B\ne = A::d * 2\n"
        outcome = evaluate_workbook(text, active="B")
        assert outcome["results"][0][0]["content"].endswith("= -4")


class TestResultOnlyAssignment:
    def test_shows_only_name_and_value(self):
        assert contents("a = 2 ; b =: a * 3 + 1\n") == [r"a = 2 \quad b = 7"]

    def test_matrix_over_several_lines(self):
        out = contents("ν = 0.3\nC =: mat([\n[1-ν, ν],\n[ν, 1-ν]])\n")
        assert out[1] == "C = " + MATRIX

    def test_value_is_stored_like_a_normal_assignment(self):
        assert contents("b =: 5'm\nc = b * 2\n")[-1].endswith(r"10\,\mathrm{m}")

    def test_target_unit_still_works(self):
        assert contents("L =: 5'm * 2 | cm\n") == [r"L = 1000\,\mathrm{cm}"]

    def test_formula_only_assignment_is_unchanged(self):
        assert contents("f := x^2 + 1\n") == ["f = x^{2} + 1"]

    def test_sheet_reference_after_equals_is_not_mistaken(self):
        out = contents("a =::b\n")
        assert "Error" in out[0]

    def test_errors_are_reported_as_usual(self):
        assert "Error in line 1" in contents("a =: 5'm + 3'kg\n")[0]


class TestAbsIsShownWithBars:
    def test_input_echo(self):
        assert contents("a = abs(-10)\n") == [r"a = \left|-10\right| = 10"]

    def test_inside_a_larger_formula(self):
        out = contents("b = -3'm\nc = abs(b) * 2\n")
        assert out[1].startswith(r"c = \left|b\right| \cdot 2 = 6")

    def test_power_of_abs_is_unambiguous(self):
        assert contents("b = -3 ; c = abs(b)^2\n")[0].endswith(
            r"c = \left|b\right|^{2} = 9"
        )


DIODE = (
    "T = 300.15'K ; e = 1.602*10^-19'C ; k = 1.38*10^-23'J/K\n"
    "i_k = 200 ; i_s = 9*10^-9\n"
)
DIODE_EQ = "Eq(exp(e*U_max/(k*T)), ((1+i_k/i_s)/(1+e*U_max/(k*T))))"


class TestNsolveWithUnits:
    def test_start_value_gives_the_unit_of_the_unknown(self):
        out = contents(DIODE + f"U_max =: nsolve({DIODE_EQ}, (U_max), (0.5'V))\n")
        assert out[-1] == r"U_{\text{max}} = 0.53637\,\mathrm{V}"

    def test_same_number_as_the_unitless_calculation(self):
        plain = contents(
            "T = 300.15 ; e = 1.602*10^-19 ; k = 1.38*10^-23\n"
            "i_k = 200 ; i_s = 9*10^-9\n"
            f"U_max =: nsolve({DIODE_EQ}, (U_max), (0.5))\n"
        )
        assert plain[-1] == r"U_{\text{max}} = 0.53637"

    def test_result_can_be_converted_and_used(self):
        out = contents(
            DIODE + f"U_max = nsolve({DIODE_EQ}, U_max, 500'mV)\n"
            "U_max | mV\nP = U_max * 2'A\n"
        )
        assert out[-2].endswith(r"536.37\,\mathrm{mV}")
        assert out[-1].endswith(r"1.0727\,\mathrm{W}")

    def test_start_value_in_another_unit_of_the_same_dimension(self):
        out = contents("L = 2'm\ns =: nsolve(Eq(x^2, L^2 * 4), x, 1'cm)\n")
        assert out[-1] == r"s = 4\,\mathrm{m}"

    def test_plain_numbers_work_as_before(self):
        assert contents("nsolve((x^2 - 2), (x), (1))\n")[0].endswith("= 1.4142")

    def test_two_argument_form(self):
        assert contents("r =: nsolve(x^2 - 2, 1)\n") == ["r = 1.4142"]

    def test_system_of_equations(self):
        out = contents("r =: nsolve([x + y - 3, x - y - 1], [x, y], [1, 1])\n")
        assert out[0] == r"r = \left[\begin{matrix}2\\1\end{matrix}\right]"

    def test_system_with_units(self):
        out = contents(
            "r = nsolve([Eq(a + b, 3'm), Eq(a/b, 2)], [a, b], [1'm, 1'm])\n"
            "a_1 =: r[1] | cm\n"
        )
        assert out[-1] == r"a_{\text{1}} = 200\,\mathrm{cm}"

    def test_unknown_with_a_value_is_a_clear_error(self):
        out = contents("x = 4\ny = nsolve(x^2 - 2, x, 1)\n")
        assert "must be a variable that has no value yet" in out[-1]

    def test_other_variable_without_a_value_is_a_clear_error(self):
        out = contents("y = nsolve(x^2 - q, x, 1)\n")
        assert "without a value: q" in out[-1]

    def test_wrong_number_of_start_values(self):
        out = contents("y = nsolve([x + z, x - z], [x, z], [1])\n")
        assert "one start value per unknown" in out[-1]


class TestScientificNotation:
    """"1.5e-3" as a number (the user guide always described it)."""

    def test_lexer_reads_one_number_token(self):
        from parsing.lexer import NUMBER, tokenize

        for text in ("1.5e-3", "2e6", "2E6", "7e+2", ".5e1"):
            tokens = tokenize(text)
            assert [t.type for t in tokens] == [NUMBER, "EOF"], text
            assert tokens[0].value == text

    @pytest.mark.parametrize("code, expected", [
        ("a = 1.5e3\n", "a = 1500"),
        ("a = 2E6\n", r"a = 2\cdot 10^{6}"),
        ("g = -2.5e+2\n", "g = -250"),
        ("a = 1.5e-3'm\n", r"a = 0.0015\,\mathrm{m}"),
        ("E = 210e3'N/mm^2 | GPa\n", r"E = 210\,\mathrm{GPa}"),
    ])
    def test_plain_literals(self, code, expected):
        assert contents(code) == [expected]

    def test_same_value_as_the_power_of_ten_spelling(self):
        short = contents("e = 1.602e-19'C ; k = 1.38e-23'J/K ; T = 300'K\nU =: k*T/e | mV\n")
        long = contents("e = 1.602*10^-19'C ; k = 1.38*10^-23'J/K ; T = 300'K\nU =: k*T/e | mV\n")
        assert short[-1] == long[-1] == r"U = 25.843\,\mathrm{mV}"

    def test_shown_as_power_of_ten_inside_a_formula(self):
        assert contents("s = 1.5e-3 * 2\n") == [r"s = 1.5 \cdot 10^{-3} \cdot 2 = 0.003"]

    def test_parenthesized_as_the_base_of_a_power(self):
        assert contents("f = 1e-3^2\n") == [
            r"f = \left(1 \cdot 10^{-3}\right)^{2} = 1\cdot 10^{-6}"
        ]

    @pytest.mark.parametrize("code", ["h = 2e\n", "x = 3\nh = 3e-x\n", "r = 4e2e3\n"])
    def test_incomplete_exponent_is_still_an_error(self, code):
        assert "Error" in contents(code)[-1]

    def test_names_starting_with_e_are_untouched(self):
        assert contents("e3 = 4 ; b = 2*e3\n") == [r"e3 = 4 \quad b = 2 \cdot e3 = 8"]

    def test_e_and_I_stay_ordinary_names(self):
        # Decision of 12.11.2026: no constants "e"/"I" (they are common
        # variable names); exp(1) and the symbol from the Greek menu are
        # the documented ways.
        assert contents("a = e^1\n") == ["a = e^{1} = e"]
        assert contents("c = exp(1)\n") == ["c = e^{1} = 2.7183"]
        assert contents("b = ⅈ^2\n") == ["b = i^{2} = -1"]
