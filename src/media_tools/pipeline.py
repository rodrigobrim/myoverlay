"""Orchestrator (`mt run`) and zero-touch watcher (`mt watch`).

Every stage is idempotent, so the orchestrator simply runs the full chain
over all days; work already recorded in the manifests is skipped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .library import Library


@dataclass
class PipelineReport:
    lines: list[str] = field(default_factory=list)

    def add(self, stage: str, entries: list[str]) -> None:
        for entry in entries:
            self.lines.append(f"[{stage}] {entry}")

    def needs_attention(self) -> list[str]:
        return [l for l in self.lines if l.split("] ", 1)[-1][:1] in "!?"]


def run_pipeline(
    cfg: Config, publish: bool = False, download: bool | None = None
) -> PipelineReport:
    """Full chain. download=None means: pull MyChron data off the device
    whenever [telemetry] auto_download is enabled in config - the pipeline
    takes every action itself by default."""
    if download is None:
        download = cfg.telemetry.auto_download
    from .correlate import correlate_day
    from .ingest.camera import ingest_camera
    from .ingest.telemetry import ingest_telemetry
    from .render import render_day
    from .sync import sync_day

    report = PipelineReport()

    if download:
        from .ingest.aim import download_sessions

        report.add("telemetry:download", download_sessions(cfg).lines())

    cam = ingest_camera(cfg)
    report.add("ingest:camera", cam.copied + [f"! {e}" for e in cam.errors])
    tel = ingest_telemetry(cfg)
    report.add("ingest:telemetry", tel.copied + [f"! {e}" for e in tel.errors])

    lib = Library(cfg.library_root)
    for d in lib.day_dates():
        manifest = lib.load_day(d)
        report.add(f"sync:{d}", sync_day(cfg, manifest, lib.day_dir(d)))
        # Correlate AFTER sync: synced clips assign to sessions by their
        # true times, immune to camera/device clock error.
        cor = correlate_day(manifest, cfg.camera.clock_tolerance_s)
        report.add(
            f"correlate:{d}",
            [f"{cor.sessions} session(s), {cor.assigned_videos} clip(s) assigned"]
            + [f"? unassigned: {f}" for f in cor.unassigned_videos],
        )
        report.add(f"render:{d}", render_day(cfg, manifest, lib.day_dir(d)))
        if publish:
            from .publish import publish_day

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
