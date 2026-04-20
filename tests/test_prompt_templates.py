"""Unit tests for gptme/prompts/ — context_cmd, templates, chat_history, helpers.

Covers the untested areas of the prompts module:
- _truncate_context_output (context_cmd.py)
- get_project_context_cmd_output (context_cmd.py)
- _join_messages (__init__.py)
- prompt_gptme — agent_name, thinking tags, base_prompt override (templates.py)
- prompt_full / prompt_short — composition (templates.py)
- prompt_user — config fallback (templates.py)
- prompt_project — markdown mode (templates.py)
- use_chat_history_context (chat_history.py)
- find_agent_files_in_tree (workspace.py)
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message

# ---------------------------------------------------------------------------
# _truncate_context_output
# ---------------------------------------------------------------------------


class TestTruncateContextOutput:
    """Tests for _truncate_context_output in context_cmd.py."""

    def _truncate(self, output: str, max_chars: int = 100_000) -> str:
        from gptme.prompts.context_cmd import _truncate_context_output

        return _truncate_context_output(output, max_chars)

    def test_short_output_unchanged(self):
        """Output shorter than limit is returned as-is."""
        text = "short output"
        assert self._truncate(text) == text

    def test_exact_limit_unchanged(self):
        """Output exactly at limit is returned as-is."""
        text = "x" * 100_000
        assert self._truncate(text, 100_000) == text

    def test_over_limit_truncated(self):
        """Output over limit is truncated with a notice."""
        text = "x" * 200
        result = self._truncate(text, 100)
        assert len(result) < 200 + 100  # truncated + notice
        assert "TRUNCATED" in result
        assert "200" in result  # original chars mentioned

    def test_truncation_breaks_at_newline(self):
        """Truncation prefers breaking at a newline boundary."""
        # Create lines that cross the boundary
        lines = ["line " + str(i) for i in range(50)]
        text = "\n".join(lines)
        result = self._truncate(text, 100)
        # Result should end at a complete line boundary before the TRUNCATED notice
        main_content = result.split("\n\n... [TRUNCATED")[0]
        # Last line should be a complete "line N" entry, not a partial cut
        last_line = main_content.rstrip().rsplit("\n", 1)[-1]
        assert last_line.startswith("line ")

    def test_truncation_notice_format(self):
        """Truncation notice includes original and kept char counts."""
        text = "a" * 500
        result = self._truncate(text, 200)
        assert "[TRUNCATED:" in result
        assert "500" in result  # original size

    def test_small_max_chars(self):
        """Edge case: very small max_chars doesn't crash."""
        text = "hello world\nfoo bar\nbaz"
        result = self._truncate(text, 5)
        assert "TRUNCATED" in result

    def test_zero_max_chars(self):
        """Edge case: max_chars=0 still produces truncation notice."""
        text = "hello"
        result = self._truncate(text, 0)
        assert "TRUNCATED" in result


# ---------------------------------------------------------------------------
# get_project_context_cmd_output
# ---------------------------------------------------------------------------


class TestGetProjectContextCmdOutput:
    """Tests for get_project_context_cmd_output in context_cmd.py."""

    def test_successful_command(self, tmp_path):
        """Successful command returns codeblock-wrapped output."""
        from gptme.prompts.context_cmd import get_project_context_cmd_output

        result = get_project_context_cmd_output("echo hello", tmp_path)
        assert result is not None
        assert "hello" in result

    def test_failed_command_includes_stderr(self, tmp_path):
        """Failed command includes both stdout and stderr."""
        from gptme.prompts.context_cmd import get_project_context_cmd_output

        result = get_project_context_cmd_output(
            "echo partial; echo error >&2; exit 1", tmp_path
        )
        assert result is not None
        assert "partial" in result
        assert "error" in result
        assert "exit 1" in result

    def test_timeout_returns_none(self, tmp_path):
        """Command that times out returns None."""
        # Patch subprocess.run to raise TimeoutExpired
        import subprocess

        from gptme.prompts.context_cmd import get_project_context_cmd_output

        with patch(
            "gptme.prompts.context_cmd.subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 60),
        ):
            result = get_project_context_cmd_output("sleep 999", tmp_path)
        assert result is None

    def test_large_output_truncated(self, tmp_path):
        """Very large output is truncated before processing."""
        from gptme.prompts.context_cmd import get_project_context_cmd_output

        # Generate output >100k chars; mock len_tokens to avoid slow tokenization
        large_text = "x" * 200_000
        with (
            patch("gptme.prompts.context_cmd.subprocess.run") as mock_run,
            patch("gptme.prompts.context_cmd.len_tokens", return_value=50_000),
        ):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = large_text
            mock_run.return_value = mock_result

            result = get_project_context_cmd_output("cat bigfile", tmp_path)
            assert result is not None
            assert "TRUNCATED" in result


