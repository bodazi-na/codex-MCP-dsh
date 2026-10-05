@echo off
rem ============================================================
rem  codex-mcp-dsh - settings
rem  Edit this file, then double-click start-bg.cmd (A2A bridge)
rem  or start-mcp.cmd (MCP over streamable-http)
rem ============================================================

rem Workspace dsh is allowed to work in (also the child process cwd).
set "DSH_MCP_WORKDIR=D:\DS-harness\.dsh-a2a"

rem Default host for both services.
set "DSH_MCP_HOST=127.0.0.1"

rem --- A2A bridge (start-bg.cmd / start.cmd / status.cmd / stop.cmd) ---------
rem Port the A2A agent card + JSON-RPC listen on.
set "DSH_MCP_PORT=9101"

rem --- MCP over streamable-http (start-mcp.cmd) ------------------------------
rem Its own names on purpose: sharing DSH_MCP_PORT with the A2A bridge would make
rem the two services fight over one port.
set "DSH_MCP_HTTP_HOST=127.0.0.1"
set "DSH_MCP_HTTP_PORT=9102"

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
