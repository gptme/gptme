"""Tests for form_autodetect hook."""

from typing import Literal
from unittest.mock import MagicMock, patch

from gptme.hooks.form_autodetect import (
    _create_form_message,
    _detect_options_heuristic,
    form_autodetect_hook,
    register,
)
from gptme.message import Message


class TestDetectOptionsHeuristic:
    """Tests for _detect_options_heuristic regex detection."""

    # --- Numbered lists ---

    def test_numbered_list_dot(self):
        content = "Choose one:\n1. React\n2. Vue\n3. Svelte"
        assert _detect_options_heuristic(content)

    def test_numbered_list_paren(self):
        content = "Options:\n1) First option\n2) Second option\n3) Third option"
        assert _detect_options_heuristic(content)

    def test_single_numbered_item_no_match(self):
        content = "1. This is just one item"
        assert not _detect_options_heuristic(content)

    # --- Lettered lists ---

    def test_lettered_list_dot(self):
        content = "a. Use TypeScript\nb. Use JavaScript"
        assert _detect_options_heuristic(content)

    def test_lettered_list_paren(self):
        content = "A) Option Alpha\nB) Option Beta\nC) Option Gamma"
        assert _detect_options_heuristic(content)

    # --- Bullet points ---

    def test_bullet_dash(self):
        content = "Frameworks:\n- React\n- Vue\n- Angular"
        assert _detect_options_heuristic(content)

    def test_bullet_asterisk(self):
        content = "* First choice\n* Second choice"
        assert _detect_options_heuristic(content)

    def test_bullet_unicode(self):
        content = "• Option A\n• Option B"
        assert _detect_options_heuristic(content)

    # --- "Please choose/select" patterns ---

    def test_please_choose(self):
        content = "Please choose one of the following"
        assert _detect_options_heuristic(content)

    def test_select_an_option(self):
        content = "Please select an option from the list below"
        assert _detect_options_heuristic(content)

    def test_pick_one(self):
        content = "Pick one from these alternatives"
        assert _detect_options_heuristic(content)

    # --- Question with options ---

    def test_question_with_numbered_options(self):
        content = "Which framework do you want?\n1. React\n2. Vue\n3. Svelte"
        assert _detect_options_heuristic(content)

    # --- "Which would you prefer" patterns ---

    def test_which_would_you_prefer(self):
        content = "Which would you prefer for the database layer?"
        assert _detect_options_heuristic(content)

    def test_which_do_you_want(self):
        content = "Which do you want to use?"
        assert _detect_options_heuristic(content)

    def test_which_option(self):
        content = "Which option should we go with?"
        assert _detect_options_heuristic(content)

    # --- Options/choices header ---

    def test_options_header(self):
        content = "Options:\n- Use Redis\n- Use Memcached"
        assert _detect_options_heuristic(content)

    def test_choices_header(self):
        content = "Choices:\nReact or Vue"
        assert _detect_options_heuristic(content)

    def test_alternatives_header(self):
        content = "Alternatives:\n1. Plan A\n2. Plan B"
        assert _detect_options_heuristic(content)

    # --- Negative cases (should NOT match) ---

    def test_plain_text_no_match(self):
        content = "This is just a regular paragraph about programming."
        assert not _detect_options_heuristic(content)

    def test_code_with_numbers_no_match(self):
        # Single numbered lines shouldn't match
        content = "def main():\n    return 42"
        assert not _detect_options_heuristic(content)

    def test_empty_string(self):
        assert not _detect_options_heuristic("")

    def test_explanation_no_options(self):
        content = "The function works by computing the hash and comparing it."
        assert not _detect_options_heuristic(content)


