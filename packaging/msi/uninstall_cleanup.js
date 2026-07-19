// MSI uninstall custom actions (deferred, impersonated - they act on the
// uninstalling user's profile).
//
// RemoveAppData: wipe %LOCALAPPDATA%\myoverlay (the launcher's pipeline
// clone, config.toml, OAuth token, client secret). The media library
// (library_root - videos/telemetry) lives elsewhere and is NEVER touched.
//
// RemoveGCloud: silently run the Google Cloud SDK uninstaller. Only invoked
// when the user ticked the checkbox on the remove-options page.

function RemoveAppData() {
    try {
        var sh = new ActiveXObject("WScript.Shell");
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        var dir = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") + "\\myoverlay";
        if (fso.FolderExists(dir)) {
            fso.DeleteFolder(dir, true); // force: .git objects are read-only
        }
    } catch (e) {
        // Non-fatal: leftover files never block the uninstall.
    }
    return 1;
}

function RemoveGCloud() {
    try {
        var sh = new ActiveXObject("WScript.Shell");
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        var cmd = null;
        try {
            cmd = sh.RegRead("HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Cloud SDK\\UninstallString");
        } catch (e) { /* not registered */ }
        if (!cmd) {
            var exe = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
                + "\\Google\\Cloud SDK\\uninstaller.exe";
            if (fso.FileExists(exe)) cmd = '"' + exe + '"';
        }
        if (cmd) sh.Run(cmd + " /S", 0, true); // hidden, wait for completion
    } catch (e) {
        // Non-fatal.
    }
    return 1;
}
