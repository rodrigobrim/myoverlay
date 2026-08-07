"""Orchestrator (`mt run`) and zero-touch watcher (`mt watch`).

Every stage is idempotent, so the orchestrator simply runs the full chain
over all days; work already recorded in the manifests is skipped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Config
from .library import Library


@dataclass
class PipelineReport:
    lines: list[str] = field(default_factory=list)
    # Called with every line as it is recorded. A full chain takes minutes
    # (device download, sync, render); without this the console stays blank
    # until the last stage finishes and the whole report prints at once.
    on_line: Callable[[str], None] | None = None

    def add(self, stage: str, entries: list[str]) -> None:
        for entry in entries:
            line = f"[{stage}] {entry}"
            self.lines.append(line)
            if self.on_line is not None:
                self.on_line(line)

    def begin(self, stage: str, note: str) -> None:
        """Announce a stage before it runs, so a long one is not silence.

        Streamed only - announcements are not results, so they stay out of
        `lines` (and so out of watch.log and needs_attention).
        """
        if self.on_line is not None:
            self.on_line(f"[{stage}] {note}...")

    def needs_attention(self) -> list[str]:
        return [l for l in self.lines if l.split("] ", 1)[-1][:1] in "!?"]


def run_pipeline(
    cfg: Config,
    publish: bool = False,
    download: bool | None = None,
    day: date | None = None,
    on_line: Callable[[str], None] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> PipelineReport:
    """Full chain. download=None means: pull MyChron data off the device
    whenever [telemetry] auto_download is enabled in config - the pipeline
    takes every action itself by default.

    day restricts the per-day stages (sync/correlate/render/publish) to that
    single day; ingest still runs, since fresh material may belong to it.
    Naming a day is an explicit request for THAT day's material, so the
    MyChron download runs regardless of auto_download (unless the caller
    passes download=False) and asks the device for that day's sessions -
    the same selection `mt telemetry get DAY` makes.

    on_line receives every result line as it happens, plus a note before
    each stage starts; progress receives (name, done, total) during device
    transfers. Both are optional - the returned report is unchanged."""
    if download is None:
        download = day is not None or cfg.telemetry.auto_download
    from .correlate import correlate_day
    from .ingest.camera import ingest_camera
    from .ingest.telemetry import ingest_telemetry
    from .render import render_day
    from .sync import sync_day

    report = PipelineReport(on_line=on_line)

    if download:
        from .ingest.aim import download_sessions

        stage = "telemetry:download"
        # The slowest silent stretch of all when no logger is around: USB
        # probe, then a WiFi scan that has to time out.
        report.begin(stage, "looking for the MyChron (USB, then WiFi)")
        dl = download_sessions(
            cfg,
            days=[day] if day else None,
            echo=lambda line: report.add(stage, [line]),
            progress=progress,
        )
        report.add(stage, dl.summary_lines())
        # A day the device's catalog dates differently (the MyChron clock
        # wanders) selects nothing, which would leave the stage silent -
        # indistinguishable from no download at all. Say so instead.
        if day and not dl.did_anything():
            report.add(
                stage,
                [
                    f"? no session dated {day} on the device "
                    f"(device clock - check `mt telemetry list`)"
                ],
            )

    report.begin("ingest:camera", "scanning the camera / card")
    cam = ingest_camera(cfg)
    report.add("ingest:camera", cam.copied + [f"! {e}" for e in cam.errors])
    report.begin("ingest:telemetry", "scanning for new telemetry")
    tel = ingest_telemetry(cfg)
    report.add("ingest:telemetry", tel.copied + [f"! {e}" for e in tel.errors])

    lib = Library(cfg.library_root)
    for d in ([day] if day else lib.day_dates()):
        manifest = lib.load_day(d)
        report.begin(f"sync:{d}", "syncing clips against the day's telemetry")
        report.add(f"sync:{d}", sync_day(cfg, manifest, lib.day_dir(d)))
        # Correlate AFTER sync: synced clips assign to sessions by their
        # true times, immune to camera/device clock error.
        cor = correlate_day(manifest, cfg.camera.clock_tolerance_s)
        report.add(
            f"correlate:{d}",
            [f"{cor.sessions} session(s), {cor.assigned_videos} clip(s) assigned"]
            + [f"? unassigned: {f}" for f in cor.unassigned_videos],
        )
        report.begin(f"render:{d}", "rendering overlays (this is the long one)")
        report.add(f"render:{d}", render_day(cfg, manifest, lib.day_dir(d)))
        if publish:
            from .publish import publish_day

            report.begin(f"publish:{d}", "uploading to YouTube")
            report.add(f"publish:{d}", publish_day(cfg, manifest, lib.day_dir(d)))
        lib.save_day(manifest)

    return report


# --- watcher ---------------------------------------------------------------


@dataclass(frozen=True)
class SourcesSnapshot:
    dcim_volumes: frozenset[str]
    telemetry_files: frozenset[tuple[str, int]]


def snapshot_sources(cfg: Config) -> SourcesSnapshot:
    from .ingest import mtp
    from .ingest.camera import find_dcim_sources
    from .ingest.telemetry import scan_sources

    return SourcesSnapshot(
        dcim_volumes=frozenset(str(p) for p in find_dcim_sources())
        | frozenset(mtp.find_mtp_sources()),
        telemetry_files=frozenset((p.name, p.stat().st_size) for p in scan_sources(cfg)),
    )


def has_new_material(before: SourcesSnapshot, after: SourcesSnapshot) -> bool:
    return bool(after.dcim_volumes - before.dcim_volumes) or bool(
        after.telemetry_files - before.telemetry_files
    )


def watch(cfg: Config, publish: bool, on_report, once: bool = False) -> None:
    """Poll for new camera volumes / telemetry files and run the pipeline on change.

    on_report: callable receiving (PipelineReport) after each triggered run.
    """
    last = snapshot_sources(cfg)

    # Initial pass picks up anything that appeared while the watcher was down
    # (and, with auto_download enabled, immediately tries a MyChron download).
    on_report(run_pipeline(cfg, publish=publish))
    last_download = time.monotonic()

    while True:
        if once:
            return
        time.sleep(cfg.watch.poll_s)

        download = (
            cfg.telemetry.auto_download
            and time.monotonic() - last_download > cfg.telemetry.download_interval_s
        )
        current = snapshot_sources(cfg)
        if has_new_material(last, current) or download:
            if download:
                last_download = time.monotonic()
            time.sleep(cfg.watch.settle_s)
            on_report(run_pipeline(cfg, publish=publish, download=download))
            current = snapshot_sources(cfg)
        last = current


def write_watch_log(cfg: Config, report: PipelineReport) -> Path | None:
    """Append triggered-run results to library_root/watch.log."""
    interesting = [l for l in report.lines if not l.endswith("nothing to publish")]
    if not interesting:
        return None
    log = cfg.library_root / "watch.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    from .library import utcnow

    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"--- run at {utcnow().isoformat()} ---\n")
        for line in interesting:
            fh.write(line + "\n")
    return log
