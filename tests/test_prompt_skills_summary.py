"""Tests for skills summary in prompts."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.prompts import prompt_skills_summary


@pytest.fixture(autouse=True)
def isolate_lesson_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "gptme.lessons.index._trusted_cache_base", str(tmp_path / "cache")
    )


def test_prompt_skills_summary_no_skills():
    """Test skills summary with no skills available."""
    msgs = list(prompt_skills_summary())
    # May be empty if no skills found
    assert isinstance(msgs, list)


def test_prompt_skills_summary_format(tmp_path: Path, monkeypatch):
    """Test skills summary generates correct format."""
    # Create a mock skill file
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("""---
name: test-skill
description: A test skill for testing
---

# Test Skill

This is a test skill.
""")

    # Mock LessonIndex to use our test directory
    from gptme.lessons.index import LessonIndex

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))

    # Also clear cache
    from gptme.lessons.index import clear_cache

    clear_cache()

    msgs = list(prompt_skills_summary())

    if msgs:
        content = msgs[0].content
        assert "Available Skills" in content
        assert "test-skill" in content
        assert "A test skill for testing" in content
        assert str(skill_file) in content or "SKILL.md" in content


def test_prompt_skills_summary_truncates_description(tmp_path: Path, monkeypatch):
    """Test that long descriptions are truncated."""
    # Create a mock skill file with long description
    skill_dir = tmp_path / "skills" / "long-desc"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    long_desc = "A" * 200  # Very long description
    skill_file.write_text(f"""---
name: long-desc
description: {long_desc}
---

# Long Description Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary())

    if msgs:
        content = msgs[0].content
        # Description should be truncated to 80 chars with "..."
        assert "..." in content
        assert long_desc not in content  # Full description should not appear


