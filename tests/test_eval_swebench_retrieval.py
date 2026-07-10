"""Tests for the local SWE-bench file-retrieval pipeline."""

from pathlib import Path

from gptme.eval.swebench.retrieval import (
    build_embedded_content_prompt,
    extract_keywords,
    rank_source_files,
    retrieve_files_for_prompt,
)


def test_extract_keywords_preserves_specific_identifiers():
    keywords = extract_keywords(
        "Django's UserValidationError comes from validate_user_name in auth.models."
    )

    assert "UserValidationError" in keywords
    assert "validate_user_name" in keywords
    assert "Django" not in keywords


def test_rank_source_files_excludes_non_source_paths():
    ranked = rank_source_files(
        {
            "django/contrib/auth/validators.py": 3,
            "django/contrib/auth/tests/test_validators.py": 4,
            "django/contrib/auth/migrations/0001_initial.py": 5,
            "docs/validators.py": 6,
        }
    )

    assert ranked == [("django/contrib/auth/validators.py", 3)]


def test_build_embedded_content_prompt_embeds_only_existing_files(tmp_path: Path):
    source = tmp_path / "django" / "contrib" / "auth" / "validators.py"
    source.parent.mkdir(parents=True)
    source.write_text("class UserValidationError(Exception): pass\n")

    prompt = build_embedded_content_prompt(
        "Fix UserValidationError handling.",
        [
            ("django/contrib/auth/validators.py", 2),
            ("django/contrib/auth/missing.py", 1),
        ],
        tmp_path,
    )

    assert "UserValidationError" in prompt
    assert "django/contrib/auth/validators.py" in prompt
    assert "django/contrib/auth/missing.py" not in prompt


def test_retrieve_files_for_prompt_filters_tests_and_embeds_source(tmp_path: Path):
    source = tmp_path / "django" / "contrib" / "auth" / "validators.py"
    test = tmp_path / "django" / "contrib" / "auth" / "tests" / "test_validators.py"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("class UserValidationError(Exception): pass\n")
    test.write_text("from validators import UserValidationError\n")

    prompt = retrieve_files_for_prompt(
        tmp_path,
        "Fix the UserValidationError raised by account validation.",
    )

    assert "django/contrib/auth/validators.py" in prompt
    assert "django/contrib/auth/tests/test_validators.py" not in prompt
