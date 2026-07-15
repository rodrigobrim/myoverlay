"""Which audio feature of the real DJI clip correlates with real kart speed?"""
import numpy as np
from pathlib import Path
from media_tools.sync import (
    AUDIO_RATE, FEATURE_HZ, _frame_features, _zscore, extract_audio_pcm, find_offset,
)
from media_tools.telemetry import unified_frame
from libxrk import aim_xrk

CLIP = Path(r"C:\Users\rodrigobrim\Videos\karting\2026-07-13\raw\video\DJI_20260713081722_0062_D.MP4")

pcm = np.load("pcm_cache.npy") if Path("pcm_cache.npy").exists() else extract_audio_pcm(CLIP)
if not Path("pcm_cache.npy").exists():
    np.save("pcm_cache.npy", pcm)
print("audio s:", len(pcm)/AUDIO_RATE)

hop = int(AUDIO_RATE / FEATURE_HZ)
win = 2048
n_frames = (len(pcm) - win) // hop + 1
window = np.hanning(win)
freqs = np.fft.rfftfreq(win, 1/AUDIO_RATE)

# compute full spectrogram stats in chunks
rms_l, cent_l, peaks = [], [], {}
BANDS = {"30-400": (30, 400), "60-800": (60, 800), "100-1500": (100, 1500), "30-150": (30, 150)}
for b in BANDS: peaks[b] = []
for start in range(0, n_frames, 4096):
    count = min(4096, n_frames - start)
    idx = np.arange(win)[None, :] + hop*(start+np.arange(count))[:, None]
    fr = pcm[idx]*window[None, :]
    rms_l.append(np.sqrt((fr**2).mean(axis=1)))
    spec = np.abs(np.fft.rfft(fr, axis=1))
    for bname, (lo, hi) in BANDS.items():
        m = (freqs >= lo) & (freqs <= hi)
        peaks[bname].append(freqs[m][np.argmax(spec[:, m], axis=1)])
rms = np.concatenate(rms_l)

# average spectrum during a known driving segment (t=595..605s)
i0, i1 = int(595*FEATURE_HZ), int(605*FEATURE_HZ)
idx = np.arange(win)[None, :] + hop*np.arange(i0, i1)[:, None]
seg_spec = np.abs(np.fft.rfft(pcm[idx]*window[None, :], axis=1)).mean(axis=0)
top = np.argsort(seg_spec)[-12:][::-1]
print("dominant freqs while driving (t~600s):", sorted(freqs[top].round(1)))

# telemetry: concatenate all four sessions is wrong (gaps unknown); use 0081 (16 laps)
log = aim_xrk(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29\KGV 101 _Race_a_0081.xrk")
df = unified_frame(log)
t_end = float(df["t_s"].iloc[-1])
grid = np.arange(0, t_end, 1/FEATURE_HZ)
speed = _zscore(np.interp(grid, df["t_s"], df["speed_ms"]))

def try_feature(name, feat):
    res = find_offset(speed, _zscore(feat), lag_window_s=(-2429, t_end),
                      audio_time_offset_s=win/(2*AUDIO_RATE))
    print(f"{name:22s} lag={res.lag_s:9.1f}s conf={res.confidence:.3f}")
    return res

try_feature("rms only", rms)
for bname in BANDS:
    try_feature(f"peak {bname}", np.concatenate(peaks[bname]))
for bname in ["30-400", "60-800"]:
    try_feature(f"rms + peak {bname}", _zscore(rms) + _zscore(np.concatenate(peaks[bname])))
