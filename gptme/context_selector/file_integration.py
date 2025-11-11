"""File-specific implementation of context selector."""

from pathlib import Path
from typing import Any

from .base import ContextItem


class FileItem(ContextItem):
    """Wrapper for files to work with context selector."""

    def __init__(
        self,
        path: Path,
        mention_count: int = 0,
        mtime: float = 0.0,
    ):
        self.path = path
        self.mention_count = mention_count
        self.mtime = mtime

    @property
    def content(self) -> str:
        """Return file content for LLM evaluation."""
        try:
            if self.path.exists() and self.path.is_file():
                # For large files, return excerpt + metadata
                content = self.path.read_text()
                if len(content) > 2000:
                    # Large file: return first 1000 chars + metadata
                    return f"{content[:1000]}...\n\n[File size: {len(content)} chars]"
                return content
        except (OSError, UnicodeDecodeError):
            pass
        return f"<{self.path.suffix} file>"

    @property
    def metadata(self) -> dict[str, Any]:
        """Return file metadata."""
        file_size = 0
        try:
            if self.path.exists():
                file_size = self.path.stat().st_size
        except OSError:
            pass

        return {
            "mention_count": self.mention_count,
            "mtime": self.mtime,
            "file_type": self.path.suffix[1:] if self.path.suffix else "unknown",
            "file_size": file_size,
            "path": str(self.path),
        }

    @property
    def identifier(self) -> str:
        """Return unique identifier for this file."""
        return str(self.path)

    def __repr__(self) -> str:
        return f"FileItem({self.path})"
