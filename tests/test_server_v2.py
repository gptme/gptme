import random
import time
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from gptme.config import ChatConfig, MCPConfig
from gptme.llm.models import ModelMeta, get_default_model
from gptme.prompts import get_prompt
from gptme.tools import get_toolchain

# Skip if flask not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

# Mark tests that require the server and add timeouts
pytestmark = [pytest.mark.timeout(10)]  # 10 second timeout for all tests


def create_conversation(client: FlaskClient, config: ChatConfig | None = None):
    """Create a V2 conversation with a session and optional config."""
    convname = f"test-server-v2-{random.randint(0, 1000000)}"

    # Create conversation with a custom system prompt
    json: dict[str, Any] = {
        "prompt": "You are an AI assistant for testing.",
    }

    if config:
        json["config"] = config.to_dict()

    response = client.put(
        f"/api/v2/conversations/{convname}",
        json=json,
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "session_id" in data

    return {"conversation_id": convname, "session_id": data["session_id"]}


@pytest.fixture
def v2_conv(client: FlaskClient):
    """Create a V2 conversation with a session."""
    return create_conversation(client)


@pytest.fixture
def v2_conv_with_config(client: FlaskClient, config: ChatConfig):
    """Create a V2 conversation with a session and config."""
    return create_conversation(client, config)


def test_v2_api_root(client: FlaskClient, monkeypatch):
    """Test the V2 API root endpoint."""
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: None
    )

    response = client.get("/api/v2")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "message" in data
    assert "gptme v2 API" in data["message"]
    assert data["capabilities"] == {
        "external_session_catalog": False,
        "external_session_transcript": False,
    }


class _FakeExternalSessionItem:
    def __init__(self):
        self.id = "abc123"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": "abc123",
            "session_id": "session-1",
            "harness": "claude-code",
            "session_name": "Test Session",
            "project": "/tmp/project",
            "model": "claude-sonnet-4-5",
            "started_at": "2026-04-06T12:00:00+00:00",
            "last_activity": "2026-04-06T12:05:00+00:00",
            "capabilities": ["view_transcript"],
            "trajectory_path": "/tmp/session.jsonl",
        }


class _FakeExternalSessionProvider:
    capabilities = {
        "external_session_catalog": True,
        "external_session_transcript": True,
    }

    def list_sessions(
        self, limit: int = 100, days: int = 30
    ) -> list[_FakeExternalSessionItem]:
        assert limit >= 1
        assert days >= 1
        return [_FakeExternalSessionItem()]

    def get_session(self, external_id: str, days: int = 30) -> dict[str, Any] | None:
        assert days >= 1
        if external_id != "abc123":
            return None
        return {
            "id": "abc123",
            "transcript": {
                "session_id": "session-1",
                "harness": "claude-code",
                "messages": [{"role": "user", "content": "hi"}],
            },
        }


@pytest.fixture
def fake_external_session_provider(monkeypatch):
    provider = _FakeExternalSessionProvider()
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: provider
    )
    return provider


def test_v2_api_root_with_external_sessions(
    client: FlaskClient, fake_external_session_provider
):
    """Test the V2 API root endpoint when optional external sessions are available."""
    response = client.get("/api/v2")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["capabilities"] == {
        "external_session_catalog": True,
        "external_session_transcript": True,
    }


def test_v2_external_sessions_list(client: FlaskClient, fake_external_session_provider):
    """Test listing read-only external sessions."""
    response = client.get("/api/v2/external-sessions")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["sessions"] == [_FakeExternalSessionItem().to_dict()]


def test_v2_external_session_get(client: FlaskClient, fake_external_session_provider):
    """Test retrieving a read-only external session transcript."""
    response = client.get("/api/v2/external-sessions/abc123")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["id"] == "abc123"
    assert data["transcript"]["session_id"] == "session-1"


def test_v2_external_sessions_unavailable(client: FlaskClient, monkeypatch):
    """Test external session endpoints when provider is unavailable."""
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: None
    )

    response = client.get("/api/v2/external-sessions")
    assert response.status_code == 503
    data = response.get_json()
    assert data is not None
    assert "unavailable" in data["error"]


