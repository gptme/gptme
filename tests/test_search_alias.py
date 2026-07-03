"""Tests for `gptme search` alias and gptme-* plugin dispatch."""

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
            patch("gptme.cli.main.chat"),
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
        assert "gptme" in result.output.lower() or "version" in result.output.lower()

    def test_search_does_not_swallow_version_json(self, runner: CliRunner):
        """gptme --version-json search QUERY shows version JSON, not search results."""
        with patch("gptme.tools.chats.search_chats") as mock_search:
            result = runner.invoke(main, ["--version-json", "search", "anything"])
        assert result.exit_code == 0
        mock_search.assert_not_called()
        assert "{" in result.output or "version" in result.output.lower()


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

    def test_plugin_not_found_falls_through(self, runner: CliRunner):
        """gptme unknowncmd with no gptme-unknowncmd in PATH falls through to normal CLI."""
        with (
            patch("gptme.cli.main.shutil.which", return_value=None),
            patch("gptme.cli.main.chat"),
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

    def test_help_mentions_plugin_dispatch(self, runner: CliRunner):
        """gptme --help mentions the plugin dispatch mechanism."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "gptme-" in result.output
