"""
Unit tests for agents.match.calibrate_score().

Pure piecewise-linear interpolation between the _CALIBRATION_ANCHORS
table. Must be monotonic non-decreasing, must clamp inputs outside
[0, 1], and must hit the anchor points exactly.

Importing agents.match is safe without sentence-transformers — the
ST import there is wrapped in try/except (EMBEDDINGS_AVAILABLE flag).
"""
import pytest

from agents.match import calibrate_score, _CALIBRATION_ANCHORS


def test_hits_anchor_points_exactly():
    for x, y in _CALIBRATION_ANCHORS:
        assert calibrate_score(x) == pytest.approx(y), f"anchor {x} -> {y}"


def test_endpoints():
    assert calibrate_score(0.0) == 0.0
    assert calibrate_score(1.0) == 0.95


def test_monotonic_non_decreasing_across_range():
    prev = -1.0
    x = 0.0
    while x <= 1.0001:
        cur = calibrate_score(x)
        assert cur >= prev - 1e-9, f"non-monotonic at x={x}: {cur} < {prev}"
        prev = cur
        x += 0.01


def test_interpolates_between_anchors():
    # Halfway between (0.48, 0.30) and (0.50, 0.40) should land at the
    # linear midpoint 0.35.
    out = calibrate_score(0.49)
    assert out == pytest.approx((0.30 + 0.40) / 2, abs=1e-6)


def test_clamps_input_below_zero():
    # Anything <= the first anchor's x clamps to the first anchor's y.
    assert calibrate_score(-5.0) == _CALIBRATION_ANCHORS[0][1]
    assert calibrate_score(-0.0001) == _CALIBRATION_ANCHORS[0][1]


def test_clamps_input_above_one():
    # Anything >= the last anchor's x clamps to the last anchor's y.
    assert calibrate_score(2.0) == _CALIBRATION_ANCHORS[-1][1]
    assert calibrate_score(1.5) == _CALIBRATION_ANCHORS[-1][1]


def test_none_input_returns_zero():
    assert calibrate_score(None) == 0.0


def test_non_numeric_input_returns_zero():
    assert calibrate_score("not a number") == 0.0


def test_output_always_in_unit_interval():
    x = -0.5
    while x <= 1.5:
        out = calibrate_score(x)
        assert 0.0 <= out <= 1.0, f"x={x} -> {out} out of [0,1]"
        x += 0.05


def test_stretches_bunched_midrange():
    # The whole point of the table: a small raw delta in the 0.45-0.55
    # clustering zone produces a larger calibrated delta.
    raw_spread = calibrate_score(0.55) - calibrate_score(0.45)
    assert raw_spread > (0.55 - 0.45)  # display spread wider than raw
