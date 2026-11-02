"""Summary configuration, selection, and bounded unavailable-model fallback."""

import httpx
import pytest
from openai import AuthenticationError, NotFoundError, RateLimitError

from gptme.config.user import load_user_config
from gptme.llm.models.resolution import get_summary_model_for
from gptme.message import Message
from gptme.util.summary import complete_summary


@pytest.mark.parametrize("value", ['"gemini/current"', "42", '""', '"gemini"'])
def test_summary_config(tmp_path, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'[models]\ndefault = "gemini"\nsummary = {value}\n')
    config = load_user_config(str(path))
    assert config.models.default == "gemini"
    assert config.models.summary == (
        "gemini/current" if value == '"gemini/current"' else None
    )


def _configure(monkeypatch, summary: str | None) -> None:
    from gptme.config import get_config

    monkeypatch.setattr(get_config().user.models, "summary", summary)


def test_summary_resolution(monkeypatch) -> None:
    _configure(monkeypatch, None)
    assert get_summary_model_for("gemini") == "gemini/gemini-3.8-flash"
    assert get_summary_model_for("local/my-model") == "local/my-model"
    _configure(monkeypatch, "openai/gpt-5-mini")
    assert get_summary_model_for("gemini") == "openai/gpt-5-mini"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(404, NotFoundError), (401, AuthenticationError), (429, RateLimitError)],
)
def test_summary_error_policy(
    monkeypatch, caplog, status: int, error_type: type
) -> None:
    _configure(monkeypatch, "gemini/unavailable")
    error = error_type(
        "model unavailable",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://example.com")
        ),
        body=None,
    )
    calls: list[str] = []

    def complete(messages, model: str, tools):
        calls.append(model)
        if len(calls) == 1:
            raise error
        return "Useful title", None

    monkeypatch.setattr("gptme.llm._chat_complete", complete)
    messages = [Message("user", "Please summarize this")]
    if status == 404:
        assert complete_summary(messages, "gemini/main") == "Useful title"
        assert calls == ["gemini/unavailable", "gemini/main"]
        assert "[models].summary" in caplog.text
    else:
        with pytest.raises(error_type):
            complete_summary(messages, "gemini/main")
        assert calls == ["gemini/unavailable"]


def test_no_retry_when_summary_is_main(monkeypatch) -> None:
    _configure(monkeypatch, "gemini/main")
    calls = []

    def complete(*args):
        calls.append(args)
        raise NotFoundError(
            "missing",
            response=httpx.Response(
                404, request=httpx.Request("POST", "https://example.com")
            ),
            body=None,
        )

    monkeypatch.setattr("gptme.llm._chat_complete", complete)
    with pytest.raises(NotFoundError):
        complete_summary([], "gemini/main")
    assert len(calls) == 1


def test_auto_naming_uses_summary_override(monkeypatch) -> None:
    from gptme.util.auto_naming import _generate_llm_name

    _configure(monkeypatch, "gemini/selected-summary")
    calls = []

    def complete(messages, model, tools):
        calls.append(model)
        return "Python debugging", None

    monkeypatch.setattr("gptme.llm._chat_complete", complete)
    result = _generate_llm_name(
        [
            Message(
                "user",
                "Help me debug a Python script that fails to parse my configuration file.",
            )
        ],
        "gemini/main",
    )
    assert result == "Python debugging"
    assert calls == ["gemini/selected-summary"]


def test_provider_only_main_is_not_retried(monkeypatch) -> None:
    _configure(monkeypatch, None)
    calls = []

    def complete(*args):
        calls.append(args)
        raise NotFoundError(
            "missing",
            response=httpx.Response(
                404, request=httpx.Request("POST", "https://example.com")
            ),
            body=None,
        )

    monkeypatch.setattr("gptme.llm._chat_complete", complete)
    with pytest.raises(NotFoundError):
        complete_summary([], "gemini")
    assert len(calls) == 1


def test_default_summary_resolver_honors_override(monkeypatch) -> None:
    from gptme.llm.models import get_default_model_summary, set_default_model
    from gptme.llm.models.resolution import _default_model_var

    token = _default_model_var.set(None)
    try:
        _configure(monkeypatch, "openai/gpt-5-mini")
        set_default_model("gemini")
        summary = get_default_model_summary()
        assert summary is not None
        assert summary.full == "openai/gpt-5-mini"
    finally:
        _default_model_var.reset(token)


def test_active_gemini_examples_do_not_reference_deprecated_models() -> None:
    from pathlib import Path

    from gptme.llm.models import MODELS

    docs = Path(__file__).resolve().parents[1] / "docs"
    retired = [
        name for name, meta in MODELS["gemini"].items() if meta.get("deprecated")
    ]
    for name in ("config.rst", "providers.rst", "getting-started.rst", "models.rst"):
        text = (docs / name).read_text()
        for model in retired:
            assert model not in text, f"{name} recommends deprecated {model}"


@pytest.mark.parametrize("provider", ["anthropic", "subscription"])
@pytest.mark.parametrize("status", [404, 401, 429, 500])
def test_other_provider_error_policy(monkeypatch, provider: str, status: int) -> None:
    import anthropic
    import requests

    _configure(monkeypatch, "gemini/unavailable")
    if provider == "anthropic":
        error = anthropic.APIStatusError(
            "failed",
            response=httpx.Response(
                status, request=httpx.Request("POST", "https://example.com")
            ),
            body=None,
        )
        if status == 404:
            error = anthropic.NotFoundError(
                "missing", response=error.response, body=None
            )
    else:
        response = requests.Response()
        response.status_code = status
        error = requests.HTTPError("failed", response=response)
    calls = []

    def complete(messages, model, tools):
        calls.append(model)
        if len(calls) == 1:
            raise error
        return "Recovered title", None

    monkeypatch.setattr("gptme.llm._chat_complete", complete)
    if status == 404:
        assert complete_summary([], "gemini/main") == "Recovered title"
        assert len(calls) == 2
    else:
        with pytest.raises(type(error)):
            complete_summary([], "gemini/main")
        assert len(calls) == 1


def test_anthropic_summary_initializes_client(monkeypatch) -> None:
    from gptme.llm import llm_anthropic

    class Initialized(Exception):
        pass

    def initialize(config) -> None:
        raise Initialized

    monkeypatch.setattr(llm_anthropic, "_anthropic", None)
    monkeypatch.setattr(llm_anthropic, "init", initialize)
    _configure(monkeypatch, "anthropic/claude-haiku-4-5")
    # Execute the real dispatch and adapter path, stopping at initialization
    # rather than sending a network request or relying on existing credentials.
    with pytest.raises(Initialized):
        complete_summary([], "gemini/main")


def test_same_model_summary_preserves_active_metadata(monkeypatch) -> None:
    from gptme.llm.models import ModelMeta, get_default_model_summary
    from gptme.llm.models.resolution import _default_model_var

    meta = ModelMeta(provider="local", model="custom", context=8192)
    token = _default_model_var.set(meta)
    try:
        for override in (None, meta.full):
            _configure(monkeypatch, override)
            assert get_default_model_summary() is meta
    finally:
        _default_model_var.reset(token)
