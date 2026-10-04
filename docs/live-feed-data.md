# What F1's live feed carries, and what needs a login

Measured on 4 Oct 2026. Two connections at the same time, one without a token and one with an F1 TV
Premium token, each subscribed to every topic this project knows of (`probe_live.py` lists them). Field
lists come from decoding a snapshot of each topic (`.z` topics are base64 + raw deflate JSON); "live"
counts come from the full Bahrain race run (`probe_live_20261004_0320.json`, 2.5 h).

## Needs the login (not sent at all without it)

| Topic | Fields | Live volume (Bahrain) |
|---|---|---|
| `CarData.z` | per car: RPM (channel 0), speed km/h (2), gear (3), throttle % (4), brake (5). About every 0.24 s. | ~9,000 updates, 5.5 MB |
| `Position.z` | per car: X, Y, Z, and a Status (`OnTrack`, ...). About every 0.24 s. | ~9,000 updates, 6.7 MB |
| `DriverRaceInfo` | per car: Position, Gap, Interval, Catching, `OvertakeState` (0, 1 or 2), IsOut, PitStops | ~9,700 updates, 0.5 MB |
| `ChampionshipPrediction` | per driver and team: current and predicted points and position | 57 updates |

## Sent to everyone, identical with or without the login

| Topic | What it holds |
|---|---|
| `TimingData` (and `TimingDataF1`, the same content) | per car: position, running order (Line), gap to leader, interval to car ahead (+ Catching), last and best lap (with overall/personal fastest flags), three sectors and their mini-sector segments, pit state (InPit, PitOut, number of stops), retired/stopped, laps completed |
| `TimingAppData` | per car: grid position, every tyre stint (compound, new/used, laps on it, start lap, lap flags) |
| `TimingStats` | personal best lap per car with rank, best sectors with rank, best speeds (finish line, intermediate 1 and 2, speed trap) with rank |
| `TopThree` | the top three as shown on the broadcast: names, team, diff to leader and to car ahead, lap time state |
| `DriverTracker` | running order and diffs (a lighter copy of timing) |
| `DriverList` | number, name, abbreviation, team, team colour, headshot path |
| `LapSeries` | per car: position at the end of every lap |
| `TyreStintSeries`, `CurrentTyres` | tyre stints per car; the tyre each car is on now |
| `SessionInfo`, `SessionStatus`, `SessionData`, `ArchiveStatus` | meeting, circuit, session type and times, Inactive/Started/Finished/Finalised/Ends, track status and lap series |
| `TrackStatus` | clear, yellow, safety car, red flag, VSC |
| `LapCount`, `ExtrapolatedClock` | current and total laps; session clock |
| `RaceControlMessages`, `TlaRcm` | stewards' and race control messages with lap and time |
| `TeamRadio` | clip paths per driver with time |
| `WeatherData`, `WeatherDataSeries` | air and track temperature, humidity, pressure, rain, wind speed and direction; history |
| `AudioStreams`, `ContentStreams` | the commentary stream address and the list of F1 TV content streams |
| `PitLaneTimeCollection` | pit lane times (empty in the snapshot we saw) |
| `Heartbeat` | a ping |

## Subscribed to, nothing sent in either case

`PitStop`, `PitStopSeries`, `OvertakeSeries`, `DriverScore`, `SPFeed`. They may carry data during a live
session; the Bahrain probe never saw any. (Pit stop data still arrives inside `TimingData`.)

## What the feed does not carry

Battery or energy state, ERS deployment, active aero mode, g-force, tyre temperatures or pressures, fuel,
DRS (the 2026 cars don't have it), onboard video. The commentary audio is in F1's public archive; video
is F1 TV's own player service.

## `OvertakeState`, checked against telemetry

Values 0, 1 and 2 per car. In the Bahrain race state 1 holds about 98% of the time, state 2 about 1%
(each stretch lasting about 5 s, never over 10 s), state 0 about 1%. Compared with the cars' own
telemetry: state 2 shows no deployment signature (no extra acceleration at full throttle; speed and
throttle in those windows are lower than average), and it clusters just after the start/finish line
through the first braking zone, with state 0 clustering just before the line. It also does not follow the
interval to the car ahead. So it is not a measure of power or boost in use, and what it flags is
unconfirmed. It is not shown anywhere in the replay.
