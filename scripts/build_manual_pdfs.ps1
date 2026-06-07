<#
.SYNOPSIS
    Erzeugt PDF-Handbuecher aus Bedienungsanleitung.md und UserManual_EN.md.

.DESCRIPTION
    Benoetigt pandoc und wkhtmltopdf (lokal oder in GitHub Actions via Chocolatey).
    Ausgabe nach dist/manuals/ mit versionsbezogenen Dateinamen.

.PARAMETER Version
    APP_VERSION ohne fuehrendes v (z. B. 1.9.5).

.PARAMETER OutputDir
    Zielordner fuer die PDF-Dateien (Standard: dist/manuals im Projektroot).

.PARAMETER ProjectRoot
    Projektroot mit den Markdown-Dateien (Standard: Parent von scripts/).

.EXAMPLE
    .\scripts\build_manual_pdfs.ps1 -Version 1.9.5
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Version,

    [string] $OutputDir = "",
    [string] $ProjectRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not $OutputDir) {
    $OutputDir = Join-Path $ProjectRoot "dist\manuals"
}

function Resolve-PdfTool {
    param(
        [string] $Name,
        [string[]] $ExtraPaths = @()
    )

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    foreach ($candidate in $ExtraPaths) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "$Name nicht gefunden. Installiere z. B. mit: choco install pandoc wkhtmltopdf -y"
}

$pandoc = Resolve-PdfTool -Name "pandoc"
$wkhtml = Resolve-PdfTool -Name "wkhtmltopdf" -ExtraPaths @(
    "${env:ProgramFiles}\wkhtmltopdf\bin\wkhtmltopdf.exe",
    "${env:ProgramFiles(x86)}\wkhtmltopdf\bin\wkhtmltopdf.exe"
)

$css = Join-Path $ProjectRoot "docs\manual-pdf.css"
if (-not (Test-Path $css)) {
    throw "CSS fuer PDF fehlt: $css"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$manuals = @(
    @{
        Input  = "Bedienungsanleitung.md"
        Output = "FT991AudioManager-Bedienungsanleitung-$Version.pdf"
        Lang   = "de"
    },
    @{
        Input  = "UserManual_EN.md"
        Output = "FT991AudioManager-UserManual-$Version.pdf"
        Lang   = "en"
    }
)

Write-Host "PDF-Tools:" -ForegroundColor Cyan
Write-Host "  pandoc:      $pandoc"
Write-Host "  wkhtmltopdf: $wkhtml"
Write-Host "  Ausgabe:     $OutputDir"
Write-Host ""

foreach ($manual in $manuals) {
    $inputPath = Join-Path $ProjectRoot $manual.Input
    $outputPath = Join-Path $OutputDir $manual.Output

    if (-not (Test-Path $inputPath)) {
        throw "Handbuch fehlt: $inputPath"
    }

    Write-Host "Erzeuge $($manual.Output) ..." -ForegroundColor Green

    & $pandoc $inputPath `
        -o $outputPath `
        --from=gfm `
        --pdf-engine=$wkhtml `
        --pdf-engine-opt=--enable-local-file-access `
        --resource-path=$ProjectRoot `
        --css=$css `
        -V lang:$($manual.Lang)

    if ($LASTEXITCODE -ne 0) {
        throw "pandoc fehlgeschlagen fuer $($manual.Input) (Exit $LASTEXITCODE)."
    }
    if (-not (Test-Path $outputPath)) {
        throw "PDF wurde nicht erzeugt: $outputPath"
    }

    $sizeKb = [math]::Round((Get-Item $outputPath).Length / 1KB, 1)
    Write-Host "  OK ($sizeKb KB): $outputPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Handbuch-PDFs fertig." -ForegroundColor Green
