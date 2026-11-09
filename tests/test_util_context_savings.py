import json
from pathlib import Path

from gptme.util.context_savings import (
    CONTEXT_SAVINGS_FILENAME,
    record_context_savings,
    summarize_context_savings,
)


def test_record_context_savings_appends_jsonl(tmp_path: Path):
    saved_path = tmp_path / "tool-outputs" / "shell" / "saved.txt"
    saved_path.parent.mkdir(parents=True)
    saved_path.write_text("full output")

    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=1200,
        kept_tokens=240,
        command_info="git log --oneline",
        saved_path=saved_path,
    )

    ledger = tmp_path / CONTEXT_SAVINGS_FILENAME
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["source"] == "shell"
    assert rows[0]["original_tokens"] == 1200
    assert rows[0]["kept_tokens"] == 240
    assert rows[0]["saved_tokens"] == 960
    assert rows[0]["command_info"] == "git log --oneline"
    assert rows[0]["saved_path"] == str(saved_path)


def test_summarize_context_savings_aggregates_per_source(tmp_path: Path):
    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=1000,
        kept_tokens=300,
        command_info="gh issue list",
        saved_path=tmp_path / "one.txt",
    )
    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=900,
        kept_tokens=400,
        command_info="git log --oneline",
        saved_path=tmp_path / "two.txt",
    )
    record_context_savings(
        logdir=tmp_path,
        source="tmux",
        original_tokens=500,
        kept_tokens=200,
        command_info="capture-pane",
        saved_path=tmp_path / "three.txt",
    )

    summary = summarize_context_savings(tmp_path)

    assert summary.entries == 3
    assert summary.total_saved_tokens == 1500
    assert summary.saved_tokens_by_source == {"shell": 1200, "tmux": 300}
    assert summary.calls_by_source == {"shell": 2, "tmux": 1}
    assert summary.max_saved_tokens == 700


def test_summarize_context_savings_missing_file(tmp_path: Path):
    summary = summarize_context_savings(tmp_path)

    assert summary.entries == 0
    assert summary.total_saved_tokens == 0
    assert summary.saved_tokens_by_source == {}
    assert summary.calls_by_source == {}
    assert summary.max_saved_tokens == 0


def test_summarize_context_savings_skips_non_numeric_saved_tokens(tmp_path: Path):
    """A row with a non-numeric saved_tokens must be skipped, not crash the read.

    Malformed rows are already tolerated for invalid JSON; a non-numeric
    ``saved_tokens`` used to escape that guard and raise ValueError from
    ``int()``, aborting the whole aggregation.
    """
    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=1000,
        kept_tokens=200,
    )
    with (tmp_path / CONTEXT_SAVINGS_FILENAME).open("a", encoding="utf-8") as f:
        f.write('{"source": "read", "saved_tokens": "n/a"}\n')
        f.write('{"source": "read", "saved_tokens": "12oops"}\n')

    summary = summarize_context_savings(tmp_path)

    assert summary.entries == 1
    assert summary.total_saved_tokens == 800
    assert summary.saved_tokens_by_source == {"shell": 800}
    assert summary.max_saved_tokens == 800


def test_summarize_context_savings_skips_non_object_rows(tmp_path: Path):
    """Valid JSON that is not an object has no .get() and must be skipped."""
    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=1000,
        kept_tokens=200,
    )
    with (tmp_path / CONTEXT_SAVINGS_FILENAME).open("a", encoding="utf-8") as f:
        f.write("[1, 2, 3]\n")
        f.write("42\n")
        f.write("null\n")

    summary = summarize_context_savings(tmp_path)

    assert summary.entries == 1
    assert summary.total_saved_tokens == 800
    assert summary.saved_tokens_by_source == {"shell": 800}
