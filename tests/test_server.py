import copy
import random
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip

from gptme.server.openapi_docs import API_VERSION, CONTRACT_REVISION


@pytest.fixture
def conv(client: FlaskClient):
    convname = f"test-server-{random.randint(0, 1000000)}"
    response = client.put(f"/api/v2/conversations/{convname}", json={})
    assert response.status_code == 200
    return convname


def test_root(client: FlaskClient):
    response = client.get("/")
    assert response.status_code == 200


def test_api_root(client: FlaskClient):
    response = client.get("/api/v2")
    assert response.status_code == 200
    data = response.get_json()
    assert "message" in data
    assert data["api_version"] == API_VERSION
    assert data["contract_revision"] == CONTRACT_REVISION
    assert response.headers.get("X-API-Version") == str(API_VERSION)


def test_api_config_no_project(client: FlaskClient, monkeypatch):
    """GET /api/v2/config returns empty agent dict when no gptme.toml is present."""
    import gptme.server.api_v2 as api_v2_module
    from gptme.config import get_config as original_get_config

    def mock_get_config_no_project():
        cfg = copy.copy(original_get_config())
        cfg.project = None
        return cfg

    monkeypatch.setattr(api_v2_module, "get_config", mock_get_config_no_project)

    response = client.get("/api/v2/config")
    assert response.status_code == 200
    data = response.get_json()
    assert "agent" in data
    # Without a workspace gptme.toml, agent info is empty
    assert data["agent"] == {}


def test_api_config_with_agent_urls(tmp_path, client: FlaskClient, monkeypatch):
    """GET /api/v2/config includes agent.urls when gptme.toml has [agent.urls]."""

    from gptme.config import get_project_config

    toml_content = """
[agent]
name = "testbot"

[agent.urls]
dashboard = "https://testbot.example.com/"
repo = "https://github.com/example/testbot"
"""
    toml_file = tmp_path / "gptme.toml"
    toml_file.write_text(toml_content)

    # Monkeypatch the get_config reference inside api_v2.py
    import gptme.server.api_v2 as api_v2_module
    from gptme.config import get_config as original_get_config

    def mock_get_config():
        cfg = copy.copy(original_get_config())
        cfg.project = get_project_config(tmp_path)
        return cfg

    monkeypatch.setattr(api_v2_module, "get_config", mock_get_config)

    response = client.get("/api/v2/config")
    assert response.status_code == 200
    data = response.get_json()
    assert data["agent"]["name"] == "testbot"
    assert data["agent"]["urls"]["dashboard"] == "https://testbot.example.com/"
    assert data["agent"]["urls"]["repo"] == "https://github.com/example/testbot"


def test_api_conversation_list(client: FlaskClient):
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200


def test_api_conversation_list_with_limit(client: FlaskClient):
    response = client.get("/api/v2/conversations?limit=5")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) <= 5


def test_api_conversation_list_paginated_cursor_preserves_equal_timestamps(
    client: FlaskClient, monkeypatch
):
    """Composite cursors must not drop conversations at equal-timestamp page boundaries."""
    import gptme.server.api_v2 as api_v2_module
    from gptme.logmanager import ConversationMeta

    api_v2_module._invalidate_conversations_cache()

    base = ConversationMeta(
        id="seed",
        name="seed",
        path="/tmp/seed/conversation.jsonl",
        created=0.0,
        modified=0.0,
        messages=1,
        branches=1,
        workspace="",
        agent_name=None,
        agent_path=None,
        agent_avatar=None,
        agent_urls=None,
        model=None,
        total_cost=0.0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_cache_read_tokens=0,
        last_message_role="user",
        last_message_preview="preview",
    )
    conversations = [
        replace(base, id="c", name="c", modified=99.0),
        replace(base, id="b", name="b", modified=99.0),
        replace(base, id="a", name="a", modified=99.0),
        replace(base, id="d", name="d", modified=98.0),
    ]

    monkeypatch.setattr(
        api_v2_module,
        "get_user_conversations",
        lambda detail=False: iter(conversations),
    )

    page1 = client.get("/api/v2/conversations?paginated=1&limit=2")
    assert page1.status_code == 200
    page1_data = page1.get_json()
    assert [item["id"] for item in page1_data["conversations"]] == ["c", "b"]
    assert page1_data["next_cursor"] == "99|b"

    page2 = client.get(
        f"/api/v2/conversations?paginated=1&limit=2&cursor={page1_data['next_cursor']}"
    )
    assert page2.status_code == 200
    page2_data = page2.get_json()
    assert [item["id"] for item in page2_data["conversations"]] == ["a", "d"]
    assert page2_data["next_cursor"] is None


def test_api_conversation_search(client: FlaskClient, tmp_path, monkeypatch):
    """Test that search parameter filters conversations by name."""

    # Use a temporary logs directory to avoid scanning real conversations
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    # Create a conversation directory (avoid "test-" prefix which is filtered)
    conv_dir = tmp_path / "my-search-target"
    conv_dir.mkdir()
    conv_file = conv_dir / "conversation.jsonl"
    conv_file.write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    # Create another conversation that should NOT match
    other_dir = tmp_path / "other-conversation"
    other_dir.mkdir()
    other_file = other_dir / "conversation.jsonl"
    other_file.write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    # Search should find only the matching one
    response = client.get("/api/v2/conversations?search=search-target")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "my-search-target"


