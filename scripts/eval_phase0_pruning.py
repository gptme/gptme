#!/usr/bin/env python3
"""
Evaluate Phase 0 relevance-scored pruning on recorded conversation logs.

Answers the three questions from issue #3997:

  1. Tokens freed: How many tokens Phase 0 removes before the compaction
     trigger fires on long sessions.
  2. False-drop rate: Fraction of dropped tool outputs whose payload appears
     again in a later message (would have been silently lost).
  3. Trigger delay: Whether the freed tokens would delay or prevent the Phase 1+
     LLM-compaction trigger.

Usage:
    uv run python scripts/eval_phase0_pruning.py [--limit 100] [--min-messages 30] [--verbose]

The script reads your own conversation logs from ~/.local/share/gptme/logs/.
No network calls are made; the shadow pass is a pure heuristic scorer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, cast

from gptme.llm.models import (
    MODEL_ALIASES,
    MODELS,
    PROVIDER_ALIASES,
    ModelMeta,
    Provider,
    _find_base_model_properties,
    _find_closest_model_properties,
    get_default_model,
    get_model,
)
from gptme.logmanager import Log, get_user_conversations
from gptme.message import len_tokens
from gptme.tools.autocompact import (
    PruneDecision,
    shadow_prune_stale_tool_outputs,
)
from gptme.tools.autocompact.config import _get_keep_head

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ConvStats:
    name: str
    total_tokens: int
    tool_output_tokens: int
    tokens_freed: int
    n_candidates: int
    n_dropped: int
    n_false_drop_candidates: int
    """Dropped messages whose digest matches a later message payload."""
    compaction_would_trigger_before: bool
    compaction_would_trigger_after: bool

    @property
    def false_drop_rate(self) -> float:
        return self.n_false_drop_candidates / self.n_dropped if self.n_dropped else 0.0

    @property
    def freed_pct(self) -> float:
        return self.tokens_freed / self.total_tokens if self.total_tokens else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_tokens": self.total_tokens,
            "tool_output_tokens": self.tool_output_tokens,
            "tokens_freed": self.tokens_freed,
            "freed_pct": round(self.freed_pct, 4),
            "n_candidates": self.n_candidates,
            "n_dropped": self.n_dropped,
            "n_false_drop_candidates": self.n_false_drop_candidates,
            "false_drop_rate": round(self.false_drop_rate, 4),
            "compaction_would_trigger_before": self.compaction_would_trigger_before,
            "compaction_would_trigger_after": self.compaction_would_trigger_after,
        }


@dataclass
class AggregateStats:
    total_conversations: int = 0
    analyzed_conversations: int = 0
    skipped_no_tool_outputs: int = 0
    total_tokens: int = 0
    total_tool_output_tokens: int = 0
    total_tokens_freed: int = 0
    total_candidates: int = 0
    total_dropped: int = 0
    total_false_drop_candidates: int = 0
    trigger_delayed_count: int = 0
    trigger_prevented_count: int = 0
    conv_stats: list[ConvStats] = field(default_factory=list)

    @property
    def overall_false_drop_rate(self) -> float:
        return (
            self.total_false_drop_candidates / self.total_dropped
            if self.total_dropped
            else 0.0
        )

    @property
    def avg_freed_pct(self) -> float:
        if not self.analyzed_conversations:
            return 0.0
        return sum(c.freed_pct for c in self.conv_stats) / self.analyzed_conversations


# ---------------------------------------------------------------------------
# False-drop detection
# ---------------------------------------------------------------------------


def _count_false_drops(decisions: list[PruneDecision], log_messages: list) -> int:
    """Count dropped decisions whose payload reappears in a later message.

    For each dropped decision at index ``d.idx``, scan every message at
    index > ``d.idx``.  A match on ``content_digest`` — a short digest of the
    *normalized* content, with line numbers and tool formatting stripped —
    means the same payload was re-read/re-used after the drop point: a silent
    context loss.  This is a normalized match rather than a byte-for-byte
    comparison, and the digest is short, so a small number of collisions is
    possible over long logs.  Each dropped item is counted at most once.
    """
    dropped = [d for d in decisions if d.decision == "drop"]
    if not dropped:
        return 0

    false_drops = 0
    for d in dropped:
        drop_digest = d.content_digest
        for later_msg in log_messages[d.idx + 1 :]:
            if PruneDecision._digest(later_msg.content) == drop_digest:
                false_drops += 1
                break  # count this dropped item once regardless of how many later matches
    return false_drops


def _resolve_recorded_model(name: str | None, fallback: ModelMeta) -> ModelMeta:
    """Resolve a conversation's recorded model name without network access.

    ``get_model`` performs a *dynamic* provider fetch when the requested model
    is absent from the static registry (see ``resolution._resolve_model``); for
    the ``openrouter``/``gptme`` providers that is an HTTP request, which would
    break this script's offline contract and can hang on a slow provider.

    Reproduce the *offline* portion of resolution instead — static exact/alias
    lookup, then the same date-suffix and closest-match fallbacks ``get_model``
    would use — and finally the caller's ``fallback``.
    """
    if not name or "/" not in name:
        return fallback
    provider_str, _, model_name = name.partition("/")
    provider = cast(Provider, PROVIDER_ALIASES.get(provider_str, provider_str))
    entries = MODELS.get(provider)
    if not entries:
        return fallback

    # Mirror resolution's normalization for subprovider (@) and reasoning (:)
    # suffixes, then try the provider alias map.
    lookup = model_name.split("@")[0].rsplit(":", 1)[0]
    if lookup in entries:
        return ModelMeta(provider, model_name, **entries[lookup])
    canonical = MODEL_ALIASES.get(provider, {}).get(lookup, lookup)
    if canonical in entries:
        return ModelMeta(provider, model_name, **entries[canonical])

    props = _find_base_model_properties(provider, lookup) or (
        _find_closest_model_properties(provider, lookup)
    )
    if props:
        return ModelMeta(provider, model_name, **props)
    return fallback


def _trigger_at_first_turn_boundary(
    messages: list,
    model_name: str,
    limit: int,
    keep_head: int,
) -> tuple[bool, bool]:
    """Evaluate the compaction trigger at the first turn boundary over ``limit``.

    Production runs the autocompact hook on ``TURN_POST`` — once per turn,
    after all steps complete (see ``gptme/tools/autocompact/hook.py``) — so the
    relevant observation point is the end of a turn, i.e. the prefix just
    before the next user message. Evaluating at an arbitrary mid-turn message
    prefix (say, right after a large tool result) would not match production:
    the rest of the turn changes both the amount over budget and which outputs
    are old enough to prune.

    Find the first turn-boundary prefix whose token count reaches ``limit``,
    run the shadow pass there, and report whether that prefix is still over
    budget after pruning.

    Returns ``(trigger_before, trigger_after)``. Both are ``False`` when no
    turn boundary reaches ``limit``.
    """
    # A turn ends just before the next user message; the final boundary is the
    # end of the log.
    boundaries = sorted(
        {i for i, m in enumerate(messages) if m.role == "user"} | {len(messages)}
    )

    for end in boundaries:
        prefix = messages[:end]
        prefix_tokens = len_tokens(prefix, model_name)
        if prefix_tokens < limit:
            continue
        decisions = shadow_prune_stale_tool_outputs(
            prefix, model_name, keep_head=keep_head
        )
        freed = sum(d.tokens_saved for d in decisions)
        return True, (prefix_tokens - freed) >= limit
    return False, False


# ---------------------------------------------------------------------------
# Per-conversation analysis
# ---------------------------------------------------------------------------


def analyze_conversation(
    conv, verbose: bool = False, budget: int | None = None
) -> ConvStats | None:
    """Run the shadow pass on one conversation and return stats."""
    try:
        log = Log.read_jsonl(conv.path)
    except Exception as exc:
        if verbose:
            print(f"  ERROR reading {conv.name}: {exc}", file=sys.stderr)
        return None

    messages = log.messages
    if not messages:
        return None

    try:
        default_model = get_default_model() or get_model("gpt-4")
        # A saved conversation may have used a different model than the current
        # default. Prefer the recorded model so the tokenizer and context window
        # match what produced the log — resolved offline to keep this script's
        # no-network contract.
        model_obj = _resolve_recorded_model(conv.model, default_model)
        model_name = model_obj.model

        # Production protects the configured head from all compaction phases.
        keep_head = _get_keep_head()

        total_tokens = len_tokens(messages, model_name)

        # Run the shadow pass (never mutates the log)
        decisions = shadow_prune_stale_tool_outputs(
            messages, model_name, keep_head=keep_head
        )
    except Exception as exc:
        # A single misconfigured conversation must not abort the whole run.
        if verbose:
            print(f"  ERROR analyzing {conv.name}: {exc}", file=sys.stderr)
        return None

    if not decisions:
        return None  # no tool outputs at all

    tool_output_tokens = sum(d.tokens for d in decisions)
    tokens_freed = sum(d.tokens_saved for d in decisions)
    n_candidates = len(decisions)
    n_dropped = sum(1 for d in decisions if d.decision == "drop")

    # False-drop detection: dropped items whose payload reappears later
    n_false_drop_candidates = _count_false_drops(decisions, messages)

    # Compaction trigger: would the trigger fire before/after Phase 0?
    try:
        if budget is not None:
            limit = budget
        else:
            import logging

            from gptme.util.context_budget import get_context_budget

            _logger = logging.getLogger("gptme.util.context_budget")
            prev_level = _logger.level
            _logger.setLevel(logging.CRITICAL)
            limit = get_context_budget(
                model_obj.context,
                max_output=model_obj.max_output or 8192,
                model_id=model_obj.full,
                model_context_budget=model_obj.context_budget,
            )
            _logger.setLevel(prev_level)
            if limit <= 2000:
                # The derived budget collapsed to the floor because model
                # metadata was missing or the window is smaller than the
                # output reservation. Fall back to the model's declared
                # window — not a hardcoded constant — so the trigger threshold
                # tracks the actual model instead of silently widening.
                fallback = (
                    (model_obj.context or 0) - (model_obj.max_output or 8192) - 1000
                )
                if fallback > 2000:
                    limit = fallback
        # Production runs the autocompact hook on TURN_POST, so evaluate the
        # trigger at the first turn boundary over budget rather than on the
        # completed log.
        trigger_before, trigger_after = _trigger_at_first_turn_boundary(
            messages, model_name, limit, keep_head
        )
    except Exception:
        trigger_before = False
        trigger_after = False

    return ConvStats(
        name=conv.name,
        total_tokens=total_tokens,
        tool_output_tokens=tool_output_tokens,
        tokens_freed=tokens_freed,
        n_candidates=n_candidates,
        n_dropped=n_dropped,
        n_false_drop_candidates=n_false_drop_candidates,
        compaction_would_trigger_before=trigger_before,
        compaction_would_trigger_after=trigger_after,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of conversations to evaluate (default: 200)",
    )
    parser.add_argument(
        "--min-messages",
        type=int,
        default=10,
        help="Skip conversations shorter than this many messages (default: 10)",
    )
    parser.add_argument(
        "--min-tokens",
        type=int,
        default=2000,
        help="Skip conversations shorter than this many tokens (default: 2000)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-conversation details",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help=(
            "Context budget in tokens used to evaluate trigger impact "
            "(default: derived from model config, falls back to 40000)"
        ),
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output full results as JSON to stdout",
    )
    args = parser.parse_args()

    agg = AggregateStats()

    import logging

    logging.basicConfig(level=logging.WARNING)
    # Suppress the "clamping context budget" noise from models without API creds
    logging.getLogger("gptme.util.context_budget").setLevel(logging.CRITICAL)

    print(
        f"Loading up to {args.limit} conversations "
        f"(≥{args.min_messages} messages, ≥{args.min_tokens} tokens)…",
        file=sys.stderr,
    )

    # Iterate newest-first until ``--limit`` conversations have been analyzed.
    # Do NOT pre-slice: conversations can be skipped by the message/token
    # filters, and slicing before filtering would silently narrow the sample
    # even when older eligible logs exist.
    conversations = get_user_conversations(detail=False)
    agg.total_conversations = 0

    for conv in conversations:
        if agg.analyzed_conversations >= args.limit:
            break
        agg.total_conversations += 1

        # ConversationMeta always carries an int message count (populated even
        # by the fast tail scan when detail=False), so the filter is
        # unconditional.
        if conv.messages < args.min_messages:
            continue

        stats = analyze_conversation(conv, verbose=args.verbose, budget=args.budget)
        if stats is None:
            agg.skipped_no_tool_outputs += 1
            continue

        if stats.total_tokens < args.min_tokens:
            continue

        agg.analyzed_conversations += 1
        agg.total_tokens += stats.total_tokens
        agg.total_tool_output_tokens += stats.tool_output_tokens
        agg.total_tokens_freed += stats.tokens_freed
        agg.total_candidates += stats.n_candidates
        agg.total_dropped += stats.n_dropped
        agg.total_false_drop_candidates += stats.n_false_drop_candidates
        if (
            stats.compaction_would_trigger_before
            and not stats.compaction_would_trigger_after
        ):
            agg.trigger_delayed_count += 1
        if (
            stats.compaction_would_trigger_before
            and not stats.compaction_would_trigger_after
        ):
            agg.trigger_prevented_count += 1
        agg.conv_stats.append(stats)

        if args.verbose and stats.n_dropped > 0:
            # stderr keeps stdout a single clean JSON document under --json -v.
            print(
                f"  [{agg.analyzed_conversations}] {conv.name}: "
                f"{stats.tokens_freed:,} tokens freed "
                f"({stats.freed_pct:.1%}), "
                f"{stats.n_dropped}/{stats.n_candidates} dropped, "
                f"{stats.n_false_drop_candidates} false-drop candidates",
                file=sys.stderr,
            )

    # --- Print summary ---
    if args.output_json:
        print(
            json.dumps(
                {
                    "summary": {
                        "total_conversations": agg.total_conversations,
                        "analyzed": agg.analyzed_conversations,
                        "skipped_no_tool_outputs": agg.skipped_no_tool_outputs,
                        "total_tokens": agg.total_tokens,
                        "total_tool_output_tokens": agg.total_tool_output_tokens,
                        "total_tokens_freed": agg.total_tokens_freed,
                        "avg_freed_pct": round(agg.avg_freed_pct, 4),
                        "overall_false_drop_rate": round(
                            agg.overall_false_drop_rate, 4
                        ),
                        "total_candidates": agg.total_candidates,
                        "total_dropped": agg.total_dropped,
                        "total_false_drop_candidates": agg.total_false_drop_candidates,
                        "trigger_delayed_count": agg.trigger_delayed_count,
                    },
                    "conversations": [c.as_dict() for c in agg.conv_stats],
                },
                indent=2,
            )
        )
        return

    # Human-readable report
    print()
    print("=" * 70)
    print("Phase 0 pruning evaluation — issue #3997")
    print("=" * 70)
    print(f"Conversations visited:          {agg.total_conversations:>8,}")
    print(f"Conversations analyzed:         {agg.analyzed_conversations:>8,}")
    print()
    print("── Metric 1: Tokens freed ──────────────────────────────────────────")
    print(f"Total tokens in analyzed logs:  {agg.total_tokens:>8,}")
    print(f"Total tool-output tokens:       {agg.total_tool_output_tokens:>8,}")
    print(f"Tokens Phase 0 would free:      {agg.total_tokens_freed:>8,}")
    if agg.total_tokens:
        freed_pct = agg.total_tokens_freed / agg.total_tokens
        print(f"  as % of total tokens:         {freed_pct:>7.1%}")
    if agg.total_tool_output_tokens:
        freed_of_tool = agg.total_tokens_freed / agg.total_tool_output_tokens
        print(f"  as % of tool-output tokens:   {freed_of_tool:>7.1%}")
    print(f"Avg freed % per conversation:   {agg.avg_freed_pct:>7.1%}")
    print()
    print("── Metric 2: False-drop rate ───────────────────────────────────────")
    print(f"Tool-output candidates scored:  {agg.total_candidates:>8,}")
    print(f"Decisions to drop:              {agg.total_dropped:>8,}")
    print(f"False-drop candidates:          {agg.total_false_drop_candidates:>8,}")
    print(f"  overall false-drop rate:      {agg.overall_false_drop_rate:>7.1%}")
    note = "  (false-drop = dropped payload reappears verbatim in a later message)"
    print(note)
    print()
    print("── Metric 3: Compaction trigger impact ─────────────────────────────")
    triggered_before = sum(
        1 for c in agg.conv_stats if c.compaction_would_trigger_before
    )
    triggered_after = sum(1 for c in agg.conv_stats if c.compaction_would_trigger_after)
    print(f"Convs where trigger fires (before Phase 0): {triggered_before:>6,}")
    print(f"Convs where trigger fires (after Phase 0):  {triggered_after:>6,}")
    if triggered_before:
        delayed = triggered_before - triggered_after
        print(
            f"Trigger delayed/prevented by Phase 0:       {delayed:>6,} "
            f"({delayed / triggered_before:.0%} of triggered)"
        )
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
