import importlib.util
import re
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_changelog.py"
_SPEC = importlib.util.spec_from_file_location("build_changelog", _SCRIPT)
assert _SPEC and _SPEC.loader
build_changelog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_changelog)

FULL = "5a8912b64f0e1d2c3b4a5968778695a4b3c2d1e0"
COMMIT_LINK = re.compile(
    r"\[`(?P<text>[0-9a-f]+)`\]\(https://github\.com/gptme/gptme/commit/(?P<sha>[0-9a-f]+)\)"
)


def test_commit_link_uses_full_sha_in_url_short_text():
    commit = build_changelog.Commit(
        id=FULL, msg="fix: a bug", org="gptme", repo="gptme", short_id=FULL[:9]
    )
    assert commit.format() == (
        f"fix: a bug ([`{FULL[:9]}`](https://github.com/gptme/gptme/commit/{FULL}))"
    )


def test_tagged_tip_commit_links_tag():
    commit = build_changelog.Commit(
        id=FULL,
        msg="chore: bump version to 0.34.0",
        org="gptme",
        repo="gptme",
        short_id=FULL[:9],
        tag="v0.34.0",
    )
    assert commit.format() == (
        "chore: bump version to 0.34.0 "
        "([`v0.34.0`](https://github.com/gptme/gptme/tree/v0.34.0))"
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_summary_repo_links_full_shas(tmp_path: Path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "config", "tag.gpgsign", "false")
    # isolate from any global hooks (e.g. identity guards)
    (tmp_path / ".nohooks").mkdir()
    _git(tmp_path, "config", "core.hooksPath", str(tmp_path / ".nohooks"))

    def commit(msg: str) -> str:
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
        return _git(tmp_path, "rev-parse", "HEAD")

    commit("chore: initial")
    _git(tmp_path, "tag", "v0.1.0")
    feat = commit("feat: add thing")
    fix = commit("fix: broken thing")
    commit("chore: bump version to 0.2.0")
    _git(tmp_path, "tag", "v0.2.0")

    monkeypatch.chdir(tmp_path)
    out = build_changelog.summary_repo(
        "gptme",
        "gptme",
        str(tmp_path),
        commit_range=("v0.1.0", "v0.2.0"),
        filter_types=[],
        repo_order=[],
    )

    links = {m["sha"]: m["text"] for m in COMMIT_LINK.finditer(out)}
    assert set(links) == {feat, fix}
    for sha, text in links.items():
        assert len(sha) == 40
        assert sha.startswith(text) and len(text) < 40

    # the tagged version-bump commit gets amended by the release flow, so it
    # must link the tag rather than its (soon orphaned) SHA
    assert (
        "chore: bump version to 0.2.0 "
        "([`v0.2.0`](https://github.com/gptme/gptme/tree/v0.2.0))"
    ) in out


def test_summary_repo_tag_link_survives_amend(tmp_path: Path, monkeypatch):
    """The bump commit gets amended during the real release flow; the tag link must survive.

    The release flow: commit bump → tag → generate changelog → amend (embed notes) →
    force-move tag. The original SHA is orphaned. The changelog (already generated)
    must still have a live link — which it does because it links the *tag*, not the SHA.
    """
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "config", "tag.gpgsign", "false")
    (tmp_path / ".nohooks").mkdir()
    _git(tmp_path, "config", "core.hooksPath", str(tmp_path / ".nohooks"))

    def commit(msg: str) -> str:
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
        return _git(tmp_path, "rev-parse", "HEAD")

    commit("chore: initial")
    _git(tmp_path, "tag", "v0.1.0")
    commit("feat: add thing")

    # Step 1: commit the bump and tag it (pre-amend state)
    bump_sha_before = commit("chore: bump version to 0.2.0")
    _git(tmp_path, "tag", "v0.2.0", bump_sha_before)

    # Step 2: generate the changelog (this is what the release flow does before amending)
    monkeypatch.chdir(tmp_path)
    out = build_changelog.summary_repo(
        "gptme",
        "gptme",
        str(tmp_path),
        commit_range=("v0.1.0", "v0.2.0"),
        filter_types=[],
        repo_order=[],
    )
    tag_link = "([`v0.2.0`](https://github.com/gptme/gptme/tree/v0.2.0))"
    assert tag_link in out, f"bump commit must link via tag before amend:\n{out}"

    # Step 3: amend (embed release notes into the bump commit) + force-move the tag
    (tmp_path / "RELEASE.md").write_text(out)
    _git(tmp_path, "add", "RELEASE.md")
    _git(tmp_path, "commit", "--amend", "--no-edit")
    bump_sha_after = _git(tmp_path, "rev-parse", "HEAD")
    assert bump_sha_after != bump_sha_before, "amend must produce a new SHA"
    _git(tmp_path, "tag", "-f", "v0.2.0", bump_sha_after)

    # The original SHA is now orphaned.  The tag-based link must still be valid:
    # the tag moved to the new commit and the link text is unchanged.
    assert _git(tmp_path, "rev-parse", "v0.2.0^{commit}") == bump_sha_after
    assert tag_link in out  # the already-generated changelog still has a live link
