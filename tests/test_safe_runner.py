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

        monkeypatch.setattr(app_module, "evaluate_workbook", _hang)
        monkeypatch.setattr(app_module, "DEFAULT_TIMEOUT_SECONDS", 0.5)

        start = time.time()
        response = client.post("/", data={"code": "a = 1\n", "precision": "0.01"})
        elapsed = time.time() - start

        assert response.status_code == 200
        assert "Timeout" in response.get_data(as_text=True)
        assert elapsed < 4  # well under the hanging function's full 5s

    def test_export_route_also_respects_timeout(self, client, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "evaluate_workbook", _hang)
        monkeypatch.setattr(app_module, "DEFAULT_TIMEOUT_SECONDS", 0.5)

        start = time.time()
        response = client.post(
            "/export/latex", data={"code": "a = 1\n", "precision": "0.01"}
        )
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 4
        assert b"Timeout" in response.data


def _big_result(size):
    return "x" * size


def _dies():
    import os
    os._exit(3)


class TestLargeResultsAndCrashes:
    """A result larger than the pipe buffer (about 64 KB) used to block:
    the child could not finish writing while the parent waited for it to
    exit, and the parent then reported a timeout. Seen in practice with
    three or more plots in one sheet."""

    def test_large_result_is_returned_quickly(self):
        start = time.time()
        status, value = run_with_timeout(_big_result, args=(2_000_000,), timeout=5)
        assert status == "ok"
        assert len(value) == 2_000_000
        assert time.time() - start < 3

    def test_child_that_dies_is_an_error_not_a_timeout(self):
        start = time.time()
        status, value = run_with_timeout(_dies, timeout=5)
        assert status == "error"
        assert "unexpectedly" in value
        assert time.time() - start < 3

    def test_three_plots_in_one_sheet(self):
        from app import app

        code = "\n".join(f"plot(sin({i}*x), x, 0, 10)" for i in (1, 2, 3, 4))
        start = time.time()
        body = app.test_client().post("/", data={"code": code}).get_data(as_text=True)
        assert body.count("data:image/png;base64,") == 4
        assert "took longer than" not in body
        assert time.time() - start < 8
