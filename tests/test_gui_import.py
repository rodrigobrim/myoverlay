"""GUI smoke: the package imports and the views build from canned CLI data
(the ScanResult / RenderPlan JSON contracts) without entering the mainloop."""

import pytest

pytest.importorskip("tkinter")

SCAN = {
    "date_guess": "2026-07-16",
    "video_groups": [
        {"video": {"source_name": "c.MP4", "size_bytes": 1, "start_utc": "2026-07-16T22:14:00Z",
                   "duration_s": 200.0},
         "telemetry": [{"source_name": "s.xrk", "size_bytes": 1, "lap_count": 4, "best_lap": "0:59.00"}]},
    ],
    "orphan_telemetry": [{"source_name": "o.xrk", "size_bytes": 1, "lap_count": 0, "best_lap": "-:--.--"}],
}

PLAN = {
    "date": "2026-07-16", "track": "kgv",
    "items": [{
        "item_id": "c", "session_id": 1,
        "slices": [{"file": "raw/video/c.MP4", "source_name": "c.MP4"}],
        "telemetry_files": ["raw/telemetry/s.xrk"],
        "start_enabled": False, "start_s": 0.0, "end_enabled": False, "end_s": 0.0,
        "quality": "2k", "title": "Karting kgv 2026-07-16 - session 1",
        "description": "desc", "append_best_lap": True, "best_lap_s": 59.0, "best_lap": "0:59.00",
    }],
}


def test_gui_package_imports():
    import media_tools.gui  # noqa: F401
    from media_tools.gui import app, gate1_view, gate2_view, mtclient, widgets  # noqa: F401


def test_mt_base_falls_back_to_module(monkeypatch):
    from media_tools.gui import mtclient

    monkeypatch.delenv("MYOVERLAY_MT", raising=False)
    monkeypatch.setattr("sys.frozen", False, raising=False)
    base = mtclient.mt_base()
    assert base[-2:] == ["-m", "media_tools.cli"]


def _root():
    import tkinter

    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    root.withdraw()
    return root


def test_gate1_builds():
    from media_tools.gui.gate1_view import Gate1View

    root = _root()
    try:
        Gate1View(root, SCAN, on_confirm=lambda: None)
    finally:
        root.destroy()


def test_gate2_editor_round_trips():
    from media_tools.gui.gate2_view import Gate2View

    root = _root()
    try:
        confirmed = {}
        view = Gate2View(root, PLAN, on_confirm=lambda p: confirmed.update({"p": p}))
        view._collect(0)  # editor -> item dict
        item = PLAN["items"][0]
        assert item["quality"] == "2k"
        assert item["title"] == "Karting kgv 2026-07-16 - session 1"
        assert item["slices"][0]["source_name"] == "c.MP4"
        view._confirm()
        assert confirmed["p"] is PLAN
    finally:
        root.destroy()
