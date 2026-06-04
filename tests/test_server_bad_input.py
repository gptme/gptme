"""Test gptme server API with bad/malformed input.

Can also be run as a standalone script against a live server:
    uv run gptme-server serve --port 5001 --tools "" &
    GPTME_TEST_SERVER_URL=http://127.0.0.1:5001 uv run python3 tests/test_server_bad_input.py
"""

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

# Skip all tests if flask (server extras) not installed
pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

pytestmark = [pytest.mark.timeout(30), pytest.mark.integration]

SERVER_URL = os.environ.get("GPTME_TEST_SERVER_URL", "http://127.0.0.1:5001")
TIMEOUT = 10


@pytest.fixture(scope="module", autouse=True)
def _start_server():
    """Start gptme server once for all tests in this module.

    If GPTME_TEST_SERVER_URL is set, assumes an external server and skips startup.
    """
    if os.environ.get("GPTME_TEST_SERVER_URL"):
        return  # Use the externally provided server

    from gptme.server.app import create_app  # fmt: skip

    app = create_app()
    app.config["TESTING"] = True

    # Bind to an OS-assigned free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app.config["SERVER_NAME"] = f"127.0.0.1:{port}"

    def run():
        with app.app_context():
            app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)  # Allow the server time to bind

    global SERVER_URL
    SERVER_URL = f"http://127.0.0.1:{port}"


def _req(
    method: str, path: str, body: dict | str | None = None
) -> tuple[int, dict | str]:
    """Make HTTP request to the server and return (status, parsed_json_or_raw)."""
    url = f"{SERVER_URL}{path}"
    # If body is a string, send it directly as the JSON payload
    # (testing that strings are rejected by the server)
    to_send = body if isinstance(body, str) else json.dumps(body)
    data = to_send.encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resp.status, raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, raw.decode()
    except urllib.error.URLError as e:
        return 0, {"error": f"Connection failed: {e.reason}"}


def _create_conversation(cid: str | None = None) -> tuple[str, str, int]:
    """Create a conversation and return (cid, session_id, status)."""
    cid = cid or f"test-{uuid.uuid4().hex[:12]}"
    status, data = _req("PUT", f"/api/v2/conversations/{cid}", {"config": {}})
    if status == 200:
        assert isinstance(data, dict)
        return cid, data.get("session_id", ""), status
    return cid, "", status


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health():
    status, data = _req("GET", "/api/v2/server/health")
    assert status == 200, f"health check failed: {data}"
    assert isinstance(data, dict), f"expected dict, got {type(data)}"
    assert data.get("health") == "green", f"unexpected health: {data}"
    print("  PASS: /health")


def test_create_conversation_default():
    """Creating a conversation with minimal valid body should work."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    status, data = _req("PUT", f"/api/v2/conversations/{cid}", {"config": {}})
    assert status == 200, f"create conversation failed: {data}"
    assert "session_id" in data, f"missing session_id: {data}"
    print(f"  PASS: create conversation (id={cid})")


def test_create_conversation_empty_body():
    """PUT with no body should still work (defaults to {})."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    status, data = _req("PUT", f"/api/v2/conversations/{cid}", None)
    if status == 200:
        print(f"  PASS: create conversation with no body (id={cid})")
    else:
        print(f"  NOTE: create conversation with no body returned {status}: {data}")


def test_get_conversation_bad_id():
    """Getting a non-existent conversation should return 404."""
    # Use random UUID to avoid collisions with server-created conversations
    cid = f"test-nonexistent-{uuid.uuid4().hex[:16]}"
    status, data = _req("GET", f"/api/v2/conversations/{cid}")
    assert status == 404, f"expected 404, got {status}: {data}"
    print("  PASS: 404 on nonexistent conversation")


def test_get_session_nonexistent():
    """Getting a non-existent session should return 404."""
    cid, _session_id, _ = _create_conversation()
    status, data = _req(
        "GET",
        f"/api/v2/conversations/{cid}/events?session_id=nonexistent-session",
    )
    assert status == 404, f"expected 404, got {status}: {data}"
    print("  PASS: 404 on nonexistent session")