# ---------------------------------------------------------------------------
# _join_messages
# ---------------------------------------------------------------------------


class TestJoinMessages:
    """Tests for _join_messages helper in __init__.py."""

    def test_joins_content(self):
        from gptme.prompts import _join_messages

        msgs = [
            Message("system", "part 1"),
            Message("system", "part 2"),
            Message("system", "part 3"),
        ]
        result = _join_messages(msgs)
        assert result.role == "system"
        assert "part 1" in result.content
        assert "part 2" in result.content
        assert "part 3" in result.content
        # Separated by double newlines
        assert result.content == "part 1\n\npart 2\n\npart 3"

    def test_preserves_hide_flag(self):
        from gptme.prompts import _join_messages

        msgs = [
            Message("system", "a", hide=True),
            Message("system", "b"),
        ]
        result = _join_messages(msgs)
        assert result.hide is True

    def test_preserves_pinned_flag(self):
        from gptme.prompts import _join_messages

        msgs = [
            Message("system", "a"),
            Message("system", "b", pinned=True),
        ]
        result = _join_messages(msgs)
        assert result.pinned is True

    def test_single_message(self):
        from gptme.prompts import _join_messages

        msgs = [Message("system", "solo")]
        result = _join_messages(msgs)
        assert result.content == "solo"

    def test_mixed_roles_raises(self):
        from gptme.prompts import _join_messages

        msgs = [Message("system", "a"), Message("user", "b")]
        with pytest.raises(AssertionError):
            _join_messages(msgs)


# ---------------------------------------------------------------------------
# prompt_gptme
# ---------------------------------------------------------------------------


