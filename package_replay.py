"""
Package the replay into a zip someone else can run: page, exported data, server, launchers.

Usage (from the repo root, after build_replay.py):
    python package_replay.py                     # every export in replay/data
    python package_replay.py 2026_Baku_Race      # just this one

Writes dist/F1-Race-Replay.zip. The person receiving it needs Python 3 installed, then
double-clicks the launcher for their system.
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "replay", "data")
OUT = os.path.join(HERE, "dist", "F1-Race-Replay.zip")
TOP = "F1 Race Replay/"

WINDOWS_LAUNCHER = r"""@echo off
title F1 Race Replay
cd /d "%~dp0"
rem Prefer the "py" launcher that python.org's installer adds; fall back to "python".
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 replay_server.py --open
  goto done
)
where python >nul 2>nul
if %errorlevel%==0 (
  python replay_server.py --open
  goto done
)
echo.
echo Python isn't installed. Get it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during setup), then double-click this again.
:done
echo.
pause
"""

MAC_LAUNCHER = """#!/bin/bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  python3 replay_server.py --open
else
  echo
  echo "Python isn't installed. Get it from https://www.python.org/downloads/"
  echo "then double-click this again."
fi
echo
read -n 1 -s -r -p "Press any key to close this window"
"""

README = """F1 RACE REPLAY
==============

A spoiler-free replay with the live commentary, timing tower, track map,
driver cards, team radio and more.

ONE-TIME SETUP
  Install Python 3 from https://www.python.org/downloads/
  - Windows: on the first installer screen, tick "Add python.exe to PATH".
  - Mac: run the installer normally.

TO WATCH
  First unzip the folder (right-click the zip > Extract All). It won't run
  from inside the zip.

  - Windows: double-click "Start Replay (Windows).bat"
    If a blue "Windows protected your PC" box appears, click "More info",
    then "Run anyway". It only asks the first time.
  - Mac: double-click "Start Replay (Mac).command"
    The first time, macOS may say it's from an unidentified developer.
    If so: right-click the file, choose Open, then Open again.

  Your browser opens on the replay. Keep the small black window open while
  you watch; closing it stops the replay.

  You need to be online: the commentary, radio and photos stream from F1.

HANDY CONTROLS
  Space            play / pause
  Left / Right     back / forward 10 seconds (hold Shift for 1 minute)
  Races (top left) switch between the races in this folder
  Click a driver   their card; Shift+click a second driver to compare
  Esc              close driver cards
  "Lights out"     jump to the start of the race

Races included: {races}
"""


def main(only=None):
    exports = sorted(f[:-5] for f in os.listdir(DATA) if f.endswith(".json") and not f.endswith("_car.json"))
    if only:
        exports = [e for e in exports if e in only]
    if not exports:
        sys.exit("No exports found. Run build_replay.py first.")
    # the page opens data/2026_Baku_Race.json by default; other races are ?data=data/<name>.json
    races = "; ".join(exports)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(os.path.join(HERE, "replay_server.py"), TOP + "replay_server.py")
        z.write(os.path.join(HERE, "race_catalog.py"), TOP + "race_catalog.py")  # imported by the server
        # shipping the code means shipping its license and credits
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            if os.path.exists(os.path.join(HERE, name)):
                z.write(os.path.join(HERE, name), TOP + name)
        for name in ("index.html", "moments.js"):
            z.write(os.path.join(HERE, "replay", name), TOP + "replay/" + name)
        for name in exports:
            for suffix in (".json", "_car.json"):
                path = os.path.join(DATA, name + suffix)
                if os.path.exists(path):
                    z.write(path, TOP + "replay/data/" + name + suffix)

        # Windows wants CRLF in .bat files
        z.writestr(TOP + "Start Replay (Windows).bat", WINDOWS_LAUNCHER.replace("\n", "\r\n"))
        # Mac: LF line endings, and mark it executable (unix permissions live in the
        # high 16 bits of external_attr; create_system=3 says "made on unix")
        mac = zipfile.ZipInfo(TOP + "Start Replay (Mac).command")
        mac.create_system = 3
        mac.external_attr = (0o100755 << 16)
        mac.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(mac, MAC_LAUNCHER)
        z.writestr(TOP + "READ ME.txt", README.format(races=races).replace("\n", "\r\n"))

    print(f"Wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB) with: {races}")


if __name__ == "__main__":
    main(set(sys.argv[1:]) or None)
