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

// First three numeric segments of a version string ("3.83.39.0" -> "3.83.39").
function versionPrefix(s) {
    var m = /(\d+)\.(\d+)\.(\d+)/.exec(s || "");
    return m ? m[1] + "." + m[2] + "." + m[3] : "";
}

// No external software is required: MyOverlay talks to the MyChron directly
// (USB/WiFi) and bundles everything else. The detection framework stays for
// any future dependency - register it here.
var DEPS = [];

function DetectDependencies() {
    var anyBlocking = "";
    var anyWarning = "";
    var blockingNames = [];
    for (var i = 0; i < DEPS.length; i++) {
        var dep = DEPS[i];
        var r = { found: false, version: "", supported: false };
        try { r = dep.detect(); } catch (e) { }
        // A present-but-unsupported version is as blocking as an absent one;
        // the bullet says which.
        var ok = r.found && r.supported !== false;
        Session.Property("DEP_" + dep.id + "_FOUND") = ok ? "1" : "";
        if (!ok) {
            if (dep.blocking) {
                anyBlocking = "1";
                // Escaped bullet keeps this file pure ASCII.
                var line = "\u2022  " + dep.name;
                if (r.found) {
                    line += " - unsupported version " +
                        (versionPrefix(r.version) || "unknown");
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
