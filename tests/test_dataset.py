"""Tests for gptme.dataset.trajectory_to_env.

These tests are fully offline: they create temporary git repos and
fake conversation logs rather than touching the real gptme logs directory.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.dataset.trajectory_to_env import (
    TaskEnvironment,
    _categorise_files,
    _extract_tool_call_counts,
    _find_session_commits,
    _get_entry_commit,
    _get_files_changed,
    _get_session_duration,
    _get_session_model,
    _get_task_description,
    extract_environments,
    scan_sessions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Allow git identity override so temp-repo commits don't trip the identity guard hook.
_GIT_ENV = {**os.environ, "ALLOW_GIT_IDENTITY": "1"}


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=_GIT_ENV,
        **kwargs,
    )


def make_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit.

    Uses a non-master/main branch name so the global pre-commit hook that
    guards against direct master commits in external repos doesn't fire.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    # Use a non-protected branch so the guard hook doesn't block test commits.
    _git(["init", "-b", "test-main"], cwd=repo)
    _git(["config", "user.email", "test@test.invalid"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)
    _git(["config", "commit.gpgsign", "false"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    return repo


def add_commit(repo: Path, filename: str, content: str, message: str) -> str:
    """Add a file and commit it; return the SHA."""
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(["add", filename], cwd=repo)
    _git(["commit", "-m", message], cwd=repo)
    return _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()


def add_merge_commit(repo: Path, session_id: str) -> str:
    """Create a non-fast-forward merge attributed to *session_id*."""
    _git(["switch", "-c", "session-branch"], cwd=repo)
    add_commit(repo, "merged.py", "merged\n", "feat: branch change")
    _git(["switch", "test-main"], cwd=repo)
    _git(
        [
            "merge",
            "--no-ff",
            "session-branch",
            "-m",
            f"chore: merge session ({session_id})",
        ],
        cwd=repo,
    )
    return _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()


def make_logs_dir(tmp_path: Path, session_id: str, messages: list[dict]) -> Path:
    """Create a fake gptme logs directory with one conversation."""
    logs_dir = tmp_path / "logs"
    conv_dir = logs_dir / f"2026-01-01_topic_{session_id}"
    conv_dir.mkdir(parents=True)
    jsonl = conv_dir / "conversation.jsonl"
    with jsonl.open("w") as fh:
        for msg in messages:
            fh.write(json.dumps(msg) + "\n")
    return logs_dir


def make_messages(session_id: str) -> list[dict]:
    """Return a minimal conversation log referencing *session_id*."""
    return [
        {
            "role": "system",
            "content": "You are a helpful assistant.",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        {
            "role": "user",
            "content": "Fix the broken test for utils.py",
            "timestamp": "2026-01-01T10:00:01Z",
        },
        {
            "role": "assistant",
            "content": "```shell\npython -m pytest tests/\n```",
            "timestamp": "2026-01-01T10:01:00Z",
            "metadata": {"model": "claude-sonnet-4-6"},
        },
    ]


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


def test_categorise_files_code():
    assert _categorise_files(["src/foo.py", "tests/test_foo.py"]) == "code"


def test_categorise_files_docs():
    assert _categorise_files(["docs/guide.md"]) == "docs"


def test_categorise_files_journal():
    assert _categorise_files(["journal/2026-01-01/notes.md"]) == "journal"


def test_categorise_files_other():
    assert _categorise_files([".gitignore"]) == "other"


def test_categorise_files_empty():
    assert _categorise_files([]) == "other"


def test_extract_tool_call_counts_markdown():
    messages = [
        {"role": "assistant", "content": "```shell\nls\n```\n```python\nprint()\n```"}
    ]
    counts = _extract_tool_call_counts(messages)
    assert counts["shell"] == 1
    assert counts["python"] == 1


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "```shell echo hi\nignored\n```\n```save path/to/my file.py\nx = 1\n```",
            {"shell": 1, "save": 1},
        ),
        (
            "<tool-use><shell>echo hi</shell><py>print(1)</py></tool-use>",
            {"shell": 1, "ipython": 1},
        ),
        (
            '@shell(call_1): {"command":"echo hi"}\n'
            '@shell(call_2): {"command":"echo bye"}',
            {"shell": 2},
        ),
        (
            "```shell\necho hi\n```\n"
            '<tool-use><save path="a.py">x = 1</save></tool-use>\n'
            '@shell(call_1): {"command":"echo bye"}\n'
            "```py\nprint(1)\n```",
            {"shell": 2, "save": 1, "ipython": 1},
        ),
        (
            '```json\n@ shell: {}\n@shell(example): {"command":"no"}\n```\n'
            '````text\n@shell(example2): {"command":"no"}\n'
            "```shell\nnot a call\n```\n"
            "<tool-use><shell>not a call</shell></tool-use>\n````\n"
            "```unknown\nnot a call\n```",
            {},
        ),
        (
            '```shell\n@shell(example): {"command":"not another call"}\n```',
            {"shell": 1},
        ),
        (
            '<function_calls><invoke name="py">print(1)</invoke></function_calls>\n'
            '@py(call_1): {"code":"print(2)"}',
            {"ipython": 2},
        ),
        ("```python\nprint(1)\n```\n```py\nprint(2)\n```", {"python": 1, "ipython": 1}),
        (
            '<tool-use><save path="example.txt">\n'
            '@shell(example): {"command":"echo hi"}\n'
            "</save></tool-use>",
            {"save": 1},
        ),
        (
            '<function_calls><invoke name="save" path="example.txt">\n'
            "```shell\necho hi\n```\n"
            '@shell(example): {"command":"echo hi"}\n'
            "</invoke></function_calls>\n"
            '@shell(real): {"command":"echo bye"}',
            {"save": 1, "shell": 1},
        ),
        (
            "<tool-use><save><![CDATA[<tool-use><shell>echo hi</shell>"
            "</tool-use>]]></save></tool-use>\n"
            '@shell(real): {"command":"echo bye"}',
            {"save": 1, "shell": 1},
        ),
        (
            '<function_calls><invoke name="save"><![CDATA['
            '<function_calls><invoke name="shell">echo hi</invoke>'
            "</function_calls>]]></invoke></function_calls>",
            {"save": 1},
        ),
        (
            "<tool-use><save><!-- </tool-use> --></save></tool-use>\n"
            "<tool-use><shell>echo real</shell></tool-use>",
            {"save": 1, "shell": 1},
        ),
        (
            "<!-- <tool-use><shell>echo example</shell></tool-use> -->",
            {},
        ),
        (
            '<!-- <function_calls><invoke name="shell">echo example</invoke>'
            "</function_calls> -->\n"
            "<tool-use><shell>echo real</shell></tool-use>",
            {"shell": 1},
        ),
        (
            '<!--\n@shell(example): {"command":"echo example"}\n'
            "```shell\necho example\n```\n"
            "<tool-use><shell>echo example</shell></tool-use>\n-->\n"
            '@save(real): {"content":"real"}',
            {"save": 1},
        ),
        (
            '@save(a): {"content":"<!--"}\n'
            '<tool-use><shell>echo "-->"</shell></tool-use>',
            {"save": 1, "shell": 1},
        ),
        (
            '```json\n{"content":"<!--"}\n```\n'
            '<tool-use><shell>echo "-->"</shell></tool-use>',
            {"shell": 1},
        ),
        (
            '@save(a): {"content":"<![CDATA["}\n'
            '<function_calls><invoke name="shell"><![CDATA[echo hi]]></invoke>'
            "</function_calls>",
            {"save": 1, "shell": 1},
        ),
        (
            "```json\n@shell(example): {\n```\n"
            '@save(real): {"content":"<tool-use><shell>echo hi</shell></tool-use>"}',
            {"save": 1},
        ),
        (
            '@save(real): {"path":"example.txt", "content":'
            '"<tool-use><shell>echo hi</shell></tool-use>"}',
            {"save": 1},
        ),
        (
            '<tool-use><save path="first.txt">\n```shell\necho example\n```\n'
            "</save></tool-use>\n"
            "```shell\necho real\n```\n"
            '<tool-use><save path="second.txt">\n'
            '@shell(example): {"command":"echo hi"}\n</save></tool-use>\n'
            '@shell(real): {"command":"echo bye"}',
            {"save": 2, "shell": 2},
        ),
    ],
)
@pytest.mark.parametrize("use_stdlib_xml", [False, True])
def test_extract_tool_call_counts_persisted_formats(
    content: str,
    expected: dict[str, int],
    use_stdlib_xml: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if use_stdlib_xml:
        monkeypatch.setattr("gptme.tools.base._LXML_AVAILABLE", False)
    assert (
        _extract_tool_call_counts([{"role": "assistant", "content": content}])
        == expected
    )


@pytest.mark.parametrize(
    "content",
    [
        "<!--\n```text\n-->\n<tool-use><shell>echo real</shell></tool-use>\n```",
        "<![CDATA[\n```text\n]]>\n"
        '<function_calls><invoke name="shell">echo real</invoke></function_calls>\n```',
    ],
)
@pytest.mark.parametrize("use_stdlib_xml", [False, True])
def test_extract_tool_call_counts_comment_owns_fence(
    content: str,
    use_stdlib_xml: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if use_stdlib_xml:
        monkeypatch.setattr("gptme.tools.base._LXML_AVAILABLE", False)
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        "shell": 1
    }


@pytest.mark.parametrize("outer", ["<!--\n```text\n-->", "<![CDATA[\n```text\n]]>"])
@pytest.mark.parametrize(
    "example",
    [
        "<tool-use><shell>echo example</shell></tool-use>",
        '<function_calls><invoke name="shell">echo example</invoke></function_calls>',
        '@save(example): {"content":"<tool-use><shell>example</shell></tool-use>"}',
    ],
)
@pytest.mark.parametrize("use_stdlib_xml", [False, True])
def test_extract_tool_call_counts_rebuilds_fences_after_comment(
    outer: str,
    example: str,
    use_stdlib_xml: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if use_stdlib_xml:
        monkeypatch.setattr("gptme.tools.base._LXML_AVAILABLE", False)
    content = f"{outer}\n```json\n{example}\n```"
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {}
    content += '\n@shell(real): {"command":"echo real"}'
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        "shell": 1
    }


@pytest.mark.parametrize(
    ("lang", "body"),
    [
        ("shell", "cat <<'EOF' > example.md\n```\n{example}\nEOF"),
        ("ipython", 'text = """\n```\n{example}\n"""'),
    ],
)
@pytest.mark.parametrize(
    "example",
    [
        "<tool-use><shell>echo example</shell></tool-use>",
        '@shell(example): {"command":"echo example"}',
    ],
)
@pytest.mark.parametrize("use_stdlib_xml", [False, True])
def test_extract_tool_call_counts_literal_fences(
    lang: str,
    body: str,
    example: str,
    use_stdlib_xml: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if use_stdlib_xml:
        monkeypatch.setattr("gptme.tools.base._LXML_AVAILABLE", False)
    content = f"```{lang}\n{body.replace('{example}', example)}\n```"
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        lang: 1
    }
    content += '\n<tool-use><save>real</save></tool-use>\n@read(real): {"path":"a"}'
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        lang: 1,
        "save": 1,
        "read": 1,
    }


@pytest.mark.parametrize(
    ("lang", "body"),
    [
        ("shell", "cat <<'EOF'\n```\nexample\nEOF"),
        ("ipython", 'text = """\n```\nexample\n"""'),
    ],
)
@pytest.mark.parametrize("use_stdlib_xml", [False, True])
def test_extract_tool_call_counts_literal_fence_then_real_blocks(
    lang: str,
    body: str,
    use_stdlib_xml: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if use_stdlib_xml:
        monkeypatch.setattr("gptme.tools.base._LXML_AVAILABLE", False)
    content = (
        f"```{lang}\n{body}\n```\n"
        "<tool-use><save>real</save></tool-use>\n"
        "```shell\necho real\n```"
    )
    expected = {lang: 1, "save": 1}
    expected["shell"] = expected.get("shell", 0) + 1
    assert (
        _extract_tool_call_counts([{"role": "assistant", "content": content}])
        == expected
    )


def test_extract_tool_call_counts_loaded_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gptme.tools as tools
    from gptme.tools.base import ToolSpec

    monkeypatch.setattr(
        tools,
        "get_tools",
        lambda: [
            ToolSpec(name="custom_plugin", desc="test", block_types=["custom_alias"])
        ],
    )
    content = "```custom_alias\n{}\n```"
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        "custom_plugin": 1
    }


