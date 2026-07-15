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
    cfg: Config, publish: bool = False, trigger_rs3: bool | None = None
) -> PipelineReport:
    """Full chain. trigger_rs3=None means: download MyChron data whenever the
    RS3 automation is enabled in config - the pipeline takes every action
    itself by default."""
    if trigger_rs3 is None:
        trigger_rs3 = cfg.rs3.enabled
    from .correlate import correlate_day
    from .ingest.camera import ingest_camera
    from .ingest.mychron import ingest_mychron
    from .render import render_day
    from .sync import sync_day

    report = PipelineReport()

    if trigger_rs3 and cfg.rs3.enabled:
        from .ingest.rs3 import trigger_rs3_download

        report.add("rs3", trigger_rs3_download(cfg))

    cam = ingest_camera(cfg)
    report.add("ingest:camera", cam.copied + [f"! {e}" for e in cam.errors])
    myc = ingest_mychron(cfg)
    report.add("ingest:mychron", myc.copied + [f"! {e}" for e in myc.errors])

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
    rs3_files: frozenset[tuple[str, int]]


def snapshot_sources(cfg: Config) -> SourcesSnapshot:
    from .ingest.camera import find_dcim_sources
    from .ingest.mychron import scan_sources

    return SourcesSnapshot(
        dcim_volumes=frozenset(str(p) for p in find_dcim_sources()),
        rs3_files=frozenset((p.name, p.stat().st_size) for p in scan_sources(cfg)),
    )


def has_new_material(before: SourcesSnapshot, after: SourcesSnapshot) -> bool:
    return bool(after.dcim_volumes - before.dcim_volumes) or bool(
        after.rs3_files - before.rs3_files
    )


def watch(cfg: Config, publish: bool, on_report, once: bool = False) -> None:
    """Poll for new camera volumes / RS3 files and run the pipeline on change.

    on_report: callable receiving (PipelineReport) after each triggered run.
    """
    last = snapshot_sources(cfg)

    # Initial pass picks up anything that appeared while the watcher was down
    # (and, with rs3 enabled, immediately tries a MyChron download).
    on_report(run_pipeline(cfg, publish=publish))
    last_rs3_trigger = time.monotonic()

    while True:
        if once:
            return
        time.sleep(cfg.watch.poll_s)

        trigger_rs3 = (
            cfg.rs3.enabled and time.monotonic() - last_rs3_trigger > cfg.rs3.trigger_interval_s
        )
        current = snapshot_sources(cfg)
        if has_new_material(last, current) or trigger_rs3:
            if trigger_rs3:
                last_rs3_trigger = time.monotonic()
            time.sleep(cfg.watch.settle_s)
            on_report(run_pipeline(cfg, publish=publish, trigger_rs3=trigger_rs3))
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
