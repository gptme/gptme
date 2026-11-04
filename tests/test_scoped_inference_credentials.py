"""Offline opt-in credential delivery tests. All authority is synthetic."""

import os
import subprocess
import sys
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from gptme.config.core import Config
from gptme.config.models import UserConfig
from gptme.llm import llm_openai


@pytest.fixture(autouse=True)
def synthetic_environment(monkeypatch, tmp_path):
    # Failure tracebacks must never contain the real parent environment.
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    monkeypatch.setattr(llm_openai, "clients", {})


def credential_file(tmp_path: Path, value: str = "synthetic-inference-only") -> Path:
    path = tmp_path / "inference-key"
    path.write_text(value)
    path.chmod(0o600)
    return path


def test_scoped_openrouter_uses_file_not_env_config_store_or_proxy(
    monkeypatch, tmp_path
):
    path = credential_file(tmp_path)
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(path))
    config = Config(
        user=UserConfig(
            env={
                "OPENROUTER_API_KEY": "synthetic-wrong-config",
                "LLM_PROXY_API_KEY": "synthetic-wrong-proxy",
                "LLM_PROXY_URL": "https://unintended.invalid",
            }
        )
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-wrong-env")
    with patch(
        "gptme.credentials.get_stored_api_key",
        side_effect=AssertionError("store fallback used"),
    ):
        llm_openai.init("openrouter", config)
    client = cast(llm_openai._LazyClient, llm_openai.clients["openrouter"])
    assert client._kwargs["api_key"] == "synthetic-inference-only"
    assert client._kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert "synthetic-inference-only" not in os.environ.values()


@pytest.mark.parametrize(
    "kind", ["missing", "empty", "oversize", "permissions", "directory", "invalid-utf8"]
)
def test_scoped_missing_or_invalid_auth_never_falls_back(
    monkeypatch, tmp_path, kind, capsys
):
    path = credential_file(tmp_path)
    if kind == "missing":
        path.unlink()
    elif kind == "empty":
        path.write_text("")
    elif kind == "oversize":
        path.write_text("x" * 20000)
    elif kind == "permissions":
        path.chmod(0o644)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(path))
    config = Config(
        user=UserConfig(
            env={
                "OPENROUTER_API_KEY": "synthetic-config-fallback",
                "LLM_PROXY_API_KEY": "synthetic-proxy-fallback",
            }
        )
    )
    with (
        patch(
            "gptme.credentials.get_stored_api_key",
            side_effect=AssertionError("store fallback used"),
        ),
        pytest.raises(
            ValueError, match="Scoped OpenRouter credential unavailable"
        ) as exc,
    ):
        llm_openai.init("openrouter", config)
    assert str(path) not in str(exc.value)
    assert "synthetic-" not in str(exc.value)
    assert not llm_openai.clients
    assert not capsys.readouterr().out


def test_empty_file_selector_is_not_opt_out(monkeypatch):
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", "")
    with pytest.raises(ValueError, match="Scoped OpenRouter credential unavailable"):
        llm_openai.init("openrouter", Config(user=UserConfig()))


def test_scoped_provider_switch_fails_closed(monkeypatch, tmp_path):
    from gptme.llm import init_llm

    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    with pytest.raises(ValueError, match="Scoped inference requires openrouter"):
        init_llm("anthropic")


def test_scoped_init_skips_dotenv(monkeypatch, tmp_path):
    import gptme.init as mod

    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    monkeypatch.setattr(mod, "_init_done", False)
    with (
        patch.object(mod, "load_dotenv") as load,
        patch.object(mod, "init_model"),
        patch.object(mod, "init_tools"),
        patch.object(mod, "init_hooks"),
        patch.object(mod, "init_commands"),
        patch.object(mod, "_init_plugins"),
    ):
        mod.init("openrouter/synthetic", False, [], "tool")
    load.assert_not_called()


def test_scoped_workspace_setup_skips_dotenv(monkeypatch, tmp_path):
    import gptme.executor as mod

    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    with (
        patch.object(mod, "load_dotenv") as load,
        patch.object(
            mod.Config, "from_workspace", return_value=Config(user=UserConfig())
        ),
        patch.object(mod, "set_config"),
        patch.object(mod, "init_tools", return_value=[]),
        patch.object(mod, "init_hooks"),
        patch.object(mod, "_register_core_compaction_hook"),
    ):
        mod.prepare_execution_environment(tmp_path)
    load.assert_not_called()


