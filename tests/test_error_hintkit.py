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

    assert rendered == "ANTHROPIC_API_KEY is missing"
    assert "hint:" not in rendered


def test_format_error_unmatched_preserves_str(monkeypatch) -> None:
    """No matching hint → historical str(exc), no Type: prefix."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)

    rendered = error_hintkit.format_error(
        ValueError("Codex API error 429: usage_limit_reached"), color=False
    )

    assert rendered == "Codex API error 429: usage_limit_reached"
    assert "hint:" not in rendered


def test_install_excepthook_writes_hint(monkeypatch) -> None:
    """Custom previous hook owns rendering; HintKit only appends the hint."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)
    stream = io.StringIO()
    previous_out = io.StringIO()

    def custom_hook(exc_type, exc, tb):
        print(f"{exc_type.__name__}: {exc}", file=previous_out)

    original = sys.excepthook
    try:
        # Pytest replaces sys.excepthook; pin a custom renderer so this
        # proves HintKit does not reprint Type: message beside it.
        monkeypatch.setattr(sys, "excepthook", custom_hook)
        error_hintkit.install_excepthook(stream=stream)
        sys.excepthook(RuntimeError, RuntimeError("Connection refused"), None)
    finally:
        sys.excepthook = original

    output = stream.getvalue()
    assert "hint:" in output
    assert "gptme-server run" in output
    assert "RuntimeError: Connection refused" not in output
    assert previous_out.getvalue().count("RuntimeError: Connection refused") == 1


def test_install_excepthook_chains_custom_previous_hook(monkeypatch) -> None:
    """A pre-existing custom sys.excepthook must be called for non-KeyboardInterrupt exceptions."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)
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

    # Unmatched error: HintKit writes nothing; the custom hook still runs.
    assert stream.getvalue() == ""
    assert len(calls) == 1
    assert calls[0][0] is ValueError


def test_install_excepthook_replace_in_place_does_not_stack(monkeypatch) -> None:
    """Repeated install must not wrap the previous HintKit hook."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)
    first = io.StringIO()
    second = io.StringIO()
    calls: list[int] = []

    def custom_hook(exc_type, exc, tb):
        calls.append(1)

    original = sys.excepthook
    try:
        monkeypatch.setattr(sys, "excepthook", custom_hook)
        error_hintkit.install_excepthook(stream=first)
        error_hintkit.install_excepthook(stream=second)
        sys.excepthook(RuntimeError, RuntimeError("Connection refused"), None)
    finally:
        sys.excepthook = original

    assert first.getvalue() == ""
    assert "hint:" in second.getvalue()
    assert second.getvalue().count("hint:") == 1
    assert "Connection refused" not in second.getvalue()
    assert calls == [1]


def test_install_excepthook_delegates_to_default_hook(monkeypatch) -> None:
    """Default sys.__excepthook__ must still run so the traceback is preserved."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)
    stream = io.StringIO()
    calls: list[type] = []

    def default_hook(exc_type, exc, tb):
        calls.append(exc_type)

    original = sys.excepthook
    try:
        monkeypatch.setattr(sys, "__excepthook__", default_hook)
        monkeypatch.setattr(sys, "excepthook", default_hook)
        assert sys.excepthook is sys.__excepthook__
        error_hintkit.install_excepthook(stream=stream)
        sys.excepthook(RuntimeError, RuntimeError("Connection refused"), None)
    finally:
        sys.excepthook = original

    assert "hint:" in stream.getvalue()
    assert "RuntimeError: Connection refused" not in stream.getvalue()
    assert calls == [RuntimeError]


def test_install_excepthook_does_not_duplicate_default_header(monkeypatch) -> None:
    """Chaining to sys.__excepthook__ must not reprint Type: message on our stream."""
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)
    stream = io.StringIO()
    previous_out = io.StringIO()

    def default_hook(exc_type, exc, tb):
        print(f"{exc_type.__name__}: {exc}", file=previous_out)

    original = sys.excepthook
    try:
        monkeypatch.setattr(sys, "__excepthook__", default_hook)
        monkeypatch.setattr(sys, "excepthook", default_hook)
        assert sys.excepthook is sys.__excepthook__
        error_hintkit.install_excepthook(stream=stream)
        sys.excepthook(RuntimeError, RuntimeError("Connection refused"), None)
    finally:
        sys.excepthook = original

    assert "hint:" in stream.getvalue()
    assert "RuntimeError: Connection refused" not in stream.getvalue()
    assert previous_out.getvalue().count("RuntimeError: Connection refused") == 1


def test_uninstall_excepthook_restores_previous(monkeypatch) -> None:
    monkeypatch.delenv("HINTKIT_ENABLED", raising=False)

    def custom_hook(exc_type, exc, tb):
        pass

    original = sys.excepthook
    try:
        monkeypatch.setattr(sys, "excepthook", custom_hook)
        error_hintkit.install_excepthook(stream=io.StringIO())
        assert sys.excepthook is not custom_hook
        error_hintkit.uninstall_excepthook()
        assert sys.excepthook is custom_hook
    finally:
        sys.excepthook = original
