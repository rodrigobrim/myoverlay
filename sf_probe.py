import numpy as np
from libxrk import aim_xrk

PATH = r"C:\Users\rodrigobrim\Videos\karting\2026-07-16\raw\telemetry\kgv e2_Race_a_0096.xrk"
log = aim_xrk(PATH)

print("=== log public attrs ===")
print([a for a in dir(log) if not a.startswith("_")])

print("\n=== metadata ===")
try:
    m = log.metadata
    print(dict(m) if m else m)
except Exception as e:
    print("meta err:", e)

# --- laps ---
laps = log.laps.to_pylist()
print(f"\n=== {len(laps)} laps ===")
for lp in laps:
    print(lp)

# --- GPS channels ---
names = list(log.channels.keys()) if hasattr(log.channels, "keys") else list(log.channels)
gps_names = [c for c in names if "GPS" in c and ("Lat" in c or "Lon" in c)]
print("\n=== gps channels ===", gps_names)

sel = log.select_channels(gps_names)
tbl = sel.get_channels_as_table().to_pandas()
print("cols:", list(tbl.columns), "rows:", len(tbl))
t = tbl["timecodes"].to_numpy(dtype=float)  # ms
latcol = [c for c in tbl.columns if "Lat" in c][0]
loncol = [c for c in tbl.columns if "Lon" in c][0]
lat = tbl[latcol].to_numpy(dtype=float)
lon = tbl[loncol].to_numpy(dtype=float)
print("sample raw lat/lon:", lat[len(lat)//2], lon[len(lon)//2])
print("lat range:", np.nanmin(lat), np.nanmax(lat), "| lon range:", np.nanmin(lon), np.nanmax(lon))

# --- S/F crossings = lap ends that another lap starts at (real beacon crossings) ---
starts = [lp["start_time"] for lp in laps]
crossings = sorted({lp["end_time"] for lp in laps} & set(starts))  # end==next start
print(f"\n=== {len(crossings)} S/F beacon crossings (interpolated GPS) ===")
cs_lat, cs_lon = [], []
for cm in crossings:
    la = float(np.interp(cm, t, lat)); lo = float(np.interp(cm, t, lon))
    cs_lat.append(la); cs_lon.append(lo)
    print(f"  t={cm/1000:8.2f}s   lat={la:.7f}   lon={lo:.7f}")

if cs_lat:
    mlat, mlon = np.mean(cs_lat), np.mean(cs_lon)
    # spread in metres (approx): 1 deg lat ~111.32 km, 1 deg lon ~111.32*cos(lat) km
    import math
    sd_lat_m = np.std(cs_lat) * 111320
    sd_lon_m = np.std(cs_lon) * 111320 * math.cos(math.radians(mlat))
    print(f"\n>>> START/FINISH LINE (mean of crossings): lat={mlat:.7f}, lon={mlon:.7f}")
    print(f"    spread: {sd_lat_m:.1f} m (N/S), {sd_lon_m:.1f} m (E/W)")
    print(f"    google maps: https://maps.google.com/?q={mlat:.7f},{mlon:.7f}")
