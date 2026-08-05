"""media-tools CLI (`mt`)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import Config, load_config
from .library import Library

app = typer.Typer(help="Zero-touch karting video + telemetry pipeline.", no_args_is_help=True)
video_app = typer.Typer(help="Camera video utilities (list / selective download).", no_args_is_help=True)
telemetry_app = typer.Typer(help="MyChron telemetry utilities (list / download).", no_args_is_help=True)
app.add_typer(video_app, name="video")
app.add_typer(telemetry_app, name="telemetry")
console = Console()

_config_path: Path | None = None


@app.callback()
def main(
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Path to config.toml")
    ] = None,
    verbosity: Annotated[
        str,
        typer.Option(
            "--verbosity",
            help="Log level: quiet | info | debug. `debug` also surfaces "
            "low-level telemetry-decoder chatter (e.g. libxrk 'Unknown units').",
        ),
    ] = "info",
):
    global _config_path
    _config_path = config

    import logging

    levels = {"quiet": logging.WARNING, "info": logging.INFO, "debug": logging.DEBUG}
    level = levels.get(verbosity.lower())
    if level is None:
        console.print(
            f"[red]--verbosity must be one of {', '.join(levels)}[/red]"
        )
        raise typer.Exit(2)
    logging.getLogger("media_tools").setLevel(level)


def get_config() -> Config:
    try:
        return load_config(_config_path)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)


def _print_ingest_report(name: str, report) -> None:
    console.print(f"[bold]{name}[/bold]: scanned {len(report.sources_scanned)} source(s)")
    for line in report.copied:
        console.print(f"  [green]+[/green] {line}")
    if report.skipped_known:
        console.print(f"  [dim]{report.skipped_known} file(s) already ingested[/dim]")
    if not report.copied and not report.skipped_known:
        console.print("  [dim]nothing new[/dim]")
    for err in report.errors:
        console.print(f"  [red]! {err}[/red]")


@app.command()
def ingest(
    source: Annotated[
        str, typer.Option(help="Which sources to ingest: all, camera, mychron")
    ] = "all",
    device: Annotated[
        bool,
        typer.Option("--device", help="First download new sessions off the MyChron (USB or WiFi)"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-download and refresh files already ingested"),
    ] = False,
):
    """Copy new camera videos and MyChron sessions into the library (download only)."""
    cfg = get_config()
    if device:
        _device_download(cfg, names=None, force=False)
    if source in ("all", "camera"):
        from .ingest.camera import ingest_camera

        _print_ingest_report("camera", ingest_camera(cfg, force=force))
    if source in ("all", "mychron"):
        from .ingest.mychron import ingest_mychron

        _print_ingest_report("mychron", ingest_mychron(cfg, force=force))


@app.command()
def scan(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List NEW camera + telemetry content (read-only), correlated by video.

    Nothing is copied or written - this is the pre-download review gate.
    Telemetry with no matching video is flagged 'orphan': it is still committed
    on ingest, it just yields no render item."""
    from .scan import scan_new

    cfg = get_config()
    result = scan_new(cfg)
    if json_out:
        typer.echo(result.model_dump_json())
        return

    from rich.tree import Tree

    if not result.video_groups and not result.orphan_telemetry:
        console.print("[dim]nothing new to ingest[/dim]")
        return
    tree = Tree(f"[bold]new content[/bold] ({result.date_guess or '?'})")
    for g in result.video_groups:
        dur = f"{g.video.duration_s:.0f}s" if g.video.duration_s else "?"
        node = tree.add(f"[cyan]{g.video.source_name}[/cyan] ({dur})")
        if not g.telemetry:
            node.add("[yellow]no telemetry[/yellow]")
        for t in g.telemetry:
            node.add(f"{t.source_name} - {t.lap_count} laps, best {t.best_lap}")
    if result.orphan_telemetry:
        orphan = tree.add("[yellow]orphan telemetry[/yellow] (committed on ingest, no video)")
        for t in result.orphan_telemetry:
            orphan.add(f"{t.source_name} - {t.lap_count} laps, best {t.best_lap}")
    console.print(tree)


def _fmt_size(n: int) -> str:
    mb = n / 1_000_000
    return f"{mb / 1000:.2f} GB" if mb >= 1000 else f"{mb:.1f} MB"


def _fmt_duration(s: float | None) -> str:
    if s is None:
        return "-"
    total = int(s)
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


video_list_app = typer.Typer(
    help="List videos - remote (on the camera card, default), local (in the "
    "library), or all (both, including already downloaded).",
    invoke_without_command=True,
)
video_app.add_typer(video_list_app, name="list")


