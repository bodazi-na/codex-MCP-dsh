@echo off
rem ============================================================
rem  DSH A2A Agent - settings
rem  Edit this file, then double-click start-bg.cmd
rem ============================================================

rem Workspace dsh is allowed to work in (also the child process cwd).
set "DSH_MCP_WORKDIR=D:\DS-harness\.dsh-a2a"

rem Listen address. 127.0.0.1 = this machine only, 0.0.0.0 = reachable from LAN.
set "DSH_MCP_HOST=127.0.0.1"
set "DSH_MCP_PORT=9101"

rem Public URL advertised in the Agent Card. Leave empty to derive from host:port.
set "DSH_MCP_PUBLIC_URL="

rem Bearer token. Empty = no auth. WorkBuddy's skill reads DSH_MCP_TOKEN too,
rem so both sides must use the same value when this is set.
set "DSH_MCP_TOKEN="

rem dsh profile to boot (headless = answer one task and exit).
set "DSH_MCP_PROFILE=headless"

rem dsh launcher. Leave empty to auto-detect (skips a stale shim on PATH).
set "DSH_MCP_DSH_BIN="

rem dsh home. Leave empty to inherit (normally %USERPROFILE%\.dsh).
set "DSH_MCP_DSH_HOME="

rem Interpreter. Leave empty to let _common.cmd pick the DSH runtime Python
rem outside the workspace (recommended); set explicitly to override.
set "DSH_MCP_PY="

rem Optional extras
rem set "DSH_MCP_TIMEOUT_SECONDS=1800"
rem set "DSH_MCP_MAX_CONCURRENCY=2"
rem set "DSH_MCP_DEBUG_DIR=%HERE%logs\debug"
