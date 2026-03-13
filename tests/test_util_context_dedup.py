"""Tests for gptme.util.context_dedup."""

from typing import Literal

from gptme.message import Message
from gptme.util.context_dedup import ContextDeduplicator, is_content_in_context

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _msg(
    content: str, role: Literal["system", "user", "assistant"] = "system"
) -> Message:
    return Message(role, content)


AGENTS_MD = """\
# AGENTS.md

This is Bob's workspace configuration.

## Section A
Some detailed instructions spanning multiple lines
to ensure paragraph chunks meet the minimum length threshold.

## Section B
More instructions here about how to behave and what to do.
"""

RETRIEVAL_DOC = """\
# Retrieved Document

This document was retrieved from the knowledge base.
It contains useful information about the topic at hand.
Multiple paragraphs ensure proper chunk indexing.
"""

SHORT_CONTENT = "Short."


# ---------------------------------------------------------------------------
# is_content_in_context
# ---------------------------------------------------------------------------


class TestIsContentInContext:
    def test_exact_match(self):
        msgs = [_msg("Hello world, this is some content")]
        assert is_content_in_context("Hello world, this is some content", msgs)

    def test_substring_match(self):
        msgs = [_msg("Prefix. Hello world. Suffix.")]
        assert is_content_in_context("Hello world", msgs)

    def test_no_match(self):
        msgs = [_msg("Something completely different")]
        assert not is_content_in_context("Hello world", msgs)

    def test_whitespace_normalisation(self):
        # Extra spaces / newlines should not prevent a match
        msgs = [_msg("Hello   world\nsome   content")]
        assert is_content_in_context("Hello world some content", msgs)

    def test_empty_content_returns_false(self):
        msgs = [_msg("Anything")]
        assert not is_content_in_context("", msgs)
        assert not is_content_in_context("   ", msgs)

    def test_empty_message_list(self):
        assert not is_content_in_context("Hello", [])

    def test_multiple_messages_match_second(self):
        msgs = [_msg("First message"), _msg("Second message with the needle")]
        assert is_content_in_context("needle", msgs)

    def test_multiple_messages_no_match(self):
        msgs = [_msg("First"), _msg("Second")]
        assert not is_content_in_context("needle", msgs)

    def test_user_message_role(self):
        msgs = [_msg("User said hello", role="user")]
        assert is_content_in_context("User said hello", msgs)


# ---------------------------------------------------------------------------
# ContextDeduplicator — basic operations
# ---------------------------------------------------------------------------


class TestContextDeduplicatorBasic:
    def test_present_after_init(self):
        msgs = [_msg(AGENTS_MD)]
        dedup = ContextDeduplicator(msgs)
        assert dedup.is_present(AGENTS_MD)

    def test_not_present_after_init(self):
        msgs = [_msg(AGENTS_MD)]
        dedup = ContextDeduplicator(msgs)
        assert not dedup.is_present(RETRIEVAL_DOC)

    def test_present_after_mark(self):
        dedup = ContextDeduplicator([])
        assert not dedup.is_present(RETRIEVAL_DOC)
        dedup.mark_present(RETRIEVAL_DOC)
        assert dedup.is_present(RETRIEVAL_DOC)

    def test_empty_messages_list(self):
        dedup = ContextDeduplicator([])
        assert not dedup.is_present(AGENTS_MD)

    def test_empty_content_not_present(self):
        dedup = ContextDeduplicator([_msg(AGENTS_MD)])
        assert not dedup.is_present("")
        assert not dedup.is_present("   ")

    def test_short_content_exact_match(self):
        # Short content below _MIN_CHUNK_LEN should still match via full-content hash
        msgs = [_msg(SHORT_CONTENT)]
        dedup = ContextDeduplicator(msgs)
        assert dedup.is_present(SHORT_CONTENT)

    def test_short_content_no_false_positive(self):
        msgs = [_msg(SHORT_CONTENT)]
        dedup = ContextDeduplicator(msgs)
        assert not dedup.is_present("Different short.")

    def test_idempotent_mark_present(self):
        dedup = ContextDeduplicator([])
        dedup.mark_present(RETRIEVAL_DOC)
        dedup.mark_present(RETRIEVAL_DOC)  # second call should not raise
        assert dedup.is_present(RETRIEVAL_DOC)


