"""TOOL_ALLOWLIST from env/[env] is documented alongside --tools (docs/tools.rst
'Pass ... to --tools (CLI) or set the TOOL_ALLOWLIST environment variable',
followed by +/-/none examples) but cli_setup only does a plain split."""

import pytest

from gptme.config import setup_config_from_cli


@pytest.fixture(autouse=True)
def isolate_tool_env(monkeypatch):
    for key in (
        "TOOL_ALLOWLIST",
        "GPTME_TOOL_ALLOWLIST",
        "TOOL_FORMAT",
        "GPTME_TOOL_FORMAT",
    ):
        monkeypatch.delenv(key, raising=False)


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
        # use opt-in tools with no availability check (browser/rag need extras)
        (
            "+form,+choice",
            lambda tools: "form" in tools and "choice" in tools and "shell" in tools,
        ),
        ("+form", lambda tools: "form" in tools and "shell" in tools),
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


@pytest.mark.parametrize("value", [" ", ",", " , , "])
@pytest.mark.parametrize("interactive", [True, False])
def test_empty_tool_allowlist_does_not_enable_defaults(
    tmp_path, monkeypatch, value, interactive
):
    from gptme.config import ChatConfig

    monkeypatch.setenv("TOOL_ALLOWLIST", value)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    logdir = tmp_path / "logs"
    config = setup_config_from_cli(
        workspace=workspace, logdir=logdir, interactive=interactive
    )
    assert config.chat is not None
    assert config.chat.tools == ([] if interactive else ["complete"])
    assert ChatConfig.from_logdir(logdir).tools == []


def test_tool_allowlist_env_rejects_mixed_syntax(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWLIST", "-shell,save")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="TOOL_ALLOWLIST"):
        setup_config_from_cli(workspace=workspace, logdir=tmp_path / "logs")


@pytest.mark.parametrize("value", ["-SHELL", "-shel", "-save,-shel"])
def test_tool_allowlist_env_warns_on_unknown_exclusion(
    tmp_path, monkeypatch, caplog, value
):
    """An unknown exclusion warns like --tools instead of forcing plugin
    discovery (and a hard startup failure) during config setup."""
    monkeypatch.setenv("TOOL_ALLOWLIST", value)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with caplog.at_level("WARNING", logger="gptme.config.cli_setup"):
        config = setup_config_from_cli(workspace=workspace, logdir=tmp_path / "logs")
    assert config.chat is not None
    assert "not in the default toolset" in caplog.text


@pytest.mark.parametrize("value", ["-form", "-someserver.sometool"])
def test_tool_allowlist_env_allows_non_default_exclusion(tmp_path, monkeypatch, value):
    """Opt-in tools and MCP names are valid to exclude even if not loaded."""
    monkeypatch.setenv("TOOL_ALLOWLIST", value)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = setup_config_from_cli(workspace=workspace, logdir=tmp_path / "logs")
    assert config.chat is not None
    assert "shell" in (config.chat.tools or [])


def test_parser_strips_each_exclusion_prefix():
    """The internal exclusion spec has one leading '-', not one per name."""
    from gptme.config.cli_setup import parse_tool_specs

    spec = parse_tool_specs(["-save,-shell"])
    assert spec == "-save,shell"
    assert spec[1:].split(",") == ["save", "shell"]


@pytest.mark.parametrize(
    "spec", ["+none", "-none", "+NoNe", "+none,shell", "- none", "+ none", "-\tNoNe"]
)
def test_parser_rejects_prefixed_none(spec):
    from gptme.config.cli_setup import parse_tool_specs

    with pytest.raises(ValueError, match="'none'"):
        parse_tool_specs([spec])


def test_env_preset_cannot_be_combined_with_other_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWLIST", "read-only,shell")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="cannot be combined"):
        setup_config_from_cli(
            workspace=workspace, logdir=tmp_path / "logs", interactive=False
        )
