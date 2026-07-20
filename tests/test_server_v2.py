import io
import json
import random
import time
import unittest.mock
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
import tomlkit

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
        config_dict = config.to_dict()
        # Strip workspace from the serialized config: the server security check
        # rejects external workspace paths (path traversal fix). Tests that pass
        # a config only care about model/tools/system_prompt, not the workspace;
        # the server will apply the safe @log default.
        config_dict.get("chat", {}).pop("workspace", None)
        json["config"] = config_dict

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
    assert "provider_configured" in data


def test_start_tool_execution_streams_only_real_tool_output(
    v2_conv, client: FlaskClient, monkeypatch
):
    from gptme.message import Message
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools import ToolUse

    conversation_id = v2_conv["conversation_id"]
    session_id = v2_conv["session_id"]
    session = SessionManager.get_session(session_id)
    assert session is not None

    tool_id = "tool-1"
    session.pending_tools[tool_id] = ToolExecution(
        tool_id=tool_id,
        tooluse=ToolUse("shell", [], "echo hello", call_id="call-1"),
    )

    def fake_execute(self, log=None, workspace=None, on_result_message=None):
        pre_hook = Message("system", "pre hook chatter")
        actual_output = Message("system", "real tool output")
        post_hook = Message("system", "post hook chatter")
        yield pre_hook
        if on_result_message:
            on_result_message(actual_output)
        yield actual_output
        yield post_hook

    monkeypatch.setattr("gptme.tools.base.ToolUse.execute", fake_execute)
    monkeypatch.setattr(
        "gptme.server.session_step.prepare_execution_environment",
        lambda workspace, tools, chat_config: None,
    )
    monkeypatch.setattr(
        "gptme.server.session_step._start_step_thread",
        lambda *args, **kwargs: None,
    )

    thread = start_tool_execution(
        conversation_id=conversation_id,
        session=session,
        tool_id=tool_id,
        edited_tooluse=None,
        model="openai/mock-model",
        chat_config=ChatConfig(),
    )
    thread.join(timeout=5)
    assert not thread.is_alive()

    tool_output_events = [e for e in session.events if e["type"] == "tool_output"]
    assert tool_output_events == [
        {
            "type": "tool_output",
            "tool_id": tool_id,
            "output": "real tool output",
        }
    ]

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    assert response.status_code == 200
    messages = response.get_json()["log"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert "pre hook chatter" in system_messages
    assert "real tool output" in system_messages
    assert "post hook chatter" in system_messages


def test_v2_api_root_provider_configured(client: FlaskClient, monkeypatch):
    """provider_configured reflects whether get_default_model() returns a model."""
    from gptme.llm.models.types import ModelMeta

    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: None
    )

    # No model configured → provider_configured should be False
    monkeypatch.setattr("gptme.server.api_v2.get_default_model", lambda: None)
    response = client.get("/api/v2")
    data = response.get_json()
    assert data["provider_configured"] is False

    # Model configured → provider_configured should be True
    fake_model = ModelMeta(
        model="test-model",
        provider="anthropic",
        context=10000,
        max_output=1000,
    )
    monkeypatch.setattr("gptme.server.api_v2.get_default_model", lambda: fake_model)
    response = client.get("/api/v2")
    data = response.get_json()
    assert data["provider_configured"] is True


def test_webui_deploy_status_disabled(client: FlaskClient, monkeypatch):
    """The web UI deploy endpoint reports disabled state by default."""
    monkeypatch.delenv("GPTME_WEBUI_ENABLE_DEV_DEPLOY", raising=False)
    monkeypatch.delenv("GPTME_WEBUI_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GPTME_WEBUI_DEPLOY_WORKFLOW", raising=False)

    response = client.get("/api/v2/dev/deploy-staging")

    assert response.status_code == 200
    data = response.get_json()
    assert data["enabled"] is False
    assert data["configured"] is False
    assert data["repository"] == "gptme/gptme"
    assert data["workflow"] == ""


def test_webui_deploy_trigger_disabled(client: FlaskClient, monkeypatch):
    """The deploy trigger fails closed until explicitly enabled."""
    monkeypatch.delenv("GPTME_WEBUI_ENABLE_DEV_DEPLOY", raising=False)

    response = client.post("/api/v2/dev/deploy-staging", json={})

    assert response.status_code == 403
    data = response.get_json()
    assert "disabled" in data["error"]


def test_webui_deploy_trigger_requires_workflow(client: FlaskClient, monkeypatch):
    """Enabled deploy trigger still requires an explicit workflow name."""
    monkeypatch.setenv("GPTME_WEBUI_ENABLE_DEV_DEPLOY", "true")
    monkeypatch.setenv("GPTME_WEBUI_GITHUB_TOKEN", "test-token")
    monkeypatch.delenv("GPTME_WEBUI_DEPLOY_WORKFLOW", raising=False)

    response = client.post("/api/v2/dev/deploy-staging", json={})

    assert response.status_code == 503
    data = response.get_json()
    assert "WORKFLOW" in data["error"]


def test_webui_deploy_trigger_dispatches_workflow(client: FlaskClient, monkeypatch):
    """The deploy trigger posts workflow_dispatch to GitHub when configured."""
    captured = {}

    class FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["authorization"] = request.headers["Authorization"]
        return FakeResponse()

    monkeypatch.setenv("GPTME_WEBUI_ENABLE_DEV_DEPLOY", "true")
    monkeypatch.setenv("GPTME_WEBUI_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GPTME_WEBUI_DEPLOY_REPOSITORY", "gptme/web ui#preview")
    monkeypatch.setenv("GPTME_WEBUI_DEPLOY_WORKFLOW", "webui-staging.yml")
    monkeypatch.setenv("GPTME_WEBUI_DEPLOY_REF", "master")
    monkeypatch.setenv("GPTME_WEBUI_DEPLOY_INPUTS_JSON", '{"environment":"staging"}')
    monkeypatch.setattr("gptme.server.api_v2.urllib.request.urlopen", fake_urlopen)

    response = client.post("/api/v2/dev/deploy-staging", json={})

    assert response.status_code == 202
    data = response.get_json()
    assert data["status"] == "queued"
    assert data["workflow"] == "webui-staging.yml"
    assert captured == {
        "url": "https://api.github.com/repos/gptme/web%20ui%23preview/actions/workflows/webui-staging.yml/dispatches",
        "timeout": 20,
        "payload": {"ref": "master", "inputs": {"environment": "staging"}},
        "authorization": "Bearer test-token",
    }


def test_v2_user_api_key_persists_env_entry(client: FlaskClient, tmp_path, monkeypatch):
    """Saving an API key should write the provider env var into user config."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post(
        "/api/v2/user/api-key",
        json={"provider": "anthropic", "api_key": "  sk-ant-test-key  "},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data == {
        "status": "ok",
        "provider": "anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "restart_required": False,
    }

    local_file = tmp_path / "config.local.toml"
    saved = tomlkit.loads(local_file.read_text()).unwrap()
    assert saved["env"]["ANTHROPIC_API_KEY"] == "sk-ant-test-key"


def test_v2_user_api_key_applies_to_env_immediately(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Saving an API key should apply it to os.environ immediately (no restart needed)."""
    import os

    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post(
        "/api/v2/user/api-key",
        json={"provider": "anthropic", "api_key": "sk-ant-live-key"},
    )

    assert response.status_code == 200
    assert response.get_json()["restart_required"] is False
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-live-key"


def test_v2_user_api_key_persists_default_model(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Saving an API key may also persist the selected default model."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MODEL", raising=False)

    response = client.post(
        "/api/v2/user/api-key",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-test-key",
            "model": "anthropic/claude-sonnet-4-7",
        },
    )

    assert response.status_code == 200
    local_file = tmp_path / "config.local.toml"
    saved = tomlkit.loads(local_file.read_text()).unwrap()
    assert saved["env"]["ANTHROPIC_API_KEY"] == "sk-ant-test-key"
    assert saved["env"]["MODEL"] == "anthropic/claude-sonnet-4-7"


