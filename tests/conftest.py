"""Test configuration and shared fixtures."""

import http.server
import json
import logging
import os
import queue
import socket
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager

import pytest
import requests

import gptme.init as _gptme_init
from gptme.config import get_config, set_config
from gptme.init import init
from gptme.llm.retry_abort import interrupt_thread
from gptme.tools import clear_tools
from gptme.tools import shell as shell_module
from gptme.tools.rag import _has_gptme_rag
from gptme.tools.subagent import (
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)
from gptme.tools.subagent.concurrency import _reset_slot_sem

logger = logging.getLogger(__name__)

# Set at session start if Anthropic API quota is exhausted or API key is invalid
_anthropic_quota_exhausted = False
# Human-readable reason for why requires_api tests are being skipped
_anthropic_skip_reason = "Anthropic API quota exhausted or invalid API key"

# Error patterns that indicate Anthropic API quota/rate-limit exhaustion.
# Keep these Anthropic-specific to avoid silently masking failures from other providers.
_QUOTA_ERROR_PATTERNS = [
    "usage limits",
    "rate limit",
    "quota exceeded",
    "billing hard limit",
    "insufficient_quota",
    "exceeded your current quota",
    "spending limit",
    # Anthropic-specific authentication failures — treat as "can't run API tests"
    "authentication_error",
    "invalid x-api-key",
]


def has_api_key() -> bool:
    """Check if any API key is configured."""
    config = get_config()
    # Check for any configured API keys
    return bool(
        config.get_env("OPENAI_API_KEY", "")
        or config.get_env("ANTHROPIC_API_KEY", "")
        or config.get_env("OPENROUTER_API_KEY", "")
        or config.get_env("DEEPSEEK_API_KEY", "")
    )


