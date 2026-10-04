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

### Controls

| | |
|---|---|
| Space | play / pause |
| ← / → | back / forward 10 s (Shift: 1 min) |
| Click a driver | full driver card |
| Shift+click a second driver | compare two drivers' telemetry |
| Esc | close driver cards |
| Top bar | track sector overlay, units, tyre strategy, championship |

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

## How it works

- **`build_replay.py`** loads a session through [LiveF1](https://github.com/GoktugOcal/LiveF1)
  and F1's raw live timing streams, and writes everything as timestamped updates.
  F1 doesn't publish where sectors or the pit lane are, so it works those out from the
  race: mini-sector boundaries from where cars were when each mini-sector completed,
  and the pit lane from where cars drove during their pit stops.
- **`replay_server.py`** serves the page and relays the commentary audio, because
  F1's servers don't allow a web page to load it directly.
- **`replay/index.html`** replays every stream against the commentary's clock.

## Credits

- [LiveF1](https://github.com/GoktugOcal/LiveF1) by Göktuğ Öcal (MIT): session and
  telemetry loading
- [hls.js](https://github.com/video-dev/hls.js) (Apache 2.0): commentary playback
- [FastF1](https://github.com/theOehrly/Fast-F1) by theOehrly (MIT): reference for
  the SignalR Core live timing connection
- [MultiViewer](https://multiviewer.app): circuit outlines and corner positions
- Formula 1: all timing data, audio and media

## License

MIT, see [LICENSE](LICENSE). This covers this project's code only, not Formula 1's
data or media.
