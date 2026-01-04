import dataclasses
import logging
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

import tomlkit
from dateutil.parser import isoparse
from rich.markup import escape as escape_markup
from rich.syntax import Syntax
from tomlkit._utils import escape_string
from typing_extensions import Self

from gptme.llm.models import get_default_model

from .codeblock import Codeblock
from .constants import ROLE_COLOR
from .util import console
from .util.prompt import rich_to_str
from .util.tokens import len_tokens

logger = logging.getLogger(__name__)


class MessageMetadata(TypedDict, total=False):
    """
    Metadata stored with each message.

    All fields are optional for compact storage - only non-None values are serialized.

    Token/cost fields are populated for assistant messages when telemetry is enabled.

    Uses flat token format (matches cost_tracker and common industry conventions):
        {
            "model": "claude-sonnet",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 80,
            "cache_creation_tokens": 10,
            "cost": 0.005
        }
    """

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost: float  # Cost in USD


def _format_toml_value(value: object) -> str:
    """Format a value for TOML inline table."""
    if isinstance(value, str):
        return f'"{value}"'
    elif isinstance(value, float):
        return f"{value:.6f}"
    else:
        return str(value)


def _format_metadata_toml(metadata: MessageMetadata) -> str:
    """Format metadata as TOML inline table."""
    meta_items = []
    for k, v in metadata.items():
        meta_items.append(f'"{k}" = {_format_toml_value(v)}')
    return f"metadata = {{ {', '.join(meta_items)} }}"


