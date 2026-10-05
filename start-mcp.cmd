@echo off
setlocal EnableExtensions
title DSH MCP Server (streamable-http)
call "%~dp0_common.cmd"

rem Serve DSH as an MCP server over streamable-http so remote-only MCP clients
rem (WorkBuddy's mcp.json, hosted clients, other machines) can mount it.
rem Clients point at:  http://<host>:<port>/mcp
rem
rem The stdio transport needs no launcher: configure the client with
rem   command = <python>  args = ["-m", "dsh_mcp.mcp_server"]
rem and set PYTHONPATH to "<this folder>\src;<this folder>\.venv\Lib\site-packages"
rem plus the pywin32 dirs "<this folder>\.venv\Lib\site-packages\win32" and
rem "...\site-packages\win32\lib" (mcp 2.x imports pywintypes on Windows and
rem PYTHONPATH does not process .pth files).

rem Port/host for THIS launcher (the HTTP transport). _common.cmd already defines
rem DSH_MCP_PORT for the A2A bridge (9101), so the HTTP server keeps its own names
rem -- otherwise both services would fight over one variable and one port.
if not defined DSH_MCP_HTTP_PORT set "DSH_MCP_HTTP_PORT=9102"
if not defined DSH_MCP_HTTP_HOST set "DSH_MCP_HTTP_HOST=127.0.0.1"

echo ============================================================
echo   DSH MCP Server (streamable-http)
echo ============================================================
echo   endpoint  : http://%DSH_MCP_HTTP_HOST%:%DSH_MCP_HTTP_PORT%/mcp
echo   workspace : %DSH_MCP_WORKDIR%
echo   profile   : %DSH_MCP_PROFILE%
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

echo Serving. Stop with Ctrl+C.
echo.
"%DSH_MCP_PY%" -m dsh_mcp.mcp_server --http --host %DSH_MCP_HTTP_HOST% --port %DSH_MCP_HTTP_PORT%

echo.
echo Server stopped.
pause
