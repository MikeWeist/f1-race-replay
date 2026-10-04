"""Tests for race_catalog: the list of races the replay page offers."""
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import race_catalog as rc  # noqa: E402

NOW = datetime(2026, 10, 4, 2, 48, tzinfo=timezone.utc)


def meeting(name, location, start, offset="03:00:00", path=True, testing=False):
    return {"Name": name, "Location": location, "Sessions": [
        {"Name": "Practice 1", "StartDate": start, "GmtOffset": offset, "Path": "x/"},
        {"Name": "Race", "StartDate": start, "GmtOffset": offset, "Path": (f"2026/{location}/Race/" if path else "")}]}


INDEX = {"Year": 2026, "Meetings": [
    {"Name": "Pre-Season Testing", "Location": "Sakhir", "Sessions": [{"Name": "Day 1", "StartDate": "2026-02-01T10:00:00"}]},
    meeting("Azerbaijan Grand Prix", "Baku", "2026-09-26T15:00:00", "04:00:00"),
    meeting("Spanish Grand Prix", "Madrid", "2026-09-13T15:00:00", "02:00:00"),
    meeting("Bahrain Grand Prix", "Kuala Lumpur", "2026-10-04T15:00:00", "08:00:00", path=False),
]}


def write_export(folder, name, **meta):
    meta = {"season": 2026, "meeting": "Azerbaijan Grand Prix", "session": "Race", "t0Utc": 1_790_000_000_000,
            "end": 1, **meta}
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "audio": None, "streams": {"x": list(range(5000))}}, f)  # meta first, like the exporter
    return path


class Stub:
    """A stand-in for F1's static site: Index.json plus an ArchiveStatus per race path."""

    def __init__(self, complete=True, fail=False):
        self.complete, self.fail, self.calls = complete, fail, []

    def __call__(self, url, timeout=8):
        self.calls.append(url)
        if self.fail:
            raise urllib.error.URLError("offline")
        if url.endswith("Index.json"):
            return INDEX
        return {"Status": "Complete" if self.complete else "Generating"}


def catalog(tmp_path, can_build=True, stub=None, now=NOW):
    return rc.Catalog(str(tmp_path), can_build, clock=lambda: now, fetch=stub or Stub())


# ---------- reading exports ----------

def test_read_meta_reads_only_the_head_of_a_big_file(tmp_path):
    p = write_export(tmp_path, "2026_Baku_Race.json")
    assert rc.read_meta(p)["meeting"] == "Azerbaijan Grand Prix"


def test_scan_skips_telemetry_hidden_and_broken_files(tmp_path):
    write_export(tmp_path, "2026_Baku_Race.json")
    write_export(tmp_path, "2026_Baku_Race_car.json")
    (tmp_path / ".index.json").write_text("{}")
    (tmp_path / "broken.json").write_text("not json at all")
    (tmp_path / "nometa.json").write_text('{"streams": {}}')
    assert [e["file"] for e in rc.scan_exports(str(tmp_path))] == ["data/2026_Baku_Race.json"]


def test_scan_of_a_missing_folder_is_empty(tmp_path):
    assert rc.scan_exports(str(tmp_path / "nope")) == []


# ---------- calendar parsing and states ----------

def test_parse_skips_testing_and_converts_local_start_to_utc():
    races = {r["location"]: r for r in rc.parse_f1_index(INDEX, 2026)}
    assert "Sakhir" not in races
    assert races["Kuala Lumpur"]["startUtc"] == datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)   # 15:00 at UTC+8
    assert races["Baku"]["startUtc"] == datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)          # 15:00 at UTC+4


@pytest.mark.parametrize("offset,expected", [("03:00:00", timedelta(hours=3)), ("-05:00:00", -timedelta(hours=5)),
                                             ("00:00:00", timedelta(0)), ("+01:30:00", timedelta(hours=1.5))])
def test_parse_offset(offset, expected):
    assert rc.parse_offset(offset) == expected


def race(start_utc, path):
    return {"startUtc": start_utc, "path": path}


def test_status_before_during_and_after_a_race():
    soon = NOW + timedelta(hours=4)
    assert rc.calendar_status(race(soon, None), NOW, lambda: False) == "upcoming"
    started = NOW - timedelta(minutes=30)
    assert rc.calendar_status(race(started, None), NOW, lambda: False) == "pending"
    assert rc.calendar_status(race(started, "p/"), NOW, lambda: False) == "pending"      # data arriving, not complete
    assert rc.calendar_status(race(started, "p/"), NOW, lambda: True) == "available"
    old = NOW - timedelta(days=9)
    assert rc.calendar_status(race(old, "p/"), NOW, lambda: False) == "available"        # old data is assumed final
    assert rc.calendar_status(race(old, None), NOW, lambda: False) == "unavailable"


def test_old_races_do_not_hit_the_network_for_an_archive_check():
    called = []
    rc.calendar_status(race(NOW - timedelta(days=9), "p/"), NOW, lambda: called.append(1) or True)
    assert not called


# ---------- the merged list ----------

