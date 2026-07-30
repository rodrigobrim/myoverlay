<#
.SYNOPSIS
    Capture RS3 <-> MyChron network traffic for protocol reverse-engineering.

.DESCRIPTION
    Wraps pktmon, which ships with Windows 10 -- nothing to install. Captures to
    an ETL ring buffer, then converts to .pcapng for analysis.

    Run START, do one full session download in Race Studio 3, then run STOP.

    Requires an elevated PowerShell (pktmon needs admin).

.EXAMPLE
    .\capture_mychron.ps1 -Action start
    # ... perform the download in RS3 ...
    .\capture_mychron.ps1 -Action stop
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action,

    # Where captures land. Defaults to a captures/ dir next to this script.
    [string]$OutDir = (Join-Path $PSScriptRoot 'captures'),

    # Ring buffer size in MB. A full session download can be tens of MB.
    [int]$BufferMB = 512
)

$ErrorActionPreference = 'Stop'

function Assert-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'pktmon requires an elevated PowerShell. Re-run as Administrator.'
    }
}

switch ($Action) {

    'start' {
        Assert-Elevated
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

        # Clear any filters left over from a previous run.
        pktmon filter remove | Out-Null

        # The AiM protocol runs over TCP for bulk transfer and UDP for
        # discovery/keepalive. Capture both rather than guessing ports -- the
        # port numbers are precisely what we do not know yet.
        pktmon filter add AiM-TCP -t TCP | Out-Null
        pktmon filter add AiM-UDP -t UDP | Out-Null

        $etl = Join-Path $OutDir 'mychron.etl'
        if (Test-Path $etl) { Remove-Item $etl -Force }

        # --comp all   : every component (we don't know if it's WiFi or IPonUSB)
        # --pkt-size 0 : keep whole packets, not just headers -- we need payload
        pktmon start --capture --pkt-size 0 --comp all -f $etl -s $BufferMB | Out-Null

        Write-Output "Capture STARTED -> $etl"
        Write-Output ''
        Write-Output 'Now, in Race Studio 3:'
        Write-Output '  1. Connect the MyChron6 over WiFi (AiM-... SSID).'
        Write-Output '  2. Download ONE small session.'
        Write-Output '  3. Re-run this script with -Action stop.'
    }

    'stop' {
        Assert-Elevated
        pktmon stop | Out-Null

        $etl = Join-Path $OutDir 'mychron.etl'
        if (-not (Test-Path $etl)) { throw "No capture found at $etl" }

        # etl2pcap writes alongside the etl unless told otherwise.
        pktmon etl2pcap $etl -o (Join-Path $OutDir 'mychron.pcapng') | Out-Null
        pktmon filter remove | Out-Null

        $pcap = Join-Path $OutDir 'mychron.pcapng'
        $size = (Get-Item $pcap).Length
        Write-Output "Capture STOPPED."
        Write-Output "  ETL:    $etl"
        Write-Output "  PCAPNG: $pcap  ($('{0:N0}' -f $size) bytes)"
    }

    'status' {
        pktmon status
    }
}
