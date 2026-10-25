; Spotify music presence - per-user installer
;
; Installs into %LOCALAPPDATA%\Programs\Spotify (the same durable per-user
; location Discord and VS Code use), so no administrator rights are needed and
; the files are not touched by temporary-folder cleanup.
;
; Everything else - sign-in startup, the Startup-folder shortcut and the browser
; native-messaging registration - is done by the application itself via
; "Spotify.exe --install", which is also what lets the EXE repair those entries
; on every launch if they are ever removed.

#define AppName "Spotify"
#define AppVersion "2.0.0"
#define AppExe "Spotify.exe"

[Setup]
SourceDir=..
AppId={{B1F4E2A7-9C3D-4E6B-8A21-5D7C0F9E3B44}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=dist-installer
OutputBaseFilename=Spotify-Setup
SetupIconFile=assets\spotify.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
VersionInfoVersion=2.0.0.0
VersionInfoCompany=Spotify
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "FIX-NOTES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "{#AppName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Registers sign-in startup, the Startup shortcut and the browser native host,
; then starts the monitor. Runs on silent installs too, which is required for
; the startup shortcut to exist.
Filename: "{app}\{#AppExe}"; Parameters: "--install"; Flags: nowait; StatusMsg: "Configuring startup and browser integration..."

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall"; Flags: runhidden; RunOnceId: "RemoveStartupAndHosts"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\SpotifyPresence"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Vendors: array[0..5] of String;
  I: Integer;
begin
  // Safety net: remove the browser native-messaging registrations even if the
  // application EXE is missing or its own cleanup could not run.
  if CurUninstallStep = usUninstall then
  begin
    Vendors[0] := 'Google\Chrome';
    Vendors[1] := 'Chromium';
    Vendors[2] := 'Thorium';
    Vendors[3] := 'BraveSoftware\Brave-Browser';
    Vendors[4] := 'Microsoft\Edge';
    Vendors[5] := 'Vivaldi';
    for I := 0 to 5 do
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER,
        'Software\' + Vendors[I] + '\NativeMessagingHosts\com.ytdp.discord.presence');
  end;
end;