class TestPromptGptme:
    """Tests for prompt_gptme in templates.py."""

    @pytest.fixture(autouse=True)
    def _isolate_from_live_config(self):
        """Mock project config lookups so tests don't depend on the live repo."""
        with (
            patch("gptme.prompts.templates.get_project_git_dir", return_value=None),
            patch(
                "gptme.prompts.templates.get_project_config",
                return_value=MagicMock(base_prompt=None),
            ),
        ):
            yield

    def test_interactive_mode(self):
        """Interactive mode includes interactive instructions."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True))
        content = msgs[0].content
        assert "interactive mode" in content
        assert "non-interactive" not in content

    def test_non_interactive_mode(self):
        """Non-interactive mode includes non-interactive instructions."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=False))
        content = msgs[0].content
        assert "non-interactive mode" in content

    def test_agent_name(self):
        """Agent name is injected into the prompt."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True, agent_name="TestBot"))
        content = msgs[0].content
        assert "TestBot" in content
        assert "agent running in gptme" in content

    def test_default_name_is_gptme(self):
        """Without agent_name, gptme version is used."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True))
        content = msgs[0].content
        assert "gptme v" in content
        assert "general-purpose AI assistant" in content

    def test_thinking_tags_without_reasoning_model(self):
        """Models without native reasoning get <thinking> tag instructions."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True, model=None))
        content = msgs[0].content
        assert "<thinking>" in content

    def test_no_thinking_tags_with_reasoning_model(self):
        """Reasoning models don't get <thinking> tag instructions."""
        from gptme.prompts import prompt_gptme

        # Mock a reasoning model
        mock_model = MagicMock()
        mock_model.supports_reasoning = True
        mock_model.full = "openai/o3"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(prompt_gptme(interactive=True, model="openai/o3"))
        content = msgs[0].content
        assert "<thinking>" not in content

    def test_model_name_in_prompt(self):
        """When model is specified, its name appears in the prompt."""
        from gptme.prompts import prompt_gptme

        mock_model = MagicMock()
        mock_model.supports_reasoning = False
        mock_model.full = "anthropic/claude-sonnet-4-6"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(
                prompt_gptme(interactive=True, model="anthropic/claude-sonnet-4-6")
            )
        content = msgs[0].content
        assert "anthropic/claude-sonnet-4-6" in content

    def test_base_prompt_override(self):
        """Project config can override the base prompt."""
        from gptme.prompts import prompt_gptme

        mock_project_config = MagicMock()
        mock_project_config.base_prompt = "Custom base prompt for my project."

        with (
            patch(
                "gptme.prompts.templates.get_project_git_dir",
                return_value=Path("/fake/project"),
            ),
            patch(
                "gptme.prompts.templates.get_project_config",
                return_value=mock_project_config,
            ),
        ):
            msgs = list(prompt_gptme(interactive=True))
        content = msgs[0].content
        assert "Custom base prompt for my project." in content

    def test_xml_format_wraps_in_role(self):
        """XML format wraps the entire prompt in <role> tags."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True, tool_format="xml"))
        content = msgs[0].content
        assert content.startswith("<role>")
        assert content.endswith("</role>")

    def test_tool_use_enforcement_for_openai(self):
        """GPT-family models get explicit tool-use enforcement guidance."""
        from gptme.prompts import prompt_gptme

        mock_model = MagicMock()
        mock_model.supports_reasoning = False
        mock_model.full = "openai/gpt-5"
        mock_model.provider = "openai"
        mock_model.model = "gpt-5"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(prompt_gptme(interactive=True, model="openai/gpt-5"))
        content = msgs[0].content
        assert "call it immediately" in content

    def test_tool_use_enforcement_for_xai(self):
        """Grok-family models get explicit tool-use enforcement guidance."""
        from gptme.prompts import prompt_gptme

        mock_model = MagicMock()
        mock_model.supports_reasoning = False
        mock_model.full = "xai/grok-4"
        mock_model.provider = "xai"
        mock_model.model = "grok-4"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(prompt_gptme(interactive=True, model="xai/grok-4"))
        assert "call it immediately" in msgs[0].content

    def test_no_tool_use_enforcement_for_anthropic(self):
        """Anthropic/Claude models do NOT get tool-use enforcement guidance."""
        from gptme.prompts import prompt_gptme

        mock_model = MagicMock()
        mock_model.supports_reasoning = False
        mock_model.full = "anthropic/claude-sonnet-4-6"
        mock_model.provider = "anthropic"
        mock_model.model = "claude-sonnet-4-6"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(
                prompt_gptme(interactive=True, model="anthropic/claude-sonnet-4-6")
            )
        assert "call it immediately" not in msgs[0].content

    def test_no_tool_use_enforcement_without_model(self):
        """When no model is specified, enforcement guidance is not injected."""
        from gptme.prompts import prompt_gptme

        msgs = list(prompt_gptme(interactive=True, model=None))
        assert "call it immediately" not in msgs[0].content

    def test_no_tool_use_enforcement_for_gptq_models(self):
        """GPTQ-quantized models (e.g. Llama-2-7B-GPTQ) must NOT get enforcement."""
        from gptme.prompts import prompt_gptme

        mock_model = MagicMock()
        mock_model.supports_reasoning = False
        mock_model.full = "openrouter/Llama-2-7B-GPTQ"
        mock_model.provider = "openrouter"
        mock_model.model = "llama-2-7b-gptq"

        with patch("gptme.prompts.templates.get_model", return_value=mock_model):
            msgs = list(
                prompt_gptme(interactive=True, model="openrouter/Llama-2-7B-GPTQ")
            )
        assert "call it immediately" not in msgs[0].content


# ---------------------------------------------------------------------------
# prompt_full / prompt_short composition
# ---------------------------------------------------------------------------


class TestPromptComposition:
    """Tests for prompt_full and prompt_short composition in templates.py."""

    @pytest.fixture(autouse=True)
    def _isolate_from_live_config(self):
        """Mock project config lookups so tests don't depend on the live repo."""
        with (
            patch("gptme.prompts.templates.get_project_git_dir", return_value=None),
            patch(
                "gptme.prompts.templates.get_project_config",
                return_value=MagicMock(base_prompt=None),
            ),
        ):
            yield

    def _make_tool(self):
        """Create a minimal ToolSpec for testing."""
        from gptme.tools import ToolSpec

        return ToolSpec(
            name="test-tool",
            desc="A test tool",
            instructions="Test instructions",
        )

    def test_prompt_full_includes_all_sections(self):
        """prompt_full should include gptme, tools, user, project, systeminfo, timeinfo."""
        from gptme.prompts.templates import prompt_full

        tools = [self._make_tool()]
        msgs = list(
            prompt_full(
                interactive=True,
                tools=tools,
                tool_format="markdown",
                model=None,
            )
        )
        combined = "\n".join(m.content for m in msgs)

        # Core identity
        assert "gptme" in combined.lower() or "general-purpose" in combined.lower()
        # Tools
        assert "test-tool" in combined
        # System info
        assert "System Information" in combined or "Working Directory" in combined
        # Time info
        assert "Current Date" in combined or "UTC" in combined

    def test_prompt_short_has_fewer_sections(self):
        """prompt_short should have fewer sections than prompt_full."""
        from gptme.prompts.templates import prompt_full, prompt_short

        tools = [self._make_tool()]
        full_msgs = list(
            prompt_full(
                interactive=True,
                tools=tools,
                tool_format="markdown",
                model=None,
            )
        )
        short_msgs = list(
            prompt_short(
                interactive=True,
                tools=tools,
                tool_format="markdown",
            )
        )
        full_content = "\n".join(m.content for m in full_msgs)
        short_content = "\n".join(m.content for m in short_msgs)

        # Short prompt should not include systeminfo or timeinfo
        assert "System Information" not in short_content
        assert "Current Date" not in short_content

        # But full prompt should
        assert (
            "System Information" in full_content or "Working Directory" in full_content
        )

    def test_prompt_full_non_interactive_skips_user(self):
        """Non-interactive mode should skip user preferences."""
        from gptme.prompts.templates import prompt_full

        tools = [self._make_tool()]
        msgs = list(
            prompt_full(
                interactive=False,
                tools=tools,
                tool_format="markdown",
                model=None,
            )
        )
        combined = "\n".join(m.content for m in msgs)

        # Should have non-interactive blurb
        assert "non-interactive" in combined.lower()
        # User-preferences section must be absent (prompt_user not called for non-interactive)
        assert "Response Preferences" not in combined
        assert "# About" not in combined

    def test_prompt_short_no_examples(self):
        """prompt_short passes examples=False, so examples are excluded."""
        from gptme.prompts.templates import prompt_short
        from gptme.tools import ToolSpec

        # Create tool WITH examples to ensure the flag actually matters
        tool = ToolSpec(
            name="test-tool",
            desc="A test tool",
            instructions="Test instructions",
            examples="User: example usage\nAssistant: example response",
        )
        msgs = list(
            prompt_short(
                interactive=True,
                tools=[tool],
                tool_format="markdown",
            )
        )
        combined = "\n".join(m.content for m in msgs)

        # Tool should be mentioned but examples section must be excluded
        assert "test-tool" in combined
        assert "### Examples" not in combined
        assert "example usage" not in combined