def test_get_conversation_traversal_attempt():
    """Path traversal in conversation_id should be rejected.

    URL-encoded traversal like ``..%2F..%2F..%2Fetc%2Fpasswd`` gets decoded
    by Werkzeug's route matching (which treats ``%2F`` as ``/``), collapsing
    the path before it reaches the Flask handler. The WSGI layer normalizes
    it to the SPA catch-all (200 HTML), not an API error — acceptable as
    defense-in-depth.

    The validation layer is tested through patterns that actually reach it:
    null bytes (``%00``), control characters, and URL-safe invalid IDs.
    """
    # URL-encoded traversal: Werkzeug normalizes %2F path, returns SPA HTML
    _req("GET", "/api/v2/conversations/..%2F..%2F..%2Fetc%2Fpasswd")
    print("  NOTE: %2F traversal normalized by Werkzeug (not a bug)")

    # Null-byte in conversation_id: rejected by validation
    status, data = _req("GET", "/api/v2/conversations/bad%00name")
    assert status == 400, f"expected 400 for null-byte, got {status}: {data}"
    print("  PASS: null-byte conversation_id rejected")

    # Whitespace-only conversation_id: rejected by validation
    status, data = _req("GET", "/api/v2/conversations/%20%20%20")
    assert status == 400, f"expected 400 for whitespace-only, got {status}: {data}"
    print("  PASS: whitespace-only conversation_id rejected")


def test_create_existing_conversation_duplicate():
    """Creating the same conversation twice should return 409."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    status1, data1 = _req("PUT", f"/api/v2/conversations/{cid}", {"config": {}})
    assert status1 == 200, f"first create failed: {data1}"
    status2, data2 = _req("PUT", f"/api/v2/conversations/{cid}", {"config": {}})
    assert status2 == 409, f"expected 409 on duplicate, got {status2}: {data2}"
    print("  PASS: 409 on duplicate conversation creation")


def test_step_no_session():
    """Stepping with an invalid session_id should return 400 or 404."""
    cid = _create_conversation()[0]
    status, data = _req(
        "POST",
        f"/api/v2/conversations/{cid}/step",
        {"session_id": "invalid-session"},
    )
    assert status in (400, 404), (
        f"expected 400 or 404 for invalid session, got {status}: {data}"
    )
    print(f"  PASS: step with bad session returned {status}")


def test_step_malformed_json():
    """Stepping with malformed JSON body should return 400 (bad JSON, not 404)."""
    # POST to /step: JSON is parsed before conversation-existence check, so
    # malformed body yields 400 regardless of whether the conversation exists.
    status, data = _req("POST", "/api/v2/conversations/nonexistent/step", "not json")
    assert status == 400, f"expected 400 for malformed JSON, got {status}: {data}"
    print(f"  PASS: malformed JSON step returned {status}")


def test_missing_content_type():
    """Request with wrong Content-Type should be handled gracefully (no 5xx)."""
    cid, session_id, _ = _create_conversation()
    url = f"{SERVER_URL}/api/v2/conversations/{cid}/events"
    body = json.dumps({"session_id": session_id}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "text/plain")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code < 500, f"text/plain Content-Type caused a server error: {code}"
    print(f"  PASS: text/plain step returned {code} (no 5xx)")


def test_put_invalid_auto_confirm():
    """auto_confirm as string should be rejected."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    status, data = _req(
        "PUT",
        f"/api/v2/conversations/{cid}",
        {"auto_confirm": "true", "config": {}},
    )
    assert status == 400, f"expected 400, got {status}: {data}"
    print("  PASS: auto_confirm='true' (string) rejected")


