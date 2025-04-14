import os
from pathlib import Path
import tempfile
from gptme.config import get_config, Config, load_user_config

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


def test_get_config():
    config = get_config()
    print(f"config: {config}")
    assert config


def test_env_vars_loaded_in_correct_priority():
    temp_dir = tempfile.gettempdir()
    temp_user_config = os.path.join(temp_dir, "config.toml")
    temp_project_config = os.path.join(temp_dir, "gptme.toml")

    try:
        # Create a temporary user config file with env vars and check that they are loaded
        with open(temp_user_config, "w") as temp_file:
            temp_file.write(default_user_config)
            temp_file.write(
                'OPENAI_API_KEY = "file_test_key"\nANOTHER_KEY = "file_value"'
            )
            temp_file.flush()
        config = Config(user=load_user_config(temp_user_config))
        assert config.get_env("OPENAI_API_KEY") == "file_test_key"
        assert config.get_env("ANOTHER_KEY") == "file_value"

        # Check that the env vars are overridden by the project config
        project_config = """[env]\nOPENAI_API_KEY = \"project_test_key\"\nANOTHER_KEY = \"project_value\""""
        with open(temp_project_config, "w") as temp_file:
            temp_file.write(project_config)
            temp_file.flush()
        config = Config.from_workspace(Path(temp_dir))
        config.user = load_user_config(temp_user_config)
        assert config.get_env("OPENAI_API_KEY") == "project_test_key"
        assert config.get_env("ANOTHER_KEY") == "project_value"

        # Check that the env vars are overridden by the environment
        os.environ["ANOTHER_KEY"] = "env_value"
        os.environ["OPENAI_API_KEY"] = "env_test_key"
        assert config.get_env("OPENAI_API_KEY") == "env_test_key"
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
        config.user = load_user_config(temp_user_config)

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
