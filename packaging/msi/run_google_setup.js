// MSI immediate custom action (UI sequence, After ExecuteAction): run
// `myoverlay.exe google-setup` in a visible console while keeping the wizard's
// Cancel button ALIVE.
//
// The old ExeCommand custom action was synchronous: the whole UI thread sat
// inside the CA until google-setup exited, so clicking Cancel during the
// sign-in phase did nothing - and even after the wizard died, the console and
// the sign-in browser kept running and the freshly installed files stayed.
//
// This version launches the exe asynchronously and polls it once a second.
// Each iteration sends a message to the installer UI (Session.Message), which
// both pumps the wizard and returns msiMessageStatusCancel once the user has
// clicked Cancel and confirmed. On cancel we:
//   1. kill the google-setup console and its whole process tree (taskkill /t:
//      the Playwright browser and the plain sign-in browser are children),
//   2. sweep any straggler chrome/msedge bound to the dedicated
//      gcp_browser_profile (belt and braces - the profile is ours alone),
//   3. schedule `msiexec /x <ProductCode> /passive`: the install committed at
//      ExecuteAction, so a real uninstall is the only way to remove the files.
//      It also runs RemoveAppData (wipes %LOCALAPPDATA%\myoverlay: launcher
//      clone, config, browser profile). REMOVE_GCLOUD is NOT passed - a
//      pre-existing machine Google Cloud SDK must survive; the bundled SDK
//      feature is removed by the uninstall itself.
// Anything already done on the Google side (project, consent screen, OAuth
// client) is deliberately left in place.
//
// Returns msiDoActionStatus values: 1 = success, 2 = user exit (makes the
// wizard close through the UserExit path instead of showing the exit page).

function _sleep1s(sh) {
    // MSI JScript has no sleep primitive; a hidden ping waits ~1 s.
    sh.Run("ping -n 2 127.0.0.1", 0, true);
}

function _findSetupPid(wmi) {
    var q = wmi.ExecQuery(
        "select ProcessId, CommandLine from Win32_Process where Name='myoverlay.exe'");
    for (var e = new Enumerator(q); !e.atEnd(); e.moveNext()) {
        var cl = "" + e.item().CommandLine;
        if (cl.indexOf("google-setup") !== -1) return e.item().ProcessId;
    }
    return 0;
}

function _isAlive(wmi, pid) {
    var q = wmi.ExecQuery(
        "select ProcessId from Win32_Process where ProcessId=" + pid);
    return !(new Enumerator(q).atEnd());
}

function _killProfileBrowsers(wmi, sh) {
    // google-setup drives the sign-in through a dedicated browser profile
    // directory named gcp_browser_profile; any chrome/msedge carrying it on
    // the command line is ours to kill.
    var q = wmi.ExecQuery(
        "select ProcessId, CommandLine from Win32_Process "
        + "where Name='chrome.exe' or Name='msedge.exe'");
    for (var e = new Enumerator(q); !e.atEnd(); e.moveNext()) {
        var cl = "" + e.item().CommandLine;
        if (cl.indexOf("gcp_browser_profile") !== -1) {
            sh.Run("taskkill /pid " + e.item().ProcessId + " /t /f", 0, true);
        }
    }
}

function _cancelCleanup(wmi, sh, pid) {
    try { if (pid) sh.Run("taskkill /pid " + pid + " /t /f", 0, true); } catch (e1) {}
    try { _killProfileBrowsers(wmi, sh); } catch (e2) {}
    try {
        // Uninstall what ExecuteAction just committed. Delayed a few seconds
        // (hidden ping) so this wizard's session is on its way out first;
        // /passive shows only a progress bar + the UAC consent it needs.
        sh.Run('cmd /c "ping -n 4 127.0.0.1 >nul & msiexec /x '
            + Session.Property("ProductCode") + ' /passive"', 0, false);
    } catch (e3) {}
}

function RunGoogleSetup() {
    var CANCEL = 2;                        // msiMessageStatusCancel
    var MSG_ACTIONDATA = 0x09000000;       // INSTALLMESSAGE_ACTIONDATA
    try {
        var sh = new ActiveXObject("WScript.Shell");
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        var folder = Session.Property("INSTALLFOLDER");
        var exe = folder + "myoverlay.exe";
        if (!fso.FileExists(exe)) return 1;
        try { sh.CurrentDirectory = folder; } catch (e0) {}
        sh.Run('"' + exe + '" google-setup', 1, false);

        var wmi = GetObject(
            "winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\cimv2");
        var rec = Session.Installer.CreateRecord(1);

        // Give the process up to ~20 s to appear (onedir exes unpack slowly
        // on cold caches), honoring Cancel while we wait.
        var pid = 0;
        for (var tries = 0; tries < 20 && pid === 0; tries++) {
            if (Session.Message(MSG_ACTIONDATA, rec) === CANCEL) {
                _cancelCleanup(wmi, sh, _findSetupPid(wmi));
                return 2;
            }
            _sleep1s(sh);
            pid = _findSetupPid(wmi);
        }
        if (pid === 0) return 1; // never showed up - keep old lenient behavior

        while (_isAlive(wmi, pid)) {
            if (Session.Message(MSG_ACTIONDATA, rec) === CANCEL) {
                _cancelCleanup(wmi, sh, pid);
                return 2;
            }
            _sleep1s(sh);
        }
    } catch (e) {
        // Never fail the install over the google step (mirrors the old
        // Return="ignore" behavior).
    }
    return 1;
}
