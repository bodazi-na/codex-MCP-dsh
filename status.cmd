@echo off
setlocal EnableExtensions
call "%~dp0_common.cmd"
chcp 65001 >nul

echo ============================================================
echo   DSH A2A Agent - status
echo ============================================================
echo   port      : %DSH_MCP_PORT%
echo   python    : %DSH_MCP_PY%
echo ============================================================

powershell -NoProfile -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$base='http://127.0.0.1:%DSH_MCP_PORT%';" ^
  "try { $h=(Invoke-WebRequest -Uri ($base+'/healthz') -UseBasicParsing -TimeoutSec 3).Content } catch { Write-Host '[x] not running on port %DSH_MCP_PORT%'; exit 1 };" ^
  "Write-Host ('health    : ' + $h);" ^
  "try { $card=(Invoke-RestMethod -Uri ($base+'/.well-known/agent-card.json') -TimeoutSec 5); Write-Host ('agent     : ' + $card.name + ' v' + $card.version); Write-Host ('skills    : ' + (($card.skills | ForEach-Object { $_.id }) -join ', ')) } catch { Write-Host 'agent card: unavailable' };" ^
  "$headers=@{'A2A-Version'='1.0'};" ^
  "try { $r=Invoke-RestMethod -Uri ($base+'/') -Method Post -ContentType 'application/json' -Headers $headers -TimeoutSec 10 -Body '{\"jsonrpc\":\"2.0\",\"id\":\"1\",\"method\":\"ListTasks\",\"params\":{\"pageSize\":100}}';" ^
  "  if ($r.error) { Write-Host ('tasks     : ' + $r.error.message) } else { $t=$r.result.tasks; Write-Host ('tasks     : ' + $t.Count + ' total'); $t | Select-Object -First 10 | ForEach-Object { Write-Host ('   - ' + $_.status.state.Replace('TASK_STATE_','') + '  ' + $_.id) } } } catch { Write-Host 'tasks     : query failed' }"

echo ============================================================
