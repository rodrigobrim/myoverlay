from libxrk import aim_xrk

xrk = aim_xrk(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29\KGV 101 _Race_a_0081.xrk")
xrz = aim_xrk(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29\KGV 101 _Race_a_0081.xrz")
for tag, log in (("xrk", xrk), ("xrz", xrz)):
    tcs = [t.column("timecodes").to_numpy() for t in log.channels.values() if len(t)]
    lo = min(tc[0] for tc in tcs)
    hi = max(tc[-1] for tc in tcs)
    meta = log.metadata
    print(f"{tag}: span {lo/1000:.2f}..{hi/1000:.2f}s laps={len(log.laps)} "
          f"date={meta.get('Log Date')} {meta.get('Log Time')}")
neg = any((t.column("timecodes").to_numpy() < 0).any() for t in xrk.channels.values() if len(t))
print("negative/pre-trigger timecodes in xrk:", neg)
