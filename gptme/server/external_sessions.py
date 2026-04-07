"""Read-only external session catalog/transcript integration for the server."""

from __future__ import annotations

import functools
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalSessionCatalogItem:
    """Normalized catalog item for an external session transcript."""

    id: str
    session_id: str
    harness: str
    session_name: str | None
    project: str | None
    model: str | None
    started_at: str | None
    last_activity: str | None
    capabilities: list[str]
    trajectory_path: str

    @classmethod
    def from_transcript_dict(cls, transcript: dict) -> ExternalSessionCatalogItem:
        path = str(transcript["trajectory_path"])
        return cls(
            id=_make_external_session_id(path),
            session_id=str(transcript["session_id"]),
            harness=str(transcript["harness"]),
            session_name=transcript.get("session_name"),
            project=transcript.get("project"),
            model=transcript.get("model"),
            started_at=transcript.get("started_at"),
            last_activity=transcript.get("last_activity"),
            capabilities=list(transcript.get("capabilities") or []),
            trajectory_path=path,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "harness": self.harness,
            "session_name": self.session_name,
            "project": self.project,
            "model": self.model,
            "started_at": self.started_at,
            "last_activity": self.last_activity,
            "capabilities": self.capabilities,
            "trajectory_path": self.trajectory_path,
        }


def _make_external_session_id(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return digest[:16]


class ExternalSessionProvider:
    """Optional provider backed by ``gptme-sessions`` discovery/transcript APIs."""

    def __init__(self) -> None:
        from gptme_sessions.discovery import (  # type: ignore[import-not-found]
            discover_cc_sessions,
            discover_codex_sessions,
            discover_copilot_sessions,
            discover_gptme_sessions,
        )
        from gptme_sessions.transcript import (  # type: ignore[import-not-found]
            read_transcript,
        )

        self._discover_gptme_sessions = discover_gptme_sessions
        self._discover_cc_sessions = discover_cc_sessions
        self._discover_codex_sessions = discover_codex_sessions
        self._discover_copilot_sessions = discover_copilot_sessions
        self._read_transcript = read_transcript

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "external_session_catalog": True,
            "external_session_transcript": True,
        }

    def _discover_paths(self, days: int) -> list[Path]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=max(days - 1, 0))

        paths: list[Path] = []
        paths.extend(self._discover_gptme_sessions(start, end))
        paths.extend(self._discover_cc_sessions(start, end))
        paths.extend(self._discover_codex_sessions(start, end))
        paths.extend(self._discover_copilot_sessions(start, end))
        return paths

    def list_sessions(
        self, limit: int = 100, days: int = 30
    ) -> list[ExternalSessionCatalogItem]:
        items: list[ExternalSessionCatalogItem] = []
        for path in self._discover_paths(days):
            try:
                transcript = self._read_transcript(path).to_dict()
                items.append(
                    ExternalSessionCatalogItem.from_transcript_dict(transcript)
                )
            except Exception:
                logger.warning(
                    "Skipping unreadable external session transcript during catalog listing: %s",
                    path,
                    exc_info=True,
                )

        items.sort(
            key=lambda item: item.last_activity or item.started_at or "", reverse=True
        )
        return items[:limit]

    def get_session(self, external_id: str, days: int = 30) -> dict | None:
        for path in self._discover_paths(days):
            path_str = str(path)
            if _make_external_session_id(path_str) != external_id:
                continue
            try:
                transcript = self._read_transcript(path).to_dict()
            except Exception:
                logger.warning(
                    "Skipping unreadable external session transcript during detail lookup: %s",
                    path,
                    exc_info=True,
                )
                continue
            return {
                "id": _make_external_session_id(path_str),
                "transcript": transcript,
            }
        return None


@functools.lru_cache(maxsize=1)
def get_external_session_provider() -> ExternalSessionProvider | None:
    """Return the optional external session provider if dependencies are available.

    Cached so the import attempt and CLI-only warning happen at most once per process.
    """
    try:
        return ExternalSessionProvider()
    except ImportError:
        _warn_if_cli_only()
        return None


def _warn_if_cli_only() -> None:
    """Log a helpful warning when gptme-sessions CLI is in PATH but not importable.

    This happens when gptme-sessions is installed via ``uv tool install`` (isolated
    venv) rather than into gptme's own Python environment.
    """
    import shutil

    if shutil.which("gptme-sessions"):
        logger.warning(
            "gptme-sessions CLI found in PATH but the gptme_sessions Python package is "
            "not importable from gptme's environment. "
            "The external session catalog feature requires gptme_sessions to be installed "
            "in the same Python environment as gptme-server. "
            "If you installed via 'uv tool install gptme-sessions', that creates an isolated "
            "venv — the module is not visible to gptme. "
            "Instead, install it into gptme's venv: "
            "pip install gptme-sessions  (or add it to gptme's dependencies)."
        )
