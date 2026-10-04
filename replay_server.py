"""
Serve the race replay page locally.

Usage (from the repo root, with any Python 3.7+; only the standard library is used):
    python replay_server.py            # then open http://localhost:8765
    python replay_server.py 8800       # pick a port
    python replay_server.py --open     # also open the browser (used by the launchers)

F1 doesn't send CORS headers, so the page can't fetch the commentary playlist/audio chunks
itself. Anything under /f1/ is fetched from livetiming.formula1.com/static/ and passed through.

The race chooser in the page talks to three small endpoints:
    GET  /api/races   the replays on this computer and, where it can build them, F1's calendar
    GET  /api/build   the state of the build in progress, if any
    POST /api/build   build a race from the calendar (only where build_replay.py is present)
Opening the page with no race chosen redirects to the last one picked, else the newest replay.
"""
import collections
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, unquote, urlsplit

from race_catalog import Catalog

args = sys.argv[1:]
PORT = next((int(a) for a in args if a.isdigit()), 8765)
OPEN_BROWSER = "--open" in args
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "replay")
BUILD_SCRIPT = os.path.join(HERE, "build_replay.py")
F1_STATIC = "https://livetiming.formula1.com/static/"

# A shared copy (the zip) has no exporter, so it can only list what's already in it
catalog = Catalog(os.path.join(ROOT, "data"), can_build=os.path.exists(BUILD_SCRIPT))


class Builds:
    """Runs build_replay.py for one race at a time and keeps its last few informative log lines."""

    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    # what build_replay.py itself says as it works: shown as the one-line status
    STATUS = re.compile(r"^(Loading|Fetching|Locating|Wrote|mini-sectors|pit lane|commentary|no outline|couldn't)")

    @staticmethod
    def _informative(line):
        line = line.strip()
        # the library's own log lines and progress bars are noise next to the script's messages
        return bool(line) and " - livef1 - " not in line and "it/s" not in line and "s/it" not in line

    def start(self, race):
        with self.lock:
            if self.job and self.job["state"] == "running":
                return None
            job = {"id": int(time.time() * 1000), "season": race["season"], "location": race["location"],
                   "name": race["name"], "state": "running", "started": time.time(), "ended": None,
                   "status": "", "tail": collections.deque(maxlen=12)}
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                [sys.executable, "-u", BUILD_SCRIPT, str(race["season"]), race["location"], "Race"],
                cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=flags)
            self.job = job
        threading.Thread(target=self._pump, args=(job, proc), daemon=True).start()
        return self.snapshot()

    def _pump(self, job, proc):
        for line in proc.stdout:
            if self._informative(line):
                text = line.strip()[:160]
                job["tail"].append(text)
                if self.STATUS.match(text):
                    job["status"] = text
        job["state"] = "done" if proc.wait() == 0 else "failed"
        job["ended"] = time.time()

    def snapshot(self):
        job = self.job
        if not job:
            return None
        end = job["ended"] or time.time()
        tail = list(job["tail"])
        return {"id": job["id"], "season": job["season"], "location": job["location"], "name": job["name"],
                "state": job["state"], "elapsed": round(end - job["started"]),
                "line": job["status"], "tail": tail[-6:]}


builds = Builds()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # ---- helpers ----

    def send_json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def remembered_race(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return unquote(cookie["lastRace"].value) if "lastRace" in cookie else None

    # ---- routes ----

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/races":
            return self.send_json(200, {**catalog.races(), "build": builds.snapshot()})
        if url.path == "/api/build":
            return self.send_json(200, builds.snapshot() or {"state": "idle"})
        if url.path in ("/", "/index.html") and "data" not in parse_qs(url.query):
            default = catalog.default_file(self.remembered_race())
            if default:
                self.send_response(302)
                self.send_header("Location", "/?data=" + quote(default))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
        if self.path.startswith("/f1/"):
            return self.proxy(unquote(self.path[len("/f1/"):]))
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/build":
            return self.send_json(404, {"error": "Not found"})
        # a custom header makes a cross-site form post impossible (it would need a CORS preflight)
        if self.headers.get("X-Replay") != "1":
            return self.send_json(403, {"error": "Missing X-Replay header"})
        try:
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size) or b"{}")
            season, location = int(body["season"]), str(body["location"])
        except (ValueError, KeyError, TypeError):
            return self.send_json(400, {"error": "Send {\"season\": 2026, \"location\": \"...\"}"})
        race = catalog.buildable(season, location)
        if not race:
            return self.send_json(400, {"error": "That race isn't available to build"})
        job = builds.start(race)
        if not job:
            return self.send_json(409, {"error": "A build is already running", "build": builds.snapshot()})
        return self.send_json(202, job)

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
