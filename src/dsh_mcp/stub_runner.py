"""A canned runner used for wiring checks and contract tests.

It replays the exact event vocabulary ``dsh --profile headless --json`` emits,
without spawning a process — so an A2A client can be validated end to end
(schemas, auth, task states, artifacts, session resume) at zero model cost:

    uv run dsh-a2a --stub

It is not a simulation of a *thinking* agent: the answer is always
``stub: <prompt>``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .dsh_runner import DshRunError, DshRunResult, DshRunner

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

STUB_SESSION_ID = "session-stub-0001"


class StubRunner:
    """Drop-in replacement for :class:`~dsh_mcp.dsh_runner.DshRunner`."""

    def __init__(self, session_id: str = STUB_SESSION_ID) -> None:
        self.session_id = session_id
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        prompt: str,
        *,
        task_id: str,
        resume_session_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> DshRunResult:
        self.calls.append({"prompt": prompt, "resume": resume_session_id})
        session_id = resume_session_id or self.session_id
        events: list[dict[str, Any]] = [
            {"type": "session", "sessionId": session_id},
            {"type": "status", "phase": "turn_start", "turn": 1},
            {
                "type": "tool_call",
                "callId": "c1",
                "tool": "read_file",
                "input": {"path": "README.md"},
            },
            {
                "type": "tool_result",
                "callId": "c1",
                "status": "completed",
                "result": "# README",
            },
            {"type": "text", "text": f"stub: {prompt}"},
            {
                "type": "status",
                "phase": "step_end",
                "turn": 1,
                "step": 1,
                "usage": {"inputTokens": 10, "outputTokens": 5},
            },
            {"type": "status", "phase": "turn_end", "turn": 1, "reason": "completed"},
            {"type": "final", "text": f"stub: {prompt}"},
        ]

        result = DshRunResult()
        for event in events:
            if on_event is not None:
                await on_event(event)
            DshRunner._absorb(result, event)
        result.exit_code = 0

        if "fail" in prompt.lower():
            raise DshRunError("simulated failure")
        return result

    async def cancel(self, task_id: str) -> bool:
        return False
