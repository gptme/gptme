"""Release-source pinning and safe publication against a real local remote."""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
PUBLISH = ROOT / "scripts/publish_release.sh"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-b", "master", str(checkout)], check=True)
    # Synthetic repositories must not inherit machine-specific signing/hooks.
    git(checkout, "config", "core.hooksPath", "/dev/null")
    git(checkout, "config", "commit.gpgsign", "false")
    git(checkout, "config", "tag.gpgsign", "false")
    git(remote, "config", "core.hooksPath", "/dev/null")
    git(checkout, "config", "user.name", "Release Test")
    git(checkout, "config", "user.email", "release@example.test")
    (checkout / "pyproject.toml").write_text('[project]\nversion = "0.35.1"\n')
    git(checkout, "add", "pyproject.toml")
    git(checkout, "commit", "-m", "initial")
    git(checkout, "tag", "v0.35.1")
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "push", "origin", "master", "--tags")
    return checkout


def source_snippet() -> str:
    workflow = WORKFLOW.read_text()
    snippet = workflow.split("      - name: Resolve release source\n", 1)[1]
    snippet = snippet.split("        run: |\n", 1)[1]
    return textwrap.dedent(snippet.split("\n      - name:", 1)[0])


@pytest.mark.parametrize("source", ["master", "v0.35.1", "", "invalid-ref", "side"])
def test_resolve_release_source(repo: Path, tmp_path: Path, source: str) -> None:
    selected = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "side")
    (repo / "side").write_text("not on master")
    git(repo, "add", "side")
    git(repo, "commit", "-m", "side")
    git(repo, "switch", "master")
    (repo / "new").write_text("master advanced after dispatch")
    git(repo, "add", "new")
    git(repo, "commit", "-m", "new master")
    git(repo, "push", "origin", "master")
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", source_snippet()],
        cwd=repo,
        env={
            **os.environ,
            "SOURCE_REF": source,
            "WORKFLOW_SHA": selected,
            "GITHUB_OUTPUT": str(output),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    if source in {"invalid-ref", "side"}:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        expected = git(repo, "rev-parse", "master") if source == "master" else selected
        assert f"source_sha={expected}" in output.read_text()
        assert git(repo, "rev-parse", "HEAD") == expected


def test_prepare_uses_checked_sha_and_current_publisher() -> None:
    workflow = WORKFLOW.read_text()
    prepare = workflow.split("  prepare:\n", 1)[1].split("  publish:\n", 1)[0]
    assert "ref: ${{ github.sha }}" in prepare
    assert "SOURCE_SHA: ${{ needs.check.outputs.source_sha }}" in prepare
    assert 'git checkout --detach "$SOURCE_SHA"' in prepare
    assert 'cp scripts/publish_release.sh "$RUNNER_TEMP/publish_release.sh"' in prepare
    assert 'bash "$RUNNER_TEMP/publish_release.sh"' in prepare


@pytest.mark.parametrize(
    "scenario",
    ["unchanged", "advanced", "race", "denied", "unmerged", "unmerged-ahead"],
)
def test_stable_publish_does_not_rewind_master(
    repo: Path, tmp_path: Path, scenario: str
) -> None:
    source = git(repo, "rev-parse", "HEAD")
    if scenario in {"advanced", "race", "unmerged"}:
        (repo / "unreleased").write_text("must not be released")
        git(repo, "add", "unreleased")
        git(repo, "commit", "-m", "unreleased change")
        git(repo, "push", "origin", "master")
    master = git(repo, "rev-parse", "origin/master")
    if scenario == "race":
        # Object exists on the remote but master moves only during publication.
        git(
            repo,
            "--git-dir",
            str(tmp_path / "remote.git"),
            "update-ref",
            "refs/heads/master",
            source,
        )
        git(repo, "fetch", "origin", "master")
    git(repo, "checkout", "--detach", source)
    if scenario in {"unmerged", "unmerged-ahead"}:
        # Local make release-* can start on a feature branch, including one
        # that could fast-forward master. Neither source is merged yet.
        (repo / "feature").write_text("unmerged feature must not ship")
        git(repo, "add", "feature")
        git(repo, "commit", "-m", "unmerged feature")
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.36.0"\n')
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-m", "release bump")
    git(repo, "tag", "v0.36.0")
    release = git(repo, "rev-parse", "HEAD")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Stub only the external publication/version commands, never git transport.
    for name, body in {
        "uv": "echo 0.36.0\n",
        "gh": 'if [ "$1 $2" = "release view" ]; then exit 1; fi\necho "$*" >> "$GH_CALLS"\n',
    }.items():
        executable = bin_dir / name
        executable.write_text("#!/bin/bash\n" + body)
        executable.chmod(0o755)
    if scenario in {"race", "denied"}:
        # Simulate a concurrent merge (or unrelated refusal) at transport time.
        # All ordinary git operations and the bare-remote push remain real.
        import shutil

        real_git = shutil.which("git")
        assert real_git
        wrapper = bin_dir / "git"
        wrapper.write_text(
            "#!/bin/bash\n"
            'if [ "$*" = "push origin HEAD:master" ]; then\n'
            + (
                f'  "{real_git}" --git-dir "{tmp_path / "remote.git"}" update-ref refs/heads/master {master}\n'
                if scenario == "race"
                else "  exit 1\n"
            )
            + "fi\n"
            + f'exec "{real_git}" "$@"\n'
        )
        wrapper.chmod(0o755)
    calls = tmp_path / "gh-calls"
    result = subprocess.run(
        ["bash", str(PUBLISH)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GH_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if scenario in {"denied", "unmerged", "unmerged-ahead"}:
        assert result.returncode != 0
        assert not git(repo, "ls-remote", "origin", "refs/tags/v0.36.0")
        assert not calls.exists()
        assert (
            git(repo, "ls-remote", "origin", "refs/heads/master").split()[0] == master
        )
        if scenario != "denied":
            assert "release source is not on origin/master" in result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "ls-remote", "origin", "refs/tags/v0.36.0").split()[0] == release
    expected_master = master if scenario in {"advanced", "race"} else release
    assert (
        git(repo, "ls-remote", "origin", "refs/heads/master").split()[0]
        == expected_master
    )
    assert "release create v0.36.0" in calls.read_text()
    if scenario in {"advanced", "race"}:
        assert not (repo / "unreleased").exists()


@pytest.mark.parametrize(
    ("release_type", "checks_ci"), [("minor", True), ("patch", True), ("dev", False)]
)
def test_stable_can_promote_last_dev_source(
    repo: Path, tmp_path: Path, release_type: str, checks_ci: bool
) -> None:
    workflow = WORKFLOW.read_text()
    snippet = workflow.split("        id: check\n", 1)[1].split("        run: |\n", 1)[
        1
    ]
    snippet = textwrap.dedent(
        snippet.split("          # Verify CI is green on HEAD", 1)[0]
    )
    snippet += "\necho reached_ci_gate\n"
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", snippet],
        cwd=repo,
        env={**os.environ, "RELEASE_TYPE": release_type, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert ("reached_ci_gate" in result.stdout) == checks_ci
    assert ("should_release=false" in output.read_text()) != checks_ci
