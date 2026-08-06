"""MyChron session listing and download over AiM's own protocol.

Talks to the logger directly through the media_tools.aim package - USB
preferred, WiFi fallback (the client joins the AiM-* access point itself).
No AiM software involved.

The device serves zlib-compressed .xrz session files; downloads are
decompressed on the fly and land as the .xrk libxrk reads, in the first
`telemetry.data_dirs` folder. Ingest then picks them up as usual.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import Config


class RemoteSession(BaseModel):
    # Name exactly as the device lists it (e.g. "a_0186.xrz").
    name: str
    size_bytes: int | None = None
    # A file for this session already exists in telemetry.data_dirs.
    downloaded: bool = False
    # The full CSV row, keys exactly as the device names them (lap counts,
    # best lap, track name/coordinates, ... - varies per model/firmware).
    meta: dict[str, str] = Field(default_factory=dict)


class RemoteListResult(BaseModel):
    # "USB" / "WiFi" once connected, None when no device was reached.
    transport: str | None = None
    sessions: list[RemoteSession] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@dataclass
class DownloadReport:
    # Local paths written, as strings.
    downloaded: list[str] = field(default_factory=list)
    skipped_existing: int = 0
    # Requested names the device does not have.
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = list(self.downloaded)
        if self.skipped_existing:
            out.append(f"{self.skipped_existing} session(s) already downloaded")
        out += [f"! not on the device: {n}" for n in self.missing]
        out += [f"! {e}" for e in self.errors]
        return out


def _local_stems(cfg: Config) -> set[str]:
    """Stems of every telemetry file already on disk, lowercased."""
    stems: set[str] = set()
    for src in cfg.telemetry_dirs():
        if not src.is_dir():
            continue
        stems.update(p.stem.lower() for p in src.rglob("*") if p.is_file())
    return stems


def _is_downloaded(device_name: str, local_stems: set[str]) -> bool:
    # Files downloaded before this client existed carry a venue/session
    # prefix ("KGV 101 _Race_a_0081.xrk" for device session "a_0081.xrz"),
    # so an exact-stem match would re-download every legacy session: match
    # any local stem that ends with the device stem instead.
    stem = Path(device_name).stem.lower()
    return any(s.endswith(stem) for s in local_stems)


def _session_size(meta: dict[str, str]) -> int | None:
    try:
        return int(meta["size"])
    except (KeyError, ValueError):
        return None


def list_remote_sessions(cfg: Config, include_downloaded: bool = False) -> RemoteListResult:
    """List the sessions recorded on the MyChron (default: only ones not on
    disk yet). Connects over USB or WiFi; downloads nothing."""
    from ..aim import NoDeviceError, UserInputNeededError, connect
    from ..aim.catalog import as_catalog

    result = RemoteListResult()
    try:
        dev = connect(prefer=cfg.telemetry.transport, verbose=False)
    except (NoDeviceError, UserInputNeededError) as exc:
        result.notes.append(f"! {exc}")
        return result

    try:
        result.transport = dev.kind
        _columns, _rows, catalog = as_catalog(dev.session_csv())
    # A device can enumerate (USB powered, e.g. charging while off) yet never
    # answer a command: report it as a note, like every other failure - a
    # traceback here would take down `mt run`/`mt watch` too.
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"! {dev.kind}: {exc}")
        return result
    finally:
        dev.close()

    local = _local_stems(cfg)
    for name, meta in catalog.items():
        downloaded = _is_downloaded(name, local)
        if downloaded and not include_downloaded:
            continue
        result.sessions.append(
            RemoteSession(
                name=name, size_bytes=_session_size(meta), downloaded=downloaded, meta=meta
            )
        )
    return result


def download_sessions(
    cfg: Config,
    names: list[str] | None = None,
    force: bool = False,
    echo=None,
    progress=None,
) -> DownloadReport:
    """Download sessions off the MyChron into telemetry.data_dirs[0].

    names=None means every session not already on disk. The device serves
    zlib-compressed .xrz files: each is decompressed and written as
    <stem>.xrk (the format libxrk and the GPS-time recovery read).

    echo: optional callable receiving one line per event, streamed as it
    happens (device transfers take a while). progress: optional
    (name, done, total) callable for in-flight transfer progress.
    """
    from ..aim import NoDeviceError, UserInputNeededError, connect
    from ..aim.catalog import as_catalog

    report = DownloadReport()
    say = echo or (lambda line: None)

    if not cfg.telemetry.data_dirs:
        report.errors.append("no [telemetry] data_dirs configured - nowhere to put downloads")
        return report

    try:
        dev = connect(prefer=cfg.telemetry.transport, verbose=False)
    except (NoDeviceError, UserInputNeededError) as exc:
        report.errors.append(str(exc))
        return report

    try:
        say(f"connected over {dev.kind}")
        try:
            _columns, _rows, catalog = as_catalog(dev.session_csv())
        # Same contract as the per-session loop below: any device failure is
        # an error line, never a traceback.
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{dev.kind}: {exc}")
            return report

        local = _local_stems(cfg)
        if names:
            wanted = list(names)
            report.missing = [n for n in wanted if n not in catalog]
            wanted = [n for n in wanted if n in catalog]
        else:
            wanted = list(catalog)

        dest_dir = cfg.telemetry.data_dirs[0]
        dest_dir.mkdir(parents=True, exist_ok=True)

        for name in wanted:
            if not force and _is_downloaded(name, local):
                report.skipped_existing += 1
                continue
            expected = _session_size(catalog[name])
            if expected is None:
                report.errors.append(f"{name}: device listing carries no size")
                continue
            try:
                buf = io.BytesIO()
                written = dev.fetch(
                    name,
                    buf,
                    expected,
                    progress=(lambda done, total, _n=name: progress(_n, done, total))
                    if progress
                    else None,
                )
                if written != expected:
                    report.errors.append(f"{name}: got {written:,} bytes, expected {expected:,}")
                    continue
                payload = buf.getvalue()
                dest = _write_session(dest_dir, name, payload)
                report.downloaded.append(str(dest))
                say(f"{name}: {written:,} bytes -> {dest}")
            except Exception as exc:  # noqa: BLE001 - one bad session must not stop the rest
                report.errors.append(f"{name}: {exc}")
    finally:
        dev.close()

    return report


def _write_session(dest_dir: Path, name: str, payload: bytes) -> Path:
    """Write one downloaded session, decompressing .xrz -> .xrk.

    The .xrz the device serves is a plain zlib stream of the .xrk (verified
    byte-identical against known-good .xrk files, minus a trailing empty
    note block other tools append). Anything that does not decompress is
    kept verbatim under its device name - never guess at unknown formats
    (.hrz etc).
    """
    try:
        raw = zlib.decompress(payload)
        dest = dest_dir / (Path(name).stem + ".xrk")
    except zlib.error:
        raw = payload
        dest = dest_dir / name
    dest.write_bytes(raw)
    return dest
