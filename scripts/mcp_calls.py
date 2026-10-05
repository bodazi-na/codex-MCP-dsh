#!/usr/bin/env python3
"""Show recent MCP facade calls from the JSONL call log.

The facade appends one line per tool call (``dsh_task`` / ``dsh_status``) to
``mcp_calls.jsonl`` — see ``DSH_A2A_CALL_LOG``. This viewer prints them as a
table.

Examples:
    python scripts/mcp_calls.py                 # last 20 calls
    python scripts/mcp_calls.py --limit 50
    python scripts/mcp_calls.py --json          # raw records
    python scripts/mcp_calls.py --path <file>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def default_log() -> Path:
    override = os.environ.get("DSH_A2A_CALL_LOG")
    if override:
        return Path(override)
    state = os.environ.get("DSH_A2A_STATE_DIR")
    if state:
        return Path(state) / "mcp_calls.jsonl"
    return Path(__file__).resolve().parents[1] / ".mcp-state" / "mcp_calls.jsonl"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def render(records: list[dict]) -> None:
    if not records:
        print("(no calls logged yet)")
        return
    print(
        f"{'when (local)':<20} {'tool':<11} {'ok':<5} {'ms':>7}  {'session':<40} prompt"
    )
    print("-" * 122)
    for record in records:
        when = _local(record.get("ts"))
        tool = str(record.get("tool", "?"))
        ok = "ok" if record.get("ok") else "FAIL"
        duration = record.get("duration_ms")
        duration_text = f"{duration}" if isinstance(duration, int) else "-"
        session = str(record.get("session_id") or record.get("requested_session") or "")
        prompt = str(record.get("prompt_preview") or record.get("error") or "")[:60]
        print(
            f"{when:<20} {tool:<11} {ok:<5} {duration_text:>7}  {session:<40} {prompt}"
        )


def _local(stamp: object) -> str:
    """Render a stored UTC timestamp in the machine's local zone."""
    if not isinstance(stamp, str):
        return ""
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stamp[:19].replace("T", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show recent MCP facade calls.")
    parser.add_argument("--path", default=None, help="call log file (default: env-derived)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="print raw records")
    parser.add_argument("--all", action="store_true", help="every record, not just the tail")
    args = parser.parse_args()

    path = Path(args.path) if args.path else default_log()
    records = load(path)
    print(f"log: {path}  ({len(records)} call(s) recorded)")
    selected = records if args.all else records[-args.limit :]

    if args.json:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
    else:
        render(selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
