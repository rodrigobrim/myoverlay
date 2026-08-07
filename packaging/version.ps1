# Shared date-based versioning, used by both local MSI builds and tagged
# releases so the two never drift onto different schemes.
#
# The MSI ProductVersion must be MAJOR.MINOR.PATCH (Windows Installer field
# limits: Major 0-255, Minor 0-255, Build/Patch 0-65535) and must increase
# between installs a user will run over one another - Product.wxs pairs a
# fixed UpgradeCode with <MajorUpgrade>, so two MSIs sharing a version never
# upgrade each other (observed live: "Setup Wizard ended prematurely"). A
# calendar date does not fit those limits directly (the year alone exceeds
# 255), so it is encoded instead of used literally:
#   Major = 0                                   (headroom, unused)
#   Minor = months since 2026-01                 (0..255 -> good until ~2047)
#   Patch = minutes since the start of the month (0..44639, fits in 65535)
#
# The human-facing label - what gets tagged, and what titles the GitHub
# Release - stays a plain yyyyMMddHHmm date string (e.g. v202608051557):
# readable at a glance, sorts correctly as text, and needs no separate
# release-notes lookup to know when a build was cut.

# UTC by default: tags are pushed from whatever timezone the dev machine is
# in, but CI always runs in UTC, so anchoring both to UTC keeps the tag ->
# MSI-version derivation identical wherever it runs.
function Get-DateTag {
    param([DateTime]$When = ([DateTime]::UtcNow))
    return $When.ToString("yyyyMMddHHmm")
}

function ConvertTo-MsiVersion {
    param([DateTime]$When = ([DateTime]::UtcNow))
    $minor = (($When.Year - 2026) * 12) + $When.Month - 1
    $patch = ($When.Day * 1440) + ($When.Hour * 60) + $When.Minute
    if ($minor -lt 0 -or $minor -gt 255) {
        throw ("Date $($When.ToString('yyyy-MM-dd')) is out of the versionable " +
               "range (2026-01 .. 2046-12).")
    }
    return "0.$minor.$patch"
}

function ConvertFrom-DateTag {
    # Parses a yyyyMMddHHmm tag (optionally 'v'-prefixed) back to a DateTime,
    # so a release can derive its MSI version from the tag that named it
    # rather than from "now" - the checkout, not the tag push, is what runs
    # in CI, and the two can differ by minutes.
    param([Parameter(Mandatory)][string]$Tag)
    $digits = $Tag -replace '^v', ''
    if ($digits -notmatch '^\d{12}$') {
        throw "Tag '$Tag' is not a yyyyMMddHHmm date tag."
    }
    return [DateTime]::ParseExact($digits, "yyyyMMddHHmm", $null)
}
