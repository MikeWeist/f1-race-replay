# F1 Race Replay

A spoiler-free replay of a finished Formula 1 session in your browser, synced to the
broadcast's live commentary: timing tower, animated track map, driver cards with live
telemetry, team radio, race control, tyre strategy and a live championship projection.
Plus a small team radio player for live sessions.

*Unofficial fan project, not affiliated with Formula 1. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).*

## Setup

Requires Python 3.11+ (for exporting races; the replay server alone runs on 3.7+).

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt      # Windows
# .venv/bin/python -m pip install -r requirements.txt        # macOS / Linux
```

## Watch a race

1. **Export it** (downloads F1's timing data; a race takes about a minute):

   ```bash
   .venv\Scripts\python build_replay.py 2026 Baku Race
   ```

   Writes `replay/data/2026_Baku_Race.json` plus a `_car.json` telemetry file.

2. **Start the replay** and open http://localhost:8765:

   ```bash
   .venv\Scripts\python replay_server.py --open
   ```

   The page opens `2026_Baku_Race` by default; open any other export with
   `http://localhost:8765/?data=data/<name>.json`.

It only ever shows data up to the current playback moment, so it's safe to watch
without knowing the result.

Starting before the race? Add `--wait` and the exporter checks F1's calendar every minute,
waits until the data is marked complete, then builds:

```bash
.venv\Scripts\python build_replay.py 2026 "Kuala Lumpur" Race --wait
```

### Choosing a race

The **Races** button in the header opens the season as a calendar. The bar on each row's left
edge says what you can do with it:

| Bar | Meaning | Action |
|---|---|---|
| Solid green | Built | **Watch** |
| Hatched | F1 has published complete data | **Build** (about a minute, on this computer) |
| Yellow | The race has started but F1's data isn't complete | Check back later |
| Plain | Hasn't happened yet | Shows a countdown |
| Red | The race that's playing now | |

