// MSI custom actions for the bundled Google Cloud SDK.
//
// The SDK ships as Google's own archive (one ~110 MB file) and is expanded
// HERE, on the target machine, rather than harvested into the MSI as ~30k
// files. Two reasons, in order of importance:
//
//   1. Path length. The SDK's deepest entry is 145 characters below its own
//      root. Under the install folder that totals ~189 - fine. Under a build
//      checkout it routinely passes Windows' 260-character limit, and the
//      WiX 3.14 tools (.NET Framework, no long-path support) then fail the
//      build outright with "cannot be found".
//   2. Size and time: harvesting 30k files dominated the MSI build.
//
// The expanded tree is NOT tracked by MSI components, so it cannot be
// removed by the installer's own file logic - RemoveBundledGCloud below is
// what deletes it, on uninstall and on rollback.
//
// Deferred actions cannot read properties, so each reads the install folder
// from CustomActionData (set by a matching SetProperty in Product.wxs).

var GCLOUD_ZIP = "google-cloud-cli-windows.zip";
var GCLOUD_DIR = "google-cloud-sdk";

function log(message) {
    // Best-effort line in the MSI log (/l*v); never worth failing over.
    try {
        var rec = Session.Installer.CreateRecord(1);
        rec.StringData(0) = "MyOverlay gcloud: [1]";
        rec.StringData(1) = message;
        Session.Message(0x04000000, rec);
    } catch (e) { }
}

function installFolder() {
    var dir = Session.Property("CustomActionData");
    if (!dir) { return ""; }
    return dir.charAt(dir.length - 1) === "\\" ? dir : dir + "\\";
}

function _sleep1s(sh) {
    // MSI JScript has no sleep primitive; a hidden ping waits ~1 s.
    sh.Run("ping -n 2 127.0.0.1", 0, true);
}

function _tick(text) {
    // Push a live status line into the wizard (the status control subscribes
    // to ActionData; this action's ProgressText declares Template="[1]").
    // Returns 2 (msiMessageStatusCancel) once the user cancels.
    try {
        var rec = Session.Installer.CreateRecord(1);
        rec.StringData(1) = text;
        return Session.Message(0x09000000, rec); // INSTALLMESSAGE_ACTIONDATA
    } catch (e) { return 0; }
}

function _findPidByCmd(wmi, exeName, needle) {
    var q = wmi.ExecQuery(
        "select ProcessId, CommandLine from Win32_Process where Name='" + exeName + "'");
    for (var e = new Enumerator(q); !e.atEnd(); e.moveNext()) {
        var cl = "" + e.item().CommandLine;
        if (cl.indexOf(needle) !== -1) return e.item().ProcessId;
    }
    return 0;
}

function _isAlive(wmi, pid) {
    var q = wmi.ExecQuery(
        "select ProcessId from Win32_Process where ProcessId=" + pid);
    return !(new Enumerator(q).atEnd());
}

// tar.exe has shipped in Windows since 10 1803 and unpacks zip archives
// natively. PowerShell's Expand-Archive is the fallback for older builds;
// it is slower and rolls back destructively on failure, so it is second
// choice, not first.
//
// The extractor runs ASYNCHRONOUSLY and is polled once a second, ticking an
// elapsed-seconds status line into the wizard. A synchronous Run() here used
// to freeze the status display for the whole multi-minute unpack - the one
// wait of the install that said nothing. Returns "done" or "cancel"; the
// caller judges success by the extracted tree, not by an exit code.
function extract(zipPath, destination, sh) {
    var tar = sh.ExpandEnvironmentStrings("%SystemRoot%\\System32\\tar.exe");
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var exeName, cmd;
    if (fso.FileExists(tar)) {
        exeName = "tar.exe";
        cmd = 'cmd /c ""' + tar + '" -xf "' + zipPath + '" -C "' + destination + '""';
    } else {
        exeName = "powershell.exe";
        cmd = 'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command '
            + '"Expand-Archive -LiteralPath \'' + zipPath + '\' -DestinationPath \''
            + destination + '\' -Force"';
    }
    sh.Run(cmd, 0, false);

    var wmi = GetObject(
        "winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\cimv2");
    var waited = 0;
    var pid = 0;
    for (var tries = 0; tries < 10 && pid === 0; tries++) {
        _sleep1s(sh);
        waited++;
        pid = _findPidByCmd(wmi, exeName, GCLOUD_ZIP);
        if (_tick("extracting the Google Cloud SDK... (" + waited + " s)") === 2) {
            return "cancel";
        }
    }
    while (pid !== 0 && _isAlive(wmi, pid)) {
        _sleep1s(sh);
        waited++;
        if (_tick("extracting the Google Cloud SDK... (" + waited + " s)") === 2) {
            try { sh.Run("taskkill /pid " + pid + " /t /f", 0, true); } catch (e) {}
            return "cancel";
        }
    }
    return "done";
}

function ExpandGCloud() {
    var dir = installFolder();
    if (!dir) {
        log("no install folder passed; skipping");
        return 1;
    }
    var sh = new ActiveXObject("WScript.Shell");
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var zipPath = dir + GCLOUD_ZIP;
    var sdkDir = dir + GCLOUD_DIR;

    if (!fso.FileExists(zipPath)) {
        // The feature was not installed (user unticked it, or gcloud was
        // already present) - nothing to do.
        log("archive not present; nothing to expand");
        return 1;
    }
    // A retried or repaired install can find a previous tree here; the
    // archive must land on a clean directory.
    if (fso.FolderExists(sdkDir)) {
        try { fso.DeleteFolder(sdkDir, true); } catch (e) {
            log("could not clear " + sdkDir + ": " + e.message);
        }
    }

    log("expanding the Google Cloud SDK into " + dir);
    var outcome = "done";
    try { outcome = extract(zipPath, dir, sh); } catch (e) {
        log("expansion raised: " + e.message);
        outcome = "failed";
    }
    if (outcome === "cancel") {
        log("cancelled by the user during expansion");
        return 2;  // user exit: MSI rolls back, RemoveBundledGCloud cleans up
    }
    // The archive's own top-level folder is google-cloud-sdk\, so a correct
    // expansion always produces it. Verify rather than trust the extractor.
    if (outcome !== "done" || !fso.FileExists(sdkDir + "\\install.bat")) {
        log("FAILED: the SDK was not expanded");
        return 3;  // abort: a half-installed SDK is worse than a clear error
    }

    // The archive has served its purpose; keeping it would waste ~110 MB in
    // Program Files forever. Its MSI component stays registered, which is
    // what still triggers this action on repair.
    try { fso.DeleteFile(zipPath, true); } catch (e) {
        log("could not delete the archive: " + e.message);
    }
    log("done");
    return 1;
}

// Used for BOTH uninstall and rollback: the expanded tree has no MSI
// components, so nothing else would ever remove it.
function RemoveBundledGCloud() {
    try {
        var dir = installFolder();
        if (!dir) { return 1; }
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        var sdkDir = dir + GCLOUD_DIR;
        if (fso.FolderExists(sdkDir)) {
            log("removing " + sdkDir);
            fso.DeleteFolder(sdkDir, true);
        }
        var zipPath = dir + GCLOUD_ZIP;
        if (fso.FileExists(zipPath)) { fso.DeleteFile(zipPath, true); }
    } catch (e) {
        // Never block an uninstall over leftovers.
        log("cleanup failed: " + e.message);
    }
    return 1;
}
