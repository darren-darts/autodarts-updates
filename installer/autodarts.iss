; Inno Setup script for the Autodarts Windows installer.
;
; Built by tools/build_installer.py, which stages the payload into
; dist-release/stage and passes the version in. Do not run this directly -
; the staging step is what creates the tree it packages.
;
; Two deliberate choices:
;
;  * **Per-user install, no administrator prompt.** The app writes to its own
;    directory (config, staged updates), so a Program Files install would
;    need elevation for every update. LOCALAPPDATA avoids UAC entirely and
;    keeps the in-app updater working without special privileges.
;
;  * **config\ is never packaged and never removed.** It holds calibration,
;    players and selfies. Reinstalling on top must preserve them, and
;    uninstalling should not silently destroy them either.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\dist-release\stage"
#endif

[Setup]
AppId={{7F3A1C42-9E5B-4B1D-8C6A-2D9F4E7B1A30}
AppName=Autodarts
AppVersion={#AppVersion}
AppPublisher=Autodarts
DefaultDirName={localappdata}\Autodarts
DefaultGroupName=Autodarts
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist-release
OutputBaseFilename=Autodarts-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Autodarts {#AppVersion}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The interpreter and its packages. Large, and changes rarely.
Source: "{#StageDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
; The app payload - this is what in-app updates replace wholesale.
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\launcher.py"; DestDir: "{app}"; Flags: ignoreversion
; Marks this as an installed tree rather than a source checkout, which is
; what tells the app that in-app updates apply here (see backend/paths.py).
Source: "{#StageDir}\.autodarts-root"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\config"; Flags: uninsneveruninstall
Name: "{app}\staging"
Name: "{app}\logs"

[Icons]
; pythonw.exe, not python.exe: the launcher is a background process and a
; console window sitting behind the browser looks broken to a non-technical
; user, who will eventually close it and stop the app mid-game.
Name: "{group}\Autodarts"; Filename: "{app}\runtime\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\app\frontend\dist\favicon.ico"
Name: "{commondesktop}\Autodarts"; Filename: "{app}\runtime\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\app\frontend\dist\favicon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; Description: "Start Autodarts"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Inno only removes files it installed. Three things here are created after
; installation and would otherwise be orphaned:
;
;  * Python writes __pycache__ folders throughout app\ the first time it runs,
;    and an in-app update replaces app\ entirely with files Inno never
;    tracked. Either leaves app\ non-empty, so it survives uninstall - which
;    was measured leaving ~200 MB behind. Removing the directory outright is
;    the only reliable answer.
;  * runtime\ likewise accumulates __pycache__ on first run.
;  * staging\, logs\ and the rollback copies are purely runtime state.
;
; config\ is deliberately NOT listed: it holds calibration, players and
; selfies. Someone uninstalling to reinstall a broken version would otherwise
; lose a painstaking three-camera calibration, which is far worse than
; leaving a few KB of settings behind.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\staging"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\app.previous"
Type: filesandordirs; Name: "{app}\app.incoming"
Type: files; Name: "{app}\last_update.json"

[Code]
// An in-app update replaces app\ with a downloaded copy, so a reinstall of
// an OLDER installer would otherwise merge its files into a newer tree and
// leave a mixture of two versions. Clearing app\ first makes a reinstall
// mean exactly what the user expects: this version, whole.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    DelTree(ExpandConstant('{app}\app'), True, True, True);
    DelTree(ExpandConstant('{app}\app.previous'), True, True, True);
    DelTree(ExpandConstant('{app}\staging'), True, True, True);
  end;
end;
