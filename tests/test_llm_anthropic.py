import logging
import os

import pytest

import gptme.llm.llm_anthropic as llm_anthropic
from gptme.llm.llm_anthropic import (
    _HAS_OUTPUT_CONFIG,
    _adjust_thinking_budget,
    _build_thinking_param,
    _output_config_kwargs,
    _prepare_messages_for_api,
    _requires_adaptive_thinking,
    _resolve_effort_level,
    _resolve_thinking_budget,
)
from gptme.message import Message
from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY
from gptme.tools import get_tool, init_tools


def test_reinit_requires_non_empty_api_key():
    with pytest.raises(ValueError, match="api_key must be non-empty"):
        llm_anthropic.reinit("")


def test_message_conversion():
    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
    ]

    messages_dicts, system_messages, tools = _prepare_messages_for_api(messages, None)

    assert tools is None

    assert system_messages == [
        {
            "type": "text",
            "text": "Initial Message\n\nProject prompt",
            "cache_control": {"type": "ephemeral"},
        }
    ]

    assert list(messages_dicts) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "First user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]


def test_message_conversion_without_tools():
    init_tools(allowlist=["save"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            content="<thinking>\nSomething\n</thinking>\n```save path.txt\nfile_content\n```",
        ),
        Message(role="system", content="Saved to toto.txt"),
    ]

    messages_dicts, _, _ = _prepare_messages_for_api(messages, None)

    assert messages_dicts == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "First user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "<thinking>\nSomething\n</thinking>\n```save path.txt\nfile_content\n```",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Saved to toto.txt</system>",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


