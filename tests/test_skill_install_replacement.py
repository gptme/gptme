"""Real-filesystem coverage of local skill replacement and its failure boundaries."""

import shutil
from pathlib import Path

import pytest

from gptme.lessons import installer


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "get_config_dir", lambda: tmp_path / "config")


def make_skill(path: Path, version: str = "1") -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: Synthetic skill\nversion: '{version}'\n---\n# Demo\n"
    )
    (path / "payload.txt").write_text(f"payload {version}")
    return path


def snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()
    }


def installed_pair(tmp_path: Path) -> tuple[Path, Path]:
    original = make_skill(tmp_path / "original")
    assert installer.install_skill(str(original))[0]
    return installer.get_skills_dir() / "demo", make_skill(
        tmp_path / "replacement", "2"
    )


@pytest.mark.parametrize(
    "input_kind", ["directory", "file", "alias", "nested", "ancestor"]
)
def test_overlap_refusal_preserves_installation(
    tmp_path: Path, input_kind: str
) -> None:
    dest, _ = installed_pair(tmp_path)
    source = dest
    if input_kind == "file":
        source = dest / "SKILL.md"
    elif input_kind == "alias":
        source = tmp_path / "alias"
        source.symlink_to(dest, target_is_directory=True)
    elif input_kind == "nested":
        source = make_skill(dest / "nested", "2")
    elif input_kind == "ancestor":
        source = dest.parent
        (source / "SKILL.md").write_bytes((dest / "SKILL.md").read_bytes())
    before = snapshot(installer.get_skills_dir())
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert snapshot(installer.get_skills_dir()) == before
    assert installer.get_manifest_path().read_bytes() == manifest


@pytest.mark.parametrize("existing", [False, True])
def test_partial_copy_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    if existing:
        dest, source = installed_pair(tmp_path)
    else:
        source = make_skill(tmp_path / "source")
        dest = installer.get_skills_dir() / "demo"
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes() if manifest_path.exists() else None

    def fail_copy(src: Path, dst: Path, **kwargs: object) -> None:
        dst.mkdir()
        shutil.copy2(src / "payload.txt", dst / "payload.txt")
        raise shutil.Error("injected partial copy failure")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copy)
    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert snapshot(dest) == before
    assert dest.exists() == existing
    assert (manifest_path.read_bytes() if manifest_path.exists() else None) == manifest
    assert list(installer.get_skills_dir().iterdir()) == ([dest] if existing else [])


@pytest.mark.parametrize(
    "invalid", ["missing-description", "list", "numeric-name", "bad-yaml"]
)
def test_invalid_replacement_preserves_prior_state(
    tmp_path: Path, invalid: str
) -> None:
    dest, source = installed_pair(tmp_path)
    bad_content = {
        "missing-description": "name: demo",
        "list": "- demo",
        "numeric-name": "name: 42\ndescription: Invalid name",
        "bad-yaml": "name: [",
    }[invalid]
    (source / "SKILL.md").write_text(f"---\n{bad_content}\n---\n# Invalid\n")
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), name="demo", force=True)

    assert not ok, message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


@pytest.mark.parametrize(
    "failure", ["manifest-write", "backup-rename", "publish-rename", "manifest-publish"]
)
def test_publication_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dest, source = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes()
    real_rename = Path.rename
    real_replace = Path.replace

    def fail_save(self: installer.SkillManifest, path: Path) -> None:
        path.write_text("partial manifest")
        raise OSError("injected manifest write failure")

    def fail_rename(self: Path, target: Path) -> Path:
        if (failure == "backup-rename" and self == dest) or (
            failure == "publish-rename" and target == dest and self.name != "old"
        ):
            raise OSError("injected rename failure")
        return real_rename(self, target)

    def fail_replace(self: Path, target: Path) -> Path:
        if target == manifest_path:
            raise OSError("injected manifest publication failure")
        return real_replace(self, target)

    if failure == "manifest-write":
        monkeypatch.setattr(installer.SkillManifest, "save", fail_save)
    monkeypatch.setattr(Path, "rename", fail_rename)
    if failure == "manifest-publish":
        monkeypatch.setattr(Path, "replace", fail_replace)

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert snapshot(dest) == before
    assert manifest_path.read_bytes() == manifest
    assert list(installer.get_skills_dir().iterdir()) == [dest]


