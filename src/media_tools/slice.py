"""Cut time slices out of rendered overlay videos.

`mt slice 2026-07-13 12:01-14:02 [more ranges...]` writes each range to
out/slices/. Only pipeline render outputs (out/*.mp4, overlay applied) may be
sliced — raw footage is never exported this way. Slices are not registered in
the manifest, so they are never auto-published.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .encoding import encoder_args
from .tools import ffmpeg_exe

_RANGE_RE = re.compile(r"^(.+?)\s*-\s*(.+)$")


def parse_timestamp(text: str) -> float:
    """'14:02' -> 842.0; '1:02:03.5' -> 3723.5; '721.5' -> 721.5 (seconds)."""
    text = text.strip()
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    if len(parts) > 3 or any(p == "" for p in parts):
        raise ValueError(f"invalid time {text!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_range(text: str) -> tuple[float, float]:
    """'12:01-14:02' -> (721.0, 842.0)."""
    m = _RANGE_RE.match(text)
    if not m:
        raise ValueError(f"invalid range {text!r} (expected START-END)")
    start, end = parse_timestamp(m.group(1)), parse_timestamp(m.group(2))
    if end <= start:
        raise ValueError(f"range {text!r}: end must be after start")
    return start, end


def _stamp(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def slice_video(
    source: Path,
    start_s: float,
    end_s: float,
    dest_dir: Path,
    codec: str = "libx264",
    crf: int = 20,
    preset: str = "medium",
    copy: bool = False,
) -> Path:
    """Cut [start_s, end_s) of source into dest_dir.

    Default re-encodes for frame-exact cuts (fast with nvenc); copy=True is
    instant but snaps to keyframes (can be several seconds off).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}_{_stamp(start_s)}-{_stamp(end_s)}{source.suffix}"

    cmd = [ffmpeg_exe(), "-y", "-v", "error", "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}", "-i", str(source)]
    if copy:
        cmd += ["-c", "copy"]
    else:
        cmd += [*encoder_args(codec, crf, preset), "-c:a", "copy"]
    cmd.append(str(dest))

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg slice failed: {proc.stderr[:500]}")
    return dest


def resolve_render_source(manifest, day_dir: Path, clip_hint: str | None) -> Path:
    """Pick which rendered output to slice; only out/ renders are eligible."""
    renders = [r for r in manifest.renders if r.file.startswith("out/")]
    # Slices registered in the manifest (via --publish) are outputs, not
    # sources: prefer full renders whenever any exist.
    non_slices = [r for r in renders if getattr(r, "kind", None) != "slice"]
    if non_slices:
        renders = non_slices
    if clip_hint:
        renders = [
            r
            for r in renders
            if clip_hint.lower() in r.file.lower()
            or any(clip_hint.lower() in s.lower() for s in r.source_videos)
        ]
    if not renders:
        raise ValueError(
            "no rendered overlay output matches; render first (mt render), "
            "raw clips cannot be sliced"
        )
    if len(renders) > 1:
        names = ", ".join(r.file for r in renders)
        raise ValueError(f"ambiguous - pick one with --clip: {names}")
    path = day_dir / renders[0].file
    if not path.is_file():
        raise ValueError(f"rendered file missing: {path}")
    return path
