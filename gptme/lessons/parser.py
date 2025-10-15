"""Lesson parser with YAML frontmatter support."""

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class LessonMetadata:
    """Metadata from lesson frontmatter."""

    keywords: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    status: str = "active"  # active, automated, deprecated, or archived
    # Future extensions:
    # globs: list[str] = field(default_factory=list)
    # semantic: list[str] = field(default_factory=list)


@dataclass
class Lesson:
    """Parsed lesson with metadata and content."""

    path: Path
    metadata: LessonMetadata
    title: str
    description: str
    category: str
    body: str


def _extract_title(content: str) -> str:
    """Extract title from first H1 heading."""
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else "Untitled"


def _extract_description(content: str) -> str:
    """Extract description from first paragraph after title."""
    lines = content.split("\n")

    # Skip to first heading
    in_content = False
    for _, line in enumerate(lines):
        if line.startswith("# "):
            in_content = True
            continue

        if in_content and line.strip() and not line.startswith("#"):
            # Found first non-empty, non-heading line
            return line.strip()

    return ""


def parse_lesson(path: Path) -> Lesson:
    """Parse lesson file with optional YAML frontmatter.

    Args:
        path: Path to lesson markdown file

    Returns:
        Parsed lesson with metadata and content

    Example lesson format:
        ---
        match:
          keywords: [patch, file, editing]
        ---

        # Lesson Title

        First paragraph becomes description...
    """
    if not path.exists():
        raise FileNotFoundError(f"Lesson file not found: {path}")

    content = path.read_text(encoding="utf-8")

    # Parse frontmatter if present
    metadata = LessonMetadata()
    body = content

    if content.startswith("---"):
        if not HAS_YAML:
            raise ImportError(
                "PyYAML is required for lesson frontmatter. "
                "Install with: pip install pyyaml"
            )

        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter_str = parts[1]
            body = parts[2].strip()

            try:
                frontmatter = yaml.safe_load(frontmatter_str)
                if frontmatter:
                    # Extract match section
                    match_data = frontmatter.get("match", {})
                    # Extract status (defaults to "active" if not present)
                    status = frontmatter.get("status", "active")
                    metadata = LessonMetadata(
                        keywords=match_data.get("keywords", []),
                        tools=match_data.get("tools", []),
                        status=status,
                    )
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML frontmatter in {path}: {e}") from e

    # Infer metadata from content and path
    title = _extract_title(body)
    description = _extract_description(body)
    category = path.parent.name  # e.g., "tools", "patterns"

    return Lesson(
        path=path,
        metadata=metadata,
        title=title,
        description=description,
        category=category,
        body=body,
    )
