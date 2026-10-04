"""record_live.Recorder: which messages end up in which file, without any network."""
import json

import record_live
from record_live import Recorder, session_slug

RACE = "2026/2026-10-04_Bahrain_Grand_Prix/2026-10-04_Race/"
QUALI = "2026/2026-10-03_Bahrain_Grand_Prix/2026-10-03_Qualifying/"


def make(tmp_path, now=1_000.0):
    clock = {"t": now}
    rec = Recorder(str(tmp_path), clock=lambda: clock["t"], log=lambda s: None)
    return rec, clock


def snapshot(rec, path, status, extra=()):
    """What F1 sends on connect, in an awkward order, then the first live update that ends it."""
    rec.new_connection()
    rec.on_feed("TimingData", {"Lines": {}}, None)
    rec.on_feed("SessionStatus", {"Status": status}, None)
    rec.on_feed("SessionInfo", {"Path": path}, None)
    for topic, data in extra:
        rec.on_feed(topic, data, None)


def lines(tmp_path, path):
    f = tmp_path / (session_slug(path) + ".jsonl")
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else None


def test_slug_is_the_meeting_and_session_and_filename_safe():
    assert session_slug(RACE) == "2026-10-04_Bahrain_Grand_Prix__2026-10-04_Race"
    assert session_slug("2026/we ird:/x?y/") == "we_ird___x_y"
    assert session_slug("") == "session"


def test_a_live_session_is_recorded_with_the_snapshot_in_any_order(tmp_path):
    rec, clock = make(tmp_path)
    snapshot(rec, RACE, "Started")
    rec.on_feed("Heartbeat", {"Utc": "x"}, "2026-10-04T07:00:00Z")      # ends the snapshot, is itself not kept
    clock["t"] = 1_001.5
    rec.on_feed("TimingData", {"Lines": {"1": {"Line": 1}}}, "2026-10-04T07:00:01Z")
    rec.close()
    got = lines(tmp_path, RACE)
    assert [m["topic"] for m in got] == ["TimingData", "SessionStatus", "SessionInfo", "TimingData"]
    assert got[0]["snap"] is True and got[3].get("snap") is None
    assert got[3]["w"] == 1_001_500 and got[3]["ts"] == "2026-10-04T07:00:01Z"
    assert all(m["topic"] != "Heartbeat" for m in got)


def test_a_session_that_was_already_over_is_not_recorded(tmp_path):
    for status in ("Finalised", "Ends"):
        rec, _ = make(tmp_path)
        snapshot(rec, QUALI, status)
        rec.on_feed("Heartbeat", {}, "2026-10-03T20:00:00Z")
        rec.on_feed("SessionStatus", {"Status": status}, "2026-10-03T20:00:01Z")
        rec.close()
        assert lines(tmp_path, QUALI) is None


def test_reconnecting_mid_session_keeps_the_fresh_state_in_the_same_file(tmp_path):
    rec, _ = make(tmp_path)
    snapshot(rec, RACE, "Started")
    rec.on_feed("TimingData", {"a": 1}, "t1")
    snapshot(rec, RACE, "Started")           # dropped and reconnected
    rec.on_feed("TimingData", {"a": 2}, "t2")
    rec.close()
    got = lines(tmp_path, RACE)
    assert [m.get("snap", False) for m in got] == [True, True, True, False, True, True, True, False]


def test_reconnecting_after_the_session_ended_adds_nothing(tmp_path):
    rec, _ = make(tmp_path)
    snapshot(rec, RACE, "Started")
    rec.on_feed("TimingData", {"a": 1}, "t1")
    rec.on_feed("SessionStatus", {"Status": "Finalised"}, "t2")   # logged: the end of the file
    n = len(lines(tmp_path, RACE))
    snapshot(rec, RACE, "Finalised")
    rec.on_feed("Heartbeat", {}, "t3")
    rec.close()
    assert len(lines(tmp_path, RACE)) == n


def test_the_next_session_gets_its_own_file_even_though_the_last_one_finished(tmp_path):
    rec, _ = make(tmp_path)
    snapshot(rec, QUALI, "Finalised")
    rec.on_feed("Heartbeat", {}, "t0")                     # connected between sessions: nothing recorded
    rec.on_feed("SessionInfo", {"Path": RACE}, "t1")       # F1 switches to the race...
    rec.on_feed("SessionStatus", {"Status": "Inactive"}, "t2")
    rec.on_feed("TimingData", {"a": 1}, "t3")
    rec.close()
    assert lines(tmp_path, QUALI) is None
    assert [m["topic"] for m in lines(tmp_path, RACE)] == ["SessionInfo", "SessionStatus", "TimingData"]


def test_a_live_switch_closes_the_old_file(tmp_path):
    rec, _ = make(tmp_path)
    snapshot(rec, QUALI, "Started")
    rec.on_feed("TimingData", {"a": 1}, "t1")
    rec.on_feed("SessionInfo", {"Path": RACE}, "t2")
    rec.on_feed("TimingData", {"b": 1}, "t3")
    rec.close()
    assert "b" not in json.dumps(lines(tmp_path, QUALI))
    assert "b" in json.dumps(lines(tmp_path, RACE))


def test_only_one_recorder_can_hold_the_lock():
    first = record_live.only_one_recorder(47899)
    try:
        assert first is not None
        assert record_live.only_one_recorder(47899) is None
    finally:
        first.close()
