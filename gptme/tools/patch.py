"""
Gives the LLM agent the ability to patch text files, by using a adapted version git conflict markers.

Environment Variables:
    GPTME_PATCH_RECOVERY: If set to "true" or "1", returns the file content in error messages
                         when patches don't match. This helps the assistant recover faster
                         by seeing the actual file contents.
"""

import difflib
import re
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from ..config import get_config
from ..message import Message
from ..util import path_with_tilde
from ..util.ask_execute import execute_with_confirmation
from ..util.file_write import write_text_preserving_original
from ..util.workspace import current_workspace, resolve_in_workspace
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
    file_newline,
    get_path,
    read_text_lf,
)

instructions = """
To patch/modify files, we use an adapted version of git conflict markers.

Keep patches small. ORIGINAL must match the file exactly, including ellipsis
comments (`# ...`): these are literal, not wildcards. Use separate
ORIGINAL/UPDATED blocks to skip unchanged code. ORIGINAL must have exactly one
match, including overlapping occurrences; add surrounding context to disambiguate.

### When to use patch vs save

Use `patch` for targeted edits to existing files.
Use `save` for new files, full rewrites, or changes too large for patch markers.

Note: When patching markdown, avoid replacing partial codeblocks (just the opening
or closing backticks). The parser needs complete codeblocks. For simple
codeblock-boundary changes (like a language tag), use `sed` or `perl` instead.
""".strip()

instructions_format = {
    "markdown": f"""
The $PATH parameter MUST be on the same line as the code block start, not on the line after.

The patch block should be written in the following format:

{
        ToolUse(
            "patch",
            ["$PATH"],
            '''
<<<<<<< ORIGINAL
$ORIGINAL_CONTENT
=======
$UPDATED_CONTENT
>>>>>>> UPDATED
'''.strip(),
        ).to_output("markdown")
    }
""",
    "tool": "The `patch` parameter must be a string containing conflict markers without any code block.",
}

ORIGINAL = "<<<<<<< ORIGINAL\n"
DIVIDER = "\n=======\n"
UPDATED = "\n>>>>>>> UPDATED"

patch_content = """
<<<<<<< ORIGINAL
    print("Hello world")
=======
    name = input("What is your name? ")
    print(f"Hello {name}")
>>>>>>> UPDATED
""".strip()


def examples(tool_format):
    return f"""
> User: patch `src/hello.py` to ask for the name of the user
```src/hello.py
def hello():
    print("Hello world")

if __name__ == "__main__":
    hello()
```
> Assistant:
{ToolUse("patch", ["src/hello.py"], patch_content).to_output(tool_format)}
> System: Patch applied
""".strip()


