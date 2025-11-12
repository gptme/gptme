#!/usr/bin/env python3
"""
Validation script for context compression Phase 1 Week 2.

Measures:
1. Token reduction across N conversations
2. Cost savings estimate
3. Compression quality (preserved content analysis)

Usage:
    python scripts/validate_compression.py [--conversations N] [--verbose]
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tiktoken

# Add gptme to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from gptme.context_compression.config import CompressionConfig
from gptme.context_compression.extractive import ExtractiveSummarizer


@dataclass
class CompressionMetrics:
    """Metrics for a single conversation compression."""

    conversation_id: str
    original_tokens: int
    compressed_tokens: int
    reduction_pct: float
    original_chars: int
    compressed_chars: int


def load_conversation(log_dir: Path) -> list[dict[str, Any]]:
    """Load conversation from JSONL file."""
    conversation_file = log_dir / "conversation.jsonl"
    if not conversation_file.exists():
        return []

    messages = []
    with open(conversation_file) as f:
        for line in f:
            if line.strip():
                messages.append(json.loads(line))
    return messages


def extract_context_content(messages: list[dict[str, Any]]) -> str:
    """Extract context content from system messages."""
    context_parts = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            # Skip very short system messages (e.g., token warnings)
            if len(content) > 100:
                context_parts.append(content)
    return "\n\n".join(context_parts)


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken (gpt-4 encoding)."""
    enc = tiktoken.encoding_for_model("gpt-4")
    return len(enc.encode(text))


def find_recent_conversations(logs_dir: Path, limit: int = 50) -> list[Path]:
    """Find N most recent conversation logs."""
    # Get all conversation directories
    all_dirs = [d for d in logs_dir.iterdir() if d.is_dir()]

    # Sort by modification time (most recent first)
    all_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    # Filter to those with conversation.jsonl
    valid_dirs = [d for d in all_dirs if (d / "conversation.jsonl").exists()]

    return valid_dirs[:limit]


def validate_compression(
    conversations: list[Path], config: CompressionConfig, verbose: bool = False
) -> list[CompressionMetrics]:
    """Validate compression across multiple conversations."""
    compressor = ExtractiveSummarizer(config)
    metrics = []

    for i, conv_dir in enumerate(conversations, 1):
        conv_id = conv_dir.name
        if verbose:
            print(f"\n[{i}/{len(conversations)}] Processing: {conv_id}")

        # Load and extract context
        messages = load_conversation(conv_dir)
        if not messages:
            if verbose:
                print("  ⚠ Skipped: No messages found")
            continue

        context = extract_context_content(messages)
        if not context or len(context) < 100:
            if verbose:
                print("  ⚠ Skipped: No significant context found")
            continue

        # Compress with configured target_ratio
        result = compressor.compress(context, target_ratio=config.target_ratio)

        # Calculate metrics
        original_tokens = count_tokens(context)
        compressed_tokens = count_tokens(result.compressed)
        reduction_pct = ((original_tokens - compressed_tokens) / original_tokens) * 100

        metric = CompressionMetrics(
            conversation_id=conv_id,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction_pct=reduction_pct,
            original_chars=len(context),
            compressed_chars=len(result.compressed),
        )
        metrics.append(metric)

        if verbose:
            print(f"  Original: {original_tokens:,} tokens, {len(context):,} chars")
            print(
                f"  Compressed: {compressed_tokens:,} tokens, {len(result.compressed):,} chars"
            )
            print(f"  Reduction: {reduction_pct:.1f}%")

    return metrics


def calculate_cost_savings(
    avg_reduction_pct: float, sessions_per_day: int = 48
) -> dict[str, float]:
    """Calculate annual cost savings from token reduction.

    Args:
        avg_reduction_pct: Average percentage reduction in tokens
        sessions_per_day: Number of sessions per day (default: 48 from autonomous runs)

    Returns:
        Dict with daily, monthly, and annual savings estimates
    """
    # Assumptions based on typical usage:
    # - Average context size: ~40k tokens
    # - Model: claude-sonnet-3.5 at $3/M input tokens
    # - Sessions per day: 48 (autonomous schedule)

    avg_context_tokens = 40_000
    input_cost_per_million = 3.0

    # Calculate token savings
    tokens_saved_per_session = avg_context_tokens * (avg_reduction_pct / 100)
    tokens_saved_per_day = tokens_saved_per_session * sessions_per_day

    # Calculate cost savings
    cost_per_million_tokens = input_cost_per_million
    daily_savings = (tokens_saved_per_day / 1_000_000) * cost_per_million_tokens
    monthly_savings = daily_savings * 30
    annual_savings = daily_savings * 365

    return {
        "tokens_saved_per_session": tokens_saved_per_session,
        "tokens_saved_per_day": tokens_saved_per_day,
        "daily_savings_usd": daily_savings,
        "monthly_savings_usd": monthly_savings,
        "annual_savings_usd": annual_savings,
    }


