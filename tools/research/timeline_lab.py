"""Coarse activity timelines: video audio energy vs telemetry session layout."""
import numpy as np
from pathlib import Path
from media_tools.sync import AUDIO_RATE
from media_tools.ingest.mychron import _parse_log_datetime
from libxrk import aim_xrk
from zoneinfo import ZoneInfo

pcm = np.load("pcm_cache.npy")
BIN = 10  # seconds
n = len(pcm) // (AUDIO_RATE * BIN)
rms = np.sqrt((pcm[: n * AUDIO_RATE * BIN].reshape(n, -1) ** 2).mean(axis=1))
lo, hi = np.percentile(rms, [5, 95])
levels = " .:-=+*#%@"
print(f"video audio energy, {BIN}s bins ({n} bins, rows of 5 min):")
for row in range(0, n, 30):
    chunk = rms[row : row + 30]
    s = "".join(levels[int(np.clip((v - lo) / (hi - lo + 1e-9), 0, 0.999) * 10)] for v in chunk)
    print(f"{row*BIN//60:3d}min {s}")

TZ = ZoneInfo("America/Sao_Paulo")
print("\nsessions (device clock, local):")
starts = []
for p in sorted(Path(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29").glob("*.xrk")):
    log = aim_xrk(str(p))
    start = _parse_log_datetime(log.metadata, TZ).astimezone(TZ)
    t = log.channels["GPS Speed"]
    dur = t.column("timecodes")[-1].as_py() / 1000
    starts.append((p.name, start, dur, len(log.laps)))
    print(f"  {p.name}: {start.time()} +{dur:5.0f}s  {len(log.laps)} laps")
t0 = starts[0][1]
span = (starts[-1][1] - t0).total_seconds() + starts[-1][2]
print(f"\nsession layout over {span/60:.1f} min (10s bins):")
bins = int(span // BIN) + 1
lay = [" "] * bins
for name, st, dur, _ in starts:
    b0 = int((st - t0).total_seconds() // BIN)
    for b in range(b0, min(bins, b0 + int(dur // BIN) + 1)):
        lay[b] = "#"
for row in range(0, bins, 30):
    print(f"{row*BIN//60:3d}min {''.join(lay[row:row+30])}")