def test_v2_user_api_key_rejects_model_provider_mismatch(client: FlaskClient):
    response = client.post(
        "/api/v2/user/api-key",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-test-key",
            "model": "openai/gpt-4.1",
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data == {"error": "Model openai/gpt-4.1 does not match provider anthropic"}


def test_v2_user_api_key_rejects_unknown_provider(client: FlaskClient):
    response = client.post(
        "/api/v2/user/api-key",
        json={"provider": "bogus", "api_key": "sk-test"},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data == {"error": "Unknown provider: bogus"}


def test_v2_user_default_model_persists_and_applies(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Saving a default model should write [models].default and update runtime state."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    applied: dict[str, ModelMeta] = {}
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    monkeypatch.setattr("gptme.config.core.reload_config", lambda: None)
    monkeypatch.setattr("gptme.llm.init_llm", lambda provider: None)
    monkeypatch.setattr(
        "gptme.server.api_v2.set_default_model",
        lambda model: applied.setdefault("model", model),
    )

    response = client.post(
        "/api/v2/user/default-model",
        json={"model": "anthropic/claude-sonnet-4-7"},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data == {
        "status": "ok",
        "model": "anthropic/claude-sonnet-4-7",
        "restart_required": False,
    }

    saved = tomlkit.loads(config_file.read_text()).unwrap()
    assert saved["models"]["default"] == "anthropic/claude-sonnet-4-7"
    assert applied["model"].full == "anthropic/claude-sonnet-4-7"


def test_v2_user_default_model_rejects_unqualified_model(client: FlaskClient):
    response = client.post(
        "/api/v2/user/default-model",
        json={"model": "anthropic"},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data == {"error": "model must be fully qualified as provider/model"}


def test_v2_user_config_file_get_reads_raw_toml(
    client: FlaskClient, tmp_path, monkeypatch
):
    """GET /api/v2/user/config-file should return the raw main config.toml text."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    config_file.write_text('[env]\nMODEL = "anthropic/claude-sonnet-4-7"\n')
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    response = client.get("/api/v2/user/config-file")

    assert response.status_code == 200
    data = response.get_json()
    assert data["content"] == '[env]\nMODEL = "anthropic/claude-sonnet-4-7"\n'
    assert data["path"] == str(config_file)
    assert data["write_target"] == str(config_file)
    assert data["local_config_exists"] is False


def test_v2_user_config_file_get_creates_missing_config(
    client: FlaskClient, tmp_path, monkeypatch
):
    """GET should create the default config.toml when the file is missing."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    response = client.get("/api/v2/user/config-file")

    assert response.status_code == 200
    assert config_file.exists()
    assert response.get_json()["content"] == config_file.read_text()


def test_v2_user_config_file_put_validates_and_writes_toml(
    client: FlaskClient, tmp_path, monkeypatch
):
    """PUT /api/v2/user/config-file should reject bad TOML and write valid TOML."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    reload_calls: list[str] = []
    config_file.write_text('[env]\nMODEL = "old/model"\n')
    monkeypatch.setattr(user_mod, "config_path", str(config_file))
    monkeypatch.setattr(
        "gptme.config.core.reload_config", lambda: reload_calls.append("reload")
    )

    invalid_response = client.put(
        "/api/v2/user/config-file",
        json={"content": "[env\nMODEL = broken"},
    )
    assert invalid_response.status_code == 400
    assert "Invalid TOML" in invalid_response.get_json()["error"]
    assert config_file.read_text() == '[env]\nMODEL = "old/model"\n'

    valid_content = '[env]\nMODEL = "anthropic/claude-sonnet-4-7"\n'
    response = client.put(
        "/api/v2/user/config-file",
        json={"content": valid_content},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["content"] == valid_content
    assert config_file.read_text() == valid_content
    assert reload_calls == ["reload"]


def test_v2_user_config_file_patch_updates_dotted_key(
    client: FlaskClient, tmp_path, monkeypatch
):
    """PATCH /api/v2/user/config-file should persist one dotted key."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    response = client.patch(
        "/api/v2/user/config-file",
        json={
            "key": "env.MODEL",
            "value": "anthropic/claude-sonnet-4-7",
            "reload": False,
        },
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["key"] == "env.MODEL"
    saved = tomlkit.loads(config_file.read_text()).unwrap()
    assert saved["env"]["MODEL"] == "anthropic/claude-sonnet-4-7"


def test_v2_user_config_file_patch_preserves_boolean_value(
    client: FlaskClient, tmp_path, monkeypatch
):
    """PATCH /api/v2/user/config-file should preserve non-string JSON scalars."""
    import gptme.config.user as user_mod

    config_file = tmp_path / "config.toml"
    monkeypatch.setattr(user_mod, "config_path", str(config_file))

    response = client.patch(
        "/api/v2/user/config-file",
        json={
            "key": "lessons.enabled",
            "value": True,
            "reload": False,
        },
    )

    assert response.status_code == 200
    saved = tomlkit.loads(config_file.read_text()).unwrap()
    assert saved["lessons"]["enabled"] is True


def test_v2_user_config_file_patch_rejects_invalid_key(client: FlaskClient):
    response = client.patch(
        "/api/v2/user/config-file",
        json={"key": "env..MODEL", "value": "anthropic/claude-sonnet-4-7"},
    )

    assert response.status_code == 400
    assert "dotted path" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Unit tests for _redact_secrets / _restore_redacted_secrets
# ---------------------------------------------------------------------------


def test_redact_secrets_double_quoted():
    from gptme.server.api_v2 import _redact_secrets

    content = '[openai]\nOPENAI_API_KEY = "sk-abc123"\n'
    redacted = _redact_secrets(content)
    assert "sk-abc123" not in redacted
    assert '***"' in redacted or '"***"' in redacted


def test_redact_secrets_single_quoted():
    """Secrets in single-quoted TOML strings must also be redacted."""
    from gptme.server.api_v2 import _redact_secrets

    content = "[openai]\nOPENAI_API_KEY = 'sk-abc123'\n"
    redacted = _redact_secrets(content)
    assert "sk-abc123" not in redacted


def test_redact_secrets_preserves_non_secret_keys():
    from gptme.server.api_v2 import _redact_secrets

    content = '[env]\nMODEL = "anthropic/claude-3"\n'
    assert _redact_secrets(content) == content


def test_restore_redacted_secrets_section_scoped():
    """Keys with the same bare name in different sections restore independently."""
    from gptme.server.api_v2 import _redact_secrets, _restore_redacted_secrets

    original = (
        '[openai]\napi_key = "key-openai"\n[anthropic]\napi_key = "key-anthropic"\n'
    )
    redacted = _redact_secrets(original)
    restored = _restore_redacted_secrets(redacted, original)
    assert restored == original


def test_restore_redacted_secrets_single_quoted():
    """Single-quoted secrets survive a redact → restore round-trip."""
    from gptme.server.api_v2 import _redact_secrets, _restore_redacted_secrets

    original = "[openai]\napi_key = 'sk-real'\n"
    redacted = _redact_secrets(original)
    assert "sk-real" not in redacted
    restored = _restore_redacted_secrets(redacted, original)
    assert restored == original


def test_restore_redacted_secrets_repeated_array_of_tables():
    """Repeated [[section]] tables each restore their own secret, not the last one."""
    from gptme.server.api_v2 import _redact_secrets, _restore_redacted_secrets

    original = (
        "[[providers]]\n"
        'name = "openai"\n'
        'api_key = "key-openai"\n'
        "\n"
        "[[providers]]\n"
        'name = "anthropic"\n'
        'api_key = "key-anthropic"\n'
    )
    redacted = _redact_secrets(original)
    assert "key-openai" not in redacted
    assert "key-anthropic" not in redacted
    restored = _restore_redacted_secrets(redacted, original)
    assert restored == original


def test_redact_secrets_quoted_key_name():
    """Secrets under quoted TOML key names must also be redacted."""
    from gptme.server.api_v2 import _redact_secrets, _restore_redacted_secrets

    # Double-quoted key with double-quoted value
    original_dq = '[env]\n"OPENAI_API_KEY" = "sk-real"\n'
    redacted_dq = _redact_secrets(original_dq)
    assert "sk-real" not in redacted_dq, "double-quoted key: value should be redacted"
    restored_dq = _restore_redacted_secrets(redacted_dq, original_dq)
    assert restored_dq == original_dq, (
        "double-quoted key: round-trip should restore value"
    )

    # Single-quoted key with double-quoted value
    original_sq = "[env]\n'ANTHROPIC_API_KEY' = \"sk-real2\"\n"
    redacted_sq = _redact_secrets(original_sq)
    assert "sk-real2" not in redacted_sq, "single-quoted key: value should be redacted"
    restored_sq = _restore_redacted_secrets(redacted_sq, original_sq)
    assert restored_sq == original_sq, (
        "single-quoted key: round-trip should restore value"
    )


@pytest.mark.parametrize(
    "endpoint", ["/api/v2/user/api-key", "/api/v2/user/default-model"]
)
@pytest.mark.parametrize("body", [[], [1, 2, 3], "string", 42])
def test_v2_user_endpoints_reject_non_object_json(
    client: FlaskClient, endpoint: str, body: object
):
    """User-setting endpoints should reject non-object JSON bodies with 400."""
    response = client.post(endpoint, json=body)

    assert response.status_code == 400
    assert "object" in response.get_json()["error"].lower()


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


# ---------------------------------------------------------------------------
# steer_inject endpoint tests
# ---------------------------------------------------------------------------


class _FakeSteerableSessionProvider(_FakeExternalSessionProvider):
    """Provider variant that supports steer_inject on session abc123."""

    def __init__(self) -> None:
        self.steered_messages: list[str] = []
        self._steer_ok = True

    def get_session(self, external_id: str, days: int = 30) -> dict[str, Any] | None:
        if external_id != "abc123":
            return None
        return {
            "id": "abc123",
            "transcript": {
                "session_id": "session-1",
                "harness": "claude-code",
                "capabilities": ["steer_inject"],
                "trajectory_path": "/tmp/session.jsonl",
                "messages": [{"role": "user", "content": "hi"}],
            },
        }

    def steer_session(self, trajectory_path: str, message: str) -> bool:
        if not self._steer_ok:
            return False
        self.steered_messages.append(message)
        return True


@pytest.fixture
def fake_steerable_provider(monkeypatch):
    provider = _FakeSteerableSessionProvider()
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: provider
    )
    return provider


def test_v2_steer_external_session_ok(client: FlaskClient, fake_steerable_provider):
    """Steer endpoint returns 200 and records the injected message."""
    response = client.post(
        "/api/v2/external-sessions/abc123/steer",
        json={"message": "hello from user"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert fake_steerable_provider.steered_messages == ["hello from user"]


def test_v2_steer_external_session_missing_message(
    client: FlaskClient, fake_steerable_provider
):
    """Steer endpoint returns 400 when message is missing or blank."""
    response = client.post("/api/v2/external-sessions/abc123/steer", json={})
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "message" in data["error"]

    response2 = client.post(
        "/api/v2/external-sessions/abc123/steer", json={"message": "   "}
    )
    assert response2.status_code == 400


def test_v2_steer_external_session_not_found(
    client: FlaskClient, fake_steerable_provider
):
    """Steer endpoint returns 404 when the session doesn't exist."""
    response = client.post(
        "/api/v2/external-sessions/does-not-exist/steer",
        json={"message": "hi"},
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "not found" in data["error"].lower()


def test_v2_steer_external_session_no_capability(
    client: FlaskClient, fake_external_session_provider
):
    """Steer endpoint returns 422 when session lacks steer_inject capability."""
    # fake_external_session_provider returns a session with only ["view_transcript"]
    response = client.post(
        "/api/v2/external-sessions/abc123/steer",
        json={"message": "hi"},
    )
    assert response.status_code == 422
    data = response.get_json()
    assert data is not None
    assert "steer_inject" in data["error"]


def test_v2_steer_external_session_unavailable(client: FlaskClient, monkeypatch):
    """Steer endpoint returns 503 when provider is not configured."""
    monkeypatch.setattr(
        "gptme.server.api_v2.get_external_session_provider", lambda: None
    )
    response = client.post(
        "/api/v2/external-sessions/abc123/steer",
        json={"message": "hi"},
    )
    assert response.status_code == 503


def test_v2_steer_external_session_not_implemented(
    client: FlaskClient, fake_steerable_provider
):
    """Steer endpoint returns 501 when provider.steer_session returns False."""
    fake_steerable_provider._steer_ok = False
    response = client.post(
        "/api/v2/external-sessions/abc123/steer",
        json={"message": "hi"},
    )
    assert response.status_code == 501
    data = response.get_json()
    assert data is not None
    assert "501" in str(response.status_code)


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

    provider._read_transcript = lambda path: _Transcript()

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

    provider._read_transcript = _read_transcript

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

    provider._read_transcript = _read_transcript

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

    provider._read_transcript = lambda path: _Transcript()
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
    provider._discover_gptme_sessions = lambda start, end: [session_dir]
    provider._discover_cc_sessions = lambda start, end: []
    provider._discover_codex_sessions = lambda start, end: []
    provider._discover_copilot_sessions = lambda start, end: []

    from gptme_sessions.transcript import (
        read_transcript,
    )

    provider._read_transcript = read_transcript

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
    provider._discover_gptme_sessions = lambda start, end: [session_dir]
    provider._discover_cc_sessions = lambda start, end: []
    provider._discover_codex_sessions = lambda start, end: []
    provider._discover_copilot_sessions = lambda start, end: []

    from gptme_sessions.transcript import (
        read_transcript,
    )

    provider._read_transcript = read_transcript

    # Should return 0 items — the directory is skipped early, not propagated as an error
    items = provider.list_sessions(limit=10, days=7)
    assert items == [], f"Expected no sessions, got: {items}"


def test_v2_server_health_empty(client: FlaskClient, monkeypatch):
    """Server health endpoint returns green status with no active sessions."""
    monkeypatch.setattr(
        "gptme.server.api_v2.SessionManager.get_all_sessions",
        lambda: [],
    )

    response = client.get("/api/v2/server/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["session_count"] == 0
    assert data["generating_count"] == 0
    assert data["idle_count"] == 0
    assert data["health"] == "green"
    assert data["slots"] == []


def test_v2_server_health_with_session(v2_conv, client: FlaskClient, monkeypatch):
    """Server health endpoint reports idle session after creation."""
    from gptme.server.session_models import SessionManager

    session_id = v2_conv["session_id"]
    session = SessionManager.get_session(session_id)
    assert session is not None

    monkeypatch.setattr(
        "gptme.server.api_v2.SessionManager.get_all_sessions",
        lambda: [(session_id, session)],
    )

    response = client.get("/api/v2/server/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["session_count"] == 1
    assert data["generating_count"] == 0
    assert data["idle_count"] == 1
    assert data["health"] == "green"
    assert len(data["slots"]) == 1
    slot = data["slots"][0]
    assert not slot["generating"]
    assert slot["elapsed_seconds"] is None


def test_v2_server_health_yellow(client: FlaskClient, monkeypatch):
    """Server health returns yellow when some sessions are generating."""
    from gptme.server.session_models import ConversationSession

    now = datetime.now(tz=timezone.utc)
    idle_session = ConversationSession(id="idle-1234", conversation_id="conv-1")
    gen_session = ConversationSession(
        id="gen-5678",
        conversation_id="conv-2",
        generating=True,
        generating_since=now - timedelta(seconds=30),
    )
    monkeypatch.setattr(
        "gptme.server.api_v2.SessionManager.get_all_sessions",
        lambda: [("idle-1234", idle_session), ("gen-5678", gen_session)],
    )

    response = client.get("/api/v2/server/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["session_count"] == 2
    assert data["generating_count"] == 1
    assert data["idle_count"] == 1
    assert data["health"] == "yellow"
    assert len(data["slots"]) == 2
    # Verify generating slot has elapsed time
    gen_slot = next(s for s in data["slots"] if s["generating"])
    assert gen_slot["elapsed_seconds"] is not None
    assert gen_slot["elapsed_seconds"] >= 0


def test_v2_server_health_red(client: FlaskClient, monkeypatch):
    """Server health returns red when all sessions are generating."""
    from gptme.server.session_models import ConversationSession

    now = datetime.now(tz=timezone.utc)
    sessions = [
        ConversationSession(
            id=f"gen-{i:04d}",
            conversation_id=f"conv-{i}",
            generating=True,
            generating_since=now - timedelta(seconds=10 + i),
        )
        for i in range(3)
    ]
    monkeypatch.setattr(
        "gptme.server.api_v2.SessionManager.get_all_sessions",
        lambda: [(s.id, s) for s in sessions],
    )

    response = client.get("/api/v2/server/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["session_count"] == 3
    assert data["generating_count"] == 3
    assert data["idle_count"] == 0
    assert data["health"] == "red"
    assert len(data["slots"]) == 3
    assert all(s["generating"] for s in data["slots"])


def test_v2_conversations_list(client: FlaskClient):
    """Test listing V2 conversations."""
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_v2_conversations_list_exposes_message_count_and_last_updated(
    client: FlaskClient,
):
    """Fast-mode (default) list response must include ``message_count`` and
    ``last_updated`` aliases for the webui stats badge. Both come from the
    cheap tail-only scan and are stable aliases for the legacy ``messages``
    and ``modified`` fields.
    """
    # Create a conversation with a non-test prefix so it isn't filtered out
    # by the user-facing list endpoint (which skips ``test-`` and ``tmp`` prefixes).
    convname = f"msglist-shape-{random.randint(0, 1000000)}"
    put_response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are an AI assistant for testing."},
    )
    assert put_response.status_code == 200

    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    matching = [c for c in data if c["id"] == convname]
    assert matching, f"created conversation {convname} not in list"
    for item in matching:
        assert "message_count" in item
        assert "last_updated" in item
        assert isinstance(item["message_count"], int)
        assert isinstance(item["last_updated"], int | float)
        # Stable aliases mirror the legacy fields.
        assert item["message_count"] == item["messages"]
        assert item["last_updated"] == item["modified"]


def test_v2_conversations_list_keeps_messages_in_fast_mode(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Regression: ``messages`` (the count) must remain in the fast-mode
    response. A previous bug stripped it via ``item.pop("messages", None)``,
    which broke webui stats that read either ``messages`` or the new
    ``message_count`` alias.
    """
    # Create a real conversation with a non-test prefix so it isn't filtered
    # out by ``_is_test_conversation_id`` (``test-`` and ``tmp`` prefixes are
    # skipped by the user-facing list endpoint).
    convname = f"msglist-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are an AI assistant for testing."},
    )
    assert response.status_code == 200

    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    matching = [c for c in data if c["id"] == convname]
    assert matching, f"created conversation {convname} not in list"
    item = matching[0]
    assert "messages" in item, "fast-mode response must keep `messages` (count)"
    assert "message_count" in item, (
        "fast-mode response must include `message_count` alias"
    )
    assert "last_updated" in item, (
        "fast-mode response must include `last_updated` alias"
    )
    assert "modified" in item, "fast-mode response must keep `modified`"
    assert item["messages"] == item["message_count"]
    assert item["modified"] == item["last_updated"]
    assert item["message_count"] >= 1  # at least the system prompt


@pytest.mark.skip(
    reason="xdist worker crash in remote.py line 206 (pre-existing, not a regression; tracked in #2631)"
)
def test_v2_conversations_list_keeps_messages_on_cache_hit(client: FlaskClient):
    """Regression: cached fast-mode responses must preserve ``messages``."""
    convname = f"msglist-cache-hit-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={"prompt": "You are an AI assistant for testing."},
    )
    assert response.status_code == 200

    response = client.get("/api/v2/conversations")
    assert response.status_code == 200

    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    matching = [c for c in data if c["id"] == convname]
    assert matching, f"created conversation {convname} not in cached list"
    item = matching[0]
    assert "messages" in item, "cached fast-mode response must keep `messages`"
    assert "message_count" in item, (
        "cached fast-mode response must include `message_count` alias"
    )
    assert "last_updated" in item, (
        "cached fast-mode response must include `last_updated` alias"
    )
    assert "modified" in item, "cached fast-mode response must keep `modified`"
    assert item["messages"] == item["message_count"]
    assert item["modified"] == item["last_updated"]


def test_v2_conversation_get(v2_conv, client: FlaskClient):
    """Test getting a V2 conversation."""
    conversation_id = v2_conv["conversation_id"]
    response = client.get(f"/api/v2/conversations/{conversation_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data
    assert data["logdir"] == str(Path(data["logfile"]).parent)

    # Should contain system messages (custom system prompt + possibly workspace prompt)
    assert len(data["log"]) >= 1  # At least custom system prompt
    assert data["log"][0]["role"] == "system"
    assert "testing" in data["log"][0]["content"]


def test_v2_conversation_get_returns_404_for_missing_conversation(
    client: FlaskClient,
):
    """Missing conversations should return a structured 404 instead of a 500."""
    conversation_id = f"missing-conversation-{random.randint(0, 1000000)}"

    response = client.get(f"/api/v2/conversations/{conversation_id}")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": f"Conversation not found: {conversation_id}"
    }


def test_v2_create_conversation_default_system_prompt(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test creating a V2 conversation with a default system prompt."""
    # Explicitly set tmp_path as workspace to avoid workspace context message.
    # New conversations now default to an isolated logdir/workspace directory,
    # so this test must not rely on cwd fallback behavior.
    # Explicitly disable chat history for this test
    monkeypatch.setenv("GPTME_CHAT_HISTORY", "false")
    # Pin env var so test is deterministic regardless of caller's environment
    monkeypatch.setenv("GPTME_SERVE_HTML_HINT", "true")

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
    # Use the default @log workspace (workspace containment requires workspace
    # to be inside the conversation logdir; external paths like tmp_path are
    # rejected since the security fix for path traversal via config).
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello, this is a test message.",
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                }
            ],
        },
    )
    assert response.status_code == 200
    conversation_id = response.get_json()["conversation_id"]

    # Fetch the config to learn the actual resolved workspace path (@log -> logdir/workspace)
    config_resp = client.get(f"/api/v2/conversations/{conversation_id}/config")
    assert config_resp.status_code == 200
    from gptme.config.chat import ChatConfig  # fmt: skip

    conv_config = ChatConfig.from_dict(config_resp.get_json())
    actual_workspace = conv_config.workspace

    response = client.get(f"/api/v2/conversations/{conversation_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data
    assert (
        len(data["log"]) == 3
    )  # System prompt + webui hint + user message (no workspace context)
    assert data["log"][0]["role"] == "system"  # Primary system prompt
    assert data["log"][1]["role"] == "system"  # Webui hint
    assert data["log"][2]["role"] == "user"
    assert data["log"][2]["content"] == "Hello, this is a test message."

    # Check that the system prompt is the default one
    prompt_msgs = get_prompt(
        tools=list(get_toolchain(None)),
        interactive=True,
        tool_format="markdown",
        model=None,
        prompt="full",
        workspace=actual_workspace,
    )
    assert data["log"][0]["content"] == prompt_msgs[0].content


def test_v2_create_conversation_webui_html_hint(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test that V2 conversations get the webui HTML output-format hint."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_CHAT_HISTORY", "false")
    monkeypatch.setenv("GPTME_SERVE_HTML_HINT", "true")

    from gptme.config import Config, set_config
    from gptme.config.user import default_config

    set_config(Config(user=default_config))
    monkeypatch.setattr(
        "gptme.prompts.workspace.config_path",
        str(tmp_path / "config.toml"),
    )

    convname = f"test-server-v2-webui-hint-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello from webui test",
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
    system_msgs = [m for m in data["log"] if m["role"] == "system"]

    # Check that the HTML rendering hint is in the system messages
    hint_found = any(
        "Output format:" in m["content"]
        and "web interface" in m["content"]
        and "```html" in m["content"]
        for m in system_msgs
    )
    assert hint_found, (
        f"WebUI HTML output hint not found in system messages. "
        f"System contents: {[m['content'][:80] for m in system_msgs]}"
    )


def test_v2_create_conversation_webui_html_hint_disabled(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test that GPTME_SERVE_HTML_HINT=false suppresses the webui HTML hint."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_CHAT_HISTORY", "false")
    monkeypatch.setenv("GPTME_SERVE_HTML_HINT", "false")

    from gptme.config import Config, set_config
    from gptme.config.user import default_config

    set_config(Config(user=default_config))
    monkeypatch.setattr(
        "gptme.prompts.workspace.config_path",
        str(tmp_path / "config.toml"),
    )

    convname = f"test-server-v2-no-hint-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Hello from non-webui client",
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
    system_msgs = [m for m in data["log"] if m["role"] == "system"]

    hint_found = any(
        "Output format:" in m["content"] and "web interface" in m["content"]
        for m in system_msgs
    )
    assert not hint_found, (
        "WebUI HTML hint should be absent when GPTME_SERVE_HTML_HINT=false. "
        f"System contents: {[m['content'][:80] for m in system_msgs]}"
    )


def test_v2_create_conversation_rejects_non_object_config_without_side_effects(
    client: FlaskClient, tmp_path, monkeypatch
):
    logs_dir = tmp_path / "logs"
    conversation_id = "test-server-v2-bad-config"
    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: logs_dir)

    response = client.put(
        f"/api/v2/conversations/{conversation_id}",
        json={"config": []},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "'config' must be an object"}
    assert not (logs_dir / conversation_id).exists()


def test_v2_create_conversation_accepts_log_workspace(
    client: FlaskClient, tmp_path, monkeypatch
):
    logs_dir = tmp_path / "logs"
    conversation_id = "test-server-v2-log-workspace"
    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: logs_dir)

    response = client.put(
        f"/api/v2/conversations/{conversation_id}",
        json={"prompt": "none", "config": {"chat": {"workspace": "@log"}}},
    )

    assert response.status_code == 200
    assert (logs_dir / conversation_id / "workspace").is_dir()


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


def test_v2_conversation_post_nonexistent_branch_returns_404(
    v2_conv, client: FlaskClient
):
    """POST to an existing conversation with a non-existent branch returns 404 with
    a 'Branch not found' error — not the misleading 'Conversation not found' message
    that would fire when the conversation itself is absent.
    """
    conversation_id = v2_conv["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "hello", "branch": "no-such-branch"},
    )

    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    # Must say "Branch not found", not "Conversation not found"
    assert "branch" in data["error"].lower()
    assert "no-such-branch" in data["error"]


@pytest.mark.parametrize("cmd", ["exit", "restart", "edit", "delete"])
def test_v2_conversation_post_blocks_unsafe_commands(
    v2_conv, client: FlaskClient, cmd: str
):
    """Commands that would crash or block the server are rejected with a clean 400.

    /exit and /restart terminate/restart the server process; /edit launches an
    interactive $EDITOR subprocess on the server host; /delete without --force
    calls input() waiting for stdin that never arrives in server mode.
    None should be dispatched to handle_cmd in server mode.
    """
    conversation_id = v2_conv["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": f"/{cmd}"},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "not available in server mode" in data["error"]


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


def _normalize_config_for_comparison(config_dict: dict) -> dict:
    """Normalize config dict for comparison by removing server-managed fields."""
    result = config_dict.copy()
    # workspace is now managed per-conversation by the server
    if "chat" in result and "workspace" in result["chat"]:
        del result["chat"]["workspace"]
    return result


def test_v2_conversation_put_injects_system_prompt(client: FlaskClient):
    """Creating a conversation with system_prompt should inject it via api_conversation_put."""
    config = ChatConfig(system_prompt="Answer in bullet points.")
    conversation = create_conversation(client, config=config)
    conversation_id = conversation["conversation_id"]

    data = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    system_messages = [m["content"] for m in data["log"] if m["role"] == "system"]
    assert "Answer in bullet points." in system_messages


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
    assert _normalize_config_for_comparison(
        config.to_dict()
    ) == _normalize_config_for_comparison(input_config.to_dict())


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
    assert _normalize_config_for_comparison(
        config_1.to_dict()
    ) == _normalize_config_for_comparison(input_config_1.to_dict())

    response_2 = client.get(f"/api/v2/conversations/{conversation_id_2}")
    data_2 = response_2.get_json()
    config_2 = ChatConfig.from_logdir(Path(data_2["logfile"]).parent)
    assert _normalize_config_for_comparison(
        config_2.to_dict()
    ) == _normalize_config_for_comparison(input_config_2.to_dict())


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
    assert _normalize_config_for_comparison(
        config.to_dict()
    ) == _normalize_config_for_comparison(input_config.to_dict())


def test_v2_chat_config_update_works(client: FlaskClient):
    """Test that the chat config update endpoint works."""
    input_config = ChatConfig(model="openai/gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()
    conversation_id = create_conversation(client, input_config)["conversation_id"]

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert _normalize_config_for_comparison(
        config.to_dict()
    ) == _normalize_config_for_comparison(input_config.to_dict())

    input_config.model = "openai/gpt-4o-mini"
    updated_config_dict = input_config.to_dict()
    # Strip workspace: server security check rejects external paths (path traversal fix).
    updated_config_dict.get("chat", {}).pop("workspace", None)
    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config", json=updated_config_dict
    )
    assert response.status_code == 200

    response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(response.get_json())
    assert _normalize_config_for_comparison(
        config.to_dict()
    ) == _normalize_config_for_comparison(input_config.to_dict())


def test_v2_chat_config_system_prompt_roundtrip_and_clear(client: FlaskClient):
    """Config PATCH should persist, apply, and clear a conversation-local system prompt."""
    conversation_id = create_conversation(client)["conversation_id"]
    system_prompt = "Answer in bullet points."

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"system_prompt": system_prompt}},
    )
    assert response.status_code == 200

    config_response = client.get(f"/api/v2/conversations/{conversation_id}/config")
    config = ChatConfig.from_dict(config_response.get_json())
    assert config.system_prompt == system_prompt

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    system_messages = [
        m["content"] for m in conversation["log"] if m["role"] == "system"
    ]
    assert system_messages[-1] == system_prompt

    clear_response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"system_prompt": ""}},
    )
    assert clear_response.status_code == 200

    cleared_config = client.get(
        f"/api/v2/conversations/{conversation_id}/config"
    ).get_json()
    assert "system_prompt" not in cleared_config["chat"]

    cleared_conversation = client.get(
        f"/api/v2/conversations/{conversation_id}"
    ).get_json()
    cleared_system_messages = [
        m["content"] for m in cleared_conversation["log"] if m["role"] == "system"
    ]
    assert system_prompt not in cleared_system_messages


