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

1. Log in at formula1.com, open the browser's developer tools (F12), go to Application, Cookies,
   `https://www.formula1.com`, and copy the value of the **`login-session`** cookie.
2. Save it where no repo can pick it up (this reads the clipboard, so nothing is shown on screen):

   ```bash
   Get-Clipboard | Set-Content $HOME\.f1tv_token
   ```

3. `python probe_live.py --token-info` checks it and shows its plan details (never the token).
4. `python probe_live.py --minutes 120` runs through a live session, connecting with and without
   the token, and compares how much of each topic arrives.

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
