"""
Read files and display their contents with git information.
"""

import glob
import re
import subprocess
from collections.abc import Generator
from pathlib import Path

from ..message import Message
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)


def execute_read(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Read files or globs and show their contents with git info."""
    # Handle different input formats
    if code is not None and code.strip():
        paths = [p.strip() for p in code.strip().split("\n") if p.strip()]
    elif args is not None and args:
        paths = args
    elif kwargs is not None:
        paths_str = kwargs.get("paths", "")
        paths = [p.strip() for p in paths_str.split("\n") if p.strip()]
    else:
        yield Message("system", "No paths provided")
        return

    if not paths:
        yield Message("system", "No paths provided")
        return

    # Get character budget (default ~2500 tokens = 10000 chars)
    max_chars = 10000
    if kwargs is not None:
        max_chars = int(kwargs.get("max_chars", "10000"))

    # Collect all files first
    all_files = []
    for path_str in paths:
        path, start_line, end_line = parse_line_range(path_str)

        if "*" in str(path) or "?" in str(path):
            matched_files = glob.glob(str(path), recursive=True)
            if not matched_files:
                yield Message("system", f"No files found matching pattern: {path}")
                continue
            for file_path_str in sorted(matched_files):
                file_path_obj = Path(file_path_str)
                if file_path_obj.is_file() and not should_ignore_file(file_path_obj):
                    all_files.append((file_path_obj, start_line, end_line))
        else:
            path = path.expanduser()
            if (path.is_file() or path.is_dir()) and not should_ignore_file(path):
                all_files.append((path, start_line, end_line))

    if not all_files:
        yield Message("system", "No readable files found (after filtering)")
        return

    # Calculate budget per file
    estimated_overhead = 200  # for headers, git info, etc.
    available_chars = max_chars - (len(all_files) * estimated_overhead)
    chars_per_file = (
        max(500, available_chars // len(all_files)) if available_chars > 0 else 500
    )

    files_processed = 0
    files_truncated = 0
    total_chars = 0

    # Process files with budget awareness and collect all outputs
    all_outputs = []
    for path_str in paths:
        path, start_line, end_line = parse_line_range(path_str)

        # Find matching files/directories for this path_str
        matching_files: list[tuple[Path, int | None, int | None, str]] = []
        if "*" in str(path) or "?" in str(path):
            matched_files = glob.glob(str(path), recursive=True)
            for file_path in sorted(matched_files):
                file_path_obj = Path(file_path)
                if (
                    file_path_obj.is_file() or file_path_obj.is_dir()
                ) and not should_ignore_file(file_path_obj):
                    matching_files.append(
                        (file_path_obj, start_line, end_line, path_str)
                    )
        else:
            path = path.expanduser()
            if (path.is_file() or path.is_dir()) and not should_ignore_file(path):
                matching_files.append((path, start_line, end_line, path_str))

        # Process each matching file
        for read_file_path, start_line, end_line, original_query in matching_files:
            file_output = process_single_file_with_budget(
                read_file_path, start_line, end_line, chars_per_file, original_query
            )
            if file_output:
                all_outputs.append(file_output)
                files_processed += 1
                total_chars += len(file_output)
                if "truncated" in file_output:
                    files_truncated += 1

    # Combine all outputs into a single message
    if all_outputs:
        combined_output = "\n\n".join(all_outputs)

        # Add summary if files were truncated or filtered
        if files_truncated > 0:
            combined_output += f"\n\n**Budget Summary**: Processed {files_processed} files ({files_truncated} truncated) using {total_chars}/{max_chars} character budget."

        yield Message("system", combined_output)
    else:
        yield Message("system", "No readable files found (after filtering)")


def parse_line_range(path_str: str) -> tuple[Path, int | None, int | None]:
    """Parse file path with optional line range.

    Formats supported:
    - file.py:10-20  (lines 10 to 20)
    - file.py:10     (just line 10)
    - file.py:10:    (from line 10 to end)
    - file.py::20    (from start to line 20)
    - file.py        (entire file)

    Returns: (path, start_line, end_line)
    """
    # Use regex to match line range patterns at the end of the string
    line_range_patterns = [
        r"(.+):(\d+)-(\d+)$",  # file.py:10-20
        r"(.+):(\d+):$",  # file.py:10:
        r"(.+)::(\d+)$",  # file.py::20
        r"(.+):(\d+)$",  # file.py:10
    ]

    for pattern in line_range_patterns:
        match = re.match(pattern, path_str)
        if match:
            if len(match.groups()) == 3:  # file.py:10-20
                file_part, start_str, end_str = match.groups()
                return Path(file_part), int(start_str), int(end_str)
            elif pattern.endswith(":$"):  # file.py:10:
                file_part, start_str = match.groups()
                return Path(file_part), int(start_str), None
            elif pattern.startswith(r"(.+)::"):  # file.py::20
                file_part, end_str = match.groups()
                return Path(file_part), None, int(end_str)
            else:  # file.py:10
                file_part, line_str = match.groups()
                line_num = int(line_str)
                return Path(file_part), line_num, line_num

    # No line range found, return entire file
    return Path(path_str), None, None


def process_single_file(
    path: Path, start_line: int | None = None, end_line: int | None = None
) -> Generator[Message, None, None]:
    """Process a single file and yield its content with git info."""
    result = process_single_file_with_budget(path, start_line, end_line, None)
    if result:
        yield Message("system", result)


def process_single_file_with_budget(
    path: Path,
    start_line: int | None = None,
    end_line: int | None = None,
    max_content_chars: int | None = None,
    original_query: str | None = None,
) -> str | None:
    """Process a single file with budget constraints and return formatted content."""
    try:
        if not path.exists():
            return f"File not found: {path}"

        if path.is_dir():
            return process_directory(path, original_query, max_content_chars)

        # Get git info
        git_info = get_git_info(path)

        # Use original query as language tag, fallback to filename
        lang_tag = original_query if original_query else str(path)

        # Read file content
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            return f"File is not text: {path}"

        # Extract specific lines if requested
        lines = content.splitlines()
        if start_line is not None or end_line is not None:
            # Convert to 0-based indexing
            start_idx = (start_line - 1) if start_line else 0
            end_idx = end_line if end_line else len(lines)

            # Validate line numbers
            if start_idx < 0:
                start_idx = 0
            if end_idx > len(lines):
                end_idx = len(lines)

            selected_lines = lines[start_idx:end_idx]

            # Add line numbers for line range output
            if start_line is not None or end_line is not None:
                numbered_lines = []
                for i, line in enumerate(selected_lines, start_idx + 1):
                    numbered_lines.append(f"{i:4d}: {line}")
                content = "\n".join(numbered_lines)
            else:
                content = "\n".join(selected_lines)

            # Update display path to show line range
            if start_line and end_line and start_line == end_line:
                display_path = f"{path}:{start_line}"
            elif start_line and end_line:
                display_path = f"{path}:{start_line}-{end_line}"
            elif start_line:
                display_path = f"{path}:{start_line}:"
            elif end_line:
                display_path = f"{path}::{end_line}"
            else:
                display_path = str(path)
        else:
            display_path = str(path)

        # Apply budget constraints if specified
        if max_content_chars is not None and len(content) > max_content_chars:
            content = truncate_content(content, max_content_chars)

        # Format output with quadruple backticks and file query as language tag
        output = f"## {display_path}\n\n"
        if git_info:
            output += f"**Git info:** {git_info}\n\n"

        output += f"````{lang_tag}\n{content}\n````"

        return output

    except Exception as e:
        return f"Error reading {path}: {e}"


def get_git_info(path: Path) -> str | None:
    """Get git information for a file."""
    try:
        # Get the most recent commit that modified this file
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h %s (%cr)", "--", str(path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return None
    except Exception:
        return None


def get_language_from_suffix(suffix: str) -> str:
    """Get language tag from file suffix."""
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".html": "html",
        ".css": "css",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".sh": "bash",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".java": "java",
        ".sql": "sql",
        ".txt": "text",
        ".toml": "toml",
        ".ini": "ini",
        ".cfg": "ini",
        ".conf": "ini",
    }
    return lang_map.get(suffix, "text")


def should_ignore_file(path: Path) -> bool:
    """Check if file should be ignored based on common patterns."""
    ignore_patterns = [
        "__pycache__",
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".DS_Store",
        "*.pyc",
        "*.pyo",
        "*.pyd",
        "*.so",
        "*.dll",
        "*.exe",
        "*.bin",
        "*.obj",
        "*.class",
        "*.jar",
        "*.war",
        ".env",
        "*.log",
        "*.tmp",
        "*.temp",
        "coverage.xml",
        ".coverage",
        "*.sqlite",
        "*.db",
    ]

    path_str = str(path)

    for pattern in ignore_patterns:
        if pattern in path_str or path.match(pattern):
            return True

    # Skip very large files (>1MB) - but not directories
    try:
        if path.exists() and path.is_file() and path.stat().st_size > 1024 * 1024:
            return True
    except (OSError, PermissionError):
        # Only filter out if we can't access it and it's a file
        if path.is_file():
            return True

    return False


def truncate_content(content: str, max_chars: int) -> str:
    """Truncate content intelligently, showing beginning and end."""
    if len(content) <= max_chars:
        return content

    if max_chars < 100:
        return content[:max_chars] + "\n... (truncated)"

    # Show beginning and end, similar to shell tool logic
    prefix_chars = max_chars // 2
    suffix_chars = max_chars - prefix_chars - 50  # leave room for truncation message

    lines = content.splitlines()
    prefix_lines: list[str] = []
    suffix_lines: list[str] = []

    # Collect prefix lines
    current_chars = 0
    for line in lines:
        if current_chars + len(line) + 1 > prefix_chars:
            break
        prefix_lines.append(line)
        current_chars += len(line) + 1

    # Collect suffix lines
    current_chars = 0
    for line in reversed(lines):
        if current_chars + len(line) + 1 > suffix_chars:
            break
        suffix_lines.insert(0, line)
        current_chars += len(line) + 1

    truncated_lines = len(lines) - len(prefix_lines) - len(suffix_lines)
    truncation_msg = f"\n... ({truncated_lines} lines truncated) ...\n"

    return "\n".join(prefix_lines) + truncation_msg + "\n".join(suffix_lines)


def get_git_commits_for_directory(path: Path, count: int = 3) -> str | None:
    """Get the last N git commits that affected files in this directory."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{count}", "--format=%h %s (%cr)", "--", str(path)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return None
    except Exception:
        return None


def list_directory_contents(path: Path) -> tuple[list[Path], list[Path]]:
    """List directory contents, separating files and directories, with filtering."""
    try:
        all_items = list(path.iterdir())
        files = []
        directories = []

        for item in sorted(all_items):
            if not should_ignore_file(item):
                if item.is_file():
                    files.append(item)
                elif item.is_dir():
                    directories.append(item)

        return files, directories
    except (OSError, PermissionError):
        return [], []


def process_directory(
    path: Path, original_query: str | None = None, max_content_chars: int | None = None
) -> str:
    """Process a directory and return formatted listing with git history."""
    # Get git commits for this directory
    git_commits = get_git_commits_for_directory(path)

    # List directory contents
    files, directories = list_directory_contents(path)

    # Use original query as language tag, fallback to directory path
    lang_tag = original_query if original_query else str(path)

    # Build directory listing content
    content_lines = []

    if directories:
        content_lines.append("📁 Directories:")
        for directory in directories:
            content_lines.append(f"  {directory.name}/")
        content_lines.append("")

    if files:
        content_lines.append("📄 Files:")
        for file in files:
            # Get file size
            try:
                size = file.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size//1024}KB"
                else:
                    size_str = f"{size//(1024*1024)}MB"
            except (OSError, PermissionError):
                size_str = "?"

            content_lines.append(f"  {file.name} ({size_str})")

    if not directories and not files:
        content_lines.append("(empty directory)")

    content = "\n".join(content_lines)

    # Apply budget constraints if specified
    if max_content_chars is not None and len(content) > max_content_chars:
        content = truncate_content(content, max_content_chars)

    # Format output
    output = f"## {path}\n\n"

    if git_commits:
        output += f"**Recent commits (last 3):**\n```\n{git_commits}\n```\n\n"

    output += f"````{lang_tag}\n{content}\n````"

    return output


instructions = """
Read and display the contents of files or directories with git information.

Takes a list of file paths, directory paths, or glob patterns (one per line).

For files, shows:
- File path
- Most recent git commit info (if available)
- File contents with syntax highlighting

For directories, shows:
- Directory path
- Last 3 git commits affecting that directory
- Directory contents (files and subdirectories) with file sizes

Features:
- Character budget (default 10000 chars ~2500 tokens)
- Automatic filtering of __pycache__, .git, node_modules, etc.
- Adaptive truncation to fit more files in budget
- Glob patterns like *.py, **/*.md, etc.

Line range syntax:
- file.py:10-20  (lines 10 to 20)
- file.py:10     (just line 10)
- file.py:10:    (from line 10 to end)
- file.py::20    (from start to line 20)
- file.py        (entire file)
- directory/     (list directory contents)
""".strip()


def examples(tool_format):
    return f"""
> User: read main.py config.json
> Assistant:
{ToolUse("read", [], "main.py\nconfig.json").to_output(tool_format)}
> System: ## main.py

**Git info:** abc1234 feat: add main function (2 days ago)

```python
def main():
    print("Hello, world!")

if __name__ == "__main__":
    main()
```

## config.json

**Git info:** def5678 chore: update config (1 week ago)

```json
{{
  "name": "example",
  "version": "1.0.0"
}}
```

> User: read lines 10-20 of main.py
> Assistant:
{ToolUse("read", [], "main.py:10-20").to_output(tool_format)}
> System: ## main.py:10-20

**Git info:** abc1234 feat: add main function (2 days ago)

```python
  10: def helper_function():
  11:     return "helper"
  12:
  13: def another_function():
  14:     print("Another function")
  15:     return True
  16:
  17: if __name__ == "__main__":
  18:     main()
  19:     helper_function()
  20:     another_function()
```

> User: read just line 5 of config.py
> Assistant:
{ToolUse("read", [], "config.py:5").to_output(tool_format)}
> System: ## config.py:5

**Git info:** def5678 chore: update config (1 week ago)

```python
   5:     "debug": True
```

> User: read all python files
> Assistant:
{ToolUse("read", [], "*.py").to_output(tool_format)}
> System: ## script.py

**Git info:** ghi9012 fix: update script (3 days ago)

```python
import os
print("Script running")
```
""".strip()


tool = ToolSpec(
    name="read",
    desc="Read and display file contents with git information",
    instructions=instructions,
    examples=examples,
    execute=execute_read,
    block_types=["read"],
    parameters=[
        Parameter(
            name="paths",
            type="string",
            description="File paths or glob patterns to read (one per line)",
            required=True,
        ),
        Parameter(
            name="max_chars",
            type="string",
            description="Maximum characters for all files combined (default: 10000)",
            required=False,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
