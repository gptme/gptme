from types import SimpleNamespace

from gptme.eval.suites.behavioral import (
    check_error_handling_parse_csv,
    check_error_handling_safe_divide,
    check_error_handling_tests_pass,
    check_error_handling_to_int,
    check_extract_callers_import,
    check_extract_no_duplication,
    check_extract_shared_module_exists,
    check_merge_commit_completed,
    check_merge_no_conflict_markers,
)


def _ctx(
    stdout: str = "",
    *,
    files: dict[str, str] | None = None,
    exit_code: int = 0,
):
    return SimpleNamespace(
        stdout=stdout,
        files=files or {},
        stderr="",
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


def test_check_merge_commit_completed_requires_merge_head_absent():
    assert check_merge_commit_completed(_ctx(files={".git/HEAD": "ref: refs/heads/master\n"}))
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
    assert not check_error_handling_tests_pass(_ctx(files={}, stdout="collected 3 items"))
    assert not check_error_handling_tests_pass(_ctx(files={}, stdout="1 failed, 2 passed"))


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
