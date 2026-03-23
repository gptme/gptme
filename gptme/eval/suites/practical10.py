"""Practical eval tests (batch 10) — semantic version sorting, date histogram, and TSV-to-CSV.

Tests capabilities not covered by earlier practical suites:
- Semantic version parsing and sorting (SemVer spec: numeric comparison, pre-release ordering)
- Date frequency analysis by day of week (datetime + Counter)
- TSV-to-CSV conversion with proper field quoting (csv module usage)
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec

# --- Input data ---

_VERSIONS_TXT = """\
1.9.0
2.0.0-alpha.1
1.11.0
0.1.0
2.0.0
1.2.3-rc.2
1.2.3
1.2.3-beta.1
"""

_DATES_TXT = """\
2024-01-01
2024-01-02
2024-01-08
2024-01-09
2024-01-10
2024-01-15
2024-01-17
2024-01-22
2024-01-24
2024-01-29
2024-01-30
2024-02-01
"""
# Mon=5 (Jan 1,8,15,22,29), Tue=3 (Jan 2,9,30), Wed=3 (Jan 10,17,24), Thu=1 (Feb 1)

_DATA_TSV = """\
name\tage\tbio
Alice\t30\tSoftware engineer, loves Python
Bob\t25\tWrites "clean code" and drinks coffee
Carol\t35\tNormal person
Dave\t42\tSenior developer
"""
# Alice's bio has a comma → must be quoted in CSV
# Bob's bio has double quotes → must be escaped (doubled) in CSV


# --- semver-sort checks ---


def check_semver_file(ctx):
    """semver_sort.py should exist."""
    return "semver_sort.py" in ctx.files


def check_semver_first_line(ctx):
    """First output line should be 2.0.0 (highest release)."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    return bool(lines) and lines[0] == "2.0.0"


def check_semver_last_line(ctx):
    """Last output line should be 0.1.0 (lowest version)."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    return bool(lines) and lines[-1] == "0.1.0"


def check_semver_numeric_order(ctx):
    """1.11.0 should appear before 1.9.0 (numeric, not lexicographic comparison)."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    try:
        idx_11 = lines.index("1.11.0")
        idx_9 = lines.index("1.9.0")
        return idx_11 < idx_9
    except ValueError:
        return False


def check_semver_release_before_prerelease(ctx):
    """2.0.0 should appear before 2.0.0-alpha.1 (release > pre-release)."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    try:
        idx_release = lines.index("2.0.0")
        idx_pre = lines.index("2.0.0-alpha.1")
        return idx_release < idx_pre
    except ValueError:
        return False


def check_semver_prerelease_order(ctx):
    """1.2.3 should appear before 1.2.3-rc.2 which should appear before 1.2.3-beta.1."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    try:
        idx_release = lines.index("1.2.3")
        idx_rc = lines.index("1.2.3-rc.2")
        idx_beta = lines.index("1.2.3-beta.1")
        return idx_release < idx_rc < idx_beta
    except ValueError:
        return False


def check_semver_prerelease_before_lower_major(ctx):
    """2.0.0-alpha.1 should appear before all 1.x.x versions (major 2 > major 1)."""
    lines = [ln.strip() for ln in ctx.stdout.splitlines() if ln.strip()]
    one_x_versions = ["1.11.0", "1.9.0", "1.2.3", "1.2.3-rc.2", "1.2.3-beta.1"]
    try:
        idx_alpha = lines.index("2.0.0-alpha.1")
        first_1x = min(lines.index(v) for v in one_x_versions if v in lines)
        return idx_alpha < first_1x
    except ValueError:
        return False


def check_semver_exit(ctx):
    """Script should exit cleanly."""
    return ctx.exit_code == 0


# --- date-histogram checks ---


def check_date_hist_file(ctx):
    """date_histogram.py should exist."""
    return "date_histogram.py" in ctx.files


def check_date_hist_monday_count(ctx):
    """Monday should show 5 occurrences."""
    return any("Monday" in line and "5" in line for line in ctx.stdout.splitlines())


def check_date_hist_busiest(ctx):
    """Output should identify Monday as the busiest day."""
    return any(
        "monday" in line.lower() and "busiest" in line.lower()
        for line in ctx.stdout.splitlines()
    )


def check_date_hist_all_days(ctx):
    """All seven days of the week should be listed."""
    days = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    return all(day in ctx.stdout for day in days)


def check_date_hist_zero_days(ctx):
    """Days with zero occurrences should still appear (Friday, Saturday, Sunday = 0)."""
    # Friday, Saturday, Sunday have 0 occurrences — verify at least one is shown with count 0
    # Anchors "0" to the same line as the day name to avoid false positives from percentages
    zero_days = ["Friday", "Saturday", "Sunday"]
    lines = ctx.stdout.splitlines()
    return any(day in line and ": 0" in line for day in zero_days for line in lines)


def check_date_hist_exit(ctx):
    """Script should exit cleanly."""
    return ctx.exit_code == 0


