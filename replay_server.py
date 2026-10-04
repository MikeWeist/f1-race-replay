"""
Serve the race replay page locally.

Usage (from the repo root, with any Python 3.7+; only the standard library is used):
    python replay_server.py            # then open http://localhost:8765
    python replay_server.py 8800       # pick a port
    python replay_server.py --open     # also open the browser (used by the launchers)

F1 doesn't send CORS headers, so the page can't fetch the commentary playlist/audio chunks
itself. Anything under /f1/ is fetched from livetiming.formula1.com/static/ and passed through.
"""
import http.server
import os
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from urllib.parse import quote, unquote

args = sys.argv[1:]
PORT = next((int(a) for a in args if a.isdigit()), 8765)
OPEN_BROWSER = "--open" in args
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replay")
F1_STATIC = "https://livetiming.formula1.com/static/"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        if self.path.startswith("/f1/"):
            return self.proxy(unquote(self.path[len("/f1/"):]))
        return super().do_GET()

    def proxy(self, path):
        req = urllib.request.Request(F1_STATIC + quote(path), headers={"User-Agent": "livef1-replay"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, ctype, body = r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            status, ctype, body = e.code, e.headers.get("Content-Type"), e.read()
        except (urllib.error.URLError, OSError) as e:
            self.send_error(502, str(e))
            return
        self.send_response(status)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; audio chunks would flood it


class Server(http.server.ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR lets a second server bind a port that's already in use,
    # so both "succeed" and fight over it. Only reuse addresses elsewhere.
    allow_reuse_address = sys.platform != "win32"


def start_server(port):
    # if the port is taken (say, a replay is already running), try the next few
    for p in range(port, port + 10):
        try:
            return Server(("127.0.0.1", p), Handler), p
        except OSError:
            continue
    sys.exit(f"Couldn't find a free port between {port} and {port + 9}.")


if __name__ == "__main__":
    server, port = start_server(PORT)
    url = f"http://localhost:{port}"
    print(f"Replay running at {url}")
    print("Leave this window open while you watch. Close it (or press Ctrl+C) to stop.")
    if OPEN_BROWSER:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
