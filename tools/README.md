# tools/

Development and diagnostic scripts (run from the repo root with `uv run python tools/<name>.py`).

## Maintained

- **sync_eval.py** — sync accuracy harness: sweeps the 3-stage estimator over
  synthetic sessions with known offsets (target ≤5 ms) plus the pit rev-kick
  guardrail scenario. The pytest suite asserts the same bounds; this prints
  the measured numbers.
- **proof_slices.py** — renders 15 s overlay test slices at given video times
  (`uv run python tools/proof_slices.py 2026-07-13 0 315`) into
  `<library>/<day>/out/tests/`. Cheap way to verify overlay changes (~30 s
  per slice) instead of a full re-render.

## research/

Frozen experiment scripts from real-data debugging (kept for reference;
several expect a cached `pcm_cache.npy` next to them or absolute paths from
the original session):

- `feature_lab*.py`, `envelope_lab.py`, `timeline_lab.py`, `tune_fine.py`,
  `eval_rmsonly.py` — audio-feature experiments that led to the envelope
  sync stage, rank normalization, and the harmonic-peak feature.
- `debug_sync.py`, `debug_feat.py` — synthetic-sync correlation debugging.
- `recover_clock.py` — derived the MyChron clock-error anchor by audio
  evidence (device clock was ~21 years wrong).
- `check_twins.py` — verified .xrz files are compressed twins of .xrk.
- `boundary_slices.py` — original telemetry-coverage boundary test.

## oneoff/

- `migrate_2047_day.py` — moved the misdated 2047-10-29 library day into
  2026-07-13 after the clock correction (historical, day-specific).
