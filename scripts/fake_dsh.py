#!/usr/bin/env python3
"""Deterministic stand-in for ``dsh --profile headless`` — TEST DOUBLE ONLY.

It accepts the same launcher arguments the bridge uses and emits the same
newline-delimited event vocabulary, so the contract tests never need a real
model, credentials, or network:

    fake_dsh.py --profile headless --json [--session-id <id>] -

Task text arrives on stdin (``-``). Behaviour keys off the task text:

* ``fail``  -> exit 1 with a diagnostic on stderr
* ``slow``  -> block long enough for the bridge's cancel path
* anything else -> session + tool call/result + text + final, exit 0
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        print("0.0.0-fake")
        return 0
    if "--help" in argv or "-h" in argv:
        print("fake dsh: answer one task and exit")
        return 0

    resume = None
    if "--session-id" in argv:
        index = argv.index("--session-id")
        if index + 1 < len(argv):
            resume = argv[index + 1]

    task = sys.stdin.read().strip()
    session_id = resume or f"session-{uuid.uuid5(uuid.NAMESPACE_URL, task or 'empty').hex}"

    emit({"type": "session", "sessionId": session_id, "cwd": os.getcwd()})

    lowered = task.lower()
    if "fail" in lowered:
        print("dsh: run/failed: simulated failure", file=sys.stderr, flush=True)
        return 1
    if "slow" in lowered:
        time.sleep(30)
        print("dsh: run/aborted: simulated abort", file=sys.stderr, flush=True)
        return 1

    emit({"type": "status", "phase": "turn_start", "turn": 1})
    emit({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
    emit(
        {
            "type": "tool_call",
            "callId": "call-1",
            "tool": "read_file",
            "input": {"path": "README.md"},
        }
    )
    emit(
        {
            "type": "tool_result",
            "callId": "call-1",
            "status": "completed",
            "result": "# README\nok",
        }
    )
    emit({"type": "thinking", "text": "Answer briefly."})
    emit({"type": "text", "text": f"echo: {task}"})
    emit(
        {
            "type": "status",
            "phase": "step_end",
            "turn": 1,
            "step": 1,
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
    )
    emit({"type": "status", "phase": "turn_end", "turn": 1, "reason": "completed"})
    emit({"type": "final", "text": f"echo: {task}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