@dataclass(frozen=True, eq=False)
class Message:
    """
    A message in the assistant conversation.

    Attributes:
        role: The role of the message sender (system, user, or assistant).
        content: The content of the message.
        timestamp: The timestamp of the message.
        files: Files attached to the message, could e.g. be images for vision.
        pinned: Whether this message should be pinned to the top of the chat, and never context-trimmed.
        hide: Whether this message should be hidden from the chat output (but still be sent to the assistant).
        quiet: Whether this message should be printed on execution (will still print on resume, unlike hide).
               This is not persisted to the log file.
        metadata: Optional metadata including token usage and cost information.
    """

    role: Literal["system", "user", "assistant"]
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    files: list[Path] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)  # {filepath: hash}
    call_id: str | None = None

    pinned: bool = False
    hide: bool = False
    quiet: bool = False

    # Metadata for token usage and cost tracking
    metadata: MessageMetadata | None = None

    def __post_init__(self):
        assert isinstance(self.timestamp, datetime)

    def __repr__(self):
        content = textwrap.shorten(self.content, 20, placeholder="...")
        return f"<Message role={self.role} content={content}>"

    def __eq__(self, other):
        # FIXME: really include timestamp?
        if not isinstance(other, Message):
            return False
        return (
            self.role == other.role
            and self.content == other.content
            and self.timestamp == other.timestamp
        )

    def len_tokens(self, model: str) -> int:
        return len_tokens(self, model=model)

    def replace(self, **kwargs) -> Self:
        """Replace attributes of the message."""
        return dataclasses.replace(self, **kwargs)

    def to_dict(self, keys=None) -> dict:
        """Return a dict representation of the message, serializable to JSON."""

        d: dict = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.files:
            # Resolve to absolute paths to prevent issues when working directory changes
            d["files"] = [str(f.resolve()) for f in self.files]
        if self.file_hashes:
            d["file_hashes"] = self.file_hashes
        if self.pinned:
            d["pinned"] = True
        if self.hide:
            d["hide"] = True
        if self.call_id:
            d["call_id"] = self.call_id
        # Only serialize metadata if it has content (compact storage)
        if self.metadata:
            d["metadata"] = dict(self.metadata)
        if keys:
            return {k: d[k] for k in keys if k in d}
        return d

    def to_xml(self) -> str:
        """Converts a message to an XML string with proper escaping."""
        # Use quoteattr for role to handle quotes and special chars safely
        # Use xml_escape for content to handle <, >, & characters
        return f"<message role={quoteattr(self.role)}>\n{xml_escape(self.content)}\n</message>"

    def format(
        self,
        oneline: bool = False,
        highlight: bool = False,
        max_length: int | None = None,
    ) -> str:
        """Format the message for display.

        Args:
            oneline: Whether to format the message as a single line
            highlight: Whether to highlight code blocks
            max_length: Maximum length of the message. If None, no truncation is applied.
                       If set, will truncate at first newline or max_length, whichever comes first.
        """
        if max_length is not None:
            first_newline = self.content.find("\n")
            max_length = (
                min(max_length, first_newline) if first_newline != -1 else max_length
            )
            content = self.content[:max_length]
            if len(content) < len(self.content):
                content += "..."
            temp_msg = self.replace(content=content)
            return format_msgs([temp_msg], oneline=True, highlight=highlight)[0]
        return format_msgs([self], oneline=oneline, highlight=highlight)[0]

    def print(self, oneline: bool = False, highlight: bool = True) -> None:
        print_msg(self, oneline=oneline, highlight=highlight)

    def to_toml(self) -> str:
        """Converts a message to a TOML string, for easy editing by hand in editor to then be parsed back."""
        flags = []
        if self.pinned:
            flags.append("pinned")
        if self.hide:
            flags.append("hide")
        flags_toml = "\n".join(f"{flag} = true" for flag in flags)
        files_toml = f"files = {[str(f) for f in self.files]}" if self.files else ""
        # Serialize file_hashes as TOML inline table
        if self.file_hashes:
            items = ", ".join(f'"{k}" = "{v}"' for k, v in self.file_hashes.items())
            file_hashes_toml = f"file_hashes = {{ {items} }}"
        else:
            file_hashes_toml = ""
        # Serialize metadata as TOML inline table if present
        if self.metadata:
            metadata_toml = _format_metadata_toml(self.metadata)
        else:
            metadata_toml = ""
        # Serialize call_id only if present (avoid serializing "None" as string)
        call_id_toml = f'call_id = "{self.call_id}"' if self.call_id else ""
        extra = (
            flags_toml
            + "\n"
            + files_toml
            + "\n"
            + file_hashes_toml
            + "\n"
            + metadata_toml
            + "\n"
            + call_id_toml
        ).strip()

        # doublequotes need to be escaped
        # content = self.content.replace('"', '\\"')
        content = escape_string(self.content)
        content = content.replace("\\n", "\n")
        # Don't strip - preserve whitespace for data integrity

        return f'''[message]
role = "{self.role}"
content = """
{content}
"""
timestamp = "{self.timestamp.isoformat()}"
{extra}
'''

    @classmethod
    def from_toml(cls, toml: str) -> Self:
        """
        Converts a TOML string to a message.

        The string can be a single [[message]].
        """

        t = tomlkit.parse(toml)
        assert "message" in t and isinstance(t["message"], dict)
        msg: dict = t["message"]  # type: ignore

        # Parse metadata if present
        metadata: MessageMetadata | None = None
        if "metadata" in msg and msg["metadata"]:
            metadata = MessageMetadata(**msg["metadata"])

        return cls(
            msg["role"],
            _fix_toml_content(msg["content"]),
            pinned=msg.get("pinned", False),
            hide=msg.get("hide", False),
            files=[Path(f) for f in msg.get("files", [])],
            file_hashes=msg.get("file_hashes", {}),
            timestamp=isoparse(msg["timestamp"]),
            call_id=msg.get("call_id", None),
            metadata=metadata,
        )

    def get_codeblocks(self) -> list[Codeblock]:
        """
        Get all codeblocks from the message content.
        """
        content_str = self.content

        # prepend newline to make sure we get the first codeblock
        if not content_str.startswith("\n"):
            content_str = "\n" + content_str

        # check if message contains a code block
        backtick_count = content_str.count("\n```")
        if backtick_count < 2:
            return []

        return Codeblock.iter_from_markdown(content_str)

    def cost(self, model: str | None = None, output=False) -> float:
        """Get the input cost of the message in USD."""
        from .llm.models import get_model  # noreorder

        m = get_model(model) if model else get_default_model()
        assert m, "No model specified or loaded"
        tok = len_tokens(self, f"{m.provider}/{m.model}")
        price = (m.price_output if output else m.price_input) / 1_000_000
        return tok * price


