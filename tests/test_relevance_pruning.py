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


def _tool_out(content: str, call_id: str = "call-test", **kw) -> Message:
    """A system message that Phase 0 will treat as a tool result."""
    return _system(content, call_id=call_id, **kw)


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
    old_output = _tool_out("Command output: lots of text that nobody cares about")
    # Add enough padding messages to push it beyond _PRUNE_MIN_AGE
    padding = [_user(f"message {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [old_output] + padding
    score = score_tool_output_relevance(old_output, 0, log)
    assert score < 1.0, f"Expected score < 1.0, got {score}"


def test_error_content_boosts_score():
    """Tool outputs containing errors score higher (hard keep, not a boost)."""
    msg = _tool_out("Error: command not found")
    # Past the age-penalty cap so a mere +2.0 boost would fall below threshold.
    padding = [_user(f"msg {i}") for i in range(20)]
    log = [msg] + padding
    score_with_error = score_tool_output_relevance(msg, 0, log)

    msg_clean = _tool_out("Command output: all good")
    log_clean = [msg_clean] + padding
    score_without = score_tool_output_relevance(msg_clean, 0, log_clean)

    assert score_with_error == 5.0
    assert score_with_error > score_without


def test_referenced_path_boosts_score():
    """Tool outputs whose file paths appear in later messages score max."""
    msg = _tool_out("Contents of /home/user/project/config.py:\nSOME_VAR = 1")
    later = _user("Can you edit /home/user/project/config.py to add a new var?")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    log = [msg] + padding + [later]
    score = score_tool_output_relevance(msg, 0, log)

    # Compare to same output with no later reference
    log_no_ref = [msg] + padding + [_user("something unrelated")]
    score_no_ref = score_tool_output_relevance(msg, 0, log_no_ref)

    assert score == 5.0
    assert score > score_no_ref + 2.0, (
        f"Referenced path should boost score by >2.0: {score} vs {score_no_ref}"
    )


def test_referenced_relative_path_boosts_score():
    """Relative paths like src/config.py must count as references."""
    msg = _tool_out("Contents of src/config.py:\nSOME_VAR = 1")
    later = _user("Please edit src/config.py")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    log = [msg] + padding + [later]
    score = score_tool_output_relevance(msg, 0, log)

    log_no_ref = [msg] + padding + [_user("something unrelated")]
    score_no_ref = score_tool_output_relevance(msg, 0, log_no_ref)

    assert score == 5.0
    assert score > score_no_ref + 2.0


# ---------------------------------------------------------------------------
# prune_stale_tool_outputs
# ---------------------------------------------------------------------------


def test_prune_keeps_recent_messages():
    """Messages within _PRUNE_MIN_AGE of the end are never pruned."""
    # Build a log where the last few messages are large tool outputs
    large_content = "x " * 300  # > 200 tokens
    recent_tool = _tool_out(large_content)
    log = [_user("q"), _assistant("a"), recent_tool]

    pruned, saved = prune_stale_tool_outputs(log, _model_name())
    assert len(pruned) == len(log)
    assert saved == 0


def test_prune_keeps_small_messages():
    """Small tool outputs (< _PRUNE_MIN_TOKENS) are always kept."""
    small_output = _tool_out("ok")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [small_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name())
    assert small_output in pruned
    assert saved == 0


def test_prune_drops_old_unreferenced_large_output():
    """Old, large, unreferenced tool outputs should be stubbed in place."""
    large_content = "word " * 400  # well above 200 tokens
    old_output = _tool_out(large_content)
    padding = [_user(f"message {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [old_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert len(pruned) == len(log), "Phase 0 must not delete messages"
    assert old_output not in pruned, "Stale large tool output should be stubbed"
    assert "Stale tool output pruned" in pruned[0].content
    assert pruned[0].call_id == old_output.call_id
    assert saved > 0, "Should report tokens saved"


def test_prune_keeps_referenced_output():
    """Tool outputs referenced by later messages are kept (large enough to prune)."""
    content = "Contents of /tmp/important.py:\n" + "result = 42\n" + "line " * 400
    old_output = _tool_out(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    later = _user("Please update /tmp/important.py")
    log = [old_output] + padding + [later]

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output in pruned, "Referenced tool output should be retained"


def test_prune_keeps_referenced_relative_path_output():
    """Large results whose relative path is named later must be kept."""
    content = "Contents of src/config.py:\n" + "SETTING = 1\n" + "line " * 400
    old_output = _tool_out(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE)]
    later = _user("Please update src/config.py")
    log = [old_output] + padding + [later]

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output in pruned, "Relative-path-referenced output should be retained"


def test_prune_keeps_error_output():
    """Tool outputs with error content are kept even if old enough to hit the age cap."""
    content = "Error: failed to connect to database\n" + "detail " * 400
    old_output = _tool_out(content)
    padding = [_user(f"msg {i}") for i in range(20)]
    log = [old_output] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert old_output in pruned, "Error-containing tool output should be retained"


def test_prune_keeps_pinned_messages():
    """Pinned messages are never pruned regardless of size or age."""
    content = "pinned output " * 300
    pinned_msg = _tool_out(content, pinned=True)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [pinned_msg] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert pinned_msg in pruned
    assert saved == 0


def test_prune_respects_keep_head():
    """Messages in the protected head are never pruned."""
    content = "head tool output " * 300
    head_msg = _tool_out(content)
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [head_msg] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=1)

    assert head_msg in pruned
    assert saved == 0


# ---------------------------------------------------------------------------
# Integration: auto_compact_log runs Phase 0
# ---------------------------------------------------------------------------


def test_auto_compact_prunes_stale_before_phases():
    """auto_compact_log should stub stale tool outputs before Phase 2 truncation."""
    stale_content = "stale file listing " * 200  # large but not referenced later
    stale_output = _tool_out(stale_content)

    recent_messages = [_user(f"new message {i}") for i in range(_PRUNE_MIN_AGE + 1)]
    log = [stale_output] + recent_messages

    compacted = list(auto_compact_log(log, keep_head=0, limit=100))

    assert len(compacted) == len(log)
    compacted_contents = [m.content for m in compacted]
    assert stale_content not in compacted_contents
    assert "Stale tool output pruned" in compacted[0].content


def test_auto_compact_skips_phase0_when_under_budget():
    """Under-budget logs must not lose tool output (Phase 0 is over-budget only)."""
    stale_content = "stale file listing " * 200
    stale_output = _tool_out(stale_content)
    recent_messages = [_user(f"new message {i}") for i in range(_PRUNE_MIN_AGE + 1)]
    log = [stale_output] + recent_messages

    compacted = list(auto_compact_log(log, keep_head=0, limit=10_000_000))

    assert [m.content for m in compacted] == [m.content for m in log]


def test_prune_keeps_system_instructions():
    """Large old system messages that are not tool results must not be stubbed."""
    content = "You are a helpful agent. " * 400
    instruction = _system(content)  # no call_id, not after a tool-call
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [instruction] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert instruction in pruned
    assert saved == 0


def test_prune_preserves_tool_call_pairing():
    """Stubbing a result must keep the matching tool-call message and call_id."""
    large_content = "word " * 400
    tool_call = _assistant("```shell\ncat /tmp/big.txt\n```")
    tool_result = _tool_out(large_content, call_id="call-abc")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [tool_call, tool_result] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert len(pruned) == len(log)
    assert pruned[0].content == tool_call.content
    assert pruned[1].role == "system"
    assert pruned[1].call_id == "call-abc"
    assert large_content not in pruned[1].content
    assert saved > 0


def test_prune_identifies_tool_output_via_preceding_tool_use():
    """Markdown tool format has no call_id; the preceding tool-call still counts."""
    large_content = "word " * 400
    tool_call = _assistant("```shell\ncat /tmp/big.txt\n```")
    tool_result = _system(large_content)  # no call_id
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 2)]
    log = [tool_call, tool_result] + padding

    pruned, saved = prune_stale_tool_outputs(log, _model_name(), keep_head=0)

    assert len(pruned) == len(log)
    assert saved > 0
    assert "Stale tool output pruned" in pruned[1].content


def test_phase0_preserves_master_context_indices(tmp_path):
    """Phase 0 must not shift indices used by Phase 2 master-context refs.

    If an earlier message were deleted, a later truncated result would look up
    the wrong byte range in conversation.jsonl.
    """
    import json
    import re

    from gptme.util.master_context import build_master_context_index

    stale_content = "stale listing " * 400
    # Massive recent result: Phase 0 keeps it (recent), Phase 2 truncates it.
    massive_words = [f"file_{i}.txt" for i in range(2500)]
    massive_content = "Ran command: find\n" + "\n".join(massive_words)

    stale = _tool_out(stale_content, call_id="call-stale")
    padding = [_user(f"msg {i}") for i in range(_PRUNE_MIN_AGE + 1)]
    massive = _tool_out(massive_content, call_id="call-massive")
    # Keep massive recent so Phase 0 will not stub it.
    log = [stale, *padding, massive, _user("thanks"), _assistant("done")]

    logfile = tmp_path / "conversation.jsonl"
    with logfile.open("w") as f:
        for msg in log:
            f.write(json.dumps(msg.to_dict()) + "\n")

    index = build_master_context_index(logfile)
    massive_idx = len(log) - 3
    expected_range = index[massive_idx]

    # Force Phase 2 to run: over-budget, max_tool_result_tokens below massive size.
    compacted = list(
        auto_compact_log(
            log,
            limit=500,
            max_tool_result_tokens=200,
            logdir=tmp_path,
            keep_head=0,
        )
    )

    assert len(compacted) == len(log)
    truncated = compacted[massive_idx]
    assert "Master context" in truncated.content
    match = re.search(r"bytes (\d+)-(\d+)", truncated.content)
    assert match, truncated.content
    assert int(match.group(1)) == expected_range.byte_start
    assert int(match.group(2)) == expected_range.byte_end
    # The stale message was stubbed, not deleted, so indices still line up.
    assert "Stale tool output pruned" in compacted[0].content
