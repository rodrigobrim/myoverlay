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


def _fake_frozen(monkeypatch, install_dir):
    """Pretend we run as the frozen exe installed in `install_dir`."""
    monkeypatch.setattr(tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tools.sys, "executable", str(install_dir / "MyOverlay.exe"))


def test_frozen_exe_dir_resolves_without_config(tmp_path, monkeypatch):
    # The launcher that wrote [tools] install_dir / the env vars may predate
    # this code (it only updates on a rebuild); the exe's own location must be
    # enough. This is the regression: gcloud installed next to the exe, but
    # config.toml had no [tools] section at all.
    monkeypatch.setattr(os, "name", "nt")
    gcloud = _touch(tmp_path / "google-cloud-sdk" / "bin" / "gcloud.cmd")
    ffmpeg = _touch(tmp_path / "_internal" / "ffmpeg" / "ffmpeg.exe")
    _fake_frozen(monkeypatch, tmp_path)
    assert tools.gcloud_available() is True
    assert tools.gcloud_cmd() == ["cmd", "/c", str(gcloud)]
    assert tools.ffmpeg_exe() == str(ffmpeg)


def test_frozen_exe_dir_without_bundled_tools_falls_back(tmp_path, monkeypatch):
    # A frozen run whose install lacks the SDK (component unticked) must still
    # degrade to PATH lookup rather than returning a non-existent path.
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    _fake_frozen(monkeypatch, tmp_path)
    assert tools.gcloud_available() is False
    assert tools.ffmpeg_exe() == "ffmpeg"


def test_config_install_dir_still_used_when_not_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tools.sys, "frozen", False, raising=False)
    gcloud = _touch(tmp_path / "google-cloud-sdk" / "bin" / "gcloud.cmd")
    monkeypatch.setattr(tools, "_config_install_dir", lambda: tmp_path)
    assert tools.gcloud_cmd() == ["cmd", "/c", str(gcloud)]


def test_gcloud_cmd_keeps_cmd_c_wrapper_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    prefix = tools.gcloud_cmd()
    assert prefix[:2] == ["cmd", "/c"]
    assert prefix[2] == "gcloud"  # bare, nothing resolved


def test_gcloud_cmd_prefers_sdk_python_over_cmd_c(tmp_path, monkeypatch):
    # `cmd /c "<path with spaces>\gcloud.cmd" "--filter=a AND b"` breaks: cmd
    # strips the outer quotes and executes `C:\Program`. With the SDK's
    # bundled python present, gcloud must be invoked through it instead.
    monkeypatch.setattr(os, "name", "nt")
    sdk = tmp_path / "Program Files" / "MyOverlay" / "google-cloud-sdk"
    gcloud = _touch(sdk / "bin" / "gcloud.cmd")
    python = _touch(sdk / "platform" / "bundledpython" / "python.exe")
    entry = _touch(sdk / "lib" / "gcloud.py")
    monkeypatch.setenv("MYOVERLAY_GCLOUD_BIN", str(gcloud.parent))
    assert tools.gcloud_cmd() == [str(python), str(entry)]


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


# --- probe_streams -----------------------------------------------------------
#
# ffmpeg 9's ffprobe answers `-show_entries stream=...` with three top-level
# sections (programs, stream_groups, streams). Flattened by the csv/default
# writers that becomes a blank line plus a duplicate of every grouped stream -
# which is what made `render` die with
# "invalid literal for int() with base 10: '1080\n\n1920'".

FFPROBE_JSON_WITH_STREAM_GROUPS = """{
    "programs": [],
    "stream_groups": [
        { "streams": [ { "index": 0, "width": 1920, "height": 1080 } ] }
    ],
    "streams": [
        { "index": 0, "width": 1920, "height": 1080 }
    ]
}"""


class _Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _fake_run(stdout="", returncode=0, seen=None):
    def run(cmd, **kwargs):
        if seen is not None:
            seen.append(cmd)
        return _Completed(stdout, returncode)

    return run


def test_probe_streams_ignores_stream_groups(monkeypatch):
    seen = []
    monkeypatch.setattr(
        tools.subprocess, "run",
        _fake_run(FFPROBE_JSON_WITH_STREAM_GROUPS, seen=seen),
    )
    streams = tools.probe_streams("clip.mp4", "width,height", select="v:0")
    assert streams == [{"index": 0, "width": 1920, "height": 1080}]
    assert "-of" in seen[0] and "json" in seen[0]
    assert seen[0][seen[0].index("-select_streams") + 1] == "v:0"


def test_probe_streams_without_select(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "run", _fake_run('{"streams": []}'))
    assert tools.probe_streams("clip.mp4", "width,height") == []


@pytest.mark.parametrize(
    "stdout, returncode",
    [("", 0), ("not json at all", 0), (FFPROBE_JSON_WITH_STREAM_GROUPS, 1)],
)
def test_probe_streams_degrades_to_empty(monkeypatch, stdout, returncode):
    monkeypatch.setattr(tools.subprocess, "run", _fake_run(stdout, returncode))
    assert tools.probe_streams("clip.mp4", "width,height") == []


def test_probe_streams_survives_missing_ffprobe(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("ffprobe not found")

    monkeypatch.setattr(tools.subprocess, "run", boom)
    assert tools.probe_streams("clip.mp4", "width,height") == []
