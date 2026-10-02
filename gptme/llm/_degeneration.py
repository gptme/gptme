"""Client-side degeneration guard for streamed LLM responses.

Detects repetitive output in OpenRouter streams and raises
``DegenerationDetected`` so the caller can abort or retry on another
subprovider.
"""

from __future__ import annotations

import logging
import os
import re
from collections import deque

logger = logging.getLogger(__name__)

_ENV_DEGEN_THRESHOLD = "GPTME_DEGENERATION_THRESHOLD"
_ENV_DEGEN_RETRY = "GPTME_DEGENERATION_RETRY"
_DEGEN_THRESHOLD_DEFAULT = 0.85
# n-gram length (chars). ~32 chars ≈ 8 tokens at 4 chars/token.
_DEGEN_NGRAM = 32
# Rolling window size (chars) over which the repetition score is computed.
_DEGEN_WINDOW = 2000
# Number of consecutive above-threshold checks before tripping the guard.
_DEGEN_TRIP_COUNT = 3
# Minimum chars of non-code content to accumulate before checking.
_DEGEN_MIN_CONTENT = 400
# Chars of new non-code content between each check.
_DEGEN_CHECK_INTERVAL = 200
# Max chars withheld while a clean retry is still possible.  Bounds the buffer
# and keeps output flowing for responses that never accumulate enough non-code
# text to disarm (e.g. code-only replies); once flushed, a later trip aborts
# rather than retries, so abandoned output is still never appended.
_DEGEN_WITHHOLD_MAX = 8192


class DegenerationDetected(RuntimeError):
    """Raised when repetition invalidates a streamed response."""

    def __init__(self, provider: str | None, score: float) -> None:
        self.degenerate_provider = provider
        self.score = score
        super().__init__(
            f"Degeneration detected (score={score:.2f}, provider={provider!r})"
        )