def test_api_conversation_search_no_results(client: FlaskClient, tmp_path, monkeypatch):
    """Test that search with non-matching query returns empty list."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    response = client.get(
        "/api/v2/conversations?search=zzz-nonexistent-conversation-xyz"
    )
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_api_conversation_search_case_insensitive(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test that search is case-insensitive."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    conv_dir = tmp_path / "MySearchConversation"
    conv_dir.mkdir()
    conv_file = conv_dir / "conversation.jsonl"
    conv_file.write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    # Search with different casing
    response = client.get("/api/v2/conversations?search=mysearchconversation")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "MySearchConversation"


def test_api_conversation_search_q_param(client: FlaskClient, tmp_path, monkeypatch):
    """Test that the ?q= param filters conversations (primary alias for ?search=)."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    conv_dir = tmp_path / "q-param-target"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )
    other_dir = tmp_path / "other-conversation"
    other_dir.mkdir()
    (other_dir / "conversation.jsonl").write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    response = client.get("/api/v2/conversations?q=q-param-target")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "q-param-target"


def test_api_conversation_search_q_param_takes_precedence(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test that ?q= takes precedence over ?search= when both are provided."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    conv_dir = tmp_path / "q-wins"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    # q= matches; search= does not — q= should win
    response = client.get("/api/v2/conversations?q=q-wins&search=no-match-here")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "q-wins"


def test_api_conversation_search_empty_q_overrides_search(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Test that explicit empty ?q= overrides ?search= (not a falsy-or fallthrough)."""
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)

    conv_dir = tmp_path / "search-only"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    # ?q= explicitly empty + ?search=search-only: empty q= should win → no filter → returns all
    response = client.get("/api/v2/conversations?q=&search=search-only")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    # empty q= means "no filter", so all conversations are returned (not just search-only match)
    assert len(data) >= 1


def test_api_conversation_list_detail_flag(client: FlaskClient, tmp_path, monkeypatch):
    """Test that detail=true returns cost/token stats while default (false) zeroes them."""
    import json

    import gptme.server.api_v2 as api_v2_module

    api_v2_module._invalidate_conversations_cache()

    empty_logs_dir = tmp_path / "empty-logs"
    empty_logs_dir.mkdir()

    # Prime the list cache against a different logs dir first. Without scoping
    # the cache to the active logs dir, the second request below reuses the
    # stale empty result instead of scanning the populated tmp_path.
    monkeypatch.setattr(
        "gptme.logmanager.conversations.get_logs_dir", lambda: empty_logs_dir
    )
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    assert response.get_json() == []

    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: tmp_path)
    # Also patch api_v2's own get_logs_dir import used for the conversations cache key.
    # Without this, prior-test cache data (keyed on the real logs dir) hits when
    # api_v2.get_logs_dir() still returns the real path even though the scanner uses tmp_path.
    monkeypatch.setattr("gptme.server.api_v2.get_logs_dir", lambda: tmp_path)

    # Create a conversation with an assistant message that carries usage info
    conv_dir = tmp_path / "stats-conversation"
    conv_dir.mkdir()
    conv_file = conv_dir / "conversation.jsonl"
    # Build a conversation > _TAIL_BYTES (8192) so the fast path actually activates
    # for large files when detail=False. Pad with many user turns first.
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"},
    ]
    messages.extend(
        {
            "role": "user",
            "content": "x" * 200,
            "timestamp": f"2026-01-01T00:01:{i:02d}",
        }
        for i in range(40)
    )
    # The metadata-bearing assistant message (carries cost/token stats)
    messages.append(
        {
            "role": "assistant",
            "content": "reply",
            "timestamp": "2026-01-02T00:00:00",
            "metadata": {
                "model": "claude-3-5-haiku",
                "cost": 0.0001,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )
    conv_file.write_text("\n".join(json.dumps(m) for m in messages) + "\n")
    assert conv_file.stat().st_size > 8192, (
        "fixture must be > _TAIL_BYTES to trigger fast path"
    )

    # Default (detail=false) should return zeroed cost/token stats but keep
    # the message count and `last_updated` (both come from the cheap tail scan).
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    # `messages` and `message_count` are stable aliases for the count and are
    # always populated by the tail scan, regardless of `detail`.
    assert data[0]["messages"] == len(messages)
    assert data[0]["message_count"] == len(messages)
    # `last_updated` is the stable alias for `modified`.
    assert data[0]["last_updated"] == data[0]["modified"]
    # Cost/token stats are zeroed in fast mode.
    assert data[0]["total_cost"] == 0.0
    assert data[0]["total_input_tokens"] == 0
    assert data[0]["total_output_tokens"] == 0

    # detail=true should return actual cost/token stats alongside the count.
    response = client.get("/api/v2/conversations?detail=true")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["messages"] == len(messages)
    assert data[0]["message_count"] == len(messages)
    assert data[0]["total_cost"] > 0
    assert data[0]["total_input_tokens"] > 0
    assert data[0]["total_output_tokens"] > 0


def test_api_conversation_list_cache_tracks_patched_logs_dir(
    client: FlaskClient, tmp_path, monkeypatch
):
    """Changing the conversations logs dir should bypass stale cached list responses."""
    import gptme.server.api_v2 as api_v2_module

    first_logs_dir = tmp_path / "logs-a"
    second_logs_dir = tmp_path / "logs-b"
    first_logs_dir.mkdir()
    second_logs_dir.mkdir()

    api_v2_module._invalidate_conversations_cache()

    monkeypatch.setattr(
        "gptme.logmanager.conversations.get_logs_dir", lambda: first_logs_dir
    )
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    assert response.get_json() == []

    conv_dir = second_logs_dir / "cache-target"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text(
        '{"role": "system", "content": "hello", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    monkeypatch.setattr(
        "gptme.logmanager.conversations.get_logs_dir", lambda: second_logs_dir
    )
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "cache-target"


def test_update_conversation_in_cache_partial_update(tmp_path):
    """_update_conversation_in_cache must refresh one entry without touching others.

    During LLM streaming (many rapid message additions to one conversation),
    each POST must not wipe the full cache.  The partial-update helper must:
    1. Keep _conversations_cache not None.
    2. Update the modified conversation's message count and preview.
    3. Leave other conversation entries unchanged.
    """
    import time

    import gptme.server.api_v2 as api_v2_module
    from gptme.logmanager import ConversationMeta

    # Set up two minimal conversation files
    active_dir = tmp_path / "active-conv"
    other_dir = tmp_path / "other-conv"
    active_dir.mkdir()
    other_dir.mkdir()
    active_file = active_dir / "conversation.jsonl"
    other_file = other_dir / "conversation.jsonl"
    active_file.write_text(
        '{"role": "system", "content": "sys", "timestamp": "2026-01-01T00:00:00"}\n'
    )
    other_file.write_text(
        '{"role": "system", "content": "other", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    def _make_meta(conv_id: str, conv_file, n_msgs: int) -> ConversationMeta:
        return ConversationMeta(
            id=conv_id,
            name=conv_id,
            path=str(conv_file),
            created=0.0,
            modified=0.0,
            messages=n_msgs,
            branches=1,
            workspace="",
            agent_name=None,
            agent_path=None,
            agent_avatar=None,
            agent_urls=None,
            model=None,
        )

    # Seed the cache as if a GET just happened
    api_v2_module._conversations_cache = [
        _make_meta("active-conv", active_file, 1),
        _make_meta("other-conv", other_file, 1),
    ]
    api_v2_module._conversations_cache_logs_dir = tmp_path
    api_v2_module._conversations_cache_time = time.monotonic()

    # Append a message to the active conversation on disk
    with active_file.open("a") as f:
        f.write(
            '{"role": "user", "content": "streaming msg", "timestamp": "2026-01-01T00:00:01"}\n'
        )

    # Partial update
    api_v2_module._update_conversation_in_cache("active-conv")

    # Cache must remain warm
    assert api_v2_module._conversations_cache is not None

    # Active conv entry must be updated
    updated_active = next(
        c for c in api_v2_module._conversations_cache if c.id == "active-conv"
    )
    assert updated_active.messages == 2
    assert updated_active.last_message_role == "user"
    assert updated_active.last_message_preview is not None
    assert "streaming msg" in (updated_active.last_message_preview or "")

    # Other conv entry must be untouched
    other = next(c for c in api_v2_module._conversations_cache if c.id == "other-conv")
    assert other.messages == 1


def test_update_conversation_in_cache_missing_conv_invalidates(tmp_path):
    """If the conversation is deleted, partial update must fall back to full invalidation."""
    import time

    import gptme.server.api_v2 as api_v2_module
    from gptme.logmanager import ConversationMeta

    gone_dir = tmp_path / "gone-conv"
    gone_dir.mkdir()
    gone_file = gone_dir / "conversation.jsonl"
    gone_file.write_text(
        '{"role": "system", "content": "sys", "timestamp": "2026-01-01T00:00:00"}\n'
    )

    api_v2_module._conversations_cache = [
        ConversationMeta(
            id="gone-conv",
            name="gone-conv",
            path=str(gone_file),
            created=0.0,
            modified=0.0,
            messages=1,
            branches=1,
            workspace="",
            agent_name=None,
            agent_path=None,
            agent_avatar=None,
            agent_urls=None,
            model=None,
        )
    ]
    api_v2_module._conversations_cache_logs_dir = tmp_path
    api_v2_module._conversations_cache_time = time.monotonic()

    # Delete the conversation file before partial update
    gone_file.unlink()
    gone_dir.rmdir()

    api_v2_module._update_conversation_in_cache("gone-conv")

    # Cache must be invalidated so next GET rebuilds without the deleted conv
    assert api_v2_module._conversations_cache is None


def test_api_conversation_get(conv, client: FlaskClient):
    response = client.get(f"/api/v2/conversations/{conv}")
    assert response.status_code == 200


def test_api_conversation_get_limit(conv, client: FlaskClient):
    # Populate with some messages
    for i in range(5):
        client.post(
            f"/api/v2/conversations/{conv}",
            json={"role": "user", "content": f"msg {i}"},
        )
    # Get full count first (no limit)
    full = client.get(f"/api/v2/conversations/{conv}").get_json()
    total = len(full["log"])
    assert total >= 5  # at least our 5 messages

    # Fetch last 2
    response = client.get(f"/api/v2/conversations/{conv}?limit=2")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["log"]) == 2
    assert data["total_messages"] == total
    assert data["has_more"] is True
    assert data["before"] == total - 2  # cursor for next older page
    # Last message must be the last one we posted
    assert data["log"][-1]["content"] == "msg 4"


def test_api_conversation_get_limit_no_more(conv, client: FlaskClient):
    # Fetch with limit larger than total — gets everything
    full = client.get(f"/api/v2/conversations/{conv}").get_json()
    total = len(full["log"])

    response = client.get(f"/api/v2/conversations/{conv}?limit=10000")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["log"]) == total
    assert data["total_messages"] == total
    assert data["has_more"] is False
    assert "before" not in data


def test_api_conversation_get_before_cursor(conv, client: FlaskClient):
    # Populate with enough messages that we need at least 3 pages of 2
    for i in range(10):
        client.post(
            f"/api/v2/conversations/{conv}",
            json={"role": "user", "content": f"msg {i}"},
        )
    # First page: last 2
    r1 = client.get(f"/api/v2/conversations/{conv}?limit=2")
    data1 = r1.get_json()
    assert data1["has_more"] is True
    cursor = data1["before"]
    assert cursor > 0

    # Second page: 2 older messages
    r2 = client.get(f"/api/v2/conversations/{conv}?limit=2&before={cursor}")
    data2 = r2.get_json()
    assert len(data2["log"]) == 2
    # Page-2 messages must be entirely before page-1 messages (non-overlapping).
    # Content is deterministic (we posted "msg 0"…"msg 9"), so compare by content.
    page1_contents = {m["content"] for m in data1["log"]}
    page2_contents = {m["content"] for m in data2["log"]}
    assert page1_contents.isdisjoint(page2_contents)


def test_api_conversation_get_limit_invalid(conv, client: FlaskClient):
    for bad in ["notanumber", "0", "-5", "-1"]:
        response = client.get(f"/api/v2/conversations/{conv}?limit={bad}")
        assert response.status_code == 400, f"expected 400 for limit={bad}"


def test_api_conversation_get_before_invalid(conv, client: FlaskClient):
    # negative before
    response = client.get(f"/api/v2/conversations/{conv}?limit=5&before=-1")
    assert response.status_code == 400
    # before without limit is not allowed
    response = client.get(f"/api/v2/conversations/{conv}?before=5")
    assert response.status_code == 400


def test_api_conversation_get_no_pagination_fields_without_limit(
    conv, client: FlaskClient
):
    # Without limit, response must not include pagination fields (backwards compat)
    client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello"},
    )
    response = client.get(f"/api/v2/conversations/{conv}")
    data = response.get_json()
    assert "has_more" not in data
    assert "total_messages" not in data
    assert "before" not in data


def test_api_conversation_get_etag_pagination_isolation(conv, client: FlaskClient):
    # Populate messages
    for i in range(5):
        client.post(
            f"/api/v2/conversations/{conv}",
            json={"role": "user", "content": f"msg {i}"},
        )

    full_r = client.get(f"/api/v2/conversations/{conv}")
    full_etag = full_r.headers["ETag"]

    page1_r = client.get(f"/api/v2/conversations/{conv}?limit=2")
    page1_etag = page1_r.headers["ETag"]

    cursor = page1_r.get_json()["before"]
    page2_r = client.get(f"/api/v2/conversations/{conv}?limit=2&before={cursor}")
    page2_etag = page2_r.headers["ETag"]

    # All three ETags must be distinct — different slices, different validators.
    assert full_etag != page1_etag
    assert page1_etag != page2_etag

    # Repeating the same paginated request with its own ETag must return 304.
    r304 = client.get(
        f"/api/v2/conversations/{conv}?limit=2",
        headers={"If-None-Match": page1_etag},
    )
    assert r304.status_code == 304

    # Using the full-response ETag for a paginated request must NOT return 304.
    r200 = client.get(
        f"/api/v2/conversations/{conv}?limit=2",
        headers={"If-None-Match": full_etag},
    )
    assert r200.status_code == 200

    # Using the full-response ETag for a paginated request must NOT return 304.
    r200 = client.get(
        f"/api/v2/conversations/{conv}?limit=2",
        headers={"If-None-Match": full_etag},
    )
    assert r200.status_code == 200


def test_api_conversation_get_limit_capped(conv, client: FlaskClient):
    """limit > 10000 is silently capped to 10000."""
    for i in range(5):
        client.post(
            f"/api/v2/conversations/{conv}",
            json={"role": "user", "content": f"msg {i}"},
        )
    full = client.get(f"/api/v2/conversations/{conv}").get_json()
    total = len(full["log"])

    # Request limit=99999 — must be capped to 10000 (effectively unlimited for this test)
    response = client.get(f"/api/v2/conversations/{conv}?limit=99999")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["log"]) == total  # all messages returned (limit >> total)
    assert data["total_messages"] == total
    assert data["has_more"] is False


def test_api_conversation_get_before_larger_than_total(conv, client: FlaskClient):
    """before > total is clamped to total, returning messages before cursor."""
    for i in range(5):
        client.post(
            f"/api/v2/conversations/{conv}",
            json={"role": "user", "content": f"msg {i}"},
        )
    full = client.get(f"/api/v2/conversations/{conv}").get_json()
    total = len(full["log"])

    # Request limit=2, before=99999 (way past the end)
    response = client.get(f"/api/v2/conversations/{conv}?limit=2&before=99999")
    assert response.status_code == 200
    data = response.get_json()
    # Should return 2 messages (the last 2, since before clamped to total)
    assert len(data["log"]) == 2
    assert data["total_messages"] == total
    assert data["has_more"] is True  # there ARE older messages
    cursor = data["before"]
    assert cursor == total - 2  # total - limit

    # Page back from that cursor — should get 2 more
    r2 = client.get(f"/api/v2/conversations/{conv}?limit=2&before={cursor}")
    data2 = r2.get_json()
    assert len(data2["log"]) == 2


def test_api_conversation_get_before_invalid_non_integer(conv, client: FlaskClient):
    """Non-integer before returns 400."""
    for bad in ["notanumber", "1.5", ""]:
        response = client.get(f"/api/v2/conversations/{conv}?limit=5&before={bad}")
        assert response.status_code == 400, f"expected 400 for before={bad}"


def test_api_conversation_post(conv, client: FlaskClient):
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello"},
    )
    assert response.status_code == 200


def test_debug_errors_disabled(monkeypatch):
    """Test that debug errors are disabled by default."""
    from gptme.server.api_v2_common import _is_debug_errors_enabled

    # Clear the env var to test default behavior
    monkeypatch.delenv("GPTME_DEBUG_ERRORS", raising=False)
    assert _is_debug_errors_enabled() is False


def test_debug_errors_enabled(monkeypatch):
    """Test that debug errors can be enabled via environment variable."""
    from gptme.server.api_v2_common import _is_debug_errors_enabled

    # Test various truthy values
    for value in ["1", "true", "TRUE", "yes", "YES"]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is True, f"Failed for value: {value}"

    # Test falsy values
    for value in ["0", "false", "no", ""]:
        monkeypatch.setenv("GPTME_DEBUG_ERRORS", value)
        assert _is_debug_errors_enabled() is False, (
            f"Should be False for value: {value}"
        )


def test_default_model_propagation():
    """Test that the server's default model is propagated to request contexts.

    This tests the before_request hook that propagates the default model
    from the startup context to each request context (ContextVar fix).
    """
    # Set a default model before creating the app (simulates server startup with --model)
    # Use a mock model object that matches what get_default_model returns
    from gptme.llm.models import ModelMeta, set_default_model
    from gptme.server.app import create_app

    test_model = ModelMeta(
        provider="openai",
        model="gpt-4",
        context=8192,
        max_output=4096,
    )
    set_default_model(test_model)

    try:
        # Create the app - this should capture the default model
        app = create_app()

        # Verify the model was stored in app config
        assert "SERVER_DEFAULT_MODEL" in app.config
        assert app.config["SERVER_DEFAULT_MODEL"] == test_model

        # Make a request - the before_request hook should propagate the model
        with app.test_client() as client:
            # The models endpoint returns the default model
            response = client.get("/api/v2/models")
            assert response.status_code == 200
            data = response.get_json()
            # Verify the default model is returned (not None)
            assert data.get("default") is not None
            assert "gpt-4" in data.get("default", "")
    finally:
        # Clean up - reset the default model by using the ContextVar directly
        from gptme.llm.models import _default_model_var

        _default_model_var.set(None)


def _reset_provider_health_cache(monkeypatch: pytest.MonkeyPatch):
    import gptme.server.api_v2 as api_module

    monkeypatch.setattr(api_module, "_provider_health_cache", {})
    monkeypatch.setattr(api_module, "_provider_health_cache_time", 0.0)
    monkeypatch.setattr(api_module, "_provider_health_refreshing", False)
    return api_module


def test_api_providers_health_structure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Test that /api/v2/providers/health returns the expected response shape."""
    api_module = _reset_provider_health_cache(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")],
    )
    monkeypatch.setattr(
        api_module,
        "_probe_provider",
        lambda provider_name: {
            "status": "ok" if provider_name == "anthropic" else "error",
            "latency_ms": 42 if provider_name == "anthropic" else 17,
            "error": None if provider_name == "anthropic" else "bad key",
        },
    )

    response = client.get("/api/v2/providers/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "providers": {
            "anthropic": {"status": "ok", "latency_ms": 42, "error": None},
            "openai": {"status": "error", "latency_ms": 17, "error": "bad key"},
        }
    }


def test_api_providers_health_cached(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Test that repeated calls use the cache instead of probing again."""
    api_module = _reset_provider_health_cache(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY")],
    )
    calls: list[str] = []

    def fake_probe(provider_name: str) -> dict[str, object]:
        calls.append(provider_name)
        return {"status": "ok", "latency_ms": 5, "error": None}

    monkeypatch.setattr(api_module, "_probe_provider", fake_probe)

    r1 = client.get("/api/v2/providers/health")
    r2 = client.get("/api/v2/providers/health")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert calls == ["anthropic"]
    assert r1.get_json() == r2.get_json()


def test_api_providers_health_force(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Test that ?force=1 bypasses the cache."""
    api_module = _reset_provider_health_cache(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY")],
    )
    calls: list[str] = []

    def fake_probe(provider_name: str) -> dict[str, object]:
        calls.append(provider_name)
        return {"status": "ok", "latency_ms": 7, "error": None}

    monkeypatch.setattr(api_module, "_probe_provider", fake_probe)

    response = client.get("/api/v2/providers/health")
    forced = client.get("/api/v2/providers/health?force=1")
    assert response.status_code == 200
    assert forced.status_code == 200
    assert calls == ["anthropic", "anthropic"]


def test_probe_provider_checks_openai_subscription_auth(
    monkeypatch: pytest.MonkeyPatch,
):
    """openai-subscription should get a real auth check, not configured fallback."""
    from gptme.llm import llm_openai_subscription
    from gptme.server import api_v2 as api_module

    calls: list[float] = []

    def fake_get_auth(timeout: float) -> object:
        calls.append(timeout)
        return object()

    monkeypatch.setattr(llm_openai_subscription, "get_auth", fake_get_auth)

    result = api_module._probe_provider("openai-subscription")

    assert result["status"] == "ok"
    assert result["error"] is None
    assert calls == [api_module._PROVIDER_HEALTH_TIMEOUT]


def test_probe_provider_empty_dynamic_models_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
):
    """local/gptme/custom probes should not report ok when model listing is empty."""
    from gptme import llm
    from gptme.server import api_v2 as api_module

    monkeypatch.setattr(llm, "get_available_models", lambda provider: [])

    result = api_module._probe_provider("local")

    assert result["status"] == "error"
    assert result["error"] == "No models returned from local"


def test_api_providers_health_force_shares_inflight_refresh(
    monkeypatch: pytest.MonkeyPatch,
):
    """Concurrent forced refreshes should share one probe instead of stampeding."""
    api_module = _reset_provider_health_cache(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY")],
    )
    probe_started = threading.Event()
    release_probe = threading.Event()
    calls: list[str] = []

    def fake_probe(provider_name: str) -> dict[str, object]:
        calls.append(provider_name)
        probe_started.set()
        assert release_probe.wait(timeout=1)
        return {"status": "ok", "latency_ms": 9, "error": None}

    monkeypatch.setattr(api_module, "_probe_provider", fake_probe)

    results: list[dict[str, object]] = []

    def load_health() -> None:
        results.append(api_module._get_provider_health_response(force=True))

    first = threading.Thread(target=load_health)
    second = threading.Thread(target=load_health)

    first.start()
    assert probe_started.wait(timeout=1)
    second.start()
    time.sleep(0.01)
    assert calls == ["anthropic"]

    release_probe.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == ["anthropic"]
    assert results == [
        {"providers": {"anthropic": {"status": "ok", "latency_ms": 9, "error": None}}},
        {"providers": {"anthropic": {"status": "ok", "latency_ms": 9, "error": None}}},
    ]


def test_api_providers_health_timeout_returns_quickly(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
):
    """Slow probes should surface as timeouts without blocking the response."""
    api_module = _reset_provider_health_cache(monkeypatch)
    monkeypatch.setattr(
        api_module,
        "list_available_providers",
        lambda: [("anthropic", "ANTHROPIC_API_KEY")],
    )
    monkeypatch.setattr(api_module, "_PROVIDER_HEALTH_TIMEOUT", 0.01)

    def slow_probe(_provider_name: str) -> dict[str, object]:
        time.sleep(0.2)
        return {"status": "ok", "latency_ms": 200, "error": None}

    monkeypatch.setattr(api_module, "_probe_provider", slow_probe)

    start = time.monotonic()
    response = client.get("/api/v2/providers/health?force=1")
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert elapsed < 0.12
    assert response.get_json() == {
        "providers": {
            "anthropic": {
                "status": "error",
                "latency_ms": 10,
                "error": "Timeout",
            }
        }
    }


def test_api_v2_commands(client: FlaskClient):
    """Test the /api/v2/commands endpoint returns available commands."""
    response = client.get("/api/v2/commands")
    assert response.status_code == 200
    data = response.get_json()
    assert "commands" in data
    commands = data["commands"]
    assert isinstance(commands, list)
    # Core commands should always be registered
    assert "/help" in commands
    assert "/exit" in commands
    assert "/model" in commands


def test_api_v2_conversation_command(conv, client: FlaskClient):
    """Test that slash commands are detected and executed when posted."""
    # /help prints to stdout (doesn't yield Messages), so responses=0
    # but the command flag should be set
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/help"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("command") is True


def test_api_v2_conversation_command_undo(conv, client: FlaskClient):
    """Test /undo command removes the last message."""
    # First, add a message
    client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "test message"},
    )
    # Then undo it via slash command
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/undo"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("command") is True


