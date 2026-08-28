"""gptme session daemon — Phase 1 MVP.

Decouples session lifecycle from terminal attachment using:
- Unix domain socket for I/O forwarding
- Simple input/output/signal message protocol
- Reused session persistence (existing JSONL conversation log)

Design decisions (resolved 2026-08-27):
- Auto-start on attach: yes (Option A — better UX, matches tmux)
- Socket location: ~/.config/gptme/daemon/<name>.socket (survives /tmp cleanup)
- Multi-client: single-client for MVP (Phase 3 for observe-only fan-out)

Each submitted prompt runs as a non-interactive turn against the same named
conversation.  The daemon, rather than an individual gptme subprocess, owns the
persistent lifecycle.  This lets an idle daemon accept a first prompt and lets
later attachments continue the conversation after earlier turns have exited.
"""

from __future__ import annotations

import codecs
import fcntl
import logging
import os
import queue
import select
import signal
import socket
import subprocess
import sys
import threading
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import IO, Protocol

    class _ReadableFD(Protocol):
        def fileno(self) -> int: ...


from ..util.conversation_ids import validate_conversation_id
from .ipc_protocol import IPCMessage, recv_msg, send_msg

logger = logging.getLogger(__name__)

# Output ring-buffer: send this many bytes of history to a newly attached client
_OUTPUT_HISTORY_BYTES = 64 * 1024
# Short polling timeout keeps disconnect and shutdown detection responsive.
_CLIENT_READ_TIMEOUT = 0.2
# Writes are bounded independently: large terminal output may legitimately take
# longer than one read-poll interval, but a non-reading client cannot block a turn.
_CLIENT_WRITE_TIMEOUT = 5.0
# A queued second attach must fail instead of waiting forever for the only client slot.
_ATTACH_HANDSHAKE_TIMEOUT = 2.0


def get_daemon_dir() -> Path:
    from ..dirs import get_config_dir

    d = get_config_dir() / "daemon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_socket_path(session_name: str) -> Path:
    return get_daemon_dir() / f"{validate_conversation_id(session_name)}.socket"


def get_pid_path(session_name: str) -> Path:
    return get_daemon_dir() / f"{validate_conversation_id(session_name)}.pid"


# ---------------------------------------------------------------------------
# Daemon process
# ---------------------------------------------------------------------------


