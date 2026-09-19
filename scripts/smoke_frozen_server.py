#!/usr/bin/env python3
"""Smoke-test a frozen (PyInstaller) gptme-server: start it and check its log.

``gptme-server --help`` only exercises argument parsing. Hooks and plugin
entrypoints are imported by string at startup, so PyInstaller's static analysis
can miss them and the binary still passes ``--help`` (v0.34.0 shipped with 18
missing modules, see gptme/gptme#3883). This starts the server on a free
loopback port, waits for the API to answer, and fails if the startup log shows
a missing-module error.

Usage: smoke_frozen_server.py dist/gptme-server [--timeout 60]
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Missing-code errors in the startup log. The log is wrapped by a rich table,
# so match against whitespace-collapsed text rather than line by line.
MISSING_MODULE = re.compile(r"No module named '([\w.]+)'")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api_answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v2", timeout=2)
    except urllib.error.HTTPError:
        return True  # any HTTP response means the server is up
    except OSError:
        return False
    return True


def run_server(binary: Path, timeout: float) -> tuple[bool, str]:
    """Start the server, return (api_came_up, combined output)."""
    port = free_port()
    with (
        tempfile.TemporaryDirectory() as home,
        tempfile.NamedTemporaryFile("w+", suffix=".log") as log,
    ):
        # Scrubbed env: a clean HOME and no provider keys, like a fresh install.
        env = {
            "HOME": home,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
        }
        proc = subprocess.Popen(
            [
                str(binary.resolve()),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=home,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        served = False
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and proc.poll() is None:
                if api_answers(port):
                    served = True
                    time.sleep(2)  # let deferred startup registration log
                    break
                time.sleep(0.5)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log.seek(0)
        return served, log.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("binary", type=Path)
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args()

    served, output = run_server(args.binary, args.timeout)
    text = " ".join(output.split())
    if "No module named" in text:
        # Names can be split by table wrapping, so the count is the reliable part.
        names = sorted(set(MISSING_MODULE.findall(text)))
        print(
            f"FAIL: {text.count('No module named')} missing-module error(s) "
            "in the startup log:"
        )
        for name in names:
            print(f"  {name}")
        return 1
    if not served:
        print(f"FAIL: API did not answer within {args.timeout:.0f}s. Log tail:")
        print(output[-2000:])
        return 1
    print("OK: server started, no missing-module errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
