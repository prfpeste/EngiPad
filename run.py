import multiprocessing
import os
import socket
import sys
import threading
import webbrowser

# A windowed build (PyInstaller --noconsole) has no console, so sys.stdout /
# sys.stderr are None. Give them a harmless sink so print()/logging never
# fail. Module level on purpose: worker processes import this file too.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

from werkzeug.serving import make_server

from app import app

HOST = "127.0.0.1"
DEFAULT_PORT = 5000
MAX_PORT_TRIES = 100  # 5000 ... 5099


def _port_answers(port: int) -> bool:
    """True if some program already accepts connections on this port.

    On Windows a second program can bind 127.0.0.1:<port> although another one
    already listens there, and the browser then talks to the wrong one -- so
    bind() alone is not a reliable test.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((HOST, port)) == 0


def create_server(start_port: int = DEFAULT_PORT, tries: int = MAX_PORT_TRIES, application=None):
    """Bind the first free port from start_port on and return the server.

    The socket is bound (and listening) when this returns, so the browser can
    be opened right away: there is no race between "server started" and
    "browser opened", and the port that is used is known before the browser
    gets its address.
    """
    last_port = min(start_port + tries - 1, 65535)
    for port in range(start_port, last_port + 1):
        if _port_answers(port):
            continue
        try:
            return make_server(HOST, port, application or app, threaded=True)
        except OSError:
            continue  # taken between the check and bind(), or not allowed
    raise RuntimeError(
        f"EngiPad could not find a free port between {start_port} and {last_port} "
        f"on {HOST}. Close other programs and try again."
    )


def show_error(message: str) -> None:
    """Make a startup error visible even without a console (Windows --noconsole)."""
    try:
        sys.__stderr__ and sys.__stderr__.write(message + "\n")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "EngiPad", 0x10)  # MB_ICONERROR
        except Exception:
            pass


def start_port_from_env() -> int:
    """ENGIPAD_PORT overrides the first port to try (default 5000)."""
    raw = os.environ.get("ENGIPAD_PORT", "").strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_PORT


def run_server(open_browser: bool):
    try:
        server = create_server(start_port_from_env())
    except RuntimeError as exc:
        show_error(str(exc))
        sys.exit(1)

    url = f"http://{HOST}:{server.server_port}"
    print(f"EngiPad is running at {url}")  # harmless without a console
    if open_browser:
        # The port is already listening, the request simply waits for serve_forever().
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    # Required for multiprocessing (used by core/safe_runner.py's
    # timeout protection) to work correctly in a PyInstaller --onefile
    # build -- without this, a frozen app can end up re-launching
    # itself instead of just spawning a worker process (most visible on
    # Windows). A no-op when running from source. Must be the very
    # first thing that happens.
    multiprocessing.freeze_support()

    # Started locally: enables the "Quit" button / POST /shutdown (see
    # app.py). NOT set when the app is imported by a WSGI server such as
    # Gunicorn (app:app) -- there the route answers 404.
    app.config["ENGIPAD_LOCAL"] = True

    frozen = getattr(sys, "frozen", False)
    run_server(open_browser=frozen)
