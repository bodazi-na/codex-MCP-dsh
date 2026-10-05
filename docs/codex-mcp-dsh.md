# Codex → MCP → DSH：一次完整接入与排障记录

这份文档记录把本机 **DeepSeek Harness（DSH）** 挂进 **Codex** 作为 MCP 工具的全过程，
包括踩到的坑、诊断方法和最终的调用记录。所有结论都来自真机验证。

## 0. 目标

```
Codex  ──MCP(stdio)──▶  dsh_mcp.mcp_server  ──▶  dsh --profile headless  ──▶  真实答复
                                                            │
                                                    每次调用留下一个 DSH 会话
```

Codex 侧只需要一个 `[mcp_servers.dsh]` 配置块；DSH 侧由本项目提供 MCP server。

## 1. 配置 Codex

`~/.codex/config.toml` 追加（完整可复制版本见 [`examples/codex-config.toml`](../examples/codex-config.toml)）：

```toml
[mcp_servers.dsh]
command = '<DSH_HOME>\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
args = ["-m", "dsh_mcp.mcp_server"]
startup_timeout_sec = 120

[mcp_servers.dsh.env]
PYTHONPATH = '<repo>\src;<repo>\.venv\Lib\site-packages;<repo>\.venv\Lib\site-packages\win32;<repo>\.venv\Lib\site-packages\win32\lib'
DSH_MCP_WORKDIR   = '<repo>'
DSH_MCP_STATE_DIR = '<repo>\.dsh-a2a'
DSH_MCP_CALL_LOG  = '<repo>\logs\mcp_calls.jsonl'
```

三个关键点，任一缺失都会失败：

| 点 | 为什么 |
| --- | --- |
| `command` 用**工作区外**的解释器 | 工作区内的 `.venv` 被 DSH 沙箱限制，它派生的 `dsh` 写不了 `~/.dsh`（`EPERM … cordis.yml`） |
| `env.PYTHONPATH` 必须给 | 运行时 python 里**没有** `dsh_mcp` 这个包（它是仓库源码），缺了 server 启动即 `ModuleNotFoundError` |
| `PYTHONPATH` 还要带 `win32` 与 `win32\lib` | `mcp` 2.x 在 Windows 上 import `pywintypes`（pywin32），而 pywin32 靠 `.pth` 注入路径——`PYTHONPATH` **不处理 `.pth`** |

## 2. 失败时的表现（重要）

少写 `env` 时，**Codex 不会报"服务器起不来"**，它只是拿不到工具。rollout 里模型的
原话是：

> "The user asks me to call the `dsh_status` tool. But looking at my available tools,
> there is no `dsh_status`…"（MCP 只返回 `{"resources":[]}`）

这种静默失败很容易被误判成"模型不听话"。诊断方法：把配置里的 `command` + `args`
**在干净环境里**原样跑一遍：

```powershell
& <运行时 python> -m dsh_mcp.mcp_server --check
# 期望输出 launcher / profile / workdir / state dir
# 缺 env 时：ModuleNotFoundError: No module named 'dsh_mcp'
```

## 3. 验证调用链

```powershell
# Codex 里：Call the dsh_status tool exactly once, then reply with the raw JSON
# 或直接命令行验证：
python scripts\mcp_smoke.py --task "Reply with exactly: MCP-OK"
```

成功的返回形如：

```json
{"answer": "MCP-OK", "session_id": "session-1d31559f-…", "exit_code": 0,
 "usage": {"inputTokens": 2386, "outputTokens": 5}}
```

## 4. 调用记录在哪查

facade 每次工具调用都会写一行 JSONL（`DSH_MCP_CALL_LOG`，默认 `logs\mcp_calls.jsonl`）：

```powershell
python scripts\mcp_calls.py --all
```

```
when (local)         tool        ok         ms  session                prompt
2026-10-05 21:29:53  dsh_status  ok          -
2026-10-05 21:29:57  dsh_task    ok       3875  session-1d31559f-…     Reply with exactly: CALL-LOG-OK
```

要回答"某个客户端到底有没有调到我"，按这个顺序取证：

| 顺序 | 位置 | 能看到什么 |
| --- | --- | --- |
| 1 | `logs\mcp_calls.jsonl` | 逐次调用：工具、耗时、会话、退出码、提示词预览 |
| 2 | `<state_dir>\mcp_sessions.json` | 最后一次成功会话 |
| 3 | `$DSH_HOME\sessions\**\session-*` | 每次 `dsh_task` 对应的真实 DSH 会话目录（可按时间对齐） |
| 4 | `~/.codex/sessions/**/rollout-*.jsonl` | 客户端侧全文：模型是否**看到**了工具、`function_call` 与输出 |
| 5 | WorkBuddy：`~/.workbuddy-ai/logs/mcp-apps-diag.log`、`audit-log\*.jsonl` | 该客户端的 MCP/工具调用流水 |

## 5. 本机实测时间线（可作为对照）

| 时间 | 事件 | 证据 |
| --- | --- | --- |
| 18:59 / 19:00 | stdio 与 streamable-http 首次跑通 | `MCP-OK` / `MCP-HTTP-OK` |
| 19:34:50、19:37:38 | 经 facade 的 `dsh_task` 成功调用 | `mcp_sessions.json` 的 `mcp-last` 与 DSH 会话目录对齐 |
| 21:22–21:25 | Codex 五个会话**看不到工具**（缺 `env`） | rollout 里模型自述没有 `dsh_status` |
| 21:43 | 补 `[mcp_servers.dsh.env]` 后重跑命令验证 | `--check` 输出正常；调用日志出现记录 |

## 6. 一个副作用：stdlib 之外的注意

- 改动 Codex 的 `config.toml` **务必先备份**，并用 `python -c "import tomllib; tomllib.load(open(path,'rb'))"`
  验证；用正则替换表头很容易留下重复 `[table]`，整份 TOML 直接非法。
- `dsh-mcp`（HTTP 传输）是常驻进程，改代码/配置后需**重启**才生效。
