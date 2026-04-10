from types import SimpleNamespace

from gptme.eval.suites.practical19 import (
    check_bst_all_pass,
    check_bst_has_class,
    check_bst_has_methods,
    check_coinchange_all_pass,
    check_coinchange_has_function,
    check_editdist_all_pass,
    check_editdist_has_function,
)


def _ctx(stdout: str, *, files: dict[str, str] | None = None, exit_code: int = 0):
    return SimpleNamespace(stdout=stdout, files=files or {}, exit_code=exit_code)


# --- edit-distance checks ---


def test_editdist_all_pass():
    assert check_editdist_all_pass(_ctx("All 8 edit-distance cases passed."))
    assert not check_editdist_all_pass(_ctx("FAIL: edit_distance('kitten', 'sitting')"))
    # must not match coin-change output
    assert not check_editdist_all_pass(_ctx("All 8 coin-change cases passed."))


def test_editdist_has_function():
    src = "def edit_distance(s1, s2):\n    pass"
    assert check_editdist_has_function(_ctx("", files={"edit_distance.py": src}))
    assert not check_editdist_has_function(
        _ctx("", files={"edit_distance.py": "def solve():"})
    )


# --- bst-operations checks ---


def test_bst_all_pass():
    assert check_bst_all_pass(_ctx("All 10 assertions passed."))
    assert not check_bst_all_pass(
        _ctx("FAIL: in_order() should return [1,3,4,5,6,7,8]")
    )


def test_bst_has_class():
    src = "class BST:\n    def __init__(self):\n        pass"
    assert check_bst_has_class(_ctx("", files={"bst.py": src}))
    assert not check_bst_has_class(_ctx("", files={"bst.py": "def insert():"}))


def test_bst_has_methods():
    src = (
        "class BST:\n"
        "    def insert(self, val): pass\n"
        "    def search(self, val): pass\n"
        "    def in_order(self): pass\n"
    )
    assert check_bst_has_methods(_ctx("", files={"bst.py": src}))
    # Missing in_order
    src_no_inorder = (
        "class BST:\n    def insert(self, val): pass\n    def search(self, val): pass\n"
    )
    assert not check_bst_has_methods(_ctx("", files={"bst.py": src_no_inorder}))


# --- coin-change checks ---


def test_coinchange_all_pass():
    assert check_coinchange_all_pass(_ctx("All 8 coin-change cases passed."))
    assert not check_coinchange_all_pass(_ctx("FAIL: coin_change([2], 3)"))
    # must not match edit-distance output
    assert not check_coinchange_all_pass(_ctx("All 8 edit-distance cases passed."))


def test_coinchange_has_function():
    src = "def coin_change(coins, amount):\n    pass"
    assert check_coinchange_has_function(_ctx("", files={"coin_change.py": src}))
    assert not check_coinchange_has_function(
        _ctx("", files={"coin_change.py": "def solve():"})
    )
