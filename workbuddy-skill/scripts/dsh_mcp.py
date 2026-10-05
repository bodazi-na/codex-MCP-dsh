#!/usr/bin/env python3
"""Call a DSH A2A agent from WorkBuddy (standard library only).

The deepseek-harness A2A bridge (`dsh-a2a`) speaks A2A 1.0 over JSON-RPC, so a
plain urllib client is enough — this works under any Python 3, no matter which
interpreter WorkBuddy hands the skill.

Examples:
    python dsh_mcp.py card
    python dsh_mcp.py send "用一句话说明 D:\\DS-harness 是做什么的"
    python dsh_mcp.py send "继续上一个话题" --context-id <ctx> --wait
    python dsh_mcp.py wait --task-id <id>
    python dsh_mcp.py send "..." --json          # machine-readable result
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = os.environ.get("DSH_MCP_URL", "http://127.0.0.1:9101")
TERMINAL = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def _request(url: str, token: str | None, payload: dict | None, timeout: float) -> dict:
    headers = {"A2A-Version": "1.0"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def rpc(base: str, method: str, params: dict, token: str | None, timeout: float = 60.0) -> dict:
    payload = _request(
        base.rstrip("/") + "/",
        token,
        {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params},
        timeout,
    )
    if "error" in payload:
        raise SystemExit(f"{method} failed: {payload['error']}")
    return payload["result"]


def fetch_card(base: str, timeout: float = 20.0) -> dict:
    return _request(base.rstrip("/") + "/.well-known/agent-card.json", None, None, timeout)


def _text_of(parts: list[dict]) -> str:
    return "\n".join(part.get("text", "") for part in parts if part.get("text"))


def _print_history(task: dict, seen: int, stream: bool) -> int:
    history = task.get("history") or []
    while seen < len(history):
        entry = history[seen]
        seen += 1
        text = _text_of(entry.get("parts") or []).strip()
        if text and stream:
            label = "agent" if entry.get("role") == "ROLE_AGENT" else "user"
            print(f"  [{label}] {text}")
    return seen


def send(
    base: str,
    prompt: str,
    token: str | None,
    context_id: str | None,
    timeout: float,
    stream: bool,
) -> dict:
    message: dict = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "parts": [{"text": prompt}],
    }
    if context_id:
        message["contextId"] = context_id
    result = rpc(base, "SendMessage", {"message": message}, token)
    task = result.get("task")
    if task is None:
        print(json.dumps(result, ensure_ascii=False))
        return {"state": "TASK_STATE_COMPLETED", "contextId": "", "artifacts": []}
    print(f"task:    {task['id']}")
    print(f"context: {task.get('contextId')}")
    return wait(base, task["id"], task.get("contextId", ""), token, timeout, stream)


def wait(
    base: str,
    task_id: str,
    context_id: str,
    token: str | None,
    timeout: float,
    stream: bool,
) -> dict:
    deadline = time.time() + timeout
    seen = 0
    task: dict = {}
    while time.time() < deadline:
        task = rpc(base, "GetTask", {"id": task_id}, token)
        seen = _print_history(task, seen, stream)
        state = (task.get("status") or {}).get("state", "TASK_STATE_UNSPECIFIED")
        if state in TERMINAL:
            task["contextId"] = task.get("contextId") or context_id
            task["state"] = state
            return task
        time.sleep(2.0)
    raise SystemExit("timed out waiting for the task to finish")


def render(task: dict, as_json: bool) -> int:
    state = task.get("state") or (task.get("status") or {}).get("state")
    artifacts = task.get("artifacts") or []
    answer = ""
    metadata: dict = {}
    for artifact in artifacts:
        body = _text_of(artifact.get("parts") or [])
        if body and not answer:
            answer = body
            metadata = artifact.get("metadata") or {}

    if as_json:
        print(
            json.dumps(
                {
                    "state": state,
                    "contextId": task.get("contextId"),
                    "taskId": task.get("id"),
                    "answer": answer,
                    "metadata": metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"\n{state}")
        if answer:
            print("\n--- DSH 答复 ---")
            print(answer)
        if metadata.get("dshSessionId"):
            print(
                f"\n[dsh session {metadata['dshSessionId']}"
                f"{' · 续接' if metadata.get('continuedSession') else ''}"
                f" · session-map context {task.get('contextId')}]"
            )
    if state == "TASK_STATE_COMPLETED":
        return 0
    message = json.dumps((task.get("status") or {}).get("message") or {}, ensure_ascii=False)
    if state and state != "TASK_STATE_COMPLETED":
        print(f"\nfailure detail: {message[:600]}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Call a DSH A2A agent.")
    parser.add_argument("action", choices=["card", "send", "wait"], nargs="?", default="card")
    parser.add_argument("prompt", nargs="?", help="task text for `send`")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default=os.environ.get("DSH_MCP_TOKEN"))
    parser.add_argument("--context-id", default=None, help="resume this A2A context")
    parser.add_argument("--task-id", default=None, help="for `wait`")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--no-stream", action="store_true", help="hide progress lines")
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    args = parser.parse_args()

    try:
        if args.action == "card":
            card = fetch_card(args.url)
            print(f"agent:  {card.get('name')} v{card.get('version')}")
            print(f"desc:   {(card.get('description') or '')[:160]}")
            print("skills: " + ", ".join(s.get("id", "") for s in card.get("skills") or []))
            interfaces = card.get("supportedInterfaces") or []
            for interface in interfaces:
                print(f"  {interface.get('protocolBinding')} {interface.get('protocolVersion')} -> {interface.get('url')}")
            return 0

        if args.action == "send":
            if not args.prompt:
                parser.error("send needs a prompt")
            task = send(
                args.url,
                args.prompt,
                args.token,
                args.context_id,
                args.timeout,
                not args.no_stream,
            )
            return render(task, args.json)

        if args.action == "wait":
            if not args.task_id:
                parser.error("wait needs --task-id")
            task = wait(args.url, args.task_id, "", args.token, args.timeout, not args.no_stream)
            return render(task, args.json)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {error.code} from {args.url}: {detail}")
    except urllib.error.URLError as error:
        raise SystemExit(
            f"cannot reach {args.url} ({error.reason}). Is `dsh-a2a` running? "
            "Start it with: uv run dsh-a2a"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
