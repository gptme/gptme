"""Tests for command argument completion."""

from gptme.commands import (
    _complete_delete,
    _complete_log,
    _complete_model,
    _complete_plugin,
    _complete_rename,
    _complete_replay,
    get_command_completer,
)


def test_model_completer_provider_prefix():
    """Test that model completer returns provider prefixes."""
    completions = _complete_model("", [])
    # Should include provider prefixes like "openai/", "anthropic/"
    providers = [c[0] for c in completions]
    assert any(p.startswith("openai/") or p == "openai/" for p in providers)
    assert any(p.startswith("anthropic/") or p == "anthropic/" for p in providers)


def test_model_completer_with_provider():
    """Test that model completer returns models for a provider."""
    completions = _complete_model("anthropic/", [])
    # Should include anthropic models
    models = [c[0] for c in completions]
    assert all(m.startswith("anthropic/") for m in models)
    assert any("claude" in m for m in models)


def test_model_completer_partial_model():
    """Test that model completer matches partial model names."""
    completions = _complete_model("anthropic/claude", [])
    models = [c[0] for c in completions]
    assert all("claude" in m for m in models)


def test_log_completer():
    """Test log command completer."""
    completions = _complete_log("--", [])
    flags = [c[0] for c in completions]
    assert "--hidden" in flags


def test_rename_completer():
    """Test rename command completer."""
    completions = _complete_rename("", [])
    options = [c[0] for c in completions]
    assert "auto" in options


def test_replay_completer():
    """Test replay command completer."""
    completions = _complete_replay("", [])
    options = [c[0] for c in completions]
    assert "last" in options
    assert "all" in options


def test_delete_completer_flags():
    """Test delete command completer returns flags."""
    completions = _complete_delete("-", [])
    flags = [c[0] for c in completions]
    assert "--force" in flags or "-f" in flags


def test_plugin_completer_subcommands():
    """Test plugin command completer returns subcommands."""
    completions = _complete_plugin("", [])
    subcommands = [c[0] for c in completions]
    assert "list" in subcommands
    assert "info" in subcommands


def test_get_command_completer():
    """Test that registered completers can be retrieved."""
    assert get_command_completer("model") is not None
    assert get_command_completer("log") is not None
    assert get_command_completer("delete") is not None
    assert get_command_completer("rename") is not None
    assert get_command_completer("replay") is not None
    assert get_command_completer("plugin") is not None

    # Commands without completers
    assert get_command_completer("exit") is None
    assert get_command_completer("help") is None


def test_completer_returns_tuples():
    """Test that all completers return list of (str, str) tuples."""
    completers: list[tuple] = [
        (_complete_model, "openai/", []),
        (_complete_log, "", []),
        (_complete_rename, "", []),
        (_complete_replay, "", []),
        (_complete_delete, "", []),
        (_complete_plugin, "", []),
    ]

    for completer, partial, prev_args in completers:
        results = completer(partial, prev_args)
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)
