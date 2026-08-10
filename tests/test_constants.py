"""Tests for prompt helpers in gptme.constants."""

from rich.console import Console

from gptme.constants import prompt_assistant, prompt_user


def _render(markup: str) -> str:
    """Render a Rich markup string to plain text (tags parsed, no color)."""
    console = Console(record=True, width=80, color_system=None)
    console.print(markup)
    return console.export_text()


def test_prompt_assistant_escapes_markup_in_name():
    """An agent name containing Rich markup syntax renders literally.

    Regression: prompt_assistant interpolated the name verbatim, so a name
    like "Agent [v2]" had "[v2]" swallowed by Rich as a (malformed) markup
    tag — the brackets and their content vanished from the rendered output.
    prompt_user already escaped its name; prompt_assistant now matches.
    """
    raw = prompt_assistant("Agent [v2]")
    # The opening bracket is backslash-escaped so Rich treats it as literal.
    assert "\\[v2]" in raw
    # The surrounding style tags stay literal (not escaped).
    assert "[bold " in raw
    # Round-trip through Rich: the full name, brackets included, is visible.
    rendered = _render(raw)
    assert "Agent [v2]" in rendered
    assert "[v2]" in rendered  # brackets survived (the buggy path dropped them)


def test_prompt_assistant_default_name(monkeypatch):
    """Falls back to GPTME_AGENT_NAME when no name is passed."""
    monkeypatch.setenv("GPTME_AGENT_NAME", "DefaultBot")
    assert "DefaultBot" in prompt_assistant(None)


def test_prompt_assistant_escapes_like_prompt_user():
    """Both prompt helpers escape user-controlled text identically."""
    name = "C++ [dev]"
    assert prompt_assistant(name).count("\\[") == prompt_user(name).count("\\[")
