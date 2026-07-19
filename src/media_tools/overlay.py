"""Karting overlay frame renderer (PIL, RGBA frames composited by ffmpeg).

Reference-style layout (shadow-cast text/lines, no backing panels):
  top-left      : track map with position dot
  top-center    : speed delta + time delta tick scales with big values
  top-right     : Atual / Anterior / Melhor lap times
  bottom-left   : G-force circle (dashed crosshair, 1G/2G rings)
  bottom-right  : analog speedometer with digital readout

Static artwork (map, tick scales, gauge face, G rings) is rendered once and
cached; per-frame work only draws the moving parts, which keeps 60 fps
overlay rendering affordable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WHITE = (255, 255, 255, 255)
DIM = (255, 255, 255, 140)
PANEL = (0, 0, 0, 110)
ACCENT = (255, 70, 40, 255)
RED = (220, 30, 30, 255)
GREEN = (60, 200, 60, 255)
SHADOW = (0, 0, 0, 160)
TRACK = (255, 255, 255, 180)

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


@dataclass
class FrameValues:
    t_video_s: float
    speed_kmh: float | None = None
    rpm: float | None = None
    water_temp: float | None = None
    lap_num: int | None = None
    lap_time_s: float | None = None
    best_lap_s: float | None = None
    pos_frac: tuple[float, float] | None = None  # (x, y) in [0,1] track-map space
    g_lat: float | None = None  # lateral acceleration, g (positive = right)
    g_lon: float | None = None  # inline acceleration, g (positive = accelerating)
    steering_deg: float | None = None
    # Most recent completed laps, oldest first: (lap_num, duration_s, valid).
    # Invalid laps (unrealistically fast = cut track / timing glitch) never
    # count as best or reference.
    recent_laps: list[tuple[int, float, bool]] = field(default_factory=list)
    # Previous completed lap (num, duration, valid) and the best lap's number.
    prev_lap: tuple[int, float, bool] | None = None
    best_lap_num: int | None = None
    # Live deltas vs the best valid lap completed before this one, at equal
    # track distance: time (negative = faster) and speed (positive = faster).
    delta_s: float | None = None
    speed_delta_kmh: float | None = None


def fmt_laptime(seconds: float | None) -> str:
    if seconds is None:
        return "-:--.--"
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:05.2f}"


def delta_bar(
    value: float, span: float, step: float, positive_is_good: bool
) -> tuple[float, float, bool, bool]:
    """Geometry of a center-anchored delta bar.

    The bar grows from the scale's midpoint: to the RIGHT (green) when the
    value is in the driver's favor, to the LEFT (red) when against. Only the
    BAR saturates at +/-`span`; the returned value is quantized to `step`
    but NOT clamped - the number always reports the true delta.

    Returns (quantized_value, length_frac 0..1, grows_right, good).
    """
    q = round(value / step) * step + 0.0  # +0.0 normalizes -0.0
    good = q >= 0 if positive_is_good else q <= 0
    frac = min(1.0, abs(q) / span)
    return q, frac, good, good


class TrackProjection:
    """GPS -> unit-box projection (equirectangular, aspect-preserving,
    north up, centered).

    Built from clean fixes only; `project` passes NaN through, so pit-gap
    rows simply produce no position dot instead of poisoning the transform.
    """

    def __init__(self, lat: np.ndarray, lon: np.ndarray):
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        ok = np.isfinite(lat) & np.isfinite(lon)
        if ok.sum() < 2:
            raise ValueError("need at least two finite GPS fixes")
        lat, lon = lat[ok], lon[ok]
        self.cos_lat = np.cos(np.radians(lat.mean()))
        x = lon * self.cos_lat
        self.x_min = float(x.min())
        self.y_min = float(lat.min())
        self.span = max(float(x.max() - self.x_min), float(lat.max() - self.y_min), 1e-12)
        self.x_frac_max = float(x.max() - self.x_min) / self.span
        self.y_frac_max = float(lat.max() - self.y_min) / self.span

    def project(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        x = (lon * self.cos_lat - self.x_min) / self.span
        y = (lat - self.y_min) / self.span
        y = self.y_frac_max - y  # north up
        return np.stack(
            [x + (1 - self.x_frac_max) / 2, y + (1 - self.y_frac_max) / 2], axis=1
        )


def track_outline_frac(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Project GPS to a unit box; see TrackProjection."""
    proj = TrackProjection(lat, lon)
    pts = proj.project(lat, lon)
    return pts[np.isfinite(pts).all(axis=1)]


