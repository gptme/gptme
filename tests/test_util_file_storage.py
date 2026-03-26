"""Tests for gptme.util.file_storage module.

Tests the content-addressable file storage system used for
preserving file versions in conversation history.
"""

from pathlib import Path

import pytest

from gptme.util.file_storage import (
    compute_file_hash,
    get_files_dir,
    get_stored_path,
    read_stored_content,
    store_file,
)


@pytest.fixture
def logdir(tmp_path: Path) -> Path:
    """Create a temporary conversation log directory."""
    return tmp_path / "conversation-log"


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    """Create a sample file for testing."""
    f = tmp_path / "sample.py"
    f.write_text("print('hello world')\n")
    return f


@pytest.fixture
def binary_file(tmp_path: Path) -> Path:
    """Create a binary file for testing."""
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
    return f


class TestComputeFileHash:
    """Tests for compute_file_hash — content hashing."""

    def test_basic_hash(self, sample_file: Path):
        """Hash returns a 16-char hex string."""
        h = compute_file_hash(sample_file)
        assert isinstance(h, str)
        assert len(h) == 16
        # Should be valid hex
        int(h, 16)

    def test_deterministic(self, sample_file: Path):
        """Same content produces same hash."""
        h1 = compute_file_hash(sample_file)
        h2 = compute_file_hash(sample_file)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert compute_file_hash(f1) != compute_file_hash(f2)

    def test_same_content_same_hash(self, tmp_path: Path):
        """Files with identical content get identical hashes."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        content = "identical content"
        f1.write_text(content)
        f2.write_text(content)
        assert compute_file_hash(f1) == compute_file_hash(f2)

    def test_binary_file(self, binary_file: Path):
        h = compute_file_hash(binary_file)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_empty_file(self, tmp_path: Path):
        """Empty files get a valid hash."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        h = compute_file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_large_file(self, tmp_path: Path):
        """Large files are handled via chunk reading."""
        f = tmp_path / "large.bin"
        # Write >8192 bytes to exercise chunked reading
        f.write_bytes(b"x" * 20000)
        h = compute_file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_custom_algorithm(self, sample_file: Path):
        """Non-default hash algorithm works."""
        h_sha256 = compute_file_hash(sample_file, algorithm="sha256")
        h_md5 = compute_file_hash(sample_file, algorithm="md5")
        # Different algorithms should produce different hashes
        assert h_sha256 != h_md5


class TestGetFilesDir:
    """Tests for get_files_dir — files directory management."""

    def test_creates_directory(self, logdir: Path):
        files_dir = get_files_dir(logdir)
        assert files_dir.exists()
        assert files_dir.is_dir()
        assert files_dir == logdir / "files"

    def test_creates_parent_dirs(self, tmp_path: Path):
        """Creates nested parent directories if needed."""
        deep = tmp_path / "a" / "b" / "c"
        files_dir = get_files_dir(deep)
        assert files_dir.exists()
        assert files_dir == deep / "files"

    def test_idempotent(self, logdir: Path):
        """Calling twice returns the same path without error."""
        d1 = get_files_dir(logdir)
        d2 = get_files_dir(logdir)
        assert d1 == d2


