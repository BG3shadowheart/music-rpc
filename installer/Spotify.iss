; Spotify music presence - per-user installer
;
; The user can choose the install folder. The installer requests elevation so
; the optional sign-in task can access elevated MSI Player or BlueStacks.
;
; The installer runs "Spotify.exe --install" to register browser integration
; and, when selected, create the sign-in task at the chosen install path.

#define AppName "Spotify"
#define AppVersion "2.2.0"
#define AppExe "Spotify.exe"

[Setup]
SourceDir=..
AppId={{B1F4E2A7-9C3D-4E6B-8A21-5D7C0F9E3B44}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
OutputDir=dist-installer
OutputBaseFilename=Spotify
SetupIconFile=assets\spotify.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
VersionInfoVersion=2.2.0.0
VersionInfoCompany=Spotify
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "dist-zig\Spotify-2.2.0.exe"; DestDir: "{app}"; DestName: "{#AppExe}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "FIX-NOTES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "{#AppName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Register the selected install path for browser integration and, when selected,
; create one elevated logon task. Setup does not launch a second background app.
Filename: "{app}\{#AppExe}"; Parameters: "--install"; Tasks: startup; Flags: waituntilterminated; StatusMsg: "Configuring startup and browser integration..."
Filename: "{app}\{#AppExe}"; Parameters: "--install --no-startup"; Tasks: not startup; Flags: waituntilterminated; StatusMsg: "Configuring browser integration..."

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
