"""replay/moments.js under node, fed small hand-made races so each moment is easy to reason about."""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MOMENTS_JS = os.path.join(os.path.dirname(HERE), "replay", "moments.js")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

START = 100_000          # lights out, session ms
END = 900_000
NAMES = {"1": "Alpha", "2": "Bravo", "3": "Charlie"}


def run_node(script, payload):
    code = f"const M = require({json.dumps(MOMENTS_JS)}); const x = JSON.parse(require('fs').readFileSync(0, 'utf8')); {script}"
    r = subprocess.run([NODE, "-e", code], input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def moments(d):
    return run_node("console.log(JSON.stringify(M.computeMoments(x)))", d)


def race(timing=(), **extra):
    """Three drivers, running order 1-2-3 from before the start; `timing` are (t, lines) updates."""
    drivers = {n: {"Tla": n, "LastName": name, "Line": int(n)} for n, name in NAMES.items()}
    base = {"Lines": {n: {"Line": int(n), "Retired": False, "InPit": False, "PitOut": False,
                          "IntervalToPositionAhead": {"Value": "+5.0" if n != "1" else ""}} for n in NAMES}}
    streams = {
        "DriverList": [[0, drivers]],
        "SessionStatus": [[START, {"Status": "Started"}], [END, {"Status": "Finished"}]],
        "TimingData": [[0, base]] + [[t, {"Lines": lines}] for t, lines in timing],
        "TimingStats": [], "TrackStatus": [[0, {"Status": "1"}]], "LapCount": [[0, {"CurrentLap": 1}]],
    }
    streams.update(extra.pop("streams", {}))
    return {"meta": {"end": END}, "streams": streams, "raceControl": [], "radio": [], "pitStops": [], **extra}


def swap(t, a, b):
    return (t, {a: {"Line": int(b)}, b: {"Line": int(a)}})


def kinds(ms):
    return [m["kind"] for m in ms]


def test_no_green_light_means_no_moments():
    d = race()
    d["streams"]["SessionStatus"] = [[0, {"Status": "Inactive"}]]
    assert moments(d) == []


def test_a_held_overtake_is_a_pass_with_both_names():
    # Charlie takes P2 from Bravo three minutes in and keeps it
    t = START + 180_000
    ms = moments(race(timing=[(t, {"3": {"Line": 2}, "2": {"Line": 3}})]))
    passes = [m for m in ms if m["kind"] == "pass"]
    assert len(passes) == 1
    assert passes[0]["text"] == "Charlie passes Bravo for P2"
    assert passes[0]["nums"] == ["3", "2"]
    assert passes[0]["t"] == t


def test_passing_for_the_lead_is_a_lead_change():
    t = START + 180_000
    ms = moments(race(timing=[(t, {"2": {"Line": 1}, "1": {"Line": 2}})]))
    assert [m["text"] for m in ms if m["kind"] == "lead"] == ["Bravo takes the lead from Alpha"]
    assert "pass" not in kinds(ms)


def test_a_swap_that_is_undone_straight_away_is_not_a_pass():
    t = START + 180_000
    ms = moments(race(timing=[(t, {"3": {"Line": 2}, "2": {"Line": 3}}), (t + 8_000, {"3": {"Line": 3}, "2": {"Line": 2}})]))
    assert "pass" not in kinds(ms)


def test_a_pit_stop_shuffle_is_not_a_pass():
    # Bravo is in the pit lane while Charlie goes by: nobody was overtaken on track
    t = START + 180_000
    ms = moments(race(timing=[
        (t - 5_000, {"2": {"InPit": True}}),
        (t, {"3": {"Line": 2}, "2": {"Line": 3}}),
        (t + 30_000, {"2": {"InPit": False}}),
    ]))
    assert "pass" not in kinds(ms)


def test_nothing_in_the_first_minute_counts():
    ms = moments(race(timing=[(START + 20_000, {"3": {"Line": 2}, "2": {"Line": 3}})]))
    assert "pass" not in kinds(ms)


def test_a_mass_reshuffle_is_the_board_resorting_not_racing():
    n = 9
    drivers = {str(i): {"Tla": str(i), "LastName": f"D{i}", "Line": i} for i in range(1, n + 1)}
    lines = {str(i): {"Line": i} for i in range(1, n + 1)}
    flipped = {str(i): {"Line": n + 1 - i} for i in range(1, n + 1)}
    d = race(timing=[(START + 180_000, flipped)])
    d["streams"]["DriverList"] = [[0, drivers]]
    d["streams"]["TimingData"][0] = [0, {"Lines": lines}]
    assert "pass" not in kinds(moments(d)) and "lead" not in kinds(moments(d))


def test_one_driver_passing_two_cars_is_one_moment():
    t = START + 180_000
    ms = moments(race(timing=[(t, {"3": {"Line": 1}, "1": {"Line": 2}, "2": {"Line": 3}})]))
    leads = [m for m in ms if m["kind"] == "lead"]
    assert len(leads) == 1 and leads[0]["nums"][0] == "3"


def test_a_battle_needs_the_gap_held_not_just_touched():
    close = {"2": {"IntervalToPositionAhead": {"Value": "+0.6"}}}
    held = [(START + 60_000 + i * 20_000, close) for i in range(10)]  # 180 s inside a second
    ms = moments(race(timing=held))
    battles = [m for m in ms if m["kind"] == "battle"]
    assert len(battles) == 1
    assert battles[0]["nums"] == ["2", "1"] and "Bravo is 0.6 s behind Alpha" in battles[0]["text"]
    # a brief touch does not count
    blip = [(START + 60_000, close), (START + 80_000, {"2": {"IntervalToPositionAhead": {"Value": "+3.0"}}})]
    assert "battle" not in kinds(moments(race(timing=blip)))


def test_pit_stop_names_the_driver_and_where_they_were_running():
    d = race(pitStops=[{"num": "2", "t": START + 200_000, "lap": "5", "stop": "2.4", "lane": "21.0"}])
    pit = [m for m in moments(d) if m["kind"] == "pit"]
    assert [m["text"] for m in pit] == ["Bravo pits from P2 (2.4 s)"]
    assert pit[0]["lap"] == 5


def test_safety_car_and_the_restart():
    d = race(streams={"TrackStatus": [[0, {"Status": "1"}], [START + 100_000, {"Status": "4"}], [START + 300_000, {"Status": "1"}]]})
    ms = moments(d)
    assert [m["text"] for m in ms if m["kind"] in ("safetycar", "restart")] == ["Safety car", "Track clear: racing resumes"]
    # a plain yellow is not worth a moment
    d = race(streams={"TrackStatus": [[0, {"Status": "1"}], [START + 100_000, {"Status": "2"}], [START + 120_000, {"Status": "1"}]]})
    assert not [m for m in moments(d) if m["kind"] in ("safetycar", "restart", "vsc", "redflag")]


def test_stewards_penalties_and_investigations_only():
    rc = [
        {"t": START + 10_000, "Lap": 2, "Message": "FIA STEWARDS: TURN 2 INCIDENT INVOLVING CARS 2 (BRA) AND 3 (CHA) UNDER INVESTIGATION - CAUSING A COLLISION (16:40:50)"},
        {"t": START + 20_000, "Lap": 2, "Message": "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 2 (BRA) - CAUSING A COLLISION (16:40:50)"},
        {"t": START + 30_000, "Lap": 3, "Message": "FIA STEWARDS: PENALTY SERVED - 10 SECOND TIME PENALTY FOR CAR 2 (BRA) - CAUSING A COLLISION (16:40:50)"},
        {"t": START + 40_000, "Lap": 3, "Message": "FIA STEWARDS: INCIDENT INVOLVING CAR 1 (ALP) REVIEWED NO FURTHER INVESTIGATION - X (16:41:00)"},
        {"t": START + 50_000, "Lap": 3, "Message": "CAR 1 (ALP) TIME 1:30.000 DELETED - TRACK LIMITS AT TURN 4 LAP 3 16:42:00"},
    ]
    d = race(raceControl=rc)
    d["streams"]["DriverList"][0][1]["2"]["Tla"] = "BRA"
    ms = [m for m in moments(d) if m["kind"] in ("investigation", "penalty")]
    assert [m["kind"] for m in ms] == ["investigation", "penalty"]
    assert ms[1]["text"] == "Bravo gets a 10-second penalty, causing a collision"


def test_everything_is_inside_the_race_and_sorted():
    d = race(timing=[(START + 180_000, {"3": {"Line": 2}, "2": {"Line": 3}})],
             pitStops=[{"num": "1", "t": 50_000, "lap": "1", "stop": "2.0", "lane": "20"}],  # before lights out
             radio=[{"t": END + 5_000, "RacingNumber": "1"}])                                   # after the flag
    ms = moments(d)
    assert all(START <= m["t"] <= END for m in ms)
    assert [m["t"] for m in ms] == sorted(m["t"] for m in ms)
    assert [m["id"] for m in ms] == list(range(len(ms)))


def test_scores_stay_between_zero_and_one_and_lead_beats_a_back_marker_pass():
    t = START + 180_000
    lead = moments(race(timing=[(t, {"2": {"Line": 1}, "1": {"Line": 2}})]))
    back = moments(race(timing=[(t, {"3": {"Line": 2}, "2": {"Line": 3}})]))
    s = lambda ms, k: next(m["score"] for m in ms if m["kind"] == k)  # noqa: E731
    assert 0 <= s(back, "pass") <= 1 and s(lead, "lead") > s(back, "pass")


# ---- the Director's choice ----

def focus(ms, calls):
    """calls: [(T, minDwell)] fed in order, threading `current` through; returns the chosen ids."""
    script = """
      let cur = null; const out = [];
      for (const [T, dwell] of x.calls) { cur = M.pickFocus(x.ms, T, cur, dwell); out.push(cur && cur.id); }
      console.log(JSON.stringify(out));"""
    return run_node(script, {"ms": ms, "calls": calls})


def m(id, t, score, kind="pass", life=10_000):
    return {"id": id, "t": t, "score": score, "kind": kind, "life": life, "nums": [], "text": ""}


def test_director_picks_the_best_live_moment_and_holds_it_for_the_dwell():
    ms = [m(0, 1000, 0.5), m(1, 2000, 0.9)]
    # once the dwell is up the better moment wins
    assert focus(ms, [[1500, 500], [2500, 500]]) == [0, 1]
    # a better one that arrives during the dwell waits for it
    ms = [m(0, 1000, 0.9), m(1, 2000, 0.95)]
    assert focus(ms, [[1500, 5000], [2500, 5000], [6500, 5000]]) == [0, 0, 1]


def test_director_lets_go_when_the_moment_expires_and_ignores_the_future_and_radio():
    ms = [m(0, 1000, 0.9, life=3000), m(1, 9000, 0.9), m(2, 1500, 0.99, kind="radio")]
    assert focus(ms, [[2000, 1000], [5000, 1000]]) == [0, None]


def test_director_follows_a_seek_backwards():
    ms = [m(0, 1000, 0.9), m(1, 20_000, 0.9)]
    assert focus(ms, [[21_000, 5000], [1_500, 5000]]) == [1, 0]
