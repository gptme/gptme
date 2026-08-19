"""Tests for non-interactive fatal error envelope and exit taxonomy.

When gptme --non-interactive encounters a fatal LLM startup error, it should:
1. Append a terminal error event to conversation.jsonl
2. Exit with a class-specific code (75/76/77/1)
"""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli

# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner(tmp_data_dir: Path) -> CliRunner:
    return CliRunner()


def _fake_config(tmp_path: Path) -> Any:
    return SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=["shell", "read"],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=tmp_path,
            stream=False,
            agent=None,
            gear=None,
            no_confirm=True,
        ),
        project=None,
    )


def _setup_cli_mocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, chat_raises: Exception
) -> None:
    """Patch the minimal set of dependencies so the CLI reaches the chat() call."""
    config = _fake_config(tmp_path)
    monkeypatch.setattr("gptme.config.setup_config_from_cli", lambda **_: config)
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **_: None)
    monkeypatch.setattr("gptme.telemetry.shutdown_telemetry", lambda: None)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise chat_raises

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", _raise)


# ---------------------------------------------------------------------------
# Unit tests for _classify_fatal_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_class", "expected_code"),
    [
        # Rate limit via ValueError from subscription LLM (the concrete production case)
        (
            ValueError("Codex API error 429: usage_limit_reached"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Rate limit via openai-style exception type name
        (
            type("RateLimitError", (Exception,), {})("too many requests"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Auth error via type name
        (
            type("AuthenticationError", (Exception,), {})("invalid key"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Auth error via 401 in message
        (
            ValueError("Codex API error 401: unauthorized"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Model unavailable via 503 in message
        (
            ValueError("Codex API error 503: service unavailable"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # Rate limit via bare "429 Too Many Requests" (no keyword suffix)
        (
            ValueError("429 Too Many Requests"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Auth error via bare "401 Client Error: Unauthorized" (no "api error" phrase)
        (
            ValueError(
                "401 Client Error: Unauthorized for url: https://api.example.com"
            ),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Model unavailable via SDK-native exception type (openai/anthropic style)
        (
            type("NotFoundError", (Exception,), {})("model not found"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # ServiceUnavailableError (503) maps to model_unavailable
        (
            type("ServiceUnavailableError", (Exception,), {})("service down"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # InternalServerError (HTTP 500/502/504) falls through to generic exit 1
        (
            type("InternalServerError", (Exception,), {})("upstream 500"),
            "generic",
            1,
        ),
        # Generic fallback
        (
            RuntimeError("something unexpected"),
            "generic",
            1,
        ),
    ],
)
def test_classify_fatal_error(
    exc: Exception, expected_class: str, expected_code: int
) -> None:
    error_class, exit_code = cli._classify_fatal_error(exc)
    assert error_class == expected_class
    assert exit_code == expected_code


# ---------------------------------------------------------------------------
# Unit tests for _write_terminal_error_to_log
# ---------------------------------------------------------------------------


def test_write_terminal_error_creates_jsonl_event(tmp_path: Path) -> None:
    """_write_terminal_error_to_log appends a valid JSON event to conversation.jsonl."""
    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"

    cli._write_terminal_error_to_log(logdir, "rate_limit", 75, "quota exhausted")

    assert log_file.exists()
    event = json.loads(log_file.read_text().strip())
    assert event["role"] == "system"
    assert "rate_limit" in event["content"]
    assert "quota exhausted" in event["content"]
    assert event["metadata"]["error"] is True
    assert event["metadata"]["error_class"] == "rate_limit"
    assert event["metadata"]["exit_code"] == 75


def test_write_terminal_error_appends_to_existing_log(tmp_path: Path) -> None:
    """_write_terminal_error_to_log appends without truncating existing content."""
    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    log_file.write_text('{"role":"user","content":"ping"}\n')

    cli._write_terminal_error_to_log(logdir, "auth_error", 76, "bad token")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    user_event = json.loads(lines[0])
    assert user_event["role"] == "user"
    err_event = json.loads(lines[1])
    assert err_event["metadata"]["error_class"] == "auth_error"


def test_write_terminal_error_missing_logdir_is_silent(tmp_path: Path) -> None:
    """_write_terminal_error_to_log does not raise if logdir doesn't exist."""
    logdir = tmp_path / "nonexistent"
    cli._write_terminal_error_to_log(logdir, "generic", 1, "boom")
    # Should complete without raising


# ---------------------------------------------------------------------------
# Integration: CLI exit codes and conversation log
# ---------------------------------------------------------------------------


def test_rate_limit_exits_75_and_writes_error_event(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """A 429 usage_limit_reached exception → exit 75 + terminal error in conversation.jsonl."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=ValueError("Codex API error 429: usage_limit_reached"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-rate-limit", "ping"]
    )

    assert result.exit_code == cli.EXIT_RATE_LIMIT, result.output

    # The conversation log should contain the terminal error event
    log_file = cli.get_logs_dir() / "test-rate-limit" / "conversation.jsonl"
    assert log_file.exists(), f"conversation.jsonl not found at {log_file}"
    events = [
        json.loads(line) for line in log_file.read_text().strip().splitlines() if line
    ]
    error_events = [e for e in events if e.get("metadata", {}).get("error")]
    assert error_events, "No error event written to conversation.jsonl"
    assert error_events[-1]["metadata"]["error_class"] == "rate_limit"
    assert error_events[-1]["metadata"]["exit_code"] == cli.EXIT_RATE_LIMIT


def test_auth_error_exits_76(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """A 401 error → exit 76."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=ValueError("Codex API error 401: unauthorized"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-auth-err", "ping"]
    )
    assert result.exit_code == cli.EXIT_AUTH_ERROR


def test_generic_error_exits_1(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """An unclassified exception still exits 1 (backward-compatible default)."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=RuntimeError("unexpected internal failure"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-generic-err", "ping"]
    )
    assert result.exit_code == 1
