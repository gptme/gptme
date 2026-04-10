"""Verify behavioral eval checkers against known-good reference solutions.

This test validates the entire eval pipeline end-to-end (setup → solution →
run → check) without any LLM calls.  Each scenario gets a hand-written
reference solution that should make all checkers pass.  If any checker fails
against its reference solution, the checker (or the solution) is wrong.

This is critical infrastructure for idea #19 (eval-to-lesson feedback loop):
before running expensive baseline experiments with real models, we need
confidence that the checkers correctly identify good work.

Covers all 28 behavioral scenarios:
  git-selective-commit, multi-file-rename, iterative-debug,
  stage-new-files, write-test-suite, test-driven-error-handling,
  merge-conflict-resolution, extract-function-refactor, debug-data-pipeline,
  scope-discipline-bugfix, add-logging, use-existing-helper,
  add-feature-preserve-default, handle-specific-exception,
  fix-security-path-traversal, refactor-for-testability, add-type-hints,
  noisy-worktree-fix, fix-data-mutation, optimize-n-squared, remove-dead-code,
  fix-mutable-default, add-deprecation-warning, add-docstrings, retry-with-backoff,
  validate-user-input, rate-limiting
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

from gptme.eval.suites.behavioral import tests as behavioral_tests
from gptme.eval.types import EvalSpec, ResultContext


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in an isolated temp directory."""
    monkeypatch.chdir(tmp_path)


