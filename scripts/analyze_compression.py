#!/usr/bin/env python3
"""
Analyze compression ratios of conversation logs to understand context quality.

This script uses the compression utilities to measure how compressible
conversation content is, which can indicate:
- Highly compressible content = repetitive, low information density
- Less compressible content = unique, high information density

Usage:
    poetry run python scripts/analyze_compression.py [--limit 100] [--verbose]
"""

import argparse
import logging
from collections import defaultdict
from typing import Any

from gptme.context.compress import (
    analyze_incremental_compression,
    analyze_log_compression,
)
from gptme.logmanager import Log, get_user_conversations

logger = logging.getLogger(__name__)


def _default_stats() -> dict[str, int | float]:
    """Factory function for defaultdict stats."""
    return {"count": 0, "total_ratio": 0.0}


def analyze_conversations_incremental(limit: int = 100, verbose: bool = False) -> dict:
    """
    Analyze incremental compression ratios for recent conversations.

    This measures marginal information contribution of each message,
    revealing information density trajectory over the conversation.

    Returns:
        Dictionary with incremental analysis results
    """
    results: dict = {
        "conversations": [],
        "overall_stats": {
            "total_conversations": 0,
            "total_messages": 0,
            "avg_novelty_ratio": 0.0,
            "low_novelty_messages": 0,  # ratio < 0.3
            "high_novelty_messages": 0,  # ratio > 0.7
        },
        "by_role": defaultdict(_default_stats),
    }

    print(f"Analyzing incremental compression for up to {limit} conversations...")
    print()

    conversations = list(get_user_conversations())[:limit]
    results["overall_stats"]["total_conversations"] = len(conversations)

    for i, conv in enumerate(conversations):
        if verbose:
            print(f"[{i+1}/{len(conversations)}] Analyzing: {conv.name}")

        try:
            log = Log.read_jsonl(conv.path)
            if not log.messages or len(log.messages) < 2:
                continue

            # Analyze incremental compression
            trajectory = analyze_incremental_compression(log.messages)

            # Store conversation results
            conv_result: dict[str, Any] = {
                "name": conv.name,
                "id": conv.id,
                "messages": len(log.messages),
                "low_novelty_msgs": [],
                "trajectory": [],
            }

            results["overall_stats"]["total_messages"] += len(log.messages)

            # Analyze trajectory
            for msg, stats in trajectory:
                # Track by role
                results["by_role"][msg.role]["count"] += 1
                results["by_role"][msg.role]["total_ratio"] += stats.ratio

                # Track trajectory point
                conv_result["trajectory"].append(
                    {
                        "role": msg.role,
                        "ratio": stats.ratio,
                        "size": stats.original_size,
                    }
                )

                # Track low novelty messages (redundant with context)
                if stats.ratio < 0.3 and len(msg.content) > 100:
                    results["overall_stats"]["low_novelty_messages"] += 1
                    conv_result["low_novelty_msgs"].append(
                        {
                            "role": msg.role,
                            "preview": msg.content[:100] + "...",
                            "stats": stats,
                        }
                    )

                # Track high novelty messages
                if stats.ratio > 0.7:
                    results["overall_stats"]["high_novelty_messages"] += 1

            results["conversations"].append(conv_result)

        except Exception as e:
            logger.error(f"Error analyzing {conv.name}: {e}")
            if verbose:
                logger.exception(e)

    # Calculate averages
    total_ratio = sum(
        role_data["total_ratio"] for role_data in results["by_role"].values()
    )
    total_msgs = results["overall_stats"]["total_messages"]
    if total_msgs > 0:
        results["overall_stats"]["avg_novelty_ratio"] = total_ratio / total_msgs

    return results


