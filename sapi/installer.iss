; Panthera SAPI -- one installer for every generation, forever.
;
; There are no per-synth installers and never will be: the engine is one
; data-driven COM class, a SAPI voice is a registry token naming a generation
; folder, and only voices whose data exists on disk are ever registered.
; This script's whole job is to place the files, register the class, and run
; the settings tool's register pass while the installer is already elevated --
; an NVDA user's extracted voices appear immediately, from the data they
; already have, and a machine with no data simply shows four "not installed"
; rows until its owner extracts.
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
#define AppVer "1.3.1"

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
OutputBaseFilename=panthera-sapi-{#AppVer}-setup
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
Source: "{#StageDir}\panthera_settings.exe"; DestDir: "{app}"
Source: "{#StageDir}\settings.ps1"; DestDir: "{app}"
Source: "{#StageDir}\settings.cmd"; DestDir: "{app}"
Source: "{#StageDir}\extract.py"; DestDir: "{app}"
Source: "{#StageDir}\pantheradiscs.py"; DestDir: "{app}"
Source: "{#StageDir}\pantherahfs.py"; DestDir: "{app}"
; The embeddable Python that runs the extractor, so extraction works on a
; machine with no Python of its own.  python.org-official bits, unmodified
; but for the ._pth naming the app folder on sys.path; still no Apple data.
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs

[Icons]
; The launcher rather than the batch file: a GUI-subsystem program creates no
; console, so nothing flashes or steals focus before the dialog appears.
Name: "{autoprograms}\Panthera SAPI settings"; Filename: "{app}\panthera_settings.exe"; WorkingDir: "{app}"

[Registry]
; The remembered data-folder choice.  Written by the settings tool, removed
; with the product; dontcreatekey keeps the installer from inventing it.
Root: HKCU; Subkey: "Software\Panthera SAPI"; Flags: uninsdeletekey dontcreatekey

[Run]
; The settings tool's register pass does everything in order: regsvr32 for
; both registry views, then one token per voice whose folder exists at the
; resolved data root (chosen folder, then NVDA's shared macintalk, then the
; standalone default).  Registering with no data present is a clean no-op.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -STA -File ""{app}\settings.ps1"" -RegisterVoices -GenerationList Tiger,Leopard,Snowleopard,Lion"; StatusMsg: "Registering voices from your speech data..."; Flags: runhidden
Filename: "{app}\panthera_settings.exe"; Description: "Open Panthera SAPI settings"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Tokens first; when the last Panthera token goes, the settings tool also
; unregisters both DLLs.  Data trees are not touched.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -STA -File ""{app}\settings.ps1"" -UnregisterVoices -GenerationList Tiger,Leopard,Snowleopard,Lion"; RunOnceId: "UnregisterVoices"; Flags: runhidden
