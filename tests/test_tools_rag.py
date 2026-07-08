"""Tests for the RAG tool."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.config import RagConfig
from gptme.message import Message
from gptme.tools.rag import (
    _has_gptme_rag,
    _project_persist_dir,
    _rag_context_hook,
    _sanitize_project_name,
    rag_index,
    rag_index_conversations,
    rag_search,
    rag_status,
)


@pytest.mark.skipif(not _has_gptme_rag(), reason="RAG is not available")
def test_rag_context_hook():
    """Test that RAG context hook yields context messages."""
    messages = [
        Message("user", "Tell me about Python"),
        Message("assistant", "Python is a programming language"),
    ]

    # Call the hook
    context_msgs = list(_rag_context_hook(messages, workspace=None))

    # Should yield at least one context message
    assert len(context_msgs) >= 1
    assert all(msg.role == "system" for msg in context_msgs)


def test_rag_context_hook_no_rag():
    """Test that hook returns nothing when RAG is unavailable."""
    with patch("gptme.tools.rag._has_gptme_rag", return_value=False):
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        context_msgs = list(_rag_context_hook(messages, workspace=None))

        # Should yield nothing when RAG is not available
        assert len(context_msgs) == 0


def test_rag_context_hook_disabled():
    """Test hook when RAG is disabled in config."""
    with (
        patch("subprocess.run", return_value=type("Proc", (), {"returncode": 0})),
        patch("gptme.tools.rag.get_project_config") as mock_config,
    ):
        mock_config.return_value.rag = RagConfig(enabled=False)
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        context_msgs = list(_rag_context_hook(messages, workspace=None))

        # Should yield nothing when RAG is disabled
        assert len(context_msgs) == 0


def _write_conversation(conv_dir: Path, messages: list[dict]) -> None:
    """Write a fake conversation.jsonl for testing."""
    conv_dir.mkdir(parents=True, exist_ok=True)
    with open(conv_dir / "conversation.jsonl", "w") as f:
        f.writelines(json.dumps(msg) + "\n" for msg in messages)


def test_rag_index_conversations_no_conversations(tmp_path):
    """Test that rag_index_conversations returns a message when there are no conversations."""
    with patch("gptme.tools.rag.get_logs_dir", return_value=tmp_path):
        result = rag_index_conversations(output_dir=str(tmp_path / "export"))
    assert "No conversations found" in result


def test_rag_index_conversations_exports_user_assistant_only(tmp_path):
    """Test that only user/assistant messages are exported, not system messages."""
    logs_dir = tmp_path / "logs"
    conv_dir = logs_dir / "test-conv-1"
    _write_conversation(
        conv_dir,
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I am doing well, thanks!"},
        ],
    )

    export_dir = tmp_path / "export"

    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"

    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=logs_dir),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc),
    ):
        result = rag_index_conversations(output_dir=str(export_dir))

    assert "Indexed 1 conversations" in result

    # Check the exported file
    exported_files = list(export_dir.glob("*.md"))
    assert len(exported_files) == 1

    content = exported_files[0].read_text()
    assert "**User**: Hello, how are you?" in content
    assert "**Assistant**: I am doing well, thanks!" in content
    # System message should NOT appear
    assert "You are a helpful assistant" not in content


def test_rag_index_conversations_skips_empty(tmp_path):
    """Test that conversations with no user/assistant messages are skipped."""
    logs_dir = tmp_path / "logs"
    # Conversation with only system messages
    _write_conversation(
        logs_dir / "system-only",
        [{"role": "system", "content": "System only conversation"}],
    )
    # Normal conversation
    _write_conversation(
        logs_dir / "normal-conv",
        [
            {"role": "user", "content": "A question"},
            {"role": "assistant", "content": "An answer"},
        ],
    )

    export_dir = tmp_path / "export"
    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"

    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=logs_dir),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc),
    ):
        result = rag_index_conversations(output_dir=str(export_dir))

    assert "Indexed 1 conversations" in result
    exported_files = list(export_dir.glob("*.md"))
    assert len(exported_files) == 1


def test_rag_index_conversations_respects_n_limit(tmp_path):
    """Test that the n parameter limits the number of conversations indexed."""
    logs_dir = tmp_path / "logs"
    for i in range(5):
        conv_dir = logs_dir / f"conv-{i}"
        _write_conversation(
            conv_dir,
            [
                {"role": "user", "content": f"Question {i}"},
                {"role": "assistant", "content": f"Answer {i}"},
            ],
        )
        mtime = 1_700_000_000 + i
        os.utime(conv_dir / "conversation.jsonl", (mtime, mtime))

    export_dir = tmp_path / "export"
    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 3 paths\n"

    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=logs_dir),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc),
    ):
        result = rag_index_conversations(n=3, output_dir=str(export_dir))

    exported_files = list(export_dir.glob("*.md"))
    assert len(exported_files) == 3
    assert {path.stem for path in exported_files} == {"conv-2", "conv-3", "conv-4"}
    assert "Indexed 3 conversations" in result


def test_rag_index_conversations_rejects_non_positive_n(tmp_path):
    """Test that non-positive n is rejected instead of silently mis-indexing."""
    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=tmp_path),
        pytest.raises(ValueError, match="n must be a positive integer"),
    ):
        rag_index_conversations(n=0, output_dir=str(tmp_path / "export"))


def test_rag_index_conversations_writes_utf8_exports(tmp_path):
    """Test that exported conversations are always written as UTF-8."""
    logs_dir = tmp_path / "logs"
    _write_conversation(
        logs_dir / "unicode-conv",
        [
            {"role": "user", "content": "Hej 👋"},
            {"role": "assistant", "content": "Hallå världen"},
        ],
    )

    export_dir = tmp_path / "export"
    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"

    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=logs_dir),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc),
        patch("pathlib.Path.write_text", autospec=True) as mock_write_text,
    ):
        result = rag_index_conversations(output_dir=str(export_dir))

    assert "Indexed 1 conversations" in result
    mock_write_text.assert_called_once_with(
        export_dir / "unicode-conv.md",
        "**User**: Hej 👋\n\n**Assistant**: Hallå världen",
        encoding="utf-8",
    )


def test_rag_index_conversations_uses_tmpdir_by_default(tmp_path):
    """Test that a temp dir is used when no output_dir is specified."""
    logs_dir = tmp_path / "logs"
    _write_conversation(
        logs_dir / "my-conv",
        [
            {"role": "user", "content": "A question"},
            {"role": "assistant", "content": "An answer"},
        ],
    )

    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"
    captured_cmd = []

    def mock_run_rag_cmd(cmd):
        captured_cmd.extend(cmd)
        return mock_proc

    with (
        patch("gptme.tools.rag.get_logs_dir", return_value=logs_dir),
        patch("gptme.tools.rag._run_rag_cmd", side_effect=mock_run_rag_cmd),
    ):
        result = rag_index_conversations()

    assert "Indexed 1 conversations" in result
    # The temp dir path should have been passed to gptme-rag index
    assert "gptme-rag-convs-" in captured_cmd[-1]


# --- Project-specific index tests ---


def test_sanitize_project_name_basic():
    assert _sanitize_project_name("myproject") == "myproject"


def test_sanitize_project_name_slashes():
    assert _sanitize_project_name("org/repo") == "org_repo"


def test_sanitize_project_name_spaces():
    assert _sanitize_project_name("my project") == "my_project"


def test_sanitize_project_name_special_chars():
    result = _sanitize_project_name("proj!@#$")
    assert "/" not in result
    assert " " not in result
    assert len(result) > 0


def test_sanitize_project_name_dot_traversal():
    # "." and ".." must not survive as the sanitized name (path-traversal hazard)
    assert _sanitize_project_name(".") == "default"
    assert _sanitize_project_name("..") == "default"
    # Internal dots in valid names must be preserved
    assert _sanitize_project_name("v1.2.3") == "v1.2.3"
    assert _sanitize_project_name("my.project") == "my.project"


def test_project_persist_dir_dot_traversal(tmp_path):
    with patch("gptme.tools.rag.get_data_dir", return_value=tmp_path):
        d_dot = _project_persist_dir(".")
        d_dotdot = _project_persist_dir("..")
    assert d_dot is not None
    # Must stay under rag/ — not resolve to rag/../ or rag/ root itself
    assert d_dot.name == "default"
    assert d_dotdot is not None
    assert d_dotdot.name == "default"


def test_project_persist_dir_none():
    assert _project_persist_dir(None) is None


def test_project_persist_dir_empty_string():
    assert _project_persist_dir("") is None


def test_project_persist_dir_returns_path(tmp_path):
    with patch("gptme.tools.rag.get_data_dir", return_value=tmp_path):
        d = _project_persist_dir("myproject")
    assert d is not None
    assert d == tmp_path / "rag" / "myproject"


def test_project_persist_dir_sanitizes_name(tmp_path):
    with patch("gptme.tools.rag.get_data_dir", return_value=tmp_path):
        d = _project_persist_dir("org/repo")
    assert d is not None
    # slash replaced with underscore
    assert d.name == "org_repo"


def test_rag_index_no_project_no_persist_dir():
    """Without project, no --persist-dir is passed to the CLI."""
    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"
    with patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc) as mock_run:
        rag_index(".")
    cmd = mock_run.call_args[0][0]
    assert "--persist-dir" not in cmd


def test_rag_index_with_project_passes_persist_dir(tmp_path):
    """With project set, --persist-dir is appended pointing to the project dir."""
    mock_proc = MagicMock()
    mock_proc.stdout = "Indexed 1 paths\n"
    with (
        patch("gptme.tools.rag.get_data_dir", return_value=tmp_path),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc) as mock_run,
    ):
        rag_index(".", project="myproject")
    cmd = mock_run.call_args[0][0]
    assert "--persist-dir" in cmd
    persist_dir_idx = cmd.index("--persist-dir")
    assert cmd[persist_dir_idx + 1] == str(tmp_path / "rag" / "myproject")


def test_rag_search_no_project_no_persist_dir():
    """Without project, no --persist-dir is passed."""
    mock_proc = MagicMock()
    mock_proc.stdout = "results\n"
    with patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc) as mock_run:
        rag_search("query")
    cmd = mock_run.call_args[0][0]
    assert "--persist-dir" not in cmd


def test_rag_search_with_project_passes_persist_dir(tmp_path):
    """With project set, search is scoped to the project-specific persist dir."""
    mock_proc = MagicMock()
    mock_proc.stdout = "results\n"
    with (
        patch("gptme.tools.rag.get_data_dir", return_value=tmp_path),
        patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc) as mock_run,
    ):
        rag_search("query", project="myproject")
    cmd = mock_run.call_args[0][0]
    assert "--persist-dir" in cmd
    persist_dir_idx = cmd.index("--persist-dir")
    assert cmd[persist_dir_idx + 1] == str(tmp_path / "rag" / "myproject")


def test_rag_status_no_project_calls_cli():
    """Without project, delegates to gptme-rag status CLI."""
    mock_proc = MagicMock()
    mock_proc.stdout = "Index contains 42 documents\n"
    with patch("gptme.tools.rag._run_rag_cmd", return_value=mock_proc) as mock_run:
        result = rag_status()
    assert "42 documents" in result
    cmd = mock_run.call_args[0][0]
    assert cmd == ["gptme-rag", "status"]


def test_rag_status_with_project_not_found(tmp_path):
    """project status reports missing index cleanly when dir doesn't exist."""
    with patch("gptme.tools.rag.get_data_dir", return_value=tmp_path):
        result = rag_status(project="myproject")
    assert "not found" in result
    assert "myproject" in result


