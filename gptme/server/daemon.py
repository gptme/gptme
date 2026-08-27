"""gptme session daemon — Phase 1 MVP.

Decouples session lifecycle from terminal attachment using:
- Unix domain socket for I/O forwarding
- Simple input/output/signal message protocol
- Reused session persistence (existing JSONL conversation log)

Design decisions (resolved 2026-08-27):
- Auto-start on attach: yes (Option A — better UX, matches tmux)
- Socket location: ~/.config/gptme/daemon/<name>.socket (survives /tmp cleanup)
- Multi-client: single-client for MVP (Phase 3 for observe-only fan-out)
"""

from __future__ import annotations

import logging
import os
import select
import socket
import subprocess
import sys
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import IO

from .ipc_protocol import IPCMessage, recv_msg, send_msg

logger = logging.getLogger(__name__)

# Output ring-buffer: send this many bytes of history to a newly attached client
_OUTPUT_HISTORY_BYTES = 64 * 1024


def get_daemon_dir() -> Path:
    from ..dirs import get_config_dir

    d = get_config_dir() / "daemon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_socket_path(session_name: str) -> Path:
    return get_daemon_dir() / f"{session_name}.socket"


def get_pid_path(session_name: str) -> Path:
    return get_daemon_dir() / f"{session_name}.pid"


# ---------------------------------------------------------------------------
# Daemon process
# ---------------------------------------------------------------------------


