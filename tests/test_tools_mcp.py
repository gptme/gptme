"""Tests for the MCP server management tool.

Tests cover:
- execute_mcp: command dispatch for search, info, load, unload, list, resources, prompts, roots, elicitation
- _get_local_server_info: local config lookup
- _cmd_mcp_* wrappers: thin delegation functions
- examples: output format
- tool spec: registration, commands, parameters
"""

from unittest.mock import MagicMock, patch

from gptme.tools.mcp import (
    _cmd_mcp_info,
    _cmd_mcp_list,
    _cmd_mcp_load,
    _cmd_mcp_roots_add,
    _cmd_mcp_roots_list,
    _cmd_mcp_roots_remove,
    _cmd_mcp_search,
    _cmd_mcp_unload,
    _get_local_server_info,
    execute_mcp,
    tool,
)

# ── execute_mcp: command dispatch ────────────────────────────────────


class TestExecuteMcpDispatch:
    """Tests for execute_mcp command routing."""

    def test_no_code_returns_error(self):
        msgs = list(execute_mcp(None, None, None))
        assert len(msgs) == 1
        assert "No command" in msgs[0].content

    def test_empty_code_returns_error(self):
        msgs = list(execute_mcp("", None, None))
        assert len(msgs) == 1
        assert "No command" in msgs[0].content

    @patch("gptme.tools.mcp.search_mcp_servers")
    def test_search_command(self, mock_search):
        mock_search.return_value = "Found: sqlite-mcp"
        msgs = list(execute_mcp("search sqlite", None, None))
        assert len(msgs) == 1
        assert "sqlite" in msgs[0].content
        mock_search.assert_called_once_with("sqlite", "all", 10)

    @patch("gptme.tools.mcp.search_mcp_servers")
    def test_search_no_query(self, mock_search):
        mock_search.return_value = "All servers"
        list(execute_mcp("search", None, None))
        mock_search.assert_called_once_with("", "all", 10)

    @patch("gptme.tools.mcp.search_mcp_servers")
    def test_search_with_json_args(self, mock_search):
        mock_search.return_value = "results"
        code = 'search sqlite\n{"registry": "official", "limit": "5"}'
        list(execute_mcp(code, None, None))
        mock_search.assert_called_once_with("sqlite", "official", 5)

    def test_info_missing_name(self):
        msgs = list(execute_mcp("info", None, None))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.get_mcp_server_info")
    @patch("gptme.tools.mcp._get_local_server_info", return_value=None)
    def test_info_from_registry(self, _, mock_info):
        mock_info.return_value = "Server sqlite: database tool"
        msgs = list(execute_mcp("info sqlite", None, None))
        assert len(msgs) == 1
        assert "sqlite" in msgs[0].content

    @patch("gptme.tools.mcp._get_local_server_info")
    def test_info_local_server(self, mock_local):
        mock_local.return_value = "# sqlite (configured locally)"
        msgs = list(execute_mcp("info sqlite", None, None))
        assert len(msgs) == 1
        assert "configured locally" in msgs[0].content

    def test_load_missing_name(self):
        msgs = list(execute_mcp("load", None, None))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.load_mcp_server")
    @patch("gptme.tools.mcp.confirm", return_value=True)
    def test_load_confirmed(self, _, mock_load):
        mock_load.return_value = "Server sqlite loaded"
        msgs = list(execute_mcp("load sqlite", None, None))
        assert len(msgs) == 1
        assert "loaded" in msgs[0].content

    @patch("gptme.tools.mcp.confirm", return_value=False)
    def test_load_cancelled(self, _):
        msgs = list(execute_mcp("load sqlite", None, None))
        assert len(msgs) == 1
        assert "Cancelled" in msgs[0].content

    def test_unload_missing_name(self):
        msgs = list(execute_mcp("unload", None, None))
        assert len(msgs) == 1
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.unload_mcp_server")
    @patch("gptme.tools.mcp.confirm", return_value=True)
    def test_unload_confirmed(self, _, mock_unload):
        mock_unload.return_value = "Server sqlite unloaded"
        msgs = list(execute_mcp("unload sqlite", None, None))
        assert len(msgs) == 1
        assert "unloaded" in msgs[0].content

    @patch("gptme.tools.mcp.confirm", return_value=False)
    def test_unload_cancelled(self, _):
        msgs = list(execute_mcp("unload sqlite", None, None))
        assert len(msgs) == 1
        assert "Cancelled" in msgs[0].content

    @patch("gptme.tools.mcp.list_loaded_servers")
    def test_list_command(self, mock_list):
        mock_list.return_value = "Loaded: sqlite, filesystem"
        msgs = list(execute_mcp("list", None, None))
        assert len(msgs) == 1
        assert "sqlite" in msgs[0].content

    def test_unknown_command(self):
        msgs = list(execute_mcp("foobar", None, None))
        assert len(msgs) == 1
        assert "Unknown MCP command" in msgs[0].content
        assert "Available commands" in msgs[0].content

    @patch("gptme.tools.mcp.list_mcp_resources")
    def test_resources_list(self, mock_resources):
        mock_resources.return_value = "db://main/users"
        msgs = list(execute_mcp("resources list sqlite", None, None))
        assert len(msgs) == 1
        mock_resources.assert_called_once_with("sqlite")

    def test_resources_list_missing_server(self):
        msgs = list(execute_mcp("resources list", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.read_mcp_resource")
    def test_resources_read(self, mock_read):
        mock_read.return_value = "resource content"
        msgs = list(execute_mcp("resources read sqlite db://main/users", None, None))
        assert len(msgs) == 1
        mock_read.assert_called_once_with("sqlite", "db://main/users")

    def test_resources_read_missing_args(self):
        msgs = list(execute_mcp("resources read sqlite", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.list_mcp_resource_templates")
    def test_templates_list(self, mock_templates):
        mock_templates.return_value = "templates..."
        msgs = list(execute_mcp("templates list sqlite", None, None))
        assert len(msgs) == 1
        mock_templates.assert_called_once_with("sqlite")

    def test_templates_list_missing_server(self):
        msgs = list(execute_mcp("templates list", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.list_mcp_prompts")
    def test_prompts_list(self, mock_prompts):
        mock_prompts.return_value = "available prompts..."
        msgs = list(execute_mcp("prompts list sqlite", None, None))
        assert len(msgs) == 1
        mock_prompts.assert_called_once_with("sqlite")

    def test_prompts_list_missing_server(self):
        msgs = list(execute_mcp("prompts list", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.get_mcp_prompt")
    def test_prompts_get(self, mock_get):
        mock_get.return_value = "prompt content"
        msgs = list(execute_mcp("prompts get sqlite create-query", None, None))
        assert len(msgs) == 1
        mock_get.assert_called_once_with("sqlite", "create-query", None)

    @patch("gptme.tools.mcp.get_mcp_prompt")
    def test_prompts_get_with_args(self, mock_get):
        mock_get.return_value = "prompt"
        list(
            execute_mcp(
                'prompts get sqlite create-query {"table": "users"}', None, None
            )
        )
        mock_get.assert_called_once_with("sqlite", "create-query", {"table": "users"})

    def test_prompts_get_invalid_json(self):
        msgs = list(
            execute_mcp("prompts get sqlite create-query {bad json}", None, None)
        )
        assert "Invalid JSON" in msgs[0].content

    def test_prompts_get_missing_args(self):
        msgs = list(execute_mcp("prompts get sqlite", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.list_mcp_roots")
    def test_roots_list(self, mock_roots):
        mock_roots.return_value = "roots..."
        list(execute_mcp("roots list", None, None))
        mock_roots.assert_called_once_with(None)

    @patch("gptme.tools.mcp.list_mcp_roots")
    def test_roots_list_with_server(self, mock_roots):
        mock_roots.return_value = "server roots..."
        list(execute_mcp("roots list filesystem", None, None))
        mock_roots.assert_called_once_with("filesystem")

    @patch("gptme.tools.mcp.add_mcp_root")
    def test_roots_add(self, mock_add):
        mock_add.return_value = "root added"
        list(execute_mcp("roots add filesystem file:///home/user Project", None, None))
        mock_add.assert_called_once_with("filesystem", "file:///home/user", "Project")

    def test_roots_add_missing_args(self):
        msgs = list(execute_mcp("roots add filesystem", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.remove_mcp_root")
    def test_roots_remove(self, mock_remove):
        mock_remove.return_value = "root removed"
        list(execute_mcp("roots remove filesystem file:///home/user", None, None))
        mock_remove.assert_called_once_with("filesystem", "file:///home/user")

    def test_roots_remove_missing_args(self):
        msgs = list(execute_mcp("roots remove filesystem", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.enable_mcp_elicitation")
    def test_elicitation_enable(self, mock_enable):
        mock_enable.return_value = "enabled"
        list(execute_mcp("elicitation enable myserver", None, None))
        mock_enable.assert_called_once_with("myserver")

    def test_elicitation_enable_missing_server(self):
        msgs = list(execute_mcp("elicitation enable", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.disable_mcp_elicitation")
    def test_elicitation_disable(self, mock_disable):
        mock_disable.return_value = "disabled"
        list(execute_mcp("elicitation disable myserver", None, None))
        mock_disable.assert_called_once_with("myserver")

    def test_elicitation_disable_missing_server(self):
        msgs = list(execute_mcp("elicitation disable", None, None))
        assert "Usage" in msgs[0].content

    @patch("gptme.tools.mcp.get_mcp_elicitation_status")
    def test_elicitation_status(self, mock_status):
        mock_status.return_value = "elicitation status..."
        list(execute_mcp("elicitation status", None, None))
        mock_status.assert_called_once_with(None)

    @patch("gptme.tools.mcp.get_mcp_elicitation_status")
    def test_elicitation_status_with_server(self, mock_status):
        mock_status.return_value = "status for server"
        list(execute_mcp("elicitation status myserver", None, None))
        mock_status.assert_called_once_with("myserver")

    def test_exception_caught(self):
        """Exceptions in execute_mcp are caught and returned as error messages."""
        with patch(
            "gptme.tools.mcp.search_mcp_servers", side_effect=RuntimeError("boom")
        ):
            msgs = list(execute_mcp("search query", None, None))
            assert len(msgs) == 1
            assert "Error" in msgs[0].content
            assert "boom" in msgs[0].content

    def test_all_messages_are_system(self):
        """All returned messages should have role='system'."""
        msgs = list(execute_mcp("unknown_command", None, None))
        for msg in msgs:
            assert msg.role == "system"


# ── _get_local_server_info ────────────────────────────────────────────


class TestGetLocalServerInfo:
    """Tests for _get_local_server_info — local config lookup."""

    @patch("gptme.config.get_config")
    def test_server_not_found(self, mock_config):
        mock_config.return_value.mcp.servers = []
        result = _get_local_server_info("nonexistent")
        assert result is None

    @patch("gptme.config.get_config")
    def test_stdio_server(self, mock_config):
        server = MagicMock()
        server.name = "sqlite"
        server.is_http = False
        server.enabled = True
        server.command = "uvx"
        server.args = ["mcp-sqlite"]
        mock_config.return_value.mcp.servers = [server]

        result = _get_local_server_info("sqlite")

        assert result is not None
        assert "sqlite" in result
        assert "configured locally" in result
        assert "stdio" in result
        assert "uvx" in result

    @patch("gptme.config.get_config")
    def test_http_server(self, mock_config):
        server = MagicMock()
        server.name = "remote-api"
        server.is_http = True
        server.enabled = True
        server.url = "https://api.example.com/mcp"
        server.headers = {"Authorization": "Bearer xxx"}
        mock_config.return_value.mcp.servers = [server]

        result = _get_local_server_info("remote-api")

        assert result is not None
        assert "HTTP" in result
        assert "https://api.example.com/mcp" in result
        assert "1 configured" in result  # headers count

    @patch("gptme.config.get_config")
    def test_disabled_server(self, mock_config):
        server = MagicMock()
        server.name = "test"
        server.is_http = False
        server.enabled = False
        server.command = "cmd"
        server.args = []
        mock_config.return_value.mcp.servers = [server]

        result = _get_local_server_info("test")

        assert result is not None
        assert "No" in result  # enabled = No


# ── _cmd_mcp_* wrappers ──────────────────────────────────────────────


class TestCmdWrappers:
    """Tests for thin _cmd_mcp_* delegation functions."""

    @patch("gptme.tools.mcp.search_mcp_servers")
    def test_cmd_search(self, mock_search):
        mock_search.return_value = "results"
        assert _cmd_mcp_search("query", "official", 5) == "results"
        mock_search.assert_called_once_with("query", "official", 5)

    @patch("gptme.tools.mcp.search_mcp_servers")
    def test_cmd_search_defaults(self, mock_search):
        mock_search.return_value = "results"
        _cmd_mcp_search()
        mock_search.assert_called_once_with("", "all", 10)

    @patch("gptme.tools.mcp._get_local_server_info", return_value="local info")
    def test_cmd_info_local(self, _):
        result = _cmd_mcp_info("server")
        assert result == "local info"

    @patch("gptme.tools.mcp.get_mcp_server_info")
    @patch("gptme.tools.mcp._get_local_server_info", return_value=None)
    def test_cmd_info_registry(self, _, mock_info):
        mock_info.return_value = "Server not found in registries"
        result = _cmd_mcp_info("server")
        assert "not configured locally" in result

    @patch("gptme.tools.mcp.list_loaded_servers")
    def test_cmd_list(self, mock_list):
        mock_list.return_value = "servers"
        assert _cmd_mcp_list() == "servers"

    @patch("gptme.tools.mcp.load_mcp_server")
    def test_cmd_load(self, mock_load):
        mock_load.return_value = "loaded"
        assert _cmd_mcp_load("server") == "loaded"
        mock_load.assert_called_once_with("server", None)

    @patch("gptme.tools.mcp.load_mcp_server")
    def test_cmd_load_with_config(self, mock_load):
        mock_load.return_value = "loaded"
        _cmd_mcp_load("server", '{"key": "value"}')
        mock_load.assert_called_once_with("server", {"key": "value"})

    def test_cmd_load_invalid_json(self):
        result = _cmd_mcp_load("server", "{bad}")
        assert "Error parsing" in result

    @patch("gptme.tools.mcp.unload_mcp_server")
    def test_cmd_unload(self, mock_unload):
        mock_unload.return_value = "unloaded"
        assert _cmd_mcp_unload("server") == "unloaded"

    @patch("gptme.tools.mcp.list_mcp_roots")
    def test_cmd_roots_list(self, mock_roots):
        mock_roots.return_value = "roots"
        assert _cmd_mcp_roots_list() == "roots"
        mock_roots.assert_called_once_with(None)

    @patch("gptme.tools.mcp.add_mcp_root")
    def test_cmd_roots_add(self, mock_add):
        mock_add.return_value = "added"
        assert _cmd_mcp_roots_add("server", "file:///path", "name") == "added"

    @patch("gptme.tools.mcp.remove_mcp_root")
    def test_cmd_roots_remove(self, mock_remove):
        mock_remove.return_value = "removed"
        assert _cmd_mcp_roots_remove("server", "file:///path") == "removed"


# ── examples ──────────────────────────────────────────────────────────


class TestExamples:
    """Tests for example output generation."""

    def test_examples_markdown(self):
        from gptme.tools.mcp import examples

        result = examples("markdown")
        assert "search" in result
        assert "sqlite" in result
        assert "load" in result
        assert "resources" in result

    def test_examples_xml(self):
        from gptme.tools.mcp import examples

        result = examples("xml")
        assert "mcp" in result

    def test_examples_contain_all_operations(self):
        from gptme.tools.mcp import examples

        result = examples("markdown")
        assert "search" in result
        assert "info" in result
        assert "load" in result
        assert "list" in result
        assert "unload" in result
        assert "resources list" in result
        assert "resources read" in result
        assert "templates list" in result
        assert "prompts list" in result
        assert "prompts get" in result
        assert "roots list" in result
        assert "roots add" in result
        assert "roots remove" in result


# ── tool spec ─────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for MCP tool registration."""

    def test_tool_name(self):
        assert tool.name == "mcp"

    def test_tool_has_description(self):
        assert tool.desc
        assert "MCP" in tool.desc

    def test_tool_has_instructions(self):
        assert tool.instructions
        assert "search" in tool.instructions.lower()

    def test_tool_block_types(self):
        assert "mcp" in tool.block_types

    def test_tool_has_execute(self):
        assert tool.execute is not None
        assert tool.execute is execute_mcp

    def test_tool_has_parameters(self):
        assert tool.parameters
        param_names = [p.name for p in tool.parameters]
        assert "command" in param_names

    def test_command_parameter_required(self):
        cmd_param = next(p for p in tool.parameters if p.name == "command")
        assert cmd_param.required is True

    def test_tool_has_commands(self):
        assert tool.commands
        assert len(tool.commands) >= 12

    def test_tool_commands_include_all(self):
        expected_commands = [
            "mcp search",
            "mcp info",
            "mcp list",
            "mcp load",
            "mcp unload",
            "mcp resources list",
            "mcp resources read",
            "mcp templates list",
            "mcp prompts list",
            "mcp prompts get",
            "mcp roots list",
            "mcp roots add",
            "mcp roots remove",
        ]
        for cmd in expected_commands:
            assert cmd in tool.commands, f"Missing command: {cmd}"

    def test_tool_has_examples(self):
        assert tool.examples is not None
