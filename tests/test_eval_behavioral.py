from types import SimpleNamespace

from gptme.eval.suites.behavioral import (
    check_compat_has_default_param,
    check_compat_new_tests_exist,
    check_compat_original_tests_intact,
    check_compat_tests_pass,
    check_config_catches_json_error,
    check_config_no_bare_except,
    check_config_propagates_file_error,
    check_config_tests_pass,
    check_debug_fix_in_file,
    check_debug_no_syntax_error,
    check_debug_tests_pass,
    check_error_handling_parse_csv,
    check_error_handling_safe_divide,
    check_error_handling_source_unchanged,
    check_error_handling_tests_pass,
    check_error_handling_to_int,
    check_extract_callers_import,
    check_extract_no_duplication,
    check_extract_shared_module_exists,
    check_extract_tests_pass,
    check_git_selective_commit_msg,
    check_git_selective_config_not_committed,
    check_git_selective_tests_pass,
    check_logging_debug_or_info_used,
    check_logging_error_level_used,
    check_logging_module_imported,
    check_logging_no_print,
    check_logging_tests_pass,
    check_merge_commit_completed,
    check_merge_no_conflict_markers,
    check_merge_null_safety,
    check_merge_tests_pass,
    check_merge_upper_function,
    check_pipeline_extract_emails_fixed,
    check_pipeline_source_unchanged,
    check_pipeline_tests_pass,
    check_rename_new_name_in_geometry,
    check_rename_no_old_name,
    check_rename_test_uses_new_name,
    check_rename_tests_pass,
    check_reuse_imports_utils,
    check_reuse_no_inline_strip,
    check_reuse_tests_pass,
    check_reuse_uses_normalize,
    check_reuse_utils_unchanged,
    check_scope_mean_fixed,
    check_scope_median_preserved,
    check_scope_mode_preserved,
    check_scope_no_new_functions,
    check_security_blocks_traversal,
    check_security_has_traversal_test,
    check_security_no_direct_join,
    check_security_tests_pass,
    check_security_uses_realpath,
    check_stage_file_has_double,
    check_stage_new_file_committed,
    check_stage_two_commits,
    check_testability_generate_report_preserved,
    check_testability_has_pure_function,
    check_testability_no_file_io_in_unit_tests,
    check_testability_pure_function_tested,
    check_testability_tests_pass,
    check_typehints_class_attribute_annotated,
    check_typehints_function_params_annotated,
    check_typehints_function_returns_annotated,
    check_typehints_mypy_passes,
    check_typehints_uses_generic_collection,
    check_write_tests_covers_extract_emails,
    check_write_tests_covers_truncate,
    check_write_tests_covers_word_count,
    check_write_tests_file_exists,
    check_write_tests_pass,
    check_write_tests_sufficient_count,
)


def _ctx(
    stdout: str = "",
    *,
    files: dict[str, str] | None = None,
    exit_code: int = 0,
    stderr: str = "",
):
    return SimpleNamespace(
        stdout=stdout,
        files=files or {},
        stderr=stderr,
        exit_code=exit_code,
    )


def test_check_merge_no_conflict_markers_ignores_git_internal_files():
    assert check_merge_no_conflict_markers(
        _ctx(
            files={
                ".git/MERGE_MSG": "<<<<<<< ours\nmsg\n=======\nmsg\n>>>>>>> theirs\n",
                "utils.py": 'def format_name(first, last):\n    return f"{first} {last}"\n',
            }
        )
    )


def test_check_merge_no_conflict_markers_detects_tracked_file_conflict_markers():
    assert not check_merge_no_conflict_markers(
        _ctx(
            files={
                "utils.py": "<<<<<<< ours\nconflict\n=======\nother\n>>>>>>> theirs\n"
            }
        )
    )


def test_check_merge_no_conflict_markers_ignores_setup_sh():
    """setup.sh often contains conflict markers as part of fixture data."""
    assert check_merge_no_conflict_markers(
        _ctx(
            files={
                "setup.sh": "<<<<<<< ours\nraw conflict data\n=======\nother\n>>>>>>> theirs\n",
                "utils.py": "def f(): pass\n",
            }
        )
    )


def test_check_merge_commit_completed_requires_merge_head_absent():
    assert check_merge_commit_completed(
        _ctx(files={".git/HEAD": "ref: refs/heads/master\n"})
    )
    assert not check_merge_commit_completed(
        _ctx(
            files={
                ".git/HEAD": "ref: refs/heads/master\n",
                ".git/MERGE_HEAD": "deadbeef\n",
            }
        )
    )


def test_check_extract_shared_module_exists_accepts_validate_email():
    assert check_extract_shared_module_exists(
        _ctx(files={"validation.py": "def validate_email(email):\n    return email\n"})
    )


def test_check_extract_no_duplication_detects_inline_at_validation():
    assert not check_extract_no_duplication(
        _ctx(
            files={
                "user_service.py": 'if "@" not in email:\n    raise ValueError\n',
                "newsletter.py": "from validation import validate_email\n",
                "contact.py": "from validation import validate_email\n",
            }
        )
    )


def test_check_extract_callers_import_requires_all_three_modules():
    assert check_extract_callers_import(
        _ctx(
            files={
                "user_service.py": "from validation import validate_email\n",
                "newsletter.py": "from validation import validate_email\n",
                "contact.py": "import validation\n",
            }
        )
    )
    assert not check_extract_callers_import(
        _ctx(
            files={
                "user_service.py": "from validation import validate_email\n",
                "newsletter.py": "from validation import validate_email\n",
                "contact.py": "def send_message(email, message):\n    ...\n",
            }
        )
    )


def test_check_error_handling_tests_pass_requires_passed_marker():
    assert check_error_handling_tests_pass(_ctx(files={}, stdout="3 passed"))
    assert not check_error_handling_tests_pass(
        _ctx(files={}, stdout="collected 3 items")
    )
    assert not check_error_handling_tests_pass(
        _ctx(files={}, stdout="1 failed, 2 passed")
    )


