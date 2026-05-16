<#
.SYNOPSIS
    Liest APP_VERSION aus version.py und triggert per Git-Tag einen Release-Build.

.DESCRIPTION
    Erzeugt den Tag v<APP_VERSION> und führt "git push origin <Tag>" aus.
    Vorher APP_VERSION und APP_DATE in version.py anpassen.

.PARAMETER Remote
    Git-Remote-Name (Standard: origin).

.PARAMETER DryRun
    Zeigt nur Version, Tag und die geplanten Befehle; kein git tag / git push.

.PARAMETER Force
    Bei schmutzigem Arbeitsverzeichnis keine Rückfrage.

.EXAMPLE
    .\release.ps1

.EXAMPLE
    .\release.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string] $Remote = "origin",
    [switch] $DryRun,
    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$VersionFile = Join-Path $ProjectRoot "version.py"
if (-not (Test-Path $VersionFile)) {
    throw "version.py nicht gefunden: $VersionFile"
}

$content = Get-Content -Path $VersionFile -Raw -Encoding UTF8
if ($content -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    throw "APP_VERSION in version.py konnte nicht gelesen werden."
}
$appVersion = $Matches[1].Trim()
if ($appVersion -eq "") {
    throw "APP_VERSION ist leer."
}

$tag = "v$appVersion"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path (Join-Path $ProjectRoot ".git"))) {
        throw "Kein Git-Repository im Projektroot: $ProjectRoot"
    }

    $dirty = (git status --porcelain 2>$null)
    if ($dirty -and -not $Force -and -not $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer. Der Tag zeigt nur auf den letzten Commit."
        $null = Read-Host "Enter zum Fortfahren oder Strg+C zum Abbrechen"
    }
    elseif ($dirty -and $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer: vor echtem Release committen."
    }

    $head = (git rev-parse --short HEAD 2>$null)
    Write-Host "Projekt:     $ProjectRoot" -ForegroundColor Cyan
    Write-Host "APP_VERSION: $appVersion" -ForegroundColor Cyan
    Write-Host "Git-Tag:     $tag  (HEAD: $head)" -ForegroundColor Cyan
    Write-Host "Remote:      $Remote" -ForegroundColor Cyan
    Write-Host ""

    $existingLocal = git tag -l $tag 2>$null
    if ($existingLocal) {
        $tip = git rev-parse ($tag + '^{}') 2>$null
        if ($tip -eq (git rev-parse HEAD 2>$null)) {
            Write-Host "Tag existiert lokal bereits und zeigt auf HEAD; nur Push noetig." -ForegroundColor Yellow
        }
        else {
            throw "Tag $tag existiert lokal auf einem anderen Commit. Entfernen mit: git tag -d $tag`nOder Version in version.py erhoehen."
        }
    }

    $onRemote = git ls-remote --tags $Remote $tag 2>$null
    if ($onRemote) {
        throw "Tag $tag existiert bereits auf $Remote. Fuer ein neues Release: version.py erhoehen oder Remote-Tag loeschen (git push $Remote --delete $tag)."
    }

    if ($DryRun) {
        Write-Host "[DryRun] git tag -a $tag -m `"Release $tag`"" -ForegroundColor Yellow
        Write-Host "[DryRun] git push $Remote $tag" -ForegroundColor Yellow
        return
    }

    git tag -a $tag -m "Release $tag"
    if ($LASTEXITCODE -ne 0) {
        throw "git tag fehlgeschlagen."
    }
    git push $Remote $tag
    if ($LASTEXITCODE -ne 0) {
        throw "git push fehlgeschlagen."
    }
    Write-Host "Release-Tag $tag wurde nach $Remote gepusht." -ForegroundColor Green
}
finally {
    Pop-Location
}
