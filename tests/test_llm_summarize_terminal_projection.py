"""Regression test for gptme#3708: summarize() must see complete message
content, not the reduced terminal_display_content projection.

format_msgs() defaults to substituting terminal_display_content (added in
gptme#3708 to dedup live-streamed shell/IPython output from the terminal) in
place of the full message content. summarize() feeds that formatted text to
an LLM, so it must opt out — otherwise summarizing a conversation that
includes a shell/IPython result silently drops the streamed execution
evidence.
"""

from gptme.llm import summarize
from gptme.message import Message


def test_summarize_sees_complete_content_not_terminal_projection(monkeypatch):
    captured: dict[str, str] = {}

    def fake_summarize_helper(content: str, **_kwargs) -> str:
        captured["content"] = content
        return "summary"

    monkeypatch.setattr("gptme.llm._summarize_helper", fake_summarize_helper)

    msg = Message(
        "system",
        "Executed code block.\n\nResult:\n```\nfull stdout that was streamed live\n```",
        terminal_display_content="Executed code block.",
    )

    summarize([msg])

    assert "full stdout that was streamed live" in captured["content"]
