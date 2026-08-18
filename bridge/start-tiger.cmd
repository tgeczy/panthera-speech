@echo off
REM Bring up the Tiger guest for the Tiger NVDA driver.
REM
REM Runs against a copy-on-write overlay so the original image is never
REM written. Delete tiger-work.qcow2 to start clean; the 4.46 GB base is
REM untouched either way.
REM
REM Port 8080 on this machine forwards to port 8000 in the guest, which is
REM where srv.py listens. The guest never opens an audio device -- speech is
REM rendered to a file and sent back over that port -- so the stuttering that
REM makes Tiger unusable for listening does not apply here.

setlocal
set QEMU=C:\Program Files\qemu\qemu-system-ppc.exe
set VMDIR=D:\qemu
set BASE=osx-tiger_10.4.11_installed.qcow2
set WORK=tiger-work.qcow2

cd /d "%VMDIR%" || exit /b 1

if not exist "%WORK%" (
  echo Creating overlay %WORK% on %BASE% ...
  "C:\Program Files\qemu\qemu-img.exe" create -f qcow2 -b "%BASE%" -F qcow2 "%WORK%" || exit /b 1
)

echo Starting Tiger. Once the desktop appears, open Terminal and run:
echo     python ~/srv.py ^&
echo.

"%QEMU%" -M mac99,via=pmu -cpu g4 -m 1024 ^
  -drive file=%WORK%,format=qcow2,media=disk -boot c -g 1024x768x32 ^
  -vnc 127.0.0.1:1 ^
  -monitor tcp:127.0.0.1:55555,server,nowait ^
  -netdev user,id=n0,hostfwd=tcp::8080-:8000 -device sungem,netdev=n0 ^
  -name Tiger

endlocal
