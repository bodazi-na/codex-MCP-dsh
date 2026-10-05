@echo off
setlocal EnableExtensions
title DSH A2A Agent (background)
call "%~dp0_common.cmd"

if not exist "%DSH_MCP_PY%" (
  echo [x] Interpreter not found:
  echo     %DSH_MCP_PY%
  echo     Run this once in this folder:  uv sync
  pause
  exit /b 1
)

rem Already serving? Just open the Agent Card.
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%DSH_MCP_PORT%/healthz' -UseBasicParsing -TimeoutSec 2 > $null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo [i] Already running on port %DSH_MCP_PORT%. Opening the Agent Card.
  start "" "%CARD_URL%"
  exit /b 0
)

for /f %%p in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %DSH_MCP_PORT% -State Listen -ErrorAction SilentlyContinue; if($c){$c[0].OwningProcess}"') do set "OWNER=%%p"
if defined OWNER (
  echo [!] Port %DSH_MCP_PORT% is held by unrelated process PID %OWNER%.
  echo     Run stop.cmd or change DSH_MCP_PORT in config.cmd.
  exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%DSH_MCP_WORKDIR%" mkdir "%DSH_MCP_WORKDIR%"

echo Interpreter: %DSH_MCP_PY%
powershell -NoProfile -Command "Start-Process -FilePath '%DSH_MCP_PY%' -ArgumentList '-m','dsh_mcp' -WorkingDirectory '%HERE%' -WindowStyle Hidden -RedirectStandardOutput '%LOG_DIR%\server.out.log' -RedirectStandardError '%LOG_DIR%\server.err.log'"

echo Starting DSH A2A Agent on port %DSH_MCP_PORT% ...
set "READY="
for /l %%i in (1,1,30) do (
  if not defined READY (
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%DSH_MCP_PORT%/healthz' -UseBasicParsing -TimeoutSec 2 > $null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 set "READY=1"
    if not defined READY ping -n 2 127.0.0.1 >nul
  )
)

if defined READY (
  echo [ok] Running.
  echo      Agent Card: %CARD_URL%
  echo      Logs      : %LOG_DIR%\server.err.log
  echo      Stop with : stop.cmd
  start "" "%CARD_URL%"
) else (
  echo [x] Did not become healthy in 30s. Check %LOG_DIR%\server.err.log
)