def test_v2_chat_config_update_missing_conversation_returns_404(client: FlaskClient):
    """Test that updating config for a missing conversation returns a 404.

    Also asserts that no orphaned directory or config file is created on disk,
    and that the existence check fires before any side-effecting operations.
    """
    from gptme.dirs import get_logs_dir  # fmt: skip

    input_config = ChatConfig(model="openai/gpt-4o")
    input_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]
    input_config.mcp = MCPConfig()

    conversation_id = "missing-conversation"
    logdir = get_logs_dir() / conversation_id

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json=input_config.to_dict(),
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": f"Conversation not found: {conversation_id}"
    }
    # Ensure no orphaned directory or config file was created on disk
    assert not logdir.exists(), (
        f"Orphaned logdir was created at {logdir} despite 404 response"
    )


def test_v2_conversation_agent_avatar_missing_conversation_returns_404(
    client: FlaskClient,
):
    """Missing conversations should return 404 before creating config side effects."""
    import shutil  # fmt: skip

    from gptme.dirs import get_logs_dir  # fmt: skip

    conversation_id = "missing-avatar-conversation"
    logdir = get_logs_dir() / conversation_id
    if logdir.exists():
        shutil.rmtree(logdir)
    assert not logdir.exists()

    response = client.get(f"/api/v2/conversations/{conversation_id}/agent/avatar")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": f"Conversation not found: {conversation_id}"
    }
    assert not logdir.exists(), (
        f"Orphaned logdir was created at {logdir} despite 404 response"
    )


