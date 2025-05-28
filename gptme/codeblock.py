from collections.abc import Generator
from dataclasses import dataclass, field
from xml.etree import ElementTree

# Enable support for quadtick-style markdown code blocks
quadticks = True


@dataclass(frozen=True)
class Codeblock:
    lang: str
    content: str
    path: str | None = None
    start: int | None = field(default=None, compare=False)

    def __post_init__(self):
        # init path if path is None and lang is pathy
        if self.path is None and self.is_filename:
            object.__setattr__(self, "path", self.lang)  # frozen dataclass workaround

    def to_markdown(self) -> str:
        ticks = "````" if quadticks else "```"
        return f"{ticks}{self.lang}\n{self.content}\n{ticks}"

    def to_xml(self) -> str:
        return f'<codeblock lang="{self.lang}" path="{self.path}">\n{self.content}\n</codeblock>'

    @classmethod
    def from_markdown(cls, content: str) -> "Codeblock":
        content = content.strip()
        # Strip start and end backticks, handling both triple and quad
        for delim in ["````", "```"]:
            if content.startswith(delim):
                content = content[len(delim) :]
            if content.endswith(delim):
                content = content[: -len(delim)]
        lang = content.splitlines()[0].strip()
        return cls(lang, content[len(lang) :])

    @classmethod
    def from_xml(cls, content: str) -> "Codeblock":
        """
        Example:
          <codeblock lang="python" path="example.py">
          print("Hello, world!")
          </codeblock>
        """
        root = ElementTree.fromstring(content)
        return cls(root.attrib["lang"], root.text or "", root.attrib.get("path"))

    @property
    def is_filename(self) -> bool:
        return "." in self.lang or "/" in self.lang

    @classmethod
    def iter_from_markdown(cls, markdown: str) -> list["Codeblock"]:
        return list(_extract_codeblocks(markdown))


def _extract_codeblocks(markdown: str) -> Generator[Codeblock, None, None]:
    # speed check (early exit): check if message contains a code block

    # check quad ticks
    backtick_count = markdown.count("````") if quadticks else 0
    is_quadtick = quadticks
    if backtick_count == 0:
        is_quadtick = False
        # check triple ticks
        backtick_count = markdown.count("```")
        if backtick_count < 2:
            return
    elif backtick_count == 1:
        # no complete quadtick codeblock
        return

    lines = markdown.split("\n")
    stack: list[tuple[str, int]] = []
    current_block = []
    current_lang = ""

    for idx, line in enumerate(lines):
        # not actually the starting index, but close enough
        # TODO: fix to actually be correct
        start_idx = sum(len(line) + 1 for line in lines[:idx])
        stripped_line = line.strip()
        if (
            stripped_line.startswith("````")
            if is_quadtick
            else stripped_line.startswith("```")
        ):
            backtick_count = 4 if is_quadtick else 3
            if not stack:  # Start of a new block
                stack.append((stripped_line[backtick_count:], backtick_count))
                current_lang = stripped_line[backtick_count:]
            elif (
                stripped_line[backtick_count:]
                and stack[-1][0] != stripped_line[backtick_count:]
            ):  # Nested start
                current_block.append(line)
                stack.append((stripped_line[backtick_count:], backtick_count))
            else:  # End of a block
                if len(stack) == 1:  # Outermost block
                    yield Codeblock(
                        current_lang, "\n".join(current_block), start=start_idx
                    )
                    current_block = []
                    current_lang = ""
                else:  # Nested end
                    current_block.append(line)
                stack.pop()
        elif stack:
            current_block.append(line)