def test_races_merge_exports_with_the_calendar(tmp_path):
    write_export(tmp_path, "2026_Baku_Race.json")
    out = catalog(tmp_path).races()
    by = {r["name"]: r for r in out["races"]}
    assert out["canBuild"] and out["calendarOk"]
    assert by["Azerbaijan Grand Prix"]["status"] == "ready" and by["Azerbaijan Grand Prix"]["file"] == "data/2026_Baku_Race.json"
    assert by["Spanish Grand Prix"]["status"] == "available" and by["Spanish Grand Prix"]["file"] is None
    assert by["Bahrain Grand Prix"]["status"] == "upcoming"
    assert by["Bahrain Grand Prix"]["startUtc"].startswith("2026-10-04T07:00")
    assert [r["name"] for r in out["races"]] == ["Bahrain Grand Prix", "Azerbaijan Grand Prix", "Spanish Grand Prix"]  # newest first


def test_an_export_is_matched_by_meeting_name_not_by_file_name(tmp_path):
    write_export(tmp_path, "whatever_i_called_it.json", meeting="Spanish Grand Prix")
    by = {r["name"]: r for r in catalog(tmp_path).races()["races"]}
    assert by["Spanish Grand Prix"]["status"] == "ready" and by["Spanish Grand Prix"]["file"] == "data/whatever_i_called_it.json"
    assert len([r for r in catalog(tmp_path).races()["races"] if r["name"] == "Spanish Grand Prix"]) == 1


def test_formula_e_and_other_exports_are_listed_as_ready(tmp_path):
    write_export(tmp_path, "fe_2026_london_race.json", series="fe", meeting="Hankook London E-Prix")
    write_export(tmp_path, "2025_Monza_Race.json", season=2025, meeting="Italian Grand Prix")
    out = catalog(tmp_path).races()["races"]
    fe = next(r for r in out if r["series"] == "fe")
    assert fe["status"] == "ready" and fe["name"] == "Hankook London E-Prix"
    assert any(r["season"] == 2025 and r["status"] == "ready" for r in out)


def test_a_shared_copy_lists_only_what_it_holds(tmp_path):
    write_export(tmp_path, "2026_Baku_Race.json")
    stub = Stub()
    out = catalog(tmp_path, can_build=False, stub=stub).races()
    assert [r["name"] for r in out["races"]] == ["Azerbaijan Grand Prix"]
    assert not out["canBuild"] and stub.calls == []        # and it never touches the network


def test_offline_keeps_local_races_and_says_so(tmp_path):
    write_export(tmp_path, "2026_Baku_Race.json")
    out = catalog(tmp_path, stub=Stub(fail=True)).races()
    assert out["calendarOk"] is False
    assert [r["name"] for r in out["races"]] == ["Azerbaijan Grand Prix"]


def test_a_recent_race_is_pending_until_f1_marks_it_complete(tmp_path):
    # the Bahrain race started at 07:00Z; pretend it is 08:00Z and F1 has published a path
    index_with_path = json.loads(json.dumps(INDEX))
    index_with_path["Meetings"][3]["Sessions"][1]["Path"] = "2026/Kuala Lumpur/Race/"
    stub_index = lambda url, timeout=8: index_with_path if url.endswith("Index.json") else {"Status": "Generating"}
    pending = rc.Catalog(str(tmp_path), True, clock=lambda: datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc), fetch=stub_index).races()
    assert {r["name"]: r["status"] for r in pending["races"]}["Bahrain Grand Prix"] == "pending"
    done = lambda url, timeout=8: index_with_path if url.endswith("Index.json") else {"Status": "Complete"}
    ready = rc.Catalog(str(tmp_path), True, clock=lambda: datetime(2026, 10, 4, 9, 30, tzinfo=timezone.utc), fetch=done).races()
    assert {r["name"]: r["status"] for r in ready["races"]}["Bahrain Grand Prix"] == "available"


def test_the_calendar_is_cached_between_requests(tmp_path):
    stub = Stub()
    c = catalog(tmp_path, stub=stub)
    c.races(); c.races(); c.races()
    assert sum(url.endswith("Index.json") for url in stub.calls) == 1


# ---------- default race and what can be built ----------

def test_default_is_the_remembered_pick_else_the_newest(tmp_path):
    write_export(tmp_path, "old.json", t0Utc=1_700_000_000_000)
    write_export(tmp_path, "new.json", t0Utc=1_790_000_000_000)
    c = catalog(tmp_path)
    assert c.default_file() == "data/new.json"
    assert c.default_file("data/old.json") == "data/old.json"
    assert c.default_file("data/deleted.json") == "data/new.json"      # a stale pick falls back
    assert catalog(tmp_path / "empty").default_file() is None


def test_only_available_races_can_be_built(tmp_path):
    write_export(tmp_path, "2026_Baku_Race.json")
    c = catalog(tmp_path)
    assert c.buildable(2026, "Madrid")["name"] == "Spanish Grand Prix"
    assert c.buildable(2026, "Baku") is None                  # already built
    assert c.buildable(2026, "Kuala Lumpur") is None          # hasn't happened
    assert c.buildable(2026, "Atlantis; calc.exe") is None    # not on the calendar
    assert catalog(tmp_path, can_build=False).buildable(2026, "Madrid") is None


def test_date_label():
    assert rc.date_label(datetime(2026, 10, 4, 15, 0)) == "Sun 4 Oct"
