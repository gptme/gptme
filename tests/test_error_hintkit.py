from __future__ import annotations

import io
import sys

from gptme import error_hintkit


def test_hint_for_exception_matches_seed_error() -> None:
    hint = error_hintkit.hint_for_exception(
        RuntimeError("tool 'read' is disabled by default")
    )

    assert hint is not None
    assert hint.id == "read_tool_disabled"


def test_format_error_renders_compact_hint(monkeypatch) -> None:
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)

    rendered = error_hintkit.format_error(
        RuntimeError("ANTHROPIC_API_KEY is missing"), color=False
    )

    assert "RuntimeError: ANTHROPIC_API_KEY is missing" in rendered
    assert "hint:" in rendered
    assert "export ANTHROPIC_API_KEY" in rendered
    assert (
        "docs: https://gptme.org/docs/providers.html#configuring-credentials"
        in rendered
    )


def test_format_error_honors_disabled_flag(monkeypatch) -> None:
    monkeypatch.setenv("HINTKIT_ENABLED", "false")

    rendered = error_hintkit.format_error(
        RuntimeError("ANTHROPIC_API_KEY is missing"), color=False
    )

    assert "hint:" not in rendered


def test_install_excepthook_writes_hint(monkeypatch) -> None:
    stream = io.StringIO()
    previous = sys.excepthook
    try:
        error_hintkit.install_excepthook(stream=stream)
        sys.excepthook(RuntimeError, RuntimeError("Connection refused"), None)
    finally:
        sys.excepthook = previous

    output = stream.getvalue()
    assert "RuntimeError: Connection refused" in output
    assert "hint:" in output
    assert "gptme-server run" in output


def test_install_excepthook_chains_custom_previous_hook(monkeypatch) -> None:
    """A pre-existing custom sys.excepthook must be called for non-KeyboardInterrupt exceptions."""
    stream = io.StringIO()
    calls: list[tuple] = []

    def custom_hook(exc_type, exc, tb):
        calls.append((exc_type, exc))

    original = sys.excepthook
    try:
        monkeypatch.setattr(sys, "excepthook", custom_hook)
        # Sanity: our hook is now the custom one, not sys.__excepthook__
        assert sys.excepthook is custom_hook
        error_hintkit.install_excepthook(stream=stream)
        sys.excepthook(ValueError, ValueError("boom"), None)
    finally:
        sys.excepthook = original

    # HintKit printed its output
    assert "ValueError: boom" in stream.getvalue()
    # And the custom hook was also called
    assert len(calls) == 1
    assert calls[0][0] is ValueError
