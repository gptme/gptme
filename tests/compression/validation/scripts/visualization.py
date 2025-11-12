"""Visualization tools for validation suite.

Generates charts and HTML reports from validation results.
"""

from datetime import datetime
from pathlib import Path
from typing import Any


def generate_ascii_bar_chart(
    data: dict[str, float], title: str, width: int = 50
) -> str:
    """Generate ASCII bar chart.

    Args:
        data: Dictionary mapping labels to values
        title: Chart title
        width: Maximum bar width in characters

    Returns:
        ASCII art bar chart
    """
    if not data:
        return f"{title}\n(No data)"

    max_value = max(data.values())

    chart = [f"\n{title}", "=" * (width + 20)]

    for label, value in data.items():
        bar_length = int((value / max_value) * width) if max_value > 0 else 0
        bar = "█" * bar_length
        chart.append(f"{label:20s} {bar} {value:.2f}")

    return "\n".join(chart)


def generate_success_rate_chart(original_rate: float, compressed_rate: float) -> str:
    """Generate success rate comparison chart."""
    data = {
        "Original": original_rate * 100,
        "Compressed": compressed_rate * 100,
    }
    return generate_ascii_bar_chart(data, "Success Rate Comparison (%)", width=50)


def generate_duration_chart(
    original_duration: float, compressed_duration: float
) -> str:
    """Generate duration comparison chart."""
    data = {
        "Original": original_duration,
        "Compressed": compressed_duration,
    }
    return generate_ascii_bar_chart(
        data, "Average Duration Comparison (seconds)", width=40
    )


def generate_token_savings_chart(compression_stats: dict[str, Any]) -> str:
    """Generate token savings visualization."""
    reduction_pct = compression_stats.get("token_reduction_pct", 0)
    remaining_pct = 100 - reduction_pct

    data = {
        "Tokens Saved": reduction_pct,
        "Tokens Remaining": remaining_pct,
    }
    return generate_ascii_bar_chart(data, "Token Usage (%)", width=50)


