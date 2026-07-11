#!/usr/bin/env python3
"""Check model ID freshness in gptme source against live provider APIs.

Scans hardcoded model IDs in gptme source files and tests them against the
live OpenRouter model list. Flags deprecated/stale models and suggests
current alternatives from the same model family.

Usage:
    python3 scripts/check_model_freshness.py [--json] [--gptme-src PATH]

Exit codes:
    0  All checked models are live
    1  One or more stale model IDs detected
    2  OpenRouter API unreachable; no models could be verified

Requires OPENROUTER_API_KEY to be set in the environment (or reachable via the
OPENAI_API_KEY fallback accepted by the OpenRouter /v1/models endpoint).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
GPTME_SRC = REPO_ROOT / "gptme"

# Files containing hardcoded model IDs (OpenRouter-context only), relative to GPTME_SRC.
MODEL_FILES = [
    "llm/models/resolution.py",
    "llm/models/data.py",
    "llm/__init__.py",
    "tools/morph.py",
]

# Matches quoted strings that look like OpenRouter-style "provider/model-name" IDs.
# We only want true OpenRouter model IDs, not gptme's internal "provider/model"
# format used for direct Anthropic/OpenAI API access (where the provider is the key).
#
# True OpenRouter IDs: meta-llama/..., google/..., deepseek/..., moonshotai/...,
#   mistralai/..., qwen/..., z-ai/..., AND anthropic/claude-X.Y (dot version).
# False positives (gptme's direct-API format): anthropic/claude-X-Y (hyphen version),
#   openai/gpt-*, openai/gpt-4o-mini (those go through OpenAI SDK, not OpenRouter).
#
# Heuristic to distinguish them:
#   - anthropic/claude-X.Y (dot) → OpenRouter format ✅
#   - anthropic/claude-X-Y (hyphen, no dot in version) → gptme direct-API ❌
#   - openai/* → skip (OpenAI SDK, not OpenRouter)
_MODEL_RE = re.compile(
    r'"('
    # gptme's "openrouter/" prefix format: openrouter/provider/model[-suffixes]
    # The prefix is stripped when recording the model ID (see _iter_model_ids).
    r"openrouter/[^/\"]+/[^\"]{4,}"
    r"|"
    # Providers where "provider/model" always means OpenRouter (not direct provider SDK)
    r"(?:meta-llama|mistral(?:ai)?|google|moonshotai|deepseek|qwen|z-ai|morph)/"
    r"[^\"]{4,}"  # at least 4 chars after the slash
    r"|"
    # anthropic/ ONLY when the version uses dots (e.g. 4.5, 4.6) → true OpenRouter format
    r"anthropic/[^\"]+\.\d"
    r')"'
)


@dataclass
class ModelOccurrence:
    model_id: str
    file: str
    line: int
    snippet: str


@dataclass
class CheckResult:
    model_id: str
    occurrences: list[ModelOccurrence] = field(default_factory=list)
    # None = provider not reachable / check skipped
    is_live: bool | None = None
    suggestion: str | None = None


def _iter_model_ids(gptme_src: Path) -> Iterator[ModelOccurrence]:
    """Yield model occurrences from all MODEL_FILES."""
    seen: set[tuple[str, str, int]] = set()
    for rel in MODEL_FILES:
        path = gptme_src / rel
        if not path.exists():
            continue
        for lineno, raw in enumerate(path.read_text().splitlines(), 1):
            for m in _MODEL_RE.finditer(raw):
                model_id = m.group(1)
                # Strip gptme's "openrouter/" prefix to get the bare OpenRouter ID
                # e.g. "openrouter/meta-llama/llama-3.3-70b" → "meta-llama/llama-3.3-70b"
                model_id = model_id.removeprefix("openrouter/")
                key = (model_id, rel, lineno)
                if key in seen:
                    continue
                seen.add(key)
                yield ModelOccurrence(
                    model_id=model_id,
                    file=rel,
                    line=lineno,
                    snippet=raw.strip()[:120],
                )


def _fetch_openrouter_models(api_key: str | None) -> set[str] | None:
    """Return the set of live OpenRouter model IDs, or None on failure."""
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "gptme-check-model-freshness/1.0")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return {m["id"] for m in data.get("data", [])}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
        print(f"Warning: could not fetch OpenRouter models: {exc}", file=sys.stderr)
        return None


_AVOID_IN_SUGGESTIONS = (
    "guard",  # safety/moderation models, not general assistants
    "embed",  # embedding models
    "vision",  # vision-only variants when the stale model is text-only
    "whisper",  # audio/speech
    "tts",  # text-to-speech
)
_PREFER_IN_SUGGESTIONS = (
    "instruct",
    "chat",
)


def _best_suggestion(model_id: str, live_ids: set[str]) -> str | None:
    """Find a live model from the same provider/family as a stale model_id."""
    base_id = model_id.split("@")[0]
    base_id = base_id.split(":")[0]

    if "/" not in base_id:
        return None
    prefix, rest = base_id.split("/", 1)

    family = rest.split("-")[0].lower()

    def _param_size(m: str) -> int:
        import re as _re

        for tok in _re.findall(r"(\d+(?:\.\d+)?)b", m.lower()):
            try:
                return int(float(tok))
            except ValueError:
                pass
        return 0

    def _score(m: str) -> tuple[int, int, str]:
        s = 0
        if any(tag in m.lower() for tag in _AVOID_IN_SUGGESTIONS):
            return (-1000, 0, m)
        if ":free" in m or ":extended" in m:
            s -= 5
        if any(tag in m.lower() for tag in _PREFER_IN_SUGGESTIONS):
            s += 3
        return (s, _param_size(m), m)

    def _valid(m: str) -> bool:
        return _score(m)[0] >= 0

    family_matches = sorted(
        (
            m
            for m in live_ids
            if m.startswith(prefix + "/") and family in m.lower() and _valid(m)
        ),
        key=_score,
        reverse=True,
    )
    if family_matches:
        return family_matches[0]

    provider_matches = sorted(
        (m for m in live_ids if m.startswith(prefix + "/") and _valid(m)),
        key=_score,
        reverse=True,
    )
    if provider_matches:
        return provider_matches[0]

    return None


def run_checks(gptme_src: Path) -> list[CheckResult]:
    """Collect model IDs from source, check against OpenRouter, return results."""
    by_id: dict[str, list[ModelOccurrence]] = {}
    for occ in _iter_model_ids(gptme_src):
        by_id.setdefault(occ.model_id, []).append(occ)

    if not by_id:
        print("No model IDs found — check --gptme-src path.", file=sys.stderr)
        return []

    api_key = os.environ.get("OPENROUTER_API_KEY")
    live_ids = _fetch_openrouter_models(api_key)

    results: list[CheckResult] = []
    for model_id, occs in sorted(by_id.items()):
        result = CheckResult(model_id=model_id, occurrences=occs)
        if live_ids is not None:
            base_id = model_id.split("@")[0]
            result.is_live = model_id in live_ids or (
                "@" in model_id and base_id in live_ids
            )
            if not result.is_live:
                result.suggestion = _best_suggestion(model_id, live_ids)
        results.append(result)

    return results


def print_results(results: list[CheckResult]) -> int:
    """Print human-readable output. Returns exit code (1 if any stale)."""
    stale: list[CheckResult] = []
    unknown: list[CheckResult] = []
    live: list[CheckResult] = []

    for r in results:
        if r.is_live is None:
            unknown.append(r)
        elif r.is_live:
            live.append(r)
        else:
            stale.append(r)

    if stale:
        print(f"❌ STALE ({len(stale)} model(s)):")
        for r in stale:
            locs = ", ".join(f"{o.file}:{o.line}" for o in r.occurrences)
            print(f"  {r.model_id}")
            print(f"    locations : {locs}")
            if r.suggestion:
                print(f"    suggestion: {r.suggestion}")
        print()

    if live:
        print(f"✅ Live ({len(live)} model(s)):")
        for r in live:
            print(f"  {r.model_id}")
        print()

    if unknown:
        print(f"⚪ Unknown/unchecked ({len(unknown)} model(s)):")
        for r in unknown:
            print(f"  {r.model_id}")
        print()

    if not stale and not live and unknown:
        # Fetch failed — every model is unchecked; do not silently exit 0
        print(
            f"Error: OpenRouter API unreachable — {len(unknown)} model(s) could not be verified.",
            file=sys.stderr,
        )
        return 2

    if stale:
        print(f"Summary: {len(stale)} stale, {len(live)} live, {len(unknown)} unknown")
        return 1
    print(
        f"Summary: all {len(live)} checked model(s) live"
        + (f" ({len(unknown)} unchecked)" if unknown else "")
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of text"
    )
    parser.add_argument(
        "--gptme-src",
        type=Path,
        default=GPTME_SRC,
        metavar="PATH",
        help="Path to gptme package directory (default: %(default)s)",
    )
    args = parser.parse_args()

    if not args.gptme_src.exists():
        print(
            f"Error: gptme source not found at {args.gptme_src}\n"
            f"  Use --gptme-src to specify the path.",
            file=sys.stderr,
        )
        sys.exit(2)

    results = run_checks(args.gptme_src)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "model_id": r.model_id,
                        "is_live": r.is_live,
                        "suggestion": r.suggestion,
                        "occurrences": [
                            {"file": o.file, "line": o.line, "snippet": o.snippet}
                            for o in r.occurrences
                        ],
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        stale_count = sum(1 for r in results if r.is_live is False)
        unknown_count = sum(1 for r in results if r.is_live is None)
        if stale_count:
            sys.exit(1)
        elif unknown_count and not stale_count and not any(r.is_live for r in results):
            sys.exit(2)
        sys.exit(0)

    sys.exit(print_results(results))


if __name__ == "__main__":
    main()
