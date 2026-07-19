"""Video encoder selection: hardware NVENC when actually usable, CPU fallback.

`ffmpeg -encoders` listing h264_nvenc does NOT mean it works - without an
NVIDIA GPU/driver the encoder fails at open time. The reliable check is a
real one-frame test encode, cached per process.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("media_tools")

_probe_cache: dict[str, bool] = {}


def encoder_available(codec: str) -> bool:
    """True when ffmpeg can actually encode with `codec` on this machine."""
    if codec not in _probe_cache:
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-v", "error",
                    # 320x240: NVENC rejects frames below its minimum dimensions
                    # (h264 ~145px wide). A tiny probe frame would fail to open
                    # the encoder and falsely report nvenc unusable on machines
                    # where it works fine at real resolutions.
                    "-f", "lavfi", "-i", "color=black:size=320x240:rate=30",
                    "-frames:v", "1",
                    "-c:v", codec,
                    "-f", "null", "-",
                ],
                capture_output=True,
                timeout=60,
            )
            _probe_cache[codec] = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _probe_cache[codec] = False
    return _probe_cache[codec]


def resolve_codec(codec: str) -> str:
    """The codec to use: hardware encoders fall back to CPU libx264 when the
    machine can't run them (no NVIDIA GPU, missing driver)."""
    if not codec.endswith("_nvenc"):
        return codec
    if encoder_available(codec):
        return codec
    logger.warning(
        "%s is not usable on this machine (no NVIDIA GPU/driver?) - "
        "falling back to CPU libx264",
        codec,
    )
    return "libx264"


def encoder_args(codec: str, crf: int, preset: str) -> list[str]:
    """ffmpeg video-encoder arguments, after hardware fallback resolution."""
    codec = resolve_codec(codec)
    if codec.endswith("_nvenc"):
        return ["-c:v", codec, "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    return ["-c:v", codec, "-crf", str(crf), "-preset", preset]
