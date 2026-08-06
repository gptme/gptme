"""Tests for the shared review pipeline utilities (gptme#3442).

Covers:
- gptme.util.gh shared helpers (run_gh_json, is_bot_user, is_trusted_reviewer)
- gptme.util.review (ReviewFinding, ReviewArtifact)
- gptme-util review command group (CLI integration)
"""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.util import gh as gh_util
from gptme.util.review import (
    FindingSeverity,
    FindingStatus,
    ReviewArtifact,
    ReviewFinding,
)

# ---------------------------------------------------------------------------
# NOTE: FindingStatus is used in artifact-mode tests below (TestArtifactMode).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# gptme.util.gh shared helpers
# ---------------------------------------------------------------------------


class TestRunGhJson:
    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args, returncode=1, stdout="", stderr="error"
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.run_gh_json(["gh", "pr", "view", "99"]) is None

    def test_returns_none_on_invalid_json(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="not json", stderr=""
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.run_gh_json(["gh", "something"]) is None

    def test_parses_list(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout=json.dumps([{"id": 1}, {"id": 2}]),
                stderr="",
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        result = gh_util.run_gh_json(["gh", "api", "/some/path"])
        assert result == [{"id": 1}, {"id": 2}]

    def test_parses_dict(self, monkeypatch):
        def fake_run(args, **kwargs):
            return subprocess.CompletedProcess(
                args,
                returncode=0,
                stdout=json.dumps({"state": "OPEN"}),
                stderr="",
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.run_gh_json(["gh", "pr", "view", "1"]) == {"state": "OPEN"}

    def test_returns_none_on_timeout(self, monkeypatch):
        def fake_run(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=5)

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.run_gh_json(["gh", "pr", "view", "1"]) is None


class TestFetchPrReviewerLogins:
    """Tests for fetch_pr_reviewer_logins, including conversation-level commenters."""

    def _mock_subprocess(self, monkeypatch, review_logins, comment_logins):
        """Stub subprocess.run to return different user lists per endpoint."""

        def fake_run(args, **kwargs):
            endpoint = args[-1] if args else ""
            if "/reviews" in endpoint:
                data = [
                    {"user": {"login": login, "type": "User"}, "state": "APPROVED"}
                    for login in review_logins
                ]
            else:
                data = [
                    {"user": {"login": login, "type": "User"}, "body": "LGTM"}
                    for login in comment_logins
                ]
            return subprocess.CompletedProcess(
                args, returncode=0, stdout=json.dumps(data), stderr=""
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)

    def test_includes_formal_reviewers(self, monkeypatch):
        self._mock_subprocess(monkeypatch, ["ErikBjare"], [])
        result = gh_util.fetch_pr_reviewer_logins("owner", "repo", 42)
        assert result == frozenset(["erikbjare"])

    def test_includes_conversation_commenters(self, monkeypatch):
        """Contributors who comment without a formal review must be included."""
        self._mock_subprocess(monkeypatch, [], ["alice"])
        result = gh_util.fetch_pr_reviewer_logins("owner", "repo", 42)
        assert result == frozenset(["alice"])

    def test_merges_both_sources(self, monkeypatch):
        """Formal reviewers and conversation commenters are combined."""
        self._mock_subprocess(monkeypatch, ["ErikBjare"], ["alice"])
        result = gh_util.fetch_pr_reviewer_logins("owner", "repo", 42)
        assert result == frozenset(["erikbjare", "alice"])

    def test_excludes_bots_from_both_sources(self, monkeypatch):
        """Bot accounts are excluded regardless of which endpoint they come from."""

        def fake_run(args, **kwargs):
            endpoint = args[-1] if args else ""
            if "/reviews" in endpoint:
                data = [
                    {
                        "user": {"login": "ErikBjare", "type": "User"},
                        "state": "APPROVED",
                    },
                    {
                        "user": {"login": "greptile-ai[bot]", "type": "Bot"},
                        "state": "COMMENTED",
                    },
                ]
            else:
                data = [
                    {"user": {"login": "alice", "type": "User"}, "body": "ok"},
                    {
                        "user": {"login": "dependabot[bot]", "type": "Bot"},
                        "body": "bump",
                    },
                ]
            return subprocess.CompletedProcess(
                args, returncode=0, stdout=json.dumps(data), stderr=""
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        result = gh_util.fetch_pr_reviewer_logins("owner", "repo", 42)
        assert result is not None
        assert "greptile-ai[bot]" not in result
        assert "dependabot[bot]" not in result
        assert "erikbjare" in result
        assert "alice" in result

    def test_returns_none_when_reviews_api_fails(self, monkeypatch):
        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return subprocess.CompletedProcess(
                    args, returncode=1, stdout="", stderr="err"
                )
            return subprocess.CompletedProcess(
                args, returncode=0, stdout="[]", stderr=""
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.fetch_pr_reviewer_logins("owner", "repo", 42) is None

    def test_returns_none_when_comments_api_fails(self, monkeypatch):
        call_count = [0]

        def fake_run(args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return subprocess.CompletedProcess(
                    args, returncode=0, stdout="[]", stderr=""
                )
            return subprocess.CompletedProcess(
                args, returncode=1, stdout="", stderr="err"
            )

        monkeypatch.setattr(gh_util.subprocess, "run", fake_run)
        assert gh_util.fetch_pr_reviewer_logins("owner", "repo", 42) is None


class TestIsBotUser:
    def test_bot_type(self):
        assert gh_util.is_bot_user({"type": "Bot", "login": "some-bot"})

    def test_bot_login_suffix(self):
        assert gh_util.is_bot_user({"type": "User", "login": "greptile-ai[bot]"})

    def test_human_user(self):
        assert not gh_util.is_bot_user({"type": "User", "login": "ErikBjare"})

    def test_empty_dict(self):
        assert not gh_util.is_bot_user({})


class TestIsTrustedReviewer:
    def _make_comment(self, login: str, utype: str, assoc: str) -> dict:
        return {
            "user": {"login": login, "type": utype},
            "author_association": assoc,
        }

    def test_owner_is_trusted(self):
        assert gh_util.is_trusted_reviewer(
            self._make_comment("ErikBjare", "User", "OWNER")
        )

    def test_member_is_trusted(self):
        assert gh_util.is_trusted_reviewer(
            self._make_comment("contributor", "User", "MEMBER")
        )

    def test_collaborator_is_trusted(self):
        assert gh_util.is_trusted_reviewer(
            self._make_comment("collab", "User", "COLLABORATOR")
        )

    def test_none_association_not_trusted(self):
        assert not gh_util.is_trusted_reviewer(
            self._make_comment("random-user", "User", "NONE")
        )

    def test_bot_not_trusted_even_with_owner_assoc(self):
        # Bots should never be treated as trusted reviewers regardless of
        # their association level (they might have OWNER assoc in some repos).
        assert not gh_util.is_trusted_reviewer(
            self._make_comment("bot[bot]", "Bot", "OWNER")
        )

    def test_bot_login_suffix_not_trusted(self):
        assert not gh_util.is_trusted_reviewer(
            self._make_comment("greptile-ai[bot]", "User", "COLLABORATOR")
        )


# ---------------------------------------------------------------------------
# ReviewFinding
# ---------------------------------------------------------------------------


class TestReviewFinding:
    def test_round_trip(self):
        f = ReviewFinding(
            body="Rename this variable.",
            file="gptme/util/review.py",
            line=42,
            severity=FindingSeverity.WARNING,
            status=FindingStatus.OPEN,
            github_comment_id=12345,
            reviewer="ErikBjare",
        )
        assert ReviewFinding.from_dict(f.to_dict()) == f

    def test_from_github_comment_inline(self):
        comment = {
            "id": 99,
            "path": "src/app.py",
            "original_line": 10,
            "body": "This is unclear.",
            "user": {"login": "reviewer1"},
        }
        f = ReviewFinding.from_github_comment(comment)
        assert f.file == "src/app.py"
        assert f.line == 10
        assert f.body == "This is unclear."
        assert f.reviewer == "reviewer1"
        assert f.github_comment_id == 99
        assert f.severity == FindingSeverity.WARNING
        assert f.status == FindingStatus.OPEN

    def test_from_github_comment_severity_override(self):
        comment = {"id": 1, "path": "a.py", "body": "note", "user": {"login": "u"}}
        f = ReviewFinding.from_github_comment(
            comment, severity=FindingSeverity.CRITICAL
        )
        assert f.severity == FindingSeverity.CRITICAL

    def test_defaults(self):
        f = ReviewFinding(body="simple finding")
        assert f.file == ""
        assert f.line is None
        assert f.severity == FindingSeverity.WARNING
        assert f.status == FindingStatus.OPEN
        assert f.github_comment_id is None
        assert f.reviewer == ""


# ---------------------------------------------------------------------------
# ReviewArtifact
# ---------------------------------------------------------------------------


class TestReviewArtifact:
    def _make_artifact(self) -> ReviewArtifact:
        return ReviewArtifact(
            pr_owner="gptme",
            pr_repo="gptme",
            pr_number=1234,
            findings=[
                ReviewFinding(
                    body="Rename this.",
                    file="app.py",
                    line=5,
                    status=FindingStatus.OPEN,
                ),
                ReviewFinding(
                    body="Add a test.",
                    file="",
                    status=FindingStatus.CONFIRMED,
                ),
                ReviewFinding(
                    body="Minor nit.",
                    file="util.py",
                    status=FindingStatus.DROPPED,
                ),
            ],
        )

    def test_round_trip_json(self):
        a = self._make_artifact()
        restored = ReviewArtifact.from_json(a.to_json())
        assert restored.pr_owner == a.pr_owner
        assert restored.pr_repo == a.pr_repo
        assert restored.pr_number == a.pr_number
        assert len(restored.findings) == len(a.findings)
        assert restored.findings[0].body == "Rename this."

    def test_open_findings(self):
        a = self._make_artifact()
        assert len(a.open_findings) == 1
        assert a.open_findings[0].body == "Rename this."

    def test_counts(self):
        a = self._make_artifact()
        assert a.confirmed_count == 1
        assert a.dropped_count == 1

    def test_schema_version(self):
        a = self._make_artifact()
        d = a.to_dict()
        assert d["schema_version"] == 1

    def test_pr_metadata(self):
        a = self._make_artifact()
        d = a.to_dict()
        assert d["pr"] == {"owner": "gptme", "repo": "gptme", "number": 1234}

    def test_save_and_load(self, tmp_path):
        a = self._make_artifact()
        path = tmp_path / "artifact.json"
        a.save(path)
        loaded = ReviewArtifact.load(path)
        assert loaded.pr_number == 1234
        assert len(loaded.findings) == 3

    def test_from_github_comments(self):
        inline = [
            {
                "id": 1,
                "path": "app.py",
                "original_line": 3,
                "body": "Rename.",
                "user": {"login": "reviewer"},
                "author_association": "MEMBER",
            }
        ]
        convo = [
            {
                "id": 2,
                "path": "",
                "body": "LGTM overall.",
                "user": {"login": "reviewer"},
                "author_association": "MEMBER",
            }
        ]
        a = ReviewArtifact.from_github_comments(
            owner="gptme",
            repo="gptme",
            pr_number=42,
            inline_comments=inline,
            conversation_comments=convo,
        )
        assert a.pr_number == 42
        assert len(a.findings) == 2
        # Inline → WARNING, conversation → NOTE
        assert a.findings[0].severity == FindingSeverity.WARNING
        assert a.findings[1].severity == FindingSeverity.NOTE

    def test_empty_artifact(self):
        a = ReviewArtifact(pr_owner="o", pr_repo="r", pr_number=1)
        assert a.open_findings == []
        assert a.confirmed_count == 0
        assert a.dropped_count == 0
        assert json.loads(a.to_json())["findings"] == []


# ---------------------------------------------------------------------------
# CLI: gptme-util review group
# ---------------------------------------------------------------------------


class TestReviewCommandGroup:
    def test_review_help_shows_watch_subcommand(self):
        """``gptme-util review --help`` should list the ``watch`` subcommand."""
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "--help"])
        assert result.exit_code == 0
        assert "watch" in result.output

    def test_review_watch_subcommand_help(self):
        """``gptme-util review watch --help`` should be reachable and show PR arg."""
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "watch", "--help"])
        assert result.exit_code == 0
        # Should mention the PR argument (inherited from cmd_review_watch)
        assert "PR" in result.output or "pr" in result.output.lower()

    def test_review_watch_reachable_without_gh(self, monkeypatch):
        """``gptme-util review watch`` should fail gracefully when gh is missing.

        This also verifies the watch subcommand is wired into the review group
        (not just review-watch at the top level).
        """
        from gptme.cli import cmd_review_watch

        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "watch", "1", "--repo", "o/r"])
        assert result.exit_code != 0
        assert "gh" in result.output.lower()

    def test_review_watch_requires_pr_without_artifact(self, monkeypatch):
        """Without --artifact, PR number is required."""
        from gptme.cli import cmd_review_watch

        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "watch", "--repo", "o/r"])
        assert result.exit_code != 0
        assert "PR" in result.output or "artifact" in result.output.lower()

    def test_review_watch_help_shows_artifact_option(self):
        """``--artifact`` option should appear in the help text."""
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "watch", "--help"])
        assert result.exit_code == 0
        assert "--artifact" in result.output


