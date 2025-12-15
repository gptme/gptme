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
    """Metadata from lesson frontmatter.

    Supports:
    - Lessons: keywords, tools, status
    - Skills (Anthropic format): name, description
    - Cursor rules (.mdc): globs, priority, triggers, alwaysApply
    """

    # Anthropic skill format fields
    name: str | None = None
    description: str | None = None

    # Lesson format fields
    keywords: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    status: str = "active"  # active, automated, deprecated, or archived

    # Future extensions:
    # globs: list[str] = field(default_factory=list)
    # semantic: list[str] = field(default_factory=list)

    # Cursor .mdc format fields (Issue #686 Phase 5)
    globs: list[str] = field(default_factory=list)
    """File path patterns for Cursor rules (e.g., '**/*.py')"""

    priority: str | None = None
    """Priority level: high, medium, low"""

    triggers: list[str] = field(default_factory=list)
    """Action triggers for Cursor rules (e.g., file_change)"""

    always_apply: bool = False
    """Whether rule should always be applied (Cursor format)"""

    version: str | None = None
    """Version tracking for rules"""


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


def _glob_to_keywords(glob_pattern: str) -> list[str]:
    """Convert a file glob pattern to likely keywords.

    Args:
        glob_pattern: File pattern like '**/*.py' or 'src/api/**/*.ts'

    Returns:
        List of inferred keywords

    Examples:
        '**/*.py' -> ['python', 'python code']
        '**/*.ts' -> ['typescript', 'typescript code']
        'src/api/**/*.js' -> ['javascript', 'api', 'backend']
    """
    keywords = []

    # Extension mapping
    ext_map = {
        ".py": ["python", "python code"],
        ".ts": ["typescript", "typescript code"],
        ".tsx": ["typescript", "react", "frontend"],
        ".js": ["javascript", "javascript code"],
        ".jsx": ["javascript", "react", "frontend"],
        ".java": ["java", "java code"],
        ".cpp": ["cpp", "c++", "c++ code"],
        ".c": ["c", "c code"],
        ".rs": ["rust", "rust code"],
        ".go": ["go", "golang", "go code"],
        ".rb": ["ruby", "ruby code"],
        ".php": ["php", "php code"],
        ".swift": ["swift", "swift code"],
        ".kt": ["kotlin", "kotlin code"],
        ".cs": ["csharp", "c#", "c# code"],
        ".sql": ["sql", "database"],
        ".md": ["markdown", "documentation"],
        ".yaml": ["yaml", "configuration"],
        ".yml": ["yaml", "configuration"],
        ".json": ["json", "configuration"],
        ".toml": ["toml", "configuration"],
        ".xml": ["xml", "configuration"],
        ".html": ["html", "frontend", "web"],
        ".css": ["css", "frontend", "styling"],
        ".scss": ["scss", "css", "styling"],
        ".vue": ["vue", "frontend", "javascript"],
        ".sh": ["shell", "bash", "script"],
    }

    # Extract file extension
    import re

    ext_match = re.search(r"\*\.(\w+)", glob_pattern)
    if ext_match:
        ext = f".{ext_match.group(1)}"
        keywords.extend(ext_map.get(ext, [ext_match.group(1)]))

    # Extract directory names for context
    # e.g., 'src/api/**/*.js' -> add 'api'
    parts = glob_pattern.split("/")
    for part in parts:
        if part and part not in ("*", "**", ".", ".."):
            # Common directory names that indicate context
            if part in [
                "api",
                "backend",
                "frontend",
                "tests",
                "test",
                "docs",
                "documentation",
                "config",
                "lib",
                "src",
            ]:
                keywords.append(part)

    return list(dict.fromkeys(keywords))  # Remove duplicates while preserving order


