import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
import tomlkit

from gptme.config import (
    ChatConfig,
    Config,
    MCPConfig,
    ProjectConfig,
    UserIdentityConfig,
    get_config,
    load_user_config,
    setup_config_from_cli,
)

default_user_config = """[prompt]
about_user = "I am a curious human programmer."
response_preference = "Don't explain basic concepts"

[env]
"""

default_mcp_config = """
[mcp]
enabled = true
auto_start = true
"""

test_mcp_server_1_enabled = """
[[mcp.servers]]
name = "my-server"
enabled = true
command = "server-command"
args = ["--arg1", "--arg2"]
env = { API_KEY = "your-key" }
"""

test_mcp_server_1_disabled = """
[[mcp.servers]]
name = "my-server"
enabled = false
"""

test_mcp_server_2_enabled = """
[[mcp.servers]]
name = "my-server-2"
enabled = true
command = "server-command-2"
args = ["--arg2", "--arg3"]
env = { API_KEY = "your-key-2" }
"""

test_mcp_server_2_disabled = """
[[mcp.servers]]
name = "my-server-2"
enabled = false
"""

test_mcp_server_3 = """
[[mcp.servers]]
name = "my-server-3"
enabled = true
command = "server-command-3"
args = ["--arg3", "--arg4"]
env = { API_KEY = "your-key-3" }
"""

test_mcp_server_4 = """
[[mcp.servers]]
name = "my-server-4"
enabled = true
command = "server-command-4"
args = ["--arg4", "--arg5"]
env = { API_KEY = "your-key-4" }
"""

chat_config_toml = """
[chat]
model = "gpt-4o"
tools = ["tool1", "tool2"]
tool_format = "markdown"
stream = true
interactive = true
workspace = "~/workspace"

[env]
API_KEY = "your-key"

[mcp]
enabled = true
auto_start = true

[[mcp.servers]]
name = "my-server"
enabled = true
command = "server-command"
args = ["--arg1", "--arg2"]
env = { API_KEY = "your-key" }
"""

config_mcp_json = """{
    "enabled": true,
    "auto_start": true,
    "servers": [
        {
            "name": "my-server",
            "enabled": true,
            "command": "server-command",
            "args": ["--arg1", "--arg2"],
            "env": {
                "API_KEY": "your-key"
            }
        }
    ]
}"""


config_json = (
    """
{
    "chat": {
        "model": "gpt-4o",
        "tools": ["tool1", "tool2"],
        "tool_format": "markdown",
        "stream": true,
        "interactive": true,
        "workspace": "~/workspace"
    },
    "env": {
        "API_KEY": "your-key"
    },
    "mcp": """
    + config_mcp_json
    + """
}
"""
)

project_config_toml = """
files = [
  "README.md",
  "ARCHITECTURE.md",
  "gptme.toml"
]
context_cmd = "scripts/context.sh"
prompt = "You are a helpful assistant."
base_prompt = "My custom base prompt."

[mcp]
enabled = true
auto_start = true

[[mcp.servers]]
name = "sqlite"
enabled = true
command = "uvx"
args = [
    "mcp-server-sqlite",
    "--db-path",
    "database.db"
]

[[mcp.servers]]
name = "oura"
enabled = true
command = "uvx"
args = ["oura-mcp-server"]

[rag]
enabled = true

[agent]
name = "TestBot"
avatar = "assets/avatar.png"

"""

project_config_json = """
{
    "files": ["README.md", "ARCHITECTURE.md", "gptme.toml"],
    "context_cmd": "scripts/context.sh",
    "prompt": "You are a helpful assistant.",
    "base_prompt": "My custom base prompt.",
    "rag": {
        "enabled": true
    },
    "mcp": {
        "enabled": true,
        "auto_start": true,
        "servers": [
            {
                "name": "sqlite",
                "enabled": true,
                "command": "uvx",
                "args": ["mcp-server-sqlite", "--db-path", "database.db"]
            },
            {
                "name": "oura",
                "enabled": true,
                "command": "uvx",
                "args": ["oura-mcp-server"]
            }
        ]
    },
    "agent": {
        "name": "TestBot",
        "avatar": "assets/avatar.png"
    }
}
"""