class SessionDaemon:
    """Background daemon that owns one gptme session and accepts socket clients."""

    def __init__(self, session_name: str) -> None:
        self.session_name = validate_conversation_id(session_name)
        self.socket_path = get_socket_path(session_name)
        self.pid_path = get_pid_path(session_name)
        self._output_buf: deque[str] = deque()
        self._output_buf_size = 0
        # Serializes history replay, client publication, and all framed writes.
        # A single lock prevents live output from interleaving with history frames.
        self._output_lock = threading.Lock()
        self._client: socket.socket | None = None
        self._turn_queue: queue.Queue[list[str]] = queue.Queue()
        self._stop_event = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._pid_file: IO[str] | None = None
        self._owns_paths = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        gptme_args: list[str],
        prompts: list[str] | None = None,
        daemonize: bool = True,
    ) -> None:
        """Daemonize and run; return immediately in the original parent."""
        if daemonize and _daemonize():
            return

        try:
            self._run(gptme_args, prompts or [])
        except Exception:
            logger.exception("Daemon crashed")
        finally:
            self._cleanup()

    def is_running(self) -> bool:
        if not self.socket_path.exists():
            return False
        try:
            pid = int(self.pid_path.read_text())
            os.kill(pid, 0)
            pid_file = self.pid_path.open()
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            return False
        try:
            fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(pid_file, fcntl.LOCK_UN)
            return False
        finally:
            pid_file.close()

    def stop(self) -> None:
        """Send SIGTERM only to the process that owns the daemon PID file."""
        if not self.is_running():
            return
        try:
            pid = int(self.pid_path.read_text())
            os.kill(pid, signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            pass

    # ------------------------------------------------------------------
    # Internal — runs in the daemon process
    # ------------------------------------------------------------------

    def _acquire_pid_file(self) -> None:
        pid_file = self.pid_path.open("a+")
        try:
            fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pid_file.close()
            raise
        pid_file.seek(0)
        pid_file.truncate()
        pid_file.write(str(os.getpid()))
        pid_file.flush()
        self._pid_file = pid_file
        self._owns_paths = True

    def _run(self, gptme_args: list[str], prompts: list[str]) -> None:
        self._acquire_pid_file()

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.socket_path.exists():
            self.socket_path.unlink()
        server_sock.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        server_sock.listen(1)
        server_sock.setblocking(False)

        def request_stop(_signum: int, _frame: object) -> None:
            self._stop_event.set()

        old_sigterm = signal.signal(signal.SIGTERM, request_stop)
        old_sigint = signal.signal(signal.SIGINT, request_stop)
        worker = threading.Thread(
            target=self._worker_loop,
            args=(gptme_args,),
            name=f"gptme-daemon-{self.session_name}",
            daemon=True,
        )
        worker.start()
        if prompts:
            self._turn_queue.put(prompts)

        try:
            self._accept_loop(server_sock)
        finally:
            self._stop_event.set()
            self._terminate_process()
            worker.join(timeout=5)
            server_sock.close()
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)

    def _terminate_process(self) -> None:
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _worker_loop(self, gptme_args: list[str]) -> None:
        """Run queued prompts as sequential turns in the named conversation."""
        while not self._stop_event.is_set():
            try:
                prompts = self._turn_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._run_turn(gptme_args, prompts)
            except Exception as exc:
                logger.exception("Daemon turn failed")
                self._publish_output(b"", f"\n[daemon error] Turn failed: {exc}\n")
            finally:
                self._turn_queue.task_done()

    def _run_turn(self, gptme_args: list[str], prompts: list[str]) -> None:
        # stdin must be DEVNULL.  A PIPE is non-TTY input, so gptme would wait for
        # EOF while trying to consume piped input before processing CLI prompts.
        proc = subprocess.Popen(
            [sys.executable, "-m", "gptme", "--non-interactive"]
            + gptme_args
            + ["--"]
            + prompts,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        with self._proc_lock:
            self._proc = proc
        try:
            assert proc.stdout is not None
            self._pump_output(proc.stdout)
            proc.wait()
        finally:
            with self._proc_lock:
                if self._proc is proc:
                    self._proc = None

    def _pump_output(self, stdout: _ReadableFD) -> None:
        """Read available subprocess output without waiting for a 4 KiB buffer."""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                chunk = os.read(stdout.fileno(), 4096)
            except OSError:
                break
            if not chunk:
                break
            self._publish_output(chunk, decoder.decode(chunk))
        if tail := decoder.decode(b"", final=True):
            self._publish_output(b"", tail)

    def _publish_output(self, chunk: bytes, text: str | None = None) -> None:
        if text is None:
            text = chunk.decode("utf-8", errors="replace")
        failed_client: socket.socket | None = None
        with self._output_lock:
            if text:
                self._output_buf.append(text)
                self._output_buf_size += len(text.encode())
                while self._output_buf_size > _OUTPUT_HISTORY_BYTES:
                    dropped = self._output_buf.popleft()
                    self._output_buf_size -= len(dropped.encode())

            if self._client is not None and text:
                if not self._write_client(
                    self._client, IPCMessage(type="output", data=text)
                ):
                    failed_client = self._client
                    self._client = None

        if failed_client is not None:
            self._disconnect_client(failed_client)

    def _accept_loop(self, server_sock: socket.socket) -> None:
        """Accept one client at a time while the daemon remains alive."""
        while not self._stop_event.is_set():
            try:
                rlist, _, _ = select.select([server_sock], [], [], 0.2)
            except InterruptedError:
                continue
            except OSError:
                break
            if not rlist:
                continue

            try:
                client, _ = server_sock.accept()
            except OSError:
                if self._stop_event.is_set():
                    break
                logger.warning("Daemon accept failed", exc_info=True)
                continue
            client.setblocking(True)
            try:
                self._serve_client(client)
            finally:
                with self._output_lock:
                    if self._client is client:
                        self._client = None
                client.close()

    def _serve_client(self, client: socket.socket) -> None:
        """Replay output history, then queue prompts received from the client."""
        client.settimeout(_CLIENT_READ_TIMEOUT)
        # Publish the client only after history is fully sent.  Holding the same
        # lock used by _publish_output makes history-before-live ordering atomic.
        with self._output_lock:
            history = "".join(self._output_buf)
            if not self._write_client(
                client, IPCMessage(type="status", data={"ready": True})
            ):
                return
            if history and not self._write_client(
                client, IPCMessage(type="output", data=history)
            ):
                return
            self._client = client

        recv_buffer = bytearray()
        while not self._stop_event.is_set():
            try:
                msg = recv_msg(client, recv_buffer)
            except TimeoutError:
                continue
            except (OSError, ValueError):
                break
            if msg is None:
                break
            if msg.type == "input":
                data = msg.data if isinstance(msg.data, str) else str(msg.data)
                if data.strip():
                    self._turn_queue.put([data.strip()])
            elif msg.type == "status":
                with self._proc_lock:
                    proc = self._proc
                self._send_to_client(
                    client,
                    IPCMessage(
                        type="status",
                        data={
                            "session": self.session_name,
                            "pid": os.getpid(),
                            "running": True,
                            "busy": proc is not None and proc.poll() is None,
                            "queued": self._turn_queue.qsize(),
                        },
                    ),
                )
            elif (
                msg.type == "signal"
                and isinstance(msg.data, dict)
                and msg.data.get("signal") == "SIGTERM"
            ):
                self._stop_event.set()
                self._terminate_process()
                break

    def _send_to_client(self, client: socket.socket, msg: IPCMessage) -> None:
        failed = False
        with self._output_lock:
            if self._client is client and not self._write_client(client, msg):
                self._client = None
                failed = True
        if failed:
            self._disconnect_client(client)

    @staticmethod
    def _write_client(client: socket.socket, msg: IPCMessage) -> bool:
        """Send one frame with a write-specific timeout."""
        client.settimeout(_CLIENT_WRITE_TIMEOUT)
        try:
            send_msg(client, msg)
        except OSError:
            return False
        finally:
            client.settimeout(_CLIENT_READ_TIMEOUT)
        return True

    @staticmethod
    def _disconnect_client(client: socket.socket) -> None:
        """Wake the synchronous client loop after a write failure."""
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _cleanup(self) -> None:
        # A child that lost PID-file lock acquisition must not remove paths owned
        # by the competing daemon that won the same-session startup race.
        if not self._owns_paths:
            return
        self.socket_path.unlink(missing_ok=True)
        if self._pid_file is not None:
            self._pid_file.close()
            self._pid_file = None
            self.pid_path.unlink(missing_ok=True)
        self._owns_paths = False


# ---------------------------------------------------------------------------
# Daemonization (double-fork on POSIX)
# ---------------------------------------------------------------------------


def _fork() -> int:
    try:
        return os.fork()
    except OSError as exc:
        raise RuntimeError("failed to fork daemon process") from exc


def _daemonize() -> bool:
    """Double-fork; return True only in the original parent process."""
    if _fork() > 0:
        return True

    os.setsid()

    if _fork() > 0:
        os._exit(0)

    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.close(devnull)
    return False


# ---------------------------------------------------------------------------
# Attach client
# ---------------------------------------------------------------------------


def attach(session_name: str) -> None:
    """Connect to a running daemon and relay stdin/stdout."""
    sock_path = get_socket_path(session_name)
    if not sock_path.exists():
        raise FileNotFoundError(f"No daemon socket for session '{session_name}'")

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(_ATTACH_HANDSHAKE_TIMEOUT)
    conn.connect(str(sock_path))
    try:
        ready = recv_msg(conn)
    except TimeoutError as exc:
        conn.close()
        raise TimeoutError(
            f"Daemon '{session_name}' already has an attached client"
        ) from exc
    if (
        ready is None
        or ready.type != "status"
        or not isinstance(ready.data, dict)
        or not ready.data.get("ready")
    ):
        conn.close()
        raise ConnectionResetError(f"Daemon '{session_name}' closed during attach")
    conn.settimeout(None)

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
    """Return info dicts for known daemons."""
    daemon_dir = get_daemon_dir()
    result = []
    for pid_file in daemon_dir.glob("*.pid"):
        name = pid_file.stem
        sock = daemon_dir / f"{name}.socket"
        daemon = SessionDaemon(name)
        running = daemon.is_running()
        try:
            pid = int(pid_file.read_text())
        except ValueError:
            pid = 0
        result.append(
            {
                "session": name,
                "pid": pid if running else None,
                "running": running,
                "socket": str(sock) if sock.exists() else None,
            }
        )
    return result
