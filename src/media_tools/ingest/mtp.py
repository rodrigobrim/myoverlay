"""Windows MTP camera access (GoPro & co.) via the Shell COM namespace.

Action cameras like the GoPro HERO9 have no USB mass-storage mode: plugged
in they appear as an MTP "portable device" with no drive letter, invisible
to psutil/pathlib. Windows exposes MTP only through the Explorer Shell
namespace, which we drive from a PowerShell child process (no extra Python
dependency, same shape as the netsh-based WiFi code).

MTP quirks, verified against a real HERO9 BLACK:
- A file's shell .Path is an opaque object-ID chain
  (::{...}\\...\\{00000033-...}), so the display .Name carries the filename
  and shell paths are only valid while the device stays connected.
- FolderItem.Size reads 0 and System.DateModified is null; System.Size and
  System.DateCreated (the recording time) do work. System.DateCreated comes
  back as an Unspecified-kind DateTime that already holds UTC wall time.
- Folder.CopyHere is asynchronous and reports nothing, so completion is
  polled on the destination file reaching the expected size.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Devices nest DCIM under a storage volume ("GoPro MTP Client Disk Volume");
# two levels below the device covers every camera seen so far.
_DCIM_SEARCH_DEPTH = 2

_LIST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$exts = @(%(exts)s)
$rootsOnly = $%(roots_only)s
$shell = New-Object -ComObject Shell.Application
$sources = @()
$files = New-Object System.Collections.Generic.List[object]

function Find-Dcim($folder, $depth) {
    foreach ($it in @($folder.Items())) {
        if (-not $it.IsFolder) { continue }
        if ($it.Name -eq 'DCIM') { return $it }
        if ($depth -gt 0) {
            $hit = Find-Dcim $it.GetFolder ($depth - 1)
            if ($null -ne $hit) { return $hit }
        }
    }
    return $null
}

function Walk-Files($folder, $rel) {
    foreach ($it in @($folder.Items())) {
        if ($it.IsFolder) {
            Walk-Files $it.GetFolder ($rel + '/' + $it.Name)
            continue
        }
        $ext = [IO.Path]::GetExtension($it.Name).ToLowerInvariant()
        if ($exts -notcontains $ext) { continue }
        $size = $it.ExtendedProperty('System.Size')
        if ($null -eq $size) { $size = $it.Size }
        $created = $it.ExtendedProperty('System.DateCreated')
        $files.Add([pscustomobject]@{
            name = $it.Name
            size = [int64]$size
            created_utc = $(if ($created -is [DateTime]) { $created.ToString('s') + 'Z' } else { $null })
            shell_path = $it.Path
            display = $rel + '/' + $it.Name
        })
    }
}

foreach ($dev in @($shell.NameSpace(17).Items())) {
    if (-not $dev.IsFolder) { continue }
    if ($dev.Path -match '^[A-Za-z]:') { continue }  # lettered volumes are psutil's job
    $dcim = $null
    try { $dcim = Find-Dcim $dev.GetFolder %(depth)d } catch { continue }
    if ($null -eq $dcim) { continue }
    $sources += ($dev.Name + '/DCIM')
    if (-not $rootsOnly) { Walk-Files $dcim.GetFolder ($dev.Name + '/DCIM') }
}

# PS 5.1 ConvertTo-Json throws on a Generic.List inside a hashtable: ToArray first.
@{ sources = @($sources); files = $files.ToArray() } | ConvertTo-Json -Depth 4 -Compress
"""

_COPY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = '%(shell_path)s'
$destDir = '%(dest_dir)s'
$expected = %(size)d
$timeoutS = %(timeout)d
$shell = New-Object -ComObject Shell.Application

