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
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
    get_path,
)

instructions = """
To patch/modify files, we use an adapted version of git conflict markers.

This can be used to edit files, without having to rewrite the whole file.
Only one patch block can be written per tool use. Extra ORIGINAL/UPDATED blocks will be ignored.
Try to keep the patch as small as possible. Avoid placeholders, as they may make the patch fail.

To keep the patch small, try to scope the patch to imports/function/class.
If the patch is large, consider using the save tool to rewrite the whole file.
""".strip()

instructions_format = {
    "markdown": f"""
The $PATH parameter MUST be on the same line as the code block start, not on the line after.

The patch block should be written in the following format:

{ToolUse("patch", ["$PATH"], '''
<<<<<<< ORIGINAL
$ORIGINAL_CONTENT
=======
$UPDATED_CONTENT
>>>>>>> UPDATED
'''.strip()).to_output("markdown")}
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
        if self.original not in content:
            # if original chunk not in file, we want to include the current file contents in the error message so it can recover fast
            file_contents = (
                f"Here are the actual file contents:\n```original\n{content}\n```"
                if get_config().get_env_bool("GPTME_PATCH_RECOVERY")
                else ""
            )
            raise ValueError(f"original chunk not found in file\n{file_contents}")
        if content.count(self.original) > 1:
            raise ValueError("original chunk not unique")
        new_content = content.replace(self.original, self.updated, 1)
        if new_content == content:
            raise ValueError("patch did not change the file")
        return new_content

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
                if "\n=======" in modified:
                    raise ValueError(
                        "invalid patch format: extra ======= marker found in updated content. "
                        "Use only one ======= between original and updated content."
                    )

            yield Patch(original, modified)

    @classmethod
    def from_codeblock(cls, codeblock: str) -> Generator["Patch", None, None]:
        for patch in cls._from_codeblock(codeblock):
            original, updated = patch.original, patch.updated
            re_placeholder = re.compile(r"^[ \t]*(#|//|\") \.\.\. ?.*$", re.MULTILINE)
            if re_placeholder.search(original) or re_placeholder.search(updated):
                originals = re_placeholder.split(original)
                modifieds = re_placeholder.split(updated)
                if len(originals) != len(modifieds):
                    raise ValueError(
                        "different number of placeholders in original and modified chunks"
                    )
                for orig, mod in zip(originals, modifieds):
                    if orig == mod:
                        continue
                    yield Patch(orig, mod)
            else:
                yield patch


def apply(codeblock: str, content: str) -> str:
    """
    Applies multiple patches in ``codeblock`` to ``content``.
    """
    new_content = content
    for patch in Patch.from_codeblock(codeblock):
        new_content = patch.apply(new_content)
    return new_content


def preview_patch(content: str, path: Path | None) -> str | None:
    """Prepare preview content for patch operation."""
    try:
        patches = Patch.from_codeblock(content)
        return "\n@@@\n".join(p.diff_minimal() for p in patches)
    except ValueError as e:
        raise ValueError(f"Invalid patch: {e.args[0]}") from None


def execute_patch_impl(
    content: str, path: Path | None, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Actual patch implementation."""
    assert path is not None

    # Print full path to give agent feedback about exactly which file is being patched
    path = path.expanduser().resolve()

    try:
        with open(path) as f:
            original_content = f.read()

        # Apply the patch
        patched_content = apply(content, original_content)

        # Compare token counts and generate warnings
        patch_len = len(content)
        full_file_len = len(patched_content)
        warnings = []
        if 1000 < full_file_len < patch_len:
            warnings.append(
                "Note: The patch was big and larger than the file. In the future, try writing smaller patches or use the save tool instead."
            )

        # Write the patched content
        with open(path, "w") as f:
            f.write(patched_content)

        # Return success message with any warnings
        warnings_str = "\n".join(warnings)
        yield Message(
            "system",
            f"Patch successfully applied to `{path}`\n{warnings_str}".strip(),
        )

    except FileNotFoundError:
        raise ValueError(
            f"Patch failed: No such file or directory `{path}` (pwd: `{Path.cwd()}`)"
        ) from None
    except ValueError as e:
        raise ValueError(f"Patch failed: {e.args[0]}") from None


def execute_patch(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc = lambda _: True,
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
        confirm,
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
)
__doc__ = tool.get_doc(__doc__)
