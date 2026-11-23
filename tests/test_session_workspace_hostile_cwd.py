"""Two interleaved server-style sessions in a process whose cwd is hostile.

A gptme server hosts every conversation in one process and never chdirs into a
session's workspace. This test runs that setup in a *subprocess* started from an
unrelated host directory holding same-named "poison" files, so any tool that
resolves a relative path against the process cwd (the pre-#5051 hashline_edit
bug) reads or writes the host's files and fails the assertions. The harness
itself never changes its own cwd (that would race with other tests).

Covered per session: save, read, patch, hashline_edit, shell ``cd`` with
``cwd_changed`` following, subprocess-spawning tools that inherit a cwd
(autocommit's git calls, a background shell job), and a failing operation. Afterwards the host cwd and
host files must be untouched. Not covered: arbitrary plugin ``open("relative")``
or native extensions (see ``test_session_workspace_guard.py`` for the limit).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

CHILD = textwrap.dedent(
    """
    import contextvars, json, os, sys, threading
    from pathlib import Path

    HOST = os.getcwd()
    ROOT = Path(sys.argv[1])
    MUTATE = sys.argv[2] if len(sys.argv) > 2 else ""

    if MUTATE == "hashline_process_relative":
        # Deliberately restore the pre-#5051 bug: resolve against the process cwd.
        import gptme.tools.hashline_edit as he
        he.resolve_in_workspace = lambda p: Path(p).expanduser().resolve()

    if MUTATE == "subprocess_process_cwd":
        # Spawn subprocesses in the inherited process cwd, as before the fix.
        import gptme.tools.autocommit as ac
        import gptme.tools.shell_background as sb
        ac.get_workspace_cwd = lambda: None
        sb.get_workspace_cwd = lambda: None

    from gptme.tools._hashline_snapshot import get_stored_tag
    from gptme.tools.autocommit import autocommit
    from gptme.tools.base import current_workspace
    from gptme.tools.hashline_edit import execute_hashline_edit
    from gptme.tools.patch import execute_patch_impl
    from gptme.tools.read import execute_read
    from gptme.tools.save import execute_save_impl
    from gptme.tools.shell import get_shell, set_workspace_cwd
    from gptme.tools.shell_background import start_background_job

    SESSIONS = ("a", "b")
    barrier = threading.Barrier(len(SESSIONS))
    results = {name: {} for name in SESSIONS}
    errors = {}

    def text(msgs):
        return "\\n".join(m.content for m in msgs)

    def session(name):
        ws = ROOT / name
        out = results[name]
        shell = None
        try:
            set_workspace_cwd(str(ws))
            shell = get_shell()
            out["workspace"] = str(current_workspace())
            barrier.wait()

            list(execute_save_impl("saved by " + name, Path("saved.txt")))
            barrier.wait()

            out["read"] = text(execute_read(None, ["note.txt"], None))
            barrier.wait()

            patch = "<<<<<<< ORIGINAL\\nnote in " + name + "\\n=======\\npatched " + name + "\\n>>>>>>> UPDATED"
            list(execute_patch_impl(patch, Path("note.txt")))
            barrier.wait()

            list(execute_read(None, ["h.txt"], None))
            tag = get_stored_tag(str((ws / "h.txt").resolve()))
            block = "[h.txt#" + str(tag) + "]\\nPUT 1.=1:\\n+hashed " + name + "\\n"
            out["hashline"] = text(execute_hashline_edit(block, ["h.txt"], None))
            barrier.wait()

            # git must inspect this session's repository, not the host cwd.
            out["autocommit"] = autocommit().content
            barrier.wait()

            status, _, err = shell.run("cd sub")
            out["cd_status"] = status
            out["live_cwd"] = str(Path(shell.get_cwd()).resolve())
            out["workspace_after_cd"] = str(current_workspace())
            barrier.wait()

            # relative paths now follow the shell into sub/
            list(execute_save_impl("in sub " + name, Path("deep.txt")))
            shell.run("pwd > pwd.txt")
            # A background job is a separate process: it must start in the
            # session's live cwd too, not inherit the host process cwd.
            job = start_background_job("pwd > bg.txt")
            out["bg_rc"] = job.process.wait(timeout=30)
            barrier.wait()

            # Failures must not leak into the host cwd or the other session.
            try:
                list(execute_save_impl("x", Path("../../escape.txt")))
                out["traversal"] = "allowed"
            except Exception as e:
                out["traversal"] = type(e).__name__
            try:
                list(execute_patch_impl("garbage", Path("missing.txt")))
            except Exception as e:
                out["patch_missing"] = type(e).__name__
            barrier.wait()
        except BaseException as e:  # noqa: BLE001
            errors[name] = repr(e)
            barrier.abort()
        finally:
            if shell is not None:
                shell.close()

    threads = []
    for name in SESSIONS:
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(session, name))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=120)

    print(json.dumps({
        "host_before": HOST, "host_after": os.getcwd(),
        "results": results, "errors": errors,
        "host_files": sorted(os.listdir(HOST)),
    }), flush=True)
    os._exit(0)
    """
)


def _run_child(tmp_path: Path, mutate: str = "") -> dict:
    host = tmp_path / "host"
    root = tmp_path / "sessions"
    host.mkdir()
    for name in ("a", "b"):
        ws = root / name
        (ws / "sub").mkdir(parents=True)
        (ws / "note.txt").write_text(f"note in {name}\n")
        (ws / "h.txt").write_text(f"hash {name}\nline2\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        # No user/global hooks or commit signing in the fixture repositories.
        git += ["-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q"], cwd=ws, check=True)
        subprocess.run([*git, "add", "note.txt", "h.txt"], cwd=ws, check=True)
        subprocess.run(
            [*git, "commit", "-q", "--no-verify", "-m", "init"], cwd=ws, check=True
        )
    # Poison: same names with different content in the hostile host cwd.
    (host / "note.txt").write_text("HOST NOTE\n")
    (host / "h.txt").write_text("HOST HASH\n")
    (host / "saved.txt").write_text("HOST SAVED\n")
    (host / "deep.txt").write_text("HOST DEEP\n")
    (host / "bg.txt").write_text("HOST BG\n")

    shell_tmp = tmp_path / "shell-tmp"
    shell_tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(shell_tmp),
        "TEMP": str(shell_tmp),
        "TMP": str(shell_tmp),
        "GPTME_SHELL_MEMORY_GUARD": "0",
        "GPTME_SANDBOX": "none",
        "GPTME_PYTHON_KERNEL": "0",
    }
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), mutate],
        check=False,
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert not list(shell_tmp.glob("gptme-shell-state-*")), "shell state files leaked"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    payload["root"], payload["host"] = str(root), str(host)
    return payload


def _check(payload: dict) -> list[str]:
    """Return the list of violated invariants (empty means isolated)."""
    problems: list[str] = []
    root, host = Path(payload["root"]), Path(payload["host"])
    if payload["errors"]:
        problems.append(f"session errors: {payload['errors']}")
    if payload["host_after"] != payload["host_before"]:
        problems.append("host cwd changed")
    expected_host = {
        "note.txt": "HOST NOTE\n",
        "h.txt": "HOST HASH\n",
        "saved.txt": "HOST SAVED\n",
        "deep.txt": "HOST DEEP\n",
        "bg.txt": "HOST BG\n",
    }
    if payload["host_files"] != sorted(expected_host):
        problems.append(f"host gained files: {payload['host_files']}")
    for fname, content in expected_host.items():
        if (host / fname).read_text() != content:
            problems.append(f"host file {fname} was modified")
    for name in ("a", "b"):
        ws = root / name
        res = payload["results"][name]
        checks = {
            "saved.txt": f"saved by {name}",
            "note.txt": f"patched {name}",
            "h.txt": f"hashed {name}",
            "sub/deep.txt": f"in sub {name}",
        }
        for rel, want in checks.items():
            f = ws / rel
            if not f.exists() or want not in f.read_text():
                problems.append(f"{name}: {rel} missing or wrong")
        if f"note in {name}" not in res.get("read", ""):
            problems.append(f"{name}: read saw wrong file: {res.get('read')!r}")
        if "applied" not in res.get("hashline", ""):
            problems.append(f"{name}: hashline_edit failed: {res.get('hashline')!r}")
        if res.get("workspace") != str(ws.resolve()):
            problems.append(f"{name}: workspace {res.get('workspace')}")
        sub = str((ws / "sub").resolve())
        if res.get("live_cwd") != sub or res.get("workspace_after_cd") != sub:
            problems.append(f"{name}: shell cd not followed: {res}")
        pwd = ws / "sub" / "pwd.txt"
        if not pwd.exists() or pwd.read_text().strip() != sub:
            problems.append(f"{name}: shell pwd wrong")
        bg = ws / "sub" / "bg.txt"
        if res.get("bg_rc") != 0 or not bg.exists() or bg.read_text().strip() != sub:
            problems.append(f"{name}: background job ran outside the session cwd")
        other = "b" if name == "a" else "a"
        commit = res.get("autocommit", "")
        if f"+patched {name}" not in commit or f"patched {other}" in commit:
            problems.append(f"{name}: autocommit inspected the wrong repository")
        if res.get("traversal") == "allowed" or "patch_missing" not in res:
            problems.append(f"{name}: failure paths not exercised: {res}")
    if (root / "escape.txt").exists() or (root.parent / "escape.txt").exists():
        problems.append("traversal write escaped the workspace")
    return problems


def test_two_sessions_stay_isolated_from_hostile_host_cwd(tmp_path: Path) -> None:
    payload = _run_child(tmp_path)
    assert _check(payload) == []


def test_harness_catches_process_relative_hashline_edit(tmp_path: Path) -> None:
    """Acceptance oracle: restoring the pre-#5051 bug must make the suite fail."""
    payload = _run_child(tmp_path, mutate="hashline_process_relative")
    problems = _check(payload)
    assert any("hashline_edit failed" in p for p in problems), problems


def test_harness_catches_subprocesses_in_process_cwd(tmp_path: Path) -> None:
    """Subprocess tools spawned in the inherited host cwd must fail the suite."""
    problems = _check(_run_child(tmp_path, mutate="subprocess_process_cwd"))
    assert problems == [
        "host file bg.txt was modified",
        "a: background job ran outside the session cwd",
        "a: autocommit inspected the wrong repository",
        "b: background job ran outside the session cwd",
        "b: autocommit inspected the wrong repository",
    ], problems
