"""
Export a finished session into a JSON file the replay page can play back.

Usage (from the repo root, with the venv's python):
    python build_replay.py                       # Baku 2026 race
    python build_replay.py 2026 Monza Race       # any season / meeting / session
    python build_replay.py 2026 Monza Race --wait   # wait for F1 to publish complete data, then build

Writes replay/data/<season>_<meeting>_<session>.json. Every timestamp in the
file is milliseconds since the start of the session's data stream ("session ms").
"""
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime
from urllib.parse import urljoin

import requests

import livef1
from outline import outline_from_positions
from race_catalog import fetch_json as get_json

warnings.filterwarnings("ignore")

STATIC_BASE = "https://livetiming.formula1.com/static/"
OUT_DIR = os.path.join(os.path.dirname(__file__), "replay", "data")

# Only the TimingData fields the page shows. Mini-sector segments are most of the raw
# stream and aren't used, so they're dropped from Sectors.
TIMING_KEYS = {
    "Position", "Line", "ShowPosition", "GapToLeader", "IntervalToPositionAhead",
    "LastLapTime", "BestLapTime", "NumberOfLaps", "NumberOfPitStops",
    "InPit", "PitOut", "Retired", "Stopped", "KnockedOut", "Sectors", "Speeds",
}
SECTOR_KEYS = {"Value", "PreviousValue", "PersonalFastest", "OverallFastest"}


def utc_ms(s):
    # F1 timestamps have up to 7 fractional digits; Python wants at most 6
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.rstrip("Z"))
    fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in s else "%Y-%m-%dT%H:%M:%S"
    return int((datetime.strptime(s, fmt) - datetime(1970, 1, 1)).total_seconds() * 1000)


def offset_ms(s):
    h, m, rest = s.split(":")
    return int((int(h) * 3600 + int(m) * 60 + float(rest)) * 1000)


def fetch_stream(session_url, topic):
    """Raw .jsonStream as [(session_ms, dict), ...]."""
    r = requests.get(urljoin(session_url, f"{topic}.jsonStream"), timeout=60)
    if r.status_code != 200:
        print(f"  {topic}: not available")
        return []
    out = []
    for line in r.content.decode("utf-8-sig").splitlines():
        if len(line) > 12:
            out.append((offset_ms(line[:12]), json.loads(line[12:])))
    return out


def fetch_json(session_url, name):
    r = requests.get(urljoin(session_url, name), timeout=30)
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))


def indexed(v):
    """F1 sends arrays either whole (list) or as {"index": item} patches."""
    return enumerate(v) if isinstance(v, list) else ((int(k), x) for k, x in v.items())


def slim_sector(s):
    kept = {k: v for k, v in s.items() if k in SECTOR_KEYS}
    segs = {str(j): {"Status": g["Status"]} for j, g in indexed(s.get("Segments") or {}) if "Status" in g}
    if segs:
        kept["Segments"] = segs
    return kept


def slim_timing(entries):
    out = []
    for t, d in entries:
        lines = {}
        for num, line in (d.get("Lines") or {}).items():
            kept = {k: v for k, v in line.items() if k in TIMING_KEYS}
            if "Sectors" in kept:
                slim = {str(i): slim_sector(s) for i, s in indexed(kept["Sectors"]) if isinstance(s, dict)}
                slim = {i: s for i, s in slim.items() if s}
                if slim:
                    kept["Sectors"] = slim
                else:
                    del kept["Sectors"]
            if kept:
                lines[num] = kept
        if lines:
            out.append([t, {"Lines": lines}])
    return out