@pytest.mark.parametrize("from_file", [False, True])
def test_success_replaces_files_and_manifest(tmp_path: Path, from_file: bool) -> None:
    dest, source = installed_pair(tmp_path)
    (dest / "obsolete.txt").write_text("must disappear")
    # A symlink is copied as a symlink, not expanded into its target bytes.
    (source / "payload-link").symlink_to("payload.txt")
    ok, message = installer.install_skill(
        str(source / "SKILL.md" if from_file else source), force=True
    )
    assert ok, message
    assert snapshot(dest) == snapshot(source)
    assert (dest / "payload-link").is_symlink()
    record = installer.get_manifest().skills["demo"]
    assert record.version == "2"
    assert record.install_path == str(dest)
    assert list(installer.get_skills_dir().iterdir()) == [dest]


def test_no_force_does_not_touch_installation(tmp_path: Path) -> None:
    dest, source = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()
    assert not installer.install_skill(str(source))[0]
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


def test_initial_install_name_override_and_minimal_metadata(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source")
    (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Minimal\n---\n# Demo\n"
    )
    ok, message = installer.install_skill(str(source / "SKILL.md"), name="custom")
    assert ok, message
    dest = installer.get_skills_dir() / "custom"
    assert snapshot(dest) == snapshot(source)
    record = installer.get_manifest().skills["custom"]
    assert record.version == "unknown"
    assert record.install_path == str(dest)


@pytest.mark.parametrize("failure", ["remove-new", "restore-old"])
def test_rollback_failure_retains_recovery_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dest, source = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes()
    real_rename = Path.rename
    real_rmtree = shutil.rmtree

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("injected publication failure")

    def fail_rename(self: Path, target: Path) -> Path:
        if failure == "restore-old" and self.name == "old":
            raise OSError("injected rollback rename failure")
        return real_rename(self, target)

    def fail_remove(path: Path, *args: object, **kwargs: object) -> None:
        if failure == "remove-new" and path == dest:
            raise OSError("injected rollback remove failure")
        real_rmtree(path)

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "rename", fail_rename)
    monkeypatch.setattr(installer.shutil, "rmtree", fail_remove)
    ok, message = installer.install_skill(str(source), force=True)

    assert not ok
    assert "Rollback failed" in message
    recovery_dirs = list(installer.get_skills_dir().glob(".skill-install-*"))
    assert len(recovery_dirs) == 1
    recovery = recovery_dirs[0]
    assert str(recovery) in message
    assert snapshot(recovery / "old") == before
    assert manifest_path.read_bytes() == manifest


