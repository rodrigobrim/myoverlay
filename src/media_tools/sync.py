"""Video<->telemetry sync via engine-audio / RPM cross-correlation.

The DJI camera has no GPS, so alignment comes from the sound of the engine:
both the audio loudness and the spectral centroid of a kart's onboard audio
track follow engine RPM closely. We extract a 10 Hz audio feature from the
clip, a 10 Hz RPM feature from the telemetry, and find the lag that maximizes
their normalized cross-correlation, bounded to a window around the camera's
own clock estimate.

Convention: `lag_s` is the video start expressed in seconds since telemetry
start, so `video_start_utc = telemetry_start_utc + lag_s`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_HZ = 10.0
AUDIO_RATE = 8000
# Overlap shorter than this contributes too little evidence to trust.
MIN_OVERLAP_S = 20.0
DEFAULT_MIN_CONFIDENCE = 0.5

# Fine pass: re-correlate at 2.5 ms resolution around the coarse peak, then
# parabolic sub-sample interpolation. Target accuracy: <= 5 ms.
FINE_FEATURE_HZ = 400.0
FINE_WIN = 512
FINE_LAG_WINDOW_S = 3.0
FINE_SEGMENT_S = 120.0

# Sync-point guardrail: only *sustained* engine activity may define the
# alignment. Short rev "kicks" (warm-up blips in the pits) are masked out of
# the transient/fine correlations so they cannot become the lock point.
MIN_SUSTAINED_S = 3.0


def _activity_threshold(sig: np.ndarray) -> float:
    """Midpoint between the quiet floor and the active level."""
    lo, hi = np.percentile(sig, [10, 90])
    return float((lo + hi) / 2)


def _sustained_mask(
    active: np.ndarray, feature_hz: float, min_run_s: float = MIN_SUSTAINED_S
) -> np.ndarray:
    """Float mask (0..1) keeping only activity runs >= min_run_s, with ~0.5 s
    tapered edges so masking cannot inject its own transients."""
    active = np.asarray(active, dtype=bool)
    n = len(active)
    mask = np.zeros(n, dtype=float)
    min_len = max(1, int(min_run_s * feature_hz))
    edges = np.diff(active.astype(np.int8))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0] + 1)
    if active[0]:
        starts.insert(0, 0)
    if active[-1]:
        ends.append(n)
    for s0, e0 in zip(starts, ends):
        if e0 - s0 >= min_len:
            mask[s0:e0] = 1.0
    taper = max(3, int(0.5 * feature_hz) | 1)
    kernel = np.hanning(taper)
    kernel /= kernel.sum()
    return np.convolve(np.pad(mask, taper // 2, mode="edge"), kernel, mode="valid")


@dataclass
class SyncResult:
    lag_s: float
    confidence: float
    method: str = "ncc"


def _ncc(
    x: np.ndarray,
    y: np.ndarray,
    feature_hz: float,
    lag_window_s: tuple[float, float] | None,
    audio_time_offset_s: float,
    min_overlap_s: float,
) -> SyncResult:
    """Normalized cross-correlation of preprocessed signals over all lags.

    x is the telemetry-side signal, y the audio-side; lag is y's start
    within x's timeline, minus audio_time_offset_s (FFT frames represent
    their window center).
    """
    n_x, n_y = len(x), len(y)
    if n_x < 2 or n_y < 2:
        return SyncResult(lag_s=0.0, confidence=0.0)

    ones_x = np.ones(n_x)
    ones_y = np.ones(n_y)
    num = np.correlate(x, y, mode="full")
    sum_x2 = np.correlate(x * x, ones_y, mode="full")
    sum_y2 = np.correlate(ones_x, y * y, mode="full")
    score = num / np.sqrt(np.maximum(sum_x2 * sum_y2, 1e-12))

    lags = np.arange(-(n_y - 1), n_x)
    overlap = np.correlate(ones_x, ones_y, mode="full")

    valid = overlap >= int(min_overlap_s * feature_hz)
    if lag_window_s is not None:
        lo, hi = lag_window_s
        valid &= (lags / feature_hz >= lo) & (lags / feature_hz <= hi)
    if not valid.any():
        return SyncResult(lag_s=0.0, confidence=0.0)

    score = np.where(valid, score, -np.inf)
    best = int(np.argmax(score))
    return SyncResult(
        lag_s=float(lags[best] / feature_hz) - audio_time_offset_s,
        confidence=float(np.clip(score[best], 0.0, 1.0)),
    )


def _smooth(sig: np.ndarray, feature_hz: float, window_s: float) -> np.ndarray:
    w = max(3, int(window_s * feature_hz) | 1)
    kernel = np.ones(w) / w
    return np.convolve(np.pad(sig, w // 2, mode="reflect"), kernel, mode="valid")


def extract_audio_pcm(video: Path) -> np.ndarray:
    """Decode the clip's audio to mono float32 at AUDIO_RATE via ffmpeg."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-i", str(video),
            "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
            "-f", "s16le", "-",
        ],
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed for {video}: {proc.stderr.decode(errors='replace')[:500]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _zscore(x: np.ndarray) -> np.ndarray:
    std = x.std()
    if std < 1e-9:
        return np.zeros_like(x)
    return (x - x.mean()) / std


# Engine fundamental + low harmonics for a kart (4k-16k RPM -> 66-266 Hz x1/x2/x3).
ENGINE_BAND_HZ = (60.0, 800.0)


def _frame_features(
    pcm: np.ndarray, rate: int, hop: int, win: int, chunk_frames: int = 8192
) -> tuple[np.ndarray, np.ndarray]:
    """(rms, dominant in-band frequency) per frame, computed in chunks so
    high frame rates over long clips stay memory-bounded."""
    n_frames = max(0, (len(pcm) - win) // hop + 1)
    if n_frames < 3:
        raise ValueError("clip audio too short for sync")

    window = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / rate)
    band = (freqs >= ENGINE_BAND_HZ[0]) & (freqs <= ENGINE_BAND_HZ[1])
    band_freqs = freqs[band]

    df_hz = rate / win
    rms_parts, peak_parts = [], []
    for start in range(0, n_frames, chunk_frames):
        count = min(chunk_frames, n_frames - start)
        idx = np.arange(win)[None, :] + hop * (start + np.arange(count))[:, None]
        frames = pcm[idx] * window[None, :]
        rms_parts.append(np.sqrt((frames**2).mean(axis=1)))
        spec = np.abs(np.fft.rfft(frames, axis=1))[:, band]
        k = np.argmax(spec, axis=1)
        # Parabolic interpolation across FFT bins: a continuous frequency
        # estimate instead of a bin staircase (bin width would otherwise
        # dominate the fine sync error budget).
        k_in = np.clip(k, 1, spec.shape[1] - 2)
        rows = np.arange(count)
        a, b, c = spec[rows, k_in - 1], spec[rows, k_in], spec[rows, k_in + 1]
        denom = a - 2 * b + c
        delta = np.where(np.abs(denom) > 1e-12, 0.5 * (a - c) / denom, 0.0)
        delta = np.clip(delta, -0.5, 0.5) * (k == k_in)
        peak_parts.append(band_freqs[k] + delta * df_hz)
    return np.concatenate(rms_parts), np.concatenate(peak_parts)


def audio_feature(
    pcm: np.ndarray,
    rate: int = AUDIO_RATE,
    feature_hz: float = FEATURE_HZ,
    win: int = 2048,
) -> np.ndarray:
    """Engine feature: z(RMS) + z(dominant frequency in the engine band).

    The dominant in-band frequency tracks the firing frequency (i.e. RPM)
    regardless of loudness; RMS adds throttle-on/off transients. Spectral
    centroid is deliberately NOT used: rising loudness of a low-frequency
    engine tone drags the centroid down while pitch pushes it up, so the two
    effects cancel.
    """
    hop = int(rate / feature_hz)
    rms, peak_freq = _frame_features(pcm, rate, hop, win)
    return _zscore(_zscore(rms) + _zscore(peak_freq))


# Channels usable as the engine trace for audio sync, best first. On a
# direct-drive kart RPM is proportional to speed, so GPS Speed is a valid
# proxy when the RPM lead isn't connected (unified_frame drops dead channels).
ENGINE_COLUMNS = ("rpm", "jackshaft", "speed_ms")


def engine_column(df: pd.DataFrame) -> str:
    for col in ENGINE_COLUMNS:
        if col in df.columns:
            v = df[col].to_numpy(dtype=float)
            v = v[np.isfinite(v)]
            if len(v) and v.std() > 1e-9:
                return col
    raise ValueError(
        "telemetry has no usable engine channel (rpm/jackshaft/speed); cannot audio-sync"
    )


def rpm_feature(df: pd.DataFrame, feature_hz: float = FEATURE_HZ) -> np.ndarray:
    """Engine trace resampled onto a uniform 10 Hz grid from telemetry t=0."""
    if df.empty:
        raise ValueError("telemetry frame is empty; cannot audio-sync")
    col = engine_column(df)
    t_end = float(df["t_s"].iloc[-1])
    grid = np.arange(0.0, t_end, 1.0 / feature_hz)
    series = np.interp(grid, df["t_s"].to_numpy(), df[col].to_numpy(dtype=float))
    return _zscore(series)


def find_offset(
    rpm: np.ndarray,
    audio: np.ndarray,
    feature_hz: float = FEATURE_HZ,
    lag_window_s: tuple[float, float] | None = None,
    audio_time_offset_s: float = 0.0,
) -> SyncResult:
    """Lag (in s, telemetry-relative video start) maximizing normalized correlation.

    `audio[j]` is assumed to line up with `rpm[j + lag*feature_hz]`.
    audio_time_offset_s: how far each audio feature's effective timestamp sits
    after its frame index (an FFT frame represents its window CENTER, so this
    is win/(2*rate) for framed features; 0 for point samples).

    Both features are differenced first: the RPM trace is a smooth
    random-walk-like signal whose raw autocorrelation makes chance alignments
    look plausible; differencing whitens it so only genuinely matching
    transients (rev spikes, lifts) score high. The score per lag is a true
    normalized cross-correlation over the overlapping segment, in [-1, 1].
    """
    result = _ncc(
        np.diff(rpm),
        np.diff(audio),
        feature_hz,
        lag_window_s,
        audio_time_offset_s,
        MIN_OVERLAP_S,
    )
    result.method = "transient"
    return result


# Envelope stage: the driving/pit on-off pattern of a whole track day is
# unmistakable at minute scale even when wind noise buries the fine features.
ENVELOPE_SMOOTH_S = 10.0
ENVELOPE_HIGHPASS_S = 120.0
ENVELOPE_MIN_OVERLAP_S = 300.0


def envelope_offset(
    df: pd.DataFrame,
    pcm: np.ndarray,
    lag_window_s: tuple[float, float] | None = None,
    rate: int = AUDIO_RATE,
    feature_hz: float = FEATURE_HZ,
) -> SyncResult:
    """Match the audio loudness envelope against the day's engine trace."""
    hop = int(rate / feature_hz)
    rms, _ = _frame_features(pcm, rate, hop, 2048)
    audio = _highpass(_smooth(_zscore(rms), feature_hz, ENVELOPE_SMOOTH_S), feature_hz, ENVELOPE_HIGHPASS_S)

    t_end = float(df["t_s"].iloc[-1])
    engine = _engine_on_grid(df, 0.0, t_end, feature_hz)
    engine = _highpass(_smooth(_zscore(engine), feature_hz, ENVELOPE_SMOOTH_S), feature_hz, ENVELOPE_HIGHPASS_S)

    result = _ncc(
        engine,
        audio,
        feature_hz,
        lag_window_s,
        2048 / (2 * rate),
        ENVELOPE_MIN_OVERLAP_S,
    )
    result.method = "envelope"
    return result


def _engine_on_grid(df: pd.DataFrame, t_start: float, t_end: float, feature_hz: float) -> np.ndarray:
    col = engine_column(df)
    grid = np.arange(t_start, t_end, 1.0 / feature_hz)
    return np.interp(grid, df["t_s"].to_numpy(), df[col].to_numpy(dtype=float))


def _rank_norm(sig: np.ndarray) -> np.ndarray:
    """Rank (quantile) transform, z-scored.

    Makes the correlation invariant to any monotone distortion between the
    two signals (mic compression, wind-noise power law, RMS convexity) -
    such distortions otherwise shift the peak of asymmetric traces.
    """
    order = np.argsort(sig, kind="stable")
    ranks = np.empty(len(sig), dtype=float)
    ranks[order] = np.arange(len(sig), dtype=float)
    return _zscore(ranks)


def _highpass(sig: np.ndarray, feature_hz: float, window_s: float = 1.0) -> np.ndarray:
    """Remove drift by subtracting a moving average.

    At fine frame rates consecutive windows overlap almost entirely, so
    per-sample differencing (used in the coarse pass) would leave only frame
    noise; subtracting a ~1 s moving average keeps the sub-second transient
    shape that carries the timing information.
    """
    w = max(3, int(window_s * feature_hz) | 1)
    kernel = np.ones(w) / w
    padded = np.pad(sig, w // 2, mode="reflect")
    return sig - np.convolve(padded, kernel, mode="valid")


def refine_offset(
    df: pd.DataFrame,
    pcm: np.ndarray,
    coarse_lag_s: float,
    rate: int = AUDIO_RATE,
    feature_hz: float = FINE_FEATURE_HZ,
    lag_window_s: float = FINE_LAG_WINDOW_S,
) -> SyncResult:
    """Fine pass: NCC at 1/feature_hz resolution around the coarse lag with
    parabolic sub-sample interpolation, estimated independently on three
    sub-segments of the most active region; the median kills residual
    per-segment noise.
    """
    clip_s = len(pcm) / rate

    # Pick the audio region with the strongest engine activity.
    seg_len_s = min(FINE_SEGMENT_S, clip_s)
    if clip_s > seg_len_s:
        coarse = audio_feature(pcm, rate, FEATURE_HZ)
        w = int(seg_len_s * FEATURE_HZ)
        rolling_var = np.convolve(coarse**2, np.ones(w) / w, mode="valid")
        region_start_s = float(np.argmax(rolling_var) / FEATURE_HZ)
    else:
        region_start_s = 0.0

    # Three overlapping sub-segments spanning the region.
    sub_len_s = seg_len_s / 2
    results = []
    for frac in (0.0, 0.25, 0.5):
        r = _refine_segment(
            df, pcm, coarse_lag_s,
            region_start_s + frac * seg_len_s, sub_len_s,
            rate, feature_hz, lag_window_s,
        )
        if r.confidence > 0.2:
            results.append(r)
    if not results:
        return SyncResult(lag_s=coarse_lag_s, confidence=0.0)
    lags = sorted(r.lag_s for r in results)
    return SyncResult(
        lag_s=float(lags[len(lags) // 2]),
        confidence=float(np.median([r.confidence for r in results])),
        method="fine",
    )


def _refine_segment(
    df: pd.DataFrame,
    pcm: np.ndarray,
    coarse_lag_s: float,
    seg_start_s: float,
    seg_len_s: float,
    rate: int,
    feature_hz: float,
    lag_window_s: float,
) -> SyncResult:
    hop = int(rate / feature_hz)
    clip_s = len(pcm) / rate
    seg_start_s = max(0.0, min(seg_start_s, clip_s - 5.0))
    seg = pcm[int(seg_start_s * rate) : int((seg_start_s + seg_len_s) * rate)]
    if len(seg) < 5 * rate:
        return SyncResult(lag_s=coarse_lag_s, confidence=0.0)

    rms, peak = _frame_features(seg, rate, hop, FINE_WIN)
    y = _highpass(_zscore(_rank_norm(rms) + _rank_norm(peak)), feature_hz)
    # Light symmetric smoothing (~45 ms) suppresses frame noise without
    # biasing the correlation peak position.
    n_smooth = max(3, int(0.045 * feature_hz) | 1)
    smooth = np.ones(n_smooth) / n_smooth
    y = np.convolve(np.pad(y, n_smooth // 2, mode="reflect"), smooth, mode="valid")
    # Guardrail: only sustained engine activity may define the sync point.
    y = y * _sustained_mask(rms > _activity_threshold(rms), feature_hz)

    # Telemetry on the same fine grid, spanning the segment +- the lag window.
    t0 = coarse_lag_s + seg_start_s - lag_window_s
    t1 = t0 + (len(y) / feature_hz) + 2 * lag_window_s
    engine = _engine_on_grid(df, t0, t1, feature_hz)
    x = _highpass(_rank_norm(engine), feature_hz)
    x = x * _sustained_mask(engine > _activity_threshold(engine), feature_hz)
    if len(x) <= len(y):
        return SyncResult(lag_s=coarse_lag_s, confidence=0.0)

    num = np.correlate(x, y, mode="valid")
    sum_x2 = np.correlate(x * x, np.ones(len(y)), mode="valid")
    sum_y2 = float((y * y).sum())
    score = num / np.sqrt(np.maximum(sum_x2 * sum_y2, 1e-12))
    confidence = float(np.clip(score.max(), 0.0, 1.0))

    # The true correlation ridge is >=100 ms wide (feature smoothing + engine
    # trace smoothness); per-sample noise on top of it is what limits the
    # sub-sample interpolation. Smoothing the ridge (~40 ms) removes the
    # jitter without moving a symmetric peak.
    w = max(3, int(0.04 * feature_hz) | 1)
    kernel = np.ones(w) / w
    ridge = np.convolve(np.pad(score, w // 2, mode="reflect"), kernel, mode="valid")

    k = int(np.argmax(ridge))
    # lag such that video_start = telemetry_start + lag: x[k] is telemetry
    # time t0 + k/fs and aligns with the segment's first frame, whose
    # effective timestamp is win/(2*rate) after the segment start.
    lag = t0 + k / feature_hz - seg_start_s - FINE_WIN / (2 * rate)

    # Parabolic interpolation around the smoothed peak for sub-sample
    # resolution.
    if 0 < k < len(ridge) - 1:
        s_prev, s_peak, s_next = ridge[k - 1], ridge[k], ridge[k + 1]
        denom = s_prev - 2 * s_peak + s_next
        if abs(denom) > 1e-12:
            delta = 0.5 * (s_prev - s_next) / denom
            lag += float(np.clip(delta, -1, 1)) / feature_hz

    return SyncResult(lag_s=float(lag), confidence=confidence)


def estimate_offset(
    df: pd.DataFrame,
    pcm: np.ndarray,
    est_lag_s: float | None = None,
    clock_tolerance_s: float | None = None,
) -> SyncResult:
    """Three-stage sync, each stage narrowing the search for the next.

    1. envelope: minute-scale driving/pit pattern - immune to wind noise and
       to arbitrarily wrong device clocks (searches the whole day if no
       window is given);
    2. transient: differenced engine feature at 10 Hz;
    3. fine: 200 Hz NCC + parabolic peak (the <=20 ms stage).

    A later stage replaces the estimate only when it agrees with (or beats
    the confidence of) the stage before it; the reported confidence is the
    best among agreeing stages.
    """
    window = None
    if est_lag_s is not None and clock_tolerance_s is not None:
        window = (est_lag_s - clock_tolerance_s, est_lag_s + clock_tolerance_s)

    env = envelope_offset(df, pcm, window)
    best = env if env.confidence > 0.0 else None

    transient_window = window
    if best is not None and best.confidence >= 0.3:
        transient_window = (best.lag_s - 60.0, best.lag_s + 60.0)
    # Guardrail: transient matching only on sustained activity, so pit rev
    # kicks cannot become the lock point (the envelope stage above is NOT
    # masked - the on/off pattern is exactly its signal).
    rpm_f = rpm_feature(df)
    rpm_f = rpm_f * _sustained_mask(rpm_f > _activity_threshold(rpm_f), FEATURE_HZ)
    aud_f = audio_feature(pcm)
    aud_f = aud_f * _sustained_mask(aud_f > _activity_threshold(aud_f), FEATURE_HZ)
    trans = find_offset(
        rpm_f,
        aud_f,
        lag_window_s=transient_window,
        audio_time_offset_s=2048 / (2 * AUDIO_RATE),
    )
    if trans.confidence > 0.0 and (best is None or trans.confidence >= best.confidence):
        trans.confidence = max(trans.confidence, best.confidence if best else 0.0)
        best = trans
    if best is None:
        return SyncResult(lag_s=0.0, confidence=0.0)

    fine = refine_offset(df, pcm, best.lag_s)
    if fine.confidence > 0.0 and abs(fine.lag_s - best.lag_s) <= FINE_LAG_WINDOW_S:
        return SyncResult(
            lag_s=fine.lag_s,
            confidence=max(fine.confidence, best.confidence),
            method="fine",
        )
    return best


def sync_day(
    cfg,
    manifest,
    day_dir: Path,
    force: bool = False,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[str]:
    """Audio-sync every clip of a day against the day's full telemetry frame.

    Session assignment is NOT required first: the sync searches the whole
    day, which also makes it immune to a wrongly-set device clock. Clips
    whose correlation is inconclusive get seeded from the day's median
    camera-clock drift once at least one clip synced confidently — DJI clock
    drift is stable within a track day.
    """
    from .library import SyncInfo
    from .telemetry import load_day_frame

    report: list[str] = []
    try:
        day = load_day_frame(day_dir, manifest)
    except ValueError:
        return ["no telemetry for this day; nothing to sync"]

    for clip in manifest.videos:
        if clip.sync is not None and not force:
            continue
        try:
            result = estimate_offset(day.df, extract_audio_pcm(day_dir / clip.file))
        except (RuntimeError, ValueError) as exc:
            report.append(f"! {clip.file}: {exc}")
            continue

        video_start = day.start_utc + timedelta(seconds=result.lag_s)
        if result.confidence >= min_confidence:
            clip.sync = SyncInfo(
                video_start_utc=video_start,
                confidence=result.confidence,
                method=f"audio-{result.method}",
            )
            report.append(
                f"+ {clip.file}: {result.method} sync ok "
                f"(confidence {result.confidence:.2f}, video starts {video_start.isoformat()})"
            )
        else:
            report.append(
                f"? {clip.file}: low confidence {result.confidence:.2f}, left unsynced"
            )

    # Seed the leftovers from confidently synced clips of the same day.
    solved = [c for c in manifest.videos if c.sync and c.sync.method.startswith("audio-")]
    if solved:
        drifts = sorted(
            (c.sync.video_start_utc - c.start_utc_estimate).total_seconds() for c in solved
        )
        drift = drifts[len(drifts) // 2]
        seed_conf = 0.9 * min(c.sync.confidence for c in solved)
        for clip in manifest.videos:
            if clip.sync is None:
                clip.sync = SyncInfo(
                    video_start_utc=clip.start_utc_estimate + timedelta(seconds=drift),
                    confidence=seed_conf,
                    method="seeded",
                )
                report.append(
                    f"+ {clip.file}: seeded from day drift {drift:+.2f}s "
                    f"(confidence {seed_conf:.2f})"
                )
    return report


