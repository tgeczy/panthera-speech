# Panthera SAPI development driver

This is a SAPI 5 engine shim built for x86 and x64. Both variants launch the
existing 32-bit `panthera_host.exe`, because Apple's engine itself is i386.

Run `powershell -ExecutionPolicy Bypass -File .\sapi\build.ps1`. All build and
test output is staged under `C:\panthera\sapi`; no generated file is written
to this repository. Run `C:\panthera\sapi\settings.cmd` to inspect the SAPI
version's own speech-data folder and register or unregister the voices per
user. Its four separate engines are kept under `%APPDATA%\Panthera SAPI` as
the `Tiger`, `Leopard`, `Snowleopard`, and `Lion` subfolders -- each named as
`extract.py` names it, the generation key title-cased. It does not read or
modify NVDA's speech-data folder.

This is development work: do not distribute it with extracted Apple data.
