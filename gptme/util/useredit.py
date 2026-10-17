"""
Tool that lets the user edit something in a temporary file using their $EDITOR.

This is typically used to edit a conversation log with /edit.
"""

import logging
import os
import shlex
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def _split_editor(editor: str) -> list[str]:
    """Split the EDITOR value into argv, respecting the platform's path syntax."""
    if os.name == "nt":
        # POSIX-mode shlex treats backslashes as escapes and would mangle
        # Windows paths like C:\tools\edit.exe. Non-POSIX splitting preserves
        # them but keeps quote characters in the tokens, so strip those.
        return [token.strip("\"'") for token in shlex.split(editor, posix=False)]
    return shlex.split(editor)


def edit_text_with_editor(initial_text: str, ext: str | None = None) -> str:
    """Edit some text in a temporary file using the user's $EDITOR."""
    suffix = f".{ext}" if ext else ""
    # Create a temporary file and write the initial text to it.
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(initial_text)
        temp_filename = f.name

    # use vi or die trying
    editor = os.environ.get("EDITOR", "nano")

    # Open the file in the user's editor.
    try:
        command = [*_split_editor(editor), temp_filename]
        logger.debug("Running editor: %s", command)
        p = subprocess.run(command, check=False)
        # now, we wait

        # Check that the editor exited successfully.
        if p.returncode != 0:
            raise RuntimeError(f"Editor exited with non-zero exit code: {p.returncode}")

        # Read the edited text back in.
        with open(temp_filename) as f:
            edited_text = f.read()

        # Check that the user actually edited the file.
        if edited_text == initial_text:
            logger.info("No changes made, exiting.")

        return edited_text
    finally:
        # Clean up the temporary file.
        try:
            os.remove(temp_filename)
        except OSError:
            pass
