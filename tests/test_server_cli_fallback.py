"""Tests for server CLI provider-aware fallback model selection."""

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.constants import (
    DEFAULT_FALLBACK_MODEL,
    PROVIDER_FALLBACK_MODELS,
    _pick_fallback_model,
)


def test_pick_fallback_prefers_available_provider():
    """When OpenAI is available but not Anthropic, fallback should use OpenAI."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[("openai", "OPENAI_API_KEY")],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openai"]


def test_pick_fallback_uses_first_available_provider():
    """Fallback picks the first provider returned by list_available_providers."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[
            ("openrouter", "OPENROUTER_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openrouter"]


def test_pick_fallback_returns_default_when_no_providers():
    """Without configured providers, fall back to the default model.

    The init() call that follows will then raise a clear error — the fallback
    is not meant to be useful in this case, just to preserve existing behavior.
    """
    with patch("gptme.llm.list_available_providers", return_value=[]):
        assert _pick_fallback_model() == DEFAULT_FALLBACK_MODEL


def test_pick_fallback_skips_unknown_providers():
    """If list_available_providers returns a provider we don't have a fallback
    model for, skip it and try the next one."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[
            ("some-unknown-plugin", "SOME_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openai"]


def test_provider_fallback_models_includes_all_api_key_providers():
    """PROVIDER_FALLBACK_MODELS should cover every API-key provider gptme
    supports (except azure, which requires a tenant-specific deployment name,
    and nvidia, which has no built-in recommended model).
    """
    from gptme.llm import PROVIDER_API_KEYS

    expected = set(PROVIDER_API_KEYS.keys()) - {"azure", "nvidia"}
    missing = expected - set(PROVIDER_FALLBACK_MODELS.keys())
    assert not missing, f"Missing fallback models for providers: {missing}"


def _assert_setup_api_serves(
    tmp_path: Path,
    port: int,
    cli_args: list[str],
    extra_env: dict[str, str] | None = None,
) -> str:
    """Start the server in a clean HOME and assert the setup API is reachable.

    Returns the captured server log so callers can assert on what was resolved.
    """
    repo = Path(__file__).resolve().parents[1]
    env = {
        name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ
    }
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        APPDATA=str(tmp_path / "AppData" / "Roaming"),
        LOCALAPPDATA=str(tmp_path / "AppData" / "Local"),
        XDG_CONFIG_HOME=str(tmp_path / ".config"),
        PYTHONPATH=str(repo),
        GPTME_SERVER_TOKEN="test-fallback-token",
        PYTHON_DOTENV_DISABLED="1",
    )
    env.update(extra_env or {})
    url = f"http://127.0.0.1:{port}/api/v2"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gptme.server.cli",
                "serve",
                *cli_args,
                "--tools",
                "none",
                "--port",
                str(port),
            ],
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with requests.Session() as client:
                client.trust_env = False
                deadline = time.monotonic() + 20
                response = None
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        response = client.get(
                            url,
                            headers={"Authorization": "Bearer test-fallback-token"},
                            timeout=1,
                        )
                        break
                    except requests.ConnectionError:
                        time.sleep(0.05)
                log.seek(0)
                assert response is not None, log.read()
                assert response.status_code == 200, response.text
                assert response.json()["provider_configured"] is False
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    return (tmp_path / "server.log").read_text()


@pytest.mark.parametrize(
    "model",
    ["openrouter/deepseek/deepseek-v4.1-flash", "openai/gpt-4.1-nano"],
)
def test_configured_model_without_key_still_serves_setup(
    model: str, tmp_path: Path, unused_tcp_port: int
) -> None:
    """Missing credentials must leave the setup API reachable, not crash startup."""
    _assert_setup_api_serves(tmp_path, unused_tcp_port, ["--model", model])


@pytest.mark.parametrize(
    ("cli_args", "extra_env"),
    [
        # --model flag, another provider's key present
        (
            ["--model", "openrouter/deepseek/deepseek-v4.1-flash"],
            {"OPENAI_API_KEY": "placeholder-not-a-real-key"},
        ),
        # MODEL environment variable, another provider's key present
        (
            [],
            {
                "MODEL": "openrouter/deepseek/deepseek-v4.1-flash",
                "OPENAI_API_KEY": "placeholder-not-a-real-key",
            },
        ),
    ],
)
def test_explicit_model_never_falls_back_to_another_provider(
    cli_args: list[str],
    extra_env: dict[str, str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    """A pinned model must never be replaced by a different provider.

    With only OPENAI_API_KEY set, startup used to pick openai/gpt-4o-mini and
    silently run every conversation there (gptme/gptme#5353). It must instead
    start in setup mode and say which model/key is missing.
    """
    log = _assert_setup_api_serves(
        tmp_path, unused_tcp_port, cli_args, extra_env=extra_env
    )
    assert "Using fallback" not in log
    assert "openai/gpt-4o-mini" not in log
    assert "openrouter/deepseek/deepseek-v4.1-flash" in log


def test_nvidia_key_without_model_still_serves_setup(
    tmp_path: Path, unused_tcp_port: int
) -> None:
    """An NVIDIA key alone selects nvidia, which has no recommended model.

    Startup must treat the explicit-model requirement as a configuration
    error and keep the setup API reachable instead of exiting.
    """
    _assert_setup_api_serves(
        tmp_path,
        unused_tcp_port,
        [],
        extra_env={"NVIDIA_API_KEY": "placeholder-not-a-real-key"},
    )
