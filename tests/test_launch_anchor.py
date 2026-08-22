"""The manual-sync anchor: the LAUNCH, not the start/finish crossing.

`mt sync DAY --video X --at MM:SS` asks where in the video the revs rise and
the kart pulls away, because that is the one instant a person can point at in
the footage and that the telemetry also records. It aligns the two clocks -
nothing more. Where the race starts is a separate question answered by the
start/finish line (tests/test_relap.py), which lies ahead of every grid slot.
"""

import numpy as np
import pandas as pd
import pytest

from media_tools.sync import engine_launches


def trace(segments) -> pd.DataFrame:
    """Build a 10 Hz engine trace from (duration_s, speed_ms) segments."""
    t, v = [], []
    now = 0.0
    for dur, speed in segments:
        n = int(dur * 10)
        t.extend(now + np.arange(n) / 10.0)
        v.extend([speed] * n)
        now += dur
    return pd.DataFrame({"t_s": t, "speed_ms": v})


def test_launch_is_where_the_revs_rise():
    """Standing for a minute, then pulling away and driving."""
    df = trace([(60.0, 0.0), (120.0, 20.0)])
    launches = engine_launches(df)
    assert len(launches) == 1
    assert launches[0] == pytest.approx(60.0, abs=1.0)


def test_launch_is_the_onset_not_the_threshold_crossing():
    """A launch ramps up; the anchor is where it leaves idle, not where it
    passes half throttle - that is the moment you hear in the video."""
    ramp = [(0.1, s) for s in np.linspace(0.0, 20.0, 50)]  # 5 s ramp
    df = trace([(30.0, 0.0)] + ramp + [(60.0, 20.0)])
    launches = engine_launches(df)
    assert len(launches) == 1
    # Onset at ~30 s (the foot going down), not ~32.5 s (mid-ramp).
    assert launches[0] == pytest.approx(30.0, abs=1.0)


def test_out_lap_and_race_start_are_separate_launches():
    """A session with a pit out-lap and then a grid start offers two
    anchors; --launch N (or the camera clock) picks between them."""
    df = trace([(30.0, 0.0), (60.0, 18.0), (90.0, 0.0), (120.0, 20.0)])
    launches = engine_launches(df)
    assert len(launches) == 2
    assert launches[0] == pytest.approx(30.0, abs=1.0)
    assert launches[1] == pytest.approx(180.0, abs=1.0)


def test_a_rev_blip_in_the_pits_is_not_a_launch():
    """Blipping the throttle while parked is not pulling away: too short to
    be a sustained run, so it never becomes the anchor."""
    df = trace([(30.0, 0.0), (2.0, 15.0), (30.0, 0.0), (120.0, 20.0)])
    launches = engine_launches(df)
    assert len(launches) == 1
    assert launches[0] == pytest.approx(62.0, abs=1.5)


def test_no_launch_when_the_kart_never_moves():
    assert engine_launches(trace([(60.0, 0.0)])) == []


def test_empty_frame_has_no_launch():
    assert engine_launches(pd.DataFrame(columns=["t_s", "speed_ms"])) == []
