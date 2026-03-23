"""Unit tests for gptme/executor.py — shared execution infrastructure.

Tests cover:
- prepare_execution_environment() orchestration
- Config loading from workspace
- Chat config assignment
- Tool and hook initialization
- .env file loading
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace directory."""
    return tmp_path


@pytest.fixture
def mock_config():
    """Create a mock Config object."""
    config = MagicMock()
    config.chat = MagicMock()
    return config


@pytest.fixture
def mock_tools():
    """Create mock ToolSpec list."""
    tool1 = MagicMock()
    tool1.name = "python"
    tool2 = MagicMock()
    tool2.name = "shell"
    return [tool1, tool2]


# ===========================================================================
# prepare_execution_environment() tests
# ===========================================================================


class TestPrepareExecutionEnvironment:
    """Tests for the main prepare_execution_environment() function."""

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_basic_setup(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
        mock_tools,
    ):
        """Verify the function loads config, sets it, loads .env, and inits tools+hooks."""
        from gptme.executor import prepare_execution_environment

        mock_config_cls.from_workspace.return_value = MagicMock()
        mock_init_tools.return_value = mock_tools

        config, tools = prepare_execution_environment(tmp_workspace)

        # Config loaded from workspace
        mock_config_cls.from_workspace.assert_called_once_with(workspace=tmp_workspace)
        # Config set globally
        mock_set_config.assert_called_once_with(config)
        # .env loaded from workspace
        mock_load_dotenv.assert_called_once_with(dotenv_path=tmp_workspace / ".env")
        # Tools and hooks initialized
        mock_init_tools.assert_called_once_with(None)
        mock_init_hooks.assert_called_once()
        # Returns the right objects
        assert tools == mock_tools

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_with_tool_filter(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
    ):
        """Verify tools parameter is forwarded to init_tools."""
        from gptme.executor import prepare_execution_environment

        mock_config_cls.from_workspace.return_value = MagicMock()
        mock_init_tools.return_value = []
        tool_names = ["python", "shell"]

        prepare_execution_environment(tmp_workspace, tools=tool_names)

        mock_init_tools.assert_called_once_with(tool_names)

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_with_chat_config(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
    ):
        """Verify chat_config is assigned to config.chat when provided."""
        from gptme.executor import prepare_execution_environment

        mock_config = MagicMock()
        mock_config_cls.from_workspace.return_value = mock_config
        mock_init_tools.return_value = []

        chat_config = MagicMock()
        chat_config.model = "anthropic/claude-sonnet-4-6"

        prepare_execution_environment(tmp_workspace, chat_config=chat_config)

        assert mock_config.chat == chat_config

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_without_chat_config(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
    ):
        """Verify config.chat is unchanged when no chat_config is provided."""
        from gptme.executor import prepare_execution_environment

        mock_config = MagicMock()
        original_chat = mock_config.chat
        mock_config_cls.from_workspace.return_value = mock_config
        mock_init_tools.return_value = []

        prepare_execution_environment(tmp_workspace)

        assert mock_config.chat == original_chat

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_return_type(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
        mock_tools,
    ):
        """Verify return is a tuple of (Config, list[ToolSpec])."""
        from gptme.executor import prepare_execution_environment

        mock_config_cls.from_workspace.return_value = MagicMock()
        mock_init_tools.return_value = mock_tools

        result = prepare_execution_environment(tmp_workspace)

        assert isinstance(result, tuple)
        assert len(result) == 2
        config, tools = result
        assert tools == mock_tools

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_call_order(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
    ):
        """Verify initialization steps happen in the correct order."""
        from gptme.executor import prepare_execution_environment

        call_order: list[str] = []
        sentinel_config = MagicMock()

        def _from_workspace(**kw: object) -> MagicMock:
            call_order.append("from_workspace")
            return sentinel_config

        def _init_tools(t: object) -> list[object]:
            call_order.append("init_tools")
            return []

        mock_config_cls.from_workspace.side_effect = _from_workspace
        mock_set_config.side_effect = lambda c: call_order.append("set_config")
        mock_load_dotenv.side_effect = lambda **kw: call_order.append("load_dotenv")
        mock_init_tools.side_effect = _init_tools
        mock_init_hooks.side_effect = lambda: call_order.append("init_hooks")

        prepare_execution_environment(tmp_workspace)

        assert call_order == [
            "from_workspace",
            "set_config",
            "load_dotenv",
            "init_tools",
            "init_hooks",
        ]

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_dotenv_path_construction(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
    ):
        """Verify .env file path is constructed from workspace path."""
        from gptme.executor import prepare_execution_environment

        mock_config_cls.from_workspace.return_value = MagicMock()
        mock_init_tools.return_value = []

        workspace = Path("/some/workspace/path")
        prepare_execution_environment(workspace)

        mock_load_dotenv.assert_called_once_with(
            dotenv_path=Path("/some/workspace/path/.env")
        )

    @patch("gptme.executor.init_hooks")
    @patch("gptme.executor.init_tools")
    @patch("gptme.executor.load_dotenv")
    @patch("gptme.executor.set_config")
    @patch("gptme.executor.Config")
    def test_tools_default_none(
        self,
        mock_config_cls,
        mock_set_config,
        mock_load_dotenv,
        mock_init_tools,
        mock_init_hooks,
        tmp_workspace,
    ):
        """Verify tools=None is the default (initializes all tools)."""
        from gptme.executor import prepare_execution_environment

        mock_config_cls.from_workspace.return_value = MagicMock()
        mock_init_tools.return_value = []

        prepare_execution_environment(tmp_workspace)

        mock_init_tools.assert_called_once_with(None)
