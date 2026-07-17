"""Semantic verification contract for desktop computer actions.

Prototype: adds an explicit expected-state contract on top of the pixel-change
detection already in :func:`~gptme.tools.computer.act_and_observe`. The goal is
to separate observation *evidence* (screen changed) from expectation
*satisfaction* (the intended state was reached).

This module is **not** a retry layer. It returns structured evidence for the
caller to decide how to recover. Auto-retry belongs in a separate, opt-in layer
restricted to explicitly idempotent actions.

Typical usage in a tool or agent that already has pre/post screenshots::

    from gptme.tools.computer_semantic import VerificationResult, verify_action_result

    result = verify_action_result(
        pre=pre_screenshot_path,
        post=post_screenshot_path,
        expected="A confirmation dialog titled 'Delete item' is visible",
        verifier=my_llm_verifier,   # or None for pixel-only mode
    )
    if result.status == "expectation_not_met":
        # Inform the agent without retrying the original action
        ...

The :class:`SemanticVerifier` protocol documents the interface a production LLM
verifier must implement. The :func:`make_fixture_verifier` helper provides a
deterministic stand-in for unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from pathlib import Path

# Change threshold matches _poll_for_change default: 0.2% detects small text
# updates (typing in a terminal) while keeping false-positive rate near zero
# in static Xvfb environments (PNG is lossless → identical frames read 0.0%).
_DEFAULT_CHANGE_THRESHOLD = 0.002


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
    """Structured result from a semantic action verification check.

    Separates what pixel-change polling detected from what semantic evaluation
    concluded. Both pieces of evidence are preserved so the caller can choose
    how to handle mismatches.

    Attributes:
        status: Summary verdict — one of:
            ``"expectation_met"``   — semantic check passed.
            ``"expectation_not_met"`` — semantic check failed despite possible pixel change.
            ``"changed"``           — pixel change detected, no semantic check requested.
            ``"unchanged"``         — no pixel change, no semantic check.
        change_detected: True when the post-action screenshot differs from the
            pre-action baseline by more than *change_threshold* pixel ratio.
        expectation_satisfied: True/False from the semantic verifier, or None
            when no expectation was requested.
        observation: Human-readable summary combining pixel-change and semantic
            evidence, suitable for inclusion in an agent message.
        attempts: Always 1 in this layer. A retry policy that increments this
            belongs in a separate, opt-in caller.
    """

    status: Literal["expectation_met", "expectation_not_met", "changed", "unchanged"]
    change_detected: bool
    expectation_satisfied: bool | None  # None → no expectation given
    observation: str
    attempts: int = field(default=1)

    @property
    def is_successful(self) -> bool:
        """True when the result is positive by the best available signal."""
        if self.expectation_satisfied is not None:
            return self.expectation_satisfied
        return self.change_detected


# ---------------------------------------------------------------------------
# Verifier protocol
# ---------------------------------------------------------------------------


class SemanticVerifier(Protocol):
    """Callable that decides whether a screenshot satisfies a natural-language expectation.

    Args:
        screenshot: Path to the post-action (settled) screenshot.
        expected: Natural-language description of the intended post-action state.

    Returns:
        A (satisfied, detail) tuple. ``satisfied`` is True when the screenshot
        matches the expectation; ``detail`` is a one-sentence explanation that
        becomes part of :attr:`VerificationResult.observation`.
    """

    def __call__(self, screenshot: Path, expected: str) -> tuple[bool, str]: ...


# ---------------------------------------------------------------------------
# Core verification function
# ---------------------------------------------------------------------------


def verify_action_result(
    pre: Path,
    post: Path,
    expected: str | None = None,
    verifier: SemanticVerifier | None = None,
    change_threshold: float = _DEFAULT_CHANGE_THRESHOLD,
) -> VerificationResult:
    """Compare pre/post screenshots and optionally apply a semantic expectation check.

    Pixel-change detection runs unconditionally. Semantic evaluation is layered
    on top when both *expected* and *verifier* are provided. Neither path
    triggers an automatic retry.

    Args:
        pre: Screenshot captured **before** the action (baseline).
        post: Screenshot captured **after** the action settled.
        expected: Natural-language description of the intended post-action state.
            If None, only pixel-change evidence is returned.
        verifier: Callable implementing :class:`SemanticVerifier`. Required when
            *expected* is provided; ignored otherwise.
        change_threshold: Pixel ratio above which a change is considered
            detected. Matches the default in :func:`_poll_for_change`.

    Returns:
        :class:`VerificationResult` with pixel and semantic evidence.

    Raises:
        ValueError: If *expected* is provided without a *verifier*.

    Example::

        result = verify_action_result(
            pre=Path("/tmp/before.png"),
            post=Path("/tmp/after.png"),
            expected="A confirmation dialog is visible",
            verifier=my_llm_verifier,
        )
        print(result.status)        # "expectation_met" or "expectation_not_met"
        print(result.change_detected)  # True/False from pixel comparison
        print(result.observation)   # human-readable summary
    """
    if expected is not None and verifier is None:
        raise ValueError("A verifier is required when expected is provided")

    # --- Pixel-change evidence --------------------------------------------------
    change_ratio = _compute_change_ratio(pre, post)
    change_detected = change_ratio >= change_threshold
    pixel_summary = (
        f"Screen {'changed' if change_detected else 'unchanged'} "
        f"({change_ratio:.3%} pixels differ)"
    )

    # --- Semantic evidence (optional) ------------------------------------------
    expectation_satisfied: bool | None = None
    semantic_detail = ""

    if expected is not None and verifier is not None:
        expectation_satisfied, semantic_detail = verifier(post, expected)

    # --- Compose observation ---------------------------------------------------
    if semantic_detail:
        observation = f"{pixel_summary}; {semantic_detail}"
    else:
        observation = pixel_summary

    # --- Derive status ---------------------------------------------------------
    if expectation_satisfied is not None:
        status: Literal[
            "expectation_met", "expectation_not_met", "changed", "unchanged"
        ] = "expectation_met" if expectation_satisfied else "expectation_not_met"
    elif change_detected:
        status = "changed"
    else:
        status = "unchanged"

    return VerificationResult(
        status=status,
        change_detected=change_detected,
        expectation_satisfied=expectation_satisfied,
        observation=observation,
    )


# ---------------------------------------------------------------------------
# Test helpers (also documents the production LLM verifier shape)
# ---------------------------------------------------------------------------


def make_fixture_verifier(satisfied: bool, detail: str = "") -> SemanticVerifier:
    """Return a deterministic verifier for use in fixture tests.

    Args:
        satisfied: The answer the verifier always returns.
        detail: Optional explanation string.
    """

    def _verifier(screenshot: Path, expected: str) -> tuple[bool, str]:
        return satisfied, detail or (
            "expectation satisfied" if satisfied else "expectation not met"
        )

    return _verifier


def make_llm_verifier_stub() -> SemanticVerifier:
    """Stub for a production LLM verifier — documents the integration shape.

    A real implementation would:
    1. Encode the screenshot as base64.
    2. Call an LLM with a system prompt like:
       "You decide whether a screenshot satisfies a stated expected state.
        Reply with JSON: {satisfied: bool, reason: str}"
    3. Parse the response and return (satisfied, reason).

    This stub always raises to make it obvious when test code accidentally
    reaches the production path.
    """

    def _verifier(
        screenshot: Path, expected: str
    ) -> tuple[bool, str]:  # pragma: no cover
        raise NotImplementedError(
            "LLM verifier not implemented in prototype. "
            "Use make_fixture_verifier() in tests. "
            "For production, call an LLM with the screenshot + expected string."
        )

    return _verifier


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _compute_change_ratio(path1: Path, path2: Path) -> float:
    """Pixel-change ratio between two screenshots (0.0–1.0).

    Mirrors ``_compute_change_ratio`` from ``computer.py`` so this module can
    be imported and tested independently, without importing the full computer
    tool (which has heavy optional dependencies).
    """
    try:
        from PIL import Image, ImageChops

        img1 = Image.open(path1).convert("RGB")
        img2 = Image.open(path2).convert("RGB")
        if img1.size != img2.size:
            return 0.0
        diff = ImageChops.difference(img1, img2)
        total_pixels = img1.width * img1.height
        raw = diff.tobytes()
        nonzero = sum(
            1 for i in range(0, len(raw), 3) if raw[i] or raw[i + 1] or raw[i + 2]
        )
        return nonzero / total_pixels
    except Exception:
        return 0.0
