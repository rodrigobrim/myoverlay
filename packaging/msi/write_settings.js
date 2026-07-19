// MSI deferred custom action: persist the setup wizard's choices as
// install_settings.json next to myoverlay.exe. The launcher reads it on
// first run to seed config.toml (language, resolution) and to copy the
// Google client secret into place.
//
// CustomActionData: INSTALLFOLDER|OUTPUT_LANGUAGE|RESOLUTION|GOOGLE_CLIENT_SECRET|GOOGLE_SKIPPED
function jsonEscape(s) {
    return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function WriteSettings() {
    try {
        var parts = Session.Property("CustomActionData").split("|");
        var folder = parts[0];
        var language = parts[1] || "en";
        var resolution = parts[2] || "2k";
        var secret = parts[3] || "";
        var skipped = parts[4] === "1";
        var json = "{\n"
            + '  "language": "' + jsonEscape(language) + '",\n'
            + '  "resolution": "' + jsonEscape(resolution) + '",\n'
            + '  "client_secret": "' + jsonEscape(secret) + '",\n'
            + '  "google_skipped": ' + (skipped ? "true" : "false") + "\n"
            + "}\n";
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        if (folder.substr(folder.length - 1) !== "\\") folder += "\\";
        var ts = fso.CreateTextFile(folder + "install_settings.json", true, false);
        ts.Write(json);
        ts.Close();
    } catch (e) {
        // Non-fatal: the app falls back to defaults without the file.
    }
    return 1;
}
