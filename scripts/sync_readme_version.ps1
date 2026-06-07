<#
.SYNOPSIS
    Setzt die Versionszeile am Anfang von README.md auf APP_VERSION.

.PARAMETER Version
    APP_VERSION ohne fuehrendes v (z. B. 1.9.6).

.PARAMETER ProjectRoot
    Projektroot (Standard: Parent von scripts/).

.PARAMETER DryRun
    Nur anzeigen, README.md nicht schreiben.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Version,

    [string] $ProjectRoot = "",
    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$readmePath = Join-Path $ProjectRoot "README.md"
if (-not (Test-Path $readmePath)) {
    Write-Warning "README.md nicht gefunden: $readmePath"
    return
}

$raw = Get-Content -Path $readmePath -Raw -Encoding UTF8
$emDash = [char]0x2014
$pattern = '\*\*Version [\d.]+?\*\* [\u2014\-] Desktop-Tool'
$replacement = "**Version $Version** ${emDash} Desktop-Tool"

if ($raw -notmatch $pattern) {
    Write-Warning "README.md: Versionszeile nicht gefunden (erwartet: **Version x.y.z** - Desktop-Tool)."
    return
}

if ($raw -match $pattern -and $Matches[0] -eq $replacement) {
    return
}

if ($DryRun) {
    Write-Host "[DryRun] README.md Versionszeile -> $Version" -ForegroundColor Magenta
    return
}

$updated = [regex]::Replace($raw, $pattern, $replacement, 1)
[System.IO.File]::WriteAllText($readmePath, $updated, [System.Text.UTF8Encoding]::new($false))
Write-Host "README.md: Version auf $Version gesetzt." -ForegroundColor Yellow