def test_v2_conversation_agent_avatar_orphaned_logdir_returns_404(
    client: FlaskClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Avatar endpoint should reject logdirs without a conversation log."""
    logdir = tmp_path / "logs" / "orphaned-avatar-conversation"
    logdir.mkdir(parents=True)

    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: tmp_path / "logs")

    response = client.get(
        "/api/v2/conversations/orphaned-avatar-conversation/agent/avatar"
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "Conversation not found: orphaned-avatar-conversation"
    }


@pytest.mark.parametrize(
    "body",
    [
        [],  # empty array — was rejected by old truthiness guard
        [1, 2, 3],  # non-empty array — the real regression: old guard accepted this
        "string",  # JSON string
        42,  # JSON number
    ],
)
def test_v2_chat_config_patch_rejects_non_object_json(
    client: FlaskClient, body: object
):
    """Config PATCH should reject JSON arrays/strings before config parsing."""
    conv = create_conversation(client)
    conversation_id = conv["conversation_id"]

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json=body,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "JSON body must be an object"}


@pytest.mark.parametrize("bad_workspace", [[], 42, {"path": "~/tmp"}])
def test_v2_chat_config_patch_rejects_invalid_workspace_type(
    client: FlaskClient, bad_workspace: object
):
    """Config PATCH should reject non-string workspace paths with 400."""
    conv = create_conversation(client)
    conversation_id = conv["conversation_id"]

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"workspace": bad_workspace}},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "chat.workspace must be a string path"}


@pytest.mark.parametrize(
    ("tools_payload", "expected_error"),
    [
        ("shell", "tools must be a list of strings"),
        (["definitely-not-a-tool"], "Tool 'definitely-not-a-tool' not found"),
    ],
)
def test_v2_chat_config_patch_validates_tools_before_init(
    client: FlaskClient, tools_payload: object, expected_error: str
):
    """Config PATCH should reject malformed tool allowlists with 400."""
    conv = create_conversation(client)
    conversation_id = conv["conversation_id"]

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"tools": tools_payload}},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert expected_error in data["error"]


@pytest.mark.parametrize(
    "bad_model",
    [12345, 3.14, True, ["gpt-4"], {"name": "gpt-4"}],
)
def test_v2_chat_config_patch_rejects_non_string_model(
    client: FlaskClient, bad_model: object
):
    """Config PATCH should reject non-string model values with 400 not 500."""
    conv = create_conversation(client)
    conversation_id = conv["conversation_id"]

    response = client.patch(
        f"/api/v2/conversations/{conversation_id}/config",
        json={"chat": {"model": bad_model}},
    )

    assert response.status_code == 400
    assert "model must be a string" in response.get_json()["error"]


def test_v2_chat_config_patch_rejected_during_generation(client: FlaskClient):
    """Config PATCH should return 409 when a session is actively generating."""
    conv = create_conversation(client)
    conversation_id = conv["conversation_id"]

    with unittest.mock.patch(
        "gptme.server.api_v2.SessionManager.get_sessions_for_conversation"
    ) as mock_get:
        mock_session = unittest.mock.MagicMock()
        mock_session.generating = True
        mock_get.return_value = [mock_session]

        response = client.patch(
            f"/api/v2/conversations/{conversation_id}/config",
            json={"model": "openai/gpt-4o"},
        )

    assert response.status_code == 409
    assert "generation is in progress" in response.get_json()["error"]


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
    ("files_payload", "expected_error"),
    [
        (["/etc/passwd"], "Absolute file paths are not supported"),
        (["../outside.txt"], "File path escapes workspace"),
    ],
)
def test_v2_edit_message_rejects_files_outside_workspace(
    client: FlaskClient, files_payload: list[str], expected_error: str
):
    """Editing message files must reject local paths outside the workspace."""
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
    assert expected_error in response.get_json()["error"]

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    assert "files" not in conversation["log"][user_index]


def test_v2_edit_message_resolves_relative_files_against_workspace(
    client: FlaskClient,
):
    """Relative edited files are stored under the conversation workspace."""
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
        json={"files": ["attachments/ok.txt"]},
    )

    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    assert conversation["log"][user_index]["files"] == ["attachments/ok.txt"]


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


@pytest.mark.parametrize(
    "tools_payload",
    [
        "bash",
        ["shell", 123],
        {"name": "shell"},
    ],
)
def test_v2_post_message_rejects_invalid_tools_payload(
    client: FlaskClient, tools_payload: object
):
    """POST message should reject malformed tool allowlists with 400."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Test message", "tools": tools_payload},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "tools must be a list of strings"}


