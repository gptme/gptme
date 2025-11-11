"""Tests for file context selector integration."""

from datetime import datetime
from pathlib import Path

import pytest

from gptme.context_selector import FileItem, FileSelectorConfig, select_relevant_files
from gptme.message import Message


@pytest.fixture
def temp_files(tmp_path: Path) -> dict[str, Path]:
    """Create temporary test files."""
    files = {
        "recent_py": tmp_path / "recent.py",
        "old_py": tmp_path / "old.py",
        "config": tmp_path / "config.toml",
        "readme": tmp_path / "README.md",
    }

    # Create files with different content and mtimes
    for name, path in files.items():
        content = f"# {name}\nContent for {name}"
        path.write_text(content)

    # Set different modification times (simulate recency)
    import os

    now = datetime.now().timestamp()
    files["recent_py"].touch()  # Most recent
    os.utime(files["old_py"], times=(now - 86400, now - 86400))  # 1 day old
    os.utime(files["config"], times=(now - 3600, now - 3600))  # 1 hour old
    os.utime(files["readme"], times=(now - 604800, now - 604800))  # 1 week old

    return files


class TestFileItem:
    """Test FileItem wrapper."""

    def test_file_item_creation(self, tmp_path: Path):
        """Test creating a FileItem."""
        f = tmp_path / "test.py"
        f.write_text("print('hello')")

        item = FileItem(f, mention_count=3, mtime=f.stat().st_mtime)

        assert item.path == f
        assert item.mention_count == 3
        assert item.identifier == str(f)

    def test_file_item_content(self, tmp_path: Path):
        """Test FileItem content property."""
        f = tmp_path / "test.py"
        f.write_text("print('hello')")

        item = FileItem(f, mention_count=1)
        assert "print('hello')" in item.content

    def test_file_item_content_large_file(self, tmp_path: Path):
        """Test FileItem content truncation for large files."""
        f = tmp_path / "large.py"
        f.write_text("x" * 3000)  # Large file

        item = FileItem(f, mention_count=1)
        content = item.content

        assert len(content) < 2000  # Truncated
        assert "[File size:" in content  # Has size metadata

    def test_file_item_metadata(self, tmp_path: Path):
        """Test FileItem metadata."""
        f = tmp_path / "test.py"
        f.write_text("content")
        mtime = f.stat().st_mtime

        item = FileItem(f, mention_count=5, mtime=mtime)
        metadata = item.metadata

        assert metadata["mention_count"] == 5
        assert metadata["mtime"] == mtime
        assert metadata["file_type"] == "py"
        assert metadata["file_size"] > 0


class TestFileSelectorConfig:
    """Test FileSelectorConfig boost calculations."""

    def test_mention_boost(self):
        """Test mention count boost calculation."""
        config = FileSelectorConfig()

        assert config.get_mention_boost(10) == 3.0  # 10+ mentions
        assert config.get_mention_boost(5) == 2.0  # 5-9 mentions
        assert config.get_mention_boost(2) == 1.5  # 2-4 mentions
        assert config.get_mention_boost(1) == 1.0  # 1 mention

    def test_recency_boost(self):
        """Test recency boost calculation."""
        config = FileSelectorConfig()

        assert config.get_recency_boost(0.5) == 1.3  # Last hour
        assert config.get_recency_boost(12.0) == 1.1  # Today
        assert config.get_recency_boost(100.0) == 1.05  # This week
        assert config.get_recency_boost(200.0) == 1.0  # Older

    def test_file_type_weight(self):
        """Test file type weight calculation."""
        config = FileSelectorConfig()

        assert config.get_file_type_weight("py") == 1.3
        assert config.get_file_type_weight("md") == 1.2
        assert config.get_file_type_weight("unknown") == 1.0

    def test_custom_boosts(self):
        """Test custom boost configuration."""
        config = FileSelectorConfig(
            mention_boost_thresholds={20: 5.0, 10: 3.0},
            file_type_weights={"rs": 1.5, "py": 1.3},
        )

        assert config.get_mention_boost(20) == 5.0
        assert config.get_file_type_weight("rs") == 1.5


class TestSelectRelevantFiles:
    """Test select_relevant_files function."""

    @pytest.mark.asyncio
    async def test_rule_based_selection(self, temp_files: dict[str, Path]):
        """Test rule-based file selection."""
        # Create messages mentioning files multiple times
        msgs = [
            Message("user", "Look at recent.py", files=[temp_files["recent_py"]]),
            Message("user", "Also check config.toml", files=[temp_files["config"]]),
            Message("user", "And recent.py again", files=[temp_files["recent_py"]]),
        ]

        config = FileSelectorConfig(strategy="rule")
        selected = await select_relevant_files(
            msgs,
            workspace=temp_files["recent_py"].parent,
            max_files=2,
            use_selector=True,
            config=config,
        )

        # recent.py should be first (2 mentions + recent)
        assert len(selected) <= 2
        assert temp_files["recent_py"] in selected

    @pytest.mark.asyncio
    async def test_fallback_without_selector(self, temp_files: dict[str, Path]):
        """Test fallback to simple sorting when selector disabled."""
        msgs = [
            Message("user", "Look at old.py", files=[temp_files["old_py"]]),
            Message("user", "And README.md", files=[temp_files["readme"]]),
        ]

        selected = await select_relevant_files(
            msgs,
            workspace=temp_files["old_py"].parent,
            max_files=2,
            use_selector=False,  # Disabled
        )

        assert len(selected) <= 2

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Test with no messages."""
        selected = await select_relevant_files(
            [],
            workspace=None,
            max_files=10,
            use_selector=True,
        )

        assert selected == []

    @pytest.mark.asyncio
    async def test_no_files_mentioned(self):
        """Test with messages but no files."""
        msgs = [
            Message("user", "Hello"),
            Message("assistant", "Hi there"),
        ]

        selected = await select_relevant_files(
            msgs,
            workspace=None,
            max_files=10,
            use_selector=True,
        )

        assert selected == []

    @pytest.mark.asyncio
    async def test_mention_count_boost(self, temp_files: dict[str, Path]):
        """Test that frequently mentioned files rank higher."""
        msgs = [
            Message("user", "Check old.py", files=[temp_files["old_py"]]),
            Message("user", "Also old.py", files=[temp_files["old_py"]]),
            Message("user", "And old.py again", files=[temp_files["old_py"]]),
            Message("user", "Look at recent.py", files=[temp_files["recent_py"]]),
        ]

        config = FileSelectorConfig(strategy="rule")
        selected = await select_relevant_files(
            msgs,
            workspace=temp_files["old_py"].parent,
            max_files=1,
            use_selector=True,
            config=config,
        )

        # old.py should rank first despite being older (3 mentions vs 1)
        assert selected[0] == temp_files["old_py"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