def test_api_v2_conversation_not_command(conv, client: FlaskClient):
    """Test that regular messages are not treated as commands."""
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello world"},
    )
    assert response.status_code == 200
    data = response.get_json()
    # Regular messages should not have the "command" flag
    assert "command" not in data


def test_api_v2_conversation_path_not_command(conv, client: FlaskClient):
    """Test that file paths starting with / are not treated as commands."""
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "/path/to/file.md"},
    )
    assert response.status_code == 200
    data = response.get_json()
    # File paths should not be treated as commands
    assert "command" not in data


def test_api_v2_conversation_post_tools_validation(conv, client: FlaskClient):
    """Test that the tools field is validated before calling init_tools.

    A non-list value (string, int) should return 400 instead of crashing
    with a 500 when get_toolchain iterates over characters.
    """
    # String tools should be rejected with 400
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello", "tools": "malformed"},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "tools" in data.get("error", "").lower()

    # Integer tools should be rejected with 400
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello", "tools": 42},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert "tools" in data.get("error", "").lower()

    # Valid list of tools should still work
    response = client.post(
        f"/api/v2/conversations/{conv}",
        json={"role": "user", "content": "hello", "tools": ["shell"]},
    )
    body = response.get_json()
    assert response.status_code == 200, (
        f"Expected 200 for valid tools list, got {response.status_code}: {body}"
    )


