// MSI immediate CA: the wizard's dependency-detection phase.
//
// The check is silent: no page appears when everything is present. DEPS
// below is the single place to register a dependency. Each entry:
//   id        - suffix of the DEP_<id>_FOUND property (drives the
//               per-dependency hint's visibility on DepsBlockedDlg - add a
//               P_DEPS_<id>_NOTE string in gen_i18n_ui.py and a note control
//               on that page).
//   name      - display name, shown in the missing-required list on the
//               DepsBlockedDlg page.
//   blocking  - true: while missing, the wizard detours to DepsBlockedDlg
//               (Back + Next re-checks, Cancel exits setup). false: reserved
//               for proceed-with-warning dependencies (none currently; no UI
//               surfaces them yet).
//   detect    - returns { found: bool, version: string }.
//
// Runs from the UI sequence before the first dialog and again from the
// language page's Next.

// RS3 versions the pipeline's GUI automation was validated against (real UI
// snapshots). Any other version is reported as blocking: the automation
// refuses to drive an unvalidated layout, so telemetry would never arrive.
// MUST stay in sync with Rs3Config.validated_versions in
// src/media_tools/config.py - a unit test compares the two.
var VALIDATED_RS3_VERSIONS = ["3.83.39"];

// First three numeric segments of a version string ("3.83.39.0" -> "3.83.39").
function versionPrefix(s) {
    var m = /(\d+)\.(\d+)\.(\d+)/.exec(s || "");
    return m ? m[1] + "." + m[2] + "." + m[3] : "";
}

function isValidatedRs3(version) {
    var v = versionPrefix(version);
    if (!v) { return false; }
    for (var i = 0; i < VALIDATED_RS3_VERSIONS.length; i++) {
        if (VALIDATED_RS3_VERSIONS[i] === v) { return true; }
    }
    return false;
}

var DEPS = [
    {
        // Race Studio 3 (AiM) downloads the telemetry from the MyChron; the
        // pipeline watches its data folder. Blocking: without it no telemetry
        // ever arrives, so the wizard will not proceed until it is installed.
        // The VERSION is validated too - see VALIDATED_RS3_VERSIONS above.
        id: "RS3",
        name: "Race Studio 3 (AiM)",
        blocking: true,
        detect: function () {
            // RS3 ships as an MSI whose ProductCode changes per release, so
            // enumerate installed products and match the display name
            // ("RaceStudio 3", historically also "Race Studio 3").
            var found = { found: false, version: "" };
            try {
                var installer = Session.Installer;
                var products = installer.Products;
                for (var i = 0; i < products.Count; i++) {
                    var code = products.Item(i);
                    var name = "";
                    try { name = installer.ProductInfo(code, "ProductName"); }
                    catch (e) { continue; }
                    if (/race\s*studio\s*3/i.test(name)) {
                        var ver = "";
                        try { ver = installer.ProductInfo(code, "VersionString"); }
                        catch (e2) { }
                        found = { found: true, version: ver };
                        break;
                    }
                }
            } catch (e3) { }
            try {
                var fso = new ActiveXObject("Scripting.FileSystemObject");
                if (!found.found && fso.FolderExists("C:\\AIM_SPORT\\RaceStudio3")) {
                    // Legacy/non-MSI install: the fixed default folder (RS3's
                    // own installer does not offer another location).
                    found = { found: true, version: "" };
                }
                if (found.found && !versionPrefix(found.version)) {
                    // No usable MSI version - read it off the real binary.
                    var exes = [
                        "C:\\AIM_SPORT\\RaceStudio3\\64\\AiMRS3-64-ReleaseU.exe",
                        "C:\\AIM_SPORT\\RaceStudio3\\RaceStudio3.exe"
                    ];
                    for (var j = 0; j < exes.length; j++) {
                        try {
                            if (fso.FileExists(exes[j])) {
                                found.version = fso.GetFileVersion(exes[j]);
                                break;
                            }
                        } catch (e5) { }
                    }
                }
            } catch (e4) { }
            found.supported = found.found && isValidatedRs3(found.version);
            return found;
        }
    }
];

function DetectDependencies() {
    var anyBlocking = "";
    var anyWarning = "";
    var blockingNames = [];
    for (var i = 0; i < DEPS.length; i++) {
        var dep = DEPS[i];
        var r = { found: false, version: "", supported: false };
        try { r = dep.detect(); } catch (e) { }
        // A present-but-unvalidated version is as blocking as an absent one:
        // the automation will refuse to drive it. The bullet says which.
        var ok = r.found && r.supported !== false;
        Session.Property("DEP_" + dep.id + "_FOUND") = ok ? "1" : "";
        if (!ok) {
            if (dep.blocking) {
                anyBlocking = "1";
                // Escaped bullet keeps this file pure ASCII.
                var line = "\u2022  " + dep.name;
                if (r.found) {
                    line += " - unsupported version " +
                        (versionPrefix(r.version) || "unknown") +
                        " (validated: " + VALIDATED_RS3_VERSIONS.join(", ") + ")";
                }
                blockingNames.push(line);
            } else {
                anyWarning = "1";
            }
        }
    }
    // Empty string clears the property, so the wizard conditions read these
    // as plain booleans.
    Session.Property("DEPS_BLOCKING_MISSING") = anyBlocking;
    Session.Property("DEPS_WARNING_MISSING") = anyWarning;
    // Bullet list of the missing required software, one per line, rendered
    // verbatim on the DepsBlockedDlg page.
    Session.Property("DEPS_BLOCKING_LIST") = blockingNames.join("\r\n");
    return 1;
}
