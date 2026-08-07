from datetime import date
from pathlib import Path

import media_tools.pipeline as pipeline
from media_tools.library import Library
from media_tools.pipeline import (
    PipelineReport,
    SourcesSnapshot,
    has_new_material,
    run_pipeline,
    snapshot_sources,
)


def test_has_new_material():
    a = SourcesSnapshot(dcim_volumes=frozenset(), telemetry_files=frozenset())
    b = SourcesSnapshot(dcim_volumes=frozenset({"E:\\DCIM"}), telemetry_files=frozenset())
    c = SourcesSnapshot(dcim_volumes=frozenset(), telemetry_files=frozenset({("s.xrk", 10)}))
    assert has_new_material(a, b)
    assert has_new_material(a, c)
    assert not has_new_material(b, b)
    # removal (card unplugged) is not new material
    assert not has_new_material(b, a)


def test_snapshot_sources_sees_telemetry_files(cfg, tmp_path, monkeypatch):
    tel = tmp_path / "tel"
    tel.mkdir()
    (tel / "a.xrk").write_bytes(b"12345")
    cfg.telemetry.data_dirs = [tel]
    monkeypatch.setattr("media_tools.ingest.camera.find_dcim_sources", lambda: [])
    snap = snapshot_sources(cfg)
    assert snap.telemetry_files == frozenset({("a.xrk", 5)})


def test_run_pipeline_stage_order_and_report(cfg, monkeypatch):
    order = []

    class FakeIngestReport:
        copied = []
        errors = []
        skipped_known = 0
        sources_scanned = []

    monkeypatch.setattr(
        "media_tools.ingest.camera.ingest_camera",
        lambda cfg: order.append("camera") or FakeIngestReport(),
    )
    monkeypatch.setattr(
        "media_tools.ingest.telemetry.ingest_telemetry",
        lambda cfg: order.append("telemetry") or FakeIngestReport(),
    )

    report = run_pipeline(cfg, publish=False)
    assert order == ["camera", "telemetry"]
    assert isinstance(report, PipelineReport)
    # empty library: no per-day stages ran
    assert not [l for l in report.lines if l.startswith("[correlate")]


def test_run_pipeline_day_restricts_per_day_stages(cfg, monkeypatch):
    class FakeIngestReport:
        copied = []
        errors = []

    monkeypatch.setattr(
        "media_tools.ingest.camera.ingest_camera", lambda cfg: FakeIngestReport()
    )
    monkeypatch.setattr(
        "media_tools.ingest.telemetry.ingest_telemetry", lambda cfg: FakeIngestReport()
    )
    lib = Library(cfg.library_root)
    for d in (date(2026, 7, 12), date(2026, 7, 13)):
        lib.save_day(lib.load_day(d))

    synced = []
    monkeypatch.setattr(
        "media_tools.sync.sync_day",
        lambda cfg, manifest, day_dir: synced.append(manifest.date) or [],
    )
    monkeypatch.setattr("media_tools.render.render_day", lambda cfg, manifest, day_dir: [])

    run_pipeline(cfg, day=date(2026, 7, 13))
    assert synced == [date(2026, 7, 13)]


def test_needs_attention_filters_flags():
    r = PipelineReport()
    r.add("sync:2026-07-12", ["+ ok clip", "? low confidence clip", "! broken clip"])
    assert len(r.needs_attention()) == 2