def test_message_conversion_with_tools():
    init_tools(allowlist=["save"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(role="user", content="First user prompt"),
        Message(
            role="assistant",
            # Include the embedded signature so the thinking block survives round-trip.
            content='<thinking>\nSomething\n<!-- think-sig: test-sig-abc== -->\n</thinking>\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(role="system", content="(Modified by user)", call_id="tool_call_id"),
    ]

    tool_save = get_tool("save")

    assert tool_save

    messages_dicts, _, tools = _prepare_messages_for_api(messages, [tool_save])

    assert tools == [
        {
            "name": "save",
            "description": "Create or overwrite a file with the given content.\n\n"
            "The path can be relative to the current directory, or absolute.\n"
            "If the current directory changes, the path will be relative to the "
            "new directory.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path of the file"},
                    "content": {"type": "string", "description": "The content to save"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        }
    ]

    # NOTE: <thinking> tags are converted to proper Anthropic thinking blocks.
    # The embedded <!-- think-sig: ... --> comment is parsed out to supply the
    # required `signature` field; without it the Anthropic API returns a 400.
    assert list(messages_dicts) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "First user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Something",
                    "signature": "test-sig-abc==",
                },
                {
                    "type": "tool_use",
                    "id": "tool_call_id",
                    "name": "save",
                    "input": {"path": "path.txt", "content": "file_content"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "text",
                            "text": "Saved to toto.txt\n\n(Modified by user)",
                        }
                    ],
                    "tool_use_id": "tool_call_id",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


def test_message_conversion_with_tool_and_non_tool():
    init_tools(allowlist=["save", "shell"])

    messages = [
        Message(role="system", content="Initial Message", pinned=True, hide=True),
        Message(role="system", content="Project prompt", hide=True),
        Message(
            role="assistant",
            content='\n@save(tool_call_id): {"path": "path.txt", "content": "file_content"}',
        ),
        Message(role="system", content="Saved to toto.txt", call_id="tool_call_id"),
        Message(
            role="assistant",
            content=(
                "The script `hello.py` has been created. "
                "Run it using the command:\n\n```shell\npython hello.py\n```"
            ),
        ),
        Message(
            role="system",
            content="Ran command: `python hello.py`\n\n `Hello, world!`\n\n",
        ),
    ]

    tool_save = get_tool("save")
    tool_shell = get_tool("shell")

    assert tool_save and tool_shell

    messages_dicts, _, _ = _prepare_messages_for_api(messages, [tool_save, tool_shell])

    assert messages_dicts == [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_call_id",
                    "name": "save",
                    "input": {"path": "path.txt", "content": "file_content"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": [{"type": "text", "text": "Saved to toto.txt"}],
                    "tool_use_id": "tool_call_id",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The script `hello.py` has been created. Run it using the command:\n\n```shell\npython hello.py\n```",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>Ran command: `python hello.py`\n\n `Hello, world!`\n\n</system>",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


def test_boundary_keeps_dynamic_context_out_of_static_system_prompt():
    messages = [
        Message(role="system", content="Core prompt"),
        Message(role="system", content="Static workspace prompt"),
        Message(role="system", content=SYSTEM_PROMPT_CACHE_BOUNDARY),
        Message(role="system", content="Dynamic context"),
        Message(role="user", content="Actual user prompt"),
    ]

    messages_dicts, system_messages, _ = _prepare_messages_for_api(messages, None)

    assert system_messages == [
        {
            "type": "text",
            "text": "Core prompt\n\nStatic workspace prompt",
            "cache_control": {"type": "ephemeral"},
        }
    ]

    assert messages_dicts == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system>"
                    + SYSTEM_PROMPT_CACHE_BOUNDARY
                    + "</system>\n\n<system>Dynamic context</system>\n\nActual user prompt",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]


# Updated tests for generator retry behavior


def test_retry_generator_only_retries_before_yield():
    """Test that retry_generator_on_overloaded only retries if no content has been yielded.

    This prevents duplicate output when an error occurs mid-stream.
    Issue: https://github.com/gptme/gptme/issues/1030 (Finding 4)
    """
    import os

    from gptme.llm.llm_anthropic import retry_generator_on_overloaded

    # Create a mock that looks like an Anthropic API 500 error
    def make_api_error():
        from anthropic import APIStatusError
        from httpx import Request, Response

        request = Request("POST", "https://api.anthropic.com/v1/messages")
        response = Response(500, request=request)
        return APIStatusError("Internal server error", response=response, body=None)

    # Clear the test max retries env var for this test
    old_val = os.environ.pop("GPTME_TEST_MAX_RETRIES", None)
    try:
        # Track call count to verify retry behavior
        call_count = 0

        @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
        def gen_fails_before_yield():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise make_api_error()
            yield "success"

        @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
        def gen_fails_after_yield():
            yield "chunk1"
            yield "chunk2"
            raise make_api_error()

        # Test 1: Should retry when error occurs before any yield
        call_count = 0
        result = list(gen_fails_before_yield())
        assert result == ["success"], f"Expected ['success'], got {result}"
        assert call_count == 3, f"Expected 3 calls (2 retries), got {call_count}"

        # Test 2: Should NOT retry when error occurs after yielding
        # (would cause duplicate output)
        collected = []
        try:
            for chunk in gen_fails_after_yield():
                collected.append(chunk)  # noqa: PERF402
        except Exception:
            pass  # Expected to raise

        # Should have received chunks before error, and NOT duplicated
        assert collected == [
            "chunk1",
            "chunk2",
        ], f"Expected ['chunk1', 'chunk2'], got {collected}"
    finally:
        if old_val is not None:
            os.environ["GPTME_TEST_MAX_RETRIES"] = old_val


def test_retry_generator_preserves_return_value():
    """Test that retry_generator_on_overloaded preserves generator return values."""
    from gptme.llm.llm_anthropic import retry_generator_on_overloaded

    @retry_generator_on_overloaded(max_retries=3, base_delay=0.01)
    def gen_with_return():
        yield "chunk1"
        yield "chunk2"
        return {"metadata": "value"}

    gen = gen_with_return()
    chunks = []
    return_value = None
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as e:
        return_value = e.value

    assert chunks == ["chunk1", "chunk2"]
    assert return_value == {"metadata": "value"}


def test_web_search_tool_enabled():
    """Test that web search tool is included when environment variable is set."""
    # Set environment variable
    os.environ["GPTME_ANTHROPIC_WEB_SEARCH"] = "true"
    os.environ["GPTME_ANTHROPIC_WEB_SEARCH_MAX_USES"] = "3"

    try:
        messages = [
            Message(
                role="system",
                content="You are a helpful assistant.",
                pinned=True,
                hide=True,
            ),
            Message(role="user", content="What's the weather today?"),
        ]

        messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
            messages, None
        )

        # Verify web search tool is included
        assert tools_dict is not None
        assert len(tools_dict) == 1
        assert tools_dict[0]["type"] == "web_search_20250305"  # type: ignore[typeddict-item]
        assert tools_dict[0]["name"] == "web_search"
        assert tools_dict[0]["max_uses"] == 3  # type: ignore[typeddict-item]
    finally:
        # Clean up environment variables
        os.environ.pop("GPTME_ANTHROPIC_WEB_SEARCH", None)
        os.environ.pop("GPTME_ANTHROPIC_WEB_SEARCH_MAX_USES", None)


def test_web_search_tool_disabled():
    """Test that web search tool is not included when environment variable is not set."""
    # Ensure environment variable is not set
    os.environ.pop("GPTME_ANTHROPIC_WEB_SEARCH", None)

    messages = [
        Message(
            role="system",
            content="You are a helpful assistant.",
            pinned=True,
            hide=True,
        ),
        Message(role="user", content="What's the weather today?"),
    ]

    messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
        messages, None
    )

    # Verify no tools are included
    assert tools_dict is None


