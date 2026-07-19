from libxrk import aim_xrk

PATH = r"C:\Users\rodrigobrim\Videos\karting\2026-07-16\raw\telemetry\kgv e2_Race_a_0096.xrk"
log = aim_xrk(PATH)

ch = log.channels
print("channels type:", type(ch))
names = list(ch.keys()) if hasattr(ch, "keys") else list(ch)
print(f"n channels: {len(names)}")
for nm in names:
    print("  ", repr(nm))

# inspect one channel object
print("\n=== single-channel table (native timecodes) ===")
one = log.select_channels([names[0]]).get_channels_as_table().to_pandas()
print("cols:", list(one.columns), "rows:", len(one), "dtypes:", dict(one.dtypes))
print(one.head(3).to_string())

# do different channels have different native rates / lengths?
print("\n=== per-channel row counts (native) ===")
for nm in names:
    try:
        tb = log.select_channels([nm]).get_channels_as_table().to_pandas()
        print(f"  {nm:32s} rows={len(tb):7d} t0={tb['timecodes'].iloc[0]} t1={tb['timecodes'].iloc[-1]}")
    except Exception as e:
        print(f"  {nm:32s} ERR {e}")
