"""MyChron ingestion: pick up new .xrk sessions from the download dir(s).

`mt telemetry get` (ingest/aim.py) downloads sessions off the MyChron into
mychron.data_dirs. We scan those directories (plus any extras), parse each
new file with libxrk, and copy it into the library day folder derived from
the session's log date. Identity = filename + size, so re-running is a no-op.
"""

from __future__ import annotations

import shutil
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from dateutil import parser as dtparser

from ..config import Config
from ..library import Lap, Library, TelemetryLog

# A data dir can hold a compressed .xrz twin alongside an .xrk session. It
# carries no extra information (the .xrk is the real telemetry libxrk reads),
# so it is never a session in its own right - always skip it, even if a config
# lists it in `extensions`. (Device downloads via ingest/aim.py are
# decompressed to .xrk on arrival, so new files never hit this.) Mirrors
# camera.SKIP_SUFFIXES for .lrf/.thm proxies.
SKIP_SUFFIXES = {".xrz"}


@dataclass
class XrkInfo:
    start_utc: datetime | None
    duration_s: float
    laps: list[Lap]
    venue: str | None
    driver: str | None
    channels: list[str]
    # Where start_utc came from: "gps" (authoritative - recovered from the
    # session's own GPS records), "logger" (the MyChron clock, which wanders),
    # or None when there was neither.
    time_source: str | None = None


@dataclass
class IngestReport:
    copied: list[str] = field(default_factory=list)
    skipped_known: int = 0
    sources_scanned: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Names passed via only_names that matched no file on any source.
    requested_missing: list[str] = field(default_factory=list)


@dataclass
class TelemetryFile:
    """One .xrk present in the download dir(s), with its already-ingested status."""

    path: Path
    name: str
    size: int
    ingested: bool


def _parse_log_datetime(metadata: dict, logger_tz: tzinfo) -> datetime | None:
    """Combine libxrk's 'Log Date' + 'Log Time' metadata into aware UTC.

    Real MyChron 6 files carry US-format dates ('10/29/2047'), so month-first
    formats are tried before the dateutil fallback.
    """
    log_date = metadata.get("Log Date")
    log_time = metadata.get("Log Time")
    if not log_date:
        return None
    text = f"{log_date} {log_time or ''}".strip()
    naive = None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            naive = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        try:
            naive = dtparser.parse(text, dayfirst=False, fuzzy=True)
        except (ValueError, OverflowError):
            return None
    if naive.tzinfo is None:
        naive = naive.replace(tzinfo=logger_tz)
    return naive.astimezone(timezone.utc)


def parse_xrk(path: Path, logger_tz: tzinfo) -> XrkInfo:
    from ..xrk_io import load_xrk

    log = load_xrk(path)

    duration_ms = 0
    for table in log.channels.values():
        col = table.column("timecodes")
        if len(col):
            duration_ms = max(duration_ms, col[-1].as_py())

    laps = [
        Lap(
            num=int(row["num"]),
            start_s=row["start_time"] / 1000.0,
            end_s=row["end_time"] / 1000.0,
        )
        for row in log.laps.to_pylist()
    ]

    meta = log.metadata or {}
    start_utc = _parse_log_datetime(meta, logger_tz)
    time_source = "logger" if start_utc is not None else None

    # The MyChron clock wanders (2047 dates; days-off dates after a reset),
    # so prefer the session's own GPS records: absolute GPS time minus the
    # first GPS sample's session-relative timecode = true logging start.
    gps_start = _gps_start_utc(path, log)
    if gps_start is not None:
        start_utc = gps_start
        time_source = "gps"

    return XrkInfo(
        start_utc=start_utc,
        duration_s=duration_ms / 1000.0,
        laps=laps,
        venue=meta.get("Venue"),
        driver=meta.get("Driver"),
        channels=sorted(log.channels.keys()),
        time_source=time_source,
    )


def _gps_start_utc(path: Path, log) -> datetime | None:
    """Logging-start UTC per the file's GPS records, or None without a fix."""
    from ..gps_time import gps_first_fix_utc

    try:
        result = gps_first_fix_utc(path.read_bytes())
    except OSError:
        return None
    if result is None:
        return None
    first_fix_utc, _aim_tc = result
    # The first GPS record sits some seconds into the log (receiver warm-up):
    # its session-relative timecode is the first entry of any GPS channel.
    offset_ms = 0
    for name in ("GPS Latitude", "GPS Longitude", "GPS Speed"):
        table = log.channels.get(name)
        if table is not None and len(table):
            offset_ms = table.column("timecodes")[0].as_py()
            break
    return first_fix_utc - timedelta(milliseconds=offset_ms)


