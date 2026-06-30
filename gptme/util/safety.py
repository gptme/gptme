"""
Deceptive Content Detector — slim local-only heuristics for agent output safety scoring.

Ported from Bob's private prototype (scripts/deceptive-content-detector.py, Phase 1).
This module uses NO external APIs and NO network calls — purely regex-based heuristics
so it can run anywhere gptme is installed.

Checks:
  - Hedging / uncertainty phrases (may inflate LLM confidence claims)
  - Jailbreak bypass indicators (could signal prompt injection in agent outputs)

The composite_score is a float in [0, 1]:
  0.0  = no signals detected
  >0.3 = moderate concern
  >0.5 = high risk — warrant manual review

Intentionally NOT included: URL liveness checks, LLM-as-Judge scoring.
These belong in a future opt-in extension, not in the local CLI path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# ── Heuristic pattern sets ────────────────────────────────────────────────

_HEDGING_PATTERNS: list[str] = [
    r"\bI think\b",
    r"\bit['']s possible\b",
    r"\bsome (?:experts|people|researchers|studies) (?:say|claim|suggest|believe)\b",
    r"\bmay (?:be|have|indicate)\b",
    r"\bmight (?:be|have|indicate)\b",
    r"\bcould (?:be|have|indicate|suggest)\b",
    r"\bto the best of my knowledge\b",
    r"\bas far as I know\b",
    r"\bI believe\b",
    r"\bI suspect\b",
    r"\bit['']s unclear\b",
    r"\bnot entirely (?:sure|certain|clear)\b",
    r"\barguably\b",
    r"\bpresumably\b",
    r"\bconceivably\b",
    r"\bfrom what I understand\b",
    r"\bI['']m not (?:sure|certain|aware)\b",
]

_HEDGING_RE = re.compile("|".join(_HEDGING_PATTERNS), re.IGNORECASE)

_JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bignore (all )?((your|previous|prior|the) )+(instructions|directives|guidelines)\b",
        "ignore_instructions",
    ),
    (
        r"\byou are now (free to|allowed to|an AI without restrictions)\b",
        "role_escape",
    ),
    (r"\bDAN\b", "dan_character"),
    (
        r"\bhypothetical (scenario|situation) where you can\b",
        "hypothetical_bypass",
    ),
    (
        r"\bpretend you (have no|don['']t have any) (restrictions|limits|rules)\b",
        "pretend_unrestricted",
    ),
    (
        r"\bin this (game|story|roleplay|scenario), you (can|are allowed to)\b",
        "roleplay_bypass",
    ),
]


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class SegmentScore:
    """Safety score for one assistant message segment."""

    text: str
    segment_index: int
    hedging_count: int = 0
    hedging_phrases: list[str] = field(default_factory=list)
    jailbreak_indicators: list[str] = field(default_factory=list)

    @property
    def composite_score(self) -> float:
        """Weighted composite risk in [0, 1]."""
        score = 0.0
        word_count = max(len(self.text.split()), 1)
        # Hedging density: cap contribution at 0.4
        hedging_density = self.hedging_count / word_count
        score += min(hedging_density * 20, 0.4)
        # Jailbreak indicators: each adds 0.15, capped at 0.6
        score += min(len(self.jailbreak_indicators) * 0.15, 0.6)
        return round(min(score, 1.0), 3)

    def to_dict(self) -> dict:
        return {
            "segment_index": self.segment_index,
            "composite_score": self.composite_score,
            "hedging_count": self.hedging_count,
            "hedging_phrases": self.hedging_phrases,
            "jailbreak_indicators": self.jailbreak_indicators,
            "segment_length_chars": len(self.text),
        }


@dataclass
class SafetyReport:
    """Full safety report for a conversation."""

    input_source: str
    total_segments: int
    segments: list[SegmentScore] = field(default_factory=list)

    @property
    def overall_risk(self) -> float:
        if not self.segments:
            return 0.0
        total_chars = sum(len(s.text) for s in self.segments) or 1
        weighted = sum(s.composite_score * len(s.text) for s in self.segments)
        return round(weighted / total_chars, 3)

    @property
    def max_risk(self) -> float:
        if not self.segments:
            return 0.0
        return max(s.composite_score for s in self.segments)

    @property
    def flags(self) -> list[str]:
        result = []
        if self.overall_risk > 0.5:
            result.append("HIGH_OVERALL_RISK")
        if self.max_risk > 0.7:
            result.append("CRITICAL_SEGMENT")
        jb_segs = sum(1 for s in self.segments if s.jailbreak_indicators)
        if jb_segs > 0:
            result.append("JAILBREAK_INDICATORS")
        return result

    def to_dict(self) -> dict:
        return {
            "input_source": self.input_source,
            "total_segments": self.total_segments,
            "overall_risk": self.overall_risk,
            "max_risk": self.max_risk,
            "flags": self.flags,
            "segments": [s.to_dict() for s in self.segments],
        }

    def to_text(self) -> str:
        lines = [
            f"Safety Check — {self.input_source}",
            f"Overall risk: {self.overall_risk:.3f}  |  Max segment risk: {self.max_risk:.3f}",
            f"Flags: {', '.join(self.flags) if self.flags else 'none'}",
            f"Segments analysed: {self.total_segments}",
            "",
        ]
        flagged = [s for s in self.segments if s.composite_score > 0.1]
        if not flagged:
            lines.append("  ✓ No significant risk signals detected.")
        else:
            for seg in flagged:
                label = (
                    "HIGH"
                    if seg.composite_score > 0.5
                    else "MEDIUM"
                    if seg.composite_score > 0.3
                    else "low"
                )
                lines.append(
                    f"  [{seg.segment_index}] risk={seg.composite_score:.3f} ({label})  {len(seg.text)}c"
                )
                if seg.hedging_count:
                    sample = seg.hedging_phrases[:3]
                    lines.append(
                        f"       hedging×{seg.hedging_count}: {', '.join(sample)}"
                    )
                if seg.jailbreak_indicators:
                    lines.append(
                        f"       jailbreak indicators: {seg.jailbreak_indicators}"
                    )
        return "\n".join(lines)


# ── Core analysis functions ───────────────────────────────────────────────


def _score_segment(text: str, index: int) -> SegmentScore:
    seg = SegmentScore(text=text, segment_index=index)
    hedging_matches = _HEDGING_RE.findall(text)
    seg.hedging_count = len(hedging_matches)
    # Deduplicate phrases while preserving order
    seen: set[str] = set()
    for m in hedging_matches:
        phrase = m.strip().lower()
        if phrase and phrase not in seen:
            seen.add(phrase)
            seg.hedging_phrases.append(phrase)

    for pattern, label in _JAILBREAK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            seg.jailbreak_indicators.append(label)

    return seg


def check_messages(messages: Sequence, source: str = "<conversation>") -> SafetyReport:
    """Score assistant messages in a conversation for deceptive content signals.

    Args:
        messages: Sequence of message objects with .role and .content attributes.
        source: Label for the report header (e.g. conversation ID).

    Returns:
        SafetyReport with per-segment scores and overall risk.
    """
    assistant_messages = [
        m for m in messages if getattr(m, "role", None) == "assistant"
    ]
    report = SafetyReport(input_source=source, total_segments=len(assistant_messages))
    for i, msg in enumerate(assistant_messages):
        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            # Handle structured content blocks
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        seg = _score_segment(str(content), index=i)
        report.segments.append(seg)
    return report


def check_text(text: str, source: str = "<text>") -> SafetyReport:
    """Score raw text (split into paragraphs as segments) for risk signals."""

    class _FakeMsg:
        def __init__(self, content: str):
            self.role = "assistant"
            self.content = content

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    return check_messages([_FakeMsg(p) for p in paragraphs], source=source)
