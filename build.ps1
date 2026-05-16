<#
.SYNOPSIS
    Erzeugt eine standalone Windows-EXE des FT-991A Audio-Profilmanagers.

.DESCRIPTION
    Baut mit PyInstaller eine "onedir"-Distribution unter
    "dist\<AppName>\". Der Zielrechner braucht KEIN Python -- alle
    Bibliotheken (PySide6, pyserial, Qt-Plugins) liegen mit im Ordner.

    Ablauf:
      1. Falls noch kein Build-venv vorhanden ist, wird ".venv-build"
         neben dieser Datei angelegt.
      2. requirements.txt + PyInstaller werden in dieses venv
         installiert (das System-Python bleibt unangetastet).
      3. PyInstaller baut das Bundle mit "--windowed" (reine GUI-App,
         kein Konsolenfenster).

    Ergebnis:
      dist\FT991AudioManager\FT991AudioManager.exe  + _internal\

    Diesen Ordner einfach komplett auf einen anderen Rechner kopieren.
    Beim ersten Start legt die App "data\settings.json" und
    "data\presets.json" NEBEN der EXE an (Defaults: Dark Mode an,
    Polling 100 ms, vier Beispiel-Profile).

.PARAMETER Python
    Python-Executable, mit dem das Build-venv erstellt wird. Wenn
    weggelassen, wird "python" aus dem PATH benutzt.

.PARAMETER VenvPath
    Pfad zum Build-venv. Default: ".venv-build" neben dieser Datei.

.PARAMETER AppName
    Name der erzeugten EXE / des Distributionsordners. Default:
    "FT991AudioManager".

.PARAMETER KeepConsole
    Wenn gesetzt, behaelt die EXE ein Konsolenfenster offen -- praktisch
    fuers Debugging (Tracebacks landen dort).

.PARAMETER Clean
    Vor dem Build werden "build\", "dist\" und die "*.spec"-Datei
    geloescht.

.EXAMPLE
    .\build.ps1
    Baut mit Standard-Einstellungen.

.EXAMPLE
    .\build.ps1 -Clean
    Saubere Neuerstellung (loescht alte Build-Artefakte vorher).

.EXAMPLE
    .\build.ps1 -Python "C:\Python313\python.exe" -KeepConsole
    Nutzt ein konkretes Python und laesst die Konsole sichtbar.

.NOTES
    Das Skript MUSS aus seinem eigenen Ordner aufgerufen werden (oder
    via vollem Pfad). Es wechselt intern nicht das Verzeichnis dauerhaft.

    Die Datei ist bewusst ohne Umlaute geschrieben, damit sie unter
    Windows PowerShell 5.1 ohne BOM zuverlaessig parsebar ist.
#>

[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$VenvPath = ".venv-build",
    [string]$AppName = "FT991AudioManager",
    [switch]$KeepConsole,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Wo liegt dieses Skript? Alle relativen Pfade gehen von hier aus.
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ">> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    # Ruft eine externe EXE auf und wirft, wenn $LASTEXITCODE != 0.
    #
    # Wichtig: Wir splatten ($ExeArgs) sowohl bei der Annahme als auch
    # beim eigentlichen Aufruf von & $Exe. Wuerden wir das nicht tun,
    # wuerde PowerShell ein uebergebenes Array zu einem einzigen
    # Whitespace-getrennten String zusammenklatschen -- dann sieht z. B.
    # python.exe die ganzen "-m PyInstaller --noconfirm ..."-Argumente
    # als EINEN Modul-Namen.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][string]$ErrorMessage,
        [Parameter(Mandatory, Position = 1)][string]$Exe,
        [Parameter(ValueFromRemainingArguments = $true)]$ExeArgs
    )
    & $Exe @ExeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage (exit code $LASTEXITCODE)"
    }
}

