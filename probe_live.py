"""
Find out what F1's live feed gives with and without an F1 TV login.

Run it before a session and leave it running. It connects twice at once, without a token and
(if ~/.f1tv_token exists) with it, subscribes to every known topic, and counts for each topic
how many updates arrived and how many bytes. A comparison prints every minute and at the end.

    python probe_live.py --minutes 120      # a whole session (reconnects if the feed drops)
    python probe_live.py --no-login         # only the no-login connection
    python probe_live.py --token-info       # what the saved token says about itself, then exit

Only counts are kept (probe_live_<time>.json); the token is never printed or stored, and
neither is any data from the feed.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import requests

from signalrcore_client import stream

TOPICS = [
    "Heartbeat", "SessionInfo", "SessionStatus", "TrackStatus", "SessionData", "ExtrapolatedClock",
    "LapCount", "TimingData", "TimingDataF1", "TimingAppData", "TimingStats", "TopThree", "DriverList",
    "LapSeries", "TyreStintSeries", "CurrentTyres", "WeatherData", "WeatherDataSeries",
    "RaceControlMessages", "TeamRadio", "TlaRcm", "AudioStreams", "ContentStreams", "Position.z",
    "CarData.z", "PitLaneTimeCollection", "PitStop", "PitStopSeries", "DriverRaceInfo", "DriverTracker",
    "OvertakeSeries", "ChampionshipPrediction", "DriverScore", "SPFeed", "ArchiveStatus",
]
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".f1tv_token")
# claims that describe the plan, not the person
PLAN_CLAIMS = ("subscriptionStatus", "subscribedProduct", "country", "iss", "aud")


def read_token():
    """The saved token, or None. Accepts the bare token or the whole login-session cookie."""
    if not os.path.exists(TOKEN_FILE):
        return None
    raw = open(TOKEN_FILE, encoding="utf-8").read().strip()
    if raw.startswith("%7B") or raw.startswith("{"):
        raw = json.loads(unquote(raw))["data"]["subscriptionToken"]
    return raw


def claims_of(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def token_info():
    token = read_token()
    if not token:
        print(f"No token yet. Save it to {TOKEN_FILE} (see the README).")
        return
    c = claims_of(token)
    exp = datetime.fromtimestamp(c["exp"], timezone.utc)
    left = (exp - datetime.now(timezone.utc)).total_seconds() / 3600
    print(f"Token {'valid' if left > 0 else 'EXPIRED'}: expires {exp:%Y-%m-%d %H:%M} UTC ({left:.1f} h left)")
    print("Claims it contains:", ", ".join(sorted(c)))
    for k in PLAN_CLAIMS:
        if k in c:
            v = c[k]
            print(f"  {k}: {v if not isinstance(v, str) or len(v) < 40 else v[:12] + '…'}")


class Lane:
    """One connection and what it has received."""

    def __init__(self, name, token):
        self.name, self.token = name, token
        self.topics = {}                # topic -> {"snap": bytes of the first full state, "n": updates, "bytes": ..., "first": s}
        self.status = "connecting"
        self.started = time.time()
        self.connects = 0

    def on_feed(self, topic, data, ts):
        size = len(json.dumps(data))
        t = self.topics.setdefault(topic, {"snap": 0, "n": 0, "bytes": 0, "first": None})
        if ts is None:  # the state F1 sends when you subscribe
            t["snap"] = size
        else:
            t["n"] += 1
            t["bytes"] += size
            if t["first"] is None:
                t["first"] = round(time.time() - self.started)
        self.status = "connected"

    async def run(self, stop):
        delay = 2
        while not stop.is_set():
            self.connects += 1
            try:
                await asyncio.wait_for(stream(TOPICS, self.on_feed, self.token), timeout=None)
                self.status = "closed by F1"
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                self.status = f"refused (HTTP {code})" + (": the token was not accepted" if code in (401, 403) and self.token else "")
            except Exception as e:  # noqa: BLE001 - keep the probe alive whatever the network does
                self.status = f"dropped ({type(e).__name__})"
            if stop.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


def table(lanes, final=False):
    names = [l.name for l in lanes]
    out = [f"{'topic':<24}" + "".join(f"{n:>26}" for n in names)]
    out.append(f"{'':<24}" + "".join(f"{'snapshot B | updates | first s':>26}" for _ in names))
    for topic in TOPICS:
        cells = []
        for l in lanes:
            t = l.topics.get(topic)
            cells.append("-" if not t else f"{t['snap']:>8} | {t['n']:>6} | {t['first'] if t['first'] is not None else '-':>4}")
        if any(c != "-" for c in cells):
            out.append(f"{topic:<24}" + "".join(f"{c:>26}" for c in cells))
    out.append("status: " + "; ".join(f"{l.name}: {l.status} (connections: {l.connects})" for l in lanes))
    return "\n".join(out)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=2, help="how long to run (default 2)")
    ap.add_argument("--no-login", action="store_true", help="skip the connection that uses the token")
    ap.add_argument("--token-info", action="store_true", help="show the saved token's plan details and exit")
    args = ap.parse_args()
    if args.token_info:
        return token_info()

    lanes = [Lane("no login", None)]
    token = None if args.no_login else read_token()
    if token:
        c = claims_of(token)
        if c["exp"] < time.time():
            print("The saved token has expired; log in again and re-save it. Running without it.")
        else:
            lanes.append(Lane("with F1 TV login", token))
    elif not args.no_login:
        print(f"No token at {TOKEN_FILE}: running the no-login connection only.")

    stop = asyncio.Event()
    tasks = [asyncio.create_task(l.run(stop)) for l in lanes]
    end = time.time() + args.minutes * 60
    print(f"Probing for {args.minutes:g} minutes. Ctrl+C stops early.")
    try:
        last = time.time()
        while time.time() < end:
            await asyncio.sleep(1)
            if time.time() - last >= 60:
                last = time.time()
                print(f"\n--- {datetime.now():%H:%M:%S} ---\n{table(lanes)}")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    stop.set()
    for t in tasks:
        t.cancel()
    print(f"\n=== final ({datetime.now():%H:%M:%S}) ===\n{table(lanes, final=True)}")
    name = f"probe_live_{datetime.now():%Y%m%d_%H%M}.json"
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), name), "w") as f:
        json.dump({l.name: {"status": l.status, "connections": l.connects, "topics": l.topics} for l in lanes}, f, indent=1)
    print(f"Counts saved to {name}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