def test_extract_tool_call_counts_thinking_offsets() -> None:
    content = (
        "<think>\na\nb\n</think>\n"
        "<tool-use><save>real</save></tool-use>\n```shell\necho hi\n```"
    )
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        "save": 1,
        "shell": 1,
    }


@pytest.mark.parametrize("lang", ["", "shell", "json"])
@pytest.mark.parametrize("suffix", ["", '\n@read(real): {"path":"a"}'])
def test_extract_tool_call_counts_empty_fence(lang: str, suffix: str) -> None:
    content = f"```{lang}\n```{suffix}"
    expected = {"shell": 1} if lang == "shell" else {}
    if suffix:
        expected["read"] = 1
    assert (
        _extract_tool_call_counts([{"role": "assistant", "content": content}])
        == expected
    )


def test_extract_tool_call_counts_fence_scan_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.codeblock import Codeblock

    original = Codeblock.iter_from_markdown
    scanned_chars = 0

    def measured(markdown: str, streaming: bool = False) -> list[Codeblock]:
        nonlocal scanned_chars
        scanned_chars += len(markdown)
        return original(markdown, streaming=streaming)

    monkeypatch.setattr(Codeblock, "iter_from_markdown", measured)
    content = "```shell\necho hi\n```\n" * 100
    assert _extract_tool_call_counts([{"role": "assistant", "content": content}]) == {
        "shell": 100
    }
    # Bound work, not wall-clock time: completed blocks must not be reparsed
    # at every later closer. Allow the ownership and format parsing passes.
    assert scanned_chars <= 10 * len(content)