def test_check_error_handling_parse_csv_ignores_nested_helper_raise():
    source = """

def parse_csv_line(line):
    def helper():
        raise ValueError("nested only")
    return [field.strip() for field in line.split(",")]
"""
    assert not check_error_handling_parse_csv(_ctx(files={"converter.py": source}))


def test_check_error_handling_to_int_accepts_direct_raise():
    source = """

def to_int(value):
    if value in (None, ""):
        raise ValueError("cannot convert")
    return int(value)
"""
    assert check_error_handling_to_int(_ctx(files={"converter.py": source}))


def test_check_error_handling_safe_divide_accepts_direct_raise():
    source = """

def safe_divide(a, b):
    if b == 0:
        raise ValueError("cannot divide by zero")
    return a / b
"""
    assert check_error_handling_safe_divide(_ctx(files={"converter.py": source}))


# ── git-selective-commit checkers ─────────────────────────────────────────


def test_check_git_selective_commit_msg_accepts_feat_divide():
    stdout = "abc1234 feat(calc): add divide function"
    assert check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_commit_msg_accepts_add_divide():
    stdout = "abc1234 add(calc): divide function"
    assert check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_commit_msg_rejects_wrong_scope():
    stdout = "abc1234 feat(config): update debug flag"
    assert not check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_commit_msg_rejects_no_colon():
    stdout = "abc1234 add divide function"
    assert not check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_commit_msg_rejects_no_divide():
    stdout = "abc1234 feat(calc): add multiply function"
    assert not check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_commit_msg_handles_single_hash_no_space():
    # Edge case: hash with no message after space
    stdout = "abc1234"
    assert not check_git_selective_commit_msg(_ctx(stdout=stdout))


def test_check_git_selective_config_not_committed_empty():
    # config.py not in commit means git show returns empty
    stdout = "abc1234 feat(calc): add divide\n__GPTME_SEP__\n__GPTME_SEP__\n3 passed"
    assert check_git_selective_config_not_committed(_ctx(stdout=stdout))


def test_check_git_selective_config_not_committed_with_content():
    # config.py in commit means git show returns its content
    stdout = "abc1234 feat(calc): add divide\n__GPTME_SEP__\nDEBUG = True  # temporary debug\nMAX_RETRIES = 3\n__GPTME_SEP__\n3 passed"
    assert not check_git_selective_config_not_committed(_ctx(stdout=stdout))


def test_check_git_selective_config_not_committed_missing_separator():
    stdout = "abc1234 feat(calc): add divide function"
    assert not check_git_selective_config_not_committed(_ctx(stdout=stdout))


def test_check_git_selective_tests_pass_with_passed():
    stdout = "abc1234 feat\n__GPTME_SEP__\n__GPTME_SEP__\n3 passed in 0.01s"
    assert check_git_selective_tests_pass(_ctx(stdout=stdout))


def test_check_git_selective_tests_pass_with_failed():
    stdout = "abc1234 feat\n__GPTME_SEP__\n__GPTME_SEP__\n1 failed, 2 passed"
    assert not check_git_selective_tests_pass(_ctx(stdout=stdout))


def test_check_git_selective_tests_pass_missing_separator():
    stdout = "abc1234 feat"
    assert not check_git_selective_tests_pass(_ctx(stdout=stdout))


# ── multi-file-rename checkers ─────────────────────────────────────────────


def test_check_rename_no_old_name_all_clean():
    assert check_rename_no_old_name(
        _ctx(
            files={
                "src/geometry.py": "def calculate_area(): pass",
                "src/utils.py": "from src.geometry import calculate_area",
                "tests/test_geometry.py": "from src.geometry import calculate_area",
            }
        )
    )


def test_check_rename_no_old_name_still_has_old():
    assert not check_rename_no_old_name(
        _ctx(files={"src/geometry.py": "def calcArea(): pass"})
    )


def test_check_rename_no_old_name_ignores_non_py():
    assert check_rename_no_old_name(
        _ctx(
            files={
                "readme.md": "The calcArea function was renamed",
                "main.py": "def calculate_area(): pass",
            }
        )
    )


def test_check_rename_new_name_in_geometry():
    assert check_rename_new_name_in_geometry(
        _ctx(files={"src/geometry.py": "def calculate_area(shape): return 0"})
    )
    assert not check_rename_new_name_in_geometry(
        _ctx(files={"src/geometry.py": "def calcArea(shape): return 0"})
    )


def test_check_rename_test_uses_new_name():
    assert check_rename_test_uses_new_name(
        _ctx(
            files={
                "tests/test_geometry.py": (
                    "from src.geometry import calculate_area\n"
                    "def test_thing(): calculate_area('circle', 1)"
                )
            }
        )
    )


def test_check_rename_test_uses_new_name_rejects_old():
    assert not check_rename_test_uses_new_name(
        _ctx(
            files={
                "tests/test_geometry.py": (
                    "from src.geometry import calcArea\n"
                    "def test_thing(): calcArea('circle', 1)"
                )
            }
        )
    )


def test_check_rename_tests_pass():
    assert check_rename_tests_pass(_ctx(exit_code=0, stdout="3 passed"))
    assert not check_rename_tests_pass(_ctx(exit_code=1, stdout="1 failed"))
    assert not check_rename_tests_pass(
        _ctx(exit_code=0, stdout="collected 3 items\n1 failed")
    )


# ── iterative-debug checkers ───────────────────────────────────────────────


def test_check_debug_tests_pass():
    assert check_debug_tests_pass(_ctx(exit_code=0))
    assert not check_debug_tests_pass(_ctx(exit_code=1))


