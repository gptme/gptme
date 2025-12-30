import base64
import tempfile
from pathlib import Path

from .types import Files


class FileStore:
    def __init__(self, working_dir: Path | None = None):
        if working_dir:
            self.working_dir = working_dir
        else:
            self.working_dir = Path(tempfile.mkdtemp(prefix="gptme-evals-"))
        self.working_dir.mkdir(parents=True, exist_ok=True)

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
