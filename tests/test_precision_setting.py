"""
Tests for the configurable rounding precision (settings panel ->
app.py -> core/engine.py::evaluate_code(rel_tol=...) ->
core/formatter.py::format_computation_result(rel_tol=...) ->
mathlib/units.py::format_magnitude_decimal(rel_tol=...)) and font size.

Before the fix: the precision input field wasn't part of the <form>
element at all (only the textarea was) -- the value was never sent to
the server, fell back to the hardcoded HTML default after every
"Calculate", and core/formatter.py's rel_tol was hardcoded to 1e-4
everywhere anyway.
"""

import pytest

from app import _parse_precision_to_rel_tol
from core.engine import evaluate_code


# ---------------------------------------------------------------------
# app.py: text -> rel_tol conversion
# ---------------------------------------------------------------------
class TestParsePrecisionToRelTol:
    def test_default_value(self):
        assert _parse_precision_to_rel_tol("0.01") == pytest.approx(1e-4)

    def test_one_percent(self):
        assert _parse_precision_to_rel_tol("1") == pytest.approx(0.01)

    def test_fine_precision(self):
        assert _parse_precision_to_rel_tol("0.000001") == pytest.approx(1e-8)

    @pytest.mark.parametrize("bad_value", ["", "abc", "-1", "0", None])
    def test_invalid_or_non_positive_falls_back_to_default(self, bad_value):
        # Invalid/empty/non-positive input -> default rel_tol (1e-4), so
        # the computation doesn't crash.
        assert _parse_precision_to_rel_tol(bad_value) == pytest.approx(1e-4)


# ---------------------------------------------------------------------
# core/engine.py: rel_tol actually affects the rounding
# ---------------------------------------------------------------------
class TestRelTolAffectsRounding:
    def test_default_rel_tol_matches_previous_hardcoded_behavior(self):
        results = evaluate_code("a = 1/3\n")
        content = results[0][0]["content"]
        assert content == r"a = \frac{1}{3} = 0.33333"

    def test_coarser_rel_tol_rounds_less_precisely(self):
        results = evaluate_code("a = 1/3\n", rel_tol=0.01)
        content = results[0][0]["content"]
        assert content == r"a = \frac{1}{3} = 0.333"

    def test_finer_rel_tol_rounds_more_precisely(self):
        results = evaluate_code("a = 1/3\n", rel_tol=1e-8)
        content = results[0][0]["content"]
        assert content == r"a = \frac{1}{3} = 0.333333333"

    def test_rel_tol_also_affects_desired_unit_conversion(self):
        # Second code path in format_computation_result() (desired_unit
        # != None) -- separate rel_tol pass-through point, tested on
        # its own.
        results = evaluate_code("x = (1/3)'m | mm\n", rel_tol=0.01)
        content = results[0][0]["content"]
        assert content == r"x = \frac{1}{3}\,\engiunit{\mathrm{m}} = 333\,\engiunit{\mathrm{mm}}"


# ---------------------------------------------------------------------
# app.py: end to end via the Flask test client -- form round trip
# ---------------------------------------------------------------------
class TestPrecisionFieldRoundTrip:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    def test_get_shows_default_value(self, client):
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'id="precision-input"' in html
        assert 'value="0.01"' in html

    def test_precision_input_is_a_plain_text_field(self, client):
        # Core point of this change: no more <input type="number"> (with
        # a spinner control), just a plain text field.
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'id="precision-input" type="number"' not in html
        assert 'type="text" inputmode="decimal" id="precision-input"' in html

    def test_precision_input_is_wired_to_the_form(self, client):
        # The field sits outside the <form> tag (in the toolbar
        # dropdown), so it must be connected via the form="..."
        # attribute to be submitted at all.
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'form="calc-form"' in html.split('id="precision-input"')[1][:60]

    def test_posted_value_is_echoed_back(self, client):
        response = client.post("/", data={"code": "a = 1\n", "precision": "0.5"})
        html = response.get_data(as_text=True)
        assert 'value="0.5"' in html

    def test_invalid_posted_value_still_stays_in_the_field(self, client):
        # Core point: the last-entered value ALWAYS stays in the field
        # -- even if it isn't (yet) a valid number while the user is
        # typing.
        response = client.post("/", data={"code": "a = 1\n", "precision": "0.0"})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'value="0.0"' in html

    def test_missing_precision_field_falls_back_to_default(self, client):
        response = client.post("/", data={"code": "a = 1\n"})
        html = response.get_data(as_text=True)
        assert 'value="0.01"' in html