def test_check_debug_no_syntax_error():
    assert check_debug_no_syntax_error(_ctx(stdout="3 passed"))
    # Note: check_debug_no_syntax_error checks ctx.stdout + ctx.stderr,
    # but _ctx helper doesn't accept stderr. The checker checks lower of both,
    # and our _ctx sets stderr="" by default. True syntax errors come via stdout
    # from the pytest subprocess output, so these are valid tests.
    assert not check_debug_no_syntax_error(
        _ctx(stdout="SyntaxError: invalid syntax in calculator.py")
    )
    assert not check_debug_no_syntax_error(
        _ctx(stdout="ImportError: No module named 'calculator'")
    )
    assert check_debug_no_syntax_error(_ctx(stdout="warning about syntax"))
    assert not check_debug_no_syntax_error(
        _ctx(stdout="", stderr="SyntaxError: invalid syntax in calculator.py")
    )
    assert not check_debug_no_syntax_error(
        _ctx(stdout="3 passed", stderr="ImportError: No module named 'utils'")
    )
    assert check_debug_no_syntax_error(
        _ctx(stdout="3 passed", stderr="some stderr noise")
    )


def test_check_debug_fix_in_file_buggy():
    # Original bug: catches ZeroDivisionError and returns None
    assert not check_debug_fix_in_file(
        _ctx(
            files={
                "calculator.py": (
                    "def divide(a, b):\n"
                    "    try:\n        return a / b\n"
                    "    except ZeroDivisionError:\n        return None"
                )
            }
        )
    )


def test_check_debug_fix_in_file_fixed_by_raise():
    # Fix: removed try/except entirely
    assert check_debug_fix_in_file(
        _ctx(files={"calculator.py": ("def divide(a, b):\n    return a / b")})
    )


def test_check_debug_fix_in_file_fixed_by_re_raise():
    # Fix: re-raises with additional context
    assert check_debug_fix_in_file(
        _ctx(
            files={
                "calculator.py": (
                    "def divide(a, b):\n"
                    "    try:\n        return a / b\n"
                    '    except ZeroDivisionError:\n        raise ValueError("Cannot divide by zero")'
                )
            }
        )
    )


def test_check_debug_fix_in_file_fixed_by_guard():
    # Fix: pre-check with raise
    assert check_debug_fix_in_file(
        _ctx(
            files={
                "calculator.py": (
                    "def divide(a, b):\n"
                    '    if b == 0: raise ZeroDivisionError("Cannot divide by zero")\n'
                    "    return a / b"
                )
            }
        )
    )


# ── stage-new-files checkers ───────────────────────────────────────────────


def test_check_stage_new_file_committed():
    stdout = "abc1234 initial\n__GPTME_SEP__\nmain.py\nnew_feature.py"
    assert check_stage_new_file_committed(_ctx(stdout=stdout))


def test_check_stage_new_file_committed_not_present():
    stdout = "abc1234 initial\n__GPTME_SEP__\nmain.py"
    assert not check_stage_new_file_committed(_ctx(stdout=stdout))


def test_check_stage_new_file_committed_missing_separator():
    assert not check_stage_new_file_committed(_ctx(stdout="abc1234 initial"))


def test_check_stage_two_commits():
    stdout = "abc1234 initial: add greet\ndef4567 feat: add double function"
    assert check_stage_two_commits(_ctx(stdout=stdout))


def test_check_stage_two_commits_single():
    stdout = "abc1234 initial: add greet"
    assert not check_stage_two_commits(_ctx(stdout=stdout))


def test_check_stage_two_commits_empty():
    assert not check_stage_two_commits(_ctx(stdout=""))


def test_check_stage_file_has_double():
    assert check_stage_file_has_double(
        _ctx(files={"new_feature.py": "def double(x): return x * 2"})
    )
    assert not check_stage_file_has_double(
        _ctx(files={"new_feature.py": "def triple(x): return x * 3"})
    )


# ── write-test-suite checkers ──────────────────────────────────────────────


def test_check_write_tests_file_exists():
    assert check_write_tests_file_exists(
        _ctx(files={"test_text_processor.py": "def test_foo(): pass"})
    )
    assert not check_write_tests_file_exists(_ctx(files={"test_text_processor.py": ""}))
    assert not check_write_tests_file_exists(_ctx(files={}))


def test_check_write_tests_pass():
    assert check_write_tests_pass(_ctx(exit_code=0, stdout="6 passed"))
    assert not check_write_tests_pass(_ctx(exit_code=1, stdout="2 failed, 4 passed"))
    # The checker only requires exit_code==0 and no "failed" in stdout.
    # "collected 0 items" is technically a pass (no failures).


def test_check_write_tests_covers_word_count():
    assert check_write_tests_covers_word_count(
        _ctx(files={"test_text_processor.py": "def test_word_count(): pass"})
    )
    assert not check_write_tests_covers_word_count(
        _ctx(files={"test_text_processor.py": "def test_truncate(): pass"})
    )


def test_check_write_tests_covers_truncate():
    assert check_write_tests_covers_truncate(
        _ctx(files={"test_text_processor.py": "def test_truncate_short(): pass"})
    )
    assert not check_write_tests_covers_truncate(
        _ctx(files={"test_text_processor.py": "def test_word_count(): pass"})
    )


def test_check_write_tests_covers_extract_emails():
    assert check_write_tests_covers_extract_emails(
        _ctx(files={"test_text_processor.py": "def test_extract_emails_basic(): pass"})
    )
    assert not check_write_tests_covers_extract_emails(
        _ctx(files={"test_text_processor.py": "def test_word_count(): pass"})
    )


def test_check_write_tests_sufficient_count():
    content = "\n".join(f"def test_{i}(): pass" for i in range(6))
    assert check_write_tests_sufficient_count(
        _ctx(files={"test_text_processor.py": content})
    )

    content = "\n".join(f"def test_{i}(): pass" for i in range(3))
    assert not check_write_tests_sufficient_count(
        _ctx(files={"test_text_processor.py": content})
    )


# ── error-handling source unchanged checker ────────────────────────────────


def test_check_error_handling_source_unchanged():
    content = (
        "def test_parse_csv_valid(): pass\n"
        "def test_to_int_valid(): pass\n"
        "def test_safe_divide_valid(): pass\n"
        "def test_parse_csv_empty(): pass"
    )
    assert check_error_handling_source_unchanged(
        _ctx(files={"test_converter.py": content})
    )


