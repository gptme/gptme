"""Internal backstop: no new process-relative cwd use in session-sensitive core code.

A server hosts many conversations in one process, so a tool that resolves a path
with ``Path.cwd()`` / ``os.getcwd()`` (or mutates the process cwd with
``os.chdir``) silently targets the *host's* directory instead of the session's
workspace (see #4951, #5051). Session code should use
``gptme.util.workspace.current_workspace`` / ``resolve_in_workspace``.

This test statically scans ``gptme/{tools,hooks,context}`` for direct calls and
fails on any not in ``REVIEWED``. It is a maintainers' backstop for core code,
NOT a requirement or guarantee for third-party plugins: it cannot see
``open("relative")``, ``subprocess`` inheritance, or native extensions. The
dynamic coverage lives in ``test_session_workspace_hostile_cwd.py``.

Adding a new entry to ``REVIEWED`` means: you checked the call is a CLI-only
fallback, the workspace definition itself, or a deliberately guarded
process-cwd pin, and wrote down why.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "gptme"
SCANNED_DIRS = ("tools", "hooks", "context")
CWD_CALLS = {"cwd", "getcwd", "getcwdb", "chdir", "fchdir"}

# (file relative to gptme/, enclosing qualname) -> reason
REVIEWED: dict[tuple[str, str], str] = {
    (
        "tools/autocompact/resume.py",
        "_load_context_files",
    ): "Fallback only when the caller passes no workspace.",
    (
        "tools/autocompact/resume.py",
        "_is_config_secret",
    ): "Matches the credential writer's process-relative store when XDG_CONFIG_HOME is empty, not a session file.",
    (
        "tools/precommit.py",
        "get_precommit_runner",
    ): "Absolutizes a relative PATH entry; unrelated to session files.",
    (
        "tools/python.py",
        "_inprocess_session_cwd",
    ): "Pins the process cwd to the session workspace for one cell under a global lock, then restores the host cwd.",
    (
        "tools/python.py",
        "suspend_inprocess_session_cwd",
    ): "Releases/re-pins the same lock-guarded cwd around blocking subagent waits.",
    (
        "tools/python.py",
        "execute_python",
    ): "Runs inside _inprocess_session_cwd, i.e. the pinned session workspace.",
    (
        "tools/shell.py",
        "ShellSession.get_cwd",
    ): "Fallback when the shell has no tracked cwd yet (CLI).",
    (
        "tools/shell.py",
        "ShellSession._set_cwd",
    ): "Process-wide chdir only for CLI shells; skipped when a session workspace is set.",
    (
        "tools/shell.py",
        "ShellSession._restart_after_death",
    ): "Fallback when the dead shell had no tracked cwd (CLI).",
    (
        "tools/shell.py",
        "ShellSession._run_with_tty",
    ): "Fallback when the shell has no tracked cwd (CLI).",
    (
        "tools/subagent/execution.py",
        "_enter_subagent_cwd",
    ): "Records the parent's process cwd so thread-mode subagent chdir can be undone.",
    (
        "tools/subagent/execution.py",
        "_current_cwd",
    ): "Same bookkeeping for the subagent chdir restore.",
    (
        "tools/subagent/execution.py",
        "_exit_subagent_cwd",
    ): "Restores the parent's process cwd after the last thread-mode subagent.",
    (
        "context/adaptive_compressor.py",
        "AdaptiveCompressor.__init__",
    ): "Fallback when no workspace_root is given.",
    (
        "context/scout.py",
        "_make_turn_pre_hook._scout_hook",
    ): "Last-resort fallback after manager.workspace and the registered workspace.",
}


def _call_name(func: ast.expr) -> str | None:
    """Return the dotted name of a cwd-sensitive call, else None."""
    if isinstance(func, ast.Attribute) and func.attr in CWD_CALLS:
        base = ast.unparse(func.value)
        if func.attr == "cwd" and base.split(".")[-1] != "Path":
            return None  # e.g. some unrelated ``.cwd()`` method
        return f"{base}.{func.attr}"
    if isinstance(func, ast.Name) and func.id in CWD_CALLS - {"cwd"}:
        return func.id  # ``from os import getcwd``
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.hits: list[tuple[str, str, int]] = []

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        if (name := _call_name(node.func)) is not None:
            self.hits.append((".".join(self.stack) or "<module>", name, node.lineno))
        self.generic_visit(node)


def scan_source(source: str) -> list[tuple[str, str, int]]:
    scanner = _Scanner()
    scanner.visit(ast.parse(source))
    return scanner.hits


def _scan_package() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for sub in SCANNED_DIRS:
        for path in sorted((PKG / sub).rglob("*.py")):
            rel = path.relative_to(PKG)
            if "tests" in rel.parts or path.name.startswith("test_"):
                continue
            for qualname, call, line in scan_source(path.read_text()):
                found.setdefault((rel.as_posix(), qualname), []).append(
                    f"{call} (line {line})"
                )
    return found


def test_no_unreviewed_process_cwd_use():
    found = _scan_package()
    unreviewed = {k: v for k, v in found.items() if k not in REVIEWED}
    assert not unreviewed, (
        "Direct process-cwd use in session-sensitive core code. In a server this "
        "targets the host's cwd, not the session workspace. Use "
        "gptme.util.workspace.current_workspace()/resolve_in_workspace(), or, if the "
        "call is a reviewed CLI-only fallback, add it to REVIEWED with a reason:\n"
        + "\n".join(f"  {f}::{q}: {', '.join(c)}" for (f, q), c in unreviewed.items())
    )


def test_reviewed_entries_are_not_stale():
    found = _scan_package()
    stale = sorted(set(REVIEWED) - set(found))
    assert not stale, f"REVIEWED entries no longer match any call; remove: {stale}"


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef f(p):\n    return Path.cwd() / p\n",
        "import os\ndef f(p):\n    return os.path.join(os.getcwd(), p)\n",
        "import os\ndef f(d):\n    os.chdir(d)\n",
        "import pathlib\ndef f():\n    return pathlib.Path.cwd()\n",
        "from os import getcwd\ndef f():\n    return getcwd()\n",
    ],
)
def test_scanner_detects_process_relative_calls(source):
    assert scan_source(source), source


def test_scanner_ignores_unrelated_code():
    assert not scan_source("def f(self):\n    return self.workspace.resolve()\n")
    assert not scan_source("def f(x):\n    return x.cwd()\n")


def test_scanner_does_not_see_implicit_cwd_resolution():
    """Documented blind spot: ``Path(rel).resolve()`` uses the process cwd implicitly.

    The pre-#5051 hashline_edit bug was exactly this, so only the dynamic
    hostile-cwd test (``test_session_workspace_hostile_cwd.py``) can catch it.
    """
    implicit = "from pathlib import Path\ndef f(p):\n    return Path(p).expanduser().resolve()\n"
    assert scan_source(implicit) == []
