"""Verify behavioral eval checkers against known-good reference solutions.

This test validates the entire eval pipeline end-to-end (setup → solution →
run → check) without any LLM calls.  Each scenario gets a hand-written
reference solution that should make all checkers pass.  If any checker fails
against its reference solution, the checker (or the solution) is wrong.

This is critical infrastructure for idea #19 (eval-to-lesson feedback loop):
before running expensive baseline experiments with real models, we need
confidence that the checkers correctly identify good work.

Covers all 14 behavioral scenarios:
  git-selective-commit, multi-file-rename, iterative-debug,
  stage-new-files, write-test-suite, test-driven-error-handling,
  merge-conflict-resolution, extract-function-refactor, debug-data-pipeline,
  scope-discipline-bugfix, add-logging, use-existing-helper,
  add-feature-preserve-default, handle-specific-exception
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
def test_reference_solution_passes_all_checkers(
    tmp_path: Path, scenario: EvalSpec
) -> None:
    """A correct reference solution should pass all checkers for the scenario."""
    spec = scenario
    name = spec["name"]

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
