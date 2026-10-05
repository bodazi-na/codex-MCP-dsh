"""Run the DSH headless profile and turn its JSON event stream into typed events.

The command surface is documented by the shipped ``@deepseek-ai/dsh-headless``
package:

    dsh --profile headless [--json] [--session-id <id>] [task | -]

``--json`` prints newline-delimited events; the stream opens with a ``session``
event carrying the identity the run adopted and ends with ``final``. Exit code 0
means a completed ``turn/end``; anything else is a failed or aborted run.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .config import Settings, shell_command

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

_USAGE_KEYS = (
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "reasoningTokens",
)


class DshRunError(RuntimeError):
    """Raised when the DSH headless run fails to finish a task."""


@dataclass
class DshRunResult:
    """Outcome of a single headless run."""

    session_id: str | None = None
    final_text: str = ""
    texts: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    exit_code: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    stderr_tail: str = ""

    @property
    def summary(self) -> str:
        return self.final_text.strip() or (
            self.texts[-1].strip() if self.texts else ""
        )


class DshRunner:
    """Spawns ``dsh --profile headless`` per task and streams its events."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))

    # ------------------------------------------------------------------ env
    def _environment(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if v is not None}
        if self._settings.dsh_home:
            env["DSH_HOME"] = self._settings.dsh_home
        env.setdefault("DSH_TELEMETRY_DISABLED", "1")
        env.setdefault("NO_COLOR", "1")
        return env

    def build_command(self, resume_session_id: str | None) -> list[str]:
        settings = self._settings
        args = [settings.dsh_bin, "--profile", settings.profile, "--json"]
        if resume_session_id:
            args += ["--session-id", resume_session_id]
        args += list(settings.extra_args)
        # ``-`` reads the task from stdin, which keeps prompts out of argv and
        # avoids Windows quoting limits.
        args.append("-")
        return shell_command(args[0], args[1:])

    # ------------------------------------------------------------------ run
    async def run(
        self,
        prompt: str,
        *,
        task_id: str,
        resume_session_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> DshRunResult:
        """Execute one headless run, streaming parsed JSON events to ``on_event``."""
        command = self.build_command(resume_session_id)
        logger.info("task %s: %s", task_id, " ".join(command[:-1]))

        async with self._semaphore:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(self._settings.workdir),
                    env=self._environment(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as error:
                # Windows sandboxes (including the DSH sandbox) deny the
                # overlapped named pipes a piped child needs, which surfaces as
                # WinError 5 here. Run the bridge from a normal terminal.
                raise DshRunError(
                    f"could not start {command[0]!r} with piped stdio: {error}"
                ) from error
            self._processes[task_id] = process
            try:
                return await asyncio.wait_for(
                    self._consume(process, prompt, on_event),
                    timeout=self._settings.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                await self._terminate(process)
                raise DshRunError(
                    f"dsh did not finish within "
                    f"{self._settings.timeout_seconds:.0f}s"
                ) from exc
            finally:
                self._processes.pop(task_id, None)

    async def _consume(
        self,
        process: asyncio.subprocess.Process,
        prompt: str,
        on_event: EventCallback | None,
    ) -> DshRunResult:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        stderr_chunks: list[str] = []

        async def drain_stderr() -> None:
            while True:
                chunk = await process.stderr.readline()
                if not chunk:
                    return
                stderr_chunks.append(chunk.decode("utf-8", errors="replace"))

        stderr_task = asyncio.create_task(drain_stderr())
        result = DshRunResult()

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if not text.startswith("{"):
                # The launcher may print plain diagnostics on stdout.
                logger.debug("ignoring non-JSON stdout line: %s", text[:200])
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("ignoring malformed JSON line: %s", text[:200])
                continue
            result.events.append(event)
            self._absorb(result, event)
            if on_event is not None:
                await on_event(event)

        result.exit_code = await process.wait()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        result.stderr_tail = "".join(stderr_chunks)[-4000:]

        if result.exit_code not in (0, None):
            detail = [line for line in result.stderr_tail.splitlines() if line.strip()]
            raise DshRunError(
                detail[-1].strip()
                if detail
                else f"dsh exited with {result.exit_code}"
            )
        return result

    @staticmethod
    def _absorb(result: DshRunResult, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "session":
            result.session_id = event.get("sessionId") or result.session_id
        elif kind == "text":
            text = event.get("text")
            if isinstance(text, str) and text.strip():
                result.texts.append(text)
        elif kind == "final":
            text = event.get("text")
            if isinstance(text, str):
                result.final_text = text
        elif kind == "status" and event.get("phase") == "step_end":
            usage = event.get("usage")
            if isinstance(usage, dict):
                result.usage = _add_usage(result.usage, usage)

    # --------------------------------------------------------------- cancel
    async def cancel(self, task_id: str) -> bool:
        process = self._processes.get(task_id)
        if process is None:
            return False
        await self._terminate(process)
        return True

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except (asyncio.TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()


def _add_usage(total: dict[str, Any] | None, next_usage: dict[str, Any]) -> dict[str, Any]:
    """Sum per-step usage, keeping every bucket that both sides reported."""
    if total is None:
        return {key: next_usage[key] for key in _USAGE_KEYS if key in next_usage}
    merged: dict[str, Any] = {}
    for key in _USAGE_KEYS:
        if key in total and key in next_usage:
            merged[key] = total[key] + next_usage[key]
        elif key in total:
            merged[key] = total[key]
        elif key in next_usage:
            merged[key] = next_usage[key]
    return merged
