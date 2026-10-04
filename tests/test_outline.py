"""Tests for outline.outline_from_positions: tracing a circuit from car positions."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outline import outline_from_positions  # noqa: E402

A, B = 8000, 3000   # a long oval: 0.1 m units, so roughly 1.5 km x 0.6 km


def drive(laps=10, per_lap=120, pit_lap=None, jitter=0.0, seed=1):
    """One car going round an ellipse; on pit_lap it cuts across the middle (a pit lane)."""
    rng = np.random.default_rng(seed)
    xs, ys = [], []
    for lap in range(laps):
        for k in range(per_lap):
            a = 2 * math.pi * k / per_lap
            r = 0.3 if lap == pit_lap and 0.25 < k / per_lap < 0.45 else 1.0
            xs.append(A * r * math.cos(a) + rng.normal(0, jitter))
            ys.append(B * r * math.sin(a) + rng.normal(0, jitter))
    return {"t": list(range(len(xs))), "x": [round(v) for v in xs], "y": [round(v) for v in ys]}


def distance_to_ellipse(x, y):
    t = np.linspace(0, 2 * math.pi, 2000)
    return float(np.hypot(A * np.cos(t) - x, B * np.sin(t) - y).min())


def test_traces_one_lap_of_the_circuit():
    xs, ys = outline_from_positions({"1": drive()})
    assert len(xs) == len(ys) == 800
    assert max(distance_to_ellipse(x, y) for x, y in zip(xs, ys)) < 150      # within 15 m
    assert max(xs) > 0.95 * A and min(xs) < -0.95 * A                         # covers the whole circuit


def test_points_are_evenly_spaced_and_follow_the_direction_of_travel():
    xs, ys = outline_from_positions({"1": drive()})
    steps = np.hypot(np.diff(xs), np.diff(ys))
    assert steps.std() / steps.mean() < 0.15
    # the car drives anticlockwise, so the traced path turns the same way (positive area)
    area = 0.5 * sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i] for i in range(len(xs)))
    assert area > 0


def test_a_lap_with_a_pit_stop_does_not_spoil_the_outline():
    # one of the candidate laps cuts through the middle; the median-length lap is a clean one
    xs, ys = outline_from_positions({"1": drive(laps=12, pit_lap=5)})
    assert max(distance_to_ellipse(x, y) for x, y in zip(xs, ys)) < 150


def test_noisy_positions_still_give_a_usable_outline():
    xs, ys = outline_from_positions({"1": drive(jitter=40)})
    assert np.median([distance_to_ellipse(x, y) for x, y in zip(xs, ys)]) < 100


def test_uses_the_car_with_the_most_samples():
    short = {"t": [0, 1], "x": [0, 1], "y": [0, 1]}
    xs, ys = outline_from_positions({"2": short, "1": drive()})
    assert max(xs) > 0.95 * A


def test_returns_none_when_no_lap_can_be_found():
    assert outline_from_positions({}) is None
    assert outline_from_positions({"1": {"t": [0, 1, 2], "x": [0, 1, 2], "y": [0, 1, 2]}}) is None
    # a car that never completes a lap
    straight = {"t": list(range(300)), "x": list(range(0, 3000, 10)), "y": [0] * 300}
    assert outline_from_positions({"1": straight}) is None
