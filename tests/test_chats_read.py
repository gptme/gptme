"""Synthetic native search-to-read recovery through functions and Click."""

import ast
import json

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.tools.chats import read_chat, search_chats


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    directory = tmp_path / "recovery-demo"
    directory.mkdir()
    content = "first line\n" + "a" * 180 + "LATE_TARGET\nfinal line"
    rows = [
        ("system", "initial system"),
        ("user", "short question"),
        ("system", "tool result"),
        ("assistant", content),
        ("user", "following question"),
    ]
    (directory / "conversation.jsonl").write_text(
        "\n".join(json.dumps({"role": role, "content": text}) for role, text in rows)
        + "\n"
    )
    return directory.name, content


def test_native_search_coordinates_recover_full_text(history, capsys):
    conv_id, content = history
    search_chats("LATE_TARGET")
    assert "message 4, assistant" in capsys.readouterr().out
    for system in (False, True):
        read_chat(
            conv_id, start_message=4, max_results=1, incl_system=system, full=True
        )
        output = capsys.readouterr().out
        assert "4. assistant" in output
        assert content in output
        assert "following question" not in output


def test_preview_stays_short_but_keeps_raw_numbers(history, capsys):
    conv_id, _ = history
    read_chat(conv_id)
    output = capsys.readouterr().out
    assert "2. User" in output
    assert "4. Assistant" in output
    assert "LATE_TARGET" not in output
    assert "first line..." in output


def test_cli_json_search_to_read(history):
    conv_id, content = history
    runner = CliRunner()
    result = runner.invoke(main, ["chats", "search", "LATE_TARGET", "--json"])
    assert result.exit_code == 0, result.output
    hit = json.loads(result.output)[0]["snippets"][0]
    assert hit["index"] == 3  # Preserve the existing zero-based JSON field.
    assert hit["message_number"] == 4
    for system in ([], ["--system"]):
        result = runner.invoke(
            main,
            [
                "chats",
                "read",
                conv_id,
                "--full",
                "--start",
                str(hit["message_number"]),
                "--limit",
                "1",
                *system,
            ],
        )
        assert result.exit_code == 0, result.output
        assert content in result.output
        assert "following question" not in result.output


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        (False, ["2. User", "4. Assistant"]),
        (True, ["3. System", "4. Assistant"]),
    ],
)
def test_visible_context_before_raw_start(history, capsys, system, expected):
    conv_id, _ = history
    read_chat(
        conv_id, start_message=4, context_messages=1, max_results=2, incl_system=system
    )
    output = capsys.readouterr().out
    for label in expected:
        assert label in output
    assert "following question" not in output


def test_bounded_character_pages_reconstruct_message(history, capsys):
    conv_id, content = history
    recovered = ""
    for offset in range(0, len(content), 37):
        read_chat(
            conv_id,
            start_message=4,
            max_results=1,
            full=True,
            content_offset=offset,
            max_chars=37,
        )
        output = capsys.readouterr().out
        # Full-mode content is emitted verbatim after the numbered role header.
        page = output.split("4. assistant:\n", 1)[1].split("\n[", 1)[0]
        recovered += page.rstrip("\n") if offset + 37 >= len(content) else page
        if offset + 37 < len(content):
            assert f"content_offset={offset + 37}" in output
    assert recovered == content


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_message": 0},
        {"start_message": -1},
        {"max_results": 0},
        {"context_messages": -1},
        {"content_offset": -1},
        {"max_chars": 0},
        {"max_chars": 100001},
        {"content_offset": 1},
    ],
)
def test_invalid_native_bounds(history, kwargs):
    with pytest.raises(ValueError, match="must be|requires"):
        read_chat(history[0], **kwargs)


def test_read_past_end_and_hidden_start(history, capsys):
    conv_id, _ = history
    read_chat(conv_id, start_message=6)
    assert "following question" not in capsys.readouterr().out
    # Hidden raw start anchors the window, rather than renumbering visible rows.
    read_chat(conv_id, start_message=3, max_results=1)
    output = capsys.readouterr().out
    assert "4. Assistant" in output
    assert "following question" not in output


@pytest.mark.parametrize(
    "args",
    [
        ["--start", "0"],
        ["--context", "-1"],
        ["--max-chars", "0"],
        ["--max-chars", "100001"],
        ["--content-offset", "-1"],
        ["--content-offset", "1"],
    ],
)
def test_cli_invalid_bounds(history, args):
    result = CliRunner().invoke(main, ["chats", "read", history[0], *args])
    assert result.exit_code != 0
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_cli_character_continuation_and_boundaries(history):
    conv_id, content = history
    runner = CliRunner()
    for offset in (0, 10, len(content), len(content) + 1):
        result = runner.invoke(
            main,
            [
                "chats",
                "read",
                conv_id,
                "--start",
                "4",
                "--limit",
                "1",
                "--full",
                "--max-chars",
                "37",
                "--content-offset",
                str(offset),
            ],
        )
        assert result.exit_code == 0, result.output
        header = f"Reading conversation: {conv_id} ({conv_id})\n4. assistant:\n"
        assert result.output.startswith(header)
        page = content[offset : offset + 37]
        assert result.output[len(header) :].startswith(page + "\n")
        if offset + 37 < len(content):
            assert f"content_offset={offset + 37}" in result.output
        else:
            assert "truncated" not in result.output


def test_first_last_and_system_search(history, capsys):
    conv_id, _ = history
    search_chats("initial system", system=True)
    assert "message 1, system" in capsys.readouterr().out
    read_chat(conv_id, start_message=1, incl_system=True, max_results=1, full=True)
    assert "1. system:\ninitial system" in capsys.readouterr().out
    read_chat(conv_id, start_message=5, max_results=1, full=True)
    assert "5. user:\nfollowing question" in capsys.readouterr().out


@pytest.mark.parametrize("system", [False, True])
def test_continuation_hint_preserves_visibility(history, tmp_path, capsys, system):
    conv_id, _ = history
    path = tmp_path / conv_id / "conversation.jsonl"
    content = "s" * 10000 + "REMAINING_SYSTEM_TEXT"
    path.write_text(
        json.dumps({"role": "system" if system else "user", "content": content})
        + "\n"
        + json.dumps({"role": "user", "content": "unrelated following message"})
        + "\n"
    )
    read_chat(conv_id, incl_system=system, max_results=1, full=True)
    output = capsys.readouterr().out
    hint = output.split("continue with ", 1)[1].split("]", 1)[0]
    # Replay only the emitted arguments, not the original call's visibility.
    call = ast.parse(f"read_chat({hint})", mode="eval").body
    assert isinstance(call, ast.Call)
    kwargs = {}
    for kw in call.keywords:
        assert kw.arg is not None
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    read_chat(conv_id, **kwargs)
    continued = capsys.readouterr().out
    assert "REMAINING_SYSTEM_TEXT" in continued
    assert "unrelated following message" not in continued


def test_full_default_cap_and_exact_boundary(history, tmp_path, capsys):
    conv_id, _ = history
    path = tmp_path / conv_id / "conversation.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[3]["content"] = "x" * 10001
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    read_chat(conv_id, start_message=4, max_results=1, full=True)
    output = capsys.readouterr().out
    assert "x" * 10000 in output
    assert "x" * 10001 not in output
    assert "content_offset=10000" in output
