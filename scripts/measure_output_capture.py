#!/usr/bin/env python3
"""Offline, synthetic output-capture evidence for issue #4663.

Run with the checkout's Python environment. No providers or network
requests are started. Only a fixed synthetic child process is run. Temporary logs
contain synthetic data only and are removed.
Measurements describe formatting/storage, not end-to-end command execution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory

# Prevent a cold tokenizer cache from attempting a download. All budget knobs
# below are explicit so local agent settings do not change the comparison.
os.environ["GPTME_TIKTOKEN_TIMEOUT"] = "0"
for _key, _value in {
    "GPTME_SHELL_TRUNC_PRE_TOKENS": "2000",
    "GPTME_SHELL_TRUNC_POST_TOKENS": "8000",
    "GPTME_SHELL_TRUNC_STDERR_PRE_TOKENS": "2000",
    "GPTME_SHELL_TRUNC_STDERR_POST_TOKENS": "2000",
}.items():
    os.environ[_key] = _value

from gptme.tools.shell import _format_shell_output
from gptme.tools.shell_background import (
    BackgroundJob,
    _write_job_log,
    format_job_output,
)


def fixtures() -> dict[str, tuple[str, str, int]]:
    """Return stdout, stderr and actual fixture exit code for each workflow."""
    return {
        "compact_success": ("12 passed in 0.10s\n", "", 0),
        "pytest_log": (
            "".join(
                f"tests/test_sample.py::test_case_{i:05d} PASSED\n"
                for i in range(20000)
            )
            + "20000 passed in 4.00s\n",
            "",
            0,
        ),
        "api_json": (
            json.dumps([{"id": i, "state": "ok", "value": i % 7} for i in range(10000)])
            + "\n",
            "",
            0,
        ),
        "compiler_failure": (
            "building module\n" * 10000,
            "".join(f"module.c:{i}: warning: unused variable\n" for i in range(5000))
            + "module.c:99999: error: missing symbol\n",
            2,
        ),
        "structured_data": (
            "id,state,value\n" + "".join(f"{i},ok,{i % 7}\n" for i in range(30000)),
            "",
            0,
        ),
        "noisy_single_line": (
            "BEGIN-MARKER " + "x" * (2 * 1024 * 1024) + " END-MARKER",
            "",
            0,
        ),
    }


def measure(root: Path, name: str, stdout: str, stderr: str, code: int) -> dict:
    logdir = root / name
    logdir.mkdir()
    tracemalloc.start()
    start = time.perf_counter()
    preview = _format_shell_output(
        f"synthetic-{name}", stdout, stderr, code, False, False, logdir=logdir
    )
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    files = sorted((logdir / "tool-outputs").rglob("*.txt"))
    contents = [p.read_text() for p in files]
    # Fixtures have no secrets or ANSI; each non-empty stream is either kept
    # inline or recoverable exactly as a separately saved text file.
    complete = all(
        s in contents or s.rstrip() in preview for s in (stdout, stderr) if s
    )
    # Presentation-only candidate: shared job-sized 8k/2k budgets, head + tail.
    # This is NOT a new capture implementation or production formatter.
    candidate = json.dumps({"case": name, "exit_code": code, "state": "complete"})
    for stream, text, limit in (("stdout", stdout, 8000), ("stderr", stderr, 2000)):
        candidate += f"\n{stream}: " + (
            text
            if len(text) <= limit
            else text[: limit // 4] + "\n[omitted]\n" + text[-3 * limit // 4 :]
        )
    candidate += "\nretained: <conversation-owned-reference>; representation=text"
    start = time.perf_counter()
    hits = sum(
        1
        for path in files
        for line in path.read_text().splitlines()
        if "error:" in line
    )
    if name == "api_json":
        selected_correct = any(
            json.loads(text)[4321] == {"id": 4321, "state": "ok", "value": 4321 % 7}
            for text in contents
        )
    elif name == "structured_data":
        selected_correct = any("12345,ok,4" in text.splitlines() for text in contents)
    elif name == "pytest_log":
        selected_correct = any(
            text.endswith("20000 passed in 4.00s\n") for text in contents
        )
    elif name == "compiler_failure":
        selected_correct = hits == 1
    elif name == "noisy_single_line":
        selected_correct = any(
            text.startswith("BEGIN-MARKER") and text.endswith("END-MARKER")
            for text in contents
        )
    else:
        selected_correct = "12 passed" in preview
    retrieval_ms = (time.perf_counter() - start) * 1000
    return {
        "case": name,
        "captured_bytes": len(stdout.encode()) + len(stderr.encode()),
        "inline_bytes": len(preview.encode()),
        "inline_approx_tokens": (len(preview) + 3) // 4,
        "captured_approx_tokens": (len(stdout) + len(stderr) + 3) // 4,
        "candidate_inline_bytes": len(candidate.encode()),
        "candidate_failure_visible": code == 0
        or (f'"exit_code": {code}' in candidate and "error:" in candidate),
        "retained_bytes": sum(p.stat().st_size for p in files),
        "saved_files": len(files),
        "represented_complete": complete,
        "exit_code": code,
        "failure_visible": code == 0
        or (f"Return code: {code}" in preview and "error:" in preview),
        "format_ms": round(elapsed * 1000, 2),
        "format_peak_python_bytes": peak,
        "selective_error_hits": hits,
        "selective_answer_correct": selected_correct,
        "reread_ms": round(retrieval_ms, 2),
        "extra_retrieval_calls": int(bool(files)),
    }


def job_probe(root: Path) -> dict:
    """Exercise direct-buffer eviction versus promoted final persistence."""
    content = "BEGIN-MARKER\n" + "noisy output\n" * 180000 + "END-MARKER\n"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('BEGIN-MARKER\\n' + 'noisy output\\n' * 180000 + 'END-MARKER\\n'); sys.exit(2)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    direct = BackgroundJob(
        1, "synthetic-noisy-job", process, 1, log_dir=root / "direct"
    )
    before = direct.log_path
    started = time.perf_counter()
    tracemalloc.start()
    try:
        direct.start_reader()
        assert direct._reader_thread is not None
        direct._reader_thread.join(timeout=10)
        assert not direct._reader_thread.is_alive()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        assert process.wait(timeout=2) == 2
    finally:
        tracemalloc.stop()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        if direct._reader_thread is not None:
            direct._reader_thread.join(timeout=2)
        assert process.stdout is not None and process.stderr is not None
        process.stdout.close()
        process.stderr.close()
    preview = format_job_output(direct, *direct.get_output(), complete=True)
    assert direct.log_path is not None
    direct_log = direct.log_path.read_text()
    promoted = BackgroundJob(
        2, "synthetic-promoted-job", None, 2, log_dir=root / "promoted"
    )
    # complete_background_job persists before appending to its bounded buffer;
    # call the same writer here without publishing a global completion event.
    _write_job_log(promoted, content, "", final=True)
    assert promoted.log_path is not None
    original_final = promoted.log_path.read_bytes()
    _write_job_log(promoted, "later snapshot", "", final=False)
    return {
        "produced_chars": len(content),
        "process_exit_code": process.returncode,
        "capture_ms": round(elapsed * 1000, 2),
        "capture_peak_python_bytes": peak,
        "direct_retained_buffer_chars": len(direct.get_output()[0]),
        "direct_evicted_chars": direct._stdout_buffer_start,
        "live_path_before_first_read": str(before),
        "direct_log_has_begin": "BEGIN-MARKER" in direct_log,
        "direct_preview_reports_loss": "not retained" in preview,
        "promoted_final_has_begin_and_end": all(
            marker.encode() in original_final
            for marker in ("BEGIN-MARKER", "END-MARKER")
        ),
        "final_survives_later_snapshot": promoted.log_path.read_bytes()
        == original_final,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    # Warm import/config paths outside the measured region. These are single
    # samples with tracemalloc enabled, not throughput or latency benchmarks.
    _format_shell_output("synthetic-warmup", "ok\n", "", 0, False, False)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True
    ).strip()
    with TemporaryDirectory(prefix="gptme-output-evidence-") as temporary:
        root = Path(temporary)
        rows = [measure(root, name, *data) for name, data in fixtures().items()]
        jobs = job_probe(root)
    assert all(
        r["represented_complete"]
        and r["failure_visible"]
        and r["candidate_failure_visible"]
        and r["selective_answer_correct"]
        for r in rows
    )
    assert not jobs["direct_log_has_begin"]
    assert jobs["direct_preview_reports_loss"]
    assert jobs["promoted_final_has_begin_and_end"]
    assert jobs["final_survives_later_snapshot"]
    print(json.dumps({"revision": revision, "workflows": rows, "jobs": jobs}, indent=2))


if __name__ == "__main__":
    main()