def _check_anthropic_quota_exhausted() -> bool:
    """Make a minimal API call to detect if Anthropic API tests should be skipped.

    Returns True if quota is exhausted or the API key is invalid/missing,
    False if the API is available or ANTHROPIC_API_KEY is not configured.
    Runs whenever ANTHROPIC_API_KEY is configured, regardless of MODEL env var.
    """
    config = get_config()
    api_key = config.get_env("ANTHROPIC_API_KEY", "")
    if not api_key:
        return False
    try:
        import anthropic
    except ImportError:
        return False
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return False
    except anthropic.BadRequestError as e:
        error_str = str(e).lower()
        if any(p in error_str for p in _QUOTA_ERROR_PATTERNS):
            logger.warning(f"Anthropic API quota exhausted: {e}")
            return True
        return False
    except anthropic.RateLimitError as e:
        logger.warning(f"Anthropic API rate limited: {e}")
        return True
    except anthropic.AuthenticationError as e:
        logger.warning(
            f"Anthropic API key invalid — requires_api tests will be skipped: {e}"
        )
        return True
    except Exception:
        return False


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "requires_api: mark test as requiring an API key",
    )
    # Disable pre-commit checks during tests to avoid interference
    os.environ["GPTME_CHECK"] = "false"
    # Disable chat history context during tests for predictable prompts
    os.environ["GPTME_CHAT_HISTORY"] = "false"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert API quota/rate-limit failures to skips for requires_api tests.
    Also suppresses pytest-retry StashKey teardown errors.

    The session-start quota check may pass with a tiny haiku call, but the
    actual test can hit quota limits with heavier models or longer generations.
    This hook catches those mid-run failures and converts them to skips.

    For the StashKey case: when pytest-retry retries a test that uses tmp_path,
    pytest's stash-based fixture tracking loses its key during teardown of the
    retried attempt (an architectural issue in pytest-retry, not version-specific).
    The test body itself passed; treat the teardown as passed to prevent a
    spurious CI ERROR from masking the real result.
    """
    report = yield

    if (
        report.when == "call"
        and report.failed
        and "requires_api" in item.keywords
        and call.excinfo is not None
    ):
        error_str = str(call.excinfo.value).lower()
        if any(pattern in error_str for pattern in _QUOTA_ERROR_PATTERNS):
            report.outcome = "skipped"
            report.longrepr = f"API quota exhausted or invalid credentials during test: {call.excinfo.value}"

    # Count call-phase attempts so the teardown guard below can verify the test
    # was actually retried (not just that a single passing call existed). Track
    # whether the last call passed so we know the retry succeeded.
    if report.when == "call":
        item._stash_guard_call_attempts = (
            getattr(item, "_stash_guard_call_attempts", 0) + 1
        )
        item._stash_guard_call_passed = report.passed

    # pytest-retry compat: tmp_path (and caplog) stash keys are
    # populated by pytest's fixture machinery during the first (failed) attempt.
    # When pytest-retry re-runs the test body without a full fixture re-setup,
    # the stash entry is absent during teardown of the retried (passing) attempt,
    # producing KeyError: <_pytest.stash.StashKey object at 0x...>.
    # Three-way guard to avoid masking genuine teardown failures (Greptile P1):
    #   (a) test was actually retried (>1 call attempts seen by this hook)
    #   (b) the last call attempt passed (retry succeeded)
    #   (c) the error is specifically from _pytest.stash internals
    if (
        report.when == "teardown"
        and report.failed
        and getattr(item, "_stash_guard_call_attempts", 0) > 1
        and getattr(item, "_stash_guard_call_passed", False)
    ):
        longrepr_str = str(report.longrepr)
        if "_pytest.stash.StashKey" in longrepr_str and "KeyError" in longrepr_str:
            logger.warning(
                "Suppressed pytest-retry StashKey teardown error (known "
                "infrastructure artifact, not version-specific) "
                "for %s — the test body passed; this is a known infrastructure "
                "artifact (see ErikBjare/bob#1084)",
                item.nodeid,
            )
            report.outcome = "passed"
            report.longrepr = None

    return report


def pytest_collection_modifyitems(config, items):
    """Skip tests marked as requiring API key if no key is configured or quota exhausted."""
    if not has_api_key():
        # Set environment variables to override LLM provider config
        os.environ["MODEL"] = "local/test"
        os.environ["OPENAI_BASE_URL"] = "http://localhost:666"

        skip_api = pytest.mark.skip(reason="No API key configured")
        for item in items:
            if "requires_api" in item.keywords:
                item.add_marker(skip_api)
    elif _anthropic_quota_exhausted:
        skip_api = pytest.mark.skip(reason=_anthropic_skip_reason)
        for item in items:
            if "requires_api" in item.keywords:
                item.add_marker(skip_api)

    # Wire up no_retry: prevent pytest-retry from retrying these tests.
    # flaky(retries=0) is NOT sufficient — the retry loop always runs at least once.
    # condition=False causes pytest-retry to return early before any retry attempt.
    # append=False prepends our marker so get_closest_marker() finds it first,
    # even if pytest-retry already added flaky(retries=N) during its own
    # pytest_collection_modifyitems pass.
    no_retry_mark = pytest.mark.flaky(condition=False)
    for item in items:
        if "no_retry" in item.keywords:
            item.add_marker(no_retry_mark, append=False)


def pytest_sessionstart(session):
    global _anthropic_quota_exhausted
    # Download the embedding model before running tests.
    download_model()
    # Check if Anthropic quota is exhausted to skip API tests gracefully.
    if has_api_key():
        _anthropic_quota_exhausted = _check_anthropic_quota_exhausted()
        if _anthropic_quota_exhausted:
            logger.warning(
                "⚠️  Anthropic API quota exhausted — requires_api tests will be skipped"
            )


def download_model():
    if not _has_gptme_rag():
        return

    try:
        # downloads the model if it doesn't exist
        from chromadb.utils import embedding_functions  # fmt: skip
    except ImportError:
        return

    ef = embedding_functions.DefaultEmbeddingFunction()
    if ef:
        ef._download_model_if_not_exists()


@pytest.fixture
def auth_headers():
    """Provide authentication headers for HTTP requests to test server."""
    return {"Authorization": "Bearer test-token-for-server-thread"}


@pytest.fixture(autouse=True)
def reduce_anthropic_retries(monkeypatch):
    """Reduce Anthropic API retries during tests to prevent timeouts.

    Anthropic API can have transient errors (5xx, overloaded) that trigger
    exponential backoff retries. With default max_retries=5 and 60s timeout
    per retry, a test with 2 sequential subagents can take ~10.5 minutes,
    causing GitHub Actions timeout (15 min).

    Reducing to max_retries=2 brings total time to ~4 minutes, well under
    the timeout while still allowing some retry resilience.
    """
    # Set environment variable to limit retries during tests
    monkeypatch.setenv("GPTME_TEST_MAX_RETRIES", "2")


@pytest.fixture(autouse=True)
def clear_tools_before():
    # Clear all tools and cache to prevent test conflicts
    clear_tools()
    # Reset init state so tools are fully re-registered on next init() call.
    # Without this, the _init_done guard prevents init_tools() from re-running
    # after clear_tools(), leaving get_tool() returning None for all tools.
    _gptme_init._init_done = False
    # Reset config.chat to prevent stale tool allowlists from tests that call
    # setup_config_from_cli() (e.g. test_custom_tool_file_mixed_allowlist).
    # Without this, init_tools(allowlist=None) picks up the previous test's
    # chat.tools and loads only those tools, skipping standard tools like 'save'.
    from dataclasses import replace

    config = get_config()
    if config.chat is not None:
        set_config(replace(config, chat=None))


@pytest.fixture(autouse=True)
def cleanup_shell_after():
    """Clean up ShellSession after each test to prevent orphaned processes.

    This is critical for CI where orphaned bash processes can cause
    the test runner to hang during cleanup (Issue #910).
    """
    yield
    # Close shell if it exists (using ContextVar API)
    shell = shell_module._shell_var.get()
    if shell is not None:
        try:
            shell.close()
        except Exception as e:
            logger.warning(f"Error closing shell during test cleanup: {e}")
        shell_module._shell_var.set(None)


@pytest.fixture(autouse=True)
def cleanup_acp_health_monitor():
    """Stop the ACP health monitor and clear SessionManager state after each test.

    The health monitor is a module-level singleton thread. Without this fixture
    the first test that starts it leaks the thread for the rest of the xdist
    worker's life, racing with any test that writes to SessionManager._sessions
    directly and causing RuntimeError: dictionary changed size during iteration.
    """
    yield
    try:
        try:
            from gptme.server.session_step import stop_acp_health_monitor

            stop_acp_health_monitor()
        except ImportError:
            pass
        try:
            from gptme.server.session_models import SessionManager

            with SessionManager._lock:
                SessionManager._sessions.clear()
                SessionManager._conversation_sessions.clear()
        except ImportError:
            pass
    except Exception as e:
        logger.warning(f"Error during ACP health monitor cleanup: {e}")


@pytest.fixture(autouse=True)
def cleanup_subagents_after():
    """Clean up subagent threads and subprocesses after each test.

    Subagent threads are daemon threads that should die with the parent,
    but explicitly clearing them prevents potential issues.
    Subprocesses in subprocess mode need explicit termination.
    """
    yield
    # Interrupt in-progress LLM retry backoff sleeps before joining subagent
    # threads. The retry decorators sleep through exponential backoff (1+2+4+8s
    # = 15s+), far past the 2s join timeout below — without this, a thread
    # stuck in backoff leaks past teardown, later mutates sys.modules via lazy
    # imports, and races any main-thread iteration of it in an unrelated test
    # file ("dictionary changed size during iteration").
    # Scoped to the registered subagent threads only (not process-wide) so
    # unrelated background LLM work in the same pytest worker is unaffected.
    with _subagents_lock:
        # Make a copy to iterate over to avoid "dictionary changed size during iteration"
        # if another thread or setup_method modifies _subagents during cleanup
        subagents_copy = list(_subagents)
    for subagent in subagents_copy:
        # Subprocess launchers don't call backoff_wait(), so interrupt_thread()
        # would create a stale pre-signaled event whose ident could be reused by
        # a later real-LLM thread, aborting its first retry immediately.
        if subagent.thread is not None and subagent.execution_mode != "subprocess":
            interrupt_thread(subagent.thread)
    # Use try/finally so _subagents.clear() and _reset_slot_sem() always run
    # even if pytest-timeout interrupts the join/terminate phase.  The 5s
    # timeouts used here (thread join + process wait) together with pytest's
    # 10s teardown limit left no room — if the thread was still alive the full
    # sequence could take exactly 10s, triggering the timeout and leaving shared
    # globals dirty for the next test.  Shorter timeouts give headroom.
    try:
        for subagent in subagents_copy:
            # Clean up threads (2s cap — well under the 10s teardown limit)
            if subagent.thread is not None and subagent.thread.is_alive():
                subagent.thread.join(timeout=2.0)
            # Clean up subprocesses (subprocess mode)
            if subagent.process is not None and subagent.process.poll() is None:
                subagent.process.terminate()
                try:
                    subagent.process.wait(timeout=2.0)
                except Exception:
                    # Force kill if graceful termination fails
                    subagent.process.kill()
        # Leak check: warn loudly if a thread survived the join above so a
        # future flake ("dictionary changed size during iteration" in an
        # unrelated test) can be traced back to this test.
        for subagent in subagents_copy:
            if subagent.thread is not None and subagent.thread.is_alive():
                logger.warning(
                    "Subagent thread leaked past teardown: "
                    f"{subagent.agent_id} ({subagent.thread.name})"
                )
    finally:
        # Always reset shared state so subsequent tests start clean,
        # even if the join/terminate phase above was interrupted.
        with _subagents_lock:
            _subagents.clear()
        # Clear cached terminal results too; many tests intentionally reuse agent IDs
        # like "explorer"/"checker", and the queued-cancel guards now treat a stale
        # cached result as "already completed" and skip the new launch.
        with _subagent_results_lock:
            _subagent_results.clear()
        # Reset the concurrency semaphore so monitor threads from this test
        # don't starve the next test when running under xdist.  Monitor threads
        # capture the old semaphore object in their closure, so they release the
        # old sem (harmless) while the next test gets a fresh one.
        _reset_slot_sem()


@pytest.fixture
def temp_file():
    @contextmanager
    def _temp_file(content):
        # Create a temporary file with the given content
        temporary_file = tempfile.NamedTemporaryFile(delete=False, mode="w+")
        try:
            temporary_file.write(content)
            temporary_file.flush()
            temporary_file.close()
            yield temporary_file.name  # Yield the path to the temporary file
        finally:
            # Delete the temporary file to ensure cleanup
            if os.path.exists(temporary_file.name):
                os.unlink(temporary_file.name)

    return _temp_file


@pytest.fixture(autouse=True)
def init_(monkeypatch):
    # Pass MODEL from env explicitly to avoid picking up stale config.chat.model
    # values left by server tests. When _init_done is reset per-test, init_model()
    # re-runs and would otherwise read the contaminated config instead of the test
    # environment's MODEL. Server tests now use fully-qualified model names
    # (e.g. "openai/gpt-4o-mini") to prevent provider validation errors.
    model = os.environ.get("MODEL")
    # Ensure OPENAI_BASE_URL is set when using local/test model.
    # Use monkeypatch so the env var is reverted after each test and doesn't
    # leak into subsequent tests that use a non-local provider.
    if model and model.startswith("local/") and not os.environ.get("OPENAI_BASE_URL"):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:666")
    init(model, interactive=False, tool_allowlist=None, tool_format="markdown")


@pytest.fixture
def cleanup_tmux_sessions():
    """Clean up gptme_* tmux sessions before and after a test.

    This prevents cross-test contamination when tests run the gptme CLI
    which creates gptme_N sessions internally.
    """
    import subprocess

    def _cleanup():
        """Kill all gptme_* sessions except worker-specific ones."""
        import re

        # Match simple gptme_N sessions (N = digits only)
        # but NOT worker-specific ones like gptme_gw0_test_*
        simple_session_pattern = re.compile(r"^gptme_\d+$")

        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for session in result.stdout.strip().split("\n"):
                    session = session.strip()
                    if not session:
                        continue
                    if simple_session_pattern.match(session):
                        subprocess.run(
                            ["tmux", "kill-session", "-t", session],
                            check=False,
                            capture_output=True,
                            timeout=5,
                        )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # tmux not available or timed out

    # Cleanup before test
    _cleanup()
    yield
    # Cleanup after test
    _cleanup()


@pytest.fixture
def server_thread():
    """Start a server in a thread for testing."""
    # Skip if flask not installed
    pytest.importorskip(
        "flask", reason="flask not installed, install server extras (-E server)"
    )

    from gptme.server.app import create_app  # fmt: skip

    app = create_app()

    # Find a free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("localhost", 0))  # Let OS assign a free port
    port = s.getsockname()[1]
    s.close()

    # Configure the app for testing
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = f"localhost:{port}"

    # Start the server in a thread
    def run_server():
        with app.app_context():
            app.run(port=port, threaded=True)

    thread = threading.Thread(target=run_server)
    thread.daemon = True
    thread.start()

    # Give server time to start (so we don't get Connection Refused)
    time.sleep(0.5)

    return port  # Return the port to the test


@pytest.fixture
def client():
    from gptme.server.app import create_app  # fmt: skip

    app = create_app()

    # Create a test client without authentication by default
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def setup_conversation(server_thread):
    """Create a conversation and return its ID, session ID, and port."""
    port = server_thread
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    conversation_id = f"test-tools-{worker_id}-{uuid.uuid4().hex}"

    # Create conversation with custom system prompt
    # Use "@log" to create workspace in the conversation's log directory
    resp = requests.put(
        f"http://localhost:{port}/api/v2/conversations/{conversation_id}",
        headers={"Authorization": "Bearer test-token-for-server-thread"},
        json={
            "prompt": "You are an AI assistant for testing.",
            "config": {
                "chat": {
                    "workspace": "@log",
                }
            },
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    return port, conversation_id, session_id


@pytest.fixture()
def event_listener(setup_conversation):
    """Set up an event listener for the conversation."""
    port, conversation_id, session_id = setup_conversation
    events: queue.Queue = queue.Queue()
    event_sequence = []
    tool_id = None
    tool_output_received = False
    tool_executing_received = False

    def listen_for_events():
        nonlocal tool_id, tool_output_received, tool_executing_received
        resp = requests.get(
            f"http://localhost:{port}/api/v2/conversations/{conversation_id}/events?session_id={session_id}",
            headers={"Authorization": "Bearer test-token-for-server-thread"},
            stream=True,
        )
        try:
            for line in resp.iter_lines():
                if line:
                    line_str = line.decode("utf-8")
                    if line_str.startswith("data: "):
                        event_data = json.loads(line_str[6:])
                        events.put(event_data)

                        # Track event types
                        if "type" in event_data:
                            event_type = event_data["type"]
                            event_sequence.append(event_type)

                            if event_type == "tool_pending":
                                tool_id = event_data.get("tool_id")
                            elif event_type == "tool_executing":
                                tool_executing_received = True
        except Exception as e:
            events.put({"error": str(e)})
        finally:
            resp.close()

    event_thread = threading.Thread(target=listen_for_events)
    event_thread.daemon = True
    event_thread.start()

    return {
        "port": port,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "events": events,
        "event_sequence": event_sequence,
        "get_tool_id": lambda: tool_id,
        "is_tool_executing_received": lambda: tool_executing_received,
    }


@pytest.fixture
def mock_generation():
    """Create a mock generation with customizable output."""

    def create(responses: list[str]):
        response_iter = iter(responses)

        def mock_stream(
            messages, model, tools=None, max_tokens=None, temperature=None, top_p=None
        ):
            try:
                content = next(response_iter)
                # Yield the content as a single chunk that will be iterated over char by char
                yield [content]  # Wrap in list so it's only iterated once
            except StopIteration:
                yield ["No more responses"]

        return mock_stream

    return create


_LOCAL_FORM_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Test Form</title></head>
<body>
<form>
  <input name="q" type="text" placeholder="Search" />
  <button type="submit">Go</button>
</form>
</body>
</html>
"""


class _LocalFormHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves a simple form page."""

    def log_message(self, *args):  # suppress request logs in test output
        pass

    def do_GET(self):
        encoded = _LOCAL_FORM_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture()
def local_form_page():
    """Serve a minimal HTML form page locally and yield its URL.

    Replaces external URLs (e.g. duckduckgo.com) in browser tests so the
    suite stays hermetic and free of network flakiness.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _LocalFormHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def wait_for_event():
    """
    Wait for a specific event type in the event listener.

    Waiting for an event will mark all events before it as already awaited,
    so repeated calls don't wait for events before the last awaited one.
    """
    # max index awaited
    already_awaited = 0

    def wait(event_listener, event_type, timeout=10):
        nonlocal already_awaited
        start_time = time.time()
        seq = event_listener["event_sequence"]
        while time.time() - start_time < timeout:
            if event_type in seq[already_awaited:]:
                events_passed = seq[already_awaited:].index(event_type) + 1
                # print(seq[already_awaited : already_awaited + events_passed])
                already_awaited += events_passed
                # print(already_awaited)
                return True
            time.sleep(0.1)
        return False

    return wait
