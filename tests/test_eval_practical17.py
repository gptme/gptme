from types import SimpleNamespace

from gptme.eval.suites.practical17 import (
    check_base_all_pass,
    check_base_has_function,
    check_lru_all_pass,
    check_lru_has_class,
    check_lru_has_get_put,
    check_merge_count,
    check_merge_first_interval,
    check_merge_isolated,
    check_merge_point_interval,
    check_merge_second_overlap,
)


def _ctx(stdout: str, *, files: dict[str, str] | None = None, exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


# --- LRU cache checks ---


def test_lru_all_pass():
    assert check_lru_all_pass(_ctx("All 8 assertions passed."))
    assert not check_lru_all_pass(_ctx("FAIL: get(1) should return 'one'"))


def test_lru_has_class():
    src = "class LRUCache:\n    def __init__(self, capacity):\n        pass"
    assert check_lru_has_class(_ctx("", files={"lru_cache.py": src}))
    assert not check_lru_has_class(_ctx("", files={"lru_cache.py": "def lru():"}))


def test_lru_has_get_put():
    src = "class LRUCache:\n    def get(self, key): pass\n    def put(self, key, val): pass"
    assert check_lru_has_get_put(_ctx("", files={"lru_cache.py": src}))
    assert not check_lru_has_get_put(
        _ctx("", files={"lru_cache.py": "def get(self, k): pass"})
    )
    assert not check_lru_has_get_put(
        _ctx("", files={"lru_cache.py": "def put(self, k, v): pass"})
    )


# --- Interval merge checks ---


def test_merge_first_interval_bracket_format():
    assert check_merge_first_interval(_ctx("[[1, 7], [8, 10], [15, 20], [25, 25]]"))


def test_merge_first_interval_dash_format():
    assert check_merge_first_interval(_ctx("1-7\n8-10\n15-20\n25-25"))


def test_merge_isolated():
    assert check_merge_isolated(_ctx("[[1, 7], [8, 10], [15, 20], [25, 25]]"))
    assert not check_merge_isolated(_ctx("[[1, 7], [15, 20], [25, 25]]"))


def test_merge_second_overlap():
    assert check_merge_second_overlap(_ctx("[[1, 7], [8, 10], [15, 20], [25, 25]]"))


def test_merge_point_interval():
    assert check_merge_point_interval(_ctx("[[1, 7], [8, 10], [15, 20], [25, 25]]"))


def test_merge_count_json():
    assert check_merge_count(_ctx("[[1, 7], [8, 10], [15, 20], [25, 25]]"))
    assert not check_merge_count(_ctx("[[1, 7], [8, 10], [15, 20]]"))


def test_merge_count_non_json():
    assert check_merge_count(_ctx("[1, 7]\n[8, 10]\n[15, 20]\n[25, 25]"))


# --- Base converter checks ---


def test_base_all_pass():
    assert check_base_all_pass(_ctx("All 7 assertions passed."))
    assert not check_base_all_pass(_ctx("FAIL: convert('255', 10, 2)"))


def test_base_has_function():
    src = "def convert(value, from_base, to_base):\n    pass"
    assert check_base_has_function(_ctx("", files={"base_convert.py": src}))
