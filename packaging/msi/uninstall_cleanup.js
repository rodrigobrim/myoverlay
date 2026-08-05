// MSI uninstall custom actions (deferred, impersonated - they act on the
// uninstalling user's profile).
//
// Uninstalling removes the APPLICATION. Nothing inside the data dir
// (~\myoverlay) goes with it unless the user ticks a box for it on the
// remove-options page, because everything there is either the user's or
// irreplaceable:
//
//   config.toml           hand-edited settings
//   client_secret.json    OAuth client secret - Google reveals it exactly
//                         once, at creation, so deleting it destroys the
//                         only copy (google-setup itself never deletes an
//                         old secret, it renames it to .bak)
//   google-token          the refresh token from an interactive consent;
//                         the real revocation lives in the Google account
//   gcp_browser_profile\  the saved Google sign-in
//   google-setup.log      the last setup report, useful after the fact
//   render\               the media library by default - irreplaceable
//                         footage and telemetry
//   repo\                 a checkout of a public repository; disposable in
//                         principle, but it may hold a branch or local edits
//
// RemoveRepo (REMOVE_REPO=1): delete the pipeline clone, in the data dir and
// in the pre-home-layout %LOCALAPPDATA%\myoverlay of older installs; that
// legacy directory is dropped too, but only once nothing remains in it.
// Wiping that path unconditionally is what once destroyed a checkout that
// happened to live there.
//
// RemoveGoogleFiles (REMOVE_GOOGLE_FILES=1): delete the Google credentials
// and sign-in state. The media library is never part of it.
//
// RemoveGCloud (REMOVE_GCLOUD=1): silently run the Google Cloud SDK
// uninstaller.
//
// Each action is conditioned on its own property in Product.wxs, so an
// unticked box means the action never runs at all.

var GOOGLE_ITEMS = [
    "client_secret.json", "google-token", "gcp_browser_profile",
    "google-setup.log"
];

function _homeDir(sh) {
    return sh.ExpandEnvironmentStrings("%USERPROFILE%") + "\\myoverlay";
}

function _legacyDir(sh) {
    return sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") + "\\myoverlay";
}

function _delete(fso, path) {
    try {
        if (fso.FolderExists(path)) {
            fso.DeleteFolder(path, true); // force: .git objects are read-only
        } else if (fso.FileExists(path)) {
            fso.DeleteFile(path, true);
        }
    } catch (e) {
        // Non-fatal: leftover files never block the uninstall.
    }
}

function RemoveRepo() {
    var sh = new ActiveXObject("WScript.Shell");
    var fso = new ActiveXObject("Scripting.FileSystemObject");

    _delete(fso, _homeDir(sh) + "\\repo");

    var legacy = _legacyDir(sh);
    _delete(fso, legacy + "\\repo");
    try {
        // Only when nothing else is left there: an empty directory is
        // clutter, a non-empty one holds something we did not put there.
        var folder = fso.GetFolder(legacy);
        if (folder.Files.Count === 0 && folder.SubFolders.Count === 0) {
            fso.DeleteFolder(legacy, true);
        }
    } catch (e) {
        // Absent or unreadable: nothing to clean up.
    }
    return 1;
}

function RemoveGoogleFiles() {
    var sh = new ActiveXObject("WScript.Shell");
    var fso = new ActiveXObject("Scripting.FileSystemObject");
    var dirs = [_homeDir(sh), _legacyDir(sh)];
    for (var d = 0; d < dirs.length; d++) {
        for (var i = 0; i < GOOGLE_ITEMS.length; i++) {
            _delete(fso, dirs[d] + "\\" + GOOGLE_ITEMS[i]);
        }
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