def test_check_error_handling_source_unchanged_missing_function():
    content = (
        "def test_parse_csv_valid(): pass\n"
        "def test_to_int_valid(): pass\n"
        "def test_safe_divide_valid(): pass"
    )
    # Missing test_parse_csv_empty
    assert not check_error_handling_source_unchanged(
        _ctx(files={"test_converter.py": content})
    )


# ── merge-conflict remaining checkers ──────────────────────────────────────


def test_check_merge_null_safety():
    content = (
        "def format_name(first, last):\n"
        "    if first is None or last is None:\n"
        '        return "Unknown"\n'
        '    return f"{first} {last}"'
    )
    assert check_merge_null_safety(_ctx(files={"utils.py": content}))

    # Missing the check
    content2 = 'def format_name(first, last):\n    return f"{first} {last}"'
    assert not check_merge_null_safety(_ctx(files={"utils.py": content2}))

    # Wrong return value
    content3 = (
        "def format_name(first, last):\n"
        "    if first is None or last is None:\n"
        '        return ""\n'
        '    return f"{first} {last}"'
    )
    assert not check_merge_null_safety(_ctx(files={"utils.py": content3}))


def test_check_merge_tests_pass():
    assert check_merge_tests_pass(_ctx(exit_code=0, stdout="4 passed"))
    assert not check_merge_tests_pass(_ctx(exit_code=1, stdout="2 failed"))
    # The checker only requires exit_code==0 and no "failed" in stdout.
    # Non-zero exit code is the primary failure signal.


def test_check_merge_upper_function():
    assert check_merge_upper_function(
        _ctx(
            files={
                "utils.py": "def format_name_upper(first, last): return format_name(first, last).upper()"
            }
        )
    )
    assert not check_merge_upper_function(
        _ctx(
            files={"utils.py": "def format_name(first, last): return f'{first} {last}'"}
        )
    )


# ── extract-function remaining checker ─────────────────────────────────────


def test_check_extract_tests_pass():
    assert check_extract_tests_pass(_ctx(exit_code=0, stdout="6 passed"))
    assert not check_extract_tests_pass(_ctx(exit_code=1, stdout="1 failed"))


# ── debug-data-pipeline ───────────────────────────────────────────────────────


def test_check_pipeline_tests_pass():
    assert check_pipeline_tests_pass(_ctx(exit_code=0, stdout="3 passed"))
    assert not check_pipeline_tests_pass(_ctx(exit_code=1, stdout="1 failed"))


def test_check_pipeline_extract_emails_fixed_correct():
    fixed = 'def extract_emails(users):\n    return [user["email"] for user in users]\n'
    assert check_pipeline_extract_emails_fixed(_ctx(files={"pipeline.py": fixed}))


def test_check_pipeline_extract_emails_fixed_alt_variable():
    # Any loop variable name should be accepted (not just 'user')
    alt_var = 'def extract_emails(users):\n    return [u["email"] for u in users]\n'
    assert check_pipeline_extract_emails_fixed(_ctx(files={"pipeline.py": alt_var}))


def test_check_pipeline_extract_emails_fixed_broken():
    broken = 'def extract_emails(users):\n    return users["emails"]\n'
    assert not check_pipeline_extract_emails_fixed(_ctx(files={"pipeline.py": broken}))


def test_check_pipeline_source_unchanged():
    content = (
        "from pipeline import run_pipeline\n"
        "def test_basic(users_file):\n"
        "    result = run_pipeline(users_file)\n"
        '    assert result == {"count": 2, "emails": ["alice@example.com"]}\n'
    )
    assert check_pipeline_source_unchanged(_ctx(files={"test_pipeline.py": content}))


def test_check_pipeline_source_unchanged_missing_assert():
    assert not check_pipeline_source_unchanged(
        _ctx(files={"test_pipeline.py": "def test_nothing(): pass"})
    )


def test_check_pipeline_source_unchanged_hollowed_assertions():
    # Agent weakened assertions to always pass — should be rejected
    content = (
        "from pipeline import run_pipeline\n"
        "def test_basic(users_file):\n"
        "    result = run_pipeline(users_file)\n"
        "    assert result == {}\n"
    )
    assert not check_pipeline_source_unchanged(
        _ctx(files={"test_pipeline.py": content})
    )


# ── scope-discipline-bugfix ──────────────────────────────────────────────────

_STATS_FIXED = """\
\"\"\"Statistical utility functions.\"\"\"


def mean(numbers):
    if not numbers:
        return 0.0
    total = sum(numbers)
    return total / len(numbers)


def median(numbers):
    if not numbers:
        return 0.0
    sorted_nums = sorted(numbers)
    n = len(sorted_nums)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_nums[mid - 1] + sorted_nums[mid]) / 2.0
    return float(sorted_nums[mid])


def mode(numbers):
    if not numbers:
        return None
    counts = {}
    for num in numbers:
        counts[num] = counts.get(num, 0) + 1
    return max(counts, key=counts.get)
"""

_STATS_BUGGY = _STATS_FIXED.replace(
    "return total / len(numbers)",
    "return total / len(numbers) + 1  # BUG",
)


def test_check_scope_mean_fixed_correct():
    assert check_scope_mean_fixed(_ctx(files={"stats.py": _STATS_FIXED}))


def test_check_scope_mean_fixed_still_buggy():
    assert not check_scope_mean_fixed(_ctx(files={"stats.py": _STATS_BUGGY}))


def test_check_scope_median_preserved_intact():
    assert check_scope_median_preserved(_ctx(files={"stats.py": _STATS_FIXED}))


def test_check_scope_median_preserved_refactored():
    # Agent replaced sorted_nums with a comprehension — scope creep
    refactored = _STATS_FIXED.replace(
        "sorted_nums = sorted(numbers)", "s = sorted(numbers)"
    )
    refactored = refactored.replace("sorted_nums", "s")
    assert not check_scope_median_preserved(_ctx(files={"stats.py": refactored}))


def test_check_scope_mode_preserved_intact():
    assert check_scope_mode_preserved(_ctx(files={"stats.py": _STATS_FIXED}))


