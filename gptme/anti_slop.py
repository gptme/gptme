"""Lightweight LLM-slop detector and quality gate.

Detects recognisable tells of AI-generated blandness — hedging openers,
ChatGPT vocabulary tics, negative parallelism, canned enthusiasm, em-dash
abuse, staccato cadence — and returns a weighted score per 1 000 words.

The gate converts that score to pass / warn / fail with calibrated defaults
based on a corpus of 1 098 technical blog posts (balanced mode).  Callers
can tune thresholds or pick a named sensitivity preset:

    "relaxed"  — heavy em-dash writers, personal blogs  (em_tol=8/1k, warn=15, fail=30)
    "balanced" — general technical writing, default      (em_tol=5/1k, warn=12, fail=25)
    "strict"   — suspected AI output, external content   (em_tol=3/1k, warn=8,  fail=20)

Stdlib-only; no external dependencies.

Example::

    from gptme.anti_slop import evaluate_gate

    result = evaluate_gate(text)          # balanced mode
    result = evaluate_gate(text, mode="strict")
    print(result["status"])               # "pass" | "warn" | "fail"
"""

from __future__ import annotations

import re
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Pattern registry
# (category, weight, regex, label)
# weight: 3=high-confidence tell, 2=moderate, 1=soft (overlaps legit prose)
# ---------------------------------------------------------------------------
_RAW: list[tuple[str, int, str, str]] = [
    # Hedging / filler openers
    (
        "hedging",
        3,
        r"\bit(?:'s| is) worth (?:noting|mentioning)\b",
        "it's worth noting",
    ),
    (
        "hedging",
        3,
        r"\bit(?:'s| is) important to (?:note|remember|understand)\b",
        "it's important to note",
    ),
    ("hedging", 2, r"\bneedless to say\b", "needless to say"),
    ("hedging", 2, r"\bthat (?:being |)said,?\b", "that said"),
    ("hedging", 2, r"\bat the end of the day\b", "at the end of the day"),
    # ChatGPT vocabulary tics
    ("vocab_strong", 3, r"\bdelv(?:e|ing|es)\b", "delve"),
    ("vocab_strong", 3, r"\btapestr(?:y|ies)\b", "tapestry"),
    ("vocab_strong", 3, r"\b(?:a |)testament to\b", "testament to"),
    ("vocab_strong", 3, r"\bin the realm of\b", "in the realm of"),
    (
        "vocab_strong",
        3,
        r"\bnavigat(?:e|ing) the (?:complexit|landscape|intricac)",
        "navigate the complexities",
    ),
    ("vocab_strong", 3, r"\bever-(?:evolving|changing|growing)\b", "ever-evolving"),
    ("vocab_strong", 2, r"\b(?:rich |)tapestry of\b", "tapestry of"),
    ("vocab_strong", 2, r"\bunderscor(?:e|es|ing)\b", "underscore"),
    ("vocab_strong", 2, r"\bshowcas(?:e|es|ing)\b", "showcase"),
    ("vocab_strong", 2, r"\bboast(?:s|ing|)\b", "boasts"),
    ("vocab_strong", 2, r"\bgame[- ]chang(?:er|ing)\b", "game-changer"),
    # Soft vocab (overlaps legitimate technical writing)
    ("vocab_soft", 1, r"\bleverag(?:e|es|ing)\b", "leverage"),
    ("vocab_soft", 1, r"\brobust\b", "robust"),
    ("vocab_soft", 1, r"\bseamless(?:ly|)\b", "seamless"),
    ("vocab_soft", 1, r"\bcrucial\b", "crucial"),
    ("vocab_soft", 1, r"\bpivotal\b", "pivotal"),
    ("vocab_soft", 1, r"\bcomprehensive\b", "comprehensive"),
    ("vocab_soft", 1, r"\bintricate\b", "intricate"),
    ("vocab_soft", 1, r"\bmeticulous(?:ly|)\b", "meticulous"),
    # Negative parallelism
    (
        "parallelism",
        3,
        r"\bit(?:'s| is) not just .{1,40}?,? (?:it(?:'s| is)|but)\b",
        "it's not just X, it's Y",
    ),
    (
        "parallelism",
        3,
        r"\bisn(?:'t| not) (?:merely|just) .{1,40}? but\b",
        "isn't merely X but Y",
    ),
    ("parallelism", 2, r"\bnot only .{1,40}? but (?:also|)\b", "not only X but also Y"),
    # Canned enthusiasm / assistant voice
    (
        "enthusiasm",
        3,
        r"(?:^|\.\s+)(?:Certainly|Absolutely|Of course)[!,]",
        "Certainly!/Absolutely!",
    ),
    ("enthusiasm", 3, r"\bgreat question\b", "great question"),
    ("enthusiasm", 3, r"\bI(?:'d| would) be (?:happy|glad) to\b", "I'd be happy to"),
    ("enthusiasm", 2, r"\bI hope this helps\b", "I hope this helps"),
    ("enthusiasm", 2, r"\bdive (?:in|into)\b", "dive in"),
    # Canned conclusions
    ("conclusion", 2, r"\bin conclusion\b", "in conclusion"),
    ("conclusion", 2, r"\bin summary\b", "in summary"),
    ("conclusion", 2, r"\bto sum (?:up|it up)\b", "to sum up"),
    ("conclusion", 1, r"(?:^|\n)\s*Overall,", "Overall,"),
    # Transition overuse
    ("transition", 1, r"(?:^|\n)\s*Moreover,", "Moreover,"),
    ("transition", 1, r"(?:^|\n)\s*Furthermore,", "Furthermore,"),
    ("transition", 1, r"(?:^|\n)\s*Additionally,", "Additionally,"),
    # Throat-clearing openers
    ("opener", 3, r"\bhere(?:'s| is) the thing\b", "here's the thing"),
    ("opener", 3, r"\bthe uncomfortable truth is\b", "the uncomfortable truth is"),
    (
        "opener",
        2,
        r"(?:^|\n)\s*Let(?:'s| us) (?:take|explore|dive|look)",
        "Let's explore/dive",
    ),
    ("opener", 2, r"\bwhen it comes to\b", "when it comes to"),
    # Vague declaratives / buzzword filler
    ("vague", 2, r"\bparadigm shift\b", "paradigm shift"),
    ("vague", 2, r"\bsynerg(?:y|ize|istic)\b", "synergy"),
    ("vague", 1, r"\blandscape\b", "landscape"),
    ("vague", 1, r"\bholistic(?:ally|)\b", "holistic"),
]

