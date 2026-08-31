"""Unit tests for context-scout pre-pass module."""

from unittest.mock import MagicMock, patch

from gptme.context.config import ContextConfig
from gptme.context.scout import (
    _SCOUT_SENTINEL,
    _build_file_tree,
    _get_messages_from_manager,
    _make_turn_pre_hook,
    scout_files,
)
from gptme.message import Message

# ---------------------------------------------------------------------------
# ContextConfig.scout_model
# ---------------------------------------------------------------------------


class TestContextConfig:
    def test_defaults_to_none(self):
        cfg = ContextConfig()
        assert cfg.scout_model is None

    def test_from_dict_sets_scout_model(self):
        cfg = ContextConfig.from_dict({"scout_model": "openai/gpt-4.1-mini"})
        assert cfg.scout_model == "openai/gpt-4.1-mini"

    def test_from_dict_without_scout_model(self):
        cfg = ContextConfig.from_dict({"enabled": True})
        assert cfg.scout_model is None

    def test_from_dict_scout_model_none_explicit(self):
        cfg = ContextConfig.from_dict({"scout_model": None})
        assert cfg.scout_model is None


# ---------------------------------------------------------------------------
# _get_messages_from_manager
# ---------------------------------------------------------------------------


class TestGetMessagesFromManager:
    def test_none_returns_empty_list(self):
        assert _get_messages_from_manager(None) == []

    def test_plain_list(self):
        msgs = [Message("user", "hello"), Message("assistant", "world")]
        assert _get_messages_from_manager(msgs) == msgs

    def test_empty_list(self):
        assert _get_messages_from_manager([]) == []

    def test_object_with_log_as_list(self):
        manager = MagicMock()
        msgs = [Message("user", "hello")]
        manager.log = msgs
        result = _get_messages_from_manager(manager)
        assert result == msgs

    def test_object_with_log_having_messages_attr(self):
        """Handles LogManager → Log → messages pattern."""
        inner_log = MagicMock()
        inner_log.messages = [Message("user", "hi")]
        del inner_log.__iter__  # make sure it's not a list
        manager = MagicMock()
        manager.log = inner_log
        result = _get_messages_from_manager(manager)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_file_tree
# ---------------------------------------------------------------------------


class TestBuildFileTree:
    def test_real_workspace(self, tmp_path):
        """Given a fresh tmp dir with some files, returns them all."""
        (tmp_path / "a.py").write_text("print('a')")
        (tmp_path / "b.md").write_text("# docs")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("hello")

        with patch(
            "gptme.context.selector.file_selector.get_workspace_files"
        ) as mock_get:
            mock_get.return_value = [
                tmp_path / "a.py",
                tmp_path / "b.md",
                sub / "c.txt",
            ]
            result = _build_file_tree(tmp_path)
        assert "a.py" in result
        assert "b.md" in result

    def test_uses_get_workspace_files(self, tmp_path):
        """Delegates to get_workspace_files for file discovery."""
        with patch(
            "gptme.context.selector.file_selector.get_workspace_files"
        ) as mock_gwf:
            mock_gwf.return_value = [tmp_path / "readme.md"]
            (tmp_path / "readme.md").write_text("# hi")
            result = _build_file_tree(tmp_path)
        assert "readme.md" in result

    def test_max_paths_truncates(self, tmp_path):
        """Files beyond max_paths are dropped."""
        with patch(
            "gptme.context.selector.file_selector.get_workspace_files"
        ) as mock_gwf:
            # Return 600 fake file paths (none need to exist — we only count)
            mock_gwf.return_value = [tmp_path / f"f{i}.py" for i in range(600)]
            result = _build_file_tree(tmp_path, max_paths=5)
        assert len(result.splitlines()) == 5


# ---------------------------------------------------------------------------
# scout_files
# ---------------------------------------------------------------------------


