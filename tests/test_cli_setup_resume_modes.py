"""Resume must not inherit per-run modes, and must keep the non-interactive tools.

- A conversation started with --non-interactive saved interactive=False and
  no_confirm=True; a plain interactive resume kept both and auto-confirmed
  every tool. --stream likewise could not undo a saved --no-stream.
- --non-interactive --resume of a conversation with saved tools appended
  'complete' to a throwaway list, so the run had no way to finish.
"""

import os
from pathlib import Path

import pytest

from gptme.config import ChatConfig, setup_config_from_cli


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k in (
            "MODEL",
            "GPTME_MODEL",
            "TOOL_FORMAT",
            "GPTME_TOOL_FORMAT",
            "TOOL_ALLOWLIST",
            "GPTME_TOOL_ALLOWLIST",
            "GPTME_CONTEXT_LENGTH",
        ):
            monkeypatch.delenv(k, raising=False)
    import gptme.config as config_module
    import gptme.config.user as user_module
    from gptme.config.core import _config_var

    cfg = home / ".config" / "gptme" / "config.toml"
    monkeypatch.setattr(user_module, "config_path", str(cfg))
    monkeypatch.setattr(config_module, "config_path", str(cfg))
    token = _config_var.set(None)
    cwd = os.getcwd()
    try:
        yield
    finally:
        _config_var.reset(token)
        os.chdir(cwd)


def test_interactive_resume_after_noninteractive_run(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"

    # gptme --non-interactive --name conv "do x"  (main forces no_confirm=True)
    setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        model="openai/gpt-4o",
        interactive=False,
        no_confirm=True,
    )

    # gptme --resume --name conv   (interactive, no -y): main passes
    # interactive=True and `no_confirm or None` == None
    cfg = setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        interactive=True,
        no_confirm=None,
    )
    assert cfg.chat is not None
    # main() passes config.chat.interactive / config.chat.no_confirm to chat()
    assert cfg.chat.interactive is True, "interactive resume runs non-interactively"


def test_no_confirm_sticks_after_noninteractive_run(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        model="openai/gpt-4o",
        interactive=False,
        no_confirm=True,
    )
    cfg = setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        interactive=True,
        no_confirm=None,
    )
    assert cfg.chat is not None
    assert cfg.chat.no_confirm is not True, "interactive resume auto-confirms all tools"


def test_stream_cannot_be_reenabled_on_resume(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    # gptme --no-stream --name conv "hi"
    setup_config_from_cli(
        workspace=ws, logdir=logdir, model="openai/gpt-4o", stream=False
    )
    # gptme --resume --name conv --stream
    cfg = setup_config_from_cli(workspace=ws, logdir=logdir, stream=True)
    assert cfg.chat is not None
    assert cfg.chat.stream is True


def test_noninteractive_resume_adds_complete(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    # interactive: gptme --name conv -t shell,save "hi"
    setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        model="openai/gpt-4o",
        tool_allowlist="shell,save",
        interactive=True,
    )
    assert ChatConfig.from_logdir(logdir).tools == ["shell", "save"]

    # gptme --non-interactive --resume --name conv "continue"
    cfg = setup_config_from_cli(
        workspace=ws, logdir=logdir, interactive=False, no_confirm=True
    )
    assert cfg.chat is not None
    assert "complete" in (cfg.chat.tools or [])


def test_control_new_noninteractive_gets_complete(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = setup_config_from_cli(
        workspace=ws,
        logdir=tmp_path / "logs" / "n",
        model="openai/gpt-4o",
        tool_allowlist="shell,save",
        interactive=False,
        no_confirm=True,
    )
    assert cfg.chat is not None
    assert "complete" in (cfg.chat.tools or [])


def test_noninteractive_resume_keeps_tools_none(tmp_path, monkeypatch):
    """A saved `tools = []` (--tools none) is a choice, not "unset": a
    non-interactive resume must not fall back to TOOL_ALLOWLIST/defaults."""
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    # interactive: gptme --tools none --name conv "hi"
    setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        model="openai/gpt-4o",
        tool_allowlist="",
        interactive=True,
    )
    assert ChatConfig.from_logdir(logdir).tools == []

    monkeypatch.setenv("TOOL_ALLOWLIST", "shell,save")
    cfg = setup_config_from_cli(
        workspace=ws, logdir=logdir, interactive=False, no_confirm=True
    )
    assert cfg.chat is not None
    # Same as a fresh `--tools none --non-interactive` run: only 'complete'.
    assert cfg.chat.tools == ["complete"]
    assert ChatConfig.from_logdir(logdir).tools == []

    # The run-only completion tool must not widen later interactive resumes.
    resumed = setup_config_from_cli(workspace=ws, logdir=logdir, interactive=True)
    assert resumed.chat is not None
    assert resumed.chat.tools == []


def test_noninteractive_resume_keeps_saved_hint_filter(tmp_path):
    """Adding 'complete' on resume must not re-normalize the saved filters:
    that expands hint:/glob entries and drops ones that match nothing now."""
    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    setup_config_from_cli(
        workspace=ws, logdir=logdir, model="openai/gpt-4o", interactive=True
    )
    saved = ChatConfig.from_logdir(logdir)
    saved.tools = ["shell", "hint:no-such-hint"]
    saved.save()

    cfg = setup_config_from_cli(
        workspace=ws, logdir=logdir, interactive=False, no_confirm=True
    )
    assert cfg.chat is not None
    assert cfg.chat.tools == ["shell", "hint:no-such-hint", "complete"]
    assert ChatConfig.from_logdir(logdir).tools == ["shell", "hint:no-such-hint"]


@pytest.mark.parametrize("interactive", [True, False])
def test_implicit_default_tools_remain_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interactive: bool
) -> None:
    """Implicit defaults stay dynamic on resume, including new plugin tools
    and tools whose optional dependencies have disappeared."""
    from gptme.tools import get_toolchain
    from gptme.tools.base import ToolSpec

    ws = tmp_path / "ws"
    ws.mkdir()
    logdir = tmp_path / "logs" / "conv"
    initial = setup_config_from_cli(
        workspace=ws,
        logdir=logdir,
        model="openai/gpt-4o",
        interactive=interactive,
    )
    assert initial.chat is not None
    # Implicit defaults stay None in memory and on disk; tool initialization
    # adds 'complete' without turning the default into an operator boundary
    # (which would disable ACP delegation).
    assert initial.chat.tools is None
    assert ChatConfig.from_logdir(logdir).tools is None

    # Resume after discovery changes: the new plugin is usable and names of
    # optional tools from the first run no longer become hard requirements.
    plugin = ToolSpec(name="new_plugin", desc="Newly installed plugin")
    monkeypatch.setattr("gptme.tools.get_available_tools", lambda **_: [plugin])
    cfg = setup_config_from_cli(workspace=ws, logdir=logdir, interactive=True)
    assert cfg.chat is not None
    assert cfg.chat.tools is None
    assert get_toolchain(cfg.chat.tools) == [plugin]
    assert ChatConfig.from_logdir(logdir).tools is None


