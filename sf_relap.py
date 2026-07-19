"""Validate S/F re-lapping against the real race telemetry (manual/real-data
counterpart of tests/test_relap.py). Reads only; writes nothing."""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
from libxrk import aim_xrk

from media_tools.relap import crossings_by_coordinate

PATH = r"C:\Users\rodrigobrim\Videos\karting\2026-07-16\raw\telemetry\kgv e2_Race_a_0096.xrk"
SF_LAT, SF_LON = -23.60492, -46.83631   # start-finish-coordinate (default) - real S/F

log = aim_xrk(PATH)
laps = log.laps.to_pylist()
orig_cross = sorted({lp["end_time"] for lp in laps} & {lp["start_time"] for lp in laps})

tbl = log.select_channels(["GPS Latitude", "GPS Longitude"]).get_channels_as_table().to_pandas()
gt = tbl["timecodes"].to_numpy(dtype=float)
glat = tbl[[c for c in tbl.columns if "Lat" in c][0]].to_numpy(dtype=float)
glon = tbl[[c for c in tbl.columns if "Lon" in c][0]].to_numpy(dtype=float)

cross = crossings_by_coordinate(gt, glat, glon, SF_LAT, SF_LON)

print(f"original beacon crossings = {len(orig_cross)}")
print(f"re-derived (real S/F)     = {len(cross)}\n")
print(f"{'lap':>3} {'orig(s)':>9} {'real S/F(s)':>11} {'shift(ms)':>9}")
for idx, oc in enumerate(orig_cross):
    rc = min(cross, key=lambda c: abs(c - oc))
    print(f"{idx+1:>3} {oc/1000:>9.2f} {rc/1000:>11.2f} {rc-oc:>9.0f}")
diffs = [min(cross, key=lambda c: abs(c - oc)) - oc for oc in orig_cross]
print(f"\nmean shift = {np.mean(diffs):.0f} ms across {len(orig_cross)} laps")
