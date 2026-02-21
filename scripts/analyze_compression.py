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
            print(f"[{i + 1}/{len(conversations)}] Analyzing: {conv.name}")

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
            print(f"[{i + 1}/{len(conversations)}] Analyzing: {conv.name}")

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


def classify_redundancy(ratio: float) -> tuple[str, str]:
    """
    Classify a message's redundancy level and recommended action.

    Returns:
        Tuple of (category, action)
    """
    if ratio < 0.2:
        return ("HIGHLY_REDUNDANT", "Aggressive compression (90% reduction)")
    if ratio < 0.3:
        return ("REDUNDANT", "Strong compression (70% reduction)")
    if ratio < 0.5:
        return ("MODERATE", "Light compression (30% reduction)")
    if ratio < 0.7:
        return ("UNIQUE", "Preserve (minimal compression)")
    return ("NOVEL", "Preserve completely")


def analyze_distribution(results: dict) -> dict:
    """Analyze the distribution of compression ratios."""
    # Collect all ratios from trajectory
    all_ratios = []
    for conv in results["conversations"]:
        for point in conv.get("trajectory", []):
            all_ratios.append(point["ratio"])

    if not all_ratios:
        return {}

    # Create distribution buckets
    buckets: dict[str, list[float]] = {
        "0.0-0.1": [],
        "0.1-0.2": [],
        "0.2-0.3": [],
        "0.3-0.4": [],
        "0.4-0.5": [],
        "0.5-0.6": [],
        "0.6-0.7": [],
        "0.7-0.8": [],
        "0.8-0.9": [],
        "0.9-1.0": [],
    }

    for ratio in all_ratios:
        if ratio < 0.1:
            buckets["0.0-0.1"].append(ratio)
        elif ratio < 0.2:
            buckets["0.1-0.2"].append(ratio)
        elif ratio < 0.3:
            buckets["0.2-0.3"].append(ratio)
        elif ratio < 0.4:
            buckets["0.3-0.4"].append(ratio)
        elif ratio < 0.5:
            buckets["0.4-0.5"].append(ratio)
        elif ratio < 0.6:
            buckets["0.5-0.6"].append(ratio)
        elif ratio < 0.7:
            buckets["0.6-0.7"].append(ratio)
        elif ratio < 0.8:
            buckets["0.7-0.8"].append(ratio)
        elif ratio < 0.9:
            buckets["0.8-0.9"].append(ratio)
        else:
            buckets["0.9-1.0"].append(ratio)

    return {
        "buckets": buckets,
        "total": len(all_ratios),
        "min": min(all_ratios),
        "max": max(all_ratios),
        "median": sorted(all_ratios)[len(all_ratios) // 2],
    }


def print_distribution(distribution: dict):
    """Print distribution as ASCII histogram."""
    if not distribution:
        return

    print("=" * 80)
    print("REDUNDANCY DISTRIBUTION")
    print("=" * 80)
    print()

    buckets = distribution["buckets"]
    total = distribution["total"]
    max_count = max(len(v) for v in buckets.values())

    print(f"Total messages analyzed: {total}")
    print(f"Range: {distribution['min']:.3f} - {distribution['max']:.3f}")
    print(f"Median: {distribution['median']:.3f}")
    print()

    # ASCII histogram
    print("Distribution (novelty ratio):")
    print()

    for bucket_name, ratios in buckets.items():
        count = len(ratios)
        pct = (count / total * 100) if total > 0 else 0

        # Create bar (max 50 chars)
        bar_len = int((count / max_count) * 50) if max_count > 0 else 0
        bar = "█" * bar_len

        # Color code based on redundancy
        if float(bucket_name.split("-")[0]) < 0.3:
            label = "🔴"  # Highly redundant
        elif float(bucket_name.split("-")[0]) < 0.5:
            label = "🟡"  # Moderate
        else:
            label = "🟢"  # Novel

        print(f"  {label} {bucket_name}: {bar} {count:4d} ({pct:5.1f}%)")

    print()
    print("Classification:")
    print("  🔴 0.0-0.3: Redundant (compress aggressively)")
    print("  🟡 0.3-0.5: Moderate (compress lightly)")
    print("  🟢 0.5-1.0: Novel (preserve)")
    print()


def create_plot(distribution: dict, output_file: str = "compression_distribution.png"):
    """Create matplotlib plot of distribution."""
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        print("Note: Install matplotlib for plot generation: pip install matplotlib")
        return

    buckets = distribution["buckets"]
    bucket_names = list(buckets.keys())
    counts = [len(buckets[name]) for name in bucket_names]

    # Create figure
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Histogram
    colors = [
        "red" if i < 3 else "orange" if i < 5 else "green"
        for i in range(len(bucket_names))
    ]
    ax1.bar(range(len(bucket_names)), counts, color=colors, alpha=0.7)
    ax1.set_xlabel("Novelty Ratio")
    ax1.set_ylabel("Message Count")
    ax1.set_title("Distribution of Information Novelty")
    ax1.set_xticks(range(len(bucket_names)))
    ax1.set_xticklabels(bucket_names, rotation=45, ha="right")
    ax1.grid(axis="y", alpha=0.3)

    # Add classification zones
    ax1.axvline(
        x=2.5, color="red", linestyle="--", alpha=0.5, label="Redundant threshold"
    )
    ax1.axvline(
        x=4.5, color="orange", linestyle="--", alpha=0.5, label="Moderate threshold"
    )
    ax1.legend()

    # Cumulative distribution
    cumsum = np.cumsum(counts)
    ax2.plot(
        range(len(bucket_names)),
        cumsum / cumsum[-1] * 100,
        marker="o",
        linewidth=2,
        markersize=6,
    )
    ax2.set_xlabel("Novelty Ratio")
    ax2.set_ylabel("Cumulative Percentage (%)")
    ax2.set_title("Cumulative Distribution")
    ax2.set_xticks(range(len(bucket_names)))
    ax2.set_xticklabels(bucket_names, rotation=45, ha="right")
    ax2.grid(alpha=0.3)
    ax2.axhline(y=50, color="gray", linestyle=":", alpha=0.5)

    # Add compression zones
    ax2.axvspan(-0.5, 2.5, alpha=0.2, color="red", label="Compress aggressively")
    ax2.axvspan(2.5, 4.5, alpha=0.2, color="orange", label="Compress lightly")
    ax2.axvspan(4.5, 9.5, alpha=0.2, color="green", label="Preserve")
    ax2.legend()

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches="tight")
    print(f"Plot saved to: {output_file}")
    plt.close()


def print_results_incremental(
    results: dict, detailed: bool = False, plot: bool = False
):
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

    # Distribution analysis
    distribution = analyze_distribution(results)
    if distribution:
        print_distribution(distribution)

        if plot:
            create_plot(distribution)

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
    parser.add_argument(
        "--plot",
        "-p",
        action="store_true",
        help="Generate matplotlib plot of distribution (requires matplotlib)",
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
        print_results_incremental(results, detailed=args.detailed, plot=args.plot)
    else:
        results = analyze_conversations(limit=args.limit, verbose=args.verbose)
        print_results(results, detailed=args.detailed)


if __name__ == "__main__":
    main()
