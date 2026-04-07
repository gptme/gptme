"""Read-only external session catalog/transcript integration for the server."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import shutil
import subprocess
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


class CLIExternalSessionProvider:
    """Fallback provider that shells out to the ``gptme-sessions`` CLI.

    Used when the ``gptme_sessions`` Python package is not importable from
    gptme's environment (e.g. installed via ``uv tool install`` which creates
    an isolated venv), but the CLI binary *is* available in PATH.

    Requires ``gptme-sessions`` >= 0.1.0 with the ``transcript`` subcommand.
    """

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "external_session_catalog": True,
            "external_session_transcript": True,
        }

    def _run_cli(self, args: list[str], timeout: int = 60) -> str | None:
        """Run a gptme-sessions CLI command and return stdout, or None on failure."""
        try:
            result = subprocess.run(
                ["gptme-sessions", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "gptme-sessions %s failed (exit %d): %s",
                    " ".join(args),
                    result.returncode,
                    result.stderr.strip()[:200],
                )
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.warning(
                "gptme-sessions %s timed out after %ds", " ".join(args), timeout
            )
            return None
        except FileNotFoundError:
            logger.warning("gptme-sessions not found in PATH")
            return None

    def _discover_paths(self, days: int) -> list[dict]:
        """Discover sessions via CLI, returning raw session dicts with path+harness."""
        output = self._run_cli(["discover", "--since", f"{days}d", "--json"])
        if not output:
            return []
        try:
            data = json.loads(output)
            return data.get("sessions", [])
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to parse gptme-sessions discover JSON output")
            return []

    def _read_transcript_cli(self, path: str) -> dict | None:
        """Read a transcript via CLI, returning the full JSON dict."""
        output = self._run_cli(["transcript", str(path), "--json"], timeout=30)
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            logger.warning("Failed to parse transcript JSON for %s", path)
            return None

    def list_sessions(
        self, limit: int = 100, days: int = 30
    ) -> list[ExternalSessionCatalogItem]:
        items: list[ExternalSessionCatalogItem] = []
        for session in self._discover_paths(days):
            path = session.get("path")
            if not path:
                continue
            try:
                transcript = self._read_transcript_cli(path)
                if transcript is None:
                    continue
                items.append(
                    ExternalSessionCatalogItem.from_transcript_dict(transcript)
                )
            except Exception:
                logger.warning(
                    "Skipping unreadable external session transcript (CLI) during catalog listing: %s",
                    path,
                    exc_info=True,
                )

        items.sort(
            key=lambda item: item.last_activity or item.started_at or "", reverse=True
        )
        return items[:limit]

    def get_session(self, external_id: str, days: int = 30) -> dict | None:
        for session in self._discover_paths(days):
            path = session.get("path")
            if not path:
                continue
            if _make_external_session_id(path) != external_id:
                continue
            try:
                transcript = self._read_transcript_cli(path)
                if transcript is None:
                    continue
            except Exception:
                logger.warning(
                    "Skipping unreadable external session transcript (CLI) during detail lookup: %s",
                    path,
                    exc_info=True,
                )
                continue
            return {
                "id": _make_external_session_id(path),
                "transcript": transcript,
            }
        return None


def _cli_has_transcript_command() -> bool:
    """Check if the installed gptme-sessions CLI has the ``transcript`` subcommand."""
    try:
        result = subprocess.run(
            ["gptme-sessions", "transcript", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@functools.lru_cache(maxsize=1)
def get_external_session_provider() -> (
    ExternalSessionProvider | CLIExternalSessionProvider | None
):
    """Return the external session provider, preferring Python import over CLI fallback.

    Detection order:
    1. Python import (``gptme_sessions`` package in gptme's env) — fastest, no subprocess overhead
    2. CLI fallback (``gptme-sessions`` binary in PATH with ``transcript`` subcommand)
    3. ``None`` — feature unavailable

    Cached so detection happens at most once per process.
    """
    # Preferred: direct Python import
    try:
        return ExternalSessionProvider()
    except ImportError:
        pass

    # Fallback: CLI-based provider
    if shutil.which("gptme-sessions") and _cli_has_transcript_command():
        logger.info(
            "gptme_sessions Python package not importable, but gptme-sessions CLI found "
            "in PATH with transcript support — using CLI-based external session provider. "
            "For better performance, install gptme-sessions into gptme's Python environment."
        )
        return CLIExternalSessionProvider()

    # Neither available
    if shutil.which("gptme-sessions"):
        logger.warning(
            "gptme-sessions CLI found in PATH but it lacks the 'transcript' subcommand. "
            "Upgrade gptme-sessions to get external session catalog support: "
            "uv tool upgrade gptme-sessions"
        )
    return None
