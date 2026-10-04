# Third-party notices

This project's own code is MIT-licensed (see [LICENSE](LICENSE)). It builds on the
following work, none of which is copied into this repository.

## Libraries

### LiveF1
- https://github.com/GoktugOcal/LiveF1
- MIT License, Copyright (c) 2024 Göktuğ Öcal
- Used as a Python dependency (`pip install livef1`) by `build_replay.py` to load
  sessions, car positions and telemetry, and circuit data.

### hls.js
- https://github.com/video-dev/hls.js
- Apache License 2.0, Copyright (c) 2017 Dailymotion (http://www.dailymotion.com)
- Loaded by `replay/index.html` from the jsDelivr CDN at runtime to play the
  commentary audio stream. Not bundled.

## References

### FastF1
- https://github.com/theOehrly/Fast-F1
- MIT License, Copyright (c) theOehrly
- `signalrcore_client.py` is an independent implementation, but FastF1's live timing
  client showed how F1's SignalR Core endpoint and F1 TV authentication work.

## Data and media

This is an unofficial fan project. It is not associated in any way with the Formula 1
companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX
and related marks are trade marks of Formula One Licensing B.V.

- **Formula 1 live timing data, team radio, commentary audio, driver photos and team
  logos** are the property of Formula One Management and the teams. They are fetched
  from Formula 1's servers at runtime and are not included in this repository. Exported
  race data (`replay/data/`) is for personal use and is not committed.
- **Circuit outlines and corner positions** come from the MultiViewer API
  (https://multiviewer.app), fetched at runtime via LiveF1.