function Find-ByPath($folder, $path) {
    foreach ($it in @($folder.Items())) {
        if ($it.Path -eq $path) { return $it }
        if ($it.IsFolder -and $path.StartsWith($it.Path + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return Find-ByPath $it.GetFolder $path
        }
    }
    return $null
}

$item = Find-ByPath $shell.NameSpace(17) $target
if ($null -eq $item) { [Console]::Error.WriteLine("item not found on device: $target"); exit 2 }
$dst = $shell.NameSpace($destDir)
if ($null -eq $dst) { [Console]::Error.WriteLine("destination not found: $destDir"); exit 3 }
# 4: no progress UI, 16: yes-to-all (overwrite), 512: no new-dir confirm, 1024: no error UI
$dst.CopyHere($item, (4 -bor 16 -bor 512 -bor 1024))
$destFile = Join-Path $destDir $item.Name
$deadline = [DateTime]::UtcNow.AddSeconds($timeoutS)
while ($true) {
    $g = Get-Item -LiteralPath $destFile -ErrorAction SilentlyContinue
    if ($null -ne $g -and $g.Length -ge $expected) { exit 0 }
    if ([DateTime]::UtcNow -gt $deadline) { [Console]::Error.WriteLine('copy timed out'); exit 4 }
    Start-Sleep -Milliseconds 500
}
"""


@dataclass(frozen=True)
class MtpFile:
    """One video on an MTP device."""

    name: str
    size: int
    created_utc: datetime  # System.DateCreated = when the clip was recorded
    shell_path: str  # opaque shell item path, valid for this connection only
    display: str  # human-readable pseudo path, e.g. "HERO9 BLACK/DCIM/100GOPRO/GH011045.MP4"


def _powershell(script: str, timeout_s: float) -> subprocess.CompletedProcess:
    # -EncodedCommand sidesteps every quoting/newline pitfall of -Command.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _list_script(extensions: list[str], roots_only: bool) -> str:
    exts = ",".join("'" + e.lower().replace("'", "''") + "'" for e in extensions)
    return _LIST_SCRIPT % {
        "exts": exts or "''",
        "roots_only": "true" if roots_only else "false",
        "depth": _DCIM_SEARCH_DEPTH,
    }


def _parse_listing(text: str) -> tuple[list[str], list[MtpFile]]:
    data = json.loads(text)
    files: list[MtpFile] = []
    for f in _as_list(data.get("files")):
        created = f.get("created_utc")
        files.append(
            MtpFile(
                name=f["name"],
                size=int(f["size"]),
                # A missing recording date should be near-impossible; "now" at
                # least lands the clip on the day the user is ingesting.
                created_utc=datetime.fromisoformat(created)
                if created
                else datetime.now(timezone.utc),
                shell_path=f["shell_path"],
                display=f["display"],
            )
        )
    return [str(s) for s in _as_list(data.get("sources"))], files


def _as_list(v) -> list:
    """ConvertTo-Json collapses some single-element collections to a scalar."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def enumerate_mtp_videos(extensions: list[str]) -> tuple[list[str], list[MtpFile]]:
    """DCIM video inventory of every connected MTP device.

    Best-effort: any failure (no PowerShell, COM error, device unplugged
    mid-scan) returns an empty inventory rather than breaking the
    volume-based scan running alongside.
    """
    if sys.platform != "win32":
        return [], []
    try:
        proc = _powershell(_list_script(extensions, roots_only=False), timeout_s=300)
        if proc.returncode != 0:
            return [], []
        return _parse_listing(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return [], []


def find_mtp_sources() -> list[str]:
    """DCIM roots of connected MTP devices (cheap presence check, no file walk)."""
    if sys.platform != "win32":
        return []
    try:
        proc = _powershell(_list_script([], roots_only=True), timeout_s=60)
        if proc.returncode != 0:
            return []
        return _parse_listing(proc.stdout)[0]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []


def copy_mtp_file(f: MtpFile, dest_dir: Path) -> None:
    """Copy one device file into dest_dir (as f.name), blocking until done.

    Raises OSError on failure so ingest records it like any file error.
    """
    # MTP over USB2 moves ~20 MB/s; budget a pessimistic 2 MB/s.
    timeout_s = 120 + f.size // (2 * 1024 * 1024)
    script = _COPY_SCRIPT % {
        "shell_path": f.shell_path.replace("'", "''"),
        "dest_dir": str(dest_dir).replace("'", "''"),
        "size": f.size,
        "timeout": timeout_s,
    }
    try:
        proc = _powershell(script, timeout_s=timeout_s + 60)
    except subprocess.SubprocessError as exc:
        raise OSError(f"MTP copy failed for {f.display}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        raise OSError(f"MTP copy failed for {f.display}: {detail}")
