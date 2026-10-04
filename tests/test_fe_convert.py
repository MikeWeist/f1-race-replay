"""Tests for fe_convert, using a small synthetic race where every right answer is known.

Three cars, three laps, 15:00:00 local start (UTC+0 so local midnight is a round number):
  car 1  laps of 60 s                          grid 1
  car 2  laps of 61, 55, 58 s                  grid 2  (overtakes car 1 on lap 2)
  car 3  laps of 70, 70 s, then retires        grid 3
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fe_convert as fc  # noqa: E402

MIDNIGHT = 1786838400000            # 2026-08-16T00:00:00Z
START = 15 * 3600 * 1000            # 15:00:00 as ms after midnight


def hour(ms):
    s = ms / 1000
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"


def car_laps(sector_ms_per_lap, attack=()):
    """Build lap records from [(s1, s2, s3), ...] durations in ms, starting at the race start."""
    laps, t = [], START
    for i, (a, b, c) in enumerate(sector_ms_per_lap, 1):
        ends = [t + a, t + a + b, t + a + b + c]
        total = a + b + c
        laps.append({
            "number": i, "time": fc.fmt_time(total), "hour": hour(ends[2]),
            "session_elapsed": fc.fmt_time(ends[2] - START), "is_valid": i != 1,
            "attackMode": i in attack, "crossing_pit_finish_lane": False,
            "sector_times": [{"index": k + 1, "time": fc.fmt_time(d), "hour": hour(e)}
                             for k, (d, e) in enumerate(zip((a, b, c), ends))],
        })
        t = ends[2]
    return laps


def build_raw(**over):
    cars = {
        "1": car_laps([(20000, 20000, 20000)] * 3, attack=(2, 3)),
        "2": car_laps([(20000, 20000, 21000), (18000, 18000, 19000), (19000, 19000, 20000)]),
        "3": car_laps([(23000, 23000, 24000)] * 2),
    }
    log = {
        MIDNIGHT + START + 10_000: {"date": MIDNIGHT + START + 10_000, "message": "YELLOW AT TURN 1, 2"},
        MIDNIGHT + START + 20_000: {"date": MIDNIGHT + START + 20_000, "message": "TRACK CLEAR AT TURN 1"},
        MIDNIGHT + START + 30_000: {"date": MIDNIGHT + START + 30_000, "message": "TRACK CLEAR AT TURN 2"},
        MIDNIGHT + START + 170_000: {"date": MIDNIGHT + START + 170_000, "message": "CHEQUERED FLAG"},
    }
    raw = {
        "__session": {"name": "Race", "date": MIDNIGHT + START, "UtcOffsetMin": 0},
        "session_results": {
            "analysis": {"participants": [{"number": n, "team": f"Team {n}", "laps": laps, "drivers": [
                {"firstname": "Driver", "surname": f"NUMBER{n}", "short_name": f"D.N{n}"}]} for n, laps in cars.items()]},
            "lapchart": {"participants": [{"number": n, "grid_position": int(n)} for n in cars]},
            "classification": {"classification": [
                {"number": "2", "position": 1, "status": "Classified", "not_finished": False, "laps": 3},
                {"number": "1", "position": 2, "status": "Classified", "not_finished": False, "laps": 3},
                {"number": "3", "position": 3, "status": "Not Classified", "not_finished": True, "laps": 2}],
                "session": {"event_name": "Round 1 - 2026 Test E-Prix"}},
        },
        "session_entry": {"entry": {n: {"driver": f"D{n}X", "firstname": "DRIVER", "lastname": f"NUMBER{n}",
                                        "team": "Jaguar TCS Racing" if n == "1" else f"Team {n}"} for n in cars}},
        "session_status": {"status": {"finalLaps": 3}},
        "race_control": {"raceControlMessages": {"log": {str(k): v for k, v in log.items()}}},
        "session_pit_info": {"pitOuts": {}},
        "track_info": {"track": {"name": "Testville"}},
        "weather": {"weather": {"ambientTemperature": 20, "trackTemperature": 30, "humidity": 50, "windSpeed": 3}},
        "session_circuit_config": {"circuitConfig": {
            # 12 points around a loop; sectors run points 1-4, 4-8, 8-12 (1-based, as the feed sends them)
            "centerPath": ";".join(f"{x:g};{y:g}" for x, y in [
                (0.1, 0.1), (0.2, 0.1), (0.3, 0.1), (0.4, 0.1), (0.4, 0.2), (0.4, 0.3),
                (0.4, 0.4), (0.3, 0.4), (0.2, 0.4), (0.1, 0.4), (0.1, 0.3), (0.1, 0.2)]) + ";",
            "sectors": "1;1;4;#6464d1;2;4;8;#ffff00;3;8;12;#ff0082;"}},
    }
    raw.update(over)
    return raw


def replay(stream_entries):
    """Merge a stream's updates the way the page does and return the state after each one."""
    def merge(t, s):
        if not isinstance(s, dict):
            return s
        t = t if isinstance(t, dict) else {}
        for k, v in s.items():
            t[k] = merge(t.get(k), v)
        return t
    state, out = {}, []
    for t, d in stream_entries:
        state = merge(state, d)
        out.append((t, __import__("copy").deepcopy(state)))
    return out


