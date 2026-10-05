"""Contract tests for the DSH A2A bridge.

Two layers, both offline:

* the A2A surface (card, auth, JSON-RPC, executor mapping, session store) runs
  against a stub runner, so it needs no subprocess at all;
* the real launcher path (probe, command shape, NDJSON parsing, failure) runs
  ``scripts/fake_dsh.py`` through a ``.cmd`` shim.

The launcher tests need pipe-capable stdio. Inside the DSH Windows sandbox,
child-process pipes are denied (``WinError 5``), so they skip there and should
be run from a normal terminal: ``uv run pytest``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest

from dsh_mcp.agent_card import SKILL_DSH_TASK, build_agent_card
from dsh_mcp.app import build_app
from dsh_mcp.config import Settings, resolve_dsh_binary, shell_command
from dsh_mcp.dsh_runner import DshRunError, DshRunResult, DshRunner, diagnose_stderr
from dsh_mcp.stub_runner import STUB_SESSION_ID, StubRunner

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "scripts" / "fake_dsh.py"

# pytest's own tmp_path machinery scandirs its base temp dir, which the DSH
# Windows sandbox refuses; these tests keep every scratch dir in the repo.
_RUNS = ROOT / ".test-runs"

TERMINAL = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


# ------------------------------------------------------------------ fixtures
@pytest.fixture()
def workdir() -> Path:
    """A workspace-local scratch directory for one test."""
    path = _RUNS / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def fake_bin(workdir: Path) -> str:
    """A ``.cmd`` wrapper around the fake launcher (CreateProcess needs one)."""
    wrapper = workdir / "fake_dsh.cmd"
    wrapper.write_text(
        f'@echo off\r\n"{sys.executable}" "{FAKE}" %*\r\n', encoding="ascii"
    )
    return str(wrapper)


def make_settings(workdir: Path, fake_bin: str, token: str | None = None) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=9101,
        public_url="http://127.0.0.1:9101",
        workdir=workdir,
        state_dir=workdir / "state",
        dsh_bin=fake_bin,
        dsh_home=None,
        profile="headless",
        timeout_seconds=60.0,
        token=token,
        max_concurrency=2,
        enable_v0_3_compat=True,
        extra_args=(),
    )


def stdio_available(fake_bin: str) -> bool:
    """True when this process may spawn a child with piped stdio.

    ``subprocess.run(capture_output=True)`` is not a good probe here: it uses
    anonymous pipes, while asyncio uses overlapped *named* pipes, which the DSH
    Windows sandbox denies (``WinError 5``). Probe the real mechanism instead.
    """

    async def probe() -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                *shell_command(fake_bin, ["--version"]),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, PermissionError):
            return False
        process.stdin.close()
        await process.wait()
        return True

    try:
        return asyncio.run(asyncio.wait_for(probe(), 120))
    except Exception:
        return False


# ------------------------------------------------------------------ stub runner
# The canned runner lives in the package so `dsh-a2a --stub` and these tests
# exercise exactly the same event vocabulary.


# --------------------------------------------------------------------- config
def test_resolve_binary_accepts_fake(fake_bin: str) -> None:
    if not stdio_available(fake_bin):
        pytest.skip("sandbox blocks piped subprocess stdio")
    assert resolve_dsh_binary(fake_bin) == fake_bin


def test_command_shape_keeps_prompt_out_of_argv(workdir: Path, fake_bin: str) -> None:
    runner = DshRunner(make_settings(workdir, fake_bin))
    command = runner.build_command(None)
    assert command[-1] == "-"
    assert "--profile" in command and "headless" in command
    assert "--json" in command
    assert "--session-id" not in command

    resumed = runner.build_command("session-abc")
    assert "--session-id" in resumed
    assert "session-abc" in resumed


def test_agent_card_shape(workdir: Path, fake_bin: str) -> None:
    card = build_agent_card(make_settings(workdir, fake_bin, token="secret"))
    assert card.name == "DSH A2A Agent"
    assert [skill.id for skill in card.skills] == [SKILL_DSH_TASK]
    assert [i.protocol_binding for i in card.supported_interfaces] == [
        "JSONRPC",
        "HTTP+JSON",
    ]
    assert card.capabilities.streaming is True
    assert "bearer" in card.security_schemes


# --------------------------------------------------------------------- runner
def test_runner_parses_events(workdir: Path, fake_bin: str) -> None:
    if not stdio_available(fake_bin):
        pytest.skip("sandbox blocks piped subprocess stdio")
    runner = DshRunner(make_settings(workdir, fake_bin))
    result = asyncio.run(runner.run("hello world", task_id="t1"))
    assert result.exit_code == 0
    assert result.session_id and result.session_id.startswith("session-")
    assert result.summary == "echo: hello world"
    assert result.usage == {"inputTokens": 10, "outputTokens": 5}
    types = [event.get("type") for event in result.events]
    assert types[0] == "session"
    assert types[-1] == "final"
    assert "tool_call" in types


def test_runner_reports_failure(workdir: Path, fake_bin: str) -> None:
    if not stdio_available(fake_bin):
        pytest.skip("sandbox blocks piped subprocess stdio")
    runner = DshRunner(make_settings(workdir, fake_bin))
    with pytest.raises(DshRunError) as excinfo:
        asyncio.run(runner.run("please fail now", task_id="t2"))
    assert "simulated failure" in str(excinfo.value)


def test_diagnose_stderr_prefers_the_real_error() -> None:
    """A Node crash dump must not surface as a bare version banner."""
    dump = (
        "node:fs:2422\n"
        "    return binding.writeFileUtf8(\n"
        "Error: EPERM: operation not permitted, open "
        "'C:\\Users\\x\\.dsh\\profiles\\headless\\cordis.yml'\n"
        "    at writeFileSync (node:fs:2422:20)\n"
        "\n"
        "Node.js v24.18.1\n"
    )
    message = diagnose_stderr(dump, 1)
    assert "EPERM" in message
    assert "cordis.yml" in message
    assert "DSH_MCP_DSH_HOME" in message
    assert not message.startswith("Node.js")


def test_diagnose_stderr_without_detail() -> None:
    assert diagnose_stderr("", 3) == "dsh exited with 3"


def test_absorb_sums_usage() -> None:
    result = DshRunResult()
    DshRunner._absorb(
        result, {"type": "status", "phase": "step_end", "usage": {"inputTokens": 3, "outputTokens": 1}}
    )
    DshRunner._absorb(
        result, {"type": "status", "phase": "step_end", "usage": {"inputTokens": 4, "outputTokens": 2}}
    )
    assert result.usage == {"inputTokens": 7, "outputTokens": 3}


# ------------------------------------------------------------------ A2A flow
def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


async def _request(
    client: httpx.AsyncClient, method: str, params: dict, token: str | None = None
) -> httpx.Response:
    headers = {"A2A-Version": "1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/",
        json={"jsonrpc": "2.0", "id": "1", "method": method, "params": params},
        headers=headers,
    )


async def _drive(
    client: httpx.AsyncClient, prompt: str, context_id: str | None = None
) -> dict:
    message: dict = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "parts": [{"text": prompt}],
    }
    if context_id:
        message["contextId"] = context_id
    response = await _request(client, "SendMessage", {"message": message})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    task_id = payload["result"]["task"]["id"]

    for _ in range(600):
        current = await _request(client, "GetTask", {"id": task_id})
        body = current.json()["result"]
        if body.get("status", {}).get("state") in TERMINAL:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("task did not reach a terminal state")


def test_agent_card_is_public(workdir: Path, fake_bin: str) -> None:
    app = build_app(make_settings(workdir, fake_bin, token="secret"), StubRunner())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9101"
        ) as client:
            return (
                await client.get("/.well-known/agent-card.json"),
                await client.get("/healthz"),
            )

    card, health = _run(scenario())
    assert card.status_code == 200
    assert card.json()["name"] == "DSH A2A Agent"
    assert health.status_code == 200


def test_bearer_token_is_enforced(workdir: Path, fake_bin: str) -> None:
    app = build_app(make_settings(workdir, fake_bin, token="secret"), StubRunner())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9101"
        ) as client:
            denied = await _request(client, "GetTask", {"id": "x"})
            allowed = await _request(client, "GetTask", {"id": "x"}, token="secret")
            return denied, allowed

    denied, allowed = _run(scenario())
    assert denied.status_code == 401
    assert allowed.status_code != 401


def test_send_message_completes_with_artifact(workdir: Path, fake_bin: str) -> None:
    app = build_app(make_settings(workdir, fake_bin), StubRunner())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9101"
        ) as client:
            return await _drive(client, "summarize the repo")

    task = _run(scenario())
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"

    artifacts = task.get("artifacts") or []
    assert artifacts and artifacts[0]["name"] == "dsh-response"
    text = "".join(part.get("text", "") for part in artifacts[0]["parts"])
    assert text == "stub: summarize the repo"

    metadata = artifacts[0].get("metadata") or {}
    assert metadata["dshSessionId"] == STUB_SESSION_ID
    assert metadata["exitCode"] == 0
    assert metadata["continuedSession"] is False
    assert metadata["usage"] == {"inputTokens": 10, "outputTokens": 5}

    history = json.dumps(task.get("history") or [], ensure_ascii=False)
    assert "$ read_file" in history
    assert "read_file finished" in history
def test_follow_up_resumes_same_session(workdir: Path, fake_bin: str) -> None:
    settings = make_settings(workdir, fake_bin)
    stub = StubRunner()
    app = build_app(settings, stub)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9101"
        ) as client:
            first = await _drive(client, "first task")
            second = await _drive(client, "second task", context_id=first["contextId"])
            return first, second

    first, second = _run(scenario())

    assert stub.calls[0]["resume"] is None
    assert stub.calls[1]["resume"] == STUB_SESSION_ID
    assert second["artifacts"][0]["metadata"]["continuedSession"] is True
    assert (
        second["artifacts"][0]["metadata"]["dshSessionId"]
        == first["artifacts"][0]["metadata"]["dshSessionId"]
    )

    stored = json.loads((settings.state_dir / "sessions.json").read_text("utf-8"))
    assert first["contextId"] in stored


def test_failed_run_marks_task_failed(workdir: Path, fake_bin: str) -> None:
    app = build_app(make_settings(workdir, fake_bin), StubRunner())

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9101"
        ) as client:
            return await _drive(client, "please fail")

    task = _run(scenario())
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    message = json.dumps(task["status"].get("message") or {}, ensure_ascii=False)
    assert "simulated failure" in message


# ------------------------------------------------------------------ MCP facade
def _mcp_text(result) -> str:
    return "\n".join(getattr(item, "text", "") for item in result.content)


def test_mcp_exposes_only_two_tools(workdir: Path, fake_bin: str) -> None:
    """Every tool schema costs context on every turn; keep the surface tiny."""
    from dsh_mcp.mcp_server import build_server

    server = build_server(make_settings(workdir, fake_bin))
    tools = asyncio.run(server.list_tools())
    assert sorted(tool.name for tool in tools) == [
        "dsh_status",
        "dsh_task",
    ]


def test_mcp_status_reports_resolved_config(workdir: Path, fake_bin: str) -> None:
    from dsh_mcp.mcp_server import build_server

    settings = make_settings(workdir, fake_bin)
    server = build_server(settings)
    result = asyncio.run(server.call_tool("dsh_status", {"probe_boot": False}))
    payload = json.loads(_mcp_text(result))
    assert payload["launcher"] == fake_bin
    assert payload["profile"] == "headless"
    assert payload["workdir"] == str(workdir)
    assert payload["state_dir"] == str(settings.state_dir)
    assert "profile_boot" not in payload


def test_mcp_task_runs_the_shared_runner(workdir: Path, fake_bin: str) -> None:
    """`dsh_task` drives the same runner the A2A side uses (fake launcher here)."""
    if not stdio_available(fake_bin):
        pytest.skip("sandbox blocks piped subprocess stdio")
    from dsh_mcp.mcp_server import build_server

    server = build_server(make_settings(workdir, fake_bin))
    result = asyncio.run(
        server.call_tool("dsh_task", {"prompt": "hello mcp", "json_output": True})
    )
    payload = json.loads(_mcp_text(result))
    assert payload["answer"] == "echo: hello mcp"
    assert payload["exit_code"] == 0
    assert payload["session_id"].startswith("session-")
    assert payload["usage"] == {"inputTokens": 10, "outputTokens": 5}