def commentary_start(playlist_url):
    """Milliseconds into the commentary audio where the broadcast actually begins.

    The archived stream starts when the session's data does, often long before the
    commentators go live, and pads that stretch (and the end, after they sign off) with
    tiny silent chunks: ~12.6 KB per 15 s, against 60 KB+ for real audio. Step through
    the chunk sizes to find the first real one, then narrow it down.
    """
    lines = requests.get(playlist_url, timeout=30).text.splitlines()
    durations = [float(l.split(":")[1].rstrip(",")) for l in lines if l.startswith("#EXTINF")]
    chunks = [urljoin(playlist_url, l) for l in lines if l and not l.startswith("#")]
    session = requests.Session()
    is_real = lambda i: int(session.head(chunks[i], timeout=30).headers.get("Content-Length", 0)) > 30_000
    if not chunks or is_real(0):
        return 0
    step = 20  # 5 minutes of audio
    hi = next((i for i in range(step, len(chunks), step) if is_real(i)), None)
    if hi is None:
        return None  # no commentary found
    lo = hi - step  # lo is filler, hi is real
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if is_real(mid):
            hi = mid
        else:
            lo = mid
    return round(sum(durations[:hi]) * 1000)


def locate_mini_sectors(timing, positions, track):
    """Find where each mini-sector ends on the track outline.

    F1 doesn't publish sector locations. Each time a car completes a mini-sector its
    segment status changes, so take the car's position at that moment, snap it to the
    nearest track outline point, and use the median over every pass.
    """
    import numpy as np

    tx, ty = np.array(track["x"]), np.array(track["y"])
    n = len(tx)
    pos = {k: tuple(np.array(v[c]) for c in ("t", "x", "y")) for k, v in positions.items()}
    counts, hits = {}, {}
    for t, d in timing:
        for num, line in (d.get("Lines") or {}).items():
            for i, s in indexed(line.get("Sectors") or {}):
                if not isinstance(s, dict):
                    continue
                for j, g in indexed(s.get("Segments") or {}):
                    counts[i] = max(counts.get(i, 0), j + 1)
                    # 2048/2049/2051 = completed (yellow/green/purple); 0 = reset, 2064 = pit lane
                    if g.get("Status") not in (2048, 2049, 2051) or num not in pos:
                        continue
                    pt, px, py = pos[num]
                    k = np.searchsorted(pt, t)
                    if k <= 0 or k >= len(pt) or pt[k] - pt[k - 1] > 3000:
                        continue
                    f = (t - pt[k - 1]) / (pt[k] - pt[k - 1])
                    x, y = px[k - 1] + (px[k] - px[k - 1]) * f, py[k - 1] + (py[k] - py[k - 1]) * f
                    hits.setdefault((i, j), []).append(int(((tx - x) ** 2 + (ty - y) ** 2).argmin()))
    if not counts:
        return None

    ends = []
    for i in sorted(counts):
        for j in range(counts[i]):
            v = np.array(hits.get((i, j), []))
            if not len(v):
                return None
            # median on a loop: measure offsets from one sample, wrapped to +-n/2
            diff = (v - v[0] + n // 2) % n - n // 2
            ends.append(int((v[0] + np.median(diff)) % n))
    # direction of travel along the outline: most steps between consecutive ends go this way
    fwd = sum(((b - a) % n) < n // 2 for a, b in zip(ends, ends[1:] + ends[:1]))
    return {"segments": [counts[i] for i in sorted(counts)], "ends": ends, "dir": 1 if fwd >= len(ends) / 2 else -1}


def locate_pit_lane(timing, positions, track, finish_idx):
    """Trace the pit lane from where cars actually drove during their pit stops.

    F1 doesn't publish the pit lane. TimingData flags InPit from pit entry until the car
    leaves its box, so take every car's positions around each stop, keep the points that
    are clearly off the racing line, and average them into one path along the track.
    """
    import numpy as np

    tx, ty = np.array(track["x"], float), np.array(track["y"], float)
    n = len(tx)
    windows = {}
    for t, d in timing:
        for num, line in (d.get("Lines") or {}).items():
            if "InPit" not in line:
                continue
            w = windows.setdefault(num, [])
            if line["InPit"]:
                w.append([t, None])
            elif w and w[-1][1] is None:
                w[-1][1] = t

    xs, ys, rel, off = [], [], [], []
    for num, spans in windows.items():
        if num not in positions:
            continue
        pt, px, py = (np.array(positions[num][c], float) for c in ("t", "x", "y"))
        for t_in, t_out in spans:
            # a real stop takes well under 2 minutes; longer means parked in the garage
            if t_out is None or t_out - t_in > 120_000:
                continue
            # pad the window: the pit entry road starts before the flag, the exit road runs after
            m = (pt >= t_in - 15_000) & (pt <= t_out + 20_000)
            for x, y in zip(px[m], py[m]):
                d2 = (tx - x) ** 2 + (ty - y) ** 2
                k = int(d2.argmin())
                xs.append(x); ys.append(y); off.append(d2[k] ** 0.5)
                # position along the lap relative to the finish line, so the pit lane
                # doesn't split where the outline wraps around
                rel.append((k - finish_idx + n // 2) % n - n // 2)
    if len(xs) < 50:
        return None

    # The windows also catch cars on track before/after their stop, sometimes running wide.
    # The pit lane is where nearly every sample is off the racing line (on-track stretches
    # manage 10-40%). How far off varies: the lane can run close to the track in places.
    xs, ys, rel, off = map(np.array, (xs, ys, rel, off))
    keep = []
    for r in range(rel.min(), rel.max() + 1):
        m = rel == r
        far = m & (off > 50)
        if m.sum() >= 5 and far.sum() / m.sum() >= 0.8 and np.median(off[far]) >= 60:
            keep.append(r)
    if not keep:
        return None
    # longest continuous run (small gaps allowed)
    runs, cur = [], [keep[0]]
    for r in keep[1:]:
        if r - cur[-1] <= 3:
            cur.append(r)
        else:
            runs.append(cur); cur = [r]
    runs.append(cur)
    run = max(runs, key=len)
    path = []
    for r in run:
        m = (rel == r) & (off > 50)
        path.append((float(np.median(xs[m])), float(np.median(ys[m])), r))
    if len(path) < 5:
        return None
    # light smoothing
    px = np.convolve([p[0] for p in path], np.ones(3) / 3, mode="same")
    py = np.convolve([p[1] for p in path], np.ones(3) / 3, mode="same")
    px[0], py[0], px[-1], py[-1] = path[0][0], path[0][1], path[-1][0], path[-1][1]

    # join each end to the nearest racing-line point so it visibly branches off the track
    def on_track(r):
        k = (finish_idx + r) % n
        return [round(float(tx[k])), round(float(ty[k]))]
    pts = [on_track(path[0][2] - 2)] + [[round(x), round(y)] for x, y in zip(px, py)] + [on_track(path[-1][2] + 2)]
    return {"x": [p[0] for p in pts], "y": [p[1] for p in pts]}


def flatten_messages(entries, key, t0):
    """RaceControlMessages / TeamRadio send a list first, then dicts keyed by index."""
    out = []
    for _, d in entries:
        items = d.get(key) or []
        if isinstance(items, dict):
            items = items.values()
        for item in items:
            if "Utc" in item:
                out.append({**item, "t": utc_ms(item["Utc"]) - t0})
    return sorted(out, key=lambda m: m["t"])


def wait_until_complete(season, meeting, session_name, poll_s=60, max_hours=12):
    """Block until F1 has published a session and marked its archive complete.

    Lets you start the build before the race and walk away: it checks the calendar every
    minute (the data path only appears once the session is under way), then waits for the
    archive to be marked complete, which is when the exporter's input is final.
    """
    wanted = meeting.lower()
    deadline = time.time() + max_hours * 3600
    last = None
    while time.time() < deadline:
        state = "waiting for the session to start"
        try:
            index = get_json(f"{STATIC_BASE}{season}/Index.json")
            for m in index.get("Meetings", []):
                if wanted in (m.get("Name") or "").lower() or wanted in (m.get("Location") or "").lower():
                    s = next((s for s in m.get("Sessions", []) if s.get("Name") == session_name), None)
                    if s and s.get("Path"):
                        status = get_json(f"{STATIC_BASE}{s['Path']}ArchiveStatus.json").get("Status")
                        if status == "Complete":
                            print(f"{datetime.now():%H:%M:%S}  F1 has published the complete data.")
                            return
                        state = f"data is arriving (archive status: {status or 'unknown'})"
                    break
            else:
                state = f"no meeting matching '{meeting}' in the {season} calendar yet"
        except (OSError, ValueError) as e:
            state = f"can't reach F1 ({type(e).__name__}); will retry"
        if state != last:
            print(f"{datetime.now():%H:%M:%S}  {state}")
            last = state
        time.sleep(poll_s)
    sys.exit(f"Gave up after {max_hours} hours without complete data for {meeting} {session_name}.")


def main(season=2026, meeting="Baku", session_name="Race"):
    print(f"Loading {season} {meeting} {session_name}...")
    session = livef1.get_session(season, meeting_identifier=meeting, session_identifier=session_name)
    session_url = urljoin(STATIC_BASE, session.path)

    # t0: the wall-clock time of stream offset 0, derived from Heartbeat (offset, Utc) pairs
    beats = fetch_stream(session_url, "Heartbeat")
    t0 = sorted(utc_ms(d["Utc"]) - t for t, d in beats)[len(beats) // 2]

    print("Fetching timing streams...")
    raw_timing = fetch_stream(session_url, "TimingData")
    streams = {
        "TimingData": slim_timing(raw_timing),
        "TimingAppData": [[t, d] for t, d in fetch_stream(session_url, "TimingAppData")],
        "DriverList": [[t, d] for t, d in fetch_stream(session_url, "DriverList")],
        "LapCount": [[t, d] for t, d in fetch_stream(session_url, "LapCount")],
        "TrackStatus": [[t, d] for t, d in fetch_stream(session_url, "TrackStatus")],
        "SessionStatus": [[t, d] for t, d in fetch_stream(session_url, "SessionStatus")],
        "WeatherData": [[t, d] for t, d in fetch_stream(session_url, "WeatherData")],
        "TimingStats": [[t, d] for t, d in fetch_stream(session_url, "TimingStats")],
        # races only: projected standings if the race finished now
        "ChampionshipPrediction": [[t, d] for t, d in fetch_stream(session_url, "ChampionshipPrediction")],
    }

    pit_stops = []
    for _, d in fetch_stream(session_url, "PitStopSeries"):
        for num, stops in (d.get("PitTimes") or {}).items():
            for stop in (stops.values() if isinstance(stops, dict) else stops):
                p = stop.get("PitStop") or {}
                if "Timestamp" in stop and p:
                    pit_stops.append({
                        "num": num, "t": utc_ms(stop["Timestamp"]) - t0, "lap": p.get("Lap"),
                        "stop": p.get("PitStopTime"), "lane": p.get("PitLaneTime"),
                    })
    pit_stops.sort(key=lambda s: s["t"])
    race_control = flatten_messages(fetch_stream(session_url, "RaceControlMessages"), "Messages", t0)
    radio = flatten_messages(fetch_stream(session_url, "TeamRadio"), "Captures", t0)
    for r in radio:
        r["url"] = urljoin(session_url, r.pop("Path"))

    print("Fetching car positions (this is the big one)...")
    pos = session.get_data("Position.z")
    pos = pos[(pos["X"] != 0) | (pos["Y"] != 0)]
    positions = {}
    for num, df in pos.groupby("DriverNo", observed=True):
        ts = [utc_ms(u) - t0 for u in df["Utc"]]
        positions[str(num)] = {
            "t": ts,
            "x": df["X"].round().astype(int).tolist(),
            "y": df["Y"].round().astype(int).tolist(),
        }

    print("Fetching car telemetry...")
    car = session.get_data("CarData.z")
    telemetry = {}
    for num, df in car.groupby("DriverNo", observed=True):
        telemetry[str(num)] = {
            "t": [utc_ms(u) - t0 for u in df["Utc"]],
            "speed": df["Speed"].round().astype(int).tolist(),
            "rpm": df["RPM"].round().astype(int).tolist(),
            "gear": df["GearNo"].astype(int).tolist(),
            "throttle": df["Throttle"].round().astype(int).tolist(),
            "brake": df["Brake"].round().astype(int).tolist(),
        }

    print("Fetching circuit outline...")
    try:
        circuit = session.meeting.circuit
        circuit._load_circuit_data()
        raw = circuit._raw_circuit_data
        track = {
            "x": raw["x"], "y": raw["y"], "rotation": raw.get("rotation", 0),
            "corners": [
                {"n": c["number"], "x": c["trackPosition"]["x"], "y": c["trackPosition"]["y"]}
                for c in raw.get("corners", [])
            ],
        }
    except Exception as e:  # noqa: BLE001 - the map service lacks new circuits; don't lose the whole replay
        print(f"  no outline for this circuit ({e}); tracing it from where the cars drove instead")
        traced = outline_from_positions(positions)
        if traced is None:
            print("  couldn't trace it either: the replay will have no track map")
            traced = ([], [])
        track = {"x": traced[0], "y": traced[1], "rotation": 0, "corners": [], "derived": True}

    print("Locating sectors on the track...")
    # timing offsets -> session ms are already the same clock as positions
    track["sectors"] = locate_mini_sectors(raw_timing, positions, track) if track["x"] else None
    if track["sectors"]:
        print(f"  mini-sectors per sector: {track['sectors']['segments']}")
        # the last mini-sector ends at the timing line: that's the start/finish line
        track["finish"] = track["sectors"]["ends"][-1]
        pit = locate_pit_lane(raw_timing, positions, track, track["finish"])
        # the path is ordered along the outline; flip it if cars drive the other way
        if pit and track["sectors"]["dir"] < 0:
            pit = {"x": pit["x"][::-1], "y": pit["y"][::-1]}
        track["pit"] = pit
        print(f"  pit lane: {len(pit['x']) if pit else 'not found'}{' points' if pit else ''}")

    audio = None
    try:
        streams_info = fetch_json(session_url, "AudioStreams.json").get("Streams", [])
        en = next((s for s in streams_info if s.get("Language") == "en"), None)
        if en:
            # The playlist Path points at a master playlist; vs1 is the 70 kbps variant
            folder = en["Path"].rsplit("/", 1)[0] + "/"
            audio = {
                "name": en["Name"],
                "playlist": session.path + folder + "vs1_stream.m3u8",
                "start": utc_ms(en["Utc"]) - t0,
            }
            live_from = commentary_start(urljoin(STATIC_BASE, audio["playlist"]))
            if live_from:
                audio["liveFrom"] = audio["start"] + live_from
                print(f"  commentary starts {live_from / 60000:.1f} min into the audio")
    except Exception as e:
        print(f"  no commentary audio: {e}")

    data = {
        "meta": {
            "season": season,
            "meeting": session.meeting.name,
            "session": session.name,
            "t0Utc": t0,
            "end": max(t for s in streams.values() for t, _ in s[-1:]) if streams else 0,
        },
        "audio": audio,
        "streams": streams,
        "raceControl": race_control,
        "radio": radio,
        "pitStops": pit_stops,
        "positions": positions,
        "track": track,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    slug = re.sub(r"\W+", "_", f"{season}_{meeting}_{session_name}").strip("_")
    # Telemetry is as big as everything else combined, so it's a separate file the page
    # loads in the background
    for name, payload in [(f"{slug}.json", data), (f"{slug}_car.json", telemetry)]:
        out = os.path.join(OUT_DIR, name)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        print(f"Wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    wait = "--wait" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--wait"]
    if args:
        season, meeting, session_name = int(args[0]), args[1], args[2]
        if wait:
            wait_until_complete(season, meeting, session_name)
        main(season, meeting, session_name)
    else:
        main()