# ---------------------------------------------------------------------------
# Artifact mode: _build_review_prompt_from_findings
# ---------------------------------------------------------------------------


class TestBuildReviewPromptFromFindings:
    def _make_findings(self):
        from gptme.util.review import FindingSeverity, FindingStatus, ReviewFinding

        return [
            ReviewFinding(
                body="Rename this variable for clarity.",
                file="gptme/util/review.py",
                line=42,
                severity=FindingSeverity.WARNING,
                status=FindingStatus.OPEN,
                reviewer="ErikBjare",
            ),
            ReviewFinding(
                body="Add a docstring.",
                file="",
                severity=FindingSeverity.NOTE,
                status=FindingStatus.OPEN,
                reviewer="ErikBjare",
            ),
        ]

    def test_prompt_contains_file_and_line(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings

        findings = self._make_findings()
        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=findings,
        )
        assert "gptme/util/review.py" in prompt
        assert "line 42" in prompt

    def test_prompt_contains_severity(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings

        findings = self._make_findings()
        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=findings,
        )
        assert "WARNING" in prompt
        assert "NOTE" in prompt

    def test_prompt_separates_inline_and_pr_level(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings

        findings = self._make_findings()
        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=findings,
        )
        assert "Inline code review findings" in prompt
        assert "PR-level findings" in prompt

    def test_prompt_includes_finding_bodies(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings

        findings = self._make_findings()
        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=findings,
        )
        assert "Rename this variable for clarity." in prompt
        assert "Add a docstring." in prompt

    def test_multiline_finding_body_all_lines_quoted(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings
        from gptme.util.review import FindingSeverity, FindingStatus, ReviewFinding

        finding = ReviewFinding(
            body="Line one.\nLine two.\nLine three.",
            file="src/foo.py",
            line=10,
            severity=FindingSeverity.WARNING,
            status=FindingStatus.OPEN,
            reviewer="reviewer",
        )
        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=[finding],
        )
        # Every body line must be blockquote-prefixed; unquoted continuations break
        # the authoritative-instruction boundary defined in the prompt header.
        assert "> Line one." in prompt
        assert "> Line two." in prompt
        assert "> Line three." in prompt
        for line in prompt.splitlines():
            if line.strip() in ("Line two.", "Line three."):
                raise AssertionError(f"Unquoted body continuation found: {line!r}")

    def test_empty_findings_no_section_headers(self):
        from gptme.cli.cmd_review_watch import _build_review_prompt_from_findings

        prompt = _build_review_prompt_from_findings(
            owner="o",
            repo="r",
            pr_num=1,
            pr_branch="fix-branch",
            findings=[],
        )
        assert "Inline code review findings" not in prompt
        assert "PR-level findings" not in prompt


