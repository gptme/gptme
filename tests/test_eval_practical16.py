from types import SimpleNamespace

from gptme.eval.suites.practical16 import (
    check_queue_all_producers_mentioned,
    check_schema_record1_multiple_errors,
    check_trie_app_prefix,
)


def _ctx(stdout: str, *, files: dict[str, str] | None = None, exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


def test_check_queue_all_producers_mentioned_accepts_equals_format():
    stdout = (
        "Consumer 1 processed item (producer=0, value=3)\n"
        "Consumer 2 processed item (producer=1, value=7)\n"
        "Consumer 1 processed item (producer=2, value=11)"
    )
    assert check_queue_all_producers_mentioned(_ctx(stdout))


def test_check_schema_record1_multiple_errors_does_not_count_bare_at_symbol():
    stdout = "Record 0 valid: alice@example.com\nRecord 1 invalid: age below 0"
    assert not check_schema_record1_multiple_errors(_ctx(stdout))


def test_check_trie_app_prefix_requires_whole_words():
    stdout = "Words with prefix app: apple, application"
    assert not check_trie_app_prefix(_ctx(stdout))


def test_check_trie_app_prefix_accepts_canonical_output():
    stdout = (
        "Words with prefix 'app': app, apple, application\n"
        "Words with prefix 'ban': banana, band\n"
        "Total words: 5\n"
    )
    assert check_trie_app_prefix(_ctx(stdout))
