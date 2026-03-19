"""Practical eval tests (batch 7) — config parsing, data comparison, changelog.

Tests capabilities not covered by earlier practical suites:
- INI config file parsing and JSON conversion (configparser + json)
- Recursive JSON comparison with path-based diff reporting
- Conventional commit changelog generation (text parsing + categorization)
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.main import EvalSpec


# --- ini-to-json checks ---


def check_ini_file(ctx):
    """convert.py should exist."""
    return "convert.py" in ctx.files


def check_ini_sections(ctx):
    """Output should contain all three sections as top-level keys."""
    try:
        data = json.loads(ctx.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    return all(s in data for s in ["database", "server", "logging"])


def check_ini_values(ctx):
    """Parsed values should match the INI content."""
    try:
        data = json.loads(ctx.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    db = data.get("database", {})
    return db.get("host") == "localhost" and db.get("port") == "5432"


def check_ini_nested(ctx):
    """Server section should have correct values."""
    try:
        data = json.loads(ctx.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    srv = data.get("server", {})
    return srv.get("workers") == "4" and srv.get("debug") == "false"


def check_ini_logging_format(ctx):
    """Logging format string with % placeholders should be preserved as-is (RawConfigParser key test)."""
    try:
        data = json.loads(ctx.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    log = data.get("logging", {})
    fmt = log.get("format", "")
    return "%(asctime)s" in fmt and "%(levelname)s" in fmt


def check_ini_exit(ctx):
    return ctx.exit_code == 0


# --- json-diff checks ---


def check_diff_file(ctx):
    """diff_json.py should exist."""
    return "diff_json.py" in ctx.files


def check_diff_added(ctx):
    """Should detect the added key 'address.zip' on the same output line."""
    lines = ctx.stdout.strip().split("\n")
    return any("address.zip" in line and "added" in line.lower() for line in lines)


def check_diff_removed(ctx):
    """Should detect the removed key 'phone' on the same output line."""
    lines = ctx.stdout.strip().split("\n")
    return any("phone" in line and "removed" in line.lower() for line in lines)


def check_diff_changed(ctx):
    """Should detect changed values for 'age' and 'address.city' on lines that mention 'changed'."""
    lines = ctx.stdout.strip().split("\n")
    has_age_changed = any("age" in line and "changed" in line.lower() for line in lines)
    has_city_changed = any(
        "address.city" in line and "changed" in line.lower() for line in lines
    )
    return has_age_changed and has_city_changed


def check_diff_unchanged(ctx):
    """Should NOT report unchanged fields (name, email, address.street, tags) as changed."""
    lines = ctx.stdout.strip().split("\n")
    unchanged_fields = ("name", "email", "address.street", "tags")
    for line in lines:
        line_lower = line.lower().strip()
        for field in unchanged_fields:
            if re.match(
                rf"^(added|removed|changed):?\s+{re.escape(field)}\b", line_lower
            ):
                return False
    return True


def check_diff_exit(ctx):
    # Accept both 0 (script convention) and 1 (Unix diff convention when files differ)
    return ctx.exit_code in (0, 1)


# --- changelog checks ---


def check_changelog_file(ctx):
    """changelog.py should exist."""
    return "changelog.py" in ctx.files


def _extract_section(output: str, heading_pattern: str) -> str | None:
    """Extract section body from heading match to next heading (or EOF)."""
    m = re.search(heading_pattern, output, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    section_start = m.end()
    next_heading = re.search(r"^#+\s+\S", output[section_start:], re.MULTILINE)
    return (
        output[section_start : section_start + next_heading.start()]
        if next_heading
        else output[section_start:]
    )


def check_changelog_features(ctx):
    """Should have a Features section with auth and search entries in that section body."""
    body = _extract_section(ctx.stdout, r"^#+\s*feat(ure)?s?.*$")
    if body is None:
        return False
    has_auth = "authentication" in body.lower() or "auth" in body.lower()
    has_search = "search" in body.lower()
    return has_auth and has_search


def check_changelog_fixes(ctx):
    """Should have a Fixes section with login and memory entries in that section body."""
    body = _extract_section(ctx.stdout, r"^#+\s*fix(es)?.*$")
    if body is None:
        return False
    has_login = "login" in body.lower()
    has_memory = "memory" in body.lower()
    return has_login and has_memory


def check_changelog_docs(ctx):
    """Should have a Docs section heading with API entry in that section body."""
    body = _extract_section(ctx.stdout, r"^#+\s*doc(s|umentation)?.*$")
    if body is None:
        return False
    return "api" in body.lower()


def check_changelog_scopes(ctx):
    """Should include explicit scope notation as bullet-point prefixes (e.g. '- (auth)').

    Anchors to bullet-point lines to prevent raw commit input like 'feat(auth):'
    from satisfying this check without proper reformatting.
    """
    lines = ctx.stdout.strip().split("\n")
    has_auth_scope = any(
        re.match(r"^\s*[-*]\s+\(auth\)", line, re.IGNORECASE) for line in lines
    )
    has_api_scope = any(
        re.match(r"^\s*[-*]\s+\(api\)", line, re.IGNORECASE) for line in lines
    )
    return has_auth_scope and has_api_scope


def check_changelog_exit(ctx):
    return ctx.exit_code == 0


# --- test data ---

_CONFIG_INI = """\
[database]
host = localhost
port = 5432
name = myapp
user = admin
password = secret123

