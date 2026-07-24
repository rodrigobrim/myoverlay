// MSI immediate CA: the wizard's dependency-detection phase.
//
// DEPS below is the single place to register a dependency. Each entry:
//   id        - suffix of the DEP_<id>_FOUND / DEP_<id>_STATUS properties the
//               wizard page binds to (add a row in DependenciesDlg,
//               WizardUI.wxs, plus a P_DEPS_<id>_NOTE string in
//               gen_i18n_ui.py for the "where to get it" hint).
//   name      - display name, shown in the missing-required list on the
//               DepsBlockedDlg page.
//   blocking  - true: Next on the dependency page leads to DepsBlockedDlg
//               (Back re-checks, Cancel exits setup) until the dependency is
//               installed. false: a warning is shown but the install may
//               proceed.
//   detect    - returns { found: bool, version: string }.
//
// Runs from the UI sequence after ApplyUiLanguage (it composes the localized
// status text from the P_DEPS_* strings) and again from the language page's
// Next, so the status is re-localized when the language changes.

var DEPS = [
    {
        // Race Studio 3 (AiM) downloads the telemetry from the MyChron; the
        // pipeline watches its data folder. Blocking: without it no telemetry
        // ever arrives, so the wizard will not proceed until it is installed.
        id: "RS3",
        name: "Race Studio 3 (AiM)",
        blocking: true,
        detect: function () {
            // RS3 ships as an MSI whose ProductCode changes per release, so
            // enumerate installed products and match the display name
            // ("RaceStudio 3", historically also "Race Studio 3").
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
                        return { found: true, version: ver };
                    }
                }
            } catch (e3) { }
            // Fallback for non-MSI/legacy installs: the fixed default folder
            // (RS3's own installer does not offer another location).
            try {
                var fso = new ActiveXObject("Scripting.FileSystemObject");
                if (fso.FolderExists("C:\\AIM_SPORT\\RaceStudio3")) {
                    return { found: true, version: "" };
                }
            } catch (e4) { }
            return { found: false, version: "" };
        }
    }
];

function DetectDependencies() {
    var anyBlocking = "";
    var anyWarning = "";
    var blockingNames = [];
    var tFound = Session.Property("P_DEPS_FOUND") || "installed";
    var tMissing = Session.Property("P_DEPS_MISSING") || "not found";
    for (var i = 0; i < DEPS.length; i++) {
        var dep = DEPS[i];
        var r = { found: false, version: "" };
        try { r = dep.detect(); } catch (e) { }
        Session.Property("DEP_" + dep.id + "_FOUND") = r.found ? "1" : "";
        Session.Property("DEP_" + dep.id + "_STATUS") = r.found
            ? (tFound + (r.version ? " (" + r.version + ")" : ""))
            : tMissing;
        if (!r.found) {
            if (dep.blocking) {
                anyBlocking = "1";
                // Escaped bullet keeps this file pure ASCII.
                blockingNames.push("\u2022  " + dep.name);
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
