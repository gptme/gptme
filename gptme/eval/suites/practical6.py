"""Practical eval tests (batch 6) — CSV analysis, text processing, and config merging.

Tests capabilities not covered by earlier practical suites:
- CSV file processing with stdlib csv module (read, compute stats, format report)
- Word frequency counting with punctuation stripping and sorting
- Deep merging of nested JSON configuration files with override precedence
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- csv-analysis checks ---


def check_csv_file(ctx):
    """analyze_sales.py should exist."""
    return "analyze_sales.py" in ctx.files


def check_csv_electronics_count(ctx):
    """Electronics count should be 3."""
    # Match "Electronics" line with count 3
    return bool(re.search(r"electronics.*\b3\b", ctx.stdout, re.IGNORECASE))


def check_csv_electronics_total(ctx):
    """Electronics total should be 2149.97 (999.99 + 799.99 + 349.99)."""
    return "2149.97" in ctx.stdout


def check_csv_clothing_total(ctx):
    """Clothing total should be 184.97 (49.99 + 89.99 + 44.99)."""
    return "184.97" in ctx.stdout


def check_csv_books_avg(ctx):
    """Books average should be 22.495 or 22.50 ((14.99 + 29.99) / 2)."""
    return bool(re.search(r"22\.(?:50|49|5\b)", ctx.stdout))


def check_csv_exit(ctx):
    return ctx.exit_code == 0


# --- word-frequency checks ---


def check_freq_file(ctx):
    """word_freq.py should exist."""
    return "word_freq.py" in ctx.files


def check_freq_top_word(ctx):
    """'the' should be the most frequent word (appears 6 times)."""
    lines = [line.strip().lower() for line in ctx.stdout.splitlines() if line.strip()]
    if not lines:
        return False
    # First non-empty line should contain "the"
    return "the" in lines[0]


def check_freq_the_count(ctx):
    """'the' count should be 11."""
    return bool(re.search(r"\bthe\b.*\b11\b", ctx.stdout, re.IGNORECASE))


def check_freq_sorted(ctx):
    """Output should be sorted by frequency (descending)."""
    counts = []
    for line in ctx.stdout.splitlines():
        match = re.search(r"\b(\d+)\b", line)
        if match:
            counts.append(int(match.group(1)))
    if len(counts) < 3:
        return False
    # Check descending order
    return all(a >= b for a, b in zip(counts, counts[1:]))


def check_freq_top5_count(ctx):
    """Output should show exactly 5 words (top 5)."""
    # Count non-empty lines with a word and a number
    word_lines = [
        line
        for line in ctx.stdout.splitlines()
        if line.strip() and re.search(r"[a-z]+.*\d+", line, re.IGNORECASE)
    ]
    return len(word_lines) == 5


def check_freq_exit(ctx):
    return ctx.exit_code == 0


# --- merge-configs checks ---


def check_merge_file(ctx):
    """merge_config.py should exist."""
    return "merge_config.py" in ctx.files


def check_merge_output_valid_json(ctx):
    """Output should be valid JSON."""
    try:
        json.loads(ctx.stdout.strip())
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def check_merge_shallow_override(ctx):
    """Shallow key 'name' should be overridden to 'myapp-prod'."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    return data.get("name") == "myapp-prod"


def check_merge_nested_override(ctx):
    """Nested key database.host should be overridden to 'db.prod.example.com'."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    db = data.get("database", {})
    return db.get("host") == "db.prod.example.com"


def check_merge_nested_preserved(ctx):
    """Nested key database.port should be preserved from defaults (5432)."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    db = data.get("database", {})
    return db.get("port") == 5432


