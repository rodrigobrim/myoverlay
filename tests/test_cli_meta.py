"""CLI contracts for `mt publish --show-published` and `mt meta`."""

from datetime import date, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

import media_tools.cli as cli
from media_tools.library import (
    DayManifest,
    Lap,
    Library,
    PublishRecord,
    RenderOutput,
    TelemetryLog,
    TrackSession,
    VideoClip,
)

runner = CliRunner()
DAY = date(2026, 7, 30)


def _flat(text: str) -> str:
    """Collapse whitespace: rich wraps long lines at the terminal width, so
    single logical lines may arrive split across physical lines."""
    return " ".join(text.split())

T1 = datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def cli_cfg(cfg, monkeypatch):
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    return cfg


def _seed(cfg, publishes=None) -> DayManifest:
    """A day with one render; by default published once as v1/unlisted."""
    if publishes is None:
        publishes = {
            "out/a_overlay.mp4": [
                PublishRecord(video_id="v1", url="https://youtu.be/v1",
                              privacy="unlisted", published_at=T1, title="T1"),
            ]
        }
    manifest = DayManifest(
        date=DAY,
        track="KGV 111",
        renders=[RenderOutput(file="out/a_overlay.mp4", session_id=None, kind="session",
                              rendered_at=T1, source_videos=[])],
        publishes=publishes,
    )
    Library(cfg.library_root).save_day(manifest)
    return manifest


# --- mt publish --show-published ------------------------------------------


def test_publish_show_published_lists_registry(cli_cfg):
    _seed(cli_cfg)
    r = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published"])
    assert r.exit_code == 0
    assert "2026-07-30:" in r.stdout
    assert "out/a_overlay.mp4 -> https://youtu.be/v1 (unlisted, published 2026-07-30 20:00 UTC)" in _flat(r.stdout)
    assert "title: T1" in r.stdout


def test_publish_show_published_all_days_and_empty_day(cli_cfg):
    _seed(cli_cfg)
    r = runner.invoke(cli.app, ["publish", "--show-published"])  # no DAY -> every day
    assert r.exit_code == 0 and "https://youtu.be/v1" in r.stdout
    r2 = runner.invoke(cli.app, ["publish", "1999-01-01", "--show-published"])
    assert r2.exit_code == 0 and "nothing published" in r2.stdout


def test_publish_show_published_uploads_nothing(cli_cfg):
    """With a pending (unpublished) render, --show-published must not try to
    upload: exit 0 without OAuth (building an uploader would fail in tests)
    and the render stays unpublished."""
    _seed(cli_cfg, publishes={})
    r = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published"])
    assert r.exit_code == 0 and "nothing published" in r.stdout
    assert Library(cli_cfg.library_root).load_day(DAY).publishes == {}


def test_publish_show_published_refuses_upload_flags(cli_cfg):
    _seed(cli_cfg)
    for flag in ("--force", "--dry-run"):
        r = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published", flag])
        assert r.exit_code == 2
        assert "--show-published only lists" in r.stdout


def test_publish_show_published_refuses_detail_options(cli_cfg):
    _seed(cli_cfg)
    for opts in (["--title", "X"], ["--desc", "Y"], ["--visibility", "public"]):
        r = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published", *opts])
        assert r.exit_code == 2
        assert "--show-published only lists" in r.stdout


def test_publish_detail_options_share_metas_validation(cli_cfg):
    """The same refusals as `mt meta`, from the one shared rulebook."""
    _seed(cli_cfg)
    r = runner.invoke(cli.app, ["publish", "2026-07-30", "--visibility", "secret"])
    assert r.exit_code == 2
    assert "--visibility must be one of: private, unlisted, public" in r.stdout
    for opts in (["--no-meta"], ["--no-meta", "--visibility", "public"]):
        r = runner.invoke(cli.app, ["publish", "2026-07-30", *opts])
        assert r.exit_code == 2
        assert "--no-meta needs --title and/or --description" in r.stdout


