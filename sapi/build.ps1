param([string]$OutputRoot = "C:\panthera")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$msvc = Get-ChildItem "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC" -Directory | Sort-Object Name | Select-Object -Last 1
$sdk = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\Include" -Directory | Sort-Object Name | Select-Object -Last 1
if (!$msvc -or !$sdk) { throw "MSVC Build Tools and the Windows SDK are required" }
$stage = Join-Path $OutputRoot "sapi"
New-Item -ItemType Directory -Force $stage,(Join-Path $stage "x86"),(Join-Path $stage "x64") | Out-Null
$hostCl = Join-Path $msvc.FullName "bin\Hostx64\x86\cl.exe"
& $hostCl /nologo /O2 /MT /W3 "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\ucrt" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" (Join-Path $repo "src\tiger_host.c") "/Fe$stage\panthera_host.exe" "/Fo$stage\" /link "/LIBPATH:$($msvc.FullName)\lib\x86" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\x86" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\x86" winmm.lib ole32.lib mfuuid.lib /LARGEADDRESSAWARE
if ($LASTEXITCODE) { throw "32-bit Panthera host build failed ($LASTEXITCODE)" }
Copy-Item (Join-Path $stage "panthera_host.exe") (Join-Path $stage "x86\panthera_host.exe")
Copy-Item (Join-Path $stage "panthera_host.exe") (Join-Path $stage "x64\panthera_host.exe")
foreach ($arch in "x86","x64") {
  $cl = Join-Path $msvc.FullName "bin\Hostx64\$arch\cl.exe"
  $libarch = if ($arch -eq "x86") { "x86" } else { "x64" }
  $out = Join-Path $stage $arch
  & $cl /nologo /EHsc /O2 /MT /LD /DUNICODE /D_UNICODE "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" "/I$($sdk.FullName)\ucrt" (Join-Path $PSScriptRoot "panthera_sapi.cpp") "/Fe$out\panthera_sapi.dll" "/Fo$out\" /link "/DEF:$PSScriptRoot\panthera_sapi.def" "/LIBPATH:$($msvc.FullName)\lib\$libarch" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\$libarch" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\$libarch" sapi.lib ole32.lib advapi32.lib
  if ($LASTEXITCODE) { throw "$arch SAPI DLL build failed ($LASTEXITCODE)" }
}
# The abbreviation rules ship in the DLL; rules_test.cpp #includes the DLL
# source and runs the parity cases mirrored from pantheraabbrev.py's tests,
# so a rules change that drifts from the Python spec fails the build here
# rather than in somebody's JAWS.
$testCl = Join-Path $msvc.FullName "bin\Hostx64\x64\cl.exe"
$testDir = Join-Path $env:TEMP "panthera-rules-test"
New-Item -ItemType Directory -Force $testDir | Out-Null
& $testCl /nologo /EHsc /O2 /MT /DUNICODE /D_UNICODE "/I$PSScriptRoot" "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" "/I$($sdk.FullName)\ucrt" (Join-Path $PSScriptRoot "rules_test.cpp") "/Fe$testDir\rules_test.exe" "/Fo$testDir\" /link "/LIBPATH:$($msvc.FullName)\lib\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\x64" sapi.lib ole32.lib advapi32.lib
if ($LASTEXITCODE) { throw "rules test build failed ($LASTEXITCODE)" }
& "$testDir\rules_test.exe"
if ($LASTEXITCODE) { throw "the SAPI abbreviation rules disagree with pantheraabbrev.py" }