class TestStoreFile:
    """Tests for store_file — content-addressable storage."""

    def test_basic_store(self, logdir: Path, sample_file: Path):
        file_hash, stored_name = store_file(logdir, sample_file)
        assert isinstance(file_hash, str)
        assert len(file_hash) == 16
        assert stored_name.endswith(".py")
        assert stored_name.startswith(file_hash)

    def test_stored_file_exists(self, logdir: Path, sample_file: Path):
        file_hash, stored_name = store_file(logdir, sample_file)
        stored_path = logdir / "files" / stored_name
        assert stored_path.exists()

    def test_stored_content_matches(self, logdir: Path, sample_file: Path):
        """Stored file has the same content as the original."""
        file_hash, stored_name = store_file(logdir, sample_file)
        stored_path = logdir / "files" / stored_name
        assert stored_path.read_text() == sample_file.read_text()

    def test_idempotent_storage(self, logdir: Path, sample_file: Path):
        """Storing the same file twice doesn't duplicate."""
        h1, n1 = store_file(logdir, sample_file)
        h2, n2 = store_file(logdir, sample_file)
        assert h1 == h2
        assert n1 == n2
        # Only one file should exist
        files = list((logdir / "files").iterdir())
        assert len(files) == 1

    def test_preserves_extension(self, tmp_path: Path, logdir: Path):
        """File extension is preserved in stored name."""
        for ext in [".py", ".txt", ".json", ".md"]:
            f = tmp_path / f"test{ext}"
            f.write_text(f"content for {ext}")
            _, stored_name = store_file(logdir, f)
            assert stored_name.endswith(ext)

    def test_no_extension(self, tmp_path: Path, logdir: Path):
        """Files without extension are stored without suffix."""
        f = tmp_path / "Makefile"
        f.write_text("all:\n\techo hello\n")
        file_hash, stored_name = store_file(logdir, f)
        assert stored_name == file_hash  # No extension appended

    def test_binary_file(self, logdir: Path, binary_file: Path):
        file_hash, stored_name = store_file(logdir, binary_file)
        stored_path = logdir / "files" / stored_name
        assert stored_path.read_bytes() == binary_file.read_bytes()

    def test_different_files_stored_separately(self, tmp_path: Path, logdir: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        h1, n1 = store_file(logdir, f1)
        h2, n2 = store_file(logdir, f2)
        assert h1 != h2
        assert n1 != n2
        files = list((logdir / "files").iterdir())
        assert len(files) == 2


class TestGetStoredPath:
    """Tests for get_stored_path — retrieving stored files."""

    def test_find_stored_file(self, logdir: Path, sample_file: Path):
        file_hash, stored_name = store_file(logdir, sample_file)
        result = get_stored_path(logdir, file_hash, suffix=".py")
        assert result is not None
        assert result.exists()

    def test_not_found_returns_none(self, logdir: Path):
        logdir.mkdir(parents=True, exist_ok=True)
        result = get_stored_path(logdir, "nonexistent_hash", suffix=".py")
        assert result is None

    def test_find_without_suffix(self, tmp_path: Path):
        """Can find files stored without extension."""
        logdir = tmp_path / "log"
        f = tmp_path / "Makefile"
        f.write_text("all: build\n")
        file_hash, _ = store_file(logdir, f)
        # Find without specifying suffix
        result = get_stored_path(logdir, file_hash)
        assert result is not None

    def test_find_with_wrong_suffix(self, logdir: Path, sample_file: Path):
        """Wrong suffix doesn't find the file."""
        file_hash, _ = store_file(logdir, sample_file)
        result = get_stored_path(logdir, file_hash, suffix=".txt")
        assert result is None

    def test_fallback_to_no_extension(self, tmp_path: Path):
        """When suffix doesn't match, falls back to extensionless lookup."""
        logdir = tmp_path / "log"
        f = tmp_path / "data"  # No extension
        f.write_text("some data")
        file_hash, _ = store_file(logdir, f)
        # Search with a suffix — should fall back to no-extension match
        result = get_stored_path(logdir, file_hash, suffix=".txt")
        assert result is not None
        assert result.name == file_hash

    def test_no_files_dir(self, tmp_path: Path):
        """Returns None when files/ directory doesn't exist."""
        logdir = tmp_path / "empty-log"
        logdir.mkdir()
        result = get_stored_path(logdir, "somehash")
        assert result is None


class TestReadStoredContent:
    """Tests for read_stored_content — reading stored file content."""

    def test_read_text_file(self, logdir: Path, sample_file: Path):
        file_hash, _ = store_file(logdir, sample_file)
        content = read_stored_content(logdir, file_hash, suffix=".py")
        assert content is not None
        assert content == "print('hello world')\n"

    def test_not_found_returns_none(self, logdir: Path):
        logdir.mkdir(parents=True, exist_ok=True)
        result = read_stored_content(logdir, "nonexistent")
        assert result is None

    def test_binary_file_decode_fails(self, logdir: Path, binary_file: Path):
        """Binary files with invalid UTF-8 sequences return None."""
        # Use bytes that are invalid UTF-8 (and invalid in most encodings)
        invalid_utf8 = logdir.parent / "invalid.bin"
        invalid_utf8.write_bytes(b"\xc3\x28\xfe\xff")  # Invalid UTF-8 sequences
        file_hash, _ = store_file(logdir, invalid_utf8)
        result = read_stored_content(logdir, file_hash, suffix=".bin")
        assert result is None  # UnicodeDecodeError handled

    def test_empty_file_content(self, tmp_path: Path):
        logdir = tmp_path / "log"
        f = tmp_path / "empty.txt"
        f.write_text("")
        file_hash, _ = store_file(logdir, f)
        content = read_stored_content(logdir, file_hash, suffix=".txt")
        assert content is not None
        assert content == ""

    def test_multiline_content(self, tmp_path: Path):
        logdir = tmp_path / "log"
        f = tmp_path / "multi.txt"
        text = "line 1\nline 2\nline 3\n"
        f.write_text(text)
        file_hash, _ = store_file(logdir, f)
        content = read_stored_content(logdir, file_hash, suffix=".txt")
        assert content == text

    def test_unicode_content(self, tmp_path: Path):
        logdir = tmp_path / "log"
        f = tmp_path / "unicode.txt"
        text = "日本語テスト\ncafé résumé\n"
        f.write_text(text, encoding="utf-8")
        file_hash, _ = store_file(logdir, f)
        content = read_stored_content(logdir, file_hash, suffix=".txt")
        assert content is not None
        assert "日本語" in content
