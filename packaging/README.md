# MyOverlay — shareable launcher

A self-contained Windows build of the pipeline for friends: no Python, git,
ffmpeg or any install needed. One folder, one exe.

## For friends (using it)

1. Unzip `MyOverlay-win64.zip` anywhere (e.g. `C:\MyOverlay`).
2. Open a terminal in that folder and run:

```
MyOverlay run                # everything: MyChron download -> ingest -> sync -> correlate -> render
MyOverlay run --publish      # ... plus YouTube upload
MyOverlay status             # table of every track day
MyOverlay ingest             # pull new files from camera/SD + telemetry folder
MyOverlay sync 2026-07-13
MyOverlay correlate 2026-07-13
MyOverlay render 2026-07-13  # --force to re-render
MyOverlay publish 2026-07-13 # --dry-run to preview
MyOverlay slice 2026-07-13 "25:15-30:37"            # cut only (lands in out\slices\)
MyOverlay slice 2026-07-13 "25:15-30:37" --publish  # cut + upload with labeled title
MyOverlay slice 2026-07-13 "12:01-14:02" "31:00-33:10"
```

On every start the launcher checks the GitHub repo for new commits and pulls
them, so the pipeline stays current without reinstalling (`--no-update` or
`MYOVERLAY_NO_UPDATE=1` skips the check). The first run creates
`config.toml` and prints its location — it works untouched (every option is
commented out at its default); uncomment lines only to change them. For
YouTube upload each person needs their own Google OAuth client (see the
main README).

The app's data lives in `~\myoverlay`: `config.toml`, the media library at
`~\myoverlay\render` (default `library_root`), the Google credentials
(`client_secret.json`, `google-token`) and the pipeline working
copy at `~\myoverlay\repo` (override with `MYOVERLAY_REPO`; point
`MYOVERLAY_REPO_URL` at a fork to test branches). Data from an older
install under `%LOCALAPPDATA%\MyOverlay` is moved there automatically on
first run.

`mt` and `uv` ship beside the exe as well. After an MSI install both are
plain commands in any terminal — the installer already puts the install
directory on the machine PATH — so `mt render 2026-07-13` is `MyOverlay
render 2026-07-13` (the shim forwards to the exe next to it, never to some
other copy on PATH), and the bundled `uv` is there for working on the
pipeline checkout without installing anything. The PATH entry is appended,
so a `uv` the machine already has keeps winning; ours is what a clean
machine resolves.

## Building the zip (maintainer)

```
powershell -File packaging\build_exe.ps1
```

