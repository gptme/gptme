"""Practical eval tests (batch 9) — .env parsing, YAML deep merge, and git log statistics.

Tests capabilities not covered by earlier practical suites:
- .env file parsing with quote stripping and inline comment handling (string manipulation)
- Deep YAML merge with recursive dict merge and list replacement (PyYAML + recursion)
- Git log statistics with author aggregation and date range extraction (parsing + sorting)
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- env-parser checks ---


def check_env_parser_file(ctx):
    """env_parser.py should exist."""
    return "env_parser.py" in ctx.files


def check_env_parser_db_host(ctx):
    """stdout should contain DB_HOST=localhost."""
    return "DB_HOST=localhost" in ctx.stdout


def check_env_parser_db_name(ctx):
    """stdout should contain DB_NAME=myapp_db (quotes stripped)."""
    return "DB_NAME=myapp_db" in ctx.stdout


def check_env_parser_app_secret(ctx):
    """stdout should contain APP_SECRET=super-secret-key (quotes stripped)."""
    return "APP_SECRET=super-secret-key" in ctx.stdout


def check_env_parser_inline_comment(ctx):
    """stdout should contain DEBUG=true (inline comment stripped)."""
    # Must contain DEBUG=true but NOT the inline comment text
    return bool(re.search(r"DEBUG=true\s*$", ctx.stdout, re.MULTILINE))


def check_env_parser_no_disabled(ctx):
    """stdout should NOT contain DISABLED_FEATURE (commented out line)."""
    return "DISABLED_FEATURE" not in ctx.stdout


def check_env_parser_line_count(ctx):
    """Exactly 6 KEY=VALUE lines in output."""
    lines = [
        line.strip()
        for line in ctx.stdout.strip().split("\n")
        if line.strip() and "=" in line
    ]
    return len(lines) == 6


def check_env_parser_exit(ctx):
    return ctx.exit_code == 0


# --- yaml-merge checks ---


def check_yaml_merge_file(ctx):
    """yaml_merge.py should exist."""
    return "yaml_merge.py" in ctx.files


def check_yaml_merge_output_exists(ctx):
    """merged.yaml should exist."""
    return "merged.yaml" in ctx.files


def check_yaml_merge_base_host(ctx):
    """merged.yaml should contain host: 0.0.0.0 (base server.host preserved)."""
    content = ctx.files.get("merged.yaml", "")
    return "0.0.0.0" in content


def check_yaml_merge_override_port(ctx):
    """merged.yaml should contain port: 9090 (overridden)."""
    content = ctx.files.get("merged.yaml", "")
    return bool(re.search(r"port:\s*9090", content))


def check_yaml_merge_override_debug(ctx):
    """merged.yaml should contain debug: true (overridden)."""
    content = ctx.files.get("merged.yaml", "")
    return bool(re.search(r"debug:\s*true", content))


def check_yaml_merge_override_db_host(ctx):
    """merged.yaml should contain db.prod.example.com (overridden)."""
    content = ctx.files.get("merged.yaml", "")
    return "db.prod.example.com" in content


def check_yaml_merge_base_db_name(ctx):
    """merged.yaml should contain name: myapp (base preserved)."""
    content = ctx.files.get("merged.yaml", "")
    return bool(re.search(r"name:\s*myapp\b", content))


def check_yaml_merge_override_level(ctx):
    """merged.yaml should contain level: info (overridden)."""
    content = ctx.files.get("merged.yaml", "")
    return bool(re.search(r"level:\s*info", content))


def check_yaml_merge_list_replaced(ctx):
    """merged.yaml should NOT contain '- file' list item (list fully replaced by override)."""
    content = ctx.files.get("merged.yaml", "")
    return not re.search(r"^\s*-\s+file\b", content, re.MULTILINE)


def check_yaml_merge_exit(ctx):
    return ctx.exit_code == 0


# --- git-log-stats checks ---


def check_git_stats_file(ctx):
    """git_stats.py should exist."""
    return "git_stats.py" in ctx.files


def check_git_stats_total(ctx):
    """stdout should contain 'Total: 7 commits'."""
    return "Total: 7 commits" in ctx.stdout


def check_git_stats_alice(ctx):
    """stdout should contain 'Alice: 4 commits'."""
    return "Alice: 4 commits" in ctx.stdout


def check_git_stats_bob(ctx):
    """stdout should contain 'Bob: 2 commits'."""
    return "Bob: 2 commits" in ctx.stdout


def check_git_stats_charlie(ctx):
    """stdout should contain 'Charlie: 1 commit(s)' (accept both singular and plural)."""
    return bool(re.search(r"Charlie: 1 commits?\b", ctx.stdout))


def check_git_stats_author_order(ctx):
    """Alice should appear before Bob in output (sorted by count descending)."""
    alice_pos = ctx.stdout.find("Alice")
    bob_pos = ctx.stdout.find("Bob")
    if alice_pos == -1 or bob_pos == -1:
        return False
    return alice_pos < bob_pos


def check_git_stats_date_range(ctx):
    """stdout should contain both 2024-01-15 and 2024-01-21 (date range)."""
    return "2024-01-15" in ctx.stdout and "2024-01-21" in ctx.stdout


def check_git_stats_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_SAMPLE_ENV = """\
# Database config
DB_HOST=localhost
DB_PORT=5432
DB_NAME="myapp_db"