# --- Cookie-based authentication tests ---


@pytest.fixture
def auth_app():
    """Create an app with auth enabled (network binding)."""
    from gptme.server.app import create_app
    from gptme.server.auth import (
        AUTH_COOKIE_NAME,
        init_auth,
        set_server_token,
    )

    app = create_app(host="0.0.0.0")
    # Force auth enabled and set a known token
    test_token = "test-token-for-cookie-auth"
    set_server_token(test_token)
    init_auth(host="0.0.0.0", display=False)

    yield app, test_token, AUTH_COOKIE_NAME

    # Reset auth state for other tests
    init_auth(host="127.0.0.1", display=False)


@pytest.fixture
def auth_client(auth_app):
    """Test client for auth-enabled app."""
    app, token, cookie_name = auth_app
    with app.test_client() as test_client:
        yield test_client, token, cookie_name


def test_auth_cookie_set(auth_client):
    """POST /api/v2/auth/cookie sets HttpOnly auth cookie."""
    client, token, cookie_name = auth_client

    response = client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    # Verify Set-Cookie header properties
    set_cookie = response.headers.get("Set-Cookie", "")
    assert cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/api/" in set_cookie


def test_auth_cookie_rejected_without_token(auth_client):
    """POST /api/v2/auth/cookie without Bearer token returns 401."""
    client, token, cookie_name = auth_client

    response = client.post("/api/v2/auth/cookie")
    assert response.status_code == 401