@dataclass
class Patch:
    original: str
    updated: str

    def apply(self, content: str) -> str:
        # Normalize the model-supplied replacement once, here. The file side
        # (``content``) is already normalized by ``read_text_lf`` and must not be
        # normalized again: a preserved lone carriage return sitting right before
        # a newline would be read as a CRLF pair and silently deleted from an
        # untouched line. For the same reason, an ORIGINAL chunk that names such
        # a carriage return puts the replacement in that model too: keep it as is.
        updated = self.updated
        if "\r\n" not in self.original and not self.original.endswith("\r"):
            updated = updated.replace("\r\n", "\n")
        # Fast path: try exact match first (backward compatible)
        first_match = content.find(self.original)
        if first_match != -1:
            # Start one character later, not after the chunk: matches can overlap.
            if content.find(self.original, first_match + 1) != -1:
                raise ValueError(
                    "original chunk is not unique (at least 2 matches found). "
                    "Provide more surrounding context to make the match unique."
                )
            new_content = content.replace(self.original, updated, 1)
            if new_content == content:
                raise ValueError(
                    "patch did not change the file "
                    "(original and updated chunks are identical)"
                )
            return new_content

        # Fallback: try relaxed matching (treat whitespace-only lines as equivalent)
        match_span = self._find_relaxed_match(content)
        if match_span is None:
            # if original chunk not found, include file contents in error for fast recovery
            file_contents = (
                f"Here are the actual file contents:\n```original\n{content}\n```"
                if get_config().get_env_bool("GPTME_PATCH_RECOVERY")
                else ""
            )
            raise ValueError(f"original chunk not found in file\n{file_contents}")

        # Splice at the matched lines. str.replace would hit the first
        # occurrence of the matched text, which can be earlier in the file
        # (e.g. the tail of a more-indented line) than the relaxed match.
        start, end = match_span
        new_content = content[:start] + updated + content[end:]
        if new_content == content:
            raise ValueError(
                "patch did not change the file "
                "(original and updated chunks are identical)"
            )
        return new_content

    def _find_relaxed_match(self, content: str) -> tuple[int, int] | None:
        """
        Find a match for self.original in content using relaxed whitespace matching.
        Returns the (start, end) character span of the matched lines in content,
        or None if no match found.
        Treats lines with only whitespace as equivalent regardless of whitespace content.
        """
        original_lines = self.original.splitlines(keepends=True)
        content_lines = content.splitlines(keepends=True)

        # Slide a window through content looking for matches
        matches = []
        for i in range(len(content_lines) - len(original_lines) + 1):
            window = content_lines[i : i + len(original_lines)]
            if self._lines_match_relaxed(window, original_lines):
                matches.append(i)

        if len(matches) == 0:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"original chunk is not unique ({len(matches)} matches with "
                "relaxed whitespace matching). "
                "Provide more surrounding context to make the match unique."
            )

        # Only calculate character offsets for the unique matching window.
        i = matches[0]
        start = sum(len(line) for line in content_lines[:i])
        end = start + sum(
            len(line) for line in content_lines[i : i + len(original_lines)]
        )
        return start, end

    def _lines_match_relaxed(
        self, window_lines: list[str], original_lines: list[str]
    ) -> bool:
        """
        Check if lines match using relaxed whitespace rules.
        Lines containing only whitespace (spaces, tabs, etc.) are treated as equivalent.
        """
        if len(window_lines) != len(original_lines):
            return False

        for window_line, original_line in zip(window_lines, original_lines):
            # If both lines are whitespace-only, they match
            if window_line.strip() == "" and original_line.strip() == "":
                continue
            # Otherwise they must match exactly
            if window_line != original_line:
                return False

        return True

    def diff_minimal(self, strip_context=False) -> str:
        """
        Show a minimal diff of the patch.
        Note that a minimal diff isn't necessarily a unique diff.
        """
        # TODO: replace previous patches with the minimal version

        diff = list(
            difflib.unified_diff(
                self.original.splitlines(),
                self.updated.splitlines(),
                lineterm="",
            )
        )[3:]
        if strip_context:
            # find first and last lines with changes
            markers = [line[0] for line in diff]
            start = min(
                markers.index("+") if "+" in markers else len(markers),
                markers.index("-") if "-" in markers else len(markers),
            )
            end = min(
                markers[::-1].index("+") if "+" in markers else len(markers),
                markers[::-1].index("-") if "-" in markers else len(markers),
            )
            diff = diff[start : len(diff) - end]
        return "\n".join(diff)

    @classmethod
    def _from_codeblock(cls, codeblock: str) -> Generator["Patch", None, None]:
        codeblock = codeblock.strip()

        # Split the codeblock into multiple patches
        patches = re.split(f"(?={re.escape(ORIGINAL)})", codeblock)

        for patch in patches:
            if not patch.strip():
                continue

            if ORIGINAL not in patch:  # pragma: no cover
                raise ValueError(f"invalid patch, no `{ORIGINAL.strip()}`", patch)

            # First split on ORIGINAL to get the content after it
            _, after_original = re.split(re.escape(ORIGINAL), patch, maxsplit=1)

            # Then split on DIVIDER to get the original content
            if DIVIDER not in after_original:  # pragma: no cover
                raise ValueError("invalid patch format: missing =======")
            original, after_divider = re.split(
                re.escape(DIVIDER), after_original, maxsplit=1
            )

            # Finally split on UPDATED to get the modified content
            # Use UPDATED[1:] to ignore the leading newline in the marker,
            # allowing us to detect truly empty content between ======= and >>>>>>> UPDATED
            if after_divider.startswith(UPDATED[1:]):
                # Special case: empty content followed immediately by UPDATED marker
                modified = ""
            else:
                if UPDATED not in after_divider:  # pragma: no cover
                    raise ValueError("invalid patch format: missing >>>>>>> UPDATED")
                modified, _ = re.split(re.escape(UPDATED), after_divider, maxsplit=1)

                # Check for extra ======= markers in the updated content
                # Use regex with negative lookahead to match exactly 7 equals signs
                # This avoids matching longer sequences like RST heading underlines
                # (e.g., ===================) while still catching actual extra dividers
                if re.search(r"\n=======(?!=)", modified):
                    raise ValueError(
                        "invalid patch format: extra ======= marker found in updated content. "
                        "Use only one ======= between original and updated content."
                    )

            yield Patch(original, modified)

    @classmethod
    def from_codeblock(cls, codeblock: str) -> Generator["Patch", None, None]:
        # Keep comment text literal; implicit omission markers discard edits and
        # let mismatched ORIGINAL context pass as a wildcard.
        yield from cls._from_codeblock(codeblock)


def apply(codeblock: str, content: str) -> str:
    """
    Applies multiple patches in ``codeblock`` to ``content``.
    Provides detailed error messages when patches fail.
    """
    patches = list(Patch.from_codeblock(codeblock))
    total_hunks = len(patches)
    new_content = content

    for i, patch in enumerate(patches, 1):
        try:
            new_content = patch.apply(new_content)
        except ValueError as e:
            error_msg = str(e.args[0]) if e.args else str(e)
            # Create a preview of the failing hunk (first few lines)
            original_preview = patch.original[:100].replace("\n", "\\n")
            if len(patch.original) > 100:
                original_preview += "..."

            if total_hunks == 1:
                raise ValueError(error_msg) from None
            status = f"Hunk {i}/{total_hunks} failed"
            if i > 1:
                # apply() is all-or-nothing: the file is only written on success
                status += (
                    f" ({i - 1} hunk(s) before it matched, but no changes were written;"
                    " fix this hunk and resend the full patch)"
                )
            raise ValueError(
                f"{status}: {error_msg}\nFailed hunk starts with: {original_preview!r}"
            ) from None

    return new_content


