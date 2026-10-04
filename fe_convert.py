"""
Convert a stored Formula E session into the replay's data format.

Input is the raw documents Formula E's live timing feed keeps for a finished session (see
fe_feed.py). They are final-state snapshots, not a recording, so the replay is rebuilt from the
lap records: every lap of every car has its three sector times and the clock time each sector
ended. From those this module reconstructs
  * the timing tower: positions, gaps and intervals at every sector crossing, sector and lap
    colours, personal bests, pit stop counts
  * Attack Mode, from the per-lap flag
  * race control messages and the track status they imply
  * estimated car positions along the circuit path (cars are placed by how far through each
    sector they are, so the map is an estimate, not tracking data)

Pure functions only: no network, no files.
"""
import re
import statistics
from datetime import datetime, timezone

DAY_MS = 86_400_000
LEAD_MS = 10 * 60 * 1000       # the replay starts this long before the race start
CLEAR_AFTER_MS = 4000          # sector colours linger this long after a lap ends
SAMPLE_MS = 1000               # estimated car positions: one sample a second
PATH_SCALE = 10_000            # circuit path (0..1) -> integer coordinates

# Approximate team colours (matched against the lower-cased team name); not official values
TEAM_COLOURS = [
    ("jaguar", "1E90FF"), ("porsche", "C9CDD6"), ("nissan", "E8003D"), ("mahindra", "FF6A3D"),
    ("penske", "D4AF37"), ("envision", "2DBE60"), ("citro", "FF4FA3"), ("andretti", "6C7BFF"),
    ("cupra", "C77D3A"), ("kiro", "C77D3A"), ("lola", "19C2C9"), ("maserati", "2F6BFF"),
    ("mclaren", "FF8000"), ("nio", "00B4D8"), ("ds ", "D4AF37"),
]
FALLBACK_COLOURS = ["E6C229", "8E7DFF", "36C5F0", "F2545B", "7BD389", "B5838D", "F4A261", "9AA5B1"]


# ---------- small helpers ----------

def parse_ms(text):
    """'1:17.822', '28.335' or '15:05:08.082' -> milliseconds."""
    secs = 0.0
    for part in str(text).split(":"):
        secs = secs * 60 + float(part)
    return secs * 1000


def fmt_time(ms):
    """Lap/sector time like the F1 feed: '1:17.822' or '28.335'."""
    minutes, seconds = divmod(ms / 1000, 60)
    return f"{int(minutes)}:{seconds:06.3f}" if minutes >= 1 else f"{seconds:.3f}"


def fmt_gap(ms):
    return "+" + fmt_time(ms)


def iso(epoch_ms):
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def team_colour(team, fallbacks):
    name = (team or "").lower()
    for key, colour in TEAM_COLOURS:
        if key in name:
            return colour
    return fallbacks.setdefault(name, FALLBACK_COLOURS[len(fallbacks) % len(FALLBACK_COLOURS)])


def _title(name):
    return (name or "").strip().title()


# ---------- reading the raw documents ----------

def read_cars(raw):
    """{car number: info} for every car in the session, in grid order where known."""
    res = raw["session_results"]
    entries = raw.get("session_entry", {}).get("entry", {})
    grid = {p["number"]: int(p.get("grid_position") or 0) for p in res.get("lapchart", {}).get("participants", [])}
    fallbacks = {}
    cars = {}
    for p in res["analysis"]["participants"]:
        num = p["number"]
        drv = (p.get("drivers") or [{}])[0]
        ent = entries.get(num, {}) if isinstance(entries.get(num), dict) else {}
        last = ent.get("lastname") or drv.get("surname") or ""
        first = ent.get("firstname") or drv.get("firstname") or ""
        team = ent.get("team") or p.get("team") or ""
        cars[num] = {
            "num": num,
            "tla": (ent.get("driver") or last[:3] or num).upper(),
            "first": _title(first),
            "last": _title(last),
            "team": team,
            "colour": team_colour(team, fallbacks),
            "grid": grid.get(num) or 99,
            "laps": sorted(p.get("laps") or [], key=lambda lap: lap["number"]),
        }
    return cars


