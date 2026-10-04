"""
Read-only client for Formula E's live timing feed (run by Al Kamel Systems).

The feed is a Meteor (DDP) websocket that Formula E's own live timing page uses. Besides the
live session it keeps every recent session's final state and serves it by session ID, which is
how finished races are fetched here.

The feed's registry document also contains an API token. This module drops it the moment it
arrives: it is never stored, returned or written anywhere.
"""
import asyncio
import json
from datetime import datetime, timezone

import websockets

URL = "wss://livetiming-formula-e.alkamelsystems.com/websocket"
ORIGIN = "https://livetiming-formula-e.alkamelsystems.com"
FEED_NAME = "fiaformulae"

# Per-session feeds (subscription name) and the collection each one fills
SESSION_FEEDS = {
    "sessionStatus": "session_status",
    "raceControl": "race_control",
    "sessionResults": "session_results",
    "standings": "standings",
    "entry": "session_entry",
    "pitInfo": "session_pit_info",
    "trackInfo": "track_info",
    "weather": "weather",
    "bestResults": "best_results",
    "countStates": "countStates",
    "sessionAttackMode": "attackMode",
    "sessionPitBoost": "pitBoost",
    "sessionCircuitPath": "session_circuit_config",
}

SECRET_FIELDS = {"apiToken", "owner"}


class FeedError(RuntimeError):
    pass


def _oid(hex_id):
    return {"$type": "oid", "$value": hex_id}


def _plain(value):
    """EJSON ObjectIDs ({"$type": "oid", ...}) become plain hex strings."""
    if isinstance(value, dict):
        if value.get("$type") == "oid":
            return value["$value"]
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class Feed:
    """Async context manager: `async with Feed() as feed: await feed.sessions()`."""

    def __init__(self):
        self.ws = None
        self._reader = None
        self._n = 0
        self._ready = {}
        self._errors = {}
        self._docs = {}  # collection -> {doc id: fields}
        self._registry = None

    async def __aenter__(self):
        self.ws = await websockets.connect(URL, max_size=None, additional_headers={"Origin": ORIGIN})
        self._reader = asyncio.create_task(self._read())
        await self.ws.send(json.dumps({"msg": "connect", "version": "1", "support": ["1", "pre2", "pre1"]}))
        return self

    async def __aexit__(self, *exc):
        self._reader.cancel()
        await self.ws.close()

    async def _read(self):
        async for raw in self.ws:
            m = json.loads(raw)
            kind = m.get("msg")
            if kind == "ping":
                await self.ws.send(json.dumps({"msg": "pong"}))
            elif kind in ("added", "changed"):
                coll = self._docs.setdefault(m["collection"], {})
                doc = coll.setdefault(m["id"], {})
                doc.update(m.get("fields", {}))
                for key in m.get("cleared", []):
                    doc.pop(key, None)
                for key in SECRET_FIELDS:
                    doc.pop(key, None)  # never keep the feed's token
            elif kind == "removed":
                self._docs.get(m["collection"], {}).pop(m["id"], None)
            elif kind == "ready":
                for sid in m["subs"]:
                    if sid in self._ready:
                        self._ready[sid].set()
            elif kind == "nosub":
                self._errors[m["id"]] = (m.get("error") or {}).get("reason", "unknown error")
                if m["id"] in self._ready:
                    self._ready[m["id"]].set()

    async def _subscribe(self, name, params, timeout=30):
        self._n += 1
        sid = str(self._n)
        self._ready[sid] = asyncio.Event()
        await self.ws.send(json.dumps({"msg": "sub", "id": sid, "name": name, "params": params}))
        try:
            await asyncio.wait_for(self._ready[sid].wait(), timeout)
        except asyncio.TimeoutError:
            raise FeedError(f"Timed out waiting for the '{name}' feed") from None
        if sid in self._errors:
            raise FeedError(f"The '{name}' feed refused the request: {self._errors[sid]}")

    def _docs_of(self, collection):
        return list(self._docs.get(collection, {}).items())

    async def sessions(self):
        """Sessions the feed currently lists, oldest first: [{id, number, name, date, ...}]."""
        await self._subscribe("livetimingFeed", [FEED_NAME])
        feeds = self._docs_of("feeds")
        if not feeds:
            raise FeedError("The feed registry returned nothing")
        oids = feeds[0][1].get("sessions") or []
        self._registry = {"running": bool(feeds[0][1].get("running")), "sessions": [o["$value"] for o in oids]}
        if not oids:
            return []
        await self._subscribe("sessions", [oids])
        out = []
        for doc_id, f in self._docs_of("sessions"):
            out.append({
                "id": doc_id,
                "number": f.get("id"),
                "name": f.get("name"),
                "date": f.get("date"),
                "endDate": f.get("endDate"),
                "UtcOffsetMin": f.get("UtcOffsetMin", 0),
            })
        return sorted(out, key=lambda s: s["date"] or 0)

    @property
    def running(self):
        return bool(self._registry and self._registry["running"])

    async def fetch_session(self, session_id, sessions=None):
        """All per-session documents for one session, as plain JSON-ready dicts keyed by collection."""
        listed = {s["id"]: s for s in (sessions or await self.sessions())}
        if session_id not in listed:
            raise FeedError(f"Session {session_id} isn't listed by the feed (it only lists the latest event)")
        raw = {"__session": listed[session_id]}
        for sub, collection in SESSION_FEEDS.items():
            before = {k: dict(v) for k, v in self._docs.get(collection, {}).items()}
            await self._subscribe(sub, [_oid(session_id)])
            docs = self._docs_of(collection)
            # a session's feeds hold exactly one document each
            if docs:
                raw[collection] = _plain(docs[-1][1])
            self._docs[collection] = before  # keep sessions from mixing if called again
        return raw


def local_time(ms, offset_min):
    """Epoch ms -> 'YYYY-MM-DD HH:MM' in the session's local time."""
    t = datetime.fromtimestamp(ms / 1000 + offset_min * 60, tz=timezone.utc)
    return t.strftime("%Y-%m-%d %H:%M")
