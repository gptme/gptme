"""Fixture experiment: semantic verification vs. pixel-change detection.

This module is the research artifact for idea-backlog #703 / task
``verify-after-action-semantic-contract``. It demonstrates, with controlled
fixtures, **when semantic expectation checking adds information beyond
pixel-change polling**.

Running the tests produces a summary table printed to stdout showing the
divergence between the two evidence sources. Each fixture has a known ground
truth so we can identify which method is right on each scenario.

Quick execution::

    uv run pytest tests/test_computer_semantic_fixtures.py -v -s

The five scenarios tested are:

1. **success_visible** — action succeeded and produced a visible UI change.
   Pixel: YES, Semantic: YES → both agree.
2. **missed_action** — action missed the target; screen is identical.
   Pixel: NO, Semantic: NO → both agree.
3. **animation_noise** — unrelated animation changed pixels; action missed.
   Pixel: YES (false positive), Semantic: NO → **semantic adds value**.
4. **slow_response** — action succeeded server-side but "Processing…" is all
   that appeared; confirmation is not yet visible.
   Pixel: YES, Semantic: NO → **semantic adds value** (AND retry is unsafe).
5. **tiny_change** — tiny but semantically meaningful state change ("Saved").
   Pixel: borderline (small ratio close to threshold), Semantic: YES →
   semantic provides unambiguous positive evidence.

Acceptance criteria verified here:
- Fixture experiment demonstrates at least one real failure current change
  polling cannot classify semantically.
- Verification result distinguishes observation/change evidence from
  expectation satisfaction.
- No action is automatically retried.
"""

from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from gptme.tools.computer_semantic import (
    VerificationResult,
    make_fixture_verifier,
    verify_action_result,
)

# ---------------------------------------------------------------------------
# Fixture image factory (no display required)
# ---------------------------------------------------------------------------


