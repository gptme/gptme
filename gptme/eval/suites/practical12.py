"""Practical eval tests (batch 12) — word frequency, Collatz sequences, and log-level stats.

Tests capabilities not covered by earlier practical suites:
- Word frequency analysis with case normalization and punctuation stripping (text processing)
- Collatz sequence length calculation for multiple inputs (algorithmic + numeric)
- Log-level statistics from a structured log file (log parsing + aggregation)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- word-frequency checks ---

_WORDS_TXT = """\
the cat sat on the mat the cat sat
the dog ran past the mat the dog ran
"""


def check_word_freq_file(ctx):
    """word_freq.py should exist."""
    return "word_freq.py" in ctx.files


def check_word_freq_the(ctx):
    """stdout should contain 'the: 6' (most frequent word)."""
    return bool(re.search(r"\bthe:\s*6\b", ctx.stdout))


def check_word_freq_cat(ctx):
    """stdout should contain 'cat: 2'."""
    return bool(re.search(r"\bcat:\s*2\b", ctx.stdout))


def check_word_freq_dog(ctx):
    """stdout should contain 'dog: 2'."""
    return bool(re.search(r"\bdog:\s*2\b", ctx.stdout))


def check_word_freq_mat(ctx):
    """stdout should contain 'mat: 2'."""
    return bool(re.search(r"\bmat:\s*2\b", ctx.stdout))


def check_word_freq_ran(ctx):
    """stdout should contain 'ran: 2'."""
    return bool(re.search(r"\bran:\s*2\b", ctx.stdout))


def check_word_freq_top5_order(ctx):
    """'the' should appear before 'cat' in output (sorted by freq desc)."""
    idx_the = ctx.stdout.find("the:")
    idx_cat = ctx.stdout.find("cat:")
    return idx_the < idx_cat if idx_the >= 0 and idx_cat >= 0 else False


def check_word_freq_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- collatz-sequence checks ---

_NUMBERS_TXT = """\
6
11
16
27
"""


def check_collatz_file(ctx):
    """collatz.py should exist."""
    return "collatz.py" in ctx.files


def check_collatz_six(ctx):
    """stdout should contain '6: 8' (6 takes 8 steps to reach 1)."""
    return bool(re.search(r"\b6:\s*8\b", ctx.stdout))


def check_collatz_eleven(ctx):
    """stdout should contain '11: 14' (11 takes 14 steps to reach 1)."""
    return bool(re.search(r"\b11:\s*14\b", ctx.stdout))


def check_collatz_sixteen(ctx):
    """stdout should contain '16: 4' (16 takes 4 steps to reach 1)."""
    return bool(re.search(r"\b16:\s*4\b", ctx.stdout))


def check_collatz_twenty_seven(ctx):
    """stdout should contain '27: 111' (27 takes 111 steps to reach 1)."""
    return bool(re.search(r"\b27:\s*111\b", ctx.stdout))


def check_collatz_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- log-level-stats checks ---

_APP_LOG = """\
[INFO] Server started on port 8080
[INFO] Connected to database
[DEBUG] Processing request from 192.168.1.1
[ERROR] Failed to read config file
[INFO] Request processed in 45ms
[WARN] Memory usage at 85%
[DEBUG] Cache miss for key user:123
[ERROR] Connection timeout to external API
[INFO] Session expired for user john
[DEBUG] Garbage collection completed in 12ms
"""


def check_log_stats_file(ctx):
    """log_stats.py should exist."""
    return "log_stats.py" in ctx.files


def check_log_stats_info(ctx):
    """stdout should contain 'INFO: 4'."""
    return bool(re.search(r"\bINFO:\s*4\b", ctx.stdout))


def check_log_stats_debug(ctx):
    """stdout should contain 'DEBUG: 3'."""
    return bool(re.search(r"\bDEBUG:\s*3\b", ctx.stdout))


def check_log_stats_error(ctx):
    """stdout should contain 'ERROR: 2'."""
    return bool(re.search(r"\bERROR:\s*2\b", ctx.stdout))


def check_log_stats_warn(ctx):
    """stdout should contain 'WARN: 1'."""
    return bool(re.search(r"\bWARN:\s*1\b", ctx.stdout))


def check_log_stats_order(ctx):
    """INFO should appear before DEBUG (sorted by count desc)."""
    idx_info = ctx.stdout.find("INFO:")
    idx_debug = ctx.stdout.find("DEBUG:")
    return idx_info < idx_debug if idx_info >= 0 and idx_debug >= 0 else False


def check_log_stats_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "frequent-words",
        "files": {"words.txt": _WORDS_TXT},
        "run": "python word_freq.py",
        "prompt": (
            "Write a Python script `word_freq.py` that reads `words.txt` and "
            "counts word frequencies. Words should be lowercased and stripped of "
            "punctuation. Output the top 5 words sorted by frequency descending, "
            "with ties broken alphabetically. Format: one word per line as "
            "'WORD: COUNT'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "word_freq.py exists": check_word_freq_file,
            "the: 6": check_word_freq_the,
            "cat: 2": check_word_freq_cat,
            "dog: 2": check_word_freq_dog,
            "mat: 2": check_word_freq_mat,
            "ran: 2": check_word_freq_ran,
            "the before cat (freq order)": check_word_freq_top5_order,
            "clean exit": check_word_freq_exit,
        },
    },
    {
        "name": "collatz-sequence",
        "files": {"numbers.txt": _NUMBERS_TXT},
        "run": "python collatz.py",
        "prompt": (
            "Write a Python script `collatz.py` that reads integers from "
            "`numbers.txt` (one per line) and prints the number of steps in the "
            "Collatz sequence for each. The Collatz sequence: if n is even divide "
            "by 2, if odd multiply by 3 and add 1, repeat until reaching 1. Count "
            "steps to reach 1 (not including the starting number). "
            "Output format: one line per input as 'N: STEPS'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "collatz.py exists": check_collatz_file,
            "6: 8 steps": check_collatz_six,
            "11: 14 steps": check_collatz_eleven,
            "16: 4 steps": check_collatz_sixteen,
            "27: 111 steps": check_collatz_twenty_seven,
            "clean exit": check_collatz_exit,
        },
    },
    {
        "name": "log-level-stats",
        "files": {"app.log": _APP_LOG},
        "run": "python log_stats.py",
        "prompt": (
            "Write a Python script `log_stats.py` that reads `app.log` and counts "
            "log entries by level. Each log line starts with '[LEVEL]' where LEVEL "
            "is DEBUG, INFO, WARN, or ERROR. Output each level and its count, "
            "sorted by count descending (ties broken alphabetically by level name). "
            "Format: one line per level as 'LEVEL: COUNT'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "log_stats.py exists": check_log_stats_file,
            "INFO: 4": check_log_stats_info,
            "DEBUG: 3": check_log_stats_debug,
            "ERROR: 2": check_log_stats_error,
            "WARN: 1": check_log_stats_warn,
            "INFO before DEBUG (freq order)": check_log_stats_order,
            "clean exit": check_log_stats_exit,
        },
    },
]