def test_v2_post_message_rejects_unknown_tool_name(client: FlaskClient):
    """POST message should surface unknown tool names as a 400, not a 500."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={
            "role": "user",
            "content": "Test message",
            "tools": ["definitely-not-a-real-tool"],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "Tool 'definitely-not-a-real-tool' not found" in data["error"]


@pytest.mark.parametrize("body", [[], [1, 2, 3], "string", 42])
def test_v2_post_message_rejects_non_object_json(client: FlaskClient, body: object):
    """POST /conversations/<id> should reject non-object JSON bodies with 400."""
    conversation_id = create_conversation(client)["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json=body,
    )

    assert response.status_code == 400
    assert "object" in response.get_json()["error"].lower()


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


@pytest.mark.parametrize(
    "endpoint_builder",
    [
        lambda conversation_id, user_index: (
            f"/api/v2/conversations/{conversation_id}",
            {
                "role": "user",
                "content": "Test message",
                "files": ["file:///etc/passwd"],
            },
            None,
        ),
        lambda conversation_id, user_index: (
            f"/api/v2/conversations/{conversation_id}/messages/{user_index}",
            {"files": ["file:///etc/passwd"]},
            user_index,
        ),
    ],
)
def test_v2_message_file_uris_reject_file_scheme(client: FlaskClient, endpoint_builder):
    """POST and PATCH message files must reject local file:// URIs."""
    conversation_id = create_conversation(client)["conversation_id"]
    response = client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Original message"},
    )
    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    user_index = len(conversation["log"]) - 1

    endpoint, payload, patched_index = endpoint_builder(conversation_id, user_index)
    response = client.open(
        endpoint, method="PATCH" if patched_index is not None else "POST", json=payload
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "file:// URIs are not supported for message attachments"
    }

    conversation = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert conversation is not None
    assert conversation["log"][user_index].get("files") is None


