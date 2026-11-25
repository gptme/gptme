"""Default non-interactive tools are not an operator restriction."""

import pytest


@pytest.mark.parametrize("resume", [False, True])
def test_default_noninteractive_session_remains_unrestricted(tmp_path, resume):
    from gptme.config import setup_config_from_cli
    from gptme.tools import clear_tools, get_session_allowlist, get_tools, init_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "chat"
    config = setup_config_from_cli(
        workspace=workspace, logdir=logdir, interactive=False
    )
    if resume:
        config = setup_config_from_cli(
            workspace=workspace, logdir=logdir, interactive=False
        )
    assert config.chat is not None
    clear_tools()
    init_tools(config.chat.tools, include_mcp=False)
    assert "complete" in {tool.name for tool in get_tools()}
    assert get_session_allowlist() is None, "default tools must not block ACP"
    assert config.chat.tools is None, "do not persist defaults as operator grants"


@pytest.mark.parametrize("selection", ["ipython", "read-only"])
def test_explicit_noninteractive_tools_remain_restricted(tmp_path, selection):
    from gptme.config import setup_config_from_cli
    from gptme.tools import clear_tools, get_session_allowlist, init_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = setup_config_from_cli(
        workspace=workspace,
        logdir=tmp_path / "chat",
        interactive=False,
        tool_allowlist=selection,
    )
    assert config.chat is not None
    clear_tools()
    init_tools(config.chat.tools, include_mcp=False)
    boundary = get_session_allowlist()
    assert boundary is not None
    assert "shell" not in boundary


@pytest.mark.parametrize("continuation", [False, True])
def test_default_noninteractive_parent_can_launch_and_continue_acp(
    tmp_path, monkeypatch, continuation
):
    from unittest.mock import AsyncMock, MagicMock

    from gptme.config import setup_config_from_cli
    from gptme.tools import clear_tools, init_tools
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import Subagent, _subagents

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = setup_config_from_cli(
        workspace=workspace, logdir=tmp_path / "parent", interactive=False
    )
    assert config.chat is not None
    clear_tools()
    init_tools(config.chat.tools, include_mcp=False)
    monkeypatch.setattr(api, "notify_completion", lambda *a, **kw: None)
    client = AsyncMock()
    client.run.return_value = MagicMock(stop_reason="end_turn")
    client.prompt.return_value = MagicMock(stop_reason="end_turn")
    client.__aenter__.return_value = client
    monkeypatch.setattr("gptme.acp.client.GptmeAcpClient", lambda **kw: client)
    agent_id = "default-cli-acp"
    if continuation:
        child = Subagent(
            agent_id,
            "task",
            None,
            tmp_path / "child",
            None,
            use_acp=True,
            execution_mode="acp",
            acp_session_id="saved-session",
            workdir=workspace,
        )
        child.logdir.mkdir()
        _subagents.append(child)
        api.subagent_continue(agent_id, "continue")
    else:
        api.subagent(agent_id, "task", use_acp=True, workdir=workspace)
    child = next(s for s in reversed(_subagents) if s.agent_id == agent_id)
    assert child.thread is not None
    child.thread.join(timeout=10)
    assert not child.thread.is_alive()
    assert child.status().status == "success"
    if continuation:
        client.load_session.assert_awaited_once_with("saved-session", cwd=workspace)
        client.prompt.assert_awaited_once_with("saved-session", "continue")
    else:
        client.run.assert_awaited_once()


@pytest.mark.parametrize("source", ["env", "saved"])
def test_configured_limits_are_not_mistaken_for_defaults(tmp_path, monkeypatch, source):
    from gptme.config import ChatConfig, setup_config_from_cli
    from gptme.tools import clear_tools, get_session_allowlist, init_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "parent"
    if source == "env":
        monkeypatch.setenv("GPTME_TOOL_ALLOWLIST", "ipython")
    else:
        logdir.mkdir()
        ChatConfig(_logdir=logdir, workspace=workspace, tools=["ipython"]).save()
    config = setup_config_from_cli(
        workspace=workspace, logdir=logdir, interactive=False
    )
    assert config.chat is not None
    clear_tools()
    init_tools(config.chat.tools, include_mcp=False)
    boundary = get_session_allowlist()
    assert boundary is not None
    assert "ipython" in boundary
    assert "shell" not in boundary


def test_interactive_frontend_resuming_autonomous_session_skips_complete(tmp_path):
    """Greptile P1: a saved `interactive=False` must not enable `complete` in the TUI.

    The TUI always resumes with ``interactive=True``, but the persisted chat
    config still describes the autonomous run that created the conversation.
    """
    from gptme.config import setup_config_from_cli
    from gptme.tools import clear_tools, get_tools, init_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "chat"
    setup_config_from_cli(workspace=workspace, logdir=logdir, interactive=False)
    config = setup_config_from_cli(workspace=workspace, logdir=logdir, interactive=True)
    assert config.chat is not None
    # Premise: the stale value survives the resume (it equals the field default,
    # so the CLI value is not treated as an override).
    assert config.chat.interactive is False

    clear_tools()
    init_tools(config.chat.tools, include_mcp=False, interactive=True)
    assert "complete" not in {tool.name for tool in get_tools()}, (
        "an interactive frontend must not load the turn-ending 'complete' tool "
        f"from a saved autonomous config; got {sorted(t.name for t in get_tools())}"
    )
