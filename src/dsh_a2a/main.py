"""Command-line entry point for the DSH A2A bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .app import agent_card_json, build_app
from .config import Settings
from .dsh_runner import DshRunner


def _read_task(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return value


def _cmd_check(settings: Settings) -> int:
    print(f"dsh launcher : {settings.dsh_bin}")
    print(f"profile      : {settings.profile}")
    print(f"workdir      : {settings.workdir}")
    print(f"state dir    : {settings.state_dir}")
    print(f"dsh home     : {settings.dsh_home or '(inherited)'}")
    print(f"listen       : http://{settings.host}:{settings.port}")
    print(f"auth         : {'bearer token set' if settings.token else 'disabled'}")
    print(f"concurrency  : {settings.max_concurrency}")
    print(f"timeout      : {settings.timeout_seconds:.0f}s")
    return 0


def _cmd_once(settings: Settings, task: str, as_json: bool) -> int:
    runner = DshRunner(settings)

    async def drive() -> int:
        def on_event(event: dict) -> None:
            if as_json:
                print(json.dumps(event, ensure_ascii=False), flush=True)

        from .dsh_runner import DshRunError

        try:
            result = await runner.run(task, task_id="once", on_event=_make_async(on_event))
        except DshRunError as error:
            print(f"run failed: {error}", file=sys.stderr)
            return 1
        if not as_json:
            print(result.summary)
        print(f"\n[session {result.session_id} exit {result.exit_code}]", file=sys.stderr)
        return 0

    return asyncio.run(drive())


def _make_async(func):
    async def wrapper(event):
        func(event)

    return wrapper


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dsh-a2a",
        description=(
            "Expose the locally installed DeepSeek Harness (dsh) as an A2A agent."
        ),
    )
    parser.add_argument("--check", action="store_true", help="print resolved config")
    parser.add_argument("--print-card", action="store_true", help="print the Agent Card")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="serve with the canned stub runner (wiring checks, no model cost)",
    )
    parser.add_argument(
        "--once",
        metavar="TASK",
        help="run one task through dsh and exit (use '-' to read stdin)",
    )
    parser.add_argument("--json", action="store_true", help="with --once: print raw events")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.host or args.port:
        settings = Settings(
            **{
                **settings.__dict__,
                "host": args.host or settings.host,
                "port": args.port or settings.port,
                "public_url": (
                    f"http://{args.host or settings.host}:{args.port or settings.port}"
                    if (args.host or args.port)
                    else settings.public_url
                ),
            }
        )

    if args.check:
        return _cmd_check(settings)
    if args.print_card:
        print(agent_card_json(settings))
        return 0
    if args.once is not None:
        return _cmd_once(settings, _read_task(args.once), args.json)

    import uvicorn

    runner = None
    if args.stub:
        from .stub_runner import StubRunner

        runner = StubRunner()
        print("dsh-a2a: --stub active, answering with canned events", file=sys.stderr)

    uvicorn.run(
        build_app(settings, runner),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
