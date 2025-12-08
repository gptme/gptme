"""Content-addressable file storage for conversation logs.

This module provides functions to store and retrieve files by their content hash,
ensuring that file versions are preserved in conversation history.
"""

import hashlib
import shutil
from pathlib import Path


def compute_file_hash(filepath: Path, algorithm: str = "sha256") -> str:
    """Compute the hash of a file's contents.

    Args:
        filepath: Path to the file to hash
        algorithm: Hash algorithm to use (default: sha256)

    Returns:
        Hex digest of the file's content hash (first 16 chars)
    """
    hasher = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    # Use first 16 chars for reasonable uniqueness while keeping filenames short
    return hasher.hexdigest()[:16]


def get_files_dir(logdir: Path) -> Path:
    """Get the files directory for a conversation log.

    Args:
        logdir: The conversation log directory

    Returns:
        Path to the files subdirectory
    """
    files_dir = logdir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    return files_dir


def store_file(logdir: Path, filepath: Path) -> tuple[str, str]:
    """Store a file by its content hash.

    Args:
        logdir: The conversation log directory
        filepath: Path to the file to store

    Returns:
        Tuple of (hash, stored_filename)
    """
    # Compute hash
    file_hash = compute_file_hash(filepath)

    # Determine extension
    suffix = filepath.suffix or ""
    stored_name = f"{file_hash}{suffix}"

    # Store in files directory
    files_dir = get_files_dir(logdir)
    stored_path = files_dir / stored_name

    # Only copy if not already stored (content-addressed = idempotent)
    if not stored_path.exists():
        shutil.copy2(filepath, stored_path)

    return file_hash, stored_name


def get_stored_path(logdir: Path, file_hash: str, suffix: str = "") -> Path | None:
    """Get the path to a stored file by its hash.

    Args:
        logdir: The conversation log directory
        file_hash: The content hash of the file
        suffix: Optional file extension (e.g., ".py")

    Returns:
        Path to the stored file, or None if not found
    """
    files_dir = logdir / "files"
    stored_path = files_dir / f"{file_hash}{suffix}"

    if stored_path.exists():
        return stored_path

    # Try without suffix (might have been stored without extension)
    if suffix:
        stored_path_no_ext = files_dir / file_hash
        if stored_path_no_ext.exists():
            return stored_path_no_ext

    return None


def read_stored_content(logdir: Path, file_hash: str, suffix: str = "") -> str | None:
    """Read content from a stored file by its hash.

    Args:
        logdir: The conversation log directory
        file_hash: The content hash of the file
        suffix: Optional file extension

    Returns:
        File content as string, or None if not found or not readable
    """
    stored_path = get_stored_path(logdir, file_hash, suffix)
    if stored_path is None:
        return None

    try:
        return stored_path.read_text()
    except (UnicodeDecodeError, OSError):
        return None
