"""Tests for the gptme-util attest commands."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.attestation import AttestationError, _attestation_id, verify_attestation
from gptme.cli.util import main


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "feat/test-attest")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")


def test_attest_sign_and_verify_file_roundtrip(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    output_file = repo / "output.txt"
    output_file.write_text("signed content\n")

    monkeypatch.setenv("GPTME_AGENT_NAME", "bob")
    monkeypatch.setenv("BOB_SESSION_ID", "84b9")
    monkeypatch.setenv("CC_MODEL", "gpt-5.4")

    runner = CliRunner()

    sign = runner.invoke(
        main, ["attest", "sign", str(output_file)], catch_exceptions=False
    )
    assert sign.exit_code == 0

    attestation_path = Path(sign.output.strip())
    assert attestation_path.exists()

    payload = json.loads(attestation_path.read_text())
    assert payload["agent"]["session_id"] == "84b9"
    assert payload["output"]["path"] == "output.txt"
    assert payload["output"]["sha256"].startswith("sha256:")

    verify = runner.invoke(
        main,
        ["attest", "verify", str(attestation_path), "--workspace", str(repo)],
        catch_exceptions=False,
    )
    assert verify.exit_code == 0
    assert "verified gai_" in verify.output


def test_attest_verify_rejects_missing_workspace_commit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    output_file = repo / "output.txt"
    output_file.write_text("signed content\n")

    monkeypatch.setenv("GPTME_AGENT_NAME", "bob")
    monkeypatch.setenv("BOB_SESSION_ID", "84b9")

    runner = CliRunner()
    sign = runner.invoke(
        main, ["attest", "sign", str(output_file)], catch_exceptions=False
    )
    attestation_path = Path(sign.output.strip())

    payload = json.loads(attestation_path.read_text())
    payload["agent"]["workspace_commit"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    payload["id"] = _attestation_id({k: v for k, v in payload.items() if k != "id"})
    attestation_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    verify = runner.invoke(
        main,
        ["attest", "verify", str(attestation_path), "--workspace", str(repo)],
        catch_exceptions=False,
    )
    assert verify.exit_code != 0
    assert (
        "Not a valid object name" in verify.output
        or "git command failed" in verify.output
    )


def test_attest_sign_requires_git_repo(tmp_path):
    target = tmp_path / "output.txt"
    target.write_text("signed content\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["attest", "sign", str(target)], catch_exceptions=False
    )
    assert result.exit_code != 0
    assert "git repository" in result.output


def test_attest_sign_text_uses_unknown_session_without_env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    for key in (
        "GPTME_SESSION_ID",
        "BOB_SESSION_ID",
        "SESSION_ID",
        "GIT_COMMITTER_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    runner = CliRunner()
    sign = runner.invoke(
        main,
        ["attest", "sign", "--text", "hello world", "--workspace", str(repo)],
        catch_exceptions=False,
    )
    assert sign.exit_code == 0

    payload = json.loads(Path(sign.output.strip()).read_text())
    assert payload["agent"]["session_id"] == "unknown"
    assert payload["output"]["type"] == "text"


def test_attest_verify_rejects_embedded_path_outside_workspace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    output_file = repo / "output.txt"
    output_file.write_text("signed content\n")
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("top secret\n")

    monkeypatch.setenv("GPTME_AGENT_NAME", "bob")
    runner = CliRunner()
    sign = runner.invoke(
        main, ["attest", "sign", str(output_file)], catch_exceptions=False
    )
    assert sign.exit_code == 0

    attestation_path = Path(sign.output.strip())
    payload = json.loads(attestation_path.read_text())
    payload["output"]["path"] = "../secret.txt"
    payload["output"]["sha256"] = (
        f"sha256:{hashlib.sha256(secret_file.read_bytes()).hexdigest()}"
    )
    payload["id"] = _attestation_id({k: v for k, v in payload.items() if k != "id"})
    attestation_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    verify = runner.invoke(
        main,
        ["attest", "verify", str(attestation_path), "--workspace", str(repo)],
        catch_exceptions=False,
    )
    assert verify.exit_code != 0
    assert "escapes workspace" in verify.output


def test_verify_attestation_rejects_content_path_and_text_together(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    output_file = repo / "output.txt"
    output_file.write_text("signed content\n")

    monkeypatch.setenv("GPTME_AGENT_NAME", "bob")
    runner = CliRunner()
    sign = runner.invoke(
        main, ["attest", "sign", str(output_file)], catch_exceptions=False
    )
    assert sign.exit_code == 0

    with pytest.raises(AttestationError, match="either content_path or text"):
        verify_attestation(
            Path(sign.output.strip()),
            workspace=repo,
            content_path=output_file,
            text="signed content\n",
        )
