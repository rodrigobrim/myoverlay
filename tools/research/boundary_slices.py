"""Two 15 s test slices: video start (no data) and the coverage boundary."""
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from media_tools.config import load_config
from media_tools.library import Library
from media_tools.overlay import OverlayRenderer
from media_tools.render import sample_timeline
from media_tools.telemetry import load_day_frame

SCRATCH = Path(tempfile.gettempdir()) / "myoverlay-research"
SCRATCH.mkdir(parents=True, exist_ok=True)

cfg = load_config()
lib = Library(cfg.library_root)
m = lib.load_day(date(2026, 7, 13))
day_dir = lib.day_dir(date(2026, 7, 13))
day = load_day_frame(day_dir, m)
clip = m.videos[0]
video = day_dir / clip.file
off = (clip.sync.video_start_utc - day.start_utc).total_seconds()
fps = 30.0  # test slices don't need 60

for name, start in (("startA", 0.0), ("boundaryB", 315.0)):
    tl = sample_timeline(day.df, day.laps, off, start, 15.0, fps, cfg.render.min_lap_s)
    r = OverlayRenderer(1920, 1080, track_frac=tl.track_frac, font_path=cfg.render.font_path)
    frames_dir = SCRATCH / f"frames_{name}"
    frames_dir.mkdir(exist_ok=True)
    for i, v in enumerate(tl.frames):
        r.render_frame(v).save(frames_dir / f"{i:06d}.png", compress_level=1)
    out = SCRATCH / f"slice_{name}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.3f}", "-t", "15",
            "-i", str(video),
            "-framerate", str(fps),
            "-i", str(frames_dir / "%06d.png"),
            "-filter_complex", "[0:v][1:v]overlay=eof_action=repeat[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "22", "-b:v", "0",
            "-c:a", "copy", str(out),
        ],
        check=True,
        capture_output=True,
    )
    print("made", out)

# verification stills: A@8s; B at 5.5s (t=320.5, pre-data) and 8.5s (t=323.5, post)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "8", "-i", str(SCRATCH / "slice_startA.mp4"), "-frames:v", "1", str(SCRATCH / "chk_A_t8.jpg")], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "5.5", "-i", str(SCRATCH / "slice_boundaryB.mp4"), "-frames:v", "1", str(SCRATCH / "chk_B_pre.jpg")], check=True)
subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "8.5", "-i", str(SCRATCH / "slice_boundaryB.mp4"), "-frames:v", "1", str(SCRATCH / "chk_B_post.jpg")], check=True)
print("stills done")
