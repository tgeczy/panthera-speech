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
# The console-free way in: a GUI-subsystem launcher, so no console ever
# exists to flash and steal focus from the dialog.  settings.cmd stays for
# anyone at a command line.
$launcherCl = Join-Path $msvc.FullName "bin\Hostx64\x64\cl.exe"
& $launcherCl /nologo /O2 /MT /W3 "/I$($msvc.FullName)\include" "/I$($sdk.FullName)\ucrt" "/I$($sdk.FullName)\um" "/I$($sdk.FullName)\shared" (Join-Path $PSScriptRoot "settings_launcher.c") "/Fe$stage\panthera_settings.exe" "/Fo$stage\" /link /SUBSYSTEM:WINDOWS "/LIBPATH:$($msvc.FullName)\lib\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\ucrt\x64" "/LIBPATH:$($sdk.Parent.Parent.FullName)\Lib\$($sdk.Name)\um\x64" user32.lib kernel32.lib
if ($LASTEXITCODE) { throw "settings launcher build failed ($LASTEXITCODE)" }
Copy-Item (Join-Path $PSScriptRoot "settings.ps1") (Join-Path $stage "settings.ps1")
Copy-Item (Join-Path $PSScriptRoot "extract.py") (Join-Path $stage "extract.py")
Copy-Item (Join-Path $repo "panthera\addon\synthDrivers\_panthera\pantheradiscs.py") $stage
Copy-Item (Join-Path $repo "panthera\addon\synthDrivers\_panthera\pantherahfs.py") $stage
Set-Content -Encoding ASCII (Join-Path $stage "settings.cmd") '@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0settings.ps1"'
Write-Host "SAPI development build: $stage"