@video_list_app.callback()
def video_list_default(
    ctx: typer.Context,
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        _video_list_remote(include_ingested=False, json_out=json_out)


@video_list_app.command("remote")
def video_list_remote(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List new videos on the connected camera. Copies nothing."""
    _video_list_remote(include_ingested=False, json_out=json_out)


@video_list_app.command("local")
def video_list_local(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List videos already downloaded into the library."""
    _video_list_local(json_out=json_out)


@video_list_app.command("all")
def video_list_all(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List everything: camera-card videos (including already-downloaded) and
    the library's downloaded videos."""
    if json_out:
        from .scan import list_camera_videos, list_library_videos

        cfg = get_config()
        remote = list_camera_videos(cfg, include_ingested=True)
        local = list_library_videos(cfg)
        typer.echo(
            f'{{"remote":{remote.model_dump_json()},"local":{local.model_dump_json()}}}'
        )
        return
    _video_list_remote(include_ingested=True, json_out=False)
    _video_list_local(json_out=False)


def _video_list_remote(include_ingested: bool, json_out: bool) -> None:
    from .scan import list_camera_videos

    cfg = get_config()
    result = list_camera_videos(cfg, include_ingested=include_ingested)
    if json_out:
        typer.echo(result.model_dump_json())
        return

    if not result.sources:
        console.print("[dim]no camera volume found[/dim]")
        return
    if not result.videos:
        console.print(
            "[dim]no videos on camera[/dim]" if include_ingested else "[dim]no new videos[/dim]"
        )
        return

    camera_tz = cfg.camera.tzinfo()
    table = Table(title="camera videos")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("captured (local)")
    table.add_column("status")
    for v in result.videos:
        status = "[green]new[/green]" if v.status == "new" else "[dim]ingested[/dim]"
        table.add_row(
            v.source_name,
            _fmt_size(v.size_bytes),
            v.start_utc.astimezone(camera_tz).strftime("%Y-%m-%d %H:%M:%S"),
            status,
        )
    console.print(table)


def _video_list_local(json_out: bool) -> None:
    from .scan import list_library_videos

    cfg = get_config()
    result = list_library_videos(cfg)
    if json_out:
        typer.echo(result.model_dump_json())
        return

    if not result.videos:
        console.print("[dim]no videos in the library[/dim]")
        return

    camera_tz = cfg.camera.tzinfo()
    table = Table(title="library videos (local)")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("captured (local)")
    table.add_column("day")
    for v in result.videos:
        table.add_row(
            v.source_name,
            _fmt_size(v.size_bytes),
            v.start_utc.astimezone(camera_tz).strftime("%Y-%m-%d %H:%M:%S"),
            v.day,
        )
    console.print(table)


@video_app.command("get")
def video_get(
    names: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="Video filenames from `mt video list`, or days (YYYY-MM-DD) to "
            "download every file captured that day (omit to download all new)"
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-download and refresh files already ingested")
    ] = False,
):
    """Download videos from the camera into the library - all new, the named
    files, or every file of the named days."""
    from .ingest.camera import ingest_camera

    cfg = get_config()
    only_names, missing_days = _expand_video_days(cfg, names)
    if missing_days:
        console.print(f"[red]no videos on the camera for: {', '.join(missing_days)}[/red]")
        raise typer.Exit(1)
    report = ingest_camera(cfg, only_names=only_names, force=force)
    _print_ingest_report("camera", report)
    if report.requested_missing:
        console.print(f"[red]not found on camera: {', '.join(report.requested_missing)}[/red]")
        raise typer.Exit(1)


def _expand_video_days(cfg: Config, names: list[str] | None):
    """Expand day-shaped args (YYYY-MM-DD) into that day's camera filenames.

    Returns (only_names_or_None, days_that_matched_nothing). Non-day args pass
    through untouched; no args at all means "all new" (None).
    """
    if not names:
        return None, []
    from .scan import list_camera_videos

    expanded: list[str] = []
    days: list[date] = []
    for n in names:
        try:
            days.append(date.fromisoformat(n))
        except ValueError:
            expanded.append(n)
    missing: list[str] = []
    if days:
        camera_tz = cfg.camera.tzinfo()
        on_card = list_camera_videos(cfg, include_ingested=True).videos
        for d in days:
            hits = [
                v.source_name for v in on_card if v.start_utc.astimezone(camera_tz).date() == d
            ]
            if hits:
                expanded.extend(hits)
            else:
                missing.append(d.isoformat())
    return expanded, missing


telemetry_list_app = typer.Typer(
    help="List MyChron sessions - remote (on the device, over USB/WiFi, default), "
    "local (downloaded to disk), or all (both, including already handled).",
    invoke_without_command=True,
)
telemetry_app.add_typer(telemetry_list_app, name="list")


@telemetry_list_app.callback()
def telemetry_list_default(
    ctx: typer.Context,
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        _telemetry_list_remote(include_downloaded=False, json_out=json_out)


@telemetry_list_app.command("remote")
def telemetry_list_remote(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List the sessions recorded on the MyChron (connects over USB or WiFi). Downloads nothing."""
    _telemetry_list_remote(include_downloaded=False, json_out=json_out)


@telemetry_list_app.command("local")
def telemetry_list_local(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List new MyChron .xrk sessions already downloaded to disk. Copies nothing."""
    _telemetry_list_local(include_ingested=False, json_out=json_out)


@telemetry_list_app.command("all")
def telemetry_list_all(
    json_out: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON (for the review GUI)")
    ] = False,
):
    """List everything: device sessions (including already-downloaded) and
    local files (including already-ingested)."""
    if json_out:
        from .ingest.aim import list_remote_sessions
        from .scan import list_telemetry_files

        cfg = get_config()
        remote = list_remote_sessions(cfg, include_downloaded=True)
        local = list_telemetry_files(cfg, include_ingested=True)
        typer.echo(
            f'{{"remote":{remote.model_dump_json()},"local":{local.model_dump_json()}}}'
        )
        return
    _telemetry_list_remote(include_downloaded=True, json_out=False)
    _telemetry_list_local(include_ingested=True, json_out=False)


def _fmt_lap_ms(text: str | None) -> str:
    """Device lap time (milliseconds) -> '52.509' / '1:02.345'."""
    try:
        ms = int(text or "")
    except ValueError:
        return text or "?"
    if ms <= 0:
        return "?"
    minutes, seconds = divmod(ms / 1000.0, 60)
    return f"{int(minutes)}:{seconds:06.3f}" if minutes else f"{seconds:.3f}"


def _fmt_device_dt(meta: dict) -> str:
    """The catalog's 'date'/'hour' -> '2026-07-30 22:26:00'.

    The MyChron writes day-first dates ('30/07/2026'); anything unparseable
    is shown verbatim rather than guessed at.
    """
    from datetime import datetime

    text = f"{meta.get('date', '')} {meta.get('hour', '')}".strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return text or "?"


def _telemetry_list_remote(include_downloaded: bool, json_out: bool) -> None:
    from .ingest.aim import list_remote_sessions

    cfg = get_config()
    result = list_remote_sessions(cfg, include_downloaded=include_downloaded)
    if json_out:
        typer.echo(result.model_dump_json())
        return

    for note in result.notes:
        style = "red" if note.startswith("!") else "dim"
        console.print(note, style=style, markup=False)
    failed = any(n.startswith("!") for n in result.notes)
    if not result.sessions:
        # Only claim an empty device when the listing actually succeeded.
        if result.transport and not failed:
            console.print("[dim]no new sessions on the device[/dim]")
        return

    title = f"MyChron sessions ({result.transport})" if result.transport else "MyChron sessions"
    table = Table(title=title)
    table.add_column("session")
    table.add_column("size", justify="right")
    table.add_column("captured")
    table.add_column("laps", justify="right")
    table.add_column("best", justify="right")
    if include_downloaded:
        table.add_column("status")
    for s in result.sessions:
        row = [
            s.name,
            _fmt_size(s.size_bytes) if s.size_bytes is not None else "?",
            _fmt_device_dt(s.meta),
            s.meta.get("nlap") or "?",
            _fmt_lap_ms(s.meta.get("best")),
        ]
        if include_downloaded:
            row.append("[dim]downloaded[/dim]" if s.downloaded else "[green]new[/green]")
        table.add_row(*row)
    console.print(table)


def _telemetry_list_local(include_ingested: bool, json_out: bool) -> None:
    from .scan import list_telemetry_files

    cfg = get_config()
    result = list_telemetry_files(cfg, include_ingested=include_ingested)
    if json_out:
        typer.echo(result.model_dump_json())
        return

    if not result.files:
        console.print(
            "[dim]no sessions found[/dim]" if include_ingested else "[dim]no new sessions[/dim]"
        )
        return

    mychron_tz = cfg.mychron.tzinfo()
    table = Table(title="mychron sessions (local)")
    table.add_column("name")
    table.add_column("size", justify="right")
    table.add_column("captured (local)")
    table.add_column("status")
    for f in result.files:
        status = "[green]new[/green]" if f.status == "new" else "[dim]ingested[/dim]"
        captured = (
            f.start_utc.astimezone(mychron_tz).strftime("%Y-%m-%d %H:%M:%S") if f.start_utc else "?"
        )
        table.add_row(f.source_name, _fmt_size(f.size_bytes), captured, status)
    console.print(table)


def _device_download(cfg, names: Optional[list[str]], force: bool):
    """Pull sessions off the MyChron, streaming per-file progress lines."""
    import sys

    from .ingest.aim import download_sessions

    console.print("[bold]mychron[/bold] (device):")

    def progress(name: str, done: int, total: int) -> None:
        # In-flight progress only where a human is watching; a scheduled run
        # must not fill the log with carriage returns.
        if sys.stderr.isatty():
            sys.stderr.write(f"\r  {name}  {done:>10,} / {total:,}")
            sys.stderr.flush()
            if done >= total:
                sys.stderr.write("\n")

    report = download_sessions(
        cfg,
        names=names,
        force=force,
        echo=lambda line: console.print(f"  {line}", markup=False),
        progress=progress,
    )
    if report.skipped_existing:
        console.print(f"  [dim]{report.skipped_existing} session(s) already downloaded[/dim]")
    for n in report.missing:
        console.print(f"  [red]not on the device: {n}[/red]")
    for e in report.errors:
        console.print(f"  [red]! {e}[/red]", markup=False)
    return report


@telemetry_app.command("get")
def telemetry_get(
    names: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="Session names from `mt telemetry list` (omit to download all new sessions)"
        ),
    ] = None,
    download: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            help="Pull sessions off the MyChron (USB or WiFi) before ingesting; "
            "--no-download only ingests files already on disk",
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-download and re-ingest sessions already handled"),
    ] = False,
):
    """Download MyChron sessions off the device and ingest them. No camera involvement."""
    from pathlib import Path as P

    cfg = get_config()
    only_names = names or None
    failed = False
    if download:
        report = _device_download(cfg, names=names or None, force=force)
        failed = bool(report.errors or report.missing)
        if names:
            # Device names (a_0186.xrz) become local .xrk files on download;
            # ingest filters by the on-disk names.
            only_names = [P(p).name for p in report.downloaded] or None
            if only_names is None:
                # Nothing newly downloaded for the requested names (already on
                # disk, or all failed): fall back to the local-name filter so
                # `telemetry get <name>` still re-ingests with --force. A
                # device name (.xrz) maps to the .xrk its download produced.
                only_names = [
                    P(n).stem + ".xrk" if P(n).suffix.lower() == ".xrz" else n for n in names
                ]

    from .ingest.mychron import ingest_mychron

    report = ingest_mychron(cfg, only_names=only_names, force=force)
    _print_ingest_report("mychron", report)
    if report.requested_missing:
        console.print(f"[red]not found: {', '.join(report.requested_missing)}[/red]")
        raise typer.Exit(1)
    if failed:
        raise typer.Exit(1)