class _RepetitionDetector:
    """Sliding-window n-gram repetition detector for streamed text.

    Tracks the fraction of repeated n-gram positions in the last
    ``window`` characters of accumulated *non-code* content.  The guard
    trips when the score stays above ``threshold`` for ``trip_count``
    consecutive checks.

    Only non-code text is scored: fenced code blocks are tracked across
    chunk boundaries (a fence split between two chunks is still
    recognized) and excluded from both the check cadence and the score, so
    legitimately repetitive code (tables, test cases) cannot trip the
    guard.  The scored buffer is bounded to ``window`` characters, so the
    detector does not retain or re-join the whole stream.
    """

    def __init__(
        self,
        *,
        threshold: float = _DEGEN_THRESHOLD_DEFAULT,
        ngram: int = _DEGEN_NGRAM,
        window: int = _DEGEN_WINDOW,
        trip_count: int = _DEGEN_TRIP_COUNT,
        min_content: int = _DEGEN_MIN_CONTENT,
        check_interval: int = _DEGEN_CHECK_INTERVAL,
    ) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be in (0, 1], got {threshold!r}")
        self._threshold = threshold
        self._ngram = ngram
        self._window = window
        self.trip_count = trip_count
        self._min_content = min_content
        self._check_interval = check_interval

        # Rolling window over non-code text only, bounded to `window` chars.
        self._clean_chunks: deque[str] = deque()
        self._window_len = 0  # chars currently held in _clean_chunks
        self._clean_len = 0  # non-code chars accumulated (lifetime)
        self._since_check = 0  # non-code chars since last check

        # Code-fence tracking (``` fences only), carried across chunks so a
        # fence split between two chunks is still recognized.
        self._in_code = False
        self._line_carry = ""
        self._carry_counted_len = 0
        self._fence_re = re.compile(r"^[ \t]*```")

        self._consecutive = 0
        self.checks_performed = 0
        self.tripped = False
        self.score: float = 0.0

    # ------------------------------------------------------------------
    def feed(self, text: str) -> bool:
        """Feed a new chunk of streamed text.

        Returns True when the degeneration guard trips (first time only).
        Subsequent calls are no-ops after tripping.
        """
        if self.tripped or not text:
            return self.tripped

        self._ingest(text)

        # A single large chunk may span multiple check intervals; loop until
        # we've consumed all pending intervals (or the guard trips).
        while (
            self._clean_len >= self._min_content
            and self._since_check >= self._check_interval
        ):
            self._since_check -= self._check_interval
            self.checks_performed += 1
            self.score = self._compute_score()

            if self.score >= self._threshold:
                self._consecutive += 1
            else:
                self._consecutive = 0

            if self._consecutive >= self.trip_count:
                self.tripped = True
                return True

        return self.tripped

    # ------------------------------------------------------------------
    def _ingest(self, text: str) -> None:
        """Split ``text`` into lines and append its non-code content.

        Incomplete trailing lines are carried into the next chunk so a fence
        marker that straddles a chunk boundary is still detected.  The carried
        fragment is counted provisionally and rolled back when it completes, so
        a stream that never sends a final newline is still scored.
        """
        # Roll back the provisional fragment from the scored window.  Its
        # lifetime/check counters stay monotonic because the bytes were already
        # streamed and any checks they triggered must not run a second time.
        previous_carry_len = self._carry_counted_len
        if previous_carry_len:
            self._drop_buffer_tail(previous_carry_len)
            self._carry_counted_len = 0

        combined = self._line_carry + text
        self._line_carry = ""
        chunk_lines = combined.splitlines(keepends=True)
        if chunk_lines and not chunk_lines[-1].endswith(("\n", "\r")):
            self._line_carry = chunk_lines.pop()

        for line in chunk_lines:
            stripped = line.rstrip("\r\n")
            if self._fence_re.match(stripped):
                self._in_code = not self._in_code
            if not self._in_code and line:
                self._append_buffer(line, count_from=previous_carry_len)
            previous_carry_len = 0

        # Count the trailing fragment provisionally (fence lines are excluded
        # once the state has flipped).
        if self._line_carry and not self._in_code:
            self._append_buffer(self._line_carry, count_from=previous_carry_len)
            self._carry_counted_len = len(self._line_carry)

    # ------------------------------------------------------------------
    def _append_buffer(self, text: str, *, count_from: int = 0) -> None:
        """Append non-code text, counting only the not-yet-seen suffix."""
        self._clean_chunks.append(text)
        self._window_len += len(text)
        new_len = max(len(text) - count_from, 0)
        self._clean_len += new_len
        self._since_check += new_len
        # Trim the window from the front.  A chunk larger than the window is
        # partially kept (its tail) rather than evicted whole: otherwise a
        # single long newline-free line would empty the window and hide
        # repetition behind a score of 0.
        while self._window_len > self._window and self._clean_chunks:
            head = self._clean_chunks[0]
            drop = self._window_len - self._window
            if drop >= len(head):
                self._clean_chunks.popleft()
                self._window_len -= len(head)
            else:
                self._clean_chunks[0] = head[drop:]
                self._window_len -= drop

    # ------------------------------------------------------------------
    def _drop_buffer_tail(self, count: int) -> None:
        """Remove ``count`` chars from the tail of the scored buffer."""
        while count > 0 and self._clean_chunks:
            last = self._clean_chunks[-1]
            if len(last) <= count:
                self._clean_chunks.pop()
                self._window_len -= len(last)
                count -= len(last)
            else:
                self._clean_chunks[-1] = last[:-count]
                self._window_len -= count
                count = 0

    def _compute_score(self) -> float:
        """Return the fraction of repeated n-gram positions in the window.

        0.0 = no repetition; 1.0 = every position is a duplicate.
        """
        # Rolling window from the tail of accumulated non-code chunks.
        buf = "".join(self._clean_chunks)
        window = buf[-self._window :]

        n = self._ngram
        total = len(window) - n + 1
        if total <= 0:
            return 0.0

        seen: set[str] = set()
        repeated = 0
        for i in range(total):
            ng = window[i : i + n]
            if ng in seen:
                repeated += 1
            else:
                seen.add(ng)

        return repeated / total


def _degeneration_threshold() -> float | None:
    """Return the configured threshold, or None when the guard is disabled.

    The value must be a finite float in (0, 1]; anything else (including
    ``nan``, infinities, negatives, or a value above 1) is rejected in favour
    of the default rather than silently disabling or over-arming the guard.
    """
    value = os.environ.get(_ENV_DEGEN_THRESHOLD)
    if value is None:
        return _DEGEN_THRESHOLD_DEFAULT  # on by default
    value = value.strip().lower()
    if value in {"0", "false", "off", "no", "disabled"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        parsed = None
    if parsed is None or not (0.0 < parsed <= 1.0):
        logger.warning(
            "%s=%r is not a finite threshold in (0, 1]; using default %.2f",
            _ENV_DEGEN_THRESHOLD,
            value,
            _DEGEN_THRESHOLD_DEFAULT,
        )
        return _DEGEN_THRESHOLD_DEFAULT
    return parsed


def _degeneration_retry_enabled() -> bool:
    """Whether a detected degeneration may retry on another subprovider.

    Off by default.  A same-turn retry is only safe when nothing has been
    handed to the caller yet — otherwise the retry's output is appended to
    abandoned text the caller has already accumulated.  Enabling this makes
    the stream withhold its prefix until the guard has had its earliest chance
    to trip, so an early trip can be discarded cleanly; a trip that happens
    after the prefix is flushed aborts the stream without a retry.
    """
    return os.environ.get(_ENV_DEGEN_RETRY, "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
        "enabled",
    }