# ---------------------------------------------------------------------------
# prompt_user
# ---------------------------------------------------------------------------


class TestPromptUser:
    """Tests for prompt_user in templates.py — config fallback logic."""

    def test_default_user_prompt(self):
        """Without user config, falls back to built-in defaults."""
        from gptme.prompts import prompt_user

        mock_config = MagicMock()
        mock_config.user.user.name = None
        mock_config.user.user.about = None
        mock_config.user.user.response_preference = None
        mock_config.user.prompt.about_user = None
        mock_config.user.prompt.response_preference = None

        with patch("gptme.prompts.templates.get_config", return_value=mock_config):
            msgs = list(prompt_user())
        assert len(msgs) == 1
        content = msgs[0].content
        # Default fallbacks from prompt_user when no config is set
        assert "# About User" in content
        assert "You are interacting with a human programmer." in content
        assert "No specific preferences set." in content

    def test_user_name_in_prompt(self):
        """User name from config appears in prompt."""
        from gptme.prompts import prompt_user

        mock_config = MagicMock()
        mock_config.user.user.name = "Alice"
        mock_config.user.user.about = "A developer"
        mock_config.user.user.response_preference = "Be brief"
        mock_config.user.prompt.about_user = None
        mock_config.user.prompt.response_preference = None

        with patch("gptme.prompts.templates.get_config", return_value=mock_config):
            msgs = list(prompt_user())
        content = msgs[0].content
        assert "Alice" in content
        assert "A developer" in content
        assert "Be brief" in content

    def test_fallback_to_prompt_section(self):
        """Falls back to [prompt] section if [user] section is empty."""
        from gptme.prompts import prompt_user

        mock_config = MagicMock()
        mock_config.user.user.name = None
        mock_config.user.user.about = None
        mock_config.user.user.response_preference = None
        mock_config.user.prompt.about_user = "Fallback about user"
        mock_config.user.prompt.response_preference = "Fallback prefs"

        with patch("gptme.prompts.templates.get_config", return_value=mock_config):
            msgs = list(prompt_user())
        content = msgs[0].content
        assert "Fallback about user" in content
        assert "Fallback prefs" in content

    def test_xml_format_structured(self):
        """XML format produces structured tags."""
        from gptme.prompts import prompt_user

        mock_config = MagicMock()
        mock_config.user.user.name = "Alice"
        mock_config.user.user.about = "A developer"
        mock_config.user.user.response_preference = "Be concise"
        mock_config.user.prompt.about_user = None
        mock_config.user.prompt.response_preference = None

        with patch("gptme.prompts.templates.get_config", return_value=mock_config):
            msgs = list(prompt_user(tool_format="xml"))
        content = msgs[0].content
        assert "<user>" in content
        assert "<name>" in content
        assert "<about>" in content
        assert "<response-preferences>" in content