_COMPILED: list[tuple[str, int, re.Pattern[str], str]] = [
    (cat, w, re.compile(rx, re.IGNORECASE | re.MULTILINE), label)
    for cat, w, rx, label in _RAW
]

_EM_DASH = re.compile(r"\s—\s|\w—\w|—")
_WORD = re.compile(r"\b\w+\b")

# Staccato cadence: ≥3 consecutive sentences of ≤8 words triggers one hit.
_SENT_END = re.compile(r"[.!?]+")
_STACCATO_MAX_WORDS = 8
_STACCATO_MIN_RUN = 3

# ---------------------------------------------------------------------------
# Mode configs  (calibrated on 1 098 Bob technical blog posts, 2026-07-14)
# ---------------------------------------------------------------------------
MODES: dict[str, dict[str, float]] = {
    "relaxed": {"em_tol": 8.0, "warn": 15.0, "fail": 30.0},
    "balanced": {"em_tol": 5.0, "warn": 12.0, "fail": 25.0},
    "strict": {"em_tol": 3.0, "warn": 8.0, "fail": 20.0},
}
DEFAULT_MODE = "balanced"


def _count_staccato_runs(text: str) -> int:
    sentences = _SENT_END.split(text)
    runs = streak = 0
    for s in sentences:
        n_words = len(_WORD.findall(s))
        if 0 < n_words <= _STACCATO_MAX_WORDS:
            streak += 1
            if streak == _STACCATO_MIN_RUN:
                runs += 1
        else:
            streak = 0
    return runs