def test_v2_external_session_not_found(
    client: FlaskClient, fake_external_session_provider
):
    """Test requesting a missing external session transcript."""
    response = client.get("/api/v2/external-sessions/does-not-exist")
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "not found" in data["error"].lower()


def test_external_session_provider_get_session_searches_beyond_list_limit():
    """Test session lookup scans all discovered sessions instead of a capped list."""
    from gptme.server.external_sessions import (
        ExternalSessionProvider,
        _make_external_session_id,
    )

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    late_path = Path("/tmp/sessions/late-session.jsonl")
    provider._discover_paths = lambda days: [late_path]  # type: ignore[method-assign]

    class _Transcript:
        def to_dict(self) -> dict[str, Any]:
            return {
                "trajectory_path": str(late_path),
                "session_id": "late-session",
                "harness": "claude-code",
                "session_name": "Late Session",
                "project": "/tmp/project",
                "model": "claude-sonnet-4-5",
                "started_at": "2026-04-01T00:00:00+00:00",
                "last_activity": "2026-04-01T00:05:00+00:00",
                "capabilities": ["view_transcript"],
                "messages": [{"role": "user", "content": "hello"}],
            }

    provider._read_transcript = lambda path: _Transcript()  # type: ignore[assignment]

    result = provider.get_session(_make_external_session_id(str(late_path)), days=30)

    assert result is not None
    assert result["id"] == _make_external_session_id(str(late_path))
    assert result["transcript"]["session_id"] == "late-session"


def test_external_session_provider_list_sessions_skips_unreadable_transcripts():
    """Test unreadable transcripts do not break catalog listing."""
    from gptme.server.external_sessions import ExternalSessionProvider

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    bad_path = Path("/tmp/sessions/bad-session.jsonl")
    good_path = Path("/tmp/sessions/good-session.jsonl")
    provider._discover_paths = lambda days: [bad_path, good_path]  # type: ignore[method-assign]

    class _Transcript:
        def __init__(self, path: Path) -> None:
            self.path = path

        def to_dict(self) -> dict[str, Any]:
            return {
                "trajectory_path": str(self.path),
                "session_id": self.path.stem,
                "harness": "claude-code",
                "session_name": self.path.stem,
                "project": "/tmp/project",
                "model": "claude-sonnet-4-5",
                "started_at": "2026-04-01T00:00:00+00:00",
                "last_activity": "2026-04-01T00:05:00+00:00",
                "capabilities": ["view_transcript"],
            }

    def _read_transcript(path: Path) -> _Transcript:
        if path == bad_path:
            raise ValueError("broken transcript")
        return _Transcript(path)

    provider._read_transcript = _read_transcript  # type: ignore[assignment]

    result = provider.list_sessions(limit=10, days=30)

    assert len(result) == 1
    assert result[0].session_id == "good-session"


def test_external_session_provider_get_session_skips_unreadable_matching_transcript():
    """Test unreadable matching transcripts do not raise during detail lookup."""
    from gptme.server.external_sessions import (
        ExternalSessionProvider,
        _make_external_session_id,
    )

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    bad_path = Path("/tmp/sessions/bad-session.jsonl")
    provider._discover_paths = lambda days: [bad_path]  # type: ignore[method-assign]

    def _read_transcript(path: Path) -> Any:
        raise ValueError(f"cannot read {path}")

    provider._read_transcript = _read_transcript  # type: ignore[assignment]

    result = provider.get_session(_make_external_session_id(str(bad_path)), days=30)

    assert result is None


