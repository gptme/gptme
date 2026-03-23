"""Practical eval tests (batch 13) — descriptive statistics, Pascal's triangle, Caesar cipher.

Tests capabilities not covered by earlier practical suites:
- Descriptive statistics (mean, median, mode, range) from a numbers file
- Pascal's triangle generation for N rows
- Caesar cipher encoding with wrap-around
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- summary-stats checks ---

_NUMBERS_TXT = """\
4
7
13
2
7
1
8
7
4
9
"""
# mean=6.2, median=7.0, mode=7, range=12


def check_stats_file(ctx):
    """stats.py should exist."""
    return "stats.py" in ctx.files


def check_stats_mean(ctx):
    """stdout should contain 'mean: 6.2' (trailing zeros accepted, e.g. 6.20)."""
    return bool(re.search(r"\bmean:\s*6\.20*\b", ctx.stdout, re.IGNORECASE))


def check_stats_median(ctx):
    """stdout should contain 'median: 7', '7.0', '7.00', etc."""
    return bool(
        re.search(r"\bmedian:\s*7(?:\.0+)?(?![\d.])", ctx.stdout, re.IGNORECASE)
    )


def check_stats_mode(ctx):
    """stdout should contain 'mode: 7'."""
    return bool(re.search(r"\bmode:\s*7\b", ctx.stdout, re.IGNORECASE))


def check_stats_range(ctx):
    """stdout should contain 'range: 12'."""
    return bool(re.search(r"\brange:\s*12\b", ctx.stdout, re.IGNORECASE))


def check_stats_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- pascal-triangle checks ---
# N=6 rows:
#   1
#   1 1
#   1 2 1
#   1 3 3 1
#   1 4 6 4 1
#   1 5 10 10 5 1


def check_pascal_file(ctx):
    """pascal.py should exist."""
    return "pascal.py" in ctx.files


def check_pascal_row1(ctx):
    """First row should be '1' (a line containing only '1')."""
    return bool(re.search(r"^\s*1\s*$", ctx.stdout, re.MULTILINE))


def check_pascal_row3(ctx):
    """Row 3 should contain '1 2 1'."""
    return bool(re.search(r"\b1\s+2\s+1\b", ctx.stdout))


def check_pascal_row5(ctx):
    """Row 5 should contain '1 4 6 4 1'."""
    return bool(re.search(r"\b1\s+4\s+6\s+4\s+1\b", ctx.stdout))


def check_pascal_row6(ctx):
    """Row 6 should contain '1 5 10 10 5 1'."""
    return bool(re.search(r"\b1\s+5\s+10\s+10\s+5\s+1\b", ctx.stdout))


def check_pascal_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- caesar-cipher checks ---
# Input: "Hello World" with shift=13 (ROT13)
# Output: "Uryyb Jbeyq"

_MESSAGE_TXT = "Hello World\n"


def check_cipher_file(ctx):
    """cipher.py should exist."""
    return "cipher.py" in ctx.files


def check_cipher_hello(ctx):
    """'Hello' should be encoded to 'Uryyb'."""
    return "Uryyb" in ctx.stdout


def check_cipher_world(ctx):
    """'World' should be encoded to 'Jbeyq'."""
    return "Jbeyq" in ctx.stdout


def check_cipher_full(ctx):
    """Full output should contain 'Uryyb Jbeyq'."""
    return "Uryyb Jbeyq" in ctx.stdout


def check_cipher_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "summary-stats",
        "files": {"numbers.txt": _NUMBERS_TXT},
        "run": "python stats.py",
        "prompt": (
            "Write a Python script `stats.py` that reads `numbers.txt` (one integer "
            "per line) and computes descriptive statistics. Output each stat on its "
            "own line in the format 'STAT: VALUE'. Compute: mean (as float, e.g. "
            "6.2), median (as float), mode (integer, the most frequent value), and "
            "range (max minus min). Use labels 'mean', 'median', 'mode', 'range' "
            "(case-insensitive)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "stats.py exists": check_stats_file,
            "mean: 6.2": check_stats_mean,
            "median: 7.0": check_stats_median,
            "mode: 7": check_stats_mode,
            "range: 12": check_stats_range,
            "clean exit": check_stats_exit,
        },
    },
    {
        "name": "pascal-triangle",
        "files": {},
        "run": "python pascal.py",
        "prompt": (
            "Write a Python script `pascal.py` that prints the first 6 rows of "
            "Pascal's triangle, one row per line. Each row should list the values "
            "separated by spaces. Row 1 is '1', row 2 is '1 1', row 3 is '1 2 1', "
            "and so on."
        ),
        "tools": ["save", "shell"],
        "expect": {
            "pascal.py exists": check_pascal_file,
            "row 1: 1": check_pascal_row1,
            "row 3: 1 2 1": check_pascal_row3,
            "row 5: 1 4 6 4 1": check_pascal_row5,
            "row 6: 1 5 10 10 5 1": check_pascal_row6,
            "clean exit": check_pascal_exit,
        },
    },
    {
        "name": "caesar-cipher",
        "files": {"message.txt": _MESSAGE_TXT},
        "run": "python cipher.py",
        "prompt": (
            "Write a Python script `cipher.py` that reads `message.txt` and encodes "
            "it using a Caesar cipher with a shift of 13 (ROT13). Only shift "
            "alphabetic characters (A-Z, a-z); leave spaces and other characters "
            "unchanged. Preserve case. Print the encoded text."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "cipher.py exists": check_cipher_file,
            "'Hello' -> 'Uryyb'": check_cipher_hello,
            "'World' -> 'Jbeyq'": check_cipher_world,
            "full: 'Uryyb Jbeyq'": check_cipher_full,
            "clean exit": check_cipher_exit,
        },
    },
]
