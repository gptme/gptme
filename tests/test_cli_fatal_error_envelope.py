"""Tests for non-interactive fatal error envelope and exit taxonomy.

When gptme --non-interactive encounters a fatal LLM startup error, it should:
1. Append a terminal error event to conversation.jsonl
2. Exit with a class-specific code (75/76/77/1)
"""

import importlib
import json
import os
import subprocess
import sys
import threading
from io import FileIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli

# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner(tmp_data_dir: Path) -> CliRunner:
    return CliRunner()


def _fake_config(tmp_path: Path) -> Any:
    return SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=["shell", "read"],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=tmp_path,
            stream=False,
            agent=None,
            gear=None,
            no_confirm=True,
        ),
        project=None,
    )


def _setup_cli_mocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, chat_raises: Exception
) -> None:
    """Patch the minimal set of dependencies so the CLI reaches the chat() call."""
    config = _fake_config(tmp_path)
    monkeypatch.setattr("gptme.config.setup_config_from_cli", lambda **_: config)
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **_: None)
    monkeypatch.setattr("gptme.telemetry.shutdown_telemetry", lambda: None)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise chat_raises

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", _raise)


def _provider_error(
    message: str, *, status_code: int | None = None, response: Any = None
) -> Exception:
    attrs: dict[str, Any] = {}
    if status_code is not None:
        attrs["status_code"] = status_code
    if response is not None:
        attrs["response"] = response
    exc_cls = type("ProviderError", (Exception,), attrs)
    return exc_cls(message)


def _read_line_with_timeout(proc: subprocess.Popen[str], timeout: float = 15.0) -> str:
    """Read one stdout line from ``proc`` with a cross-platform deadline."""
    assert proc.stdout is not None
    lines: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        lines.append(proc.stdout.readline())

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        proc.kill()
        _, stderr = proc.communicate(timeout=5)
        pytest.fail(
            f"Child process timed out after {timeout}s waiting for readiness: {stderr}"
        )
    return lines[0].strip() if lines else ""