def test_extract_tool_call_counts_unterminated_xml_is_bounded() -> None:
    # A subprocess deadline makes exponential regex backtracking fail safely,
    # rather than hanging the test runner on this malformed persisted message.
    script = (
        "from gptme.dataset.trajectory_to_env import _extract_tool_call_counts\n"
        "content = '<tool-use>' + '<![CDATA[x]]>' * 35\n"
        "assert _extract_tool_call_counts([{'role': 'assistant', 'content': content}]) == {}\n"
    )
    import sys

    subprocess.run([sys.executable, "-c", script], check=True, timeout=5)


def test_extract_tool_call_counts_ignores_nonassistant() -> None:
    content = '```shell\necho hi\n```\n@shell(call_1): {"command":"echo hi"}'
    assert (
        _extract_tool_call_counts(
            [{"role": role, "content": content} for role in ("system", "user", "tool")]
            + [{"role": "user", "content": [{"type": "tool_use", "name": "shell"}]}]
        )
        == {}
    )


def test_extract_tool_call_counts_cold_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gptme.tools as tools
    from gptme.tools.base import _tool_format_var

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Dataset extraction must not initialize or discover tools")

    monkeypatch.setattr(tools, "init_tools", forbidden)
    monkeypatch.setattr(tools, "get_available_tools", forbidden)
    loaded = tools._loaded_tools_var.set(None)
    initialized = tools._tools_initialized_var.set(False)
    tool_format = _tool_format_var.set("xml")
    try:
        assert _extract_tool_call_counts(
            [
                {
                    "role": "assistant",
                    "content": '```shell echo hi\nignored\n```\n\n@shell(c): {"command":"hi"}',
                }
            ]
        ) == {"shell": 2}
        assert tools._loaded_tools_var.get() is None
        assert not tools.tools_initialized()
        assert _tool_format_var.get() == "xml"
    finally:
        _tool_format_var.reset(tool_format)
        tools._tools_initialized_var.reset(initialized)
        tools._loaded_tools_var.reset(loaded)


