"""Statistical analysis tools for validation suite.

Provides statistical comparison between compressed and original runs,
including significance testing and confidence intervals.
"""

import math
from typing import Any


def calculate_mean(values: list[float]) -> float:
    """Calculate mean of values."""
    return sum(values) / len(values) if values else 0.0


def calculate_std_dev(values: list[float]) -> float:
    """Calculate standard deviation of values."""
    if len(values) < 2:
        return 0.0

    mean = calculate_mean(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def calculate_confidence_interval(
    values: list[float], confidence: float = 0.95
) -> tuple[float, float]:
    """Calculate confidence interval for mean.

    Args:
        values: Sample values
        confidence: Confidence level (default: 0.95 for 95% CI)

    Returns:
        Tuple of (lower_bound, upper_bound)
    """
    if len(values) < 2:
        return (0.0, 0.0)

    mean = calculate_mean(values)
    std_dev = calculate_std_dev(values)
    n = len(values)

    # Use t-distribution critical value (approximation for small samples)
    # For 95% CI and n>30, use 1.96; for smaller n, use 2.0 as conservative estimate
    t_value = 1.96 if n > 30 else 2.0

    margin = t_value * (std_dev / math.sqrt(n))

    return (mean - margin, mean + margin)


def welch_t_test(group1: list[float], group2: list[float]) -> dict[str, Any]:
    """Perform Welch's t-test (unequal variances assumed).

    Args:
        group1: First sample
        group2: Second sample

    Returns:
        Dictionary with t-statistic and p-value estimate
    """
    if len(group1) < 2 or len(group2) < 2:
        return {
            "t_statistic": 0.0,
            "p_value": 1.0,
            "significant": False,
            "error": "Insufficient data",
        }

    mean1 = calculate_mean(group1)
    mean2 = calculate_mean(group2)

    std1 = calculate_std_dev(group1)
    std2 = calculate_std_dev(group2)

    n1 = len(group1)
    n2 = len(group2)

    # Calculate t-statistic
    numerator = mean1 - mean2
    denominator = math.sqrt((std1**2 / n1) + (std2**2 / n2))

    if denominator == 0:
        return {
            "t_statistic": 0.0,
            "p_value": 1.0,
            "significant": False,
            "error": "Zero variance",
        }

    t_stat = numerator / denominator

    # Degrees of freedom (Welch-Satterthwaite)
    df = (((std1**2 / n1) + (std2**2 / n2)) ** 2) / (
        ((std1**2 / n1) ** 2 / (n1 - 1)) + ((std2**2 / n2) ** 2 / (n2 - 1))
    )

    # Simplified p-value estimation (for common significance levels)
    # For proper p-value, would need scipy.stats or t-distribution table
    abs_t = abs(t_stat)

    # Rough p-value estimates based on t-distribution
    if abs_t > 2.58:  # 99% CI
        p_value = "< 0.01"
    elif abs_t > 1.96:  # 95% CI
        p_value = "< 0.05"
    elif abs_t > 1.645:  # 90% CI
        p_value = "< 0.10"
    else:
        p_value = "> 0.10"

    return {
        "t_statistic": t_stat,
        "degrees_of_freedom": df,
        "p_value": p_value,
        "significant": abs_t > 1.96,  # 95% confidence
    }


def compare_success_rates(
    original_results: list[dict[str, Any]],
    compressed_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare success rates between original and compressed runs.

    Args:
        original_results: Results from original runs
        compressed_results: Results from compressed runs

    Returns:
        Statistical comparison of success rates
    """
    orig_success = [1.0 if r.get("success", False) else 0.0 for r in original_results]
    comp_success = [1.0 if r.get("success", False) else 0.0 for r in compressed_results]

    orig_rate = calculate_mean(orig_success)
    comp_rate = calculate_mean(comp_success)

    orig_ci = calculate_confidence_interval(orig_success)
    comp_ci = calculate_confidence_interval(comp_success)

    # Perform t-test
    t_test = welch_t_test(orig_success, comp_success)

    return {
        "original": {
            "success_rate": orig_rate,
            "confidence_interval": orig_ci,
            "sample_size": len(orig_success),
        },
        "compressed": {
            "success_rate": comp_rate,
            "confidence_interval": comp_ci,
            "sample_size": len(comp_success),
        },
        "difference": orig_rate - comp_rate,
        "statistical_test": t_test,
    }


def compare_durations(
    original_results: list[dict[str, Any]],
    compressed_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare execution durations between original and compressed runs.

    Args:
        original_results: Results from original runs
        compressed_results: Results from compressed runs

    Returns:
        Statistical comparison of durations
    """
    orig_durations = [r.get("duration", 0.0) for r in original_results]
    comp_durations = [r.get("duration", 0.0) for r in compressed_results]

    orig_mean = calculate_mean(orig_durations)
    comp_mean = calculate_mean(comp_durations)

    orig_ci = calculate_confidence_interval(orig_durations)
    comp_ci = calculate_confidence_interval(comp_durations)

    # Perform t-test
    t_test = welch_t_test(orig_durations, comp_durations)

    return {
        "original": {
            "mean_duration": orig_mean,
            "confidence_interval": orig_ci,
            "std_dev": calculate_std_dev(orig_durations),
        },
        "compressed": {
            "mean_duration": comp_mean,
            "confidence_interval": comp_ci,
            "std_dev": calculate_std_dev(comp_durations),
        },
        "difference": orig_mean - comp_mean,
        "statistical_test": t_test,
    }


def compare_criteria_completion(
    original_results: list[dict[str, Any]],
    compressed_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare success criteria completion rates.

    Args:
        original_results: Results from original runs
        compressed_results: Results from compressed runs

    Returns:
        Statistical comparison of criteria completion
    """

    def get_completion_rate(result: dict[str, Any]) -> float:
        criteria = result.get("criteria_met", {})
        if not criteria:
            return 0.0
        met = sum(1 for v in criteria.values() if v)
        return met / len(criteria)

    orig_rates = [get_completion_rate(r) for r in original_results]
    comp_rates = [get_completion_rate(r) for r in compressed_results]

    orig_mean = calculate_mean(orig_rates)
    comp_mean = calculate_mean(comp_rates)

    orig_ci = calculate_confidence_interval(orig_rates)
    comp_ci = calculate_confidence_interval(comp_rates)

    # Perform t-test
    t_test = welch_t_test(orig_rates, comp_rates)

    return {
        "original": {
            "mean_completion": orig_mean,
            "confidence_interval": orig_ci,
        },
        "compressed": {
            "mean_completion": comp_mean,
            "confidence_interval": comp_ci,
        },
        "difference": orig_mean - comp_mean,
        "statistical_test": t_test,
    }


def generate_comparison_report(
    original_results: list[dict[str, Any]],
    compressed_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate comprehensive statistical comparison report.

    Args:
        original_results: Results from original runs
        compressed_results: Results from compressed runs

    Returns:
        Complete statistical comparison
    """
    return {
        "success_rates": compare_success_rates(original_results, compressed_results),
        "durations": compare_durations(original_results, compressed_results),
        "criteria_completion": compare_criteria_completion(
            original_results, compressed_results
        ),
    }


def assess_quality_degradation(
    comparison: dict[str, Any], threshold: float = 0.05
) -> dict[str, Any]:
    """Assess if compression causes significant quality degradation.

    Args:
        comparison: Comparison report from generate_comparison_report
        threshold: Acceptable degradation threshold (default: 5%)

    Returns:
        Assessment of quality impact
    """
    success_diff = comparison["success_rates"]["difference"]
    criteria_diff = comparison["criteria_completion"]["difference"]

    success_significant = comparison["success_rates"]["statistical_test"]["significant"]
    criteria_significant = comparison["criteria_completion"]["statistical_test"][
        "significant"
    ]

    # Assess degradation
    assessment = {
        "success_rate_degradation": abs(success_diff),
        "criteria_completion_degradation": abs(criteria_diff),
        "success_significant": success_significant,
        "criteria_significant": criteria_significant,
        "threshold": threshold,
    }

    # Determine if acceptable
    acceptable = True
    issues = []

    if abs(success_diff) > threshold and success_significant:
        acceptable = False
        issues.append(f"Success rate degraded by {abs(success_diff):.1%} (significant)")

    if abs(criteria_diff) > threshold and criteria_significant:
        acceptable = False
        issues.append(
            f"Criteria completion degraded by {abs(criteria_diff):.1%} (significant)"
        )

    assessment["acceptable"] = acceptable
    assessment["issues"] = issues

    return assessment
