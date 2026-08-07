# Tag HEAD with the current UTC date (yyyyMMddHHmm, 'v'-prefixed) and push
# the tag, which triggers .github/workflows/release.yml to build and publish
# a GitHub Release. See packaging\version.ps1 for the tag/version scheme.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\release.ps1
#         ... -WhatIf     print the tag that would be created, do nothing
param([switch]$WhatIf)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "version.ps1")

$tag = "v" + (Get-DateTag)

Push-Location $repo
try {
    if ($WhatIf) {
        Write-Host "Would tag and push: $tag"
        return
    }
    git tag $tag
    git push origin $tag
    Write-Host "Tagged and pushed $tag"
    Write-Host "Release workflow: https://github.com/rodrigobrim/myoverlay/actions/workflows/release.yml"
} finally {
    Pop-Location
}
