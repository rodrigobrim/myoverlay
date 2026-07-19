"""Race-end auto-trim (scan-video-for-race-end).

In plain words: when you forget to stop the camera after the race, the
recording keeps going while you park and walk away. This feature trims the
video to just after your last lap - so the published video ends with the
race, not with minutes of parked/pit-lane footage.

Mechanics (telemetry only, no audio):
- The last finish-line crossing is the end of the last COMPLETE lap (one the
  MyChron opened and closed with a beacon). The driver's in-lap and parking
  come after it.
- The cut is placed `RACE_END_BUFFER_S` seconds after that crossing, so it
  keeps the flag lap + a short cool-down and drops everything after.

(An earlier version listened to the engine audio for the shutdown; a busy pit
lane full of other idling karts made that unreliable, so we rely on the lap
beacons instead.)
"""

from __future__ import annotations

from pathlib import Path

from .library import RaceEnd

# Keep this many seconds after the driver's last lap ended.
RACE_END_BUFFER_S = 15.0


def detect_race_end(
    video_path: Path,
    laps: list[tuple[int, float, float]],
    video_offset_s: float,
    clip_duration_s: float,
    buffer_s: float = RACE_END_BUFFER_S,
) -> RaceEnd:
    """Cut the clip `buffer_s` seconds after the driver's last lap ended.

    The anchor is the LAST recorded lap boundary (the last finish-line
    crossing). The MyChron stops logging when the kart stops moving, so the
    last lap end is when the driving ended; everything after it is the
    in-lap tail / parking / pit-lane footage we want to drop. The final lap
    (often a slower in-lap) is KEPT - it must not be trimmed off.

    laps: day-telemetry lap tuples (lap_num, start_s, end_s), seconds since the
    day frame start. video_offset_s: day-telemetry time at which the video
    starts (t_tel = t_video + video_offset_s). video_path is unused now
    (kept for signature stability).
    """
    if not laps:
        return RaceEnd()  # no laps: nothing to anchor a cut to

    last_crossing_tel = max(e for _, _, e in laps)
    cut = (last_crossing_tel - video_offset_s) + buffer_s
    if not (0.0 < cut < clip_duration_s):
        return RaceEnd()  # last lap outside this clip / would not shorten it
    return RaceEnd(cut_at_s=cut)