class SessionDaemon:
    """Background daemon that owns one gptme session and accepts socket clients."""

    def __init__(self, session_name: str) -> None:
        self.session_name = session_name
        self.socket_path = get_socket_path(session_name)
        self.pid_path = get_pid_path(session_name)
        self._output_buf: deque[bytes] = deque()
        self._output_buf_size = 0
        self._buf_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client: socket.socket | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, gptme_args: list[str], daemonize: bool = True) -> None:
        """Daemonize and run; returns immediately in the parent."""
        if daemonize:
            _daemonize()
        # Now we are the daemon process
        try:
            self._run(gptme_args)
        except Exception:
            logger.exception("Daemon crashed")
        finally:
            self._cleanup()

    def is_running(self) -> bool:
        if not self.socket_path.exists():
            return False
        try:
            pid = int(self.pid_path.read_text())
            os.kill(pid, 0)  # check existence
            return True
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            return False

    def stop(self) -> None:
        """Send SIGTERM to the daemon process."""
        try:
            pid = int(self.pid_path.read_text())
            os.kill(pid, 15)  # SIGTERM
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Internal — runs in the daemon process
    # ------------------------------------------------------------------

    def _run(self, gptme_args: list[str]) -> None:
        self.pid_path.write_text(str(os.getpid()))

        # Set up Unix socket
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.socket_path.exists():
            self.socket_path.unlink()
        server_sock.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        server_sock.listen(1)
        server_sock.setblocking(False)

        # Start gptme subprocess (non-interactive, stdin/stdout piped)
        proc = subprocess.Popen(
            [sys.executable, "-m", "gptme", "--non-interactive"] + gptme_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        assert proc.stdout is not None
        assert proc.stdin is not None

        # Thread: read subprocess output → buffer + forward to client
        output_thread = threading.Thread(
            target=self._pump_output,
            args=(proc.stdout,),
            daemon=True,
        )
        output_thread.start()

        # Main loop: accept connections and forward client input to subprocess
        try:
            self._accept_loop(server_sock, proc)
        finally:
            proc.terminate()
            proc.wait()

    def _pump_output(self, stdout: IO[bytes]) -> None:
        """Read subprocess stdout, buffer it, and forward to connected client."""
        for chunk in iter(lambda: stdout.read(4096), b""):
            with self._buf_lock:
                self._output_buf.append(chunk)
                self._output_buf_size += len(chunk)
                # Trim ring buffer
                while self._output_buf_size > _OUTPUT_HISTORY_BYTES:
                    dropped = self._output_buf.popleft()
                    self._output_buf_size -= len(dropped)

            # Forward to client if connected
            with self._client_lock:
                client = self._client
            if client is not None:
                try:
                    send_msg(
                        client,
                        IPCMessage(
                            type="output", data=chunk.decode("utf-8", errors="replace")
                        ),
                    )
                except OSError:
                    with self._client_lock:
                        self._client = None

    def _accept_loop(self, server_sock: socket.socket, proc: subprocess.Popen) -> None:
        """Accept one client at a time and forward their input to the subprocess."""
        while proc.poll() is None:
            try:
                rlist, _, _ = select.select([server_sock], [], [], 1.0)
            except OSError:
                break
            if not rlist:
                continue

            client, _ = server_sock.accept()
            client.setblocking(True)
            with self._client_lock:
                self._client = client

            try:
                self._serve_client(client, proc)
            finally:
                with self._client_lock:
                    self._client = None
                client.close()

    def _serve_client(self, client: socket.socket, proc: subprocess.Popen) -> None:
        """Send output history then relay client input → subprocess stdin."""
        # Send buffered output history to the new client
        with self._buf_lock:
            history = b"".join(self._output_buf)
        if history:
            try:
                send_msg(
                    client,
                    IPCMessage(
                        type="output", data=history.decode("utf-8", errors="replace")
                    ),
                )
            except OSError:
                return

        # Relay client input to subprocess stdin
        while proc.poll() is None:
            msg = recv_msg(client)
            if msg is None:
                break  # client disconnected
            if msg.type == "input":
                data = msg.data if isinstance(msg.data, str) else str(msg.data)
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(data.encode())
                    proc.stdin.flush()
                except OSError:
                    break
            elif msg.type == "status":
                try:
                    send_msg(
                        client,
                        IPCMessage(
                            type="status",
                            data={
                                "session": self.session_name,
                                "pid": os.getpid(),
                                "running": proc.poll() is None,
                            },
                        ),
                    )
                except OSError:
                    break
            elif (
                msg.type == "signal"
                and isinstance(msg.data, dict)
                and msg.data.get("signal") == "SIGTERM"
            ):
                proc.terminate()
                break

    def _cleanup(self) -> None:
        for path in (self.socket_path, self.pid_path):
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Daemonization (double-fork on POSIX)
# ---------------------------------------------------------------------------


def _daemonize() -> None:
    """Double-fork to create a proper daemon process."""
    if os.fork() > 0:
        # Parent exits — child continues
        os._exit(0)

    os.setsid()  # new session leader

    if os.fork() > 0:
        # Second parent exits — grandchild is fully detached
        os._exit(0)

    # Redirect stdin/stdout/stderr to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.close(devnull)


# ---------------------------------------------------------------------------
# Attach client
# ---------------------------------------------------------------------------


def attach(session_name: str) -> None:
    """Connect to a running daemon and relay stdin/stdout."""
    sock_path = get_socket_path(session_name)
    if not sock_path.exists():
        raise FileNotFoundError(f"No daemon socket for session '{session_name}'")

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(sock_path))

    # Thread: receive daemon output → print to stdout
    stop_event = threading.Event()

    def _receive() -> None:
        while not stop_event.is_set():
            try:
                msg = recv_msg(conn)
            except OSError:
                break
            if msg is None:
                break
            if msg.type in ("output", "error"):
                data = msg.data if isinstance(msg.data, str) else str(msg.data)
                sys.stdout.write(data)
                sys.stdout.flush()
            elif msg.type == "status":
                print(f"[daemon status] {msg.data}", flush=True)
        stop_event.set()

    recv_thread = threading.Thread(target=_receive, daemon=True)
    recv_thread.start()

    # Main thread: read stdin → send to daemon
    seq = 0
    try:
        for line in sys.stdin:
            if stop_event.is_set():
                break
            seq += 1
            send_msg(conn, IPCMessage(type="input", data=line, seq=seq))
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop_event.set()
        conn.close()


# ---------------------------------------------------------------------------
# List running daemons
# ---------------------------------------------------------------------------


def list_daemons() -> list[dict]:
    """Return info dicts for running daemons."""
    daemon_dir = get_daemon_dir()
    result = []
    for pid_file in daemon_dir.glob("*.pid"):
        name = pid_file.stem
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)
            running = True
        except (ValueError, ProcessLookupError, PermissionError):
            running = False
        sock = daemon_dir / f"{name}.socket"
        result.append(
            {
                "session": name,
                "pid": pid if running else None,
                "running": running,
                "socket": str(sock) if sock.exists() else None,
            }
        )
    return result
