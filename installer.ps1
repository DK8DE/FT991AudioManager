<#
.SYNOPSIS
    Erstellt den Windows-Setup-Installer (Inno Setup) für den FT-991A Audio-Profilmanager.

.DESCRIPTION
    Liest APP_VERSION aus version.py und kompiliert Installer.iss nach
    dist\installer\FT991AudioManager-Setup-<Version>.exe

    Voraussetzung: PyInstaller-Build unter dist\FT991AudioManager\ (.\build.ps1).
    Inno Setup 6: https://jrsoftware.org/isinfo.php

.PARAMETER SkipBuild
    Kein erneuter Aufruf von build.ps1 — nur Installer bauen.

.PARAMETER BuildConsole
    build.ps1 mit sichtbarer Konsole (Debugging).

.EXAMPLE
    .\installer.ps1

.EXAMPLE
    .\installer.ps1 -SkipBuild
#>
[CmdletBinding()]
param(
    [switch] $SkipBuild,
    [switch] $BuildConsole
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$VersionFile = Join-Path $ProjectRoot "version.py"
$IssFile = Join-Path $ProjectRoot "Installer.iss"
$DistApp = Join-Path $ProjectRoot "dist\FT991AudioManager"
$ExePath = Join-Path $DistApp "FT991AudioManager.exe"

if (-not (Test-Path $VersionFile)) {
    throw "version.py nicht gefunden: $VersionFile"
}
if (-not (Test-Path $IssFile)) {
    throw "Installer.iss nicht gefunden: $IssFile"
}

$content = Get-Content -Path $VersionFile -Raw -Encoding UTF8
if ($content -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    throw "APP_VERSION in version.py konnte nicht gelesen werden."
}
$appVersion = $Matches[1].Trim()
if ($appVersion -eq "") {
    throw "APP_VERSION ist leer."
}

Push-Location $ProjectRoot
try {
    if (-not $SkipBuild) {
        Write-Host ">> PyInstaller-Build (build.ps1)" -ForegroundColor Cyan
        $buildArgs = @()
        if ($BuildConsole) {
            $buildArgs += "-KeepConsole"
        }
        & (Join-Path $ProjectRoot "build.ps1") @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "build.ps1 fehlgeschlagen (exit $LASTEXITCODE)."
        }
    }

    if (-not (Test-Path $ExePath)) {
        throw @(
            "PyInstaller-Ausgabe fehlt: $ExePath",
            "Zuerst .\build.ps1 ausfuehren oder .\installer.ps1 ohne -SkipBuild."
        ) -join [Environment]::NewLine
    }

    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        throw @(
            "ISCC.exe (Inno Setup 6) nicht gefunden.",
            "Installation: https://jrsoftware.org/isdl.php",
            "oder: choco install innosetup -y"
        ) -join [Environment]::NewLine
    }

    Write-Host ""
    Write-Host ">> Inno Setup" -ForegroundColor Cyan
    Write-Host "   Version: $appVersion" -ForegroundColor Cyan
    Write-Host "   ISCC:    $iscc" -ForegroundColor Cyan
    & $iscc "/DMyAppVersion=$appVersion" $IssFile
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup beendete sich mit ExitCode $LASTEXITCODE"
    }

    $setup = Join-Path $ProjectRoot "dist\installer\FT991AudioManager-Setup-$appVersion.exe"
    if (-not (Test-Path $setup)) {
        throw "Installer fehlt: $setup"
    }

    Write-Host ""
    Write-Host "===========================================================" -ForegroundColor Green
    Write-Host " Installer erfolgreich" -ForegroundColor Green
    Write-Host "===========================================================" -ForegroundColor Green
    Write-Host " Setup: $setup" -ForegroundColor Green
    Write-Host ""
    Write-Host " Optional: Eigene Wizard-Bilder wie bei RotorTcpBridge" -ForegroundColor Green
    Write-Host "   Installer.png / InstallerSmall.png ins Projektroot legen" -ForegroundColor Green
    Write-Host "   und in Installer.iss die WizardImage-Zeilen aktivieren." -ForegroundColor Green
    Write-Host "===========================================================" -ForegroundColor Green
}
finally {
    Pop-Location
}
