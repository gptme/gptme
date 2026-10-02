"""Share an auto-generated server token with local clients via a file.

When ``GPTME_SERVER_TOKEN`` is unset, ``gptme-server serve`` generates a token
per run. Local clients (``/restart web``, ``gptme-server token``) cannot know
it, so the server writes it to ``<data dir>/server-token`` (mode 0600) and
removes it on clean shutdown. Flask-free so clients can import it cheaply.
"""

import logging
import os
from pathlib import Path

from ..dirs import get_data_dir

logger = logging.getLogger(__name__)


def get_token_file() -> Path:
    return get_data_dir() / "server-token"


def write_token_file(token: str) -> None:
    path = get_token_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        # O_CREAT's mode does not apply when the file already exists.
        path.chmod(0o600)
    except OSError as e:
        logger.warning(f"Could not write server token file {path}: {e}")


def read_token_file() -> str | None:
    try:
        return get_token_file().read_text().strip() or None
    except OSError:
        return None


def remove_token_file(token: str) -> None:
    """Remove the token file if it still holds ``token``.

    Another server started later may have replaced it; leave theirs alone.
    """
    if read_token_file() == token:
        get_token_file().unlink(missing_ok=True)
