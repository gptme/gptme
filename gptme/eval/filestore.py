import atexit
import base64
import shutil
import tempfile
from pathlib import Path

from .types import Files


class FileStore:
    """File store for eval working directories with automatic cleanup."""

    # Track all created temp dirs for cleanup
    _temp_dirs: list[Path] = []
    _cleanup_registered: bool = False

    def __init__(self, working_dir: Path | None = None):
        if working_dir:
            self.working_dir = working_dir
            self._is_temp = False
        else:
            self.working_dir = Path(tempfile.mkdtemp(prefix="gptme-evals-"))
            self._is_temp = True
            FileStore._temp_dirs.append(self.working_dir)
            # Register cleanup on first temp dir creation
            if not FileStore._cleanup_registered:
                atexit.register(FileStore._cleanup_all)
                FileStore._cleanup_registered = True
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        """Clean up the working directory if it was auto-created."""
        if self._is_temp and self.working_dir.exists():
            shutil.rmtree(self.working_dir, ignore_errors=True)
            if self.working_dir in FileStore._temp_dirs:
                FileStore._temp_dirs.remove(self.working_dir)

    def __enter__(self) -> "FileStore":
        return self

    def __exit__(self, *args) -> None:
        self.cleanup()

    @classmethod
    def _cleanup_all(cls) -> None:
        """Clean up all remaining temp directories on exit."""
        for temp_dir in cls._temp_dirs[
            :
        ]:  # Copy list to avoid mutation during iteration
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        cls._temp_dirs.clear()

    def upload(self, files: Files):
        for name, content in files.items():
            path = self.working_dir / name
            # Validate path stays within working_dir to prevent path traversal
            try:
                path.resolve().relative_to(self.working_dir.resolve())
            except ValueError as err:
                raise ValueError(f"Path traversal detected: {name}") from err
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                with open(path, "w") as f:
                    f.write(content)
            elif isinstance(content, bytes):
                with open(path, "wb") as f:
                    f.write(base64.b64decode(content))

    def download(self) -> Files:
        files: Files = {}
        for path in self.working_dir.glob("**/*"):
            if path.is_file():
                key = str(path.relative_to(self.working_dir))
                try:
                    with open(path) as f:
                        files[key] = f.read()
                except UnicodeDecodeError:
                    # file is binary
                    with open(path, "rb") as f:
                        files[key] = base64.b64encode(f.read())
        return files