def test_auth_cookie_rejected_with_bad_token(auth_client):
    """POST /api/v2/auth/cookie with wrong token returns 401."""
    client, token, cookie_name = auth_client

    response = client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_auth_cookie_used_for_api(auth_client):
    """API requests authenticate via cookie when no header is present."""
    client, token, cookie_name = auth_client

    # Verify protected endpoint rejects without any auth
    response = client.get("/api/v2/conversations")
    assert response.status_code == 401

    # Set the cookie
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Now make an API request without Authorization header — cookie should work
    response = client.get("/api/v2/conversations")
    assert response.status_code == 200


def test_auth_cookie_clear(auth_client):
    """DELETE /api/v2/auth/cookie returns success and sets expired cookie."""
    client, token, cookie_name = auth_client

    # Set cookie first
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Clear it
    response = client.delete("/api/v2/auth/cookie")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True

    # Verify the Set-Cookie header expires the cookie
    set_cookie = response.headers.get("Set-Cookie", "")
    assert cookie_name in set_cookie
    # Expired cookies have Expires in the past or Max-Age=0
    assert "Expires=" in set_cookie or "Max-Age=0" in set_cookie

    # Verify cookie-only requests are rejected after clearing
    # Use a fresh client (no cookie jar) to simulate browser honoring Max-Age=0
    app = client.application
    with app.test_client() as fresh_client:
        response = fresh_client.get("/api/v2/conversations")
        assert response.status_code == 401