def test_get_config():
    config = get_config()
    assert config


def test_env_vars_loaded_in_correct_priority(monkeypatch, tmp_path):
    temp_user_config = str(tmp_path / "config.toml")
    temp_project_config = str(tmp_path / "gptme.toml")

    # Create a temporary user config file with env vars and check that they are loaded
    with open(temp_user_config, "w") as temp_file:
        temp_file.write(default_user_config)
        temp_file.write('TEST_KEY = "file_test_key"\nANOTHER_KEY = "file_value"')
        temp_file.flush()
    config = Config(user=load_user_config(temp_user_config))
    assert config.get_env("TEST_KEY") == "file_test_key"
    assert config.get_env("ANOTHER_KEY") == "file_value"

    # Check that the env vars are overridden by the project config
    project_config = (
        """[env]\nTEST_KEY = \"project_test_key\"\nANOTHER_KEY = \"project_value\""""
    )
    with open(temp_project_config, "w") as temp_file:
        temp_file.write(project_config)
        temp_file.flush()
    config = Config.from_workspace(tmp_path)
    config = replace(config, user=load_user_config(temp_user_config))
    assert config.get_env("TEST_KEY") == "project_test_key"
    assert config.get_env("ANOTHER_KEY") == "project_value"

    # Check that the env vars are overridden by the environment
    monkeypatch.setenv("ANOTHER_KEY", "env_value")
    monkeypatch.setenv("TEST_KEY", "env_test_key")
    assert config.get_env("TEST_KEY") == "env_test_key"
    assert config.get_env("ANOTHER_KEY") == "env_value"


