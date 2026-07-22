"""Camera ingestion: copy new clips from DJI SD/USB volumes into the library.

The DJI Osmo Action 5 Pro exposes no API; when plugged in (or its SD card is
inserted) it appears as a removable volume with a DCIM directory. We detect
those volumes, plus any configured source dirs, and copy files we have not
seen before (identity = original filename + size). Originals are never
deleted from the card.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path

import psutil

from ..config import Config
from ..library import Library, VideoClip
from ..tools import ffprobe_exe

# e.g. DJI_20260712143205_0012_D.MP4
DJI_NAME_RE = re.compile(r"DJI_(\d{14})_")

# DJI low-resolution proxy files that ride along with the real footage.
SKIP_SUFFIXES = {".lrf", ".thm"}


@dataclass
class IngestReport:
    copied: list[str] = field(default_factory=list)
    skipped_known: int = 0
    sources_scanned: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_dcim_sources() -> list[Path]:
    """Removable/mounted volumes that contain a DCIM directory."""
    sources = []
    for part in psutil.disk_partitions(all=False):
        try:
            dcim = Path(part.mountpoint) / "DCIM"
            if dcim.is_dir():
                sources.append(dcim)
        except OSError:
            continue
    return sources


def capture_time(path: Path, camera_tz: tzinfo) -> datetime:
    """Best-effort capture start time in UTC.

    Prefers the timestamp embedded in DJI filenames (camera local clock),
    falls back to file mtime.
    """
    m = DJI_NAME_RE.search(path.name)
    if m:
        naive = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=camera_tz).astimezone(timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def probe_duration_s(path: Path) -> float | None:
    try:
        out = subprocess.run(
            [
                ffprobe_exe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def iter_source_videos(sources: list[Path], extensions: list[str]):
    exts = {e.lower() for e in extensions}
    for src in sources:
        if not src.is_dir():
            continue
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in SKIP_SUFFIXES or suffix not in exts:
                continue
            yield path


def ingest_camera(cfg: Config, extra_sources: list[Path] | None = None) -> IngestReport:
    report = IngestReport()
    lib = Library(cfg.library_root)
    camera_tz = cfg.camera.tzinfo()

    sources = find_dcim_sources() + list(cfg.camera.source_dirs) + list(extra_sources or [])
    report.sources_scanned = [str(s) for s in sources]
    if not sources:
        return report

    seen = lib.known_videos()
    manifests: dict[str, tuple] = {}  # date iso -> (manifest, day_dir)

    for path in iter_source_videos(sources, cfg.camera.extensions):
        try:
            size = path.stat().st_size
            if (path.name, size) in seen:
                report.skipped_known += 1
                continue

            start_utc = capture_time(path, camera_tz)
            # Day folder keyed by *camera-local* capture date: a late session
            # should land with its track day, not the next UTC day.
            local_day = start_utc.astimezone(camera_tz).date()

            key = local_day.isoformat()
            if key not in manifests:
                manifests[key] = (lib.load_day(local_day), lib.ensure_day(local_day))
            manifest, day_dir = manifests[key]

            dest = day_dir / "raw" / "video" / path.name
            if not (dest.is_file() and dest.stat().st_size == size):
                shutil.copy2(path, dest)
            if dest.stat().st_size != size:
                report.errors.append(f"size mismatch after copy: {path} -> {dest}")
                dest.unlink(missing_ok=True)
                continue

            manifest.videos.append(
                VideoClip(
                    file=str(dest.relative_to(day_dir)).replace("\\", "/"),
                    source_name=path.name,
                    size_bytes=size,
                    duration_s=probe_duration_s(dest),
                    start_utc_estimate=start_utc,
                )
            )
            seen.add((path.name, size))
            report.copied.append(f"{path} -> {dest}")
        except OSError as exc:
            report.errors.append(f"{path}: {exc}")

    for manifest, _ in manifests.values():
        manifest.videos.sort(key=lambda v: v.start_utc_estimate)
        lib.save_day(manifest)
    return report
