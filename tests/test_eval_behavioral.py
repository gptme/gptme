from types import SimpleNamespace

from gptme.eval.suites.behavioral import (
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
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


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