class OverlayRenderer:
    def __init__(
        self,
        width: int,
        height: int,
        track_frac: np.ndarray | None = None,
        max_rpm: int = 16000,
        font_path: Path | None = None,
        max_speed_kmh: float = 80.0,
        channels: set[str] | None = None,
    ):
        # channels: which telemetry channels exist for this DAY ("speed",
        # "g", "steering"). When given, those widgets' chrome is drawn from
        # the first frame with explicit no-data placeholders, and values
        # appear the instant coverage begins. When None (legacy/tests), each
        # widget shows only on frames that carry its value.
        self.channels = channels
        self.w = width
        self.h = height
        self.s = height / 1080.0
        self.max_rpm = max_rpm
        self.max_speed_kmh = max_speed_kmh
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._font_path = self._resolve_font(font_path)
        self._map_size = int(240 * self.s)
        self._map_base = self._build_map(track_frac) if track_frac is not None else None
        self._static_cache: dict[tuple, Image.Image] = {}

        # fixed widget geometry
        s = self.s
        self.pad = int(28 * s)
        self.ruler_w = int(400 * s)
        self.speed_ruler_x = int(0.30 * width)
        self.time_ruler_x = int(0.53 * width)
        gauge_r = int(210 * s)
        self.gauge_cx = width - self.pad - gauge_r
        self.gauge_cy = height - self.pad - gauge_r + int(30 * s)
        self.gauge_r = gauge_r
        # G-force circle now sits in the bottom-left corner (it replaced the
        # old steering-wheel widget).
        g_size = int(190 * s)
        self.g_r = g_size // 2
        self.g_cx = self.pad + self.g_r
        self.g_cy = height - self.pad - int(44 * s) - g_size // 2

    @staticmethod
    def _resolve_font(font_path: Path | None) -> str | None:
        candidates = ([str(font_path)] if font_path else []) + _FONT_CANDIDATES
        for cand in candidates:
            if Path(cand).is_file():
                return cand
        return None

    def _font(self, size: int) -> ImageFont.ImageFont:
        size = max(10, int(size * self.s))
        if size not in self._fonts:
            if self._font_path:
                self._fonts[size] = ImageFont.truetype(self._font_path, size)
            else:
                self._fonts[size] = ImageFont.load_default(size)
        return self._fonts[size]

    def _build_map(self, track_frac: np.ndarray) -> Image.Image:
        size = self._map_size
        margin = int(size * 0.08)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pts = [
            (margin + p[0] * (size - 2 * margin), margin + p[1] * (size - 2 * margin))
            for p in track_frac
        ]
        if len(pts) >= 2:
            shadow_pts = [(px + 2, py + 2) for px, py in pts]
            draw.line(shadow_pts, fill=(0, 0, 0, 140), width=max(2, int(3 * self.s)), joint="curve")
            draw.line(pts, fill=WHITE, width=max(2, int(3 * self.s)), joint="curve")
        self._map_pts_transform = (margin, size - 2 * margin)
        return img

    def map_point(self, pos_frac: tuple[float, float]) -> tuple[float, float]:
        margin, span = self._map_pts_transform
        return (margin + pos_frac[0] * span, margin + pos_frac[1] * span)

    def _shadow_text(self, draw, xy, text, font, fill, anchor=None) -> None:
        """White-on-anything text: dark offset shadow keeps it readable
        without a backing panel (matches the reference overlay style)."""
        off = max(1, int(2 * self.s))
        draw.text((xy[0] + off, xy[1] + off), text, font=font, fill=SHADOW, anchor=anchor)
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    def _dashed_line(self, draw, p0, p1, fill, width, dash=8.0, gap=6.0) -> None:
        x0, y0 = p0
        x1, y1 = p1
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1:
            return
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        d = 0.0
        while d < length:
            e = min(d + dash * self.s, length)
            draw.line([x0 + ux * d, y0 + uy * d, x0 + ux * e, y0 + uy * e], fill=fill, width=width)
            d = e + gap * self.s

    def _gauge_angle(self, value: float) -> float:
        frac = max(0.0, min(1.0, value / self.max_speed_kmh))
        return math.radians(210 - 240 * frac)

    # ------------------------------------------------------------------
    # static artwork (drawn once per widget combination, then cached)

    def _ruler_static(self, draw, x: int, y: int, label: str) -> None:
        s = self.s
        width = self.ruler_w
        self._shadow_text(draw, (x, y), label, self._font(30), WHITE)
        line_y = y + int(72 * s)
        draw.line([x + 1, line_y + 1, x + width + 1, line_y + 1], fill=SHADOW, width=max(1, int(2 * s)))
        draw.line([x, line_y, x + width, line_y], fill=WHITE, width=max(1, int(2 * s)))
        n_ticks = 21
        for i in range(n_ticks):
            tx = x + width * i / (n_ticks - 1)
            major = i % 5 == 0
            th = int((16 if major else 9) * s)
            draw.line([tx + 1, line_y + 1, tx + 1, line_y - th + 1], fill=SHADOW, width=max(1, int(2 * s)))
            draw.line([tx, line_y, tx, line_y - th], fill=WHITE, width=max(1, int(2 * s)))

    def _gauge_static(self, draw) -> None:
        s = self.s
        cx, cy, radius = self.gauge_cx, self.gauge_cy, self.gauge_r
        step_minor, step_major = 5, 10
        k = 0
        while k <= self.max_speed_kmh:
            a = self._gauge_angle(k)
            major = k % step_major == 0
            inner = radius - int((30 if major else 18) * s)
            x0 = cx + inner * math.cos(a)
            y0 = cy - inner * math.sin(a)
            x1 = cx + radius * math.cos(a)
            y1 = cy - radius * math.sin(a)
            w = max(2, int((4 if major else 2) * s))
            draw.line([x0 + 2, y0 + 2, x1 + 2, y1 + 2], fill=SHADOW, width=w)
            draw.line([x0, y0, x1, y1], fill=WHITE, width=w)
            if major:
                lx = cx + (radius - int(58 * s)) * math.cos(a)
                ly = cy - (radius - int(58 * s)) * math.sin(a)
                self._shadow_text(draw, (lx, ly), str(int(k)), self._font(26), WHITE, anchor="mm")
            k += step_minor
        self._shadow_text(
            draw, (cx, cy - int(radius * 0.42)), "km/h", self._font(28), WHITE, anchor="mm"
        )

    def _g_static(self, draw) -> None:
        s = self.s
        cx, cy, r2g = self.g_cx, self.g_cy, self.g_r
        for frac, label in ((0.5, "1G"), (1.0, "2G")):
            rr = r2g * frac
            draw.ellipse(
                [cx - rr + 2, cy - rr + 2, cx + rr + 2, cy + rr + 2],
                outline=SHADOW,
                width=max(1, int(2 * s)),
            )
            draw.ellipse(
                [cx - rr, cy - rr, cx + rr, cy + rr],
                outline=(255, 255, 255, 200),
                width=max(1, int(2 * s)),
            )
            self._shadow_text(
                draw, (cx + int(10 * s), cy - rr + int(4 * s)), label, self._font(22), WHITE
            )
        dash_color = (255, 255, 255, 170)
        self._dashed_line(draw, (cx - r2g, cy), (cx + r2g, cy), dash_color, max(1, int(2 * s)))
        self._dashed_line(draw, (cx, cy - r2g), (cx, cy + r2g), dash_color, max(1, int(2 * s)))

    def _static_layer(self, key: tuple) -> Image.Image:
        if key in self._static_cache:
            return self._static_cache[key]
        has_speed, has_g, has_sd, has_td = key
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if self._map_base is not None:
            img.alpha_composite(self._map_base, (self.pad, self.pad))
        if has_sd:
            self._ruler_static(draw, self.speed_ruler_x, self.pad, "Speed delta")
        if has_td:
            self._ruler_static(draw, self.time_ruler_x, self.pad, "Time delta")
        if has_g:
            self._g_static(draw)
        if has_speed:
            self._gauge_static(draw)
        self._static_cache[key] = img
        return img

    # ------------------------------------------------------------------
    # per-frame dynamics

    def _ruler_dynamic(
        self, draw, x, y, value, span, step, fmt, positive_is_good
    ) -> None:
        """Center-anchored delta bar riding on the scale: grows right (green)
        when the delta favors the driver, left (red) when against."""
        s = self.s
        width = self.ruler_w
        line_y = y + int(72 * s)
        cx = x + width / 2
        q, frac, grows_right, good = delta_bar(value, span, step, positive_is_good)
        bar_len = frac * (width / 2)
        bh = int(34 * s)
        if bar_len >= 1:
            x0, x1 = (cx, cx + bar_len) if grows_right else (cx - bar_len, cx)
            color = GREEN if good else RED
            draw.rectangle([x0 + 1, line_y - bh + 1, x1 + 1, line_y + 1], fill=SHADOW)
            draw.rectangle([x0, line_y - bh, x1, line_y], fill=color)
        self._shadow_text(
            draw, (cx, line_y + int(12 * s)), format(q, fmt), self._font(72), WHITE, anchor="ma"
        )

    def render_frame(self, v: FrameValues) -> Image.Image:
        if self.channels is not None:
            chrome_speed = "speed" in self.channels
            chrome_g = "g" in self.channels
        else:
            chrome_speed = v.speed_kmh is not None
            chrome_g = v.g_lat is not None or v.g_lon is not None
        key = (
            chrome_speed,
            chrome_g,
            v.speed_delta_kmh is not None,
            v.delta_s is not None,
        )
        img = self._static_layer(key).copy()
        draw = ImageDraw.Draw(img)
        s = self.s
        pad = self.pad

        # Explicit awaiting state: chrome is up, no channel has data yet
        # (e.g. camera rolling before the logger auto-starts).
        no_data = v.speed_kmh is None and v.pos_frac is None and v.lap_num is None
        if self.channels is not None and no_data:
            self._shadow_text(
                draw,
                (pad + self._map_size / 2, pad + self._map_size + int(14 * s)),
                "sem telemetria",
                self._font(26),
                DIM,
                anchor="ma",
            )

        # --- map position dot ---
        if self._map_base is not None and v.pos_frac is not None:
            cx, cy = self.map_point(v.pos_frac)
            r = max(4, int(9 * s))
            draw.ellipse(
                [pad + cx - r, pad + cy - r, pad + cx + r, pad + cy + r], fill=RED
            )

        # --- delta ruler markers + values ---
        if v.speed_delta_kmh is not None:
            # +/-5 km/h scale, 0.1 km/h bar steps; faster than the ref = green
            # right. The bar moves in 0.1 increments, the number reads whole
            # km/h (never clamped to the scale).
            self._ruler_dynamic(
                draw, self.speed_ruler_x, pad, v.speed_delta_kmh,
                span=5.0, step=0.1, fmt="+.0f", positive_is_good=True,
            )
            if v.speed_kmh is not None:
                self._shadow_text(
                    draw, (self.speed_ruler_x, pad + int(84 * s)), f"{v.speed_kmh:.0f}",
                    self._font(30), WHITE,
                )
                self._shadow_text(
                    draw, (self.speed_ruler_x, pad + int(122 * s)), "km/h", self._font(24), WHITE
                )
        if v.delta_s is not None:
            # +/-1 s scale, 0.01 s steps; ahead of the ref (negative) = green right
            self._ruler_dynamic(
                draw, self.time_ruler_x, pad, v.delta_s,
                span=1.0, step=0.01, fmt="+.2f", positive_is_good=False,
            )

        # --- top-right: Atual / Anterior / Melhor ---
        if v.lap_num is not None:
            right = self.w - pad
            y = pad
            rows = [("Atual", v.lap_num, v.lap_time_s, 56, WHITE)]
            if v.prev_lap is not None:
                p_num, p_dur, p_valid = v.prev_lap
                color = WHITE if p_valid else (255, 110, 110, 255)
                rows.append(("Anterior", p_num, p_dur, 44, color))
            if v.best_lap_s is not None:
                rows.append(("Melhor", v.best_lap_num, v.best_lap_s, 44, WHITE))
            label_x = right - int(300 * s)
            for label, num, seconds, size, color in rows:
                self._shadow_text(draw, (label_x, y), label, self._font(28), WHITE)
                y += int(36 * s)
                if num is not None:
                    self._shadow_text(
                        draw, (label_x, y + int(12 * s)), str(num), self._font(24), WHITE
                    )
                self._shadow_text(
                    draw,
                    (right, y),
                    f"{seconds:.2f}" if seconds is not None and seconds < 60 else fmt_laptime(seconds),
                    self._font(size),
                    color,
                    anchor="ra",
                )
                y += int((size + 20) * s)
            temp_y = y
        else:
            temp_y = pad

        # --- water temp under the lap stack (only with a sensor fitted) ---
        if v.water_temp is not None:
            self._shadow_text(
                draw,
                (self.w - pad, temp_y),
                f"H2O {v.water_temp:.0f}\N{DEGREE SIGN}",
                self._font(30),
                WHITE,
                anchor="ra",
            )

        # --- G-force dot + value (bottom-left; replaced the steering wheel) ---
        if chrome_g:
            cx, cy, r2g = self.g_cx, self.g_cy, self.g_r
            have = v.g_lat is not None or v.g_lon is not None
            if have:
                g_lat = v.g_lat or 0.0
                g_lon = v.g_lon or 0.0
                dot_x = cx + max(-1.0, min(1.0, g_lat / 2.0)) * r2g
                dot_y = cy + max(-1.0, min(1.0, g_lon / 2.0)) * r2g
                r = max(4, int(11 * s))
                draw.ellipse([dot_x - r, dot_y - r, dot_x + r, dot_y + r], fill=RED)
            self._shadow_text(
                draw, (cx, cy + r2g + int(44 * s)),
                f"{(v.g_lat or 0.0):.1f}" if have else "-.-",
                self._font(40), WHITE,
                anchor="ms",
            )

        # --- speedometer needle + digital readout ---
        if chrome_speed:
            cx, cy, radius = self.gauge_cx, self.gauge_cy, self.gauge_r
            have = v.speed_kmh is not None
            a = self._gauge_angle(v.speed_kmh if have else 0.0)
            nx = cx + (radius - int(20 * s)) * math.cos(a)
            ny = cy - (radius - int(20 * s)) * math.sin(a)
            tail = int(26 * s)
            tx = cx - tail * math.cos(a)
            ty = cy + tail * math.sin(a)
            needle = RED if have else (200, 60, 45, 150)
            draw.line([tx + 2, ty + 2, nx + 2, ny + 2], fill=SHADOW, width=max(3, int(7 * s)))
            draw.line([tx, ty, nx, ny], fill=needle, width=max(3, int(7 * s)))
            self._shadow_text(
                draw, (cx, cy + int(24 * s)),
                f"{v.speed_kmh:.0f}" if have else "--",
                self._font(72), WHITE,
                anchor="ma",
            )

        return img