def test_cli_overlap_failure_is_clean_and_non_destructive(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from gptme.cli.cmd_skills import skills

    dest, _ = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()
    result = CliRunner().invoke(skills, ["install", str(dest / "SKILL.md"), "--force"])
    assert result.exit_code == 1
    assert "overlap" in result.output
    assert "Traceback" not in result.output
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


def test_validation_checks_copied_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, source = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()
    real_copytree = shutil.copytree

    def corrupt_copy(src: Path, dst: Path, **kwargs: object) -> Path:
        result = real_copytree(src, dst, symlinks=True)
        (dst / "SKILL.md").write_text("---\nname: demo\n---\n# Invalid staged copy\n")
        return result

    monkeypatch.setattr(installer.shutil, "copytree", corrupt_copy)
    ok, message = installer.install_skill(str(source), force=True)
    assert not ok
    assert "validation failed" in message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


@pytest.mark.parametrize("existing_link", [False, True])
def test_destination_symlink_does_not_delete_its_target(
    tmp_path: Path, existing_link: bool
) -> None:
    source = make_skill(tmp_path / "source")
    external = tmp_path / "external"
    if existing_link:
        external.mkdir()
        (external / "keep.txt").write_text("external bytes")
    skills_dir = installer.get_skills_dir()
    skills_dir.mkdir(parents=True)
    dest = skills_dir / "demo"
    dest.symlink_to(external, target_is_directory=True)
    before = snapshot(external)

    assert not installer.install_skill(str(source))[0]
    assert dest.is_symlink()
    ok, message = installer.install_skill(str(source), force=True)
    assert ok, message
    assert not dest.is_symlink()
    assert snapshot(dest) == snapshot(source)
    assert snapshot(external) == before


def test_ancestor_overlap_through_destination_symlink_is_refused(
    tmp_path: Path,
) -> None:
    dest, _ = installed_pair(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    shutil.rmtree(dest)
    dest.symlink_to(external, target_is_directory=True)
    source = dest.parent
    (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Ancestor\n---\n# Demo\n"
    )
    before = snapshot(source)
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert dest.is_symlink()
    assert snapshot(source) == before
    assert installer.get_manifest_path().read_bytes() == manifest


def test_validation_error_containing_recommended_is_not_a_warning(
    tmp_path: Path,
) -> None:
    dest, source = installed_pair(tmp_path)
    (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Invalid dependency\n"
        "depends: ['recommended/invalid']\n---\n# Demo\n"
    )
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()
    ok, message = installer.install_skill(str(source), force=True)
    assert not ok, message
    assert "invalid characters" in message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


@pytest.mark.parametrize("force", [False, True])
def test_skills_on_another_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    """Model device-local rename rules without requiring a second CI filesystem."""
    import errno

    skills_dir = installer.get_skills_dir()
    skills_dir.parent.mkdir(parents=True)
    external = tmp_path / "external-skills"
    external.mkdir()
    skills_dir.symlink_to(external, target_is_directory=True)
    if force:
        dest, source = installed_pair(tmp_path)
    else:
        dest = skills_dir / "demo"
        source = make_skill(tmp_path / "source")
    real_rename = Path.rename
    real_replace = Path.replace

    def on_skill_device(path: Path) -> bool:
        return path.parent.resolve().is_relative_to(external)

    def device_rename(self: Path, target: Path) -> Path:
        if on_skill_device(self) != on_skill_device(target):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(self, target)

    def device_replace(self: Path, target: Path) -> Path:
        if on_skill_device(self) != on_skill_device(target):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "rename", device_rename)
    monkeypatch.setattr(Path, "replace", device_replace)
    ok, message = installer.install_skill(str(source), force=force)

    assert ok, message
    assert snapshot(dest) == snapshot(source)
    assert installer.get_manifest().skills["demo"].version == ("2" if force else "1")
    assert list(skills_dir.iterdir()) == [dest]
    assert not list(skills_dir.parent.glob(".skill-*"))


@pytest.mark.parametrize("failure_number", [1, 2, 3])
def test_staging_creation_failure_returns_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_number: int
) -> None:
    dest, source = installed_pair(tmp_path)
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()
    real_mkdtemp = installer.tempfile.mkdtemp
    calls = 0

    def fail_mkdtemp(*, prefix: str, dir: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == failure_number:
            raise OSError("injected staging creation failure")
        return real_mkdtemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(installer.tempfile, "mkdtemp", fail_mkdtemp)
    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert "injected staging creation failure" in message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest
    assert list(installer.get_skills_dir().iterdir()) == [dest]
    assert not list(installer.get_skills_dir().parent.glob(".skill-*"))


@pytest.mark.parametrize("force", [False, True])
def test_relative_metadata_symlink_keeps_publication_coordinates(
    tmp_path: Path, force: bool
) -> None:
    if force:
        dest, _ = installed_pair(tmp_path)
    else:
        dest = installer.get_skills_dir() / "demo"
    source = make_skill(tmp_path / "sources" / "demo", "2")
    common = make_skill(source.parent / "common", "source-version")
    installed_common = installer.get_skills_dir() / "common"
    installed_common.mkdir(parents=True)
    (installed_common / "SKILL.md").write_text(
        (common / "SKILL.md").read_text().replace("source-version", "2")
    )
    (source / "SKILL.md").unlink()
    (source / "SKILL.md").symlink_to("../common/SKILL.md")

    ok, message = installer.install_skill(str(source), force=force)

    assert ok, message
    assert (dest / "SKILL.md").is_symlink()
    assert (dest / "SKILL.md").readlink() == Path("../common/SKILL.md")
    assert (dest / "SKILL.md").read_bytes() == (
        installed_common / "SKILL.md"
    ).read_bytes()
    assert installer.get_manifest().skills["demo"].version == "2"
    assert not list(installer.get_skills_dir().glob(".skill-*"))


@pytest.mark.parametrize("mode", [0o600, 0o640])
def test_local_install_preserves_existing_manifest_mode(
    tmp_path: Path, mode: int
) -> None:
    import stat

    _, source = installed_pair(tmp_path)
    manifest_path = installer.get_manifest_path()
    manifest_path.chmod(mode)

    ok, message = installer.install_skill(str(source), force=True)

    assert ok, message
    assert stat.S_IMODE(manifest_path.stat().st_mode) == mode


def test_new_local_manifest_is_private(tmp_path: Path) -> None:
    import stat

    source = make_skill(tmp_path / "source")
    ok, message = installer.install_skill(str(source))
    assert ok, message
    assert stat.S_IMODE(installer.get_manifest_path().stat().st_mode) == 0o600


def test_validation_tree_is_private_and_published_mode_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    source = make_skill(tmp_path / "source")
    source.chmod(0o755)
    real_validate = installer.validate_skill
    validated = []

    def check_private(path: Path, *, metadata_path: Path | None = None) -> list[str]:
        assert path.parent == installer.get_skills_dir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        validated.append(path)
        return real_validate(path, metadata_path=metadata_path)

    monkeypatch.setattr(installer, "validate_skill", check_private)
    ok, message = installer.install_skill(str(source))
    assert ok, message
    assert len(validated) == 1
    assert stat.S_IMODE((installer.get_skills_dir() / "demo").stat().st_mode) == 0o755
    assert not validated[0].exists()


@pytest.mark.parametrize("failure", ["tree-mode", "manifest-mode", "final-tree-mode"])
def test_permission_preparation_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dest, source = installed_pair(tmp_path)
    source.chmod(0o755)
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes()
    real_chmod = Path.chmod

    def fail_chmod(self: Path, mode: int, **kwargs: object) -> None:
        if (
            (failure == "tree-mode" and self.name == "new")
            or (failure == "manifest-mode" and self.name == installer.MANIFEST_FILE)
            or (
                failure == "final-tree-mode"
                and self.name.startswith(".skill-new-")
                and mode != 0o700
            )
        ):
            raise OSError("injected permission preparation failure")
        real_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", fail_chmod)
    ok, message = installer.install_skill(str(source), force=True)
    assert not ok, message
    assert "permission preparation failure" in message
    assert snapshot(dest) == before
    assert manifest_path.read_bytes() == manifest
    assert list(installer.get_skills_dir().iterdir()) == [dest]


def test_relative_metadata_is_validated_in_installed_context(tmp_path: Path) -> None:
    dest, _ = installed_pair(tmp_path)
    source = make_skill(tmp_path / "sources" / "demo", "2")
    make_skill(source.parent / "common", "2")
    common = installer.get_skills_dir() / "common"
    common.mkdir()
    (common / "SKILL.md").write_text("---\nname: demo\n---\n# Invalid\n")
    (source / "SKILL.md").unlink()
    (source / "SKILL.md").symlink_to("../common/SKILL.md")
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert "Missing required field: description" in message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest
    assert not list(installer.get_skills_dir().glob(".skill-*"))


@pytest.mark.parametrize("failure", ["publish-tree", "publish-manifest"])
def test_readonly_source_failure_cleans_tree_and_restores_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dest, source = installed_pair(tmp_path)
    source.chmod(0o555)
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes()
    real_rename = Path.rename
    real_replace = Path.replace

    def fail_rename(self: Path, target: Path) -> Path:
        if failure == "publish-tree" and target == dest and self.name != "old":
            raise OSError("injected tree publication failure")
        return real_rename(self, target)

    def fail_replace(self: Path, target: Path) -> Path:
        if failure == "publish-manifest" and target == manifest_path:
            raise OSError("injected manifest publication failure")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "rename", fail_rename)
    monkeypatch.setattr(Path, "replace", fail_replace)
    try:
        ok, message = installer.install_skill(str(source), force=True)
        assert not ok, message
        assert snapshot(dest) == before
        assert manifest_path.read_bytes() == manifest
        assert list(installer.get_skills_dir().iterdir()) == [dest]
    finally:
        source.chmod(0o755)
        for leftover in installer.get_skills_dir().glob(".skill-new-*"):
            leftover.chmod(0o700)


