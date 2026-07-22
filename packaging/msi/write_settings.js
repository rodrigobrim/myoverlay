// MSI deferred custom action: persist the setup wizard's choices as
// install_settings.yaml next to myoverlay.exe. The launcher reads it on first
// run to seed config.toml (language, resolution, install_dir) and to copy the
// Google client secret into place.
//
// The file is a flat `key: value` document (the launcher parses it with a tiny
// hand parser - no YAML library). Values are written literally and unquoted:
// the launcher splits each line on its FIRST colon, so paths keep their drive
// letter and backslashes untouched.
//
// CustomActionData: INSTALLFOLDER|OUTPUT_LANGUAGE|RESOLUTION|GOOGLE_CLIENT_SECRET|GOOGLE_SKIPPED
// (INSTALLFOLDER is both where the file is written and the recorded install_dir.)

// Immediate custom action: preselect OUTPUT_LANGUAGE from the machine's UI
// language when it is one of the supported nine, else leave English. The
// low 10 bits of the LCID are the primary language id. Only overrides the
// untouched default (a command-line OUTPUT_LANGUAGE=xx is respected).
// Chinese primary id 4 maps to Simplified (the only Chinese we ship).
function DetectLanguage() {
    try {
        if (Session.Property("OUTPUT_LANGUAGE") !== "en") return 1;
        var primary = parseInt(Session.Property("SystemLanguageID"), 10) & 0x3FF;
        var map = { 9: "en", 22: "pt", 10: "es", 17: "ja",
                    1: "ar", 12: "fr", 16: "it", 25: "ru", 4: "zh" };
        if (map[primary]) Session.Property("OUTPUT_LANGUAGE") = map[primary];
    } catch (e) {
        // Non-fatal: default English stands.
    }
    return 1;
}

function WriteSettings() {
    try {
        var parts = Session.Property("CustomActionData").split("|");
        var folder = parts[0];
        var language = parts[1] || "en";
        var resolution = parts[2] || "2k";
        var secret = parts[3] || "";
        var skipped = parts[4] === "1";
        // The install dir is INSTALLFOLDER without its trailing backslash.
        var installDir = folder;
        if (installDir.substr(installDir.length - 1) === "\\") {
            installDir = installDir.substr(0, installDir.length - 1);
        }
        var yaml = ""
            + "language: " + language + "\n"
            + "resolution: " + resolution + "\n"
            + "client_secret: " + secret + "\n"
            + "google_skipped: " + (skipped ? "true" : "false") + "\n"
            + "install_dir: " + installDir + "\n";
        var fso = new ActiveXObject("Scripting.FileSystemObject");
        if (folder.substr(folder.length - 1) !== "\\") folder += "\\";
        var ts = fso.CreateTextFile(folder + "install_settings.yaml", true, false);
        ts.Write(yaml);
        ts.Close();
    } catch (e) {
        // Non-fatal: the app falls back to defaults without the file.
    }
    return 1;
}
