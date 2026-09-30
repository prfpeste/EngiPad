"""Tests for core/safe_runner.py (timeout protection against expensive
computations) and its integration in app.py.

Deliberately uses artificially slow/failing test functions instead of an
actually slow SymPy computation: SymPy turned out to be surprisingly
robust (several attempts with high-degree polynomials/integrals all
finished in under 1.5s during development) -- a guaranteed reproducible,
fast test suite matters more than a "natural" example. The mechanism
itself is deliberately generic (kills ANY function that runs too long,
regardless of the cause).
"""

import time

import pytest

from core.safe_runner import run_with_timeout


def _fast():
    return 42


def _slow(seconds):
    time.sleep(seconds)
    return "done"


def _raises():
    raise ValueError("broken")


class TestRunWithTimeout:
    def test_fast_function_returns_ok(self):
        status, value = run_with_timeout(_fast, timeout=2)
        assert status == "ok"
        assert value == 42

    def test_slow_function_times_out(self):
        start = time.time()
        status, value = run_with_timeout(_slow, args=(5,), timeout=0.5)
        elapsed = time.time() - start

        assert status == "timeout"
        assert value is None
        # Must return AFTER the timeout, not after the child function's
        # full sleep duration -- that's the whole point.
        assert elapsed < 3

    def test_raising_function_returns_error_with_message(self):
        status, value = run_with_timeout(_raises, timeout=2)
        assert status == "error"
        assert "broken" in value

    def test_args_and_kwargs_are_passed_through(self):
        status, value = run_with_timeout(
            _slow, args=(0.1,), timeout=2
        )
        assert status == "ok"
        assert value == "done"


def _hang(*args, **kwargs):
    time.sleep(5)
    return []


class TestTimeoutIntegrationInApp:
    @pytest.fixture
    def client(self):
        from app import app
        return app.test_client()

    def test_normal_request_is_unaffected(self, client):
        response = client.post("/", data={"code": "a = 1+1\n", "precision": "0.01"})
        assert response.status_code == 200
        # Not just "Timeout": that word is also the label of the
        # timeout field in the Settings menu.
        assert "took longer than" not in response.get_data(as_text=True)

    def test_timeout_shows_clear_message_instead_of_hanging(self, client, monkeypatch):
        # _hang must be at MODULE level (not defined locally here):
        # multiprocessing.Process needs to be able to pickle the
        # function object to hand it to the child process -- a function
        # nested inside a test body wouldn't be picklable ("Can't pickle
        # local object").
        import app as app_module

        monkeypatch.setattr(app_module, "evaluate_code", _hang)
        monkeypatch.setattr(app_module, "DEFAULT_TIMEOUT_SECONDS", 0.5)

        start = time.time()
        response = client.post("/", data={"code": "a = 1\n", "precision": "0.01"})
        elapsed = time.time() - start

        assert response.status_code == 200
        assert "Timeout" in response.get_data(as_text=True)
        assert elapsed < 4  # well under the hanging function's full 5s

    def test_export_route_also_respects_timeout(self, client, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "evaluate_code", _hang)
        monkeypatch.setattr(app_module, "DEFAULT_TIMEOUT_SECONDS", 0.5)

        start = time.time()
        response = client.post(
            "/export/latex", data={"code": "a = 1\n", "precision": "0.01"}
        )
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 4
        assert b"Timeout" in response.data