@pytest.mark.parametrize("force", [False, True])
def test_metadata_link_through_final_name_uses_new_tree(
    tmp_path: Path, force: bool
) -> None:
    if force:
        dest, _ = installed_pair(tmp_path)
    else:
        dest = installer.get_skills_dir() / "demo"
    source = make_skill(tmp_path / "demo", "2")
    (source / "SKILL.md").rename(source / "metadata.md")
    (source / "SKILL.md").symlink_to("../demo/metadata.md")
    ok, message = installer.install_skill(str(source), force=force)
    assert ok, message
    assert installer.get_manifest().skills["demo"].version == "2"
    assert (dest / "SKILL.md").readlink() == Path("../demo/metadata.md")
    assert (dest / "SKILL.md").read_text() == (source / "metadata.md").read_text()


def test_final_name_metadata_validation_does_not_read_old_bytes(tmp_path: Path) -> None:
    dest, _ = installed_pair(tmp_path)
    source = make_skill(tmp_path / "sources" / "demo", "2")
    (dest / "metadata.md").write_bytes((dest / "SKILL.md").read_bytes())
    (source / "SKILL.md").unlink()
    (source / "metadata.md").write_text("---\nname: demo\n---\n# Invalid\n")
    (source / "SKILL.md").symlink_to("../demo/metadata.md")
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert "Missing required field: description" in message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest


