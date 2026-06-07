<#
.SYNOPSIS
    Liest APP_VERSION aus version.py und triggert per Git-Tag einen GitHub-Actions-Release-Build.

.DESCRIPTION
    Der Workflow (.github/workflows/build-windows.yml) startet den Release-Job nur bei einem
    Push eines Tags, der mit "v" beginnt (z. B. v1.1). Auf GitHub werden dann erzeugt:

      - FT991AudioManager-Setup-<Version>.exe  (Inno Setup, empfohlen)
      - FT991AudioManager-v<Version>-Windows.zip (portable)
      - FT991AudioManager-Bedienungsanleitung-<Version>.pdf (Deutsch)
      - FT991AudioManager-UserManual-<Version>.pdf (Englisch)

    Das Skript erzeugt den Tag v<APP_VERSION> und fuehrt "git push origin <Tag>" aus.
    Vorher APP_VERSION und APP_DATE in version.py anpassen; Aenderungen committen und
    auf main pushen, damit der Tag auf dem richtigen Stand liegt.

.PARAMETER Remote
    Git-Remote-Name (Standard: origin).

.PARAMETER DryRun
    Zeigt nur Version, Tag und die geplanten Befehle; kein git tag / git push.

.PARAMETER Force
    Bei schmutzigem Arbeitsverzeichnis keine Rueckfrage.

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
$WorkflowFile = Join-Path $ProjectRoot ".github\workflows\build-windows.yml"

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

# Workflow: tags v* - Inno-Version ohne fuehrendes v, Git-Tag mit v
$tag = "v$appVersion"
$setupName = "FT991AudioManager-Setup-$appVersion.exe"
$zipName = "FT991AudioManager-$tag-Windows.zip"
$manualDeName = "FT991AudioManager-Bedienungsanleitung-$appVersion.pdf"
$manualEnName = "FT991AudioManager-UserManual-$appVersion.pdf"
$buildManualsScript = Join-Path $ProjectRoot "scripts\build_manual_pdfs.ps1"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path (Join-Path $ProjectRoot ".git"))) {
        throw "Kein Git-Repository im Projektroot: $ProjectRoot"
    }

    if (-not (Test-Path $WorkflowFile)) {
        throw "Release-Workflow fehlt: $WorkflowFile`nOhne diese Datei baut GitHub keinen Installer."
    }
    $wfRaw = Get-Content -Path $WorkflowFile -Raw -Encoding UTF8
    if ($wfRaw -notmatch 'innosetup|ISCC') {
        Write-Warning "build-windows.yml: kein Inno-Setup-Schritt erkannt - Installer auf GitHub evtl. nicht verfuegbar."
    }

    $dirty = (git status --porcelain 2>$null)
    if ($dirty -and -not $Force -and -not $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer (uncommittete Aenderungen). Der Tag zeigt nur auf den letzten Commit, nicht auf ungespeicherte Dateien."
        $null = Read-Host "Enter zum Fortfahren oder Strg+C zum Abbrechen"
    }
    elseif ($dirty -and $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer: vor echtem Release committen, sonst fehlen Aenderungen im Tag."
    }

    $head = (git rev-parse --short HEAD 2>$null)
    $remoteUrl = (git remote get-url $Remote 2>$null)

    Write-Host "Projekt:     $ProjectRoot" -ForegroundColor Cyan
    Write-Host "APP_VERSION: $appVersion" -ForegroundColor Cyan
    Write-Host "Git-Tag:     $tag  (HEAD: $head)" -ForegroundColor Cyan
    Write-Host "Remote:      $Remote" -ForegroundColor Cyan
    if ($remoteUrl) {
        Write-Host "Repo-URL:    $remoteUrl" -ForegroundColor Cyan
    }
    Write-Host ""
    Write-Host "Nach dem Push erstellt GitHub Actions:" -ForegroundColor Cyan
    Write-Host "  - dist/installer/$setupName" -ForegroundColor Cyan
    Write-Host "  - dist/$zipName" -ForegroundColor Cyan
    Write-Host "  - dist/manuals/$manualDeName" -ForegroundColor Cyan
    Write-Host "  - dist/manuals/$manualEnName" -ForegroundColor Cyan
    Write-Host ""

    if (Test-Path $buildManualsScript) {
        $pandocCmd = Get-Command pandoc -ErrorAction SilentlyContinue
        if ($pandocCmd) {
            Write-Host "Lokale Vorab-Pruefung: Handbuch-PDFs erzeugen ..." -ForegroundColor Cyan
            & $buildManualsScript -Version $appVersion
            Write-Host "Lokale PDFs in dist/manuals/ (werden nicht mit dem Tag gepusht)." -ForegroundColor Yellow
            Write-Host ""
        }
        elseif (-not $DryRun) {
            Write-Host "Pandoc lokal nicht installiert - PDFs werden in GitHub Actions erzeugt." -ForegroundColor Yellow
            Write-Host ""
        }
    }

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
        Write-Host '[DryRun] Geplante Befehle:' -ForegroundColor Magenta
        if (-not $existingLocal) {
            Write-Host "  git tag $tag" -ForegroundColor Magenta
        }
        Write-Host "  git push $Remote $tag" -ForegroundColor Magenta
        if (Test-Path $buildManualsScript) {
            Write-Host "  (CI) scripts/build_manual_pdfs.ps1 -Version $appVersion" -ForegroundColor Magenta
        }
        Write-Host "`nKeine Aenderungen ausgefuehrt." -ForegroundColor Magenta
        return
    }

    if (-not $existingLocal) {
        Write-Host "git tag $tag ..." -ForegroundColor Green
        git tag $tag
        if ($LASTEXITCODE -ne 0) { throw "git tag fehlgeschlagen (Exit $LASTEXITCODE)." }
    }

    Write-Host "git push $Remote $tag ..." -ForegroundColor Green
    git push $Remote $tag
    if ($LASTEXITCODE -ne 0) { throw "git push fehlgeschlagen (Exit $LASTEXITCODE)." }

    Write-Host ""
    Write-Host "Fertig. GitHub Actions sollte jetzt den Release-Workflow starten (ZIP, Inno Setup, Handbuch-PDFs)." -ForegroundColor Green
    Write-Host "Auf GitHub: Repository oeffnen, Tab Actions, Workflow Build Windows EXE, danach Releases." -ForegroundColor Green
    Write-Host "Erwartete Assets: $setupName, $zipName, $manualDeName, $manualEnName" -ForegroundColor Green
}
finally {
    Pop-Location
}
