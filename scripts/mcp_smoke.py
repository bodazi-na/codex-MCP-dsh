#!/usr/bin/env python3
"""Smoke-test the DSH MCP server over stdio, exactly like a real client.

Examples:
    python scripts/mcp_smoke.py                          # tools + dsh_status
    python scripts/mcp_smoke.py --task "Reply with: OK"  # also run one real task
    python scripts/mcp_smoke.py --server-python <py>     # pin the interpreter
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _text(result) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    if getattr(result, "structuredContent", None):
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return "\n".join(parts) or repr(result)


async def run(args: argparse.Namespace) -> int:
    environment = {k: v for k, v in os.environ.items() if v is not None}
    if args.workdir:
        environment["DSH_MCP_WORKDIR"] = args.workdir

    if args.http:
        return await run_http(args)

    params = StdioServerParameters(
        command=args.server_python,
        args=["-m", "dsh_mcp.mcp_server"],
        env=environment,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            return await drive(session, args)


async def run_http(args: argparse.Namespace) -> int:
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(args.http) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            return await drive(session, args)


async def drive(session: ClientSession, args: argparse.Namespace) -> int:
    info = await session.initialize()
    server = getattr(info, "serverInfo", None)
    print(f"server   : {getattr(server, 'name', '?')} "
          f"v{getattr(server, 'version', '?')}")

    tools = await session.list_tools()
    names = [tool.name for tool in tools.tools]
    print(f"tools    : {', '.join(names)}")

    status = await session.call_tool("dsh_status", {"probe_boot": True})
    print("--- dsh_status ---")
    print(_text(status))

    if args.task:
        print("--- dsh_task ---")
        result = await session.call_tool(
            "dsh_task", {"prompt": args.task, "json_output": True}
        )
        print(_text(result))
        payload = json.loads(_text(result))
        return 0 if (payload.get("exit_code") == 0 and payload.get("answer")) else 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the DSH MCP server over stdio.")
    parser.add_argument("--task", default=None, help="run one real task through the server")
    parser.add_argument(
        "--http",
        default=None,
        metavar="URL",
        help="connect over streamable-http instead of spawning a stdio server "
        "(e.g. http://127.0.0.1:9102/mcp)",
    )
    parser.add_argument("--workdir", default=None, help="override DSH_MCP_WORKDIR")
    parser.add_argument(
        "--server-python",
        default=sys.executable,
        help="interpreter used to launch the MCP server (defaults to this one)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
