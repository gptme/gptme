"""Tests for the --default-profile server option (issue #216).

Verifies that gptme-server injects a profile's system_prompt into new
conversations when the client doesn't supply one — the mechanism that lets the
computer-use Docker container start every session with the structured-first
backend-selection policy.
"""

import random

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(default_profile: str | None = None) -> FlaskClient:
    from gptme.server.app import create_app  # fmt: skip

    app = create_app(default_profile=default_profile)
    app.config["TESTING"] = True
    return app.test_client()


def _put_conversation(
    client: FlaskClient,
    system_prompt: str | None = None,
) -> tuple[int, dict]:
    """PUT a new conversation and return (status_code, response_json)."""
    conv_id = f"test-default-profile-{random.randint(0, 10_000_000)}"
    body: dict = {}
    if system_prompt is not None:
        body["config"] = {"chat": {"system_prompt": system_prompt}}

    resp = client.put(f"/api/v2/conversations/{conv_id}", json=body)
    data = resp.get_json() or {}
    return resp.status_code, data


def _get_messages(client: FlaskClient, conv_id: str) -> list[dict]:
    resp = client.get(f"/api/v2/conversations/{conv_id}")
    assert resp.status_code == 200
    return resp.get_json()["log"]


# ---------------------------------------------------------------------------
# create_app integration
# ---------------------------------------------------------------------------


class TestCreateAppDefaultProfile:
    def test_no_default_profile_sets_no_config_key(self):
        from gptme.server.app import create_app  # fmt: skip

        app = create_app()
        assert "SERVER_DEFAULT_PROFILE" not in app.config

    def test_default_profile_stored_in_config(self):
        from gptme.server.app import create_app  # fmt: skip

        app = create_app(default_profile="computer-use")
        assert app.config["SERVER_DEFAULT_PROFILE"] == "computer-use"

    def test_none_default_profile_not_stored(self):
        from gptme.server.app import create_app  # fmt: skip

        app = create_app(default_profile=None)
        assert "SERVER_DEFAULT_PROFILE" not in app.config


# ---------------------------------------------------------------------------
# Conversation creation — system prompt injection
# ---------------------------------------------------------------------------


