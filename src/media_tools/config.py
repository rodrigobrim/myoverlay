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

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _local_tzinfo() -> tzinfo:
    from datetime import datetime

    tz = datetime.now().astimezone().tzinfo
    assert tz is not None
    return tz


def _expand(v):
    """Expand a leading ~ in configured paths (config.toml documents its path
    defaults in ~/... form; tomllib hands them over verbatim)."""
    if isinstance(v, str):
        return Path(v).expanduser()
    if isinstance(v, Path):
        return v.expanduser()
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


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
    # Where downloaded MyChron sessions live on disk. The first entry is
    # where `mt telemetry get` writes device downloads; every entry is
    # scanned on ingest.
    data_dirs: list[Path] = Field(
        default_factory=lambda: [Path.home() / "myoverlay" / "render" / "mychron"]
    )

    _expand_paths = field_validator("data_dirs", mode="before")(_expand)
    # Force one transport for device downloads: "usb" | "wifi";
    # null tries USB first (faster, charges the logger), then WiFi.
    transport: str | None = None
    # Zero-touch device download: with auto_download on, `mt run` / the
    # watcher pull new sessions off the MyChron whenever one is reachable
    # (WiFi downloads replace the house network while they run).
    auto_download: bool = False
    download_interval_s: float = 600.0
    # .xrz files are compressed twins of the .xrk; ingesting both would
    # duplicate every session.
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


class WatchConfig(BaseModel):
    poll_s: float = 30.0
    # Give the OS/camera a moment to finish mounting before ingesting.
    settle_s: float = 5.0


# Named output-resolution presets -> frame height. The source is scaled
# (up or down) to the chosen height; picking a preset is the explicit intent
# to output at that resolution.
RESOLUTIONS: dict[str, int] = {"hd": 720, "fhd": 1080, "2k": 1440, "4k": 2160}


class RenderConfig(BaseModel):
    # Re-validate on assignment so a CLI --resolution override is checked;
    # populate_by_name lets the hyphenated TOML alias and the Python name
    # both work (scan-video-for-race-end).
    model_config = ConfigDict(validate_assignment=True, populate_by_name=True)

    overlay_fps: float = 10.0
    # h264_nvenc/hevc_nvenc render 4K many times faster on NVIDIA GPUs;
    # crf maps to -cq for nvenc encoders.
    codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    # ffmpeg scaler for resizing the footage (mainly the 1080p->2K upscale):
    # lanczos is sharper than the default bilinear at a negligible cost.
    scale_flags: str = "lanczos"
    # Output resolution preset: hd (720p) | fhd (1080p) | 2k (1440p) | 4k (2160p).
    resolution: str = "2k"
    # Laps faster than this are physically impossible for the circuit
    # (cut track / timing glitch): flagged invalid, never best/delta ref.
    min_lap_s: float = 0.0
    max_rpm: int = 16000
    font_path: Path | None = None

    _expand_paths = field_validator("font_path", mode="before")(_expand)
    # Clips synced below this confidence are not rendered (use `mt sync
    # --clip ... --video-start ...` to pin them manually).
    min_sync_confidence: float = 0.5
    # When you forget to stop recording after the race, this listens to the
    # video's engine sound to find where the kart was switched off, then
    # trims the video to just after your last lap. Disable to skip the scan
    # and keep the full recording (renders a bit faster).
    scan_video_for_race_end: bool = Field(default=True, alias="scan-video-for-race-end")

    @field_validator("resolution")
    @classmethod
    def _valid_resolution(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in RESOLUTIONS:
            raise ValueError(
                f"resolution must be one of {list(RESOLUTIONS)} (got {v!r})"
            )
        return key

    def target_height(self) -> int:
        return RESOLUTIONS[self.resolution]


class YouTubeConfig(BaseModel):
    privacy: str = "private"
    # None -> language-appropriate default from i18n (per top-level `language`).
    title_template: str | None = None
    description_template: str | None = None
    playlist_id: str | None = None
    client_secret_file: Path = Path("client_secret.json")
    # Relative paths resolve against the process cwd: the launcher chdirs to
    # ~\myoverlay (installed) or the checkout root (dev) before the CLI runs.
    token_file: Path = Path("google-token")

    _expand_paths = field_validator("client_secret_file", "token_file", mode="before")(
        _expand
    )
    # Cloud project used by `mt google-setup`. Default "myoverlay": the setup
    # creates it (or reuses it if it already exists) under your Google account.
    project_id: str = "myoverlay"


class ToolsConfig(BaseModel):
    # Install directory of the frozen app: the pipeline locates the bundled
    # ffmpeg / Google Cloud SDK under it. The launcher rewrites it only when
    # the real location differs (custom install folder). Every candidate is
    # existence-checked at use, so on dev checkouts and zip deploys the
    # default quietly degrades to bare-name PATH resolution.
    install_dir: Path | None = Field(
        default_factory=lambda: Path("C:/Program Files/MyOverlay")
    )

    _expand_paths = field_validator("install_dir", mode="before")(_expand)


class Config(BaseModel):
    # Root folder where ingested/processed media lives (one subfolder per track
    # day). Defaults to ~/myoverlay/render when unset in config.toml - inside
    # the app data dir, next to config.toml and the repo clone.
    library_root: Path = Field(default_factory=lambda: Path.home() / "myoverlay" / "render")

    _expand_paths = field_validator("library_root", mode="before")(_expand)
    # Output language for the overlay labels and YouTube title/description
    # defaults. Everything else (config, CLI, logs) stays in English.
    language: str = "en"
    camera: CameraConfig = Field(default_factory=CameraConfig)
    mychron: MychronConfig = Field(default_factory=MychronConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @field_validator("language")
    @classmethod
    def _valid_language(cls, v: str) -> str:
        from .i18n import LANGUAGES

        key = v.strip().lower()
        if key not in LANGUAGES:
            raise ValueError(f"language must be one of {LANGUAGES} (got {v!r})")
        return key


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
            "No config.toml found. The app ships one with every option "
            "commented out (repo root); run the app once to create yours."
        )
    # utf-8-sig: Windows editors (and PowerShell) often write a UTF-8 BOM,
    # which tomllib rejects.
    data = tomllib.loads(file.read_bytes().decode("utf-8-sig"))
    return Config.model_validate(data)
