"""Tests for URI support in Message.files."""

from pathlib import Path

import pytest

from gptme.message import Message
from gptme.util.uri import URI, is_uri, parse_file_reference


class TestURI:
    """Test URI class functionality."""

    def test_uri_creation(self):
        """Test URI object creation."""
        uri = URI("http://example.com/image.jpg")
        assert str(uri) == "http://example.com/image.jpg"
        assert uri.scheme == "http"
        assert uri.is_http is True
        assert uri.is_mcp is False

    def test_uri_https(self):
        """Test HTTPS URI."""
        uri = URI("https://example.com/doc.pdf")
        assert uri.scheme == "https"
        assert uri.is_http is True

    def test_uri_mcp_memo(self):
        """Test MCP memo:// URI."""
        uri = URI("memo://resource/123")
        assert uri.scheme == "memo"
        assert uri.is_mcp is True
        assert uri.is_http is False

    def test_uri_mcp_scheme(self):
        """Test MCP mcp:// URI."""
        uri = URI("mcp://server/resource")
        assert uri.scheme == "mcp"
        assert uri.is_mcp is True

    def test_uri_invalid(self):
        """Test that invalid URIs raise ValueError."""
        with pytest.raises(ValueError):
            URI("not-a-uri")
        with pytest.raises(ValueError):
            URI("/local/path")
        with pytest.raises(ValueError):
            URI("relative/path.txt")

    def test_uri_equality(self):
        """Test URI equality."""
        uri1 = URI("http://example.com")
        uri2 = URI("http://example.com")
        uri3 = URI("http://other.com")
        assert uri1 == uri2
        assert uri1 != uri3
        assert uri1 == "http://example.com"  # String comparison

    def test_uri_hash(self):
        """Test URI is hashable."""
        uri = URI("http://example.com")
        s = {uri}  # Can be added to set
        assert uri in s


class TestIsUri:
    """Test is_uri detection function."""

    def test_http_urls(self):
        """Test HTTP URL detection."""
        assert is_uri("http://example.com") is True
        assert is_uri("https://example.com/path") is True
        assert is_uri("http://localhost:8080") is True

    def test_mcp_uris(self):
        """Test MCP URI detection."""
        assert is_uri("memo://resource/123") is True
        assert is_uri("mcp://server/tool") is True

    def test_other_schemes(self):
        """Test other URI schemes."""
        assert is_uri("ftp://ftp.example.com") is True
        assert is_uri("ws://websocket.example.com") is True
        assert is_uri("data://base64,abc123") is True

    def test_paths_not_uris(self):
        """Test that paths are not detected as URIs."""
        assert is_uri("/home/user/file.txt") is False
        assert is_uri("relative/path.txt") is False
        assert is_uri("file.txt") is False
        assert is_uri("./local") is False
        assert is_uri("../parent") is False

    def test_path_objects(self):
        """Test that Path objects are not URIs."""
        assert is_uri(Path("/home/user")) is False
        assert is_uri(Path("relative")) is False


class TestParseFileReference:
    """Test parse_file_reference function."""

    def test_parse_uri(self):
        """Test parsing URIs."""
        ref = parse_file_reference("http://example.com")
        assert isinstance(ref, URI)
        assert str(ref) == "http://example.com"

    def test_parse_path(self):
        """Test parsing paths."""
        ref = parse_file_reference("/home/user/file.txt")
        assert isinstance(ref, Path)
        assert str(ref) == "/home/user/file.txt"

    def test_parse_relative_path(self):
        """Test parsing relative paths."""
        ref = parse_file_reference("relative/path.txt")
        assert isinstance(ref, Path)


class TestMessageWithURI:
    """Test Message class with URI support."""

    def test_message_with_uri_files(self):
        """Test Message can hold both Paths and URIs."""
        msg = Message(
            role="user",
            content="Check this",
            files=[Path("/tmp/local.txt"), URI("http://example.com/image.jpg")],
        )
        assert len(msg.files) == 2
        assert isinstance(msg.files[0], Path)
        assert isinstance(msg.files[1], URI)

    def test_message_to_dict_with_uris(self):
        """Test Message.to_dict() serializes URIs correctly."""
        msg = Message(
            role="user",
            content="Test",
            files=[Path("/tmp/file.txt"), URI("http://example.com")],
        )
        d = msg.to_dict()
        assert "files" in d
        # Paths are resolved, so we need to compare with the resolved path
        assert d["files"] == [str(Path("/tmp/file.txt").resolve()), "http://example.com"]

    def test_message_toml_roundtrip(self):
        """Test TOML serialization roundtrip preserves URIs."""
        original = Message(
            role="user",
            content="Test message",
            files=[Path("/tmp/local.txt"), URI("https://example.com/doc.pdf")],
        )
        toml_str = original.to_toml()
        restored = Message.from_toml(toml_str)

        assert len(restored.files) == 2
        assert isinstance(restored.files[0], Path)
        assert isinstance(restored.files[1], URI)
        assert str(restored.files[0]) == "/tmp/local.txt"
        assert str(restored.files[1]) == "https://example.com/doc.pdf"

    def test_message_uri_only(self):
        """Test Message with only URIs."""
        msg = Message(
            role="user",
            content="Remote resources",
            files=[
                URI("http://example.com/a.jpg"),
                URI("memo://resource/123"),
            ],
        )
        assert len(msg.files) == 2
        assert all(isinstance(f, URI) for f in msg.files)

    def test_message_path_only(self):
        """Test Message with only Paths (backward compatibility)."""
        msg = Message(
            role="user",
            content="Local files",
            files=[Path("/tmp/a.txt"), Path("/tmp/b.txt")],
        )
        assert len(msg.files) == 2
        assert all(isinstance(f, Path) for f in msg.files)


class TestAbsToRelWorkspace:
    """Test _abs_to_rel_workspace handles URIs correctly."""

    def test_uri_passed_through(self):
        """URIs should be returned as-is, not converted to paths."""
        from pathlib import Path

        from gptme.server.api import _abs_to_rel_workspace

        workspace = Path("/tmp/workspace")
        uri = URI("https://example.com/doc.pdf")

        result = _abs_to_rel_workspace(uri, workspace)

        assert result == "https://example.com/doc.pdf"

    def test_mcp_uri_passed_through(self):
        """MCP URIs should be returned as-is."""
        from pathlib import Path

        from gptme.server.api import _abs_to_rel_workspace

        workspace = Path("/tmp/workspace")
        uri = URI("memo://resource/123")

        result = _abs_to_rel_workspace(uri, workspace)

        assert result == "memo://resource/123"

    def test_path_still_converted(self):
        """Regular paths should still be converted to relative."""
        from pathlib import Path

        from gptme.server.api import _abs_to_rel_workspace

        workspace = Path("/tmp/workspace")
        abs_path = Path("/tmp/workspace/subdir/file.txt")

        result = _abs_to_rel_workspace(abs_path, workspace)

        assert result == "subdir/file.txt"

    def test_path_outside_workspace(self):
        """Paths outside workspace should be returned as-is."""
        from pathlib import Path

        from gptme.server.api import _abs_to_rel_workspace

        workspace = Path("/tmp/workspace")
        outside_path = Path("/other/location/file.txt")

        result = _abs_to_rel_workspace(outside_path, workspace)

        assert result == "/other/location/file.txt"