def test_web_search_tool_with_other_tools():
    """Test that web search tool is combined with other tools."""
    os.environ["GPTME_ANTHROPIC_WEB_SEARCH"] = "true"

    try:
        init_tools(allowlist=["save"])
        tool_save = get_tool("save")
        assert tool_save is not None

        messages = [
            Message(
                role="system",
                content="You are a helpful assistant.",
                pinned=True,
                hide=True,
            ),
            Message(role="user", content="Search and save results"),
        ]

        messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
            messages, [tool_save]
        )

        # Verify both tools are included
        assert tools_dict is not None
        assert len(tools_dict) == 2

        # Check that save tool is present
        save_tool = next((t for t in tools_dict if t.get("name") == "save"), None)
        assert save_tool is not None

        # Check that web_search tool is present
        web_search_tool = next(
            (t for t in tools_dict if t.get("type") == "web_search_20250305"), None
        )
        assert web_search_tool is not None
        assert web_search_tool["max_uses"] == 5  # type: ignore[typeddict-item]  # Default value
    finally:
        os.environ.pop("GPTME_ANTHROPIC_WEB_SEARCH", None)


class TestResolveThinkingBudget:
    """_resolve_thinking_budget handles GPTME_THINKING_EFFORT and GPTME_REASONING_BUDGET."""

    def setup_method(self):
        # Isolate tests from ambient env
        self._saved = {}
        for key in ("GPTME_THINKING_EFFORT", "GPTME_REASONING_BUDGET"):
            self._saved[key] = os.environ.pop(key, None)

    def teardown_method(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_default_is_high(self):
        assert _resolve_thinking_budget() == 16000

    def test_reasoning_budget_integer(self):
        os.environ["GPTME_REASONING_BUDGET"] = "12345"
        assert _resolve_thinking_budget() == 12345

    def test_reasoning_budget_invalid_raises(self):
        os.environ["GPTME_REASONING_BUDGET"] = "not-a-number"
        with pytest.raises(ValueError, match="GPTME_REASONING_BUDGET"):
            _resolve_thinking_budget()

    @pytest.mark.parametrize(
        ("effort", "expected"),
        [
            ("low", 2000),
            ("medium", 8000),
            ("high", 16000),
            ("xhigh", 24000),
            ("max", 32000),
        ],
    )
    def test_effort_levels(self, effort, expected):
        os.environ["GPTME_THINKING_EFFORT"] = effort
        assert _resolve_thinking_budget() == expected

    def test_effort_case_insensitive(self):
        os.environ["GPTME_THINKING_EFFORT"] = "XHIGH"
        assert _resolve_thinking_budget() == 24000

    def test_effort_invalid_raises(self):
        os.environ["GPTME_THINKING_EFFORT"] = "extreme"
        with pytest.raises(ValueError, match="GPTME_THINKING_EFFORT"):
            _resolve_thinking_budget()

    def test_effort_wins_when_both_set(self, caplog):
        os.environ["GPTME_THINKING_EFFORT"] = "low"
        os.environ["GPTME_REASONING_BUDGET"] = "99999"
        with caplog.at_level(logging.WARNING, logger="gptme.llm.llm_anthropic"):
            assert _resolve_thinking_budget() == 2000
        assert any(
            "GPTME_THINKING_EFFORT" in rec.message
            and "GPTME_REASONING_BUDGET" in rec.message
            for rec in caplog.records
        )


class TestResolveEffortLevel:
    """_resolve_effort_level returns the raw level string or None."""

    def setup_method(self):
        self._saved = os.environ.pop("GPTME_THINKING_EFFORT", None)

    def teardown_method(self):
        if self._saved is None:
            os.environ.pop("GPTME_THINKING_EFFORT", None)
        else:
            os.environ["GPTME_THINKING_EFFORT"] = self._saved

    def test_returns_none_when_not_set(self):
        assert _resolve_effort_level() is None

    @pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
    def test_returns_level_string(self, level):
        os.environ["GPTME_THINKING_EFFORT"] = level
        assert _resolve_effort_level() == level

    def test_normalises_to_lowercase(self):
        os.environ["GPTME_THINKING_EFFORT"] = "XHIGH"
        assert _resolve_effort_level() == "xhigh"

    def test_raises_on_invalid_level(self):
        os.environ["GPTME_THINKING_EFFORT"] = "extreme"
        with pytest.raises(ValueError, match="Invalid GPTME_THINKING_EFFORT"):
            _resolve_effort_level()


class TestOutputConfigKwargs:
    def setup_method(self):
        self._saved = os.environ.pop("GPTME_THINKING_EFFORT", None)

    def teardown_method(self):
        if self._saved is None:
            os.environ.pop("GPTME_THINKING_EFFORT", None)
        else:
            os.environ["GPTME_THINKING_EFFORT"] = self._saved

    def test_returns_empty_when_not_using_thinking(self, monkeypatch):
        monkeypatch.setattr("gptme.llm.llm_anthropic._HAS_OUTPUT_CONFIG", True)
        os.environ["GPTME_THINKING_EFFORT"] = "xhigh"

        assert _output_config_kwargs(use_thinking=False) == {}

    def test_returns_empty_when_sdk_does_not_support_output_config(self, monkeypatch):
        monkeypatch.setattr("gptme.llm.llm_anthropic._HAS_OUTPUT_CONFIG", False)
        os.environ["GPTME_THINKING_EFFORT"] = "xhigh"

        assert _output_config_kwargs(use_thinking=True) == {}

    def test_returns_empty_when_effort_is_unset(self, monkeypatch):
        monkeypatch.setattr("gptme.llm.llm_anthropic._HAS_OUTPUT_CONFIG", True)

        assert _output_config_kwargs(use_thinking=True) == {}

    def test_returns_output_config_when_supported(self, monkeypatch):
        monkeypatch.setattr("gptme.llm.llm_anthropic._HAS_OUTPUT_CONFIG", True)
        os.environ["GPTME_THINKING_EFFORT"] = "XHIGH"

        assert _output_config_kwargs(use_thinking=True) == {
            "output_config": {"effort": "xhigh"}
        }


def test_has_output_config_is_bool():
    """_HAS_OUTPUT_CONFIG must be a bool (True when SDK >= 0.77)."""
    assert isinstance(_HAS_OUTPUT_CONFIG, bool)


class TestRequiresAdaptiveThinking:
    """Opus 4.7+ rejects ``thinking.type=enabled`` with HTTP 400."""

    @pytest.mark.parametrize(
        "model",
        [
            "claude-opus-4-7",
            "anthropic/claude-opus-4-7",
            "openrouter/anthropic/claude-opus-4-7",
            "claude-opus-4-7-20260401",
            "anthropic/claude-opus-4-7-20260401",
        ],
    )
    def test_adaptive_required(self, model):
        assert _requires_adaptive_thinking(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-opus-4-5-20251101",
            "anthropic/claude-opus-4-6",
            "openrouter/anthropic/claude-sonnet-4-5",
            "claude-haiku-4-5",
        ],
    )
    def test_legacy_still_used(self, model):
        assert _requires_adaptive_thinking(model) is False


