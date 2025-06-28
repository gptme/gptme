from collections.abc import Generator
from dataclasses import dataclass, field
from xml.etree import ElementTree


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
        return f"```{self.lang}\n{self.content}\n```"

    def to_xml(self) -> str:
        return f'<codeblock lang="{self.lang}" path="{self.path}">\n{self.content}\n</codeblock>'

    @classmethod
    def from_markdown(cls, content: str) -> "Codeblock":
        if content.strip().startswith("```"):
            content = content[3:]
        if content.strip().endswith("```"):
            content = content[:-3]
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


import re

# valid start/end of markdown code blocks
re_triple_tick_start = re.compile(r"^```.*\n")
re_triple_tick_end = re.compile(r"^```$")


def _extract_codeblocks(markdown: str) -> Generator[Codeblock, None, None]:
    """
    Extracts code blocks from a markdown string.
    """
    lines = markdown.split("\n")
    in_codeblock = False
    current_codeblock_lines: list[str] = []
    lang = ""
    start_line = 0

    for i, line in enumerate(lines):
        if line.startswith("```"):
            if in_codeblock:
                # End of a code block
                yield Codeblock(lang, "\n".join(current_codeblock_lines), start=start_line)
                in_codeblock = False
                current_codeblock_lines = []
                lang = ""
            else:
                # Start of a code block
                in_codeblock = True
                start_line = i
                lang = line[3:].strip()
        elif in_codeblock:
            current_codeblock_lines.append(line)
    # If the markdown ends with an unclosed code block, yield it
    if in_codeblock:
        yield Codeblock(lang, "\n".join(current_codeblock_lines), start=start_line)
