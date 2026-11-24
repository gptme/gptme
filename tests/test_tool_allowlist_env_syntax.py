"""TOOL_ALLOWLIST from env/[env] is documented alongside --tools (docs/tools.rst
'Pass ... to --tools (CLI) or set the TOOL_ALLOWLIST environment variable',
followed by +/-/none examples) but cli_setup only does a plain split."""

import pytest

from gptme.config import setup_config_from_cli


@pytest.mark.parametrize(
    ("value", "check"),
    [
        ("-shell", lambda tools: "shell" not in tools and "save" in tools),
        # every exclusion carries its own '-', as in --tools=-shell,-computer
        (
            "-save,-shell",
            lambda tools: "shell" not in tools and "save" not in tools,
        ),
        (
            "-save, -shell",
            lambda tools: "shell" not in tools and "save" not in tools,
        ),
        ("+browser,+rag", lambda tools: "browser" in tools and "shell" in tools),
        ("+browser", lambda tools: "browser" in tools and "shell" in tools),
        ("none", lambda tools: tools == []),
    ],
)
def test_tool_allowlist_env_supports_cli_syntax(tmp_path, monkeypatch, value, check):
    monkeypatch.setenv("TOOL_ALLOWLIST", value)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "logs"
    logdir.mkdir()

    config = setup_config_from_cli(
        workspace=workspace,
        logdir=logdir,
        model=None,
        tool_allowlist=None,
        tool_format=None,
        stream=True,
        interactive=True,
        agent_path=None,
    )
    assert config.chat is not None
    assert check(config.chat.tools or []), config.chat.tools


def test_tool_allowlist_env_rejects_mixed_syntax(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWLIST", "-shell,save")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="TOOL_ALLOWLIST"):
        setup_config_from_cli(workspace=workspace, logdir=tmp_path / "logs")
