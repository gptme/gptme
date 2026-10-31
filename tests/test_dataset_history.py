"""Conservative dataset eligibility exercised through actual local Git histories.

No dependency analysis is implied: even independently replayable A/B/A work is
ineligible. Only a single parent-linked, non-merge attributed run is accepted.
"""

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_dataset import dataset
from gptme.dataset.trajectory_to_env import corpus_stats, extract_environments

_SESSION = "history-session-a"
_MODEL = "offline-history-model"
_LOGGER = "gptme.dataset.trajectory_to_env"
_CASES = [
    "contiguous",
    "foreign-before-entry",
    "clock-skewed-contiguous",
    "intervening-dependent",
    "intervening-independent",
    "attributed-root",
    "merge-first",
    "merge-middle",
    "merge-last",
    "foreign-merge-of-attributed-siblings",
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, filename: str, content: str, attributed: bool = False) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", "--", filename)
    subject = f"Update {filename}"
    message = f"{subject}\n\nGit-Session-Id: {_SESSION}" if attributed else subject
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@dataclass
class History:
    repo: Path
    logs: Path
    entry: str
    solution: list[str]
    reason: str | None = None

    @property
    def eligible(self) -> bool:
        return self.reason is None


@pytest.fixture(params=_CASES)
def history(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> History:
    """Build each graph without inherited Git config, hooks, or environment.

    All commits deliberately share a timestamp. Parent links, not dates or the
    global default branch, must establish the solution order.
    """
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    home = tmp_path / "git-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    for key, value in {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "History Author",
        "GIT_AUTHOR_EMAIL": "author@history.invalid",
        "GIT_COMMITTER_NAME": "History Committer",
        "GIT_COMMITTER_EMAIL": "committer@history.invalid",
        "GIT_AUTHOR_DATE": "2001-02-03T04:05:06+0000",
        "GIT_COMMITTER_DATE": "2001-02-03T04:05:06+0000",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_MERGE_AUTOEDIT": "no",
    }.items():
        monkeypatch.setenv(key, value)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--template=", "-b", "history-main")
    _git(repo, "config", "core.hooksPath", str(home / "no-hooks"))
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    logs = tmp_path / "logs"
    conversation = logs / _SESSION
    conversation.mkdir(parents=True)
    messages = [
        {"role": "user", "content": "Make the local program print 42."},
        {"role": "assistant", "content": "Done.", "metadata": {"model": _MODEL}},
    ]
    (conversation / "conversation.jsonl").write_text(
        "".join(json.dumps(message) + "\n" for message in messages)
    )

    case = request.param
    # Root R is deliberately foreign except in the root-only rejection case.
    root = _commit(repo, "README.md", "Fixture root\n", case == "attributed-root")
    if case == "attributed-root":
        return History(repo, logs, "", [root], "no parent")

    if case == "clock-skewed-contiguous":
        # R -- A1 -- A2 -- M (foreign)
        #       \-- F ---/  Fork at A1, not R: the shared ancestor is crucial
        # to reverse date traversal returning A2 before A1. A2's clock
        # predates A1; graph order must still recover A1 -> A2.
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            monkeypatch.setenv(key, "2001-02-05T04:05:06+0000")
        first = _commit(repo, "program.py", "print(41)\n", True)
        _git(repo, "checkout", "-b", "history-side", first)
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            monkeypatch.setenv(key, "2001-02-06T04:05:06+0000")
        _commit(repo, "foreign.txt", "Foreign side work\n")
        _git(repo, "checkout", "history-main")
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            monkeypatch.setenv(key, "2001-02-02T04:05:06+0000")
        last = _commit(repo, "program.py", "print(42)\n", True)
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            monkeypatch.setenv(key, "2001-02-07T04:05:06+0000")
        _git(repo, "merge", "--no-ff", "history-side", "-m", "Foreign merge")
        # The solution ends before the foreign merge: replay targets A2's
        # tree, not repository HEAD (which contains unrelated foreign work).
        return History(repo, logs, root, [first, last])

    if case in {"contiguous", "foreign-before-entry"}:
        # R [-- B] -- A1 -- A2: earlier foreign work is part of entry, not a gap.
        entry = root
        if case == "foreign-before-entry":
            entry = _commit(repo, "foreign.txt", "Already present at entry\n")
        first = _commit(repo, "program.py", "value = 41\nprint(value)\n", True)
        last = _commit(repo, "program.py", "value = 42\nprint(value)\n", True)
        return History(repo, logs, entry, [first, last])

    if case.startswith("intervening-"):
        # R -- A1 -- B -- A2. Both A-only patch sequences apply cleanly,
        # but dependent A2 imports B's module. Structural eligibility must
        # reject both cases, not attempt to infer runtime dependencies.
        first = _commit(repo, "program.py", "print(41)\n", True)
        _commit(repo, "dependency.py", "value = 42\n")
        if case == "intervening-dependent":
            last = _commit(
                repo, "program.py", "import dependency\nprint(dependency.value)\n", True
            )
            # Exclude inherited PYTHONPATH and installed site-packages while
            # retaining local imports, so only the fixture supplies dependency.
            original = subprocess.run(
                [sys.executable, "-E", "-S", "program.py"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            assert original.stdout == "42\n"
            replay = tmp_path / "missing-dependency-replay"
            _git(repo, "worktree", "add", "--detach", str(replay), root)
            try:
                _git(replay, "cherry-pick", first, last)
                assert not (replay / "dependency.py").exists()
                result = subprocess.run(
                    [sys.executable, "-E", "-S", "program.py"],
                    cwd=replay,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                assert result.returncode != 0
                assert "ModuleNotFoundError" in result.stderr
                assert "No module named 'dependency'" in result.stderr
            finally:
                _git(repo, "worktree", "remove", "--force", str(replay))
        else:
            last = _commit(repo, "program.py", "print(42)\n", True)
        return History(repo, logs, root, [first, last], "not contiguous")

    if case == "foreign-merge-of-attributed-siblings":
        # R -- A-left --- M (foreign)
        #  \-- A-right -/   Both A commits are reachable, but neither parents
        # the other. Looking only at first-parent history would miss one.
        _git(repo, "checkout", "-b", "history-side", root)
        right = _commit(repo, "right.py", "print('right')\n", True)
        _git(repo, "checkout", "history-main")
        left = _commit(repo, "left.py", "print('left')\n", True)
        _git(repo, "merge", "--no-ff", "history-side", "-m", "Foreign merge")
        return History(repo, logs, root, [left, right], "not contiguous")

    # Build a genuine two-parent merge M with disjoint foreign side work:
    # R [-- A-before] -- F ---- M [-- A-after]
    #                  \-- S -/  (M is attributed).
    # Parameterization places M first, middle, or last in the attributed set.
    solution = []
    if case in {"merge-middle", "merge-last"}:
        solution.append(_commit(repo, "before.py", "print('before')\n", True))
    fork = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "history-side", fork)
    _commit(repo, "side.txt", "Foreign side branch\n")
    _git(repo, "checkout", "history-main")
    _commit(repo, "main.txt", "Foreign main branch\n")
    _git(
        repo,
        "merge",
        "--no-ff",
        "history-side",
        "-m",
        f"Merge solution\n\nGit-Session-Id: {_SESSION}",
    )
    merge = _git(repo, "rev-parse", "HEAD")
    assert len(_git(repo, "show", "-s", "--format=%P", merge).split()) == 2
    solution.append(merge)
    if case in {"merge-first", "merge-middle"}:
        solution.append(_commit(repo, "after.py", "print('after')\n", True))
    return History(repo, logs, root, solution, "merge")


def test_extract_history_eligibility_and_exact_replay(history: History) -> None:
    envs = list(extract_environments(repo_path=history.repo, logs_dir=history.logs))
    if not history.eligible:
        assert envs == [], f"Unsafe graph was exported: {history.reason}"
        return

    assert len(envs) == 1
    env = envs[0]
    assert env.session_id == _SESSION
    assert env.entry_commit == history.entry
    assert env.solution_commits == history.solution
    assert env.files_changed == ["program.py"]
    expected_tree = _git(history.repo, "rev-parse", f"{history.solution[-1]}^{{tree}}")
    replay = history.repo.parent / "replay"
    _git(history.repo, "worktree", "add", "--detach", str(replay), env.entry_commit)
    try:
        # Execute exactly the exported recipe: no -m, foreign patches, or reset.
        _git(replay, "cherry-pick", *env.solution_commits)
        assert _git(replay, "rev-parse", "HEAD^{tree}") == expected_tree
        result = subprocess.run(
            [sys.executable, "program.py"],
            cwd=replay,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "42\n"
    finally:
        _git(history.repo, "worktree", "remove", "--force", str(replay))


def _assert_stats(stats: dict, history: History) -> None:
    assert stats["scanned"] == 1
    assert stats["convertible"] == int(history.eligible)
    assert stats["yield_pct"] == (100.0 if history.eligible else 0.0)
    assert stats["category_counts"] == ({"code": 1} if history.eligible else {})
    # Rejection affects conversion, not the count of scanned model usage.
    assert stats["model_counts"] == {_MODEL: 1}


def test_corpus_stats_uses_history_eligibility(history: History) -> None:
    _assert_stats(corpus_stats(repo_path=history.repo, logs_dir=history.logs), history)


class _CurrentStderrHandler(logging.Handler):
    """Route warnings to Click's current stderr, like application logging setup."""

    def emit(self, record: logging.LogRecord) -> None:
        print(self.format(record), file=sys.stderr)


def test_cli_export_history_eligibility(
    history: History, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger(_LOGGER)
    handler = _CurrentStderrHandler(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = CliRunner().invoke(
                dataset,
                [
                    "export",
                    "--repo",
                    str(history.repo),
                    "--logs-dir",
                    str(history.logs),
                ],
            )
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert result.exit_code == 0, result.output
    # Parse ALL stdout, not just lines starting with '{': diagnostics must not
    # silently poison JSONL pipelines.
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == int(history.eligible)
    assert f"Exported {int(history.eligible)} environments" in result.stderr
    if history.eligible:
        assert records[0]["entry_commit"] == history.entry
        assert records[0]["solution_commits"] == history.solution
    else:
        assert history.reason is not None
        warnings = [
            record for record in caplog.records if record.levelno >= logging.WARNING
        ]
        assert any(
            _SESSION in record.getMessage()
            and history.reason in record.getMessage().lower()
            for record in warnings
        ), caplog.text
        assert _SESSION in result.stderr
        assert history.reason in result.stderr.lower()


def test_cli_stats_uses_history_eligibility(history: History) -> None:
    result = CliRunner().invoke(
        dataset,
        [
            "stats",
            "--repo",
            str(history.repo),
            "--logs-dir",
            str(history.logs),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    _assert_stats(json.loads(result.stdout), history)


@pytest.mark.parametrize("history", ["contiguous"], indirect=True)
@pytest.mark.parametrize("depth", [1, 2])
def test_real_shallow_boundary(history: History, depth: int) -> None:
    """Only the new tip is attributed; depth two exposes its foreign entry."""
    session = "boundary-session"
    (history.logs / _SESSION).rename(history.logs / session)
    entry = history.solution[-1]
    (history.repo / "program.py").write_text("print(43)\n")
    _git(history.repo, "add", "--", "program.py")
    _git(history.repo, "commit", "-m", f"Boundary task\n\nGit-Session-Id: {session}")
    tip = _git(history.repo, "rev-parse", "HEAD")
    clone = history.repo.parent / "shallow"
    # file:// is essential: plain local paths ignore --depth.
    _git(
        history.repo, "clone", "--depth", str(depth), history.repo.as_uri(), str(clone)
    )
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    assert int(_git(clone, "rev-list", "--count", "HEAD")) == depth
    envs = list(extract_environments(repo_path=clone, logs_dir=history.logs))
    assert len(envs) == depth - 1
    if envs:
        assert envs[0].entry_commit == entry
        assert envs[0].solution_commits == [tip]
    expected = History(
        clone, history.logs, entry, [tip], "no parent" if depth == 1 else None
    )
    _assert_stats(corpus_stats(repo_path=clone, logs_dir=history.logs), expected)
    runner = CliRunner()
    args = ["--repo", str(clone), "--logs-dir", str(history.logs)]
    exported = runner.invoke(dataset, ["export", *args])
    assert exported.exit_code == 0, exported.output
    records = [json.loads(line) for line in exported.stdout.splitlines()]
    assert len(records) == depth - 1
    if records:
        assert records[0]["entry_commit"] == entry
        assert records[0]["solution_commits"] == [tip]
    stats = runner.invoke(dataset, ["stats", *args, "--json"])
    assert stats.exit_code == 0, stats.output
    _assert_stats(json.loads(stats.stdout), expected)


@pytest.mark.parametrize("history", ["contiguous"], indirect=True)
@pytest.mark.parametrize("failure", ["parents", "entry"])
def test_inspection_failure_is_closed(
    history: History,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail only one inspection operation; all other Git work stays real."""
    from gptme.dataset import trajectory_to_env

    original = trajectory_to_env._run_git

    def fail_inspection(args: list[str], cwd: Path) -> str:
        if failure == "parents" and args[:2] == ["rev-list", "--parents"]:
            return ""
        if failure == "entry" and args[:2] == ["rev-parse", "--verify"]:
            return ""
        return original(args, cwd)

    monkeypatch.setattr(trajectory_to_env, "_run_git", fail_inspection)
    history.reason = "cannot inspect"
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert (
            list(extract_environments(repo_path=history.repo, logs_dir=history.logs))
            == []
        )
        _assert_stats(
            corpus_stats(repo_path=history.repo, logs_dir=history.logs), history
        )
        runner = CliRunner()
        args = ["--repo", str(history.repo), "--logs-dir", str(history.logs)]
        exported = runner.invoke(dataset, ["export", *args])
        assert exported.exit_code == 0, exported.output
        assert exported.stdout == ""
        stats = runner.invoke(dataset, ["stats", *args, "--json"])
        assert stats.exit_code == 0, stats.output
        _assert_stats(json.loads(stats.stdout), history)
    assert any(
        _SESSION in record.getMessage()
        and "cannot inspect" in record.getMessage().lower()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), caplog.text
