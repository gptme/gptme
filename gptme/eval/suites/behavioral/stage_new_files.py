"""Behavioral scenario: stage-new-files."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_stage_new_file_committed(ctx):
    """new_feature.py should appear in the HEAD commit's file list."""
    # stdout: <git log --oneline>\n__GPTME_SEP__\n<git show HEAD --name-only --format="">
    parts = ctx.stdout.split("__GPTME_SEP__")
    if len(parts) < 2:
        return False
    files_in_commit = parts[1]
    return "new_feature.py" in files_in_commit


def check_stage_two_commits(ctx):
    """There should be at least 2 commits (initial + new file)."""
    parts = ctx.stdout.split("__GPTME_SEP__")
    if not parts[0].strip():
        return False
    log_lines = [line for line in parts[0].strip().split("\n") if line.strip()]
    return len(log_lines) >= 2


def check_stage_file_has_double(ctx):
    """new_feature.py contains the double function as requested."""
    content = ctx.files.get("new_feature.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def double" in content


test: "EvalSpec" = {
    "name": "stage-new-files",
    "files": {
        "setup.sh": """\
#!/usr/bin/env bash
set -e
git init -q
git config user.email "test@example.com"
git config user.name "Test"
git config core.hooksPath /dev/null

cat > main.py << 'PYEOF'
def greet(name):
    return f"Hello, {name}!"
PYEOF

git add main.py
git commit -q -m "initial: add greet function"
""",
    },
    "run": "git log --oneline && echo __GPTME_SEP__ && git show HEAD --name-only --format=''",
    "prompt": (
        "Run `bash setup.sh` to initialise the git repository. "
        "Then create a new file `new_feature.py` with a simple Python function "
        "`def double(x): return x * 2`, and commit it with a conventional "
        "commit message (e.g. `feat: add double function`). "
        "The file does not exist yet — you need to create it and stage it "
        "before committing."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "new_feature.py in HEAD commit": check_stage_new_file_committed,
        "at least 2 commits": check_stage_two_commits,
        "new_feature.py contains double function": check_stage_file_has_double,
    },
}
