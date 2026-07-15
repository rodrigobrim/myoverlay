"""Render short test slices of a day's clip with the current overlay code.

Cheap verification of overlay changes without a full re-render: each slice
takes ~30 s of compute instead of ~20 min.

Usage (from the repo root):
    uv run python tools/proof_slices.py 2026-07-13 0 315
    uv run python tools/proof_slices.py 2026-07-13 --len 20 600
renders slices (default 15 s) starting at each given video-time (seconds)
into <library>/<day>/out/tests/.
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np

from media_tools.config import load_config
from media_tools.library import Library
from media_tools.overlay import OverlayRenderer
from media_tools.render import sample_timeline
from media_tools.telemetry import load_day_frame

SLICE_S = 15.0
FPS = 30.0

args = sys.argv[1:]
if "--len" in args:
    i = args.index("--len")
    SLICE_S = float(args[i + 1])
    del args[i : i + 2]
day_arg = args[0] if args else "2026-07-13"
starts = [float(a) for a in args[1:]] or [0.0]

cfg = load_config()
lib = Library(cfg.library_root)
d = date.fromisoformat(day_arg)
manifest = lib.load_day(d)
day_dir = lib.day_dir(d)
day = load_day_frame(day_dir, manifest)
clip = manifest.videos[0]
video = day_dir / clip.file
off = (clip.sync.video_start_utc - day.start_utc).total_seconds()
out_dir = day_dir / "out" / "tests"
out_dir.mkdir(parents=True, exist_ok=True)

observed = float(np.nanmax(day.df["speed_ms"].to_numpy(dtype=float))) * 3.6
max_speed = max(60.0, np.ceil(observed * 1.02 / 10) * 10)
channels = set()
if "speed_ms" in day.df.columns:
    channels.add("speed")
if "g_lat" in day.df.columns or "g_lon" in day.df.columns:
    channels.add("g")
if "steering_deg" in day.df.columns:
    channels.add("steering")

for start in starts:
    tl = sample_timeline(day.df, day.laps, off, start, SLICE_S, FPS, cfg.render.min_lap_s)
    renderer = OverlayRenderer(
        1920, 1080, track_frac=tl.track_frac, font_path=cfg.render.font_path,
        max_speed_kmh=max_speed, channels=channels,
    )
    out = out_dir / f"test_{int(start):04d}s.mp4"
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.3f}", "-t", str(SLICE_S),
            "-i", str(video),
            "-f", "rawvideo", "-pixel_format", "rgba",
            "-video_size", "1920x1080", "-framerate", str(FPS), "-i", "pipe:0",
            "-filter_complex", "[0:v][1:v]overlay=eof_action=repeat[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "22", "-b:v", "0",
            "-c:a", "copy", str(out),
        ],
        stdin=subprocess.PIPE,
    )
    for values in tl.frames:
        proc.stdin.write(renderer.render_frame(values).tobytes())
    proc.stdin.close()
    proc.wait(timeout=600)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed for start={start}")
    print("made", out)