Downloads MinGit + ffmpeg into `packaging\vendor\` (cached), then produces
`dist\MyOverlay\` and `dist\MyOverlay-win64.zip` with PyInstaller.

`path_tools.ps1` then stages `uv.exe` and `mt.cmd` into the payload ROOT
(uv is vendored into `packaging\vendor\uv`, cached like the others). The
root is deliberate: PyInstaller 6 puts every bundled data file under
`_internal\`, and only the payload root becomes the install directory —
the one already on the machine PATH — so a file dropped there is a command
after install with no extra PATH entry to add or to clean up on uninstall.
`build_msi.ps1` runs the same script, so the zip and the installer cannot
disagree, and a payload built before this existed still gets both without a
full exe rebuild.

**Rebuild needed only when** `pyproject.toml` gains a new dependency — the
launcher imports the pulled source against the bundled packages, so pure
code changes reach friends via git pull, but new packages must be added to
`PIPELINE_PACKAGES` in `myoverlay.spec` and reshipped.

## MSI installer (maintainer)

```
powershell -File packaging\build_exe.ps1      # payload (if not already built)
powershell -File packaging\msi\build_msi.ps1  # -> dist\MyOverlay-setup.msi
```

`build_msi.ps1` downloads the WiX 3.14 binaries into `packaging\vendor\wix`
(cached), harvests `dist\MyOverlay\` and links `dist\MyOverlay-setup.msi`.

The Google Cloud SDK is bundled as Google's own archive — one file — and is
expanded on the target machine by the `ExpandGCloud` custom action
(`gcloud_payload.js`), not harvested into the MSI. That is not an
optimisation: the SDK's deepest entry sits 145 characters below its own
root, which pushes a build checkout past Windows' 260-character path limit,
and the WiX 3.14 tools are .NET Framework programs that simply report such a
file as "cannot be found". Installed, the same path is ~189 characters and
perfectly fine. It also keeps ~30k files out of the harvest, which was most
of the build time. The expanded tree has no MSI components, so
`RemoveBundledGCloud` is what deletes it on uninstall and on rollback.

The setup wizard asks for:

1. **Video language** (en default, pt/es/ja/ar/fr/it/ru) — applies to the
   delta overlay labels and the YouTube title/description defaults only;
   config and CLI stay English.
   Its Next also runs a silent **dependency check** (`detect_deps.js`).
   No external software is currently required — MyOverlay talks to the
   MyChron directly (USB/WiFi) and bundles everything else — so the DEPS
   table is empty; the framework stays for any future dependency. Nothing
   is shown when everything is present; a missing blocking dependency
   detours to a dead-end page listing it, whose only options are Back
   (return; Next re-checks) and Cancel (exits setup).
2. **Components** — the bundled software, with the version read from the
   vendored binaries at build time. FFmpeg and Git are required (ticked,
   disabled); the Google Cloud SDK is optional (`INSTALL_GCLOUD`), needed
   only for YouTube upload. The SDK ships as Google's own archive and is
   expanded on this machine by `ExpandGCloud`.
3. **Install destination folder** — where the app (and all bundled tools:
   ffmpeg, git, the Google Cloud SDK) is installed. Defaults to
   `Program Files\MyOverlay`; a Browse button and path validation are the
   stock WiX folder dialogs.
4. **Start Menu / Desktop shortcuts** (they launch `MyOverlay run`).
5. **Default output resolution** (hd/fhd/2k/4k combo, default 2k).

There is no Google API page: the wizard collects nothing about Google and
validates nothing. `MyOverlay google-setup` does the whole job after the
files land (see below), and produces `client_secret.json` itself. The
`GOOGLE_CLIENT_SECRET` / `GOOGLE_SKIPPED` properties are leftovers of the
old manual page — always empty, so the launcher branches reading them are
dead code.

The choices are written to `install_settings.yaml` next to the installed
exe; the launcher applies them when it creates `config.toml` on first run
(language, resolution, and it copies the validated client secret next to it
as `client_secret.json`). A NON-default destination is recorded as
`[tools] install_dir` in `config.toml` (refreshed on every launch; the
default `C:\Program Files\MyOverlay` lives in the code and is never
written), so the pipeline finds the bundled ffmpeg and Google Cloud SDK by
full path.

That recording, like the `MYOVERLAY_*` env vars, comes from the launcher —
which is frozen into the exe and only changes on a rebuild. `media_tools.tools`
therefore also resolves the bundled tools from `sys.executable`'s own
directory, so new pipeline code running under an older exe still finds them.
`build_msi.ps1` refuses to package a `dist\MyOverlay` payload older than
`myoverlay_launcher.py`/`myoverlay.spec` for the same reason.

**Uninstall** (Programs and Features > Change > Remove — the Uninstall
button is hidden so the options page is always shown) removes everything
the application: app files, shortcuts and `install_settings.yaml`. Nothing
in `~\myoverlay` goes with it by default — `config.toml`, the pipeline
clone, the Google credentials and the media library `render\` all stay, so
a reinstall picks up where you left off.

Three checkboxes on the remove-options page, all unticked, each gating its
own custom action:

| Checkbox | Deletes |
|---|---|
| local copy of the repository | `~\myoverlay\repo` and the legacy `%LOCALAPPDATA%\myoverlay\repo` (that directory goes too, but only if it ends up empty) |
| Google sign-in and credentials | `client_secret.json`, `google-token`, `gcp_browser_profile\`, `google-setup.log` |
| Google Cloud SDK | runs the SDK's own uninstaller |

The credentials box warns what it costs: Google reveals an OAuth client
secret only at creation, so deleting `client_secret.json` destroys the only
copy and the next install has to mint a new client and re-authorize
YouTube. `config.toml` and `render\` have no checkbox at all — delete
`~\myoverlay` by hand to hand the machine over to someone else.