@app.command()
def plan(
    day: Annotated[str, typer.Argument(help="Day (YYYY-MM-DD)")],
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable JSON (for the GUI)")] = False,
    emit: Annotated[
        Optional[Path],
        typer.Option("--emit", help="Write the plan to this file (default work/render_plan.json)"),
    ] = None,
):
    """Build the render-plan queue (review Gate 2) - one item per synced video."""
    from .reviewplan import build_plan, save_plan

    cfg = get_config()
    lib = Library(cfg.library_root)
    d = date.fromisoformat(day)
    manifest = lib.load_day(d)
    day_dir = lib.day_dir(d)
    plan_obj = build_plan(cfg, day_dir, manifest)
    if json_out:
        typer.echo(plan_obj.model_dump_json())
        return
    if emit is not None:
        emit.write_text(plan_obj.model_dump_json(indent=2), encoding="utf-8")
        dest = emit
    else:
        dest = save_plan(day_dir, plan_obj)
    console.print(f"[bold]{d}[/bold]: {len(plan_obj.items)} item(s) -> {dest}")
    for it in plan_obj.items:
        console.print(f"  {it.item_id}  [dim]{it.quality}, best {it.best_lap}[/dim]")


@app.command(name="best-lap")
def best_lap_cmd(
    day: Annotated[str, typer.Argument(help="Day (YYYY-MM-DD)")],
    session: Annotated[Optional[int], typer.Option("--session", help="Session id; default all")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
):
    """Best lap (the single source of truth) per session, S/F-relap-aware."""
    import json as _json

    from .overlay import fmt_laptime
    from .telemetry import best_lap, session_laps_derived

    cfg = get_config()
    lib = Library(cfg.library_root)
    d = date.fromisoformat(day)
    manifest = lib.load_day(d)
    day_dir = lib.day_dir(d)
    out: dict[int, str] = {}
    for s in manifest.sessions:
        if session is not None and s.id != session:
            continue
        bl = best_lap(session_laps_derived(day_dir, manifest, s), cfg.render.min_lap_s)
        out[s.id] = fmt_laptime((bl[2] - bl[1]) if bl else None)
    if json_out:
        typer.echo(_json.dumps(out))
        return
    for sid, bl in out.items():
        console.print(f"session {sid}: best lap {bl}")


@app.command()
def gui():
    """Launch the desktop review GUI (Gate 1 content review + Gate 2 render plan)."""
    try:
        from .gui import main
    except ImportError as exc:
        console.print(f"[red]GUI unavailable: {exc}[/red]")
        console.print(
            "[dim]The frozen exe must be built with tkinter "
            "(remove it from excludes in packaging/myoverlay.spec and rebuild).[/dim]"
        )
        raise typer.Exit(1)
    main()


@app.command(name="google-setup")
def google_setup(
    troubleshoot: Annotated[
        bool,
        typer.Option(
            "--troubleshoot",
            help="Snapshot every Console step into <library>/gcp_troubleshoot/ "
            "to understand/refine the procedure when Google shifts the UI",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Reconfigure even when the account is already set up "
            "(mints a new OAuth client; the old secret is kept as .bak)",
        ),
    ] = False,
):
    """Configure the Google side of `mt publish` by driving the Cloud Console
    (consent screen + Desktop OAuth client + client_secret.json download).
    Sign-in is the one manual step: do it in the window that opens.

    Already configured? It says so and exits without opening a browser."""
    from .gcp_console import setup_google_api

    cfg = get_config()
    console.print("[bold]google-setup[/bold]:")
    # Streamed line by line to a fixed path (not config-relative): the MSI
    # wizard tails it for live status while this runs in a console that dies
    # with the process, and reads it back afterwards to detect failure.
    log = Path.home() / "MyOverlay" / "google-setup.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        # When running via the exe, the launcher already started this run's
        # log (MYOVERLAY_SETUP_LOG_ACTIVE); append to it instead of wiping
        # its lines from the wizard's live view.
        if not os.environ.get("MYOVERLAY_SETUP_LOG_ACTIVE"):
            log.write_text("", encoding="utf-8")
    except OSError:
        log = None
    report = setup_google_api(
        cfg, troubleshoot=troubleshoot, log_path=log, force=force
    )
    for line in report:
        console.print(f"  {line}", markup=False)
    if log is not None:
        console.print(f"  (full report saved to {log})", markup=False)
    # "!" lines mark failures; the nonzero exit (and the log) let the wizard
    # surface them instead of assuming success.
    if any(line.lstrip().startswith("!") for line in report):
        raise typer.Exit(1)


@app.command(name="google-auth")
def google_auth() -> None:
    """Authorize YouTube uploads: opens Google's consent screen, then saves the
    refresh token. Uploads nothing - `google-setup` only creates the OAuth
    client, and until this runs the first upload is what triggers consent."""
    from .publish import get_credentials

    cfg = get_config()
    console.print("[bold]google-auth[/bold]:")
    if not cfg.youtube.client_secret_file.is_file():
        console.print(
            f"  ! no OAuth client secret at {cfg.youtube.client_secret_file}"
            " - run `mt google-setup` first",
            markup=False,
        )
        raise typer.Exit(1)
    # Say up front whether consent is actually needed, so a silent no-op is
    # never mistaken for a completed authorization.
    from .publish import _token_client_mismatch

    reused = False
    if cfg.youtube.token_file.is_file():
        from google.oauth2.credentials import Credentials

        from .publish import SCOPES

        try:
            existing = Credentials.from_authorized_user_file(
                str(cfg.youtube.token_file), SCOPES
            )
        except Exception:  # noqa: BLE001 - unreadable token: just re-authorize
            existing = None
        if existing is not None and _token_client_mismatch(cfg, existing):
            console.print("  existing token belongs to a different OAuth client - re-authorizing")
        elif existing is not None:
            reused = True
            console.print("  existing token is still valid - reusing it")
    if not reused:
        console.print("  a browser window will open - click Allow to authorize")
    try:
        creds = get_credentials(cfg)
    except Exception as exc:  # noqa: BLE001 - report, never traceback at the CLI
        console.print(f"  ! authorization failed: {exc}", markup=False)
        raise typer.Exit(1) from exc
    console.print(
        f"  + {'token still valid' if reused else 'authorized'}"
        f" -> token saved to {cfg.youtube.token_file}",
        markup=False,
    )
    if not getattr(creds, "refresh_token", None):
        # Without a refresh token the watcher cannot upload unattended.
        console.print("  ? no refresh token returned - re-run after revoking the old grant")
    console.print("  next: `mt publish` uploads your renders")


@app.command(name="join")
def join_cmd(
    day: Annotated[str, typer.Argument(help="Day (YYYY-MM-DD)")],
    clips: Annotated[
        Optional[str],
        typer.Option(
            "--videos",
            "--clips",
            help="Comma-separated source-name substrings to join as ONE session "
            "(e.g. 0065,0066). Omit to auto-detect all split runs.",
        ),
    ] = None,
    gap_s: Annotated[
        float,
        typer.Option("--gap-s", help="Max seconds between segments to treat as one recording"),
    ] = 8.0,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be joined")] = False,
):
    """Join camera-split video segments (GoPro/DJI ~4 GB rollovers) into one video."""
    from .videojoin import join_day

    cfg = get_config()
    lib = Library(cfg.library_root)
    d = date.fromisoformat(day)
    manifest = lib.load_day(d)
    day_dir = lib.day_dir(d)
    subs = [s.strip() for s in clips.split(",") if s.strip()] if clips else None
    lines = join_day(
        manifest, day_dir, only_substrings=subs, gap_tolerance_s=gap_s, dry_run=dry_run
    )
    if not dry_run:
        lib.save_day(manifest)
    console.print(f"[bold]{d}[/bold]:")
    for line in lines:
        console.print(f"  {line}", markup=False)


@app.command()
def correlate(
    day: Annotated[Optional[str], typer.Argument(help="Day (YYYY-MM-DD); default all")] = None,
):
    """Group ingested files into track sessions and assign videos to them."""
    from .correlate import correlate_day

    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()
    for d in days:
        manifest = lib.load_day(d)
        report = correlate_day(manifest, cfg.camera.clock_tolerance_s)
        lib.save_day(manifest)
        console.print(
            f"[bold]{d}[/bold]: {report.sessions} session(s), "
            f"{report.assigned_videos} video(s) assigned"
        )
        for f in report.unassigned_videos:
            console.print(f"  [yellow]unassigned:[/yellow] {f}")
        for f in report.ambiguous_videos:
            console.print(f"  [yellow]ambiguous (best-overlap used):[/yellow] {f}")
    if not days:
        console.print("[dim]library is empty[/dim]")


@app.command()
def sync(
    day: Annotated[Optional[str], typer.Argument(help="Day (YYYY-MM-DD); default all")] = None,
    clip: Annotated[
        Optional[str],
        typer.Option("--video", "--clip", help="Manual mode: video source name"),
    ] = None,
    video_start: Annotated[
        Optional[str],
        typer.Option(help="Manual mode: exact UTC start of the video (ISO 8601)"),
    ] = None,
    lap: Annotated[
        Optional[int],
        typer.Option(help="Manual mode: telemetry lap number you start at video time --at"),
    ] = None,
    at: Annotated[
        Optional[str],
        typer.Option("--at", help="Manual mode: video time (MM:SS) of the --lap start/finish crossing"),
    ] = None,
    force: Annotated[bool, typer.Option(help="Re-sync videos that already have a sync")] = False,
):
    """Align videos with telemetry (auto, or manual --video with --video-start or --lap/--at)."""
    from datetime import datetime as dt, timedelta

    from .library import SyncInfo
    from .sync import sync_day

    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()

    if clip or video_start or lap is not None:
        if not (clip and day and (video_start or lap is not None)):
            console.print(
                "[red]manual mode needs DAY, --video and either --video-start or --lap/--at[/red]"
            )
            raise typer.Exit(2)
        manifest = lib.load_day(date.fromisoformat(day))
        matches = [v for v in manifest.videos if v.source_name == clip]
        if not matches:
            console.print(f"[red]no video named {clip} on {day}[/red]")
            raise typer.Exit(2)
        target = matches[0]

        if lap is not None:
            # Lap anchor: telemetry lap N of the clip's session starts at
            # video time --at, so video_start = lap_N_start_utc - at.
            from .slice import parse_timestamp

            if at is None:
                console.print("[red]--lap needs --at MM:SS[/red]")
                raise typer.Exit(2)
            lap_utc = None
            for t in manifest.telemetry:
                if t.session_id != target.session_id:
                    continue
                for lp in t.laps:
                    if lp.num == lap:
                        lap_utc = t.start_utc + timedelta(seconds=lp.start_s)
                        break
            if lap_utc is None:
                console.print(
                    f"[red]no telemetry lap {lap} in {clip}'s session "
                    f"(id {target.session_id}); run 'mt correlate {day}' first[/red]"
                )
                raise typer.Exit(2)
            vs = lap_utc - timedelta(seconds=parse_timestamp(at))
        else:
            vs = dt.fromisoformat(video_start)

        target.sync = SyncInfo(video_start_utc=vs, confidence=1.0, method="manual")
        lib.save_day(manifest)
        console.print(f"[green]pinned {clip} -> {vs.isoformat()} (manual)[/green]")
        return

    for d in days:
        manifest = lib.load_day(d)
        lines = sync_day(cfg, manifest, lib.day_dir(d), force=force)
        lib.save_day(manifest)
        console.print(f"[bold]{d}[/bold]:")
        for line in lines or ["  nothing to sync"]:
            console.print(f"  {line}")


@app.command()
def render(
    day: Annotated[Optional[str], typer.Argument(help="Day (YYYY-MM-DD); default all")] = None,
    force: Annotated[bool, typer.Option(help="Re-render already rendered videos")] = False,
    resolution: Annotated[
        Optional[str],
        typer.Option("--resolution", "--res", help="Output resolution: hd|fhd|2k|4k (default from config)"),
    ] = None,
    scan_race_end: Annotated[
        Optional[bool],
        typer.Option(
            "--scan-race-end/--no-scan-race-end",
            help="Trim the video after the engine shutdown (default from config)",
        ),
    ] = None,
    clip: Annotated[
        Optional[str],
        typer.Option(
            "--video",
            "--clip",
            help="Only render videos whose name contains this substring (re-renders them)",
        ),
    ] = None,
    sample_from: Annotated[
        Optional[str],
        typer.Option(
            "--from",
            help="Render only from this video time (MM:SS or seconds) - a short sample "
            "to validate the trim without rendering the whole video",
        ),
    ] = None,
    sample_to: Annotated[
        Optional[str],
        typer.Option(
            "--to",
            help="With --from, render only up to this video time (else up to the race-end cut)",
        ),
    ] = None,
    codec: Annotated[
        Optional[str],
        typer.Option("--codec", help="Override the video codec (e.g. libx264, h264_nvenc)"),
    ] = None,
    crf: Annotated[
        Optional[int],
        typer.Option("--crf", help="Override quality (lower = higher quality; libx264 CRF / nvenc CQ)"),
    ] = None,
    plan_file: Annotated[
        Optional[Path],
        typer.Option("--plan", help="Execute an edited render plan (work/render_plan.json)"),
    ] = None,
):
    """Render telemetry overlays onto synced videos (or an edited --plan)."""
    from .render import RenderProgress, render_day
    from .slice import parse_timestamp

    cfg = get_config()
    if plan_file is not None:
        from .reviewplan import execute_item, load_plan
        from .telemetry import load_day_frame

        if not day:
            console.print("[red]--plan requires a DAY[/red]")
            raise typer.Exit(2)
        lib = Library(cfg.library_root)
        d = date.fromisoformat(day)
        manifest = lib.load_day(d)
        day_dir = lib.day_dir(d)
        plan_obj = load_plan(plan_file)
        dayframe = load_day_frame(day_dir, manifest)
        console.print(f"[bold]{d}[/bold] (plan {plan_file.name}):")
        for item in plan_obj.items:
            line = execute_item(
                cfg, manifest, day_dir, dayframe, item,
                save=lambda m=manifest: lib.save_day(m),
            )
            console.print(f"  {line}")
        return
    window_start_s = parse_timestamp(sample_from) if sample_from else 0.0
    window_end_s = parse_timestamp(sample_to) if sample_to else 0.0
    if scan_race_end is not None:
        cfg.render.scan_video_for_race_end = scan_race_end
    if codec:
        cfg.render.codec = codec
    if crf is not None:
        cfg.render.crf = crf
    if resolution:
        try:
            cfg.render.resolution = resolution
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
        console.print(f"[dim]output resolution: {cfg.render.resolution} ({cfg.render.target_height()}p)[/dim]")
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()

    import contextlib

    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeRemainingColumn,
    )

    class _RichRenderProgress(RenderProgress):
        """Two-level live bar: an overall clips-of-day task plus a per-clip
        frames task that is created when a clip starts and removed when it
        finishes (only the encoding clip shows a frame bar)."""

        def __init__(self, prog: Progress, day_label: str):
            self._prog = prog
            self._label = day_label
            self._overall = None
            self._clip = None

        def start_day(self, total_clips):
            self._overall = self._prog.add_task(
                f"[bold]{self._label}[/bold] clips", total=total_clips or 1
            )

        def advance_clip(self):
            if self._overall is not None:
                self._prog.advance(self._overall)

        def start_clip(self, name, total_frames):
            self._clip = self._prog.add_task(f"  {name}", total=total_frames or 1)

        def advance_frame(self):
            if self._clip is not None:
                self._prog.advance(self._clip)

        def finish_clip(self):
            if self._clip is not None:
                self._prog.remove_task(self._clip)
                self._clip = None

    # Live bars only make sense on a real terminal; when piped, redirected, or
    # run in the background, fall back to the plain per-clip summary lines.
    use_bars = console.is_terminal
    for d in days:
        manifest = lib.load_day(d)
        prog_cm = (
            Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
                transient=True,
            )
            if use_bars
            else contextlib.nullcontext()
        )
        with prog_cm as prog:
            reporter = _RichRenderProgress(prog, str(d)) if use_bars else None
            lines = render_day(
                cfg, manifest, lib.day_dir(d), force=force, clip_filter=clip,
                window_start_s=window_start_s, window_end_s=window_end_s,
                progress=reporter,
            )
        lib.save_day(manifest)
        console.print(f"[bold]{d}[/bold]:")
        for line in lines or ["  nothing to render"]:
            console.print(f"  {line}")


