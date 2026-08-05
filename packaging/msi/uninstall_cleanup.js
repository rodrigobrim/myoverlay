// MSI uninstall custom actions (deferred, impersonated - they act on the
// uninstalling user's profile).
//
// RemoveAppData: remove the app's OWN items from ~\myoverlay (pipeline
// clone, config.toml, google-token, client secret, setup log, browser
// profile) - NEVER the whole directory: the media library lives inside it by
// default (~\myoverlay\render - irreplaceable footage/telemetry) and
// anything unrecognized stays untouched with it. The pre-home-layout
// %LOCALAPPDATA%\myoverlay (older installs) is still wiped whole; it never
// held media.
//
// RemoveGCloud: silently run the Google Cloud SDK uninstaller. Only invoked
// when the user ticked the checkbox on the remove-options page.

function RemoveAppData() {
    var sh = new ActiveXObject("WScript.Shell");
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var home = sh.ExpandEnvironmentStrings("%USERPROFILE%") + "\\myoverlay";
    var appItems = [
        "repo", "gcp_browser_profile", "config.toml", "google-token",
        "client_secret.json", "google-setup.log"
    ];
    for (var i = 0; i < appItems.length; i++) {
        var p = home + "\\" + appItems[i];
        try {
            if (fso.FolderExists(p)) {
                fso.DeleteFolder(p, true); // force: .git objects are read-only
            } else if (fso.FileExists(p)) {
                fso.DeleteFile(p, true);
            }
        } catch (e) {
            // Non-fatal: leftover files never block the uninstall.
        }
    }
    try {
        var legacy = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") + "\\myoverlay";
        if (fso.FolderExists(legacy)) {
            fso.DeleteFolder(legacy, true);
        }
    } catch (e2) {
        // Non-fatal.
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
