<#
.SYNOPSIS
    Entfernt unnoetige PySide6-Teile aus dem PyInstaller-onedir-Bundle.

.DESCRIPTION
    Nach einem schlanken Spec (ohne collect_submodules PySide6) bleiben oft
    noch qml/, translations/ und ueberfluessige plugins/ uebrig.
    Gibt die eingesparte Groesse in MB zurueck.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $DistAppDir
)

Set-StrictMode -Version Latest

if (-not (Test-Path $DistAppDir)) {
    throw "Dist-Ordner nicht gefunden: $DistAppDir"
}

function Get-DirSizeMB {
    param([string] $Path)
    if (-not (Test-Path $Path)) { return 0.0 }
    $sum = (Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if (-not $sum) { return 0.0 }
    return [math]::Round($sum / 1MB, 1)
}

$before = Get-DirSizeMB $DistAppDir
$pyside = Join-Path $DistAppDir "_internal\PySide6"
if (-not (Test-Path $pyside)) {
    Write-Host "  (kein PySide6-Ordner, Trim uebersprungen)" -ForegroundColor DarkGray
    return 0.0
}

foreach ($subdir in @("qml", "translations", "resources", "qmltypes")) {
    $target = Join-Path $pyside $subdir
    if (Test-Path $target) {
        Remove-Item -Path $target -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$plugins = Join-Path $pyside "plugins"
$keepPlugins = @(
    "platforms",
    "styles",
    "imageformats",
    "multimedia",
    "tls",
    "networkinformation"
)
if (Test-Path $plugins) {
    Get-ChildItem -Path $plugins -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin $keepPlugins } |
        ForEach-Object {
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
}

$after = Get-DirSizeMB $DistAppDir
$saved = [math]::Round($before - $after, 1)
Write-Host "  Bundle-Trim: $before MB -> $after MB (entfernt ca. $saved MB)" -ForegroundColor Cyan
return $saved