@pytest.mark.parametrize("target_kind", ["chain", "external-backlink", "absolute"])
def test_metadata_links_resolve_against_future_tree(
    tmp_path: Path, target_kind: str
) -> None:
    dest, _ = installed_pair(tmp_path)
    source = make_skill(tmp_path / "sources" / "demo", "2")
    (source / "SKILL.md").rename(source / "metadata.md")
    if target_kind == "chain":
        (source / "inner").symlink_to("../demo/metadata.md")
        (source / "SKILL.md").symlink_to("inner")
    elif target_kind == "external-backlink":
        common = installer.get_skills_dir() / "common"
        common.mkdir()
        (common / "SKILL.md").symlink_to("../demo/metadata.md")
        source_common = source.parent / "common"
        source_common.mkdir()
        (source_common / "SKILL.md").symlink_to("../demo/metadata.md")
        (source / "SKILL.md").symlink_to("../common/SKILL.md")
    else:
        (dest / "metadata.md").write_bytes((source / "metadata.md").read_bytes())
        (source / "SKILL.md").symlink_to(dest / "metadata.md")

    ok, message = installer.install_skill(str(source), force=True)

    assert ok, message
    assert installer.get_manifest().skills["demo"].version == "2"
    assert (dest / "SKILL.md").read_bytes() == (source / "metadata.md").read_bytes()
    assert not list(installer.get_skills_dir().glob(".skill-*"))