def test_cli_provider_list_sessions(monkeypatch):
    """Test CLIExternalSessionProvider.list_sessions parses discover + transcript output."""
    import json

    from gptme.server.external_sessions import CLIExternalSessionProvider

    provider = CLIExternalSessionProvider()

    discover_output = json.dumps(
        {
            "sessions": [
                {
                    "harness": "gptme",
                    "path": "/tmp/sessions/test.jsonl",
                    "session_date": "2026-04-07",
                    "synced": False,
                }
            ]
        }
    )
    transcript_output = json.dumps(
        {
            "schema_version": 1,
            "session_id": "test",
            "harness": "gptme",
            "session_name": "test-session",
            "project": "/tmp/project",
            "model": "claude-sonnet-4-5",
            "started_at": "2026-04-07T10:00:00+00:00",
            "last_activity": "2026-04-07T10:05:00+00:00",
            "trajectory_path": "/tmp/sessions/test.jsonl",
            "capabilities": ["view_transcript"],
            "messages": [],
        }
    )

    call_count = {"discover": 0, "transcript": 0}

    def mock_run_cli(args: list[str], timeout: int = 60) -> str | None:
        if args[0] == "discover":
            call_count["discover"] += 1
            return discover_output
        if args[0] == "transcript":
            call_count["transcript"] += 1
            return transcript_output
        return None

    monkeypatch.setattr(provider, "_run_cli", mock_run_cli)

    items = provider.list_sessions(limit=10, days=7)
    assert len(items) == 1
    assert items[0].session_id == "test"
    assert items[0].harness == "gptme"
    assert items[0].session_name == "test-session"
    assert call_count["discover"] == 1
    assert call_count["transcript"] == 1


