"""Expose the local DeepSeek Harness as an MCP server.

This is the mirror image of :mod:`dsh_a2a.app`: A2A is how other *agents* call
DSH; MCP is how other *clients* (WorkBuddy, Codex, Claude Code, Cursor, Cherry
Studio, …) mount DSH as a native tool. Both share :mod:`dsh_a2a.dsh_runner`, so
a task runs through exactly one code path.

Transports:

    python -m dsh_a2a.mcp_server                 # stdio (most clients)
    python -m dsh_a2a.mcp_server --http          # streamable-http on /mcp

Tool surface is deliberately tiny — every tool schema is paid for on every
turn: ``dsh_task`` (run one task) and ``dsh_status`` (diagnose the wiring).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import Settings, resolve_dsh_binary, shell_command
from .dsh_runner import DshRunError, DshRunner
from .session_store import SessionStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

INSTRUCTIONS = (
    "DeepSeek Harness (dsh) running on this machine as a native tool.\n"
    "\n"
    "`dsh_task` runs ONE task with the local harness: it can read and write "
    "files in its workspace, run commands, use its own skills and memory, and "
    "answer in the caller's language. Pass the returned `session_id` back on a "
    "later call to continue the same conversation.\n"
    "\n"
    "Use it for work that suits a second agent with its own workspace: "
    "multi-step file or code tasks, long-form writing, research, anything the "
    "local harness has a skill for. Do not use it for questions you can answer "
    "directly — each call costs a full agent run.\n"
    "\n"
    "`dsh_status` reports which launcher, profile and workspace the bridge "
    "resolved, plus whether the profile boots; call it first when a task fails "
    "with an environment error."
)


def build_server(settings: Settings) -> MCPServer:
    """Assemble the MCP server for the given settings."""
    server = MCPServer(
        name="dsh",
        title="DeepSeek Harness",
        version=__version__,
        instructions=INSTRUCTIONS,
    )
    runner = DshRunner(settings)
    sessions = SessionStore(settings.state_dir / "mcp_sessions.json")

    @server.tool(
        name="dsh_task",
        description=(
            "Run one task with the local DeepSeek Harness agent and return its "
            "final answer. Optionally pass session_id from a previous call to "
            "continue that session; pass json_output=true to also receive the "
            "session id, exit code and token usage."
        ),
    )
    async def dsh_task(
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        json_output: bool = False,
    ) -> str:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        effective = settings
        if timeout_seconds and timeout_seconds > 0:
            effective = _with_timeout(settings, float(timeout_seconds))

        task_id = f"mcp-{os.getpid()}-{abs(hash(prompt)) % 10_000_000}"
        active = DshRunner(effective) if effective is not settings else runner
        try:
            result = await active.run(
                prompt,
                task_id=task_id,
                resume_session_id=session_id,
            )
        except DshRunError as error:
            raise RuntimeError(f"dsh could not finish the task: {error}") from error

        if result.session_id:
            sessions.set("mcp-last", result.session_id)

        answer = result.summary or "(dsh returned no text for this run.)"
        if not json_output:
            return answer
        return json.dumps(
            {
                "answer": answer,
                "session_id": result.session_id,
                "exit_code": result.exit_code,
                "usage": result.usage,
                "continued_session": bool(session_id),
            },
            ensure_ascii=False,
            indent=2,
        )

    @server.tool(
        name="dsh_status",
        description=(
            "Report the DeepSeek Harness bridge configuration: resolved dsh "
            "launcher, profile, workspace, state directory and whether the "
            "profile boots. Use it to diagnose environment errors."
        ),
    )
    async def dsh_status(probe_boot: bool = False) -> str:
        payload: dict[str, Any] = {
            "launcher": settings.dsh_bin,
            "profile": settings.profile,
            "workdir": str(settings.workdir),
            "state_dir": str(settings.state_dir),
            "dsh_home": settings.dsh_home or "(inherited)",
            "max_concurrency": settings.max_concurrency,
            "timeout_seconds": settings.timeout_seconds,
            "python": sys.executable,
            "last_session": sessions.get("mcp-last"),
        }
        if probe_boot:
            payload["profile_boot"] = await _probe_profile(settings)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return server


def _with_timeout(settings: Settings, timeout_seconds: float) -> Settings:
    return Settings(
        **{**settings.__dict__, "timeout_seconds": timeout_seconds}
    )


async def _probe_profile(settings: Settings) -> str:
    """Boot the profile once (``--help``); catches an unwritable DSH home."""
    from .dsh_runner import diagnose_stderr

    environment = {k: v for k, v in os.environ.items() if v is not None}
    if settings.dsh_home:
        environment["DSH_HOME"] = settings.dsh_home
    environment.setdefault("DSH_TELEMETRY_DISABLED", "1")
    try:
        process = await asyncio.create_subprocess_exec(
            *shell_command(settings.dsh_bin, ["--profile", settings.profile, "--help"]),
            cwd=str(settings.workdir),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        return f"could not start the launcher: {error}"
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    if process.returncode == 0:
        return "OK"
    text = stdout.decode("utf-8", "replace") + stderr.decode("utf-8", "replace")
    return "FAILED — " + diagnose_stderr(text, process.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dsh-mcp",
        description="Serve the local DeepSeek Harness over the Model Context Protocol.",
    )
    parser.add_argument("--http", action="store_true", help="streamable-http instead of stdio")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None, help="default 9102")
    parser.add_argument("--check", action="store_true", help="print the resolved config and exit")
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.check:
        print(f"launcher : {settings.dsh_bin}")
        print(f"profile  : {settings.profile}")
        print(f"workdir  : {settings.workdir}")
        print(f"state dir: {settings.state_dir}")
        print(f"python   : {sys.executable}")
        if args.http:
            print(f"endpoint : http://{args.host or settings.host}:{args.port or 9102}/mcp")
        else:
            print("transport: stdio")
        return 0

    server = build_server(settings)
    if not args.http:
        server.run(transport="stdio")
        return 0

    host = args.host or settings.host
    port = args.port or 9102
    server.run(transport="streamable-http", host=host, port=port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