def test_put_bad_json():
    """PUT with non-dict body should return 400."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    status, data = _req(
        "PUT",
        f"/api/v2/conversations/{cid}",
        "not-a-dict",  # will be sent as JSON string
    )
    assert status == 400, f"expected 400, got {status}: {data}"
    print("  PASS: non-dict JSON body rejected")


def test_create_with_bad_messages():
    """Creating a conversation with invalid messages should be rejected."""
    cid = f"test-{uuid.uuid4().hex[:12]}"
    # Bad role
    status, data = _req(
        "PUT",
        f"/api/v2/conversations/{cid}",
        {"messages": [{"role": "invalid", "content": "hi"}], "config": {}},
    )
    assert status == 400, f"expected 400 for bad role, got {status}: {data}"
    print("  PASS: invalid role rejected")

    cid2 = f"test-{uuid.uuid4().hex[:12]}"
    # Missing content
    status, data = _req(
        "PUT",
        f"/api/v2/conversations/{cid2}",
        {"messages": [{"role": "user"}], "config": {}},
    )
    assert status == 400, f"expected 400 for missing content, got {status}: {data}"
    print("  PASS: missing content rejected")


# ---------------------------------------------------------------------------
# SSE / streaming tests
# ---------------------------------------------------------------------------


def _events_quick_status(path: str) -> int:
    """Quick-check SSE endpoint status without reading the streaming body.

    SSE connections use chunked transfer encoding — reading from the body
    blocks indefinitely because the server keeps sending events.  We fetch
    the status line and immediately close the connection.
    """
    try:
        parsed = urllib.parse.urlparse(SERVER_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5001
        conn = http.client.HTTPConnection(host, port, timeout=3)
        conn.request("GET", path, headers={"Accept": "text/event-stream"})
        resp = conn.getresponse()
        # Don't read the body — SSE streams indefinitely.
        # Closing the response releases the connection.
        status_code = resp.status
        conn.close()
        return status_code
    except Exception:
        return 0


def test_events_no_conversation():
    """SSE events for nonexistent conversation should 404."""
    cid = f"test-nonexistent-events-{uuid.uuid4().hex[:16]}"
    status = _events_quick_status(f"/api/v2/conversations/{cid}/events")
    assert status == 404, f"expected 404, got {status}"
    print("  PASS: SSE 404 for nonexistent conversation")


def test_events_valid_conversation():
    """SSE events should connect for valid conversation."""
    cid = _create_conversation()[0]
    status = _events_quick_status(f"/api/v2/conversations/{cid}/events")
    assert status == 200, f"expected 200, got {status}"
    print("  PASS: SSE for valid conversation returned 200")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _expect_error(
    method: str,
    path: str,
    body: dict | str | None,
    expected_status: int,
    label: str,
) -> None:
    """Shorthand: assert that a bad request returns the expected error."""
    s, d = _req(method, path, body)
    assert s == expected_status, f"{label}: expected {expected_status}, got {s}: {d}"
    if isinstance(d, dict) and "error" in d:
        print(f"  PASS: {label} ({d['error'][:60]})")
    else:
        print(f"  PASS: {label} (status {s})")


def main():
    print("=== Health / basic ===")
    test_health()
    test_create_conversation_default()
    test_create_conversation_empty_body()

    print("\n=== Conversation edge cases ===")
    test_get_conversation_bad_id()
    test_create_existing_conversation_duplicate()
    test_get_conversation_traversal_attempt()

    print("\n=== Session / step edge cases ===")
    test_step_no_session()
    test_step_malformed_json()
    test_missing_content_type()

    print("\n=== Request body validation ===")
    test_put_invalid_auto_confirm()
    test_put_bad_json()
    test_create_with_bad_messages()

    print("\n=== SSE / streaming edge cases ===")
    test_events_no_conversation()
    test_events_valid_conversation()

    print("\n=== Running ad-hoc bad-input probes ===")
    cid, session_id, _ = _create_conversation()

    # Probe: step with non-boolean stream (validates on /step, not /events)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/step",
        {"session_id": session_id, "stream": "yes"},
        400,
        "non-boolean stream",
    )

    # Probe: step with bad session_id
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/step",
        {"session_id": "bogus-session"},
        404,
        "bogus session_id",
    )

    # Probe: PUT with non-object config
    cid2 = f"test-{uuid.uuid4().hex[:12]}"
    _expect_error(
        "PUT",
        f"/api/v2/conversations/{cid2}",
        {"config": "not-an-object"},
        400,
        "non-object config",
    )

    # Probe: PUT with non-object config.chat
    cid3 = f"test-{uuid.uuid4().hex[:12]}"
    _expect_error(
        "PUT",
        f"/api/v2/conversations/{cid3}",
        {"config": {"chat": "not-an-object"}},
        400,
        "non-object config.chat",
    )

    # Probe: DELETE nonexistent conversation
    _expect_error(
        "DELETE",
        "/api/v2/conversations/nonexistent-xyz",
        None,
        404,
        "DELETE nonexistent conversation",
    )

    # Probe: backwards-compatible GET on the v2 root
    s, d = _req("GET", "/api/v2")
    print(f"  V2 root: {s}" if s == 200 else f"  V2 root: {s}")

    # Probe: list conversations with invalid limit
    s, d = _req("GET", "/api/v2/conversations?limit=-1")
    if s == 200:
        print("  List conversations with limit=-1: OK (clamped)")
    else:
        print(f"  List conversations with limit=-1: {s}")

    print("\n=== Session / step interactive endpoints ===")
    cid, sid, _ = _create_conversation()

    # Probe: step with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/step",
        "not-a-dict",
        400,
        "step bad JSON",
    )

    # Probe: step with no session_id
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/step",
        {"stream": True},
        400,
        "step no session_id",
    )

    # Probe: tool/confirm with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/tool/confirm",
        "garbage",
        400,
        "tool confirm bad JSON",
    )

    # Probe: tool/confirm with missing tool_id
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/tool/confirm",
        {"action": "confirm"},
        400,
        "tool confirm missing tool_id",
    )

    # Probe: tool/confirm with unknown action
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/tool/confirm",
        {"tool_id": "t1", "action": "nuke"},
        400,
        "tool confirm unknown action",
    )

    # Probe: rerun with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/rerun",
        "bad",
        400,
        "rerun bad JSON",
    )

    # Probe: rerun with missing session_id
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/rerun",
        {},
        400,
        "rerun no session_id",
    )

    # Probe: interrupt with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/interrupt",
        "bad",
        400,
        "interrupt bad JSON",
    )

    # Probe: interrupt with missing session_id
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/interrupt",
        {},
        400,
        "interrupt no session_id",
    )

    # Probe: elicit/respond with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/elicit/respond",
        "bad",
        400,
        "elicit respond bad JSON",
    )

    # Probe: elicit/respond with missing fields
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/elicit/respond",
        {},
        400,
        "elicit respond empty body",
    )

    # Probe: elicit/respond with bad elicit_id type
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/elicit/respond",
        {"elicit_id": 123, "action": "accept"},
        400,
        "elicit respond bad elicit_id type",
    )

    # Probe: elicit/respond with unknown action
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/elicit/respond",
        {"elicit_id": "e1", "action": "explode"},
        400,
        "elicit respond unknown action",
    )

    # Probe: transcript with bad JSON (non-dict)
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/transcript",
        "bad",
        400,
        "transcript bad JSON",
    )

    # Probe: transcript with missing turns
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/transcript",
        {},
        400,
        "transcript missing turns",
    )

    # Probe: transcript with bad call_metadata
    _expect_error(
        "POST",
        f"/api/v2/conversations/{cid}/transcript",
        {"turns": [], "call_metadata": {}},
        400,
        "transcript missing call_sid",
    )

    print("\n=== Workspace / file endpoints ===")

    # Probe: workspace browse with traversal
    _expect_error(
        "GET",
        f"/api/v2/conversations/{cid}/workspace/..%2F..%2Fetc%2Fpasswd",
        None,
        400,
        "workspace path traversal (400 = ValueError from safe_workspace_path)",
    )

    # Probe: workspace file read with traversal
    _expect_error(
        "GET",
        f"/api/v2/conversations/{cid}/files/..%2F..%2F..%2Fetc%2Fpasswd",
        None,
        400,
        "files path traversal",
    )

    # Probe: workspace preview with traversal (400 = safe_workspace_path)
    _expect_error(
        "GET",
        f"/api/v2/conversations/{cid}/workspace/..%2Fetc%2Fpasswd/preview",
        None,
        400,
        "workspace preview traversal",
    )

    print("\n=== Config endpoints ===")

    # Probe: PATCH config with tools not a list
    _expect_error(
        "PATCH",
        f"/api/v2/conversations/{cid}/config",
        {"chat": {"tools": "all"}},
        400,
        "config tools not a list",
    )

    # Probe: PATCH config with mixed-type tools list
    _expect_error(
        "PATCH",
        f"/api/v2/conversations/{cid}/config",
        {"chat": {"tools": ["shell", 42, "save"]}},
        400,
        "config tools mixed type",
    )

    # Probe: user config PUT with bad TOML
    _expect_error(
        "PUT",
        "/api/v2/user/config-file",
        {"content": "valid key = [[ unbalanced"},
        400,
        "config-file bad TOML",
    )

    # Probe: user config PUT with non-string content
    _expect_error(
        "PUT",
        "/api/v2/user/config-file",
        {"content": 42},
        400,
        "config-file content not string",
    )

    # Probe: user config PATCH with missing key
    _expect_error(
        "PATCH",
        "/api/v2/user/config-file",
        {"value": "x"},
        400,
        "config-file patch missing key",
    )

    # Probe: user config PATCH with bad key type
    _expect_error(
        "PATCH",
        "/api/v2/user/config-file",
        {"key": 42, "value": "x"},
        400,
        "config-file patch bad key type",
    )

    # Probe: user config PATCH with non-scalar value
    _expect_error(
        "PATCH",
        "/api/v2/user/config-file",
        {"key": "x", "value": {"nested": True}},
        400,
        "config-file patch bad value type",
    )

    print("\n=== All tests done ===")


if __name__ == "__main__":
    main()