def test_check_scope_mode_preserved_removed():
    no_mode = "\n".join(
        line for line in _STATS_FIXED.splitlines() if "def mode" not in line
    )
    assert not check_scope_mode_preserved(_ctx(files={"stats.py": no_mode}))


def test_check_scope_no_new_functions_correct():
    assert check_scope_no_new_functions(_ctx(files={"stats.py": _STATS_FIXED}))


def test_check_scope_no_new_functions_extra_added():
    with_extra = _STATS_FIXED + "\ndef variance(numbers):\n    return 0.0\n"
    assert not check_scope_no_new_functions(_ctx(files={"stats.py": with_extra}))


# ── add-logging ───────────────────────────────────────────────────────────────

_PROCESSOR_ORIGINAL = """\
\"\"\"Data record processor.\"\"\"


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            return None
        record["processed"] = value * 2
        return True
    except (KeyError, ValueError):
        return None
"""

_PROCESSOR_WITH_LOGGING = """\
\"\"\"Data record processor.\"\"\"
import logging


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            logging.error("Negative value: %s", value)
            return None
        record["processed"] = value * 2
        logging.debug("Processed record: value=%s", value)
        return True
    except (KeyError, ValueError) as e:
        logging.error("Invalid record: %s", e)
        return None
"""

_PROCESSOR_WITH_PRINT = """\
\"\"\"Data record processor.\"\"\"


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            print(f"Error: negative value {value}")
            return None
        record["processed"] = value * 2
        print(f"Processed: {value}")
        return True
    except (KeyError, ValueError) as e:
        print(f"Error: {e}")
        return None
"""

_PROCESSOR_MISSING_DEBUG = """\
\"\"\"Data record processor.\"\"\"
import logging


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            logging.error("Negative value: %s", value)
            return None
        record["processed"] = value * 2
        return True
    except (KeyError, ValueError) as e:
        logging.error("Invalid record: %s", e)
        return None
"""

# Named-logger style: logger = logging.getLogger(__name__); logger.error(...)
_PROCESSOR_NAMED_LOGGER = """\
\"\"\"Data record processor.\"\"\"
import logging

logger = logging.getLogger(__name__)


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            logger.error("Negative value: %s", value)
            return None
        record["processed"] = value * 2
        logger.debug("Processed record: value=%s", value)
        return True
    except (KeyError, ValueError) as e:
        logger.error("Invalid record: %s", e)
        return None
"""

# from-import style: from logging import getLogger
_PROCESSOR_FROM_IMPORT = """\
\"\"\"Data record processor.\"\"\"
from logging import getLogger

logger = getLogger(__name__)


def process_record(record):
    try:
        value = int(record["value"])
        if value < 0:
            logger.error("Negative value: %s", value)
            return None
        record["processed"] = value * 2
        logger.debug("Processed record: value=%s", value)
        return True
    except (KeyError, ValueError) as e:
        logger.error("Invalid record: %s", e)
        return None
"""


def test_check_logging_tests_pass_success():
    assert check_logging_tests_pass(_ctx("4 passed", exit_code=0))


def test_check_logging_tests_pass_failure():
    assert not check_logging_tests_pass(_ctx("1 failed", exit_code=1))


def test_check_logging_module_imported_present():
    assert check_logging_module_imported(
        _ctx(files={"processor.py": _PROCESSOR_WITH_LOGGING})
    )


def test_check_logging_module_imported_missing():
    assert not check_logging_module_imported(
        _ctx(files={"processor.py": _PROCESSOR_ORIGINAL})
    )


def test_check_logging_error_level_used_present():
    assert check_logging_error_level_used(
        _ctx(files={"processor.py": _PROCESSOR_WITH_LOGGING})
    )


def test_check_logging_error_level_used_missing():
    assert not check_logging_error_level_used(
        _ctx(files={"processor.py": _PROCESSOR_ORIGINAL})
    )


def test_check_logging_no_print_clean():
    assert check_logging_no_print(_ctx(files={"processor.py": _PROCESSOR_WITH_LOGGING}))


def test_check_logging_no_print_uses_print():
    assert not check_logging_no_print(
        _ctx(files={"processor.py": _PROCESSOR_WITH_PRINT})
    )


def test_check_logging_debug_or_info_used_present():
    assert check_logging_debug_or_info_used(
        _ctx(files={"processor.py": _PROCESSOR_WITH_LOGGING})
    )


def test_check_logging_debug_or_info_used_missing():
    assert not check_logging_debug_or_info_used(
        _ctx(files={"processor.py": _PROCESSOR_MISSING_DEBUG})
    )


# Named-logger pattern: logger = logging.getLogger(__name__); logger.error(...)
def test_check_logging_error_level_named_logger():
    """Named-logger pattern (logger.error) should be accepted."""
    assert check_logging_error_level_used(
        _ctx(files={"processor.py": _PROCESSOR_NAMED_LOGGER})
    )


def test_check_logging_debug_named_logger():
    """Named-logger pattern (logger.debug) should be accepted."""
    assert check_logging_debug_or_info_used(
        _ctx(files={"processor.py": _PROCESSOR_NAMED_LOGGER})
    )


def test_check_logging_module_imported_from_style():
    """from logging import ... should be accepted."""
    assert check_logging_module_imported(
        _ctx(files={"processor.py": _PROCESSOR_FROM_IMPORT})
    )


def test_check_logging_error_level_from_import_named_logger():
    """from-import + named logger pattern should be accepted."""
    assert check_logging_error_level_used(
        _ctx(files={"processor.py": _PROCESSOR_FROM_IMPORT})
    )


def test_check_logging_debug_from_import_named_logger():
    """from-import + named logger debug should be accepted."""
    assert check_logging_debug_or_info_used(
        _ctx(files={"processor.py": _PROCESSOR_FROM_IMPORT})
    )


# ── use-existing-helper fixtures ─────────────────────────────────────────────

_UTILS_SRC = """\
\"\"\"Shared utility functions.\"\"\"

import re


def normalize_email(email: str) -> str:
    \"\"\"Normalize an email address: strip surrounding whitespace and lowercase.\"\"\"
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    \"\"\"Return True if *email* looks like a valid address (basic RFC check).\"\"\"
    return bool(re.match(r"[^@]+@[^@]+\\.[^@]+", email))
"""