# ---------------------------------------------------------------------
# app.py: text -> font size (px) conversion
# ---------------------------------------------------------------------
class TestParseFontSizePx:
    def test_default_value(self):
        from app import _parse_font_size_px
        assert _parse_font_size_px("16") == 16

    def test_valid_value_in_range(self):
        from app import _parse_font_size_px
        assert _parse_font_size_px("22") == 22

    def test_rounds_to_nearest_int(self):
        from app import _parse_font_size_px
        assert _parse_font_size_px("22.6") == 23

    @pytest.mark.parametrize("bad_value", ["", "abc", "-5", "0", None])
    def test_invalid_or_non_positive_falls_back_to_default(self, bad_value):
        from app import _parse_font_size_px
        assert _parse_font_size_px(bad_value) == 14

    def test_too_small_value_is_clamped(self):
        # Protects against broken layout (text practically unreadably
        # small) -- AND against arbitrary text ever reaching a
        # style="..." attribute in the template via this field.
        from app import _parse_font_size_px
        assert _parse_font_size_px("1") == 10

    def test_too_large_value_is_clamped(self):
        from app import _parse_font_size_px
        assert _parse_font_size_px("9999") == 40


# ---------------------------------------------------------------------
# app.py: font-size field end to end (form round trip + application)
# ---------------------------------------------------------------------
class TestFontSizeFieldRoundTrip:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    def test_get_shows_default_value(self, client):
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'id="fontsize-input"' in html
        assert 'value="14"' in html

    def test_font_size_input_is_a_plain_text_field_wired_to_the_form(self, client):
        response = client.get("/")
        html = response.get_data(as_text=True)
        assert 'type="text" inputmode="numeric" id="fontsize-input"' in html
        after_id = html.split('id="fontsize-input"')[1][:80]
        assert 'form="calc-form"' in after_id

    def test_posted_value_is_echoed_back_and_applied(self, client):
        response = client.post("/", data={"code": "a = 1\n", "font_size": "20"})
        html = response.get_data(as_text=True)
        assert 'id="fontsize-input"' in html
        assert 'value="20"' in html
        assert "font-size: 20px;" in html

    def test_invalid_posted_value_stays_in_the_field_but_falls_back_visually(self, client):
        # Value stays visible (user is currently typing), but the actual
        # styling falls back to the default so the page doesn't break.
        response = client.post("/", data={"code": "a = 1\n", "font_size": "abc"})
        html = response.get_data(as_text=True)
        assert 'value="abc"' in html
        assert "font-size: 14px;" in html

    def test_missing_font_size_field_falls_back_to_default(self, client):
        response = client.post("/", data={"code": "a = 1\n"})
        html = response.get_data(as_text=True)
        assert 'value="14"' in html
        assert "font-size: 14px;" in html

    def test_font_size_is_applied_to_both_input_and_result_panel(self, client):
        # Core point: not just the result panel -- the input panel (the
        # textarea in the form) should also pick up the configured font
        # size.
        response = client.post("/", data={"code": "a = 1\n", "font_size": "22"})
        html = response.get_data(as_text=True)
        assert 'id="calc-form"' in html
        # Both containers carry the same font-size inline style.
        assert html.count("font-size: 22px;") == 2