def test_publish_show_published_superseded_and_video_filter(cli_cfg):
    _seed(cli_cfg, publishes={
        "out/a_overlay.mp4": [
            PublishRecord(video_id="v1", url="https://youtu.be/v1",
                          privacy="unlisted", published_at=T1, title="T1"),
            PublishRecord(video_id="v2", url="https://youtu.be/v2",
                          privacy="unlisted", published_at=T2, title="T2"),
        ]
    })
    r = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published"])
    assert "https://youtu.be/v2 (unlisted, published 2026-07-30 21:00 UTC)" in _flat(r.stdout)
    assert "superseded: https://youtu.be/v1" in r.stdout
    r2 = runner.invoke(
        cli.app, ["publish", "2026-07-30", "--show-published", "--video", "nomatch"]
    )
    assert r2.exit_code == 0 and "nothing published" in r2.stdout


# --- mt meta DAY (no VIDEO) == mt publish DAY --show-published ------------


def test_meta_without_video_delegates_to_show_published(cli_cfg):
    _seed(cli_cfg)
    r_meta = runner.invoke(cli.app, ["meta", "2026-07-30"])
    r_pub = runner.invoke(cli.app, ["publish", "2026-07-30", "--show-published"])
    assert r_meta.exit_code == 0
    assert r_meta.stdout == r_pub.stdout


def test_meta_options_need_a_video(cli_cfg):
    _seed(cli_cfg)
    for opts in (["--title", "X"], ["--desc", "Y"], ["--visibility", "public"]):
        r = runner.invoke(cli.app, ["meta", "2026-07-30", *opts])
        assert r.exit_code == 2
        assert "need a VIDEO argument" in r.stdout


# --- mt meta contracts ----------------------------------------------------


def test_meta_no_meta_needs_title_or_description(cli_cfg):
    _seed(cli_cfg)
    for args in (["a_overlay", "--no-meta"],
                 ["a_overlay", "--no-meta", "--visibility", "public"],
                 ["--no-meta"]):
        r = runner.invoke(cli.app, ["meta", "2026-07-30", *args])
        assert r.exit_code == 2
        assert "--no-meta needs --title and/or --description" in r.stdout


def test_meta_rejects_bad_visibility(cli_cfg):
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay", "--visibility", "secret"])
    assert r.exit_code == 2
    assert "--visibility must be one of: private, unlisted, public" in r.stdout


def test_meta_unknown_video_errors(cli_cfg):
    _seed(cli_cfg)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "nope"])
    assert r.exit_code == 2
    assert "no published video matching" in r.stdout


def test_meta_hints_when_rendered_but_unpublished(cli_cfg):
    _seed(cli_cfg, publishes={})
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay"])
    assert r.exit_code == 2
    assert "rendered but not published" in r.stdout


# --- mt meta functionality (YouTube calls faked) --------------------------


@pytest.fixture
def fake_service(monkeypatch):
    monkeypatch.setattr("media_tools.meta.youtube_service", lambda cfg: object())


def test_meta_shows_current_details(cli_cfg, fake_service, monkeypatch):
    _seed(cli_cfg)
    seen = {}

    def fake_fetch(video_id, service):
        seen["video_id"] = video_id
        return {
            "title": "Live title",
            "description": "line1\nline2",
            "visibility": "unlisted",
            "published_at": "2026-07-30T20:00:00Z",
        }

    monkeypatch.setattr("media_tools.meta.fetch_details", fake_fetch)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay"])
    assert r.exit_code == 0
    assert seen["video_id"] == "v1"
    assert "out/a_overlay.mp4" in r.stdout and "https://youtu.be/v1" in r.stdout
    assert "title:      Live title" in r.stdout
    assert "visibility: unlisted" in r.stdout
    assert "published:  2026-07-30T20:00:00Z" in r.stdout
    assert "line1" in r.stdout and "line2" in r.stdout


def test_meta_show_reports_vanished_video(cli_cfg, fake_service, monkeypatch):
    _seed(cli_cfg)
    monkeypatch.setattr("media_tools.meta.fetch_details", lambda video_id, service: None)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay"])
    assert r.exit_code == 1
    assert "no longer exists on YouTube" in r.stdout