def _stop_child(proc: subprocess.Popen[str], timeout: float = 5.0) -> None:
    """Stop a stdin-waiting helper child cleanly, falling back to kill."""
    if proc.poll() is not None:
        return
    try:
        proc.communicate("\n", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        proc.kill()
        proc.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Unit tests for _classify_fatal_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_class", "expected_code"),
    [
        # Rate limit via ValueError from subscription LLM (the concrete production case)
        (
            ValueError("Codex API error 429: usage_limit_reached"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Rate limit via openai-style exception type name
        (
            type("RateLimitError", (Exception,), {})("too many requests"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Auth error via type name
        (
            type("AuthenticationError", (Exception,), {})("invalid key"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Auth error via 401 in message
        (
            ValueError("Codex API error 401: unauthorized"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Model unavailable via 503 in message
        (
            ValueError("Codex API error 503: service unavailable"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # Rate limit via bare "429 Too Many Requests" (no keyword suffix)
        (
            ValueError("429 Too Many Requests"),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        # Auth error via bare "401 Client Error: Unauthorized" (no "api error" phrase)
        (
            ValueError(
                "401 Client Error: Unauthorized for url: https://api.example.com"
            ),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Model unavailable via SDK-native exception type (openai/anthropic style)
        (
            type("NotFoundError", (Exception,), {})("model not found"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # ServiceUnavailableError (503) maps to model_unavailable
        (
            type("ServiceUnavailableError", (Exception,), {})("service down"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        # InternalServerError (HTTP 500/502/504) falls through to generic exit 1
        (
            type("InternalServerError", (Exception,), {})("upstream 500"),
            "generic",
            1,
        ),
        # Generic fallback
        (
            RuntimeError("something unexpected"),
            "generic",
            1,
        ),
        # Regression: substrings inside longer numbers must NOT misfire
        (
            RuntimeError("Another gptme instance (PID 14032) is using /tmp/logs/foo"),
            "generic",
            1,
        ),
        (
            RuntimeError("Context limit exceeded: 140352 tokens"),
            "generic",
            1,
        ),
        (
            RuntimeError("Request timed out after 4013 ms"),
            "generic",
            1,
        ),
        (
            RuntimeError("Request timed out after 5030 ms"),
            "generic",
            1,
        ),
        (
            RuntimeError("Found 4040 items"),
            "generic",
            1,
        ),
        (
            RuntimeError("Model claude-opus-4-7-20260401 failed"),
            "generic",
            1,
        ),
        (
            RuntimeError("Rate limit reset in 4290 ms"),
            "generic",
            1,
        ),
        # Regression: standalone non-status quantities (PID / tokens / ms) must NOT misfire
        (
            RuntimeError("Another gptme instance (PID 403) is using /tmp/logs/foo"),
            "generic",
            1,
        ),
        (
            RuntimeError("Another gptme instance (PID 1234) is using /tmp/logs/404"),
            "generic",
            1,
        ),
        (
            RuntimeError("Context limit exceeded: 403 tokens"),
            "generic",
            1,
        ),
        (
            RuntimeError("Request timed out after 401 ms"),
            "generic",
            1,
        ),
        # Genuine standalone / HTTP-status messages must still classify properly
        (
            RuntimeError("401 Unauthorized"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            RuntimeError("HTTP 403"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            RuntimeError("HTTP 403 Forbidden"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            RuntimeError("status 401"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            RuntimeError("status 404"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        (
            RuntimeError("status 503"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        (
            ValueError("HTTP 404 Not Found"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        (
            ValueError("Error code: 403 - permission denied"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            ValueError("HTTP/1.1 503 Service Unavailable"),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        (
            ValueError("401"),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        # Structured provider status_code / response.status_code attributes
        (
            _provider_error("bad token", status_code=401),
            "auth_error",
            cli.EXIT_AUTH_ERROR,
        ),
        (
            _provider_error(
                "HTTP 401 upstream hint",
                response=SimpleNamespace(status_code=503),
            ),
            "model_unavailable",
            cli.EXIT_MODEL_UNAVAIL,
        ),
        (
            _provider_error("rate limit exceeded", status_code=429),
            "rate_limit",
            cli.EXIT_RATE_LIMIT,
        ),
        (
            _provider_error("HTTP 401", status_code=500),
            "generic",
            1,
        ),
    ],
)
def test_classify_fatal_error(
    exc: Exception, expected_class: str, expected_code: int
) -> None:
    error_class, exit_code = cli._classify_fatal_error(exc)
    assert error_class == expected_class
    assert exit_code == expected_code


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RuntimeError("Another gptme instance is using /tmp/logs/foo"), True),
        (
            RuntimeError("Another gptme instance (PID 1234) is using /tmp/logs/foo"),
            True,
        ),
        (RuntimeError("boom"), False),
        (RuntimeError("invalid config"), False),
    ],
)
def test_is_conversation_lock_error(exc: Exception, expected: bool) -> None:
    assert cli._is_conversation_lock_error(exc) is expected


# ---------------------------------------------------------------------------
# Unit tests for _write_terminal_error_to_log
# ---------------------------------------------------------------------------


def test_write_terminal_error_creates_jsonl_event(tmp_path: Path) -> None:
    """_write_terminal_error_to_log appends a valid JSON event to conversation.jsonl."""
    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"

    cli._write_terminal_error_to_log(logdir, "rate_limit", 75, "quota exhausted")

    assert log_file.exists()
    event = json.loads(log_file.read_text().strip())
    assert event["role"] == "system"
    assert "rate_limit" in event["content"]
    assert "quota exhausted" in event["content"]
    assert event["metadata"]["error"] is True
    assert event["metadata"]["error_class"] == "rate_limit"
    assert event["metadata"]["exit_code"] == 75


def test_write_terminal_error_appends_to_existing_log(tmp_path: Path) -> None:
    """_write_terminal_error_to_log appends without truncating existing content."""
    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    log_file.write_text('{"role":"user","content":"ping"}\n')

    cli._write_terminal_error_to_log(logdir, "auth_error", 76, "bad token")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    user_event = json.loads(lines[0])
    assert user_event["role"] == "user"
    err_event = json.loads(lines[1])
    assert err_event["metadata"]["error_class"] == "auth_error"


def test_write_terminal_error_missing_logdir_is_silent(tmp_path: Path) -> None:
    """_write_terminal_error_to_log does not raise if logdir doesn't exist."""
    logdir = tmp_path / "nonexistent"
    cli._write_terminal_error_to_log(logdir, "generic", 1, "boom")
    # Should complete without raising


def test_write_terminal_error_skips_when_lifecycle_lock_held(tmp_path: Path) -> None:
    """Writer must not race conversation startup before .lock is created."""
    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    original_content = '{"role":"user","content":"existing"}\n'
    log_file.write_text(original_content)

    holder_code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from gptme.logmanager.locking import conversation_lifecycle_lock\n"
        "with conversation_lifecycle_lock(Path(sys.argv[1])):\n"
        "    print('LOCKED', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(logdir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = _read_line_with_timeout(holder)
        if ready != "LOCKED":
            holder.kill()
            _, stderr = holder.communicate(timeout=5)
            pytest.fail(f"Lifecycle lock holder failed to start ({ready!r}): {stderr}")

        # No .lock exists yet, simulating failure before LogManager.load.
        assert not (logdir / ".lock").exists()
        cli._write_terminal_error_to_log(logdir, "generic", 1, "should not race")
        assert log_file.read_text() == original_content
    finally:
        _stop_child(holder)

    # Once the lifecycle gate is released, writing succeeds.
    cli._write_terminal_error_to_log(logdir, "generic", 1, "after release")
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2


def test_write_terminal_error_holds_lifecycle_lock_through_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifecycle lock must stay held across stale-.lock check and append."""
    from contextlib import contextmanager

    import gptme.logmanager.locking as locking_mod

    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    (logdir / ".lock").write_text("999999")

    events: list[str] = []
    real_lock = locking_mod.conversation_lifecycle_lock

    @contextmanager
    def _tracking_lifecycle_lock(path: Path, *, blocking: bool = True):
        events.append(f"enter:{blocking}")
        with real_lock(path, blocking=blocking):
            yield
        events.append("exit")

    monkeypatch.setattr(
        locking_mod, "conversation_lifecycle_lock", _tracking_lifecycle_lock
    )

    real_open = open

    def _tracking_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if Path(file) == log_file and "a" in mode:
            events.append("append")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _tracking_open)

    cli._write_terminal_error_to_log(logdir, "generic", 1, "stale lock append")
    assert events == ["enter:False", "append", "exit"]
    assert log_file.exists()


def test_write_terminal_error_respects_windows_msvcrt_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the Windows _msvcrt locking path on every platform."""
    from gptme.logmanager import manager as logmanager_mod

    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    original_content = '{"role":"user","content":"active"}\n'
    log_file.write_text(original_content)
    lockfile = logdir / ".lock"
    lockfile.write_text(str(os.getpid() + 1))
    lock_stat = lockfile.stat()
    lock_id = (lock_stat.st_dev, lock_stat.st_ino)

    calls: list[tuple[tuple[int, int], int]] = []
    lock_held_by_other = True

    class _FakeMsvcrt:
        LK_NBLCK = 2
        LK_UNLCK = 0

        @staticmethod
        def locking(fd: int, mode: int, nbytes: int) -> None:
            st = os.fstat(fd)
            fid = (st.st_dev, st.st_ino)
            calls.append((fid, mode))
            if lock_held_by_other and mode == _FakeMsvcrt.LK_NBLCK and fid == lock_id:
                raise PermissionError("locked by another Windows process")

    monkeypatch.setattr(logmanager_mod, "fcntl", None)
    monkeypatch.setattr(logmanager_mod, "_msvcrt", _FakeMsvcrt)

    # Held by another Windows process -> must refuse to append.
    cli._write_terminal_error_to_log(logdir, "generic", 1, "must not write")
    assert log_file.read_text() == original_content
    assert any(fid == lock_id and mode == _FakeMsvcrt.LK_NBLCK for fid, mode in calls)

    # Same-process Windows owner in _windows_lock_owners -> must allow append
    # even when a second handle cannot lock or read byte 0.
    calls.clear()
    dummy_fd = FileIO(lockfile, "r+")
    lock_key = lockfile.resolve()
    try:
        with logmanager_mod._windows_lock_owners_guard:
            logmanager_mod._windows_lock_owners[lock_key] = (
                logmanager_mod._WindowsLockState(dummy_fd)
            )
        cli._write_terminal_error_to_log(logdir, "auth_error", 76, "same-process win")
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["metadata"]["error_class"] == "auth_error"
    finally:
        with logmanager_mod._windows_lock_owners_guard:
            logmanager_mod._windows_lock_owners.pop(lock_key, None)
        dummy_fd.close()

    # Unlocked Windows .lock -> acquires and unlocks both gate and .lock, then writes.
    calls.clear()
    lock_held_by_other = False
    cli._write_terminal_error_to_log(logdir, "rate_limit", 75, "unlocked win")
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 3
    assert (lock_id, _FakeMsvcrt.LK_NBLCK) in calls
    assert (lock_id, _FakeMsvcrt.LK_UNLCK) in calls


def test_write_terminal_error_refuses_when_no_locking_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If neither fcntl nor msvcrt is available, fail closed instead of appending."""
    from gptme.logmanager import manager as logmanager_mod

    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    original_content = '{"role":"user","content":"active"}\n'
    log_file.write_text(original_content)
    (logdir / ".lock").write_text(str(os.getpid() + 1))

    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setattr(logmanager_mod, "fcntl", None)
    monkeypatch.setattr(logmanager_mod, "_msvcrt", None)

    cli._write_terminal_error_to_log(logdir, "generic", 1, "must not write")
    assert log_file.read_text() == original_content


@pytest.mark.parametrize(
    "raw_lock_bytes",
    [
        b"\xff\xfe",  # invalid UTF-8 -> UnicodeDecodeError
        "²".encode(),  # str.isdigit() is True, int() raises ValueError
        b"not-a-pid",
    ],
)
def test_write_terminal_error_corrupt_lock_pid_skips_without_raising(
    tmp_path: Path, raw_lock_bytes: bytes
) -> None:
    """Corrupt/unreadable .lock content must skip the write and never mask the error."""
    from gptme.logmanager import LogManager

    logdir = tmp_path / "conv"
    logdir.mkdir()
    log_file = logdir / "conversation.jsonl"
    original_content = '{"role":"user","content":"active"}\n'
    log_file.write_text(original_content)
    lockfile = logdir / ".lock"
    lockfile.write_bytes(raw_lock_bytes)

    holder_fd = FileIO(lockfile, "r+")
    try:
        holder_fd.seek(0)
        LogManager._platform_lock(holder_fd)
        cli._write_terminal_error_to_log(logdir, "generic", 1, "must not write")
    finally:
        LogManager._platform_unlock(holder_fd)
        holder_fd.close()

    assert log_file.read_text() == original_content


# ---------------------------------------------------------------------------
# Integration: CLI exit codes and conversation log
# ---------------------------------------------------------------------------


def test_rate_limit_exits_75_and_writes_error_event(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """A 429 usage_limit_reached exception → exit 75 + terminal error in conversation.jsonl."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=ValueError("Codex API error 429: usage_limit_reached"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-rate-limit", "ping"]
    )

    assert result.exit_code == cli.EXIT_RATE_LIMIT, result.output

    # The conversation log should contain the terminal error event
    log_file = cli.get_logs_dir() / "test-rate-limit" / "conversation.jsonl"
    assert log_file.exists(), f"conversation.jsonl not found at {log_file}"
    events = [
        json.loads(line) for line in log_file.read_text().strip().splitlines() if line
    ]
    error_events = [e for e in events if e.get("metadata", {}).get("error")]
    assert error_events, "No error event written to conversation.jsonl"
    assert error_events[-1]["metadata"]["error_class"] == "rate_limit"
    assert error_events[-1]["metadata"]["exit_code"] == cli.EXIT_RATE_LIMIT


def test_auth_error_exits_76(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """A 401 error → exit 76."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=ValueError("Codex API error 401: unauthorized"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-auth-err", "ping"]
    )
    assert result.exit_code == cli.EXIT_AUTH_ERROR


def test_generic_error_exits_1(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """An unclassified exception still exits 1 (backward-compatible default)."""
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=RuntimeError("unexpected internal failure"),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-generic-err", "ping"]
    )
    assert result.exit_code == 1


def test_fatal_error_prints_resume_hint(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After a crash the conversation is saved: tell the user how to resume it."""
    handlers: list = []
    monkeypatch.setattr(cli.atexit, "register", handlers.append)
    _setup_cli_mocks(monkeypatch, tmp_path, chat_raises=RuntimeError("boom"))
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "test-crash-hint", "ping"]
    )
    assert result.exit_code != 0

    # the saved log is nonempty (the terminal error event), so the exit
    # handler registered by the CLI prints the resume command
    for handler in handlers:
        handler()
    assert "gptme --name test-crash-hint" in capsys.readouterr().out


def test_lock_refusal_does_not_append_to_conversation_log(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """A lock-refused non-interactive run must exit 1 and leave conversation.jsonl untouched."""
    logdir = cli.get_logs_dir() / "locked-unit-conv"
    logdir.mkdir(parents=True, exist_ok=True)
    log_file = logdir / "conversation.jsonl"
    original_content = '{"role":"user","content":"active session"}\n'
    log_file.write_text(original_content)

    # Use a PID containing "403" to also guard against exit-code misclassification.
    _setup_cli_mocks(
        monkeypatch,
        tmp_path,
        chat_raises=RuntimeError(
            f"Another gptme instance (PID 14032) is using {logdir}"
        ),
    )
    result = runner.invoke(
        cli.main, ["--non-interactive", "--name", "locked-unit-conv", "ping"]
    )

    assert result.exit_code == 1
    assert log_file.read_text() == original_content


def test_lock_refusal_with_live_holder_leaves_conversation_log_untouched(
    tmp_path: Path,
) -> None:
    """Real subprocess lock refusal must not write to the holder's conversation.jsonl."""
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("GPTME_LOGS_HOME", None)
    env["XDG_DATA_HOME"] = str(data_dir)
    env["XDG_STATE_HOME"] = str(state_dir)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["HOME"] = str(tmp_path)
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:1/v1"
    env["OPENAI_API_KEY"] = "dummy"

    logdir = data_dir / "gptme" / "logs" / "locked-live-conv"
    logdir.mkdir(parents=True, exist_ok=True)
    log_file = logdir / "conversation.jsonl"
    original_content = json.dumps({"role": "user", "content": "active session"}) + "\n"
    log_file.write_text(original_content)

    holder_code = (
        "import sys; "
        "from pathlib import Path; "
        "from gptme.logmanager import LogManager; "
        "m = LogManager(logdir=Path(sys.argv[1]), lock=True); "
        "print('LOCKED', flush=True); "
        "sys.stdin.readline(); "
        "m._release_lock()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(logdir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        ready = _read_line_with_timeout(holder)
        if ready != "LOCKED":
            holder.kill()
            _, stderr = holder.communicate(timeout=5)
            pytest.fail(f"Lock holder failed to start ({ready!r}): {stderr}")

        # Direct call to _write_terminal_error_to_log must also respect the live lock.
        cli._write_terminal_error_to_log(logdir, "generic", 1, "should not be written")
        assert log_file.read_text() == original_content

        # Full non-interactive CLI run refused on the lock.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "gptme",
                "--non-interactive",
                "--name",
                "locked-live-conv",
                "-m",
                "local/test",
                "ping",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=30,
        )
        assert proc.returncode == 1, proc.stderr
        assert "Another gptme instance" in proc.stderr
        assert log_file.read_text() == original_content
    finally:
        _stop_child(holder)
