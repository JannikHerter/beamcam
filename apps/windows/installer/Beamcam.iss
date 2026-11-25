; The Beamcam installer: one file, a few clicks, no terminal. Build on Windows with Inno Setup 6:
;   iscc installer\Beamcam.iss        (expects the built app in out\app, see README.md)
; Saved as UTF-8 with a byte order mark, which Inno Setup needs for the German texts.
; It needs administrator rights once, because Windows only loads virtual camera sources that are
; registered for the whole computer (HKLM), from a folder the camera service can read.

#define AppVersion "0.2.0"

[Setup]
AppId={{6B1A7C52-3E0B-4B8E-9F57-2A1C4E7D9B30}
AppName=Beamcam
AppVersion={#AppVersion}
AppVerName=Beamcam {#AppVersion}
AppPublisher=Beamcam
AppCopyright=© 2026 Beamcam contributors
DefaultDirName={autopf}\Beamcam
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\out
OutputBaseFilename=Beamcam-Setup-{#AppVersion}
SetupIconFile=..\src\Beamcam\Assets\beamcam.ico
UninstallDisplayIcon={app}\Beamcam.exe
UninstallDisplayName=Beamcam
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

ShowLanguageDialog=auto

; Setup follows the Windows display language, like the app. German: informal "du", like the app.
[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

; Only the pages people see: Inno's stock German says "Sie".
[Messages]
en.FinishedHeadingLabel=Beamcam is installed
de.FinishedHeadingLabel=Beamcam ist installiert
en.FinishedLabel=Open Beamcam here and on your phone, then click Connect.
de.FinishedLabel=Öffne Beamcam hier und auf deinem Handy und klicke dann auf „Verbinden“.
en.WizardReady=Ready to install
en.ReadyLabel1=Beamcam is ready to be installed on your computer.
en.ReadyLabel2b=Click Install to start.
en.ClickFinish=
de.ClickFinish=
de.WizardReady=Bereit zur Installation
de.ReadyLabel1=Beamcam kann jetzt auf deinem Computer installiert werden.
de.ReadyLabel2b=Klicke auf „Installieren“, um loszulegen.
de.WizardInstalling=Wird installiert
de.InstallingLabel=Einen Moment, Beamcam wird installiert.
de.ConfirmUninstall=Möchtest du Beamcam von diesem Computer entfernen?
de.UninstallStatusLabel=Einen Moment, Beamcam wird entfernt.
de.UninstalledAll=Beamcam wurde von diesem Computer entfernt.

[CustomMessages]
en.FindPhone=Allowing Beamcam to find your phone…
de.FindPhone=Beamcam darf dein Handy finden…
en.OpenBeamcam=Open Beamcam
de.OpenBeamcam=Beamcam öffnen
en.NeedsWindows11=Beamcam needs Windows 11. This computer has an older Windows.
de.NeedsWindows11=Beamcam braucht Windows 11. Auf diesem Computer läuft ein älteres Windows.

[Files]
Source: "..\out\app\*"; Excludes: "BeamcamCamera.dll,*.pdb"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "..\out\app\BeamcamCamera.dll"; DestDir: "{app}"; Flags: ignoreversion regserver
Source: "..\..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Beamcam"; Filename: "{app}\Beamcam.exe"

[Run]
; Phones announce themselves on the local network. Answers to Beamcam's own questions get through
; Windows' firewall anyway; this rule also lets the phones' unasked announcements in. Local network only.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Beamcam"""; Flags: runhidden; StatusMsg: "{cm:FindPhone}"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Beamcam"" dir=in action=allow protocol=UDP remoteip=localsubnet program=""{app}\Beamcam.exe"" enable=yes"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Beamcam"" dir=in action=allow protocol=UDP remoteip=localsubnet program=""{app}\beamcam-cli.exe"" enable=yes"; Flags: runhidden
Filename: "{app}\Beamcam.exe"; Description: "{cm:OpenBeamcam}"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Beamcam"""; Flags: runhidden; RunOnceId: "firewall"

[Code]
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := (Version.Major > 10) or ((Version.Major = 10) and (Version.Build >= 22000));
  if not Result then
    MsgBox(CustomMessage('NeedsWindows11'), mbError, MB_OK);
end;

// The camera service keeps the camera's DLL loaded; stop it so an update can replace the file.
// Windows starts it again the next time an app opens a camera.
procedure StopCameraService();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\net.exe'), 'stop FrameServer /y', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopCameraService();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // A running app would keep its files, and the camera, from being removed.
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im Beamcam.exe /im beamcam-cli.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    StopCameraService();
  end;
end;
