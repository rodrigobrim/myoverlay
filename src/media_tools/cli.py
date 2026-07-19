"""media-tools CLI (`mt`)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import Config, load_config
from .library import Library

app = typer.Typer(help="Zero-touch karting video + telemetry pipeline.", no_args_is_help=True)
console = Console()

_config_path: Path | None = None


@app.callback()
def main(
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Path to config.toml")
    ] = None,
):
    global _config_path
    _config_path = config


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
    rs3: Annotated[
        bool,
        typer.Option("--rs3", help="First drive Race Studio 3 to download from the MyChron"),
    ] = False,
    rs3_only: Annotated[
        bool,
        typer.Option(
            "--rs3-only",
            help="Only drive the Race Studio 3 download; skip camera/mychron file ingest",
        ),
    ] = False,
    troubleshoot: Annotated[
        bool,
        typer.Option(
            "--troubleshoot",
            help="RS3 diagnostics (implies --rs3-only): snapshot every step, dump the "
            "control tree, unhide already-downloaded sessions, scroll the list, 30s waits",
        ),
    ] = False,
):
    """Copy new camera clips and MyChron sessions into the library (download only)."""
    cfg = get_config()
    if rs3 or rs3_only or troubleshoot:
        from .ingest.rs3 import trigger_rs3_download

        console.print("[bold]rs3[/bold]:")
        for line in trigger_rs3_download(cfg, troubleshoot=troubleshoot):
            console.print(f"  {line}", markup=False)
    if rs3_only or troubleshoot:
        # RS-download-only: never touch camera/mychron ingest (and never render).
        return
    if source in ("all", "camera"):
        from .ingest.camera import ingest_camera

        _print_ingest_report("camera", ingest_camera(cfg))
    if source in ("all", "mychron"):
        from .ingest.mychron import ingest_mychron

        _print_ingest_report("mychron", ingest_mychron(cfg))


@app.command(name="join")
def join_cmd(
    day: Annotated[str, typer.Argument(help="Day (YYYY-MM-DD)")],
    clips: Annotated[
        Optional[str],
        typer.Option(
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
    """Join camera-split video segments (GoPro/DJI ~4 GB rollovers) into one clip."""
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
    """Group ingested files into track sessions and assign clips to them."""
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
            f"{report.assigned_videos} clip(s) assigned"
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
    clip: Annotated[Optional[str], typer.Option(help="Manual mode: clip source name")] = None,
    video_start: Annotated[
        Optional[str],
        typer.Option(help="Manual mode: exact UTC start of the clip (ISO 8601)"),
    ] = None,
    lap: Annotated[
        Optional[int],
        typer.Option(help="Manual mode: telemetry lap number you start at video time --at"),
    ] = None,
    at: Annotated[
        Optional[str],
        typer.Option("--at", help="Manual mode: video time (MM:SS) of the --lap start/finish crossing"),
    ] = None,
    force: Annotated[bool, typer.Option(help="Re-sync clips that already have a sync")] = False,
):
    """Align clips with telemetry (auto, or manual --clip with --video-start or --lap/--at)."""
    from datetime import datetime as dt, timedelta

    from .library import SyncInfo
    from .sync import sync_day

    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()

    if clip or video_start or lap is not None:
        if not (clip and day and (video_start or lap is not None)):
            console.print(
                "[red]manual mode needs DAY, --clip and either --video-start or --lap/--at[/red]"
            )
            raise typer.Exit(2)
        manifest = lib.load_day(date.fromisoformat(day))
        matches = [v for v in manifest.videos if v.source_name == clip]
        if not matches:
            console.print(f"[red]no clip named {clip} on {day}[/red]")
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
    force: Annotated[bool, typer.Option(help="Re-render already rendered clips")] = False,
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
            "--clip",
            help="Only render clips whose name contains this substring (re-renders them)",
        ),
    ] = None,
    sample_from: Annotated[
        Optional[str],
        typer.Option(
            "--from",
            help="Render only from this video time (MM:SS or seconds) - a short sample "
            "to validate the trim without rendering the whole clip",
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
):
    """Render telemetry overlays onto synced clips."""
    from .render import render_day
    from .slice import parse_timestamp

    cfg = get_config()
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
    for d in days:
        manifest = lib.load_day(d)
        lines = render_day(
            cfg, manifest, lib.day_dir(d), force=force, clip_filter=clip,
            window_start_s=window_start_s, window_end_s=window_end_s,
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
        Optional[str], typer.Option(help="Which rendered clip (substring) when the day has several")
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
    clip: Annotated[
        Optional[str],
        typer.Option("--clip", help="Only publish renders whose file name contains this substring"),
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
            save=lambda m=manifest: lib.save_day(m),
        )
        lib.save_day(manifest)
        console.print(f"[bold]{d}[/bold]:")
        for line in lines:
            console.print(f"  {line}")


@app.command()
def run(
    publish: Annotated[bool, typer.Option(help="Also upload to YouTube")] = False,
    rs3: Annotated[
        Optional[bool],
        typer.Option("--rs3/--no-rs3", help="Trigger Race Studio 3 download (default: [rs3] enabled)"),
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
    report = run_pipeline(cfg, publish=publish, trigger_rs3=rs3)
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

        # When running from the frozen myoverlay.exe, autostart launches the
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
    """Show the pipeline state of one or all track days."""
    cfg = get_config()
    lib = Library(cfg.library_root)
    days = [date.fromisoformat(day)] if day else lib.day_dates()
    if not days:
        console.print("[dim]library is empty[/dim]")
        return

    table = Table(title=str(cfg.library_root))
    for col in ("day", "track", "videos", "telemetry", "sessions", "synced", "rendered", "published"):
        table.add_column(col)
    for d in days:
        m = lib.load_day(d)
        synced = sum(1 for v in m.videos if v.sync is not None)
        table.add_row(
            d.isoformat(),
            m.track or "-",
            str(len(m.videos)),
            str(len(m.telemetry)),
            str(len(m.sessions)),
            f"{synced}/{len(m.videos)}",
            str(len(m.renders)),
            str(len(m.publishes)),
        )
    console.print(table)


if __name__ == "__main__":
    app()
