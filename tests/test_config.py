from dataclasses import replace
import json
import os
import tempfile
from pathlib import Path

from gptme.config import Config, get_config, load_user_config, ChatConfig, MCPConfig

import tomlkit

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

test_mcp_server_2 = """
[[mcp.servers]]
name = "my-server-2"
enabled = true
command = "server-command-2"
args = ["--arg2", "--arg3"]
env = { API_KEY = "your-key-2" }
"""

test_mcp_server_3 = """
[[mcp.servers]]
name = "my-server-3"
enabled = true
command = "server-command-3"
args = ["--arg3", "--arg4"]
env = { API_KEY = "your-key-3" }
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


def test_get_config():
    config = get_config()
    assert config


def test_env_vars_loaded_in_correct_priority(monkeypatch):
    temp_dir = tempfile.gettempdir()
    temp_user_config = os.path.join(temp_dir, "config.toml")
    temp_project_config = os.path.join(temp_dir, "gptme.toml")

    try:
        # Create a temporary user config file with env vars and check that they are loaded
        with open(temp_user_config, "w") as temp_file:
            temp_file.write(default_user_config)
            temp_file.write('TEST_KEY = "file_test_key"\nANOTHER_KEY = "file_value"')
            temp_file.flush()
        config = Config(user=load_user_config(temp_user_config))
        assert config.get_env("TEST_KEY") == "file_test_key"
        assert config.get_env("ANOTHER_KEY") == "file_value"

        # Check that the env vars are overridden by the project config
        project_config = """[env]\nTEST_KEY = \"project_test_key\"\nANOTHER_KEY = \"project_value\""""
        with open(temp_project_config, "w") as temp_file:
            temp_file.write(project_config)
            temp_file.flush()
        config = Config.from_workspace(Path(temp_dir))
        config = replace(config, user=load_user_config(temp_user_config))
        assert config.get_env("TEST_KEY") == "project_test_key"
        assert config.get_env("ANOTHER_KEY") == "project_value"

        # Check that the env vars are overridden by the environment
        monkeypatch.setenv("ANOTHER_KEY", "env_value")
        monkeypatch.setenv("TEST_KEY", "env_test_key")
        assert config.get_env("TEST_KEY") == "env_test_key"
        assert config.get_env("ANOTHER_KEY") == "env_value"

    finally:
        # Delete the temporary files
        if os.path.exists(temp_user_config):
            os.remove(temp_user_config)
        if os.path.exists(temp_project_config):
            os.remove(temp_project_config)


def test_mcp_config_loaded_in_correct_priority():
    temp_dir = tempfile.gettempdir()
    temp_user_config = os.path.join(temp_dir, "config.toml")
    temp_project_config = os.path.join(temp_dir, "gptme.toml")

    try:
        # Create a temporary user config file with MCP config
        with open(temp_user_config, "w") as temp_file:
            temp_file.write(default_user_config)
            temp_file.write("\n" + default_mcp_config)
            temp_file.write("\n" + test_mcp_server_1_enabled)
            temp_file.write("\n" + test_mcp_server_2)
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
        config = Config.from_workspace(Path(temp_dir))
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

    finally:
        # Delete the temporary files
        if os.path.exists(temp_user_config):
            os.remove(temp_user_config)
        if os.path.exists(temp_project_config):
            os.remove(temp_project_config)


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
    config_json = """{
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
    config = MCPConfig.from_dict(json.loads(config_json))

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
    assert config.workspace == Path("~/workspace")
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

    return config


def test_chat_config_loaded_from_json():
    config_json = """{
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
        "mcp": {
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
        }
    }"""
    config = ChatConfig.from_dict(json.loads(config_json))

    assert config.model == "gpt-4o"
    assert config.tools == ["tool1", "tool2"]
    assert config.tool_format == "markdown"
    assert config.stream is True
    assert config.interactive is True
    assert config.workspace == Path("~/workspace")
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

    return config


def test_chat_config_to_dict():
    config = test_chat_config_loaded_from_json()
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
            }
        ],
    }


def test_chat_config_to_toml():
    config = test_chat_config_loaded_from_toml()
    config_dict = config.to_dict()
    toml_str = tomlkit.dumps(config_dict)
    config_new = ChatConfig.from_dict(tomlkit.loads(toml_str).unwrap())
    assert config_new == config


def test_default_chat_config_to_toml():
    config = ChatConfig()
    toml_str = tomlkit.dumps(config.to_dict())
    config_new = ChatConfig.from_dict(tomlkit.loads(toml_str).unwrap())
    assert config_new == config