Opening the page with no race chosen opens the last one you picked, or the newest you've built.
In the shared zip the list shows only the races inside it. If a circuit isn't in the outline
service LiveF1 uses (new circuits often aren't), the exporter traces it from where the cars drove.

### Moments and the Director

Under the timeline, a strip marks the moments worth a glance, **only the ones that have already
happened**, so it never spoils what's coming. Hover one to read it, click to jump to just before it, or
Tab to the strip and use the arrow keys and Enter.

| Mark | Moment |
|---|---|
| Diamond | Overtake in the top ten, or a lead change |
| Amber dot | Battle: within a second of the car ahead for a couple of laps |
| Purple dot | New fastest lap |
| Grey dot | Pit stop |
| Green dot | Team radio |
| Bar | Safety car, VSC, red flag, penalty or investigation, retirement, restart |

Taller marks mattered more. The weights live in one table at the top of `replay/moments.js`.

**Director** (button in the footer, or `D`) is off by default and remembers your choice. When it's on, the
cars in the most interesting moment get a cyan ring on the map and a cyan edge in the timing tower, and a
caption says what's happening ("Verstappen takes the lead from Antonelli"); a **Compare** button on the
caption opens those two drivers' cards side by side. It holds each focus for at least 12 seconds, and
picking a driver yourself pauses it for a minute.

### Controls

| | |
|---|---|
| Space | play / pause |
| ← / → | back / forward 10 s (Shift: 1 min) |
| Click a driver | full driver card |
| Shift+click a second driver | compare two drivers' telemetry |
| Esc | close driver cards |
| Top bar | track sector overlay, units, tyre strategy, championship |

## Formula E (past races)

Formula E's live timing feed keeps the latest event's sessions and serves each one by ID, so a
finished race can be replayed even if you never recorded it live:

```bash
.venv\Scripts\python build_fe_replay.py --list      # the sessions the feed currently stores
.venv\Scripts\python build_fe_replay.py             # the Race of the latest event
```

Then open `http://localhost:8765/?data=data/fe_2026_london_race.json` (the script prints the
exact address).

**You get:** the timing tower, rebuilt from every sector crossing (positions, gaps, intervals,
sector and lap colours, personal bests, pit stops), Attack Mode, race control, the circuit, and
car positions.

**You don't get:** commentary, team radio, car telemetry or tyres (Formula E publishes none).

**Car positions are estimates.** The stored data has sector times, not tracking, so each car is
placed by how far through its current sector it is (the page says so). Everything else is the
recorded data: for the London E-Prix the rebuilt final order, lap counts and fastest lap match
the official classification.

**Limits:** the feed only lists the most recent event; only races are supported; the feed is
unofficial and can change without notice. `fe_feed.py` drops the API token that the feed
publishes the moment it arrives, so it is never stored or used. Downloads are cached in
`fe_cache/` (not committed).

## Tests

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests
```

## Share it

```bash
.venv\Scripts\python package_replay.py
```

Builds `dist/F1-Race-Replay.zip` with the page, your exported races, the server and
double-click launchers for Windows and macOS. The person receiving it only needs
Python 3 installed.

## Recording live sessions

```bash
.venv\Scripts\python record_live.py
```

Leave it running. It connects to F1's live feed (using your saved F1 TV token, if any, which is what
makes F1 send car positions and telemetry) and writes every message to `recordings/<session>.jsonl`,
starting a new file whenever F1 moves to a new session. A session that was already over when it started
isn't recorded, and it reconnects on its own if the connection drops. About 10-20 MB per race. The files
are the raw feed, so a recorded session can later be turned into a replay with the telemetry F1 never
publishes afterwards. Only one recorder runs at a time; `recordings/` is not committed.

The token lasts about four days: before a race weekend, re-save it (see below) and restart the recorder.

## Team radio during a live session

```bash
.venv\Scripts\python team_radio.py live      # F1's live feed
.venv\Scripts\python team_radio.py poll      # fallback: poll the published radio list
.venv\Scripts\python team_radio.py replay 2026/2026-09-26_Azerbaijan_Grand_Prix/2026-09-24_Practice_2/
```

`live` works without logging in. With an F1 TV subscription it can also send your
token: save the value of formula1.com's `login-session` cookie to `~/.f1tv_token`
(outside the repo, so it can't be committed).

## F1 TV login (optional)

Past races need no login. A login may matter for **live** data. `probe_live.py` shows what F1's
live feed gives with and without one:

1. Log in at formula1.com, open the browser's developer tools (F12) and go to the **Console**. Run
   this one line, which copies your `login-session` cookie to the clipboard (it sends nothing
   anywhere; Chrome may ask you to type `allow pasting` first):

   ```js
   copy(document.cookie.split('; ').find(c => c.startsWith('login-session=')).slice(14))
   ```

   Don't copy the value out of the Application tab's cookie table instead: it **truncates long
   values**, and a cut-off token can't log in (the tools tell you when that has happened).
   (The Console prints `undefined` afterwards. That's normal: `copy()` always does.)
2. Save it. This checks the clipboard first and only writes `~/.f1tv_token` (outside every repo) if
   it holds a complete token, so a bad copy can't overwrite a good one. Nothing is shown on screen:

   ```bash
   python probe_live.py --save-token
   ```

3. `python probe_live.py --token-info` checks it and shows its plan details (never the token).
4. `python probe_live.py --minutes 120` runs through a live session, connecting with and without
   the token, and compares how much of each topic arrives.

**What the login unlocks (measured through the 4 Oct 2026 Bahrain race with an active F1 TV Premium
account):** F1's live feed accepts the token, and four topics reach only logged-in connections:
**`CarData.z`** (speed, throttle, brake, RPM, gear), **`Position.z`** (car positions on track),
**`DriverRaceInfo`** (running order, gaps, and an `OvertakeState` value per car) and
**`ChampionshipPrediction`**. Every other topic, including `TeamRadio`, is identical either way.
(Checking only the state F1 sends on connect misses the last two; they differ in the live updates.)
Past races need neither: F1 publishes all of these publicly afterwards.

The token is read from `~/.f1tv_token`, never from the repo; there is no `.env` file. It lasts
about four days. The probe keeps only counts, never the token or any feed data.

## How it works

- **`build_replay.py`** loads a session through [LiveF1](https://github.com/GoktugOcal/LiveF1)
  and F1's raw live timing streams, and writes everything as timestamped updates.
  F1 doesn't publish where sectors or the pit lane are, so it works those out from the
  race: mini-sector boundaries from where cars were when each mini-sector completed,
  and the pit lane from where cars drove during their pit stops.
- **`replay_server.py`** serves the page and relays the commentary audio, because
  F1's servers don't allow a web page to load it directly.
- **`replay/index.html`** replays every stream against the commentary's clock.
- **`replay/moments.js`** finds the moments (overtakes, battles, pit stops, flags, penalties) by walking
  the timing updates in order, and picks what the Director focuses on. No page code in it, so
  `tests/test_moments.py` runs the same file under node.
- **`record_live.py`** is the live recorder.
- **`fe_feed.py`**, **`fe_convert.py`** and **`build_fe_replay.py`** do the same for Formula E:
  fetch a stored session, rebuild the timeline from its lap records, and write the same replay
  format (the page switches to a Formula E profile).

## Credits

- [LiveF1](https://github.com/GoktugOcal/LiveF1) by Göktuğ Öcal (MIT): session and
  telemetry loading
- [hls.js](https://github.com/video-dev/hls.js) (Apache 2.0): commentary playback
- [FastF1](https://github.com/theOehrly/Fast-F1) by theOehrly (MIT): reference for
  the SignalR Core live timing connection
- [MultiViewer](https://multiviewer.app): circuit outlines and corner positions
- Formula 1: all timing data, audio and media
- Formula E and Al Kamel Systems: the live timing data behind the Formula E replays

## License

MIT, see [LICENSE](LICENSE). This covers this project's code only, not Formula 1's
data or media.
