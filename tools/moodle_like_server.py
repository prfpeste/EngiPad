# tools/moodle_like_server.py  <folder> <port>
"""Static file server that answers with the MIME types Moodle uses for
course files (checked 17.11.2026, Firefox, Moodle pluginfile.php).

Moodle does not know .mjs (text/plain) and .wasm (octet-stream); a test
against this server shows whether the web version copes with that.
.css/.svg/.png/.woff2 are estimates until checked on the real Moodle.
"""
import http.server
import os
import sys

MOODLE_TYPES = {
    ".mjs": "text/plain; charset=utf-8",
    ".wasm": "application/octet-stream",
    ".js": "application/x-javascript",
    ".whl": "application/zip",
    ".zip": "application/zip",
    ".json": "application/json",
    ".html": "text/html",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def guess_type(self, path):
        ext = os.path.splitext(path)[1].lower()
        return MOODLE_TYPES.get(ext, "application/octet-stream")

    def log_message(self, format, *args):  # keep test output quiet
        if os.environ.get("MOODLE_SERVER_LOG"):
            super().log_message(format, *args)


def make_server(folder: str, port: int, host: str = "127.0.0.1"):
    """Returns a ThreadingHTTPServer serving `folder` (port 0 = any free port)."""
    folder = os.path.abspath(folder)

    class _H(Handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=folder, **kwargs)

    return http.server.ThreadingHTTPServer((host, port), _H)


if __name__ == "__main__":
    server = make_server(sys.argv[1], int(sys.argv[2]))
    print(f"Serving {os.path.abspath(sys.argv[1])} on http://127.0.0.1:{server.server_address[1]}/")
    server.serve_forever()