def test_extract_tool_call_counts_tool_use_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash"},
                {"type": "tool_use", "name": "Read"},
                {"type": "tool_use", "name": "Bash"},
            ],
        }
    ]
    counts = _extract_tool_call_counts(messages)
    assert counts["Bash"] == 2
    assert counts["Read"] == 1


def test_get_task_description_string():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Fix the bug in utils.py"},
    ]
    desc = _get_task_description(messages)
    assert desc == "Fix the bug in utils.py"


def test_get_task_description_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Part A"},
                {"type": "text", "text": "Part B"},
            ],
        }
    ]
    desc = _get_task_description(messages)
    assert "Part A" in desc
    assert "Part B" in desc


def test_get_task_description_truncates():
    messages = [{"role": "user", "content": "x" * 5000}]
    desc = _get_task_description(messages)
    assert len(desc) <= 2000


def test_get_session_model():
    messages = [
        {"role": "assistant", "content": "...", "metadata": {"model": "claude-foo"}},
    ]
    assert _get_session_model(messages) == "claude-foo"


def test_get_session_model_missing():
    messages = [{"role": "assistant", "content": "..."}]
    assert _get_session_model(messages) == ""


def test_get_session_duration():
    messages = [
        {"role": "user", "content": "q", "timestamp": "2026-01-01T10:00:00Z"},
        {"role": "assistant", "content": "a", "timestamp": "2026-01-01T10:14:07Z"},
    ]
    secs = _get_session_duration(messages)
    assert abs(secs - 847.0) < 1.0


