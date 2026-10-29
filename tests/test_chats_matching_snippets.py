"""Real external-session files must display the text that matched the query."""

import json
from pathlib import Path

import pytest

from gptme.tools.chats import (
    _search_codex_session,
    _search_cursor_session,
    search_external_chats,
)


@pytest.mark.parametrize("kind", ["cursor", "alternate", "codex"])
@pytest.mark.parametrize("offset", [0, 190, 250, 490, 900])
@pytest.mark.parametrize("query", ["Needle_RECEIPT", "évidence", "Q" * 600])
def test_returned_and_displayed_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    offset: int,
    query: str,
) -> None:
    cursor = tmp_path / "cursor" / "conversations"
    cursor.mkdir(parents=True)
    codex = tmp_path / "codex"
    codex.mkdir()
    # Lowercasing İ expands to two code points: matching offsets must still
    # index the original string, not the expanded lowercased text.
    content = "İ" * offset + query.swapcase() + " trailing\ncontext" * 50
    if kind == "codex":
        path = codex / "rollout-synthetic.jsonl"
        payload = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": content}],
        }
        path.write_text(json.dumps({"type": "response_item", "payload": payload}))
        hits = _search_codex_session(path, query)
    elif kind == "alternate":
        path = cursor.parent / "cursor-chat-history.json"
        path.write_text(
            json.dumps(
                {
                    "allConversations": [
                        {
                            "composerId": "test",
                            "bubbles": [{"type": "user", "text": content}],
                        }
                    ]
                }
            )
        )
        hits = _search_cursor_session(path, query)
    else:
        session = cursor / "synthetic"
        session.mkdir()
        path = session / "conversation.json"
        path.write_text(
            json.dumps(
                {"title": "test", "messages": [{"role": "user", "content": content}]}
            )
        )
        hits = _search_cursor_session(path, query)
    assert len(hits) == 1
    assert query.lower() in hits[0]["content"].lower()
    assert len(hits[0]["content"]) <= max(500, len(query))
    search_external_chats(query, cursor_dir=cursor, codex_dir=codex)
    snippet_lines = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("   (")
    ]
    assert len(snippet_lines) == 1
    assert query.lower() in snippet_lines[0].lower()
    # Only the message excerpt is budgeted (not role or indentation).
    excerpt = snippet_lines[0].split(": ", 1)[1].removesuffix("…")
    assert len(excerpt) <= max(200, len(query))


def test_external_no_hit_and_one_match_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = tmp_path / "conversations"
    session = cursor / "synthetic"
    session.mkdir(parents=True)
    path = session / "conversation.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "first match"},
                    {"role": "assistant", "content": "second match"},
                ]
            }
        )
    )
    search_external_chats("absent", cursor_dir=cursor, include_codex=False)
    assert capsys.readouterr().out == ""
    search_external_chats("match", cursor_dir=cursor, include_codex=False)
    lines = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("   (")
    ]
    assert len(lines) == 1
    assert "first match" in lines[0]


@pytest.mark.parametrize("prefix", ["ordinary " * 100, "İ" * 900])
def test_match_at_message_end(tmp_path: Path, prefix: str) -> None:
    path = tmp_path / "conversation.json"
    path.write_text(json.dumps({"messages": [{"content": prefix + "THE_END"}]}))
    hit = _search_cursor_session(path, "the_end")[0]
    assert hit["content"].endswith("THE_END")
    assert len(hit["content"]) <= 500


def test_newline_match_display_normalization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cursor = tmp_path / "conversations"
    session = cursor / "synthetic"
    session.mkdir(parents=True)
    (session / "conversation.json").write_text(
        json.dumps(
            {"messages": [{"role": "user", "content": "x" * 900 + "first\nsecond"}]}
        )
    )
    hit = _search_cursor_session(session / "conversation.json", "first\nsecond")[0]
    assert "first\nsecond" in hit["content"]
    search_external_chats("first\nsecond", cursor_dir=cursor, include_codex=False)
    snippets = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("   (")
    ]
    assert len(snippets) == 1
    assert "first second" in snippets[0]


@pytest.mark.parametrize("kind", ["cursor", "alternate", "codex"])
@pytest.mark.parametrize(
    ("content", "query"),
    [
        ("A" * 600 + "ΣA", "a" * 600 + "σ"),
        ("AΣ" + " " * 600, "ς" + " " * 600),
        # Case-ignorable combining marks can put the decisive context far
        # outside the nominal excerpt boundary.
        ("A" * 600 + "Σ" + "\u0301" * 1000 + "A", "a" * 600 + "σ"),
        ("A" + "\u0301" * 1000 + "Σ" + " " * 600, "ς" + " " * 600),
    ],
    ids=[
        "following-cased",
        "preceding-cased",
        "distant-following",
        "distant-preceding",
    ],
)
def test_context_sensitive_lowercase_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    content: str,
    query: str,
) -> None:
    cursor = tmp_path / "cursor" / "conversations"
    cursor.mkdir(parents=True)
    codex = tmp_path / "codex"
    codex.mkdir()
    if kind == "codex":
        path = codex / "rollout-synthetic.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}],
                    },
                }
            )
        )
        hits = _search_codex_session(path, query)
    elif kind == "alternate":
        path = cursor.parent / "cursor-chat-history.json"
        path.write_text(
            json.dumps(
                {"allConversations": [{"bubbles": [{"type": "user", "text": content}]}]}
            )
        )
        hits = _search_cursor_session(path, query)
    else:
        session = cursor / "synthetic"
        session.mkdir()
        path = session / "conversation.json"
        path.write_text(json.dumps({"messages": [{"content": content}]}))
        hits = _search_cursor_session(path, query)
    assert query.lower() in content.lower()
    assert len(hits) == 1
    assert query.lower() in hits[0]["content"].lower()
    search_external_chats(query, cursor_dir=cursor, codex_dir=codex)
    snippets = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("   (")
    ]
    assert len(snippets) == 1
    assert query.lower() in snippets[0].lower()