@pytest.fixture
def sent(monkeypatch):
    sent = {}

    def fake_update(video_id, service, title=None, description=None, visibility=None):
        sent.update(video_id=video_id, title=title, description=description,
                    visibility=visibility)

    monkeypatch.setattr("media_tools.meta.update_details", fake_update)
    return sent


def test_meta_updates_fields_and_registry(cli_cfg, fake_service, sent):
    _seed(cli_cfg)
    r = runner.invoke(cli.app, [
        "meta", "2026-07-30", "a_overlay",
        "--title", "Meu vídeo", "--desc", "Minha descrição.", "--visibility", "public",
    ])
    assert r.exit_code == 0
    # no laps seeded -> the auto title meta is track + DD/MM/YY only
    assert sent["video_id"] == "v1"
    assert sent["title"] == "Meu vídeo - KGV 111 30/07/26"
    assert sent["description"].startswith("Minha descrição.\n\nRecorded 2026-07-30 at KGV 111.")
    assert sent["description"].endswith("https://github.com/rodrigobrim/myoverlay")
    assert sent["visibility"] == "public"
    # the manifest registry mirrors what was sent
    rec = Library(cli_cfg.library_root).load_day(DAY).publishes["out/a_overlay.mp4"][-1]
    assert rec.title == sent["title"]
    assert rec.description == sent["description"]
    assert rec.privacy == "public"
    assert "updated https://youtu.be/v1" in r.stdout
    assert "Meu vídeo - KGV 111 30/07/26" in r.stdout


def test_meta_no_meta_sets_verbatim(cli_cfg, fake_service, sent):
    _seed(cli_cfg)
    r = runner.invoke(
        cli.app, ["meta", "2026-07-30", "a_overlay", "--title", "Só isso", "--no-meta"]
    )
    assert r.exit_code == 0
    assert sent == {"video_id": "v1", "title": "Só isso",
                    "description": None, "visibility": None}
    rec = Library(cli_cfg.library_root).load_day(DAY).publishes["out/a_overlay.mp4"][-1]
    assert rec.title == "Só isso"
    assert rec.privacy == "unlisted"  # untouched


def test_meta_visibility_only(cli_cfg, fake_service, sent):
    _seed(cli_cfg)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay", "--visibility", "private"])
    assert r.exit_code == 0
    assert sent == {"video_id": "v1", "title": None,
                    "description": None, "visibility": "private"}
    rec = Library(cli_cfg.library_root).load_day(DAY).publishes["out/a_overlay.mp4"][-1]
    assert rec.privacy == "private"
    assert rec.title == "T1"  # untouched


def test_meta_targets_latest_upload(cli_cfg, fake_service, sent):
    _seed(cli_cfg, publishes={
        "out/a_overlay.mp4": [
            PublishRecord(video_id="v1", url="https://youtu.be/v1",
                          privacy="unlisted", published_at=T1),
            PublishRecord(video_id="v2", url="https://youtu.be/v2",
                          privacy="unlisted", published_at=T2),
        ]
    })
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay", "--visibility", "public"])
    assert r.exit_code == 0
    assert "uploaded 2 times; using the latest" in r.stdout
    assert sent["video_id"] == "v2"


