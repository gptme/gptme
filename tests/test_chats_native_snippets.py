"""Native search snippets use real synthetic logs, never user history."""

import json
import re

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_chats import chats
from gptme.tools.chats import _format_message_with_context, read_chat, search_chats


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    def write(content: str, name: str = "2026-01-01-synthetic") -> str:
        directory = tmp_path / name
        directory.mkdir(exist_ok=True)
        messages = [
            {
                "role": "system",
                "content": "hidden setup",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
            {
                "role": "user",
                "content": content,
                "timestamp": "2026-01-01T00:00:01+00:00",
            },
            {
                "role": "assistant",
                "content": "safe continuation",
                "timestamp": "2026-01-01T00:00:02+00:00",
            },
        ]
        (directory / "conversation.jsonl").write_text(
            "\n".join(json.dumps(m) for m in messages) + "\n"
        )
        return name

    return write


@pytest.mark.parametrize(
    ("content", "query", "highlights"),
    [
        ("prefix needle suffix", "needle", ["**needle**"]),
        ("İ needle", "needle", ["İ **needle**"]),
        ("İstanbul city", "i\u0307stanbul", ["**İstanbul** city"]),
        ("x" * 250 + " TARGET end", "TARGET", ["**TARGET**"]),
        (
            "x" * 250 + "\nalpha\nbeta\nend",
            "alpha\nbeta",
            ["2| **alpha**", "3| **beta**"],
        ),
    ],
)
def test_native_and_click_snippets(history, capsys, content, query, highlights):
    history(content)
    search_chats(query, context_lines=0)
    native = capsys.readouterr().out
    cli = CliRunner().invoke(chats, ["search", query, "--context", "0"])
    assert cli.exit_code == 0, cli.exception
    assert cli.output == native
    assert "message 2, user" in native
    for highlight in highlights:
        assert highlight in native
    machine = CliRunner().invoke(chats, ["search", query, "--json"])
    assert machine.exit_code == 0, machine.exception
    result = json.loads(machine.output)
    snippet = result[0]["snippets"][0]
    assert snippet["index"] == 1  # Legacy machine coordinate remains zero-based.
    assert query.lower() in snippet["content"].lower()
    assert snippet["content"] in content
    assert len(snippet["content"]) <= 200


@pytest.mark.parametrize(
    ("content", "query", "expected"),
    [
        ("needle end", "needle", "**needle** end"),
        ("start needle", "needle", "start **needle**"),
        ("İİ needle NEEDLE", "needle", "İİ **needle** **NEEDLE**"),
        ("İstanbul", "i", "**İ**stanbul"),
        ("ΟΣ middle", "ος", "**ΟΣ** middle"),
    ],
)
def test_original_spans(content, query, expected):
    assert expected in _format_message_with_context(content, query, max_matches=3)


def test_multiline_context_and_match_limits():
    content = "before\nalpha\nbeta\nbetween\nalpha\nbeta\nafter"
    result = _format_message_with_context(
        content, "alpha\nbeta", context_lines=1, max_matches=2
    )
    assert result.count("4| between") == 1
    assert "1| before" in result and "7| after" in result
    assert result.count("**alpha**") == result.count("**beta**") == 2
    limited = _format_message_with_context(
        content, "alpha\nbeta", context_lines=0, max_matches=1
    )
    assert limited.count("**alpha**") == 1
    assert "(+1 more matches)" in limited


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_is_bounded(query):
    assert _format_message_with_context("hello", query) == "hello"


def test_long_query_and_lossless_continuation(history, capsys):
    query = "MATCH" * 60 + "\nend"
    content = "x" * 250 + query + "z" * 250
    name = history(content)
    result = CliRunner().invoke(chats, ["search", query, "--json"])
    assert result.exit_code == 0, result.exception
    snippet = json.loads(result.output)[0]["snippets"][0]["content"]
    assert query in snippet
    assert len(snippet) == len(query)  # Query length, not an unrelated fixed prefix.
    read_chat(name, start_message=2, max_results=1, full=True)
    assert content in capsys.readouterr().out


def test_no_hit_and_hidden_system(history, capsys):
    history("only regular content")
    search_chats("hidden setup")
    assert "No results found" in capsys.readouterr().out
    machine = CliRunner().invoke(chats, ["search", "absent", "--json"])
    assert machine.exit_code == 0
    assert json.loads(machine.output) == []
    search_chats("hidden setup", system=True)
    assert "message 1, system" in capsys.readouterr().out


def test_repeated_matches_and_context_before(history, capsys):
    name = history("before\nneedle\nneedle\nafter")
    search_chats("needle", max_matches=2, context_lines=1)
    output = capsys.readouterr().out
    assert output.count("1| before") == 1
    assert output.count("**needle**") == 2
    read_chat(name, start_message=2, max_results=1, context_messages=1, full=True)
    output = capsys.readouterr().out
    assert "hidden setup" not in output
    assert "before\nneedle\nneedle\nafter" in output


def test_multiline_original_line_numbers():
    result = _format_message_with_context(
        "zero\none\ntwo\nthree", "one\ntwo", context_lines=0
    )
    assert re.search(r"2\| \*\*one\*\*", result)
    assert re.search(r"3\| \*\*two\*\*", result)
    assert "zero" not in result and "three" not in result


@pytest.mark.parametrize(
    ("content", "query"),
    [
        ("begin TARGET" + "x" * 300, "target"),
        ("x" * 300 + "TARGET", "target"),
        ("İ" * 250 + " TARGET end", "target"),
        ("x" * 250 + "İstanbul end", "i\u0307stanbul"),
        ("x" * 250 + "alpha\n\nbeta end", "alpha\n\nbeta"),
        ("x" * 250 + "ΟΣ" + "x" * 250, "οσ"),
    ],
)
def test_json_excerpt_boundaries(history, content, query):
    history(content)
    result = CliRunner().invoke(chats, ["search", query, "--json"])
    assert result.exit_code == 0, result.exception
    snippet = json.loads(result.output)[0]["snippets"][0]["content"]
    assert query.lower() in snippet.lower()
    assert snippet in content
    # Existing excerpt policy expands only to retain a long/context-sensitive match.
    assert len(snippet) <= max(200, len(query))


def test_substring_not_regex_or_casefold(history, capsys):
    history("İ a+b Straße")
    search_chats("a+b")
    assert "**a+b**" in capsys.readouterr().out
    search_chats("STRASSE")
    assert "No results found" in capsys.readouterr().out


@pytest.mark.parametrize("max_matches", [0, -1])
def test_nonpositive_match_count(max_matches):
    result = _format_message_with_context(
        "needle needle", "needle", max_matches=max_matches
    )
    assert "**" not in result
    assert "(+2 more matches)" in result


def test_json_keeps_three_snippet_limit(history, tmp_path):
    name = history("needle")
    path = tmp_path / name / "conversation.jsonl"
    messages = [json.loads(line) for line in path.read_text().splitlines()]
    messages.extend([{**messages[1], "content": "needle"}] * 4)
    path.write_text("\n".join(json.dumps(message) for message in messages) + "\n")
    result = CliRunner().invoke(chats, ["search", "needle", "--json"])
    assert result.exit_code == 0, result.exception
    payload = json.loads(result.output)[0]
    assert payload["matches"] == 5
    assert len(payload["snippets"]) == 3
    assert [snippet["index"] for snippet in payload["snippets"]] == [1, 3, 4]
