"""MyChron ingestion: pick up new .xrk sessions from Race Studio 3's data dir.

Race Studio 3 downloads sessions from the MyChron over WiFi into its data
directory. We scan that directory (plus any extras), parse each new file with
libxrk, and copy it into the library day folder derived from the session's
log date. Identity = filename + size, so re-running is a no-op.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from dateutil import parser as dtparser

from ..config import Config
from ..library import Lap, Library, TelemetryLog


@dataclass
class XrkInfo:
    start_utc: datetime | None
    duration_s: float
    laps: list[Lap]
    venue: str | None
    driver: str | None
    channels: list[str]


@dataclass
class IngestReport:
    copied: list[str] = field(default_factory=list)
    skipped_known: int = 0
    sources_scanned: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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
    from libxrk import aim_xrk  # deferred: import builds Cython state

    log = aim_xrk(str(path))

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
    return XrkInfo(
        start_utc=_parse_log_datetime(meta, logger_tz),
        duration_s=duration_ms / 1000.0,
        laps=laps,
        venue=meta.get("Venue"),
        driver=meta.get("Driver"),
        channels=sorted(log.channels.keys()),
    )


def scan_sources(cfg: Config, extra_sources: list[Path] | None = None) -> list[Path]:
    exts = {e.lower() for e in cfg.mychron.extensions}
    files: list[Path] = []
    for src in list(cfg.mychron.rs3_data_dirs) + list(extra_sources or []):
        if not src.is_dir():
            continue
        files.extend(
            p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in exts
        )
    return files


def ingest_mychron(cfg: Config, extra_sources: list[Path] | None = None) -> IngestReport:
    report = IngestReport()
    lib = Library(cfg.library_root)
    logger_tz = cfg.mychron.tzinfo()

    sources = list(cfg.mychron.rs3_data_dirs) + list(extra_sources or [])
    report.sources_scanned = [str(s) for s in sources]

    seen = lib.known_telemetry()

    for path in scan_sources(cfg, extra_sources):
        try:
            size = path.stat().st_size
            if (path.name, size) in seen:
                report.skipped_known += 1
                continue

            info = parse_xrk(path, logger_tz)
            if info.start_utc is not None:
                # Correct a wrongly-set device clock (mychron.clock_reads/
                # clock_actual) - but only for timestamps that are actually
                # implausible (far in the future). Once the user fixes the
                # device clock, newer sessions carry sane dates and must NOT
                # be shifted. The mtime fallback below is real time already.
                start_utc = info.start_utc
                if start_utc > datetime.now(timezone.utc) + timedelta(days=90):
                    start_utc = start_utc + cfg.mychron.clock_offset()
            else:
                start_utc = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ) - timedelta(seconds=info.duration_s)
            end_utc = start_utc + timedelta(seconds=info.duration_s)
            local_day = start_utc.astimezone(logger_tz).date()

            manifest = lib.load_day(local_day)
            day_dir = lib.ensure_day(local_day)

            dest = day_dir / "raw" / "telemetry" / path.name
            if not (dest.is_file() and dest.stat().st_size == size):
                shutil.copy2(path, dest)

            manifest.telemetry.append(
                TelemetryLog(
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
            )
            if manifest.track is None and info.venue:
                manifest.track = info.venue
            manifest.telemetry.sort(key=lambda t: t.start_utc or datetime.min.replace(tzinfo=timezone.utc))
            lib.save_day(manifest)

            seen.add((path.name, size))
            report.copied.append(f"{path} -> {dest}")
        except Exception as exc:  # noqa: BLE001 - a corrupt .xrk must not stop the scan
            report.errors.append(f"{path}: {exc}")

    return report
