; Inno Setup 6 script for the Windows installer.
; Build (after PyInstaller has produced dist\WhatDidIRun):
;   iscc /DAppVersion=0.1.2 packaging\windows\whatdidirun.iss
; Output: dist\installer\WhatDidIRun-<version>-setup.exe
;
; Per-user install (no admin): %LOCALAPPDATA%\Programs\WhatDidIRun, added to the user PATH.
; Your data (%LOCALAPPDATA%\recall) is kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "What did I Run ??"
#define AppExe "WhatDidIRun.exe"
#define SrcDir "..\..\dist\WhatDidIRun"

[Setup]
AppId={{E1A907B0-7C20-4A0A-B0BB-42ACBCE4C108}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=vedkcoder
AppPublisherURL=https://github.com/vedkcoder/whatdidirun
AppSupportURL=https://github.com/vedkcoder/whatdidirun/issues
DefaultDirName={localappdata}\Programs\WhatDidIRun
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\src\recall\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\..\dist\installer
OutputBaseFilename=WhatDidIRun-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "powershell"; Description: "Record commands in PowerShell and bind Ctrl-G (edits your PowerShell profile)"
Name: "autostart"; Description: "Start {#AppName} in the system tray when I sign in"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
    Check: NeedsAddPath(ExpandConstant('{app}'))
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "WhatDidIRun"; \
    ValueData: """{app}\{#AppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\recall.exe"; Parameters: "init powershell --install"; Flags: runhidden waituntilterminated; \
    Tasks: powershell; StatusMsg: "Adding the PowerShell hook..."
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#AppExe}"; Flags: runhidden; RunOnceId: "StopTray"
Filename: "{app}\recall.exe"; Parameters: "init powershell --uninstall"; Flags: runhidden waituntilterminated; \
    RunOnceId: "RemoveProfileHook"

[Code]
function NeedsAddPath(Dir: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Lowercase(Dir) + ';', ';' + Lowercase(Paths) + ';') = 0;
end;

procedure RemovePath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Lowercase(Dir) + ';', Lowercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { Stop a running tray app so its files can be replaced on upgrade. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePath(ExpandConstant('{app}'));
end;
