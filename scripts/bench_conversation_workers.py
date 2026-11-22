#!/usr/bin/env python3
"""Research prototype for gptme/gptme#5060: process-per-conversation workers.

Reproducible measurements and behaviour demos backing
``docs/design/conversation-workers.md``. Not production code: it prototypes the
IPC contract (newline-delimited JSON over the worker's stdio) and measures the
spawn/memory/latency costs the design note's recommendation rests on.

    python scripts/bench_conversation_workers.py startup   [--preload gptme.tools]
    python scripts/bench_conversation_workers.py ipc       [--turns 300]
    python scripts/bench_conversation_workers.py scaling   [--max-workers 8]
    python scripts/bench_conversation_workers.py isolation
    python scripts/bench_conversation_workers.py lifecycle
    python scripts/bench_conversation_workers.py kernel    # needs jupyter_client+ipykernel
    python scripts/bench_conversation_workers.py all

Every subcommand prints one JSON document so runs can be diffed. Requires
Linux for PSS (``/proc/<pid>/smaps_rollup``); other platforms report RSS only
via ``psutil`` when installed.

IPC contract prototyped here (host -> worker / worker -> host), one JSON per line:

    host:   {"t":"turn","id":N,"code":...}   run one turn (here: exec'd code)
            {"t":"confirm_reply","id":N,"ok":bool}
            {"t":"quit"}                     graceful shutdown
            (cancel is a signal, not a message: SIGINT on POSIX, so a worker
             blocked in a C call or a tight loop is still interruptible)
    worker: {"t":"ready","pid":P,"cwd":...}
            {"t":"stream","id":N,"chunk":...}   incremental output
            {"t":"confirm","id":N,"q":...}      blocks the turn until confirm_reply
            {"t":"done","id":N,"result":...} | {"t":"cancelled","id":N}
"""

from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import multiprocessing as mp
import os
import platform
import queue
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SELF = str(Path(__file__).resolve())


