import re
from pathlib import Path
from urllib.parse import urlparse

# Pattern to detect URIs like memo://, http://, etc.
URI_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class URIPath(Path):
    """A Path subclass that can handle URI strings like http://, memo://, etc.

    This allows URIs like URLs (http://) and MCP resources (e.g. memo://) to be stored alongside regular file paths and
    used with the existing Path interface.
    """

    def __new__(cls, *args, **kwargs):
        # Handle URI strings
        if len(args) == 1 and isinstance(args[0], str):
            path_str = args[0]
            # Check if it's a URI string (e.g., memo://name)
            if URI_PATTERN.match(path_str):
                # For URI strings, we'll create a Path object with an empty string
                # but store the URI separately and override methods
                obj = super(URIPath, cls).__new__(cls, "")
                object.__setattr__(obj, "_uri", path_str)
                object.__setattr__(obj, "_is_uri", True)
                return obj

        # For regular paths, behave like a normal Path
        obj = super(URIPath, cls).__new__(cls, *args, **kwargs)
        object.__setattr__(obj, "_is_uri", False)
        object.__setattr__(obj, "_uri", "")
        return obj

    def __str__(self) -> str:
        """Return the path as a string."""
        if getattr(self, "_is_uri", False):
            return getattr(self, "_uri", "")
        return super().__str__()

    def __repr__(self) -> str:
        """Return a string representation."""
        if getattr(self, "_is_uri", False):
            return f"URIPath('{getattr(self, '_uri', '')}')"
        return super().__repr__()

    def is_uri(self) -> bool:
        """Returns True if this is a URI rather than a filesystem path."""
        return getattr(self, "_is_uri", False)

    def get_scheme(self) -> str | None:
        """Returns the URI scheme (e.g., 'memo', 'http') if this is a URI."""
        if not getattr(self, "_is_uri", False):
            return None
        uri = getattr(self, "_uri", "")
        parsed = urlparse(uri)
        return parsed.scheme

    # Override methods that would fail on URIs

    def exists(self, *args, **kwargs) -> bool:
        """Check if path exists. Always returns True for URIs."""
        if getattr(self, "_is_uri", False):
            return True
        return super().exists(*args, **kwargs)

    def is_file(self) -> bool:
        """Check if path is a file. Always returns True for URIs."""
        if getattr(self, "_is_uri", False):
            return True
        return super().is_file()

    def is_dir(self) -> bool:
        """Check if path is a directory. Always returns False for URIs."""
        if getattr(self, "_is_uri", False):
            return False
        return super().is_dir()

    def is_absolute(self) -> bool:
        """Check if path is absolute. Always returns True for URIs."""
        if getattr(self, "_is_uri", False):
            return True
        return super().is_absolute()

    def resolve(self, strict=False):
        """Resolve path. Returns self for URIs."""
        if getattr(self, "_is_uri", False):
            return self
        result = super().resolve(strict)
        return URIPath(str(result))

    def absolute(self):
        """Return absolute path. Returns self for URIs."""
        if getattr(self, "_is_uri", False):
            return self
        result = super().absolute()
        return URIPath(str(result))
