"""Tests for the MCP namespace hint hook."""

from unittest.mock import MagicMock, patch

from gptme.hooks import StopPropagation
from gptme.hooks.mcp_namespace_hint import _MCP_HINT_RE, mcp_namespace_hint
from gptme.message import Message


def _make_client(*tool_specs: tuple[str, str]) -> MagicMock:
    """Build a mock MCPClient with the given (name, description) tools."""
    tools = []
    for name, desc in tool_specs:
        t = MagicMock()
        t.name = name
        t.description = desc
        tools.append(t)
    result = MagicMock()
    result.tools = tools
    client = MagicMock()
    client.tools = result
    return client


def _run(messages: list[Message], clients: dict) -> list[Message]:
    """Invoke the hook under a patched MCP adapter state."""
    with patch("gptme.tools.mcp_adapter.get_mcp_clients", return_value=clients):
        return [
            item
            for item in mcp_namespace_hint(messages)
            if not isinstance(item, StopPropagation)
        ]


# ---------------------------------------------------------------------------
# Regex unit tests
# ---------------------------------------------------------------------------


class TestMcpHintRegex:
    def test_matches_simple_at_ref(self):
        assert _MCP_HINT_RE.findall("@github list PRs") == ["github"]

    def test_matches_hyphenated_name(self):
        assert _MCP_HINT_RE.findall("use @my-server") == ["my-server"]

    def test_matches_multiple_refs(self):
        assert _MCP_HINT_RE.findall("@github and @linear") == ["github", "linear"]

    def test_no_match_plain_text(self):
        assert _MCP_HINT_RE.findall("just some text here") == []

    def test_no_false_positive_on_email_address(self):
        # word char before @ means it's an email, not an MCP ref
        assert _MCP_HINT_RE.findall("contact user@github.com for help") == []


# ---------------------------------------------------------------------------
# Hook behaviour
# ---------------------------------------------------------------------------


class TestMcpNamespaceHint:
    def test_no_messages_yields_nothing(self):
        results = _run([], {})
        assert results == []

    def test_no_at_ref_yields_nothing(self):
        msgs = [Message("user", "list my issues")]
        results = _run(msgs, {"github": _make_client(("list_issues", "List issues"))})
        assert results == []

    def test_at_ref_matches_server_yields_hint(self):
        client = _make_client(("list_issues", "List GitHub issues"))
        msgs = [Message("user", "@github list my open PRs")]
        results = _run(msgs, {"github": client})

        assert len(results) == 1
        hint = results[0]
        assert hint.role == "system"
        assert "github" in hint.content
        assert "mcp__github__list_issues" in hint.content
        assert "List GitHub issues" in hint.content

    def test_at_ref_no_matching_server_yields_nothing(self):
        msgs = [Message("user", "@unknown do something")]
        results = _run(msgs, {"github": _make_client(("list_issues", ""))})
        assert results == []

    def test_no_loaded_servers_yields_nothing(self):
        msgs = [Message("user", "@github list PRs")]
        with patch("gptme.tools.mcp_adapter.get_mcp_clients", return_value={}):
            results = list(mcp_namespace_hint(msgs))
        assert results == []

    def test_multiple_servers_in_one_message(self):
        clients = {
            "github": _make_client(("list_issues", "List issues")),
            "linear": _make_client(("create_ticket", "Create ticket")),
        }
        msgs = [Message("user", "@github check PRs and @linear create a ticket")]
        results = _run(msgs, clients)

        assert len(results) == 1
        content = results[0].content
        assert "mcp__github__list_issues" in content
        assert "mcp__linear__create_ticket" in content

    def test_only_last_user_message_checked(self):
        client = _make_client(("list_issues", "List issues"))
        msgs = [
            Message("user", "@github list PRs"),
            Message("assistant", "Sure"),
            Message("user", "just plain text"),
        ]
        results = _run(msgs, {"github": client})
        # The last user message has no @ref, so nothing fires
        assert results == []

    def test_tool_without_description(self):
        tool = MagicMock()
        tool.name = "ping"
        tool.description = ""
        result = MagicMock()
        result.tools = [tool]
        client = MagicMock()
        client.tools = result

        msgs = [Message("user", "@myserver ping")]
        results = _run(msgs, {"myserver": client})

        assert len(results) == 1
        # Tool appears without description suffix
        assert "mcp__myserver__ping`" in results[0].content

    def test_hint_format_contains_full_tool_name(self):
        client = _make_client(("do_thing", "Does a thing"))
        msgs = [Message("user", "@srv do the thing")]
        results = _run(msgs, {"srv": client})
        content = results[0].content
        # Full namespaced name must appear
        assert "mcp__srv__do_thing" in content
        # Description must appear
        assert "Does a thing" in content