def test_mcp_config_loaded_in_correct_priority(tmp_path):
    temp_user_config = str(tmp_path / "config.toml")
    temp_project_config = str(tmp_path / "gptme.toml")

    # Create a temporary user config file with MCP config
    with open(temp_user_config, "w") as temp_file:
        temp_file.write(default_user_config)
        temp_file.write("\n" + default_mcp_config)
        temp_file.write("\n" + test_mcp_server_1_enabled)
        temp_file.write("\n" + test_mcp_server_2_enabled)
        temp_file.flush()
    config = Config(user=load_user_config(temp_user_config))
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True
    assert len(config.mcp.servers) == 2
    my_server = next(s for s in config.mcp.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is True
    assert my_server.command == "server-command"
    assert my_server.args == ["--arg1", "--arg2"]
    assert my_server.env == {"API_KEY": "your-key"}
    my_server_2 = next(s for s in config.mcp.servers if s.name == "my-server-2")
    assert my_server_2.name == "my-server-2"
    assert my_server_2.enabled is True
    assert my_server_2.command == "server-command-2"
    assert my_server_2.args == ["--arg2", "--arg3"]
    assert my_server_2.env == {"API_KEY": "your-key-2"}

    # Check that the MCP config is overridden by the project config
    project_config = """[mcp]\nenabled = false\nauto_start = false"""
    with open(temp_project_config, "w") as temp_file:
        temp_file.write(project_config)
        temp_file.write("\n" + test_mcp_server_1_disabled)
        temp_file.write("\n" + test_mcp_server_3)
        temp_file.flush()
    config = Config.from_workspace(tmp_path)
    config = replace(config, user=load_user_config(temp_user_config))

    # Check that the MCP config is overridden by the project config
    assert config.mcp.enabled is False
    assert config.mcp.auto_start is False

    # Check that the MCP servers are merged from the user and project configs
    # Should have 3 servers:
    # - my-server (enabled in user config, disabled in project config)
    # - my-server-2 (added in user config, not in project config)
    # - my-server-3 (added in project config, not in user config)
    assert len(config.mcp.servers) == 3
    my_server = next(s for s in config.mcp.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is False
    my_server_2 = next(s for s in config.mcp.servers if s.name == "my-server-2")
    assert my_server_2.name == "my-server-2"
    assert my_server_2.enabled is True
    assert my_server_2.command == "server-command-2"
    assert my_server_2.args == ["--arg2", "--arg3"]
    assert my_server_2.env == {"API_KEY": "your-key-2"}
    my_server_3 = next(s for s in config.mcp.servers if s.name == "my-server-3")
    assert my_server_3.name == "my-server-3"
    assert my_server_3.enabled is True
    assert my_server_3.command == "server-command-3"
    assert my_server_3.args == ["--arg3", "--arg4"]
    assert my_server_3.env == {"API_KEY": "your-key-3"}

    # Load chat config
    chat_config_toml_str = """
        [chat]
        model = "gpt-4o"
        tools = ["tool1", "tool2"]
        tool_format = "markdown"
        stream = true
        interactive = true

        [mcp]
        enabled = true
        auto_start = true

    """
    chat_config_toml_str += test_mcp_server_2_disabled + "\n\n" + test_mcp_server_4
    chat_config_dict = tomlkit.loads(chat_config_toml_str)
    chat_config = ChatConfig.from_dict(chat_config_dict.unwrap())
    assert chat_config.mcp is not None
    assert chat_config.mcp.enabled is True
    assert chat_config.mcp.auto_start is True
    assert len(chat_config.mcp.servers) == 2

    # Check that the MCP config is merged from the chat config, project config, and the user config
    # Should have 4 servers:
    # - my-server (enabled in user config, disabled in project config)
    # - my-server-2 (added in user config, not in project config, disabled in chat config)
    # - my-server-3 (added in project config, not in user config)
    # - my-server-4 (added in chat config, not in user config or project config)
    config.chat = chat_config
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True
    assert len(config.mcp.servers) == 4
    my_server = next(s for s in config.mcp.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is False
    my_server_2 = next(s for s in config.mcp.servers if s.name == "my-server-2")
    assert my_server_2.name == "my-server-2"
    assert my_server_2.enabled is False
    my_server_3 = next(s for s in config.mcp.servers if s.name == "my-server-3")
    assert my_server_3.name == "my-server-3"
    assert my_server_3.enabled is True
    assert my_server_3.command == "server-command-3"
    assert my_server_3.args == ["--arg3", "--arg4"]
    assert my_server_3.env == {"API_KEY": "your-key-3"}
    my_server_4 = next(s for s in config.mcp.servers if s.name == "my-server-4")
    assert my_server_4.name == "my-server-4"
    assert my_server_4.enabled is True
    assert my_server_4.command == "server-command-4"
    assert my_server_4.args == ["--arg4", "--arg5"]
    assert my_server_4.env == {"API_KEY": "your-key-4"}


def test_mcp_config_loaded_from_toml():
    config_toml = """[mcp]
        enabled = true
        auto_start = true

        [[mcp.servers]]
        name = "my-server"
        enabled = true
        command = "server-command"
        args = ["--arg1", "--arg2"]
        env = { API_KEY = "your-key" }
    """
    config_dict = tomlkit.loads(config_toml)
    mcp = config_dict.pop("mcp", {})
    config = MCPConfig.from_dict(mcp)

    assert config.enabled is True
    assert config.auto_start is True
    assert len(config.servers) == 1
    my_server = next(s for s in config.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is True
    assert my_server.command == "server-command"
    assert my_server.args == ["--arg1", "--arg2"]
    assert my_server.env == {"API_KEY": "your-key"}


def test_mcp_config_loaded_from_json():
    config = MCPConfig.from_dict(json.loads(config_mcp_json))

    assert config.enabled is True
    assert config.auto_start is True
    assert len(config.servers) == 1
    my_server = next(s for s in config.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is True


def test_chat_config_loaded_from_toml():
    toml_doc = tomlkit.loads(chat_config_toml)
    config = ChatConfig.from_dict(toml_doc.unwrap())

    assert config.model == "gpt-4o"
    assert config.tools == ["tool1", "tool2"]
    assert config.tool_format == "markdown"
    assert config.stream is True
    assert config.interactive is True
    assert config.workspace == Path.home() / "workspace"
    assert config.env == {"API_KEY": "your-key"}
    assert config.mcp is not None
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True
    assert len(config.mcp.servers) == 1
    my_server = next(s for s in config.mcp.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is True
    assert my_server.command == "server-command"
    assert my_server.args == ["--arg1", "--arg2"]
    assert my_server.env == {"API_KEY": "your-key"}


def test_chat_config_loaded_from_json():
    config = ChatConfig.from_dict(json.loads(config_json))

    assert config.model == "gpt-4o"
    assert config.tools == ["tool1", "tool2"]
    assert config.tool_format == "markdown"
    assert config.stream is True
    assert config.interactive is True
    assert config.workspace == Path.home() / "workspace"
    assert config.env == {"API_KEY": "your-key"}
    assert config.mcp is not None
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True
    assert len(config.mcp.servers) == 1
    my_server = next(s for s in config.mcp.servers if s.name == "my-server")
    assert my_server.name == "my-server"
    assert my_server.enabled is True
    assert my_server.command == "server-command"
    assert my_server.args == ["--arg1", "--arg2"]
    assert my_server.env == {"API_KEY": "your-key"}


def test_chat_config_workspace_at_log(tmp_path):
    """Test that workspace '@log' magic value resolves to logdir/workspace."""
    logdir = tmp_path / "test-conversation"
    logdir.mkdir()

    config_dict = {
        "chat": {"workspace": "@log"},
        "_logdir": logdir,
    }

    config = ChatConfig.from_dict(config_dict)

    # Should resolve to logdir/workspace
    expected_workspace = logdir / "workspace"
    assert config.workspace == expected_workspace

    # Should create the directory
    assert expected_workspace.exists()
    assert expected_workspace.is_dir()


def test_chat_config_workspace_at_log_without_logdir():
    """Test that workspace '@log' raises error without logdir."""
    config_dict = {"chat": {"workspace": "@log"}}

    with pytest.raises(ValueError, match="Cannot use '@log' workspace without logdir"):
        ChatConfig.from_dict(config_dict)


def test_chat_config_to_dict():
    config = ChatConfig.from_dict(json.loads(config_json))
    config_dict = config.to_dict()
    assert config_dict["chat"]["model"] == "gpt-4o"
    assert config_dict["chat"]["tools"] == ["tool1", "tool2"]
    assert config_dict["chat"]["tool_format"] == "markdown"
    assert config_dict["chat"]["stream"] is True
    assert config_dict["chat"]["interactive"] is True
    assert config_dict["chat"]["workspace"] == "~/workspace"
    assert config_dict["env"] == {"API_KEY": "your-key"}
    assert config_dict["mcp"] == {
        "enabled": True,
        "auto_start": True,
        "servers": [
            {
                "name": "my-server",
                "enabled": True,
                "command": "server-command",
                "args": ["--arg1", "--arg2"],
                "env": {"API_KEY": "your-key"},
                "url": "",
                "headers": {},
            }
        ],
    }


def test_chat_config_to_toml():
    config = ChatConfig.from_dict(json.loads(config_json))
    config_dict = config.to_dict()
    toml_str = tomlkit.dumps(config_dict)
    config_new = ChatConfig.from_dict(tomlkit.loads(toml_str).unwrap())
    assert config_new == config


def test_default_chat_config_to_toml():
    config = ChatConfig()
    toml_str = tomlkit.dumps(config.to_dict())
    config_new = ChatConfig.from_dict(tomlkit.loads(toml_str).unwrap())
    assert config_new == config


def test_project_config_loaded_from_toml():
    config = ProjectConfig.from_dict(tomlkit.loads(project_config_toml).unwrap())

    assert config.files == ["README.md", "ARCHITECTURE.md", "gptme.toml"]
    assert config.context_cmd == "scripts/context.sh"
    assert config.prompt == "You are a helpful assistant."
    assert config.base_prompt == "My custom base prompt."
    assert config.rag.enabled is True

    assert config.mcp is not None
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True

    assert len(config.mcp.servers) == 2

    sqlite_server = next(s for s in config.mcp.servers if s.name == "sqlite")
    assert sqlite_server.name == "sqlite"
    assert sqlite_server.enabled is True
    assert sqlite_server.command == "uvx"
    assert sqlite_server.args == ["mcp-server-sqlite", "--db-path", "database.db"]

    oura_server = next(s for s in config.mcp.servers if s.name == "oura")
    assert oura_server.name == "oura"
    assert oura_server.enabled is True
    assert oura_server.command == "uvx"
    assert oura_server.args == ["oura-mcp-server"]

    # Agent config
    assert config.agent is not None
    assert config.agent.name == "TestBot"
    assert config.agent.avatar == "assets/avatar.png"


def test_project_config_loaded_from_json():
    config = ProjectConfig.from_dict(json.loads(project_config_json))

    assert config.files == ["README.md", "ARCHITECTURE.md", "gptme.toml"]
    assert config.context_cmd == "scripts/context.sh"
    assert config.prompt == "You are a helpful assistant."
    assert config.base_prompt == "My custom base prompt."
    assert config.rag.enabled is True

    assert config.mcp is not None
    assert config.mcp.enabled is True
    assert config.mcp.auto_start is True

    assert len(config.mcp.servers) == 2

    sqlite_server = next(s for s in config.mcp.servers if s.name == "sqlite")
    assert sqlite_server.name == "sqlite"
    assert sqlite_server.enabled is True
    assert sqlite_server.command == "uvx"
    assert sqlite_server.args == ["mcp-server-sqlite", "--db-path", "database.db"]

    oura_server = next(s for s in config.mcp.servers if s.name == "oura")
    assert oura_server.name == "oura"
    assert oura_server.enabled is True
    assert oura_server.command == "uvx"
    assert oura_server.args == ["oura-mcp-server"]

    # Agent config
    assert config.agent is not None
    assert config.agent.name == "TestBot"
    assert config.agent.avatar == "assets/avatar.png"


def test_project_config_to_dict():
    config = ProjectConfig.from_dict(json.loads(project_config_json))
    config_dict = config.to_dict()
    assert config_dict["files"] == ["README.md", "ARCHITECTURE.md", "gptme.toml"]
    assert config_dict["context_cmd"] == "scripts/context.sh"
    assert config_dict["prompt"] == "You are a helpful assistant."
    assert config_dict["base_prompt"] == "My custom base prompt."
    assert config_dict["rag"]["enabled"] is True
    assert config_dict["mcp"]["enabled"] is True
    assert config_dict["mcp"]["auto_start"] is True
    assert len(config_dict["mcp"]["servers"]) == 2
    assert config_dict["mcp"]["servers"][0]["name"] == "sqlite"
    assert config_dict["mcp"]["servers"][0]["enabled"] is True
    assert config_dict["mcp"]["servers"][0]["command"] == "uvx"
    assert config_dict["mcp"]["servers"][0]["args"] == [
        "mcp-server-sqlite",
        "--db-path",
        "database.db",
    ]
    assert config_dict["mcp"]["servers"][1]["name"] == "oura"
    assert config_dict["mcp"]["servers"][1]["enabled"] is True
    assert config_dict["mcp"]["servers"][1]["command"] == "uvx"
    assert config_dict["mcp"]["servers"][1]["args"] == ["oura-mcp-server"]


def test_project_config_to_toml():
    config = ProjectConfig.from_dict(json.loads(project_config_json))
    config_dict = config.to_dict()
    toml_str = tomlkit.dumps(config_dict)
    config_new = ProjectConfig.from_dict(tomlkit.loads(toml_str).unwrap())
    assert config_new == config


def test_resume_config_precedence():
    """Test that resume configuration respects saved config unless CLI overrides provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logdir = Path(tmpdir) / "test-conversation"
        logdir.mkdir()
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()

        # Create a saved conversation config with specific model and tool_format
        saved_config_content = f"""[chat]
model = "openrouter/test-model"
tool_format = "xml"
tools = ["shell", "python"]
stream = true
interactive = true
workspace = "{workspace!s}"

[env]
"""

        config_file = logdir / "config.toml"
        config_file.write_text(saved_config_content)

        # Test 1: Resume without CLI overrides - should use saved config
        config = setup_config_from_cli(
            workspace=workspace,
            logdir=logdir,
            model=None,  # No CLI override
            tool_allowlist=None,  # No CLI override
            tool_format=None,  # No CLI override
            stream=True,
            interactive=True,
            agent_path=None,
        )

        assert config.chat is not None, "Chat config should be loaded"
        assert (
            config.chat.model == "openrouter/test-model"
        ), "Should use saved model when no CLI override"
        assert (
            config.chat.tool_format == "xml"
        ), "Should use saved tool_format when no CLI override"
        assert config.chat.tools is not None and (
            "shell" in config.chat.tools
        ), "Should use saved tools when no CLI override"

        # Test 2: Resume with CLI overrides - should use CLI values
        config = setup_config_from_cli(
            workspace=workspace,
            logdir=logdir,
            model="anthropic/claude-3-sonnet",  # CLI override
            tool_allowlist="read,save",  # CLI override
            tool_format="markdown",  # CLI override
            stream=True,
            interactive=True,
            agent_path=None,
        )

        assert config.chat is not None, "Chat config should be loaded"
        assert (
            config.chat.model == "anthropic/claude-3-sonnet"
        ), "Should use CLI model when provided"
        assert (
            config.chat.tool_format == "markdown"
        ), "Should use CLI tool_format when provided"
        assert config.chat.tools == [
            "read",
            "save",
        ], "Should use CLI tools when provided"

        # Test 3: New conversation (no saved config) - should fall back to env/defaults
        new_logdir = Path(tmpdir) / "new-conversation"
        new_logdir.mkdir()

        config = setup_config_from_cli(
            workspace=workspace,
            logdir=new_logdir,
            model=None,  # No CLI override
            tool_allowlist=None,  # No CLI override
            tool_format=None,  # No CLI override
            stream=True,
            interactive=True,
            agent_path=None,
        )

        # For new conversations, should use defaults/env (tool_format defaults to "markdown")
        assert config.chat is not None, "Chat config should be loaded"
        assert (
            config.chat.tool_format == "markdown"
        ), "Should use default tool_format for new conversation"
        # Model will depend on env vars, so we just check it's not the saved value
        assert (
            config.chat.model != "openrouter/test-model"
        ), "Should not use saved model for new conversation"


def test_reload_config_clears_tools(monkeypatch, tmp_path):
    """Test that reload_config() clears the tools cache so MCP tools are recreated."""
    from unittest.mock import MagicMock

    from gptme.config import Config, _config_var, reload_config

    # Set up initial config
    _config_var.set(Config())

    # Mock clear_tools in the tools module
    mock_clear_tools = MagicMock()
    monkeypatch.setattr("gptme.tools.clear_tools", mock_clear_tools)

    # Call reload_config
    reload_config()

    # Verify clear_tools was called
    assert mock_clear_tools.called, "reload_config() should call clear_tools()"


def test_user_identity_config_new_format():
    """Test that [user] section is parsed correctly."""
    config_toml = """
[user]
name = "Erik"
about = "I am a curious human programmer."
response_preference = "Basic concepts don't need to be explained."

[prompt]
[prompt.project]
myproject = "A cool project."

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
        f.flush()
        try:
            config = load_user_config(f.name)
            assert config.user.name == "Erik"
            assert config.user.about == "I am a curious human programmer."
            assert (
                config.user.response_preference
                == "Basic concepts don't need to be explained."
            )
            assert config.prompt.project == {"myproject": "A cool project."}
        finally:
            os.remove(f.name)


def test_user_identity_config_backward_compat():
    """Test that old [prompt] about_user/response_preference still works as fallback."""
    config_toml = """
[prompt]
about_user = "I am a legacy user."
response_preference = "Keep it short."

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
        f.flush()
        try:
            config = load_user_config(f.name)
            # Should fall back to [prompt] values
            assert config.user.name == "User"
            assert config.user.about == "I am a legacy user."
            assert config.user.response_preference == "Keep it short."
        finally:
            os.remove(f.name)


def test_user_identity_config_new_overrides_old():
    """Test that [user] values take priority over [prompt] fallback."""
    config_toml = """
[user]
name = "Erik"
about = "New about text."
response_preference = "New preference."

[prompt]
about_user = "Old about text."
response_preference = "Old preference."

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
        f.flush()
        try:
            config = load_user_config(f.name)
            # [user] should take priority
            assert config.user.name == "Erik"
            assert config.user.about == "New about text."
            assert config.user.response_preference == "New preference."
        finally:
            os.remove(f.name)


def test_user_identity_config_defaults():
    """Test that UserIdentityConfig has sensible defaults."""
    identity = UserIdentityConfig()
    assert identity.name == "User"
    assert identity.about is None
    assert identity.response_preference is None


def test_user_identity_config_partial_fallback():
    """Test that fallback works per-field."""
    config_toml = """
[user]
name = "Erik"
about = "Custom about."

[prompt]
response_preference = "Fallback preference."

[env]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(config_toml)
        f.flush()
        try:
            config = load_user_config(f.name)
            assert config.user.name == "Erik"
            assert config.user.about == "Custom about."
            assert config.user.response_preference == "Fallback preference."
        finally:
            os.remove(f.name)


def test_user_config_local_toml(tmp_path):
    """Test that config.local.toml is merged into the user config."""
    # Create main config with preferences (committable to dotfiles)
    main_config = tmp_path / "config.toml"
    main_config.write_text(
        '[prompt]\nabout_user = "I am a developer."\n\n[env]\nEDITOR = "vim"\n'
    )

    # Create local config with secrets (gitignored)
    local_config = tmp_path / "config.local.toml"
    local_config.write_text(
        '[env]\nOPENAI_API_KEY = "sk-secret-123"\nEDITOR = "nvim"\n'
    )

    user_config = load_user_config(str(main_config))

    # Local env values should be merged in, overriding main where they overlap
    # (check user.env directly to avoid os.environ interference in CI)
    assert user_config.env["OPENAI_API_KEY"] == "sk-secret-123"
    assert user_config.env["EDITOR"] == "nvim"

    # Non-overlapping values from main config should be preserved
    assert user_config.prompt.about_user == "I am a developer."


def test_user_config_local_toml_mcp_merge(tmp_path):
    """Test that config.local.toml merges MCP server env vars into main config."""
    main_config = tmp_path / "config.toml"
    main_config.write_text(
        "[prompt]\n\n"
        "[mcp]\nenabled = true\nauto_start = true\n\n"
        "[[mcp.servers]]\n"
        'name = "my-server"\n'
        'command = "server-cmd"\n'
        'args = ["--arg1"]\n'
    )

    local_config = tmp_path / "config.local.toml"
    local_config.write_text(
        '[[mcp.servers]]\nname = "my-server"\nenv = { API_KEY = "secret-key" }\n'
    )

    config = Config(user=load_user_config(str(main_config)))

    assert config.mcp.enabled is True
    assert len(config.mcp.servers) == 1
    server = config.mcp.servers[0]
    assert server.name == "my-server"
    assert server.command == "server-cmd"
    assert server.env == {"API_KEY": "secret-key"}


def test_user_config_no_local_toml(tmp_path):
    """Test that missing config.local.toml doesn't cause errors."""
    main_config = tmp_path / "config.toml"
    main_config.write_text('[prompt]\nabout_user = "I am a developer."\n\n[env]\n')

    # Should work fine without config.local.toml
    config = Config(user=load_user_config(str(main_config)))
    assert config.user.prompt.about_user == "I am a developer."


def test_cli_auto_envvar_prefix():
    """Test that the main CLI command has auto_envvar_prefix='GPTME' set."""
    from gptme.cli.main import main

    # Verify the Click command has auto_envvar_prefix configured
    assert main.context_settings.get("auto_envvar_prefix") == "GPTME"

    # Verify key options would resolve to expected GPTME_* env var names
    params = {p.name: p for p in main.params}
    assert "model" in params, "CLI should have --model option"
    assert "tool_format" in params, "CLI should have --tool-format option"
    assert "workspace" in params, "CLI should have --workspace option"