# ---------------------------------------------------------------------------
# prompt_project
# ---------------------------------------------------------------------------


class TestPromptProject:
    """Tests for prompt_project in templates.py."""

    def test_no_project_yields_nothing(self):
        """When not in a git repo, no project prompt is generated."""
        from gptme.prompts import prompt_project

        with patch("gptme.prompts.templates.get_project_git_dir", return_value=None):
            msgs = list(prompt_project())
        assert len(msgs) == 0

    def test_project_with_info(self):
        """Project with info shows project name and description."""
        from gptme.prompts import prompt_project

        project_dir = Path("/fake/my-project")
        mock_config = MagicMock()
        mock_config.prompt = "This project does amazing things"

        with (
            patch(
                "gptme.prompts.templates.get_project_git_dir",
                return_value=project_dir,
            ),
            patch(
                "gptme.prompts.templates.get_project_config",
                return_value=mock_config,
            ),
            patch("gptme.prompts.templates.get_config") as mock_get_config,
        ):
            mock_get_config.return_value.user.prompt.project = None
            msgs = list(prompt_project())

        assert len(msgs) == 1
        content = msgs[0].content
        assert "my-project" in content
        assert "amazing things" in content

    def test_project_without_info(self):
        """Project without info still shows project name."""
        from gptme.prompts import prompt_project

        project_dir = Path("/fake/bare-project")
        mock_config = MagicMock()
        mock_config.prompt = None

        with (
            patch(
                "gptme.prompts.templates.get_project_git_dir",
                return_value=project_dir,
            ),
            patch(
                "gptme.prompts.templates.get_project_config",
                return_value=mock_config,
            ),
            patch("gptme.prompts.templates.get_config") as mock_get_config,
        ):
            mock_get_config.return_value.user.prompt.project = {}
            msgs = list(prompt_project())

        assert len(msgs) == 1
        assert "bare-project" in msgs[0].content
        # project_info is None — verify no literal "None" string in the output
        assert "None" not in msgs[0].content


# ---------------------------------------------------------------------------
# prompt_systeminfo
# ---------------------------------------------------------------------------