def scan_sources(cfg: Config, extra_sources: list[Path] | None = None) -> list[Path]:
    # A session IS its .xrk; the compressed .xrz twin the device also offers
    # would duplicate every session, so it is ignored unconditionally.
    exts = {".xrk"} - SKIP_SUFFIXES
    files: list[Path] = []
    for src in list(cfg.mychron.data_dirs) + list(extra_sources or []):
        if not src.is_dir():
            continue
        files.extend(
            p
            for p in sorted(src.rglob("*"))
            if p.is_file() and p.suffix.lower() in exts and p.suffix.lower() not in SKIP_SUFFIXES
        )
    return files


def enumerate_telemetry_files(
    cfg: Config, extra_sources: list[Path] | None = None
) -> tuple[list[Path], list[TelemetryFile]]:
    """List every .xrk in the download dir(s), with ingested status.

    Read-only: does not parse the .xrk (parsing is the slow/fragile part, done
    only on ingest). Returns (sources, files).
    """
    lib = Library(cfg.library_root)
    sources = list(cfg.mychron.data_dirs) + list(extra_sources or [])
    known = lib.known_telemetry()

    files: list[TelemetryFile] = []
    for path in scan_sources(cfg, extra_sources):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(
            TelemetryFile(
                path=path, name=path.name, size=size, ingested=(path.name, size) in known
            )
        )
    return sources, files


def ingest_mychron(
    cfg: Config,
    extra_sources: list[Path] | None = None,
    only_names: Collection[str] | None = None,
    force: bool = False,
) -> IngestReport:
    report = IngestReport()
    lib = Library(cfg.library_root)
    logger_tz = cfg.mychron.tzinfo()

    sources, files = enumerate_telemetry_files(cfg, extra_sources)
    report.sources_scanned = [str(s) for s in sources]

    wanted = {n.casefold(): n for n in only_names} if only_names is not None else None
    matched: set[str] = set()

    for tf in files:
        path, size = tf.path, tf.size
        try:
            if wanted is not None:
                key = tf.name.casefold()
                if key not in wanted:
                    continue
                matched.add(key)

            if tf.ingested and not force:
                report.skipped_known += 1
                continue

            info = parse_xrk(path, logger_tz)
            if info.start_utc is not None:
                # A wrongly-set device clock is NOT corrected here: sessions
                # land on whatever day the logger claims, and mismatches are
                # resolved by manually correlating files and videos by name.
                start_utc = info.start_utc
            else:
                start_utc = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ) - timedelta(seconds=info.duration_s)
            end_utc = start_utc + timedelta(seconds=info.duration_s)
            local_day = start_utc.astimezone(logger_tz).date()

            manifest = lib.load_day(local_day)
            day_dir = lib.ensure_day(local_day)

            dest = day_dir / "raw" / "telemetry" / path.name
            if force or not (dest.is_file() and dest.stat().st_size == size):
                shutil.copy2(path, dest)

            log = TelemetryLog(
                file=str(dest.relative_to(day_dir)).replace("\\", "/"),
                source_name=path.name,
                size_bytes=size,
                start_utc=start_utc,
                end_utc=end_utc,
                venue=info.venue,
                driver=info.driver,
                laps=info.laps,
                channels=info.channels,
            )
            # On --force, replace the existing entry in place rather than
            # appending a duplicate for the same (source_name, size).
            existing = next(
                (
                    i
                    for i, t in enumerate(manifest.telemetry)
                    if t.source_name == path.name and t.size_bytes == size
                ),
                None,
            )
            if existing is not None:
                manifest.telemetry[existing] = log
            else:
                manifest.telemetry.append(log)
            if manifest.track is None and info.venue:
                manifest.track = info.venue
            manifest.telemetry.sort(key=lambda t: t.start_utc or datetime.min.replace(tzinfo=timezone.utc))
            lib.save_day(manifest)

            report.copied.append(f"{path} -> {dest}")
        except Exception as exc:  # noqa: BLE001 - a corrupt .xrk must not stop the scan
            report.errors.append(f"{path}: {exc}")

    if wanted is not None:
        report.requested_missing = [orig for key, orig in wanted.items() if key not in matched]

    return report
