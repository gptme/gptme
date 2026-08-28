"""Tests for `gptme search` alias and gptme-* plugin dispatch."""

import importlib
import re
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.main import main


@pytest.fixture
def runner():
    return CliRunner()


class TestSearchAlias:
    def test_search_calls_search_chats(self, runner: CliRunner):
        """gptme search QUERY delegates to search_chats."""
        with patch("gptme.tools.chats.search_chats") as mock_search:
            result = runner.invoke(main, ["search", "flask migration"])
        assert result.exit_code == 0
        mock_search.assert_called_once_with(
            "flask migration", max_results=20, context_lines=1, max_matches=1
        )

    def test_search_multiword_query_joined(self, runner: CliRunner):
        """gptme search word1 word2 joins terms into a single query."""
        with patch("gptme.tools.chats.search_chats") as mock_search:
            result = runner.invoke(main, ["search", "flask", "migration"])
        assert result.exit_code == 0
        mock_search.assert_called_once_with(
            "flask migration", max_results=20, context_lines=1, max_matches=1
        )

    def test_search_empty_query_errors(self, runner: CliRunner):
        """gptme search with no query prints an error."""
        result = runner.invoke(main, ["search"])
        assert result.exit_code != 0
        assert "Usage:" in result.output or "query" in result.output.lower()

    def test_search_does_not_intercept_non_search_prompts(self, runner: CliRunner):
        """gptme 'search for recipes' is not intercepted by the search alias.

        prompts[0] == "search for recipes" (one string), not "search".
        """
        with (
            patch("gptme.tools.chats.search_chats") as mock_search,
            patch.object(importlib.import_module("gptme.chat"), "chat"),
        ):
            runner.invoke(
                main,
                ["--non-interactive", "--no-confirm", "search for recipes"],
            )
        # search_chats must NOT be called — this is a regular chat prompt
        mock_search.assert_not_called()

    def test_help_mentions_search(self, runner: CliRunner):
        """gptme --help mentions the search shortcut."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "search" in result.output.lower()

    def test_search_does_not_swallow_version(self, runner: CliRunner):
        """gptme --version search QUERY shows version, not search results."""
        with patch("gptme.tools.chats.search_chats") as mock_search:
            result = runner.invoke(main, ["--version", "search", "anything"])
        assert result.exit_code == 0
        mock_search.assert_not_called()
        assert re.search(r"\d+\.\d+\.\d+", result.output), (
            f"Expected version output (X.Y.Z), got: {result.output!r}"
        )

    def test_search_does_not_swallow_version_json(self, runner: CliRunner):
        """gptme --version-json search QUERY shows version JSON, not search results."""
        with patch("gptme.tools.chats.search_chats") as mock_search:
            result = runner.invoke(main, ["--version-json", "search", "anything"])
        assert result.exit_code == 0
        mock_search.assert_not_called()
        assert "{" in result.output or "version" in result.output.lower()

    def test_search_suppressed_after_double_dash(self, runner: CliRunner):
        """gptme -- search treats search as a prompt, not the search alias."""
        with (
            patch("gptme.tools.chats.search_chats") as mock_search,
            patch("gptme.cli.main.shutil.which", return_value=None),
            patch.object(importlib.import_module("gptme.chat"), "chat") as mock_chat,
        ):
            result = runner.invoke(main, ["--", "search"])
        assert result.exit_code == 0
        mock_search.assert_not_called()
        prompt_msgs = mock_chat.call_args.args[0]
        assert [msg.content for msg in prompt_msgs] == ["search"]


class TestPluginDispatch:
    def test_plugin_found_in_path_is_executed(self, runner: CliRunner):
        """gptme sessions delegates to gptme-sessions if found in PATH."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-sessions",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            # Use only positional args — gptme CLI rejects unknown --flags before dispatch
            result = runner.invoke(main, ["sessions"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(["/usr/local/bin/gptme-sessions"])

    def test_plugin_options_are_forwarded(self, runner: CliRunner):
        """gptme sessions --format json forwards option-like plugin arguments."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-sessions",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["sessions", "--format", "json"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-sessions", "--format", "json"]
        )

    def test_plugin_not_found_falls_through(self, runner: CliRunner):
        """gptme unknowncmd with no gptme-unknowncmd in PATH falls through to normal CLI."""
        with (
            patch("gptme.cli.main.shutil.which", return_value=None),
            patch.object(importlib.import_module("gptme.chat"), "chat"),
        ):
            # Normal CLI starts a chat session; shutil.which returning None means no dispatch
            runner.invoke(main, ["unknowncmd"])

    def test_plugin_dispatch_skipped_for_version_flag(self, runner: CliRunner):
        """gptme --version sessions does not trigger plugin dispatch."""
        with (
            patch("gptme.cli.main.subprocess.call") as mock_call,
        ):
            result = runner.invoke(main, ["--version", "sessions"])
        assert result.exit_code == 0
        mock_call.assert_not_called()

    def test_plugin_dispatch_suppressed_after_double_dash(self, runner: CliRunner):
        """gptme -- sessions treats sessions as a prompt, not a plugin."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-sessions",
            ),
            patch("gptme.cli.main.subprocess.call") as mock_call,
            patch.object(importlib.import_module("gptme.chat"), "chat"),
        ):
            result = runner.invoke(main, ["--", "sessions"])
        assert result.exit_code == 0
        mock_call.assert_not_called()

    def test_help_mentions_plugin_dispatch(self, runner: CliRunner):
        """gptme --help mentions the plugin dispatch mechanism."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "gptme-" in result.output


class TestUtilSubcommandMirroring:
    def test_util_subcmd_delegates_to_gptme_util(self, runner: CliRunner):
        """gptme chats [args] delegates to gptme-util chats [args]."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["chats"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(["/usr/local/bin/gptme-util", "chats"])

    def test_util_subcmd_passes_all_args(self, runner: CliRunner):
        """gptme chats list passes 'chats list' to gptme-util."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["chats", "list"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list"]
        )

    def test_util_subcmd_forwards_nested_help(self, runner: CliRunner):
        """gptme chats list --help shows the mirrored command's help."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["chats", "list", "--help"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list", "--help"]
        )

    def test_util_subcmd_forwards_nested_help_with_leading_flag(
        self, runner: CliRunner
    ):
        """gptme --verbose chats list --help forwards --help to gptme-util."""
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["--verbose", "chats", "list", "--help"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list", "--help"]
        )

    def test_util_subcmd_forwards_nested_help_with_leading_value_option(
        self, runner: CliRunner
    ):
        """gptme --model gpt-4 chats list --help forwards --help to gptme-util.

        The option value 'gpt-4' must not be mistaken for the first positional.
        """
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(
                main, ["--model", "gpt-4", "chats", "list", "--help"]
            )
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list", "--help"]
        )

    def test_util_subcmd_forwards_nested_help_with_grouped_short_option(
        self, runner: CliRunner
    ):
        """gptme -vm gpt-4 chats list --help forwards --help to gptme-util.

        '-v' is a flag and '-m' is a value option; the grouped token '-vm' must
        cause the scanner to skip the next token ('gpt-4') so that 'chats' is
        found as the first positional.
        """
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["-vm", "gpt-4", "chats", "list", "--help"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list", "--help"]
        )

    def test_util_subcmd_forwards_nested_help_with_inline_value_short_option(
        self, runner: CliRunner
    ):
        """gptme -mgpt-4 chats list --help forwards --help to gptme-util.

        '-mgpt-4' is a short option with the value attached inline (no space).
        The scanner must not skip the next token ('chats') — the value is already
        embedded in the token, so 'chats' is the first true positional.
        """
        with (
            patch(
                "gptme.cli.main.shutil.which",
                return_value="/usr/local/bin/gptme-util",
            ),
            patch("gptme.cli.main.subprocess.call", return_value=0) as mock_call,
        ):
            result = runner.invoke(main, ["-mgpt-4", "chats", "list", "--help"])
        assert result.exit_code == 0
        mock_call.assert_called_once_with(
            ["/usr/local/bin/gptme-util", "chats", "list", "--help"]
        )

    def test_util_subcmd_after_double_dash_suppresses_dispatch(self):
        """The '--' sentinel suppresses utility dispatch.

        We test parse_args via make_context (which runs parse_args but not the
        main() body) to avoid triggering LLM initialisation in the test.
        """
        with patch("gptme.cli.main.shutil.which", return_value=None):
            ctx = main.make_context("gptme", ["--", "chats", "list"])
        assert ctx.meta.get("shortcut_dispatch_suppressed", False)

    def test_util_subcmd_after_unknown_option_does_not_dispatch(
        self, runner: CliRunner
    ):
        """gptme --bogus chats list --help shows gptme help, not gptme-util.

        With ignore_unknown_options=True, Click treats '--bogus' as a positional.
        The scanner mirrors this: unknown long options stop the scan without
        enabling util dispatch, so allow_interspersed_args stays True and
        --help fires as an eager option showing gptme's top-level help.
        """
        with (
            patch("gptme.cli.main.shutil.which", return_value=None),
            patch("gptme.cli.main.subprocess.call") as mock_call,
        ):
            result = runner.invoke(main, ["--bogus", "chats", "list", "--help"])
        assert "Usage:" in result.output
        assert result.exit_code == 0
        mock_call.assert_not_called()

    def test_util_subcmd_after_unknown_short_option_does_not_dispatch(
        self, runner: CliRunner
    ):
        """gptme -x chats list --help shows gptme help, not gptme-util."""
        with (
            patch("gptme.cli.main.shutil.which", return_value=None),
            patch("gptme.cli.main.subprocess.call") as mock_call,
        ):
            result = runner.invoke(main, ["-x", "chats", "list", "--help"])
        assert "Usage:" in result.output
        assert result.exit_code == 0
        mock_call.assert_not_called()

    def test_util_subcmd_skipped_for_version_flag(self, runner: CliRunner):
        """gptme --version chats does not trigger gptme-util dispatch."""
        with patch("gptme.cli.main.subprocess.call") as mock_call:
            result = runner.invoke(main, ["--version", "chats"])
        assert result.exit_code == 0
        mock_call.assert_not_called()

    def test_util_subcommands_all_known(self):
        """UTIL_SUBCOMMANDS contains expected gptme-util subcommands."""
        from gptme.cli.util import UTIL_SUBCOMMANDS

        for expected in ("chats", "tools", "skills", "models", "context"):
            assert expected in UTIL_SUBCOMMANDS

    def test_util_subcmd_takes_priority_over_path_dispatch(self, runner: CliRunner):
        """gptme chats delegates to gptme-util, not a hypothetical gptme-chats binary."""
        calls: list = []

        def fake_which(cmd: str) -> str | None:
            if cmd == "gptme-util":
                return "/usr/local/bin/gptme-util"
            if cmd == "gptme-chats":
                return "/usr/local/bin/gptme-chats"
            return None

        def fake_call(args: list) -> int:
            calls.append(args)
            return 0

        with (
            patch("gptme.cli.main.shutil.which", side_effect=fake_which),
            patch("gptme.cli.main.subprocess.call", side_effect=fake_call),
        ):
            runner.invoke(main, ["chats"])

        assert calls == [["/usr/local/bin/gptme-util", "chats"]]

    def test_help_mentions_util_subcommand_shortcut(self, runner: CliRunner):
        """gptme --help describes the gptme-util subcommand shortcut."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        # Help text should describe that gptme-util subcommands work directly
        assert "chats" in result.output

    def test_util_subcmd_errors_when_gptme_util_not_installed(self, runner: CliRunner):
        """gptme chats exits with code 1 and an actionable error when gptme-util is absent."""
        with patch("gptme.cli.main.shutil.which", return_value=None):
            result = runner.invoke(main, ["chats"])
        assert result.exit_code == 1
        assert "gptme-util" in result.output
