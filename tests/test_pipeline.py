from pathlib import Path

import media_tools.pipeline as pipeline
from media_tools.pipeline import (
    PipelineReport,
    SourcesSnapshot,
    has_new_material,
    run_pipeline,
    snapshot_sources,
)


def test_has_new_material():
    a = SourcesSnapshot(dcim_volumes=frozenset(), rs3_files=frozenset())
    b = SourcesSnapshot(dcim_volumes=frozenset({"E:\\DCIM"}), rs3_files=frozenset())
    c = SourcesSnapshot(dcim_volumes=frozenset(), rs3_files=frozenset({("s.xrk", 10)}))
    assert has_new_material(a, b)
    assert has_new_material(a, c)
    assert not has_new_material(b, b)
    # removal (card unplugged) is not new material
    assert not has_new_material(b, a)


def test_snapshot_sources_sees_rs3_files(cfg, tmp_path, monkeypatch):
    rs3 = tmp_path / "rs3"
    rs3.mkdir()
    (rs3 / "a.xrk").write_bytes(b"12345")
    cfg.mychron.rs3_data_dirs = [rs3]
    monkeypatch.setattr("media_tools.ingest.camera.find_dcim_sources", lambda: [])
    snap = snapshot_sources(cfg)
    assert snap.rs3_files == frozenset({("a.xrk", 5)})


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
        "media_tools.ingest.mychron.ingest_mychron",
        lambda cfg: order.append("mychron") or FakeIngestReport(),
    )

    report = run_pipeline(cfg, publish=False)
    assert order == ["camera", "mychron"]
    assert isinstance(report, PipelineReport)
    # empty library: no per-day stages ran
    assert not [l for l in report.lines if l.startswith("[correlate")]


def test_needs_attention_filters_flags():
    r = PipelineReport()
    r.add("sync:2026-07-12", ["+ ok clip", "? low confidence clip", "! broken clip"])
    assert len(r.needs_attention()) == 2
