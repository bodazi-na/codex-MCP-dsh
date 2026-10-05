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
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

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


_FAILURE_MARKERS = ("EPERM", "EACCES", "ENOENT", "WinError", "EADDRINUSE", "MODULE_NOT_FOUND")


def diagnose_stderr(text: str, exit_code: int | None) -> str:
    """Turn a crashed child's stderr into one actionable line.

    A Node crash dump ends with a bare ``Node.js v24.18.1`` banner, which tells
    a caller nothing; the real reason sits a few lines above it.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return f"dsh exited with {exit_code}"

    chosen = next(
        (line for line in lines if any(marker in line for marker in _FAILURE_MARKERS)),
        None,
    )
    if chosen is None:
        chosen = next(
            (
                line
                for line in lines
                if line.startswith(
                    ("Error", "error", "fatal", "TypeError", "ReferenceError", "SyntaxError")
                )
            ),
            None,
        )
    if chosen is None:
        chosen = lines[-1]

    message = chosen if len(chosen) <= 400 else chosen[:399] + "…"
    hint = _hint_for(chosen)
    return f"{message}{hint}"


def _hint_for(line: str) -> str:
    """Append the fix for the two sandbox-shaped failures we know about."""
    if "EPERM" in line or "EACCES" in line:
        return (
            " | dsh could not write its DSH home. Start dsh-a2a from a normal "
            "terminal (not from an agent's sandboxed shell), or set "
            "DSH_MCP_DSH_HOME to a writable directory."
        )
    if "WinError 5" in line:
        return (
            " | the sandbox denied the child's pipes. Run dsh-a2a outside the "
            "sandbox."
        )
    return ""


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
                    self._consume(process, prompt, on_event, command),
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
        command: list[str],
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
        stdout_lines: list[str] = []

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            stdout_lines.append(text)
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
        stderr_text = "".join(stderr_chunks)
        result.stderr_tail = stderr_text[-4000:]
        self._dump_debug(command, result, stdout_lines, stderr_text)

        if result.exit_code not in (0, None):
            raise DshRunError(diagnose_stderr(stderr_text, result.exit_code))
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

    # ---------------------------------------------------------------- debug
    def _dump_debug(
        self,
        command: list[str],
        result: DshRunResult,
        stdout_lines: list[str],
        stderr_text: str,
    ) -> None:
        """Write one run's raw streams when ``DSH_MCP_DEBUG_DIR`` is configured."""
        target = self._settings.debug_dir
        if target is None:
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            path = target / f"run-{stamp}-{uuid4().hex[:8]}.log"
            env = self._environment()
            interesting = {
                key: env.get(key)
                for key in ("DSH_HOME", "TEMP", "TMP", "PATH", "NO_COLOR")
                if key in env
            }
            interesting["PATH"] = (interesting.get("PATH") or "")[:600]
            path.write_text(
                "\n".join(
                    [
                        f"command: {command}",
                        f"cwd: {self._settings.workdir}",
                        f"exit: {result.exit_code}",
                        f"env: {json.dumps(interesting, ensure_ascii=False)}",
                        "--- stdout ---",
                        *stdout_lines,
                        "--- stderr ---",
                        stderr_text,
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        except OSError as error:  # pragma: no cover - diagnostics must not fail a run
            logger.warning("could not write debug dump: %s", error)

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
