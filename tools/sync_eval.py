"""Sync accuracy evaluation harness (run from the repo root: uv run python tools/sync_eval.py).

Sweeps the full 3-stage estimator over synthetic sessions with known
off-grid offsets, plus the pit rev-kick guardrail scenario. Target: <=5 ms.
The pytest suite asserts the same bounds; this harness prints the numbers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tests"))

import numpy as np
import pandas as pd

from media_tools.sync import FEATURE_HZ, estimate_offset
from test_sync import synth_audio_from_rpm, synth_rpm_profile

print("=== accuracy sweep (clean driving) ===")
errs = []
for seed, true_lag in [(7, 84.3123), (11, 33.017), (23, 140.5049), (5, 61.111), (31, 99.9871), (42, 120.0301)]:
    rpm = synth_rpm_profile(300, seed=seed)
    audio = synth_audio_from_rpm(rpm, start_s=true_lag, duration_s=120.0)
    df = pd.DataFrame({"t_s": np.arange(len(rpm)) / FEATURE_HZ, "rpm": rpm})
    r = estimate_offset(df, audio, true_lag + 40.0, 120.0)
    err = abs(r.lag_s - true_lag) * 1000
    errs.append(err)
    print(f"  seed {seed}: {err:6.2f} ms  conf {r.confidence:.2f} method {r.method}")
print(f"  max {max(errs):.2f} ms, mean {np.mean(errs):.2f} ms")

print("=== rev-kick guardrail scenario ===")
rng = np.random.default_rng(3)
n_pit = int(90 * FEATURE_HZ)
pit = np.full(n_pit, 1800.0) + rng.normal(0, 30, n_pit)
for k0 in (20.0, 45.0, 70.0):
    i0 = int(k0 * FEATURE_HZ)
    pit[i0 : i0 + int(1.5 * FEATURE_HZ)] = 9000.0
drive = synth_rpm_profile(300, seed=13)
rpm_all = np.concatenate([pit, drive])
df = pd.DataFrame({"t_s": np.arange(len(rpm_all)) / FEATURE_HZ, "rpm": rpm_all})

true_lag = 30.0
audio = synth_audio_from_rpm(rpm_all, start_s=true_lag, duration_s=260.0)
r = estimate_offset(df, audio, true_lag + 20.0, 120.0)
err = abs(r.lag_s - true_lag) * 1000
print(f"  with kicks: err {err:6.2f} ms  conf {r.confidence:.2f} method {r.method}")
assert err <= 5.0, "guardrail scenario failed"
print("OK")
