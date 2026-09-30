"""Tests for the local "Quit" button (POST /shutdown, app.py).

The real exit (os._exit) is NEVER triggered here: _schedule_exit() is
replaced by a recorder in every test that reaches it.
"""

import multiprocessing
import time

import pytest

import app as app_module


@pytest.fixture
def flask_app():
    return app_module.app


@pytest.fixture
def local(flask_app, monkeypatch):
    """App in local mode (as started by run.py) + recorded exit."""
    monkeypatch.setitem(flask_app.config, "ENGIPAD_LOCAL", True)
    calls = {"exit": 0, "children": 0}
    monkeypatch.setattr(app_module, "_schedule_exit", lambda *a, **k: calls.__setitem__("exit", calls["exit"] + 1))
    monkeypatch.setattr(app_module, "_terminate_children", lambda: calls.__setitem__("children", calls["children"] + 1))
    return calls


def _post(flask_app, token=None, host="127.0.0.1:5000", remote="127.0.0.1"):
    headers = {"X-Shutdown-Token": token} if token is not None else {}
    return flask_app.test_client().post(
        "/shutdown",
        headers={"Host": host, **headers},
        environ_overrides={"REMOTE_ADDR": remote},
    )


class TestServerModeHasNoQuit:
    def test_default_config_is_not_local(self, flask_app):
        assert flask_app.config["ENGIPAD_LOCAL"] is False

    def test_route_is_404_on_a_server(self, flask_app):
        token = flask_app.config["SHUTDOWN_TOKEN"]
        assert _post(flask_app, token).status_code == 404

    def test_button_is_not_rendered_on_a_server(self, flask_app):
        html = flask_app.test_client().get("/").get_data(as_text=True)
        assert 'id="exit-btn"' not in html
        assert flask_app.config["SHUTDOWN_TOKEN"] not in html


class TestLocalMode:
    def test_button_is_rendered_with_token_right_of_info(self, flask_app, local):
        html = flask_app.test_client().get("/").get_data(as_text=True)
        assert 'id="exit-btn"' in html
        assert 'data-icon="exit"' in html
        assert f'data-token="{flask_app.config["SHUTDOWN_TOKEN"]}"' in html
        assert html.index('data-icon="info"') < html.index('id="exit-btn"')

    def test_correct_request_stops_and_cleans_up(self, flask_app, local):
        response = _post(flask_app, flask_app.config["SHUTDOWN_TOKEN"])
        assert response.status_code == 200
        assert local == {"exit": 1, "children": 1}

    @pytest.mark.parametrize("token", [None, "", "wrong", "x" * 200])
    def test_missing_or_wrong_token_is_rejected(self, flask_app, local, token):
        assert _post(flask_app, token).status_code == 403
        assert local == {"exit": 0, "children": 0}

    def test_non_local_client_is_rejected(self, flask_app, local):
        response = _post(flask_app, flask_app.config["SHUTDOWN_TOKEN"], remote="203.0.113.7")
        assert response.status_code == 403
        assert local["exit"] == 0

    @pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:5000", "127.0.0.1.evil.com"])
    def test_foreign_host_header_is_rejected(self, flask_app, local, host):
        # DNS rebinding: the request arrives via a foreign name.
        response = _post(flask_app, flask_app.config["SHUTDOWN_TOKEN"], host=host)
        assert response.status_code == 403
        assert local["exit"] == 0

    @pytest.mark.parametrize("host", ["127.0.0.1:5000", "localhost:5000", "localhost", "[::1]:5000"])
    def test_local_host_names_are_accepted(self, flask_app, local, host):
        assert _post(flask_app, flask_app.config["SHUTDOWN_TOKEN"], host=host).status_code == 200

    def test_get_is_not_allowed(self, flask_app, local):
        assert flask_app.test_client().get("/shutdown").status_code == 405
        assert local["exit"] == 0

    def test_token_differs_between_app_instances(self):
        assert app_module.create_app().config["SHUTDOWN_TOKEN"] != app_module.create_app().config["SHUTDOWN_TOKEN"]


def _sleeper():
    time.sleep(30)


class TestTerminateChildren:
    def test_running_computation_processes_are_killed(self):
        process = multiprocessing.Process(target=_sleeper)
        process.start()
        try:
            assert process.is_alive()
            app_module._terminate_children()
            process.join(5)
            assert not process.is_alive()
        finally:
            if process.is_alive():
                process.kill()

    def test_no_children_is_fine(self):
        app_module._terminate_children()


class TestScheduleExit:
    def test_schedules_os_exit_zero_after_a_delay(self, monkeypatch):
        captured = {}

        class FakeTimer:
            def __init__(self, delay, func, args=()):
                captured.update(delay=delay, func=func, args=args)
                self.daemon = False

            def start(self):
                captured["started"] = True

        monkeypatch.setattr(app_module.threading, "Timer", FakeTimer)
        app_module._schedule_exit(0.4)
        assert captured["func"] is app_module.os._exit
        assert captured["args"] == (0,)
        assert captured["delay"] == 0.4
        assert captured["started"]