class TestBuildThinkingParam:
    """_build_thinking_param dispatches on model and thinking flag."""

    def test_disabled_returns_none(self):
        assert (
            _build_thinking_param(
                "claude-opus-4-7", use_thinking=False, thinking_budget=8000
            )
            is None
        )

    def test_opus_47_returns_adaptive(self):
        # Opus 4.7 gets ``{"type": "adaptive"}`` — never legacy, regardless of budget.
        assert _build_thinking_param(
            "claude-opus-4-7", use_thinking=True, thinking_budget=8000
        ) == {"type": "adaptive"}

    def test_opus_47_adaptive_ignores_budget(self):
        # Budget is irrelevant once adaptive: effort flows via output_config.
        assert _build_thinking_param(
            "claude-opus-4-7", use_thinking=True, thinking_budget=32000
        ) == {"type": "adaptive"}

    def test_opus_46_returns_legacy_enabled(self):
        assert _build_thinking_param(
            "claude-opus-4-6", use_thinking=True, thinking_budget=12345
        ) == {"type": "enabled", "budget_tokens": 12345}

    def test_sonnet_returns_legacy_enabled(self):
        assert _build_thinking_param(
            "claude-sonnet-4-6", use_thinking=True, thinking_budget=4000
        ) == {"type": "enabled", "budget_tokens": 4000}

    def test_openrouter_prefix_opus_47(self):
        assert _build_thinking_param(
            "openrouter/anthropic/claude-opus-4-7",
            use_thinking=True,
            thinking_budget=8000,
        ) == {"type": "adaptive"}


