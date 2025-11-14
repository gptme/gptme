"""Compression ratio selection based on task complexity.

Maps complexity scores to compression ratios:
- Focused (0.0-0.3): ratio 0.10-0.20 (aggressive, 80-90% reduction)
- Mixed (0.3-0.7): ratio 0.20-0.30 (moderate, 70-80% reduction)
- Architecture (0.7-1.0): ratio 0.30-0.50 (conservative, 50-70% reduction)

Lower ratio = more aggressive compression (keep less content).
Higher ratio = more conservative compression (keep more content).
"""


def select_compression_ratio(
    complexity: float,
    ratio_ranges: dict[str, tuple[float, float]] | None = None,
    thresholds: dict[str, float] | None = None,
) -> float:
    """Select compression ratio based on task complexity score.

    Args:
        complexity: Complexity score 0.0-1.0
        ratio_ranges: Custom ratio ranges (defaults to standard ranges)
        thresholds: Custom thresholds for category boundaries

    Returns:
        Compression ratio 0.10-0.50 where:
        - Lower ratio = more aggressive compression (keep less)
        - Higher ratio = more conservative compression (keep more)

    Examples:
        >>> select_compression_ratio(0.0)  # Very focused
        0.10
        >>> select_compression_ratio(0.2)  # Focused
        0.166
        >>> select_compression_ratio(0.5)  # Mixed
        0.25
        >>> select_compression_ratio(1.0)  # Architecture
        0.50
    """
    # Default ranges and thresholds
    if ratio_ranges is None:
        ratio_ranges = {
            "focused": (0.10, 0.20),
            "mixed": (0.20, 0.30),
            "architecture": (0.30, 0.50),
        }
    if thresholds is None:
        thresholds = {"focused": 0.3, "architecture": 0.7}

    # Clamp complexity to valid range
    complexity = max(0.0, min(1.0, complexity))

    if complexity < thresholds["focused"]:
        # Focused: Aggressive compression
        min_ratio, max_ratio = ratio_ranges["focused"]
        # Linear interpolation within focused range
        normalized = complexity / thresholds["focused"]
        return min_ratio + (normalized * (max_ratio - min_ratio))

    elif complexity < thresholds["architecture"]:
        # Mixed: Moderate compression
        min_ratio, max_ratio = ratio_ranges["mixed"]
        # Linear interpolation within mixed range
        normalized = (complexity - thresholds["focused"]) / (
            thresholds["architecture"] - thresholds["focused"]
        )
        return min_ratio + (normalized * (max_ratio - min_ratio))

    else:
        # Architecture: Conservative compression
        min_ratio, max_ratio = ratio_ranges["architecture"]
        # Linear interpolation within architecture range
        normalized = (complexity - thresholds["architecture"]) / (
            1.0 - thresholds["architecture"]
        )
        return min_ratio + (normalized * (max_ratio - min_ratio))


def get_ratio_category(ratio: float) -> str:
    """Get category name for compression ratio.

    Args:
        ratio: Compression ratio 0.10-0.50

    Returns:
        Category: "aggressive", "moderate", or "conservative"
    """
    if ratio < 0.20:
        return "aggressive"
    elif ratio < 0.30:
        return "moderate"
    else:
        return "conservative"


def estimate_reduction(ratio: float) -> float:
    """Estimate token reduction percentage for given ratio.

    Args:
        ratio: Compression ratio 0.10-0.50

    Returns:
        Reduction percentage (0.0-1.0)
        e.g., 0.85 means 85% reduction

    Examples:
        >>> estimate_reduction(0.15)  # Aggressive
        0.85
        >>> estimate_reduction(0.25)  # Moderate
        0.75
        >>> estimate_reduction(0.40)  # Conservative
        0.60
    """
    # Reduction = 1 - ratio
    # ratio 0.15 → keep 15% → reduce 85%
    return 1.0 - ratio