@app.command(name="slice")
def slice_cmd(
    day: Annotated[str, typer.Argument(help="Day (YYYY-MM-DD)")],
    ranges: Annotated[
        list[str],
        typer.Argument(help="Time ranges like 12:01-14:02, 1:02:03-1:04:00 or 721-842 (seconds)"),
    ],
    clip: Annotated[
        Optional[str],
        typer.Option("--video", "--clip", help="Which rendered video (substring) when the day has several"),
    ] = None,
    copy: Annotated[
        bool, typer.Option(help="Keyframe-snapped stream copy (instant, cuts up to a few seconds early)")
    ] = False,
    publish: Annotated[
        bool, typer.Option(help="Also register the slice and upload it to YouTube")
    ] = False,
):
    """Cut slices out of a rendered overlay video into out/slices/."""
    from .slice import parse_range, resolve_render_source, slice_video

    cfg = get_config()
    lib = Library(cfg.library_root)
    d = date.fromisoformat(day)
    manifest = lib.load_day(d)
    day_dir = lib.day_dir(d)
    try:
        source = resolve_render_source(manifest, day_dir, clip)
        parsed = [parse_range(r) for r in ranges]
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)

    parent = next(r for r in manifest.renders if (day_dir / r.file) == source)
    for text, (start_s, end_s) in zip(ranges, parsed):
        dest = slice_video(
            source,
            start_s,
            end_s,
            day_dir / "out" / "slices",
            codec=cfg.render.codec,
            crf=cfg.render.crf,
            preset=cfg.render.preset,
            copy=copy,
        )
        console.print(f"[green]+[/green] {dest}")
        if publish:
            from .library import RenderOutput, utcnow

            rel = str(dest.relative_to(day_dir)).replace("\\", "/")
            manifest.renders = [r for r in manifest.renders if r.file != rel]
            manifest.renders.append(
                RenderOutput(
                    file=rel,
                    session_id=parent.session_id,
                    kind="slice",
                    label=text.strip(),
                    rendered_at=utcnow(),
                    source_videos=parent.source_videos,
                )
            )

    if publish:
        from .publish import publish_day

        lib.save_day(manifest)
        for line in publish_day(cfg, manifest, day_dir):
            console.print(f"  {line}", markup=False)
        lib.save_day(manifest)