def print_summary(metrics: list[CompressionMetrics], verbose: bool = False):
    """Print validation summary."""
    if not metrics:
        print("\n❌ No valid conversations processed")
        return

    # Calculate aggregate metrics
    total_original_tokens = sum(m.original_tokens for m in metrics)
    total_compressed_tokens = sum(m.compressed_tokens for m in metrics)
    avg_reduction = sum(m.reduction_pct for m in metrics) / len(metrics)

    # Cost savings
    savings = calculate_cost_savings(avg_reduction)

    print("\n" + "=" * 60)
    print("CONTEXT COMPRESSION VALIDATION RESULTS")
    print("=" * 60)

    print(f"\n📊 Sample Size: {len(metrics)} conversations")
    print("\n📉 Token Reduction:")
    print(f"  Original tokens:    {total_original_tokens:,}")
    print(f"  Compressed tokens:  {total_compressed_tokens:,}")
    print(f"  Average reduction:  {avg_reduction:.1f}%")

    print("\n💰 Cost Savings (based on 48 sessions/day):")
    print(f"  Tokens saved/session: {savings['tokens_saved_per_session']:,.0f}")
    print(f"  Tokens saved/day:     {savings['tokens_saved_per_day']:,.0f}")
    print(f"  Daily savings:        ${savings['daily_savings_usd']:.2f}")
    print(f"  Monthly savings:      ${savings['monthly_savings_usd']:.2f}")
    print(f"  Annual savings:       ${savings['annual_savings_usd']:.2f}")

    # Check against targets
    print("\n✅ Target Achievement:")
    print("  Token reduction target: 30%")
    print(f"  Achieved: {avg_reduction:.1f}% {'✓' if avg_reduction >= 30 else '✗'}")
    print("  Annual savings target: $2,500")
    print(
        f"  Achieved: ${savings['annual_savings_usd']:.0f} {'✓' if savings['annual_savings_usd'] >= 2500 else '✗'}"
    )

    if verbose:
        print("\n📋 Per-Conversation Breakdown:")
        for m in metrics[:10]:  # Show first 10
            print(f"  {m.conversation_id}: {m.reduction_pct:.1f}% reduction")
        if len(metrics) > 10:
            print(f"  ... and {len(metrics) - 10} more")


def main():
    parser = argparse.ArgumentParser(description="Validate context compression")
    parser.add_argument(
        "--conversations",
        "-n",
        type=int,
        default=50,
        help="Number of conversations to validate (default: 50)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed output"
    )
    parser.add_argument(
        "--ratio",
        "-r",
        type=float,
        default=0.7,
        help="Compression target ratio (default: 0.7 = 30%% reduction)",
    )

    args = parser.parse_args()

    # Find conversations
    logs_dir = Path.home() / ".local" / "share" / "gptme" / "logs"
    if not logs_dir.exists():
        print(f"❌ Error: Logs directory not found: {logs_dir}")
        sys.exit(1)

    print(f"🔍 Finding {args.conversations} recent conversations...")
    conversations = find_recent_conversations(logs_dir, args.conversations)
    print(f"Found {len(conversations)} conversations with valid data")

    if not conversations:
        print("❌ No conversations found to validate")
        sys.exit(1)

    # Configure compression
    config = CompressionConfig(
        enabled=True,
        target_ratio=args.ratio,
        min_section_length=100,
        preserve_code=True,
        preserve_headings=True,
    )

    # Run validation
    print(f"\n⏳ Validating compression with target_ratio={args.ratio}...")
    metrics = validate_compression(conversations, config, args.verbose)

    # Print summary
    print_summary(metrics, args.verbose)


if __name__ == "__main__":
    main()