def _run(cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Helper: run shell command, return CompletedProcess."""
    return subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=cwd,
    )


def _apply_solution(workspace: Path, scenario_name: str) -> None:
    """Apply a known-correct reference solution for a scenario.

    These solutions simulate what a competent agent would produce.
    """
    if scenario_name == "git-selective-commit":
        # Stage only calc.py and test_calc.py (not config.py)
        _run("git add calc.py test_calc.py", cwd=workspace)
        _run('git commit -m "feat(calc): add divide function"', cwd=workspace)

    elif scenario_name == "multi-file-rename":
        # Replace calcArea with calculate_area in all 3 files
        for f in ["src/geometry.py", "src/utils.py", "tests/test_geometry.py"]:
            p = workspace / f
            p.write_text(p.read_text().replace("calcArea", "calculate_area"))

    elif scenario_name == "iterative-debug":
        # Fix the bug: remove try/except and let ZeroDivisionError propagate
        p = workspace / "calculator.py"
        p.write_text(
            p.read_text().replace(
                "    try:\n        return a / b\n    except ZeroDivisionError:\n        return None  # BUG: should raise, not return None",
                "    return a / b",
            )
        )

    elif scenario_name == "stage-new-files":
        (workspace / "new_feature.py").write_text("def double(x):\n    return x * 2\n")
        _run("git add new_feature.py", cwd=workspace)
        _run('git commit -m "feat: add double function"', cwd=workspace)

    elif scenario_name == "write-test-suite":
        (workspace / "test_text_processor.py").write_text(
            textwrap.dedent("""\
            import pytest
            from text_processor import word_count, truncate, extract_emails

            # word_count
            def test_word_count_basic():
                assert word_count("hello world") == 2

            def test_word_count_single():
                assert word_count("hello") == 1

            def test_word_count_empty():
                assert word_count("") == 0

            def test_word_count_none():
                assert word_count(None) == 0

            def test_word_count_multiple_spaces():
                assert word_count("  hello   world  ") == 2

            # truncate
            def test_truncate_short():
                assert truncate("hi", 10) == "hi"

            def test_truncate_exact():
                assert truncate("hello", 5) == "hello"

            def test_truncate_long():
                assert truncate("hello world", 8) == "hello..."

            def test_truncate_empty():
                assert truncate("", 10) == ""

            def test_truncate_none():
                assert truncate(None, 10) == ""

            def test_truncate_custom_suffix():
                assert truncate("hello world", 9, suffix="…") == "hello wo…"

            # extract_emails
            def test_extract_emails_basic():
                assert extract_emails("email@test.com") == ["email@test.com"]

            def test_extract_emails_multiple():
                result = extract_emails("a@b.com and c@d.com")
                assert result == ["a@b.com", "c@d.com"]

            def test_extract_emails_none():
                assert extract_emails("no emails here") == []

            def test_extract_emails_empty():
                assert extract_emails("") == []

            def test_extract_emails_none_input():
                assert extract_emails(None) == []
            """)
        )

    elif scenario_name == "test-driven-error-handling":
        # Record original test content to verify it wasn't changed
        original_test = (workspace / "test_converter.py").read_text()

        # Fix converter.py: add input validation
        (workspace / "converter.py").write_text(
            textwrap.dedent("""\
            import numbers

            def parse_csv_line(line):
                if not line:
                    raise ValueError("empty input")
                return [field.strip() for field in line.split(",")]

            def to_int(value):
                if value is None:
                    raise ValueError(f"cannot convert None to int")
                try:
                    return int(value)
                except (ValueError, TypeError) as e:
                    raise ValueError(f"cannot convert {value!r} to int") from e

            def safe_divide(a, b):
                if isinstance(a, str) or not isinstance(a, numbers.Number):
                    raise ValueError(f"cannot divide with non-numeric a={a!r}")
                if isinstance(b, str) or not isinstance(b, numbers.Number):
                    raise ValueError(f"cannot divide with non-numeric b={b!r}")
                if b == 0:
                    raise ValueError("cannot divide by zero")
                return a / b
            """)
        )

        # Verify test file wasn't modified
        assert (workspace / "test_converter.py").read_text() == original_test

    elif scenario_name == "merge-conflict-resolution":
        # Write the correctly resolved merge result
        (workspace / "utils.py").write_text(
            textwrap.dedent('''\
            def format_name(first, last):
                """Format a person's full name."""
                if first is None or last is None:
                    return "Unknown"
                return f"{first} {last}"


            def format_name_upper(first, last):
                """Format a person's full name in uppercase."""
                return format_name(first, last).upper()
            ''')
        )
        _run("git add utils.py", cwd=workspace)
        _run(
            'git commit -m "merge: resolve conflict — keep null safety and format_name_upper"',
            cwd=workspace,
        )

    elif scenario_name == "extract-function-refactor":
        # Create the shared validation module
        (workspace / "validation.py").write_text(
            textwrap.dedent("""\
            def validate_email(email):
                if not email or "@" not in email or "." not in email.split("@")[1]:
                    raise ValueError("Invalid email address")
                return email.lower().strip()
            """)
        )

        # Update all three service modules — replace inline validation with import
        (workspace / "user_service.py").write_text(
            textwrap.dedent("""\
            from validation import validate_email


            def create_user(email, name):
                "Create a new user account."
                clean_email = validate_email(email)
                return {"email": clean_email, "name": name}
            """)
        )

        (workspace / "newsletter.py").write_text(
            textwrap.dedent("""\
            from validation import validate_email


            def subscribe(email):
                "Subscribe an email to the newsletter."
                clean_email = validate_email(email)
                return {"email": clean_email, "subscribed": True}
            """)
        )

        (workspace / "contact.py").write_text(
            textwrap.dedent("""\
            from validation import validate_email


            def send_message(email, message):
                "Send a contact form message."
                clean_email = validate_email(email)
                return {"email": clean_email, "message": message, "sent": True}
            """)
        )

    elif scenario_name == "debug-data-pipeline":
        # Fix extract_emails to iterate over the list instead of subscripting as dict
        p = workspace / "pipeline.py"
        p.write_text(
            p.read_text().replace(
                'return users["emails"]  # bug: users is a list, not a dict',
                'return [u["email"] for u in users]',
            )
        )

    elif scenario_name == "scope-discipline-bugfix":
        # Fix only the spurious +1 in mean() — leave median() and mode() untouched
        p = workspace / "stats.py"
        p.write_text(
            p.read_text().replace(
                "return total / len(numbers) + 1  # BUG: spurious +1",
                "return total / len(numbers)",
            )
        )

    elif scenario_name == "add-logging":
        # Add Python logging to process_record — error level for failures,
        # debug level for success, no print statements.
        p = workspace / "processor.py"
        p.write_text(
            '"""Data record processor."""\n'
            "import logging\n"
            "\n"
            "\n"
            "def process_record(record):\n"
            '    """Process a single data record.\n'
            "\n"
            "    Returns True on success, None if the record is invalid or negative.\n"
            '    """\n'
            "    try:\n"
            '        value = int(record["value"])\n'
            "        if value < 0:\n"
            '            logging.error("Negative value: %s", value)\n'
            "            return None\n"
            '        record["processed"] = value * 2\n'
            '        logging.debug("Processed record: value=%s", value)\n'
            "        return True\n"
            "    except (KeyError, ValueError) as e:\n"
            '        logging.error("Invalid record: %s", e)\n'
            "        return None\n"
        )

    elif scenario_name == "use-existing-helper":
        # Refactor users.py to import normalize_email from utils and call it
        # instead of duplicating the .strip().lower() logic inline.
        p = workspace / "users.py"
        p.write_text(
            '"""User management module."""\n'
            "\n"
            "from utils import normalize_email\n"
            "\n"
            "\n"
            "def create_user(email: str) -> dict:\n"
            '    """Create a new user record with a normalized email address.\n'
            "\n"
            "    Raises ValueError for obviously invalid addresses.\n"
            "    Returns a dict with keys 'email' (normalized) and 'active'.\n"
            '    """\n'
            "    normalized = normalize_email(email)\n"
            '    if "@" not in normalized or "." not in normalized.split("@")[-1]:\n'
            '        raise ValueError(f"Invalid email: {email!r}")\n'
            '    return {"email": normalized, "active": True}\n'
        )

    elif scenario_name == "add-feature-preserve-default":
        # Add include_chars parameter with default False; append new tests
        (workspace / "text_stats.py").write_text(
            textwrap.dedent("""\
            def summarize(text, include_chars=False):
                \"\"\"Return a dict with word and line counts for the given text.\"\"\"
                if not text:
                    result = {"words": 0, "lines": 0}
                else:
                    words = len(text.split())
                    lines = len(text.splitlines())
                    result = {"words": words, "lines": lines}
                if include_chars:
                    result["chars"] = len(text) if text else 0
                return result
            """)
        )
        # Append new tests after the marker — do not modify original tests
        original = (workspace / "test_text_stats.py").read_text()
        (workspace / "test_text_stats.py").write_text(
            original
            + textwrap.dedent("""\

            def test_include_chars_true():
                result = summarize("hello world", include_chars=True)
                assert result == {"words": 2, "lines": 1, "chars": 11}


            def test_include_chars_empty():
                result = summarize("", include_chars=True)
                assert result == {"words": 0, "lines": 0, "chars": 0}


            def test_include_chars_false_explicit():
                result = summarize("hello", include_chars=False)
                assert set(result.keys()) == {"words", "lines"}
            """)
        )

    elif scenario_name == "handle-specific-exception":
        # Narrow the broad except Exception: to json.JSONDecodeError,
        # and add a test verifying FileNotFoundError propagates.
        (workspace / "config.py").write_text(
            textwrap.dedent("""\
            \"\"\"Application configuration loader.\"\"\"

            import json


            def parse_config(path):
                \"\"\"Load configuration from a JSON file.

                Raises FileNotFoundError if the file does not exist.
                Returns an empty dict if the file contains invalid JSON.
                \"\"\"
                try:
                    with open(path) as f:
                        return json.load(f)
                except json.JSONDecodeError:
                    return {}
            """)
        )
        original = (workspace / "test_config.py").read_text()
        (workspace / "test_config.py").write_text(
            original
            + textwrap.dedent("""\


            def test_missing_file_raises():
                import pytest
                with pytest.raises(FileNotFoundError):
                    parse_config("/nonexistent/path/config.json")
            """)
        )

    elif scenario_name == "fix-security-path-traversal":
        # Fix path traversal: resolve both paths and validate containment.
        (workspace / "server.py").write_text(
            textwrap.dedent("""\
            \"\"\"Minimal static file server.\"\"\"

            import os


            def serve_file(base_dir: str, filename: str) -> bytes:
                \"\"\"Return the contents of *filename* from *base_dir*.

                Raises FileNotFoundError if the file does not exist.
                Raises ValueError if *filename* is absolute or escapes base_dir.
                \"\"\"
                if os.path.isabs(filename):
                    raise ValueError(f"Absolute paths not allowed: {filename!r}")

                base = os.path.realpath(base_dir)
                filepath = os.path.realpath(os.path.join(base_dir, filename))

                if not filepath.startswith(base + os.sep) and filepath != base:
                    raise ValueError(
                        f"Path escapes base directory: {filename!r}"
                    )

                with open(filepath, "rb") as f:
                    return f.read()
            """)
        )
        original = (workspace / "test_server.py").read_text()
        (workspace / "test_server.py").write_text(
            original
            + textwrap.dedent("""\


            def test_blocks_path_traversal(tmp_path):
                \"\"\"Requesting ../../etc/passwd must raise ValueError.\"\"\"
                safe_dir = tmp_path / "safe"
                safe_dir.mkdir()
                with pytest.raises(ValueError, match="escapes"):
                    serve_file(str(safe_dir), "../../etc/passwd")
            """)
        )

    elif scenario_name == "refactor-for-testability":
        # Extract pure computation into compute_stats(rows), keep generate_report
        # as the I/O wrapper that delegates to compute_stats.
        (workspace / "report.py").write_text(
            textwrap.dedent('''\
            import csv


            def compute_stats(rows):
                """Compute summary statistics from a list of row dicts."""
                if not rows:
                    return {"total_rows": 0}

                scores = [int(r["score"]) for r in rows]
                hours = [float(r["hours"]) for r in rows]

                return {
                    "total_rows": len(rows),
                    "score_total": sum(scores),
                    "score_average": round(sum(scores) / len(scores), 2),
                    "score_min": min(scores),
                    "score_max": max(scores),
                    "hours_total": round(sum(hours), 2),
                    "hours_average": round(sum(hours) / len(hours), 2),
                    "hours_min": min(hours),
                    "hours_max": max(hours),
                }


            def generate_report(filepath):
                """Generate summary statistics from a CSV file."""
                rows = []
                with open(filepath, newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rows.append(row)
                return compute_stats(rows)
            ''')
        )
        (workspace / "test_report.py").write_text(
            textwrap.dedent("""\
            from report import compute_stats


            def test_compute_stats_normal():
                rows = [
                    {"name": "Alice", "score": "90", "hours": "5.5"},
                    {"name": "Bob", "score": "70", "hours": "3.0"},
                ]
                result = compute_stats(rows)
                assert result["total_rows"] == 2
                assert result["score_total"] == 160
                assert result["score_average"] == 80.0
                assert result["score_min"] == 70
                assert result["score_max"] == 90


            def test_compute_stats_empty():
                assert compute_stats([]) == {"total_rows": 0}


            def test_compute_stats_single_row():
                rows = [{"name": "Carol", "score": "85", "hours": "4.0"}]
                result = compute_stats(rows)
                assert result["total_rows"] == 1
                assert result["score_average"] == 85.0
                assert result["score_min"] == 85
                assert result["score_max"] == 85
            """)
        )

    elif scenario_name == "add-type-hints":
        (workspace / "datastore.py").write_text(
            textwrap.dedent("""\
            from typing import Any, Optional


            class DataStore:
                def __init__(self) -> None:
                    self.data: dict[str, Any] = {}
                    self.metadata: dict[str, dict[str, Any]] = {}

                def set(self, key: str, value: Any) -> None:
                    self.data[key] = value

                def get(self, key: str, default: Optional[Any] = None) -> Any:
                    return self.data.get(key, default)

                def delete(self, key: str) -> bool:
                    if key in self.data:
                        del self.data[key]
                        return True
                    return False

                def keys(self) -> list[str]:
                    return list(self.data.keys())

                def items(self) -> list[tuple[str, Any]]:
                    return list(self.data.items())

                def update_metadata(self, key: str, info: dict[str, Any]) -> None:
                    self.metadata[key] = info

                def get_metadata(self, key: str) -> Optional[dict[str, Any]]:
                    return self.metadata.get(key)

                def count(self) -> int:
                    return len(self.data)

                def search(self, prefix: str) -> list[str]:
                    return [k for k in self.data if k.startswith(prefix)]


            def merge_stores(
                primary: DataStore, secondary: DataStore
            ) -> DataStore:
                result = DataStore()
                for key, value in primary.items():
                    result.set(key, value)
                for key, value in secondary.items():
                    if key not in primary.data:
                        result.set(key, value)
                return result


            def filter_by_value(
                store: DataStore, predicate: Any
            ) -> DataStore:
                result = DataStore()
                for key, value in store.items():
                    if predicate(value):
                        result.set(key, value)
                return result
            """)
        )

    elif scenario_name == "noisy-worktree-fix":
        # setup.sh introduced the bug in the working tree without committing it.
        # First commit the buggy state so auth.py differs from HEAD, then fix and
        # commit only auth.py — leaving api.py, config.py, utils.py uncommitted.
        _run("git add auth.py", cwd=workspace)
        _run(
            'git commit -q -m "chore: introduce auth bug (test artifact)"',
            cwd=workspace,
        )
        (workspace / "auth.py").write_text(
            textwrap.dedent("""\
            \"\"\"Authentication utilities.\"\"\"


            def validate_email(email: str) -> bool:
                \"\"\"Return True if email looks valid (must have @ and a dot in the domain).\"\"\"
                parts = email.split("@")
                if len(parts) != 2:
                    return False
                local, domain = parts
                return bool(local) and "." in domain
            """)
        )
        _run("git add auth.py", cwd=workspace)
        _run(
            'git commit -m "fix(auth): fix email validation to require dot in domain"',
            cwd=workspace,
        )

    elif scenario_name == "fix-data-mutation":
        # Fix both mutation bugs in records.py without touching test_records.py
        (workspace / "records.py").write_text(
            textwrap.dedent("""\
            \"\"\"Record processing utilities.\"\"\"


            def tag_records(records: list, tag: str) -> list:
                \"\"\"Add a tag to each record and return the updated records.\"\"\"
                return [{**record, "tags": record["tags"] + [tag]} for record in records]


            def apply_updates(state: dict, updates: dict) -> dict:
                \"\"\"Apply updates to state and return the new state.\"\"\"
                return {**state, **updates}
            """)
        )

    elif scenario_name == "optimize-n-squared":
        # Rewrite find_duplicates using Counter for O(n) performance
        (workspace / "analytics.py").write_text(
            textwrap.dedent("""\
            \"\"\"Analytics utilities.\"\"\"
            from collections import Counter


            def find_duplicates(items: list) -> list:
                \"\"\"Return a sorted list of items that appear more than once in *items*.\"\"\"
                counts = Counter(items)
                return sorted(item for item, count in counts.items() if count > 1)
            """)
        )

    elif scenario_name == "remove-dead-code":
        # Remove the unused _batch_normalize function; keep everything else intact
        (workspace / "utils.py").write_text(
            textwrap.dedent("""\
            \"\"\"Record processing utilities.\"\"\"


            def parse_record(data: dict) -> dict:
                \"\"\"Parse and normalize a record from raw input.\"\"\"
                return {
                    "id": data["id"],
                    "value": _normalize_value(data.get("value", "")),
                    "valid": validate_record(data),
                }


            def validate_record(data: dict) -> bool:
                \"\"\"Return True if *data* contains the required fields.\"\"\"
                return "id" in data and "value" in data


            def _normalize_value(value: str) -> str:
                \"\"\"Normalize a string value for storage.\"\"\"
                return value.strip().lower()
            """)
        )

    elif scenario_name == "add-docstrings":
        # Add Google-style docstrings to all three functions in utils.py
        (workspace / "utils.py").write_text(
            textwrap.dedent("""\
            import re


            def parse_args(input_str):
                \"\"\"Parse a string of arguments respecting quoted substrings.

                Args:
                    input_str: The input string to parse, where spaces separate
                        tokens and quoted substrings are treated as single tokens.

                Returns:
                    list[str]: A list of parsed argument tokens.
                \"\"\"
                parts = []
                current = ""
                in_quotes = False
                quote_char = None
                for ch in input_str:
                    if ch in ('"', "'") and not in_quotes:
                        if current:
                            parts.append(current)
                            current = ""
                        in_quotes = True
                        quote_char = ch
                    elif ch == quote_char and in_quotes:
                        in_quotes = False
                        quote_char = None
                    elif ch == " " and not in_quotes:
                        if current:
                            parts.append(current)
                            current = ""
                    else:
                        current += ch
                if current:
                    parts.append(current)
                return parts


            def validate_email(email):
                \"\"\"Validate an email address and return it lowercased.

                Args:
                    email: The email address string to validate.

                Returns:
                    The email address converted to lowercase.

                Raises:
                    ValueError: If the email is empty, missing '@', has an
                        empty local part or domain, has an invalid local part,
                        or has a domain without a dot.
                \"\"\"
                if not email or "@" not in email:
                    raise ValueError(f"Invalid email: {email}")
                local, domain = email.rsplit("@", 1)
                if not local or not domain:
                    raise ValueError(f"Invalid email: {email}")
                if not re.match(r"^[\\w.-]+$", local):
                    raise ValueError(f"Invalid email local part: {local}")
                if "." not in domain:
                    raise ValueError(f"Invalid email domain: {domain}")
                return email.lower()


            def compute_stats(numbers):
                \"\"\"Compute basic statistics for a list of numbers.

                Args:
                    numbers: A list of numeric values to summarize.

                Returns:
                    dict: A dictionary with keys 'mean', 'median', and 'count'.

                Example:
                    >>> compute_stats([1, 2, 3, 4, 5])
                    {'mean': 3.0, 'median': 3.0, 'count': 5}
                \"\"\"
                if not numbers:
                    return {"mean": 0, "median": 0, "count": 0}
                n = len(numbers)
                mean = sum(numbers) / n
                sorted_nums = sorted(numbers)
                if n % 2 == 0:
                    median = (sorted_nums[n // 2 - 1] + sorted_nums[n // 2]) / 2
                else:
                    median = sorted_nums[n // 2]
                return {"mean": mean, "median": median, "count": n}
            """)
        )

    elif scenario_name == "fix-mutable-default":
        # Apply the None-sentinel fix to both functions
        (workspace / "records.py").write_text(
            textwrap.dedent("""\
            def collect_records(items, result=None):
                \"\"\"Collect non-empty, stripped records into result list.\"\"\"
                if result is None:
                    result = []
                for item in items:
                    stripped = item.strip()
                    if stripped:
                        result.append(stripped)
                return result


            def deduplicate(items, seen=None):
                \"\"\"Return unique items from *items* preserving order.\"\"\"
                if seen is None:
                    seen = []
                for item in items:
                    if item not in seen:
                        seen.append(item)
                return seen
            """)
        )

    elif scenario_name == "add-deprecation-warning":
        # Add warnings.warn() with DeprecationWarning to get_data()
        (workspace / "api_client.py").write_text(
            textwrap.dedent("""\
            \"\"\"API client module for the metrics service.\"\"\"

            import urllib.request
            import json
            import warnings
            from typing import Any


            def fetch_metrics(endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
                \"\"\"
                Fetch metrics from the given endpoint.

                Args:
                    endpoint: The API endpoint URL
                    params: Optional query parameters

                Returns:
                    The JSON response as a dictionary
                \"\"\"
                url = endpoint
                if params:
                    query = "&".join(f"{k}={v}" for k, v in params.items())
                    url = f"{endpoint}?{query}"

                with urllib.request.urlopen(url) as response:
                    return json.loads(response.read().decode())


            def get_summary(metrics: list[dict[str, Any]]) -> dict[str, float]:
                \"\"\"
                Calculate summary statistics from a list of metric dictionaries.

                Args:
                    metrics: List of metric dictionaries with 'value' keys

                Returns:
                    Dictionary with min, max, and average values
                \"\"\"
                if not metrics:
                    return {"min": 0.0, "max": 0.0, "average": 0.0}

                values = [m["value"] for m in metrics if "value" in m]
                if not values:
                    return {"min": 0.0, "max": 0.0, "average": 0.0}

                return {
                    "min": min(values),
                    "max": max(values),
                    "average": sum(values) / len(values),
                }


            # DEPRECATED: Use fetch_metrics instead
            def get_data(url: str) -> dict[str, Any]:
                \"\"\"
                Fetch JSON data from a URL.

                DEPRECATED: Use fetch_metrics() instead. This function will be removed
                in a future version.

                Args:
                    url: The URL to fetch from

                Returns:
                    The JSON response as a dictionary
                \"\"\"
                warnings.warn(
                    "get_data() is deprecated, use fetch_metrics() instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                with urllib.request.urlopen(url) as response:
                    return json.loads(response.read().decode())
            """)
        )

    elif scenario_name == "retry-with-backoff":
        (workspace / "api_client.py").write_text(
            textwrap.dedent("""\
            \"\"\"API client for fetching user data.\"\"\"

            import time
            import requests


            def retry_with_backoff(func, max_retries=3, base_delay=0.1):
                \"\"\"Retry a function with exponential backoff.\"\"\"
                for attempt in range(max_retries):
                    try:
                        return func()
                    except Exception:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            time.sleep(delay)
                        else:
                            raise


            def fetch_user(user_id: int) -> dict:
                \"\"\"Fetch a user from the API. Retries on transient failures.\"\"\"
                url = f"https://api.example.com/users/{user_id}"
                def _fetch():
                    response = requests.get(url, timeout=5)
                    response.raise_for_status()
                    return response.json()
                return retry_with_backoff(_fetch)
            """)
        )

    elif scenario_name == "validate-user-input":
        (workspace / "user_service.py").write_text(
            textwrap.dedent("""\
            \"\"\"User service for managing user profiles.\"\"\"


            def create_user(name: str, age: int, email: str) -> dict:
                \"\"\"Create a new user profile.

                Args:
                    name: Full name of the user.
                    age: Age in years.
                    email: Email address.

                Returns:
                    Dict with user info.
                \"\"\"
                if not isinstance(name, str):
                    raise TypeError(f"name must be a string, got {type(name).__name__}")
                if not isinstance(age, int):
                    raise TypeError(f"age must be an integer, got {type(age).__name__}")
                if not name.strip():
                    raise ValueError("name must not be empty or whitespace-only")
                if age < 0 or age > 150:
                    raise ValueError(f"age must be between 0 and 150, got {age}")
                return {"name": name, "age": age, "email": email}
            """)
        )

    elif scenario_name == "rate-limiting":
        # Write the implemented solution with exponential backoff
        (workspace / "client.py").write_text(
            textwrap.dedent("""\
            \"\"\"API client for external service.\"\"\"

            import urllib.request
            import json
            import time


            class RateLimitError(Exception):
                \"\"\"Raised when rate limit is exceeded.\"\"\"


            class APIClient:
                \"\"\"API client with rate limiting and exponential backoff.\"\"\"

                def __init__(self, api_key: str, max_retries: int = 5, base_delay: float = 0.1):
                    self.api_key = api_key
                    self.base_url = "https://api.example.com"
                    self.max_retries = max_retries
                    self.base_delay = base_delay

                def get(self, endpoint: str) -> dict:
                    \"\"\"Make a GET request to the API with rate limit handling.

                    Args:
                        endpoint: The API endpoint path.

                    Returns:
                        Parsed JSON response as a dict.

                    Raises:
                        RateLimitError: When rate limit is exceeded after all retries.
                        urllib.error.HTTPError: For non-rate-limit HTTP errors.
                    \"\"\"
                    url = f"{self.base_url}/{endpoint}"
                    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
                    for attempt in range(self.max_retries):
                        try:
                            with urllib.request.urlopen(req, timeout=10) as resp:
                                return json.loads(resp.read().decode())
                        except urllib.error.HTTPError as e:
                            if e.code == 429:
                                if attempt < self.max_retries - 1:
                                    delay = self.base_delay * (2 ** attempt)
                                    time.sleep(delay)
                                else:
                                    raise RateLimitError("Rate limit exceeded")
                            else:
                                raise
                    raise RateLimitError("Rate limit exceeded")
            """)
        )

    else:
        raise ValueError(f"Unknown scenario: {scenario_name}")


def _get_workspace_files(
    workspace: Path, include_git: bool = False
) -> dict[str, str | bytes]:
    """Read all files in workspace as {relative_path: content}.

    Args:
        include_git: Include .git metadata files (needed for merge-commit checker).
    """
    files: dict[str, str | bytes] = {}
    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts:
            continue
        if not include_git and ".git" in p.parts:
            continue
        try:
            files[str(p.relative_to(workspace))] = p.read_text()
        except UnicodeDecodeError:
            try:
                files[str(p.relative_to(workspace))] = p.read_bytes()
            except OSError:
                pass
    return files


# ── Parametrized solution verification ───────────────────────────────────────


@pytest.mark.parametrize("scenario", behavioral_tests, ids=lambda t: t["name"])
@pytest.mark.timeout(30)
def test_reference_solution_passes_all_checkers(
    tmp_path: Path, scenario: EvalSpec
) -> None:
    """A correct reference solution should pass all checkers for the scenario."""
    spec = scenario
    name = spec["name"]

    # Skip scenarios requiring external tools not available in the test env
    if name == "add-type-hints":
        result = _run("python3 -m mypy --version")
        if result.returncode != 0:
            pytest.skip("mypy not available")

    # Write scenario files
    for fname, content in spec["files"].items():
        fpath = tmp_path / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            fpath.write_bytes(content)
        else:
            fpath.write_text(content)

    # Run setup script if present
    if "setup.sh" in spec["files"]:
        result = _run("bash setup.sh", cwd=tmp_path)
        assert result.returncode == 0, f"setup.sh failed: {result.stderr}"

    # Apply reference solution
    _apply_solution(tmp_path, name)

    # Run the eval's "run" command
    run_result = _run(spec["run"], cwd=tmp_path)
    run_stdout = run_result.stdout
    run_stderr = run_result.stderr

    # Build ResultContext
    files = _get_workspace_files(
        tmp_path, include_git=name == "merge-conflict-resolution"
    )
    ctx = ResultContext(
        files=files,
        stdout=run_stdout,
        stderr=run_stderr,
        exit_code=run_result.returncode,
    )

    # Check all checkers
    failed: list[str] = []
    for check_name, checker in spec["expect"].items():
        try:
            passed = checker(ctx)
        except Exception as e:
            failed.append(f"{check_name}: EXCEPTION {e}")
            continue
        if not passed:
            failed.append(check_name)

    if failed:
        msg = (
            f"Scenario '{name}': {len(failed)}/{len(spec['expect'])} checkers FAILED\n"
            f"Failed: {', '.join(failed)}\n"
            f"--- stdout ---\n{run_stdout[-500:]}\n"
            f"--- stderr ---\n{run_stderr[-500:]}"
        )
        pytest.fail(msg)


# ── Smoke tests for individual scenarios ─────────────────────────────────────


def test_git_selective_commit_setup_and_solution(tmp_path: Path) -> None:
    """Smoke test: git-selective-commit pipeline works end-to-end."""
    spec = next(t for t in behavioral_tests if t["name"] == "git-selective-commit")
    setup_content = spec["files"]["setup.sh"]
    (tmp_path / "setup.sh").write_bytes(
        setup_content if isinstance(setup_content, bytes) else setup_content.encode()
    )
    _run("bash setup.sh", cwd=tmp_path)

    # Apply solution
    _run("git add calc.py test_calc.py", cwd=tmp_path)
    _run('git commit -m "feat(calc): add divide function"', cwd=tmp_path)

    # Run eval command
    result = _run(spec["run"], cwd=tmp_path)
    ctx = ResultContext(
        files=_get_workspace_files(tmp_path),
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
    )

    assert spec["expect"]["conventional commit message"](ctx)
    assert spec["expect"]["config.py not committed"](ctx)
    assert spec["expect"]["tests pass"](ctx)


def test_merge_conflict_setup_creates_conflict(tmp_path: Path) -> None:
    """Verify that the merge-conflict setup actually produces a conflict."""
    spec = next(t for t in behavioral_tests if t["name"] == "merge-conflict-resolution")
    setup_content = spec["files"]["setup.sh"]
    (tmp_path / "setup.sh").write_bytes(
        setup_content if isinstance(setup_content, bytes) else setup_content.encode()
    )
    result = _run("bash setup.sh", cwd=tmp_path)
    assert result.returncode == 0, f"setup failed: {result.stderr}"

    # Verify conflict markers exist
    utils = (tmp_path / "utils.py").read_text()
    assert "<<<<<<< " in utils, "setup.sh should produce a merge conflict"
