"""
Play F1 team radio clips as they're published.

Usage (from the repo root, with the venv's python):
    python team_radio.py live                  # during a session (uses F1 TV token if present)
    python team_radio.py poll [session_path]   # backup: poll the static file (defaults to the Baku race)
    python team_radio.py replay <session_path> # play a finished session's clips

F1 TV token: log in at formula1.com, then save the `login-session` cookie value to ~/.f1tv_token

session_path looks like: 2026/2026-09-26_Azerbaijan_Grand_Prix/2026-09-26_Race/
"""
import asyncio
import base64
import ctypes
import json
import os
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin

import requests

STATIC_BASE = "https://livetiming.formula1.com/static/"
DEFAULT_SESSION = "2026/2026-09-26_Azerbaijan_Grand_Prix/2026-09-26_Race/"
CLIP_DIR = os.path.join(tempfile.gettempdir(), "f1_team_radio")
os.makedirs(CLIP_DIR, exist_ok=True)

clips = queue.Queue()
seen = set()


# ---------- playback ----------

def _mci(cmd):
    """Send a command to Windows' built-in media player (winmm). Plays MP3 with no extra installs."""
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    if err:
        raise RuntimeError(f"MCI error {err} for: {cmd}")


def play_mp3(path):
    _mci(f'open "{path}" type mpegvideo alias clip')
    try:
        _mci("play clip wait")
    finally:
        _mci("close clip")


def player():
    while True:
        url, label = clips.get()
        try:
            local = os.path.join(CLIP_DIR, url.rsplit("/", 1)[-1])
            if not os.path.exists(local):
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                with open(local, "wb") as f:
                    f.write(r.content)
            print(f"▶ {label}   ({clips.qsize()} queued)")
            play_mp3(local)
        except Exception as e:
            print(f"  could not play {url}: {e}")
        finally:
            clips.task_done()


def enqueue(url, utc=""):
    if url in seen:
        return
    seen.add(url)
    # File names look like LEC_16_20260924_161432.mp3 -> driver code + number
    parts = url.rsplit("/", 1)[-1].split("_")
    label = f"{parts[0]} #{parts[1]}" if len(parts) > 2 else url
    clips.put((url, f"{utc[11:19]}  {label}" if utc else label))


# ---------- sources ----------

def fetch_static_captures(session_path):
    url = urljoin(STATIC_BASE, session_path.strip("/") + "/TeamRadio.json")
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return []
    data = r.content.decode("utf-8-sig")  # F1 files start with a BOM
    captures = json.loads(data).get("Captures", [])
    if isinstance(captures, dict):
        captures = captures.values()
    base = urljoin(STATIC_BASE, session_path.strip("/") + "/")
    return [(urljoin(base, c["Path"]), c.get("Utc", "")) for c in captures]


def run_poll(session_path, interval=10, skip_existing=True):
    print(f"Polling {session_path} every {interval}s. Ctrl+C to stop.")
    first = True
    while True:
        try:
            for url, utc in fetch_static_captures(session_path):
                if first and skip_existing:
                    seen.add(url)  # don't replay the backlog when joining mid-session
                else:
                    enqueue(url, utc)
        except Exception as e:
            print(f"  poll error: {e}")
        first = False
        time.sleep(interval)


def run_replay(session_path):
    caps = fetch_static_captures(session_path)
    print(f"Replaying {len(caps)} clips from {session_path}")
    for url, utc in caps:
        enqueue(url, utc)
    clips.join()  # wait until every clip has finished playing


TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".f1tv_token")


def load_token():
    """Read the F1 TV token from ~/.f1tv_token (outside the repo so it can't be committed).

    Accepts either the bare token or the whole `login-session` cookie value from formula1.com.
    """
    if not os.path.exists(TOKEN_FILE):
        print("No F1 TV token found; connecting without login (F1 may withhold some data).")
        return None
    raw = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if raw.startswith("%7B") or raw.startswith("{"):
        raw = json.loads(unquote(raw))["data"]["subscriptionToken"]

    try:
        payload = raw.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expires = datetime.fromtimestamp(claims["exp"], timezone.utc)
        if expires < datetime.now(timezone.utc):
            print(f"F1 TV token EXPIRED at {expires:%Y-%m-%d %H:%M} UTC. Log in again and re-copy it.")
            sys.exit(1)
        print(f"Using F1 TV token (expires {expires:%Y-%m-%d %H:%M} UTC)")
    except (IndexError, ValueError, KeyError):
        print("Token file doesn't look like an F1 TV token; check what you pasted.")
        sys.exit(1)
    return raw


def run_live():
    from signalrcore_client import stream

    token = load_token()
    state = {"session_path": None, "pending": [], "first_radio": True}

    def on_feed(topic, data, ts):
        if topic == "SessionInfo" and data.get("Path"):
            state["session_path"] = data["Path"]
            print(f"Session: {data.get('Meeting', {}).get('Name', '')} {data.get('Name', '')}")
        elif topic == "TeamRadio":
            captures = data.get("Captures", [])
            if isinstance(captures, dict):
                captures = captures.values()
            if ts is None:
                # Initial state = clips from before we connected; don't replay the backlog
                for c in captures:
                    state["pending"].append((c["Path"], c.get("Utc", ""), True))
            else:
                for c in captures:
                    state["pending"].append((c["Path"], c.get("Utc", ""), False))

        if state["session_path"]:
            base = urljoin(STATIC_BASE, state["session_path"].strip("/") + "/")
            for rel, utc, backlog in state["pending"]:
                url = urljoin(base, rel)
                if backlog:
                    seen.add(url)
                else:
                    enqueue(url, utc)
            state["pending"].clear()

    print("Connecting to F1 live timing. Ctrl+C to stop.")
    while True:
        try:
            asyncio.run(stream(["SessionInfo", "TeamRadio"], on_feed, token))
            print("Connection ended; reconnecting in 5s...")
        except Exception as e:
            print(f"Connection error: {e}; reconnecting in 5s...")
        time.sleep(5)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("live", "poll", "replay"):
        print(__doc__)
        sys.exit(1)

    threading.Thread(target=player, daemon=True).start()
    mode = sys.argv[1]
    try:
        if mode == "live":
            run_live()
        elif mode == "poll":
            run_poll(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SESSION)
        else:
            run_replay(sys.argv[2])
    except KeyboardInterrupt:
        print("Bye.")