_USERS_ORIGINAL = """\
\"\"\"User management module.\"\"\"


def create_user(email: str) -> dict:
    normalized = email.strip().lower()
    if "@" not in normalized or "." not in normalized.split("@")[-1]:
        raise ValueError(f"Invalid email: {email!r}")
    return {"email": normalized, "active": True}
"""

_USERS_REFACTORED = """\
\"\"\"User management module.\"\"\"

from utils import normalize_email, is_valid_email


def create_user(email: str) -> dict:
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise ValueError(f"Invalid email: {email!r}")
    return {"email": normalized, "active": True}
"""

_USERS_PARTIAL = """\
\"\"\"User management module.\"\"\"

from utils import normalize_email


def create_user(email: str) -> dict:
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.split("@")[-1]:
        raise ValueError(f"Invalid email: {email!r}")
    return {"email": normalized, "active": True}
"""


def test_check_reuse_tests_pass_ok():
    assert check_reuse_tests_pass(_ctx(stdout="5 passed", exit_code=0))


def test_check_reuse_tests_pass_failed():
    assert not check_reuse_tests_pass(_ctx(stdout="1 failed", exit_code=1))


def test_check_reuse_imports_utils_refactored():
    assert check_reuse_imports_utils(
        _ctx(files={"users.py": _USERS_REFACTORED, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_imports_utils_partial():
    assert check_reuse_imports_utils(
        _ctx(files={"users.py": _USERS_PARTIAL, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_imports_utils_original():
    assert not check_reuse_imports_utils(
        _ctx(files={"users.py": _USERS_ORIGINAL, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_uses_normalize_refactored():
    assert check_reuse_uses_normalize(
        _ctx(files={"users.py": _USERS_REFACTORED, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_uses_normalize_original():
    assert not check_reuse_uses_normalize(
        _ctx(files={"users.py": _USERS_ORIGINAL, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_no_inline_strip_refactored():
    assert check_reuse_no_inline_strip(
        _ctx(files={"users.py": _USERS_REFACTORED, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_no_inline_strip_original():
    assert not check_reuse_no_inline_strip(
        _ctx(files={"users.py": _USERS_ORIGINAL, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_utils_unchanged_intact():
    assert check_reuse_utils_unchanged(
        _ctx(files={"users.py": _USERS_REFACTORED, "utils.py": _UTILS_SRC})
    )


def test_check_reuse_utils_unchanged_missing_func():
    stripped = "\n".join(
        line for line in _UTILS_SRC.splitlines() if "def normalize_email" not in line
    )
    assert not check_reuse_utils_unchanged(
        _ctx(files={"users.py": _USERS_REFACTORED, "utils.py": stripped})
    )


# ── add-feature-preserve-default ─────────────────────────────────────────────

_TEXT_STATS_CORRECT = """\
def summarize(text, include_chars=False):
    if not text:
        result = {"words": 0, "lines": 0}
    else:
        words = len(text.split())
        lines = len(text.splitlines())
        result = {"words": words, "lines": lines}
    if include_chars:
        result["chars"] = len(text) if text else 0
    return result
"""

_TEXT_STATS_NO_DEFAULT = """\
def summarize(text, include_chars):
    if not text:
        return {"words": 0, "lines": 0}
    words = len(text.split())
    lines = len(text.splitlines())
    result = {"words": words, "lines": lines}
    if include_chars:
        result["chars"] = len(text)
    return result
"""

_TEXT_STATS_WRONG_DEFAULT = """\
def summarize(text, include_chars=True):
    if not text:
        return {"words": 0, "lines": 0}
    words = len(text.split())
    lines = len(text.splitlines())
    result = {"words": words, "lines": lines}
    if include_chars:
        result["chars"] = len(text)
    return result
"""

_TEST_WITH_MARKER_AND_NEW = """\
# Original tests — do not modify above this line
from text_stats import summarize

def test_basic():
    result = summarize("hello world\\nfoo bar")
    assert result == {"words": 4, "lines": 2}

def test_keys_only_words_lines():
    result = summarize("test")
    assert set(result.keys()) == {"words", "lines"}
# END_ORIGINAL_TESTS

def test_include_chars_true():
    result = summarize("hello world", include_chars=True)
    assert "chars" in result
    assert result["chars"] == 11
"""

_TEST_MARKER_REMOVED = """\
from text_stats import summarize

def test_basic():
    result = summarize("hello world\\nfoo bar")
    assert result == {"words": 4, "lines": 2}

def test_include_chars():
    result = summarize("hi", include_chars=True)
    assert result["chars"] == 2
"""


def test_check_compat_tests_pass_success():
    assert check_compat_tests_pass(_ctx("6 passed", exit_code=0))


def test_check_compat_tests_pass_failure():
    assert not check_compat_tests_pass(_ctx("1 failed, 5 passed", exit_code=1))


def test_check_compat_has_default_param_correct():
    assert check_compat_has_default_param(
        _ctx(files={"text_stats.py": _TEXT_STATS_CORRECT})
    )


def test_check_compat_has_default_param_no_default():
    assert not check_compat_has_default_param(
        _ctx(files={"text_stats.py": _TEXT_STATS_NO_DEFAULT})
    )


def test_check_compat_has_default_param_wrong_default():
    assert not check_compat_has_default_param(
        _ctx(files={"text_stats.py": _TEXT_STATS_WRONG_DEFAULT})
    )


def test_check_compat_has_default_param_kwonly():
    """include_chars as keyword-only arg should also be accepted."""
    source = """\
def summarize(text, *, include_chars=False):
    pass
"""
    assert check_compat_has_default_param(_ctx(files={"text_stats.py": source}))


def test_check_compat_original_tests_intact_present():
    assert check_compat_original_tests_intact(
        _ctx(files={"test_text_stats.py": _TEST_WITH_MARKER_AND_NEW})
    )


def test_check_compat_original_tests_intact_removed():
    assert not check_compat_original_tests_intact(
        _ctx(files={"test_text_stats.py": _TEST_MARKER_REMOVED})
    )


def test_check_compat_new_tests_exist_present():
    assert check_compat_new_tests_exist(
        _ctx(files={"test_text_stats.py": _TEST_WITH_MARKER_AND_NEW})
    )


def test_check_compat_new_tests_exist_absent():
    test_no_new = """\
from text_stats import summarize
def test_basic():
    result = summarize("hello")
    assert result == {"words": 1, "lines": 1}
# END_ORIGINAL_TESTS
"""
    assert not check_compat_new_tests_exist(
        _ctx(files={"test_text_stats.py": test_no_new})
    )


def test_check_compat_new_tests_exist_comment_only():
    """String match would pass on a comment; AST check should not."""
    test_comment_only = """\
from text_stats import summarize
def test_basic():
    result = summarize("hello")
    assert result == {"words": 1, "lines": 1}
# END_ORIGINAL_TESTS
# TODO: add include_chars=True test later
"""
    assert not check_compat_new_tests_exist(
        _ctx(files={"test_text_stats.py": test_comment_only})
    )


# ── handle-specific-exception checker tests ───────────────────────────────────

_CONFIG_BROAD_EXCEPT = """\
import json

def parse_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        pass
    return {}
"""

_CONFIG_BARE_EXCEPT = """\
import json

def parse_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        pass
    return {}
"""

_CONFIG_FIXED = """\
import json

def parse_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}
"""

_CONFIG_FIXED_IMPORTED = """\
import json
from json import JSONDecodeError

def parse_config(path):
    try:
        with open(path) as f:
            return json.load(f)
    except JSONDecodeError:
        return {}
"""

_TEST_WITH_FILE_ERROR = """\
import pytest
from config import parse_config

def test_valid_config():
    pass

def test_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_config("/nonexistent/path.json")
"""

_TEST_WITHOUT_FILE_ERROR = """\
from config import parse_config

def test_valid_config():
    pass
"""


def test_check_config_no_bare_except_broad():
    assert not check_config_no_bare_except(
        _ctx(files={"config.py": _CONFIG_BROAD_EXCEPT})
    )


def test_check_config_no_bare_except_bare():
    assert not check_config_no_bare_except(
        _ctx(files={"config.py": _CONFIG_BARE_EXCEPT})
    )


def test_check_config_no_bare_except_fixed():
    assert check_config_no_bare_except(_ctx(files={"config.py": _CONFIG_FIXED}))


def test_check_config_catches_json_error_broad():
    assert not check_config_catches_json_error(
        _ctx(files={"config.py": _CONFIG_BROAD_EXCEPT})
    )


def test_check_config_catches_json_error_fixed():
    assert check_config_catches_json_error(_ctx(files={"config.py": _CONFIG_FIXED}))


def test_check_config_catches_json_error_imported():
    assert check_config_catches_json_error(
        _ctx(files={"config.py": _CONFIG_FIXED_IMPORTED})
    )


def test_check_config_propagates_file_error_present():
    assert check_config_propagates_file_error(
        _ctx(files={"test_config.py": _TEST_WITH_FILE_ERROR})
    )


def test_check_config_propagates_file_error_missing():
    assert not check_config_propagates_file_error(
        _ctx(files={"test_config.py": _TEST_WITHOUT_FILE_ERROR})
    )


def test_check_config_tests_pass_ok():
    assert check_config_tests_pass(_ctx(exit_code=0, stdout="2 passed"))


def test_check_config_tests_pass_fail():
    assert not check_config_tests_pass(_ctx(exit_code=1, stdout="1 failed"))


# ── fix-security-path-traversal checkers ──────────────────────────────────────

_SERVER_FIXED = """\
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
        raise ValueError(f"Path escapes base directory: {filename!r}")

    with open(filepath, "rb") as f:
        return f.read()
"""

_SERVER_UNFIXED = """\
\"\"\"Minimal static file server.\"\"\"

import os


def serve_file(base_dir: str, filename: str) -> bytes:
    \"\"\"Return the contents of *filename* from *base_dir*.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if *filename* is absolute.
    \"\"\"
    if os.path.isabs(filename):
        raise ValueError(f"Absolute paths not allowed: {filename!r}")

    filepath = os.path.join(base_dir, filename)
    with open(filepath, "rb") as f:
        return f.read()
"""

_TEST_WITH_TRAVERSAL = """\
import os
import pytest
from server import serve_file


def test_serves_existing_file(tmp_path):
    (tmp_path / "hello.txt").write_bytes(b"hello world")
    assert serve_file(str(tmp_path), "hello.txt") == b"hello world"


def test_rejects_absolute_path(tmp_path):
    with pytest.raises(ValueError):
        serve_file(str(tmp_path), "/etc/passwd")


def test_blocks_path_traversal(tmp_path):
    \"\"\"Requesting ../../etc/passwd must raise ValueError.\"\"\"
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        serve_file(str(safe_dir), "../../etc/passwd")
"""


def test_check_security_tests_pass_ok():
    assert check_security_tests_pass(_ctx(exit_code=0, stdout="3 passed"))


def test_check_security_tests_pass_fail():
    assert not check_security_tests_pass(_ctx(exit_code=1, stdout="1 failed"))


def test_check_security_uses_realpath_fixed():
    assert check_security_uses_realpath(_ctx(files={"server.py": _SERVER_FIXED}))


def test_check_security_uses_realpath_unfixed():
    assert not check_security_uses_realpath(_ctx(files={"server.py": _SERVER_UNFIXED}))


def test_check_security_blocks_traversal_fixed():
    assert check_security_blocks_traversal(_ctx(files={"server.py": _SERVER_FIXED}))


def test_check_security_blocks_traversal_unfixed():
    assert not check_security_blocks_traversal(
        _ctx(files={"server.py": _SERVER_UNFIXED})
    )


def test_check_security_no_direct_join_fixed():
    assert check_security_no_direct_join(_ctx(files={"server.py": _SERVER_FIXED}))


def test_check_security_no_direct_join_unfixed():
    assert not check_security_no_direct_join(_ctx(files={"server.py": _SERVER_UNFIXED}))


def test_check_security_has_traversal_test_present():
    assert check_security_has_traversal_test(
        _ctx(files={"test_server.py": _TEST_WITH_TRAVERSAL})
    )


def test_check_security_has_traversal_test_missing():
    assert not check_security_has_traversal_test(
        _ctx(files={"test_server.py": "no traversal test here"})
    )


# ── refactor-for-testability checkers ────────────────────────────────────────

_REPORT_ORIGINAL = '''\
import csv


def generate_report(filepath):
    """Generate summary statistics from a CSV file."""
    rows = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

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
'''

_REPORT_REFACTORED = '''\
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
'''

_TEST_WITH_PURE_FUNCTION = """\
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

def test_compute_stats_empty():
    assert compute_stats([]) == {"total_rows": 0}
"""

_TEST_WITHOUT_PURE_FUNCTION = """\
import tempfile

def test_generate_report(tmp_path):
    # only tests the I/O function using files, no pure function test
    tmp_path.joinpath("data.csv").write_text("name,score,hours\\nAlice,90,5.5\\n")
    result = generate_report(str(tmp_path / "data.csv"))
    assert result["total_rows"] == 1
"""


def test_check_testability_tests_pass_ok():
    assert check_testability_tests_pass(_ctx(exit_code=0, stdout="5 passed"))


def test_check_testability_tests_pass_fail():
    assert not check_testability_tests_pass(_ctx(exit_code=1, stdout="1 failed"))


def test_check_testability_has_pure_function_refactored():
    assert check_testability_has_pure_function(
        _ctx(files={"report.py": _REPORT_REFACTORED})
    )


def test_check_testability_has_pure_function_original():
    assert not check_testability_has_pure_function(
        _ctx(files={"report.py": _REPORT_ORIGINAL})
    )


def test_check_testability_pure_function_tested_present():
    assert check_testability_pure_function_tested(
        _ctx(
            files={
                "report.py": _REPORT_REFACTORED,
                "test_report.py": _TEST_WITH_PURE_FUNCTION,
            }
        )
    )


def test_check_testability_pure_function_tested_missing():
    assert not check_testability_pure_function_tested(
        _ctx(
            files={
                "report.py": _REPORT_REFACTORED,
                "test_report.py": _TEST_WITHOUT_PURE_FUNCTION,
            }
        )
    )


def test_check_testability_generate_report_preserved_yes():
    assert check_testability_generate_report_preserved(
        _ctx(files={"report.py": _REPORT_REFACTORED})
    )


def test_check_testability_generate_report_preserved_no():
    assert not check_testability_generate_report_preserved(
        _ctx(files={"report.py": "# nothing here"})
    )


def test_check_testability_no_file_io_in_unit_tests_good():
    assert check_testability_no_file_io_in_unit_tests(
        _ctx(files={"test_report.py": _TEST_WITH_PURE_FUNCTION})
    )


def test_check_testability_no_file_io_in_unit_tests_bad():
    assert not check_testability_no_file_io_in_unit_tests(
        _ctx(files={"test_report.py": _TEST_WITHOUT_PURE_FUNCTION})
    )


# ── add-type-hints fixtures ──────────────────────────────────────────────────

_DATASTORE_UNTYPED = """\
class DataStore:
    def __init__(self):
        self.data = {}
        self.metadata = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def delete(self, key):
        if key in self.data:
            del self.data[key]
            return True
        return False

    def keys(self):
        return list(self.data.keys())

    def items(self):
        return list(self.data.items())

    def count(self):
        return len(self.data)
"""


_DATASTORE_TYPED = """\
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

    def count(self) -> int:
        return len(self.data)
"""


_DATASTORE_PARTIALLY_TYPED = """\
class DataStore:
    def __init__(self):
        self.data: dict[str, int] = {}

    def set(self, key: str, value: int) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)
"""


# ── add-type-hints tests ─────────────────────────────────────────────────────


def test_check_typehints_mypy_passes_yes():
    assert check_typehints_mypy_passes(_ctx(stdout="mypy: success", exit_code=0))


def test_check_typehints_mypy_passes_no():
    assert not check_typehints_mypy_passes(
        _ctx(stdout="error: incompatible type", exit_code=1)
    )


def test_check_typehints_function_params_annotated_typed():
    assert check_typehints_function_params_annotated(
        _ctx(files={"datastore.py": _DATASTORE_TYPED})
    )


def test_check_typehints_function_params_annotated_untyped():
    assert not check_typehints_function_params_annotated(
        _ctx(files={"datastore.py": _DATASTORE_UNTYPED})
    )


def test_check_typehints_function_params_annotated_partial():
    assert not check_typehints_function_params_annotated(
        _ctx(files={"datastore.py": _DATASTORE_PARTIALLY_TYPED})
    )


def test_check_typehints_function_returns_annotated_typed():
    assert check_typehints_function_returns_annotated(
        _ctx(files={"datastore.py": _DATASTORE_TYPED})
    )


def test_check_typehints_function_returns_annotated_untyped():
    assert not check_typehints_function_returns_annotated(
        _ctx(files={"datastore.py": _DATASTORE_UNTYPED})
    )


def test_check_typehints_uses_generic_collection_yes():
    assert check_typehints_uses_generic_collection(
        _ctx(files={"datastore.py": _DATASTORE_TYPED})
    )


def test_check_typehints_uses_generic_collection_no():
    assert not check_typehints_uses_generic_collection(
        _ctx(files={"datastore.py": _DATASTORE_UNTYPED})
    )


def test_check_typehints_class_attribute_annotated_yes():
    assert check_typehints_class_attribute_annotated(
        _ctx(files={"datastore.py": _DATASTORE_TYPED})
    )


def test_check_typehints_class_attribute_annotated_no():
    assert not check_typehints_class_attribute_annotated(
        _ctx(files={"datastore.py": _DATASTORE_UNTYPED})
    )