def test_get_session_duration_single():
    messages = [{"role": "user", "content": "q", "timestamp": "2026-01-01T10:00:00Z"}]
    assert _get_session_duration(messages) == 0.0


# ---------------------------------------------------------------------------
# Integration tests — real git repo
# ---------------------------------------------------------------------------


def test_find_session_commits(tmp_path):
    repo = make_repo(tmp_path)
    sha = add_commit(repo, "foo.py", "pass\n", "fix: handle edge case (a1b2)")
    commits = _find_session_commits("a1b2", repo)
    assert sha in commits


def test_find_session_commits_no_match(tmp_path):
    repo = make_repo(tmp_path)
    add_commit(repo, "bar.py", "pass\n", "fix: unrelated change")
    commits = _find_session_commits("zzzz", repo)
    assert commits == []


def test_find_session_commits_requires_exact_marker(tmp_path):
    repo = make_repo(tmp_path)
    unrelated = add_commit(repo, "bar.py", "pass\n", "fix: feed parser")
    matching = add_commit(repo, "foo.py", "pass\n", "fix: parser (feed)")

    commits = _find_session_commits("feed", repo)

    assert matching in commits
    assert unrelated not in commits


def test_find_session_commits_does_not_search_divergent_refs(tmp_path):
    repo = make_repo(tmp_path)
    _git(["switch", "-c", "other"], cwd=repo)
    add_commit(repo, "other.py", "pass\n", "fix: other branch (cafe)")
    _git(["switch", "test-main"], cwd=repo)

    assert _find_session_commits("cafe", repo) == []


def test_find_session_commits_accepts_full_id_trailer(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "2026-01-01-fix-utils"
    matching = add_commit(
        repo,
        "foo.py",
        "pass\n",
        f"fix: parser\n\nGit-Session-Id: {session_id}",
    )

    assert _find_session_commits(session_id, repo) == [matching]


def test_get_entry_commit(tmp_path):
    repo = make_repo(tmp_path)
    # initial commit is the parent we expect
    init_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    ).stdout.strip()
    session_sha = add_commit(repo, "foo.py", "pass\n", "fix: something (abcd)")
    entry = _get_entry_commit(session_sha, repo)
    assert entry == init_sha


def test_get_files_changed(tmp_path):
    repo = make_repo(tmp_path)
    sha = add_commit(repo, "utils.py", "# util\n", "feat: add utils (beef)")
    files = _get_files_changed([sha], repo)
    assert "utils.py" in files


def test_get_files_changed_includes_merge_changes(tmp_path):
    repo = make_repo(tmp_path)
    sha = add_merge_commit(repo, "beef")

    assert "merged.py" in _get_files_changed([sha], repo)


def test_extract_environments_end_to_end(tmp_path):
    session_id = "cafe"
    repo = make_repo(tmp_path)
    add_commit(repo, "script.py", "print('hi')\n", f"feat: add script ({session_id})")

    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    envs = list(extract_environments(repo_path=repo, logs_dir=logs_dir, limit=10))
    assert len(envs) == 1

    env = envs[0]
    # session_id is the full conversation directory name, not just the short hex
    assert env.session_id == f"2026-01-01_topic_{session_id}"
    assert env.entry_commit  # non-empty
    assert "script.py" in env.files_changed
    assert env.task_description == "Fix the broken test for utils.py"
    assert env.outcome == "productive"
    assert env.model == "claude-sonnet-4-6"
    assert env.category == "code"


def test_extract_environments_no_commits(tmp_path):
    """Sessions with no associated commits must be skipped."""
    session_id = "dead"
    repo = make_repo(tmp_path)
    # Commit exists but does not reference this session_id
    add_commit(repo, "x.py", "", "chore: unrelated")

    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    envs = list(extract_environments(repo_path=repo, logs_dir=logs_dir))
    assert envs == []