def test_scoped_client_setup_then_child_and_nested_child_have_no_key(tmp_path):
    path = credential_file(tmp_path)
    # Actual subprocess inheritance, with no network and no production stores.
    script = """
import os, subprocess, sys
from gptme.config.core import Config
from gptme.config.models import UserConfig
from gptme.llm.llm_openai import init, clients
init('openrouter', Config(user=UserConfig()))
assert clients['openrouter']._kwargs['api_key'] == 'synthetic-inference-only'
child = subprocess.run([sys.executable, '-c', "import os,subprocess,sys; assert 'synthetic-inference-only' not in os.environ.values(); subprocess.run([sys.executable,'-c',\\"import os; assert 'synthetic-inference-only' not in os.environ.values()\\"],check=True)"],check=True,capture_output=True,text=True)
print('client-setup-ok; child-clean; nested-clean')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "GPTME_OPENROUTER_API_KEY_FILE": str(path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "client-setup-ok; child-clean; nested-clean"
    assert "synthetic-inference-only" not in result.stderr


def test_scoped_discovery_does_not_consult_stored_or_config_auth(monkeypatch, tmp_path):
    from gptme.llm import list_available_providers

    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    config = Config(
        user=UserConfig(env={"ANTHROPIC_API_KEY": "synthetic-wrong-provider"})
    )
    with patch(
        "gptme.credentials.list_stored_credentials",
        side_effect=AssertionError("store discovery used"),
    ):
        assert list_available_providers(config) == [
            ("openrouter", "GPTME_OPENROUTER_API_KEY_FILE")
        ]


def test_scoped_model_listing_uses_only_scoped_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    config = Config(
        user=UserConfig(
            env={
                "LLM_PROXY_API_KEY": "synthetic-wrong-proxy",
                "LLM_PROXY_URL": "https://unintended.invalid",
            }
        )
    )
    llm_openai._get_available_models_cached.cache_clear()
    with (
        patch.object(llm_openai, "get_config", return_value=config),
        patch.object(llm_openai.requests, "get") as request,
    ):
        request.return_value.json.return_value = {"data": []}
        assert llm_openai.get_available_models("openrouter") == []
        assert request.call_args.args[0] == "https://openrouter.ai/api/v1/models"
        assert request.call_args.kwargs["headers"] == {
            "Authorization": "Bearer synthetic-inference-only"
        }
    llm_openai._get_available_models_cached.cache_clear()


@pytest.mark.parametrize("dispatch", ["_chat_complete", "_stream"])
def test_scoped_direct_dispatch_rejects_other_provider_before_auth(
    monkeypatch, tmp_path, dispatch
):
    import gptme.llm as llm

    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    with (
        patch.object(
            llm,
            "_gptme_backend",
            side_effect=AssertionError("provider dispatch reached"),
        ),
        pytest.raises(ValueError, match="Scoped inference requires openrouter"),
    ):
        getattr(llm, dispatch)([], "anthropic/claude-haiku-4-5", None)


def test_scoped_cached_catalog_does_not_bypass_guard(monkeypatch, tmp_path):
    llm_openai._get_available_models_cached.cache_clear()
    with (
        patch.object(llm_openai, "get_config", return_value=Config(user=UserConfig())),
        patch.object(llm_openai, "_get_openai_compatible_models", return_value=[]),
    ):
        assert llm_openai.get_available_models("local") == []
        monkeypatch.setenv(
            "GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path))
        )
        with pytest.raises(ValueError, match="Scoped inference requires openrouter"):
            llm_openai.get_available_models("local")
    llm_openai._get_available_models_cached.cache_clear()


def test_scoped_cached_legacy_client_is_not_reused(monkeypatch, tmp_path):
    config = Config(user=UserConfig(env={"OPENROUTER_API_KEY": "synthetic-legacy"}))
    llm_openai.init("openrouter", config)
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    with patch.object(llm_openai, "get_config", return_value=config):
        client = cast(llm_openai._LazyClient, llm_openai.get_client("openrouter"))
    assert client._kwargs["api_key"] == "synthetic-inference-only"


def test_scoped_reinit_cannot_replace_delivered_credential(monkeypatch, tmp_path):
    monkeypatch.setenv("GPTME_OPENROUTER_API_KEY_FILE", str(credential_file(tmp_path)))
    llm_openai.init("openrouter", Config(user=UserConfig()))
    with pytest.raises(
        ValueError,
        match="Scoped inference does not support runtime credential switching",
    ):
        llm_openai.reinit("openrouter", "synthetic-account-override")