# --------------------------------------------------------------------------- worker
def worker_main(argv: list[str]) -> None:
    """Worker process: native cwd, owns one conversation's Python state."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--preload", default="")
    ap.add_argument("--lease", default="")
    args = ap.parse_args(argv)

    if args.lease:  # single-writer lease: held for the worker's lifetime
        fd = open(args.lease, "a+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({"t": "lease_denied"}), flush=True)
            sys.exit(3)
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    os.chdir(args.cwd)  # the whole point: process-native cwd
    for mod in [m for m in args.preload.split(",") if m]:
        importlib.import_module(mod)

    out = sys.stdout

    def emit(msg: dict) -> None:
        out.write(json.dumps(msg) + "\n")
        out.flush()

    state: dict = {}  # live Python state: survives across turns, lost on eviction
    emit({"t": "ready", "pid": os.getpid(), "cwd": os.getcwd()})
    for line in sys.stdin:
        msg = json.loads(line)
        if msg["t"] == "quit":
            return
        if msg["t"] != "turn":
            continue
        tid = msg["id"]

        def stream(chunk: str, tid: int = tid) -> None:
            emit({"t": "stream", "id": tid, "chunk": chunk})

        def confirm(q: str, tid: int = tid) -> bool:
            emit({"t": "confirm", "id": tid, "q": q})
            reply = json.loads(sys.stdin.readline())
            return bool(reply.get("ok"))

        env = {"state": state, "stream": stream, "confirm": confirm, "result": None}
        try:
            exec(msg["code"], env)
            emit({"t": "done", "id": tid, "result": env.get("result")})
        except KeyboardInterrupt:
            emit({"t": "cancelled", "id": tid})
        except Exception as e:  # tool error != worker death
            emit({"t": "done", "id": tid, "result": None, "error": repr(e)})


# --------------------------------------------------------------------------- host
class Worker:
    def __init__(
        self,
        cwd: str,
        preload: str = "",
        lease: str = "",
        python: str = sys.executable,
    ) -> None:
        self.t_spawn = time.perf_counter()
        cmd = [python, SELF, "--worker", "--cwd", cwd, "--preload", preload]
        if lease:
            cmd += ["--lease", lease]
        # Own process group so SIGKILL escalation reaches orphan children (shell/kernel).
        self.p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._q: queue.Queue[dict] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.ready = self.recv()
        self.t_ready = time.perf_counter() - self.t_spawn
        self._id = 0

    def send(self, msg: dict) -> None:
        assert self.p.stdin
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _pump(self) -> None:
        assert self.p.stdout
        for line in self.p.stdout:  # ends at EOF (maybe never, see recv)
            self._q.put(json.loads(line))

    def recv(self) -> dict:
        """Next worker message, or a synthetic ``exit`` event.

        A reader thread feeds a queue (select() on the fd would miss lines already
        buffered by the text wrapper). Exit is detected by waiting on the
        *process*, never by pipe EOF alone: a grandchild that inherited the
        worker's stdout (an orphaned shell job) keeps the pipe open after the
        worker is SIGKILLed, so a blocking readline() would hang forever. The
        lifecycle bench demonstrates this.
        """
        while True:
            try:
                return self._q.get(timeout=0.05)
            except queue.Empty:
                if self.p.poll() is not None and self._q.empty():
                    return {"t": "exit", "returncode": self.p.returncode}

    def turn(self, code: str, on_msg=None) -> dict:
        self._id += 1
        self.send({"t": "turn", "id": self._id, "code": code})
        while True:
            m = self.recv()
            if on_msg:
                on_msg(m)
            if m["t"] in ("done", "cancelled", "exit"):
                return m

    def close(self, grace: float = 2.0) -> None:
        if self.p.poll() is None:
            try:
                self.send({"t": "quit"})
                self.p.wait(grace)
            except Exception:
                pass
        self.kill()

    def kill(self) -> None:
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.p.wait()


def mem_kb(pid: int) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("Rss", "Pss", "Private_Dirty", "Private_Clean"):
                out[k] = int(v.split()[0])
    except OSError:
        try:
            import psutil

            out["Rss"] = psutil.Process(pid).memory_info().rss // 1024
        except Exception:
            pass
    return out


def cpu_s(pid: int) -> float:
    try:
        f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    except OSError:
        return float("nan")


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def env_info() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "mp_default_start": mp.get_start_method(allow_none=True) or "(unset)",
    }


# --------------------------------------------------------------------------- benches
def _thread_noop(ev: threading.Event) -> None:
    ev.set()


def _fork_child(conn) -> None:  # module-level: picklable for forkserver
    conn.send(os.getpid())
    conn.recv()


def bench_startup(preload: str, reps: int = 10) -> dict:
    res: dict = {"preload": preload or "(none)"}
    # thread baseline
    ts = []
    for _ in range(200):
        ev = threading.Event()
        t0 = time.perf_counter()
        threading.Thread(target=_thread_noop, args=(ev,)).start()
        ev.wait()
        ts.append((time.perf_counter() - t0) * 1000)
    res["thread_start_ms_p50"] = round(statistics.median(ts), 3)

    def run(name: str, pre: str) -> None:
        tmp = tempfile.mkdtemp()
        ready, first, rss, pss = [], [], [], []
        for _ in range(reps):
            w = Worker(tmp, preload=pre)
            ready.append(w.t_ready * 1000)
            t0 = time.perf_counter()
            w.turn("result = 1")
            first.append((time.perf_counter() - t0) * 1000)
            m = mem_kb(w.p.pid)
            rss.append(m.get("Rss", 0) / 1024)
            pss.append(m.get("Pss", 0) / 1024)
            w.close()
        res[name] = {
            "cold_to_ready_ms_p50": round(statistics.median(ready), 1),
            "cold_to_ready_ms_max": round(max(ready), 1),
            "first_turn_ms_p50": round(statistics.median(first), 2),
            "rss_mb_p50": round(statistics.median(rss), 1),
            "pss_mb_p50": round(statistics.median(pss), 1),
        }

    run("spawn_bare_python", "")
    if preload:
        run("spawn_preload", preload)
        # forkserver whose preloaded parent is single-threaded: children share
        # pages copy-on-write. Measures what a warm zygote could buy.
        ctx = mp.get_context("forkserver")
        ctx.set_forkserver_preload(preload.split(","))
        parent_conn, child_conn = ctx.Pipe()
        p = ctx.Process(target=_fork_child, args=(child_conn,))
        p.start()  # first start launches the forkserver itself (cold)
        parent_conn.recv()
        ts, rss, pss = [], [], []
        for _ in range(reps):
            a, b = ctx.Pipe()
            t0 = time.perf_counter()
            q = ctx.Process(target=_fork_child, args=(b,))
            q.start()
            a.recv()
            ts.append((time.perf_counter() - t0) * 1000)
            m = mem_kb(q.pid or 0)
            rss.append(m.get("Rss", 0) / 1024)
            pss.append(m.get("Pss", 0) / 1024)
            a.send(1)
            q.join()
        parent_conn.send(1)
        p.join()
        res["forkserver_preload_warm"] = {
            "to_ready_ms_p50": round(statistics.median(ts), 1),
            "rss_mb_p50": round(statistics.median(rss), 1),
            "pss_mb_p50": round(statistics.median(pss), 1),
        }
    return res


def bench_ipc(turns: int, preload: str) -> dict:
    tmp = tempfile.mkdtemp()
    w = Worker(tmp, preload=preload)
    lat = []
    for _ in range(turns):
        t0 = time.perf_counter()
        w.turn("result = 1")
        lat.append((time.perf_counter() - t0) * 1000)
    # streaming: 1000 chunks in one turn
    t0 = time.perf_counter()
    n = 0

    def count(m: dict) -> None:
        nonlocal n
        n += m["t"] == "stream"

    w.turn("for i in range(1000): stream('x'*80)", on_msg=count)
    stream_ms = (time.perf_counter() - t0) * 1000
    # large payload: 8 MiB result inline (why #4663 file-backing matters)
    t0 = time.perf_counter()
    w.turn("result = 'x' * (8 * 1024 * 1024)")
    big_ms = (time.perf_counter() - t0) * 1000
    # confirmation round trip
    got: list[dict] = []

    def on_msg(m: dict) -> None:
        got.append(m)
        if m["t"] == "confirm":
            w.send({"t": "confirm_reply", "id": m["id"], "ok": True})

    r = w.turn("result = confirm('run rm -rf build?')", on_msg=on_msg)
    cpu0, t0 = cpu_s(w.p.pid), time.perf_counter()
    time.sleep(3)
    idle = (cpu_s(w.p.pid) - cpu0) / (time.perf_counter() - t0) * 100
    w.close()
    return {
        "warm_turn_ms_p50": round(statistics.median(lat), 3),
        "warm_turn_ms_p99": round(pct(lat, 0.99), 3),
        "stream_1000_chunks_ms": round(stream_ms, 1),
        "stream_chunks_received": n,
        "inline_8MiB_result_ms": round(big_ms, 1),
        "confirm_roundtrip_result": r.get("result"),
        "idle_cpu_pct_over_3s": round(idle, 3),
    }


CPU_TURN = "x=0\nfor i in range(3_000_000): x+=i\nresult = x"


def bench_scaling(max_workers: int, preload: str) -> dict:
    tmp = tempfile.mkdtemp()
    out: dict = {"cpu_turn": "3M-iteration pure-Python loop", "rows": []}
    # single-process baseline: how long does ONE turn take?
    t0 = time.perf_counter()
    exec(CPU_TURN, {})
    one = (time.perf_counter() - t0) * 1000
    n = 1
    while n <= max_workers:
        # threads in this process (today's server model)
        t0 = time.perf_counter()
        ths = [threading.Thread(target=lambda: exec(CPU_TURN, {})) for _ in range(n)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        thread_ms = (time.perf_counter() - t0) * 1000
        # N workers, idle first so we measure turn wall time and memory separately
        ws = [Worker(tmp, preload=preload) for _ in range(n)]
        mem = [mem_kb(w.p.pid) for w in ws]
        t0 = time.perf_counter()
        thr = [threading.Thread(target=w.turn, args=(CPU_TURN,)) for w in ws]
        for t in thr:
            t.start()
        for t in thr:
            t.join()
        proc_ms = (time.perf_counter() - t0) * 1000
        out["rows"].append(
            {
                "conversations": n,
                "single_turn_ms": round(one, 0),
                "threads_wall_ms": round(thread_ms, 0),
                "workers_wall_ms": round(proc_ms, 0),
                "worker_rss_mb_each": round(
                    statistics.mean(m["Rss"] for m in mem) / 1024, 1
                ),
                "worker_pss_mb_each": round(
                    statistics.mean(m["Pss"] for m in mem) / 1024, 1
                ),
            }
        )
        for w in ws:
            w.close()
        n *= 2
    return out


ISOLATION_CODE = """
import os, subprocess, ctypes
from pathlib import Path
libc = ctypes.CDLL(None)
buf = ctypes.create_string_buffer(4096)
libc.getcwd(buf, 4096)
result = {
  "open_relative": open("marker.txt").read().strip(),
  "path_exists": Path("marker.txt").exists(),
  "subprocess_no_cwd": subprocess.run(["cat", "marker.txt"], capture_output=True, text=True).stdout.strip(),
  "native_getcwd": os.path.basename(buf.value.decode()),
}
"""

THREAD_RACE = """
import os, threading, time
bad = 0
def conv(name, d):
    global bad
    for _ in range(2000):
        os.chdir(d)
        if open("marker.txt").read().strip() != name:  # unmodified relative open
            bad += 1