@app.command()
def publish(
    day: Annotated[Optional[str], typer.Argument(help="Day (YYYY-MM-DD); default all")] = None,
    dry_run: Annotated[bool, typer.Option(help="Show what would be uploaded")] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-upload renders even if already published (a fresh video)"),
    ] = False,
    clip: Annotated[
        Optional[str],
        typer.Option("--video", "--clip", help="Only publish renders whose file name contains this substring"),
    ] = None,
):
    """Upload rendered videos to YouTube (as configured, default private)."""
    from .publish import publish_day

    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()
    for d in days:
        manifest = lib.load_day(d)
        lines = publish_day(
            cfg, manifest, lib.day_dir(d), dry_run=dry_run, clip_filter=clip,
            force=force, save=lambda m=manifest: lib.save_day(m),
        )
        lib.save_day(manifest)
        console.print(f"[bold]{d}[/bold]:")
        for line in lines:
            console.print(f"  {line}")


@app.command()
def run(
    publish: Annotated[bool, typer.Option(help="Also upload to YouTube")] = False,
    download: Annotated[
        Optional[bool],
        typer.Option(
            "--download/--no-download",
            help="Pull new sessions off the MyChron first (default: [mychron] auto_download)",
        ),
    ] = None,
    resolution: Annotated[
        Optional[str],
        typer.Option("--resolution", "--res", help="Output resolution: hd|fhd|2k|4k (default from config)"),
    ] = None,
):
    """Full pipeline: MyChron download -> ingest -> correlate -> sync -> render [-> publish]."""
    from .pipeline import run_pipeline

    cfg = get_config()
    if resolution:
        try:
            cfg.render.resolution = resolution
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
    report = run_pipeline(cfg, publish=publish, download=download)
    for line in report.lines:
        console.print(line, markup=False)
    attention = report.needs_attention()
    if attention:
        console.print(f"\n[yellow]{len(attention)} item(s) need attention (see ? / ! above)[/yellow]")


