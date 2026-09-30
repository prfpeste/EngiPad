"""Tests for the user-adjustable timeout (Settings -> "Timeout", 1-30 s).

The existing tests/test_safe_runner.py covers the timeout MECHANISM; this
file covers the setting: parsing/clamping, that the value really reaches
run_with_timeout() on both routes, and the message.
"""

import time

import pytest

import app as app_module
from app import _parse_timeout_seconds
from tests.test_safe_runner import _hang  # module-level, picklable


class TestParseTimeoutSeconds:
    @pytest.mark.parametrize("raw, expected", [
        ("10", 10), ("1", 1), ("30", 30), ("15.4", 15), ("15.6", 16),
        (" 20 ", 20), ("999", 30), ("31", 30), ("0.4", 1), ("1e2", 30),
    ])
    def test_valid_values_are_rounded_and_clamped(self, raw, expected):
        assert _parse_timeout_seconds(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "abc", "-5", "0", "nan", "inf", "-inf", None, "10s", "1,5",
    ])
    def test_invalid_values_mean_default(self, raw):
        assert _parse_timeout_seconds(raw) is None


class _Recorder:
    """Stands in for run_with_timeout() and records the timeout it got."""

    def __init__(self):
        self.timeout = None

    def __call__(self, func, args=(), kwargs=None, timeout=None):
        self.timeout = timeout
        return "ok", [[{"type": "latex", "content": "x"}]]


@pytest.fixture
def client():
    return app_module.app.test_client()


class TestTimeoutReachesRunner:
    @pytest.mark.parametrize("route", ["/", "/export/latex"])
    @pytest.mark.parametrize("posted, expected", [
        ("20", 20), ("999", 30), ("0", 7), ("abc", 7), ("", 7),
    ])
    def test_value_used_on_both_routes(self, client, monkeypatch, route, posted, expected):
        recorder = _Recorder()
        monkeypatch.setattr(app_module, "run_with_timeout", recorder)
        monkeypatch.setattr(app_module, "DEFAULT_TIMEOUT_SECONDS", 7)

        client.post(route, data={"code": "a = 1\n", "timeout": posted})
        assert recorder.timeout == expected

    def test_missing_field_uses_default(self, client, monkeypatch):
        recorder = _Recorder()
        monkeypatch.setattr(app_module, "run_with_timeout", recorder)
        client.post("/", data={"code": "a = 1\n"})
        assert recorder.timeout == app_module.DEFAULT_TIMEOUT_SECONDS


class TestTimeoutField:
    def test_field_shows_default_on_first_load(self, client):
        html = client.get("/").get_data(as_text=True)
        assert 'name="timeout"' in html
        assert 'id="timeout-input"' in html
        assert f'value="{app_module.DEFAULT_TIMEOUT_SECONDS}"' in html

    def test_entered_value_stays_in_the_field(self, client):
        html = client.post("/", data={"code": "a = 1\n", "timeout": "25"}).get_data(as_text=True)
        assert 'name="timeout" form="calc-form" value="25"' in html

    def test_invalid_entry_stays_in_the_field_but_default_is_used(self, client):
        html = client.post("/", data={"code": "a = 1\n", "timeout": "abc"}).get_data(as_text=True)
        assert 'name="timeout" form="calc-form" value="abc"' in html
        assert "took longer than" not in html  # no error result

    def test_html_in_field_is_escaped(self, client):
        html = client.post("/", data={"code": "a = 1\n", "timeout": '"><script>x</script>'}).get_data(as_text=True)
        assert "<script>x</script>" not in html


class TestRealTimeout:
    def test_set_timeout_really_applies_and_is_named_in_message(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "evaluate_code", _hang)  # sleeps 5 s

        start = time.time()
        response = client.post("/", data={"code": "a = 1\n", "timeout": "1"})
        elapsed = time.time() - start

        text = response.get_data(as_text=True)
        assert 1 <= elapsed < 4
        assert "longer than 1s" in text
        assert "Settings" in text and "max. 30s" in text

    def test_no_increase_hint_at_the_maximum(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_TIMEOUT_MAX_SECONDS", 1)
        monkeypatch.setattr(app_module, "evaluate_code", _hang)

        response = client.post("/export/latex", data={"code": "a = 1\n", "timeout": "5"})
        assert b"longer than 1s" in response.data
        assert b"increase the timeout" not in response.data
