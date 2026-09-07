@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x86 >nul
cl /O2 /nologo "%~dp0src\native_ref.c" /Fe:"%~dp0build\native_ref.exe" /Fo:"%~dp0build\native_ref.obj"