Push-Location $Here
try {
    # ------------------------------------------------------------------
    # Clean-Up vor dem Build
    #
    # Wir loeschen `dist\<AppName>` und `build\` **immer** vor dem
    # PyInstaller-Lauf. PyInstaller stolpert in der COLLECT-Phase
    # gelegentlich ueber halb-bereinigte Vorzustaende -- besonders, wenn
    # Defender o.ae. dazwischengefunkt hat. Mit `-Clean` raeumen wir
    # zusaetzlich noch die `.spec`-Datei weg, damit eine wirklich
    # frische Build-Konfiguration generiert wird.
    # ------------------------------------------------------------------
    $DistAppDir = Join-Path (Join-Path $Here "dist") $AppName
    $BuildDir = Join-Path $Here "build"
    foreach ($p in @($DistAppDir, $BuildDir)) {
        if (Test-Path $p) {
            Write-Step "Loesche $p"
            Remove-Item -Recurse -Force $p
        }
    }
    if ($Clean) {
        $spec = Join-Path $Here "$AppName.spec"
        if (Test-Path $spec) {
            Write-Step "Loesche $AppName.spec"
            Remove-Item -Force $spec
        }
    }

    # ------------------------------------------------------------------
    # Python pruefen
    # ------------------------------------------------------------------
    Write-Step "Pruefe Python-Installation ($Python)"
    & $Python --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python '$Python' wurde nicht gefunden. Tipp: '-Python C:\Pfad\zu\python.exe' uebergeben."
    }

    # ------------------------------------------------------------------
    # Build-venv erstellen (falls noetig)
    # ------------------------------------------------------------------
    $VenvFull = Join-Path $Here $VenvPath
    $VenvPython = Join-Path $VenvFull "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Step "Erstelle Build-venv: $VenvFull"
        Invoke-Checked "venv-Erstellung fehlgeschlagen" $Python "-m" "venv" $VenvFull
    } else {
        Write-Step "Bestehendes Build-venv wird benutzt: $VenvFull"
    }

    # ------------------------------------------------------------------
    # pip + Abhaengigkeiten
    # ------------------------------------------------------------------
    Write-Step "Aktualisiere pip"
    Invoke-Checked "pip-Upgrade fehlgeschlagen" $VenvPython "-m" "pip" "install" "--upgrade" "pip"

    $Req = Join-Path $Here "requirements.txt"
    if (-not (Test-Path $Req)) {
        throw "requirements.txt nicht gefunden unter $Req"
    }
    Write-Step "Installiere Projekt-Abhaengigkeiten (requirements.txt)"
    Invoke-Checked "Install der requirements.txt fehlgeschlagen" $VenvPython "-m" "pip" "install" "-r" $Req

    Write-Step "Installiere PyInstaller"
    Invoke-Checked "PyInstaller-Install fehlgeschlagen" $VenvPython "-m" "pip" "install" "pyinstaller>=6.0"

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    $WindowFlag = if ($KeepConsole) { "--console" } else { "--windowed" }
    $MainPy = Join-Path $Here "main.py"
    if (-not (Test-Path $MainPy)) {
        throw "main.py nicht gefunden unter $MainPy"
    }

    $VersionFile = Join-Path $Here "version.py"
    if (Test-Path $VersionFile) {
        $verContent = Get-Content -Path $VersionFile -Raw -Encoding UTF8
        if ($verContent -match 'APP_VERSION\s*=\s*"([^"]+)"') {
            Write-Host " Version: $($Matches[1])" -ForegroundColor Cyan
        }
    }

    Write-Step "Baue $AppName (onedir, $WindowFlag)"
    $IconIco = Join-Path $Here "logo.ico"
    $IconSvg = Join-Path $Here "logo.svg"
    if (-not (Test-Path $IconIco)) {
        throw "logo.ico nicht gefunden unter $IconIco"
    }
    $SpecFile = Join-Path $Here "$AppName.spec"
    if (-not (Test-Path $SpecFile)) {
        throw "$AppName.spec nicht gefunden unter $SpecFile"
    }
    $PyInstallerArgs = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", (Join-Path $Here "dist"),
        "--workpath", (Join-Path $Here "build")
    )
    if ($KeepConsole) {
        $PyInstallerArgs += "--console"
    }
    $PyInstallerArgs += $SpecFile
    # @-Splatting beim Aufruf, sonst sieht Python alle Argumente als
    # einen einzigen Modul-Namen-String. Siehe Doku zu Invoke-Checked.
    Invoke-Checked "PyInstaller-Build fehlgeschlagen" $VenvPython @PyInstallerArgs

    # ------------------------------------------------------------------
    # Ergebnis melden
    # ------------------------------------------------------------------
    $OutDir = Join-Path $Here "dist\$AppName"
    $ExePath = Join-Path $OutDir "$AppName.exe"
    if (-not (Test-Path $ExePath)) {
        # Diagnose-Hilfe: Wenn die PyInstaller-EXE im build\-Tree
        # noch existiert, aber im dist\-Tree fehlt, ist das fast immer
        # ein Antivirus-Eingriff (typischerweise Windows Defender, der
        # den PyInstaller-Bootloader runw.exe falsch-positiv erkennt).
        $BuildExe = Join-Path $Here ("build\" + $AppName + "\" + $AppName + ".exe")
        if (Test-Path $BuildExe) {
            $msg = @(
                "Build lief durch, aber EXE fehlt unter '$ExePath'.",
                "Im build\-Tree existiert die EXE noch ($BuildExe) -- das ist",
                "ein klassisches Anzeichen, dass dein Antivirus die EXE beim",
                "Kopieren nach dist\ entfernt hat (PyInstaller-Bootloader wird",
                "haeufig falsch-positiv erkannt).",
                "",
                "Loesung: In einer Administrator-PowerShell einmalig",
                "  Add-MpPreference -ExclusionPath '$Here'",
                "ausfuehren und dann den Build erneut starten."
            ) -join [System.Environment]::NewLine
            throw $msg
        }
        throw "Build abgeschlossen, aber EXE nicht gefunden unter '$ExePath'."
    }

    Write-Host ""
    Write-Host "===========================================================" -ForegroundColor Green
    Write-Host " Build erfolgreich" -ForegroundColor Green
    Write-Host "===========================================================" -ForegroundColor Green
    Write-Host " EXE   : $ExePath" -ForegroundColor Green
    Write-Host " Ordner: $OutDir" -ForegroundColor Green
    Write-Host ""
    Write-Host " Den Ordner '$OutDir' komplett auf einen anderen Rechner" -ForegroundColor Green
    Write-Host " kopieren -- die EXE laeuft dort ohne Python-Installation." -ForegroundColor Green
    Write-Host ""
    Write-Host " Beim ersten Start legt die App neben der EXE an:" -ForegroundColor Green
    Write-Host "   data\settings.json   (Defaults: Dark Mode an)" -ForegroundColor Green
    Write-Host "   data\presets.json    (4 Beispiel-Profile)" -ForegroundColor Green
    Write-Host "===========================================================" -ForegroundColor Green
}
finally {
    Pop-Location
}
