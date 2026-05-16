; Inno Setup — FT-991A Audio-Profilmanager (Deutsch)
; Kompilieren (nach PyInstaller-Build):
;   ISCC.exe /DMyAppVersion=1.0 Installer.iss
; Version aus version.py:  .\installer.ps1

#define MyAppName "FT-991A Audio-Profilmanager"
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
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#MyProjDir}\dist\installer
OutputBaseFilename=FT991AudioManager-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Eigene Wizard-Bilder (wie RotorTcpBridge) — später ergänzen:
;   Installer.png      (~164×314, Willkommen/Fertig links)
;   InstallerSmall.png (quadratisch, oben rechts auf den anderen Seiten)
; WizardImageFile={#MyProjDir}\Installer.png
; WizardSmallImageFile={#MyProjDir}\InstallerSmall.png
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
; PyInstaller onedir: EXE, _internal\, logo.ico, logo.svg
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; IconFilename: "{app}\logo.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"

[Run]
Filename: "{app}\{#MyExeName}"; Description: "{#MyAppName} starten"; Flags: nowait postinstall skipifsilent unchecked

[Code]
procedure UninstallOldVersion();
var
  sRegKey:     String;
  sUninstall:  String;
  iResultCode: Integer;
begin
  sRegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
             '{#MyAppId}' + '_is1';

  sUninstall := '';
  if not RegQueryStringValue(HKLM64, sRegKey, 'UninstallString', sUninstall) then
    RegQueryStringValue(HKLM, sRegKey, 'UninstallString', sUninstall);

  if sUninstall <> '' then
  begin
    sUninstall := RemoveQuotes(sUninstall);
    Exec(sUninstall, '/SILENT /NORESTART', '', SW_HIDE,
         ewWaitUntilTerminated, iResultCode);
  end;
end;

function InitializeSetup(): Boolean;
begin
  UninstallOldVersion();
  Result := True;
end;
