@echo off
rem Shared bootstrap used by start*.cmd / stop.cmd / status.cmd
rem ASCII only: this file must survive any console code page.

set "HERE=%~dp0"
cd /d "%HERE%"

if exist "%HERE%config.cmd" call "%HERE%config.cmd"

if not defined DSH_A2A_HOST set "DSH_A2A_HOST=127.0.0.1"
if not defined DSH_A2A_PORT set "DSH_A2A_PORT=9101"
if not defined DSH_A2A_PUBLIC_URL set "DSH_A2A_PUBLIC_URL=http://%DSH_A2A_HOST%:%DSH_A2A_PORT%"
if not defined DSH_A2A_WORKDIR set "DSH_A2A_WORKDIR=%HERE%"
if not defined DSH_A2A_PROFILE set "DSH_A2A_PROFILE=headless"

set "CARD_URL=%DSH_A2A_PUBLIC_URL%/.well-known/agent-card.json"
set "LOG_DIR=%HERE%logs"

rem ---------------------------------------------------------------------------
rem Interpreter choice
rem
rem A .venv created inside this workspace is confined by the DSH Windows
rem sandbox: any `dsh` child it spawns cannot write %USERPROFILE%\.dsh\profiles
rem and every task fails with "EPERM ... cordis.yml". So prefer the DSH runtime
rem Python (lives outside the workspace) and expose the workspace packages via
rem PYTHONPATH; fall back to the venv interpreter when the runtime is missing.
rem Override with DSH_A2A_PY / DSH_A2A_PYTHONPATH in config.cmd.
rem ---------------------------------------------------------------------------
set "DSH_RUNTIME_HOME=%DSH_HOME%"
if not defined DSH_RUNTIME_HOME set "DSH_RUNTIME_HOME=%USERPROFILE%\.dsh"
set "DSH_RUNTIME_PY=%DSH_RUNTIME_HOME%\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
set "DSH_VENV_PY=%HERE%.venv\Scripts\python.exe"

if defined DSH_A2A_PY goto :py_done
set "DSH_A2A_PY=%DSH_VENV_PY%"
set "DSH_A2A_PYTHONPATH="
if not exist "%DSH_RUNTIME_PY%" goto :py_done
set "DSH_A2A_PY=%DSH_RUNTIME_PY%"
set "DSH_A2A_PYTHONPATH=%HERE%src;%HERE%.venv\Lib\site-packages"

:py_done
if defined DSH_A2A_PYTHONPATH set "PYTHONPATH=%DSH_A2A_PYTHONPATH%"
exit /b 0
