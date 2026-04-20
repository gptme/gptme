"""Unit tests for gptme/init.py — core initialization module.

Tests cover:
- init() orchestration and double-init guard
- init_model() provider/model parsing, auto-detection, env overrides
- init_logging() configuration and cleanup
"""

import logging
import os
from unittest.mock import MagicMock, call, patch

import pytest

from gptme.llm.models.types import CustomProvider, ModelMeta

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_init_done():
    """Reset the _init_done guard before each test."""
    import gptme.init as mod

    mod._init_done = False
    yield
    mod._init_done = False


@pytest.fixture
def mock_config():
    """Create a mock config object with sensible defaults."""
    config = MagicMock()
    config.chat = MagicMock()
    config.chat.model = None
    config.get_env.return_value = None
    return config


@pytest.fixture
def dummy_model_meta():
    """A minimal ModelMeta for testing."""
    return ModelMeta(
        provider="anthropic",
        model="anthropic/claude-sonnet-4-6-20260101",
        context=200000,
        max_output=8192,
    )


# ===========================================================================
# init() — orchestration tests
# ===========================================================================


class TestInit:
    """Tests for the main init() function."""

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_calls_all_subsystems(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """init() should call load_dotenv, init_model, init_tools, init_hooks, init_commands, set_tool_format."""
        from gptme.init import init

        init(
            model="anthropic/claude-sonnet-4-6",
            interactive=True,
            tool_allowlist=None,
            tool_format="markdown",
            no_confirm=False,
            server=False,
        )

        mock_dotenv.assert_called_once()
        mock_init_model.assert_called_once_with("anthropic/claude-sonnet-4-6", True)
        mock_init_tools.assert_called_once_with(None)
        mock_init_hooks.assert_called_once_with(
            interactive=True, no_confirm=False, server=False
        )
        mock_init_commands.assert_called_once()
        mock_set_fmt.assert_called_once_with("markdown")

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_double_call_skips_subsystems(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """Second call to init() should only update tool_format."""
        from gptme.init import init

        init(model="m", interactive=False, tool_allowlist=None, tool_format="markdown")
        # Reset call counts
        mock_init_model.reset_mock()
        mock_init_tools.reset_mock()
        mock_init_hooks.reset_mock()
        mock_init_commands.reset_mock()
        mock_dotenv.reset_mock()
        mock_set_fmt.reset_mock()

        init(model="m", interactive=False, tool_allowlist=None, tool_format="xml")

        mock_dotenv.assert_not_called()
        mock_init_model.assert_not_called()
        mock_init_tools.assert_not_called()
        mock_init_hooks.assert_not_called()
        mock_init_commands.assert_not_called()
        mock_set_fmt.assert_called_once_with("xml")

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_tool_allowlist_passed(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """Tool allowlist should be forwarded to init_tools."""
        from gptme.init import init

        allowlist = ["shell", "python"]
        init(
            model="m",
            interactive=False,
            tool_allowlist=allowlist,
            tool_format="markdown",
        )
        mock_init_tools.assert_called_once_with(allowlist)

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_server_mode(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """Server mode and no_confirm should be forwarded to init_hooks."""
        from gptme.init import init

        init(
            model="m",
            interactive=False,
            tool_allowlist=None,
            tool_format="tool",
            no_confirm=True,
            server=True,
        )
        mock_init_hooks.assert_called_once_with(
            interactive=False, no_confirm=True, server=True
        )

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_tool_format_xml(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """XML tool format should be passed through."""
        from gptme.init import init

        init(model="m", interactive=False, tool_allowlist=None, tool_format="xml")
        mock_set_fmt.assert_called_once_with("xml")

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_tool_format_changes_on_reinit(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """Re-entry should allow changing tool_format (needed by test suites)."""
        from gptme.init import init

        init(model="m", interactive=False, tool_allowlist=None, tool_format="markdown")
        init(model="m", interactive=False, tool_allowlist=None, tool_format="xml")
        init(model="m", interactive=False, tool_allowlist=None, tool_format="tool")

        assert mock_set_fmt.call_args_list[-1] == call("tool")

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model", side_effect=ValueError("No API key found"))
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_require_llm_true_propagates_error(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """require_llm=True (default) should re-raise init_model failures."""
        from gptme.init import init

        with pytest.raises(ValueError, match="No API key found"):
            init(
                model=None,
                interactive=False,
                tool_allowlist=None,
                tool_format="markdown",
            )
        # Subsystems that come after init_model must not have been called
        mock_init_tools.assert_not_called()
        mock_init_hooks.assert_not_called()
        mock_init_commands.assert_not_called()

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model", side_effect=ValueError("No API key found"))
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_require_llm_false_continues_on_error(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
        caplog,
    ):
        """require_llm=False should swallow config errors and init the rest."""
        from gptme.init import init

        with caplog.at_level(logging.WARNING, logger="gptme.init"):
            init(
                model=None,
                interactive=False,
                tool_allowlist=None,
                tool_format="markdown",
                server=True,
                require_llm=False,
            )

        mock_init_tools.assert_called_once()
        mock_init_hooks.assert_called_once()
        mock_init_commands.assert_called_once()
        mock_set_fmt.assert_called_once_with("markdown")
        assert any(
            "Continuing without a default model" in rec.message
            for rec in caplog.records
        )

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch(
        "gptme.init.init_model",
        side_effect=KeyError("ANTHROPIC_API_KEY not set in env or config"),
    )
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_require_llm_false_handles_keyerror(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """require_llm=False should also swallow KeyError from missing API keys."""
        from gptme.init import init

        init(
            model="anthropic/claude-sonnet-4-6",
            interactive=False,
            tool_allowlist=None,
            tool_format="markdown",
            server=True,
            require_llm=False,
        )
        mock_init_tools.assert_called_once()
        mock_init_hooks.assert_called_once()
        mock_init_commands.assert_called_once()

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_can_be_retried_after_failure(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """If init_model fails, subsequent init() calls should actually run
        (not be short-circuited by the _init_done guard)."""
        from gptme.init import init

        mock_init_model.side_effect = [ValueError("No API key found"), None]

        # First call raises
        with pytest.raises(ValueError, match="No API key found"):
            init(
                model=None,
                interactive=False,
                tool_allowlist=None,
                tool_format="markdown",
            )

        # Second call succeeds — tools/hooks/commands must run this time
        mock_init_tools.reset_mock()
        mock_init_hooks.reset_mock()
        mock_init_commands.reset_mock()

        init(
            model="anthropic/claude-sonnet-4-6",
            interactive=False,
            tool_allowlist=None,
            tool_format="markdown",
        )

        mock_init_tools.assert_called_once()
        mock_init_hooks.assert_called_once()
        mock_init_commands.assert_called_once()


# ===========================================================================
# init_model() — provider/model parsing
# ===========================================================================


class TestInitModelParsing:
    """Tests for init_model() provider and model name resolution."""

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_builtin_provider_with_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'anthropic/claude-sonnet-4-6' should parse to provider='anthropic', model='claude-sonnet-4-6'."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="anthropic/claude-sonnet-4-6")

        mock_init_llm.assert_called_once_with("anthropic")
        mock_get_model.assert_called_once_with("anthropic/claude-sonnet-4-6")
        mock_set_default.assert_called_once_with(dummy_model_meta)

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="gpt-5")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_provider_only_no_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'openai' alone should use get_recommended_model to fill in the model name."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="openai")

        mock_recommend.assert_called_once_with("openai")
        mock_init_llm.assert_called_once_with("openai")
        mock_get_model.assert_called_once_with("openai/gpt-5")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_openrouter_provider_with_nested_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'openrouter/anthropic/claude-sonnet-4-6' should parse provider='openrouter'."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="openrouter/anthropic/claude-sonnet-4-6")

        mock_init_llm.assert_called_once_with("openrouter")
        mock_get_model.assert_called_once_with("openrouter/anthropic/claude-sonnet-4-6")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_custom_provider_with_model(
        self,
        mock_console,
        mock_config_fn,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'mycorp/my-model' with mycorp as custom provider should use CustomProvider."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        with patch(
            "gptme.init.is_custom_provider", side_effect=lambda p: p == "mycorp"
        ):
            init_model(model="mycorp/my-model")

        # init_llm should receive a CustomProvider instance
        args = mock_init_llm.call_args[0]
        assert isinstance(args[0], CustomProvider)
        assert str(args[0]) == "mycorp"
        mock_get_model.assert_called_once_with("mycorp/my-model")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_custom_provider_no_slash(
        self,
        mock_console,
        mock_config_fn,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """Custom provider without slash should use get_model to resolve default model."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )

        resolved_meta = ModelMeta(
            provider=CustomProvider("mycorp"),
            model="mycorp/default-model",
            context=100000,
        )
        mock_get_model.return_value = resolved_meta

        with patch(
            "gptme.init.is_custom_provider", side_effect=lambda p: p == "mycorp"
        ):
            init_model(model="mycorp")

        # Should call get_model to resolve the default model for this custom provider
        assert mock_get_model.call_count >= 1
        # init_llm should receive a CustomProvider
        args = mock_init_llm.call_args[0]
        assert isinstance(args[0], CustomProvider)

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_unknown_provider_treated_as_provider_only(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'unknownprov/model' where unknownprov is neither builtin nor custom
        should treat the entire string as provider, model=None."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_recommend.return_value = "default-model"
        mock_get_model.return_value = dummy_model_meta

        init_model(model="unknownprov/some-model")

        # Should have called get_recommended_model since model_name was None
        mock_recommend.assert_called_once()

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="gemini-pro")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_gemini_provider(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'gemini' should be recognized as a builtin provider."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="gemini")

        mock_init_llm.assert_called_once_with("gemini")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_custom_provider_multi_slash_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """'mycorp/org/model-name' should extract model_name='org/model-name'."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        with patch(
            "gptme.init.is_custom_provider", side_effect=lambda p: p == "mycorp"
        ):
            init_model(model="mycorp/org/model-name")

        mock_get_model.assert_called_once_with("mycorp/org/model-name")


# ===========================================================================
# init_model() — config and auto-detection
# ===========================================================================


class TestInitModelConfig:
    """Tests for init_model() config resolution and auto-detection."""

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_model_from_config_chat(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """When model=None, should use config.chat.model."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat.model = "anthropic/claude-sonnet-4-6"
        config.get_env.return_value = None
        mock_config_fn.return_value = config
        mock_get_model.return_value = dummy_model_meta

        init_model(model=None)

        mock_init_llm.assert_called_once_with("anthropic")
        mock_get_model.assert_called_once_with("anthropic/claude-sonnet-4-6")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_model_from_env(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """When config.chat.model is None, should fall back to config.get_env('MODEL')."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat.model = None
        config.get_env.return_value = "openai/gpt-5"
        mock_config_fn.return_value = config
        mock_get_model.return_value = dummy_model_meta

        init_model(model=None)

        config.get_env.assert_called_with("MODEL")
        mock_init_llm.assert_called_once_with("openai")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_model_from_config_no_chat(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """When config.chat is None, should fall back to get_env."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat = None
        config.get_env.return_value = "anthropic/claude-haiku-4-5"
        mock_config_fn.return_value = config
        mock_get_model.return_value = dummy_model_meta

        init_model(model=None)

        mock_init_llm.assert_called_once_with("anthropic")

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.guess_provider_from_config", return_value=None)
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_no_model_no_provider_raises(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_guess,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """When no model and no provider can be detected, should raise ValueError."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat = MagicMock(model=None)
        config.get_env.return_value = None
        mock_config_fn.return_value = config

        with pytest.raises(ValueError, match="No API key found"):
            init_model(model=None, interactive=False)

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="gpt-5")
    @patch("gptme.init.guess_provider_from_config", return_value="openai")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_model_explicit_overrides_config(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_guess,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """Explicit model parameter should take priority over config."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat = MagicMock(model="anthropic/claude-sonnet-4-6")
        mock_config_fn.return_value = config
        mock_get_model.return_value = dummy_model_meta

        init_model(model="openai/gpt-5")

        mock_init_llm.assert_called_once_with("openai")
        mock_get_model.assert_called_once_with("openai/gpt-5")


# ===========================================================================
# init_model() — GPTME_CONTEXT_LENGTH override
# ===========================================================================


class TestInitModelContextOverride:
    """Tests for GPTME_CONTEXT_LENGTH environment variable override."""

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_context_length_override_valid(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """GPTME_CONTEXT_LENGTH should override the model's context length."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="anthropic",
            model="anthropic/claude-sonnet-4-6",
            context=200000,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {"GPTME_CONTEXT_LENGTH": "500000"}):
            init_model(model="anthropic/claude-sonnet-4-6")

        # set_default_model should receive a model with overridden context
        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 500000
        # Other fields should be preserved
        assert saved_meta.provider == "anthropic"
        assert saved_meta.model == "anthropic/claude-sonnet-4-6"

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_context_length_override_invalid(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """Invalid GPTME_CONTEXT_LENGTH should be ignored with a warning."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="anthropic",
            model="anthropic/claude-sonnet-4-6",
            context=200000,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {"GPTME_CONTEXT_LENGTH": "not-a-number"}):
            init_model(model="anthropic/claude-sonnet-4-6")

        # Context should remain unchanged
        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 200000

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_no_context_length_env(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """Without GPTME_CONTEXT_LENGTH, context should not be modified."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="openai",
            model="openai/gpt-5",
            context=128000,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {}, clear=False):
            # Ensure GPTME_CONTEXT_LENGTH is not set
            os.environ.pop("GPTME_CONTEXT_LENGTH", None)
            init_model(model="openai/gpt-5")

        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 128000

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_context_length_override_small_value(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """Small context length values (e.g. for local models) should work."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="local",
            model="local/llama-3",
            context=8192,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {"GPTME_CONTEXT_LENGTH": "4096"}):
            init_model(model="local/llama-3")

        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 4096

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_context_length_override_empty_string(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """Empty GPTME_CONTEXT_LENGTH should be treated as invalid."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="anthropic",
            model="anthropic/claude-sonnet-4-6",
            context=200000,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {"GPTME_CONTEXT_LENGTH": ""}):
            init_model(model="anthropic/claude-sonnet-4-6")

        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 200000

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_context_length_override_preserves_other_fields(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """Context length override should not change any other ModelMeta fields."""
        from gptme.init import init_model

        original_meta = ModelMeta(
            provider="anthropic",
            model="anthropic/claude-opus-4-6",
            context=200000,
            max_output=8192,
            supports_streaming=True,
            supports_vision=True,
            supports_reasoning=True,
            price_input=15.0,
            price_output=75.0,
        )
        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = original_meta

        with patch.dict(os.environ, {"GPTME_CONTEXT_LENGTH": "1000000"}):
            init_model(model="anthropic/claude-opus-4-6")

        saved_meta = mock_set_default.call_args[0][0]
        assert saved_meta.context == 1000000
        assert saved_meta.max_output == 8192
        assert saved_meta.supports_streaming is True
        assert saved_meta.supports_vision is True
        assert saved_meta.supports_reasoning is True
        assert saved_meta.price_input == 15.0
        assert saved_meta.price_output == 75.0


# ===========================================================================
# init_model() — console logging
# ===========================================================================


class TestInitModelConsole:
    """Tests for init_model() console output."""

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_console_logs_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """init_model should log the full model name to console."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="anthropic/claude-sonnet-4-6")

        mock_console.log.assert_called_once()
        log_msg = mock_console.log.call_args[0][0]
        assert "anthropic/claude-sonnet-4-6" in log_msg

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="gpt-5")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_console_logs_resolved_model(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """When model is resolved from provider, console should show full provider/model."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="openai")

        log_msg = mock_console.log.call_args[0][0]
        assert "openai/gpt-5" in log_msg


# ===========================================================================
# init_model() — interactive mode
# ===========================================================================


class TestInitModelInteractive:
    """Tests for init_model() interactive API key prompt."""

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="gpt-5")
    @patch("gptme.init.ask_for_api_key", return_value=("openai/gpt-5", "sk-key"))
    @patch("gptme.init.guess_provider_from_config", return_value=None)
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_interactive_asks_for_key(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_guess,
        mock_ask,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """In interactive mode with no model detected, should prompt for API key."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat = MagicMock(model=None)
        config.get_env.return_value = None
        mock_config_fn.return_value = config
        mock_get_model.return_value = dummy_model_meta

        init_model(model=None, interactive=True)

        mock_ask.assert_called_once()

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.ask_for_api_key")
    @patch("gptme.init.guess_provider_from_config", return_value=None)
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_non_interactive_does_not_ask(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_guess,
        mock_ask,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
    ):
        """In non-interactive mode with no model, should raise instead of prompting."""
        from gptme.init import init_model

        config = MagicMock()
        config.chat = MagicMock(model=None)
        config.get_env.return_value = None
        mock_config_fn.return_value = config

        with pytest.raises(ValueError, match="No API key found"):
            init_model(model=None, interactive=False)

        mock_ask.assert_not_called()


# ===========================================================================
# init_model() — all builtin providers
# ===========================================================================


class TestInitModelBuiltinProviders:
    """Test that all builtin providers are correctly recognized."""

    @pytest.mark.parametrize(
        "provider",
        [
            "openai",
            "anthropic",
            "azure",
            "openrouter",
            "gptme",
            "gemini",
            "groq",
            "xai",
            "deepseek",
            "nvidia",
            "local",
            "openai-subscription",
        ],
    )
    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="test-model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_builtin_provider_recognized(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        provider,
        dummy_model_meta,
    ):
        """Each builtin provider should be recognized and passed to init_llm."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model=f"{provider}/test-model")

        mock_init_llm.assert_called_once_with(provider)

    @pytest.mark.parametrize(
        "provider",
        [
            "openai",
            "anthropic",
            "azure",
            "openrouter",
            "gptme",
            "gemini",
            "groq",
            "xai",
            "deepseek",
            "nvidia",
            "local",
            "openai-subscription",
        ],
    )
    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model", return_value="test-model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_provider_only_uses_recommended(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        provider,
        dummy_model_meta,
    ):
        """Provider-only string should call get_recommended_model."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model=provider)

        mock_recommend.assert_called_once_with(provider)


# ===========================================================================
# init_logging() tests
# ===========================================================================


class TestInitLogging:
    """Tests for init_logging() configuration."""

    def test_verbose_sets_debug_level(self):
        """Verbose mode should set root logger to DEBUG."""
        from gptme.init import init_logging

        init_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_non_verbose_sets_info_level(self):
        """Non-verbose mode should set root logger to INFO."""
        from gptme.init import init_logging

        init_logging(verbose=False)
        assert logging.getLogger().level == logging.INFO

    def test_anthropic_logger_info_level(self):
        """Anthropic logger should be set to INFO to suppress debug spam."""
        from gptme.init import init_logging

        init_logging(verbose=True)
        assert logging.getLogger("anthropic").level == logging.INFO

    def test_openai_logger_info_level(self):
        """OpenAI logger should be set to INFO."""
        from gptme.init import init_logging

        init_logging(verbose=True)
        assert logging.getLogger("openai").level == logging.INFO

    def test_httpx_logger_warning_level(self):
        """httpx logger should be set to WARNING."""
        from gptme.init import init_logging

        init_logging(verbose=False)
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_httpcore_logger_warning_level(self):
        """httpcore logger should be set to WARNING."""
        from gptme.init import init_logging

        init_logging(verbose=False)
        assert logging.getLogger("httpcore").level == logging.WARNING

    def test_rich_handler_added(self):
        """Root logger should have a RichHandler."""
        from rich.logging import RichHandler as _RichHandler

        from gptme.init import init_logging

        init_logging(verbose=False)
        handlers = logging.getLogger().handlers
        assert any(isinstance(h, _RichHandler) for h in handlers)

    def test_atexit_cleanup_registered(self):
        """An atexit cleanup handler should be registered."""
        from gptme.init import init_logging

        with patch("atexit.register") as mock_register:
            init_logging(verbose=False)
            mock_register.assert_called_once()
            # The registered function should be a callable
            registered_fn = mock_register.call_args[0][0]
            assert callable(registered_fn)

    def test_otel_filter_applied_when_available(self):
        """When opentelemetry is installed, debouncing filter should be applied."""
        from gptme.init import init_logging

        mock_filter = MagicMock()
        with (
            patch.dict(
                "sys.modules",
                {
                    "gptme.util._telemetry": MagicMock(
                        get_connection_error_filter=MagicMock(return_value=mock_filter)
                    )
                },
            ),
            patch("logging.getLogger") as mock_get_logger,
        ):
            mock_otel_logger = MagicMock()
            mock_get_logger.return_value = mock_otel_logger
            init_logging(verbose=False)
            mock_get_logger.assert_any_call("opentelemetry")
            mock_otel_logger.addFilter.assert_called_once_with(mock_filter)

    def test_otel_filter_skipped_when_unavailable(self):
        """When opentelemetry is not installed, filter setup should be silently skipped."""
        from gptme.init import init_logging

        # This should not raise even if opentelemetry is not installed
        with patch.dict("sys.modules", {"gptme.util._telemetry": None}):
            # Simulates ImportError during import
            init_logging(verbose=False)

    def test_verbose_false_then_true(self):
        """Calling init_logging with force=True allows re-configuration."""
        from gptme.init import init_logging

        init_logging(verbose=False)
        assert logging.getLogger().level == logging.INFO

        init_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG


# ===========================================================================
# Edge cases and integration
# ===========================================================================


class TestInitEdgeCases:
    """Edge cases and integration tests for init module."""

    def test_init_done_flag_default(self):
        """_init_done should start as False (reset by fixture)."""
        import gptme.init as mod

        assert mod._init_done is False

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_sets_done_flag(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """init() should set _init_done to True."""
        import gptme.init as mod
        from gptme.init import init

        assert mod._init_done is False
        init(model="m", interactive=False, tool_allowlist=None, tool_format="markdown")
        assert mod._init_done is True

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_order_of_operations(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """Subsystems should be initialized in correct order."""
        from gptme.init import init

        call_order = []
        mock_dotenv.side_effect = lambda: call_order.append("dotenv")
        mock_init_model.side_effect = lambda *a, **kw: call_order.append("model")
        mock_init_tools.side_effect = lambda *a, **kw: call_order.append("tools")
        mock_init_hooks.side_effect = lambda *a, **kw: call_order.append("hooks")
        mock_init_commands.side_effect = lambda: call_order.append("commands")
        mock_set_fmt.side_effect = lambda *a, **kw: call_order.append("tool_format")

        init(model="m", interactive=False, tool_allowlist=None, tool_format="markdown")

        assert call_order == [
            "dotenv",
            "model",
            "tools",
            "hooks",
            "commands",
            "tool_format",
        ]

    @patch("gptme.init.set_default_model")
    @patch("gptme.init.get_model")
    @patch("gptme.init.init_llm")
    @patch("gptme.init.get_recommended_model")
    @patch("gptme.init.is_custom_provider", return_value=False)
    @patch("gptme.init.get_config")
    @patch("gptme.init.console")
    def test_init_model_with_openai_subscription(
        self,
        mock_console,
        mock_config_fn,
        mock_custom,
        mock_recommend,
        mock_init_llm,
        mock_get_model,
        mock_set_default,
        dummy_model_meta,
    ):
        """openai-subscription provider should be recognized."""
        from gptme.init import init_model

        mock_config_fn.return_value = MagicMock(
            chat=MagicMock(model=None), get_env=MagicMock(return_value=None)
        )
        mock_get_model.return_value = dummy_model_meta

        init_model(model="openai-subscription/gpt-5")

        mock_init_llm.assert_called_once_with("openai-subscription")

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_with_all_tool_formats(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
    ):
        """All tool format values should be accepted."""
        from typing import get_args

        import gptme.init as mod
        from gptme.init import init
        from gptme.tools.base import ToolFormat

        for fmt in get_args(ToolFormat):
            mod._init_done = False
            mock_set_fmt.reset_mock()
            init(model="m", interactive=False, tool_allowlist=None, tool_format=fmt)
            mock_set_fmt.assert_called_once_with(fmt)

    @patch("gptme.init.init_commands")
    @patch("gptme.init.init_hooks")
    @patch("gptme.init.init_tools")
    @patch("gptme.init.init_model")
    @patch("gptme.init.set_tool_format")
    @patch("gptme.init.load_dotenv")
    def test_init_double_call_logs_warning(
        self,
        mock_dotenv,
        mock_set_fmt,
        mock_init_model,
        mock_init_tools,
        mock_init_hooks,
        mock_init_commands,
        caplog,
    ):
        """Second init() call should log a warning."""
        from gptme.init import init

        init(model="m", interactive=False, tool_allowlist=None, tool_format="markdown")

        with caplog.at_level(logging.WARNING, logger="gptme.init"):
            init(model="m", interactive=False, tool_allowlist=None, tool_format="xml")

        assert "init() called twice" in caplog.text
