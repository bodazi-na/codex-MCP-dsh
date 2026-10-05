"""A2A agent executor backed by the local DeepSeek Harness."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from .config import Settings
from .dsh_runner import DshRunError, DshRunner
from .session_store import SessionStore

logger = logging.getLogger(__name__)

_MAX_STATUS_MESSAGE = 400
_MAX_OUTPUT_SNIPPET = 1200
_MAX_TOOL_INPUT = 300


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_tool(event: dict[str, Any]) -> str:
    tool = str(event.get("tool") or "tool")
    payload = event.get("input")
    if payload in (None, {}, ""):
        return tool
    try:
        rendered = payload if isinstance(payload, str) else _compact(payload)
    except Exception:  # pragma: no cover - defensive
        rendered = str(payload)
    return f"{tool} {_clip(rendered, _MAX_TOOL_INPUT)}"


def _compact(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(value)


class DshAgentExecutor(AgentExecutor):
    """Turns A2A messages into DSH headless runs and streams progress back."""

    def __init__(self, settings: Settings, runner: DshRunner) -> None:
        self._settings = settings
        self._runner = runner
        # A2A context_id -> DSH session id, so follow-up messages in the same
        # conversation resume the same DSH session instead of starting over.
        self._sessions = SessionStore(settings.state_dir / "sessions.json")
        self._tool_names: dict[str, str] = {}

    # ------------------------------------------------------------- execute
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id or ""
        context_id = context.context_id or ""
        updater = TaskUpdater(event_queue, task_id, context_id)

        prompt = (context.get_user_input() or "").strip()
        if not prompt:
            await updater.reject(
                message=updater.new_agent_message(
                    [
                        Part(
                            text=(
                                "Send the task as a text part, for example: "
                                "「summarize the TODOs in this workspace」."
                            )
                        )
                    ]
                )
            )
            return

        if context.current_task is None:
            # The framework requires the initial Task event before any status
            # update; follow-up messages on an existing task skip this step.
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message] if context.message else [],
                )
            )

        resume_session_id = self._sessions.get(context_id)
        await updater.start_work(
            message=updater.new_agent_message(
                [
                    Part(
                        text=(
                            f"Running `dsh --profile {self._settings.profile}` in "
                            f"{self._settings.workdir}"
                            + (
                                f", resuming session {resume_session_id}"
                                if resume_session_id
                                else ""
                            )
                        )
                    )
                ]
            )
        )

        on_event = functools.partial(self._publish, updater, context_id=context_id)

        try:
            result = await self._runner.run(
                prompt,
                task_id=task_id,
                resume_session_id=resume_session_id,
                on_event=on_event,
            )
        except asyncio.CancelledError:
            # The framework cancels this coroutine on tasks/cancel; the
            # cancel() hook below reports the terminal state.
            await self._runner.cancel(task_id)
            raise
        except DshRunError as error:
            logger.warning("task %s failed: %s", task_id, error)
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text=f"DSH could not finish the task: {error}")]
                )
            )
            return
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("unexpected failure for task %s", task_id)
            await updater.failed(
                message=updater.new_agent_message(
                    [Part(text=f"Unexpected bridge failure: {error}")]
                )
            )
            return

        if result.session_id:
            self._sessions.set(context_id, result.session_id)

        answer = result.summary or "(DSH returned no text for this run.)"
        metadata: dict[str, Any] = {
            "dshSessionId": result.session_id or "",
            "exitCode": result.exit_code,
            "continuedSession": bool(resume_session_id),
            "profile": self._settings.profile,
        }
        if result.usage:
            metadata["usage"] = result.usage

        await updater.add_artifact(
            [Part(text=answer)],
            artifact_id=f"{task_id}:dsh-response",
            name="dsh-response",
            metadata=metadata,
            last_chunk=True,
        )
        await updater.complete(
            message=updater.new_agent_message(
                [Part(text=_clip(answer, _MAX_STATUS_MESSAGE))]
            )
        )

    # -------------------------------------------------------------- cancel
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id or ""
        updater = TaskUpdater(event_queue, task_id, context.context_id or "")
        stopped = await self._runner.cancel(task_id)
        await updater.cancel(
            message=updater.new_agent_message(
                [
                    Part(
                        text=(
                            "DSH run cancelled."
                            if stopped
                            else "Nothing was running for this task."
                        )
                    )
                ]
            )
        )

    # ------------------------------------------------------------ mapping
    async def _publish(
        self,
        updater: TaskUpdater,
        event: dict[str, Any],
        *,
        context_id: str | None = None,
    ) -> None:
        """Map one ``dsh --json`` event onto an A2A status update."""
        kind = event.get("type")

        if kind == "session":
            # Remember the session as soon as it exists, so a later turn can
            # resume it even if this run is cancelled or fails.
            self._sessions.set(context_id, event.get("sessionId"))
            return

        if kind == "status":
            phase = event.get("phase")
            if phase == "turn_start":
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message(
                        [Part(text=f"turn {event.get('turn')} started")]
                    ),
                )
            return

        if kind == "tool_call":
            call_id = str(event.get("callId") or "")
            if call_id:
                self._tool_names[call_id] = str(event.get("tool") or "tool")
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(
                    [Part(text=f"$ {_describe_tool(event)}")]
                ),
            )
            return

        if kind == "tool_result":
            call_id = str(event.get("callId") or "")
            tool = self._tool_names.pop(call_id, "tool")
            ok = event.get("status") != "error"
            snippet = _clip(str(event.get("result") or ""), _MAX_OUTPUT_SNIPPET)
            text = f"{'✓' if ok else '✗'} {tool} finished"
            if snippet:
                text = f"{text}\n{snippet}"
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message([Part(text=_clip(text, 1500))]),
                metadata={"tool": tool, "status": event.get("status")},
            )
            return

        if kind == "text":
            text = _clip(str(event.get("text") or ""), _MAX_STATUS_MESSAGE)
            if text:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text=text)]),
                )
            return

        if kind == "thinking":
            text = _clip(str(event.get("text") or ""), _MAX_STATUS_MESSAGE)
            if text:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text=f"(thinking) {text}")]),
                    metadata={"dshEventType": "thinking"},
                )
            return
