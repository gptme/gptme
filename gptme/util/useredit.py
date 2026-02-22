"""
Tool that lets the user edit something in a temporary file using their $EDITOR.

This is typically used to edit a conversation log with /edit.
"""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def edit_text_with_editor(initial_text: str, ext=None) -> str:  # pragma: no cover
    """Edit some text in a temporary file using the user's $EDITOR."""
    suffix = f".{ext}" if ext else ""
    # Create a temporary file and write the initial text to it.
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False) as f:
        f.write(initial_text)
        temp_filename = f.name

    # use vi or die trying
    editor = os.environ.get("EDITOR", "nano")

    # Open the file in the user's editor.
    logger.debug("Running editor:", [editor, temp_filename])
    p = subprocess.run([editor, temp_filename], check=False)
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

    # Delete the temporary file.
    os.remove(temp_filename)

    return edited_text
