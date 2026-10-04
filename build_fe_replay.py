"""
Build a replay of a finished Formula E race from the live timing feed's stored data.

Usage (from the repo root, with the venv's python):
    python build_fe_replay.py --list              # sessions the feed currently stores
    python build_fe_replay.py                     # the Race of the latest event
    python build_fe_replay.py "Race"              # a session by name (case-insensitive, must be unique)
    python build_fe_replay.py --id <session id>   # a session by ID (see --list)
    python build_fe_replay.py --refresh           # download again instead of using the cache

The feed only lists the sessions of the most recent event, and stores each one as a final
snapshot. That is enough for a lap-by-lap replay with Attack Mode and estimated car positions;
there is no commentary, team radio or telemetry for Formula E.

Writes replay/data/fe_<year>_<track>_<session>.json. Open it with
    http://localhost:8765/?data=data/<that file name>
"""
import argparse
import asyncio
import json
import os
import re
import sys

from fe_convert import convert
from fe_feed import Feed, FeedError, local_time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "fe_cache")
OUT_DIR = os.path.join(HERE, "replay", "data")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def pick(sessions, name, session_id):
    if session_id:
        match = [s for s in sessions if s["id"] == session_id]
        if not match:
            sys.exit(f"No session with ID {session_id}. Run with --list to see what the feed stores.")
        return match[0]
    wanted = (name or "Race").lower()
    exact = [s for s in sessions if (s["name"] or "").lower() == wanted]
    match = exact or [s for s in sessions if wanted in (s["name"] or "").lower()]
    if len(match) != 1:
        names = ", ".join(s["name"] for s in match) if match else "none"
        sys.exit(f"'{name or 'Race'}' matches {len(match)} sessions ({names}). Be more specific, or use --id.")
    return match[0]


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", nargs="?", help="session name (default: Race)")
    ap.add_argument("--list", action="store_true", help="list the sessions the feed stores")
    ap.add_argument("--id", help="session ID instead of a name")
    ap.add_argument("--refresh", action="store_true", help="ignore the cached download")
    ap.add_argument("--any-session", action="store_true",
                    help="allow practice and qualifying (built for races: the tower orders by laps, not best lap)")
    args = ap.parse_args()

    async with Feed() as feed:
        sessions = await feed.sessions()
        if feed.running:
            print("A session is running right now: this stored copy may be incomplete.")
        if args.list:
            print(f"The feed stores {len(sessions)} sessions:")
            for s in sessions:
                print(f"  {local_time(s['date'], s['UtcOffsetMin'])}  {s['name']:<34} id={s['id']}")
            return
        chosen = pick(sessions, args.session, args.id)
        if "race" not in (chosen["name"] or "").lower() and not args.any_session:
            sys.exit(f"'{chosen['name']}' isn't a race. This tool is built for races; add --any-session to try anyway.")

        cache = os.path.join(CACHE_DIR, chosen["id"] + ".json")
        if os.path.exists(cache) and not args.refresh:
            print(f"Using the cached download ({os.path.basename(cache)}); --refresh fetches it again.")
            raw = json.load(open(cache, encoding="utf-8"))
        else:
            print(f"Fetching '{chosen['name']}' from the feed...")
            raw = await feed.fetch_session(chosen["id"], sessions)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(raw, f)

    print("Converting...")
    replay = convert(raw)
    meta = replay["meta"]
    name = f"fe_{meta['season']}_{slug(meta['track'] or meta['meeting'])}_{slug(meta['session'])}.json"
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, name)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(replay, f, separators=(",", ":"))
    print(f"Wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB): {meta['meeting']}, {len(replay['positions'])} cars, "
          f"{len(replay['streams']['TimingData'])} timing updates, {len(replay['raceControl'])} race control messages")
    print(f"Open: http://localhost:8765/?data=data/{name}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except FeedError as e:
        sys.exit(f"Formula E feed problem: {e}")