class TestPromptSysteminfo:
    """Tests for prompt_systeminfo in templates.py."""

    def test_includes_os_info(self):
        """System info includes OS name and working directory."""
        from gptme.prompts import prompt_systeminfo

        msgs = list(prompt_systeminfo())
        content = msgs[0].content
        # Should have some OS info (could be distro name like Ubuntu, or generic)
        assert "**OS:**" in content
        assert "**Working Directory:**" in content

    def test_workspace_path_used(self, tmp_path):
        """When workspace is provided, it's used instead of cwd."""
        from gptme.prompts import prompt_systeminfo

        msgs = list(prompt_systeminfo(workspace=tmp_path))
        content = msgs[0].content
        assert str(tmp_path) in content

    def test_markdown_format(self):
        """Markdown format uses headers."""
        from gptme.prompts import prompt_systeminfo

        msgs = list(prompt_systeminfo(tool_format="markdown"))
        content = msgs[0].content
        assert "## System Information" in content
        assert "**OS:**" in content
        assert "**Working Directory:**" in content


# ---------------------------------------------------------------------------
# prompt_timeinfo
# ---------------------------------------------------------------------------


class TestPromptTimeinfo:
    """Tests for prompt_timeinfo in templates.py."""

    def test_contains_date(self):
        """Time info contains a date string."""
        from datetime import datetime, timezone

        from gptme.prompts import prompt_timeinfo

        msgs = list(prompt_timeinfo())
        content = msgs[0].content
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in content

    def test_markdown_format(self):
        """Markdown format uses header."""
        from gptme.prompts import prompt_timeinfo

        msgs = list(prompt_timeinfo(tool_format="markdown"))
        content = msgs[0].content
        assert "## Current Date" in content
        assert "**UTC:**" in content

    def test_xml_format(self):
        """XML format uses current-date tag."""
        from gptme.prompts import prompt_timeinfo

        msgs = list(prompt_timeinfo(tool_format="xml"))
        content = msgs[0].content
        assert "<current-date>" in content
        assert "</current-date>" in content


# ---------------------------------------------------------------------------
# use_chat_history_context
# ---------------------------------------------------------------------------


class TestUseChatHistoryContext:
    """Tests for use_chat_history_context in chat_history.py."""

    def test_disabled_by_default(self):
        """Chat history is disabled when env var is not set."""
        from gptme.prompts.chat_history import use_chat_history_context

        mock_config = MagicMock()
        mock_config.get_env.return_value = ""

        with patch("gptme.prompts.chat_history.get_config", return_value=mock_config):
            assert use_chat_history_context() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
    def test_enabled_truthy_values(self, value):
        """Chat history is enabled with truthy env var values."""
        from gptme.prompts.chat_history import use_chat_history_context

        mock_config = MagicMock()
        mock_config.get_env.return_value = value

        with patch("gptme.prompts.chat_history.get_config", return_value=mock_config):
            assert use_chat_history_context() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "other"])
    def test_disabled_non_truthy_values(self, value):
        """Chat history is disabled with non-truthy env var values (allowlist-based)."""
        from gptme.prompts.chat_history import use_chat_history_context

        mock_config = MagicMock()
        mock_config.get_env.return_value = value

        with patch("gptme.prompts.chat_history.get_config", return_value=mock_config):
            assert use_chat_history_context() is False


# ---------------------------------------------------------------------------
# find_agent_files_in_tree
# ---------------------------------------------------------------------------