def analyze_conversations(limit: int = 100, verbose: bool = False) -> dict:
    """
    Analyze compression ratios for recent conversations.

    Returns:
        Dictionary with analysis results
    """
    results: dict = {
        "conversations": [],
        "overall_stats": {
            "total_conversations": 0,
            "total_messages": 0,
            "avg_compression_ratio": 0.0,
            "highly_compressible": 0,  # ratio < 0.3
            "poorly_compressible": 0,  # ratio > 0.7
        },
        "by_role": defaultdict(_default_stats),
        "by_tool": defaultdict(_default_stats),
    }

    print(f"Analyzing up to {limit} conversations...")
    print()

    conversations = list(get_user_conversations())[:limit]
    results["overall_stats"]["total_conversations"] = len(conversations)

    for i, conv in enumerate(conversations):
        if verbose:
            print(f"[{i+1}/{len(conversations)}] Analyzing: {conv.name}")

        try:
            log = Log.read_jsonl(conv.path)
            if not log.messages:
                continue

            # Analyze overall conversation compression
            overall_stats, message_stats = analyze_log_compression(log.messages)

            # Store conversation results
            conv_result: dict[str, Any] = {
                "name": conv.name,
                "id": conv.id,
                "messages": len(log.messages),
                "overall_compression": overall_stats,
                "highly_compressible_msgs": [],
            }

            results["overall_stats"]["total_messages"] += len(log.messages)

            # Analyze individual messages
            for msg, stats in message_stats:
                # Track by role
                results["by_role"][msg.role]["count"] += 1
                results["by_role"][msg.role]["total_ratio"] += stats.ratio

                # Track highly compressible messages
                if stats.ratio < 0.3 and len(msg.content) > 100:
                    conv_result["highly_compressible_msgs"].append(
                        {
                            "role": msg.role,
                            "preview": msg.content[:100] + "...",
                            "stats": stats,
                        }
                    )

                # Track by tool (for system messages from tools)
                if msg.role == "system" and msg.content:
                    first_word = msg.content.split()[0].lower()
                    if first_word in [
                        "ran",
                        "executed",
                        "saved",
                        "appended",
                        "patch",
                        "error",
                    ]:
                        tool = first_word
                        results["by_tool"][tool]["count"] += 1
                        results["by_tool"][tool]["total_ratio"] += stats.ratio

            results["conversations"].append(conv_result)

            # Track overall compression distribution
            if overall_stats.ratio < 0.3:
                results["overall_stats"]["highly_compressible"] += 1
            elif overall_stats.ratio > 0.7:
                results["overall_stats"]["poorly_compressible"] += 1

        except Exception as e:
            logger.error(f"Error analyzing {conv.name}: {e}")
            if verbose:
                logger.exception(e)

    # Calculate averages
    total_ratio = sum(
        role_data["total_ratio"] for role_data in results["by_role"].values()
    )
    total_msgs = results["overall_stats"]["total_messages"]
    if total_msgs > 0:
        results["overall_stats"]["avg_compression_ratio"] = total_ratio / total_msgs

    return results


def print_results(results: dict, detailed: bool = False):
    """Print analysis results in a readable format."""
    stats = results["overall_stats"]

    print("=" * 80)
    print("COMPRESSION ANALYSIS RESULTS")
    print("=" * 80)
    print()

    # Overall statistics
    print("Overall Statistics:")
    print(f"  Total conversations analyzed: {stats['total_conversations']}")
    print(f"  Total messages: {stats['total_messages']}")
    print(f"  Average compression ratio: {stats['avg_compression_ratio']:.3f}")
    print(
        f"  Highly compressible conversations (ratio < 0.3): {stats['highly_compressible']}"
    )
    print(
        f"  Poorly compressible conversations (ratio > 0.7): {stats['poorly_compressible']}"
    )
    print()

    # By role statistics
    print("Compression by Role:")
    for role, data in sorted(results["by_role"].items()):
        avg_ratio = data["total_ratio"] / data["count"] if data["count"] > 0 else 0
        print(f"  {role:12s}: {avg_ratio:.3f} (n={data['count']:,})")
    print()

    # By tool statistics
    if results["by_tool"]:
        print("Compression by Tool:")
        for tool, data in sorted(
            results["by_tool"].items(),
            key=lambda x: x[1]["total_ratio"] / x[1]["count"],
        ):
            avg_ratio = data["total_ratio"] / data["count"] if data["count"] > 0 else 0
            print(f"  {tool:12s}: {avg_ratio:.3f} (n={data['count']:,})")
        print()

    # Interpretation guide
    print("Interpretation Guide:")
    print("  Ratio < 0.3: Highly compressible (repetitive, low information density)")
    print("  Ratio 0.3-0.7: Normal (balanced content)")
    print("  Ratio > 0.7: Poorly compressible (unique, high information density)")
    print()

    # Most compressible conversations
    if detailed:
        print("=" * 80)
        print("TOP 10 MOST COMPRESSIBLE CONVERSATIONS")
        print("=" * 80)
        print()

        sorted_convs = sorted(
            results["conversations"],
            key=lambda x: x["overall_compression"].ratio,
        )

        for i, conv in enumerate(sorted_convs[:10], 1):
            stats = conv["overall_compression"]
            print(f"{i}. {conv['name']}")
            print(f"   {stats}")
            print(f"   Messages: {conv['messages']}")

            if conv["highly_compressible_msgs"]:
                print(
                    f"   Highly compressible messages: {len(conv['highly_compressible_msgs'])}"
                )
                for msg in conv["highly_compressible_msgs"][:3]:
                    print(f"     - {msg['role']}: {msg['preview']}")
                    print(f"       {msg['stats']}")
            print()