def test_v2_fork_conversation_from_message(client: FlaskClient):
    """Forking should create a new conversation with messages up to the selected index."""
    conversation_id = create_conversation(client)["conversation_id"]
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "First question"},
    )
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "assistant", "content": "First answer"},
    )
    client.post(
        f"/api/v2/conversations/{conversation_id}",
        json={"role": "user", "content": "Second question"},
    )

    source = client.get(f"/api/v2/conversations/{conversation_id}").get_json()
    assert source is not None
    fork_index = len(source["log"]) - 2

    response = client.post(
        f"/api/v2/conversations/{conversation_id}/fork?after_message={fork_index}"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert data["conversation_id"] != conversation_id
    assert data["session_id"]

    forked = client.get(f"/api/v2/conversations/{data['conversation_id']}").get_json()
    assert forked is not None
    assert forked["name"] == f"Fork of {conversation_id} @ msg {fork_index + 1}"
    assert [msg["content"] for msg in forked["log"]] == [
        msg["content"] for msg in source["log"][: fork_index + 1]
    ]
    assert [msg["role"] for msg in forked["log"]] == [
        msg["role"] for msg in source["log"][: fork_index + 1]
    ]


def test_v2_fork_conversation_rejects_out_of_range_index(client: FlaskClient):
    """Out-of-range fork indexes are bad requests, not missing conversations."""
    conversation_id = create_conversation(client)["conversation_id"]

    response = client.post(
        f"/api/v2/conversations/{conversation_id}/fork?after_message=999"
    )

    assert response.status_code == 400
    assert "out of range" in response.get_json()["error"]


def test_copy_messages_for_fork_copies_only_referenced_attachments(tmp_path: Path):
    """Forking should not copy attachments excluded from the retained message slice."""
    from gptme.message import Message  # fmt: skip
    from gptme.server.api_v2 import _copy_messages_for_fork  # fmt: skip

    source_logdir = tmp_path / "source"
    dest_logdir = tmp_path / "fork"
    source_attachments = source_logdir / "attachments"
    source_attachments.mkdir(parents=True)
    (source_attachments / "keep.txt").write_text("keep", encoding="utf-8")
    (source_attachments / "skip.txt").write_text("skip", encoding="utf-8")

    copied = _copy_messages_for_fork(
        [Message("user", "Keep this file", files=[Path("attachments/keep.txt")])],
        source_logdir,
        dest_logdir,
    )

    assert copied[0].files == [dest_logdir / "attachments" / "keep.txt"]
    fork_attachments = dest_logdir / "attachments"
    assert (fork_attachments / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (fork_attachments / "skip.txt").exists()


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
    def mock_stream_error(
        messages, model, tools=None, max_tokens=None, temperature=None, top_p=None
    ):
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

    def mock_stream_error(
        messages, model, tools=None, max_tokens=None, temperature=None, top_p=None
    ):
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


def test_v2_create_conversation_missing_content(client: FlaskClient):
    """Creating a conversation with a message missing 'content' returns 400."""
    import uuid

    conv_id = f"test-missing-content-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "messages": [{"role": "user"}],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "content" in data["error"].lower()


@pytest.mark.parametrize("bad_content", [12345, None, True])
def test_v2_create_conversation_non_string_content(
    client: FlaskClient, bad_content: object
):
    """Creating a conversation with non-string message content returns 400 (not 500)."""
    import uuid

    conv_id = f"test-non-str-content-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "messages": [{"role": "user", "content": bad_content}],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "content" in data["error"].lower()


def test_v2_create_conversation_invalid_timestamp(client: FlaskClient):
    """Creating a conversation with an invalid timestamp returns 400."""
    import uuid

    conv_id = f"test-bad-timestamp-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "messages": [
                {"role": "user", "content": "hello", "timestamp": "not-a-date"}
            ],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "timestamp" in data["error"].lower()


def test_v2_create_conversation_non_string_timestamp(client: FlaskClient):
    """Creating a conversation with a non-string timestamp returns 400 (not 500)."""
    import uuid

    conv_id = f"test-numeric-ts-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "messages": [{"role": "user", "content": "hello", "timestamp": 12345}],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "timestamp" in data["error"].lower()


@pytest.mark.parametrize("body", [[], [1, 2, 3], "string", 42])
def test_v2_create_conversation_non_object_body(client: FlaskClient, body: object):
    """PUT /conversations/<id> with a non-object JSON body returns 400 (not 500)."""
    import uuid

    conv_id = f"test-non-object-body-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json=body,
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "object" in data["error"].lower()


def test_v2_create_conversation_empty_body_defaults(client: FlaskClient):
    """PUT /conversations/<id> with no body creates a default conversation."""
    import uuid

    conv_id = f"test-empty-body-{uuid.uuid4().hex[:8]}"
    response = client.put(f"/api/v2/conversations/{conv_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["conversation_id"] == conv_id
    assert "session_id" in data


def test_v2_create_conversation_malformed_json_body(client: FlaskClient):
    """PUT /conversations/<id> with malformed JSON returns 400 (not Werkzeug 400).

    When get_json(silent=True) encounters malformed JSON (e.g. {bad:),
    it returns None rather than raising BadRequest.  The endpoint should
    surface a structured "Malformed JSON in request body" error instead of the
    raw Werkzeug 400 that flask.request.json would have produced.
    """
    import uuid

    conv_id = f"test-malformed-json-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        data="{bad:",  # malformed JSON: unclosed brace
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data == {"error": "Malformed JSON in request body"}


def test_v2_edit_message_malformed_json_body(client: FlaskClient):
    """PATCH /messages/<index> with malformed JSON should return a JSON 400."""
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
        data="{bad:",
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data == {"error": "Malformed JSON in request body"}


@pytest.mark.parametrize("messages", ["not-a-list", 42, {"key": "val"}])
def test_v2_create_conversation_messages_not_list(
    client: FlaskClient, messages: object
):
    """PUT /conversations/<id> with non-list 'messages' returns 400 (not 500)."""
    import uuid

    conv_id = f"test-msgs-not-list-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={"messages": messages},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "messages" in data["error"].lower()


@pytest.mark.parametrize("bad_prompt", [None, [], 42, {"mode": "full"}])
def test_v2_create_conversation_rejects_non_string_prompt(
    client: FlaskClient, tmp_path, monkeypatch, bad_prompt: object
):
    """PUT /conversations/<id> should reject non-string prompt values before side effects."""
    import uuid

    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: logs_dir)
    conv_id = f"test-bad-prompt-{uuid.uuid4().hex[:8]}"

    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={"prompt": bad_prompt},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "'prompt' must be a string"}
    assert not (logs_dir / conv_id).exists()

    retry = client.put(f"/api/v2/conversations/{conv_id}", json={"prompt": "none"})
    assert retry.status_code == 200


@pytest.mark.parametrize("bad_agent", [[], 42, {"path": "~/agent"}])
def test_v2_create_conversation_rejects_invalid_agent_type(
    client: FlaskClient, tmp_path, monkeypatch, bad_agent: object
):
    """PUT /conversations/<id> should reject non-string config.chat.agent values."""
    import uuid

    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: logs_dir)
    conv_id = f"test-bad-agent-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={"config": {"chat": {"agent": bad_agent}}},
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "chat.agent must be a string path"}
    assert not (logs_dir / conv_id).exists()

    retry = client.put(f"/api/v2/conversations/{conv_id}", json={"prompt": "none"})
    assert retry.status_code == 200


def test_v2_create_conversation_message_not_object(client: FlaskClient):
    """PUT /conversations/<id> with a non-object message item returns 400 (not 500)."""
    import uuid

    conv_id = f"test-msg-not-obj-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={"messages": [1, 2, 3]},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "object" in data["error"].lower()


