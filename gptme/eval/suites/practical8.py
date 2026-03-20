"""Practical eval tests (batch 8) — URL parsing, markdown TOC, and JSON flattening.

Tests capabilities not covered by earlier practical suites:
- URL parsing with domain grouping and path statistics (urllib.parse + collections)
- Markdown table-of-contents generation with anchor links (regex + string formatting)
- Nested JSON flattening to dot-notation keys (recursion + dict manipulation)
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- url-stats checks ---


def check_url_stats_file(ctx):
    """url_stats.py should exist."""
    return "url_stats.py" in ctx.files


def check_url_stats_top_domain(ctx):
    """other.org should appear as the top domain (3 URLs).

    NOTE: other.org is alphabetically last but has the highest count — this ensures
    agents must implement count-descending sort, not naive alphabetical sort.
    """
    lines = ctx.stdout.strip().split("\n")
    # Find the first line that looks like a domain entry (skip preamble/headers)
    # Expected format: '<domain>: <count>'
    for line in lines:
        stripped = line.strip()
        # Anchor to '<domain>: <count>' format to avoid matching filenames like 'urls.txt:'
        if re.match(r"[\w.-]+\.[a-z]+:\s*\d+$", stripped):
            domain = stripped.split(":")[0].strip()
            return domain == "other.org"
    return False


def check_url_stats_count(ctx):
    """Top domain count should be 3."""
    # Require the expected 'domain: count' format to avoid matching preamble lines
    return bool(re.search(r"other\.org:\s*3\b", ctx.stdout))


def check_url_stats_docs_domain(ctx):
    """docs.example.com should appear with count 2."""
    return bool(re.search(r"docs\.example\.com:\s*2\b", ctx.stdout))


def check_url_stats_tiebreak_order(ctx):
    """other.org (count-3) first; docs.example.com (d<e) before example.com (both count-2); api.example.com (count-1) last."""
    lines = ctx.stdout.strip().split("\n")
    # Use strip().startswith to avoid matching docs.example.com when looking for example.com
    other_pos = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("other.org")), None
    )
    docs_pos = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("docs.example.com")),
        None,
    )
    example_pos = next(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("example.com")), None
    )
    if other_pos is None or docs_pos is None or example_pos is None:
        return False
    # other.org (count-3) before docs/example (count-2); docs before example (alpha tiebreak)
    return other_pos < docs_pos < example_pos


def check_url_stats_exit(ctx):
    return ctx.exit_code == 0


# --- markdown-toc checks ---


def check_toc_file(ctx):
    """gen_toc.py should exist."""
    return "gen_toc.py" in ctx.files


def check_toc_has_links(ctx):
    """Output should contain markdown links like [text](#anchor)."""
    return bool(re.search(r"\[.+\]\(#.+\)", ctx.stdout))


def check_toc_installation_heading(ctx):
    """Should contain a TOC link entry for 'Installation'."""
    return bool(re.search(r"\[Installation\]\(#installation\)", ctx.stdout))


def check_toc_h3_indented(ctx):
    """All 4 h3 entries should be present and indented relative to h2."""
    lines = ctx.stdout.strip().split("\n")
    # One line per h3 keyword must be found and indented (per-keyword deduplication)
    h3_keywords = ["prerequisite", "quick-start", "environment", "config-file"]
    for kw in h3_keywords:
        kw_lines = [line for line in lines if kw in line.lower().replace(" ", "-")]
        if not kw_lines or not kw_lines[0].startswith((" ", "\t")):
            return False
    return True


def check_toc_exit(ctx):
    return ctx.exit_code == 0


# --- json-flatten checks ---


def _parse_flatten_output(ctx) -> dict | None:
    """Parse flatten output as JSON dict, returning None on non-dict or parse failure."""
    try:
        result = json.loads(ctx.stdout.strip())
        return result if isinstance(result, dict) else None
    except ValueError:
        return None


def check_flatten_file(ctx):
    """flatten.py should exist."""
    return "flatten.py" in ctx.files


def check_flatten_valid_json(ctx):
    """Output should be valid JSON."""
    return _parse_flatten_output(ctx) is not None


def check_flatten_nested_key(ctx):
    """Nested key 'database.host' should appear in output."""
    data = _parse_flatten_output(ctx)
    return data is not None and "database.host" in data


def check_flatten_deep_key(ctx):
    """Deep nested key 'server.tls.cert_file' should appear in output."""
    data = _parse_flatten_output(ctx)
    return data is not None and "server.tls.cert_file" in data


def check_flatten_values_preserved(ctx):
    """Values should be preserved correctly (database.port == 5432)."""
    data = _parse_flatten_output(ctx)
    return data is not None and data.get("database.port") == 5432


def check_flatten_list_preserved(ctx):
    """List values should be preserved as-is (not further flattened)."""
    data = _parse_flatten_output(ctx)
    if data is None:
        return False
    # 'server.allowed_hosts' should be a list, not split by index
    val = data.get("server.allowed_hosts")
    return isinstance(val, list) and len(val) == 3


def check_flatten_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_URLS_TXT = """\
https://other.org/page1
https://other.org/page2
https://other.org/page3
https://docs.example.com/getting-started
https://docs.example.com/api-reference
https://example.com/blog/introducing-v2
https://example.com/about
https://api.example.com/v1/users
"""

_GUIDE_MD = """\
# User Guide

## Installation

Some installation text here.

### Prerequisites

You need Python 3.10+.

### Quick Start

Run the install command.

## Configuration

Configure your settings.

### Environment Variables

Set these env vars.

### Config File

Or use a config file.

## Usage

Basic usage examples.
"""

_NESTED_JSON = json.dumps(
    {
        "database": {
            "host": "localhost",
            "port": 5432,
            "credentials": {
                "user": "admin",
                "password": "secret",
            },
        },
        "server": {
            "host": "0.0.0.0",
            "port": 8080,
            "tls": {
                "enabled": True,
                "cert_file": "/etc/ssl/cert.pem",
                "key_file": "/etc/ssl/key.pem",
            },
            "allowed_hosts": ["localhost", "example.com", "*.example.com"],
        },
        "debug": False,
        "version": "2.0.0",
    },
    indent=2,
)

tests: list["EvalSpec"] = [
    {
        "name": "url-stats",
        "files": {"urls.txt": _URLS_TXT},
        "run": "python url_stats.py urls.txt",
        "prompt": (
            "Write url_stats.py that reads a file of URLs (one per line) and prints "
            "a domain frequency report. The script should accept the filename as a "
            "command-line argument.\n\n"
            "Use Python's urllib.parse module to extract domain names.\n\n"
            "Output format: list each domain and its URL count, sorted by count "
            "descending (then alphabetically for ties). Print one domain per line "
            "in the format: '<domain>: <count>'\n\n"
            "Skip empty lines. Use only the Python standard library."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "url_stats.py exists": check_url_stats_file,
            "top domain is other.org": check_url_stats_top_domain,
            "top domain count is 3": check_url_stats_count,
            "docs.example.com count is 2": check_url_stats_docs_domain,
            "tie-break: docs before example.com": check_url_stats_tiebreak_order,
            "clean exit": check_url_stats_exit,
        },
    },
    {
        "name": "markdown-toc",
        "files": {"guide.md": _GUIDE_MD},
        "run": "python gen_toc.py guide.md",
        "prompt": (
            "Write gen_toc.py that reads a Markdown file and prints a table of "
            "contents. The script should accept the filename as a command-line "
            "argument.\n\n"
            "Process all headings at level 2 (##) and level 3 (###). Skip h1 (#) "
            "headings.\n\n"
            "For each heading, generate a markdown link with a GitHub-style anchor:\n"
            "- Convert heading text to lowercase\n"
            "- Replace spaces with hyphens\n"
            "- Remove non-alphanumeric characters except hyphens\n\n"
            "Indent h3 entries with 2 spaces relative to h2 entries. Output format:\n"
            "- [Installation](#installation)\n"
            "  - [Prerequisites](#prerequisites)\n"
            "  - [Quick Start](#quick-start)\n"
            "- [Configuration](#configuration)\n\n"
            "Use only the Python standard library."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "gen_toc.py exists": check_toc_file,
            "contains markdown links": check_toc_has_links,
            "Installation heading present": check_toc_installation_heading,
            "h3 entries are indented": check_toc_h3_indented,
            "clean exit": check_toc_exit,
        },
    },
    {
        "name": "json-flatten",
        "files": {"config.json": _NESTED_JSON},
        "run": "python flatten.py config.json",
        "prompt": (
            "Write flatten.py that reads a nested JSON file and flattens it to a "
            "single level using dot notation for keys. The script should accept the "
            "filename as a command-line argument.\n\n"
            "Flattening rules:\n"
            "- Nested dicts are expanded: {'a': {'b': 1}} → {'a.b': 1}\n"
            "- Lists/arrays are kept as-is (not further flattened by index)\n"
            "- Scalar values (strings, numbers, booleans, null) are leaf nodes\n\n"
            "Example:\n"
            'Input: {"server": {"host": "localhost", "port": 8080}}\n'
            'Output: {"server.host": "localhost", "server.port": 8080}\n\n'
            "Print the result as pretty-printed JSON (indent=2) to stdout. "
            "Use only the Python standard library."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "flatten.py exists": check_flatten_file,
            "valid JSON output": check_flatten_valid_json,
            "database.host key present": check_flatten_nested_key,
            "server.tls.cert_file key present": check_flatten_deep_key,
            "database.port value preserved": check_flatten_values_preserved,
            "lists preserved as-is": check_flatten_list_preserved,
            "clean exit": check_flatten_exit,
        },
    },
]
