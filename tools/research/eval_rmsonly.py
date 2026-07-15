import sys
sys.path.insert(0, "tests")
import numpy as np, pandas as pd
import media_tools.sync as S
from test_sync import synth_rpm_profile, synth_audio_from_rpm

orig = S._frame_features

def rms_only(pcm, rate, hop, win, chunk_frames=8192):
    rms, peak = orig(pcm, rate, hop, win, chunk_frames)
    return rms, rms  # combined feature collapses to z(rms)

cases = [(7, 84.3123), (11, 33.017), (23, 140.5049), (5, 61.111), (31, 99.9871), (42, 120.0301)]
for label, feat in [("rms+peak", orig), ("rms-only", rms_only)]:
    S._frame_features = feat
    errs = []
    for seed, true_lag in cases:
        rpm = synth_rpm_profile(300, seed=seed)
        audio = synth_audio_from_rpm(rpm, start_s=true_lag, duration_s=120.0)
        df = pd.DataFrame({"t_s": np.arange(len(rpm)) / S.FEATURE_HZ, "rpm": rpm})
        r = S.estimate_offset(df, audio, true_lag + 40.0, 120.0)
        errs.append(abs(r.lag_s - true_lag) * 1000)
    print(f"{label:10s} max {max(errs):6.2f} ms  mean {np.mean(errs):5.2f} ms  " + " ".join(f"{e:5.1f}" for e in errs))
S._frame_features = orig