# ---------------------------------------------------------------------------
# Artifact mode: CLI integration
# ---------------------------------------------------------------------------


class TestArtifactMode:
    def _make_artifact_file(self, tmp_path, *, has_open=True):
        from gptme.util.review import FindingSeverity, FindingStatus, ReviewFinding

        findings = []
        if has_open:
            findings.append(
                ReviewFinding(
                    body="Fix this bug.",
                    file="gptme/util/review.py",
                    line=10,
                    severity=FindingSeverity.ERROR,
                    status=FindingStatus.OPEN,
                    reviewer="ErikBjare",
                )
            )
        findings.append(
            ReviewFinding(
                body="Already fixed.",
                file="",
                severity=FindingSeverity.NOTE,
                status=FindingStatus.CONFIRMED,
                reviewer="ErikBjare",
            )
        )
        artifact = ReviewArtifact(
            pr_owner="gptme",
            pr_repo="gptme",
            pr_number=1234,
            findings=findings,
        )
        path = tmp_path / "artifact.json"
        artifact.save(path)
        return path

    def test_artifact_mode_no_gh_needed(self, tmp_path, monkeypatch):
        """With --artifact, gh is not called even when not available."""
        from gptme.cli import cmd_review_watch

        artifact_path = self._make_artifact_file(tmp_path)

        # Patch spawn so we don't actually run gptme
        monkeypatch.setattr(
            cmd_review_watch,
            "spawn_review_session",
            lambda **_: {"exit_reason": "done", "duration_s": 0.1},
        )
        # gh is unavailable — artifact mode should still work
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            ["review", "watch", "--artifact", str(artifact_path)],
        )
        assert result.exit_code == 0, result.output

    def test_artifact_mode_empty_artifact_exits_cleanly(self, tmp_path, monkeypatch):
        """Artifact with no open findings should exit with informational message."""
        artifact_path = self._make_artifact_file(tmp_path, has_open=False)

        from gptme.cli import cmd_review_watch

        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            ["review", "watch", "--artifact", str(artifact_path)],
        )
        assert result.exit_code == 0
        assert "no open findings" in result.output.lower()

    def test_artifact_mode_infers_pr_from_artifact(self, tmp_path, monkeypatch):
        """Artifact mode infers owner/repo/number from the artifact."""
        from gptme.cli import cmd_review_watch

        artifact_path = self._make_artifact_file(tmp_path)

        calls = []

        def fake_spawn(**kwargs):
            calls.append(kwargs)
            return {"exit_reason": "done", "duration_s": 0.1}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            ["review", "watch", "--artifact", str(artifact_path)],
        )
        assert result.exit_code == 0, result.output
        assert len(calls) == 1
        # PR metadata from artifact should appear in the prompt
        prompt = calls[0]["prompt"]
        assert "gptme/gptme#1234" in prompt

    def test_artifact_mode_repo_flag_overrides_artifact(self, tmp_path, monkeypatch):
        """--repo flag takes precedence over artifact's owner/repo."""
        from gptme.cli import cmd_review_watch

        artifact_path = self._make_artifact_file(tmp_path)

        calls = []

        def fake_spawn(**kwargs):
            calls.append(kwargs)
            return {"exit_reason": "done", "duration_s": 0.1}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            [
                "review",
                "watch",
                "--artifact",
                str(artifact_path),
                "--repo",
                "other/repo",
            ],
        )
        assert result.exit_code == 0, result.output
        prompt = calls[0]["prompt"]
        assert "other/repo" in prompt

    def test_artifact_updates_finding_status_on_success(self, tmp_path, monkeypatch):
        """After a successful session, the artifact file is updated."""
        from gptme.cli import cmd_review_watch

        artifact_path = self._make_artifact_file(tmp_path)

        monkeypatch.setattr(
            cmd_review_watch,
            "spawn_review_session",
            lambda **_: {"exit_reason": "done", "duration_s": 0.1},
        )
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        runner.invoke(
            util_main,
            ["review", "watch", "--artifact", str(artifact_path)],
        )

        updated = ReviewArtifact.load(artifact_path)
        in_progress = [
            f for f in updated.findings if f.status == FindingStatus.IN_PROGRESS
        ]
        assert len(in_progress) == 1  # the previously-open finding

    def test_artifact_mode_invalid_path_errors(self, monkeypatch):
        """Invalid artifact path should produce a clear error."""
        from gptme.cli import cmd_review_watch

        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            ["review", "watch", "--artifact", "/nonexistent/path/artifact.json"],
        )
        assert result.exit_code != 0
        assert "artifact" in result.output.lower()


