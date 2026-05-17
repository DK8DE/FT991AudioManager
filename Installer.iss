; Inno Setup — FT-991/A Audiomanager (Deutsch)
; Kompilieren (nach PyInstaller-Build):
;   ISCC.exe /DMyAppVersion=1.0 Installer.iss
; Version aus version.py:  .\installer.ps1

#define MyAppName "FT-991/A Audiomanager"
; Kein "/" im Namen — Inno [Icons] Name: und DefaultGroupName werten "/" als
; Unterordner (Desktop: Ordner "FT-991" + "A Audiomanager.lnk").
#define MyAppShortcutName "FT-991-A Audiomanager"
#ifndef MyAppVersion
  #define MyAppVersion "1.0"
#endif
#define MyAppPublisher "Joerg Koerner DK8DE"
#define MyAppURL "https://github.com/DK8DE/FT991AudioManager"
#ifndef MyProjDir
  #define MyProjDir SourcePath
#endif
#define MySourceDir MyProjDir + "/dist/FT991AudioManager"
#define MyExeName "FT991AudioManager.exe"
#define MyAppIcon MyProjDir + "/logo.ico"
; AppId NIEMALS ändern — wird für Upgrade/Deinstallation benötigt
#define MyAppId "E7A91C32-5B4F-4D2E-9A1C-8F3D2E1B0A94"

[Setup]
AppId={{{#MyAppId}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}.0.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\FT991AudioManager
DefaultGroupName={#MyAppShortcutName}
DisableProgramGroupPage=yes
OutputDir={#MyProjDir}\dist\installer
OutputBaseFilename=FT991AudioManager-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Eigene Wizard-Bilder (wie RotorTcpBridge) — später ergänzen:
;   Installer.png      (~164×314, Willkommen/Fertig links)
;   InstallerSmall.png (quadratisch, oben rechts auf den anderen Seiten)
WizardImageFile={#MyProjDir}\Installer.png
WizardSmallImageFile={#MyProjDir}\InstallerSmall.png
PrivilegesRequired=admin
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\logo.ico
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#MyProjDir}\LICENSE
CloseApplications=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Zusätzliche Symbole:"; Flags: unchecked

[Files]
; PyInstaller onedir: EXE + _internal\ (logo.ico liegt dort in _internal\, nicht im App-Root)
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Icon für Verknüpfungen, Deinstaller und Qt (Taskleiste): fest neben der EXE
Source: "{#MyAppIcon}"; DestDir: "{app}"; DestName: "logo.ico"; Flags: ignoreversion

[Icons]
; IconFilename muss auf eine existierende .ico zeigen — nicht nur auf die EXE
; Kurzname ohne "/" — siehe Kommentar bei MyAppShortcutName.
Name: "{group}\{#MyAppShortcutName}"; Filename: "{app}\{#MyExeName}"; IconFilename: "{app}\logo.ico"
Name: "{autodesktop}\{#MyAppShortcutName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyExeName}"; Description: "{#MyAppName} starten"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Programmordner restlos entfernen — auch Dateien, die der Installer
; nicht kennt (z. B. Logs, gecachte Daten, ggf. lokale data\settings.json,
; PyInstaller-Reste).
; %APPDATA%\FT991AudioManager bleibt davon unberuehrt — das liegt
; ausserhalb von {app} und wird vom Inno-Uninstaller nie angefasst.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}"

[Code]
function GetUninstallRegKey(): String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            '{#MyAppId}' + '_is1';
end;

function GetInstalledVersion(): String;
var
  sVer: String;
begin
  sVer := '';
  if not RegQueryStringValue(HKLM64, GetUninstallRegKey(), 'DisplayVersion', sVer) then
    RegQueryStringValue(HKLM, GetUninstallRegKey(), 'DisplayVersion', sVer);
  Result := sVer;
end;

function GetUninstallString(): String;
var
  sCmd: String;
begin
  sCmd := '';
  if not RegQueryStringValue(HKLM64, GetUninstallRegKey(), 'UninstallString', sCmd) then
    RegQueryStringValue(HKLM, GetUninstallRegKey(), 'UninstallString', sCmd);
  Result := sCmd;
end;

{ Numerischer Versionsvergleich. Liefert <0, 0 oder >0 (wie strcmp). }
function CompareVersionStrings(V1, V2: String): Integer;
var
  P, N1, N2: Integer;
  S1, S2: String;
begin
  Result := 0;
  S1 := V1;
  S2 := V2;
  while (Length(S1) > 0) or (Length(S2) > 0) do
  begin
    P := Pos('.', S1);
    if P > 0 then
    begin
      N1 := StrToIntDef(Copy(S1, 1, P - 1), 0);
      Delete(S1, 1, P);
    end
    else
    begin
      N1 := StrToIntDef(S1, 0);
      S1 := '';
    end;

    P := Pos('.', S2);
    if P > 0 then
    begin
      N2 := StrToIntDef(Copy(S2, 1, P - 1), 0);
      Delete(S2, 1, P);
    end
    else
    begin
      N2 := StrToIntDef(S2, 0);
      S2 := '';
    end;

    if N1 < N2 then begin Result := -1; Exit; end;
    if N1 > N2 then begin Result :=  1; Exit; end;
  end;
end;

{ Vorhandene Installation komplett im Hintergrund entfernen.
  Wartet auf das Hilfsprogramm und gibt dem Uninstaller noch kurz Zeit,
  den Programmordner final wegzuraeumen (Inno benutzt eine Helper-Kopie). }
procedure UninstallOldVersion();
var
  sUninstall:  String;
  iResultCode: Integer;
  iWait:       Integer;
begin
  sUninstall := GetUninstallString();
  if sUninstall = '' then Exit;

  sUninstall := RemoveQuotes(sUninstall);
  if not Exec(sUninstall,
              '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
              '', SW_HIDE, ewWaitUntilTerminated, iResultCode) then
    Exit;

  { Inno-Uninstaller startet sich ueber eine Kopie selbst. Diese kopierte
    EXE laeuft nach unserem ewWaitUntilTerminated noch ein wenig weiter,
    um sich selbst und den App-Ordner zu loeschen. Wir warten max. 5 s,
    bis der Programmordner tatsaechlich verschwunden ist. }
  iWait := 0;
  while (iWait < 50) and DirExists(ExpandConstant('{app}')) do
  begin
    Sleep(100);
    iWait := iWait + 1;
  end;
end;

function InitializeSetup(): Boolean;
var
  sInstalled: String;
  iCmp:       Integer;
begin
  Result := True;

  sInstalled := GetInstalledVersion();
  if sInstalled = '' then Exit;  { nichts installiert -> direkt installieren }

  iCmp := CompareVersionStrings(sInstalled, '{#MyAppVersion}');
  if iCmp > 0 then
  begin
    { Bereits eine NEUERE Version vorhanden — nur nach Bestaetigung downgraden. }
    if MsgBox(
         'Es ist bereits eine neuere Version (' + sInstalled +
         ') installiert.' #13#10 +
         'Trotzdem mit Version {#MyAppVersion} fortfahren (Downgrade)?',
         mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;

  { Altere, gleiche (oder bestaetigte neuere) Version: sauber im
    Hintergrund deinstallieren, dann ganz normal weiter mit der
    Installation der neuen Version. }
  UninstallOldVersion();
end;