def test_cli_provider_get_session(monkeypatch):
    """Test CLIExternalSessionProvider.get_session returns matching transcript."""
    import json

    from gptme.server.external_sessions import (
        CLIExternalSessionProvider,
        _make_external_session_id,
    )

    provider = CLIExternalSessionProvider()
    target_path = "/tmp/sessions/target.jsonl"
    target_id = _make_external_session_id(target_path)

    discover_output = json.dumps(
        {
            "sessions": [
                {
                    "harness": "claude-code",
                    "path": target_path,
                    "session_date": "2026-04-07",
                }
            ]
        }
    )
    transcript_output = json.dumps(
        {
            "schema_version": 1,
            "session_id": "target",
            "harness": "claude-code",
            "trajectory_path": target_path,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    def mock_run_cli(args: list[str], timeout: int = 60) -> str | None:
        if args[0] == "discover":
            return discover_output
        if args[0] == "transcript":
            return transcript_output
        return None

    monkeypatch.setattr(provider, "_run_cli", mock_run_cli)

    result = provider.get_session(target_id, days=7)
    assert result is not None
    assert result["id"] == target_id
    assert result["transcript"]["session_id"] == "target"

    # Non-matching ID returns None
    result = provider.get_session("nonexistent", days=7)
    assert result is None


def test_cli_provider_handles_failed_transcript(monkeypatch):
    """Test CLIExternalSessionProvider gracefully handles transcript failures."""
    import json

    from gptme.server.external_sessions import CLIExternalSessionProvider

    provider = CLIExternalSessionProvider()

    discover_output = json.dumps(
        {
            "sessions": [
                {"harness": "gptme", "path": "/tmp/bad.jsonl"},
                {"harness": "gptme", "path": "/tmp/good.jsonl"},
            ]
        }
    )

    def mock_run_cli(args: list[str], timeout: int = 60) -> str | None:
        if args[0] == "discover":
            return discover_output
        if args[0] == "transcript":
            path = args[1]
            if "bad" in path:
                return None  # Simulate failure
            return json.dumps(
                {
                    "session_id": "good",
                    "harness": "gptme",
                    "trajectory_path": "/tmp/good.jsonl",
                    "messages": [],
                }
            )
        return None

    monkeypatch.setattr(provider, "_run_cli", mock_run_cli)

    items = provider.list_sessions(limit=10, days=7)
    assert len(items) == 1
    assert items[0].session_id == "good"


def test_get_provider_prefers_python_import(monkeypatch):
    """Test that get_external_session_provider prefers Python import over CLI."""
    from gptme.server.external_sessions import (
        ExternalSessionProvider,
        get_external_session_provider,
    )

    get_external_session_provider.cache_clear()

    fake_provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    monkeypatch.setattr(
        "gptme.server.external_sessions.ExternalSessionProvider",
        lambda: fake_provider,
    )

    result = get_external_session_provider()
    assert result is fake_provider
    get_external_session_provider.cache_clear()


def test_get_provider_falls_back_to_cli(monkeypatch):
    """Test that get_external_session_provider falls back to CLI when import fails."""
    from gptme.server.external_sessions import (
        CLIExternalSessionProvider,
        get_external_session_provider,
    )

    get_external_session_provider.cache_clear()

    def _raise_import_error():
        raise ImportError("no gptme_sessions")

    monkeypatch.setattr(
        "gptme.server.external_sessions.ExternalSessionProvider",
        _raise_import_error,
    )
    monkeypatch.setattr(
        "gptme.server.external_sessions.shutil.which",
        lambda cmd: "/usr/bin/gptme-sessions",
    )
    monkeypatch.setattr(
        "gptme.server.external_sessions._cli_has_transcript_command", lambda: True
    )

    result = get_external_session_provider()
    assert isinstance(result, CLIExternalSessionProvider)
    get_external_session_provider.cache_clear()


def test_get_provider_returns_none_when_cli_lacks_transcript(monkeypatch):
    """Test that get_external_session_provider returns None when CLI lacks transcript command."""
    from gptme.server.external_sessions import get_external_session_provider

    get_external_session_provider.cache_clear()

    def _raise_import_error():
        raise ImportError("no gptme_sessions")

    monkeypatch.setattr(
        "gptme.server.external_sessions.ExternalSessionProvider",
        _raise_import_error,
    )
    monkeypatch.setattr(
        "gptme.server.external_sessions.shutil.which",
        lambda cmd: "/usr/bin/gptme-sessions",
    )
    monkeypatch.setattr(
        "gptme.server.external_sessions._cli_has_transcript_command", lambda: False
    )

    result = get_external_session_provider()
    assert result is None
    get_external_session_provider.cache_clear()


@pytest.mark.timeout(30)
def test_external_session_list_then_get_roundtrip(client: FlaskClient, monkeypatch):
    """Webui flow: list sessions → pick ID → fetch that session by ID.

    This is the exact sequence the webui performs. If the IDs returned by
    list don't match what get_session expects, the detail request 404s.
    """
    from gptme.server.external_sessions import ExternalSessionProvider

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    # Use a resolved path so the ID is the same everywhere — _discover_paths
    # resolves in production; tests that override it must do the same.
    session_path = Path("/tmp/sessions/roundtrip-session.jsonl").resolve()
    provider._discover_paths = lambda days: [session_path]  # type: ignore[method-assign]

    class _Transcript:
        def to_dict(self) -> dict[str, Any]:
            return {
                "trajectory_path": str(session_path),
                "session_id": "roundtrip",
                "harness": "claude-code",
                "session_name": "Roundtrip Session",
                "project": "/tmp/project",
                "model": "claude-sonnet-4-5",
                "started_at": "2026-04-07T10:00:00+00:00",
                "last_activity": "2026-04-07T10:05:00+00:00",
                "capabilities": ["view_transcript"],
                "messages": [{"role": "user", "content": "hello"}],
            }

    provider._read_transcript = lambda path: _Transcript()  # type: ignore[assignment]
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: provider
    )

    # Step 1: list sessions (webui catalog fetch)
    list_resp = client.get("/api/v2/external-sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.get_json()["sessions"]
    assert len(sessions) == 1
    session_id = sessions[0]["id"]

    # Step 2: fetch that specific session by its ID (webui detail fetch)
    detail_resp = client.get(f"/api/v2/external-sessions/{session_id}")
    assert detail_resp.status_code == 200, (
        f"Expected 200 but got {detail_resp.status_code} — "
        f"ID {session_id} from list was not found by get_session"
    )
    detail = detail_resp.get_json()
    assert detail["id"] == session_id
    assert detail["transcript"]["session_id"] == "roundtrip"


def test_external_session_gptme_directory_roundtrip(tmp_path: Path):
    """ExternalSessionProvider resolves gptme session dirs and IDs stay consistent.

    discover_gptme_sessions returns directories. _discover_paths must resolve
    them to conversation.jsonl so list_sessions and get_session use matching IDs.
    """
    pytest.importorskip("gptme_sessions")
    import json

    from gptme.server.external_sessions import ExternalSessionProvider

    # Set up a fake gptme session directory with conversation.jsonl
    session_dir = tmp_path / "2026-04-07-test-gptme-session"
    session_dir.mkdir()
    conv_jsonl = session_dir / "conversation.jsonl"
    conv_jsonl.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "role": "user",
                    "content": "hello",
                    "timestamp": "2026-04-07T10:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "hi there",
                    "timestamp": "2026-04-07T10:01:00+00:00",
                },
            ]
        )
        + "\n"
    )

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    # Simulate discover_gptme_sessions returning directory paths
    provider._discover_gptme_sessions = lambda start, end: [session_dir]  # type: ignore[attr-defined]
    provider._discover_cc_sessions = lambda start, end: []  # type: ignore[attr-defined]
    provider._discover_codex_sessions = lambda start, end: []  # type: ignore[attr-defined]
    provider._discover_copilot_sessions = lambda start, end: []  # type: ignore[attr-defined]

    from gptme_sessions.transcript import (  # type: ignore[import-not-found]
        read_transcript,
    )

    provider._read_transcript = read_transcript  # type: ignore[assignment]

    # List: should succeed (not IsADirectoryError) and return a session
    items = provider.list_sessions(limit=10, days=7)
    assert len(items) == 1
    assert items[0].harness == "gptme"
    listed_id = items[0].id

    # Get: the ID from listing must work for detail lookup
    result = provider.get_session(listed_id, days=7)
    assert result is not None, (
        f"get_session returned None for ID {listed_id} — "
        "ID mismatch between list_sessions and get_session"
    )
    assert result["id"] == listed_id
    assert result["transcript"]["harness"] == "gptme"


