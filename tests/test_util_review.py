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
