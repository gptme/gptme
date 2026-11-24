"""Real-git coverage of git/registry skill installs and symlinked uninstall.

Uses local ``file://`` repositories in place of remotes, so no network access.
"""

import os
import subprocess
from pathlib import Path

import pytest

from gptme.lessons import installer


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "get_config_dir", lambda: tmp_path / "config")
    # Test seam: let a local file:// repo stand in for an https:// remote.
    orig = installer._is_git_url
    monkeypatch.setattr(
        installer, "_is_git_url", lambda s: s.startswith("file://") or orig(s)
    )


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: test skill\n---\n# {name}\n"


def make_repo(
    root: Path, files: dict[str, str], links: dict[str, str] | None = None
) -> Path:
    root.mkdir(parents=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for rel, target in (links or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["commit", "-q", "--no-verify", "-m", "init"],
    ):
        subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, env=env
        )
    return root


def use_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str] | None = None,
    links: dict[str, str] | None = None,
) -> None:
    repo = make_repo(
        tmp_path / "contrib",
        files
        or {
            "skills/foo/SKILL.md": skill_md("foo"),
            "shared/common.md": "shared text\n",
        },
        links=links,
    )
    monkeypatch.setattr(installer, "DEFAULT_REGISTRY_REPO", f"file://{repo}")


def install_local(tmp_path: Path, name: str, extra: str) -> Path:
    src = tmp_path / f"local-{name}"
    src.mkdir()
    (src / "SKILL.md").write_text(skill_md(name))
    (src / extra).write_text("user data")
    assert installer.install_skill(str(src))[0]
    return installer.get_skills_dir() / name


def test_whole_repo_install_keeps_subdirectories_and_drops_git_dir(
    tmp_path: Path,
) -> None:
    repo = make_repo(
        tmp_path / "repo",
        {
            "SKILL.md": skill_md("whole"),
            "scripts/helper.py": "print('hi')\n",
            "references/guide.md": "guide\n",
        },
    )
    ok, msg = installer.install_skill(f"file://{repo}")
    assert ok, msg
    installed = installer.get_skills_dir() / "whole"
    assert (installed / "SKILL.md").exists()
    assert (installed / "scripts" / "helper.py").exists()
    assert (installed / "references" / "guide.md").exists()
    assert not (installed / ".git").exists()
    assert installer.get_manifest().skills["whole"].source == f"file://{repo}"


def test_symlink_outside_sparse_cone_is_kept_as_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_registry(
        tmp_path, monkeypatch, links={"skills/foo/common.md": "../../shared/common.md"}
    )
    ok, msg = installer.install_skill("foo")
    assert ok, msg
    link = installer.get_skills_dir() / "foo" / "common.md"
    assert link.is_symlink()
    assert os.readlink(link) == "../../shared/common.md"


def test_absolute_symlink_is_not_dereferenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOP-SECRET")
    use_registry(tmp_path, monkeypatch, links={"skills/foo/creds.txt": str(secret)})
    ok, msg = installer.install_skill("foo")
    assert ok, msg
    assert (installer.get_skills_dir() / "foo" / "creds.txt").is_symlink()


def test_failed_force_reinstall_preserves_previous_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = install_local(tmp_path, "foo", "notes.txt")
    manifest_before = installer.get_manifest_path().read_bytes()
    # Replacement fails validation (no description) after it has been fetched.
    use_registry(
        tmp_path, monkeypatch, files={"skills/foo/SKILL.md": "---\nname: foo\n---\n"}
    )
    ok, msg = installer.install_skill("foo", force=True)
    assert not ok
    assert "Missing required field: description" in msg
    assert (installed / "notes.txt").read_text() == "user data"
    assert installer.get_manifest_path().read_bytes() == manifest_before


def test_non_string_name_fails_cleanly_without_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_registry(
        tmp_path,
        monkeypatch,
        files={"skills/year/SKILL.md": "---\nname: 2024\ndescription: d\n---\nbody\n"},
    )
    ok, msg = installer.install_skill("year")
    assert not ok
    assert "must be a string" in msg
    assert list(installer.get_skills_dir().iterdir()) == []
    assert installer.get_manifest().skills == {}


def test_force_install_does_not_delete_unrelated_skill_at_url_basename(
    tmp_path: Path,
) -> None:
    review = install_local(tmp_path, "review", "my-notes.md")
    # Third-party repo: directory skills/review, but the skill is "code-review".
    repo = make_repo(
        tmp_path / "thirdparty", {"skills/review/SKILL.md": skill_md("code-review")}
    )
    ok, msg = installer.install_skill(f"file://{repo}#skills/review", force=True)
    assert ok, msg
    assert (installer.get_skills_dir() / "code-review" / "SKILL.md").exists()
    assert (review / "my-notes.md").read_text() == "user data"
    assert set(installer.get_manifest().skills) == {"review", "code-review"}


def test_install_without_force_refuses_existing_final_name(tmp_path: Path) -> None:
    install_local(tmp_path, "code-review", "notes.txt")
    repo = make_repo(
        tmp_path / "thirdparty", {"skills/review/SKILL.md": skill_md("code-review")}
    )
    ok, msg = installer.install_skill(f"file://{repo}#skills/review")
    assert not ok
    assert "already installed" in msg


def test_failed_clone_reports_error(tmp_path: Path) -> None:
    ok, msg = installer.install_skill(f"file://{tmp_path / 'missing'}")
    assert not ok
    assert "Failed to install skill from" in msg


@pytest.mark.parametrize("in_manifest", [False, True])
def test_uninstall_symlinked_skill_unlinks_without_touching_source(
    tmp_path: Path, in_manifest: bool
) -> None:
    dev = tmp_path / "dev-skill"
    dev.mkdir()
    (dev / "SKILL.md").write_text(skill_md("dev"))
    skills = installer.get_skills_dir()
    skills.mkdir(parents=True)
    other = skills / "other"
    other.mkdir()
    (other / "SKILL.md").write_text(skill_md("other"))
    link = skills / "dev"
    # A manifest entry may only point inside the skills dir; use an in-tree target.
    link.symlink_to(other if in_manifest else dev, target_is_directory=True)
    if in_manifest:
        manifest = installer.get_manifest()
        manifest.skills["dev"] = installer.InstalledSkill(
            name="dev", version="1", source="x", install_path=str(link)
        )
        manifest.save(installer.get_manifest_path())

    ok, msg = installer.uninstall_skill("dev")
    assert ok, msg
    assert not link.is_symlink()
    assert (dev / "SKILL.md").exists()
    assert (other / "SKILL.md").exists()
