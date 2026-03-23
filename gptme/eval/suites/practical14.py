"""Practical eval tests (batch 14) — matrix transpose, IPv4 classification, bracket balance.

Tests capabilities not covered by earlier practical suites:
- Matrix transposition from space-separated input (2D data manipulation)
- IPv4 address classification by RFC 1918 private ranges and address class (networking)
- Bracket/parenthesis balance checking with error position reporting (stack algorithm)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- matrix-transpose checks ---

_MATRIX_TXT = """\
1 2 3 4
5 6 7 8
9 10 11 12
"""


def check_transpose_file(ctx):
    """transpose.py should exist."""
    return "transpose.py" in ctx.files


def check_transpose_row1(ctx):
    """First row of transpose should be '1 5 9'."""
    return bool(re.search(r"\b1\s+5\s+9\b", ctx.stdout))


def check_transpose_row2(ctx):
    """Second row of transpose should be '2 6 10'."""
    return bool(re.search(r"\b2\s+6\s+10\b", ctx.stdout))


def check_transpose_row3(ctx):
    """Third row of transpose should be '3 7 11'."""
    return bool(re.search(r"\b3\s+7\s+11\b", ctx.stdout))


def check_transpose_row4(ctx):
    """Fourth row of transpose should be '4 8 12'."""
    return bool(re.search(r"\b4\s+8\s+12\b", ctx.stdout))


def check_transpose_dimensions(ctx):
    """Output should have exactly 4 non-empty lines (4 rows after transpose)."""
    lines = [line for line in ctx.stdout.strip().splitlines() if line.strip()]
    return len(lines) == 4


def check_transpose_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- ipv4-classify checks ---

_IPS_TXT = """\
192.168.1.1
10.0.0.5
172.16.254.3
8.8.8.8
127.0.0.1
224.0.0.1
"""


def check_ipv4_file(ctx):
    """ipv4_classify.py should exist."""
    return "ipv4_classify.py" in ctx.files


def check_ipv4_192_private(ctx):
    """192.168.1.1 should be classified as private."""
    # Find the line containing 192.168.1.1 and check it says private
    for line in ctx.stdout.splitlines():
        if "192.168.1.1" in line and re.search(r"\bprivate\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_10_private(ctx):
    """10.0.0.5 should be classified as private."""
    for line in ctx.stdout.splitlines():
        if "10.0.0.5" in line and re.search(r"\bprivate\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_172_private(ctx):
    """172.16.254.3 should be classified as private."""
    for line in ctx.stdout.splitlines():
        if "172.16.254.3" in line and re.search(r"\bprivate\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_8_public(ctx):
    """8.8.8.8 should be classified as public."""
    for line in ctx.stdout.splitlines():
        if "8.8.8.8" in line and re.search(r"\bpublic\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_127_loopback(ctx):
    """127.0.0.1 should be classified as loopback."""
    for line in ctx.stdout.splitlines():
        if "127.0.0.1" in line and re.search(r"\bloopback\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_224_multicast(ctx):
    """224.0.0.1 should be classified as multicast."""
    for line in ctx.stdout.splitlines():
        if "224.0.0.1" in line and re.search(r"\bmulticast\b", line, re.IGNORECASE):
            return True
    return False


def check_ipv4_class_a(ctx):
    """10.0.0.5 should be class A (first octet 1-126)."""
    for line in ctx.stdout.splitlines():
        if re.search(r"10\.0\.0\.5\s+[Aa]\b", line):
            return True
    return False


def check_ipv4_class_c(ctx):
    """192.168.1.1 should be class C (first octet 192-223)."""
    for line in ctx.stdout.splitlines():
        if re.search(r"192\.168\.1\.1\s+[Cc]\b", line):
            return True
    return False


def check_ipv4_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


# --- bracket-balance checks ---

_BRACKETS_TXT = """\
(())(())
([{}])
((]
{[}]
(((
"""


def check_brackets_file(ctx):
    """brackets.py should exist."""
    return "brackets.py" in ctx.files


def check_brackets_valid1(ctx):
    """'(())(())' should be reported as valid/balanced."""
    for line in ctx.stdout.splitlines():
        if "(())(())" in line and re.search(
            r"\b(valid|balanced|ok|yes)\b", line, re.IGNORECASE
        ):
            return True
    return False


def check_brackets_valid2(ctx):
    """'([{}])' should be reported as valid/balanced."""
    for line in ctx.stdout.splitlines():
        if "([{}])" in line and re.search(
            r"\b(valid|balanced|ok|yes)\b", line, re.IGNORECASE
        ):
            return True
    return False


def check_brackets_invalid1(ctx):
    """'((]' should be reported as invalid/unbalanced."""
    for line in ctx.stdout.splitlines():
        if "((]" in line and re.search(
            r"\b(invalid|unbalanced|error|no|mismatch)\b", line, re.IGNORECASE
        ):
            return True
    return False


def check_brackets_invalid2(ctx):
    """'{[}]' should be reported as invalid/unbalanced."""
    for line in ctx.stdout.splitlines():
        if "{[}]" in line and re.search(
            r"\b(invalid|unbalanced|error|no|mismatch)\b", line, re.IGNORECASE
        ):
            return True
    return False


def check_brackets_unclosed(ctx):
    """'(((' should be reported as invalid/unbalanced."""
    for line in ctx.stdout.splitlines():
        if "(((" in line and re.search(
            r"\b(invalid|unbalanced|error|no|unclosed)\b", line, re.IGNORECASE
        ):
            return True
    return False


def check_brackets_error_pos(ctx):
    """'((]' error should reference position 3 (1-indexed, per prompt spec)."""
    for line in ctx.stdout.splitlines():
        if "((]" in line and re.search(r"\b3\b", line):
            return True
    return False


def check_brackets_exit(ctx):
    """Script should exit with code 0."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "matrix-transpose",
        "files": {"matrix.txt": _MATRIX_TXT},
        "run": "python transpose.py",
        "prompt": (
            "Write a Python script `transpose.py` that reads a space-separated "
            "matrix from `matrix.txt` (one row per line) and prints its transpose. "
            "Each row of the transposed matrix should be printed on its own line "
            "with values separated by spaces."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "transpose.py exists": check_transpose_file,
            "row 1: 1 5 9": check_transpose_row1,
            "row 2: 2 6 10": check_transpose_row2,
            "row 3: 3 7 11": check_transpose_row3,
            "row 4: 4 8 12": check_transpose_row4,
            "4 output rows": check_transpose_dimensions,
            "clean exit": check_transpose_exit,
        },
    },
    {
        "name": "ipv4-classify",
        "files": {"ips.txt": _IPS_TXT},
        "run": "python ipv4_classify.py",
        "prompt": (
            "Write a Python script `ipv4_classify.py` that reads IPv4 addresses "
            "from `ips.txt` (one per line) and classifies each. For each address, "
            "print one line with: the IP address, its class (A/B/C/D/E based on "
            "first octet: 1-126=A, 128-191=B, 192-223=C, 224-239=D, 240-255=E; "
            "use '-' for 127.x.x.x loopback which has no standard class), "
            "and its type (private if in 10.0.0.0/8, 172.16.0.0/12, or "
            "192.168.0.0/16; loopback if 127.x.x.x; multicast if class D; "
            "otherwise public). Format: 'IP CLASS TYPE' (e.g. '10.0.0.5 A private')."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "ipv4_classify.py exists": check_ipv4_file,
            "192.168.1.1 private": check_ipv4_192_private,
            "10.0.0.5 private": check_ipv4_10_private,
            "172.16.254.3 private": check_ipv4_172_private,
            "8.8.8.8 public": check_ipv4_8_public,
            "127.0.0.1 loopback": check_ipv4_127_loopback,
            "224.0.0.1 multicast": check_ipv4_224_multicast,
            "10.x is class A": check_ipv4_class_a,
            "192.x is class C": check_ipv4_class_c,
            "clean exit": check_ipv4_exit,
        },
    },
    {
        "name": "bracket-balance",
        "files": {"brackets.txt": _BRACKETS_TXT},
        "run": "python brackets.py",
        "prompt": (
            "Write a Python script `brackets.py` that reads strings from "
            "`brackets.txt` (one per line, skip empty lines) and checks if each "
            "string's brackets are balanced. Supported brackets: (), [], {}. "
            "For each string, print the string followed by 'valid' if balanced, "
            "or 'invalid' with the 1-based position of the first error. "
            "Format: 'STRING valid' or 'STRING invalid at position N'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "brackets.py exists": check_brackets_file,
            "(())(()) valid": check_brackets_valid1,
            "([{}]) valid": check_brackets_valid2,
            "((] invalid": check_brackets_invalid1,
            "{[}] invalid": check_brackets_invalid2,
            "((( unclosed": check_brackets_unclosed,
            "((] error at pos 3": check_brackets_error_pos,
            "clean exit": check_brackets_exit,
        },
    },
]
