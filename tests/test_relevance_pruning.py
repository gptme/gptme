"""Tests for Phase 0 relevance-scored pruning of stale tool outputs.

Covers score_tool_output_relevance() and prune_stale_tool_outputs(), which
form the pre-pass that drops stale tool results before Phase 1-3 fire.
"""

from datetime import datetime, timezone

from gptme.llm.models import get_default_model, get_model
from gptme.message import Message
from gptme.tools.autocompact import (
    auto_compact_log,
    prune_stale_tool_outputs,
    score_tool_output_relevance,
)
from gptme.tools.autocompact.scoring import _PRUNE_MIN_AGE


def _ts():
    return datetime.now(tz=timezone.utc)


def _system(content: str, **kw) -> Message:
    return Message("system", content, _ts(), **kw)


def _user(content: str) -> Message:
    return Message("user", content, _ts())


def _assistant(content: str) -> Message:
    return Message("assistant", content, _ts())


def _model_name() -> str:
    m = get_default_model() or get_model("gpt-4")
    return m.model


# ---------------------------------------------------------------------------
# score_tool_output_relevance
# ---------------------------------------------------------------------------


def test_pinned_scores_max():
    msg = _system("some tool output", pinned=True)
    log = [msg]
    assert score_tool_output_relevance(msg, 0, log) == 5.0


def test_recent_scores_max():
    """Messages within the last _PRUNE_MIN_AGE positions are always kept."""
    log = [_user("q"), _assistant("a"), _system("tool out")]
    msg = log[-1]
    idx = len(log) - 1
    # distance_from_end == 0 < _PRUNE_MIN_AGE
    assert score_tool_output_relevance(msg, idx, log) == 5.0


def test_non_system_scores_max():
    """Only system messages are scored; others always return 5.0."""
    msg = _user("some user message")
    log = [msg]
    assert score_tool_output_relevance(msg, 0, log) == 5.0


def test_old_unreferenced_scores_low():
    """Old tool output not mentioned again should score low (< threshold)."""
    old_output = _system("Command output: lots of text that nobody cares about")
    # Add enough padding messages to push it beyond _PRUNE_MIN_AGE
    padding = [_user(f"message {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [old_output] + padding
    score = score_tool_output_relevance(old_output, 0, log)
    assert score < 1.0, f"Expected score < 1.0, got {score}"


def test_error_content_boosts_score():
    """Tool outputs containing errors score higher."""
    msg = _system("Error: command not found")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [msg] + padding
    score_with_error = score_tool_output_relevance(msg, 0, log)

    msg_clean = _system("Command output: all good")
    log_clean = [msg_clean] + padding
    score_without = score_tool_output_relevance(msg_clean, 0, log_clean)

    assert score_with_error > score_without


def test_referenced_path_boosts_score():
    """Tool outputs whose file paths appear in later messages score higher."""
    msg = _system("Contents of /home/user/project/config.py:\nSOME_VAR = 1")
    later = _user("Can you edit /home/user/project/config.py to add a new var?")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    log = [msg] + padding + [later]
    score = score_tool_output_relevance(msg, 0, log)

    # Compare to same output with no later reference
    log_no_ref = [msg] + padding + [_user("something unrelated")]
    score_no_ref = score_tool_output_relevance(msg, 0, log_no_ref)

    assert score > score_no_ref + 2.0, (
        f"Referenced path should boost score by >2.0: {score} vs {score_no_ref}"
    )


# ---------------------------------------------------------------------------
# prune_stale_tool_outputs
# ---------------------------------------------------------------------------


def test_prune_keeps_recent_messages():
    """Messages within _PRUNE_MIN_AGE of the end are never pruned."""
    # Build a log where the last few messages are large tool outputs
    large_content = "x " * 300  # > 200 tokens
    recent_tool = _system(large_content)
    log = [_user("q"), _assistant("a"), recent_tool]

    pruned, saved = prune_stale_tool_outputs(log, _model_name())
    assert len(pruned) == len(log)
    assert saved == 0


def test_prune_keeps_small_messages():
    """Small tool outputs (< _PRUNE_MIN_TOKENS) are always kept."""
    small_output = _system("ok")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [small_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name())
    assert small_output in pruned
    assert saved == 0


def test_prune_drops_old_unreferenced_large_output():
    """Old, large, unreferenced tool outputs should be dropped."""
    large_content = "word " * 400  # well above 200 tokens
    old_output = _system(large_content)
    padding = [_user(f"message {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [old_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output not in pruned, "Stale large tool output should be dropped"
    assert saved > 0, "Should report tokens saved"


def test_prune_keeps_referenced_output():
    """Tool outputs referenced by later messages are kept."""
    content = "Contents of /tmp/important.py:\nresult = 42"
    old_output = _system(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    later = _user("Please update /tmp/important.py")
    log = [old_output] + padding + [later]

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output in pruned, "Referenced tool output should be retained"


def test_prune_keeps_error_output():
    """Tool outputs with error content are kept even if old."""
    content = "Error: failed to connect to database\n" + "detail " * 100
    old_output = _system(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [old_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output in pruned, "Error-containing tool output should be retained"


def test_prune_keeps_pinned_messages():
    """Pinned messages are never pruned regardless of size or age."""
    content = "pinned output " * 300
    pinned_msg = _system(content, pinned=True)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [pinned_msg] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert pinned_msg in pruned
    assert saved == 0


def test_prune_respects_keep_head():
    """Messages in the protected head are never pruned."""
    content = "head tool output " * 300
    head_msg = _system(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [head_msg] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=1)

    assert head_msg in pruned
    assert saved == 0


# ---------------------------------------------------------------------------
# Integration: auto_compact_log runs Phase 0
# ---------------------------------------------------------------------------


def test_auto_compact_prunes_stale_before_phases():
    """auto_compact_log should drop stale tool outputs before Phase 2 truncation."""
    # Create a conversation with some stale large tool outputs that push
    # overall tokens up, interleaved with newer messages.
    stale_content = "stale file listing " * 200  # large but not referenced later
    stale_output = _system(stale_content)

    recent_messages = [_user(f"new message {i}") for i in range(_PRUNE_MIN_AGE + 1)]
    log = [stale_output] + recent_messages

    compacted = list(auto_compact_log(log, keep_head=0))

    # The stale output should either be dropped or the total should be reduced
    compacted_contents = [m.content for m in compacted]
    # Stale output must not survive verbatim (either dropped or truncated)
    assert stale_content not in compacted_contents or len(compacted) < len(log), (
        "Expected stale large output to be pruned or message count to decrease"
    )
