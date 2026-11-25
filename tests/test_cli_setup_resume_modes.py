"""Resume must not inherit per-run modes, and must keep the non-interactive tools.

- A conversation started with --non-interactive saved interactive=False and
  no_confirm=True; a plain interactive resume kept both and auto-confirmed
  every tool. --stream likewise could not undo a saved --no-stream.
- --non-interactive --resume of a conversation with saved tools appended
  'complete' to a throwaway list, so the run had no way to finish.
"""

import os

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
            "TOOL_ALLOWLIST",
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