@pytest.mark.parametrize("component", ["missing", "file"])
def test_metadata_parent_traversal_requires_real_directory(
    tmp_path: Path, component: str
) -> None:
    dest, _ = installed_pair(tmp_path)
    source = make_skill(tmp_path / "sources" / "demo", "2")
    source_common = make_skill(source.parent / "common", "2")
    (source_common / "subdir").mkdir()
    common = installer.get_skills_dir() / "common"
    common.mkdir()
    shutil.copy2(source_common / "SKILL.md", common / "SKILL.md")
    if component == "file":
        (common / "subdir").write_text("not a directory")
    (source / "SKILL.md").unlink()
    (source / "SKILL.md").symlink_to("../common/subdir/../SKILL.md")
    before = snapshot(dest)
    manifest = installer.get_manifest_path().read_bytes()

    ok, message = installer.install_skill(str(source), force=True)

    assert not ok, message
    assert snapshot(dest) == before
    assert installer.get_manifest_path().read_bytes() == manifest
    assert not list(installer.get_skills_dir().glob(".skill-*"))


def test_explicit_name_allows_metadata_link_only_valid_at_destination(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "sources" / "demo", "2")
    common = make_skill(installer.get_skills_dir() / "common", "2")
    (source / "SKILL.md").unlink()
    (source / "SKILL.md").symlink_to("../common/SKILL.md")
    ok, message = installer.install_skill(str(source), name="demo")
    assert ok, message
    dest = installer.get_skills_dir() / "demo"
    assert (dest / "SKILL.md").read_bytes() == (common / "SKILL.md").read_bytes()
    assert installer.get_manifest().skills["demo"].version == "2"


def test_missing_source_metadata_returns_normal_error(tmp_path: Path) -> None:
    source = make_skill(tmp_path / "source")
    (source / "SKILL.md").unlink()
    (source / "SKILL.md").symlink_to("missing.md")
    ok, message = installer.install_skill(str(source))
    assert not ok
    assert "Failed to read skill metadata" in message


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("suffix", ["/", "/."])
@pytest.mark.parametrize("link_kind", ["relative", "absolute", "chain"])
def test_metadata_link_directory_suffix_preserves_prior_state(
    tmp_path: Path, force: bool, suffix: str, link_kind: str
) -> None:
    if force:
        dest, _ = installed_pair(tmp_path)
    else:
        dest = installer.get_skills_dir() / "demo"
    source = make_skill(tmp_path / "source", "2")
    (source / "SKILL.md").rename(source / "metadata.md")
    target = str(dest / "metadata.md") if link_kind == "absolute" else "metadata.md"
    if link_kind == "chain":
        (source / "inner").symlink_to(target + suffix)
        (source / "SKILL.md").symlink_to("inner")
    else:
        (source / "SKILL.md").symlink_to(target + suffix)
    before = snapshot(dest)
    manifest_path = installer.get_manifest_path()
    manifest = manifest_path.read_bytes() if manifest_path.exists() else None

    ok, message = installer.install_skill(str(source), name="demo", force=force)

    assert not ok, message
    assert dest.exists() == force
    assert snapshot(dest) == before
    assert (manifest_path.read_bytes() if manifest_path.exists() else None) == manifest
    assert not list(installer.get_skills_dir().glob(".skill-*"))


@pytest.mark.parametrize("directory_target", ["metadata/", "metadata/."])
def test_metadata_link_directory_suffix_accepts_actual_directory(
    tmp_path: Path, directory_target: str
) -> None:
    source = make_skill(tmp_path / "source", "2")
    (source / "metadata").mkdir()
    (source / "SKILL.md").rename(source / "metadata" / "content.md")
    (source / "inner").symlink_to(directory_target, target_is_directory=True)
    (source / "SKILL.md").symlink_to("inner/content.md")

    ok, message = installer.install_skill(str(source), name="demo")

    assert ok, message
    dest = installer.get_skills_dir() / "demo"
    assert (dest / "SKILL.md").read_bytes() == (
        source / "metadata" / "content.md"
    ).read_bytes()
    assert installer.get_manifest().skills["demo"].version == "2"
