"""Unit tests for gptme.server.external_sessions module.

Tests the data layer (ExternalSessionCatalogItem, ID generation, sorting)
and the CLIExternalSessionProvider subprocess wrapper independently of Flask.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Skip if flask not installed (server extras required)
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.external_sessions import (
    CLIExternalSessionProvider,
    ExternalSessionCatalogItem,
    _cli_has_transcript_command,
    _make_external_session_id,
    get_external_session_provider,
)

# ---------------------------------------------------------------------------
# ExternalSessionCatalogItem
# ---------------------------------------------------------------------------


class TestExternalSessionCatalogItem:
    """Tests for ExternalSessionCatalogItem dataclass methods."""

    @pytest.fixture
    def full_transcript(self) -> dict[str, Any]:
        return {
            "trajectory_path": "/home/user/.local/share/gptme/sessions/test.jsonl",
            "session_id": "sess-123",
            "harness": "gptme",
            "session_name": "My Session",
            "project": "/home/user/project",
            "model": "claude-sonnet-4-5",
            "started_at": "2026-04-07T10:00:00+00:00",
            "last_activity": "2026-04-07T10:30:00+00:00",
            "capabilities": ["view_transcript"],
        }

    def test_from_transcript_dict_full(self, full_transcript: dict[str, Any]):
        item = ExternalSessionCatalogItem.from_transcript_dict(full_transcript)
        assert item.session_id == "sess-123"
        assert item.harness == "gptme"
        assert item.session_name == "My Session"
        assert item.project == "/home/user/project"
        assert item.model == "claude-sonnet-4-5"
        assert item.started_at == "2026-04-07T10:00:00+00:00"
        assert item.last_activity == "2026-04-07T10:30:00+00:00"
        assert item.capabilities == ["view_transcript"]
        assert item.trajectory_path == full_transcript["trajectory_path"]
        # ID is derived from path
        expected_id = _make_external_session_id(full_transcript["trajectory_path"])
        assert item.id == expected_id

    def test_from_transcript_dict_minimal(self):
        """Only required fields present — optional fields default to None/empty."""
        transcript = {
            "trajectory_path": "/tmp/session.jsonl",
            "session_id": "s1",
            "harness": "claude-code",
        }
        item = ExternalSessionCatalogItem.from_transcript_dict(transcript)
        assert item.session_id == "s1"
        assert item.harness == "claude-code"
        assert item.session_name is None
        assert item.project is None
        assert item.model is None
        assert item.started_at is None
        assert item.last_activity is None
        assert item.capabilities == []

    def test_from_transcript_dict_capabilities_none(self):
        """When capabilities is None, it should become an empty list."""
        transcript = {
            "trajectory_path": "/tmp/s.jsonl",
            "session_id": "s1",
            "harness": "gptme",
            "capabilities": None,
        }
        item = ExternalSessionCatalogItem.from_transcript_dict(transcript)
        assert item.capabilities == []

    def test_to_dict_round_trip(self, full_transcript: dict[str, Any]):
        """to_dict() output should match from_transcript_dict() input (plus ID)."""
        item = ExternalSessionCatalogItem.from_transcript_dict(full_transcript)
        d = item.to_dict()
        assert d["session_id"] == full_transcript["session_id"]
        assert d["harness"] == full_transcript["harness"]
        assert d["trajectory_path"] == full_transcript["trajectory_path"]
        assert d["id"] == item.id
        # Reconstruct from to_dict output
        item2 = ExternalSessionCatalogItem(**d)
        assert item2 == item

    def test_frozen_dataclass(self, full_transcript: dict[str, Any]):
        """Items are immutable (frozen=True)."""
        item = ExternalSessionCatalogItem.from_transcript_dict(full_transcript)
        with pytest.raises(AttributeError):
            item.session_id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _make_external_session_id
# ---------------------------------------------------------------------------


class TestMakeExternalSessionId:
    """Tests for the deterministic ID generator."""

    def test_consistent(self):
        """Same path always produces the same ID."""
        path = "/home/user/.local/share/sessions/abc.jsonl"
        assert _make_external_session_id(path) == _make_external_session_id(path)

    def test_different_paths_different_ids(self):
        """Different paths produce different IDs."""
        id1 = _make_external_session_id("/tmp/a.jsonl")
        id2 = _make_external_session_id("/tmp/b.jsonl")
        assert id1 != id2

    def test_length_16_hex(self):
        """ID is exactly 16 hex characters."""
        result = _make_external_session_id("/any/path")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_empty_path(self):
        """Empty string still produces a valid 16-char hex ID."""
        result = _make_external_session_id("")
        assert len(result) == 16


# ---------------------------------------------------------------------------
# CLIExternalSessionProvider._run_cli
# ---------------------------------------------------------------------------


class TestCLIRunCli:
    """Tests for the subprocess wrapper in CLIExternalSessionProvider."""

    def test_run_cli_success(self):
        provider = CLIExternalSessionProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gptme-sessions", "discover"],
                returncode=0,
                stdout='{"sessions": []}',
                stderr="",
            )
            result = provider._run_cli(["discover", "--json"])
            assert result == '{"sessions": []}'

    def test_run_cli_nonzero_exit(self):
        provider = CLIExternalSessionProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gptme-sessions", "bad"],
                returncode=1,
                stdout="",
                stderr="unknown command",
            )
            result = provider._run_cli(["bad"])
            assert result is None

    def test_run_cli_timeout(self):
        provider = CLIExternalSessionProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["gptme-sessions"], timeout=60
            )
            result = provider._run_cli(["discover"], timeout=60)
            assert result is None

    def test_run_cli_file_not_found(self):
        provider = CLIExternalSessionProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("gptme-sessions")
            result = provider._run_cli(["discover"])
            assert result is None


# ---------------------------------------------------------------------------
# CLIExternalSessionProvider._discover_paths and _read_transcript_cli
# ---------------------------------------------------------------------------


class TestCLIDiscoverAndTranscript:
    """Tests for CLI JSON parsing edge cases."""

    def test_discover_paths_invalid_json(self, monkeypatch):
        provider = CLIExternalSessionProvider()
        monkeypatch.setattr(provider, "_run_cli", lambda args, timeout=60: "not json")
        result = provider._discover_paths(days=7)
        assert result == []

    def test_discover_paths_missing_sessions_key(self, monkeypatch):
        provider = CLIExternalSessionProvider()
        monkeypatch.setattr(
            provider, "_run_cli", lambda args, timeout=60: '{"other": []}'
        )
        result = provider._discover_paths(days=7)
        assert result == []

    def test_discover_paths_empty_output(self, monkeypatch):
        provider = CLIExternalSessionProvider()
        monkeypatch.setattr(provider, "_run_cli", lambda args, timeout=60: None)
        result = provider._discover_paths(days=7)
        assert result == []

    def test_read_transcript_cli_invalid_json(self, monkeypatch):
        provider = CLIExternalSessionProvider()
        monkeypatch.setattr(
            provider, "_run_cli", lambda args, timeout=30: "not valid json"
        )
        result = provider._read_transcript_cli("/tmp/s.jsonl")
        assert result is None

    def test_read_transcript_cli_success(self, monkeypatch):
        expected = {"session_id": "abc", "messages": []}
        provider = CLIExternalSessionProvider()
        monkeypatch.setattr(
            provider, "_run_cli", lambda args, timeout=30: json.dumps(expected)
        )
        result = provider._read_transcript_cli("/tmp/s.jsonl")
        assert result == expected


# ---------------------------------------------------------------------------
# _cli_has_transcript_command
# ---------------------------------------------------------------------------


class TestCliHasTranscriptCommand:
    """Tests for the CLI feature-detection helper."""

    def test_returns_true_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gptme-sessions", "transcript", "--help"],
                returncode=0,
                stdout="Usage: ...",
                stderr="",
            )
            assert _cli_has_transcript_command() is True

    def test_returns_false_on_nonzero_exit(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gptme-sessions", "transcript", "--help"],
                returncode=2,
                stdout="",
                stderr="no such command",
            )
            assert _cli_has_transcript_command() is False

    def test_returns_false_on_timeout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["gptme-sessions"], timeout=10
            )
            assert _cli_has_transcript_command() is False

    def test_returns_false_on_file_not_found(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("gptme-sessions")
            assert _cli_has_transcript_command() is False


# ---------------------------------------------------------------------------
# list_sessions sorting
# ---------------------------------------------------------------------------


class TestListSessionsSorting:
    """Tests for sort order and limit enforcement in list_sessions."""

    def test_sorted_by_last_activity_descending(self):
        """Sessions should be sorted by last_activity, most recent first."""
        from gptme.server.external_sessions import ExternalSessionProvider

        provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
        paths = [
            Path("/tmp/old.jsonl"),
            Path("/tmp/new.jsonl"),
            Path("/tmp/mid.jsonl"),
        ]
        provider._discover_paths = lambda days: paths  # type: ignore[method-assign]

        transcripts = {
            "/tmp/old.jsonl": {
                "trajectory_path": "/tmp/old.jsonl",
                "session_id": "old",
                "harness": "gptme",
                "last_activity": "2026-04-01T00:00:00+00:00",
            },
            "/tmp/new.jsonl": {
                "trajectory_path": "/tmp/new.jsonl",
                "session_id": "new",
                "harness": "gptme",
                "last_activity": "2026-04-07T00:00:00+00:00",
            },
            "/tmp/mid.jsonl": {
                "trajectory_path": "/tmp/mid.jsonl",
                "session_id": "mid",
                "harness": "gptme",
                "last_activity": "2026-04-04T00:00:00+00:00",
            },
        }

        class _FakeTranscript:
            def __init__(self, d: dict):
                self._d = d

            def to_dict(self) -> dict:
                return self._d

        provider._read_transcript = lambda p: _FakeTranscript(transcripts[str(p)])  # type: ignore[assignment]

        items = provider.list_sessions(limit=10, days=30)
        assert [i.session_id for i in items] == ["new", "mid", "old"]

    def test_limit_enforced(self):
        """list_sessions respects the limit parameter."""
        from gptme.server.external_sessions import ExternalSessionProvider

        provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
        paths = [Path(f"/tmp/s{i}.jsonl") for i in range(5)]
        provider._discover_paths = lambda days: paths  # type: ignore[method-assign]

        class _FakeTranscript:
            def __init__(self, path: Path):
                self._path = path

            def to_dict(self) -> dict:
                return {
                    "trajectory_path": str(self._path),
                    "session_id": self._path.stem,
                    "harness": "gptme",
                    "last_activity": "2026-04-07T00:00:00+00:00",
                }

        provider._read_transcript = lambda p: _FakeTranscript(p)  # type: ignore[assignment]

        items = provider.list_sessions(limit=2, days=30)
        assert len(items) == 2

    def test_sorting_with_none_last_activity(self):
        """Sessions with None last_activity fall back to started_at."""
        from gptme.server.external_sessions import ExternalSessionProvider

        provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
        paths = [Path("/tmp/with.jsonl"), Path("/tmp/without.jsonl")]
        provider._discover_paths = lambda days: paths  # type: ignore[method-assign]

        transcripts = {
            "/tmp/with.jsonl": {
                "trajectory_path": "/tmp/with.jsonl",
                "session_id": "with",
                "harness": "gptme",
                "last_activity": "2026-04-05T00:00:00+00:00",
            },
            "/tmp/without.jsonl": {
                "trajectory_path": "/tmp/without.jsonl",
                "session_id": "without",
                "harness": "gptme",
                "started_at": "2026-04-06T00:00:00+00:00",
                # no last_activity
            },
        }

        class _FakeTranscript:
            def __init__(self, d: dict):
                self._d = d

            def to_dict(self) -> dict:
                return self._d

        provider._read_transcript = lambda p: _FakeTranscript(transcripts[str(p)])  # type: ignore[assignment]

        items = provider.list_sessions(limit=10, days=30)
        # "without" has started_at=Apr 6, "with" has last_activity=Apr 5
        # "without" should come first (more recent fallback)
        assert items[0].session_id == "without"
        assert items[1].session_id == "with"


# ---------------------------------------------------------------------------
# get_external_session_provider — neither available
# ---------------------------------------------------------------------------


class TestGetProviderNeitherAvailable:
    """Test get_external_session_provider when neither Python nor CLI is available."""

    def test_returns_none(self, monkeypatch):
        get_external_session_provider.cache_clear()
        try:

            def _raise_import_error():
                raise ImportError("no gptme_sessions")

            monkeypatch.setattr(
                "gptme.server.external_sessions.ExternalSessionProvider",
                _raise_import_error,
            )
            monkeypatch.setattr(
                "gptme.server.external_sessions.shutil.which", lambda cmd: None
            )

            result = get_external_session_provider()
            assert result is None
        finally:
            get_external_session_provider.cache_clear()
