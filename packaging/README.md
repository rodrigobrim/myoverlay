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

## MSI installer (maintainer)

```
powershell -File packaging\build_exe.ps1      # payload (if not already built)
powershell -File packaging\msi\build_msi.ps1  # -> dist\myoverlay-setup.msi
```

`build_msi.ps1` downloads the WiX 3.14 binaries into `packaging\vendor\wix`
(cached), harvests `dist\myoverlay\` and links `dist\myoverlay-setup.msi`.

The setup wizard asks for:

1. **Video language** (en default, pt/es/ja/ar/fr/it/ru) — applies to the
   delta overlay labels and the YouTube title/description defaults only;
   config and CLI stay English.
2. **Google Cloud SDK** — the official Windows installer
   (`GoogleCloudSDKInstaller.exe`, bundled into the MSI at build time) is
   launched and must complete before the wizard continues (an
   "already installed" checkbox skips it).
3. **Install destination folder** — where the app (and all bundled tools:
   ffmpeg, git, the Google Cloud SDK) is installed. Defaults to
   `Program Files\MyOverlay`; a Browse button and path validation are the
   stock WiX folder dialogs.
4. **Start Menu / Desktop shortcuts** (they launch `myoverlay run`).
5. **Google API configuration** — step-by-step Cloud Console instructions,
   a Validate button that checks the client_secret JSON is a Desktop-app
   OAuth client, and a Skip button that warns YouTube publishing will be
   unavailable.
6. **Default output resolution** (hd/fhd/2k/4k combo, default 2k).

The choices are written to `install_settings.yaml` next to the installed
exe; the launcher applies them when it creates `config.toml` on first run
(language, resolution, and it copies the validated client secret to the
repo as `client_secret.json`). The chosen destination is recorded as
`[tools] install_dir` in `config.toml` (refreshed on every launch), so the
pipeline finds the bundled ffmpeg and Google Cloud SDK by full path.

**Uninstall** (Programs and Features > Change > Remove — the Uninstall
button is hidden so the options page is always shown) removes everything
the software installed: app files, shortcuts, `install_settings.yaml`, and
`%LOCALAPPDATA%\myoverlay` (pipeline clone, config.toml, Google
credentials). A checkbox on the remove-options page additionally
uninstalls the Google Cloud SDK (unchecked by default). The media library
(`library_root` — videos/telemetry) and Race Studio 3 data are never
touched.
