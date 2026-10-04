"""
A circuit outline from where the cars actually drove.

The map service LiveF1 uses doesn't have every circuit (new ones are missing for a while), but
the position data of any race contains the circuit: one clean lap of one car traces it.
Used by build_replay.py as a fallback; the outline has no corner numbers.
"""
import numpy as np


def outline_from_positions(positions, points=800, starts=(0.2, 0.3, 0.4, 0.5, 0.6)):
    """({x}, {y}) lists tracing one lap in the direction of travel, or None if no lap can be found.

    positions: {car: {"t": [...], "x": [...], "y": [...]}} as build_replay.py produces.
    Takes the car with the most samples and finds laps starting at several points of the race (a lap
    ends when the car comes back to where it started). Picking the median-length lap skips a lap that
    includes a pit stop or an off.
    """
    if not positions:
        return None
    car = max(positions, key=lambda n: len(positions[n]["t"]))
    x = np.asarray(positions[car]["x"], float)
    y = np.asarray(positions[car]["y"], float)
    if len(x) < 200:
        return None
    extent = max(np.ptp(x), np.ptp(y))
    if extent <= 0:
        return None

    laps = []
    for frac in starts:
        s = int(len(x) * frac)
        d = np.hypot(x[s:] - x[s], y[s:] - y[s])
        far = np.nonzero(d > 0.25 * extent)[0]          # the car has gone round the circuit...
        if not len(far):
            continue
        back = np.nonzero(d[far[0]:] < 0.03 * extent)[0]  # ...and is back at the start
        if not len(back):
            continue
        laps.append((x[s:s + far[0] + back[0] + 1], y[s:s + far[0] + back[0] + 1]))
    if not laps:
        return None

    lengths = [float(np.hypot(np.diff(lx), np.diff(ly)).sum()) for lx, ly in laps]
    lx, ly = laps[sorted(range(len(laps)), key=lambda i: lengths[i])[len(laps) // 2]]

    # resample evenly along the path, so sector boundaries found by index land in sensible places
    dist = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(lx), np.diff(ly)))])
    grid = np.linspace(0, dist[-1], points, endpoint=False)
    return (np.interp(grid, dist, lx).round().astype(int).tolist(),
            np.interp(grid, dist, ly).round().astype(int).tolist())