def test_auth_header_takes_priority_over_cookie(auth_client):
    """Authorization header is preferred over cookie."""
    client, token, cookie_name = auth_client

    # Set a valid cookie
    client.post(
        "/api/v2/auth/cookie",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Request with valid header should work even if we clear cookie
    response = client.get(
        "/api/v2/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_auth_query_param_still_works(auth_client):
    """Query parameter auth still works as fallback (backward compat)."""
    client, token, cookie_name = auth_client

    response = client.get(f"/api/v2/conversations?token={token}")
    assert response.status_code == 200


def test_http_errors_return_json(client: FlaskClient):
    """HTTP errors from Flask (404, 405) must return JSON, not HTML.

    Without registered error handlers, Flask returns HTML error pages for routing
    errors. The webui expects JSON from all /api/* responses, so HTML breaks client
    error handling.
    """
    # 404: nonexistent API route
    response = client.get("/api/v2/this-does-not-exist")
    assert response.status_code == 404
    assert response.content_type.startswith("application/json")
    data = response.get_json()
    assert data is not None
    assert "error" in data

    # 405: valid route but wrong HTTP method
    response = client.post("/api/v2/models")
    assert response.status_code == 405
    assert response.content_type.startswith("application/json")
    data = response.get_json()
    assert data is not None
    assert "error" in data

    # 404: invalid message index type (-1 doesn't match <int:index> pattern)
    response = client.delete("/api/v2/conversations/any-conv/messages/-1")
    assert response.status_code in (404, 405)
    assert response.content_type.startswith("application/json"), (
        f"Expected JSON, got {response.content_type}: {response.data[:200]}"
    )
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_spa_fallback_api_returns_json(tmp_path: Path):
    """In custom-webui mode, unknown api/ paths return JSON 404 (not index.html).

    The spa_fallback catch-all route guards api/ prefixes explicitly so that
    API clients don't receive index.html when they hit an unknown endpoint.
    """
    # Minimal webui build: any directory != static_path triggers is_custom_webui=True
    (tmp_path / "index.html").write_text("<html></html>")

    from gptme.server.app import create_app  # fmt: skip

    app = create_app(webui_dir=tmp_path)
    with app.test_client() as c:
        # api/ path → JSON 404, never index.html
        response = c.get("/api/v2/this-does-not-exist")
        assert response.status_code == 404
        assert response.content_type.startswith("application/json")
        data = response.get_json()
        assert data is not None
        assert "error" in data

        # non-api path → SPA fallback serves index.html
        response = c.get("/some/deep/link")
        assert response.status_code == 200


# --- Message edit (PATCH) and delete (DELETE) edge cases ---


@pytest.fixture
def conv_with_messages(client: FlaskClient):
    """Create a conversation with a system message and a user message.

    Returns (convname, user_msg_index) since the server prepends its own
    system messages so the client-supplied messages don't start at index 0.
    """
    convname = f"test-edit-{random.randint(0, 1000000)}"
    response = client.put(
        f"/api/v2/conversations/{convname}",
        json={
            "messages": [
                {"role": "system", "content": "you are a test assistant"},
                {"role": "user", "content": "hello world"},
            ]
        },
    )
    assert response.status_code == 200

    # Discover the actual index of the first user message (server prepends
    # its own system messages so the index is not always 0 or 1).
    r = client.get(f"/api/v2/conversations/{convname}")
    log = r.get_json().get("log", [])
    user_indices = [i for i, m in enumerate(log) if m["role"] == "user"]
    assert user_indices, f"No user message found in log: {log!r}"
    return convname, user_indices[0]


def test_api_v2_edit_message_nonexistent_conversation(client: FlaskClient):
    """PATCH on a conversation that does not exist returns 404, not 500."""
    response = client.patch(
        "/api/v2/conversations/does-not-exist-xyz/messages/0",
        json={"content": "updated"},
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_edit_message_out_of_range(conv_with_messages, client: FlaskClient):
    """PATCH with an index beyond the message count returns 404."""
    convname, _ = conv_with_messages
    response = client.patch(
        f"/api/v2/conversations/{convname}/messages/999",
        json={"content": "updated"},
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_edit_message_no_content_no_truncate(
    conv_with_messages, client: FlaskClient
):
    """PATCH with neither content nor truncate=1 returns 400."""
    convname, user_idx = conv_with_messages
    response = client.patch(
        f"/api/v2/conversations/{convname}/messages/{user_idx}",
        json={},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_edit_message_non_user_message(conv_with_messages, client: FlaskClient):
    """PATCH to edit a system message (index 0) returns 400."""
    convname, _ = conv_with_messages
    response = client.patch(
        f"/api/v2/conversations/{convname}/messages/0",
        json={"content": "trying to edit system message"},
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_edit_message_success(conv_with_messages, client: FlaskClient):
    """PATCH editing a user message returns 200 with updated content."""
    convname, user_idx = conv_with_messages
    response = client.patch(
        f"/api/v2/conversations/{convname}/messages/{user_idx}",
        json={"content": "updated message content"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    # Response should contain the updated log
    assert "log" in data


def test_api_v2_delete_message_nonexistent_conversation(client: FlaskClient):
    """DELETE on a conversation that does not exist returns 404, not 500."""
    response = client.delete(
        "/api/v2/conversations/does-not-exist-xyz/messages/0",
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_delete_message_out_of_range(conv_with_messages, client: FlaskClient):
    """DELETE with an index beyond the message count returns 404."""
    convname, _ = conv_with_messages
    response = client.delete(
        f"/api/v2/conversations/{convname}/messages/999",
    )
    assert response.status_code == 404
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_delete_message_system_message(conv_with_messages, client: FlaskClient):
    """DELETE of a system message (index 0) returns 400."""
    convname, _ = conv_with_messages
    response = client.delete(
        f"/api/v2/conversations/{convname}/messages/0",
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data is not None
    assert "error" in data


def test_api_v2_delete_message_success(conv_with_messages, client: FlaskClient):
    """DELETE of a user message returns 200 and removes the message."""
    convname, user_idx = conv_with_messages

    # Get initial message count
    r = client.get(f"/api/v2/conversations/{convname}")
    initial_count = len(r.get_json().get("log", []))

    # Delete the user message
    response = client.delete(
        f"/api/v2/conversations/{convname}/messages/{user_idx}",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data is not None
    assert "log" in data

    # Verify the message was removed
    r = client.get(f"/api/v2/conversations/{convname}")
    final_log = r.get_json().get("log", [])
    assert len(final_log) == initial_count - 1
    # Verify no message at the deleted index has the original user content
    assert not any(
        m["role"] == "user" and m["content"] == "hello world" for m in final_log
    )
