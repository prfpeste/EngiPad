"""run.py: port selection (a busy port 5000 must not break the start)."""

import socket
import threading
import urllib.request

import pytest

import run


def _listen(port=0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(5)
    return sock


@pytest.fixture
def busy():
    socks = []
    yield socks
    for s in socks:
        s.close()


def _free_start():
    """A port that is free now with a few free ones after it."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestCreateServer:
    def test_uses_the_start_port_when_free(self):
        start = _free_start()
        server = run.create_server(start, tries=5)
        try:
            assert server.server_port == start
        finally:
            server.server_close()

    def test_skips_a_port_that_something_else_listens_on(self, busy):
        taken = _listen()
        busy.append(taken)
        start = taken.getsockname()[1]
        server = run.create_server(start, tries=10)
        try:
            assert server.server_port != start
            assert start < server.server_port < start + 10
        finally:
            server.server_close()

    def test_skips_several_busy_ports(self, busy):
        first = _listen()
        busy.append(first)
        start = first.getsockname()[1]
        for offset in (1, 2):
            try:
                busy.append(_listen(start + offset))
            except OSError:
                pytest.skip("neighbour port not available on this machine")
        server = run.create_server(start, tries=10)
        try:
            assert server.server_port >= start + 3
        finally:
            server.server_close()

    def test_error_when_no_port_is_free(self, busy):
        taken = _listen()
        busy.append(taken)
        start = taken.getsockname()[1]
        with pytest.raises(RuntimeError) as err:
            run.create_server(start, tries=1)
        assert "free port" in str(err.value)

    def test_server_answers_with_the_app(self):
        server = run.create_server(_free_start(), tries=5)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            with urllib.request.urlopen(url, timeout=10) as response:
                assert response.status == 200
                assert b"EngiPad" in response.read()
        finally:
            server.shutdown()
            server.server_close()


class TestPortFromEnvironment:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("ENGIPAD_PORT", raising=False)
        assert run.start_port_from_env() == 5000

    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("ENGIPAD_PORT", "5600")
        assert run.start_port_from_env() == 5600

    @pytest.mark.parametrize("raw", ["abc", "", "80", "70000", "-1"])
    def test_invalid_values_fall_back(self, monkeypatch, raw):
        monkeypatch.setenv("ENGIPAD_PORT", raw)
        assert run.start_port_from_env() == 5000


class TestRunServer:
    class _FakeServer:
        server_port = 5123

        def __init__(self):
            self.served = False
            self.closed = False

        def serve_forever(self):
            self.served = True

        def server_close(self):
            self.closed = True

    def test_browser_gets_the_port_that_is_really_used(self, monkeypatch):
        fake = self._FakeServer()
        opened = []

        class _Timer:
            def __init__(self, delay, func, args=()):
                self.func, self.args = func, args

            def start(self):
                opened.append(self.args[0])

        monkeypatch.setattr(run, "create_server", lambda *a, **k: fake)
        monkeypatch.setattr(run.threading, "Timer", _Timer)
        run.run_server(open_browser=True)
        assert opened == ["http://127.0.0.1:5123"]
        assert fake.served and fake.closed

    def test_no_browser_from_source(self, monkeypatch):
        fake = self._FakeServer()
        opened = []
        monkeypatch.setattr(run, "create_server", lambda *a, **k: fake)
        monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
        run.run_server(open_browser=False)
        assert opened == [] and fake.served

    def test_startup_failure_is_shown_and_exits(self, monkeypatch):
        shown = []

        def _boom(*a, **k):
            raise RuntimeError("no port")

        monkeypatch.setattr(run, "create_server", _boom)
        monkeypatch.setattr(run, "show_error", shown.append)
        with pytest.raises(SystemExit) as exit_info:
            run.run_server(open_browser=True)
        assert exit_info.value.code == 1
        assert shown == ["no port"]