def _translate_cursor_metadata(frontmatter: dict) -> LessonMetadata:
    """Translate Cursor .mdc frontmatter to gptme LessonMetadata.

    Args:
        frontmatter: Parsed YAML frontmatter from .mdc file

    Returns:
        Translated LessonMetadata
    """
    # Start with basic fields
    name = frontmatter.get("name")
    description = frontmatter.get("description")

    # Translate globs to keywords
    # Note: YAML parses empty values (e.g., "globs:") as None, not missing
    globs = frontmatter.get("globs") or []
    keywords = []
    for glob in globs:
        keywords.extend(_glob_to_keywords(glob))

    # If alwaysApply is true, add high-frequency keywords
    always_apply = frontmatter.get("alwaysApply", False)
    if always_apply:
        # Add generic keywords that are likely to match frequently
        keywords.extend(["code", "development", "project"])

    # Map priority to status or store separately
    priority = frontmatter.get("priority")
    status = "active"  # Default status

    # Extract other Cursor-specific fields
    # Note: YAML parses empty values as None, not missing
    triggers = frontmatter.get("triggers") or []
    version = frontmatter.get("version")

    return LessonMetadata(
        name=name,
        description=description,
        keywords=list(dict.fromkeys(keywords)),  # Remove duplicates
        status=status,
        globs=globs,
        priority=priority,
        triggers=triggers,
        always_apply=always_apply,
        version=version,
    )


def _fix_unquoted_globs(frontmatter_str: str) -> str:
    """Fix unquoted glob patterns that would be interpreted as YAML aliases.

    Cursor .mdc files often have globs like `globs: *,**/*` which YAML
    interprets as an alias reference (starting with *). This function
    quotes such values.

    Args:
        frontmatter_str: Raw YAML frontmatter string

    Returns:
        Fixed frontmatter string with problematic values quoted
    """
    lines = frontmatter_str.split("\n")
    fixed_lines = []

    for line in lines:
        # Match lines like "globs: *,**/*" or "globs: **/*.py"
        # where the value starts with * but isn't already quoted
        match = re.match(r"^(\s*globs:\s*)(\*[^\"']+)$", line)
        if match:
            prefix, value = match.groups()
            # Quote the value
            line = f'{prefix}"{value}"'
        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def parse_lesson(path: Path) -> Lesson:
    """Parse lesson or skill file with optional YAML frontmatter.

    Args:
        path: Path to lesson/skill markdown file

    Returns:
        Parsed lesson with metadata and content

    Supports three formats:

    Lesson format:
        ---
        match:
          keywords: [patch, file, editing]
        status: active
        ---
        # Lesson Title
        First paragraph becomes description...

    Skill format (Anthropic):
        ---
        name: skill-name
        description: Brief description of the skill
        ---
        # Skill Title
        Detailed instructions...

    Cursor rules format (.mdc):
        ---
        name: Rule Name
        description: Brief summary
        globs: ["**/*.py"]
        priority: high
        alwaysApply: true
        ---
        # Rule Title
        Detailed instructions...
    """
    if not path.exists():
        raise FileNotFoundError(f"Lesson file not found: {path}")

    content = path.read_text(encoding="utf-8")
    is_mdc = path.suffix == ".mdc"

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
                # Pre-process frontmatter to handle unquoted glob patterns
                # that look like YAML aliases (e.g., "globs: *,**/*")
                frontmatter_str = _fix_unquoted_globs(frontmatter_str)
                frontmatter = yaml.safe_load(frontmatter_str)
                if frontmatter:
                    # Detect format based on file extension and frontmatter structure
                    has_globs = "globs" in frontmatter

                    if is_mdc or has_globs:
                        # Cursor .mdc format - translate to gptme format
                        metadata = _translate_cursor_metadata(frontmatter)
                    else:
                        # Standard gptme lesson or Anthropic skill format
                        # Extract Anthropic skill format fields
                        name = frontmatter.get("name")
                        description = frontmatter.get("description")

                        # Extract lesson format fields
                        match_data = frontmatter.get("match", {})
                        status = frontmatter.get("status", "active")

                        metadata = LessonMetadata(
                            name=name,
                            description=description,
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
