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

Only RS3 versions validated against real UI snapshots are ever driven.
Current list: `3.83.39`.

- Runtime: `Rs3Config.validated_versions` (config.py) - checked against the
  running window title and the exe file version; mismatch = refuse + clear
  message.
- Wizard: `VALIDATED_RS3_VERSIONS` in `packaging/msi/detect_deps.js` - an
  unsupported version blocks setup with the version named on the deps page.
- `tests/test_rs3_dialogs.py` asserts both lists match.

## WiFi connect flow (learned from real screenshots, NOT yet automated)

Screenshots from a 3.83.39 install (Win11 machine) show the manual flow:

1. No device: left pane shows "Connected Devices / No device connected".
2. An "Available devices" dialog (title exactly that) lists under
   "AiM devices" rows like `AiM-MYC6-021763-MyChron6 Brim`, separate from
   "Other networks". Buttons: Connect / Rescan / View: all / Settings... /
   Exit. Tooltip on Connect: "Connect to the selected device or network".
3. Select the AiM row, click Connect -> device appears in Connected Devices
   as "MyChron6 Brim" (WiFi icon), device page title "MyChron6 Brim (WiFi)".
4. When connected, the same dialog shows the row suffixed "- connected" and
   the Connect button becomes Disconnect.

Desired automation (requested, pending):

- USB device present -> proceed as today.
- No USB -> open Available devices, Rescan, pick the AiM device row, Connect,
  wait for it to appear in Connected Devices, then download as usual, then
  Disconnect (only if the automation itself connected).
- More than one AiM device -> ask the user to choose.
- No device at all -> stop and ask the user to make a connection available;
  resume after confirmation.
- The WiFi error state "Can't communicate, try reconnecting or restarting
  your device" (red text on the device page) must be detected and reported.

### Blocker: how to OPEN the Available devices dialog

Unknown. On the 3.83.39 home view (no device), UIA enumeration of the main
window returns NOTHING - not even nameless controls via `descendants()` -
so the opener cannot be found by name from that state. In the screenshots
the 6th top-left toolbar icon appears pressed while the dialog is open
(the same slot shows the "AiM Devices" nav label when a device is
connected). Next step: on a machine with working WiFi, probe UIA with the
dialog OPEN (it is a titled window - `Available devices` - so it can be
found directly) and check whether the opener icon exposes a name/automation
id from other states, or whether the dialog auto-opens on some action.

## Testing without a rebuild

Point the installed exe at a checkout of this branch:

    $env:MYOVERLAY_BRANCH = "worktree-rs3-download-breaking-mychron"
    myoverlay ingest --rs3-only

(or `MYOVERLAY_REPO=<path to a clone on this branch>` +
`MYOVERLAY_NO_UPDATE=1`). The launcher loads the pipeline from the repo, so
code changes apply without rebuilding the exe.

## Hardware/env facts that keep biting

- The real RS3 binary is `C:\AIM_SPORT\RaceStudio3\64\AiMRS3-64-ReleaseU.exe`
  (NOT `RaceStudio3.exe`); `find_rs3_exe()` auto-discovers it.
- Report lines stream only when the caller passes `echo` to
  `trigger_rs3_download` (wired for the CLI pending); without it, long runs
  look like a silent hang.
- VirtualBox (VBoxSVC/VirtualBox.exe) shadow-claims the MyChron USB when
  running - stop it before USB work.
- "No downloadable file(s) from device" is the NORMAL all-downloaded state.
