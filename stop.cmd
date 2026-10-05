@echo off
setlocal EnableExtensions
call "%~dp0_common.cmd"

for /f %%p in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %DSH_A2A_PORT% -State Listen -ErrorAction SilentlyContinue; if($c){$c[0].OwningProcess}"') do set "OWNER=%%p"

if not defined OWNER (
  echo Nothing is listening on port %DSH_A2A_PORT%.
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

echo Stopping DSH A2A Agent (PID %OWNER%) ...
powershell -NoProfile -Command "Stop-Process -Id %OWNER% -Force -ErrorAction SilentlyContinue"
ping -n 3 127.0.0.1 >nul

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %DSH_A2A_PORT% -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo [!] Port %DSH_A2A_PORT% is still busy.
) else (
  echo [ok] Stopped.
)
ping -n 2 127.0.0.1 >nul