def final_lines(out):
    return replay(out["streams"]["TimingData"])[-1][1]["Lines"]


# ---------- helpers ----------

def test_parse_and_format():
    assert fc.parse_ms("1:17.822") == 77822
    assert fc.parse_ms("28.335") == 28335
    assert fc.parse_ms("15:05:08.082") == (15 * 3600 + 5 * 60 + 8.082) * 1000
    assert fc.fmt_time(77822) == "1:17.822"
    assert fc.fmt_time(28335) == "28.335"
    assert fc.fmt_gap(6000) == "+6.000"


def test_midnight_wrap_only_when_the_clock_really_wraps():
    car = {"laps": [
        {"number": 1, "time": "30.000", "hour": "23:59:50.000", "sector_times": [
            {"index": 1, "time": "10.000", "hour": "23:59:40.000"}, {"index": 2, "time": "10.000", "hour": "23:59:30.000"},
            {"index": 3, "time": "10.000", "hour": "23:59:50.000"}]},  # a data glitch: 10 s backwards
        {"number": 2, "time": "30.000", "hour": "00:00:20.000", "sector_times": [
            {"index": i, "time": "10.000", "hour": h} for i, h in ((1, "00:00:00.000"), (2, "00:00:10.000"), (3, "00:00:20.000"))]},
    ]}
    ts = [c[0] for c in fc.crossings(car, 0)]
    assert ts[1] - ts[0] == -10_000      # a small step back is left alone
    assert ts[3] > ts[2]                 # a real wrap past midnight adds a day
    assert ts[3] - ts[2] > 0 and ts[3] - ts[2] < 60_000


# ---------- the converted race ----------

def test_overall_shape_and_clocks():
    out = fc.convert(build_raw())
    assert out["meta"]["series"] == "fe"
    assert out["meta"]["meeting"] == "Test E-Prix"
    assert out["audio"] is None and out["radio"] == []
    start = next(t for t, s in out["streams"]["SessionStatus"] if s["Status"] == "Started")
    assert start == fc.LEAD_MS
    for name, stream in out["streams"].items():
        times = [t for t, _ in stream]
        assert times == sorted(times), f"{name} is not time-ordered"


def test_order_changes_and_final_positions_match_the_classification():
    out = fc.convert(build_raw())
    lines = final_lines(out)
    final = sorted(lines, key=lambda n: int(lines[n]["Position"]))
    assert final == ["2", "1", "3"]
    # positions are always a clean 1..N
    for _, state in replay(out["streams"]["TimingData"]):
        assert sorted(int(l["Position"]) for l in state["Lines"].values()) == [1, 2, 3]


def test_gaps_and_intervals_use_the_same_timing_point():
    lines = final_lines(fc.convert(build_raw()))
    assert lines["2"]["GapToLeader"] == "" and lines["2"]["IntervalToPositionAhead"]["Value"] == ""
    # car 1 finishes lap 3 at 180 s, the leader at 174 s
    assert lines["1"]["GapToLeader"] == "+6.000" and lines["1"]["IntervalToPositionAhead"]["Value"] == "+6.000"
    # car 3 stopped after lap 2: 140 s vs the leader's 116 s there, and car 1's 120 s
    assert lines["3"]["GapToLeader"] == "+24.000"
    assert lines["3"]["IntervalToPositionAhead"]["Value"] == "+20.000"