def format_msgs(
    msgs: list[Message],
    oneline: bool = False,
    highlight: bool = False,
    indent: int = 0,
) -> list[str]:
    """Formats messages for printing to the console."""
    outputs = []
    for msg in msgs:
        userprefix = msg.role.capitalize()
        if highlight:
            color = ROLE_COLOR[msg.role]
            userprefix = f"[bold {color}]{userprefix}[/bold {color}]"

        # get terminal width
        max_len = shutil.get_terminal_size().columns - len(userprefix)
        output = ""
        if oneline:
            content = msg.content.replace("\n", "\\n")
            if highlight:
                content = escape_markup(content)
            output += textwrap.shorten(content, width=max_len, placeholder="...")
            if len(output) < 20:
                output = content[:max_len] + "..."
        else:
            multiline = len(msg.content.split("\n")) > 1
            output += "\n" + indent * " " if multiline else ""
            for i, block in enumerate(msg.content.split("```")):
                if i % 2 == 0:
                    # Escape Rich markup in non-code-block content
                    if highlight:
                        block = escape_markup(block)
                    output += textwrap.indent(block, prefix=indent * " ")
                    continue
                elif highlight:
                    lang = block.split("\n", 1)[0]
                    content = block.split("\n", 1)[-1]
                    fmt = "underline blue"
                    block = f"[{fmt}]{lang}\n[/{fmt}]" + rich_to_str(
                        Syntax(
                            content.rstrip().replace("[", r"\["),
                            lang,
                        )
                    )
                output += f"```{block.rstrip()}\n```"

        status_emoji = ""
        if msg.role == "system":
            first_line = msg.content.split("\n", 1)[0].lower()
            first_three_words = first_line.split()[:3]
            isSuccess = first_line.startswith(("saved", "appended")) or any(
                word in ["success", "successfully"] for word in first_three_words
            )
            isError = first_line.startswith(("error", "failed"))
            if isSuccess:
                status_emoji = "✅ "
            elif isError:
                status_emoji = "❌ "

        outputs.append(f"{userprefix}: {status_emoji}{output.rstrip()}")
    return outputs


def print_msg(
    msg: Message | list[Message],
    oneline: bool = False,
    highlight: bool = True,
    show_hidden: bool = False,
) -> None:
    """Prints the log to the console."""
    # if not tty, force highlight=False (for tests and such)
    if not sys.stdout.isatty():
        highlight = False

    msgs = msg if isinstance(msg, list) else [msg]
    msgstrs = format_msgs(msgs, highlight=highlight, oneline=oneline)
    skipped_hidden = 0
    for m, s in zip(msgs, msgstrs):
        if m.hide and not show_hidden:
            skipped_hidden += 1
            continue
        try:
            console.print(s)
        except Exception:
            # rich can throw errors, if so then print the raw message
            logger.exception("Error printing message")
            print(s)
    if skipped_hidden:
        console.print(
            f"[grey30]Skipped {skipped_hidden} hidden system messages, show with --show-hidden[/]"
        )


def msgs_to_toml(msgs: list[Message]) -> str:
    """Converts a list of messages to a TOML string, for easy editing by hand in editor to then be parsed back."""
    t = ""
    for msg in msgs:
        t += msg.to_toml().replace("[message]", "[[messages]]") + "\n\n"

    return t


def _fix_toml_content(content: str) -> str:
    """
    Remove exactly one trailing newline that TOML multiline format adds.

    TOML multiline strings (using triple quotes) add a newline before the
    closing delimiter. This function removes that artifact while preserving
    all other whitespace.
    """
    if content.endswith("\n"):
        content = content[:-1]
    return content


def toml_to_msgs(toml: str) -> list[Message]:
    """
    Converts a TOML string to a list of messages.

    The string can be a whole file with multiple [[messages]].
    """
    t = tomlkit.parse(toml)
    assert "messages" in t and isinstance(t["messages"], list)
    msgs: list[dict] = t["messages"]  # type: ignore

    return [
        Message(
            msg["role"],
            _fix_toml_content(msg["content"]),
            pinned=msg.get("pinned", False),
            hide=msg.get("hide", False),
            timestamp=isoparse(msg["timestamp"]),
            metadata=MessageMetadata(**msg["metadata"])
            if msg.get("metadata")
            else None,
        )
        for msg in msgs
    ]


def msgs2dicts(msgs: list[Message]) -> list[dict]:
    """Convert a list of Message objects to a list of dicts ready to pass to an LLM."""
    return [msg.to_dict(keys=["role", "content", "files", "call_id"]) for msg in msgs]
