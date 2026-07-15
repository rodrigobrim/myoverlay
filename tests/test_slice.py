import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from media_tools.library import DayManifest, RenderOutput
from media_tools.slice import parse_range, parse_timestamp, resolve_render_source, slice_video


def test_parse_timestamp():
    assert parse_timestamp("721") == 721.0
    assert parse_timestamp("721.5") == 721.5
    assert parse_timestamp("12:01") == 721.0
    assert parse_timestamp("14:02") == 842.0
    assert parse_timestamp("1:02:03.5") == 3723.5
    with pytest.raises(ValueError):
        parse_timestamp("1:2:3:4")


def test_parse_range():
    assert parse_range("12:01-14:02") == (721.0, 842.0)
    assert parse_range("721 - 842.5") == (721.0, 842.5)
    with pytest.raises(ValueError, match="end must be after start"):
        parse_range("14:02-12:01")
    with pytest.raises(ValueError, match="invalid range"):
        parse_range("12:01")


def make_manifest_with_render(n=1):
    m = DayManifest(date=date(2026, 7, 13))
    for i in range(n):
        m.renders.append(
            RenderOutput(
                file=f"out/clip{i}_overlay.mp4",
                kind="session",
                rendered_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
                source_videos=[f"raw/video/clip{i}.MP4"],
            )
        )
    return m


def test_resolve_render_source(tmp_path):
    m = make_manifest_with_render(1)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "clip0_overlay.mp4").write_bytes(b"x")
    assert resolve_render_source(m, tmp_path, None).name == "clip0_overlay.mp4"

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_render_source(make_manifest_with_render(2), tmp_path, None)

    m2 = make_manifest_with_render(2)
    (tmp_path / "out" / "clip1_overlay.mp4").write_bytes(b"x")
    assert resolve_render_source(m2, tmp_path, "clip1").name == "clip1_overlay.mp4"

    # raw clips are never sliceable
    with pytest.raises(ValueError, match="no rendered overlay"):
        resolve_render_source(DayManifest(date=date(2026, 7, 13)), tmp_path, None)


def test_registered_slices_do_not_shadow_the_main_render(tmp_path):
    """A slice registered in the manifest (--publish) must not make plain
    `mt slice` ambiguous: full renders win as the default source."""
    m = make_manifest_with_render(1)
    m.renders.append(
        RenderOutput(
            file="out/slices/clip0_overlay_25m15s-30m37s.mp4",
            kind="slice",
            rendered_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
            source_videos=["raw/video/clip0.MP4"],
        )
    )
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "clip0_overlay.mp4").write_bytes(b"x")
    assert resolve_render_source(m, tmp_path, None).name == "clip0_overlay.mp4"


def have_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not available")
def test_slice_video_exact(tmp_path):
    src = tmp_path / "src_overlay.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
        ],
        check=True,
        capture_output=True,
    )
    dest = slice_video(src, 1.0, 3.0, tmp_path / "slices", codec="libx264", preset="ultrafast")
    assert dest.name == "src_overlay_00m01s-00m03s.mp4"
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dest)],
        capture_output=True,
        text=True,
    )
    assert float(out.stdout.strip()) == pytest.approx(2.0, abs=0.15)