[server]
bind = 0.0.0.0
port = 8080
workers = 4
debug = false

[logging]
level = INFO
file = /var/log/myapp.log
format = %(asctime)s - %(levelname)s - %(message)s
"""

_OLD_JSON = """\
{
  "name": "Alice",
  "age": 30,
  "email": "alice@example.com",
  "phone": "555-1234",
  "address": {
    "street": "123 Main St",
    "city": "Springfield"
  },
  "tags": ["admin", "user"]
}
"""

_NEW_JSON = """\
{
  "name": "Alice",
  "age": 31,
  "email": "alice@example.com",
  "address": {
    "street": "123 Main St",
    "city": "Shelbyville",
    "zip": "62704"
  },
  "tags": ["admin", "user"]
}
"""

_COMMITS_TXT = """\
feat(auth): add JWT-based authentication
fix(api): resolve login timeout on slow connections
docs(api): update REST API reference with new endpoints
feat(search): implement full-text search for articles
fix: correct memory leak in background worker
chore: update dependencies to latest versions
feat(auth): add OAuth2 provider support
fix(ui): fix button alignment on mobile devices
docs: add contributing guidelines
refactor(db): optimize query performance for user lookups
"""

tests: list["EvalSpec"] = [
    {
        "name": "ini-to-json",
        "files": {"config.ini": _CONFIG_INI},
        "run": "python convert.py config.ini",
        "prompt": (
            "Write convert.py that reads an INI config file and converts it to JSON, "
            "printing the result to stdout. The script should accept the filename as "
            "a command-line argument.\n\n"
            "Each INI section becomes a top-level JSON key, and each key-value pair "
            "within a section becomes a nested key-value pair. All values should be "
            "kept as strings (do not attempt type conversion).\n\n"
            "Important: use configparser.RawConfigParser (or ConfigParser(interpolation=None)) "
            "so that values containing percent signs (e.g. logging format strings) are "
            "preserved as-is without triggering interpolation errors.\n\n"
            "Output should be pretty-printed JSON with 2-space indentation."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "convert.py exists": check_ini_file,
            "all sections present": check_ini_sections,
            "database values correct": check_ini_values,
            "server values correct": check_ini_nested,
            "logging format preserved": check_ini_logging_format,
            "clean exit": check_ini_exit,
        },
    },
    {
        "name": "json-diff",
        "files": {"old.json": _OLD_JSON, "new.json": _NEW_JSON},
        "run": "python diff_json.py old.json new.json",
        "prompt": (
            "Write diff_json.py that compares two JSON files and reports the "
            "differences. The script should accept two filenames as command-line "
            "arguments (old file and new file).\n\n"
            "For each difference, print one line in this format:\n"
            "- Added keys: 'added: <dotted.path> = <value>'\n"
            "- Removed keys: 'removed: <dotted.path> (was <value>)'\n"
            "- Changed values: 'changed: <dotted.path>: <old_value> -> <new_value>'\n\n"
            "Use dot notation for nested paths (e.g., 'address.city'). "
            "Compare recursively into nested objects. For arrays/lists, compare "
            "them as whole values (not element-by-element). Sort output lines "
            "alphabetically by path."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "diff_json.py exists": check_diff_file,
            "detects added key": check_diff_added,
            "detects removed key": check_diff_removed,
            "detects changed values": check_diff_changed,
            "ignores unchanged": check_diff_unchanged,
            "clean exit": check_diff_exit,
        },
    },
    {
        "name": "changelog-gen",
        "files": {"commits.txt": _COMMITS_TXT},
        "run": "python changelog.py commits.txt",
        "prompt": (
            "Write changelog.py that reads a file of conventional commit messages "
            "(one per line) and generates a categorized changelog. The script should "
            "accept the filename as a command-line argument.\n\n"
            "Each commit line follows the format: 'type(scope): description' or "
            "'type: description' (scope is optional).\n\n"
            "Group commits by type into these sections (in this order):\n"
            "- Features (from 'feat' commits)\n"
            "- Fixes (from 'fix' commits)\n"
            "- Documentation (from 'docs' commits)\n"
            "- Other (everything else: chore, refactor, style, test, etc.)\n\n"
            "Within each section, list commits as bullet points. Include the scope "
            "in parentheses if present. Format:\n"
            "### Features\n"
            "- (auth) add JWT-based authentication\n"
            "- (search) implement full-text search for articles\n\n"
            "Skip empty sections (don't print a heading if there are no commits "
            "of that type)."
        ),
        "tools": ["read", "save", "shell"],
        "expect": {
            "changelog.py exists": check_changelog_file,
            "features section correct": check_changelog_features,
            "fixes section correct": check_changelog_fixes,
            "docs section correct": check_changelog_docs,
            "scopes included": check_changelog_scopes,
            "clean exit": check_changelog_exit,
        },
    },
]