def test_extract_environments_requires_git_repo(tmp_path):
    logs_dir = make_logs_dir(tmp_path, "1234", make_messages("1234"))
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        list(extract_environments(repo_path=not_a_repo, logs_dir=logs_dir))


def test_extract_environments_accepts_repo_subdirectory(tmp_path):
    session_id = "cafe"
    repo = make_repo(tmp_path)
    add_commit(repo, "src/impl.py", "# impl\n", f"feat: implement ({session_id})")
    logs_dir = make_logs_dir(tmp_path, session_id, make_messages(session_id))

    envs = list(
        extract_environments(repo_path=repo / "src", logs_dir=logs_dir, limit=10)
    )

    assert len(envs) == 1
    # session_id is the full conversation directory name, not just the short hex
    assert envs[0].session_id == f"2026-01-01_topic_{session_id}"


def test_extract_environments_accepts_nonhex_conversation_id(tmp_path):
    # A real non-hex session ID: the conv directory name IS the full session ID.
    session_id = "2026-01-01-fix-utils"
    repo = make_repo(tmp_path)
    add_commit(
        repo,
        "impl.py",
        "# impl\n",
        f"feat: implement\n\nGit-Session-Id: {session_id}",
    )
    # Create the conv dir with session_id as the exact directory name so the
    # scanned conv_id matches the Git-Session-Id trailer in the commit.
    logs_dir = tmp_path / "logs"
    conv_dir = logs_dir / session_id
    conv_dir.mkdir(parents=True)
    with (conv_dir / "conversation.jsonl").open("w") as fh:
        for msg in make_messages(session_id):
            fh.write(json.dumps(msg) + "\n")

    envs = list(extract_environments(repo_path=repo, logs_dir=logs_dir))

    assert len(envs) == 1
    assert envs[0].session_id == session_id


def test_extract_environments_rejects_nonpositive_min_commits(tmp_path):
    repo = make_repo(tmp_path)

    with pytest.raises(ValueError, match="at least 1"):
        list(extract_environments(repo_path=repo, min_commits=0))


def test_task_environment_to_jsonl_roundtrip():
    env = TaskEnvironment(
        session_id="cafe",
        entry_commit="abc123",
        solution_commits=["def456"],
        files_changed=["foo.py"],
        task_description="Fix the bug",
        category="code",
        outcome="productive",
        tool_call_counts={"shell": 3},
        model="claude-sonnet-4-6",
        duration_seconds=120.0,
    )
    jsonl = env.to_jsonl()
    d = json.loads(jsonl)
    assert d["session_id"] == "cafe"
    assert d["entry_commit"] == "abc123"
    assert d["category"] == "code"
    env2 = TaskEnvironment.from_dict(d)
    assert env2 == env


