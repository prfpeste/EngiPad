import multiprocessing
import os
import sys
import threading
import time
import webbrowser

# A windowed build (PyInstaller --noconsole) has no console, so sys.stdout /
# sys.stderr are None. Give them a harmless sink so print()/logging never
# fail. Module level on purpose: worker processes import this file too.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

from app import app


def run_server(open_browser: bool, debug: bool):
    if open_browser:
        def _run():
            app.run(host="127.0.0.1", port=5000, debug=debug)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:5000")
        thread.join()
    else:
        app.run(host="127.0.0.1", port=5000, debug=debug)


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
    if frozen:
        run_server(open_browser=True, debug=False)
    else:
        run_server(open_browser=False, debug=False)
