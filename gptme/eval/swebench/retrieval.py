"""File retrieval utilities for SWE-bench evaluation.

Two-stage retrieval approach:
  Stage 1 (Localize): Extract keywords from problem statement, grep for relevant files.
  Stage 2 (Embed): Read top-N file contents and embed them directly in the prompt.

This is fundamentally different from prompt-constraint approaches (sessions 0ff2, 13bf):
- Constraint approaches tell the agent WHICH files to edit → agent still explores, ignores.
- Embedded-content approach GIVES the agent the code directly → no exploration needed.

Research context: knowledge/research/2026-07-10-swebench-retrieval-experiment.md
"""

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Files/paths to exclude from retrieval results — the agent kept "fixing" these
_EXCLUDE_PATTERNS = (
    "docs/",
    "migrations/",
    ".egg-info/",
    "dist/",
    "build/",
)


def extract_keywords(problem_statement: str) -> list[str]:
    """Extract identifiers from a problem statement for keyword-grep retrieval.

    Extracts CamelCase class names, snake_case function names, and dotted module
    paths. These are the most discriminating signals for file localization.
    """
    camel = re.findall(r"\b[A-Z][a-zA-Z0-9]{4,}\b", problem_statement)
    snake = re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b", problem_statement)
    dotted = re.findall(
        r"\b[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]+){2,}\b", problem_statement
    )

    stopwords = {
        "This",
        "The",
        "When",
        "That",
        "There",
        "What",
        "From",
        "With",
        "Django",
        "Python",
        "Error",
        "True",
        "False",
        "None",
        "Model",
        "Value",
        "Field",
        "Method",
        "Class",
        "Object",
        "String",
        "Integer",
        "Using",
        "Should",
        "Could",
        "Would",
        "Raise",
        "Return",
        "Since",
        "However",
        "Therefore",
        "Description",
        "Given",
    }
    filtered_camel = [k for k in camel if k not in stopwords]
    return list(dict.fromkeys(filtered_camel + snake + dotted))[:12]


def grep_candidate_files(workspace_dir: Path, keywords: list[str]) -> dict[str, int]:
    """Grep for Python files containing any of the keywords.

    Returns a dict of {filepath: hit_count}, where hit_count is the number of
    distinct keywords found in the file.
    """
    file_scores: dict[str, int] = {}
    for kw in keywords:
        try:
            result = subprocess.run(
                ["grep", "-rln", "--include=*.py", kw, "."],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            for line in result.stdout.strip().split("\n"):
                line = line.strip().lstrip("./")
                if line and not line.startswith("Binary"):
                    file_scores[line] = file_scores.get(line, 0) + 1
        except subprocess.TimeoutExpired:
            logger.warning(f"grep timed out for keyword: {kw!r}")

    return file_scores


def rank_source_files(
    file_scores: dict[str, int],
    top_n: int = 5,
    include_tests: bool = False,
) -> list[tuple[str, int]]:
    """Rank files by keyword hit count, filtering to source files.

    By default excludes test files and migrations. The agent kept "fixing"
    docs/_theme/ symlinks in prior sessions — _EXCLUDE_PATTERNS blocks those.
    Uses substring match (not startswith) so nested paths like
    django/contrib/auth/migrations/ are caught by the "migrations/" pattern.
    """
    filtered = []
    for path, score in file_scores.items():
        if any(p in path for p in _EXCLUDE_PATTERNS):
            continue
        if not include_tests and ("test_" in path or "/tests/" in path):
            continue
        filtered.append((path, score))

    return sorted(filtered, key=lambda x: x[1], reverse=True)[:top_n]


def build_embedded_content_prompt(
    problem_statement: str,
    candidate_files: list[tuple[str, int]],
    workspace_dir: Path,
    max_file_bytes: int = 8000,
) -> str:
    """Build a two-stage prompt with embedded file contents.

    Stage 2 of the retrieval pipeline: embed the actual file contents in the
    prompt so the agent can fix the bug without any exploration phase.

    This is the key insight from the Agentless approach:
    > "Constrained patch: embed the file contents directly in the prompt, ask for
    > a targeted edit. By embedding file contents, the agent sees the specific code
    > immediately, without an exploration phase."

    Args:
        problem_statement: The original SWE-bench problem statement.
        candidate_files: Ranked list of (filepath, score) from rank_source_files().
        workspace_dir: The agent's workspace directory containing the repo.
        max_file_bytes: Truncate files larger than this to stay within context limits.

    Returns:
        A prompt string with embedded file contents and a constrained fix instruction.
        Falls back to the plain problem_statement if no candidate files are found
        or readable.
    """
    if not candidate_files:
        logger.info("No candidate files — falling back to plain problem statement")
        return problem_statement

    embedded_files = []
    for filepath, score in candidate_files:
        full_path = workspace_dir / filepath
        if not full_path.exists():
            logger.warning(f"Candidate file not found in workspace: {filepath}")
            continue
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning(f"Could not read {filepath}: {e}")
            continue

        if len(content.encode()) > max_file_bytes:
            # Truncate large files — first N bytes usually contain the relevant code
            content = (
                content[: max_file_bytes // 2] + "\n... [truncated for context] ..."
            )

        ext = full_path.suffix.lstrip(".")
        embedded_files.append(
            f"### `{filepath}` (relevance score: {score})\n```{ext or 'python'}\n{content}\n```"
        )

    if not embedded_files:
        logger.info(
            "No readable candidate files — falling back to plain problem statement"
        )
        return problem_statement

    files_section = "\n\n".join(embedded_files)
    file_list = "\n".join(
        f"- `{path}`" for path, _ in candidate_files if (workspace_dir / path).exists()
    )

    return f"""You are fixing a bug in a Python codebase. The relevant source files are provided below.

**INSTRUCTIONS — READ CAREFULLY:**
1. Study the issue description to understand what bug needs to be fixed.
2. Read the provided source files below.
3. Apply the MINIMAL fix to resolve the issue described.
4. ONLY modify the files listed below. Do NOT explore or modify other files.
5. Do NOT fix unrelated issues you notice (symlinks, formatting, style, etc.).
6. After making your changes, run `git diff` to verify only the expected files changed.

**Files you are allowed to modify:**
{file_list}

---

## Relevant Source Files

{files_section}

---

## Issue to fix

{problem_statement}"""


def retrieve_files_for_prompt(
    workspace_dir: Path,
    problem_statement: str,
    top_n: int = 3,
    include_tests: bool = False,
    max_file_bytes: int = 8000,
) -> str:
    """End-to-end two-stage retrieval: localize → embed → return prompt.

    Stage 1 (Localize): Extract keywords, grep for relevant files, rank by hits.
    Stage 2 (Embed): Read top-N file contents, embed in constrained prompt.

    Returns the embedded-content prompt, or the plain problem_statement on failure.
    """
    keywords = extract_keywords(problem_statement)
    if not keywords:
        logger.info("No keywords extracted — using plain problem statement")
        return problem_statement

    logger.info(f"Extracted keywords: {keywords}")

    file_scores = grep_candidate_files(workspace_dir, keywords)
    if not file_scores:
        logger.info("No files found via grep — using plain problem statement")
        return problem_statement

    ranked = rank_source_files(file_scores, top_n=top_n, include_tests=include_tests)
    logger.info(f"Ranked candidate files: {ranked}")

    prompt = build_embedded_content_prompt(
        problem_statement, ranked, workspace_dir, max_file_bytes=max_file_bytes
    )
    logger.info(
        f"Built embedded-content prompt ({len(prompt)} chars) "
        f"with {len(ranked)} embedded files"
    )
    return prompt
