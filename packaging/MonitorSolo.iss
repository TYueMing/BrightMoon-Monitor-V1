; Compile only after the onedir build has passed its native smoke tests.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef BuildRoot
  #define BuildRoot "..\dist\MonitorSolo"
#endif

[Setup]
AppId={{CE50658E-62AC-48E1-8C15-D734E8F10150}
AppName=Bright Moon Monitor (Solo)
AppVersion={#AppVersion}
AppPublisher=Tao Yueming
DefaultDirName={localappdata}\Programs\MonitorSolo
DefaultGroupName=Bright Moon Monitor (Solo)
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
OutputDir=..\dist\installer
OutputBaseFilename=MonitorSolo-{#AppVersion}-windows-x64-setup
SetupIconFile=assets\MonitorSolo.ico
UninstallDisplayIcon={app}\MonitorSolo.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter=MonitorSolo.exe
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Bright Moon Monitor (Solo)"; Filename: "{app}\MonitorSolo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\MonitorSolo.exe"
Name: "{autodesktop}\Bright Moon Monitor (Solo)"; Filename: "{app}\MonitorSolo.exe"; WorkingDir: "{app}"; IconFilename: "{app}\MonitorSolo.exe"; Tasks: desktopicon
Name: "{group}\User Guide"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\_internal\docs\INSTALLED_USER_GUIDE.md"""
Name: "{group}\Uninstall Bright Moon Monitor (Solo)"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\MonitorSolo.exe"; Description: "{cm:LaunchProgram,Bright Moon Monitor (Solo)}"; Flags: nowait postinstall skipifsilent

; Deliberately no UninstallDelete for AppData, agent directories or user files.

[UninstallRun]
Filename: "{app}\MonitorSolo.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "QuitMonitorSolo"

[Code]
var
  ClearPersonalData: Boolean;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\MonitorSolo.exe')) then
  begin
    if not Exec(ExpandConstant('{app}\MonitorSolo.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := '无法关闭正在运行的 Solo，请先从托盘退出后再安装。'
    else if ExitCode <> 0 then
      Result := 'Solo 尚未退出，请稍后重新安装。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExistingCommand: String;
begin
  if CurStep = ssPostInstall then
    if RegQueryStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run',
      'BrightMoonMonitorSolo', ExistingCommand) then
      RegWriteStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run',
        'BrightMoonMonitorSolo', '"' + ExpandConstant('{app}\MonitorSolo.exe') + '" --background');
end;

function InitializeUninstall(): Boolean;
var
  ExitCode: Integer;
begin
  Result := False;
  if FileExists(ExpandConstant('{app}\MonitorSolo.exe')) then
  begin
    if not Exec(ExpandConstant('{app}\MonitorSolo.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Exit;
    if ExitCode <> 0 then
    begin
      MsgBox('Solo 尚未退出，请从托盘退出后重试卸载。', mbError, MB_OK);
      Exit;
    end;
  end;
  ClearPersonalData := False;
  if not UninstallSilent then
    ClearPersonalData := MsgBox('是否同时删除个人设置、待办、壁纸和自定义角色？' + #13#10 +
      '选择“否”将保留这些数据，方便重新安装后继续使用。',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run', 'BrightMoonMonitorSolo');
    RegDeleteValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run', 'BrightMoonMonitorSolo');
    if ClearPersonalData then
      DelTree(ExpandConstant('{localappdata}\MonitorSolo\data'), True, True, True);
  end;
end;