class TestDefaultProfileInjectedOnConversationCreate:
    def test_computer_use_profile_system_prompt_injected(self):
        """Server with --default-profile computer-use injects the profile's system
        prompt into new conversations that don't supply one."""
        client = _make_client(default_profile="computer-use")
        conv_id = f"test-cu-profile-{random.randint(0, 10_000_000)}"

        resp = client.put(f"/api/v2/conversations/{conv_id}", json={})
        assert resp.status_code == 200

        messages = _get_messages(client, conv_id)
        system_msgs = [m for m in messages if m.get("role") == "system"]

        # At least one system message should contain the computer-use profile content
        from gptme.profiles import get_profile  # fmt: skip

        profile = get_profile("computer-use")
        assert profile is not None
        assert profile.system_prompt

        found = any(profile.system_prompt in m.get("content", "") for m in system_msgs)
        assert found, (
            "computer-use profile system prompt not found in conversation messages. "
            f"System messages: {[m.get('content', '')[:100] for m in system_msgs]}"
        )

    def test_explicit_system_prompt_takes_precedence(self):
        """When the client supplies a system_prompt, the server default is NOT added."""
        client = _make_client(default_profile="computer-use")
        conv_id = f"test-explicit-sp-{random.randint(0, 10_000_000)}"

        explicit_prompt = "You are a specialized test assistant."
        resp = client.put(
            f"/api/v2/conversations/{conv_id}",
            json={"config": {"chat": {"system_prompt": explicit_prompt}}},
        )
        assert resp.status_code == 200

        messages = _get_messages(client, conv_id)
        system_msgs = [m for m in messages if m.get("role") == "system"]

        from gptme.profiles import get_profile  # fmt: skip

        profile = get_profile("computer-use")
        assert profile and profile.system_prompt

        # The profile's system prompt must NOT appear (explicit prompt took precedence)
        assert not any(
            profile.system_prompt in m.get("content", "") for m in system_msgs
        ), "Profile system prompt should not be injected when client supplies one"

        # The explicit prompt must appear
        assert any(explicit_prompt in m.get("content", "") for m in system_msgs), (
            "Explicit system prompt should be present in conversation messages"
        )

    def test_no_default_profile_injects_nothing_extra(self):
        """Without --default-profile, conversation creation behaves as before."""
        client = _make_client(default_profile=None)
        conv_id = f"test-no-default-{random.randint(0, 10_000_000)}"

        resp = client.put(f"/api/v2/conversations/{conv_id}", json={})
        assert resp.status_code == 200

        messages = _get_messages(client, conv_id)
        from gptme.profiles import get_profile  # fmt: skip

        profile = get_profile("computer-use")
        assert profile and profile.system_prompt

        # The computer-use profile's system prompt must NOT appear
        assert not any(profile.system_prompt in m.get("content", "") for m in messages)

    def test_unknown_profile_name_silently_skipped(self):
        """An unrecognised profile name in --default-profile is skipped gracefully
        rather than causing a 500 on conversation creation."""
        client = _make_client(default_profile="nonexistent-profile-xyz")
        conv_id = f"test-unknown-profile-{random.randint(0, 10_000_000)}"

        resp = client.put(f"/api/v2/conversations/{conv_id}", json={})
        # Must not 500 — unknown profile is silently ignored
        assert resp.status_code == 200

    def test_profile_system_prompt_survives_config_patch(self):
        """Config PATCH must not drop the server default profile's system prompt."""
        client = _make_client(default_profile="computer-use")
        conv_id = f"test-profile-patch-{random.randint(0, 10_000_000)}"

        resp = client.put(f"/api/v2/conversations/{conv_id}", json={})
        assert resp.status_code == 200

        from gptme.profiles import get_profile  # fmt: skip

        profile = get_profile("computer-use")
        assert profile and profile.system_prompt

        patch_resp = client.patch(
            f"/api/v2/conversations/{conv_id}/config",
            json={"chat": {"model": "openai/gpt-4o-mini"}},
        )
        assert patch_resp.status_code == 200

        messages = _get_messages(client, conv_id)
        system_messages = [
            m.get("content", "") for m in messages if m.get("role") == "system"
        ]
        assert system_messages.count(profile.system_prompt) == 1

    def test_profile_system_prompt_durable_across_server_restart(self):
        """System prompt injected from --default-profile must survive a simulated
        server restart (new app instance without --default-profile) followed by a
        PATCH call — i.e., it must be persisted to config.toml on PUT, not only
        held in the live server config."""
        import os  # fmt: skip
        import tempfile  # fmt: skip
        from unittest.mock import patch  # fmt: skip

        from gptme.profiles import get_profile  # fmt: skip
        from gptme.server.app import create_app  # fmt: skip

        profile = get_profile("computer-use")
        assert profile and profile.system_prompt

        conv_id = f"test-restart-durability-{random.randint(0, 10_000_000)}"

        with tempfile.TemporaryDirectory() as tmpdir:
            env_override = {"GPTME_LOGS_HOME": tmpdir}

            # Phase 1 — server WITH --default-profile creates conversation
            with patch.dict(os.environ, env_override):
                app1 = create_app(default_profile="computer-use")
                app1.config["TESTING"] = True
                with app1.test_client() as client1:
                    resp = client1.put(f"/api/v2/conversations/{conv_id}", json={})
                    assert resp.status_code == 200

            # Phase 2 — server WITHOUT --default-profile (simulates restart)
            with patch.dict(os.environ, env_override):
                app2 = create_app(default_profile=None)
                app2.config["TESTING"] = True
                with app2.test_client() as client2:
                    patch_resp = client2.patch(
                        f"/api/v2/conversations/{conv_id}/config",
                        json={"chat": {"model": "openai/gpt-4o-mini"}},
                    )
                    assert patch_resp.status_code == 200

                    messages = _get_messages(client2, conv_id)
                    system_messages = [
                        m.get("content", "")
                        for m in messages
                        if m.get("role") == "system"
                    ]
                    assert system_messages.count(profile.system_prompt) == 1, (
                        "Profile system prompt must survive a server restart without "
                        "--default-profile when it was persisted to config.toml on PUT."
                    )


# ---------------------------------------------------------------------------
# entrypoint.sh uses --default-profile computer-use
# ---------------------------------------------------------------------------


class TestDockerEntrypointUsesDefaultProfile:
    def test_entrypoint_passes_default_profile_computer_use(self):
        """entrypoint.sh must pass --default-profile computer-use to the server
        so that Docker computer-use sessions get the structured-first policy."""
        from pathlib import Path

        entrypoint = (
            Path(__file__).parent.parent / "scripts" / "computer_home" / "entrypoint.sh"
        )
        text = entrypoint.read_text()
        assert "--default-profile" in text, (
            "entrypoint.sh must pass --default-profile to gptme-server so the "
            "computer-use Docker container applies the structured-first policy "
            "without requiring the client to set --agent-profile."
        )
        assert "computer-use" in text
