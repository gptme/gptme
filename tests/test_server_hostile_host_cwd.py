"""HTTP-server lifecycle with a hostile host cwd (gptme/gptme#5059).

A gptme server hosts every conversation in one process whose cwd belongs to
whoever launched it. This test starts a real server (Flask, threaded) in a
*subprocess* launched from an unrelated host directory seeded with same-named
"poison" files, interleaves two conversations through the HTTP API (mocked LLM,
auto-confirmed tools), and verifies that every relative-path tool call, shell
``cd``, failure and interrupt landed in the owning conversation's workspace and
that the host cwd and files were never touched.

The hostile cwd lives in a subprocess because changing the test runner's own
cwd would race with other tests. This is an internal regression backstop for
core tools; it does not make arbitrary plugin ``open("relative")`` calls or
native extensions session-safe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("flask", reason="flask not installed; install -E server")

CHILD = textwrap.dedent(
    r"""
    import json, os, socket, sys, threading, time, unittest.mock
    from pathlib import Path

    import requests

    HOST = os.getcwd()
    ROOT = Path(sys.argv[1])
    MUTATE = sys.argv[2] if len(sys.argv) > 2 else ""
    TOKEN = "hostile-cwd-token"
    NAMES = ("a", "b")
    cwd_seen = []  # os.getcwd() as observed from inside each LLM call

    if MUTATE == "hashline_process_relative":
        # Deliberately restore the pre-#5051 bug: resolve against the process cwd.
        import gptme.tools.hashline_edit as he
        he.resolve_in_workspace = lambda p: Path(p).expanduser().resolve()

    if MUTATE == "target_hook_process_relative":
        # Same class of bug in a hook: derive the AGENTS.md scan directory from
        # the process cwd instead of the session workspace.
        import gptme.hooks.tool_target_instructions as tti
        tti.resolve_in_workspace = lambda p: Path(p).expanduser().resolve()

    from gptme.init import init
    from gptme.lessons.index import LessonIndex
    from gptme.server import auth as auth_mod
    from gptme.server.app import create_app
    from gptme.tools import ToolUse
    from gptme.tools._hashline_snapshot import compute_tag

    os.environ["GPTME_SERVER_TOKEN"] = TOKEN
    auth_mod._server_token = None
    auth_mod._auth_enabled = True
    LessonIndex._default_dirs = staticmethod(lambda: [])
    init("openai/mock-model", interactive=False, tool_allowlist=None, tool_format="markdown")

    app = create_app()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, threaded=True),
        daemon=True,
    ).start()
    BASE = f"http://127.0.0.1:{port}/api/v2"
    H = {"Authorization": f"Bearer {TOKEN}"}
    http = requests.Session()
    http.trust_env = False
    for _ in range(100):
        try:
            http.get(BASE, headers=H, timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)

    def tool(name, args, content):
        return ToolUse(name, args, content).to_output("markdown")

    def script(name):
        patch = f"<<<<<<< ORIGINAL\nnote in {name}\n=======\npatched {name}\n>>>>>>> UPDATED"
        return "\n\n".join([
            f"Working in {name}.",
            tool("patch", ["note.txt"], patch),
            tool("read", ["note.txt"], ""),
            tool("read", ["h.txt"], ""),
            tool("hashline_edit", ["h.txt"], f"[h.txt#{compute_tag(f'hash {name}' + chr(10) + 'line2' + chr(10))}]" + f"\nPUT 1.=1:\n+hashed {name}\n"),
            tool("save", ["saved.txt"], f"saved by {name}"),
            tool("shell", [], "cd sub"),
            tool("save", ["deep.txt"], f"in sub {name}"),
            tool("shell", [], "pwd > pwd.txt"),
            # failures must stay in the session, not leak to the host cwd
            tool("save", ["../../escape.txt"], "escape"),
            tool("patch", ["missing.txt"], patch),
        ])

    barrier = threading.Barrier(len(NAMES))
    overlapped = threading.Event()

    def mock_stream(messages, model, tools=None, max_tokens=None, temperature=None, top_p=None):
        cwd_seen.append(os.getcwd())
        turns = [m for m in messages if m.role in ("user", "assistant") and not m.content.startswith("Output format")]
        last = turns[-1]
        if last.role == "assistant":
            user = [m for m in turns if m.role == "user"][-1].content.split()
            prefix = {"run": "Done ", "slow": "Slow continued ", "resume": "Resumed "}[user[0]]
            yield [prefix + user[1]]
            return
        words = last.content.split()
        kind, name = words[0], words[1]
        if kind == "run":
            try:
                barrier.wait(timeout=20)  # both generations in flight together
            except threading.BrokenBarrierError:
                errors["concurrency"] = "generations did not overlap at the barrier"
                raise
            overlapped.set()
            yield [script(name)]
        elif kind == "slow":
            last_user = max(i for i, m in enumerate(messages) if m.role == "user")
            if any(m.role == "tool" for m in messages[last_user + 1:]):
                # Only reachable if /interrupt failed to revoke this step: the
                # shell returned and the server continued the turn.
                yield [f"Slow continued {name}"]
                return
            # The real shell signals entry, then stays active until the driver
            # releases it after /interrupt; no scheduling-dependent sleep.
            yield ["Waiting.\n\n" + tool("shell", [],
                "touch tool-started; for i in $(seq 1 600); do "
                "test -f tool-release && break; sleep 0.1; done; "
                "touch tool-finished")]
        else:  # "resume"
            yield [tool("save", ["after.txt"], "after interrupt") ]

    def get_log(conv):
        return http.get(f"{BASE}/conversations/{conv}", headers=H).json().get("log", [])

    def wait_done(conv, text, timeout=90):
        # Wait for the assistant text AND for the server to leave generating.
        # The server persists the assistant message before the step's finally
        # block clears session.generating; returning on the text alone lets the
        # next /step race the still-active step and get a 409.
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = http.get(f"{BASE}/conversations/{conv}", headers=H).json()
            log = data.get("log", [])
            done = any(
                m["role"] == "assistant" and m["content"].strip() == text
                for m in log
            )
            generating = bool((data.get("session") or {}).get("generating"))
            if done and not generating:
                return log
            time.sleep(0.2)
        return get_log(conv)

    def send(name, content):
        r = http.post(f"{BASE}/conversations/{convs[name]}", headers=H, json={"role": "user", "content": content})
        assert r.status_code == 200, r.text
        r = http.post(
            f"{BASE}/conversations/{convs[name]}/step",
            headers=H,
            json={"session_id": sessions[name], "model": "openai/mock-model", "auto_confirm": True},
        )
        assert r.status_code == 200, r.text

    results, errors, logs = {}, {}, {}
    convs, sessions = {}, {}
    try:
        for name in NAMES:
            conv = f"hostile-{name}-{os.getpid()}"
            r = http.put(
                f"{BASE}/conversations/{conv}",
                headers=H,
                json={
                    "prompt": "You are a test assistant.",
                    "config": {
                        "chat": {
                            "workspace": str(ROOT / name),
                            "tools": ["save", "patch", "read", "hashline_edit", "shell"],
                        }
                    },
                },
            )
            assert r.status_code == 200, r.text
            convs[name], sessions[name] = conv, r.json()["session_id"]
            r = http.post(
                f"{BASE}/conversations/{conv}",
                headers=H,
                json={"role": "user", "content": f"run {name}"},
            )
            assert r.status_code == 200, r.text

        with (
            unittest.mock.patch("gptme.server.session_step._stream", mock_stream),
            unittest.mock.patch(
                "gptme.server.session_step._try_auto_name_and_notify", return_value=None
            ),
        ):
            for name in NAMES:
                r = http.post(
                    f"{BASE}/conversations/{convs[name]}/step",
                    headers=H,
                    json={
                        "session_id": sessions[name],
                        "model": "openai/mock-model",
                        "auto_confirm": True,
                    },
                )
                assert r.status_code == 200, r.text
            # A broken barrier must fail the driver, not silently serialize the
            # workspace scripts (background generation errors aren't propagated).
            deadline = time.monotonic() + 25
            while not overlapped.wait(timeout=0.1):
                assert not barrier.broken, errors
                assert time.monotonic() < deadline, "generations never overlapped"
            for name in NAMES:
                logs[name] = wait_done(convs[name], f"Done {name}")
            assert not barrier.broken, errors

            # Interrupt a running tool in b, then keep using the session.
            send("b", "slow b")
            # The conversation reuses its shell across steps (gptme/gptme#5187),
            # so the ``cd sub`` from the first turn still holds here.
            started = ROOT / "b" / "sub" / "tool-started"
            release = ROOT / "b" / "sub" / "tool-release"
            finished = ROOT / "b" / "sub" / "tool-finished"
            try:
                deadline = time.monotonic() + 30
                while not started.exists():
                    assert time.monotonic() < deadline, "shell tool never started"
                    time.sleep(0.1)
                r = http.post(
                    f"{BASE}/conversations/{convs['b']}/interrupt",
                    headers=H,
                    json={"session_id": sessions["b"]},
                )
                assert r.status_code == 200, r.text
                assert r.json()["message"] == "Interrupted", r.text
            finally:
                release.touch()
            # The shell exits on release, but its exit marker does not establish
            # that the server has processed its result or continued the turn.
            deadline = time.monotonic() + 10
            while not finished.exists():
                assert time.monotonic() < deadline, "shell tool never exited"
                time.sleep(0.05)
            # Observe a bounded window, not a single sample: a delayed server
            # continuation must still fail before a new user turn can hide it.
            deadline = time.monotonic() + 10
            while True:
                assert not any(
                    m["role"] == "assistant" and m["content"].strip() == "Slow continued b"
                    for m in get_log(convs["b"])
                ), "slow step continued after /interrupt — interrupt did not cancel it"
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            send("b", "resume b")
            logs["b"] = wait_done(convs["b"], "Resumed b", timeout=60)
    except BaseException as e:  # noqa: BLE001
        errors["driver"] = repr(e)

    print(json.dumps({
        "host_before": HOST, "host_after": os.getcwd(),
        "cwd_seen": sorted(set(cwd_seen)),
        "errors": errors,
        "logs": {n: [m["content"] for m in l] for n, l in logs.items()},
        "host_files": sorted(os.listdir(HOST)),
    }), flush=True)
    os._exit(0)
    """
)

HOST_FILES = {
    "note.txt": "HOST NOTE\n",
    "h.txt": "HOST HASH\n",
    "saved.txt": "HOST SAVED\n",
    "deep.txt": "HOST DEEP\n",
    "missing.txt": "HOST MISSING\n",
}


def _run_child(tmp_path: Path, mutate: str = "") -> dict:
    host = tmp_path / "host"
    root = tmp_path / "workspaces"
    home = tmp_path / "home"
    shell_tmp = tmp_path / "shell-tmp"
    for d in (host, home, shell_tmp):
        d.mkdir()
    for name in ("a", "b"):
        ws = root / name
        (ws / "sub").mkdir(parents=True)
        (ws / "note.txt").write_text(f"note in {name}\n")
        (ws / "h.txt").write_text(f"hash {name}\nline2\n")
        # Picked up by the CWD_CHANGED hooks after the session's shell ``cd sub``.
        (ws / "sub" / "AGENTS.md").write_text(f"AGENTS-MARK-{name}\n")
    for fname, content in HOST_FILES.items():  # poison, same names as workspaces
        (host / fname).write_text(content)
    (host / "sub").mkdir()
    (host / "sub" / "AGENTS.md").write_text("AGENTS-MARK-host\n")
    (host / "AGENTS.md").write_text("AGENTS-MARK-host\n")

    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(shell_tmp),
        "TEMP": str(shell_tmp),
        "TMP": str(shell_tmp),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "GPTME_SHELL_MEMORY_GUARD": "0",
        "GPTME_SANDBOX": "none",
        "GPTME_PYTHON_KERNEL": "0",
        "OPENAI_API_KEY": "sk-not-a-real-key",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), mutate],
        check=False,
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as e:
        raise AssertionError(
            f"no payload\n{proc.stdout[-2000:]}\n{proc.stderr[-3000:]}"
        ) from e
    payload["root"], payload["host"] = str(root), str(host)
    return payload


def _check(payload: dict) -> list[str]:
    """Return the violated invariants (empty means isolated)."""
    problems: list[str] = []
    root, host = Path(payload["root"]), Path(payload["host"])
    if payload["errors"]:
        problems.append(f"driver errors: {payload['errors']}")
    if payload["host_after"] != payload["host_before"]:
        problems.append("host cwd changed")
    if payload["cwd_seen"] != [payload["host_before"]]:
        problems.append(f"process cwd drifted during generation: {payload['cwd_seen']}")
    if payload["host_files"] != sorted([*HOST_FILES, "AGENTS.md", "sub"]):
        problems.append(f"host gained files: {payload['host_files']}")
    for fname, content in HOST_FILES.items():
        if (host / fname).read_text() != content:
            problems.append(f"host file {fname} was modified")
    for name in ("a", "b"):
        ws = root / name
        sub = str((ws / "sub").resolve())
        expect = {
            "note.txt": f"patched {name}",
            "saved.txt": f"saved by {name}",
            "h.txt": f"hashed {name}",
            "sub/deep.txt": f"in sub {name}",
        }
        for rel, want in expect.items():
            f = ws / rel
            if not f.exists() or want not in f.read_text():
                problems.append(f"{name}: {rel} missing or wrong")
        pwd = ws / "sub" / "pwd.txt"
        if not pwd.exists() or pwd.read_text().strip() != sub:
            problems.append(f"{name}: shell cd not followed (pwd.txt)")
        joined = "\n".join(payload["logs"].get(name, []))
        if f"Done {name}" not in joined:
            problems.append(f"{name}: conversation never completed")
        if name == "b" and "Resumed b" not in joined:
            problems.append("b: session unusable after interrupt")
        # CWD_CHANGED hooks (cwd_awareness, agents_md_inject) must follow the
        # session's shell cd, not the host process cwd.
        if f"AGENTS-MARK-{name}" not in joined:
            problems.append(f"{name}: cd hook did not inject the workspace's AGENTS.md")
        if "AGENTS-MARK-host" in joined:
            problems.append(f"{name}: cd hook injected the host's AGENTS.md")
        if "HOST " in joined:
            problems.append(f"{name}: log shows host file content")
        other = "b" if name == "a" else "a"
        if f"saved by {other}" in joined or f"patched {other}" in joined:
            problems.append(f"{name}: log shows the other conversation's output")
    after = [
        p
        for p in (root / "b" / "after.txt", root / "b" / "sub" / "after.txt")
        if p.exists()
    ]
    if len(after) != 1 or "after interrupt" not in after[0].read_text():
        problems.append(f"b: post-interrupt save missing/misplaced: {after}")
    if (root / "a" / "after.txt").exists() or (
        root / "a" / "sub" / "after.txt"
    ).exists():
        problems.append("a: received b's post-interrupt save")
    if (root / "escape.txt").exists() or (root.parent / "escape.txt").exists():
        problems.append("traversal write escaped the workspace")
    return problems


@pytest.mark.timeout(300)
def test_server_conversations_stay_isolated_from_hostile_host_cwd(
    tmp_path: Path,
) -> None:
    assert _check(_run_child(tmp_path)) == []


@pytest.mark.timeout(300)
def test_harness_catches_process_relative_hashline_edit(tmp_path: Path) -> None:
    """Acceptance oracle: restoring the pre-#5051 bug must make the suite fail."""
    payload = _run_child(tmp_path, mutate="hashline_process_relative")
    problems = _check(payload)
    # Only the mutated hashline edits may fail: driver errors, cwd drift, or
    # unrelated missing files must not masquerade as detecting this regression.
    assert problems == ["a: h.txt missing or wrong", "b: h.txt missing or wrong"]
    host_file = Path(payload["host"]) / "h.txt"
    expected = (
        f"hashline_edit: no snapshot found for {host_file}. "
        "Call `read` first to capture a snapshot."
    )
    for name in ("a", "b"):
        assert expected in payload["logs"][name], payload["logs"][name]
        assert (Path(payload["root"]) / name / "h.txt").read_text() == (
            f"hash {name}\nline2\n"
        )


@pytest.mark.timeout(300)
def test_harness_catches_process_relative_tool_target_hook(tmp_path: Path) -> None:
    """The hook layer is covered too: a process-relative scan dir leaks host AGENTS.md."""
    problems = _check(_run_child(tmp_path, mutate="target_hook_process_relative"))
    assert problems == [
        "a: cd hook injected the host's AGENTS.md",
        "b: cd hook injected the host's AGENTS.md",
    ]
