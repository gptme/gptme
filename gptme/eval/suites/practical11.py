"""Practical eval tests (batch 11) — Roman numerals, run-length encoding, and anagram groups.

Tests capabilities not covered by earlier practical suites:
- Roman numeral encoding: convert integers to/from Roman notation (algorithm + lookup table)
- Run-length encoding: compact string representation via count+char pairs (string processing)
- Anagram grouping: group words by sorted-char signature (dict + sorting)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- roman-numerals checks ---


def check_roman_file(ctx):
    """roman.py should exist."""
    return "roman.py" in ctx.files


def check_roman_i(ctx):
    """stdout should contain I (for 1)."""
    return bool(any(line.strip() == "I" for line in ctx.stdout.splitlines()))


def check_roman_iv(ctx):
    """stdout should contain IV on its own line (for 4)."""
    return bool(any(line.strip() == "IV" for line in ctx.stdout.splitlines()))


def check_roman_ix(ctx):
    """stdout should contain IX on its own line (for 9)."""
    return bool(any(line.strip() == "IX" for line in ctx.stdout.splitlines()))


def check_roman_xlii(ctx):
    """stdout should contain XLII (for 42)."""
    return "XLII" in ctx.stdout


def check_roman_mcmxcix(ctx):
    """stdout should contain MCMXCIX (for 1999)."""
    return "MCMXCIX" in ctx.stdout


def check_roman_mmxxiv(ctx):
    """stdout should contain MMXXIV (for 2024)."""
    return "MMXXIV" in ctx.stdout


def check_roman_exit(ctx):
    return ctx.exit_code == 0


# --- run-length-encoding checks ---


def check_rle_file(ctx):
    """rle.py should exist."""
    return "rle.py" in ctx.files


def check_rle_encoded_aaabbbcccc(ctx):
    """stdout should contain 3A3B4C (encoded form of AAABBBCCCC)."""
    return "3A3B4C" in ctx.stdout


def check_rle_encoded_single_chars(ctx):
    """stdout should contain ABCD4E (singles uncompressed, run of 4E compressed)."""
    return "ABCD4E" in ctx.stdout


def check_rle_encoded_ten_w(ctx):
    """stdout should contain 10W (ten W's compressed)."""
    return "10W" in ctx.stdout


def check_rle_roundtrip(ctx):
    """AAABBBCCCC should appear in stdout (round-trip decode)."""
    return "AAABBBCCCC" in ctx.stdout


def check_rle_exit(ctx):
    return ctx.exit_code == 0


# --- anagram-groups checks ---


def check_anagram_file(ctx):
    """anagram_groups.py should exist."""
    return "anagram_groups.py" in ctx.files


def check_anagram_eat_group(ctx):
    """stdout should contain ate, eat, and tea on the same line (they are anagrams)."""
    for line in ctx.stdout.splitlines():
        words = set(line.lower().split())
        if {"ate", "eat", "tea"} <= words:
            return True
    return False


def check_anagram_tan_group(ctx):
    """stdout should contain nat and tan on the same line (they are anagrams)."""
    for line in ctx.stdout.splitlines():
        words = set(line.lower().split())
        if {"nat", "tan"} <= words:
            return True
    return False


def check_anagram_bat_alone(ctx):
    """stdout should contain a line with bat that does NOT also contain other words from the set."""
    for line in ctx.stdout.splitlines():
        words = set(line.lower().split())
        if "bat" in words and not (words & {"eat", "tea", "ate", "tan", "nat"}):
            return True
    return False


def check_anagram_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_NUMBERS_TXT = "1\n4\n9\n42\n1999\n2024\n"

_RLE_SAMPLES_TXT = "AAABBBCCCC\nABCDEEEE\nWWWWWWWWWW\n"

_WORDS_TXT = "eat tea tan ate nat bat\n"

tests: list["EvalSpec"] = [
    {
        "name": "roman-numerals",
        "files": {"numbers.txt": _NUMBERS_TXT},
        "run": "python roman.py",
        "prompt": (
            "Write a Python script `roman.py` that reads integers from `numbers.txt` "
            "(one per line) and prints the Roman numeral equivalent for each, one per "
            "line. Use standard Roman numeral rules (I=1, IV=4, V=5, IX=9, X=10, "
            "XL=40, L=50, XC=90, C=100, CD=400, D=500, CM=900, M=1000)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "roman.py exists": check_roman_file,
            "1 → I": check_roman_i,
            "4 → IV (subtractive)": check_roman_iv,
            "9 → IX (subtractive)": check_roman_ix,
            "42 → XLII": check_roman_xlii,
            "1999 → MCMXCIX": check_roman_mcmxcix,
            "2024 → MMXXIV": check_roman_mmxxiv,
            "clean exit": check_roman_exit,
        },
    },
    {
        "name": "run-length-encoding",
        "files": {"samples.txt": _RLE_SAMPLES_TXT},
        "run": "python rle.py",
        "prompt": (
            "Write a Python script `rle.py` that reads strings from `samples.txt` "
            "(one per line) and prints the run-length encoded form of each, then the "
            "decoded form to verify the round-trip. Encoding rules: consecutive "
            "identical characters are replaced by count+char (e.g. AAA→3A); single "
            "characters are NOT prefixed with 1 (e.g. B stays B, not 1B). "
            "Print encoded form first, then decoded form, for each input line."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "rle.py exists": check_rle_file,
            "AAABBBCCCC → 3A3B4C": check_rle_encoded_aaabbbcccc,
            "ABCDEEEE → ABCD4E": check_rle_encoded_single_chars,
            "WWWWWWWWWW → 10W": check_rle_encoded_ten_w,
            "round-trip: AAABBBCCCC restored": check_rle_roundtrip,
            "clean exit": check_rle_exit,
        },
    },
    {
        "name": "anagram-groups",
        "files": {"words.txt": _WORDS_TXT},
        "run": "python anagram_groups.py",
        "prompt": (
            "Write a Python script `anagram_groups.py` that reads space-separated "
            "words from `words.txt` and prints groups of anagrams, one group per line "
            "with words separated by spaces and sorted alphabetically within the group. "
            "Two words are anagrams if they contain the same letters (case-insensitive). "
            "Groups should be sorted alphabetically by their first word."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "anagram_groups.py exists": check_anagram_file,
            "ate/eat/tea grouped together": check_anagram_eat_group,
            "nat/tan grouped together": check_anagram_tan_group,
            "bat in its own group": check_anagram_bat_alone,
            "clean exit": check_anagram_exit,
        },
    },
]
