"""Internal cwd backstop; third-party plugins need not adopt this AST check."""

import ast
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reviewed process-cwd operations. Counts prevent adding calls inside an exception.
# Shell fallbacks and Python/subagent pinning intentionally touch real OS state.
ALLOWED = Counter(
    {
        ("tools/shell.py", "get_cwd", "os.getcwd"): 1,
        ("tools/shell.py", "_set_cwd", "os.chdir"): 1,
        ("tools/shell.py", "_restart_after_death", "os.getcwd"): 1,
        ("tools/shell.py", "_run_with_tty", "os.getcwd"): 1,
        ("tools/python.py", "_inprocess_session_cwd", "Path.cwd"): 1,
        ("tools/python.py", "_inprocess_session_cwd", "os.chdir"): 2,
        ("tools/python.py", "suspend_inprocess_session_cwd", "Path.cwd"): 2,
        ("tools/python.py", "suspend_inprocess_session_cwd", "os.chdir"): 3,
        ("tools/python.py", "execute_python", "Path.cwd"): 1,
        # which() resolves relative PATH entries against the host cwd too.
        ("tools/precommit.py", "get_precommit_runner", "os.getcwd"): 1,
        # Explicit workspace parameter, process cwd only when absent.
        ("tools/autocompact/resume.py", "_load_context_files", "Path.cwd"): 1,
        # Match the credential writer's cwd-relative store for empty XDG_CONFIG_HOME.
        ("tools/autocompact/resume.py", "_is_config_secret", "Path.cwd"): 1,
        ("tools/subagent/execution.py", "_enter_subagent_cwd", "os.getcwd"): 1,
        ("tools/subagent/execution.py", "_current_cwd", "Path.cwd"): 1,
        ("tools/subagent/execution.py", "_exit_subagent_cwd", "os.chdir"): 1,
    }
)


def _cwd_calls(source: str) -> Counter[tuple[str, str]]:
    """Detect direct calls including ordinary import aliases, not arbitrary I/O."""
    tree = ast.parse(source)
    aliases = {"os": "os", "Path": "pathlib.Path", "pathlib": "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name] = name.name
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    calls: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, scope: str = "<module>") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            head, *tail = name.split(".")
            canonical = ".".join([aliases.get(head, head), *tail])
            if canonical in ("os.getcwd", "os.chdir", "pathlib.Path.cwd"):
                calls[scope, canonical.replace("pathlib.Path", "Path")] += 1
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return calls


def test_tools_and_hooks_use_session_cwd() -> None:
    actual: Counter[tuple[str, str, str]] = Counter()
    for folder in ("tools", "hooks"):
        for path in (ROOT / "gptme" / folder).rglob("*.py"):
            if "tests" in path.parts:
                continue
            for (scope, call), count in _cwd_calls(path.read_text()).items():
                actual[
                    str(path.relative_to(ROOT / "gptme")).replace(os.sep, "/"),
                    scope,
                    call,
                ] = count
    assert not (actual - ALLOWED), "Use gptme.util.workspace for session-relative paths"
    assert not (ALLOWED - actual), "Remove obsolete process-cwd exceptions"


def test_cwd_guard_detects_aliases() -> None:
    assert _cwd_calls(
        "import os as system\nfrom pathlib import Path as P\nfrom os import chdir as cd\ndef f():\n system.getcwd(); P.cwd(); cd('/tmp')"
    ) == Counter(
        {
            ("f", "os.getcwd"): 1,
            ("f", "Path.cwd"): 1,
            ("f", "os.chdir"): 1,
        }
    )


def test_workspace_leaf_import_is_lightweight(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gptme.util.workspace import current_workspace; import sys; assert 'gptme.tools.shell' not in sys.modules; assert 'gptme.tools.base' not in sys.modules; print(current_workspace())",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path.resolve()


def test_file_tools_from_hostile_host_cwd(tmp_path: Path) -> None:
    """Run in another process: never chdir the pytest process to test isolation."""
    host = tmp_path / "host"
    host.mkdir()
    (host / "note.txt").write_text("host poison\n")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    # No live parent conversation identity/config should select a test workspace.
    for key in (
        "GPTME_LOGS_HOME",
        "GPTME_WORKSPACE",
        "BOB_SESSION_LABEL",
        "BOB_SESSION_ID",
    ):
        env.pop(key, None)
    code = """
import contextvars
import os
from pathlib import Path
from gptme.util.workspace import set_workspace_cwd, current_workspace
from gptme.tools.save import execute_save_impl
from gptme.tools.read import execute_read
from gptme.tools.hashline_edit import execute_hashline_edit
from gptme.tools._hashline_snapshot import get_stored_tag
from gptme.tools.shell import get_shell
host = Path.cwd()
sessions = []
for name in ("a", "b"):
    workspace = host.parent / name
    workspace.mkdir()
    context = contextvars.Context()
    project = workspace / "project"
    project.mkdir()
    context.run(set_workspace_cwd, str(workspace))
    shell = context.run(get_shell)
    import shlex
    status, _, error = context.run(shell.run, "cd " + shlex.quote(str(project)))
    assert status == 0, error
    sessions.append((context, project, name))
for context, workspace, name in reversed(sessions):
    messages = context.run(lambda: list(execute_save_impl(name + "\\n", Path("note.txt"))))
    assert (workspace / "note.txt").read_text() == name + "\\n", messages
    assert context.run(current_workspace) == workspace.resolve()
for context, workspace, name in sessions:
    context.run(lambda: list(execute_read(None, ["note.txt"], None)))
    tag = get_stored_tag(str((workspace / "note.txt").resolve()))
    assert tag is not None
    block = f"[note.txt#{tag}]\\nPUT 1.=1:\\n+edited {name}\\n"
    messages = context.run(lambda: list(execute_hashline_edit(block, ["note.txt"], None)))
    assert "applied" in messages[0].content, messages
    assert (workspace / "note.txt").read_text() == f"edited {name}\\n"
    context.run(get_shell).close()
assert (host / "note.txt").read_text() == "host poison\\n"
assert Path.cwd() == host
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
