"""Export the full .xrk telemetry to a new file with the start/finish line
moved to the `start-finish-coordinate` flag value (the real S/F).

Keeps ALL data from the .xrk: every one of the 42 channels at its native
sample rate/timecodes, plus the full logger metadata. The lap table is
RE-DERIVED at the real S/F (-23.60492, -46.83631) instead of the deliberately
early beacon pin, so every lap boundary shifts ~+7.25 s. The original early
laps are also preserved in metadata for reference. Output is a single
self-contained Parquet; the original .xrk is never touched.
"""
import json
import os
import pathlib
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
from libxrk import aim_xrk

from media_tools.relap import crossings_by_coordinate, laps_from_crossings

PATH = r"C:\Users\rodrigobrim\Videos\karting\2026-07-16\raw\telemetry\kgv e2_Race_a_0096.xrk"
OUT = r"C:\Users\rodrigobrim\Videos\karting\2026-07-16\raw\telemetry\kgv e2_Race_a_0096.sf-relapped.parquet"
SF_LAT, SF_LON = -23.60492, -46.83631          # start-finish-coordinate (default) - real S/F
EARLY_LAT, EARLY_LON = -23.6042405, -46.8368762  # deliberately-early beacon pin (old trip point)
RADIUS_M = 12.0

log = aim_xrk(PATH)
names = list(log.channels.keys())

# --- pull every channel at its native timecodes (nothing resampled/dropped) ---
long_ch, long_t, long_v, ch_meta = [], [], [], []
gps = {}
for nm in names:
    tb = log.select_channels([nm]).get_channels_as_table().to_pandas()
    t = tb["timecodes"].to_numpy(dtype=np.int64)
    v = tb[nm].to_numpy()
    native_dtype = str(v.dtype)
    v = v.astype(np.float64)                    # int32 & float32 are lossless in float64
    long_ch.append(np.full(len(t), nm, dtype=object))
    long_t.append(t)
    long_v.append(v)
    units = getattr(log.channels.get(nm), "units", None) or getattr(log.channels.get(nm), "unit", None)
    ch_meta.append({"name": nm, "native_dtype": native_dtype, "units": units, "n": int(len(t))})
    if nm in ("GPS Latitude", "GPS Longitude"):
        gps[nm] = v

chan = np.concatenate(long_ch)
tcol = np.concatenate(long_t)
vcol = np.concatenate(long_v)
t_end = int(tcol.max())

# --- re-derive each lap boundary at the real S/F, purely by MAP POSITION ---
# (media_tools.relap: crossing = GPS closest approach; grid launch excluded by
# track distance travelled, not by any clock. Same code the tests pin.)
gps_t = log.select_channels(["GPS Latitude"]).get_channels_as_table().to_pandas()["timecodes"].to_numpy(np.int64)
cross = [int(c) for c in crossings_by_coordinate(
    gps_t, gps["GPS Latitude"], gps["GPS Longitude"], SF_LAT, SF_LON, RADIUS_M)]
relapped = [
    {"num": lp["num"], "start_time": int(lp["start_time"]), "end_time": int(lp["end_time"])}
    for lp in laps_from_crossings(cross, 0, t_end)
]
orig_laps = [dict(lp) for lp in log.laps.to_pylist()]

# quantify the early offset vs the original beacon crossings
orig_cross = sorted({lp["end_time"] for lp in orig_laps} & {lp["start_time"] for lp in orig_laps})
offsets = [min(cross, key=lambda c: abs(c - oc)) - oc for oc in orig_cross]
mean_offset = int(np.mean(offsets))

# --- write single self-contained Parquet ---
table = pa.table({
    "channel": pa.array(chan.tolist(), type=pa.string()),
    "timecode_ms": pa.array(tcol, type=pa.int64()),
    "value": pa.array(vcol, type=pa.float64()),
})
meta = {
    b"format": b"media-tools xrk full-export v1 (long-format, native rates)",
    b"source_xrk": PATH.encode(),
    b"start_finish_coordinate": json.dumps({"lat": SF_LAT, "lon": SF_LON, "note": "real S/F (flag default); laps re-derived here"}).encode(),
    b"early_pin": json.dumps({"lat": EARLY_LAT, "lon": EARLY_LON, "note": "deliberately-early beacon pin that generated the original laps"}).encode(),
    b"early_offset_ms": str(mean_offset).encode(),
    b"xrk_metadata": json.dumps(log.metadata, default=str).encode(),
    b"channels": json.dumps(ch_meta).encode(),
    b"laps": json.dumps(relapped).encode(),
    b"laps_original_early": json.dumps(orig_laps).encode(),
}
table = table.replace_schema_metadata(meta)
pq.write_table(table, OUT, compression="zstd")

# --- verify readback ---
back = pq.read_table(OUT)
bm = back.schema.metadata
print(f"wrote {OUT}")
print(f"rows={back.num_rows:,}  channels={len(ch_meta)}  laps(relapped)={len(relapped)}  laps(original)={len(orig_laps)}")
print(f"file size = {os.path.getsize(OUT):,} bytes  (source .xrk = {os.path.getsize(PATH):,})")
print(f"S/F moved to real line: every boundary +{mean_offset} ms vs early pin")
print(f"channels preserved: {sorted(set(back.column('channel').to_pylist())) == sorted(names)}  ({len(names)} channels)")
print(f"embedded metadata keys: {[k.decode() for k in bm]}")
print(f"relapped laps[:3]: {json.loads(bm[b'laps'])[:3]}")
print(f"total native samples across all channels: {sum(c['n'] for c in ch_meta):,}")
