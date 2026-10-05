@echo off
setlocal EnableExtensions
title DSH A2A Agent
call "%~dp0_common.cmd"

echo ============================================================
echo   DSH A2A Agent
echo ============================================================
echo   workspace : %DSH_MCP_WORKDIR%
echo   profile   : %DSH_MCP_PROFILE%
echo   agent card: %CARD_URL%
echo   json-rpc  : %DSH_MCP_PUBLIC_URL%/
echo   auth      : %DSH_MCP_TOKEN%
echo   python    : %DSH_MCP_PY%
echo ============================================================
echo.

if not exist "%DSH_MCP_PY%" (
  echo [x] Interpreter not found:
  echo     %DSH_MCP_PY%
  echo     Run this once in this folder:  uv sync
  echo.
  pause
  exit /b 1
)

if not exist "%DSH_MCP_WORKDIR%" mkdir "%DSH_MCP_WORKDIR%"

rem Already serving? Then there is nothing to start.
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:%DSH_MCP_PORT%/healthz' -UseBasicParsing -TimeoutSec 2 > $null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo [i] An agent is already running on port %DSH_MCP_PORT%.
  echo     Agent Card: %CARD_URL%
  echo.
  pause
  exit /b 0
)

for /f %%p in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort %DSH_MCP_PORT% -State Listen -ErrorAction SilentlyContinue; if($c){$c[0].OwningProcess}"') do set "OWNER=%%p"
if defined OWNER (
  echo [!] Port %DSH_MCP_PORT% is held by unrelated process PID %OWNER%.
  echo     Run stop.cmd if it is a leftover agent, or change DSH_MCP_PORT in config.cmd.
  echo.
  pause
  exit /b 1
)

echo Starting...  to stop: press Ctrl+C, then answer Y.
echo.
"%DSH_MCP_PY%" -m dsh_mcp

echo.
echo Server stopped.
pause
