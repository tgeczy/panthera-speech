; Panthera SAPI -- one installer for every generation, forever.
;
; There are no per-synth installers and never will be: the engine is one
; data-driven COM class, a SAPI voice is a registry token naming a generation
; folder, and only voices whose data exists on disk are ever registered.
; This script's whole job is to place the files, register the class, and run
; the settings tool's register pass on a fresh install, while elevated --
; an NVDA user's extracted voices appear immediately, from the data they
; already have, and a machine with no data simply shows four "not installed"
; rows until its owner extracts. Upgrades refresh the COM class only, leaving
; existing voice tokens and deliberately unregistered generations alone.
;
; It ships only our code.  No Apple data is packaged, looked for, or touched;
; uninstall removes the voices and the class and leaves every extracted tree
; exactly where it was.
;
; Build:  stage the binaries first, then compile --
;     powershell -ExecutionPolicy Bypass -File .\sapi\build.ps1
;     ISCC .\sapi\installer.iss
; The staged folder can be overridden with /DStageDir=<path>.

#ifndef StageDir
#define StageDir "C:\panthera\sapi"
#endif
#define AppVer "3.3.3"
#define AssetVer "3.3.3"

[Setup]
AppId={{8E1B0A4C-5A0D-4F2E-9C1B-7D64A2153F90}
AppName=Panthera SAPI
AppVersion={#AppVer}
AppPublisher=Panthera Speech
AppSupportURL=https://github.com/tgeczy/panthera-speech
DefaultDirName={autopf}\Panthera SAPI
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
OutputDir={#StageDir}\out
OutputBaseFilename=panthera-sapi-{#AssetVer}-setup
DisableProgramGroupPage=yes
UninstallDisplayName=Panthera SAPI {#AppVer}
; The HKCU entry below is the settings tool's remembered folder choice.  On a
; consent elevation the elevated HKCU is the installing user's, which is the
; same account the settings tool's own elevated register pass writes as -- the
; warning this silences was read, considered, and does not apply.
UsedUserAreasWarning=no

[Files]
; Both bitnesses of the engine DLL, each beside its own copy of the host --
; the DLL launches panthera_host.exe from its own folder, and the host is
; i386 either way because Apple's engine is.
Source: "{#StageDir}\x86\panthera_sapi.dll"; DestDir: "{app}\x86"
Source: "{#StageDir}\x86\panthera_host.exe"; DestDir: "{app}\x86"
Source: "{#StageDir}\x64\panthera_sapi.dll"; DestDir: "{app}\x64"; Check: Is64BitInstallMode
Source: "{#StageDir}\x64\panthera_host.exe"; DestDir: "{app}\x64"; Check: Is64BitInstallMode
; The settings launcher in the bitness of the Windows it lands on: 32-bit
; Windows cannot run the x64 one, and on 64-bit Windows an x86 one would start
; the 32-bit PowerShell (panthera-speech#37).
Source: "{#StageDir}\panthera_settings.exe"; DestDir: "{app}"; Check: Is64BitInstallMode
Source: "{#StageDir}\x86\panthera_settings.exe"; DestDir: "{app}"; Check: not Is64BitInstallMode
Source: "{#StageDir}\settings.ps1"; DestDir: "{app}"
Source: "{#StageDir}\settings.cmd"; DestDir: "{app}"
Source: "{#StageDir}\extract.py"; DestDir: "{app}"
Source: "{#StageDir}\pantheradiscs.py"; DestDir: "{app}"
Source: "{#StageDir}\pantherahfs.py"; DestDir: "{app}"
Source: "{#StageDir}\Glint-MIT.txt"; DestDir: "{app}\licenses"
Source: "{#StageDir}\Panthera-MIT.txt"; DestDir: "{app}\licenses"
; The embeddable Python that runs the extractor, so extraction works on a
; machine with no Python of its own.  python.org-official bits, unmodified
; but for the ._pth naming the app folder on sys.path; still no Apple data.
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs; Check: Is64BitInstallMode
Source: "{#StageDir}\python-x86\*"; DestDir: "{app}\python"; Flags: recursesubdirs; Check: not Is64BitInstallMode

[Dirs]
; The machine-wide settings file lives here, and every standard account
; writes it: the settings tool keeps this copy current on every save, so the
; sign-in screen speaks with the settings its owner chose last rather than
; whatever an elevated trip mirrored months ago.  Modify and not full
; control -- SYSTEM reads this file, and its permissions are not something a
; standard account should be able to change.  The tool's own elevated trips
; grant the same on a machine upgraded from an installer without this entry.
Name: "{commonappdata}\Panthera SAPI"; Permissions: users-modify

[Icons]
; The launcher rather than the batch file: a GUI-subsystem program creates no
; console, so nothing flashes or steals focus before the dialog appears.
Name: "{autoprograms}\Panthera SAPI settings"; Filename: "{app}\panthera_settings.exe"; WorkingDir: "{app}"

[Registry]
; The remembered data-folder choice.  Written by the settings tool, removed
; with the product; dontcreatekey keeps the installer from inventing it.
Root: HKCU; Subkey: "Software\Panthera SAPI"; Flags: uninsdeletekey dontcreatekey

[Run]
; Registration is not here: it runs from CurStepChanged below, because a
; [Run] entry cannot see the settings tool's exit code, and a failed
; registration has to be said out loud -- see RunSettingsStep.
Filename: "{app}\panthera_settings.exe"; Description: "Open Panthera SAPI settings"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; The settings files go with the product, as the HKCU key always has.
Type: filesandordirs; Name: "{commonappdata}\Panthera SAPI"
Type: filesandordirs; Name: "{userappdata}\Panthera SAPI"

[UninstallRun]
; Tokens first; when the last Panthera token goes, the settings tool also
; unregisters both DLLs.  Data trees are not touched.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -STA -File ""{app}\settings.ps1"" -UnregisterVoices -GenerationList Tiger,Leopard,Snowleopard,Lion"; RunOnceId: "UnregisterVoices"; Flags: runhidden

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E1B0A4C-5A0D-4F2E-9C1B-7D64A2153F90}_is1';
  { Windows PowerShell 3, 4 and 5.x all register under "3". }
  PowerShellKey = 'SOFTWARE\Microsoft\PowerShell\3\PowerShellEngine';
var
  ExistingInstall: Boolean;

function InitializeSetup: Boolean;
begin
  { The settings tool, which registers every voice, needs PowerShell 3 or
    later.  Windows 7 comes with 2.0, and without this the install finished
    and registered nothing, silently.  WMF 5.1 is the one update for
    Windows 7 that Microsoft still offers. }
  if not RegValueExists(HKLM, PowerShellKey, 'PowerShellVersion') then
  begin
    SuppressibleMsgBox('Panthera SAPI needs Windows PowerShell 3 or later, and this copy of Windows does not have it yet.' + #13#10#13#10 +
      'On Windows 7, install Windows Management Framework 5.1 from https://aka.ms/wmf51download, restart, and run this installer again.',
      mbCriticalError, MB_OK, IDOK);
    Result := False;
    exit;
  end;
  { Snapshot before Setup writes its own uninstall key. Checking in [Run]
    would mistake a first install for an upgrade. Read both registry views. }
  ExistingInstall := RegKeyExists(HKLM32, UninstallKey);
  if IsWin64 then
    ExistingInstall := ExistingInstall or RegKeyExists(HKLM64, UninstallKey);
  Result := True;
end;

function IsUpgrade: Boolean;
begin
  Result := ExistingInstall;
end;

function SettingsScript: String;
begin
  Result := ExpandConstant('{app}\settings.ps1');
end;

{ One elevated-already run of the settings tool, with its exit code heard.
  A [Run] entry cannot see one, so a registration that failed used to finish
  Setup with no voices and nothing said (Tomi, 2026-11-21: "a pretty good one
  to say out loud so the person knows").  Silent installs log it instead. }
procedure RunSettingsStep(const Switches, Status, Failed: String);
var
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := Status;
  if not Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -STA -File "' + SettingsScript + '" ' + Switches,
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Log(Format('Panthera SAPI: the settings tool could not start (%d)', [Code]))
  else if Code <> 0 then
    Log(Format('Panthera SAPI: the settings tool failed with %d', [Code]))
  else
    exit;
  SuppressibleMsgBox(Failed + #13#10#13#10 +
    'Open "Panthera SAPI settings" from the Start menu and press Register to try again.',
    mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;
  { On a fresh install the settings tool does everything in order: regsvr32
    for each DLL installed, then one token per voice whose folder exists at
    the resolved data root (chosen folder, then NVDA's shared macintalk, then
    the standalone default).  Registering with no data present is a clean
    no-op.  On an upgrade, even zero registered voices is a choice to
    preserve: do not rebuild tokens from available data or infer a choice
    from elevated HKCU. }
  if IsUpgrade then
    RunSettingsStep('-RegisterServer', 'Updating SAPI components...',
      'Panthera SAPI was updated, but its SAPI components could not be registered, so its voices may not speak.')
  else
    RunSettingsStep('-RegisterVoices -GenerationList Tiger,Leopard,Snowleopard,Lion',
      'Registering voices from your speech data...',
      'Panthera SAPI was installed, but its voices could not be registered, so no program can use them yet.');
end;
