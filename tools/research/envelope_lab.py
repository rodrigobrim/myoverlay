"""Validate envelope-stage sync: whole-day speed trace (gaps = 0) vs audio RMS."""
import numpy as np
from pathlib import Path
from zoneinfo import ZoneInfo
from media_tools.sync import AUDIO_RATE, FEATURE_HZ, _frame_features, _highpass, _zscore
from media_tools.ingest.mychron import _parse_log_datetime
from media_tools.telemetry import unified_frame
from libxrk import aim_xrk

TZ = ZoneInfo("America/Sao_Paulo")
pcm = np.load("pcm_cache.npy")

# day speed trace on a 10 Hz grid, 0 in the gaps
logs = []
for p in sorted(Path(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29").glob("*.xrk")):
    log = aim_xrk(str(p))
    start = _parse_log_datetime(log.metadata, TZ)
    logs.append((start, unified_frame(log)))
day_start = logs[0][0]
t_end = max((s - day_start).total_seconds() + df["t_s"].iloc[-1] for s, df in logs)
grid = np.arange(0, t_end, 1 / FEATURE_HZ)
speed = np.zeros_like(grid)
for s, df in logs:
    base = (s - day_start).total_seconds()
    mask = (grid >= base + df["t_s"].iloc[0]) & (grid <= base + df["t_s"].iloc[-1])
    speed[mask] = np.interp(grid[mask], base + df["t_s"], df["speed_ms"])

def smooth(sig, seconds):
    w = max(3, int(seconds * FEATURE_HZ) | 1)
    k = np.ones(w) / w
    return np.convolve(np.pad(sig, w // 2, mode="reflect"), k, mode="valid")

hop = int(AUDIO_RATE / FEATURE_HZ)
rms, _ = _frame_features(pcm, AUDIO_RATE, hop, 2048)
a = _highpass(smooth(_zscore(rms), 10.0), FEATURE_HZ, 120.0)
e = _highpass(smooth(_zscore(speed), 10.0), FEATURE_HZ, 120.0)

num = np.correlate(e, a, "full")
sx = np.correlate(e * e, np.ones(len(a)), "full")
sy = np.correlate(np.ones(len(e)), a * a, "full")
sc = num / np.sqrt(np.maximum(sx * sy, 1e-12))
lags = np.arange(-(len(a) - 1), len(e)) / FEATURE_HZ - 2048 / (2 * AUDIO_RATE)
overlap = np.correlate(np.ones(len(e)), np.ones(len(a)), "full")
valid = overlap >= 300 * FEATURE_HZ  # >= 5 min overlap
sc = np.where(valid, sc, -np.inf)
best = np.argsort(sc)[-5:][::-1]
print("top envelope lags:")
for i in best:
    print(f"  lag={lags[i]:8.1f}s  score={sc[i]:.3f}")
