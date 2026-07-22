"""Full-path resolution of the bundled binaries (media_tools.tools)."""

import os

import pytest

import media_tools.tools as tools


@pytest.fixture(autouse=True)
def clear_tool_env(monkeypatch):
    monkeypatch.delenv("MYOVERLAY_FFMPEG_DIR", raising=False)
    monkeypatch.delenv("MYOVERLAY_GCLOUD_BIN", raising=False)
    # Nothing in these tests should touch a real config.
    monkeypatch.setattr(tools, "_config_install_dir", lambda: None)
    tools._reset_cache()
    yield
    tools._reset_cache()


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="ascii")
    return path


def test_bare_name_when_nothing_resolves():
    assert tools.ffmpeg_exe() == "ffmpeg"
    assert tools.ffprobe_exe() == "ffprobe"


def test_env_var_dir_wins(tmp_path, monkeypatch):
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    _touch(tmp_path / exe)
    monkeypatch.setenv("MYOVERLAY_FFMPEG_DIR", str(tmp_path))
    assert tools.ffmpeg_exe() == str(tmp_path / exe)


def test_env_var_dir_missing_exe_falls_through(tmp_path, monkeypatch):
    # Env var points at a dir that has no ffmpeg -> bare name, not a bad path.
    monkeypatch.setenv("MYOVERLAY_FFMPEG_DIR", str(tmp_path))
    assert tools.ffmpeg_exe() == "ffmpeg"


def test_config_install_dir_resolves_full_path(tmp_path, monkeypatch):
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    ffmpeg = _touch(tmp_path / "_internal" / "ffmpeg" / exe)
    monkeypatch.setattr(tools, "_config_install_dir", lambda: tmp_path)
    assert tools.ffmpeg_exe() == str(ffmpeg)


def test_config_pointing_at_deleted_install_falls_back(tmp_path, monkeypatch):
    # install_dir set, but no ffmpeg under it (moved/deleted) -> bare name.
    monkeypatch.setattr(tools, "_config_install_dir", lambda: tmp_path / "gone")
    assert tools.ffmpeg_exe() == "ffmpeg"


def test_gcloud_cmd_keeps_cmd_c_wrapper_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    prefix = tools.gcloud_cmd()
    assert prefix[:2] == ["cmd", "/c"]
    assert prefix[2] == "gcloud"  # bare, nothing resolved


def test_gcloud_cmd_full_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    gcloud = _touch(tmp_path / "gcloud.cmd")
    monkeypatch.setenv("MYOVERLAY_GCLOUD_BIN", str(tmp_path))
    assert tools.gcloud_cmd() == ["cmd", "/c", str(gcloud)]


def test_gcloud_available_true_when_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    _touch(tmp_path / "gcloud.cmd")
    monkeypatch.setenv("MYOVERLAY_GCLOUD_BIN", str(tmp_path))
    assert tools.gcloud_available() is True


def test_gcloud_available_false_when_absent(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.gcloud_available() is False
