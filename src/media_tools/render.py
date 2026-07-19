"""Render stage: overlay telemetry onto synced clips with PIL + ffmpeg.

For each synced clip we sample the session telemetry on the video timeline at
the overlay frame rate, render RGBA frames, and let ffmpeg composite them
over the source video (overlay frames are held between updates, audio is
copied through untouched).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .encoding import encoder_args
from .library import DayManifest, Library, RenderOutput, TrackSession, VideoClip, utcnow
from .overlay import FrameValues, OverlayRenderer, TrackProjection
from .telemetry import DayFrame, load_day_frame, opened_laps, valid_laps


@dataclass
class Timeline:
    frames: list[FrameValues]
    track_frac: np.ndarray | None


def probe_video_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {path}")
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


def _lap_distance_profiles(
    df: pd.DataFrame, laps: list[tuple[int, float, float]]
) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    """Per lap (keyed by start_s): (elapsed_s, cumulative_distance_m).

    Used for the live delta: comparing two laps at equal *distance* is what
    makes the delta meaningful mid-lap.
    """
    if "speed_ms" not in df.columns:
        return {}
    t = df["t_s"].to_numpy()
    v = df["speed_ms"].to_numpy(dtype=float)
    profiles = {}
    for _, st, e in laps:
        mask = (t >= st) & (t <= e)
        tt, vv = t[mask], v[mask]
        if len(tt) < 3:
            continue
        dist = np.concatenate([[0.0], np.cumsum(np.diff(tt) * (vv[1:] + vv[:-1]) / 2)])
        profiles[st] = (tt - st, dist)
    return profiles


def sample_timeline(
    df: pd.DataFrame,
    laps: list[tuple[int, float, float]],
    video_offset_s: float,
    start_s: float,
    duration_s: float,
    fps: float,
    min_lap_s: float = 0.0,
) -> Timeline:
    """Frame values for video times [start_s, start_s+duration_s).

    video_offset_s: session-relative time at which the video starts,
    i.e. t_session = t_video + video_offset_s.
    """
    t_video = np.arange(start_s, start_s + duration_s, 1.0 / fps)
    t_sess = t_video + video_offset_s
    t = df["t_s"].to_numpy()

    def interp(col: str) -> np.ndarray | None:
        if col not in df.columns:
            return None
        return np.interp(t_sess, t, df[col].to_numpy(dtype=float))

    speed = interp("speed_ms")
    rpm = interp("rpm")
    temp = interp("water_temp")
    lat = interp("lat")
    lon = interp("lon")
    g_lat = interp("g_lat")
    g_lon = interp("g_lon")
    steering = interp("steering_deg")

    track_frac = None
    pos = None
    if lat is not None and lon is not None:
        # Pit gaps carry NaN GPS; the projection is built from real fixes
        # only and passes NaN through (no dot in the pits).
        gps = df[["lat", "lon"]].dropna()
        if len(gps) >= 2:
            proj = TrackProjection(gps["lat"].to_numpy(), gps["lon"].to_numpy())
            track_frac = proj.project(gps["lat"].to_numpy(), gps["lon"].to_numpy())
            pos = proj.project(lat, lon)

    # Coverage mask: no telemetry values outside the logged window.
    covered = (t_sess >= t[0]) & (t_sess <= t[-1])

    # Lap validity: truncated fragments (session cut mid-lap) don't show at
    # all; laps faster than min_lap_s are physically impossible for the
    # circuit (cut track / timing glitch) - shown, but flagged invalid and
    # never counted as best or delta reference.
    # Only laps the MyChron opened AND closed with a beacon crossing count as
    # best / delta reference (drops the out-lap and the in-lap fragment).
    # Eligible laps (complete + >= 0.6x median). The min_lap_s floor is applied
    # separately by lap_valid() below, so a sub-minimum lap still shows (flagged
    # invalid) in the recent-laps list yet never wins best/delta. valid_laps()
    # is the single source of truth shared with the title and the review UI.
    full_laps = valid_laps(laps)

    def lap_valid(st: float, e: float) -> bool:
        return min_lap_s <= 0.0 or (e - st) >= min_lap_s

    profiles = _lap_distance_profiles(df, full_laps)
    v_arr = df["speed_ms"].to_numpy(dtype=float) if "speed_ms" in df.columns else None

    def reference_lap(current_start: float):
        """Best valid lap completed before the current lap starts."""
        done = [
            (n, st, e)
            for n, st, e in full_laps
            if e <= current_start + 0.001 and lap_valid(st, e) and st in profiles
        ]
        if not done:
            return None
        return min(done, key=lambda l: l[2] - l[1])

    def val(arr: np.ndarray | None, i: int) -> float | None:
        if arr is None or not covered[i] or not np.isfinite(arr[i]):
            return None
        return float(arr[i])

    # The current-lap timer counts only laps the MyChron opened with a crossing
    # (a real lap in progress, incl. the in-lap); the out-lap is never timed.
    display_laps = opened_laps(laps)

    frames: list[FrameValues] = []
    started = False  # has the lap overlay begun (first active lap seen)?
    last_lap_num: int | None = None
    for i, tv in enumerate(t_video):
        ts = t_sess[i]
        lap_num = lap_time = None
        best = None
        best_num = None
        delta = None
        speed_delta = None
        # full_laps is sorted by start time, so `completed` is oldest-first.
        completed = [
            (n, e - st, lap_valid(st, e)) for (n, st, e) in full_laps if e <= ts
        ]
        valid_completed = [(n, d) for n, d, ok in completed if ok]
        if valid_completed:
            best_num, best = min(valid_completed, key=lambda x: x[1])
        recent = completed[-5:]
        prev_lap = completed[-1] if completed else None
        for n, st, e in display_laps:
            if st <= ts < e:
                lap_num = n
                lap_time = ts - st
                started = True
                last_lap_num = n
                # Live deltas vs the reference lap at equal distance.
                ref = reference_lap(st)
                if ref is not None and st in profiles:
                    cur_t, cur_d = profiles[st]
                    ref_t, ref_d = profiles[ref[1]]
                    d_now = float(np.interp(lap_time, cur_t, cur_d))
                    ref_elapsed = float(np.interp(d_now, ref_d, ref_t))
                    delta = lap_time - ref_elapsed
                    if speed is not None and v_arr is not None:
                        ref_speed = float(np.interp(ref[1] + ref_elapsed, t, v_arr))
                        speed_delta = (float(speed[i]) - ref_speed) * 3.6
                break
        # Persistence: once the lap overlay has started, never hide it again.
        # Between laps / after the stint / during a telemetry gap, hold the
        # lap number and best/previous, and ZERO the deltas rather than
        # dropping the widgets.
        if started:
            if lap_num is None:
                lap_num = last_lap_num
            if delta is None:
                delta = 0.0
            if speed_delta is None:
                speed_delta = 0.0
        speed_v = val(speed, i)
        pos_ok = (
            pos is not None and covered[i] and np.isfinite(pos[i]).all()
        )
        frames.append(
            FrameValues(
                t_video_s=float(tv),
                speed_kmh=speed_v * 3.6 if speed_v is not None else None,
                rpm=val(rpm, i),
                water_temp=val(temp, i),
                lap_num=lap_num,
                lap_time_s=lap_time,
                best_lap_s=best,
                pos_frac=tuple(pos[i]) if pos_ok else None,
                g_lat=val(g_lat, i),
                g_lon=val(g_lon, i),
                steering_deg=val(steering, i),
                recent_laps=recent,
                prev_lap=prev_lap,
                best_lap_num=best_num,
                delta_s=delta,
                speed_delta_kmh=speed_delta,
            )
        )
    return Timeline(frames=frames, track_frac=track_frac)


def output_size(src_w: int, src_h: int, target_height: int | None) -> tuple[int, int]:
    """Frame size for a target output height. Aspect kept, even dims.

    The source is scaled (up or down) to target_height, because choosing a
    resolution preset is the explicit intent to output at that height.
    target_height None (or equal to the source) keeps the source size.
    """
    if not target_height or target_height == src_h:
        return src_w, src_h
    w = int(round(src_w * target_height / src_h / 2) * 2)
    return w, target_height


def composite_stream(
    video: Path,
    frames,
    frame_size: tuple[int, int],
    dest: Path,
    fps: float,
    start_s: float,
    duration_s: float | None,
    crf: int,
    preset: str,
    codec: str = "libx264",
    scale_to: tuple[int, int] | None = None,
    scale_flags: str = "lanczos",
) -> None:
    """Composite overlay frames onto the video, streaming raw RGBA frames
    into ffmpeg over stdin (no intermediate PNGs - at 60 fps the disk round
    trip would dominate the render time).

    `frames` is an iterator of PIL RGBA images at `frame_size`.
    """
    w, h = frame_size
    tmp = dest.with_name(dest.stem + ".encoding.mp4")
    log_path = dest.with_name(dest.stem + ".ffmpeg.log")

    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-i", str(video)]
    cmd += [
        "-f", "rawvideo",
        "-pixel_format", "rgba",
        "-video_size", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "pipe:0",
    ]
    if scale_to:
        flags = f":flags={scale_flags}" if scale_flags else ""
        graph = (
            f"[0:v]scale={scale_to[0]}:{scale_to[1]}{flags}[base];"
            "[base][1:v]overlay=eof_action=repeat[v]"
        )
    else:
        graph = "[0:v][1:v]overlay=eof_action=repeat[v]"
    cmd += [
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        *encoder_args(codec, crf, preset),
        "-c:a", "copy",
        str(tmp),
    ]

    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            for img in frames:
                proc.stdin.write(img.tobytes())
            proc.stdin.close()
            proc.wait(timeout=6 * 3600)
        except (BrokenPipeError, OSError):
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg composite failed: {tail}")
    log_path.unlink(missing_ok=True)
    _replace_with_retry(tmp, dest)


def _replace_with_retry(
    tmp: Path, dest: Path, attempts: int = 24, delay_s: float = 5.0
) -> None:
    """Atomic swap that survives transient Windows file locks.

    A media player, Explorer preview, or the search indexer holding either
    file must not discard a finished multi-minute render; retry for a couple
    of minutes and, if the lock persists, keep the finished file next to the
    destination and say exactly where it is.
    """
    import time

    for attempt in range(attempts):
        try:
            tmp.replace(dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                break
            time.sleep(delay_s)
    raise RuntimeError(
        f"finished render is at {tmp} but {dest} is locked by another program "
        "(video player / Explorer preview?). Close it and rename the file "
        f"manually, or re-run render - the encode does NOT need to repeat."
    )


def composite(
    video: Path,
    frames_dir: Path,
    dest: Path,
    fps: float,
    start_s: float,
    duration_s: float | None,
    crf: int,
    preset: str,
    codec: str = "libx264",
    scale_to: tuple[int, int] | None = None,
) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    # Encode to a temp name and swap in atomically: a previous good render
    # must never be replaced by a half-written file.
    tmp = dest.with_name(dest.stem + ".encoding.mp4")
    if scale_to:
        graph = (
            f"[0:v]scale={scale_to[0]}:{scale_to[1]}[base];"
            "[base][1:v]overlay=eof_action=repeat[v]"
        )
    else:
        graph = "[0:v][1:v]overlay=eof_action=repeat[v]"
    cmd += [
        "-i", str(video),
        "-framerate", str(fps),
        "-i", str(frames_dir / "%06d.png"),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        *encoder_args(codec, crf, preset),
        "-c:a", "copy",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg composite failed: {proc.stderr[:800]}")
    tmp.replace(dest)


# Keep this many seconds of lead-in before the first lap (the launch / race
# start), dropping the pre-race grid staging.
RACE_START_BUFFER_S = 15.0


def render_clip(
    cfg,
    day_dir: Path,
    manifest: DayManifest,
    clip: VideoClip,
    day: DayFrame,
    lap_num: int | None = None,
    force: bool = False,
    window_start_s: float = 0.0,
    window_end_s: float = 0.0,
    title: str | None = None,
    description: str | None = None,
    append_best_lap: bool = True,
) -> Path:
    assert clip.sync is not None
    video = day_dir / clip.file
    df = day.df
    video_offset_s = (clip.sync.video_start_utc - day.start_utc).total_seconds()

    # Scope lap-based overlays (lap counter, best, previous, delta reference)
    # to THIS clip's session. A day frame concatenates every stint of the day
    # - often across different track layouts - so a day-wide best lap or delta
    # reference belongs to a different heat and is meaningless here.
    laps = day.laps
    sess = next((s for s in manifest.sessions if s.id == clip.session_id), None)
    if sess is not None:
        s0 = (sess.start_utc - day.start_utc).total_seconds()
        s1 = (sess.end_utc - day.start_utc).total_seconds()
        laps = [(n, st, e) for (n, st, e) in day.laps if s0 - 1.0 <= st <= s1 + 1.0]

    # Guard: a video whose start lands after the race is already underway
    # (>=1 completed lap) means the auto-sync mislocated it - a race starts
    # at zero laps. Refuse (require a manual anchor). Manual syncs are
    # trusted and bypass the guard.
    if lap_num is None and clip.sync.method != "manual":
        done = _valid_laps_before(laps, video_offset_s, cfg.render.min_lap_s)
        if done:
            raise RaceAlreadyRunning(len(done))

    start_s = float(window_start_s)
    duration_s = clip.duration_s or 0.0
    suffix = "overlay"
    race_end_cut = False
    if lap_num is None and cfg.render.scan_video_for_race_end:
        # scan-video-for-race-end: listen for the engine shutdown and trim
        # to the last finish-line crossing + buffer. Cached in the manifest
        # (clip.race_end) so each clip is scanned once.
        if clip.race_end is None or force:
            from .raceend import detect_race_end

            clip.race_end = detect_race_end(
                video, laps, video_offset_s, duration_s
            )
        cut = clip.race_end.cut_at_s
        if cut is not None and 0.0 < cut < duration_s:
            duration_s = cut
            race_end_cut = True

    # Race-start trim: begin RACE_START_BUFFER_S before the first lap (the
    # launch), dropping the pre-race grid staging. Only for a full clip render
    # (not a lap render or an explicit --from sample). `duration_s` here holds
    # the END time (the race-end cut, or the full clip length since start was
    # 0); convert it to a render length once start_s moves.
    race_start_trim = False
    if lap_num is None and window_start_s == 0.0 and window_end_s == 0.0 and laps:
        first_lap_video = min(st for _, st, _ in laps) - video_offset_s
        rs = first_lap_video - RACE_START_BUFFER_S
        if rs > start_s + 0.5:
            end_time = duration_s
            start_s = rs
            duration_s = end_time - start_s
            race_start_trim = True

    if lap_num is not None:
        match = [l for l in laps if l[0] == lap_num]
        if not match:
            raise ValueError(f"lap {lap_num} not found in day telemetry")
        _, lap_start, lap_end = match[0]
        start_s = max(0.0, lap_start - video_offset_s - 1.0)  # 1 s lead-in
        duration_s = min(duration_s, lap_end - video_offset_s + 1.0) - start_s
        if duration_s <= 0:
            raise ValueError(f"lap {lap_num} is not covered by clip {clip.file}")
        suffix = f"lap{lap_num}"

    # Windowed sample render (validation): render only [window_start_s, end],
    # where end is the race-end cut (or the clip's end). `duration_s` above
    # holds the END time; convert it to a length for the sub-range render.
    windowed = (window_start_s > 0.0 or window_end_s > 0.0) and lap_num is None
    if windowed:
        if window_end_s > 0.0:
            end_time = window_end_s
        else:
            end_time = duration_s if race_end_cut else (clip.duration_s or 0.0)
        duration_s = end_time - start_s
        if duration_s <= 0:
            raise ValueError("--from starts after --to / the clip's end/cut")
        suffix = f"overlay_sample_{int(start_s)}-{int(end_time)}"

    fps = cfg.render.overlay_fps
    timeline = sample_timeline(
        df, laps, video_offset_s, start_s, duration_s, fps, cfg.render.min_lap_s
    )

    src_w, src_h = probe_video_size(video)
    width, height = output_size(src_w, src_h, cfg.render.target_height())
    # Speedometer scale: day's top speed rounded up to the next 10 km/h.
    max_speed_kmh = 80.0
    if "speed_ms" in df.columns:
        observed = float(np.nanmax(df["speed_ms"].to_numpy(dtype=float))) * 3.6
        max_speed_kmh = max(60.0, np.ceil(observed * 1.02 / 10) * 10)
    # Chrome for every channel the DAY has: widgets are visible from the
    # first frame (placeholder state) and populate the instant data begins.
    channels = set()
    if "speed_ms" in df.columns:
        channels.add("speed")
    if "g_lat" in df.columns or "g_lon" in df.columns:
        channels.add("g")
    if "steering_deg" in df.columns:
        channels.add("steering")
    renderer = OverlayRenderer(
        width,
        height,
        track_frac=timeline.track_frac,
        max_rpm=cfg.render.max_rpm,
        font_path=cfg.render.font_path,
        max_speed_kmh=max_speed_kmh,
        channels=channels,
        language=cfg.language,
    )

    dest = day_dir / "out" / f"{video.stem}_{suffix}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    composite_stream(
        video,
        (renderer.render_frame(values) for values in timeline.frames),
        (width, height),
        dest,
        fps,
        start_s,
        # ffmpeg needs a real -t whenever the window is a sub-range of the
        # clip (lap render or race-end trim); only a full-length render may
        # pass None (read to EOF).
        duration_s if (lap_num is not None or race_end_cut or race_start_trim or windowed) else None,
        cfg.render.crf,
        cfg.render.preset,
        cfg.render.codec,
        scale_to=(width, height) if (width, height) != (src_w, src_h) else None,
        scale_flags=cfg.render.scale_flags,
    )

    manifest.renders = [r for r in manifest.renders if r.file != _rel(dest, day_dir)]
    manifest.renders.append(
        RenderOutput(
            file=_rel(dest, day_dir),
            session_id=clip.session_id,
            kind="slice" if windowed else ("lap" if lap_num is not None else "session"),
            lap_num=lap_num,
            label=(
                f"sample {int(start_s // 60)}:{int(start_s % 60):02d}"
                f"-{int((start_s + duration_s) // 60)}:{int((start_s + duration_s) % 60):02d}"
                if windowed
                else None
            ),
            rendered_at=utcnow(),
            source_videos=[clip.file],
            title=title,
            description=description,
            append_best_lap=append_best_lap,
        )
    )
    return dest


def _rel(path: Path, day_dir: Path) -> str:
    return str(path.relative_to(day_dir)).replace("\\", "/")


class RaceAlreadyRunning(Exception):
    """The clip's sync places its start after the session's race is already
    underway (>= 1 completed lap). A race starts at zero laps, so this means
    the automatic sync mislocated the video (or the clip is a mid-race
    continuation). Rendering it would show a false lap count / reference lap,
    so we stop and require a manual sync anchor instead."""

    def __init__(self, laps_done: int):
        self.laps_done = laps_done
        super().__init__(f"{laps_done} lap(s) already completed at video start")


def _valid_laps_before(laps, cutoff_s: float, min_lap_s: float) -> list:
    """Laps that ended before cutoff_s (video start), used only by the
    mis-sync guard. This is deliberately NOT best-lap eligibility (valid_laps /
    complete_laps): the guard must fire on ANY real lap progress before the
    clip starts - even a single beacon crossing means the race was underway -
    so it filters by a plausible duration floor, not by beacon open+close."""
    durs = sorted(e - st for _, st, e in laps)
    median = durs[len(durs) // 2] if durs else 0.0
    floor = max(min_lap_s, 0.6 * median if median else 0.0)
    return [(n, st, e) for (n, st, e) in laps if e <= cutoff_s and (e - st) >= floor]


def render_day(
    cfg,
    manifest: DayManifest,
    day_dir: Path,
    force: bool = False,
    clip_filter: str | None = None,
    window_start_s: float = 0.0,
    window_end_s: float = 0.0,
) -> list[str]:
    report: list[str] = []
    already = {src for r in manifest.renders for src in r.source_videos}
    day: DayFrame | None = None

    for clip in manifest.videos:
        # Targeting a clip by name re-renders just it (implies force).
        if clip_filter and clip_filter not in clip.source_name and clip_filter not in clip.file:
            continue
        clip_force = force or bool(clip_filter)
        if clip.sync is None:
            continue
        if clip.sync.confidence < cfg.render.min_sync_confidence:
            report.append(
                f"? {clip.file}: sync confidence {clip.sync.confidence:.2f} below "
                f"{cfg.render.min_sync_confidence}, skipped"
            )
            continue
        if clip.file in already and not clip_force:
            report.append(f"= {clip.file}: already rendered")
            continue
        if day is None:
            day = load_day_frame(day_dir, manifest)
        try:
            dest = render_clip(
                cfg, day_dir, manifest, clip, day, force=clip_force,
                window_start_s=window_start_s, window_end_s=window_end_s,
            )
        except RaceAlreadyRunning as exc:
            report.append(
                f"! {clip.file}: starts mid-race ({exc.laps_done} lap(s) already "
                f"completed) - a race starts at 0 laps, so the auto-sync is wrong. "
                f"Set it manually: mt sync {manifest.date} --clip {clip.source_name} "
                f"--lap N --at MM:SS  (N = the telemetry lap you are starting at "
                f"video time MM:SS)"
            )
            continue
        except RuntimeError as exc:
            # A locked output (open in a player), an ffmpeg error, etc. must
            # not abort the whole day's batch - report and move on.
            report.append(f"! {clip.file}: {exc}")
            continue
        report.append(f"+ {clip.file} -> {_rel(dest, day_dir)}")
    return report