# ---------------------------------------------------------------------------
# Trusted-reviewer guard (--trusted-reviewer / --require-trust)
# ---------------------------------------------------------------------------


_BODIES_NOT_MOCKED = (
    object()
)  # sentinel: skip fetch_pr_review_comment_bodies_by_user mock


class TestTrustedReviewerGuard:
    """Tests for the --trusted-reviewer / --require-trust artifact-mode guard."""

    def _make_artifact(
        self,
        tmp_path,
        findings_spec: list[dict],
    ):
        """Build an artifact JSON file from a list of finding spec dicts.

        Each spec may contain: body, file, reviewer (str|None), status.
        """
        findings = [
            ReviewFinding(
                body=spec.get("body", "A finding."),
                file=spec.get("file", "app.py"),
                severity=FindingSeverity.WARNING,
                status=FindingStatus(spec.get("status", "open")),
                reviewer=spec.get("reviewer", ""),
            )
            for spec in findings_spec
        ]
        artifact = ReviewArtifact(
            pr_owner="gptme",
            pr_repo="gptme",
            pr_number=42,
            findings=findings,
        )
        path = tmp_path / "artifact.json"
        artifact.save(path)
        return path

    def _run(
        self,
        monkeypatch,
        args: list[str],
        github_verified_logins: frozenset[str] | None = None,
        github_comment_bodies=_BODIES_NOT_MOCKED,
    ):
        """Invoke review watch with spawn patched out; return (result, spawn_calls).

        ``github_verified_logins`` controls what ``fetch_pr_reviewer_logins``
        returns for tests that exercise the --trusted-reviewer allowlist.  Pass
        an explicit frozenset to override the default broad mock, or ``None`` to
        skip the mock entirely (for tests that don't use --trusted-reviewer).

        ``github_comment_bodies`` controls what ``fetch_pr_review_comment_bodies_by_user``
        returns for tests that exercise the --verify-bodies flag.  Pass a dict
        (login → list of comment records) to mock a successful API call, ``None`` to
        mock an API failure (function returns None), or leave as the sentinel
        ``_BODIES_NOT_MOCKED`` to skip the mock entirely (for tests that don't use
        --verify-bodies).

        Each comment record is a dict with ``body``, ``path`` (file path for inline
        review comments, ``None`` for conversation comments), and ``line`` (int or
        ``None``).  When a finding has a file, the matching record must be an inline
        comment on the same file — this validates the artifact's location metadata
        against the GitHub-authoritative source.

        When --trusted-reviewer is present in ``args``, ``_gh_available`` is
        automatically stubbed to ``True`` and ``fetch_pr_reviewer_logins`` is
        stubbed to the provided (or default) set so the security check can run.
        """
        from gptme.cli import cmd_review_watch

        has_trusted_reviewer = "--trusted-reviewer" in args
        has_verify_bodies = "--verify-bodies" in args
        # Default: broad set that covers reviewer names used in most test fixtures
        if github_verified_logins is None and has_trusted_reviewer:
            github_verified_logins = frozenset(["erikbjare", "alice", "bob", "someone"])

        monkeypatch.setattr(
            cmd_review_watch,
            "_gh_available",
            lambda: has_trusted_reviewer or has_verify_bodies,
        )

        if github_verified_logins is not None:
            monkeypatch.setattr(
                cmd_review_watch,
                "fetch_pr_reviewer_logins",
                lambda owner, repo, pr_num, **kw: github_verified_logins,
            )

        if github_comment_bodies is not _BODIES_NOT_MOCKED:
            # Wire the mock with whatever value was passed (dict or None).
            # None simulates an API failure (the function returns None on error).
            _bodies_return = github_comment_bodies
            monkeypatch.setattr(
                cmd_review_watch,
                "fetch_pr_review_comment_bodies_by_user",
                lambda owner, repo, pr_num, **kw: _bodies_return,
            )

        spawn_calls: list[dict] = []

        def fake_spawn(**kwargs):
            spawn_calls.append(kwargs)
            return {"exit_reason": "done", "duration_s": 0.1}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)
        runner = CliRunner()
        result = runner.invoke(util_main, ["review", "watch"] + args)
        return result, spawn_calls

    # ------------------------------------------------------------------
    # --trusted-reviewer
    # ------------------------------------------------------------------

    def test_all_trusted_findings_are_injected(self, tmp_path, monkeypatch):
        """All findings from the trusted reviewer pass through unchanged."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Fix typo.", "reviewer": "ErikBjare"},
                {"body": "Add docstring.", "reviewer": "ErikBjare"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--trusted-reviewer", "ErikBjare"],
            github_comment_bodies={
                "erikbjare": [
                    {"body": "Fix typo.", "path": "app.py", "line": None},
                    {"body": "Add docstring.", "path": "app.py", "line": None},
                ]
            },
        )
        assert result.exit_code == 0, result.output
        # Both trusted findings → session spawned with both bodies in the prompt
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Fix typo." in prompt
        assert "Add docstring." in prompt

    def test_untrusted_findings_are_skipped(self, tmp_path, monkeypatch):
        """Findings from a non-whitelisted reviewer are not injected."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Trusted finding.", "reviewer": "ErikBjare"},
                {"body": "Attacker payload.", "reviewer": "attacker"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--trusted-reviewer", "ErikBjare"],
            github_comment_bodies={
                "erikbjare": [
                    {"body": "Trusted finding.", "path": "app.py", "line": None}
                ]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Trusted finding." in prompt
        assert "Attacker payload." not in prompt

    def test_all_untrusted_no_session_spawned(self, tmp_path, monkeypatch):
        """When every finding is from an untrusted reviewer, the policy raises an error.

        A trust-policy rejection (all findings filtered out) must NOT exit 0 —
        that would let automation treat "artifact rejected by policy" as
        "artifact already clean", silently discarding the guard result.
        """
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Evil instruction 1.", "reviewer": "attacker"},
                {"body": "Evil instruction 2.", "reviewer": "attacker"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--trusted-reviewer", "ErikBjare"],
            github_comment_bodies={"erikbjare": []},  # No bodies for erikbjare
        )
        assert result.exit_code != 0, (
            "Trust-policy rejection must return a non-zero exit code"
        )
        assert len(spawn_calls) == 0, (
            "No session should be spawned when all findings are untrusted"
        )
        assert "trust policy" in result.output.lower()

    def test_multiple_trusted_reviewers_union(self, tmp_path, monkeypatch):
        """Multiple --trusted-reviewer flags form a union allowlist."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "From alice.", "reviewer": "alice"},
                {"body": "From bob.", "reviewer": "bob"},
                {"body": "From attacker.", "reviewer": "attacker"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "alice",
                "--trusted-reviewer",
                "bob",
            ],
            github_comment_bodies={
                "alice": [{"body": "From alice.", "path": "app.py", "line": None}],
                "bob": [{"body": "From bob.", "path": "app.py", "line": None}],
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "From alice." in prompt
        assert "From bob." in prompt
        assert "From attacker." not in prompt

    # ------------------------------------------------------------------
    # --require-trust
    # ------------------------------------------------------------------

    def test_require_trust_skips_findings_without_reviewer(self, tmp_path, monkeypatch):
        """--require-trust drops findings whose reviewer field is empty."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Has reviewer.", "reviewer": "ErikBjare"},
                {"body": "No reviewer.", "reviewer": ""},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--require-trust",
            ],
            github_comment_bodies={
                "erikbjare": [{"body": "Has reviewer.", "path": "app.py", "line": None}]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Has reviewer." in prompt
        assert "No reviewer." not in prompt

    def test_require_trust_alone_skips_unattributed_findings(
        self, tmp_path, monkeypatch
    ):
        """--require-trust without --trusted-reviewer still drops un-attributed findings."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Attributed finding.", "reviewer": "someone"},
                {"body": "Unattributed finding.", "reviewer": ""},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--require-trust"],
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Attributed finding." in prompt
        assert "Unattributed finding." not in prompt

    def test_require_trust_all_unattributed_no_session(self, tmp_path, monkeypatch):
        """When all findings lack reviewer + --require-trust, no session is spawned."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "No reviewer 1.", "reviewer": ""},
                {"body": "No reviewer 2.", "reviewer": ""},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--require-trust"],
        )
        assert result.exit_code != 0, (
            "Trust-policy rejection (--require-trust dropped all findings) must return non-zero"
        )
        assert len(spawn_calls) == 0, (
            "No session should spawn when all findings are unattributed under --require-trust"
        )
        assert "trust policy" in result.output.lower()

    # ------------------------------------------------------------------
    # stdin mode (--artifact -)
    # ------------------------------------------------------------------

    def test_trusted_reviewer_filter_applies_to_stdin_mode(self, tmp_path, monkeypatch):
        """--trusted-reviewer guard applies equally when artifact is read from stdin."""

        from gptme.cli import cmd_review_watch

        artifact = ReviewArtifact(
            pr_owner="gptme",
            pr_repo="gptme",
            pr_number=99,
            findings=[
                ReviewFinding(
                    body="Trusted finding.",
                    file="app.py",
                    status=FindingStatus.OPEN,
                    reviewer="ErikBjare",
                ),
                ReviewFinding(
                    body="Untrusted finding.",
                    file="app.py",
                    status=FindingStatus.OPEN,
                    reviewer="attacker",
                ),
            ],
        )

        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
        monkeypatch.setattr(
            cmd_review_watch,
            "fetch_pr_reviewer_logins",
            lambda owner, repo, pr_num, **kw: frozenset(["erikbjare"]),
        )
        monkeypatch.setattr(
            cmd_review_watch,
            "fetch_pr_review_comment_bodies_by_user",
            lambda owner, repo, pr_num, **kw: {
                "erikbjare": [
                    {"body": "Trusted finding.", "path": "app.py", "line": None}
                ]
            },
        )
        spawn_calls: list[dict] = []

        def fake_spawn(**kwargs):
            spawn_calls.append(kwargs)
            return {"exit_reason": "done", "duration_s": 0.1}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            ["review", "watch", "--artifact", "-", "--trusted-reviewer", "ErikBjare"],
            input=artifact.to_json(),
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Trusted finding." in prompt
        assert "Untrusted finding." not in prompt

    # ------------------------------------------------------------------
    # No flags → existing behaviour unchanged
    # ------------------------------------------------------------------

    def test_no_flags_all_findings_pass(self, tmp_path, monkeypatch):
        """Without --trusted-reviewer/--require-trust all findings pass (regression guard)."""
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Finding A.", "reviewer": "ErikBjare"},
                {"body": "Finding B.", "reviewer": ""},
                {"body": "Finding C.", "reviewer": "unknown"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path)],
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Finding A." in prompt
        assert "Finding B." in prompt
        assert "Finding C." in prompt

    # ------------------------------------------------------------------
    # Security: forged attribution blocked by GitHub API verification
    # ------------------------------------------------------------------

    def test_forged_reviewer_blocked_by_github_verification(
        self, tmp_path, monkeypatch
    ):
        """A crafted artifact that forges an allowlisted login is rejected.

        The artifact's self-reported ``reviewer`` field says "ErikBjare", but
        the GitHub API reports no reviews from that login.  The finding must be
        blocked even though it passes the allowlist name check.
        """
        path = self._make_artifact(
            tmp_path,
            [{"body": "Injected payload.", "reviewer": "ErikBjare"}],
        )
        # GitHub API returns an empty set — the forged reviewer never actually
        # submitted a review on this PR.
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--trusted-reviewer", "ErikBjare"],
            github_verified_logins=frozenset(),  # no real reviews on the PR
            github_comment_bodies={},  # no comments from the forged reviewer
        )
        assert result.exit_code != 0, "Forged reviewer must be rejected (non-zero exit)"
        assert len(spawn_calls) == 0, (
            "No fix session should spawn for a forged reviewer"
        )
        assert "trust policy" in result.output.lower()

    # ------------------------------------------------------------------
    # Security: login comparison is case-insensitive
    # ------------------------------------------------------------------

    def test_trusted_reviewer_comparison_is_case_insensitive(
        self, tmp_path, monkeypatch
    ):
        """--trusted-reviewer matching is case-insensitive on both the CLI flag
        and the artifact's reviewer field.

        GitHub login comparison must be case-insensitive: ``ErikBjare`` and
        ``erikbjare`` identify the same account.  Dropping a finding solely
        because of casing differences would break legitimate workflows.
        """
        path = self._make_artifact(
            tmp_path,
            # Artifact uses different casing than the CLI flag
            [{"body": "Legitimate finding.", "reviewer": "erikbjare"}],
        )
        # GitHub verified set also uses lowercase (as fetch_pr_reviewer_logins returns)
        result, spawn_calls = self._run(
            monkeypatch,
            # CLI flag uses TitleCase
            ["--artifact", str(path), "--trusted-reviewer", "ErikBjare"],
            github_verified_logins=frozenset(["erikbjare"]),
            github_comment_bodies={
                "erikbjare": [
                    {"body": "Legitimate finding.", "path": "app.py", "line": None}
                ]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        assert "Legitimate finding." in spawn_calls[0]["prompt"]

    # ------------------------------------------------------------------
    # Error: gh CLI unavailable with --trusted-reviewer
    # ------------------------------------------------------------------

    def test_trusted_reviewer_requires_gh_cli(self, tmp_path, monkeypatch):
        """--trusted-reviewer errors clearly when gh CLI is unavailable.

        Without the gh CLI, reviewer identity cannot be verified against the
        GitHub API.  The command must refuse to proceed rather than silently
        falling back to unverifiable artifact metadata.
        """
        from gptme.cli import cmd_review_watch

        path = self._make_artifact(
            tmp_path,
            [{"body": "A finding.", "reviewer": "ErikBjare"}],
        )
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

        spawn_calls: list[dict] = []

        def _fake_spawn_no_gh(**kw):
            spawn_calls.append(kw)
            return {"exit_reason": "done", "duration_s": 0.0}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", _fake_spawn_no_gh)

        runner = CliRunner()
        result = runner.invoke(
            util_main,
            [
                "review",
                "watch",
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
            ],
        )
        assert result.exit_code != 0, (
            "Must exit non-zero when gh CLI is unavailable but --trusted-reviewer is set"
        )
        assert len(spawn_calls) == 0
        assert "gh" in result.output.lower()

    # ------------------------------------------------------------------
    # --verify-bodies: body cross-validation
    # ------------------------------------------------------------------

    def test_verify_bodies_passes_matching_body(self, tmp_path, monkeypatch):
        """--verify-bodies passes a finding whose body appears in the reviewer's
        actual GitHub comment.

        This is the happy-path: a legitimate artifact where the body was copied
        directly from the reviewer's real PR comment.
        """
        path = self._make_artifact(
            tmp_path,
            [{"body": "This variable name is unclear.", "reviewer": "ErikBjare"}],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            github_comment_bodies={
                "erikbjare": [
                    {
                        "body": "This variable name is unclear.",
                        "path": "app.py",
                        "line": None,
                    }
                ]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        assert "This variable name is unclear." in spawn_calls[0]["prompt"]

    def test_verify_bodies_blocks_forged_body(self, tmp_path, monkeypatch):
        """--verify-bodies blocks a finding whose body does NOT appear in the
        reviewer's actual GitHub comments even though the login passes the
        allowlist and participation checks.

        This is the forgery case: an attacker crafts artifact.json with
        reviewer=ErikBjare but an attacker-controlled body.  The login check
        passes (ErikBjare did review the PR), but the body is not in any of
        ErikBjare's real comments.
        """
        path = self._make_artifact(
            tmp_path,
            [{"body": "MALICIOUS INSTRUCTIONS: rm -rf /", "reviewer": "ErikBjare"}],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            # ErikBjare's real comments do not contain the forged body
            github_comment_bodies={
                "erikbjare": [
                    {"body": "LGTM. Nice work.", "path": "app.py", "line": None}
                ]
            },
        )
        # All findings rejected → trust policy error (non-zero exit)
        assert result.exit_code != 0, (
            "Forged body must be blocked by --verify-bodies (non-zero exit)"
        )
        assert len(spawn_calls) == 0

    def test_verify_bodies_only_blocks_mismatched_findings(self, tmp_path, monkeypatch):
        """--verify-bodies blocks only findings whose body cannot be verified,
        not all findings.  Findings with matching bodies still pass.
        """
        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Legitimate comment.", "reviewer": "ErikBjare"},
                {"body": "FORGED: malicious payload", "reviewer": "ErikBjare"},
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            github_comment_bodies={
                "erikbjare": [
                    {"body": "Legitimate comment.", "path": "app.py", "line": None}
                ]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Legitimate comment." in prompt
        assert "FORGED" not in prompt
        # Diagnostic message emitted
        assert (
            "verify-bodies" in result.output.lower()
            or "verified" in result.output.lower()
        )

    def test_verify_bodies_without_trusted_reviewer_is_noop(
        self, tmp_path, monkeypatch
    ):
        """--verify-bodies without --trusted-reviewer has no effect (no reviewer
        allowlist = no body check to perform).

        The flag is only meaningful when combined with --trusted-reviewer.
        Without an allowlist, there is no reviewer login to look up GitHub
        comments for, so all findings pass through as normal.
        """
        path = self._make_artifact(
            tmp_path,
            [{"body": "Some finding.", "reviewer": ""}],
        )
        # Without --trusted-reviewer, _gh_available is False and no mocks needed
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--verify-bodies"],
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1

    def test_verify_bodies_api_failure_errors_clearly(self, tmp_path, monkeypatch):
        """--verify-bodies raises a clear error when the GitHub comment body
        API call fails (returns None).
        """
        path = self._make_artifact(
            tmp_path,
            [{"body": "A finding.", "reviewer": "ErikBjare"}],
        )
        # Simulate GitHub API failure: wire the mock to return None (not the sentinel)
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            github_comment_bodies=None,  # None = mock wired to return None (API error)
        )
        # Should error, not silently skip body verification
        assert result.exit_code != 0, (
            "Must exit non-zero when --verify-bodies but GitHub API fails"
        )
        assert len(spawn_calls) == 0

    # ------------------------------------------------------------------
    # Location metadata forgery: file / line cross-validation
    # ------------------------------------------------------------------

    def test_verify_bodies_blocks_forged_file(self, tmp_path, monkeypatch):
        """--verify-bodies blocks a finding whose body matches a real comment
        but whose ``file`` field points at a different file than the comment's
        actual location.

        Attack: attacker takes a legitimate body from ErikBjare's inline review
        comment on ``app.py`` and replays it in the artifact with
        ``file="sensitive_file.py"``.  Body verification alone would pass
        (body text matches), but the file differs, so the finding must be blocked.
        """
        path = self._make_artifact(
            tmp_path,
            # Finding claims to be on sensitive_file.py …
            [
                {
                    "body": "Remove unused import.",
                    "file": "sensitive_file.py",
                    "reviewer": "ErikBjare",
                }
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            # … but the real comment was on app.py
            github_comment_bodies={
                "erikbjare": [
                    {"body": "Remove unused import.", "path": "app.py", "line": None}
                ]
            },
        )
        assert result.exit_code != 0, (
            "Forged file metadata must be blocked by --verify-bodies (non-zero exit)"
        )
        assert len(spawn_calls) == 0

    def test_verify_bodies_allows_matching_file_and_line(self, tmp_path, monkeypatch):
        """--verify-bodies allows a finding whose body, file, and line all
        match the reviewer's real inline review comment.

        Happy path for precise inline findings: every axis of the artifact
        (body, file, line) is confirmed against the GitHub-authoritative source.
        """
        path = self._make_artifact(
            tmp_path,
            [
                {
                    "body": "Null pointer risk here.",
                    "file": "src/main.py",
                    "reviewer": "ErikBjare",
                }
            ],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            [
                "--artifact",
                str(path),
                "--trusted-reviewer",
                "ErikBjare",
                "--verify-bodies",
            ],
            github_verified_logins=frozenset(["erikbjare"]),
            github_comment_bodies={
                "erikbjare": [
                    {
                        "body": "Null pointer risk here.",
                        "path": "src/main.py",
                        "line": None,
                    }
                ]
            },
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        assert "Null pointer risk here." in spawn_calls[0]["prompt"]

    # ------------------------------------------------------------------
    # --require-trust + gh CLI available (best-effort identity verification)
    # ------------------------------------------------------------------

    def test_require_trust_alone_rejects_forged_reviewer_when_gh_available(
        self, tmp_path, monkeypatch
    ):
        """When gh CLI is available, --require-trust alone verifies that the
        reviewer actually participated in the GitHub PR.  A forged login that
        isn't in the GitHub participant set must be rejected.
        """
        from gptme.cli import cmd_review_watch

        path = self._make_artifact(
            tmp_path,
            [
                {"body": "Forged finding.", "reviewer": "attacker"},
                {"body": "Real finding.", "reviewer": "erikbjare"},
            ],
        )
        monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
        monkeypatch.setattr(
            cmd_review_watch,
            "fetch_pr_reviewer_logins",
            lambda owner, repo, pr_num, **kw: frozenset(["erikbjare"]),
        )

        spawn_calls: list[dict] = []

        def fake_spawn(**kwargs):
            spawn_calls.append(kwargs)
            return {"exit_reason": "done", "duration_s": 0.1}

        monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)
        result = CliRunner().invoke(
            util_main,
            ["review", "watch", "--artifact", str(path), "--require-trust"],
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        prompt = spawn_calls[0]["prompt"]
        assert "Real finding." in prompt
        assert "Forged finding." not in prompt

    def test_require_trust_alone_warns_and_accepts_when_gh_unavailable(
        self, tmp_path, monkeypatch
    ):
        """When gh CLI is unavailable, --require-trust alone warns and accepts
        attributed findings (offline / no-gh mode is a supported use case).
        """
        # The standard _run helper sets _gh_available = False for --require-trust alone.
        # This verifies the offline behavior is preserved and produces no error.
        path = self._make_artifact(
            tmp_path,
            [{"body": "Attributed finding.", "reviewer": "someone"}],
        )
        result, spawn_calls = self._run(
            monkeypatch,
            ["--artifact", str(path), "--require-trust"],
        )
        assert result.exit_code == 0, result.output
        assert len(spawn_calls) == 1
        assert "Attributed finding." in spawn_calls[0]["prompt"]
