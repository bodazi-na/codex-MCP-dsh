"""Persist the A2A context -> DSH session mapping across restarts."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class SessionStore:
    """A tiny JSON-backed map so follow-up turns resume the same DSH session."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sessions: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as error:  # pragma: no cover - defensive
            logger.warning("could not read %s: %s", self._path, error)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ignoring corrupt session map at %s", self._path)
            return
        if isinstance(data, dict):
            self._sessions = {
                str(key): str(value)
                for key, value in data.items()
                if isinstance(key, str) and isinstance(value, str)
            }

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".sessions-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self._sessions, stream, indent=2, ensure_ascii=False)
            os.replace(tmp_name, self._path)
        except OSError as error:  # pragma: no cover - defensive
            logger.warning("could not persist session map: %s", error)

    def get(self, context_id: str | None) -> str | None:
        if not context_id:
            return None
        return self._sessions.get(context_id)

    def set(self, context_id: str | None, session_id: str | None) -> None:
        if not context_id or not session_id:
            return
        if self._sessions.get(context_id) == session_id:
            return
        self._sessions[context_id] = session_id
        self._flush()

    def as_dict(self) -> dict[str, str]:
        return dict(self._sessions)
