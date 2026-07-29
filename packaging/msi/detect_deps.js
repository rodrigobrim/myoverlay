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

// Windows' installed-programs record (the Uninstall keys behind Add/Remove
// Programs and PowerShell Get-Package) is the SINGLE source of truth for
// which RS3 is installed - here and in the CLI alike. rs3.py reads these
// very same keys through winreg and normalises versions the same way; a
// unit test compares both lists. Do not add a second detection path (MSI
// product enumeration, folder existence, exe file version): the wizard
// admitting an install the CLI then refuses to drive is exactly the
// mismatch this single source exists to prevent.
var HKLM = 0x80000002;
var HKCU = 0x80000001;
var UNINSTALL_KEYS = [
    [HKLM, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"],
    [HKLM, "SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"],
    [HKCU, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"]
];

// AiM writes "RaceStudio 3" in one key and "RaceStudio 3 <version>" in
// another; historically also "Race Studio 3". Race Studio 2 must not match.
function looksLikeRs3(displayName) {
    return /race\s*studio\s*3(\D|$)/i.test(displayName || "");
}

// StdRegProv has to be driven through ExecMethod: JScript cannot receive
// WMI's [out] parameters from a plain reg.EnumKey(...) call - that returns
// the bare return code, so every hive silently looks empty. SpawnInstance_
// + ExecMethod gives a real out-object, and its SAFEARRAY is only iterable
// via VBArray. Verified against the live registry with cscript.
function Rs3Registry() {
    var svc = new ActiveXObject("WbemScripting.SWbemLocator")
        .ConnectServer(".", "root\\default");
    this.svc = svc;
    this.cls = svc.Get("StdRegProv");
}

Rs3Registry.prototype.call = function (method, hive, sub, valueName) {
    var inp = this.cls.Methods_.Item(method).InParameters.SpawnInstance_();
    inp.hDefKey = hive;
    inp.sSubKeyName = sub;
    if (valueName !== undefined) { inp.sValueName = valueName; }
    return this.svc.ExecMethod("StdRegProv", method, inp);
};

Rs3Registry.prototype.subKeys = function (hive, sub) {
    try {
        var out = this.call("EnumKey", hive, sub);
        if (out.ReturnValue !== 0 || out.sNames === null) { return []; }
        return new VBArray(out.sNames).toArray();
    } catch (e) { return []; }
};

Rs3Registry.prototype.stringValue = function (hive, sub, name) {
    try {
        var out = this.call("GetStringValue", hive, sub, name);
        if (out.ReturnValue !== 0 || out.sValue === null) { return ""; }
        return out.sValue;
    } catch (e) { return ""; }
};

// { found: bool, version: string } straight from the registry.
function detectRs3FromRegistry() {
    var result = { found: false, version: "" };
    var reg;
    try {
        reg = new Rs3Registry();
    } catch (e) {
        return result;
    }
    for (var i = 0; i < UNINSTALL_KEYS.length; i++) {
        var hive = UNINSTALL_KEYS[i][0];
        var path = UNINSTALL_KEYS[i][1];
        var names = reg.subKeys(hive, path);
        for (var j = 0; j < names.length; j++) {
            var sub = path + "\\" + names[j];
            if (!looksLikeRs3(reg.stringValue(hive, sub, "DisplayName"))) { continue; }
            var ver = reg.stringValue(hive, sub, "DisplayVersion");
            // Prefer an entry that actually carries a version - AiM's two
            // entries agree, but a versionless one must not mask a good one.
            if (!result.found || (!versionPrefix(result.version) && versionPrefix(ver))) {
                result = { found: true, version: ver };
            }
        }
    }
    return result;
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
            // Registry only - see detectRs3FromRegistry. An install Windows
            // has no record of is treated as absent: the CLI would refuse to
            // drive it anyway (it reads the same record), so letting setup
            // pass here would only move the failure later.
            var found = detectRs3FromRegistry();
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
