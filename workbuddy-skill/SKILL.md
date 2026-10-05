---
name: dsh-a2a
description: 把任务派给本机的 DeepSeek Harness（DSH）agent 执行，并取回结果。触发场景：用户说"让 DSH 做…""用 dsh-a2a 调 DSH""交给 DSH agent""问一下 DSH 那边"；或需要另一个本地 agent 的独立视角来完成代码、文件、研究类工作。
description_zh: "通过 A2A 协议调用本机 DSH agent（deepseek-harness），支持会话续接"
description_en: "Call the local DeepSeek Harness agent over A2A, with session resume"
version: 1.0.0
license: MIT
display_name: "dsh-a2a"
visibility: "private"
---

# dsh-a2a —— 让 WorkBuddy 调用 DSH

本机有一个 A2A agent 端点（`dsh-a2a`），它把 **DeepSeek Harness** 包装成标准 A2A agent：
发一个任务过去，DSH 那一侧会用它的技能、记忆和工作区真正执行，并把过程与最终答复回传。

## 前置检查

先确认服务在跑：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/dsh_mcp.py" card
```

- 打印出 `agent: DSH A2A Agent …` → 就绪，继续。
- 报 `cannot reach http://127.0.0.1:9101` → 告诉用户先在**普通终端**里启动：

  ```powershell
  cd D:\DS-harness\.dsh-a2a
  uv run dsh-a2a
  ```

  若 DSH 侧设了 `DSH_MCP_TOKEN`，本技能也要带同一个 token：加 `--token <token>`
  或设环境变量 `DSH_MCP_TOKEN`。

## 派活

```bash
python "${CLAUDE_SKILL_DIR}/scripts/dsh_mcp.py" send "把任务原文写在这里"
```

- 脚本会打印 `task:` / `context:`，流式显示 DSH 的工具调用与文本，最后给
  `TASK_STATE_COMPLETED` 和 `--- DSH 答复 ---` 段落。
- 退出码：`0` 完成 / `1` 失败或被拒 / 非 0 超时。失败时 stderr 有 `failure detail`。
- 需要机器可读结果时加 `--json`（返回 `state` / `contextId` / `taskId` / `answer` / `metadata`）。

## 追问（会话续接）

把上一轮打印的 `context:` 带上，DSH 会 resume 同一个会话，保留上文：

```bash
python "${CLAUDE_SKILL_DIR}/scripts/dsh_mcp.py" send "基于刚才的结论，给出下一步" --context-id <上一轮的 context>
```

`metadata.continuedSession` 为 `true` 且 `dshSessionId` 与上一轮相同时，说明确实续接成功。

## 什么时候用它 / 不用它

**用**：任务适合交给另一个独立 agent 完成——写代码、改文件、跑命令、写报告、查资料，
尤其是需要 DSH 侧已有的技能（教育时评、题库、文档流水线等）或它的本地记忆时。

**不用**：本机能直接做完的简单问答（多一次网络往返没有收益）；需要 WorkBuddy 自己
浏览器登录态的任务（DSH 没有你的浏览器会话）。

## 注意

- 长任务默认最长等 1800 秒，可用 `--timeout` 调整；期间脚本每 2 秒轮询一次。
- 任务在 DSH 侧的工作目录由服务端配置（默认 `D:\DS-harness\.dsh-a2a`），
  写文件类任务请在提示里写清目标路径。
- 一次只发一个任务；同一时刻多个任务会各自占用 DSH 的并发额度（默认 2）。