# --- tsv-to-csv checks ---


def check_tsv_script_file(ctx):
    """tsv_to_csv.py should exist."""
    return "tsv_to_csv.py" in ctx.files


def check_tsv_output_file(ctx):
    """data.csv should be produced."""
    return "data.csv" in ctx.files


def check_tsv_alice_quoted(ctx):
    """Alice's bio (contains comma) should be quoted in CSV output."""
    csv_content = ctx.files.get("data.csv", "")
    # The field should be quoted: "Software engineer, loves Python"
    return '"Software engineer, loves Python"' in csv_content


def check_tsv_bob_escaped_quotes(ctx):
    """Bob's bio (contains double quotes) should have escaped quotes in CSV."""
    csv_content = ctx.files.get("data.csv", "")
    # Double quotes must be doubled: ""clean code""
    return '""clean code""' in csv_content


def check_tsv_header_preserved(ctx):
    """Header row (name,age,bio) should be first line."""
    csv_content = ctx.files.get("data.csv", "")
    lines = csv_content.splitlines()
    return lines[0] == "name,age,bio" if lines else False


def check_tsv_exit(ctx):
    """Script should exit cleanly."""
    return ctx.exit_code == 0


tests: list["EvalSpec"] = [
    {
        "name": "semver-sort",
        "files": {"versions.txt": _VERSIONS_TXT},
        "run": "python semver_sort.py versions.txt",
        "prompt": (
            "Write a Python script `semver_sort.py` that reads a file of semantic "
            "version strings (one per line, passed as CLI argument) and prints them "
            "sorted in descending order (newest first).\n\n"
            "Follow SemVer rules: compare major, minor, patch numerically (not "
            "lexicographically). A pre-release version (e.g. '1.0.0-alpha') has "
            "lower precedence than the associated normal version (e.g. '1.0.0'). "
            "When both pre-release identifiers exist, compare them alphabetically "
            "(e.g. 'beta' < 'rc').\n\n"
            "Use only the Python standard library. Print one version per line."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "semver_sort.py exists": check_semver_file,
            "2.0.0 is first (highest)": check_semver_first_line,
            "0.1.0 is last (lowest)": check_semver_last_line,
            "1.11.0 before 1.9.0 (numeric)": check_semver_numeric_order,
            "2.0.0 before 2.0.0-alpha.1 (release > pre-release)": check_semver_release_before_prerelease,
            "1.2.3 > 1.2.3-rc.2 > 1.2.3-beta.1 (pre-release order)": check_semver_prerelease_order,
            "2.0.0-alpha.1 before all 1.x.x (cross-major pre-release)": check_semver_prerelease_before_lower_major,
            "clean exit": check_semver_exit,
        },
    },
    {
        "name": "date-histogram",
        "files": {"dates.txt": _DATES_TXT},
        "run": "python date_histogram.py dates.txt",
        "prompt": (
            "Write a Python script `date_histogram.py` that reads a file of dates "
            "(one ISO 8601 date YYYY-MM-DD per line, passed as CLI argument) and "
            "prints a day-of-week frequency report.\n\n"
            "Output requirements:\n"
            "1. Print all seven days (Monday through Sunday) with their occurrence "
            "count and percentage, even if count is 0. Format each line as: "
            "'<Day>: <count> (<pct>%)' where pct is one decimal place.\n"
            "2. After the histogram, print a 'Busiest: <Day> (<count> occurrences)' "
            "line identifying the most frequent day.\n\n"
            "Use only the Python standard library."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "date_histogram.py exists": check_date_hist_file,
            "Monday count is 5": check_date_hist_monday_count,
            "identifies Monday as busiest": check_date_hist_busiest,
            "all seven days listed": check_date_hist_all_days,
            "zero-count days shown": check_date_hist_zero_days,
            "clean exit": check_date_hist_exit,
        },
    },
    {
        "name": "tsv-to-csv",
        "files": {"data.tsv": _DATA_TSV},
        "run": "python tsv_to_csv.py data.tsv data.csv",
        "prompt": (
            "Write a Python script `tsv_to_csv.py` that converts a TSV (tab-separated "
            "values) file to a CSV (comma-separated values) file.\n\n"
            "Arguments: input TSV filename and output CSV filename.\n\n"
            "Requirements: fields containing commas must be wrapped in double quotes; "
            "fields containing double quotes must have those quotes escaped by doubling "
            'them (e.g. \'He said "hi"\' becomes \'"He said ""hi"""\'). Use '
            "Python's built-in `csv` module which handles quoting automatically.\n\n"
            "The output file should use Unix line endings (newline only)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "tsv_to_csv.py exists": check_tsv_script_file,
            "data.csv produced": check_tsv_output_file,
            "header row correct": check_tsv_header_preserved,
            "comma-containing field quoted": check_tsv_alice_quoted,
            "double-quotes escaped": check_tsv_bob_escaped_quotes,
            "clean exit": check_tsv_exit,
        },
    },
]