ths = [threading.Thread(target=conv, args=(n, d)) for n, d in ARGS]
for t in ths:
            t.start(); for t in ths:
            t.join()
result = bad
"""


def bench_isolation(preload: str) -> dict:
    base = Path(tempfile.mkdtemp())
    dirs = {}
    for name in ("conv_a", "conv_b"):
        d = base / name
        d.mkdir()
        (d / "marker.txt").write_text(name)
        dirs[name] = str(d)
    ws = {n: Worker(d, preload=preload) for n, d in dirs.items()}
    results = {n: w.turn(ISOLATION_CODE)["result"] for n, w in ws.items()}
    # same unmodified code, run concurrently in both
    for w in ws.values():
        w.close()
    ok = all(
        r["open_relative"] == n
        and r["path_exists"]
        and r["subprocess_no_cwd"] == n
        and r["native_getcwd"] == n
        for n, r in results.items()
    )
    # contrast: today's shared-process model with os.chdir under threads
    env: dict = {"ARGS": list(dirs.items())}
    exec(THREAD_RACE, env)
    return {
        "workers": results,
        "all_four_probes_independent": ok,
        "shared_process_threads_wrong_reads_of_4000": env["result"],
    }


def bench_lifecycle(preload: str) -> dict:
    tmp = tempfile.mkdtemp()
    lease = os.path.join(tmp, ".lock")
    res: dict = {}
    # 1. single-writer lease: a second worker for the same conversation is refused
    w1 = Worker(tmp, preload=preload, lease=lease)
    w2 = Worker.__new__(Worker)
    p2 = subprocess.Popen(
        [sys.executable, SELF, "--worker", "--cwd", tmp, "--lease", lease],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert p2.stdout
    res["second_writer_refused"] = (
        json.loads(p2.stdout.readline()).get("t") == "lease_denied"
    )
    p2.wait()
    del w2
    # 2. cancellation of a tight pure-Python loop AND of a blocking C call (sleep)
    for label, code in (
        ("busy_loop", "while True: pass"),
        ("blocking_sleep", "import time; time.sleep(60)"),
    ):
        w1._id += 1
        w1.send({"t": "turn", "id": w1._id, "code": code})
        time.sleep(0.3)
        t0 = time.perf_counter()
        os.kill(w1.p.pid, signal.SIGINT)
        m = w1.recv()
        res[f"sigint_cancel_{label}"] = {
            "reply": m["t"],
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    res["worker_survives_cancel"] = w1.turn("result = 2")["result"] == 2
    # 3. live state persists across turns (and would be lost on eviction)
    w1.turn("state['x'] = 41")
    res["live_state_persists"] = w1.turn("result = state['x'] + 1")["result"] == 42
    # 4. orphan cleanup: worker spawns a grandchild; SIGKILL of the group reaps it
    r = w1.turn("import subprocess; result = subprocess.Popen(['sleep','300']).pid")
    child = r["result"]
    # 5. crash: kill -9 the worker mid-turn; host sees exit, lease is released by the kernel
    w1._id += 1
    w1.send({"t": "turn", "id": w1._id, "code": "import time; time.sleep(60)"})
    time.sleep(0.2)
    os.kill(w1.p.pid, signal.SIGKILL)
    m = w1.recv()
    res["crash_detected"] = m["t"] == "exit"
    res["crash_returncode"] = m.get("returncode")
    time.sleep(0.2)
    res["orphan_grandchild_alive_after_worker_sigkill"] = Path(
        f"/proc/{child}"
    ).exists()
    try:
        os.killpg(w1.p.pid, signal.SIGKILL)  # group kill reaps it
    except ProcessLookupError:
        pass
    time.sleep(0.3)
    res["orphan_grandchild_alive_after_group_kill"] = Path(f"/proc/{child}").exists()
    w1.p.wait()
    # 6. restart after crash: new worker acquires the released lease (no duplicate writer window)
    w3 = Worker(tmp, preload=preload, lease=lease)
    res["restart_acquires_lease"] = w3.ready["t"] == "ready"
    w3.close()
    return res


def bench_kernel() -> dict:
    """Existing KernelBackend: cost, RPC bridge workload, live-object boundary."""
    try:
        from gptme.tools.python_kernel import KernelBackend
    except Exception as e:  # pragma: no cover
        return {"skipped": repr(e)}
    host_obj: list = []

    def host_echo(x):
        return x

    def host_get_obj():
        return host_obj  # pickled over the bridge: the kernel gets a COPY

    def host_len():
        return len(host_obj)

    be = KernelBackend()
    t0 = time.perf_counter()
    be.execute("1+1")
    out: dict = {"cold_first_cell_ms": round((time.perf_counter() - t0) * 1000, 0)}
    lat = []
    for _ in range(100):
        t0 = time.perf_counter()
        be.execute("1+1")
        lat.append((time.perf_counter() - t0) * 1000)
    out["warm_cell_ms_p50"] = round(statistics.median(lat), 2)
    out["warm_cell_ms_p99"] = round(pct(lat, 0.99), 2)
    for fn in (host_echo, host_get_obj, host_len):
        be.register_bridge_function(fn)
    # RPC workload: 200 small calls inside one cell, then one 4 MiB payload
    t0 = time.perf_counter()
    r = be.execute("for i in range(200): host_echo(i)\nprint('ok')")
    out["rpc_200_small_calls_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["rpc_small_ok"] = r.stdout.strip() == "ok"
    t0 = time.perf_counter()
    r = be.execute("print(len(host_echo('x' * (4*1024*1024))))")
    out["rpc_4MiB_roundtrip_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # live-object identity: kernel mutates what it got from the host; host unchanged
    be.execute("o = host_get_obj(); o.append(1)")
    out["live_object_identity_preserved"] = host_len() != 0
    # cwd: the kernel is a separate process with its own native cwd
    r = be.execute("import os; print(os.getcwd())")
    out["kernel_cwd"] = r.stdout.strip()
    out["host_cwd"] = os.getcwd()
    be.shutdown()
    return out


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker_main(sys.argv[2:])
        return
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "what",
        choices=[
            "startup",
            "ipc",
            "scaling",
            "isolation",
            "lifecycle",
            "kernel",
            "all",
        ],
    )
    ap.add_argument(
        "--preload",
        default="gptme.tools",
        help="comma-separated modules the worker imports",
    )
    ap.add_argument("--turns", type=int, default=300)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--max-workers", type=int, default=8)
    a = ap.parse_args()
    out: dict = {"env": env_info(), "argv": sys.argv[1:]}
    todo = (
        ["startup", "ipc", "scaling", "isolation", "lifecycle", "kernel"]
        if a.what == "all"
        else [a.what]
    )
    for name in todo:
        if name == "startup":
            out[name] = bench_startup(a.preload, a.reps)
        elif name == "ipc":
            out[name] = bench_ipc(a.turns, a.preload)
        elif name == "scaling":
            out[name] = bench_scaling(a.max_workers, a.preload)
        elif name == "isolation":
            out[name] = bench_isolation(a.preload)
        elif name == "lifecycle":
            out[name] = bench_lifecycle(a.preload)
        elif name == "kernel":
            out[name] = bench_kernel()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