def test_external_session_gptme_directory_no_jsonl_skipped(tmp_path: Path):
    """gptme session dirs without conversation.jsonl are skipped at discovery time."""
    pytest.importorskip("gptme_sessions")

    from gptme.server.external_sessions import ExternalSessionProvider

    # Session directory with no conversation.jsonl inside
    session_dir = tmp_path / "2026-04-07-empty-gptme-session"
    session_dir.mkdir()

    provider = ExternalSessionProvider.__new__(ExternalSessionProvider)
    provider._discover_gptme_sessions = lambda start, end: [session_dir]  # type: ignore[attr-defined]
    provider._discover_cc_sessions = lambda start, end: []  # type: ignore[attr-defined]
    provider._discover_codex_sessions = lambda start, end: []  # type: ignore[attr-defined]
    provider._discover_copilot_sessions = lambda start, end: []  # type: ignore[attr-defined]

    from gptme_sessions.transcript import (  # type: ignore[import-not-found]
        read_transcript,
    )

    provider._read_transcript = read_transcript  # type: ignore[assignment]

    # Should return 0 items — the directory is skipped early, not propagated as an error
    items = provider.list_sessions(limit=10, days=7)
    assert items == [], f"Expected no sessions, got: {items}"


def test_v2_conversations_list(client: FlaskClient):
    """Test listing V2 conversations."""
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_v2_conversation_get(v2_conv, client: FlaskClient):
    """Test getting a V2 conversation."""
    conversation_id = v2_conv["conversation_id"]
    response = client.get(f"/api/v2/conversations/{conversation_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data

    # Should contain system messages (custom system prompt + possibly workspace prompt)
    assert len(data["log"]) >= 1  # At least custom system prompt
    assert data["log"][0]["role"] == "system"
    assert "testing" in data["log"][0]["content"]


def test_v2_create_conversation_default_system_prompt(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test creating a V2 conversation with a default system prompt."""
    # Use tmp_path as workspace to avoid workspace context message
    monkeypatch.chdir(tmp_path)
    # Explicitly disable chat history for this test
    monkeypatch.setenv("GPTME_CHAT_HISTORY", "false")

    # Fully isolate from user config.
    # Patching gptme.config.config_path alone is insufficient because:
    #   - user.py has its own module-level config_path (not affected by __init__ patch)
    #   - workspace.py imports config_path at import time (binding is stale after patch)
    # Instead, inject a clean Config with default user settings directly.
    from gptme.config import Config, set_config
    from gptme.config.user import default_config

    set_config(Config(user=default_config))
    # Also patch workspace.py's config_path so it doesn't find user-level agent files
    monkeypatch.setattr(
        "gptme.prompts.workspace.config_path",
        str(tmp_path / "config.toml"),
    )

    convname = f"test-server-v2-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello, this is a test message.",
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }
            ]
        },
    )
    assert response.status_code == 200
    conversation_id = response.get_json()["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data
    assert (
        len(data["log"]) == 2
    )  # Only system prompt + user message (no workspace context)
    assert data["log"][0]["role"] == "system"  # Primary system prompt
    assert data["log"][1]["role"] == "user"
    assert data["log"][1]["content"] == "Hello, this is a test message."

    # Check that the system prompt is the default one
    prompt_msgs = get_prompt(
        tools=list(get_toolchain(None)),
        interactive=True,
        tool_format="markdown",
        model=None,
        prompt="full",
        workspace=tmp_path,
    )
    assert data["log"][0]["content"] == prompt_msgs[0].content


def test_v2_conversation_post(v2_conv, client: FlaskClient):
    """Test posting a message to a V2 conversation."""
    conversation_id = v2_conv["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Hello, this is a test message."},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"

    # Verify message was added
    response = client.get(f"/api/v2/conversations/{conversation_id}")
    data = response.get_json()
    # Should have system messages + the user message we just added
    assert len(data["log"]) >= 2  # At least custom system prompt + user message
    # Last message should be the user message we added
    assert data["log"][-1]["role"] == "user"
    assert data["log"][-1]["content"] == "Hello, this is a test message."


@pytest.mark.slow
@pytest.mark.requires_api
def test_v2_generate(v2_conv, client: FlaskClient):
    """Test generating a response in a V2 conversation."""
    # Skip if no API key is available
    default_model = get_default_model()
    if default_model is None:
        pytest.skip("No API key available for testing")

    # Use cast to tell mypy that default_model is not None
    model = cast(ModelMeta, default_model)
    model_name = model.full

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "What is 2+2?"},
    )

    # Start generation
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": model_name},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert data["session_id"] == session_id


@pytest.mark.slow
@pytest.mark.requires_api
def test_v2_interrupt(v2_conv, client: FlaskClient):
    """Test interrupting generation in a V2 conversation."""
    # Skip if no API key is available
    default_model = get_default_model()
    if default_model is None:
        pytest.skip("No API key available for testing")

    # Use cast to tell mypy that default_model is not None
    model = cast(ModelMeta, default_model)
    model_name = model.full

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message (simple prompt to minimize API usage)
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Count from 1 to 10"},
    )

    # Start generation
    client.post(
        f"/api/v2/conversations/{conversation_id}/step",
        json={"session_id": session_id, "model": model_name},
    )

    # Wait briefly to let generation start (but with a short timeout)
    time.sleep(0.2)

    # Interrupt generation
    response = client.post(
        f"/api/v2/conversations/{conversation_id}/interrupt",
        json={"session_id": session_id},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert "interrupted" in data["message"].lower()


def test_v2_chat_config_saved_on_conversation_create(client: FlaskClient):
    """Test that the chat config is saved on conversation create."""
    input_config = ChatConfig(model="openai/gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    data = response.get_json()
    assert data is not None
    assert "logfile" in data

    logfile = Path(data["logfile"])
    assert logfile.exists()
    assert logfile.is_file()

    config_path = logfile.parent / "config.toml"
    assert config_path.exists()
    assert config_path.is_file()

    config = ChatConfig.from_logdir(logfile.parent)
    print("old config", input_config.to_dict())
    print("-" * 80)
    print("new config", config.to_dict())
    assert config.to_dict() == input_config.to_dict()


def test_v2_chat_config_saved_separately_for_each_conversation(client: FlaskClient):
    """Test that the chat config is saved separately for each conversation."""
    input_config_1 = ChatConfig(model="openai/gpt-4o")
    input_config_1.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config_1.mcp = MCPConfig()
    conversation_id_1 = create_conversation(client, input_config_1)["conversation_id"]

    input_config_2 = ChatConfig(model="openai/gpt-4o-mini")
    input_config_2.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config_2.mcp = MCPConfig()
    conversation_id_2 = create_conversation(client, input_config_2)["conversation_id"]

    response_1 = client.get(f"/api/v2/conversations/{conversation_id_1}")
    data_1 = response_1.get_json()
    config_1 = ChatConfig.from_logdir(Path(data_1["logfile"]).parent)
    assert config_1.to_dict() == input_config_1.to_dict()

    response_2 = client.get(f"/api/v2/conversations/{conversation_id_2}")
    data_2 = response_2.get_json()
    config_2 = ChatConfig.from_logdir(Path(data_2["logfile"]).parent)
    assert config_2.to_dict() == input_config_2.to_dict()


def test_v2_chat_config_get_works(client: FlaskClient):
    """Test that the chat config get endpoint works."""
    input_config = ChatConfig(model="openai/gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    print("config", config.to_dict())
    print("input_config", input_config.to_dict())
    assert config.to_dict() == input_config.to_dict()


def test_v2_chat_config_update_works(client: FlaskClient):
    """Test that the chat config update endpoint works."""
    input_config = ChatConfig(model="openai/gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert config.to_dict() == input_config.to_dict()

    input_config.model = "openai/gpt-4o-mini"
    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config", json=input_config.to_dict()
    )
    assert response.status_code == 200

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert config.to_dict() == input_config.to_dict()


@pytest.mark.parametrize(
    "files_payload",
    [
        "attachments/image.png",
        ["attachments/image.png", 123],
        {"path": "attachments/image.png"},
    ],
)
def test_v2_edit_message_rejects_invalid_files_payload(
    client: FlaskClient, files_payload: object
):
    """Test that edit rejects malformed files payloads with a 400."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Original message"},
    )
    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    user_index = len(conversation["log"]) - 1

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/messages/{user_index}",
        json={"files": files_payload},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "files must be a list of strings"}

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    assert conversation["log"][user_index]["content"] == "Original message"
    assert "files" not in conversation["log"][user_index]


