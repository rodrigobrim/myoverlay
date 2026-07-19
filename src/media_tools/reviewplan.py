"""Render-plan model for review Gate 2.

One RenderItem == one rendered output. build_plan() derives the default queue
from the manifest (correlate/join/sync results); the review GUI edits the items
and writes the plan back; `mt render --plan` runs execute_item() per item. The
plan is a sidecar (work/render_plan.json), never session.json - the durable
outcome is the RenderOutput rows execute_item produces.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from .config import Config
from .i18n import strings as i18n_strings
from .library import DayManifest
from .overlay import fmt_laptime
from .telemetry import best_lap, session_laps_derived


class RenderSlice(BaseModel):
    file: str  # day-dir-relative video path
    source_name: str


class RenderItem(BaseModel):
    item_id: str
    session_id: int | None = None
    slices: list[RenderSlice] = []       # ordered join order (reorder/add/remove)
    telemetry_files: list[str] = []      # add/remove telemetry
    start_enabled: bool = False          # checkbox + value (video seconds)
    start_s: float = 0.0
    end_enabled: bool = False
    end_s: float = 0.0
    quality: str = "2k"                  # combo: hd|fhd|2k|4k
    title: str = ""
    description: str = ""
    append_best_lap: bool = True         # checkbox (default checked)
    best_lap_s: float | None = None      # display-only, via the SSOT best_lap
    best_lap: str = "-:--.--"


class RenderPlan(BaseModel):
    date: str  # ISO date
    track: str | None = None
    items: list[RenderItem] = []


def _title_values(cfg: Config, day_dir: Path, manifest: DayManifest, session_id):
    from .publish import _title_context

    ctx = _title_context(day_dir, manifest, session_id, cfg.render.min_lap_s)
    values = {"track": ctx.track, "date": ctx.date, "session": ctx.session,
              "best_lap": ctx.best_lap, "lap": ""}
    return values, ctx


def _base_title(cfg: Config, values: dict) -> str:
    """The default title WITHOUT the best lap, so append_best_lap can add it.

    The best-lap position is marked, then the bracketed clause holding it is
    stripped - language-agnostic for the "... (best lap {best_lap})" templates.
    """
    tmpl = cfg.youtube.title_template or i18n_strings(cfg.language)["title_template"]
    marked = tmpl.format(**{**values, "best_lap": "\x00"})
    base = re.sub(r"\s*\([^)]*\x00[^)]*\)", "", marked)
    if "\x00" in base:  # best lap wasn't parenthesised - just drop the marker
        base = base.replace("\x00", "").rstrip(" -")
    return re.sub(r"\s{2,}", " ", base).strip()


def _default_description(cfg: Config, values: dict) -> str:
    tmpl = cfg.youtube.description_template or i18n_strings(cfg.language)["description_template"]
    return tmpl.format(**values)


def build_plan(cfg: Config, day_dir: Path, manifest: DayManifest) -> RenderPlan:
    """One item per synced clip, defaults taken from correlate/join/sync."""
    items: list[RenderItem] = []
    for clip in manifest.videos:
        if clip.sync is None or clip.sync.confidence < cfg.render.min_sync_confidence:
            continue
        session = next((s for s in manifest.sessions if s.id == clip.session_id), None)
        slices = (
            [RenderSlice(file=seg, source_name=Path(seg).name) for seg in clip.segments]
            if clip.segments
            else [RenderSlice(file=clip.file, source_name=clip.source_name)]
        )
        bl = (
            best_lap(session_laps_derived(day_dir, manifest, session), cfg.render.min_lap_s)
            if session
            else None
        )
        bl_s = (bl[2] - bl[1]) if bl else None
        values, _ = _title_values(cfg, day_dir, manifest, clip.session_id)
        items.append(RenderItem(
            item_id=Path(clip.file).stem,
            session_id=clip.session_id,
            slices=slices,
            telemetry_files=list(session.telemetry_files) if session else [],
            quality=cfg.render.resolution,
            title=_base_title(cfg, values),
            description=_default_description(cfg, values),
            best_lap_s=bl_s,
            best_lap=fmt_laptime(bl_s),
        ))
    return RenderPlan(date=manifest.date.isoformat(), track=manifest.track, items=items)


def plan_path(day_dir: Path) -> Path:
    return day_dir / "work" / "render_plan.json"


def save_plan(day_dir: Path, plan: RenderPlan) -> Path:
    dest = plan_path(day_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(dest)
    return dest


def load_plan(path: Path) -> RenderPlan:
    return RenderPlan.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


def _resolve_clip(manifest: DayManifest, day_dir: Path, item: RenderItem):
    """The VideoClip to render for this item, joining slices first if needed."""
    files = [s.file for s in item.slices]
    for clip in manifest.videos:
        if len(files) == 1 and clip.file == files[0]:
            return clip
        if clip.segments and list(clip.segments) == files:
            return clip
    if len(files) > 1:
        from .videojoin import join_day

        want = {s.source_name for s in item.slices}
        join_day(manifest, day_dir, only_substrings=[s.source_name for s in item.slices])
        for clip in manifest.videos:
            if clip.segments and want <= {Path(x).name for x in clip.segments}:
                return clip
    return None


def execute_item(cfg: Config, manifest: DayManifest, day_dir: Path, day, item: RenderItem, save=None) -> str:
    """Render one item: join (if multi-slice) -> render_clip with the item's
    window/quality/title. Returns a one-line report."""
    from .render import render_clip

    clip = _resolve_clip(manifest, day_dir, item)
    if clip is None:
        return f"! {item.item_id}: no matching clip to render (join failed?)"
    cfg.render.resolution = item.quality
    dest = render_clip(
        cfg, day_dir, manifest, clip, day,
        window_start_s=item.start_s if item.start_enabled else 0.0,
        window_end_s=item.end_s if item.end_enabled else 0.0,
        title=item.title or None,
        description=item.description or None,
        append_best_lap=item.append_best_lap,
        force=True,
    )
    if save is not None:
        save()
    return f"+ {item.item_id} -> {dest.name}"
