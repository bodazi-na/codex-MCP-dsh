"""Runtime configuration for the DSH -> A2A bridge.

Everything is driven by ``DSH_MCP_*`` environment variables so the server can be
started from a shell, a scheduled task, or an orchestrator without editing code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

AGENT_CARD_PATH = "/.well-known/agent-card.json"

_TRUTHY = {"1", "true", "yes", "y", "on"}
_SHELL_SUFFIXES = (".cmd", ".bat")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_flag(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if raw is None else raw.lower() in _TRUTHY


def shell_command(binary: str, args: list[str]) -> list[str]:
    """Wrap a batch shim so ``CreateProcess`` can launch it on Windows."""
    if binary.lower().endswith(_SHELL_SUFFIXES):
        return ["cmd.exe", "/c", binary, *args]
    return [binary, *args]


def _probe(binary: str, timeout: float = 120.0) -> bool:
    """Return True when ``binary`` behaves like the dsh launcher.

    The desktop app ships several shims; a stale one exists on PATH but only
    prints ``[dsh] Harness CLI not found`` and exits 1, so a version probe is
    the only reliable filter.
    """
    environment = {
        **{k: v for k, v in os.environ.items() if v is not None},
        "DSH_TELEMETRY_DISABLED": "1",
        "NO_COLOR": "1",
    }
    try:
        completed = subprocess.run(
            shell_command(binary, ["--version"]),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    output = f"{completed.stdout}{completed.stderr}".strip()
    return bool(output) and "harness cli not found" not in output.lower()


def default_dsh_candidates() -> list[str]:
    """Launcher paths worth probing, most canonical first."""
    candidates: list[str] = []

    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "deepseek-harness" / "bin" / "dsh.cmd"))

    app_dirs = [
        os.environ.get("DSH_APP_DIR"),
        os.environ.get("DSH_MCP_DSH_APP_DIR"),
        "D:\\DSH",
        r"C:\Program Files\DSH",
        r"C:\Program Files (x86)\DSH",
    ]
    if local:
        app_dirs.append(str(Path(local) / "Programs" / "DSH"))
    for app_dir in app_dirs:
        if not app_dir:
            continue
        candidates.append(
            str(Path(app_dir) / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd")
        )

    found = shutil.which("dsh")
    if found:
        candidates.append(found)
    return candidates


def resolve_dsh_binary(explicit: str | None = None) -> str:
    """Find a dsh launcher that actually runs on this machine."""
    candidates: list[str] = []
    for value in (explicit, _env("DSH_MCP_DSH_BIN"), _env("DSH_BIN")):
        if value:
            candidates.append(value)
    candidates.extend(default_dsh_candidates())

    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(Path(candidate))
        if resolved.lower() in seen:
            continue
        seen.add(resolved.lower())
        if not Path(resolved).exists():
            continue
        if _probe(resolved):
            return resolved

    raise RuntimeError(
        "Could not find a working dsh launcher. Set DSH_MCP_DSH_BIN to the full "
        r"path of dsh.cmd (for example D:\DSH\resources\runtime\cli\bin\dsh.cmd) "
        "and try again."
    )


@dataclass(frozen=True)
class Settings:
    """Resolved server settings."""

    host: str = "127.0.0.1"
    port: int = 9101
    public_url: str = ""
    workdir: Path = field(default_factory=Path.cwd)
    state_dir: Path = field(default_factory=Path.cwd)
    dsh_bin: str = ""
    dsh_home: str | None = None
    profile: str = "headless"
    timeout_seconds: float = 1800.0
    token: str | None = None
    max_concurrency: int = 2
    enable_v0_3_compat: bool = True
    extra_args: tuple[str, ...] = ()
    debug_dir: Path | None = None
    call_log: Path | None = None
    agent_name: str = "DSH A2A Agent"
    agent_version: str = "0.1.0"

    @property
    def card_url(self) -> str:
        return self.public_url.rstrip("/") + AGENT_CARD_PATH

    @property
    def rpc_url(self) -> str:
        return self.public_url.rstrip("/") + "/"

    @classmethod
    def from_env(cls) -> "Settings":
        host = _env("DSH_MCP_HOST", "127.0.0.1") or "127.0.0.1"
        port = int(_env("DSH_MCP_PORT", "9101") or "9101")
        public_url = _env("DSH_MCP_PUBLIC_URL") or f"http://{host}:{port}"

        workdir = Path(_env("DSH_MCP_WORKDIR") or os.getcwd()).expanduser()
        workdir.mkdir(parents=True, exist_ok=True)
        state_dir = Path(
            _env("DSH_MCP_STATE_DIR") or str(workdir / ".dsh-a2a")
        ).expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)

        dsh_home = _env("DSH_MCP_DSH_HOME") or _env("DSH_HOME")
        debug_dir_raw = _env("DSH_MCP_DEBUG_DIR")
        call_log_raw = _env("DSH_MCP_CALL_LOG")
        extra_args = tuple(
            part for part in (_env("DSH_MCP_EXTRA_ARGS") or "").split() if part
        )

        return cls(
            host=host,
            port=port,
            public_url=public_url,
            workdir=workdir,
            state_dir=state_dir,
            dsh_bin=resolve_dsh_binary(),
            dsh_home=dsh_home,
            profile=_env("DSH_MCP_PROFILE", "headless") or "headless",
            timeout_seconds=float(
                _env("DSH_MCP_TIMEOUT_SECONDS", "1800") or "1800"
            ),
            token=_env("DSH_MCP_TOKEN"),
            max_concurrency=int(_env("DSH_MCP_MAX_CONCURRENCY", "2") or "2"),
            enable_v0_3_compat=_env_flag("DSH_MCP_V0_3_COMPAT", True),
            extra_args=extra_args,
            debug_dir=Path(debug_dir_raw).expanduser() if debug_dir_raw else None,
            call_log=Path(call_log_raw).expanduser() if call_log_raw else None,
            agent_name=_env("DSH_MCP_AGENT_NAME", "DSH A2A Agent")
            or "DSH A2A Agent",
            agent_version=_env("DSH_MCP_AGENT_VERSION", "0.1.0") or "0.1.0",
        )