@app.command()
def watch(
    publish: Annotated[bool, typer.Option(help="Also upload to YouTube on each run")] = False,
    once: Annotated[bool, typer.Option(help="Run a single pass and exit")] = False,
    install: Annotated[
        bool, typer.Option(help="Install as a Windows Scheduled Task (runs at logon)")
    ] = False,
):
    """Zero-touch daemon: poll for new camera/telemetry material and run the pipeline."""
    import subprocess
    import sys
    from pathlib import Path as P

    from .pipeline import watch as watch_loop, write_watch_log

    cfg = get_config()

    if install:
        import os

        # When running from the frozen MyOverlay.exe, autostart launches the
        # exe itself (everything via the exe) and must carry the same
        # MYOVERLAY_REPO/NO_UPDATE env, which schtasks /TR can't set - so use
        # the per-user Startup .cmd. A dev checkout uses mt.exe via schtasks.
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            cmd_args = f'"{sys.executable}" watch' + (" --publish" if publish else "")
            env_lines = ""
            repo = os.environ.get("MYOVERLAY_REPO")
            if repo:
                env_lines += f'set "MYOVERLAY_REPO={repo}"\n'
            env_lines += 'set "MYOVERLAY_NO_UPDATE=1"\n'
        else:
            mt_exe = P(sys.executable).parent / "mt.exe"
            cmd_args = f'"{mt_exe}" watch' + (" --publish" if publish else "")
            env_lines = ""

        if not frozen:
            result = subprocess.run(
                ["schtasks", "/Create", "/F", "/TN", "media-tools-watch",
                 "/SC", "ONLOGON", "/TR", cmd_args],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                console.print("[green]Scheduled task 'media-tools-watch' installed (runs at logon).[/green]")
                return
        # ONLOGON scheduled tasks need elevation; the per-user Startup folder
        # doesn't (and it can set env vars for the frozen exe).
        startup = P(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        if startup.is_dir():
            script = startup / "media-tools-watch.cmd"
            script.write_text(
                f'@echo off\n{env_lines}start "media-tools watch" /MIN {cmd_args}\n',
                encoding="ascii",
            )
            console.print(
                f"[green]Installed startup entry {script} (runs minimized at logon).[/green]"
            )
        else:
            console.print(f"[red]could not install autostart; launch manually: {cmd_args}[/red]")
        return

    def on_report(report):
        for line in report.lines:
            console.print(line, markup=False)
        write_watch_log(cfg, report)

    console.print(f"[bold]watching[/bold] (poll every {cfg.watch.poll_s:.0f}s, Ctrl+C to stop)")
    try:
        watch_loop(cfg, publish=publish, on_report=on_report, once=once)
    except KeyboardInterrupt:
        console.print("stopped")


@app.command()
def status(
    day: Annotated[Optional[str], typer.Argument(help="Day (YYYY-MM-DD); default all")] = None,
):
    """Show the pipeline state of one or all track days, one line per video."""
    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()
    if not days:
        console.print("[dim]library is empty[/dim]")
        return

    table = Table(title=str(cfg.library_root))
    table.add_column("video", overflow="fold", min_width=34)
    for col in ("date", "length", "session", "synced", "rendered", "published"):
        table.add_column(col)
    for d in sorted(days):
        m = lib.load_day(d)
        for v in m.videos:
            renders = [r for r in m.renders if v.file in r.source_videos]
            render_files = {r.file for r in renders}
            pubs = [p for p in m.publishes if p.file in render_files]
            if v.sync is None:
                synced = "[red]no[/red]"
            else:
                synced = f"[green]{v.sync.method}[/green] ({v.sync.confidence:.2f})"
            table.add_row(
                v.source_name,
                d.isoformat(),
                _fmt_duration(v.duration_s),
                str(v.session_id) if v.session_id is not None else "-",
                synced,
                str(len(renders)) if renders else "[dim]0[/dim]",
                "\n".join(p.video_id for p in pubs) if pubs else "-",
            )
    if not table.rows:
        console.print("[dim]no videos in the library[/dim]")
        return
    console.print(table)


@app.command()
def docs(
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Write to this file (default docs/CLI.md in the repo checkout; '-' for stdout)",
        ),
    ] = None,
):
    """Regenerate the CLI reference (docs/CLI.md) from the command tree."""
    from .docs_gen import generate_cli_docs

    text = generate_cli_docs()
    if output is not None and str(output) == "-":
        typer.echo(text, nl=False)
        return
    if output is None:
        root = next(
            (p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file()),
            None,
        )
        if root is None:
            # Frozen exe / installed wheel: no repo checkout to write into.
            typer.echo(text, nl=False)
            return
        output = root / "docs" / "CLI.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")
    console.print(f"[green]+[/green] {output}")


if __name__ == "__main__":
    app()