def check_merge_deep_nested(ctx):
    """Deep nested key logging.handlers.file.path should be overridden."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    handlers = data.get("logging", {}).get("handlers", {})
    file_handler = handlers.get("file", {})
    return file_handler.get("path") == "/var/log/myapp/prod.log"


def check_merge_default_preserved(ctx):
    """Default-only key 'version' should be preserved."""
    try:
        data = json.loads(ctx.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    return data.get("version") == "1.0.0"


def check_merge_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_SALES_CSV = """\
date,category,amount,description
2024-01-15,Electronics,999.99,Laptop
2024-01-16,Clothing,49.99,T-Shirt
2024-01-17,Books,14.99,Python Cookbook
2024-01-18,Electronics,799.99,Tablet
2024-01-19,Clothing,89.99,Jacket
2024-01-20,Books,29.99,Design Patterns
2024-01-21,Electronics,349.99,Headphones
2024-01-22,Clothing,44.99,Hat
"""

_SAMPLE_TEXT = """\
The quick brown fox jumps over the lazy dog. The dog barked at the fox,
but the fox was too quick. A quick movement caught the attention of
the brown cat sitting by the window. The cat watched as the fox
disappeared into the forest.
"""

_DEFAULTS_JSON = json.dumps(
    {
        "name": "myapp",
        "version": "1.0.0",
        "database": {
            "host": "localhost",
            "port": 5432,
            "name": "myapp_db",
        },
        "logging": {
            "level": "INFO",
            "handlers": {
                "console": {"enabled": True},
                "file": {
                    "enabled": False,
                    "path": "/tmp/myapp.log",
                },
            },
        },
        "features": {
            "cache": True,
            "debug": False,
        },
    },
    indent=2,
)

_OVERRIDES_JSON = json.dumps(
    {
        "name": "myapp-prod",
        "database": {
            "host": "db.prod.example.com",
            "name": "myapp_production",
        },
        "logging": {
            "level": "WARNING",
            "handlers": {
                "file": {
                    "enabled": True,
                    "path": "/var/log/myapp/prod.log",
                },
            },
        },
    },
    indent=2,
)

tests: list["EvalSpec"] = [
    {
        "name": "csv-analysis",
        "files": {"sales.csv": _SALES_CSV},
        "run": "python analyze_sales.py sales.csv",
        "prompt": (
            "Write analyze_sales.py that reads a CSV file and prints a summary "
            "report grouped by category. The script should accept the filename "
            "as a command-line argument.\n\n"
            "Use Python's built-in csv module (no pandas).\n\n"
            "For each category, print:\n"
            "- Count of transactions\n"
            "- Total amount\n"
            "- Average amount\n\n"
            "Format output as:\n"
            "<Category>: count=<N>, total=<amount>, avg=<amount>\n\n"
            "Sort categories alphabetically. Format amounts to 2 decimal places."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "analyze_sales.py exists": check_csv_file,
            "Electronics count=3": check_csv_electronics_count,
            "Electronics total": check_csv_electronics_total,
            "Clothing total": check_csv_clothing_total,
            "Books average": check_csv_books_avg,
            "clean exit": check_csv_exit,
        },
    },
    {
        "name": "word-frequency",
        "files": {"sample.txt": _SAMPLE_TEXT},
        "run": "python word_freq.py sample.txt 5",
        "prompt": (
            "Write word_freq.py that reads a text file and prints the top N "
            "most frequent words. The script takes two command-line arguments: "
            "the filename and the number N.\n\n"
            "Rules:\n"
            "- Convert all words to lowercase\n"
            "- Strip punctuation (periods, commas, etc.) from words\n"
            "- Sort by frequency descending, then alphabetically for ties\n"
            "- Print one word per line in the format: '<word>: <count>'\n"
            "- Use only the Python standard library"
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "word_freq.py exists": check_freq_file,
            "'the' is top word": check_freq_top_word,
            "'the' count is 11": check_freq_the_count,
            "sorted by frequency": check_freq_sorted,
            "exactly 5 words shown": check_freq_top5_count,
            "clean exit": check_freq_exit,
        },
    },
    {
        "name": "merge-configs",
        "files": {
            "defaults.json": _DEFAULTS_JSON,
            "overrides.json": _OVERRIDES_JSON,
        },
        "run": "python merge_config.py defaults.json overrides.json",
        "prompt": (
            "Write merge_config.py that deep-merges two JSON configuration files. "
            "The script takes two command-line arguments: the defaults file and the "
            "overrides file.\n\n"
            "Merge rules:\n"
            "- For nested dicts: recursively merge (override keys replace default keys, "
            "default-only keys are preserved)\n"
            "- For non-dict values: the override value replaces the default\n"
            "- Keys only in defaults are preserved\n"
            "- Keys only in overrides are added\n\n"
            "Print the merged result as pretty-printed JSON (indent=2) to stdout.\n"
            "Use only the Python standard library."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "merge_config.py exists": check_merge_file,
            "valid JSON output": check_merge_output_valid_json,
            "shallow override": check_merge_shallow_override,
            "nested override (db host)": check_merge_nested_override,
            "nested preserved (db port)": check_merge_nested_preserved,
            "deep nested override (log path)": check_merge_deep_nested,
            "default-only key preserved": check_merge_default_preserved,
            "clean exit": check_merge_exit,
        },
    },
]
