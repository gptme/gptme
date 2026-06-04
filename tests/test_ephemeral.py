"""Tests for ephemeral message pruning and cache boundary optimization."""

from datetime import datetime, timezone
from typing import Literal

from gptme.logmanager.manager import (
    _auto_tag_ephemeral,
    ephemeral_cache_boundary,
    prune_ephemeral_messages,
)
from gptme.message import Message

Role = Literal["system", "user", "assistant"]


def _msg(role: Role, content: str = "hello", ttl: int | None = None) -> Message:
    return Message(
        role,
        content,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ephemeral_ttl=ttl,
    )  # type: ignore[call-arg]


def _assert_no_consecutive_same_role(msgs: list[Message]) -> None:
    for prev, current in zip(msgs, msgs[1:], strict=False):
        assert prev.role != current.role


# ---------------------------------------------------------------------------
# Message serialization round-trips
# ---------------------------------------------------------------------------


def test_ephemeral_ttl_json_roundtrip():
    import json

    from dateutil.parser import isoparse

    msg = _msg("assistant", "thinking...", ttl=2)
    # Serialize as the logmanager JSONL path does
    serialized = json.dumps(msg.to_dict())
    d = json.loads(serialized)
    assert d["ephemeral_ttl"] == 2

    # Deserialize mimicking _gen_read_jsonl
    d["timestamp"] = isoparse(d["timestamp"])
    files = d.pop("files", [])
    restored = Message(**d, files=files)
    assert restored.ephemeral_ttl == 2


def test_no_ephemeral_ttl_not_serialized():
    msg = _msg("assistant", "plain")
    d = msg.to_dict()
    assert "ephemeral_ttl" not in d


def test_ephemeral_ttl_toml_roundtrip():
    msg = _msg("assistant", "I thought about it.", ttl=3)
    toml_str = msg.to_toml()
    assert "ephemeral_ttl = 3" in toml_str

    restored = Message.from_toml(toml_str)
    assert restored.ephemeral_ttl == 3


def test_toml_no_ephemeral_ttl_when_none():
    msg = _msg("assistant", "plain")
    toml_str = msg.to_toml()
    assert "ephemeral_ttl" not in toml_str


def test_concat_preserves_min_ttl():
    a = _msg("user", "part a", ttl=3)
    b = _msg("user", "part b", ttl=1)
    merged = a.concat(b)
    assert merged.ephemeral_ttl == 1


def test_concat_none_ttl_propagates():
    a = _msg("user", "part a", ttl=None)
    b = _msg("user", "part b", ttl=2)
    merged = a.concat(b)
    assert merged.ephemeral_ttl == 2

    # Both None → None
    c = _msg("user", "c", ttl=None)
    d = _msg("user", "d", ttl=None)
    assert c.concat(d).ephemeral_ttl is None


# ---------------------------------------------------------------------------
# prune_ephemeral_messages
# ---------------------------------------------------------------------------


def _make_convo() -> list[Message]:
    """Build a simple 5-message conversation:
    system, user0, assistant0 (ephemeral ttl=2), user1, assistant1
    """
    return [
        _msg("system", "You are a helpful assistant."),
        _msg("user", "Question 0"),
        _msg("assistant", "<think>reasoning</think>Answer 0", ttl=2),
        _msg("user", "Question 1"),
        _msg("assistant", "Answer 1"),
    ]


def test_recent_ephemeral_message_kept():
    msgs = _make_convo()
    # Only 1 assistant turn after assistant0 (that's assistant1), TTL=2 → keep
    result = prune_ephemeral_messages(msgs)
    assert len(result) == len(msgs)


def test_expired_ephemeral_message_pruned():
    msgs = _make_convo()
    # Add two more assistant turns so assistant0 has 3 turns after it (TTL=2 → drop)
    msgs += [
        _msg("user", "Question 2"),
        _msg("assistant", "Answer 2"),
        _msg("user", "Question 3"),
        _msg("assistant", "Answer 3"),
    ]
    result = prune_ephemeral_messages(msgs)
    # assistant0 should be gone
    contents = [m.content for m in result]
    assert "<think>reasoning</think>Answer 0" not in contents
    # Adjacent user messages on either side of the pruned assistant are merged.
    assert "Question 0\n\nQuestion 1" in contents
    _assert_no_consecutive_same_role(result)