def test_interactive_default_setup_does_not_discover_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TUI plugin discovery happens after config setup, so setup cannot cache
    the tool inventory prematurely when no allowlist was requested."""

    def unexpected_discovery(*args: object, **kwargs: object) -> None:
        pytest.fail("implicit defaults must be discovered during tool initialization")

    monkeypatch.setattr("gptme.config.cli_setup.get_toolchain", unexpected_discovery)
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = setup_config_from_cli(workspace=ws, logdir=tmp_path / "logs")
    assert cfg.chat is not None
    assert cfg.chat.tools is None


def test_interactive_resume_keeps_saved_modes_on_disk(tmp_path):
    """A resume applies this run's modes in memory without rewriting the record."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "logs"
    setup_config_from_cli(
        workspace=workspace,
        logdir=logdir,
        interactive=False,
        no_confirm=True,
        stream=False,
    )
    cfg = setup_config_from_cli(workspace=workspace, logdir=logdir, interactive=True)
    assert cfg.chat is not None
    # This run is interactive, so it must not auto-confirm tools...
    assert cfg.chat.interactive is True
    assert cfg.chat.no_confirm is None
    # ...and an unspecified --stream keeps the saved value.
    assert cfg.chat.stream is False
    # ...but the conversation's saved run modes are unchanged.
    saved = ChatConfig.from_logdir(logdir)
    assert saved.no_confirm is True
    assert saved.interactive is False
    assert saved.stream is False

    # An explicit flag wins in the other direction too.
    cfg = setup_config_from_cli(
        workspace=workspace, logdir=logdir, interactive=True, stream=True
    )
    assert cfg.chat is not None
    assert cfg.chat.stream is True
    saved = ChatConfig.from_logdir(logdir)
    assert saved.stream is False


def test_noninteractive_preset_resume_keeps_boundary_without_complete(tmp_path):
    """A saved preset is an exclusive boundary; resume must not widen it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "logs"
    setup_config_from_cli(
        workspace=workspace,
        logdir=logdir,
        tool_allowlist="read-only",
        interactive=False,
    )
    assert ChatConfig.from_logdir(logdir).tools == ["read-only"]

    cfg = setup_config_from_cli(workspace=workspace, logdir=logdir, interactive=False)
    assert cfg.chat is not None
    assert cfg.chat.tools == ["read-only"]
    assert ChatConfig.from_logdir(logdir).tools == ["read-only"]