def _seed_two_sessions(cfg, render_sid: int, clip_sid: int | None) -> None:
    """The real 2026-07-30 shape: a rollout-only session (one out-lap, no
    complete lap) plus the clip's real session, and a render row whose
    session_id may disagree with its source clip's."""
    s1_start = datetime(2026, 7, 30, 23, 4, tzinfo=timezone.utc)
    s11_start = datetime(2026, 7, 31, 1, 25, tzinfo=timezone.utc)
    manifest = DayManifest(
        date=DAY,
        track="KGV 111",
        videos=[VideoClip(file="raw/video/c.MP4", source_name="c.MP4", size_bytes=1,
                          start_utc_estimate=s11_start, session_id=clip_sid)],
        sessions=[
            TrackSession(id=1, start_utc=s1_start,
                         end_utc=s1_start + timedelta(seconds=9)),
            TrackSession(id=11, start_utc=s11_start,
                         end_utc=s11_start + timedelta(minutes=14)),
        ],
        telemetry=[
            # session 1: MyChron only logged the rollout - a single lap, so no
            # beacon-complete lap and therefore no best lap
            TelemetryLog(file="raw/telemetry/s1.xrk", source_name="s1.xrk", size_bytes=1,
                         start_utc=s1_start, end_utc=s1_start + timedelta(seconds=9),
                         session_id=1, laps=[Lap(num=1, start_s=0.0, end_s=9.3)]),
            # session 11: out-lap, one complete 61.555 s lap (best), in-lap
            TelemetryLog(file="raw/telemetry/s11.xrk", source_name="s11.xrk", size_bytes=1,
                         start_utc=s11_start, end_utc=s11_start + timedelta(minutes=14),
                         session_id=11,
                         laps=[Lap(num=1, start_s=0.0, end_s=62.345),
                               Lap(num=2, start_s=62.345, end_s=123.9),
                               Lap(num=3, start_s=123.9, end_s=185.0)]),
        ],
        renders=[RenderOutput(file="out/c_overlay.mp4", session_id=render_sid,
                              kind="session", rendered_at=s11_start,
                              source_videos=["raw/video/c.MP4"])],
        publishes={"out/c_overlay.mp4": [
            PublishRecord(video_id="v1", url="https://youtu.be/v1",
                          privacy="private", published_at=T1)
        ]},
    )
    Library(cfg.library_root).save_day(manifest)


def test_meta_title_uses_source_clip_session_when_render_row_is_stale(
    cli_cfg, fake_service, sent
):
    """Regression (real 2026-07-30 library): the render row for the 0015 clip
    carried session_id=1 - a 9-second rollout-only session with no complete lap
    - while the clip itself belongs to session 11. The best lap must come from
    the clip's session; before the fix the '- BL m:ss.ss' block silently
    vanished from the title and description."""
    _seed_two_sessions(cli_cfg, render_sid=1, clip_sid=11)
    r = runner.invoke(cli.app, [
        "meta", "2026-07-30", "c_overlay", "--title", "Karteiros Master",
        "--desc", "Minha descrição.",
    ])
    assert r.exit_code == 0
    assert sent["title"] == "Karteiros Master - KGV 111 30/07/26 - BL 1:01.56"
    assert sent["description"].startswith(
        "Minha descrição.\n\nRecorded 2026-07-30 at KGV 111.\nBest lap: 1:01.56"
    )
    assert "no complete lap" not in r.stdout


def test_meta_warns_when_the_session_really_has_no_complete_lap(
    cli_cfg, fake_service, sent
):
    """Both the render row and its clip on the rollout-only session: there is
    genuinely no best lap, so the title carries none - but say so instead of
    dropping it silently."""
    _seed_two_sessions(cli_cfg, render_sid=1, clip_sid=1)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "c_overlay", "--title", "Karteiros Master"])
    assert r.exit_code == 0
    assert sent["title"] == "Karteiros Master - KGV 111 30/07/26"
    assert "no complete lap" in r.stdout


def test_meta_no_meta_skips_the_best_lap_warning(cli_cfg, fake_service, sent):
    _seed_two_sessions(cli_cfg, render_sid=1, clip_sid=1)
    r = runner.invoke(
        cli.app, ["meta", "2026-07-30", "c_overlay", "--title", "Karteiros Master", "--no-meta"]
    )
    assert r.exit_code == 0
    assert sent["title"] == "Karteiros Master"
    assert "no complete lap" not in r.stdout


def test_meta_update_of_vanished_video_fails_cleanly(cli_cfg, fake_service, monkeypatch):
    _seed(cli_cfg)

    def gone(video_id, service, **kw):
        raise ValueError(f"video {video_id} no longer exists on YouTube")

    monkeypatch.setattr("media_tools.meta.update_details", gone)
    r = runner.invoke(cli.app, ["meta", "2026-07-30", "a_overlay", "--visibility", "public"])
    assert r.exit_code == 1
    assert "no longer exists on YouTube" in r.stdout
    # nothing recorded on failure
    rec = Library(cli_cfg.library_root).load_day(DAY).publishes["out/a_overlay.mp4"][-1]
    assert rec.privacy == "unlisted"
