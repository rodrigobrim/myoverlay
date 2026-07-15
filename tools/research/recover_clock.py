"""Derive the MyChron clock error by audio-syncing the Jul 13 clip against
each downloaded session, with no assumption about the offset."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from media_tools.sync import extract_audio_pcm, audio_feature, rpm_feature, find_offset, AUDIO_RATE
from media_tools.telemetry import unified_frame
from media_tools.ingest.mychron import _parse_log_datetime
from libxrk import aim_xrk

CLIP = Path(r"C:\Users\rodrigobrim\Videos\karting\2026-07-13\raw\video\DJI_20260713081722_0062_D.MP4")
SESSIONS = sorted(Path(r"C:\AIM_SPORT\RaceStudio3\user\data\2047-10-29").glob("*.xrk"))
TZ = ZoneInfo("America/Sao_Paulo")

video_start_utc = datetime(2026, 7, 13, 8, 17, 22, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
print(f"camera says clip starts {video_start_utc.isoformat()}")

print("extracting clip audio (12.4 GB source)...")
pcm = extract_audio_pcm(CLIP)
clip_s = len(pcm) / AUDIO_RATE
print(f"audio: {clip_s:.0f} s")
af = audio_feature(pcm)

for path in SESSIONS:
    log = aim_xrk(str(path))
    device_start = _parse_log_datetime(log.metadata, TZ)
    df = unified_frame(log)
    dur = float(df["t_s"].iloc[-1])
    rf = rpm_feature(df)
    res = find_offset(rf, af, lag_window_s=(-clip_s, dur), audio_time_offset_s=2048/(2*AUDIO_RATE))
    session_actual_start = video_start_utc - timedelta(seconds=res.lag_s)
    offset = session_actual_start - device_start
    print(f"{path.name}: lag={res.lag_s:9.1f}s conf={res.confidence:.2f} "
          f"device_start={device_start.isoformat()} -> actual~{session_actual_start.isoformat()} "
          f"offset={offset}")
