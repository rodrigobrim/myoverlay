"""Camera ingestion: copy new clips from camera volumes/devices into the library.

The DJI Osmo Action 5 Pro exposes no API; when plugged in (or its SD card is
inserted) it appears as a removable volume with a DCIM directory. GoPros
have no mass-storage mode at all: over USB they show up as an MTP portable
device without a drive letter, reached through the Windows Shell namespace
(see mtp.py). We detect both, plus any configured source dirs, and copy
files we have not seen before (identity = original filename + size).
Originals are never deleted from the card.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path

import psutil

from ..config import Config
from ..library import Library, VideoClip
from ..tools import ffprobe_exe
from . import mtp

# e.g. DJI_20260712143205_0012_D.MP4
DJI_NAME_RE = re.compile(r"DJI_(\d{14})_")

# DJI low-resolution proxy files that ride along with the real footage.
SKIP_SUFFIXES = {".lrf", ".thm"}


def is_junk_name(name: str) -> bool:
    """AppleDouble droppings ("._GH011045.MP4") a Mac leaves on the card."""
    return name.startswith("._")


@dataclass
class IngestReport:
    copied: list[str] = field(default_factory=list)
    skipped_known: int = 0
    sources_scanned: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Names passed via only_names that matched no file on any source.
    requested_missing: list[str] = field(default_factory=list)


@dataclass
class CameraFile:
    """One video present on a source, with its already-ingested status."""

    path: Path  # filesystem path, or a display-only pseudo path for MTP files
    name: str
    size: int
    start_utc: datetime
    ingested: bool
    # Set for files living on an MTP device: they cannot be stat'ed, probed
    # or shutil-copied; mtp.copy_mtp_file is the only way to reach the bytes.
    mtp: mtp.MtpFile | None = None


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


def capture_time_from_name(name: str, camera_tz: tzinfo) -> datetime | None:
    """UTC capture time embedded in the filename (camera local clock), if any."""
    m = DJI_NAME_RE.search(name)
    if m:
        naive = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=camera_tz).astimezone(timezone.utc)
    return None


def capture_time(path: Path, camera_tz: tzinfo) -> datetime:
    """Best-effort capture start time in UTC.

    Prefers the timestamp embedded in DJI filenames (camera local clock),
    falls back to file mtime.
    """
    return capture_time_from_name(path.name, camera_tz) or datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )


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
            if suffix in SKIP_SUFFIXES or suffix not in exts or is_junk_name(path.name):
                continue
            yield path


def enumerate_camera_videos(
    cfg: Config, extra_sources: list[Path] | None = None
) -> tuple[list[Path], list[CameraFile]]:
    """List every video on the connected camera/sources, with ingested status.

    Read-only: copies nothing and does not probe durations, so it is instant.
    Returns (sources, files).
    """
    lib = Library(cfg.library_root)
    camera_tz = cfg.camera.tzinfo()
    sources = find_dcim_sources() + list(cfg.camera.source_dirs) + list(extra_sources or [])
    known = lib.known_videos()

    files: list[CameraFile] = []
    for path in iter_source_videos(sources, cfg.camera.extensions):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(
            CameraFile(
                path=path,
                name=path.name,
                size=size,
                start_utc=capture_time(path, camera_tz),
                ingested=(path.name, size) in known,
            )
        )

    mtp_sources, mtp_files = mtp.enumerate_mtp_videos(cfg.camera.extensions)
    sources += [Path(s) for s in mtp_sources]
    for mf in mtp_files:
        if is_junk_name(mf.name):
            continue
        files.append(
            CameraFile(
                path=Path(mf.display),
                name=mf.name,
                size=mf.size,
                # GoPro names carry no timestamp; the device's recording
                # date stands in for mtime (there is none over MTP).
                start_utc=capture_time_from_name(mf.name, camera_tz) or mf.created_utc,
                ingested=(mf.name, mf.size) in known,
                mtp=mf,
            )
        )
    return sources, files


def ingest_camera(
    cfg: Config,
    extra_sources: list[Path] | None = None,
    only_names: Collection[str] | None = None,
    force: bool = False,
) -> IngestReport:
    report = IngestReport()
    lib = Library(cfg.library_root)
    camera_tz = cfg.camera.tzinfo()

    sources, files = enumerate_camera_videos(cfg, extra_sources)
    report.sources_scanned = [str(s) for s in sources]
    if not sources:
        return report

    wanted = {n.casefold(): n for n in only_names} if only_names is not None else None
    matched: set[str] = set()
    manifests: dict[str, tuple] = {}  # date iso -> (manifest, day_dir)

    for cf in files:
        try:
            if wanted is not None:
                key = cf.name.casefold()
                if key not in wanted:
                    continue
                matched.add(key)

            if cf.ingested and not force:
                report.skipped_known += 1
                continue

            path, size, start_utc = cf.path, cf.size, cf.start_utc
            # Day folder keyed by *camera-local* capture date: a late session
            # should land with its track day, not the next UTC day.
            local_day = start_utc.astimezone(camera_tz).date()

            day_key = local_day.isoformat()
            if day_key not in manifests:
                manifests[day_key] = (lib.load_day(local_day), lib.ensure_day(local_day))
            manifest, day_dir = manifests[day_key]

            dest = day_dir / "raw" / "video" / path.name
            if force or not (dest.is_file() and dest.stat().st_size == size):
                if cf.mtp is not None:
                    mtp.copy_mtp_file(cf.mtp, dest.parent)
                else:
                    shutil.copy2(path, dest)
            if dest.stat().st_size != size:
                report.errors.append(f"size mismatch after copy: {path} -> {dest}")
                dest.unlink(missing_ok=True)
                continue

            clip = VideoClip(
                file=str(dest.relative_to(day_dir)).replace("\\", "/"),
                source_name=path.name,
                size_bytes=size,
                duration_s=probe_duration_s(dest),
                start_utc_estimate=start_utc,
            )
            # On --force, replace the existing entry in place rather than
            # appending a duplicate for the same (source_name, size).
            existing = next(
                (
                    i
                    for i, v in enumerate(manifest.videos)
                    if v.source_name == path.name and v.size_bytes == size
                ),
                None,
            )
            if existing is not None:
                manifest.videos[existing] = clip
            else:
                manifest.videos.append(clip)
            report.copied.append(f"{path} -> {dest}")
        except OSError as exc:
            report.errors.append(f"{cf.path}: {exc}")

    if wanted is not None:
        report.requested_missing = [orig for key, orig in wanted.items() if key not in matched]

    for manifest, _ in manifests.values():
        manifest.videos.sort(key=lambda v: v.start_utc_estimate)
        lib.save_day(manifest)
    return report