class TestFindAgentFilesInTree:
    """Tests for find_agent_files_in_tree in workspace.py."""

    def test_finds_agents_md_in_directory(self, tmp_path):
        """Finds AGENTS.md in the target directory."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("# Instructions")

        # Patch home to be the tmp_path parent so we don't walk the real home
        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path.parent):
            result = find_agent_files_in_tree(tmp_path)

        paths = [str(p) for p in result]
        assert any("AGENTS.md" in p for p in paths)

    def test_finds_claude_md(self, tmp_path):
        """Finds CLAUDE.md alongside AGENTS.md."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        (tmp_path / "CLAUDE.md").write_text("# Claude instructions")
        (tmp_path / "AGENTS.md").write_text("# Agent instructions")

        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path.parent):
            result = find_agent_files_in_tree(tmp_path)

        names = [p.name for p in result]
        assert "AGENTS.md" in names
        assert "CLAUDE.md" in names

    def test_walks_from_home_to_target(self, tmp_path):
        """Finds agent files in parent directories between home and target."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        # Create nested structure: home/project/subdir/
        home = tmp_path / "home"
        project = home / "project"
        subdir = project / "subdir"
        subdir.mkdir(parents=True)

        # Put AGENTS.md in the project (parent of target)
        (project / "AGENTS.md").write_text("# Project rules")
        # And in the subdir (target)
        (subdir / "AGENTS.md").write_text("# Subdir rules")

        with patch("gptme.prompts.workspace.Path.home", return_value=home):
            result = find_agent_files_in_tree(subdir)

        # Should find both, project first (most general first)
        assert len(result) >= 2
        assert result[0].parent.name == "project"
        assert result[1].parent.name == "subdir"

    def test_excludes_specified_paths(self, tmp_path):
        """Files in the exclude set are skipped."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("# Instructions")
        exclude = {str(agents_file.resolve())}

        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path.parent):
            result = find_agent_files_in_tree(tmp_path, exclude=exclude)

        assert len(result) == 0

    def test_finds_cross_tool_files(self, tmp_path):
        """Finds .cursorrules and .windsurfrules files."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        (tmp_path / ".cursorrules").write_text("cursor rules")
        (tmp_path / ".windsurfrules").write_text("windsurf rules")

        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path.parent):
            result = find_agent_files_in_tree(tmp_path)

        names = [p.name for p in result]
        assert ".cursorrules" in names
        assert ".windsurfrules" in names

    def test_empty_directory_returns_empty(self, tmp_path):
        """Empty directory returns no agent files."""
        from gptme.prompts.workspace import find_agent_files_in_tree

        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path.parent):
            result = find_agent_files_in_tree(tmp_path)

        assert result == []


# ---------------------------------------------------------------------------
# _xml_section helper
# ---------------------------------------------------------------------------


class TestXmlSection:
    """Tests for _xml_section in __init__.py."""

    def test_basic_wrapping(self):
        from gptme.prompts import _xml_section

        result = _xml_section("test", "content")
        assert result == "<test>\ncontent\n</test>"

    def test_strips_whitespace(self):
        from gptme.prompts import _xml_section

        result = _xml_section("tag", "  padded  \n\n")
        assert result == "<tag>\npadded\n</tag>"

    def test_preserves_nested_xml(self):
        """Nested XML tags are preserved (content is NOT escaped)."""
        from gptme.prompts import _xml_section

        result = _xml_section("outer", "<inner>data</inner>")
        assert "<inner>data</inner>" in result
        assert result.startswith("<outer>")
        assert result.endswith("</outer>")


# ---------------------------------------------------------------------------
# AGENT_FILES constant
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_agent_files_includes_standard_names(self):
        from gptme.prompts import AGENT_FILES

        assert "AGENTS.md" in AGENT_FILES
        assert "CLAUDE.md" in AGENT_FILES
        assert "COPILOT.md" in AGENT_FILES
        assert "GEMINI.md" in AGENT_FILES

    def test_agent_files_includes_cross_tool(self):
        from gptme.prompts import AGENT_FILES

        assert ".cursorrules" in AGENT_FILES
        assert ".windsurfrules" in AGENT_FILES

    def test_agent_files_includes_github_copilot(self):
        from gptme.prompts import AGENT_FILES

        assert ".github/copilot-instructions.md" in AGENT_FILES

    def test_default_context_files(self):
        from gptme.prompts import DEFAULT_CONTEXT_FILES

        assert any("README" in f for f in DEFAULT_CONTEXT_FILES)
        assert any("pyproject.toml" in f for f in DEFAULT_CONTEXT_FILES)
        assert any("Makefile" in f for f in DEFAULT_CONTEXT_FILES)

    def test_always_load_files_alias(self):
        """ALWAYS_LOAD_FILES is kept for backwards compatibility."""
        from gptme.prompts import AGENT_FILES, ALWAYS_LOAD_FILES

        assert ALWAYS_LOAD_FILES is AGENT_FILES
