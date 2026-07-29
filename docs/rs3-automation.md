# RS3 automation - state, findings, and pending work

Working notes for the Race Studio 3 GUI automation
(`src/media_tools/ingest/rs3.py`), kept current so work can continue from any
machine. Branch: `worktree-rs3-download-breaking-mychron`.

## Root cause of the MyChron getting wedged (fixed)

While a download ran, `_confirm_dialogs` scanned the ENTIRE main RS3 window
(not just popup dialogs) and clicked any enabled button named
ok/yes/start/continue/proceed. RS3's device tabs (WiFi and Properties,
Settings, Firmware) are full of such buttons and they send commands straight
to the MyChron. A run report line `answered dialog with ''` was the tell: it
clicked a button it could not even name. Symptoms: device unresponsive over
USB, corrupted WiFi config (recovery required USB + rewriting WiFi
name/password), same look as the earlier VirtualBox USB conflict.

Fixes on this branch:

- Dialog scans only touch true dialog windows; the main window is never a
  click surface. Empty-named buttons are never clicked.
- Firmware/WiFi topics are always declined; buttons naming firmware/firmup
  are forbidden outright.
- Reports now include the dialog's text: `answered dialog [<text>] with 'OK'`.
- An already-running transfer (toolbar shows Cancel instead of Data Download)
  is detected and waited out, never driven over.
- UIA polling during a transfer is coarse (20 s / 8 s): each poll is a
  synchronous COM call into RS3's UI thread, which also services the device
  link.

## Version gate (wizard + runtime)

Only RS3 versions validated against real UI snapshots are ever driven, and
the match is EXACT - `3.83.40` is refused just like `3.72.27`. An unknown
version is refused too. Current list: `3.83.39`. Note RS3 auto-updates, so
a new AiM release turns the automation off with a clear message until
someone validates it and adds the version.

- Runtime: `Rs3Config.validated_versions` (config.py) - checked against the
  running window title and the exe file version; mismatch = refuse + clear
  message.

### Where the version comes from - ONE source

Windows' installed-programs record (the Uninstall keys that Add/Remove
Programs and PowerShell `Get-Package` read) is the SINGLE source of truth
for "which RS3 is installed", for the CLI and the MSI wizard alike. AiM
registers RS3 twice (MSI product code + a plain `RaceStudio 3 <version>`
key), both carrying `DisplayVersion` and `InstallLocation`. Windows writes
four parts (`3.83.39.0`); both sides trim to the three-part form the
validated list uses.

- CLI: `rs3_registry_entries()` / `rs3_installed_version()` via stdlib
  `winreg`. Both gates (before launching, and when attaching to an
  already-running RS3) validate this one number.
- Wizard: `detectRs3FromRegistry()` in `packaging/msi/detect_deps.js`,
  reading the same three key paths.
- `tests/test_rs3_registry.py` asserts the two sides share key paths,
  hives and normalisation, and that neither grows a second detection path.

Removed on purpose (each was a way for the two to disagree): the exe's
file-version metadata, the wizard's MSI product enumeration, and its
`C:\AIM_SPORT` folder-exists check. A hand-copied install Windows has no
record of therefore counts as ABSENT in the wizard - which is honest, since
the CLI would refuse to drive it anyway.

The window title is not a version source either; it is only compared
against the registry to notice that the window we attached to belongs to a
different install (portable copy, second install), which is refused.

`rs3.exe_path` (config.toml) still decides WHICH BINARY runs - that is a
separate question from which version is installed; the recorded
`InstallLocation` is used to find it when it is not in the default place.

`winget` is not used: it demands accepting the msstore source agreements
before listing anything, and reads this same registry data anyway.

Gotcha for the wizard: JScript cannot receive WMI `[out]` parameters from a
plain `reg.EnumKey(...)` call - it gets the bare return code, so every hive
looks empty and RS3 reads as missing on a machine that has it.
`Methods_.Item(...).InParameters.SpawnInstance_()` + `ExecMethod` is the
pattern that works, and the returned SAFEARRAY only iterates through
`VBArray`. Verify changes with
`cscript //nologo` against the real registry, not just unit tests.
- Wizard: `VALIDATED_RS3_VERSIONS` in `packaging/msi/detect_deps.js` - an
  unsupported version blocks setup with the version named on the deps page.
- `tests/test_rs3_dialogs.py` asserts both lists match.