class TestScoutFiles:
    def _make_reply(self, text: str) -> Message:
        m = MagicMock(spec=Message)
        m.content = text
        return m

    def test_returns_valid_files(self, tmp_path):
        """Scout response lines that correspond to real files are returned."""
        readme = tmp_path / "README.md"
        readme.write_text("# hi")

        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = [readme]
            mock_reply.return_value = self._make_reply("README.md")
            paths = scout_files("fix the readme documentation", tmp_path, "cheap-model")

        assert paths == [readme.resolve()]

    def test_ignores_nonexistent_paths(self, tmp_path):
        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = []
            mock_reply.return_value = self._make_reply("does/not/exist.py")
            paths = scout_files("do something", tmp_path, "cheap-model")
        assert paths == []

    def test_rejects_path_outside_workspace(self, tmp_path):
        """Paths that escape the workspace root are silently dropped."""
        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = []
            mock_reply.return_value = self._make_reply("/etc/passwd")
            paths = scout_files("read config", tmp_path, "cheap-model")
        assert paths == []

    def test_empty_file_tree_returns_early(self, tmp_path):
        """If the workspace has no tracked files, skip the LLM call."""
        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = []
            paths = scout_files("do something", tmp_path, "cheap-model")
        mock_reply.assert_not_called()
        assert paths == []

    def test_llm_error_returns_empty(self, tmp_path):
        """Any exception from reply() degrades gracefully to empty list."""
        f = tmp_path / "foo.py"
        f.write_text("pass")
        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = [f]
            mock_reply.side_effect = RuntimeError("network error")
            paths = scout_files("fix foo", tmp_path, "cheap-model")
        assert paths == []

    def test_ignores_comment_lines(self, tmp_path):
        """Lines starting with # in the LLM response are skipped."""
        real_file = tmp_path / "real.py"
        real_file.write_text("pass")
        with (
            patch(
                "gptme.context.selector.file_selector.get_workspace_files"
            ) as mock_gwf,
            patch("gptme.llm.reply") as mock_reply,
        ):
            mock_gwf.return_value = [real_file]
            mock_reply.return_value = self._make_reply(
                "# relevant files:\nreal.py\n# end"
            )
            paths = scout_files("do something here", tmp_path, "cheap-model")
        assert paths == [real_file.resolve()]


# ---------------------------------------------------------------------------
# Hook behavior
# ---------------------------------------------------------------------------


class TestTurnPreHook:
    def _make_messages(self, *pairs) -> list[Message]:
        msgs = []
        for role, content in pairs:
            msgs.append(Message(role, content))
        return msgs

    def _run_hook(self, hook_fn, manager_msgs: list[Message]) -> list[Message]:
        return list(hook_fn(manager=manager_msgs))

    def test_short_message_skips_scout(self, tmp_path):
        """Hook does nothing for very short user messages."""
        hook = _make_turn_pre_hook("cheap-model", tmp_path)
        msgs = self._make_messages(("user", "hello"))
        with patch("gptme.context.scout.scout_files", return_value=[]) as mock_sf:
            result = self._run_hook(hook, msgs)
        mock_sf.assert_not_called()
        assert result == []

    def test_long_message_triggers_scout(self, tmp_path):
        """Hook calls scout_files for messages above the word threshold."""
        hook = _make_turn_pre_hook("cheap-model", tmp_path)
        long_msg = "please fix the authentication bug in the login module " * 3
        msgs = self._make_messages(("user", long_msg))
        with patch("gptme.context.scout.scout_files", return_value=[]) as mock_sf:
            self._run_hook(hook, msgs)
        mock_sf.assert_called_once()

    def test_yields_system_message_with_files(self, tmp_path):
        """When scout returns files, hook yields a system message with their content."""
        readme = tmp_path / "README.md"
        readme.write_text("# My Project")
        hook = _make_turn_pre_hook("cheap-model", tmp_path)
        long_msg = (
            "update the readme to describe the new architecture properly and add usage examples "
            * 2
        )
        msgs = self._make_messages(("user", long_msg))

        with patch("gptme.context.scout.scout_files") as mock_sf:
            mock_sf.return_value = [readme.resolve()]
            result = self._run_hook(hook, msgs)

        assert len(result) == 1
        injected = result[0]
        assert injected.role == "system"
        assert _SCOUT_SENTINEL in injected.content
        assert "README.md" in injected.content
        assert "My Project" in injected.content

    def test_sentinel_prevents_double_injection(self, tmp_path):
        """If sentinel is present in recent context, scout is skipped."""
        hook = _make_turn_pre_hook("cheap-model", tmp_path)
        long_msg = "do something important with the database schema code " * 3
        msgs = self._make_messages(
            ("system", f"{_SCOUT_SENTINEL}\n**Context-scout pre-loaded files:**\n"),
            ("user", long_msg),
        )
        with patch("gptme.context.scout.scout_files") as mock_sf:
            result = self._run_hook(hook, msgs)
        mock_sf.assert_not_called()
        assert result == []

    def test_no_user_messages_skips_scout(self, tmp_path):
        """Hook returns nothing if there are no user messages."""
        hook = _make_turn_pre_hook("cheap-model", tmp_path)
        msgs = self._make_messages(("system", "You are a helper."))
        with patch("gptme.context.scout.scout_files") as mock_sf:
            result = self._run_hook(hook, msgs)
        mock_sf.assert_not_called()
        assert result == []