class TestAdjustThinkingBudgetAdaptive:
    """_adjust_thinking_budget must not disable thinking for adaptive models."""

    def test_adaptive_model_small_max_tokens_keeps_thinking(self):
        # Legacy logic would disable thinking here (budget=8000 > max_tokens=100).
        # For Opus 4.7 (adaptive), budget_tokens is irrelevant — thinking stays on.
        budget, use_thinking = _adjust_thinking_budget(
            max_tokens=100,
            thinking_budget=8000,
            use_thinking=True,
            model="claude-opus-4-7",
        )
        assert use_thinking is True
        assert budget == 8000  # unchanged; adaptive doesn't use this field

    def test_adaptive_model_tiny_max_tokens_keeps_thinking(self):
        # Even max_tokens=1 must not suppress adaptive thinking.
        budget, use_thinking = _adjust_thinking_budget(
            max_tokens=1,
            thinking_budget=8000,
            use_thinking=True,
            model="claude-opus-4-7",
        )
        assert use_thinking is True

    def test_adaptive_model_prefixed_keeps_thinking(self):
        budget, use_thinking = _adjust_thinking_budget(
            max_tokens=50,
            thinking_budget=16000,
            use_thinking=True,
            model="anthropic/claude-opus-4-7",
        )
        assert use_thinking is True

    def test_legacy_model_small_max_tokens_disables_thinking(self):
        # Legacy path unchanged: Opus 4.6 with tiny max_tokens still disables.
        _, use_thinking = _adjust_thinking_budget(
            max_tokens=50,
            thinking_budget=8000,
            use_thinking=True,
            model="claude-opus-4-6",
        )
        assert use_thinking is False

    def test_adaptive_thinking_disabled_returns_unchanged(self):
        # use_thinking=False stays False regardless of model.
        budget, use_thinking = _adjust_thinking_budget(
            max_tokens=100,
            thinking_budget=8000,
            use_thinking=False,
            model="claude-opus-4-7",
        )
        assert use_thinking is False
        assert budget == 8000
