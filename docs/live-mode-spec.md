# Live mode and the curated race: spec

Status: building. Written 4 Oct 2026, after the Bahrain probe. Decided: moments and the Director first
(Director off by default), and every live session is recorded.
Done: `replay/moments.js` + strip + Director (sections 3.1 to 3.3, replays only), `record_live.py` (the
recorder in section 5). Not started: the live relay, live page mode, estimated positions, and the
sign-in states (sections 3.4 and 5).

## 1. What we know (measured, not assumed)

During the Bahrain race, with and without an F1 TV login, side by side (`probe_live_20261004_*.json`):

| Topic | No login | Login |
|---|---|---|
| `CarData.z` (speed, throttle, brake, RPM, gear) | none | ~9,000 updates, 5.5 MB per 2.5 h |
| `Position.z` (car x/y) | none | ~9,000 updates, 6.7 MB per 2.5 h |
| `TimingData` (~22,000 updates), `TeamRadio`, `RaceControlMessages`, `TimingAppData`, `TrackStatus`, `LapCount`, `WeatherData`, `DriverList`, `SessionStatus` | full | identical |
| `DriverTracker` | only running order (`Lines: [{Position, RacingNumber}]`) | same |

So the login unlocks exactly two things: **where the cars are** and **what the cars are doing**.
It does not unlock video or commentary audio (those are F1 TV's separate player service, out of scope).
Everything else needed for a good live page is public.

## 2. Goal

One page, two states, and a curated layer that works in both.

- **Signed out:** the race as a story. Full timing, radio, race control, strategy, and a track map whose
  cars are *estimated* from sector progress (the method already used for Formula E).
- **Signed in:** the same, plus real car positions and live telemetry.
- **Curated, both states:** the page notices what matters and puts it in front of you, instead of leaving you
  to watch 20 rows.

Non-goals: video, commentary audio, a hosted/public live service, sharing a token with anyone.

## 3. The curated experience

### 3.1 Moments

A moment is a thing worth a glance, derived from the data we already receive. Each has a time, the cars
involved, a one-line plain description and an interest score.

| Moment | Derived from | Needs login |
|---|---|---|
| Lead change, overtake in the top 10 | `TimingData` running order, held for a sector to ignore pit-cycle noise | no |
| Battle (interval under 1.0 s for 3 laps) | `TimingData` interval | no |
| Pit stop, undercut result | `TimingAppData`, pit stop topic | no |
| Safety car / VSC / red flag / penalty / investigation | `TrackStatus`, `RaceControlMessages` | no |
| Overall fastest lap, personal bests | `TimingData` lap colours | no |
| Team radio clip | `TeamRadio` | no |
| Retirement | `TimingData` flags | no |
| Lock-up, big lift, top speed in a battle | `CarData.z` | **yes** |

Moments are computed by one engine that runs the same way on a live feed and on a finished replay, so a
replay gets them for free and the scrubber can show where they happened.

### 3.2 The Director

An optional auto-focus. It picks the highest-scoring moment, highlights those cars on the map and in the tower,
and opens a one-line caption saying what and why ("Norris is 0.6 s behind Piastri, third lap in a row").
Rules to keep it calm: minimum dwell 12 s on a focus, a manual click always wins and pauses it for 60 s, off
by default and remembered per browser.

### 3.3 Battle view

When the Director focuses a two-car battle, it can open the existing compare card.
- Signed in: speed and throttle traces of the two cars (the compare mode that exists today, fed live).
- Signed out: a gap-history trace from timing, which is honest about having no telemetry.

### 3.4 Sign-in states

The page never touches the token. The server reports one of: `none`, `ok` (with hours left), `expiring`
(under 12 h), `expired`, `rejected`. The page shows this as a quiet status next to the clock, with the one
thing the viewer can do ("Telemetry is off. Add your F1 TV login"), and never as an error wall. Features that
need login show their signed-out alternative, not a lock icon.

## 4. Design plan (frontend-design pass)

Constraint from the brief: this extends an existing page, so it keeps that page's system rather than
introducing a second look. The page is a dark broadcast-style control room: near-black panels, F1 red accent,
tabular monospace for every number. The new work spends its boldness in **one** place: the Director's
caption and the moment markers. Everything else stays quiet.

**Color (existing tokens, reused):** `--bg #0d0e12`, `--panel #15171d`, `--line #262a34`, `--text #e9ebf0`,
`--muted #8a90a0`, `--accent #e10600`. One addition: `--focus #4cc9f0`, a cool cyan used only for
"the Director is looking here" (ring on a car dot, tower row edge, caption stripe). Red already means flag and
danger on this page, so focus must not be red or purple (purple means fastest lap).

**Type:** keep Segoe UI for words and JetBrains Mono for numbers. The caption is the one place that gets a
larger size (17 px, sentence case, medium weight) because it is the only prose on a data screen.

**Layout concept:** the track map stays the stage. The caption sits as a single line along the bottom of the
map, not a card. Moments live on a thin strip above the existing scrubber: one tick per moment, tick height =
interest, colour = kind. Hover or tap shows the line; click jumps (replay) or follows (live).

```
+-------------------------------------------------------------------+
| lap 31/57   GREEN    Sun 4 Oct   [login: telemetry on, 71 h]       |
+--------------+---------------------------------------+------------+
| tower        |                                        | feed       |
|  1 PIA +0.0  |           track map                    | radio      |
|  2 NOR +0.6 o|        (focus ring on 1, 2)            | race ctrl  |
|  3 ...       |                                        |            |
|              |  Norris closing on Piastri: 0.6 s, 3rd |            |
|              |  lap running (compare)                 |            |
+--------------+---------------------------------------+------------+
| | |  ||    |    ||  |   |  |      moments strip                    |
| ----------------o------------------------------ scrubber  LIVE    |
+-------------------------------------------------------------------+
```

**Alignment:** left-aligned everywhere; numbers right-aligned in mono, as today.

**Review against the defaults:** a cream/serif look, an acid-green accent, a card grid, and ALL-CAPS eyebrow
labels would all be wrong here and none are used. Moment kinds are told apart by shape *and* color (circle,
diamond, bar), not color alone. The sign-in status is a plain sentence, not a badge with an icon. No
entrance animation; the only motion is the focus ring easing between cars, and it respects reduced-motion
(it jumps instead).

**Build quality floor:** keyboard reachable moments (arrow keys step between them), visible focus,
`prefers-reduced-motion`, works down to a laptop-width window (the feed column collapses as it does now).

## 5. Architecture

```
F1 SignalR ──▶ live_relay (server side) ──SSE──▶ page
  token read here only        decode .z, assemble       same Stream merge as replay
  (~/.f1tv_token)             ring buffer for catch-up  + a moments engine
                              optional raw recorder
```

- **Relay in `replay_server.py`** (or a sibling module): one SignalR connection reusing
  `signalrcore_client.stream`, started on demand. Binds to localhost only, as the server does today.
  New endpoints: `GET /api/live/status` and `GET /api/live/stream` (server-sent events), both behind the
  existing `X-Replay` guard. The token is read server-side and is never sent to the page or logged.
- **Decode `.z` topics:** payloads are base64 + raw deflate JSON. Position and car data are expanded into the
  same `{t, x, y}` / `{t, speed, …}` per-car shape the replay file already uses.
- **Page:** `Stream` gains `append(entries)`; in live mode `T` follows the newest data minus a small buffer
  (default 5 s, to smooth bursts) instead of the audio clock. Everything downstream (tower, map, cards) already
  reads `stream.at(T)`, so it should not need to know it's live.
- **Signed-out positions:** reuse the Formula E estimator (fraction through the current sector) so the map is
  never empty. The map labels them "estimated" exactly as the Formula E replay does today.
- **Recorder (optional, cheap):** append every raw feed message to `recordings/<session>.jsonl`. After the
  race the build step can turn it into a replay with telemetry even if F1 never publishes it. Also removes
  the need to rerun a probe to learn anything new.
- **Simulator (essential for development):** `replay_server.py --simulate <replay.json>` streams a built race
  as if live at 1x to 20x. Live sessions are rare, so without this the feature can't be developed or tested.

## 6. Work breakdown

| # | Piece | Size | Notes |
|---|---|---|---|
| 0 | Spike: connect during the next live session, capture raw `.z` messages, confirm the decode and the shape of updates | S | needs a real session; the recorder from #3 makes it one run |
| 1 | Live relay + status + SSE + simulator | M | tests against the simulator |
| 2 | Page live mode: `append`, live clock, LIVE chip, catch-up on reconnect | M | the riskiest page change; same render path |
| 3 | Estimated positions for signed-out | S | port from `fe_convert` |
| 4 | Moments engine (pure JS module, unit-testable) | M | shared by live and replay |
| 5 | Moments strip + caption + Director + battle view | M | the visible part; built with the design plan above |
| 6 | Sign-in states, token expiry handling, README | S | |
| 7 | Raw recorder + recording-to-replay build | S | optional, high value |

Roughly 8 focused working sessions end to end. Pieces 4 and 5 are useful without any live work, because they
can be built and tuned against finished replays, so they could go first and ship on their own.

## 7. Risks and decisions

- **Unofficial feed.** F1 can change or block it; the relay must degrade to "feed unavailable", never crash.
  Personal, local use only. The live mode is not included in the shared zip, and the token never leaves
  this computer.
- **Token lifetime ~4 days.** It needs refreshing before each race weekend; the status line says when.
- **Interest scoring is taste.** The first scores will be wrong. Keep them as a small table of weights in one
  place, and tune against the Baku, Madrid and Bahrain replays rather than guessing.
- **Position estimates can mislead** (a car that stops on track keeps "moving"). Mark them clearly, and freeze
  an estimated car when it is in the pits or retired.

## 8. Open questions for the owner

1. Build order: moments and Director first (works on replays today), or the live relay first?
2. Director on or off by default?
3. Keep recordings of every live session (disk use is small, about 10 MB per race)?