def local_midnight(session):
    offset = session.get("UtcOffsetMin", 0) * 60_000
    return ((session["date"] + offset) // DAY_MS) * DAY_MS - offset


def crossings(car, base):
    """Timing-line crossings for a car: [(epoch ms, loop, sector 0..2, lap, sector ms)].

    Loop numbers count every timing point passed: loop 3*(lap-1) + sector + 1.
    """
    out, prev = [], None
    for lap in car["laps"]:
        sectors = lap.get("sector_times") or []
        if len(sectors) != 3:  # incomplete record: use the lap end only
            sectors = [{"index": 3, "time": lap["time"], "hour": lap["hour"]}]
        for s in sectors:
            t = base + parse_ms(s["hour"])
            if prev is not None and t < prev - DAY_MS / 2:  # the clock ran past midnight
                t += DAY_MS
            prev = t
            idx = int(s["index"]) - 1
            out.append((t, 3 * (lap["number"] - 1) + idx + 1, idx, lap, parse_ms(s["time"])))
    return out


def race_start(cars, base):
    """Epoch ms of the start: the first lap ends at 'hour', and its elapsed time began at the start."""
    starts = []
    for car in cars.values():
        if car["laps"]:
            lap = car["laps"][0]
            starts.append(base + parse_ms(lap["hour"]) - parse_ms(lap["session_elapsed"]))
    return statistics.median(starts)


# ---------- race control ----------

def race_control(raw, to_ms, lap_at):
    """(messages for the replay, track status stream entries)."""
    log = (raw.get("race_control", {}).get("raceControlMessages", {}) or {}).get("log", {}) or {}
    items = sorted(((int(k), v.get("message", "")) for k, v in log.items()), key=lambda kv: kv[0])
    messages, status, zones = [], [], set()
    now = ("1", "AllClear")
    status.append([0, {"Status": now[0], "Message": now[1]}])

    def turns(text):
        return {t.strip() for t in text.split(",") if t.strip()}

    for epoch, msg in items:
        t = to_ms(epoch)
        if t < 0:
            continue
        up = msg.upper()
        flag = None
        new = now
        if "FULL COURSE YELLOW" in up:
            flag, new = "YELLOW", ("6", "Full course yellow")
        elif "SAFETY CAR" in up:
            new = ("4", "Safety car") if not any(w in up for w in ("IN THIS LAP", "ENDING", "WITHDRAWN", "IN AT")) else ("1", "AllClear")
        elif "CHEQUERED" in up:  # checked before RED FLAG: "CHEQUERED FLAG" contains those letters
            flag = "CHEQUERED"
        elif re.search(r"\bRED FLAG", up):
            flag, new = "RED", ("5", "Red flag")
        elif "DOUBLE YELLOW" in up or up.startswith("YELLOW AT TURN"):
            flag = "DOUBLE YELLOW" if "DOUBLE" in up else "YELLOW"
            zones |= turns(up.split("TURN", 1)[1])
            new = ("2", "Yellow") if zones else ("1", "AllClear")
        elif "TRACK CLEAR" in up:
            flag = "CLEAR"
            zones -= turns(up.split("TURN", 1)[1]) if "TURN" in up else zones
            new = ("2", "Yellow") if zones else ("1", "AllClear")
        elif "GREEN FLAG" in up:
            flag, zones, new = "GREEN", set(), ("1", "AllClear")
        elif "BLACK AND WHITE" in up:
            flag = "BLACK AND WHITE"
        category = "SafetyCar" if "SAFETY CAR" in up else ("Flag" if flag else "Other")
        messages.append({"Utc": iso(epoch), "t": t, "Message": msg.strip(), "Category": category,
                         **({"Flag": flag} if flag else {}), **({"Mode": "SC"} if category == "SafetyCar" else {}),
                         "Lap": lap_at(t)})
        if new != now:
            now = new
            status.append([t, {"Status": new[0], "Message": new[1]}])
    return messages, status


# ---------- the conversion ----------

def convert(raw, lead_ms=LEAD_MS):
    res = raw["session_results"]
    session = raw["__session"]
    cars = read_cars(raw)
    timed = {n: c for n, c in cars.items() if c["laps"]}
    if not timed:
        raise ValueError("This session has no lap data to replay")

    base = local_midnight(session)
    start = race_start(timed, base)
    t0 = start - lead_ms

    def to_ms(epoch):
        return int(round(epoch - t0))

    cls = {c["number"]: c for c in res.get("classification", {}).get("classification", [])}
    status_doc = raw.get("session_status", {}).get("status", {})
    total_laps = int(status_doc.get("finalLaps") or max(len(c["laps"]) for c in timed.values()))

    # crossing times per car and loop, and the events to replay in time order
    events, cross, last_cross = [], {}, {}
    for n, car in timed.items():
        cross[n] = {}
        for t, loop, sidx, lap, sms in crossings(car, base):
            cross[n][loop] = t
            events.append((t, 1, loop, n, sidx, lap, sms))
            if sidx == 2:
                events.append((t + CLEAR_AFTER_MS, 2, loop, n, sidx, lap, sms))
            last_cross[n] = t
    for n, c in cls.items():
        if n in timed and c.get("not_finished") in (True, "True"):
            events.append((last_cross[n] + 3000, 3, 0, n, 0, None, 0))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    # when the leader completed each lap (the first car over the line)
    leader_lap_t = {}
    for n in timed:
        for loop, t in cross[n].items():
            if loop % 3 == 0:
                k = loop // 3
                leader_lap_t[k] = min(t, leader_lap_t.get(k, t))
    leader_ts = sorted(leader_lap_t.items())

    def lap_at(t_ms):
        done = sum(1 for _, lt in leader_ts if to_ms(lt) <= t_ms)
        return max(1, min(done + 1, total_laps))

    streams = {k: [] for k in ("TimingData", "TimingAppData", "TimingStats", "DriverList", "LapCount",
                                "TrackStatus", "SessionStatus", "WeatherData", "AttackMode")}
    end_epoch = max(e[0] for e in events)

    # ---- initial state at t = 0
    order_grid = sorted(timed, key=lambda n: timed[n]["grid"])
    streams["DriverList"].append([0, {n: {
        "RacingNumber": n, "BroadcastName": f"{c['first'][:1]} {c['last'].upper()}".strip(),
        "FullName": f"{c['first']} {c['last'].upper()}".strip(), "Tla": c["tla"], "Line": i + 1,
        "TeamName": c["team"], "TeamColour": c["colour"], "FirstName": c["first"], "LastName": c["last"],
        "Reference": "", "HeadshotUrl": ""} for i, (n, c) in enumerate(
            sorted(cars.items(), key=lambda kv: (kv[1]["grid"], kv[0])))}])
    streams["TimingAppData"].append([0, {"Lines": {n: {"RacingNumber": n, "Line": i + 1, "GridPos": str(timed[n]["grid"])}
                                                   for i, n in enumerate(order_grid)}}])
    shown = {}
    init_lines = {}
    for i, n in enumerate(order_grid):
        line = {"Position": str(i + 1), "Line": i + 1, "ShowPosition": True, "NumberOfLaps": 0, "NumberOfPitStops": 0,
                "GapToLeader": "", "IntervalToPositionAhead": {"Value": "", "Catching": False},
                "Retired": False, "Stopped": False}
        init_lines[n] = line
        shown[n] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in line.items()}
    streams["TimingData"].append([0, {"Lines": init_lines}])
    streams["LapCount"].append([0, {"CurrentLap": 1, "TotalLaps": total_laps}])
    streams["SessionStatus"].append([0, {"Status": "Inactive"}])
    streams["SessionStatus"].append([to_ms(start), {"Status": "Started"}])
    weather = raw.get("weather", {}).get("weather", {})
    if weather:
        streams["WeatherData"].append([0, {
            "AirTemp": str(weather.get("ambientTemperature", "")), "TrackTemp": str(weather.get("trackTemperature", "")),
            "Humidity": str(weather.get("humidity", "")), "WindSpeed": str(weather.get("windSpeed", "")), "Rainfall": "0"}])

    # ---- replay the crossings
    loops = {n: 0 for n in timed}
    last_t = {n: 0 for n in timed}
    pits = {n: 0 for n in timed}
    pb_sector = {n: [None, None, None] for n in timed}
    pb_lap = {n: None for n in timed}
    pb_lap_no = {n: None for n in timed}
    best_sector = [None, None, None]
    best_lap = None
    prev_interval = {n: None for n in timed}
    catching = {n: False for n in timed}
    last_lap_ms = {n: None for n in timed}
    stats_shown = {n: {} for n in timed}
    lap_vals = {n: ["", "", ""] for n in timed}
    retired = set()

    def order_now():
        return sorted(timed, key=lambda n: (-loops[n], last_t[n], timed[n]["grid"]))

    def gap_texts(n, order):
        """(gap to leader, interval to the car ahead, interval in ms) for a car at its latest loop."""
        idx = order.index(n)
        L = loops[n]
        if idx == 0 or L == 0:
            return "", "", None
        ahead, leader = order[idx - 1], order[0]
        if L not in cross[ahead] or L not in cross[leader]:
            return "", "", None
        ref = last_lap_ms[leader] or 90_000
        gap_ms = cross[n][L] - cross[leader][L]
        int_ms = cross[n][L] - cross[ahead][L]

        def text(ms):
            if ms >= ref * 0.98:
                k = int(ms // ref)
                return f"+{k} LAP" + ("S" if k > 1 else "")
            return fmt_gap(ms)
        return text(gap_ms), text(int_ms), int_ms

    def stats_update(t_ms):
        """Personal-best lap and sector ranks for every car; emit what changed."""
        lap_rank = sorted((v, n) for n, v in pb_lap.items() if v is not None)
        deltas = {}
        for n in timed:
            cur = {}
            if pb_lap[n] is not None:
                rank = 1 + sum(1 for v, _ in lap_rank if v < pb_lap[n])
                cur["PersonalBestLapTime"] = {"Value": fmt_time(pb_lap[n]), "Lap": pb_lap_no[n], "Position": rank}
            sec = {}
            for i in range(3):
                if pb_sector[n][i] is not None:
                    rank = 1 + sum(1 for m in timed if pb_sector[m][i] is not None and pb_sector[m][i] < pb_sector[n][i])
                    sec[str(i)] = {"Value": fmt_time(pb_sector[n][i]), "Position": rank}
            if sec:
                cur["BestSectors"] = sec
            if cur != stats_shown[n]:
                deltas[n] = cur
                stats_shown[n] = cur
        if deltas:
            streams["TimingStats"].append([t_ms, {"Lines": deltas}])

    for t, kind, loop, n, sidx, lap, sms in events:
        t_ms = to_ms(t)
        delta = {}
        if kind == 1:
            loops[n], last_t[n] = loop, t
            valid = lap.get("is_valid", True) is not False
            personal = overall = False
            if valid:
                personal = pb_sector[n][sidx] is None or sms < pb_sector[n][sidx]
                overall = best_sector[sidx] is None or sms < best_sector[sidx]
                if personal:
                    pb_sector[n][sidx] = sms
                if overall:
                    best_sector[sidx] = sms
            lap_vals[n][sidx] = fmt_time(sms)
            d = {"Sectors": {str(sidx): {"Value": fmt_time(sms), "PersonalFastest": personal, "OverallFastest": overall}}}
            if sidx == 2:
                lap_ms = parse_ms(lap["time"])
                last_lap_ms[n] = lap_ms
                lp = lo = False
                if valid:
                    lp = pb_lap[n] is None or lap_ms < pb_lap[n]
                    lo = best_lap is None or lap_ms < best_lap
                    if lp:
                        pb_lap[n], pb_lap_no[n] = lap_ms, lap["number"]
                    if lo:
                        best_lap = lap_ms
                if lap.get("crossing_pit_finish_lane"):
                    pits[n] += 1
                d["LastLapTime"] = {"Value": fmt_time(lap_ms), "PersonalFastest": lp, "OverallFastest": lo}
                if pb_lap[n] is not None:
                    d["BestLapTime"] = {"Value": fmt_time(pb_lap[n]), "Lap": pb_lap_no[n]}
                d["NumberOfLaps"] = lap["number"]
                d["NumberOfPitStops"] = pits[n]
                k = loop // 3
                if cross[n][loop] == leader_lap_t[k]:  # this car is the first over the line for this lap
                    nxt = min(k + 1, total_laps)
                    if streams["LapCount"][-1][1].get("CurrentLap") != nxt:
                        streams["LapCount"].append([t_ms, {"CurrentLap": nxt}])
                    if k == total_laps:
                        streams["SessionStatus"].append([t_ms, {"Status": "Finished"}])
            delta[n] = d
            changed_pb = personal or (sidx == 2 and valid and pb_lap_no[n] == lap["number"])
        elif kind == 2:
            # the lap summary has been on screen long enough: clear the sector colours
            if loops[n] == loop:
                delta[n] = {"Sectors": {str(i): {"Value": "", "PreviousValue": lap_vals[n][i],
                                                 "PersonalFastest": False, "OverallFastest": False} for i in range(3)}}
            changed_pb = False
        else:
            retired.add(n)
            delta[n] = {"Retired": True, "Stopped": True}
            changed_pb = False

        # positions, gaps and intervals for everyone
        order = order_now()
        for i, c in enumerate(order):
            gap, interval, int_ms = gap_texts(c, order)
            if kind == 1 and c == n and int_ms is not None and prev_interval[n] is not None:
                catching[n] = int_ms < prev_interval[n] - 100
            if kind == 1 and c == n:
                prev_interval[n] = int_ms
            new = {"Position": str(i + 1), "Line": i + 1,
                   "GapToLeader": gap, "IntervalToPositionAhead": {"Value": interval, "Catching": catching[c]}}
            cur = shown[c]
            for k, v in new.items():
                if cur.get(k) != v:
                    delta.setdefault(c, {})[k] = v
                    cur[k] = dict(v) if isinstance(v, dict) else v
        if delta:
            streams["TimingData"].append([t_ms, {"Lines": delta}])
        if kind == 1 and changed_pb:
            stats_update(t_ms)

    # ---- the finished state
    end_ms = to_ms(end_epoch) + 60_000
    streams["SessionStatus"].append([end_ms, {"Status": "Finalised"}])

    messages, track_status = race_control(raw, to_ms, lap_at)
    streams["TrackStatus"] = track_status

    attack_stream, attack_by_car = attack_mode(timed, base, start, to_ms)
    streams["AttackMode"] = attack_stream

    pit_stops = []
    for n, p in (raw.get("session_pit_info", {}).get("pitOuts", {}) or {}).items():
        if n in timed and p.get("lastPitHour"):
            t = to_ms(p["lastPitHour"])
            pit_stops.append({"num": n, "t": t, "lap": lap_at(t), "stop": f"{p.get('lastPitTime', 0) / 1000:.1f}",
                              "lane": f"{p.get('totalPitTime', 0) / 1000:.1f}"})
    pit_stops.sort(key=lambda p: p["t"])

    track, positions = circuit_and_positions(raw, timed, base, start, to_ms)

    local_year = datetime.fromtimestamp(session["date"] / 1000 + session.get("UtcOffsetMin", 0) * 60, tz=timezone.utc).year
    info = res.get("classification", {}).get("session", {})
    event = info.get("event_name", "")
    meeting = event.split(" - ", 1)[-1] if " - " in event else event
    if meeting[:5].strip().isdigit():  # '2026 Hankook London E-Prix' -> 'Hankook London E-Prix'
        meeting = meeting.split(" ", 1)[1]
    track_name = (raw.get("track_info", {}).get("track", {}) or {}).get("name", "")

    return {
        "meta": {
            "series": "fe", "season": local_year, "meeting": meeting or track_name or "Formula E",
            "session": session.get("name", ""), "track": track_name, "t0Utc": int(t0), "end": end_ms,
            "subtitle": f"Formula E · {local_year} · {session.get('name', '')} · lap-by-lap replay",
            "attackModeByCar": attack_by_car,
        },
        "audio": None,
        "streams": streams,
        "raceControl": messages,
        "radio": [],
        "pitStops": pit_stops,
        "positions": positions,
        "track": track,
    }


# ---------- Attack Mode ----------

def attack_mode(timed, base, start, to_ms):
    """The per-lap Attack Mode flag as activation windows: (stream entries, activations per car)."""
    changes, per_car = [], {}
    for n, car in timed.items():
        prev_end, used, active = start, 0, False
        for lap in car["laps"]:
            end = base + parse_ms(lap["hour"])
            flagged = bool(lap.get("attackMode"))
            if flagged and not active:
                used += 1
                changes.append((prev_end, n, True, used))
            elif not flagged and active:
                changes.append((prev_end, n, False, used))
            active = flagged
            prev_end = end
        if active:
            changes.append((prev_end, n, False, used))
        per_car[n] = used
    changes.sort(key=lambda c: c[0])
    stream = [[0, {"Lines": {n: {"Active": False, "Used": 0} for n in timed}}]]
    for t, n, active, used in changes:
        stream.append([max(0, to_ms(t)), {"Lines": {n: {"Active": active, "Used": used}}}])
    return stream, per_car


# ---------- circuit outline and estimated positions ----------

def circuit_and_positions(raw, timed, base, start, to_ms):
    cc = (raw.get("session_circuit_config") or {}).get("circuitConfig") or {}
    nums = [float(v) for v in (cc.get("centerPath") or "").strip(";").split(";") if v != ""]
    if len(nums) < 8:
        return {"x": [], "y": [], "rotation": 0, "corners": []}, {}
    xs, ys = nums[0::2], nums[1::2]
    n_pts = len(xs)
    sec_nums = [v for v in (cc.get("sectors") or "").strip(";").split(";") if v != ""]
    bounds = []
    for i in range(0, len(sec_nums) - 3, 4):  # id; first point; last point; colour
        bounds.append((int(sec_nums[i + 1]) - 1, int(sec_nums[i + 2]) - 1))
    bounds = bounds[:3]
    # the page draws y upwards, the circuit path is screen coordinates: flip y
    track = {"x": [round(x * PATH_SCALE) for x in xs], "y": [round(-y * PATH_SCALE) for y in ys],
             "rotation": 0, "corners": [], "estimated": True}
    if len(bounds) == 3:
        track["sectors"] = {"segments": [1, 1, 1], "ends": [b for _, b in bounds], "dir": 1}
        track["finish"] = bounds[2][1] % n_pts
    if len(bounds) != 3:
        return track, {}

    def point(idx):
        i = max(0.0, min(idx, n_pts - 1.0))
        lo = int(i)
        hi = min(lo + 1, n_pts - 1)
        f = i - lo
        return (round((xs[lo] + (xs[hi] - xs[lo]) * f) * PATH_SCALE), round(-(ys[lo] + (ys[hi] - ys[lo]) * f) * PATH_SCALE))

    positions = {}
    for n, car in timed.items():
        segs, prev = [], start
        for t, _, sidx, _, _ in crossings(car, base):
            if t > prev:
                segs.append((prev, t, bounds[sidx][0], bounds[sidx][1]))
            prev = max(prev, t)
        if not segs:
            continue
        ts, px, py = [], [], []
        si, t = 0, segs[0][0]
        while si < len(segs):
            s0, s1, a, b = segs[si]
            if t >= s1:
                si += 1
                continue
            idx = a + (b - a) * (t - s0) / (s1 - s0)
            x, y = point(idx)
            ts.append(to_ms(t)); px.append(x); py.append(y)
            t += SAMPLE_MS
        x, y = point(segs[-1][3])
        ts.append(to_ms(segs[-1][1])); px.append(x); py.append(y)
        positions[n] = {"t": ts, "x": px, "y": py}
    return track, positions