def test_prompt_skills_summary_xml_escaping(tmp_path: Path, monkeypatch):
    """Test that skill descriptions with special XML characters are properly escaped and not split mid-entity."""
    skill_dir = tmp_path / "skills" / "xml-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    # Description with XML special chars and length near 77 chars to trigger mid-entity truncation bug
    # "&amp;" expands to 5 chars; if truncated post-escape, "&am" etc. could appear
    desc_with_specials = (
        "A skill using C++ & Python <template> for parsing things nicely"
    )
    skill_file.write_text(f"""---
name: xml-skill
description: {desc_with_specials}
---

# XML Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary(tool_format="xml"))

    if msgs:
        content = msgs[0].content
        # Should contain escaped versions, not raw special chars inside XML attributes/tags
        assert (
            "&amp;" in content
            or "&" not in content.split("xml-skill")[1].split("</skill>")[0]
        )
        assert "&lt;" in content or "<template>" not in content
        # Must not have bare "&" or "<" inside element text (malformed XML)
        import xml.etree.ElementTree as ET

        ET.fromstring(content)  # Would raise if malformed


def test_prompt_skills_summary_xml_truncate_before_escape(tmp_path: Path, monkeypatch):
    """Test that truncation happens before XML escaping to prevent mid-entity cuts."""
    skill_dir = tmp_path / "skills" / "trunc-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    # Place '&' at position 76 so truncation-then-escape never splits "&amp;"
    # but escape-then-truncation would produce "&am" if & fell at pos 75-77
    desc = "A" * 74 + "& rest of description that is long enough to trigger truncation"
    skill_file.write_text(f"""---
name: trunc-skill
description: {desc}
---

# Trunc Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary(tool_format="xml"))

    if msgs:
        content = msgs[0].content
        # Must not contain a partial XML entity like "&am" or "&l"
        assert "&am" not in content or "&amp;" in content
        assert "&lt" not in content or "&lt;" in content
        # Must be valid XML
        import xml.etree.ElementTree as ET

        ET.fromstring(content)


def _write_skill(root: Path, word: str) -> Path:
    skill_file = root / "skills" / "secret-word" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(f"""---
name: secret-word
description: The secret word of this workspace
---

The secret word is {word}.
""")
    return skill_file


def _isolate_from_cwd_workspace(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Return workspace and cwd dirs, each with its own skill, cwd set to the latter."""
    from gptme.lessons.index import clear_cache

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("GPTME_LESSONS_EXTRA_DIRS", raising=False)
    workspace = tmp_path / "workspace"
    cwd = tmp_path / "cwd"
    _write_skill(workspace, "mango")
    _write_skill(cwd, "papaya")
    monkeypatch.chdir(cwd)
    clear_cache()
    return workspace, cwd


def test_prompt_skills_summary_uses_workspace_not_cwd(tmp_path: Path, monkeypatch):
    """Skills come from the given workspace even when the process cwd differs (#5113)."""
    workspace, cwd = _isolate_from_cwd_workspace(tmp_path, monkeypatch)

    msgs = list(prompt_skills_summary(workspace=workspace))

    content = "\n".join(m.content for m in msgs)
    assert str(workspace / "skills" / "secret-word" / "SKILL.md") in content
    assert str(cwd) not in content


def test_get_prompt_skills_summary_uses_workspace(tmp_path: Path, monkeypatch):
    """get_prompt passes its workspace through to the skills summary (#5113)."""
    from gptme.prompts import get_prompt
    from gptme.tools import get_toolchain

    workspace, cwd = _isolate_from_cwd_workspace(tmp_path, monkeypatch)

    msgs = get_prompt(
        list(get_toolchain(["shell"])),
        prompt="full",
        workspace=workspace,
        include_user_context=False,
    )

    content = "\n".join(m.content for m in msgs)
    assert str(workspace / "skills" / "secret-word" / "SKILL.md") in content
    assert str(cwd / "skills") not in content


def test_get_prompt_project_uses_workspace(tmp_path: Path, monkeypatch):
    """The project section names the workspace repo, not the cwd repo (#5113)."""
    import subprocess

    from gptme.config import reload_config
    from gptme.prompts import get_prompt
    from gptme.tools import get_toolchain

    workspace, cwd = _isolate_from_cwd_workspace(tmp_path, monkeypatch)
    for repo, word in ((workspace, "WORKSPACE-RULES"), (cwd, "CWD-RULES")):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "gptme.toml").write_text(f'prompt = "{word}"\n')
    reload_config()

    msgs = get_prompt(
        list(get_toolchain(["shell"])),
        prompt="full",
        workspace=workspace,
        include_user_context=False,
    )

    content = "\n".join(m.content for m in msgs)
    assert f"## Current Project: {workspace.name}" in content
    assert "WORKSPACE-RULES" in content
    assert "CWD-RULES" not in content


@pytest.mark.parametrize("cwd_source", ["shell", "promoted"])
def test_explicit_prompt_workspace_overrides_shell(
    tmp_path: Path, monkeypatch, cwd_source
):
    from gptme.util.workspace import _promoted_next_cwd, _shell_var, _workspace_cwd

    workspace, cwd = _isolate_from_cwd_workspace(tmp_path, monkeypatch)
    workspace_token = _workspace_cwd.set(str(workspace))
    shell_token = _shell_var.set(Mock(_cwd=str(cwd)) if cwd_source == "shell" else None)
    promoted_token = _promoted_next_cwd.set(str(cwd))
    try:
        content = "\n".join(
            m.content for m in prompt_skills_summary(workspace=workspace)
        )
        assert str(workspace / "skills" / "secret-word" / "SKILL.md") in content
        assert str(cwd / "skills") not in content
    finally:
        _promoted_next_cwd.reset(promoted_token)
        _shell_var.reset(shell_token)
        _workspace_cwd.reset(workspace_token)


def test_workspace_scope_restores_shell_and_nested_scope(tmp_path: Path):
    from gptme.util.workspace import (
        _promoted_next_cwd,
        _shell_var,
        _workspace_cwd,
        current_workspace,
        workspace_scope,
    )

    shell_cwd = tmp_path / "shell"
    outer = tmp_path / "outer"
    inner = tmp_path / "inner"
    tokens = (
        _workspace_cwd.set(str(tmp_path)),
        _shell_var.set(Mock(_cwd=str(shell_cwd))),
        _promoted_next_cwd.set(str(tmp_path / "promoted")),
    )
    try:
        assert current_workspace() == shell_cwd
        with workspace_scope(outer):
            assert current_workspace() == outer
            with (
                pytest.raises(ValueError, match="restore on error"),
                workspace_scope(inner),
            ):
                assert current_workspace() == inner
                raise ValueError("restore on error")
            with workspace_scope(None):
                assert current_workspace() == outer
        assert current_workspace() == shell_cwd
    finally:
        _promoted_next_cwd.reset(tokens[2])
        _shell_var.reset(tokens[1])
        _workspace_cwd.reset(tokens[0])


def test_prompt_full_workspace_and_no_scope_leak(tmp_path: Path, monkeypatch):
    from gptme.prompts import prompt_full
    from gptme.util.workspace import current_workspace

    workspace, cwd = _isolate_from_cwd_workspace(tmp_path, monkeypatch)
    for repo, word in ((workspace, "WORKSPACE-BASE"), (cwd, "CWD-BASE")):
        (repo / ".git").mkdir()
        (repo / "gptme.toml").write_text(
            f'base_prompt = "{word}"\nprompt = "{word}-PROJECT"\n'
        )
    before = current_workspace()
    content = []
    for msg in prompt_full(False, [], "markdown", None, workspace=workspace):
        assert current_workspace() == before
        content.append(msg.content)
    text = "\n".join(content)
    assert "WORKSPACE-BASE" in text
    assert "CWD-BASE" not in text
    assert f"## Current Project: {workspace.name}" in text


def test_relative_workspace_git_walk(tmp_path: Path, monkeypatch):
    from gptme.dirs import get_project_git_dir
    from gptme.util.workspace import _workspace_cwd

    (tmp_path / ".git").mkdir()
    (tmp_path / "src" / "logs").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "src")
    token = _workspace_cwd.set("logs")
    try:
        assert get_project_git_dir() == tmp_path
    finally:
        _workspace_cwd.reset(token)


def test_gptme_dir_git_fallback_stays_with_launch_cwd(tmp_path: Path, monkeypatch):
    from gptme.config import get_project_config
    from gptme.dirs import get_project_gptme_dir
    from gptme.util.workspace import workspace_scope

    launch = tmp_path / "launch"
    (launch / ".git").mkdir(parents=True)
    (launch / ".github").mkdir()
    (launch / ".github" / "gptme.toml").write_text("[rag]\nenabled = true\n")
    (launch / "src").mkdir()
    workspace = tmp_path / "conversation"
    workspace.mkdir()
    monkeypatch.chdir(launch / "src")
    with workspace_scope(workspace):
        project_dir = get_project_gptme_dir()
        assert project_dir == launch
        config = get_project_config(project_dir)
        assert config is not None and config.rag.enabled
