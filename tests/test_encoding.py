import subprocess

import pytest

import media_tools.encoding as enc


@pytest.fixture(autouse=True)
def clear_probe_cache():
    enc._probe_cache.clear()
    yield
    enc._probe_cache.clear()


def fake_run(returncode: int, calls: list | None = None):
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, b"", b"")

    return run


def test_nvenc_used_when_the_probe_encode_succeeds(monkeypatch):
    monkeypatch.setattr(enc.subprocess, "run", fake_run(0))
    assert enc.resolve_codec("h264_nvenc") == "h264_nvenc"
    assert enc.encoder_args("h264_nvenc", 20, "medium") == [
        "-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "20", "-b:v", "0",
    ]


def test_falls_back_to_cpu_when_nvenc_cannot_encode(monkeypatch):
    """ffmpeg may LIST h264_nvenc yet fail to open it (no NVIDIA GPU/driver),
    so the probe is a real encode and failure must fall back to libx264."""
    monkeypatch.setattr(enc.subprocess, "run", fake_run(1))
    assert enc.resolve_codec("h264_nvenc") == "libx264"
    assert enc.encoder_args("h264_nvenc", 22, "fast") == [
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
    ]


def test_falls_back_when_ffmpeg_is_missing_or_hangs(monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(enc.subprocess, "run", boom)
    assert enc.resolve_codec("hevc_nvenc") == "libx264"

    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    enc._probe_cache.clear()
    monkeypatch.setattr(enc.subprocess, "run", timeout)
    assert enc.resolve_codec("h264_nvenc") == "libx264"


def test_cpu_codecs_are_never_probed(monkeypatch):
    calls: list = []
    monkeypatch.setattr(enc.subprocess, "run", fake_run(0, calls))
    assert enc.resolve_codec("libx264") == "libx264"
    assert calls == []  # no probe needed for a software encoder


def test_probe_result_is_cached(monkeypatch):
    calls: list = []
    monkeypatch.setattr(enc.subprocess, "run", fake_run(0, calls))
    for _ in range(5):
        enc.resolve_codec("h264_nvenc")
    assert len(calls) == 1  # probed once, reused after


def test_probe_uses_a_real_test_encode(monkeypatch):
    calls: list = []
    monkeypatch.setattr(enc.subprocess, "run", fake_run(0, calls))
    enc.encoder_available("h264_nvenc")
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-c:v" in cmd and "h264_nvenc" in cmd
    assert "-f" in cmd and "null" in cmd  # encodes, discards output


@pytest.mark.skipif(
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
    reason="ffmpeg not available",
)
def test_real_ffmpeg_probe_returns_a_usable_codec():
    """Against the real ffmpeg on this machine, whatever comes back must be
    an encoder that actually works here."""
    codec = enc.resolve_codec("h264_nvenc")
    assert codec in ("h264_nvenc", "libx264")
    assert enc.encoder_available(codec)