def test_pinned_message_never_pruned():
    msgs = [
        _msg("system", "System"),
        _msg("user", "Q0"),
        Message(
            "assistant",
            "<think>pinned thinking</think>",
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            pinned=True,
            ephemeral_ttl=0,  # TTL=0 but pinned → must survive
        ),
        _msg("user", "Q1"),
        _msg("assistant", "A1"),
        _msg("user", "Q2"),
        _msg("assistant", "A2"),
    ]
    result = prune_ephemeral_messages(msgs)
    pinned_msgs = [m for m in result if m.pinned]
    assert len(pinned_msgs) == 1


def test_prune_merges_consecutive_roles_after_drop():
    msgs = [
        _msg("system", "System"),
        _msg("user", "Q0"),
        _msg("assistant", "A0"),
        _msg("user", "Q1"),
        _msg("assistant", "<think>x</think>", ttl=0),  # expires immediately
        _msg("user", "Q2"),
        _msg("assistant", "A2"),
    ]
    result = prune_ephemeral_messages(msgs)
    roles = [m.role for m in result]
    # Should maintain chronological order and merge the user turns around the
    # pruned assistant so strict providers do not reject the sequence.
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert result[3].content == "Q1\n\nQ2"


def test_no_ephemeral_messages_unchanged():
    msgs = [
        _msg("system", "System"),
        _msg("user", "Q0"),
        _msg("assistant", "A0"),
    ]
    result = prune_ephemeral_messages(msgs)
    assert result == msgs


def test_ttl_zero_expires_after_one_assistant_turn():
    # TTL=0 means: drop as soon as there is any assistant turn after it
    msgs = [
        _msg("system", "S"),
        _msg("user", "Q"),
        _msg("assistant", "<think>fast expiry</think>", ttl=0),
        _msg("user", "Q2"),
        _msg("assistant", "A2"),  # 1 assistant turn after → exceeds TTL=0
    ]
    result = prune_ephemeral_messages(msgs)
    assert all("<think>" not in m.content for m in result)


def test_mixed_ttl_dropped_message_does_not_inflate_counter():
    """Regression: a dropped short-TTL assistant message must not count against
    the TTL of an earlier message with a longer TTL.

    Layout (forward order): A0(TTL=2), A1(TTL=0), A2(plain), A3(plain)
    After A2+A3, A1 has 2 turns after it → drops (2 > 0 ✓).
    A0 should see only 2 *surviving* turns (A2, A3), not 3.
    """
    msgs = [
        _msg("system", "S"),
        _msg("user", "Q0"),
        _msg("assistant", "A0 long-lived", ttl=2),  # should survive
        _msg("user", "Q1"),
        _msg("assistant", "A1 quick expiry", ttl=0),  # should be dropped
        _msg("user", "Q2"),
        _msg("assistant", "A2"),
        _msg("user", "Q3"),
        _msg("assistant", "A3"),
    ]
    result = prune_ephemeral_messages(msgs)
    contents = [m.content for m in result]
    assert "A1 quick expiry" not in contents, "A1(TTL=0) should be pruned"
    assert "A0 long-lived" in contents, (
        "A0(TTL=2) must survive — only 2 real turns remain after it"
    )


# ---------------------------------------------------------------------------
# ephemeral_cache_boundary
# ---------------------------------------------------------------------------


def test_ephemeral_cache_boundary_returns_none_when_no_ephemeral():
    msgs = [_msg("system"), _msg("user"), _msg("assistant")]
    assert ephemeral_cache_boundary(msgs) == None  # noqa: E711


def test_ephemeral_cache_boundary_returns_index_before_first_ephemeral():
    msgs = [
        _msg("system"),  # 0
        _msg("user"),  # 1
        _msg("assistant"),  # 2  ← boundary (last stable)
        _msg("user"),  # 3  ← first ephemeral
        _msg("assistant"),  # 4
    ]
    msgs[3] = _msg("user", ttl=1)
    result = ephemeral_cache_boundary(msgs)
    assert result == 2


