"""Tests for gptme session daemon — Phase 1 MVP.

Tests cover:
- IPC protocol encode/decode roundtrip
- Daemon socket path helpers
- start → attach → detach → re-attach session state preservation
- Daemon survives client SIGHUP (SSH-drop case)
"""

from __future__ import annotations

import fcntl
import io
import os
import signal
import socket
import subprocess
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from gptme.server.daemon import (
    SessionDaemon,
    attach,
    get_pid_path,
    get_socket_path,
    list_daemons,
)
from gptme.server.ipc_protocol import MAX_IPC_PAYLOAD, IPCMessage, recv_msg, send_msg

# ---------------------------------------------------------------------------
# IPC protocol tests
# ---------------------------------------------------------------------------


class TestIPCProtocol:
    def test_roundtrip_input(self):
        """Input message survives encode → decode."""
        msg = IPCMessage(type="input", data="hello world\n", seq=1)
        raw = msg.encode()
        decoded = IPCMessage.from_bytes(raw[4:])  # skip length header
        assert decoded.type == "input"
        assert decoded.data == "hello world\n"
        assert decoded.seq == 1

    def test_roundtrip_output(self):
        """Output message with unicode survives encode → decode."""
        msg = IPCMessage(type="output", data="→ done ✓", seq=42)
        raw = msg.encode()
        decoded = IPCMessage.from_bytes(raw[4:])
        assert decoded.type == "output"
        assert decoded.data == "→ done ✓"

    def test_roundtrip_status(self):
        """Status message with dict data survives encode → decode."""
        msg = IPCMessage(type="status", data={"session": "test", "running": True})
        raw = msg.encode()
        decoded = IPCMessage.from_bytes(raw[4:])
        assert decoded.type == "status"
        assert isinstance(decoded.data, dict)
        assert decoded.data["session"] == "test"
        assert decoded.data["running"] is True

    def test_send_recv_over_socket_pair(self):
        """send_msg / recv_msg work over a real socket pair."""
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sent = IPCMessage(type="input", data="ping", seq=1)
            send_msg(a, sent)
            received = recv_msg(b)
            assert received is not None
            assert received.type == "input"
            assert received.data == "ping"
        finally:
            a.close()
            b.close()

    def test_buffer_preserves_partial_frame_across_timeout(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        b.settimeout(0.01)
        buffer = bytearray()
        encoded = IPCMessage(type="input", data="split frame").encode()
        try:
            a.sendall(encoded[:6])
            with pytest.raises(TimeoutError):
                recv_msg(b, buffer)
            assert buffer == encoded[:6]

            a.sendall(encoded[6:])
            received = recv_msg(b, buffer)
            assert received is not None
            assert received.data == "split frame"
            assert buffer == bytearray()
        finally:
            a.close()
            b.close()

    @pytest.mark.parametrize("split_at", [2, 6])
    def test_buffer_rejects_eof_mid_frame(self, split_at):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        encoded = IPCMessage(type="input", data="partial").encode()
        try:
            a.sendall(encoded[:split_at])
            a.close()
            with pytest.raises(ValueError, match="incomplete IPC message"):
                recv_msg(b, bytearray())
        finally:
            a.close()
            b.close()

    def test_recv_returns_none_on_eof(self):
        """recv_msg returns None when the other end closes."""
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        b.close()
        result = recv_msg(a)
        assert result is None
        a.close()

    def test_multiple_messages_in_sequence(self):
        """Multiple framed messages are received in order."""
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            for i in range(5):
                send_msg(a, IPCMessage(type="input", data=f"msg{i}", seq=i))
            for i in range(5):
                msg = recv_msg(b)
                assert msg is not None
                assert msg.data == f"msg{i}"
                assert msg.seq == i
        finally:
            a.close()
            b.close()

    @pytest.mark.parametrize(
        "payload",
        [b"not json", b"[]", b"{}", b'{"type":"bogus"}'],
    )
    def test_rejects_malformed_messages(self, payload):
        with pytest.raises(ValueError, match="IPC message|message JSON"):
            IPCMessage.from_bytes(payload)

    @pytest.mark.parametrize("use_buffer", [False, True])
    def test_recv_rejects_oversize_payload(self, use_buffer):
        import struct

        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            a.sendall(struct.pack("!I", MAX_IPC_PAYLOAD + 1))
            with pytest.raises(ValueError, match="too large"):
                recv_msg(b, bytearray() if use_buffer else None)
        finally:
            a.close()
            b.close()


# ---------------------------------------------------------------------------
# Daemon path helpers
# ---------------------------------------------------------------------------


class TestDaemonPaths:
    def test_socket_path_is_under_config_dir(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            sp = get_socket_path("mysession")
            assert sp == tmp_path / "mysession.socket"
        finally:
            _dm.get_daemon_dir = orig

    def test_pid_path_matches_session(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            pp = get_pid_path("mysession")
            assert pp == tmp_path / "mysession.pid"
        finally:
            _dm.get_daemon_dir = orig

    @pytest.mark.parametrize("name", ["../../target", "foo/bar", "..", "."])
    def test_paths_reject_unsafe_session_names(self, tmp_path, name):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            with pytest.raises(ValueError, match="single path component"):
                get_socket_path(name)
            with pytest.raises(ValueError, match="single path component"):
                get_pid_path(name)
        finally:
            _dm.get_daemon_dir = orig


# ---------------------------------------------------------------------------
# SessionDaemon unit tests (no real subprocess)
# ---------------------------------------------------------------------------


class TestSessionDaemonState:
    def test_is_running_false_when_no_pid_file(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            d = SessionDaemon("noexist")
            assert not d.is_running()
        finally:
            _dm.get_daemon_dir = orig

    def test_is_running_false_when_pid_file_stale(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            d = SessionDaemon("stale")
            # Write a PID that does not exist
            (tmp_path / "stale.pid").write_text("999999999")
            assert not d.is_running()
        finally:
            _dm.get_daemon_dir = orig

    def test_is_running_false_when_stale_pid_was_reused(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            d = SessionDaemon("reused")
            d.pid_path.write_text(str(os.getpid()))
            d.socket_path.touch()

            # A live PID and stale socket do not prove that process owns the daemon.
            assert not d.is_running()
        finally:
            _dm.get_daemon_dir = orig

    def test_stop_does_not_signal_reused_pid(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        d = SessionDaemon("reused")
        d.pid_path.write_text(str(os.getpid()))
        d.socket_path.touch()
        signals: list[int] = []

        monkeypatch.setattr(os, "kill", lambda pid, signum: signals.append(signum))
        d.stop()

        assert signals == []

    def test_stop_holds_ownership_proof_while_signaling(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("owned-stop")
        owner._acquire_pid_file()
        owner.socket_path.touch()
        observed: list[tuple[int, int]] = []
        real_close = os.close

        def record_send(pidfd: int, signum: int) -> None:
            probe = owner.pid_path.open()
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observed.append((pidfd, signum))
            finally:
                probe.close()

        monkeypatch.setattr(_dm, "_pidfd_open", lambda _pid: 99)
        monkeypatch.setattr(_dm, "_pidfd_send_signal", record_send)
        monkeypatch.setattr(
            os, "close", lambda fd: None if fd == 99 else real_close(fd)
        )
        monkeypatch.setattr(os, "kill", lambda *_args: pytest.fail("unpinned os.kill"))
        try:
            SessionDaemon("owned-stop").stop()
            assert observed == [(99, signal.SIGTERM)]
        finally:
            owner._cleanup()

    def test_stop_without_pidfd_uses_socket_not_kill(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        monkeypatch.setattr(_dm, "_pidfd_open", lambda _pid: None)
        monkeypatch.setattr(os, "kill", lambda *_args: pytest.fail("unpinned os.kill"))
        owner = SessionDaemon("socket-stop")
        owner._acquire_pid_file()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if owner.socket_path.exists():
            owner.socket_path.unlink()
        sock.bind(str(owner.socket_path))
        sock.listen(1)
        received: list[str] = []

        def accept_stop() -> None:
            conn, _ = sock.accept()
            try:
                msg = recv_msg(conn)
                if msg is not None:
                    received.append(msg.type)
            finally:
                conn.close()

        thread = threading.Thread(target=accept_stop)
        thread.start()
        try:
            SessionDaemon("socket-stop").stop()
            thread.join(timeout=2)
            assert received == ["signal"]
        finally:
            sock.close()
            owner._cleanup()

    def test_stop_uses_pidfd_when_available(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("pidfd-stop")
        owner._acquire_pid_file()
        owner.socket_path.touch()
        sent: list[tuple[int, int]] = []
        real_close = os.close
        monkeypatch.setattr(_dm, "_pidfd_open", lambda _pid: 99)
        monkeypatch.setattr(
            _dm,
            "_pidfd_send_signal",
            lambda pidfd, signum: sent.append((pidfd, signum)),
        )
        monkeypatch.setattr(
            os, "close", lambda fd: None if fd == 99 else real_close(fd)
        )
        monkeypatch.setattr(
            os, "kill", lambda pid, signum: pytest.fail("PID kill raced identity")
        )
        try:
            SessionDaemon("pidfd-stop").stop()
            assert sent == [(99, signal.SIGTERM)]
        finally:
            owner._cleanup()

    def test_stop_aborts_if_owner_exits_before_signal(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("exit-before-signal")
        owner._acquire_pid_file()
        owner.socket_path.touch()
        sent: list[int] = []

        def fake_pidfd_open(_pid: int) -> int:
            # Simulate the owner exiting after the PID is read: the lock is
            # released and the PID could be reused before the signal.
            assert owner._pid_file is not None
            owner._pid_file.close()
            owner._pid_file = None
            owner._owns_paths = False
            return 99

        real_close = os.close
        monkeypatch.setattr(_dm, "_pidfd_open", fake_pidfd_open)
        monkeypatch.setattr(
            _dm, "_pidfd_send_signal", lambda _pidfd, signum: sent.append(signum)
        )
        monkeypatch.setattr(os, "kill", lambda _pid, signum: sent.append(signum))
        monkeypatch.setattr(
            os, "close", lambda fd: None if fd == 99 else real_close(fd)
        )
        try:
            SessionDaemon("exit-before-signal").stop()
            assert sent == []
        finally:
            if owner._pid_file is not None:
                owner._cleanup()
            owner.socket_path.unlink(missing_ok=True)
            owner.pid_path.unlink(missing_ok=True)

    def test_is_running_true_with_owned_pid_file(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            d = SessionDaemon("live")
            d._acquire_pid_file()
            d.socket_path.touch()
            assert d.is_running()
        finally:
            d._cleanup()
            _dm.get_daemon_dir = orig

    def test_cleanup_unlinks_pid_before_releasing_lock(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        daemon = SessionDaemon("cleanup-order")
        daemon._acquire_pid_file()
        daemon.socket_path.touch()
        assert daemon._pid_file is not None
        original_close = daemon._pid_file.close

        def assert_unlinked_before_close():
            assert not daemon.pid_path.exists()
            original_close()

        monkeypatch.setattr(daemon._pid_file, "close", assert_unlinked_before_close)
        daemon._cleanup()

    def test_cleanup_does_not_unlink_successor_pid(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("handoff")
        owner._acquire_pid_file()
        owner.socket_path.touch()
        assert owner._pid_file is not None
        original_close = owner._pid_file.close
        successor = SessionDaemon("handoff")

        def close_after_successor_starts() -> None:
            successor._acquire_pid_file()
            successor.socket_path.touch()
            original_close()

        monkeypatch.setattr(owner._pid_file, "close", close_after_successor_starts)
        try:
            owner._cleanup()
            assert successor.pid_path.exists()
            assert successor.socket_path.exists()
            assert successor.is_running()
        finally:
            successor._cleanup()

    def test_acquire_retries_when_inode_is_unlinked(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("unlinked-inode")
        owner._acquire_pid_file()
        successor = SessionDaemon("unlinked-inode")
        real_flock = fcntl.flock

        def unlink_then_flock(fd, flags):
            incoming = fd if isinstance(fd, int) else fd.fileno()
            if (
                flags & fcntl.LOCK_NB
                and owner._pid_file is not None
                and incoming != owner._pid_file.fileno()
            ):
                owner._cleanup()
            real_flock(fd, flags)

        monkeypatch.setattr(fcntl, "flock", unlink_then_flock)
        successor._acquire_pid_file()
        try:
            assert successor.pid_path.exists()
            assert successor._owns_paths
            assert successor._pid_file is not None
            assert _dm._pid_fd_matches_path(successor._pid_file, successor.pid_path)
        finally:
            successor._cleanup()

    def test_failed_competing_start_does_not_remove_owner_paths(
        self, tmp_path, monkeypatch
    ):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        owner = SessionDaemon("racing")
        contender = SessionDaemon("racing")
        owner._acquire_pid_file()
        owner.socket_path.touch()
        try:
            contender.start([], daemonize=False)
            assert owner.pid_path.exists()
            assert owner.socket_path.exists()
            assert owner.is_running()
        finally:
            owner._cleanup()


# ---------------------------------------------------------------------------
# Persistent turn lifecycle
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = io.BytesIO()
        self.returncode: int | None = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = -15


class TestPersistentTurnLifecycle:
    def test_worker_survives_failed_turn(self, monkeypatch):
        daemon = SessionDaemon("failed-turn")
        calls = 0

        def fail_once(_args, _prompts):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("spawn failed")
            daemon._stop_event.set()

        monkeypatch.setattr(daemon, "_run_turn", fail_once)
        daemon._turn_queue.put(["first"])
        daemon._turn_queue.put(["second"])
        daemon._worker_loop([])

        assert calls == 2
        assert "Turn failed: spawn failed" in "".join(daemon._output_buf)

    def test_idle_daemon_waits_for_later_prompts(self, monkeypatch):
        daemon = SessionDaemon("persistent")
        calls: list[list[str]] = []

        def fake_run_turn(args, prompts):
            calls.append(prompts)

        monkeypatch.setattr(daemon, "_run_turn", fake_run_turn)
        worker = threading.Thread(
            target=daemon._worker_loop, args=(["--name", "persistent"],)
        )
        worker.start()
        try:
            time.sleep(0.05)
            assert worker.is_alive()
            assert calls == []

            daemon._turn_queue.put(["first prompt"])
            daemon._turn_queue.join()
            daemon._turn_queue.put(["follow-up"])
            daemon._turn_queue.join()
            assert calls == [["first prompt"], ["follow-up"]]
            assert worker.is_alive()
        finally:
            daemon._stop_event.set()
            worker.join(timeout=1)

    def test_run_turn_uses_devnull_and_named_conversation(self, monkeypatch):
        daemon = SessionDaemon("persistent")
        captured = {}
        fake_proc = _FakeProcess()

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            return fake_proc

        monkeypatch.setattr("gptme.server.daemon.subprocess.Popen", fake_popen)
        daemon._run_turn(["--name", "persistent"], ["hello"])

        assert captured["argv"][-4:] == ["--name", "persistent", "--", "hello"]
        assert captured["stdin"] is subprocess.DEVNULL

    def test_pump_output_preserves_split_utf8(self, monkeypatch):
        daemon = SessionDaemon("unicode")
        read_chunks = iter([b"prefix \xe2", b"\x86\x92 suffix", b""])
        published: list[tuple[bytes, str | None]] = []

        monkeypatch.setattr(
            "gptme.server.daemon.os.read", lambda _fd, _n: next(read_chunks)
        )
        monkeypatch.setattr(
            daemon,
            "_publish_output",
            lambda chunk, text=None: published.append((chunk, text)),
        )

        class _Output:
            def fileno(self) -> int:
                return 0

        daemon._pump_output(_Output())

        assert "".join(text or "" for _, text in published) == "prefix → suffix"
        assert "�" not in "".join(text or "" for _, text in published)

    def test_history_is_sent_before_client_is_published(self):
        daemon = SessionDaemon("ordered")
        daemon._output_buf.append("history")
        daemon._output_buf_size = len(b"history")
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            thread = threading.Thread(target=daemon._serve_client, args=(server,))
            thread.start()
            ready = recv_msg(client)
            assert ready is not None
            assert ready.type == "status"
            history = recv_msg(client)
            assert history is not None
            assert history.data == "history"
            daemon._publish_output(b"live")
            live = recv_msg(client)
            assert live is not None
            assert live.data == "live"
        finally:
            client.close()
            thread.join(timeout=1)
            server.close()

    def test_client_uses_distinct_write_and_read_timeouts(self, monkeypatch):
        from gptme.server import daemon as daemon_module

        daemon = SessionDaemon("bounded-writes")
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        observed_timeouts: list[float | None] = []
        original_send_msg = send_msg

        def capture_timeout(sock, msg):
            observed_timeouts.append(sock.gettimeout())
            original_send_msg(sock, msg)

        monkeypatch.setattr("gptme.server.daemon.send_msg", capture_timeout)
        try:
            thread = threading.Thread(target=daemon._serve_client, args=(server,))
            thread.start()
            assert recv_msg(client) is not None
            assert observed_timeouts[0] == daemon_module._CLIENT_WRITE_TIMEOUT
            assert server.gettimeout() == daemon_module._CLIENT_READ_TIMEOUT
            daemon._publish_output(b"live")
            assert recv_msg(client) is not None
            assert observed_timeouts[-1] == daemon_module._CLIENT_WRITE_TIMEOUT
            assert server.gettimeout() == daemon_module._CLIENT_READ_TIMEOUT
        finally:
            client.close()
            thread.join(timeout=1)
            server.close()

    def test_live_write_failure_disconnects_served_client(self, monkeypatch):
        daemon = SessionDaemon("failed-write")
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        thread = threading.Thread(target=daemon._serve_client, args=(server,))
        thread.start()
        assert recv_msg(client) is not None

        def fail_send(_sock, _msg):
            raise TimeoutError

        monkeypatch.setattr("gptme.server.daemon.send_msg", fail_send)
        daemon._publish_output(b"blocked")
        thread.join(timeout=1)
        try:
            assert not thread.is_alive()
            assert daemon._client is None
        finally:
            client.close()
            server.close()

    def test_control_connection_stops_occupied_daemon(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        monkeypatch.setattr(_dm, "_pidfd_open", lambda _pid: None)
        monkeypatch.setattr(os, "kill", lambda *_args: pytest.fail("unpinned os.kill"))
        daemon = SessionDaemon("occupied-stop")
        daemon._acquire_pid_file()
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if daemon.socket_path.exists():
            daemon.socket_path.unlink()
        server_sock.bind(str(daemon.socket_path))
        server_sock.listen(2)
        server_sock.setblocking(False)
        loop = threading.Thread(target=daemon._accept_loop, args=(server_sock,))
        loop.start()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(str(daemon.socket_path))
        try:
            assert recv_msg(client) is not None
            SessionDaemon("occupied-stop").stop()
            loop.join(timeout=2)
            assert daemon._stop_event.is_set()
            assert not loop.is_alive()
        finally:
            client.close()
            server_sock.close()
            daemon._stop_event.set()
            loop.join(timeout=1)
            daemon._cleanup()

    def test_accept_loop_retries_interrupted_select(self, monkeypatch):
        daemon = SessionDaemon("interrupted")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        calls = 0

        def interrupted_once(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise InterruptedError
            daemon._stop_event.set()
            return [], [], []

        monkeypatch.setattr("gptme.server.daemon.select.select", interrupted_once)
        try:
            daemon._accept_loop(server)
            assert calls == 2
        finally:
            server.close()

    def test_accept_loop_retries_transient_accept_error(self, monkeypatch):
        daemon = SessionDaemon("accept-error")
        select_calls = 0

        class FailingServer:
            def accept(self):
                raise OSError

        server = FailingServer()

        def readable_once(*_args):
            nonlocal select_calls
            select_calls += 1
            if select_calls == 1:
                return [server], [], []
            daemon._stop_event.set()
            return [], [], []

        monkeypatch.setattr("gptme.server.daemon.select.select", readable_once)
        daemon._accept_loop(server)  # type: ignore[arg-type]
        assert select_calls == 2


# ---------------------------------------------------------------------------
# Integration: echo server simulating daemon I/O
# ---------------------------------------------------------------------------


class _ReadyDaemonThread(threading.Thread):
    def __init__(self, sock_path: Path) -> None:
        super().__init__(daemon=True)
        self.sock_path = sock_path
        self.ready = threading.Event()

    def run(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.sock_path))
        server.listen(1)
        self.ready.set()
        conn, _ = server.accept()
        send_msg(conn, IPCMessage(type="status", data={"ready": True}))
        conn.close()
        server.close()


class _EchoDaemonThread(threading.Thread):
    """Minimal echo server that speaks the IPC protocol — stand-in for daemon."""

    def __init__(self, sock_path: Path) -> None:
        super().__init__(daemon=True)
        self.sock_path = sock_path
        self.ready = threading.Event()
        self.received: list[str | dict] = []

    def run(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.sock_path))
        server.listen(1)
        self.ready.set()
        conn, _ = server.accept()
        try:
            send_msg(conn, IPCMessage(type="status", data={"ready": True}))
            # Send a welcome message (simulates buffered history)
            send_msg(conn, IPCMessage(type="output", data="[session output]\n"))
            # Echo input back as output
            while True:
                msg = recv_msg(conn)
                if msg is None:
                    break
                if msg.type == "input":
                    self.received.append(msg.data)
                    send_msg(conn, IPCMessage(type="output", data=f"echo: {msg.data}"))
        except OSError:
            pass  # client disconnected mid-send; expected in disconnect test
        finally:
            conn.close()
            server.close()


class TestAttachProtocol:
    """Test the attach protocol against a mock daemon (no real gptme subprocess)."""

    def test_attach_times_out_when_client_slot_is_occupied(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        monkeypatch.setattr(_dm, "_ATTACH_HANDSHAKE_TIMEOUT", 0.01)
        sock_path = tmp_path / "occupied.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        try:
            with pytest.raises(TimeoutError, match="already has an attached client"):
                attach("occupied")
        finally:
            server.close()

    def test_attach_reports_socket_closed_before_handshake(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        sock_path = tmp_path / "stale.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        def close_immediately():
            conn, _ = server.accept()
            conn.close()
            server.close()

        thread = threading.Thread(target=close_immediately)
        thread.start()
        try:
            with pytest.raises(ConnectionResetError, match="closed during attach"):
                attach("stale")
        finally:
            thread.join(timeout=1)
            assert not thread.is_alive()

    def test_attach_receiver_handles_truncated_frame(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        sock_path = tmp_path / "truncated.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        def send_truncated_frame():
            conn, _ = server.accept()
            send_msg(conn, IPCMessage(type="status", data={"ready": True}))
            conn.sendall(IPCMessage(type="output", data="partial").encode()[:6])
            conn.close()
            server.close()

        thread = threading.Thread(target=send_truncated_frame)
        thread.start()
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        try:
            attach("truncated")
        finally:
            thread.join(timeout=1)
            assert not thread.is_alive()

    def test_attach_returns_when_daemon_exits_without_stdin(
        self, tmp_path, monkeypatch
    ):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        sock_path = tmp_path / "hang.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        def send_ready_and_close() -> None:
            conn, _ = server.accept()
            send_msg(conn, IPCMessage(type="status", data={"ready": True}))
            conn.close()
            server.close()

        read_fd, write_fd = os.pipe()
        monkeypatch.setattr("sys.stdin", os.fdopen(read_fd))
        server_thread = threading.Thread(target=send_ready_and_close)
        server_thread.start()
        done = threading.Event()
        errors: list[BaseException] = []

        def run_attach() -> None:
            try:
                attach("hang")
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        attach_thread = threading.Thread(target=run_attach)
        attach_thread.start()
        try:
            finished = done.wait(timeout=3)
            assert finished, "attach hung on stdin after daemon disconnect"
            assert errors == []
        finally:
            os.close(write_fd)
            attach_thread.join(timeout=1)
            server_thread.join(timeout=1)

    def test_attach_receives_history_and_echo(self, tmp_path):
        sock_path = tmp_path / "test.socket"

        server = _EchoDaemonThread(sock_path)
        server.start()
        server.ready.wait(timeout=2)

        # Connect as a client
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(str(sock_path))

        ready = recv_msg(conn)
        assert ready is not None
        assert ready.type == "status"

        # Receive the welcome message (buffered history)
        welcome = recv_msg(conn)
        assert welcome is not None
        assert welcome.type == "output"
        assert "session output" in welcome.data

        # Send input
        send_msg(conn, IPCMessage(type="input", data="test input\n"))

        # Receive echo
        echo = recv_msg(conn)
        assert echo is not None
        assert echo.type == "output"
        assert "test input" in echo.data

        conn.close()
        server.join(timeout=1)
        assert "test input\n" in server.received

    def test_client_disconnect_does_not_crash_server(self, tmp_path):
        """Daemon (echo server) should survive a client that disconnects abruptly."""
        sock_path = tmp_path / "crash.socket"

        server = _EchoDaemonThread(sock_path)
        server.start()
        server.ready.wait(timeout=2)

        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(str(sock_path))
        # Disconnect immediately without sending anything
        conn.close()

        # Server thread should exit cleanly
        server.join(timeout=2)
        assert not server.is_alive()


# ---------------------------------------------------------------------------
# list_daemons
# ---------------------------------------------------------------------------


class TestListDaemons:
    def test_is_running_while_primary_client_is_attached(self, tmp_path, monkeypatch):
        from gptme.server import daemon as _dm

        monkeypatch.setattr(_dm, "get_daemon_dir", lambda: tmp_path)
        daemon = SessionDaemon("occupied")
        daemon._acquire_pid_file()
        daemon.socket_path.touch()
        try:
            # The advisory lock remains held while any client occupies the single
            # connection slot, so liveness does not depend on a status handshake.
            assert daemon.is_running()
        finally:
            daemon._cleanup()

    def test_list_empty(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            assert list_daemons() == []
        finally:
            _dm.get_daemon_dir = orig

    def test_list_with_stale_pid(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            (tmp_path / "old.pid").write_text("999999999")
            daemons = list_daemons()
            assert len(daemons) == 1
            assert daemons[0]["session"] == "old"
            assert daemons[0]["running"] is False
        finally:
            _dm.get_daemon_dir = orig

    def test_list_with_live_pid(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            daemon = SessionDaemon("live")
            daemon._acquire_pid_file()
            daemon.socket_path.touch()
            daemons = list_daemons()
            assert len(daemons) == 1
            assert daemons[0]["running"] is True
        finally:
            daemon._cleanup()
            _dm.get_daemon_dir = orig


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestDaemonCLI:
    @pytest.mark.parametrize("command", ["start", "attach", "stop", "status"])
    def test_commands_reject_invalid_session_cleanly(self, command):
        from click.testing import CliRunner

        from gptme.cli.cmd_daemon import cli

        args = [command, "foo/bar"]
        if command == "start":
            args = [command, "--session", "foo/bar"]
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 2
        assert "Invalid value" in result.output
        assert "single path component" in result.output
        assert "Traceback" not in result.output

    def test_attach_can_disable_auto_start(self):
        from click.testing import CliRunner

        from gptme.cli.cmd_daemon import cli

        result = CliRunner().invoke(cli, ["attach", "--help"])
        assert result.exit_code == 0
        assert "--no-start-if-missing" in result.output

    def test_list_command_empty(self, tmp_path):
        from click.testing import CliRunner

        from gptme.cli.cmd_daemon import cli
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "No daemon sessions found" in result.output
        finally:
            _dm.get_daemon_dir = orig

    def test_status_command_not_running(self, tmp_path):
        from click.testing import CliRunner

        from gptme.cli.cmd_daemon import cli
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["status", "ghost"])
            assert result.exit_code == 0
            assert "stopped" in result.output
        finally:
            _dm.get_daemon_dir = orig

    def test_stop_command_not_running(self, tmp_path):
        from click.testing import CliRunner

        from gptme.cli.cmd_daemon import cli
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["stop", "ghost"])
            assert result.exit_code != 0  # exits 1 when not running
        finally:
            _dm.get_daemon_dir = orig