def preview_patch(content: str, path: Path | None) -> str | None:
    """Prepare preview content for patch operation."""
    try:
        patches = Patch.from_codeblock(content)
        return "\n@@@\n".join(p.diff_minimal() for p in patches)
    except ValueError as e:
        raise ValueError(f"Invalid patch: {e.args[0]}") from None


def execute_patch_impl(
    content: str, path: Path | None
) -> Generator[Message, None, None]:
    """Actual patch implementation."""
    if path is None:
        raise ValueError("Path is required to apply a patch")

    # Print full path to give agent feedback about exactly which file is being patched
    path_display = path
    path = resolve_in_workspace(path)

    # Path traversal protection: validate relative paths stay within cwd
    # Absolute paths are intentional and allowed, relative paths should not escape cwd
    if not path_display.is_absolute():
        cwd = current_workspace()
        try:
            path.relative_to(cwd)
        except ValueError as err:
            raise ValueError(
                f"Path traversal detected: {path_display} resolves to {path} "
                f"which is outside current directory {cwd}"
            ) from err

    try:
        if path.exists() and not path.is_file():
            raise ValueError(f"Cannot patch non-regular file `{path}`")

        # Keep lone "\r" characters: universal-newline mode would turn them into
        # line breaks on write-back, even far away from the patched hunk.
        original_content = read_text_lf(path)

        # Apply the patch
        patched_content = apply(content, original_content)

        # Compare token counts and generate warnings
        patch_len = len(content)
        full_file_len = len(patched_content)
        warnings = []
        if patch_len > 1000 and patch_len > full_file_len:
            warnings.append(
                "Note: The patch was big and larger than the file. In the future, try writing smaller patches or use the save tool instead."
            )

        # Write the patched content, keeping the file's line endings
        newline = file_newline(path)  # before open() truncates the file
        # Re-check before the write: the read above is fast but not atomic with this write.
        if path.exists() and not path.is_file():
            raise ValueError(f"Cannot patch non-regular file `{path}`")
        # Do not normalize here. ``patched_content`` derives from
        # ``read_text_lf``, whose LF model keeps a lone carriage return as content;
        # the patch's own replacement text was normalized in ``Patch.apply``.
        # Python's newline= then applies the target ending exactly once
        # (\n→\r\n for CRLF files, no-op for LF). Normalizing again here would
        # pair a preserved lone CR with its following newline and delete it from
        # an untouched line.
        text = patched_content
        # Validate UTF-8 before staging the replacement.
        text.encode("utf-8")
        write_text_preserving_original(path, text, newline=newline)

        # Return success message with any warnings
        warnings_str = "\n".join(warnings)
        yield Message(
            "system",
            f"Patch successfully applied to `{path_with_tilde(path)}`\n{warnings_str}".strip(),
        )

    except FileNotFoundError:
        raise ValueError(
            f"Patch failed: No such file or directory `{path}` (pwd: `{current_workspace()}`)"
        ) from None
    except UnicodeEncodeError as e:
        raise ValueError(
            f"Patch failed: content is not encodable as UTF-8 at character {e.start}."
        ) from None
    except UnicodeDecodeError as e:
        # Subclass of ValueError whose args[0] is just the codec name ("utf-8")
        raise ValueError(
            f"Patch failed: `{path}` is not valid UTF-8 (byte 0x{e.object[e.start]:02x}"
            f" at offset {e.start}). Edit it with the shell tool (e.g. sed, iconv) instead."
        ) from None
    except ValueError as e:
        raise ValueError(f"Patch failed: {e.args[0]}") from None


def execute_patch(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Applies the patch."""
    if code is None and kwargs is not None:
        code = kwargs.get("patch", code)

    if not code:
        yield Message("system", "Error: No patch provided by the assistant")
        return

    yield from execute_with_confirmation(
        code,
        args,
        kwargs,
        execute_fn=execute_patch_impl,
        get_path_fn=get_path,
        preview_fn=preview_patch,
        preview_lang="diff",
        confirm_msg=f"Apply patch to {get_path(code, args, kwargs)}?",
        allow_edit=True,
    )


tool = ToolSpec(
    name="patch",
    desc="Apply a patch to a file",
    instructions=instructions,
    examples=examples,
    execute=execute_patch,
    block_types=["patch"],
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="The path of the file to patch.",
            required=True,
        ),
        Parameter(
            name="patch",
            type="string",
            description=f"The patch to apply. Use conflict markers! Example:\n{patch_content}",
            required=True,
        ),
    ],
    hints=frozenset({"file-ops", "destructive"}),
)
__doc__ = tool.get_doc(__doc__)