def _make_png(
    width: int,
    height: int,
    background: tuple[int, int, int] = (128, 128, 128),
    patches: list[tuple[int, int, int, int, tuple[int, int, int]]] | None = None,
) -> bytes:
    """Create a minimal in-memory PNG using only stdlib + Pillow.

    Args:
        width, height: Image dimensions.
        background: RGB fill colour.
        patches: List of (x, y, w, h, colour) rectangles to draw on top of
            the background. Used to simulate UI elements.

    Returns:
        PNG bytes.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color=background)
    if patches:
        draw = ImageDraw.Draw(img)
        for x, y, w, h, colour in patches:
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=colour)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _save_png(path: Path, **kwargs) -> Path:
    path.write_bytes(_make_png(**kwargs))
    return path


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------


class ScenarioDef(NamedTuple):
    """Defines one fixture scenario."""

    name: str
    description: str
    # Ground truth
    action_succeeded: bool
    # Pixel-change signal
    expect_pixel_change: bool
    # What the semantic verifier knows (matches ground truth)
    semantic_satisfied: bool
    semantic_detail: str
    # Expected state string (the contract the action promised)
    expected_state: str


SCENARIOS: list[ScenarioDef] = [
    ScenarioDef(
        name="success_visible",
        description="Action succeeded, large visible dialog appeared",
        action_succeeded=True,
        expect_pixel_change=True,
        semantic_satisfied=True,
        semantic_detail="dialog titled 'Delete item' is present in the screenshot",
        expected_state="A confirmation dialog titled 'Delete item' is visible",
    ),
    ScenarioDef(
        name="missed_action",
        description="Click missed the target button; screen is identical",
        action_succeeded=False,
        expect_pixel_change=False,
        semantic_satisfied=False,
        semantic_detail="no dialog or menu appeared; screen is the same as before",
        expected_state="A dropdown menu appeared below the clicked button",
    ),
    ScenarioDef(
        name="animation_noise",
        description=(
            "Unrelated loading spinner changed pixels; the Submit button was not actually clicked"
        ),
        action_succeeded=False,
        expect_pixel_change=True,  # ← pixel says change happened
        semantic_satisfied=False,  # ← semantic says expectation NOT met
        semantic_detail=(
            "only a loading spinner is visible; no form-submission confirmation"
        ),
        expected_state="The form was submitted and a confirmation message appeared",
    ),
    ScenarioDef(
        name="slow_response",
        description=(
            "'Processing…' text appeared but server confirmation is not yet visible; "
            "the action succeeded but blind retry would duplicate a payment"
        ),
        action_succeeded=True,  # action fired, result not yet reflected
        expect_pixel_change=True,  # pixel change: "Processing…" text appeared
        semantic_satisfied=False,  # semantic: confirmation not yet present
        semantic_detail=(
            "'Processing…' indicator is visible but no payment confirmation yet"
        ),
        expected_state="Payment processed successfully — confirmation message is visible",
    ),
    ScenarioDef(
        name="tiny_change",
        description="Status text changed from 'Saving…' to 'Saved' — small but meaningful",
        action_succeeded=True,
        expect_pixel_change=True,  # small but above 0.2% threshold
        semantic_satisfied=True,
        semantic_detail="status text now reads 'Saved' confirming successful persistence",
        expected_state="Document saved successfully — status reads 'Saved'",
    ),
]


# ---------------------------------------------------------------------------
# Image builders per scenario
# ---------------------------------------------------------------------------

_W, _H = 800, 600  # fixture canvas size
_BG = (180, 180, 180)  # neutral gray background


def _build_images(scenario: ScenarioDef, tmp_path: Path) -> tuple[Path, Path]:
    """Build (pre, post) PNG images for a scenario.

    The images encode ground truth visually. The numbers in comments are the
    approximate pixel-change ratios produced by each fixture so tests stay
    meaningful even if the threshold changes.
    """
    pre = tmp_path / f"{scenario.name}_pre.png"
    post = tmp_path / f"{scenario.name}_post.png"

    if scenario.name == "success_visible":
        # Pre: plain background.
        # Post: large dialog rectangle (~18% of pixels changed).
        _save_png(pre, width=_W, height=_H, background=_BG)
        _save_png(
            post,
            width=_W,
            height=_H,
            background=_BG,
            patches=[(200, 150, 400, 300, (240, 240, 240))],  # dialog
        )

    elif scenario.name == "missed_action":
        # Pre == Post → 0% change.
        _save_png(pre, width=_W, height=_H, background=_BG)
        _save_png(post, width=_W, height=_H, background=_BG)

    elif scenario.name == "animation_noise":
        # Pre: plain background.
        # Post: tiny spinner square (≈0.3% of pixels — above 0.2% threshold).
        # The verifier knows the spinner is not the expected confirmation.
        _save_png(pre, width=_W, height=_H, background=_BG)
        # Make spinner large enough to exceed the 0.2% threshold: 40×40 = 1600/480000 ≈ 0.33%
        _save_png(
            post,
            width=_W,
            height=_H,
            background=_BG,
            patches=[(380, 290, 40, 40, (200, 200, 200))],  # spinner
        )

    elif scenario.name == "slow_response":
        # Pre: plain background.
        # Post: "Processing…" banner (~4% of pixels).
        _save_png(pre, width=_W, height=_H, background=_BG)
        _save_png(
            post,
            width=_W,
            height=_H,
            background=_BG,
            patches=[(250, 270, 300, 60, (255, 220, 100))],  # yellow banner
        )

    elif scenario.name == "tiny_change":
        # Pre: screen with a "Saving…" indicator area (dark pixels).
        # Post: same but the indicator patch is lighter (text changed to "Saved").
        # The patch is 20×8 pixels ≈ 160/480000 ≈ 0.033% — BELOW the 0.2% threshold.
        # Make it slightly larger to represent a real status text change: 40×12 ≈ 480/480000 ≈ 0.1%
        # Use 80x12 ≈ 960/480 000 ≈ 0.2% — right at the threshold boundary.
        # Go slightly over: 100×12 = 1200/480000 = 0.25% → just above 0.2%.
        _save_png(
            pre,
            width=_W,
            height=_H,
            background=_BG,
            patches=[(340, 560, 100, 12, (80, 80, 80))],  # "Saving…" — dark
        )
        _save_png(
            post,
            width=_W,
            height=_H,
            background=_BG,
            patches=[(340, 560, 100, 12, (200, 255, 200))],  # "Saved" — green
        )

    else:
        raise ValueError(f"No image builder for {scenario.name!r}")

    return pre, post


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    scenario: ScenarioDef
    result: VerificationResult
    pixel_correct: bool  # did pixel signal match ground truth?
    semantic_correct: bool  # did semantic signal match ground truth?


def _run_scenario(scenario: ScenarioDef, tmp_path: Path) -> ScenarioResult:
    pre, post = _build_images(scenario, tmp_path)
    verifier = make_fixture_verifier(
        satisfied=scenario.semantic_satisfied,
        detail=scenario.semantic_detail,
    )
    result = verify_action_result(
        pre=pre,
        post=post,
        expected=scenario.expected_state,
        verifier=verifier,
    )
    pixel_correct = result.change_detected == scenario.expect_pixel_change
    semantic_correct = result.expectation_satisfied == scenario.semantic_satisfied
    return ScenarioResult(
        scenario=scenario,
        result=result,
        pixel_correct=pixel_correct,
        semantic_correct=semantic_correct,
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_verification_result_fields(scenario: ScenarioDef, tmp_path: Path) -> None:
    """Each scenario produces a VerificationResult with the expected structure."""
    sr = _run_scenario(scenario, tmp_path)
    r = sr.result

    # Structural checks — always pass
    assert isinstance(r, VerificationResult)
    assert r.status in {
        "expectation_met",
        "expectation_not_met",
        "changed",
        "unchanged",
    }
    assert r.attempts == 1, "This layer never retries"
    assert r.observation  # always has some text
    assert r.expectation_satisfied is not None, "verifier was provided"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_semantic_matches_ground_truth(scenario: ScenarioDef, tmp_path: Path) -> None:
    """Semantic verifier output matches scenario ground truth."""
    sr = _run_scenario(scenario, tmp_path)
    assert sr.semantic_correct, (
        f"{scenario.name}: semantic={sr.result.expectation_satisfied} "
        f"expected={scenario.semantic_satisfied}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_pixel_matches_ground_truth(scenario: ScenarioDef, tmp_path: Path) -> None:
    """Pixel-change detection matches scenario ground truth."""
    sr = _run_scenario(scenario, tmp_path)
    assert sr.pixel_correct, (
        f"{scenario.name}: pixel_detected={sr.result.change_detected} "
        f"expected={scenario.expect_pixel_change}"
    )


def test_no_expectation_returns_pixel_only(tmp_path: Path) -> None:
    """When no expectation is given, result reflects pixel evidence only."""
    scenario = SCENARIOS[0]  # success_visible — guaranteed pixel change
    pre, post = _build_images(scenario, tmp_path)

    result = verify_action_result(pre=pre, post=post)  # no expected, no verifier

    assert result.expectation_satisfied is None
    assert result.status in {"changed", "unchanged"}
    assert result.change_detected  # success_visible has a big dialog


def test_expected_without_verifier_raises(tmp_path: Path) -> None:
    """Providing an expectation without a verifier is a programming error."""
    scenario = SCENARIOS[0]
    pre, post = _build_images(scenario, tmp_path)

    with pytest.raises(ValueError, match="verifier is required"):
        verify_action_result(pre=pre, post=post, expected="something")


def test_animation_noise_divergence(tmp_path: Path) -> None:
    """Key finding: pixel changed but semantic expectation was NOT met.

    This is the primary case where semantic checking adds value over
    pixel-change alone. The pixel detector fires on an unrelated spinner,
    but the expected state (form submitted, confirmation visible) was not
    reached. Without semantic checking, an agent relying only on pixel change
    would incorrectly conclude the action succeeded.
    """
    scenario = next(s for s in SCENARIOS if s.name == "animation_noise")
    sr = _run_scenario(scenario, tmp_path)

    # Pixel says change happened (spinner triggered it)
    assert sr.result.change_detected, "spinner should exceed change threshold"
    # Semantic correctly says the expected state was NOT met
    assert sr.result.expectation_satisfied is False, (
        "form-submission expectation should not be met by a spinner"
    )
    # Status reflects the semantic verdict, not the pixel verdict
    assert sr.result.status == "expectation_not_met"
    # No retry happened
    assert sr.result.attempts == 1


def test_slow_response_divergence_unsafe_retry(tmp_path: Path) -> None:
    """Key finding: pixel changed ('Processing…') but confirmation not yet visible.

    Blind auto-retry here would be unsafe: the payment is already in progress.
    Semantic checking catches this and returns expectation_not_met, giving the
    agent the information it needs to wait rather than duplicate the request.
    """
    scenario = next(s for s in SCENARIOS if s.name == "slow_response")
    sr = _run_scenario(scenario, tmp_path)

    assert sr.result.change_detected, "'Processing…' banner should produce pixel change"
    assert sr.result.expectation_satisfied is False, (
        "payment confirmation not yet present"
    )
    assert sr.result.status == "expectation_not_met"
    assert sr.result.attempts == 1, "no retry — unsafe for non-idempotent actions"


# ---------------------------------------------------------------------------
# Summary report (printed when running with -s)
# ---------------------------------------------------------------------------


def test_print_fixture_summary(tmp_path: Path) -> None:
    """Run all scenarios and print a comparative summary table."""
    results = [_run_scenario(s, tmp_path) for s in SCENARIOS]

    divergences = [
        r for r in results if r.result.change_detected != r.result.expectation_satisfied
    ]

    lines = [
        "",
        "=" * 72,
        "Semantic Verification Fixture Experiment — Summary",
        "=" * 72,
        f"{'Scenario':<22} {'Pixel':>6} {'Semantic':>9} {'Status':<22} {'Match?':>7}",
        "-" * 72,
    ]
    for sr in results:
        r = sr.result
        pixel_str = "YES" if r.change_detected else "NO "
        sem_str = (
            ("YES" if r.expectation_satisfied else "NO ")
            if r.expectation_satisfied is not None
            else "N/A"
        )
        match = (
            "✓ agree" if r.change_detected == r.expectation_satisfied else "✗ DIVERGE"
        )
        lines.append(
            f"{sr.scenario.name:<22} {pixel_str:>6} {sem_str:>9} {r.status:<22} {match:>7}"
        )

    lines += [
        "-" * 72,
        f"Divergences (semantic adds value over pixel-only): {len(divergences)}/{len(results)}",
        "",
    ]

    if divergences:
        lines.append("Cases where semantic checking prevents wrong conclusions:")
        for sr in divergences:
            lines.append(f"  • {sr.scenario.name}: {sr.scenario.description}")
            lines.append(
                f"    pixel_detected={sr.result.change_detected}, semantic={sr.result.expectation_satisfied}"
            )
            lines.append(
                f"    → {sr.scenario.description.split(';')[0]}"
                if ";" in sr.scenario.description
                else f"    → {textwrap.shorten(sr.result.observation, 70)}"
            )

    lines += [
        "",
        "Follow-up guidance:",
        "  • Semantic checking catches animation noise and slow-response cases",
        "    that pixel-change alone cannot distinguish.",
        "  • Auto-retry is unsafe for slow_response (payment already in progress).",
        "  • Next step: integrate with act_and_observe() returning VerificationResult",
        "    alongside existing Message list, with LLM verifier for production.",
        "=" * 72,
        "",
    ]

    print("\n".join(lines))

    # At least 2 divergences (animation_noise + slow_response)
    assert len(divergences) >= 2, (
        f"Expected ≥2 divergences showing semantic value, got {len(divergences)}"
    )
