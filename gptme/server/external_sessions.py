"""Read-only external session catalog/transcript integration for the server."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _path_is_under(path: str | None, parent: Path) -> bool:
    """Whether ``path`` (resolved) is inside ``parent``. False on any error."""
    if not path:
        return False
    try:
        return Path(path).resolve().is_relative_to(parent)
    except OSError:
        return False


def _own_logs_dir() -> Path | None:
    """This server's own conversation logs dir (resolved), or None on error.

    Used to exclude the server's native conversations from external-session
    discovery — the gptme harness discovery scans the same logs directory the
    server already serves, so without this filter every local conversation
    shows up twice in the webui (once native, once "external").
    """
    try:
        from ..dirs import get_logs_dir

        return get_logs_dir().resolve()
    except Exception:
        return None


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
        caps: dict[str, bool] = {
            "external_session_catalog": True,
            "external_session_transcript": True,
        }
        try:
            from gptme_sessions.steer import (
                inject_message,  # type: ignore[import-not-found]
            )

            _ = inject_message  # verify importable
            caps["external_session_steer"] = True
        except ImportError:
            pass
        return caps

    def steer_session(self, trajectory_path: str, message: str) -> bool:
        """Inject a user message into a running session via gptme_sessions.steer.

        Returns True on success, False if the steer module is unavailable.
        Raises on other errors (session not found, harness not supported, etc.).
        """
        try:
            from gptme_sessions.steer import (
                inject_message,  # type: ignore[import-not-found]
            )
        except ImportError:
            return False
        inject_message(trajectory_path, message)
        return True

    def _discover_paths(self, days: int) -> list[Path]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=max(days - 1, 0))

        paths: list[Path] = []
        # gptme discovery returns session *directories*; resolve to the
        # conversation.jsonl file inside so IDs are consistent everywhere.
        # Sessions under this server's own logs dir are excluded: they are
        # already served as native conversations, and listing them again as
        # "external" duplicates every conversation in the webui.
        own_logs_dir = _own_logs_dir()
        for p in self._discover_gptme_sessions(start, end):
            jsonl = p / "conversation.jsonl"
            if not jsonl.exists():
                logger.debug(
                    "gptme session dir has no conversation.jsonl, skipping: %s", p
                )
                continue
            resolved = jsonl.resolve()
            if own_logs_dir and resolved.is_relative_to(own_logs_dir):
                continue
            paths.append(resolved)
        paths.extend(p.resolve() for p in self._discover_cc_sessions(start, end))
        paths.extend(p.resolve() for p in self._discover_codex_sessions(start, end))
        paths.extend(p.resolve() for p in self._discover_copilot_sessions(start, end))
        return paths

    def list_sessions(
        self, limit: int = 100, days: int = 30
    ) -> list[ExternalSessionCatalogItem]:
        items: list[ExternalSessionCatalogItem] = []
        # Cap discovery to avoid scanning thousands of sessions when we only
        # need `limit` results. Sort recently-active paths first so the most
        # relevant sessions appear even under the cap.
        paths = self._discover_paths(days)
        paths.sort(key=self._mtime, reverse=True)

        # Process at most limit * 3 paths to allow for unreadable/failed
        # transcripts while still hitting the target count. This prevents
        # the endpoint from hanging when there are 30k+ sessions on disk.
        max_paths = max(limit * 3, 500)
        for path in paths[:max_paths]:
            try:
                transcript = self._read_transcript(path).to_dict()
                # Canonicalize to the discovered path: get_session() derives the
                # ID from this exact string, while the transcript may report a
                # differently-normalized trajectory_path (unresolved symlinks,
                # session dir vs file). Any mismatch makes listed sessions 404
                # on detail lookup.
                transcript["trajectory_path"] = str(path)
                items.append(
                    ExternalSessionCatalogItem.from_transcript_dict(transcript)
                )
            except Exception:
                logger.debug(
                    "Skipping unreadable external session transcript during catalog listing: %s",
                    path,
                )

        items.sort(
            key=lambda item: item.last_activity or item.started_at or "", reverse=True
        )
        return items[:limit]

    @staticmethod
    def _mtime(path: Path) -> float:
        """Get mtime of a Path, returning 0 on any filesystem error."""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    def get_session(self, external_id: str, days: int = 30) -> dict | None:
        paths = self._discover_paths(days)
        # Sort paths by mtime (most recent first) before capping, so that
        # sessions found by list_sessions() (which also sorts by mtime) are
        # reachable by ID lookup. Without this sort, a recently-active session
        # that lives deep in unsorted path order would 404 from get_session()
        # while appearing in list_sessions() results.
        paths.sort(key=self._mtime, reverse=True)
        # Cap the scan to avoid 30k+ transcript reads for a single ID
        # lookup when the session doesn't exist.
        max_scan = 2000
        for path in paths[:max_scan]:
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
            # Keep trajectory_path consistent with the catalog listing (the ID
            # is derived from this string in both places).
            transcript["trajectory_path"] = path_str
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
        caps: dict[str, bool] = {
            "external_session_catalog": True,
            "external_session_transcript": True,
        }
        if _cli_has_steer_command():
            caps["external_session_steer"] = True
        return caps

    def steer_session(self, trajectory_path: str, message: str) -> bool:
        """Inject a user message into a running session via the gptme-sessions CLI.

        Returns True on success, False if the steer subcommand is unavailable.
        Raises on other errors (non-zero exit after args are validated, timeout).
        """
        if not _cli_has_steer_command():
            return False
        output = self._run_cli(
            ["steer", trajectory_path, "--message", message, "--json"], timeout=30
        )
        return output is not None

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
            sessions = data.get("sessions", [])
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to parse gptme-sessions discover JSON output")
            return []
        # Exclude sessions under this server's own logs dir — they are already
        # served as native conversations (see ExternalSessionProvider._discover_paths).
        own_logs_dir = _own_logs_dir()
        if own_logs_dir:
            sessions = [
                s for s in sessions if not _path_is_under(s.get("path"), own_logs_dir)
            ]
        # gptme discovery reports session *directories*, but the `transcript`
        # subcommand only accepts files — resolve to the conversation.jsonl
        # inside (mirrors ExternalSessionProvider._discover_paths). Without
        # this every remaining gptme session logs a CLI usage error and is
        # skipped.
        normalized: list[dict] = []
        for s in sessions:
            path = s.get("path")
            if path and Path(path).is_dir():
                jsonl = Path(path) / "conversation.jsonl"
                if not jsonl.exists():
                    logger.debug(
                        "gptme session dir has no conversation.jsonl, skipping: %s",
                        path,
                    )
                    continue
                s = {**s, "path": str(jsonl)}
            normalized.append(s)
        return normalized

    def _read_transcript_cli(self, path: str) -> dict | None:
        """Read a transcript via CLI, returning the full JSON dict."""
        output = self._run_cli(["transcript", str(path), "--json"], timeout=10)
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            logger.warning("Failed to parse transcript JSON for %s", path)
            return None

    @staticmethod
    def _cli_mtime(s: dict) -> float:
        """Get mtime from a session dict, returning 0 on any filesystem error."""
        path = s.get("path")
        if not path:
            return 0
        try:
            return os.path.getmtime(str(path))
        except OSError:
            return 0

    def list_sessions(
        self, limit: int = 100, days: int = 30
    ) -> list[ExternalSessionCatalogItem]:
        items: list[ExternalSessionCatalogItem] = []
        sessions = self._discover_paths(days)

        # Sort by mtime descending (most recent first) so the most relevant
        # sessions are processed early under the cap. _cli_mtime handles
        # missing files internally so the sort always completes.
        sessions.sort(key=self._cli_mtime, reverse=True)

        # Cap the number of sessions we process to avoid hanging on 30k+ sessions.
        # Process at most limit * 3 paths to allow for unreadable transcripts
        # while still hitting the target count.
        max_paths = max(limit * 3, 500)
        for session in sessions[:max_paths]:
            path = session.get("path")
            if not path:
                continue
            try:
                transcript = self._read_transcript_cli(path)
                if transcript is None:
                    continue
                # Canonicalize to the discovered path: get_session() derives
                # the ID from this exact string, while the transcript may
                # report a differently-normalized trajectory_path (e.g.
                # symlinks resolved: ~/.claude -> ~/dotfiles/home/.claude).
                # Any mismatch makes every listed session 404 on detail lookup.
                transcript["trajectory_path"] = str(path)
                items.append(
                    ExternalSessionCatalogItem.from_transcript_dict(transcript)
                )
            except Exception:
                logger.debug(
                    "Skipping unreadable external session transcript (CLI) during catalog listing: %s",
                    path,
                )

        items.sort(
            key=lambda item: item.last_activity or item.started_at or "", reverse=True
        )
        return items[:limit]

    def get_session(self, external_id: str, days: int = 30) -> dict | None:
        sessions = self._discover_paths(days)

        # Sort by mtime descending (same as list_sessions()) so that sessions
        # visible in catalog results are also reachable by ID lookup. Without
        # this sort, a recently-active session at deep path order would appear
        # in list_sessions() results but 404 from get_session().
        sessions.sort(key=self._cli_mtime, reverse=True)

        # Cap the scan to avoid 30k+ subprocess calls for a single ID
        # lookup when the session doesn't exist (worst case: 30s timeout × N).
        max_scan = 2000
        for session in sessions[:max_scan]:
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
            # Keep trajectory_path consistent with the catalog listing (the ID
            # is derived from this string in both places).
            transcript["trajectory_path"] = str(path)
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
def _cli_has_steer_command() -> bool:
    """Check if the installed gptme-sessions CLI has the ``steer`` subcommand."""
    try:
        result = subprocess.run(
            ["gptme-sessions", "steer", "--help"],
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
