#define AppName "HC User Management"
#define AppVersion "0.1.0"
[Setup]
AppId={{1A0E5F74-449E-4AA8-A6CD-650209D67B20}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\HCUserManagement
DefaultGroupName={#AppName}
OutputBaseFilename=HCUserManagement-Setup
OutputDir=..\..\artifacts\windows-native
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.20348
SetupLogging=yes
UninstallDisplayIcon={app}\portal\app\static\favicon.ico
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
WizardStyle=modern

[Files]
Source: "build\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "*.env,*.pem,*.key"
Source: "env.production.native.example"; DestDir: "{commonappdata}\HCUserManagement\config"; Flags: onlyifdoesntexist
Source: "common.ps1"; Flags: dontcopy
Source: "backup-data.ps1"; Flags: dontcopy
Source: "preflight.ps1"; Flags: dontcopy

[Dirs]
Name: "{commonappdata}\HCUserManagement\data"
Name: "{commonappdata}\HCUserManagement\config"
Name: "{commonappdata}\HCUserManagement\certificates"
Name: "{commonappdata}\HCUserManagement\logs"
Name: "{commonappdata}\HCUserManagement\backups"
Name: "{commonappdata}\HCUserManagement\iis"

[Code]
var
  InstallFailed: Boolean;

function InitializeSetup(): Boolean;
var ProductType: String;
begin
  Result := RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\ProductOptions', 'ProductType', ProductType)
    and (ProductType = 'ServerNT');
  if not Result then MsgBox('Installer ini hanya untuk Windows Server 2022/2025 x64.', mbError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
  Result := '';
  ExtractTemporaryFile('common.ps1');
  ExtractTemporaryFile('backup-data.ps1');
  ExtractTemporaryFile('preflight.ps1');
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\preflight.ps1') +
    '" -InstallDir "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := 'Gagal menjalankan pemeriksaan awal server. Lihat %TEMP%\HCUserManagement-preflight.log.'
  else if ExitCode <> 0 then
    Result := 'Pemeriksaan awal server gagal (kode ' + IntToStr(ExitCode) + '). Lihat %TEMP%\HCUserManagement-preflight.log.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ExitCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\finalize-install.ps1') +
    '" -InstallDir "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    InstallFailed := True
  else if ExitCode <> 0 then
    InstallFailed := True;
  if InstallFailed then
    MsgBox('Finalisasi instalasi gagal. Periksa C:\ProgramData\HCUserManagement\logs\install-errors.log. Installer akan keluar dengan kode gagal.', mbError, MB_OK);
end;

function GetCustomSetupExitCode(): Integer;
begin
  if InstallFailed then Result := 10 else Result := 0;
end;

function InitializeUninstall(): Boolean;
var ExitCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\uninstall-services.ps1') +
    '" -InstallDir "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  if Result then Result := ExitCode = 0;
  if not Result then MsgBox('Service belum berhasil dilepas. Uninstall dibatalkan agar kode tidak dihapus saat service masih berjalan.', mbError, MB_OK);
end;
