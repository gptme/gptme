"""Tests for `gptme search` alias → gptme-util chats search."""

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