# ---------------------------------------------------------------------
# app.py: text -> unit font size (percent) conversion
# ---------------------------------------------------------------------
class TestParseUnitFontSizePct:
    def test_default_is_80_percent(self):
        # 80% = units slightly smaller than the surrounding text (looks
        # better; a deliberate choice, changed from the original 100%).
        # The other tests below refer to DEFAULT_UNIT_FONT_SIZE, only
        # this one pins the actual value.
        from app import DEFAULT_UNIT_FONT_SIZE, _parse_unit_font_size_pct
        assert DEFAULT_UNIT_FONT_SIZE == "80"
        assert _parse_unit_font_size_pct("80") == 80
        assert _parse_unit_font_size_pct("100") == 100

    def test_valid_value_in_range(self):
        from app import _parse_unit_font_size_pct
        assert _parse_unit_font_size_pct("75") == 75

    def test_rounds_to_nearest_int(self):
        from app import _parse_unit_font_size_pct
        assert _parse_unit_font_size_pct("87.6") == 88

    @pytest.mark.parametrize("bad_value", ["", "abc", "-5", "0", "50%", None])
    def test_invalid_or_non_positive_falls_back_to_default(self, bad_value):
        from app import DEFAULT_UNIT_FONT_SIZE, _parse_unit_font_size_pct
        assert _parse_unit_font_size_pct(bad_value) == int(DEFAULT_UNIT_FONT_SIZE)

    def test_too_small_value_is_clamped(self):
        from app import _parse_unit_font_size_pct
        assert _parse_unit_font_size_pct("1") == 25

    def test_too_large_value_is_clamped(self):
        from app import _parse_unit_font_size_pct
        assert _parse_unit_font_size_pct("9999") == 200

    def test_only_a_plain_number_can_ever_come_out(self):
        # The value lands in a style="..." attribute and in a LaTeX
        # \scalebox factor.
        from app import DEFAULT_UNIT_FONT_SIZE, _parse_unit_font_size_pct
        assert isinstance(_parse_unit_font_size_pct("75; color: red"), int)
        assert _parse_unit_font_size_pct("75; color: red") == int(DEFAULT_UNIT_FONT_SIZE)


# ---------------------------------------------------------------------
# app.py: unit-font-size field end to end (form round trip + application)
# ---------------------------------------------------------------------
class TestUnitFontSizeFieldRoundTrip:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    def test_get_shows_default_value_and_percent_suffix(self, client):
        html = client.get("/").get_data(as_text=True)
        assert 'id="unitfontsize-input"' in html
        after_id = html.split('id="unitfontsize-input"')[1][:140]
        assert 'value="80"' in after_id
        assert 'class="setting-suffix">%</span>' in after_id

    def test_field_is_labelled_and_sits_below_font_size(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "<span>Unit font size</span>" in html
        assert html.index('id="fontsize-input"') < html.index('id="unitfontsize-input"')

    def test_field_is_a_plain_text_field_wired_to_the_form(self, client):
        html = client.get("/").get_data(as_text=True)
        assert 'type="text" inputmode="numeric" id="unitfontsize-input"' in html
        after_id = html.split('id="unitfontsize-input"')[1][:80]
        assert 'name="unit_font_size"' in after_id
        assert 'form="calc-form"' in after_id

    def test_default_scale_is_applied_to_the_result_panel(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "--unit-scale: 80%;" in html

    def test_posted_value_is_echoed_back_and_applied(self, client):
        response = client.post("/", data={"code": "a = 1\n", "unit_font_size": "75"})
        html = response.get_data(as_text=True)
        after_id = html.split('id="unitfontsize-input"')[1][:140]
        assert 'value="75"' in after_id
        assert "--unit-scale: 75%;" in html

    def test_invalid_posted_value_stays_in_the_field_but_falls_back_visually(self, client):
        response = client.post("/", data={"code": "a = 1\n", "unit_font_size": "abc"})
        html = response.get_data(as_text=True)
        after_id = html.split('id="unitfontsize-input"')[1][:140]
        assert 'value="abc"' in after_id
        assert "--unit-scale: 80%;" in html

    def test_missing_field_falls_back_to_default(self, client):
        response = client.post("/", data={"code": "a = 1\n"})
        html = response.get_data(as_text=True)
        assert "--unit-scale: 80%;" in html

    def test_out_of_range_value_is_clamped_in_the_style(self, client):
        response = client.post("/", data={"code": "a = 1\n", "unit_font_size": "9999"})
        assert "--unit-scale: 200%;" in response.get_data(as_text=True)

    def test_unit_setting_does_not_disturb_font_size_and_precision(self, client):
        response = client.post("/", data={
            "code": "a = 1/3\n", "precision": "1", "font_size": "20", "unit_font_size": "75",
        })
        html = response.get_data(as_text=True)
        assert "font-size: 20px; --unit-scale: 75%;" in html
        assert 'value="1"' in html