# App settings
APP_SECRET='super-secret-key'
DEBUG=true  # development only
LOG_LEVEL=info
# DISABLED_FEATURE=yes
"""

_BASE_YAML = """\
server:
  host: 0.0.0.0
  port: 8080
  debug: false
database:
  host: localhost
  port: 5432
  name: myapp
logging:
  level: warning
  handlers:
    - console
    - file
"""

_OVERRIDE_YAML = """\
server:
  port: 9090
  debug: true
database:
  host: db.prod.example.com
logging:
  level: info
  handlers:
    - console
"""

_GIT_LOG_TXT = """\
a1b2c3d|Alice|2024-01-15|feat: add user auth
d4e5f6g|Bob|2024-01-16|fix: login redirect
h7i8j9k|Alice|2024-01-17|docs: update README
l0m1n2o|Charlie|2024-01-18|feat: add dashboard
p3q4r5s|Alice|2024-01-19|fix: auth token expiry
t6u7v8w|Bob|2024-01-20|refactor: extract middleware
x9y0z1a|Alice|2024-01-21|test: add auth tests
"""

tests: list["EvalSpec"] = [
    {
        "name": "env-parser",
        "files": {"sample.env": _SAMPLE_ENV},
        "run": "python env_parser.py",
        "prompt": (
            "Write a Python script `env_parser.py` that reads a `.env` file and "
            "prints each variable as `KEY=VALUE` with the following rules: skip "
            "blank lines and comments (lines starting with #), strip surrounding "
            "quotes from values, handle inline comments (strip everything after "
            "` #` that's not inside quotes). Read from `sample.env`."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "env_parser.py exists": check_env_parser_file,
            "DB_HOST=localhost present": check_env_parser_db_host,
            "DB_NAME quotes stripped": check_env_parser_db_name,
            "APP_SECRET quotes stripped": check_env_parser_app_secret,
            "inline comment stripped": check_env_parser_inline_comment,
            "commented line excluded": check_env_parser_no_disabled,
            "exactly 6 output lines": check_env_parser_line_count,
            "clean exit": check_env_parser_exit,
        },
    },
    {
        "name": "yaml-merge",
        "files": {"base.yaml": _BASE_YAML, "override.yaml": _OVERRIDE_YAML},
        "run": "python yaml_merge.py",
        "prompt": (
            "Write a Python script `yaml_merge.py` that deeply merges two YAML "
            "files. The second file's values override the first. For nested dicts, "
            "merge recursively. For lists, the second file's list replaces the "
            "first entirely. Read `base.yaml` and `override.yaml`, write the "
            "merged result to `merged.yaml`. Use PyYAML."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "yaml_merge.py exists": check_yaml_merge_file,
            "merged.yaml exists": check_yaml_merge_output_exists,
            "base server.host preserved": check_yaml_merge_base_host,
            "port overridden to 9090": check_yaml_merge_override_port,
            "debug overridden to true": check_yaml_merge_override_debug,
            "db host overridden": check_yaml_merge_override_db_host,
            "base db name preserved": check_yaml_merge_base_db_name,
            "log level overridden": check_yaml_merge_override_level,
            "list fully replaced": check_yaml_merge_list_replaced,
            "clean exit": check_yaml_merge_exit,
        },
    },
    {
        "name": "git-log-stats",
        "files": {"git_log.txt": _GIT_LOG_TXT},
        "run": "python git_stats.py",
        "prompt": (
            "Write a Python script `git_stats.py` that reads a git log from "
            "`git_log.txt` (one commit per line in format "
            "`HASH|AUTHOR|DATE|MESSAGE`) and prints statistics: total commits, "
            "unique authors sorted by commit count descending, and the date range "
            "(earliest to latest). Output format: 'Total: N commits', then "
            "'Authors:', then each author as '  AUTHOR: N commits', then "
            "'Date range: EARLIEST to LATEST'."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "git_stats.py exists": check_git_stats_file,
            "total 7 commits": check_git_stats_total,
            "Alice 4 commits": check_git_stats_alice,
            "Bob 2 commits": check_git_stats_bob,
            "Charlie 1 commit (singular)": check_git_stats_charlie,
            "Alice before Bob (sorted)": check_git_stats_author_order,
            "date range present": check_git_stats_date_range,
            "clean exit": check_git_stats_exit,
        },
    },
]
