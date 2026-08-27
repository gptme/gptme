"""Tests for gptme session daemon — Phase 1 MVP.

Tests cover:
- IPC protocol encode/decode roundtrip
- Daemon socket path helpers
- start → attach → detach → re-attach session state preservation
- Daemon survives client SIGHUP (SSH-drop case)
"""

from __future__ import annotations

import io
import os
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
from gptme.server.ipc_protocol import IPCMessage, recv_msg, send_msg

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
        real_kill = os.kill

        def record_kill(pid: int, signum: int) -> None:
            if signum == 0:
                real_kill(pid, signum)
            else:
                signals.append(signum)

        monkeypatch.setattr(os, "kill", record_kill)
        d.stop()

        assert signals == []

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

        assert captured["argv"][-3:] == ["--name", "persistent", "hello"]
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

    def test_client_writes_have_timeout_before_handshake(self, monkeypatch):
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
            assert observed_timeouts[0] is not None
        finally:
            client.close()
            thread.join(timeout=1)
            server.close()

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