@pytest.mark.parametrize(
    "files_payload",
    [
        "attachments/image.png",
        ["attachments/image.png", 123],
        {"path": "attachments/image.png"},
    ],
)
def test_v2_post_message_rejects_invalid_files_payload(
    client: FlaskClient, files_payload: object
):
    """Test that POST message rejects malformed files payloads with a 400."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Test message", "files": files_payload},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "files must be a list of strings"}


def test_v2_edit_message_preserves_uri_files(client: FlaskClient):
    """Test that editing message files preserves URI attachments."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Original message"},
    )
    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    user_index = len(conversation["log"]) - 1
    uri = "https://example.com/image.png"

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/messages/{user_index}",
        json={"files": [uri]},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["log"][user_index]["files"] == [uri]

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    assert conversation["log"][user_index]["files"] == [uri]


def test_v2_edit_message_rejects_non_string_content(client: FlaskClient):
    """Test that edit rejects non-string content with a 400."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Original message"},
    )
    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    user_index = len(conversation["log"]) - 1

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/messages/{user_index}",
        json={"content": 123},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "content must be a string"}


@pytest.mark.parametrize(
    "auto_confirm_value",
    [
        "true",  # string "true" — truthy in Python, must be rejected
        "false",  # string "false" — truthy in Python, must be rejected (CWE-20)
        "yes",
        "1",
        [],
        {},
    ],
)
def test_v2_create_conversation_rejects_invalid_auto_confirm_type(
    client: FlaskClient, auto_confirm_value: object
):
    """PUT /conversations/<id> must reject non-bool/non-int auto_confirm with 400.

    CWE-20: Python truthy coercion causes string "false" to be treated as True,
    enabling unlimited auto-confirm tool execution.  We must reject non-bool/int values.
    """
    import random

    convname = f"test-auto-confirm-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"auto_confirm": auto_confirm_value},
    )
    assert response.status_code == 400, (
        f"Expected 400 for auto_confirm={auto_confirm_value!r}, got {response.status_code}"
    )
    data = response.get_json()
    assert data is not None
    assert "auto_confirm" in data.get("error", ""), (
        f"Expected error mentioning 'auto_confirm', got: {data}"
    )


@pytest.mark.parametrize(
    "auto_confirm_value",
    [
        True,  # bool True
        False,  # bool False
        0,  # int 0 (no auto-confirm)
        5,  # int count
    ],
)
def test_v2_create_conversation_accepts_valid_auto_confirm(
    client: FlaskClient, auto_confirm_value: object
):
    """PUT /conversations/<id> must accept bool and int auto_confirm values."""
    import random

    convname = f"test-auto-confirm-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"auto_confirm": auto_confirm_value},
    )
    assert response.status_code == 200, (
        f"Expected 200 for auto_confirm={auto_confirm_value!r}, got {response.status_code}: {response.get_json()}"
    )


@pytest.mark.timeout(15)
def test_v2_step_error_returned_in_response(v2_conv, client: FlaskClient):
    """Test that LLM errors during step are returned in the HTTP response.

    Regression test for https://github.com/gptme/gptme-cloud/issues/172
    Previously, the step endpoint always returned 200 even when the LLM call
    failed — errors were only visible via SSE events.
    """
    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "Hello",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 200

    # Mock _stream to raise an error (simulating an API auth failure)
    def mock_stream_error(messages, model, tools=None, max_tokens=None):
        raise RuntimeError("API key is invalid")

    with unittest.mock.patch("gptme.server.session_step._stream", mock_stream_error):
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/step",
            json={
                "session_id": session_id,
                "model": "openai/mock-model",
            },
        )

    # Should return 500 with error message, not 200 with "ok"
    assert response.status_code == 500, (
        f"Expected 500 for LLM error, got {response.status_code}: {response.get_json()}"
    )
    data = response.get_json()
    assert data is not None
    assert data["status"] == "error"
    assert "API key is invalid" in data["error"]


@pytest.mark.timeout(15)
def test_v2_step_last_error_set_on_failure(v2_conv, client: FlaskClient):
    """Test that session.last_error is set when a step fails."""
    from gptme.server.session_models import SessionManager

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]

    # Add a user message
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "Hello",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 200

    def mock_stream_error(messages, model, tools=None, max_tokens=None):
        raise ValueError("Model not found: fake-model")

    with unittest.mock.patch("gptme.server.session_step._stream", mock_stream_error):
        response = client.post(
            f"/api/v2/conversations/{conversation_id}/step",
            json={
                "session_id": session_id,
                "model": "openai/fake-model",
            },
        )

    assert response.status_code == 500

    # Give the background thread time to finish cleanup (set generating=False).
    # The error event is sent before generating is cleared, so poll with a
    # deadline instead of a fixed sleep to avoid timing-sensitive flakes.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        session = SessionManager.get_session(session_id)
        if session and not session.generating:
            break
        time.sleep(0.05)

    # Verify session.last_error is set
    session = SessionManager.get_session(session_id)
    assert session is not None
    assert session.last_error is not None
    assert "Model not found" in session.last_error
    # Session should not be stuck in generating state
    assert not session.generating
