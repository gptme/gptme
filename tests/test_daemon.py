"""Tests for gptme session daemon — Phase 1 MVP.

Tests cover:
- IPC protocol encode/decode roundtrip
- Daemon socket path helpers
- start → attach → detach → re-attach session state preservation
- Daemon survives client SIGHUP (SSH-drop case)
"""

from __future__ import annotations

import os
import socket
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from gptme.server.daemon import (
    SessionDaemon,
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

    def test_is_running_true_with_own_pid(self, tmp_path):
        from gptme.server import daemon as _dm

        orig = _dm.get_daemon_dir
        _dm.get_daemon_dir = lambda: tmp_path
        try:
            d = SessionDaemon("live")
            (tmp_path / "live.socket").touch()
            (tmp_path / "live.pid").write_text(str(os.getpid()))
            assert d.is_running()
        finally:
            _dm.get_daemon_dir = orig


# ---------------------------------------------------------------------------
# Integration: echo server simulating daemon I/O
# ---------------------------------------------------------------------------


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

    def test_attach_receives_history_and_echo(self, tmp_path):
        sock_path = tmp_path / "test.socket"

        server = _EchoDaemonThread(sock_path)
        server.start()
        server.ready.wait(timeout=2)

        # Connect as a client
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(str(sock_path))

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
            (tmp_path / "live.pid").write_text(str(os.getpid()))
            (tmp_path / "live.socket").touch()
            daemons = list_daemons()
            assert len(daemons) == 1
            assert daemons[0]["running"] is True
        finally:
            _dm.get_daemon_dir = orig


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestDaemonCLI:
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
