"""Minimal A2A client: discover the card, send a task, poll until it finishes.

Usage:
    python scripts/a2a_smoke.py "summarize this workspace" \
        --url http://127.0.0.1:9101 --token secret
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

import httpx

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles default to GBK; keep the agent's UTF-8 answers intact.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


def rpc(
    client: httpx.Client,
    url: str,
    method: str,
    params: dict,
    request_id: str,
) -> dict:
    response = client.post(
        url,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers={"A2A-Version": "1.0", "Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise SystemExit(f"{method} failed: {payload['error']}")
    return payload["result"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--url", default="http://127.0.0.1:9101")
    parser.add_argument("--token", default=None)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument(
        "--context-id",
        default=None,
        help="reuse an A2A context so dsh resumes the same session",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="continue an existing A2A task instead of creating a new one",
    )
    args = parser.parse_args()

    base = args.url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    with httpx.Client(headers=headers, timeout=60.0) as client:
        card = client.get(f"{base}/.well-known/agent-card.json")
        card.raise_for_status()
        card_json = card.json()
        print(f"agent: {card_json['name']} v{card_json['version']}")
        print(f"skills: {', '.join(s['id'] for s in card_json.get('skills', []))}")

        message: dict = {
            "messageId": uuid.uuid4().hex,
            "role": "ROLE_USER",
            "parts": [{"text": args.prompt}],
        }
        if args.context_id:
            message["contextId"] = args.context_id
        if args.task_id:
            message["taskId"] = args.task_id

        result = rpc(client, f"{base}/", "SendMessage", {"message": message}, "1")

        task = result.get("task")
        if task is None:
            print("immediate message response:", json.dumps(result, ensure_ascii=False))
            return 0

        task_id = task["id"]
        context_id = task.get("contextId")
        print(f"task: {task_id}\ncontext: {context_id}\n")

        seen = 0
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            current = rpc(client, f"{base}/", "GetTask", {"id": task_id}, "2")
            status = current.get("status", {})
            state = status.get("state", "TASK_STATE_UNSPECIFIED")
            history = current.get("history", [])
            while seen < len(history):
                entry = history[seen]
                seen += 1
                text = "\n".join(
                    p.get("text", "") for p in entry.get("parts", []) if p.get("text")
                )
                if text:
                    label = "agent" if entry.get("role") == "ROLE_AGENT" else "user"
                    print(f"  [{label}] {text.strip()}")
            if state in TERMINAL_STATES:
                print(f"\n{state}")
                for artifact in current.get("artifacts", []):
                    body = "\n".join(
                        p.get("text", "")
                        for p in artifact.get("parts", [])
                        if p.get("text")
                    )
                    print(f"--- artifact {artifact.get('name')} ---")
                    print(body)
                    if artifact.get("metadata"):
                        print(f"--- metadata --- {json.dumps(artifact['metadata'], ensure_ascii=False)}")
                return 0 if state == "TASK_STATE_COMPLETED" else 1
            time.sleep(2.0)

    print("timed out waiting for the task to finish", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
