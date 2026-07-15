"""Configuration loading for media-tools.

Config is read from a TOML file. Search order: explicit --config path,
MEDIA_TOOLS_CONFIG env var, ./config.toml, <repo>/config.toml next to the
package. All times in manifests are stored as timezone-aware UTC; camera and
logger clocks are interpreted using the configured timezones.
"""

from __future__ import annotations

import os
import tomllib
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


def _local_tzinfo() -> tzinfo:
    from datetime import datetime

    tz = datetime.now().astimezone().tzinfo
    assert tz is not None
    return tz


class CameraConfig(BaseModel):
    # Extra directories to scan besides auto-detected removable DCIM volumes
    # (useful for testing and for cameras mounted at fixed paths).
    source_dirs: list[Path] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=lambda: [".mp4", ".mov"])
    # IANA timezone the camera clock is set to; null means system local time.
    timezone: str | None = None
    # How far a clip's timestamp may fall outside a telemetry session window
    # and still be assigned to it (camera clocks drift).
    clock_tolerance_s: float = 900.0

    def tzinfo(self) -> tzinfo:
        return ZoneInfo(self.timezone) if self.timezone else _local_tzinfo()


class MychronConfig(BaseModel):
    # Where Race Studio 3 stores downloaded sessions.
    rs3_data_dirs: list[Path] = Field(
        default_factory=lambda: [Path("C:/AIM_SPORT/RaceStudio3/user/data")]
    )
    # .xrz files are compressed twins of the .xrk RS3 writes alongside;
    # ingesting both would duplicate every session.
    extensions: list[str] = Field(default_factory=lambda: [".xrk"])
    # Timezone of the MyChron 'Log Date'/'Log Time' metadata; null = system local.
    timezone: str | None = None
    # Correct a wrongly-set device clock: a moment the device recorded as
    # clock_reads actually happened at clock_actual (both in the logger's
    # timezone). The difference is applied to every parsed session time.
    clock_reads: datetime | None = None
    clock_actual: datetime | None = None

    def tzinfo(self) -> tzinfo:
        return ZoneInfo(self.timezone) if self.timezone else _local_tzinfo()

    def clock_offset(self) -> timedelta:
        if self.clock_reads and self.clock_actual:
            return self.clock_actual - self.clock_reads
        return timedelta(0)


class Rs3Config(BaseModel):
    """Best-effort GUI automation of Race Studio 3 (no CLI exists).

    When enabled, the watcher periodically brings RS3 up and clicks its
    download control so new MyChron sessions land in rs3_data_dirs without a
    human click. Brittle by nature (breaks if AiM redesigns the UI); the
    folder watcher keeps working either way.
    """

    enabled: bool = False
    exe_path: Path | None = None  # e.g. C:/AIM_SPORT/RaceStudio3/RaceStudio3.exe
    # Real RS3 window title is e.g. "RaceStudio3 (64 bit) 3.83.11" (no space).
    window_title_re: str = ".*Race\\s*Studio.*"
    # RS3 3.83 names its download view button "Data Download".
    download_button_names: list[str] = Field(
        default_factory=lambda: ["Data Download", "Download Data", "Download"]
    )
    trigger_interval_s: float = 600.0


class WatchConfig(BaseModel):
    poll_s: float = 30.0
    # Give the OS/camera a moment to finish mounting before ingesting.
    settle_s: float = 5.0


class RenderConfig(BaseModel):
    overlay_fps: float = 10.0
    # h264_nvenc/hevc_nvenc render 4K many times faster on NVIDIA GPUs;
    # crf maps to -cq for nvenc encoders.
    codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    # Downscale output to this height (e.g. 1440 for 2K, 1080 for full HD);
    # null keeps the source resolution. Never upscales.
    output_height: int | None = None
    # Laps faster than this are physically impossible for the circuit
    # (cut track / timing glitch): flagged invalid, never best/delta ref.
    min_lap_s: float = 0.0
    max_rpm: int = 16000
    font_path: Path | None = None
    # Clips synced below this confidence are not rendered (use `mt sync
    # --clip ... --video-start ...` to pin them manually).
    min_sync_confidence: float = 0.5


class YouTubeConfig(BaseModel):
    privacy: str = "private"
    title_template: str = "Karting {track} {date} - session {session} (best lap {best_lap})"
    description_template: str = "Recorded {date} at {track}.\nBest lap: {best_lap}\n\nUploaded by media-tools."
    playlist_id: str | None = None
    client_secret_file: Path = Path("client_secret.json")
    token_file: Path = Path("token.json")


class Config(BaseModel):
    library_root: Path
    camera: CameraConfig = Field(default_factory=CameraConfig)
    mychron: MychronConfig = Field(default_factory=MychronConfig)
    rs3: Rs3Config = Field(default_factory=Rs3Config)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)


def find_config_file(explicit: Path | None = None) -> Path | None:
    if explicit:
        return explicit
    env = os.environ.get("MEDIA_TOOLS_CONFIG")
    if env:
        return Path(env)
    for candidate in (Path.cwd() / "config.toml", Path(__file__).parents[2] / "config.toml"):
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None) -> Config:
    file = find_config_file(path)
    if file is None:
        raise FileNotFoundError(
            "No config.toml found. Copy config.example.toml to config.toml and edit paths."
        )
    # utf-8-sig: Windows editors (and PowerShell) often write a UTF-8 BOM,
    # which tomllib rejects.
    data = tomllib.loads(file.read_bytes().decode("utf-8-sig"))
    return Config.model_validate(data)
