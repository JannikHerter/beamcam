; The Beamcam installer: one file, a few clicks, no terminal. Build on Windows with Inno Setup 6:
;   iscc installer\Beamcam.iss        (expects the built app in out\app, see README.md)
; It needs administrator rights once, because Windows only loads virtual camera sources that are
; registered for the whole computer (HKLM), from a folder the camera service can read.

#define AppVersion "0.1.0"

[Setup]
AppId={{6B1A7C52-3E0B-4B8E-9F57-2A1C4E7D9B30}
AppName=Beamcam
AppVersion={#AppVersion}
AppVerName=Beamcam {#AppVersion}
AppPublisher=Beamcam
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

[Messages]
WelcomeLabel2=Beamcam turns your Android phone into a webcam for this computer.%n%nAfter installing, open Beamcam here and on your phone, and click Connect.

[Tasks]
Name: desktopicon; Description: "Put Beamcam on the desktop"; Flags: unchecked

[Files]
Source: "..\out\app\*"; Excludes: "BeamcamCamera.dll,*.pdb"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
Source: "..\out\app\BeamcamCamera.dll"; DestDir: "{app}"; Flags: ignoreversion regserver

[Icons]
Name: "{autoprograms}\Beamcam"; Filename: "{app}\Beamcam.exe"
Name: "{autodesktop}\Beamcam"; Filename: "{app}\Beamcam.exe"; Tasks: desktopicon

[Run]
; Phones answer the search for them from the local network; without this rule Windows would drop
; those answers or ask the user about the firewall. Local network only.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Beamcam"""; Flags: runhidden; StatusMsg: "Allowing Beamcam to find your phone…"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Beamcam"" dir=in action=allow protocol=UDP remoteip=localsubnet program=""{app}\Beamcam.exe"" enable=yes"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Beamcam"" dir=in action=allow protocol=UDP remoteip=localsubnet program=""{app}\beamcam-cli.exe"" enable=yes"; Flags: runhidden
Filename: "{app}\Beamcam.exe"; Description: "Open Beamcam"; Flags: postinstall nowait skipifsilent runasoriginaluser

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
    MsgBox('Beamcam needs Windows 11. This computer has an older Windows.', mbError, MB_OK);
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
begin
  if CurUninstallStep = usUninstall then
    StopCameraService();
end;