def test_fastest_laps_and_ranks():
    out = fc.convert(build_raw())
    stats = replay(out["streams"]["TimingStats"])[-1][1]["Lines"]
    assert stats["2"]["PersonalBestLapTime"] == {"Value": "55.000", "Lap": 2, "Position": 1}
    assert stats["1"]["PersonalBestLapTime"]["Position"] == 2
    assert stats["3"]["PersonalBestLapTime"]["Position"] == 3
    # lap 1 is invalid (standing start) so it never counts as a best
    assert stats["1"]["PersonalBestLapTime"]["Lap"] == 2
    lines = final_lines(out)
    assert lines["2"]["BestLapTime"]["Value"] == "55.000"


def test_sector_flags_mark_the_overall_best():
    out = fc.convert(build_raw())
    flagged = []
    for t, d in out["streams"]["TimingData"]:
        for n, line in d["Lines"].items():
            s0 = (line.get("Sectors") or {}).get("0")
            if n == "2" and s0 and s0.get("Value") == "18.000":
                flagged.append(s0)
    assert flagged and flagged[0]["OverallFastest"] and flagged[0]["PersonalFastest"]


def test_retired_car_is_flagged():
    lines = final_lines(fc.convert(build_raw()))
    assert lines["3"]["Retired"] is True
    assert lines["1"]["Retired"] is False and lines["2"]["Retired"] is False


def test_lap_count_and_session_status():
    out = fc.convert(build_raw())
    laps = [(s.get("CurrentLap"), s.get("TotalLaps")) for _, s in out["streams"]["LapCount"]]
    assert laps[0] == (1, 3) and laps[-1][0] == 3
    statuses = [s["Status"] for _, s in out["streams"]["SessionStatus"]]
    assert statuses == ["Inactive", "Started", "Finished", "Finalised"]


def test_attack_mode_windows_and_usage():
    out = fc.convert(build_raw())
    car1 = [(t, s["Lines"]["1"]) for t, s in out["streams"]["AttackMode"][1:] if "1" in s["Lines"]]
    assert [(v["Active"], v["Used"]) for _, v in car1] == [(True, 1), (False, 1)]
    start = out["meta"]["t0Utc"]  # not used for timing; the window starts when lap 1 ends (60 s after the start)
    assert car1[0][0] == fc.LEAD_MS + 60_000
    assert out["meta"]["attackModeByCar"]["1"] == 1 and out["meta"]["attackModeByCar"]["2"] == 0


def test_race_control_and_track_status():
    out = fc.convert(build_raw())
    flags = [m.get("Flag") for m in out["raceControl"]]
    assert flags == ["YELLOW", "CLEAR", "CLEAR", "CHEQUERED"]
    statuses = [s["Status"] for _, s in out["streams"]["TrackStatus"]]
    # clear -> yellow while zone 2 is still open -> clear; the chequered flag is not a red flag
    assert statuses == ["1", "2", "1"]
    assert "5" not in statuses
    assert out["raceControl"][0]["Lap"] == 1


def test_estimated_positions_follow_the_circuit_path():
    out = fc.convert(build_raw())
    track = out["track"]
    assert len(track["x"]) == 12
    assert track["y"][0] == -round(0.1 * fc.PATH_SCALE)     # y is flipped for the page
    assert track["sectors"]["ends"] == [3, 7, 11] and track["finish"] == 11
    p = out["positions"]["1"]
    assert p["t"] == sorted(p["t"])
    # at the race start the car is at the first path point; at its last crossing, at the last
    assert (p["x"][0], p["y"][0]) == (track["x"][0], track["y"][0])
    assert (p["x"][-1], p["y"][-1]) == (track["x"][11], track["y"][11])
    # the retired car stops sending positions after its last crossing
    assert out["positions"]["3"]["t"][-1] < out["positions"]["1"]["t"][-1]


def test_team_colours_are_stable_and_distinct_for_unknown_teams():
    cars = fc.read_cars(build_raw())
    assert cars["1"]["colour"] == "1E90FF"          # "Jaguar" is a known team
    assert cars["2"]["colour"] != cars["3"]["colour"]  # unknown teams get different fallbacks
    assert cars["1"]["tla"] == "D1X" and cars["1"]["last"] == "Number1"


def test_a_session_without_laps_is_refused():
    raw = build_raw()
    raw["session_results"]["analysis"]["participants"] = []
    try:
        fc.convert(raw)
    except ValueError as e:
        assert "no lap data" in str(e)
    else:
        raise AssertionError("expected a ValueError")