def detect_smells(text: str, *, em_dash_tolerance: float = 1.0) -> dict[str, Any]:
    """Scan *text* for LLM smells and return a structured report.

    Args:
        text: The text to scan.
        em_dash_tolerance: Tolerated em-dashes per 1 000 words before the
            excess contributes to the score (default 1.0; raise to 5.0 for
            balanced mode on technical writing).

    Returns:
        Dict with keys: ``word_count``, ``total_hits``, ``weighted_score``
        (hits per 1 000 words), ``em_dash_count``, ``em_dash_per_1k``,
        ``by_category``, ``hits`` (sorted by impact, descending).
    """
    word_count = len(_WORD.findall(text))
    hits: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    total_hits = 0
    weighted_total = 0.0

    for cat, weight, rx, label in _COMPILED:
        n = len(rx.findall(text))
        if n:
            hits.append({"category": cat, "label": label, "count": n, "weight": weight})
            by_category[cat] = by_category.get(cat, 0) + n
            total_hits += n
            weighted_total += n * weight

    em_dash_count = len(_EM_DASH.findall(text))
    per_1k = (1000.0 / word_count) if word_count else 0.0
    tolerated = word_count * em_dash_tolerance / 1000.0
    em_excess = max(0, em_dash_count - round(tolerated))
    if em_excess:
        weighted_total += em_excess
        hits.append(
            {
                "category": "em_dash",
                "label": "em-dash abuse",
                "count": em_excess,
                "weight": 1,
            }
        )
        by_category["em_dash"] = em_excess
        total_hits += em_excess

    staccato = _count_staccato_runs(text)
    if staccato:
        weighted_total += staccato * 2
        hits.append(
            {
                "category": "staccato",
                "label": "staccato cadence",
                "count": staccato,
                "weight": 2,
            }
        )
        by_category["staccato"] = staccato
        total_hits += staccato

    hits.sort(key=lambda h: h["count"] * h["weight"], reverse=True)

    return {
        "word_count": word_count,
        "total_hits": total_hits,
        "weighted_score": round(weighted_total * per_1k, 2),
        "em_dash_count": em_dash_count,
        "em_dash_per_1k": round(em_dash_count * per_1k, 2),
        "by_category": by_category,
        "hits": hits,
    }


def evaluate_gate(
    text: str,
    *,
    mode: str | None = None,
    warn_threshold: float | None = None,
    fail_threshold: float | None = None,
    em_dash_tolerance: float | None = None,
) -> dict[str, Any]:
    """Return a pass / warn / fail gate report for *text*.

    Args:
        text: Text to evaluate.
        mode: Sensitivity preset — ``"relaxed"``, ``"balanced"`` (default),
            or ``"strict"``.  Explicit threshold args override the preset.
        warn_threshold: Override the warn level from the preset.
        fail_threshold: Override the fail level from the preset.
        em_dash_tolerance: Override em-dashes per 1 000 words tolerated.

    Returns:
        Dict with ``status`` (``"pass"`` / ``"warn"`` / ``"fail"``),
        ``reason``, ``mode``, ``thresholds``, and ``smell_report``.
    """
    cfg = MODES.get(mode or DEFAULT_MODE, MODES[DEFAULT_MODE])
    _warn = warn_threshold if warn_threshold is not None else cfg["warn"]
    _fail = fail_threshold if fail_threshold is not None else cfg["fail"]
    _em_tol = em_dash_tolerance if em_dash_tolerance is not None else cfg["em_tol"]

    if _warn < 0:
        raise ValueError("warn_threshold must be non-negative")
    if _fail <= _warn:
        raise ValueError("fail_threshold must be greater than warn_threshold")

    smell_report = detect_smells(text, em_dash_tolerance=_em_tol)
    score = float(smell_report["weighted_score"])

    if score >= _fail:
        status = "fail"
        reason = f"weighted_score {score:g} >= fail_threshold {_fail:g}"
    elif score >= _warn:
        status = "warn"
        reason = f"weighted_score {score:g} >= warn_threshold {_warn:g}"
    else:
        status = "pass"
        reason = f"weighted_score {score:g} < warn_threshold {_warn:g}"

    return {
        "status": status,
        "reason": reason,
        "mode": mode or DEFAULT_MODE,
        "thresholds": {"warn": _warn, "fail": _fail, "em_dash_tolerance": _em_tol},
        "smell_report": smell_report,
    }


# ---------------------------------------------------------------------------
# Convenience: run as a script  ``python -m gptme.anti_slop FILE``
# ---------------------------------------------------------------------------
def _format_report(report: dict[str, Any], *, top: int = 5) -> str:
    smell = report["smell_report"]
    status = report["status"].upper()
    lines = [
        f"Anti-Slop Gate: {status}  [mode={report['mode']}]",
        f"reason: {report['reason']}",
        f"words: {smell['word_count']}  hits: {smell['total_hits']}  "
        f"weighted_score: {smell['weighted_score']} /1k words",
    ]
    if smell["hits"]:
        lines.append("\nTop smells:")
        lines.extend(
            f"  [{h['category']:<13}] {h['label']:<28} x{h['count']}  (w{h['weight']})"
            for h in smell["hits"][:top]
        )
    return "\n".join(lines)


if __name__ == "__main__":
    text = sys.stdin.read() if len(sys.argv) < 2 else open(sys.argv[1]).read()
    r = evaluate_gate(text)
    print(_format_report(r))
    sys.exit(1 if r["status"] == "fail" else 0)
