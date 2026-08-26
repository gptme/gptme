"""Tests for the gptme memory write tool (cross-runtime memory store)."""

from pathlib import Path

from gptme.dirs import get_cc_memory_dir, get_cc_memory_file
from gptme.tools.memory import (
    _slugify,
    _update_memory_index,
    execute_memory,
    save_memory,
    tool,
)


class TestSlugify:
    def test_basic(self):
        assert _slugify("hello world") == "hello-world"

    def test_already_slug(self):
        assert _slugify("my-memory") == "my-memory"

    def test_uppercase(self):
        assert _slugify("My Memory Name") == "my-memory-name"

    def test_special_chars(self):
        assert _slugify("prefer short answers!") == "prefer-short-answers"

    def test_empty_falls_back(self):
        assert _slugify("") == "memory"

    def test_leading_trailing_dashes_stripped(self):
        assert _slugify("  --hello--  ") == "hello"


class TestUpdateMemoryIndex:
    def test_creates_index_when_missing(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        _update_memory_index(memory_dir, "test", "test.md", "A test memory")
        index = (memory_dir / "MEMORY.md").read_text()
        assert "- [test](test.md) — A test memory" in index

    def test_appends_new_entry(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n- [old](old.md) — Old\n"
        )
        _update_memory_index(memory_dir, "new", "new.md", "New memory")
        index = (memory_dir / "MEMORY.md").read_text()
        assert "- [old](old.md)" in index
        assert "- [new](new.md) — New memory" in index

    def test_updates_existing_entry(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n- [test](test.md) — Old description\n"
        )
        _update_memory_index(memory_dir, "test", "test.md", "Updated description")
        index = (memory_dir / "MEMORY.md").read_text()
        assert "Updated description" in index
        assert "Old description" not in index
        # Should not duplicate
        assert index.count("test.md") == 1


class TestSaveMemory:
    def test_creates_memory_file(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("my-fact", "This is a fact about the project.", workspace=workspace)
        memory_dir = get_cc_memory_dir(workspace)
        assert (memory_dir / "my-fact.md").exists()

    def test_memory_file_has_frontmatter(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory(
            "my-fact", "Short description.\nBody text here.", workspace=workspace
        )
        memory_dir = get_cc_memory_dir(workspace)
        content = (memory_dir / "my-fact.md").read_text()
        assert "---" in content
        assert "name: my-fact" in content
        assert "description:" in content
        assert "type: general" in content

    def test_first_line_is_description(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory(
            "pref",
            "User prefers short answers.\nMore detail here.",
            workspace=workspace,
        )
        memory_dir = get_cc_memory_dir(workspace)
        content = (memory_dir / "pref.md").read_text()
        assert "description: User prefers short answers." in content
        assert "More detail here." in content

    def test_single_line_content(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("one-liner", "Just this one line.", workspace=workspace)
        memory_dir = get_cc_memory_dir(workspace)
        content = (memory_dir / "one-liner.md").read_text()
        assert "Just this one line." in content

    def test_updates_memory_index(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("fact-a", "First fact.", workspace=workspace)
        save_memory("fact-b", "Second fact.", workspace=workspace)
        memory_dir = get_cc_memory_dir(workspace)
        index = (memory_dir / "MEMORY.md").read_text()
        assert "fact-a" in index
        assert "fact-b" in index

    def test_overwrite_existing_memory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("my-fact", "First version.", workspace=workspace)
        save_memory("my-fact", "Second version.", workspace=workspace)
        memory_dir = get_cc_memory_dir(workspace)
        content = (memory_dir / "my-fact.md").read_text()
        assert "Second version." in content
        assert "First version." not in content

    def test_returns_file_path(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        path = save_memory("fact", "Some content.", workspace=workspace)
        assert path.endswith("fact.md")
        assert Path(path).exists()

    def test_name_slugified_in_filename(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("My Cool Fact", "Content.", workspace=workspace)
        memory_dir = get_cc_memory_dir(workspace)
        assert (memory_dir / "my-cool-fact.md").exists()

    def test_memory_dir_created_if_missing(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        memory_dir = get_cc_memory_dir(workspace)
        assert not memory_dir.exists()
        save_memory("test", "Content.", workspace=workspace)
        assert memory_dir.exists()

    def test_memory_readable_by_cc_memory_file_path(self, tmp_path):
        """Memory saved by gptme is at the path CC expects to read."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        save_memory("shared-fact", "Shared fact content.", workspace=workspace)
        # The CC memory file should now exist (gptme already reads this in prompt_workspace)
        cc_file = get_cc_memory_file(workspace)
        assert cc_file.exists(), "MEMORY.md index not created at CC memory path"
        index_content = cc_file.read_text()
        assert "shared-fact" in index_content


class TestExecuteMemory:
    def test_yields_success_message(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Patch get_workspace to return our tmp workspace
        import gptme.tools.memory as mem_module

        monkeypatch.setattr(mem_module, "get_workspace", lambda: workspace)

        messages = list(execute_memory("Test content.", ["test-mem"], None))
        assert len(messages) == 1
        assert "test-mem" in messages[0].content
        assert "saved" in messages[0].content.lower()

    def test_error_on_no_args(self):
        messages = list(execute_memory("content", [], None))
        assert len(messages) == 1
        assert "error" in messages[0].content.lower()

    def test_error_on_empty_content(self):
        messages = list(execute_memory("", ["name"], None))
        assert len(messages) == 1
        assert "empty" in messages[0].content.lower()

    def test_error_on_none_content(self):
        messages = list(execute_memory(None, ["name"], None))
        assert len(messages) == 1
        assert "empty" in messages[0].content.lower()


class TestToolSpec:
    def test_tool_name(self):
        assert tool.name == "memory"

    def test_tool_block_types(self):
        assert "memory" in tool.block_types

    def test_tool_has_instructions(self):
        assert len(tool.instructions) > 0

    def test_tool_has_execute(self):
        assert tool.execute is not None
