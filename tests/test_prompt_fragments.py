"""Named deployment/user fragments use the shared prompt assembly path."""

from contextvars import ContextVar
from pathlib import Path

import pytest
import tomlkit

from gptme.config import Config, UserConfig, UserPromptConfig
from gptme.message import Message
from gptme.profiles import Profile
from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY, get_prompt, get_prompt_stats


@pytest.fixture
def fragment_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    config = Config(
        user=UserConfig(
            prompt=UserPromptConfig(
                fragments={
                    "preview": "Development apps are reachable through /preview/{port}/.",
                    "disabled": "",
                    "whitespace": " \n",
                    "additional": "Keep the deployment's existing instructions.",
                }
            )
        )
    )
    monkeypatch.setattr(
        "gptme.config.core._config_var", ContextVar("test_config", default=config)
    )
    monkeypatch.setattr("gptme.prompts.prompt_chat_history", lambda: [])
    return config


@pytest.mark.parametrize(
    "prompt", ["full", "short", "full-noexamples", "Custom rules."]
)
@pytest.mark.parametrize("interactive", [True, False])
def test_prompt_fragments_preserve_base_and_profile(
    fragment_config: Config, prompt: str, interactive: bool
) -> None:
    profile = Profile(
        name="test", description="Test profile", system_prompt="Profile instructions."
    )
    messages = get_prompt([], prompt=prompt, interactive=interactive, profile=profile)
    content = "\n\n".join(msg.content for msg in messages)
    for name in ("additional", "preview"):
        assert content.count(fragment_config.user.prompt.fragments[name]) == 1
    assert content.count(profile.system_prompt) == 1
    assert content.index(profile.system_prompt) < content.index(
        fragment_config.user.prompt.fragments["additional"]
    )
    if prompt == "Custom rules.":
        assert content.startswith(prompt)
    if SYSTEM_PROMPT_CACHE_BOUNDARY in content:
        assert content.index(fragment_config.user.prompt.fragments["preview"]) < (
            content.index(SYSTEM_PROMPT_CACHE_BOUNDARY)
        )
    assert all(msg.hide and msg.pinned for msg in messages)


def test_prompt_fragments_survive_selective_context_without_workspace(
    fragment_config: Config,
) -> None:
    messages = get_prompt([], context_mode="selective", context_include=[])
    assert fragment_config.user.prompt.fragments["preview"] in messages[0].content


@pytest.mark.parametrize(
    ("include_user_context", "prompt"), [(False, "full"), (True, "none")]
)
def test_prompt_fragments_respect_opt_outs(
    fragment_config: Config, include_user_context: bool, prompt: str
) -> None:
    messages = get_prompt([], prompt=prompt, include_user_context=include_user_context)
    assert not any("/preview/" in msg.content for msg in messages)
    stats = get_prompt_stats(
        [], prompt=prompt, include_user_context=include_user_context
    )
    assert not any(
        section.name.startswith("prompt_fragment:") for section in stats.sections
    )


def test_prompt_fragments_named_stats_and_generation_are_stable(
    fragment_config: Config,
) -> None:
    messages = get_prompt([], prompt="Custom rules.", prompt_generation="replacement")
    repeated = get_prompt([], prompt="Custom rules.", prompt_generation="replacement")
    assert [msg.content for msg in messages] == [msg.content for msg in repeated]
    assert all(
        msg.metadata and msg.metadata["prompt_generation"] == "replacement"
        for msg in messages
    )
    stats = get_prompt_stats([], prompt="Custom rules.")
    assert [section.name for section in stats.sections] == [
        "custom_prompt",
        "prompt_fragment:additional",
        "prompt_fragment:preview",
    ]
    assert stats.cacheable_tokens == stats.total_tokens
    assert stats.dynamic_tokens == 0


def test_prompt_fragments_absent_preserves_custom_prompt(
    fragment_config: Config,
) -> None:
    fragment_config.user.prompt.fragments.clear()
    messages = get_prompt([], prompt="Custom rules.")
    assert len(messages) == 1
    assert messages[0].content == "Custom rules."


def test_runtime_replacement_keeps_fragments_and_chat_prompt(
    fragment_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gptme.commands.llm import _replacement_prompt
    from gptme.config import ChatConfig

    monkeypatch.setattr("gptme.config.user.config_path", str(tmp_path / "config.toml"))
    (tmp_path / "config.runtime.toml").write_text(
        tomlkit.dumps({"prompt": {"fragments": fragment_config.user.prompt.fragments}})
    )
    monkeypatch.setattr("gptme.tools.get_tools", lambda: [])
    chat_config = ChatConfig(workspace=tmp_path, system_prompt="Conversation rules.")
    messages = _replacement_prompt(
        chat_config, model="openai/gpt-4o-mini", tool_format="markdown"
    )
    assert messages[0].content.startswith("Conversation rules.")
    assert (
        messages[0].content.count(fragment_config.user.prompt.fragments["preview"]) == 1
    )
    assert messages[0].metadata and messages[0].metadata["prompt_generation"]
    assert chat_config.system_prompt == "Conversation rules."


def test_prompt_fragments_do_not_duplicate_across_workspace_layers(
    fragment_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    agent = tmp_path / "agent"
    workspace.mkdir()
    agent.mkdir()
    monkeypatch.setattr(
        "gptme.prompts.prompt_workspace",
        lambda *args, **kwargs: [Message("system", "Workspace instructions.")],
    )
    monkeypatch.setattr(
        "gptme.prompts.prompt_workspace_runtime", lambda *args, **kwargs: []
    )
    messages = get_prompt(
        [], prompt="Custom rules.", workspace=workspace, agent_path=agent
    )
    content = "\n\n".join(msg.content for msg in messages)
    assert content.count(fragment_config.user.prompt.fragments["preview"]) == 1