# The host outlives the utterance now, so the questions only a resident engine
# can get wrong get asked here: that a warm utterance is byte-identical to a
# cold one, that returning inflection to the middle returns it, that a
# settings change respawns the host rather than being quietly ignored, and
# that an interruption leaves an engine which still speaks the same way.
# It needs extracted speech data, and says so and passes when there is none,
# so the gate is real on a machine with voices and silent on a build box.
$resDir = Join-Path $env:TEMP "panthera-resident-test"
New-Item -ItemType Directory -Force $resDir | Out-Null
Copy-Item (Join-Path $stage "panthera_host.exe") $resDir -Force
& $testCl /nologo /EHsc /O2 /MT /DUNICODE /D_UNICODE "/I$PSScriptRoot" "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" "/I$($sdk.FullName)\ucrt" (Join-Path $PSScriptRoot "resident_test.cpp") "/Fe$resDir\resident_test.exe" "/Fo$resDir\" /link "/LIBPATH:$($msvc.FullName)\lib\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\x64" sapi.lib ole32.lib advapi32.lib
if ($LASTEXITCODE) { throw "resident host test build failed ($LASTEXITCODE)" }
foreach ($g in "tiger","leopard","snowleopard","Lion") {
  # Lion is asked about Alex: its mtk3 voices are not reproducible run to run,
  # so a byte-identity check on Lion's Fred would fail for reasons of its own.
  $v = if ($g -eq "Lion") { "Alex" } else { "Fred" }
  & "$resDir\resident_test.exe" (Join-Path $env:APPDATA "nvda\macintalk") $g $v
  if ($LASTEXITCODE) { throw "the resident host misbehaves on $g" }
}

# The console-free way in: a GUI-subsystem launcher, so no console ever
# exists to flash and steal focus from the dialog.  settings.cmd stays for
# anyone at a command line.
$launcherCl = Join-Path $msvc.FullName "bin\Hostx64\x64\cl.exe"
& $launcherCl /nologo /O2 /MT /W3 "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\ucrt" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" (Join-Path $PSScriptRoot "settings_launcher.c") "/Fe$stage\panthera_settings.exe" "/Fo$stage\" /link /SUBSYSTEM:WINDOWS "/LIBPATH:$($msvc.FullName)\lib\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\x64" user32.lib kernel32.lib
if ($LASTEXITCODE) { throw "settings launcher build failed ($LASTEXITCODE)" }
# Embeddable Python rides with the stage so extraction owes nothing to the
# machine's PATH.  Fetched once and kept; the ._pth is rewritten because the
# embeddable build locks sys.path to that file's entries -- without the ..
# line, "import pantheradiscs" from the parent folder fails.
#
# 3.8.10 on purpose: the last Windows build of CPython that runs on
# Windows 7, which this community runs.  The extraction chain imports
# clean under it -- measured -- and an offline extractor gains nothing
# from a newer interpreter that excludes those machines.
$py = Join-Path $stage "python"
if (!(Test-Path (Join-Path $py "python38.dll"))) {
  if (Test-Path $py) { Remove-Item -Recurse -Force $py }
  $pyzip = Join-Path $env:TEMP "python-3.8.10-embed-amd64.zip"
  if (!(Test-Path $pyzip)) {
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.8.10/python-3.8.10-embed-amd64.zip" -OutFile $pyzip
  }
  Expand-Archive $pyzip -DestinationPath $py -Force
}
Set-Content -Encoding ASCII (Join-Path $py "python38._pth") @'
python38.zip
.
..
# The parent directory is where extract.py and its modules live; the
# embeddable build locks sys.path to exactly this file's entries, so
# without the .. line "import pantheradiscs" fails and extraction with
# the bundled interpreter never works.
#import site
'@
Copy-Item (Join-Path $PSScriptRoot "settings.ps1") (Join-Path $stage "settings.ps1")
Copy-Item (Join-Path $PSScriptRoot "extract.py") (Join-Path $stage "extract.py")
Copy-Item (Join-Path $repo "panthera\addon\synthDrivers\_panthera\pantheradiscs.py") $stage
Copy-Item (Join-Path $repo "panthera\addon\synthDrivers\_panthera\pantherahfs.py") $stage
Set-Content -Encoding ASCII (Join-Path $stage "settings.cmd") '@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0settings.ps1"'
Write-Host "SAPI development build: $stage"
