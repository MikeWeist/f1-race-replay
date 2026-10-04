"""
The races the replay page can offer: replays already exported on this computer, plus F1's
published calendar when this copy of the project can build them.

Standard library only, so it also runs inside the zip that gets shared.

A calendar race is in one of these states:
  ready        a replay is exported: open it
  available    F1 has published complete data: it can be built
  pending      the race has started (or just finished) but F1's data isn't complete yet
  upcoming     the race hasn't started
  unavailable  it should have data by now but doesn't (cancelled, or F1 never published it)
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

STATIC = "https://livetiming.formula1.com/static/"
CALENDAR_TTL = 600           # seconds the F1 calendar is cached
ARCHIVE_TTL = 60             # seconds an archive-complete check is cached
INCOMPLETE_WINDOW = timedelta(days=2)  # after this long, a published race is assumed complete


# ---------- reading exported replays ----------

def read_meta(path):
    """The 'meta' object of an exported replay without loading the whole file (it comes first)."""
    with open(path, encoding="utf-8") as f:
        head = f.read(16384)
    at = head.find('"meta":')
    if at < 0:
        return None
    try:
        meta, _ = json.JSONDecoder().raw_decode(head[at + len('"meta":'):].lstrip())
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def scan_exports(data_dir):
    """Every exported replay in a folder, as dicts; telemetry files and unreadable files are skipped."""
    out = []
    if not os.path.isdir(data_dir):
        return out
    for name in sorted(os.listdir(data_dir)):
        if not name.endswith(".json") or name.endswith("_car.json") or name.startswith("."):
            continue
        meta = read_meta(os.path.join(data_dir, name))
        if not meta or "meeting" not in meta:
            continue
        out.append({
            "file": "data/" + name,
            "series": meta.get("series", "f1"),
            "season": meta.get("season"),
            "meeting": meta.get("meeting", ""),
            "session": meta.get("session", ""),
            "t0Utc": meta.get("t0Utc") or 0,
        })
    return out


# ---------- the F1 calendar ----------

def parse_offset(text):
    sign = -1 if str(text).startswith("-") else 1
    h, m, s = (str(text).lstrip("+-").split(":") + ["0", "0"])[:3]
    return sign * timedelta(hours=int(h), minutes=int(m), seconds=float(s))


def parse_f1_index(index, season):
    """Race weekends from F1's Index.json: [{meeting, location, start, startUtc, path}]."""
    out = []
    for m in index.get("Meetings", []):
        if "testing" in (m.get("Name") or "").lower():
            continue
        race = next((s for s in m.get("Sessions", []) if s.get("Name") == "Race"), None)
        if not race or not race.get("StartDate"):
            continue
        local = datetime.fromisoformat(race["StartDate"])
        utc = (local - parse_offset(race.get("GmtOffset", "00:00:00"))).replace(tzinfo=timezone.utc)
        out.append({
            "season": season, "meeting": m["Name"], "location": m.get("Location") or m["Name"],
            "start": local, "startUtc": utc, "path": race.get("Path") or None,
        })
    return out


def calendar_status(race, now, archive_complete):
    """The state of a calendar race that has no replay yet."""
    if not race["path"]:
        if now < race["startUtc"]:
            return "upcoming"
        return "pending" if now - race["startUtc"] < INCOMPLETE_WINDOW else "unavailable"
    if now - race["startUtc"] > INCOMPLETE_WINDOW:
        return "available"
    return "available" if archive_complete() else "pending"


def date_label(dt):
    return f"{dt:%a} {dt.day} {dt:%b}"


def fetch_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "livef1-replay"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8-sig"))


# ---------- the catalog ----------

class Catalog:
    def __init__(self, data_dir, can_build, clock=None, fetch=fetch_json):
        self.data_dir, self.can_build = data_dir, can_build
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fetch = fetch
        self._lock = threading.Lock()
        self._calendar = {}   # season -> (fetched at, races)
        self._archive = {}    # path -> (fetched at, complete)
        self.calendar_ok = True

    # -- cached network reads (a failure keeps the last good answer) --

    def _season(self, season):
        with self._lock:
            hit = self._calendar.get(season)
            if hit and time.time() - hit[0] < CALENDAR_TTL:
                return hit[1]
        try:
            races = parse_f1_index(self._fetch(f"{STATIC}{season}/Index.json"), season)
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            self.calendar_ok = False
            return hit[1] if hit else []
        with self._lock:
            self._calendar[season] = (time.time(), races)
        self.calendar_ok = True
        return races

    def _archive_complete(self, path):
        with self._lock:
            hit = self._archive.get(path)
            if hit and time.time() - hit[0] < ARCHIVE_TTL:
                return hit[1]
        try:
            complete = self._fetch(f"{STATIC}{path}ArchiveStatus.json").get("Status") == "Complete"
        except (urllib.error.URLError, OSError, ValueError):
            complete = False
        with self._lock:
            self._archive[path] = (time.time(), complete)
        return complete

    # -- the merged list --

    def exports(self):
        return scan_exports(self.data_dir)

    def races(self):
        """{'canBuild': bool, 'races': [...]} newest first within each season."""
        exports = self.exports()
        now = self._clock()
        used, races = set(), []

        def ready_entry(e, name=None):
            t0 = datetime.fromtimestamp((e["t0Utc"] or 0) / 1000, tz=timezone.utc)
            return {"series": e["series"], "season": e["season"], "name": name or e["meeting"],
                    "location": "", "date": t0.date().isoformat(), "dateLabel": date_label(t0),
                    "sortKey": t0.isoformat(), "status": "ready", "file": e["file"]}

        if self.can_build:
            seasons = {now.year} | {e["season"] for e in exports if e["series"] == "f1" and e["season"]}
            for season in sorted(seasons):
                for r in self._season(season):
                    match = next((e for e in exports if e["series"] == "f1" and e["season"] == season
                                  and e["meeting"] == r["meeting"] and e["file"] not in used), None)
                    entry = {"series": "f1", "season": season, "name": r["meeting"], "location": r["location"],
                             "date": r["start"].date().isoformat(), "dateLabel": date_label(r["start"]),
                             "startUtc": r["startUtc"].isoformat(),
                             "sortKey": r["startUtc"].isoformat(), "file": None}
                    if match:
                        used.add(match["file"])
                        entry.update(status="ready", file=match["file"])
                    else:
                        entry["status"] = calendar_status(r, now, lambda p=r["path"]: self._archive_complete(p))
                    races.append(entry)
        for e in exports:
            if e["file"] not in used:
                races.append(ready_entry(e))
        # without the calendar (shared copy), only what's ready is listed
        races.sort(key=lambda r: (r["season"] or 0, r["sortKey"]), reverse=True)
        for r in races:
            r.pop("sortKey", None)
        return {"canBuild": self.can_build, "calendarOk": self.calendar_ok or not self.can_build, "races": races}

    def default_file(self, preferred=None):
        """The replay to open when none is asked for: the remembered pick, else the newest one."""
        exports = self.exports()
        files = {e["file"] for e in exports}
        if preferred in files:
            return preferred
        if not exports:
            return None
        return max(exports, key=lambda e: (e["t0Utc"], e["file"]))["file"]

    def buildable(self, season, location):
        """The calendar race to build for (season, location), or None unless it's available."""
        if not self.can_build:
            return None
        for r in self.races()["races"]:
            if r["series"] == "f1" and r["season"] == season and r["location"] == location and r["status"] == "available":
                return r
        return None