def test_rag_status_with_project_found(tmp_path):
    """project status reports directory info when the index dir exists."""
    project_dir = tmp_path / "rag" / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "chroma.sqlite3").write_bytes(b"x" * 1024)

    with patch("gptme.tools.rag.get_data_dir", return_value=tmp_path):
        result = rag_status(project="myproject")

    assert "myproject" in result
    assert str(project_dir) in result


def test_project_isolation(tmp_path):
    """Index in project A must not appear when searching project B."""
    calls = []

    def fake_run_rag_cmd(cmd):
        calls.append(cmd[:])
        proc = MagicMock()
        proc.stdout = "results\n"
        return proc

    with (
        patch("gptme.tools.rag.get_data_dir", return_value=tmp_path),
        patch("gptme.tools.rag._run_rag_cmd", side_effect=fake_run_rag_cmd),
    ):
        rag_index(".", project="projectA")
        rag_search("query", project="projectB")

        index_cmd, search_cmd = calls
        # Both commands use --persist-dir
        assert "--persist-dir" in index_cmd
        assert "--persist-dir" in search_cmd

        # The persist dirs must differ between project A and project B
        idx_persist = index_cmd[index_cmd.index("--persist-dir") + 1]
        search_persist = search_cmd[search_cmd.index("--persist-dir") + 1]
        assert idx_persist != search_persist

        # Global search (no project) must not pass --persist-dir at all
        calls.clear()
        rag_search("query")
        global_cmd = calls[0]
        assert "--persist-dir" not in global_cmd