def test_v2_create_conversation_preserves_message_files(client: FlaskClient):
    """PUT /conversations/<id> must preserve the 'files' field in initial messages."""
    conv_id = f"test-msg-files-{uuid.uuid4().hex[:8]}"
    uri = "https://example.com/image.png"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "prompt": "none",
            "messages": [{"role": "user", "content": "hello", "files": [uri]}],
        },
    )
    assert response.status_code == 200

    conversation = client.get(f"/api/v2/conversations/{conv_id}").get_json()
    assert conversation is not None
    user_msgs = [m for m in conversation["log"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["files"] == [uri]


@pytest.mark.parametrize(
    "files_payload",
    ["not-a-list", 42, {"key": "val"}, [1, 2, 3], [None]],
)
def test_v2_create_conversation_rejects_invalid_message_files(
    client: FlaskClient, files_payload: object
):
    """PUT /conversations/<id> must reject non-list or non-string-list 'files' with 400."""
    conv_id = f"test-msg-bad-files-{uuid.uuid4().hex[:8]}"
    response = client.put(
        f"/api/v2/conversations/{conv_id}",
        json={
            "prompt": "none",
            "messages": [{"role": "user", "content": "hello", "files": files_payload}],
        },
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "files" in data["error"].lower()


@pytest.mark.parametrize("body", [[], [1, 2, 3], "string", 42])
def test_v2_tasks_put_rejects_non_object_json(client: FlaskClient, body: object):
    """Task PUT should reject non-object JSON bodies with 400."""
    create_resp = client.post(
        "/api/v2/tasks",
        json={"content": "test task for put validation"},
    )
    assert create_resp.status_code == 201
    task_id = create_resp.get_json()["id"]

    response = client.put(
        f"/api/v2/tasks/{task_id}",
        json=body,
    )
    assert response.status_code == 400
    assert "object" in response.get_json()["error"].lower()


def test_v2_user_settings_returns_providers_and_model(client: FlaskClient, monkeypatch):
    """GET /api/v2/user/settings should reflect configured providers and default model."""
    from gptme.llm.models import ModelMeta

    fake_model = ModelMeta(
        model="claude-sonnet-4-5",
        provider="anthropic",
        context=10000,
        max_output=1000,
    )
    monkeypatch.setattr("gptme.server.api_v2.get_default_model", lambda: fake_model)
    monkeypatch.setattr(
        "gptme.server.api_v2.list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY")],
    )
    monkeypatch.setattr(
        "gptme.server.api_v2.get_user_config_env_source",
        lambda key: "config.local.toml" if key == "ANTHROPIC_API_KEY" else None,
    )
    monkeypatch.setattr(
        "gptme.server.api_v2.get_default_model_source",
        lambda: "config.toml",
    )
    monkeypatch.setattr(
        "gptme.server.api_v2.get_user_config_runtime_info",
        lambda: {
            "config_path": "~/.config/gptme/config.toml",
            "local_config_path": "~/.config/gptme/config.local.toml",
            "local_config_exists": True,
            "write_target": "~/.config/gptme/config.toml",
            "local_overrides_main": True,
        },
    )

    response = client.get("/api/v2/user/settings")
    assert response.status_code == 200
    data = response.get_json()
    assert data["providers_configured"] == ["anthropic"]
    assert data["provider_sources"] == {
        "anthropic": {
            "auth_source": "ANTHROPIC_API_KEY",
            "effective_source": "config.local.toml",
        }
    }
    assert data["default_model"] == "anthropic/claude-sonnet-4-5"
    assert data["default_model_source"] == "config.toml"
    assert data["config_files"]["write_target"] == "~/.config/gptme/config.toml"


def test_v2_user_settings_no_providers_no_model(client: FlaskClient, monkeypatch):
    """GET /api/v2/user/settings with no config returns empty lists and null model."""
    monkeypatch.setattr(
        "gptme.server.api_v2.list_available_providers",
        lambda: [],
    )
    monkeypatch.setattr("gptme.server.api_v2.get_default_model", lambda: None)
    monkeypatch.setattr(
        "gptme.server.api_v2.get_user_config_env_source", lambda _key: None
    )
    monkeypatch.setattr("gptme.server.api_v2.get_default_model_source", lambda: None)
    monkeypatch.setattr(
        "gptme.server.api_v2.get_user_config_runtime_info",
        lambda: {
            "config_path": "~/.config/gptme/config.toml",
            "local_config_path": "~/.config/gptme/config.local.toml",
            "local_config_exists": False,
            "write_target": "~/.config/gptme/config.toml",
            "local_overrides_main": True,
        },
    )

    response = client.get("/api/v2/user/settings")
    assert response.status_code == 200
    data = response.get_json()
    assert data["providers_configured"] == []
    assert data["provider_sources"] == {}
    assert data["default_model"] is None
    assert data["default_model_source"] is None
    assert data["config_files"]["local_config_exists"] is False


def test_v2_audio_transcriptions_success(client: FlaskClient, monkeypatch):
    """POST /api/v2/audio/transcriptions proxies a short recording to OpenRouter."""

    class FakeConfig:
        def get_env(self, key: str, default: str | None = None):
            if key == "OPENROUTER_API_KEY":
                return "sk-or-test"
            return default

    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "text": "hello from openrouter",
                    "usage": {"seconds": 1.2, "total_tokens": 12},
                }
            ).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("gptme.server.api_v2.get_config", lambda: FakeConfig())
    monkeypatch.setattr("gptme.server.api_v2.urllib.request.urlopen", fake_urlopen)

    response = client.post(
        "/api/v2/audio/transcriptions",
        data={
            "file": (io.BytesIO(b"audio-bytes"), "speech.webm"),
            "format": "webm",
            "language": "en",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data == {
        "text": "hello from openrouter",
        "model": "openai/whisper-1",
        "usage": {"seconds": 1.2, "total_tokens": 12},
    }
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert captured["timeout"] == 60
    assert captured["body"]["model"] == "openai/whisper-1"
    assert captured["body"]["language"] == "en"
    assert captured["body"]["input_audio"]["format"] == "webm"
    assert isinstance(captured["body"]["input_audio"]["data"], str)


def test_v2_audio_transcriptions_requires_file(client: FlaskClient):
    """POST /api/v2/audio/transcriptions rejects empty multipart uploads."""
    response = client.post(
        "/api/v2/audio/transcriptions",
        data={},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "No audio file provided"}


def test_v2_audio_transcriptions_requires_openrouter(client: FlaskClient, monkeypatch):
    """POST /api/v2/audio/transcriptions fails cleanly without OpenRouter config."""

    class FakeConfig:
        def get_env(self, _key: str, default: str | None = None):
            return default

    monkeypatch.setattr("gptme.server.api_v2.get_config", lambda: FakeConfig())
    monkeypatch.setattr(
        "gptme.server.api_v2.get_stored_api_key", lambda _provider: None
    )

    response = client.post(
        "/api/v2/audio/transcriptions",
        data={"file": (io.BytesIO(b"audio-bytes"), "speech.webm"), "format": "webm"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "OpenRouter is not configured on this server"
    }


def test_v2_conversation_transcript_append(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """POST /api/v2/conversations/{id}/transcript appends voice transcript turns."""
    conv_id = f"test-transcript-{random.randint(0, 1000000)}"

    # Create conversation (also creates logdir)
    response = client.put(f"/api/v2/conversations/{conv_id}", json={"prompt": "Test"})
    assert response.status_code == 200

    # First call: should add messages
    transcript_payload = {
        "turns": [
            {"role": "user", "text": "Hello Bob!", "ended_at": 1234567890.0},
            {
                "role": "assistant",
                "text": "Hi! How can I help?",
                "ended_at": 1234567891.0,
            },
        ],
        "call_metadata": {
            "call_sid": "CA_test123",
            "source": "twilio",
            "duration_seconds": 60.0,
            "archive_path": "state/voice-calls/archive/test.json",
        },
    }
    response = client.post(
        f"/api/v2/conversations/{conv_id}/transcript", json=transcript_payload
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["conversation_id"] == conv_id
    assert data["messages_added"] == 2

    # Second call with same call_sid: idempotent (already_acked)
    response = client.post(
        f"/api/v2/conversations/{conv_id}/transcript", json=transcript_payload
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "already_acked"
    assert data["messages_added"] == 0

    # Different call_sid: should add more messages
    transcript_payload2 = {
        "turns": [
            {"role": "user", "text": "Follow-up question", "ended_at": 1234567892.0},
            {"role": "assistant", "text": "Sure, go ahead!", "ended_at": 1234567893.0},
        ],
        "call_metadata": {
            "call_sid": "CA_test456",
            "source": "twilio",
        },
    }
    response = client.post(
        f"/api/v2/conversations/{conv_id}/transcript", json=transcript_payload2
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["messages_added"] == 2


def test_v2_conversation_transcript_skips_empty_turns(client: FlaskClient):
    """Transcript endpoint skips whitespace-only and invalid-role turns."""
    conv_id = f"test-transcript-skip-{random.randint(0, 1000000)}"

    response = client.put(f"/api/v2/conversations/{conv_id}", json={"prompt": "Test"})
    assert response.status_code == 200

    payload = {
        "turns": [
            {"role": "user", "text": "Valid message", "ended_at": 1234567890.0},
            {
                "role": "user",
                "text": "   ",
                "ended_at": 1234567891.0,
            },  # whitespace-only
            {
                "role": "invalid",
                "text": "Should skip",
                "ended_at": 1234567892.0,
            },  # bad role
            {"role": "assistant", "text": "", "ended_at": 1234567893.0},  # empty
        ],
        "call_metadata": {"call_sid": "CA_skip_test"},
    }
    response = client.post(f"/api/v2/conversations/{conv_id}/transcript", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["messages_added"] == 1  # only the valid user message


def test_v2_conversation_transcript_creates_conversation(client: FlaskClient):
    """Transcript endpoint creates a new conversation if one doesn't exist."""
    conv_id = f"test-new-conv-{random.randint(0, 1000000)}"

    # Don't create the conversation first — transcript endpoint should create it
    payload = {
        "turns": [
            {"role": "user", "text": "First message ever", "ended_at": 1234567890.0}
        ],
        "call_metadata": {"call_sid": "CA_new_conv"},
    }
    response = client.post(f"/api/v2/conversations/{conv_id}/transcript", json=payload)
    # Should succeed even without pre-creating the conversation
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["messages_added"] == 1


def test_v2_conversation_transcript_rejects_non_dict_turns(client: FlaskClient):
    """Transcript endpoint returns 400 when any turn element is not an object."""
    conv_id = f"test-transcript-nondict-{random.randint(0, 1000000)}"

    for bad_turns in [
        ["bare string"],
        [42],
        [{"role": "user", "text": "valid"}, "oops"],
    ]:
        payload = {
            "turns": bad_turns,
            "call_metadata": {"call_sid": "CA_nondict"},
        }
        response = client.post(
            f"/api/v2/conversations/{conv_id}/transcript", json=payload
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "turns[" in data["error"] and "must be an object" in data["error"]


# --- Tasks and Agents malformed-JSON regression tests ---


@pytest.mark.parametrize("body", [[1, 2, 3], "string", 42])
def test_v2_tasks_post_rejects_non_object_json(client: FlaskClient, body: object):
    """Task POST should reject non-object JSON bodies with 400."""
    response = client.post("/api/v2/tasks", json=body)
    assert response.status_code == 400
    assert "object" in response.get_json()["error"].lower()


def test_v2_tasks_post_rejects_malformed_json(client: FlaskClient):
    """Task POST should return 400 (not 400 Werkzeug HTML) on truly malformed JSON."""
    response = client.post(
        "/api/v2/tasks",
        data=b"{bad:",
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None, (
        "Response must be valid JSON, not a raw Werkzeug error page"
    )
    assert "error" in data


def test_v2_tasks_put_rejects_malformed_json(client: FlaskClient):
    """Task PUT should return clean JSON 400 on truly malformed JSON body."""
    create_resp = client.post(
        "/api/v2/tasks", json={"content": "task for put malformed"}
    )
    assert create_resp.status_code == 201
    task_id = create_resp.get_json()["id"]

    response = client.put(
        f"/api/v2/tasks/{task_id}",
        data=b"{bad:",
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None, (
        "Response must be valid JSON, not a raw Werkzeug error page"
    )
    assert "error" in data


@pytest.mark.parametrize("body", [[1, 2, 3], "string", 42])
def test_v2_agents_put_rejects_non_object_json(client: FlaskClient, body: object):
    """Agents PUT should reject non-object JSON bodies with 400."""
    response = client.put("/api/v2/agents", json=body)
    assert response.status_code == 400
    assert "object" in response.get_json()["error"].lower()


def test_v2_agents_put_rejects_malformed_json(client: FlaskClient):
    """Agents PUT should return clean JSON 400 on truly malformed JSON body."""
    response = client.put(
        "/api/v2/agents",
        data=b"{bad:",
        content_type="application/json",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None, (
        "Response must be valid JSON, not a raw Werkzeug error page"
    )
    assert "error" in data


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        (
            {
                "name": ["bob"],
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": "echo ok",
            },
            "name must be a string",
        ),
        (
            {
                "name": False,
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": "echo ok",
            },
            "name must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": 123,
                "template_branch": "master",
                "fork_command": "echo ok",
            },
            "template_repo must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": 0,
                "template_branch": "master",
                "fork_command": "echo ok",
            },
            "template_repo must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": ["master"],
                "fork_command": "echo ok",
            },
            "template_branch must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": [],
                "fork_command": "echo ok",
            },
            "template_branch must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": {"cmd": "echo ok"},
            },
            "fork_command must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": False,
            },
            "fork_command must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": "echo ok",
                "path": 123,
            },
            "path must be a string",
        ),
        (
            {
                "name": "bob2",
                "template_repo": "https://example.com/repo.git",
                "template_branch": "master",
                "fork_command": "echo ok",
                "project_config": "bad",
            },
            "project_config must be an object",
        ),
    ],
)
def test_v2_agents_put_rejects_invalid_field_types(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    body: dict,
    expected_error: str,
):
    """Agents PUT should reject invalid field types before any side effects start."""

    def fail_workspace(*args, **kwargs):
        pytest.fail("create_workspace_from_template should not run for invalid input")

    def fail_project_config(*args, **kwargs):
        pytest.fail("ProjectConfig.from_dict should not run for invalid input")

    monkeypatch.setattr(
        "gptme.server.api_v2_agents.create_workspace_from_template", fail_workspace
    )
    monkeypatch.setattr(
        "gptme.server.api_v2_agents.ProjectConfig.from_dict", fail_project_config
    )

    response = client.put("/api/v2/agents", json=body)

    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == expected_error


def test_v2_agents_put_parses_project_config_object(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Agents PUT should parse a valid project_config object before workspace creation."""

    parsed_project_config = object()
    called: dict[str, Any] = {}

    def fake_project_config_from_dict(raw_config: dict[str, Any], workspace: Path):
        called["project_config_raw"] = raw_config
        called["project_config_workspace"] = workspace
        return parsed_project_config

    def fake_create_workspace_from_template(**kwargs):
        called["workspace_kwargs"] = kwargs

    def fake_init_conversation(workspace: Path):
        called["conversation_workspace"] = workspace
        return "conv-test"

    monkeypatch.setattr(
        "gptme.server.api_v2_agents.ProjectConfig.from_dict",
        fake_project_config_from_dict,
    )
    monkeypatch.setattr(
        "gptme.server.api_v2_agents.create_workspace_from_template",
        fake_create_workspace_from_template,
    )
    monkeypatch.setattr(
        "gptme.server.api_v2_agents.init_conversation", fake_init_conversation
    )

    body = {
        "name": "bob2",
        "template_repo": "https://example.com/repo.git",
        "template_branch": "master",
        "fork_command": "echo ok",
        "project_config": {"models": {"default": "openai/gpt-4o-mini"}},
    }

    response = client.put("/api/v2/agents", json=body)

    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"
    assert data["initial_conversation_id"] == "conv-test"
    assert called["project_config_raw"] == body["project_config"]
    assert called["project_config_workspace"] == called["workspace_kwargs"]["path"]
    assert called["workspace_kwargs"]["project_config"] is parsed_project_config
    assert called["conversation_workspace"] == called["workspace_kwargs"]["path"]


def test_v2_agents_put_rejects_invalid_nested_project_config(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Agents PUT should return 400 when nested project_config sections are malformed."""

    def fail_workspace(*args, **kwargs):
        pytest.fail(
            "create_workspace_from_template should not run for invalid project_config"
        )

    monkeypatch.setattr(
        "gptme.server.api_v2_agents.create_workspace_from_template", fail_workspace
    )

    response = client.put(
        "/api/v2/agents",
        json={
            "name": "bob2",
            "template_repo": "https://example.com/repo.git",
            "template_branch": "master",
            "fork_command": "echo ok",
            "project_config": {"rag": "boom"},
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == "Invalid project_config: rag must be an object"


def test_v2_agents_put_rejects_non_list_mcp_servers(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Agents PUT should return 400 when MCP servers is not a list."""

    def fail_workspace(*args, **kwargs):
        pytest.fail(
            "create_workspace_from_template should not run for invalid project_config"
        )

    monkeypatch.setattr(
        "gptme.server.api_v2_agents.create_workspace_from_template", fail_workspace
    )

    response = client.put(
        "/api/v2/agents",
        json={
            "name": "bob2",
            "template_repo": "https://example.com/repo.git",
            "template_branch": "master",
            "fork_command": "echo ok",
            "project_config": {"mcp": {"servers": "not_a_list"}},
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert data["error"] == "Invalid project_config: mcp.servers must be a list"


def test_v2_agents_put_rejects_non_object_mcp_server_entries(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Agents PUT should return 400 when an MCP server entry is not a dict."""

    def fail_workspace(*args, **kwargs):
        pytest.fail(
            "create_workspace_from_template should not run for invalid project_config"
        )

    monkeypatch.setattr(
        "gptme.server.api_v2_agents.create_workspace_from_template", fail_workspace
    )

    response = client.put(
        "/api/v2/agents",
        json={
            "name": "bob2",
            "template_repo": "https://example.com/repo.git",
            "template_branch": "master",
            "fork_command": "echo ok",
            "project_config": {"mcp": {"servers": ["not_an_object"]}},
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert (
        data["error"] == "Invalid project_config: mcp.servers entries must be objects"
    )
