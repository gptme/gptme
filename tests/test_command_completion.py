"""Tests for command argument completion."""

from gptme.commands import (
    _complete_delete,
    _complete_log,
    _complete_model,
    _complete_plugin,
    _complete_rename,
    _complete_replay,
    _complete_tools,
    get_command_completer,
)
from gptme.tools import clear_tools, init_tools


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


def test_tools_completer_subcommands():
    """Test tools completer returns subcommands and tool names."""
    completions = _complete_tools("", [])
    names = [c[0] for c in completions]
    # Should include the 'load' subcommand
    assert "load" in names
    # Should also include tool names
    assert "save" in names


def test_tools_completer_filter():
    """Test tools completer filters by prefix."""
    completions = _complete_tools("lo", [])
    names = [c[0] for c in completions]
    assert "load" in names
    # Other subcommands/tools not starting with "lo" should be excluded
    assert "save" not in names


def test_tools_completer_load_subcommand():
    """Test tools completer after 'load' suggests unloaded tools."""
    clear_tools()
    init_tools(allowlist=["save"])

    completions = _complete_tools("", ["load"])
    names = [c[0] for c in completions]
    # 'save' is already loaded, shouldn't appear
    assert "save" not in names
    # 'patch' is not loaded, should appear
    assert "patch" in names


def test_tools_completer_load_filter():
    """Test tools completer after 'load' filters by prefix."""
    clear_tools()
    init_tools(allowlist=["save"])

    completions = _complete_tools("pa", ["load"])
    names = [c[0] for c in completions]
    assert "patch" in names
    assert "append" not in names  # doesn't start with "pa"


def test_get_command_completer():
    """Test that registered completers can be retrieved."""
    assert get_command_completer("model") is not None
    assert get_command_completer("log") is not None
    assert get_command_completer("delete") is not None
    assert get_command_completer("rename") is not None
    assert get_command_completer("replay") is not None
    assert get_command_completer("plugin") is not None
    assert get_command_completer("tools") is not None

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
        (_complete_tools, "", []),
        (_complete_tools, "", ["load"]),
    ]

    for completer, partial, prev_args in completers:
        results = completer(partial, prev_args)
        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], str)
