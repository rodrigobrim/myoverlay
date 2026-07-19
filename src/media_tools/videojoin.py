"""Join camera-split recording segments into one clip per session.

GoPro and DJI roll a single long recording into multiple files (~4 GB FAT32
segments). Each segment's filename carries its own start time, and the next
segment begins ~1-2 s after the previous one ended. This module detects such
runs - consecutive clips that are time-contiguous and share identical
codec/resolution/fps - and losslessly concatenates each run into one file with
the ffmpeg concat demuxer (`-c copy`: no re-encode, seconds instead of a full
transcode).

Distinct sessions on the same day stay separate: a large time gap between
files ends a run. Originals are never deleted - the joined clip records the
segments it was built from, the pipeline treats it as a single clip, and the
segments are marked consumed so a re-ingest does not resurrect them.
"""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from pathlib import Path

from .library import ConsumedSegment, DayManifest, VideoClip

# Two consecutive segments belong to one recording when the second starts
# within this many seconds of the first ending. Real split rollovers are ~1-2s;
# distinct track sessions are minutes/hours apart, so a few seconds is safe.
DEFAULT_GAP_TOLERANCE_S = 8.0


def _probe_video_params(path: Path) -> tuple[str, str, str, str] | None:
    """(codec, width, height, r_frame_rate) of the first video stream, or None.

    Two segments can be stream-copied into one file only when these match, so
    this is the guard against a corrupt/lossy join.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                "-of", "default=nw=1", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    d: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v.strip()
    keys = ("codec_name", "width", "height", "r_frame_rate")
    if not all(k in d for k in keys):
        return None
    return (d["codec_name"], d["width"], d["height"], d["r_frame_rate"])


def _joined_name(first_name: str, last_name: str) -> str:
    """Name for the joined file, preserving the first segment's timestamp so the
    rest of the pipeline reads its capture time as usual, e.g.
    DJI_20260716221408_0065_D.MP4 + ..._0066_... -> DJI_20260716221408_0065-0066_D.MP4.
    """
    mf = re.match(r"(DJI_\d{14})_(\d+)_D\.(\w+)$", first_name, re.IGNORECASE)
    ml = re.search(r"_(\d+)_D\.\w+$", last_name, re.IGNORECASE)
    if mf and ml:
        return f"{mf.group(1)}_{mf.group(2)}-{ml.group(1)}_D.{mf.group(3).upper()}"
    return f"{Path(first_name).stem}_joined{Path(first_name).suffix or '.MP4'}"


def detect_groups(
    clips: list[VideoClip], gap_tolerance_s: float = DEFAULT_GAP_TOLERANCE_S
) -> list[list[VideoClip]]:
    """Runs of >=2 time-contiguous segments, ordered by capture start."""
    usable = [c for c in clips if not c.segments and c.duration_s is not None]
    usable.sort(key=lambda c: c.start_utc_estimate)

    groups: list[list[VideoClip]] = []
    cur: list[VideoClip] = []
    for c in usable:
        if not cur:
            cur = [c]
            continue
        prev = cur[-1]
        prev_end = prev.start_utc_estimate + timedelta(seconds=prev.duration_s or 0.0)
        gap = (c.start_utc_estimate - prev_end).total_seconds()
        # Small negative gaps happen when a duration reads a hair long.
        if -2.0 <= gap <= gap_tolerance_s:
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    if cur:
        groups.append(cur)
    return [g for g in groups if len(g) >= 2]


def _join_group(manifest: DayManifest, day_dir: Path, group: list[VideoClip]) -> str:
    """Losslessly concat one group; rewrite the manifest to a single clip."""
    params = [_probe_video_params(day_dir / c.file) for c in group]
    if any(p is None for p in params):
        return f"! could not probe {', '.join(c.source_name for c in group)}; skipped"
    if len(set(params)) != 1:
        return (
            f"! {', '.join(c.source_name for c in group)} differ in codec/res/fps "
            f"({params}); lossless join not possible - skipped"
        )

    first, last = group[0], group[-1]
    joined_name = _joined_name(first.source_name, last.source_name)
    dest = day_dir / "raw" / "video" / joined_name

    # concat demuxer list file: absolute posix paths, single-quote-escaped.
    work = day_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    listfile = work / f"concat_{Path(joined_name).stem}.txt"
    lines = []
    for c in group:
        p = (day_dir / c.file).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{p}'")
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile), "-c", "copy", "-movflags", "+faststart",
                str(dest),
            ],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()[-3:]
        return f"! ffmpeg concat failed for {joined_name}: {' / '.join(tail)}"
    except OSError as exc:
        return f"! ffmpeg not runnable: {exc}"

    joined = VideoClip(
        file=f"raw/video/{joined_name}",
        source_name=joined_name,
        size_bytes=dest.stat().st_size,
        duration_s=sum((c.duration_s or 0.0) for c in group),
        start_utc_estimate=first.start_utc_estimate,
        # The joined video starts on the same frame as segment 1, so a sync
        # pinned on that segment stays valid. Race-end must rescan the whole
        # joined length, so drop it.
        sync=first.sync,
        session_id=first.session_id,
        race_end=None,
        segments=[c.file for c in group],
    )

    group_files = {c.file for c in group}
    manifest.videos = [v for v in manifest.videos if v.file not in group_files]
    manifest.videos.append(joined)
    manifest.videos.sort(key=lambda v: v.start_utc_estimate)
    for c in group:
        manifest.consumed_segments.append(
            ConsumedSegment(source_name=c.source_name, size_bytes=c.size_bytes)
        )
    gb = joined.size_bytes / 1e9
    return (
        f"joined [{', '.join(c.source_name for c in group)}] -> {joined_name} "
        f"({joined.duration_s:.0f}s, {gb:.2f} GB); originals kept on disk"
    )


def join_day(
    manifest: DayManifest,
    day_dir: Path,
    only_substrings: list[str] | None = None,
    gap_tolerance_s: float = DEFAULT_GAP_TOLERANCE_S,
    dry_run: bool = False,
) -> list[str]:
    """Join split segments for one day.

    only_substrings: when given, the clips whose source_name contains any of
    these are treated as ONE explicit group (the user asserting they are one
    session), bypassing auto-detection. Otherwise groups are auto-detected.
    """
    if only_substrings:
        sel = [
            v
            for v in manifest.videos
            if not v.segments
            and any(s in v.source_name for s in only_substrings)
        ]
        sel.sort(key=lambda c: c.start_utc_estimate)
        if len(sel) < 2:
            return [f"need >=2 matching clips to join (matched {len(sel)})"]
        groups = [sel]
    else:
        groups = detect_groups(manifest.videos, gap_tolerance_s)

    if not groups:
        return ["no split segments detected"]

    lines: list[str] = []
    for g in groups:
        if dry_run:
            total = sum(c.duration_s or 0.0 for c in g)
            lines.append(
                f"would join [{', '.join(c.source_name for c in g)}] -> "
                f"{_joined_name(g[0].source_name, g[-1].source_name)} ({total:.0f}s total)"
            )
        else:
            lines.append(_join_group(manifest, day_dir, g))
    return lines
