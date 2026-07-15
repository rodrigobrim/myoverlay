import sys
sys.path.insert(0, "tests")
import numpy as np, pandas as pd
from test_sync import synth_rpm_profile, synth_audio_from_rpm
from media_tools.sync import (
    AUDIO_RATE, FEATURE_HZ, FINE_WIN, _frame_features, _highpass, _zscore,
    audio_feature, find_offset, rpm_feature,
)

FS = 200.0
HOP = int(AUDIO_RATE / FS)

def fine_scores(df, pcm, coarse_lag, seg_start_s, seg_len_s, win=FINE_WIN, window_s=3.0, smooth_n=9):
    seg = pcm[int(seg_start_s*AUDIO_RATE): int((seg_start_s+seg_len_s)*AUDIO_RATE)]
    rms, peak = _frame_features(seg, AUDIO_RATE, HOP, win)
    y = _highpass(_zscore(_zscore(rms)+_zscore(peak)), FS)
    if smooth_n:
        k = np.ones(smooth_n)/smooth_n
        y = np.convolve(np.pad(y, smooth_n//2, mode="reflect"), k, mode="valid")
    t0 = coarse_lag + seg_start_s - window_s
    t1 = t0 + len(y)/FS + 2*window_s
    grid = np.arange(t0, t1, 1/FS)
    rpm = np.interp(grid, df["t_s"].to_numpy(), df["rpm"].to_numpy(float))
    x = _highpass(_zscore(rpm), FS)
    num = np.correlate(x, y, "valid")
    sx = np.correlate(x*x, np.ones(len(y)), "valid")
    sy = float((y*y).sum())
    score = num/np.sqrt(np.maximum(sx*sy, 1e-12))
    lag0 = t0 - seg_start_s - win/(2*AUDIO_RATE)
    return score, lag0  # lag for index k = lag0 + k/FS

def peak_parabola(score, lag0):
    k = int(np.argmax(score))
    lag = lag0 + k/FS
    if 0 < k < len(score)-1:
        a, b, c = score[k-1], score[k], score[k+1]
        d = a - 2*b + c
        if abs(d) > 1e-12:
            lag += np.clip(0.5*(a-c)/d, -1, 1)/FS
    return lag, score[k]

def peak_centroid(score, lag0, half=20):
    k = int(np.argmax(score))
    lo, hi = max(0, k-half), min(len(score), k+half+1)
    w = score[lo:hi] - score[lo:hi].min()
    lags = lag0 + np.arange(lo, hi)/FS
    return float((w*lags).sum()/w.sum()), score[k]

def coarse(df, pcm, est, tol):
    return find_offset(rpm_feature(df), audio_feature(pcm),
                       lag_window_s=(est-tol, est+tol),
                       audio_time_offset_s=2048/(2*AUDIO_RATE)).lag_s

cases = [(7, 84.3123), (11, 33.017), (23, 140.5049), (5, 61.111), (31, 99.9871), (42, 120.0301)]
for label, fn, kw in [
    ("parab seg60", peak_parabola, dict(seg_len_s=60)),
    ("parab seg120", peak_parabola, dict(seg_len_s=120)),
    ("centroid seg60", peak_centroid, dict(seg_len_s=60)),
    ("centroid seg120", peak_centroid, dict(seg_len_s=120)),
]:
    errs = []
    for seed, true_lag in cases:
        rpm_t = synth_rpm_profile(300, seed=seed)
        pcm = synth_audio_from_rpm(rpm_t, start_s=true_lag, duration_s=120.0)
        df = pd.DataFrame({"t_s": np.arange(len(rpm_t))/FEATURE_HZ, "rpm": rpm_t})
        cl = coarse(df, pcm, true_lag+40, 120)
        seg_len = min(kw["seg_len_s"], 120.0)
        score, lag0 = fine_scores(df, pcm, cl, 0.0, seg_len)
        lag, conf = fn(score, lag0)
        errs.append(abs(lag-true_lag)*1000)
    print(f"{label:16s} max {max(errs):6.2f} ms  mean {np.mean(errs):6.2f} ms  " +
          " ".join(f"{e:5.1f}" for e in errs))

# multi-segment median (3 x 40s thirds), parabola + centroid
for label, fn in [("parab 3seg med", peak_parabola), ("centr 3seg med", peak_centroid)]:
    errs = []
    for seed, true_lag in cases:
        rpm_t = synth_rpm_profile(300, seed=seed)
        pcm = synth_audio_from_rpm(rpm_t, start_s=true_lag, duration_s=120.0)
        df = pd.DataFrame({"t_s": np.arange(len(rpm_t))/FEATURE_HZ, "rpm": rpm_t})
        cl = coarse(df, pcm, true_lag+40, 120)
        lags = []
        for s0 in (0.0, 40.0, 80.0):
            score, lag0 = fine_scores(df, pcm, cl, s0, 40.0)
            lag, conf = fn(score, lag0)
            lags.append(lag)
        errs.append(abs(np.median(lags)-true_lag)*1000)
    print(f"{label:16s} max {max(errs):6.2f} ms  mean {np.mean(errs):6.2f} ms  " +
          " ".join(f"{e:5.1f}" for e in errs))
