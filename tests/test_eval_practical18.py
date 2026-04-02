from types import SimpleNamespace

from gptme.eval.suites.practical18 import (
    check_histogram_only_int,
    check_histogram_result,
    check_knight_all_pass,
    check_knight_has_function,
    check_minstack_all_pass,
    check_minstack_has_class,
    check_minstack_has_methods,
)


def _ctx(stdout: str, *, files: dict[str, str] | None = None, exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


# --- min-stack checks ---


def test_minstack_all_pass():
    assert check_minstack_all_pass(_ctx("All 9 assertions passed."))
    assert not check_minstack_all_pass(_ctx("FAIL: get_min() should return 3"))


def test_minstack_has_class():
    src = "class MinStack:\n    def __init__(self):\n        pass"
    assert check_minstack_has_class(_ctx("", files={"min_stack.py": src}))
    assert not check_minstack_has_class(_ctx("", files={"min_stack.py": "def push():"}))


def test_minstack_has_methods():
    src = (
        "class MinStack:\n"
        "    def push(self, val): pass\n"
        "    def pop(self): pass\n"
        "    def top(self): pass\n"
        "    def get_min(self): pass\n"
    )
    assert check_minstack_has_methods(_ctx("", files={"min_stack.py": src}))
    # Missing get_min
    src_no_min = (
        "class MinStack:\n"
        "    def push(self, val): pass\n"
        "    def pop(self): pass\n"
        "    def top(self): pass\n"
    )
    assert not check_minstack_has_methods(_ctx("", files={"min_stack.py": src_no_min}))


# --- knight-moves checks ---


def test_knight_all_pass():
    assert check_knight_all_pass(_ctx("All 6 cases passed."))
    assert not check_knight_all_pass(_ctx("FAIL: min_knight_moves('a1', 'h8')"))


def test_knight_has_function():
    src = "def min_knight_moves(source, dest):\n    pass"
    assert check_knight_has_function(_ctx("", files={"knight.py": src}))
    assert not check_knight_has_function(_ctx("", files={"knight.py": "def solve():"}))


# --- histogram-area checks ---


def test_histogram_result():
    assert check_histogram_result(_ctx("10"))
    assert check_histogram_result(_ctx("10\n"))
    assert check_histogram_result(_ctx("result: 10"))
    assert not check_histogram_result(_ctx("100"))
    assert not check_histogram_result(_ctx("5"))


def test_histogram_only_int():
    assert check_histogram_only_int(_ctx("10"))
    assert check_histogram_only_int(_ctx("10\n"))
    assert not check_histogram_only_int(_ctx("result: 10"))
    assert not check_histogram_only_int(_ctx("10 (largest area)"))
