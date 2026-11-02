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
