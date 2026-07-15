"""Move the misdated 2047-10-29 library copies into 2026-07-13 (no deletion:
files are moved, the stale manifest is emptied of telemetry entries)."""
import shutil
from datetime import date
from pathlib import Path

from media_tools.config import load_config
from media_tools.library import Library
from media_tools.ingest.mychron import ingest_mychron

cfg = load_config()
lib = Library(cfg.library_root)

wrong_dir = lib.day_dir(date(2047, 10, 29))
target_dir = lib.ensure_day(date(2026, 7, 13))

moved = 0
for f in sorted((wrong_dir / "raw" / "telemetry").glob("*")):
    dest = target_dir / "raw" / "telemetry" / f.name
    if not dest.exists():
        shutil.move(str(f), str(dest))
        moved += 1
print(f"moved {moved} file(s) to {target_dir}")

# Empty the stale manifest (its day was never real).
wrong_manifest = lib.load_day(date(2047, 10, 29))
wrong_manifest.telemetry = []
wrong_manifest.sessions = []
lib.save_day(wrong_manifest)

# Rebuild 2026-07-13 telemetry entries by re-parsing the moved .xrk files
# with the corrected clock (ingest scans the library day folder as a source;
# identity checks keep it idempotent).
m = lib.load_day(date(2026, 7, 13))
m.telemetry = []
m.sessions = []
lib.save_day(m)
report = ingest_mychron(cfg, extra_sources=[target_dir / "raw" / "telemetry"])
print("re-parse:", len(report.copied), "session(s),", len(report.errors), "error(s)")
for line in report.errors:
    print("  !", line)

m = lib.load_day(date(2026, 7, 13))
for t in m.telemetry:
    print(f"  {t.source_name}: {t.start_utc} .. {t.end_utc} laps={len(t.laps)} venue={t.venue!r}")
