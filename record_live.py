"""
Record F1's live timing feed to disk, every session, unattended.

    python record_live.py                  # leave it running; it follows the sessions on its own
    python record_live.py --dir D:\\f1      # somewhere other than ./recordings

It connects to the live feed (with your F1 TV token from ~/.f1tv_token if there is one, which is what
makes F1 send car positions and telemetry), and writes every message it receives to
recordings/<session>.jsonl, one JSON object per line:

    {"w": <wall clock ms>, "topic": "TimingData", "data": {...}, "ts": "<F1's timestamp>"}
    {"w": ..., "topic": "TimingData", "data": {...}, "ts": null, "snap": true}   # state sent on connect

A new file starts whenever F1 moves to a new session. A session that was already over when the
recorder connected is not recorded (there would only be its final state). If the connection drops it
reconnects, and the fresh state it receives is kept in the file so the gap is visible.

Only one recorder runs at a time. The token is never written anywhere.
"""
import argparse
import asyncio
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone

import requests

from probe_live import TOPICS, TokenError, claims_of, read_token
from signalrcore_client import stream

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_TOPICS = {"Heartbeat"}          # a ping every few seconds, nothing in it
OVER = {"Finalised", "Ends"}         # SessionStatus values after which a session has nothing left to say
LOCK_PORT = 47831


def session_slug(path):
    """'2026/2026-10-04_Bahrain_Grand_Prix/2026-10-04_Race/' -> '2026-10-04_Bahrain_Grand_Prix__2026-10-04_Race'."""
    parts = [p for p in str(path).strip("/").split("/") if p]
    name = "__".join(parts[1:] or parts)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "session"


class Recorder:
    """Turns feed callbacks into session files. No network in here, so it can be tested."""

    def __init__(self, directory, clock=time.time, log=print):
        self.dir, self.clock, self.log = directory, clock, log
        self.path = None          # F1's path of the session being recorded
        self.status = None        # its SessionStatus
        self.file = None
        self.written = 0
        self.holding = True       # the connect-time snapshot lists topics in any order: wait for SessionInfo
        self.buffer = []
        os.makedirs(directory, exist_ok=True)

    @property
    def over(self):
        return self.status in OVER

    def file_path(self, path=None):
        return os.path.join(self.dir, session_slug(path or self.path) + ".jsonl")

    def new_connection(self):
        """Call before each (re)connect: the next messages are a fresh snapshot."""
        self.holding, self.buffer = True, []

    def on_feed(self, topic, data, ts):
        if topic == "SessionStatus" and isinstance(data, dict) and data.get("Status"):
            self.status = data["Status"]
        if topic == "SessionInfo" and isinstance(data, dict) and data.get("Path") and data["Path"] != self.path:
            self.switch(data["Path"], forget_status=not self.holding)
        msg = None
        if topic not in SKIP_TOPICS:
            msg = {"w": round(self.clock() * 1000), "topic": topic, "data": data, "ts": ts}
            if ts is None:
                msg["snap"] = True
        if self.holding:
            # the snapshot F1 sends on connect lists topics in any order. Keep it all until the first live
            # update says it is complete: only then is it known which session this is and whether it is over.
            if msg:
                self.buffer.append(msg)
            if ts is not None:
                self.holding = False
                self.flush_buffer()
            return
        if msg:
            self.write(msg)

    def switch(self, path, forget_status):
        self.close()
        self.path = path
        if forget_status:         # a live switch: the old session's status says nothing about this one
            self.status = None
        self.log(f"session: {path}")

    def flush_buffer(self):
        buffered, self.buffer = self.buffer, []
        for m in buffered:
            if self.path is not None:
                self.write(m)

    def write(self, msg):
        target = self.file_path()
        if self.over and (msg.get("snap") or not os.path.exists(target)):
            return  # the final state of a finished session, which is not worth a file of its own
        if self.file is None:
            self.file = open(target, "a", encoding="utf-8", buffering=1)
            self.log(f"recording to {target}")
        self.file.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.written += 1

    def close(self):
        if self.file:
            self.file.close()
            self.log(f"closed {self.file.name}: {self.written} messages, {os.path.getsize(self.file.name) / 1e6:.1f} MB")
            self.file, self.written = None, 0


def only_one_recorder(port=LOCK_PORT):
    """Hold a local port for as long as this process lives; a second recorder can't take it."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return None
    return s


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def token_for_this_connection():
    """The saved token if it is usable right now, else None (the feed still works without it)."""
    try:
        token = read_token()
    except TokenError as e:
        return None, f"{e} Recording without it."
    if not token:
        return None, "no token saved: recording without car positions and telemetry"
    left = (claims_of(token)["exp"] - time.time()) / 3600
    if left <= 0:
        return None, "the saved token has expired: recording without car positions and telemetry. Log in again and run probe_live.py --save-token"
    return token, f"token valid for {left:.0f} more hours"


async def run(rec):
    delay = 2
    while True:
        token, note = token_for_this_connection()
        print(f"{stamp()}  connecting ({note})", flush=True)
        rec.new_connection()
        started = time.time()
        try:
            await stream(TOPICS, rec.on_feed, token)
            print(f"{stamp()}  closed by F1", flush=True)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            print(f"{stamp()}  refused (HTTP {code})", flush=True)
        except Exception as e:  # noqa: BLE001 - a recorder has to outlive whatever the network does
            print(f"{stamp()}  dropped ({type(e).__name__})", flush=True)
        delay = 2 if time.time() - started > 120 else min(delay * 2, 60)
        await asyncio.sleep(delay)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.path.join(HERE, "recordings"), help="where session files go")
    args = ap.parse_args()
    lock = only_one_recorder()
    if lock is None:
        sys.exit("Another recorder is already running on this computer.")
    rec = Recorder(args.dir, log=lambda s: print(f"{stamp()}  {s}", flush=True))
    print(f"{stamp()}  recorder started; files go to {args.dir}", flush=True)
    try:
        asyncio.run(run(rec))
    except KeyboardInterrupt:
        pass
    finally:
        rec.close()
        print(f"{stamp()}  stopped", flush=True)


if __name__ == "__main__":
    main()