class TestCreateFormMessage:
    """Tests for _create_form_message helper."""

    def test_basic_form_creation(self):
        parsed = {
            "detected": True,
            "question": "Which framework?",
            "options": ["React", "Vue", "Svelte"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert msg.role == "assistant"
        assert "```form" in msg.content
        assert "Which framework?" in msg.content
        assert "React" in msg.content
        assert "Vue" in msg.content
        assert "Svelte" in msg.content

    def test_form_with_default_question(self):
        parsed = {
            "detected": True,
            "options": ["Option A", "Option B"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert "Please select an option" in msg.content

    def test_none_input(self):
        msg = _create_form_message(None)
        assert msg is None

    def test_empty_options(self):
        parsed = {"detected": True, "options": []}
        msg = _create_form_message(parsed)
        assert msg is None

    def test_no_options_key(self):
        parsed = {"detected": True}
        msg = _create_form_message(parsed)
        assert msg is None

    def test_two_options(self):
        parsed = {
            "detected": True,
            "question": "Yes or no?",
            "options": ["Yes", "No"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert "Yes, No" in msg.content

    def test_form_message_format(self):
        """Verify the exact form format."""
        parsed = {
            "detected": True,
            "question": "Pick a DB",
            "options": ["PostgreSQL", "MySQL"],
        }
        msg = _create_form_message(parsed)
        assert msg is not None
        assert msg.content == "```form\nselection: Pick a DB [PostgreSQL, MySQL]\n```"


class TestFormAutodetectHook:
    """Tests for the main form_autodetect_hook function."""

    def _make_message(
        self,
        content: str,
        role: Literal["system", "user", "assistant"] = "assistant",
    ) -> Message:
        return Message(role, content)

    def test_ignores_non_assistant_messages(self):
        """Hook only processes assistant messages."""
        msg = self._make_message("1. Option A\n2. Option B", role="user")
        results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_disabled_by_default(self, mock_config):
        """Hook is disabled unless FORM_AUTO_DETECT=true."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = False
        mock_config.return_value = mock_cfg

        with patch("gptme.tools.has_tool", return_value=True):
            msg = self._make_message("1. Option A\n2. Option B\n3. Option C")
            results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_skips_if_form_tool_not_loaded(self, mock_config):
        """Hook exits early if form tool is not loaded."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        with patch("gptme.tools.has_tool", return_value=False):
            msg = self._make_message("1. Option A\n2. Option B\n3. Option C")
            results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_skips_short_messages(self, mock_config):
        """Hook skips messages shorter than 50 chars."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        with patch("gptme.tools.has_tool", return_value=True):
            msg = self._make_message("1. A\n2. B")  # Too short
            results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_skips_very_long_messages(self, mock_config):
        """Hook skips messages longer than 5000 chars."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        with patch("gptme.tools.has_tool", return_value=True):
            msg = self._make_message("x" * 5001)
            results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_skips_existing_form_content(self, mock_config):
        """Hook skips messages that already contain form blocks."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        with patch("gptme.tools.has_tool", return_value=True):
            msg = self._make_message(
                "Choose one:\n1. A\n2. B\n```form\nselection: test [A, B]\n```"
            )
            results = list(form_autodetect_hook(msg))
        assert results == []

    @patch("gptme.hooks.form_autodetect._parse_options_with_llm")
    @patch("gptme.hooks.form_autodetect.get_config")
    def test_full_pipeline_with_llm(self, mock_config, mock_llm):
        """Full pipeline: heuristics trigger → LLM parse → form creation."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        mock_llm.return_value = {
            "detected": True,
            "question": "Which framework?",
            "options": ["React", "Vue", "Svelte"],
        }

        with patch("gptme.tools.has_tool", return_value=True):
            content = (
                "I can help you set up a frontend framework. "
                "Which one would you prefer?\n\n"
                "1. React - Popular, large ecosystem\n"
                "2. Vue - Easy to learn, great docs\n"
                "3. Svelte - Compiled, fast runtime"
            )
            msg = self._make_message(content)
            results = list(form_autodetect_hook(msg))

        assert len(results) == 2  # system message + form message
        msgs = [r for r in results if isinstance(r, Message)]
        assert len(msgs) == 2
        assert msgs[0].role == "system"
        assert msgs[0].hide is True
        assert msgs[1].role == "assistant"
        assert "```form" in msgs[1].content

    @patch("gptme.hooks.form_autodetect._parse_options_with_llm")
    @patch("gptme.hooks.form_autodetect.get_config")
    def test_llm_returns_no_options(self, mock_config, mock_llm):
        """When LLM says no valid options detected, no form is created."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        mock_llm.return_value = None  # LLM says not a selection prompt

        with patch("gptme.tools.has_tool", return_value=True):
            content = (
                "Here are some features of the framework:\n"
                "1. Fast rendering\n"
                "2. Component-based\n"
                "3. Virtual DOM"
            )
            msg = self._make_message(content)
            results = list(form_autodetect_hook(msg))

        assert results == []

    @patch("gptme.hooks.form_autodetect.get_config")
    def test_no_heuristic_match_skips_llm(self, mock_config):
        """When heuristics don't match, LLM is never called."""
        mock_cfg = MagicMock()
        mock_cfg.get_env_bool.return_value = True
        mock_config.return_value = mock_cfg

        with (
            patch("gptme.tools.has_tool", return_value=True),
            patch("gptme.hooks.form_autodetect._parse_options_with_llm") as mock_llm,
        ):
            msg = self._make_message(
                "This is a regular explanation about how databases work. "
                "The query optimizer analyzes the execution plan."
            )
            results = list(form_autodetect_hook(msg))

        mock_llm.assert_not_called()
        assert results == []


class TestRegister:
    """Tests for hook registration."""

    def test_register_adds_hook(self):
        """register() adds form_autodetect to the hook registry."""
        from gptme.hooks import HookType, get_hooks

        register()

        hooks = get_hooks(HookType.GENERATION_POST)
        hook_names = [h.name for h in hooks]
        assert "form_autodetect" in hook_names