def print_results_incremental(results: dict, detailed: bool = False):
    """Print incremental compression analysis results."""
    stats = results["overall_stats"]

    print("=" * 80)
    print("INCREMENTAL COMPRESSION ANALYSIS RESULTS")
    print("=" * 80)
    print()

    # Overall statistics
    print("Overall Statistics:")
    print(f"  Total conversations analyzed: {stats['total_conversations']}")
    print(f"  Total messages: {stats['total_messages']}")
    print(f"  Average novelty ratio: {stats['avg_novelty_ratio']:.3f}")
    print(f"  Low novelty messages (ratio < 0.3): {stats['low_novelty_messages']}")
    print(f"  High novelty messages (ratio > 0.7): {stats['high_novelty_messages']}")
    print()

    # By role statistics
    print("Information Novelty by Role:")
    for role, data in sorted(results["by_role"].items()):
        avg_ratio = data["total_ratio"] / data["count"] if data["count"] > 0 else 0
        print(f"  {role:12s}: {avg_ratio:.3f} (n={data['count']:,})")
    print()

    # Interpretation guide
    print("Interpretation Guide:")
    print("  Ratio < 0.3: Redundant with context (low novelty)")
    print("  Ratio 0.3-0.7: Moderate novelty")
    print("  Ratio > 0.7: High novelty (adds unique information)")
    print()

    # Detailed breakdown
    if detailed:
        print("=" * 80)
        print("TOP 10 CONVERSATIONS WITH MOST LOW-NOVELTY MESSAGES")
        print("=" * 80)
        print()

        sorted_convs = sorted(
            results["conversations"],
            key=lambda x: len(x["low_novelty_msgs"]),
            reverse=True,
        )

        for i, conv in enumerate(sorted_convs[:10], 1):
            print(f"{i}. {conv['name']}")
            print(f"   Messages: {conv['messages']}")
            print(f"   Low novelty messages: {len(conv['low_novelty_msgs'])}")

            if conv["low_novelty_msgs"]:
                print("   Examples of redundant messages:")
                for msg in conv["low_novelty_msgs"][:3]:
                    print(f"     - {msg['role']}: {msg['preview']}")
                    print(f"       {msg['stats']}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze compression ratios of conversation logs"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of conversations to analyze (default: 100)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show verbose output"
    )
    parser.add_argument(
        "--detailed", "-d", action="store_true", help="Show detailed results"
    )
    parser.add_argument(
        "--incremental",
        "-i",
        action="store_true",
        help="Use incremental compression analysis (measures marginal information contribution)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.incremental:
        results = analyze_conversations_incremental(
            limit=args.limit, verbose=args.verbose
        )
        print_results_incremental(results, detailed=args.detailed)
    else:
        results = analyze_conversations(limit=args.limit, verbose=args.verbose)
        print_results(results, detailed=args.detailed)


if __name__ == "__main__":
    main()