def test_scan_sessions_excludes_test_convs(tmp_path):
    """Conversations with IDs like 'tmp*' or 'test-*' are excluded by default."""
    logs_dir = tmp_path / "logs"
    for conv_id in ["test-abc", "tmp123", "2026-01-01_normal_ab12"]:
        (logs_dir / conv_id).mkdir(parents=True)
        (logs_dir / conv_id / "conversation.jsonl").write_text(
            json.dumps({"role": "user", "content": "hi"}) + "\n"
        )

    sessions = list(scan_sessions(logs_dir=logs_dir))
    ids = [s for s, _ in sessions]
    assert not any(i.startswith(("test-", "tmp")) for i in ids)
    assert any("normal" in i for i in ids)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_stats_no_repo(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    result = runner.invoke(dataset, ["stats", "--repo", str(not_a_repo)])
    assert result.exit_code != 0
    assert "Error" in result.output


def test_cli_export_invalid_repo_has_no_traceback(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()

    result = runner.invoke(dataset, ["export", "--repo", str(not_a_repo)])

    assert result.exit_code != 0
    assert "Error" in result.output
    assert "Traceback" not in result.output


def test_cli_export_failure_preserves_existing_output(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    output = tmp_path / "dataset.jsonl"
    output.write_text("existing\n")

    result = runner.invoke(
        dataset,
        ["export", "--repo", str(not_a_repo), "--output", str(output)],
    )

    assert result.exit_code != 0
    assert output.read_text() == "existing\n"
    assert not list(tmp_path.glob(".dataset.jsonl.*"))


def test_cli_export_parent_is_file_emits_clean_error(tmp_path):
    """Writing to a path whose parent is a regular file must raise a clean
    ClickException, not a raw Python traceback (regression for raw
    NotADirectoryError in `gptme-util dataset export -o`)."""
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    repo = make_repo(tmp_path)
    logs_dir = make_logs_dir(tmp_path, "cafe", make_messages("cafe"))
    blocker = tmp_path / "blocker"
    blocker.write_text("regular file contents")
    output_file = blocker / "envs.jsonl"

    result = runner.invoke(
        dataset,
        [
            "export",
            "--repo",
            str(repo),
            "--logs-dir",
            str(logs_dir),
            "--output",
            str(output_file),
            "-n",
            "1",
        ],
    )

    assert result.exit_code == 1, result.output
    assert not output_file.exists()
    assert "Traceback (most recent call last)" not in result.output
    assert result.output.startswith(
        f"Error: Failed to write dataset export to {output_file}:"
    )


def test_cli_export_write_error_emits_clean_error(tmp_path, monkeypatch):
    """A mid-export OSError on the temp file must be a ClickException,
    not a raw traceback (gptme/gptme#3842 AI-review finding)."""
    import tempfile as tf

    from gptme.cli.cmd_dataset import dataset

    session_id = "feed"
    repo = make_repo(tmp_path)
    add_commit(repo, "impl.py", "# impl\n", f"feat: implement ({session_id})")
    logs_dir = make_logs_dir(tmp_path, session_id, make_messages(session_id))
    output_file = tmp_path / "envs.jsonl"
    output_file.write_text("existing\n")

    real_ntf = tf.NamedTemporaryFile

    def exploding_ntf(*args, **kwargs):
        handle = real_ntf(*args, **kwargs)

        def boom(*_a, **_k):
            raise OSError(28, "No space left on device")

        handle.write = boom  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(
        "gptme.cli.cmd_dataset.tempfile.NamedTemporaryFile", exploding_ntf
    )

    runner = CliRunner()
    result = runner.invoke(
        dataset,
        [
            "export",
            "--repo",
            str(repo),
            "--logs-dir",
            str(logs_dir),
            "--output",
            str(output_file),
            "-n",
            "1",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "Traceback (most recent call last)" not in result.output
    assert result.output.startswith(
        f"Error: Failed to write dataset export to {output_file}:"
    )
    assert output_file.read_text() == "existing\n"
    assert not list(tmp_path.glob(".envs.jsonl.*"))


def test_cli_export_creates_missing_parent_dirs(tmp_path):
    """Writing to a path whose parent doesn't exist should auto-mkdir
    the parent directory (mkdir -p style) instead of failing."""
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    repo = make_repo(tmp_path)
    logs_dir = make_logs_dir(tmp_path, "cafe", make_messages("cafe"))
    nested = tmp_path / "new" / "subdir" / "envs.jsonl"

    result = runner.invoke(
        dataset,
        [
            "export",
            "--repo",
            str(repo),
            "--logs-dir",
            str(logs_dir),
            "--output",
            str(nested),
            "-n",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert nested.exists()
    assert "Exported" in result.output


def test_cli_export_rejects_nonpositive_min_commits(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    repo = make_repo(tmp_path)

    result = runner.invoke(
        dataset,
        ["export", "--repo", str(repo), "--min-commits", "0"],
    )

    assert result.exit_code != 0
    # click.IntRange(min=1) produces "not in the range x>=1"; check non-zero exit
    assert "Error" in result.output


def test_cli_export_stdout(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    session_id = "feed"
    repo = make_repo(tmp_path)
    add_commit(repo, "impl.py", "# impl\n", f"feat: implement ({session_id})")
    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    runner = CliRunner()
    result = runner.invoke(
        dataset,
        ["export", "--repo", str(repo), "--logs-dir", str(logs_dir)],
    )
    assert result.exit_code == 0, result.output
    # At least one JSONL line on stdout
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
    assert lines, f"Expected JSON output, got:\n{result.output}"
    env_dict = json.loads(lines[0])
    # session_id is the full conversation directory name; short hex appears as suffix
    assert session_id in env_dict["session_id"]


def test_cli_stats_json(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    repo = make_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        dataset, ["stats", "--repo", str(repo), "--json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "scanned" in data
    assert "yield_pct" in data
