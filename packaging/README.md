# myoverlay — shareable launcher

A self-contained Windows build of the pipeline for friends: no Python, git,
ffmpeg or any install needed. One folder, one exe.

## For friends (using it)

1. Unzip `myoverlay-win64.zip` anywhere (e.g. `C:\myoverlay`).
2. Open a terminal in that folder and run:

```
myoverlay run                # everything: MyChron download -> ingest -> sync -> correlate -> render
myoverlay run --publish      # ... plus YouTube upload
myoverlay status             # table of every track day
myoverlay ingest             # pull new files from camera/SD + RS3 folder
myoverlay sync 2026-07-13
myoverlay correlate 2026-07-13
myoverlay render 2026-07-13  # --force to re-render
myoverlay publish 2026-07-13 # --dry-run to preview
myoverlay slice 2026-07-13 "25:15-30:37"            # cut only (lands in out\slices\)
myoverlay slice 2026-07-13 "25:15-30:37" --publish  # cut + upload with labeled title
myoverlay slice 2026-07-13 "12:01-14:02" "31:00-33:10"
```

On every start the launcher checks the GitHub repo for new commits and pulls
them, so the pipeline stays current without reinstalling (`--no-update` or
`MYOVERLAY_NO_UPDATE=1` skips the check). The first run creates
`config.toml` and prints its location — edit `library_root` and the Race
Studio 3 data folder before the first real use. For YouTube upload each
person needs their own Google OAuth client (see the main README).

The pipeline working copy lives in `%LOCALAPPDATA%\myoverlay\repo`
(override with `MYOVERLAY_REPO`; point `MYOVERLAY_REPO_URL` at a fork to
test branches).

## Building the zip (maintainer)

```
powershell -File packaging\build_exe.ps1
```

Downloads MinGit + ffmpeg into `packaging\vendor\` (cached), then produces
`dist\myoverlay\` and `dist\myoverlay-win64.zip` with PyInstaller.

**Rebuild needed only when** `pyproject.toml` gains a new dependency — the
launcher imports the pulled source against the bundled packages, so pure
code changes reach friends via git pull, but new packages must be added to
`PIPELINE_PACKAGES` in `myoverlay.spec` and reshipped.