def generate_html_report(
    results: list[dict[str, Any]],
    comparison: dict[str, Any],
    output_path: Path,
) -> None:
    """Generate HTML report with charts and tables.

    Args:
        results: Validation results
        comparison: Statistical comparison
        output_path: Path to save HTML report
    """
    # Group results by config
    by_config: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        config = result.get("config", "unknown")
        if config not in by_config:
            by_config[config] = []
        by_config[config].append(result)

    # Generate HTML
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Context Compression Validation Report</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 1200px;
            margin: 40px auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
        }}
        .section {{
            background: white;
            padding: 25px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .metric {{
            display: inline-block;
            margin: 10px 20px 10px 0;
            padding: 15px 25px;
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            border-radius: 4px;
        }}
        .metric-label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .metric-value {{
            font-size: 28px;
            font-weight: bold;
            color: #333;
            margin-top: 5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #e0e0e0;
        }}
        th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #333;
        }}
        tr:hover {{
            background: #f8f9fa;
        }}
        .success {{ color: #28a745; }}
        .warning {{ color: #ffc107; }}
        .danger {{ color: #dc3545; }}
        .chart {{
            margin: 20px 0;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Context Compression Validation Report</h1>
        <p>Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    </div>
"""

    # Summary metrics
    orig_results = by_config.get("original", [])
    comp_results = by_config.get("compressed-0.15", [])

    if orig_results and comp_results:
        orig_success = sum(1 for r in orig_results if r.get("success")) / len(
            orig_results
        )
        comp_success = sum(1 for r in comp_results if r.get("success")) / len(
            comp_results
        )

        html += f"""
    <div class="section">
        <h2>Summary</h2>
        <div class="metric">
            <div class="metric-label">Total Tasks</div>
            <div class="metric-value">{len(orig_results)}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Original Success Rate</div>
            <div class="metric-value class="success">{orig_success:.1%}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Compressed Success Rate</div>
            <div class="metric-value class="{'success' if comp_success >= 0.95 else 'warning'}">{comp_success:.1%}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Quality Threshold</div>
            <div class="metric-value">95%</div>
        </div>
    </div>
"""

    # Statistical comparison
    success_comp = comparison.get("success_rates", {})
    duration_comp = comparison.get("durations", {})

    html += f"""
    <div class="section">
        <h2>Statistical Analysis</h2>
        <h3>Success Rate Comparison</h3>
        <table>
            <tr>
                <th>Metric</th>
                <th>Original</th>
                <th>Compressed</th>
                <th>Difference</th>
            </tr>
            <tr>
                <td>Success Rate</td>
                <td>{success_comp.get('original', {}).get('success_rate', 0):.1%}</td>
                <td>{success_comp.get('compressed', {}).get('success_rate', 0):.1%}</td>
                <td class="{'success' if success_comp.get('difference', 0) >= 0 else 'warning'}">{success_comp.get('difference', 0):.1%}</td>
            </tr>
            <tr>
                <td>Statistical Significance</td>
                <td colspan="2">
                    {success_comp.get('statistical_test', {}).get('p_value', 'N/A')}
                </td>
                <td>{'Significant' if success_comp.get('statistical_test', {}).get('significant', False) else 'Not significant'}</td>
            </tr>
        </table>

        <h3>Duration Comparison</h3>
        <table>
            <tr>
                <th>Metric</th>
                <th>Original</th>
                <th>Compressed</th>
                <th>Difference</th>
            </tr>
            <tr>
                <td>Mean Duration (s)</td>
                <td>{duration_comp.get('original', {}).get('mean_duration', 0):.1f}</td>
                <td>{duration_comp.get('compressed', {}).get('mean_duration', 0):.1f}</td>
                <td>{duration_comp.get('difference', 0):.1f}</td>
            </tr>
        </table>
    </div>
"""

    # Task results table
    html += """
    <div class="section">
        <h2>Task Results</h2>
        <table>
            <tr>
                <th>Task ID</th>
                <th>Config</th>
                <th>Success</th>
                <th>Criteria Met</th>
                <th>Duration (s)</th>
            </tr>
"""

    for result in results:
        task_id = result.get("task_id", "unknown")
        config = result.get("config", "unknown")
        success = "✓" if result.get("success", False) else "✗"
        success_class = "success" if result.get("success", False) else "danger"

        criteria = result.get("criteria_met", {})
        criteria_pct = (
            sum(1 for v in criteria.values() if v) / len(criteria) if criteria else 0
        )

        duration = result.get("duration", 0)

        html += f"""
            <tr>
                <td>{task_id}</td>
                <td>{config}</td>
                <td class="{success_class}">{success}</td>
                <td>{criteria_pct:.0%}</td>
                <td>{duration:.1f}</td>
            </tr>
"""

    html += """
        </table>
    </div>
</body>
</html>
"""

    output_path.write_text(html)


def generate_markdown_summary(
    results: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> str:
    """Generate markdown summary of validation results.

    Args:
        results: Validation results
        comparison: Statistical comparison

    Returns:
        Markdown formatted summary
    """
    # Group by config
    by_config: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        config = result.get("config", "unknown")
        if config not in by_config:
            by_config[config] = []
        by_config[config].append(result)

    md = ["# Context Compression Validation Summary\n"]
    md.append(f"**Generated**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    # Summary metrics
    orig_results = by_config.get("original", [])
    comp_results = by_config.get("compressed-0.15", [])

    if orig_results and comp_results:
        orig_success = sum(1 for r in orig_results if r.get("success")) / len(
            orig_results
        )
        comp_success = sum(1 for r in comp_results if r.get("success")) / len(
            comp_results
        )

        md.append("## Summary\n")
        md.append(f"- **Total Tasks**: {len(orig_results)}")
        md.append(f"- **Original Success Rate**: {orig_success:.1%}")
        md.append(f"- **Compressed Success Rate**: {comp_success:.1%}")
        md.append("- **Quality Threshold**: 95%")
        md.append(
            f"- **Threshold Met**: {'✅ Yes' if comp_success >= 0.95 else '❌ No'}\n"
        )

    # Statistical analysis
    success_comp = comparison.get("success_rates", {})
    md.append("## Statistical Analysis\n")
    md.append("### Success Rate Comparison\n")
    md.append("| Metric | Original | Compressed | Difference |")
    md.append("|--------|----------|------------|------------|")
    md.append(
        f"| Success Rate | {success_comp.get('original', {}).get('success_rate', 0):.1%} | "
        f"{success_comp.get('compressed', {}).get('success_rate', 0):.1%} | "
        f"{success_comp.get('difference', 0):.1%} |"
    )
    md.append(
        f"| P-value | {success_comp.get('statistical_test', {}).get('p_value', 'N/A')} | "
        f"{'Significant' if success_comp.get('statistical_test', {}).get('significant', False) else 'Not significant'} | - |\n"
    )

    # ASCII charts
    if orig_results and comp_results:
        orig_avg_duration = sum(r.get("duration", 0) for r in orig_results) / len(
            orig_results
        )
        comp_avg_duration = sum(r.get("duration", 0) for r in comp_results) / len(
            comp_results
        )

        md.append("## Visualizations\n")
        md.append("```")
        md.append(generate_success_rate_chart(orig_success, comp_success))
        md.append("")
        md.append(generate_duration_chart(orig_avg_duration, comp_avg_duration))
        md.append("```\n")

    return "\n".join(md)
