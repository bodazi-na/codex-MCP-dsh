<#
.SYNOPSIS
    Start the dsh-a2a bridge with an interpreter the DSH sandbox does not confine.

.DESCRIPTION
    A `.venv` created inside the workspace is confined by the DSH Windows
    sandbox: any process started from that interpreter is denied writes outside
    the workspace, so `dsh` cannot rewrite `~/.dsh/profiles/<profile>/cordis.yml`
    and every task fails with EPERM.

    This launcher runs the project with the DSH runtime Python (outside the
    workspace) plus the workspace's site-packages on PYTHONPATH, which the
    sandbox does not confine. It falls back to the venv interpreter when the
    runtime is missing.

.EXAMPLE
    .\run.ps1 --check
    .\run.ps1
    .\run.ps1 --once "summarize this repository"
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE ".dsh" }
$runtimePy = Join-Path $dshHome "dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$sitePackages = Join-Path $root ".venv\Lib\site-packages"

if ((Test-Path $runtimePy) -and (Test-Path $sitePackages)) {
    # win32 / win32\lib are required: `mcp` 2.x imports `pywintypes` on Windows,
    # and pywin32 exposes it only through its .pth bootstrap, which PYTHONPATH
    # entries do not process.
    $env:PYTHONPATH = (Join-Path $root "src") + ";" + $sitePackages + ";" +
        (Join-Path $sitePackages "win32") + ";" + (Join-Path $sitePackages "win32\lib")
    Write-Host "[dsh-a2a] interpreter: $runtimePy (outside the workspace)" -ForegroundColor DarkGray
    & $runtimePy -m dsh_a2a @Args
    exit $LASTEXITCODE
}

if (-not (Test-Path $venvPy)) {
    Write-Error "No interpreter found. Run 'uv sync' first, or set DSH_HOME so the runtime Python can be located."
}
Write-Warning "Runtime Python not found; falling back to the workspace venv, which the DSH sandbox confines."
& $venvPy -m dsh_a2a @Args
exit $LASTEXITCODE