# ---------------------------------------------------------------------------
# ContextDeduplicator — chunk / paragraph detection
# ---------------------------------------------------------------------------


class TestContextDeduplicatorChunks:
    def test_paragraph_of_larger_message_is_detected(self):
        """A paragraph within a larger indexed message should be detected as present."""
        big_msg = _msg(AGENTS_MD)
        dedup = ContextDeduplicator([big_msg])

        # The indexed chunks are split on \n\n, so this paragraph includes its header:
        # "## Section A\nSome detailed instructions..."
        section_a_para = "## Section A\nSome detailed instructions spanning multiple lines\nto ensure paragraph chunks meet the minimum length threshold."
        assert dedup.is_present(section_a_para)

    def test_document_already_in_big_message_detected(self):
        """If a retrieval doc is fully embedded in a system message it should be detected."""
        combined = "Preamble text.\n\n" + RETRIEVAL_DOC + "\n\nTrailing text."
        msgs = [_msg(combined)]
        dedup = ContextDeduplicator(msgs)
        assert dedup.is_present(RETRIEVAL_DOC)

    def test_full_doc_not_falsely_detected_in_unrelated(self):
        msgs = [_msg(AGENTS_MD)]
        dedup = ContextDeduplicator(msgs)
        assert not dedup.is_present(RETRIEVAL_DOC)


# ---------------------------------------------------------------------------
# ContextDeduplicator — update methods
# ---------------------------------------------------------------------------


class TestContextDeduplicatorUpdate:
    def test_update_adds_new_message(self):
        dedup = ContextDeduplicator([])
        assert not dedup.is_present(RETRIEVAL_DOC)
        dedup.update(_msg(RETRIEVAL_DOC))
        assert dedup.is_present(RETRIEVAL_DOC)

    def test_update_from_log_incremental(self):
        initial = [_msg(AGENTS_MD)]
        dedup = ContextDeduplicator(initial)

        new_messages = initial + [_msg(RETRIEVAL_DOC)]
        dedup.update_from_log(new_messages)

        assert dedup.is_present(AGENTS_MD)
        assert dedup.is_present(RETRIEVAL_DOC)

    def test_update_from_log_idempotent(self):
        msgs = [_msg(AGENTS_MD)]
        dedup = ContextDeduplicator(msgs)
        dedup.update_from_log(msgs)  # re-processing same messages — no-op
        assert dedup.is_present(AGENTS_MD)


# ---------------------------------------------------------------------------
# Integration: simulate a STEP_PRE retrieval workflow
# ---------------------------------------------------------------------------


class TestRetrievalWorkflowSimulation:
    """Simulate the canonical STEP_PRE deduplication use-case."""

    def test_skip_already_injected_document(self):
        # AGENTS.md was loaded as a static file at session start
        existing_context = [_msg(AGENTS_MD, role="system")]
        dedup = ContextDeduplicator(existing_context)

        # Retrieval returns AGENTS.md as a result (cross-source dup!)
        injected = []
        retrieval_results = [AGENTS_MD, RETRIEVAL_DOC]
        for doc in retrieval_results:
            if not dedup.is_present(doc):
                dedup.mark_present(doc)
                injected.append(doc)

        # Only RETRIEVAL_DOC should be injected; AGENTS.md was already there
        assert injected == [RETRIEVAL_DOC]

    def test_second_step_does_not_reinject(self):
        dedup = ContextDeduplicator([])
        dedup.mark_present(RETRIEVAL_DOC)

        # On a second step the same document should be skipped
        injected = []
        for doc in [RETRIEVAL_DOC]:
            if not dedup.is_present(doc):
                dedup.mark_present(doc)
                injected.append(doc)

        assert injected == []