def test_ephemeral_cache_boundary_none_when_first_msg_is_ephemeral():
    msgs = [_msg("user", ttl=1), _msg("assistant")]
    assert ephemeral_cache_boundary(msgs) is None


# ---------------------------------------------------------------------------
# _auto_tag_ephemeral
# ---------------------------------------------------------------------------


def test_auto_tag_thinking_message():
    msg = _msg("assistant", "Let me think.\n<think>\nreasoning here\n</think>\nAnswer.")
    tagged = _auto_tag_ephemeral(msg)
    assert tagged.ephemeral_ttl is not None
    assert tagged.ephemeral_ttl > 0


def test_auto_tag_thinking_tag():
    msg = _msg("assistant", "<thinking>chain of thought</thinking>Result.")
    tagged = _auto_tag_ephemeral(msg)
    assert tagged.ephemeral_ttl is not None


def test_auto_tag_no_thinking_unchanged():
    msg = _msg("assistant", "Plain answer with no thinking block.")
    tagged = _auto_tag_ephemeral(msg)
    assert tagged.ephemeral_ttl is None


def test_auto_tag_respects_existing_ttl():
    msg = _msg("assistant", "<think>x</think>", ttl=5)
    # Should not overwrite existing TTL set by caller
    tagged = _auto_tag_ephemeral(msg)
    assert tagged.ephemeral_ttl == 5


# ---------------------------------------------------------------------------
# apply_cache_control integration
# ---------------------------------------------------------------------------


def test_apply_cache_control_ephemeral_boundary():
    from gptme.llm.utils import apply_cache_control

    messages: list[dict] = [
        {"role": "user", "content": "Stable question"},
        {"role": "assistant", "content": "Stable answer"},  # boundary idx=1
        {
            "role": "user",
            "content": "<think>ephemeral</think> follow up",
        },  # ephemeral idx=2
        {"role": "assistant", "content": "Final"},
    ]
    modified, _ = apply_cache_control(messages, ephemeral_boundary_idx=1)

    # Boundary message (idx=1) should have cache_control
    boundary_content = modified[1]["content"]
    assert isinstance(boundary_content, list)
    last_part = boundary_content[-1]
    assert last_part.get("cache_control") == {"type": "ephemeral"}


def test_apply_cache_control_caches_last_two_users_without_ephemeral():
    """Regression: without ephemeral messages, both last two user messages must be cached."""
    from gptme.llm.utils import apply_cache_control

    messages: list[dict] = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        {"role": "user", "content": "second to last"},
        {"role": "assistant", "content": [{"type": "text", "text": "answer 2"}]},
        {"role": "user", "content": "last"},
    ]
    modified, _ = apply_cache_control(messages)

    def _has_cache(msg: dict) -> bool:
        content = msg.get("content")
        if isinstance(content, str):
            return False
        if isinstance(content, list):
            return any(
                p.get("cache_control") == {"type": "ephemeral"}
                for p in content
                if isinstance(p, dict)
            )
        return False

    user_msgs = [m for m in modified if m.get("role") == "user"]
    assert _has_cache(user_msgs[-1]), "last user message must be cached"
    assert _has_cache(user_msgs[-2]), (
        "second-to-last user message must be cached (Anthropic multi-turn pattern)"
    )
    assert not _has_cache(user_msgs[0]), "first user message should not be cached"


def test_apply_cache_control_last_user_still_cached():
    from gptme.llm.utils import apply_cache_control

    messages: list[dict] = [
        {"role": "user", "content": "stable"},
        {"role": "assistant", "content": [{"type": "text", "text": "boundary"}]},
        {"role": "user", "content": "ephemeral user"},
        {"role": "user", "content": "last user"},
    ]
    modified, _ = apply_cache_control(messages, ephemeral_boundary_idx=1)

    # Last user message should also have cache_control
    last_user = modified[-1]
    content = last_user["content"]
    if isinstance(content, str):
        pass  # string content — just check it wasn't lost
    else:
        last_part = content[-1]
        assert last_part.get("cache_control") == {"type": "ephemeral"}
