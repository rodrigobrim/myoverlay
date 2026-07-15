"""Harmonic-product-spectrum fundamental + band energy vs real speed."""
import numpy as np
from pathlib import Path
from media_tools.sync import AUDIO_RATE, FEATURE_HZ, _zscore, find_offset
from media_tools.telemetry import unified_frame
from libxrk import aim_xrk

pcm = np.load("pcm_cache.npy")
hop = int(AUDIO_RATE / FEATURE_HZ)
win = 4096  # 2 Hz bins for a 20-50 Hz fundamental search
n_frames = (len(pcm) - win) // hop + 1
window = np.hanning(win)
freqs = np.fft.rfftfreq(win, 1/AUDIO_RATE)

f0_grid = np.arange(20.0, 50.0, 0.5)  # 2400-6000 rpm on a 4-stroke single
HARMONICS = range(2, 9)

f0_l, e_band_l, rms_l = [], [], []
for start in range(0, n_frames, 2048):
    count = min(2048, n_frames - start)
    idx = np.arange(win)[None, :] + hop*(start+np.arange(count))[:, None]
    fr = pcm[idx]*window[None, :]
    rms_l.append(np.sqrt((fr**2).mean(axis=1)))
    spec = np.log1p(np.abs(np.fft.rfft(fr, axis=1)) * 1000)
    # engine band energy 50-250 Hz
    m = (freqs >= 50) & (freqs <= 250)
    e_band_l.append(spec[:, m].sum(axis=1))
    # HPS over the f0 grid using harmonics 2..8
    hps = np.zeros((count, len(f0_grid)))
    for k in HARMONICS:
        bins = np.clip(np.round(f0_grid*k*win/AUDIO_RATE).astype(int), 0, spec.shape[1]-1)
        hps += spec[:, bins]
    f0_l.append(f0_grid[np.argmax(hps, axis=1)])

f0 = np.concatenate(f0_l)
e_band = np.concatenate(e_band_l)
rms = np.concatenate(rms_l)
print("f0 stats:", f0.min(), np.median(f0), f0.max())

log = aim_xrk(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29\KGV 101 _Race_a_0081.xrk")
df = unified_frame(log)
t_end = float(df["t_s"].iloc[-1])
grid = np.arange(0, t_end, 1/FEATURE_HZ)
speed = _zscore(np.interp(grid, df["t_s"], df["speed_ms"]))

off = win/(2*AUDIO_RATE)
for name, feat in [("HPS f0", f0), ("band energy 50-250", e_band), ("rms", rms),
                   ("f0 + band", _zscore(f0)+_zscore(e_band))]:
    res = find_offset(speed, _zscore(feat), lag_window_s=(-2429, t_end), audio_time_offset_s=off)
    print(f"{name:20s} lag={res.lag_s:9.1f}s conf={res.confidence:.3f}")

# score profile near the candidate to check lap-aliasing: manual NCC around 600-700
x = np.diff(speed); y = np.diff(_zscore(f0))
num = np.correlate(x, y, "full"); n_x, n_y = len(x), len(y)
sx = np.correlate(x*x, np.ones(n_y), "full"); sy = np.correlate(np.ones(n_x), y*y, "full")
sc = num/np.sqrt(np.maximum(sx*sy, 1e-12))
lags = np.arange(-(n_y-1), n_x)/FEATURE_HZ - off
m = (lags > 550) & (lags < 750)
best = np.argsort(sc[m])[-6:]
print("top lags 550-750s:", [f"{lags[m][i]:.1f}:{sc[m][i]:.3f}" for i in sorted(best)])