## WiFi connect flow (AUTOMATED - `_connect_over_wifi` in rs3.py)

Implemented and validated against the live 3.83.39 UI + a real MyChron6.
Behaviour: no USB device -> open Available devices, filter to AiM devices,
one device -> connect; several -> ask which (via the CLI `ask` callback);
none -> ask the user to turn the MyChron on, offer a rescan. Every path
that connects also disconnects afterwards (RS3 holding the MyChron's WiFi
means the PC has NO INTERNET - the logger's AP replaces the house network),
and the "Can't communicate" error state is detected by OCR and aborts the
download with a disconnect.

What the live probing established (2026-07-28):

- The old "UIA sees nothing" blocker was wrong: the home view exposes the
  toolbar as nameless Buttons with numeric automation_ids (2506..2521 on
  this build). Their ONLY identification is their tooltip - hover and read.
  Left: Preferences, Configurations, Analysis, Tracks, Custom Sensors, CAN
  Protocols, Devices. Right: user login, **WiFi (2519 - the opener)**, Web
  Updates, AiM Website.
- Clicking the WiFi icon opens the "Available devices" dialog: a child
  Window of the MAIN window (not reliably a desktop top-level). Named
  buttons: Connect (disabled until a row is selected) / Rescan / View: all /
  Settings... / Exit, plus an Edit search box.
- The device/network rows are OWNER-DRAWN: no UIA text, no children, and
  the underlying ListBox returns garbage for LB_GETTEXT (items are
  pointers). Section headers ("AiM devices", "Other networks") are ~33px,
  selectable entries ~40px - height is the only structural difference.
  Scrolled-out rows report zero-size rects.
- Typing the `AiM-` SSID prefix into the search box filters the list to
  AiM devices only - that is how device rows are isolated without reading
  labels. Row NAMES (for the "which one?" prompt) come from OCR of the row
  bitmap via Windows' built-in OCR engine (PowerShell WinRT, no extra
  dependency).
- "View: all" is a CYCLING filter (all -> only mine -> ...), NOT a menu.
  Cycling it hides unmarked devices and pops an info dialog ("You have not
  marked any device as 'mine' yet"). Never click it. Never touch
  "Settings..." either.
- The connected device pane ("Connected Devices", top-left) is ALSO
  owner-drawn: the rows exist as whitespace-named ListItems. A WiFi-linked
  device is detected by OCR of that pane region.
- **The handshake is fragile**: after clicking Connect, RS3 negotiates with
  the logger on the same thread that runs its UI. Typing/clicking ANYTHING
  during those seconds (e.g. clearing the search box) starves the link and
  leaves the device in the red "Can't communicate, try reconnecting or
  restarting your device" state. The flow waits ~25s doing NOTHING after
  Connect, then polls coarsely (20s); the dialog is only tidied up after
  the link settles. This mirrors the coarse-polling rule for transfers.
- After a failed handshake RS3 may keep a stale "connected" entry with the
  red banner; once the AP link drops, RS3 clears it by itself and Windows
  falls back to the house network.

## Testing without a rebuild

Point the installed exe at a checkout of this branch:

    $env:MYOVERLAY_BRANCH = "worktree-rs3-download-breaking-mychron"
    myoverlay ingest --rs3-only

(or `MYOVERLAY_REPO=<path to a clone on this branch>` +
`MYOVERLAY_NO_UPDATE=1`). The launcher loads the pipeline from the repo, so
code changes apply without rebuilding the exe. Note: `MYOVERLAY_REPO` must
point at a REGULAR clone - a `.claude/worktrees` worktree has a `.git`
*file*, which the launcher's `(repo / ".git").is_dir()` first-run check does
not recognise, so it tries to clone over it and errors out.

## Hardware/env facts that keep biting

- The real RS3 binary is `C:\AIM_SPORT\RaceStudio3\64\AiMRS3-64-ReleaseU.exe`
  (NOT `RaceStudio3.exe`); `find_rs3_exe()` auto-discovers it.
- Report lines stream only when the caller passes `echo` to
  `trigger_rs3_download` (wired for the CLI pending); without it, long runs
  look like a silent hang.
- VirtualBox (VBoxSVC/VirtualBox.exe) shadow-claims the MyChron USB when
  running - stop it before USB work.
- "No downloadable file(s) from device" is the NORMAL all-downloaded state.
