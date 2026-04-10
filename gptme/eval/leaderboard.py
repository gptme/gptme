#!/usr/bin/env python3
"""Aggregate eval results into a model comparison leaderboard.

Reads eval_results/*/eval_results.csv files and generates a comparison table
showing pass rates per model across test suites.

Usage (standalone):
    python -m gptme.eval.leaderboard [--format rst|csv|markdown|json|html] [--min-tests N]

Usage (via eval CLI):
    gptme-eval --leaderboard [--leaderboard-format rst|csv|markdown|json|html]
"""

import argparse
import csv
import html
import io
import json
import logging
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wilson score lower bound — used for ranking and format selection
# ---------------------------------------------------------------------------

# z=1.0 (~68% confidence) gives a moderate penalty for low sample sizes.
# This means 4/4 (100%) ranks below 56/59 (95%) because the confidence
# interval is much wider with n=4.
_WILSON_Z = 1.0
_WILSON_Z2 = _WILSON_Z * _WILSON_Z


def wilson_lower_bound(passed: int, total: int) -> float:
    """Wilson score lower bound for a binomial proportion.

    Penalizes models tested on few tests: a perfect 4/4 result ranks below
    a strong 56/59 result because the confidence interval is wider.
    """
    if total <= 0:
        return 0.0
    p = passed / total
    n = total
    return (
        p
        + _WILSON_Z2 / (2 * n)
        - _WILSON_Z * math.sqrt((p * (1 - p) / n) + _WILSON_Z2 / (4 * n * n))
    ) / (1 + _WILSON_Z2 / n)


# ---------------------------------------------------------------------------
# Test suite membership — auto-derived from the eval suite registry so that
# new practical/basic suites are categorized automatically.
# ---------------------------------------------------------------------------


def _derive_test_sets() -> tuple[frozenset[str], frozenset[str]]:
    """Derive BASIC_TESTS and PRACTICAL_TESTS from the suite registry."""
    try:
        from .suites import suites
    except Exception:
        logger.warning(
            "Failed to import eval suites; BASIC_TESTS and PRACTICAL_TESTS will be empty",
            exc_info=True,
        )
        return frozenset(), frozenset()

    basic: set[str] = set()
    practical: set[str] = set()

    for suite_name, suite_tests in suites.items():
        names = {t["name"] for t in suite_tests}
        if suite_name == "basic":
            basic.update(names)
        elif suite_name.startswith("practical"):
            practical.update(names)

    return frozenset(basic), frozenset(practical)


BASIC_TESTS, PRACTICAL_TESTS = _derive_test_sets()


def normalize_model(model: str) -> str:
    """Normalize model names for cleaner display."""
    replacements = {
        "anthropic/claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet (Oct 2024)",
        "anthropic/claude-3-5-sonnet-20240620": "Claude 3.5 Sonnet (Jun 2024)",
        "anthropic/claude-3-5-haiku-20241022": "Claude 3.5 Haiku",
        "anthropic/claude-3-haiku-20240307": "Claude 3 Haiku",
        "anthropic/claude-haiku-4-5": "Claude Haiku 4.5",
        "anthropic/claude-sonnet-4-20250514": "Claude Sonnet 4",
        "anthropic/claude-sonnet-4-6": "Claude Sonnet 4.6",
        "anthropic/claude-opus-4-1-20250805": "Claude Opus 4.1",
        "anthropic/claude-opus-4-6": "Claude Opus 4.6",
        "openai/gpt-4o": "GPT-4o",
        "openai/gpt-4o-mini": "GPT-4o Mini",
        "openai/gpt-4-turbo": "GPT-4 Turbo",
        "openai/gpt-5": "GPT-5",
        "openai/gpt-5-mini": "GPT-5 Mini",
        "openai/o1-mini": "o1-mini",
        "openai/o1-preview": "o1-preview",
        "openai-subscription/gpt-5.4": "GPT-5.4",
        "deepseek/deepseek-chat": "DeepSeek V3",
        "deepseek/deepseek-reasoner": "DeepSeek R1",
        "gemini/gemini-1.5-flash-latest": "Gemini 1.5 Flash",
        "gemini/gemini-2.5-flash": "Gemini 2.5 Flash",
        "groq/moonshotai/kimi-k2-instruct": "Kimi K2",
        "groq/qwen/qwen3-32b": "Qwen3 32B",
        "openrouter/google/gemini-flash-1.5": "Gemini 1.5 Flash (OR)",
        "openrouter/google/gemini-pro-1.5": "Gemini 1.5 Pro (OR)",
        "openrouter/google/gemma-2-9b-it": "Gemma 2 9B",
        "openrouter/google/gemma-2-27b-it": "Gemma 2 27B",
        "openrouter/meta-llama/llama-3.1-8b-instruct": "Llama 3.1 8B",
        "openrouter/meta-llama/llama-3.1-70b-instruct": "Llama 3.1 70B",
        "openrouter/meta-llama/llama-3.1-405b-instruct": "Llama 3.1 405B",
        "openrouter/meta-llama/llama-3.2-11b-vision-instruct": "Llama 3.2 11B",
        "openrouter/meta-llama/llama-3.2-90b-vision-instruct": "Llama 3.2 90B",
        "openrouter/nousresearch/hermes-2-pro-llama-3-8b": "Hermes 2 Pro 8B",
        "openrouter/nousresearch/hermes-3-llama-3.1-405b": "Hermes 3 405B",
        "openrouter/nousresearch/hermes-3-llama-3.1-70b": "Hermes 3 70B",
        "openrouter/nousresearch/hermes-4-70b": "Hermes 4 70B",
        "openrouter/mistralai/magistral-medium-2506": "Magistral Medium",
        "openrouter/moonshotai/kimi-k2-0905": "Kimi K2 (OR)",
        "openrouter/qwen/qwen3-max": "Qwen3 Max",
        "openrouter/x-ai/grok-4-fast:free": "Grok 4 Fast",
        "openrouter/x-ai/grok-code-fast-1": "Grok Code Fast",
        "openrouter/z-ai/glm-5": "GLM-5",
        # OpenRouter-proxied versions of direct-API models
        "openrouter/openai/gpt-4o-mini": "GPT-4o Mini (OR)",
        "openrouter/anthropic/claude-sonnet-4-6": "Claude Sonnet 4.6 (OR)",
        "openrouter/anthropic/claude-sonnet-4-5": "Claude Sonnet 4.5 (OR)",
        "openrouter/anthropic/claude-haiku-4-5": "Claude Haiku 4.5 (OR)",
        "openrouter/anthropic/claude-opus-4-6": "Claude Opus 4.6 (OR)",
        "openrouter/openai/gpt-4o": "GPT-4o (OR)",
        "openrouter/deepseek/deepseek-chat": "DeepSeek V3 (OR)",
        # Model IDs without date suffix
        "anthropic/claude-sonnet-4-5": "Claude Sonnet 4.5",
    }
    return replacements.get(model, model)


def parse_model_format(model: str, fmt: str) -> tuple[str, str]:
    """Parse model name and format, handling @format suffix in model name.

    Some older CSV rows encode format in the model name as 'model@format'.
    Newer rows use the Tool Format column separately.
    """
    if "@" in model:
        base, embedded_fmt = model.rsplit("@", 1)
        return base, embedded_fmt
    return model, fmt


def load_results(results_dir: Path) -> list[dict]:
    """Load all eval results from CSV files in timestamped subdirectories."""
    if not results_dir.exists():
        return []
    all_results = []
    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        csv_path = d / "eval_results.csv"
        if not csv_path.exists():
            continue
        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_model = row.get("Model", "").strip()
                    raw_fmt = row.get("Tool Format", "").strip()
                    test = row.get("Test", "").strip()
                    passed = row.get("Passed", "").strip().lower()
                    if raw_model and test and passed in ("true", "false"):
                        model, fmt = parse_model_format(raw_model, raw_fmt)
                        all_results.append(
                            {
                                "model": model,
                                "format": fmt,
                                "test": test,
                                "passed": passed == "true",
                                "run_dir": d.name,
                            }
                        )
        except csv.Error as e:
            print(f"Warning: skipping malformed CSV {csv_path}: {e}", file=sys.stderr)
    return all_results


def aggregate_results(results: list[dict], min_tests: int = 4) -> list[dict]:
    """Aggregate results into per-model stats.

    For each model, picks the best tool format and computes pass rates
    on basic and practical test suites.
    """
    # Group by (model, format) -> test -> latest result
    model_fmt: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in results:
        model_fmt[(r["model"], r["format"])][r["test"]].append(r)

    # For each (model, format), compute stats
    fmt_stats = {}
    for (model, fmt), tests_dict in model_fmt.items():
        basic_passed = 0
        basic_total = 0
        practical_passed = 0
        practical_total = 0
        total_passed = 0
        total_tests = 0

        for test_name, runs in tests_dict.items():
            # Sort by run_dir to ensure chronological order
            latest = sorted(runs, key=lambda r: r["run_dir"])[-1]
            total_tests += 1
            if latest["passed"]:
                total_passed += 1

            if test_name in BASIC_TESTS:
                basic_total += 1
                if latest["passed"]:
                    basic_passed += 1
            elif test_name in PRACTICAL_TESTS:
                practical_total += 1
                if latest["passed"]:
                    practical_passed += 1

        if total_tests >= min_tests:
            fmt_stats[(model, fmt)] = {
                "model": model,
                "format": fmt,
                "basic_passed": basic_passed,
                "basic_total": basic_total,
                "practical_passed": practical_passed,
                "practical_total": practical_total,
                "total_passed": total_passed,
                "total_tests": total_tests,
                "pass_rate": total_passed / total_tests if total_tests > 0 else 0,
            }

    # Compute Wilson score lower bound for all format entries.
    for stats in fmt_stats.values():
        n: int = stats["total_tests"]  # type: ignore[assignment]
        passed: int = stats["total_passed"]  # type: ignore[assignment]
        stats["ranking_score"] = wilson_lower_bound(passed, n)

    # For each base model, pick the best format by Wilson score (consistent
    # with the final ranking criterion). Tie-break: prefer format with more tests.
    best_by_model: dict[str, dict] = {}
    for (model, _fmt), stats in fmt_stats.items():
        current = best_by_model.get(model)
        if (
            current is None
            or stats["ranking_score"] > current["ranking_score"]
            or (
                stats["ranking_score"] == current["ranking_score"]
                and stats["total_tests"] > current["total_tests"]
            )
        ):
            best_by_model[model] = stats

    # Sort by Wilson score descending, then total tests descending
    ranked = sorted(
        best_by_model.values(),
        key=lambda x: (-x.get("ranking_score", 0), -x["total_tests"]),
    )
    return ranked


def format_rst_table(ranked: list[dict]) -> str:
    """Format results as an RST table."""
    lines = []

    max_model_len = max(
        (len(normalize_model(s["model"])) for s in ranked),
        default=5,
    )
    model_col_width = max(5, max_model_len)

    cols = [
        ("Model", model_col_width),
        ("Format", 10),
        ("Overall", 15),
        ("Basic", 10),
        ("Practical", 10),
    ]
    header_sep = "  ".join("=" * w for _, w in cols)
    header = "  ".join(f"{name:<{w}}" for name, w in cols)

    lines.append(header_sep)
    lines.append(header)
    lines.append(header_sep)

    for stats in ranked:
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        overall = (
            f"{stats['total_passed']}/{stats['total_tests']} ({stats['pass_rate']:.0%})"
        )
        if stats["basic_total"] > 0:
            basic = f"{stats['basic_passed']}/{stats['basic_total']}"
        else:
            basic = "-"
        if stats["practical_total"] > 0:
            practical = f"{stats['practical_passed']}/{stats['practical_total']}"
        else:
            practical = "-"

        row = (
            f"{display_name:<{cols[0][1]}}  "
            f"{fmt:<{cols[1][1]}}  "
            f"{overall:<{cols[2][1]}}  "
            f"{basic:<{cols[3][1]}}  "
            f"{practical:<{cols[4][1]}}"
        )
        lines.append(row)

    lines.append(header_sep)
    return "\n".join(lines)


def format_csv_table(ranked: list[dict]) -> str:
    """Format results as a CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Model", "Format", "Passed", "Total", "Pass Rate", "Basic", "Practical"]
    )
    for stats in ranked:
        writer.writerow(
            [
                normalize_model(stats["model"]),
                stats["format"] or "default",
                stats["total_passed"],
                stats["total_tests"],
                f"{stats['pass_rate']:.1%}",
                f"{stats['basic_passed']}/{stats['basic_total']}"
                if stats["basic_total"]
                else "-",
                f"{stats['practical_passed']}/{stats['practical_total']}"
                if stats["practical_total"]
                else "-",
            ]
        )
    return output.getvalue()


def format_markdown_table(ranked: list[dict]) -> str:
    """Format results as a Markdown table."""
    lines = []
    lines.append("| Model | Format | Overall | Basic | Practical |")
    lines.append("|-------|--------|---------|-------|-----------|")

    for stats in ranked:
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        overall = (
            f"{stats['total_passed']}/{stats['total_tests']} ({stats['pass_rate']:.0%})"
        )
        if stats["basic_total"] > 0:
            basic = f"{stats['basic_passed']}/{stats['basic_total']}"
        else:
            basic = "-"
        if stats["practical_total"] > 0:
            practical = f"{stats['practical_passed']}/{stats['practical_total']}"
        else:
            practical = "-"
        lines.append(f"| {display_name} | {fmt} | {overall} | {basic} | {practical} |")

    return "\n".join(lines)


def format_html_page(ranked: list[dict]) -> str:
    """Format results as a self-contained interactive HTML page for publishing.

    Features: sortable columns (click headers), search/filter, keyboard nav (/ to focus).
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_models = len(ranked)
    max_tests = max((s["total_tests"] for s in ranked), default=0)

    rows_html = []
    for i, stats in enumerate(ranked):
        display_name = normalize_model(stats["model"])
        fmt = stats["format"] or "default"
        pct = stats["pass_rate"] * 100

        if pct >= 80:
            badge_class = "badge-green"
        elif pct >= 50:
            badge_class = "badge-yellow"
        else:
            badge_class = "badge-red"

        basic = (
            f"{stats['basic_passed']}/{stats['basic_total']}"
            if stats["basic_total"] > 0
            else "-"
        )
        practical = (
            f"{stats['practical_passed']}/{stats['practical_total']}"
            if stats["practical_total"] > 0
            else "-"
        )

        basic_sort = (
            stats["basic_passed"] / stats["basic_total"] * 100
            if stats["basic_total"] > 0
            else None
        )
        practical_sort = (
            stats["practical_passed"] / stats["practical_total"] * 100
            if stats["practical_total"] > 0
            else None
        )
        # Empty string → JS parseFloat returns NaN → sort sends to end
        basic_data = f"{basic_sort:.2f}" if basic_sort is not None else ""
        practical_data = f"{practical_sort:.2f}" if practical_sort is not None else ""

        rows_html.append(
            f"<tr data-rank='{i + 1}' data-model='{_html_escape(display_name)}'"
            f" data-rate='{pct:.2f}' data-passed='{stats['total_passed']}'"
            f" data-basic='{basic_data}' data-practical='{practical_data}'>"
            f"<td class='rank'>{i + 1}</td>"
            f"<td class='model'>{_html_escape(display_name)}</td>"
            f"<td>{_html_escape(fmt)}</td>"
            f"<td><span class='badge {badge_class}'>{pct:.0f}%</span></td>"
            f"<td>{stats['total_passed']}/{stats['total_tests']}</td>"
            f"<td>{basic}</td>"
            f"<td>{practical}</td>"
            f"<td><div class='bar' style='width:{pct:.0f}%'></div></td>"
            f"</tr>"
        )

    rows_str = "\n        ".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gptme Eval Leaderboard</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --accent: #58a6ff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); padding: 2rem 1rem;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h1 a {{ color: var(--accent); text-decoration: none; }}
  h1 a:hover {{ text-decoration: underline; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem; }}
  .search-box {{
    margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem;
  }}
  .search-box input {{
    background: var(--surface); border: 1px solid var(--border); color: var(--text);
    padding: 0.4rem 0.75rem; border-radius: 6px; font-size: 0.85rem; width: 260px;
  }}
  .search-box input:focus {{ outline: none; border-color: var(--accent); }}
  .search-box kbd {{
    background: var(--bg); border: 1px solid var(--border); border-radius: 3px;
    padding: 0.1rem 0.4rem; font-size: 0.7rem; color: var(--muted);
  }}
  .search-box .count {{ color: var(--muted); font-size: 0.8rem; }}
  table {{
    width: 100%; border-collapse: collapse;
    background: var(--surface); border-radius: 6px; overflow: hidden;
  }}
  th, td {{ padding: 0.6rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{
    background: var(--bg); font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--muted); cursor: pointer; user-select: none;
    white-space: nowrap;
  }}
  th:hover {{ color: var(--text); }}
  th .sort-icon {{ margin-left: 0.3rem; font-size: 0.65rem; }}
  th.sorted-asc .sort-icon::after {{ content: ' \\25B2'; }}
  th.sorted-desc .sort-icon::after {{ content: ' \\25BC'; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.hidden {{ display: none; }}
  .rank {{ color: var(--muted); width: 2rem; text-align: center; }}
  .model {{ font-weight: 600; }}
  .badge {{
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 12px;
    font-size: 0.8rem; font-weight: 600;
  }}
  .badge-green {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .badge-yellow {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .badge-red {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  td:last-child {{ width: 120px; }}
  .bar {{
    height: 8px; border-radius: 4px; background: var(--accent);
    min-width: 2px; transition: width 0.3s;
  }}
  footer {{ margin-top: 1.5rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
  footer a {{ color: var(--accent); text-decoration: none; }}
  @media (max-width: 640px) {{
    th, td {{ padding: 0.4rem; font-size: 0.85rem; }}
    td:last-child {{ display: none; }}
    th:last-child {{ display: none; }}
    .search-box input {{ width: 100%; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1><a href="https://gptme.org">gptme</a> Eval Leaderboard</h1>
  <p class="meta">{total_models} models &middot; up to {max_tests} tests &middot; updated {now}</p>
  <div class="search-box">
    <input type="text" id="search" placeholder="Filter models..." autocomplete="off">
    <kbd>/</kbd>
    <span class="count" id="count"></span>
  </div>
  <table id="leaderboard">
    <thead>
      <tr>
        <th data-sort="rank" class="sorted-asc">#<span class="sort-icon"></span></th>
        <th data-sort="model">Model<span class="sort-icon"></span></th>
        <th>Format</th>
        <th data-sort="rate">Rate<span class="sort-icon"></span></th>
        <th data-sort="passed">Passed<span class="sort-icon"></span></th>
        <th data-sort="basic">Basic<span class="sort-icon"></span></th>
        <th data-sort="practical">Practical<span class="sort-icon"></span></th>
        <th></th>
      </tr>
    </thead>
    <tbody>
        {rows_str}
    </tbody>
  </table>
  <footer>
    Generated by <a href="https://github.com/gptme/gptme">gptme</a> eval suite
    &middot; <code>gptme-eval --leaderboard --leaderboard-format html</code>
  </footer>
</div>
<script>
(function() {{
  var table = document.getElementById('leaderboard');
  var tbody = table.querySelector('tbody');
  var searchInput = document.getElementById('search');
  var countEl = document.getElementById('count');
  var headers = table.querySelectorAll('th[data-sort]');
  var currentSort = 'rank';
  var currentDir = 'asc';

  function sortTable(key, dir) {{
    var rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort(function(a, b) {{
      if (key === 'model') {{
        var av = a.dataset[key] || '';
        var bv = b.dataset[key] || '';
        var cmp = av.localeCompare(bv);
        return dir === 'asc' ? cmp : -cmp;
      }}
      var av = parseFloat(a.dataset[key]);
      var bv = parseFloat(b.dataset[key]);
      // NaN (missing data, e.g. no basic/practical tests) always sorts to end
      if (isNaN(av) && isNaN(bv)) return 0;
      if (isNaN(av)) return 1;
      if (isNaN(bv)) return -1;
      return dir === 'asc' ? av - bv : bv - av;
    }});
    rows.forEach(function(r) {{ tbody.appendChild(r); }});
    // Re-apply current filter so rank cells are correct for all rows
    // (including hidden ones that reappear when filter is cleared)
    filterRows(searchInput.value);
  }}

  function updateCount() {{
    var total = tbody.querySelectorAll('tr').length;
    var visible = tbody.querySelectorAll('tr:not(.hidden)').length;
    countEl.textContent = visible < total ? visible + '/' + total : '';
  }}

  function filterRows(query) {{
    var q = query.toLowerCase();
    var rows = tbody.querySelectorAll('tr');
    var rank = 0;
    rows.forEach(function(r) {{
      var model = (r.dataset.model || '').toLowerCase();
      if (!q || model.indexOf(q) !== -1) {{
        r.classList.remove('hidden');
        rank++;
        r.querySelector('.rank').textContent = rank;
      }} else {{
        r.classList.add('hidden');
      }}
    }});
    updateCount();
  }}

  headers.forEach(function(th) {{
    th.addEventListener('click', function() {{
      var key = th.dataset.sort;
      var dir = (currentSort === key && currentDir === 'asc') ? 'desc' : 'asc';
      // Default to descending for numeric columns (except rank)
      if (currentSort !== key && key !== 'rank' && key !== 'model') dir = 'desc';
      currentSort = key;
      currentDir = dir;
      headers.forEach(function(h) {{ h.classList.remove('sorted-asc', 'sorted-desc'); }});
      th.classList.add(dir === 'asc' ? 'sorted-asc' : 'sorted-desc');
      sortTable(key, dir);
    }});
  }});

  searchInput.addEventListener('input', function() {{ filterRows(this.value); }});

  document.addEventListener('keydown', function(e) {{
    if (e.key === '/' && document.activeElement !== searchInput) {{
      e.preventDefault();
      searchInput.focus();
    }}
    if (e.key === 'Escape') {{
      searchInput.value = '';
      filterRows('');
      searchInput.blur();
    }}
  }});
}})();
</script>
</body>
</html>"""


def _html_escape(s: str) -> str:
    """HTML escaping for untrusted model names in HTML attributes and content.

    html.escape(quote=True) escapes " but not '; we also escape ' because
    some data-* attributes use single-quote delimiters.
    """
    return html.escape(s).replace("'", "&#x27;")


def format_json(ranked: list[dict]) -> str:
    """Format results as JSON for programmatic use."""
    models = [
        {
            "model": stats["model"],
            "display_name": normalize_model(stats["model"]),
            "format": stats["format"] or "default",
            "pass_rate": round(stats["pass_rate"], 4),
            "ranking_score": round(stats.get("ranking_score", 0), 4),
            "total_passed": stats["total_passed"],
            "total_tests": stats["total_tests"],
            "basic": {
                "passed": stats["basic_passed"],
                "total": stats["basic_total"],
            },
            "practical": {
                "passed": stats["practical_passed"],
                "total": stats["practical_total"],
            },
        }
        for stats in ranked
    ]
    return json.dumps({"models": models}, indent=2)


def aggregate_per_test(
    results: list[dict], min_tests: int = 4
) -> tuple[list[str], list[str], dict[str, dict[str, bool | None]]]:
    """Build a model x test matrix from results.

    Returns:
        (model_names, test_names, matrix) where matrix[model][test] is
        True/False/None (not tested).
    """
    # Group by (model, format) -> test -> runs
    model_fmt: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in results:
        model_fmt[(r["model"], r["format"])][r["test"]].append(r)

    # Pick best format per model by Wilson score (consistent with aggregate_results)
    best_fmt: dict[str, tuple[str, dict[str, list[dict]], float]] = {}
    for (model, fmt), tests_dict in model_fmt.items():
        total = len(tests_dict)
        if total < min_tests:
            continue
        passed = sum(
            1
            for runs in tests_dict.values()
            if sorted(runs, key=lambda r: r["run_dir"])[-1]["passed"]
        )
        score = wilson_lower_bound(passed, total)
        current = best_fmt.get(model)
        if current is None:
            best_fmt[model] = (fmt, tests_dict, score)
        else:
            cur_score = current[2]
            cur_total = len(current[1])
            if score > cur_score or (score == cur_score and total > cur_total):
                best_fmt[model] = (fmt, tests_dict, score)

    # Collect all test names across all models
    all_tests: set[str] = set()
    for _fmt, tests_dict, _score in best_fmt.values():
        all_tests.update(tests_dict.keys())

    # Order tests: basic first, then practical, then others — alphabetical within each
    basic_sorted = sorted(t for t in all_tests if t in BASIC_TESTS)
    practical_sorted = sorted(t for t in all_tests if t in PRACTICAL_TESTS)
    other_sorted = sorted(
        t for t in all_tests if t not in BASIC_TESTS and t not in PRACTICAL_TESTS
    )
    test_names = basic_sorted + practical_sorted + other_sorted

    # Build matrix
    matrix: dict[str, dict[str, bool | None]] = {}
    model_wilson_scores: dict[str, float] = {}
    for model, (_fmt, tests_dict, fmt_score) in best_fmt.items():
        row: dict[str, bool | None] = {}
        for test in test_names:
            if test in tests_dict:
                latest = sorted(tests_dict[test], key=lambda r: r["run_dir"])[-1]
                row[test] = latest["passed"]
            else:
                row[test] = None
        matrix[model] = row
        model_wilson_scores[model] = fmt_score

    # Sort models by Wilson score descending (consistent with summary leaderboard)
    model_names = sorted(matrix.keys(), key=lambda m: -model_wilson_scores[m])

    return model_names, test_names, matrix


def format_per_test_markdown(
    model_names: list[str],
    test_names: list[str],
    matrix: dict[str, dict[str, bool | None]],
) -> str:
    """Format per-test breakdown as a Markdown table."""
    display_names = [normalize_model(m) for m in model_names]
    lines = []
    header = "| Test | " + " | ".join(display_names) + " |"
    sep = "|------|" + "|".join(":-:" for _ in model_names) + "|"
    lines.append(header)
    lines.append(sep)

    for test in test_names:
        cells = []
        for model in model_names:
            val = matrix[model].get(test)
            if val is True:
                cells.append("P")
            elif val is False:
                cells.append("F")
            else:
                cells.append("-")
        lines.append(f"| {test} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


def format_per_test_html(
    model_names: list[str],
    test_names: list[str],
    matrix: dict[str, dict[str, bool | None]],
) -> str:
    """Format per-test breakdown as a self-contained HTML page."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    display_names = [normalize_model(m) for m in model_names]

    # Build header
    th_models = "".join(
        f"<th class='model-col'>{_html_escape(n)}</th>" for n in display_names
    )

    # Build rows
    rows = []
    current_suite = None
    for test in test_names:
        # Determine suite for section headers
        if test in BASIC_TESTS:
            suite = "Basic"
        elif test in PRACTICAL_TESTS:
            suite = "Practical"
        else:
            suite = "Other"
        if suite != current_suite:
            current_suite = suite
            rows.append(
                f"<tr class='suite-header'>"
                f"<td colspan='{len(model_names) + 1}'>{suite}</td></tr>"
            )

        cells = []
        for model in model_names:
            val = matrix[model].get(test)
            if val is True:
                cells.append("<td class='pass'>P</td>")
            elif val is False:
                cells.append("<td class='fail'>F</td>")
            else:
                cells.append("<td class='na'>-</td>")
        rows.append(
            f"<tr><td class='test-name'>{_html_escape(test)}</td>{''.join(cells)}</tr>"
        )

    rows_str = "\n        ".join(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gptme Eval Per-Test Breakdown</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --accent: #58a6ff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); padding: 2rem 1rem;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; overflow-x: auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h1 a {{ color: var(--accent); text-decoration: none; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  table {{
    border-collapse: collapse; background: var(--surface);
    border-radius: 6px; overflow: hidden; white-space: nowrap;
  }}
  th, td {{
    padding: 0.4rem 0.6rem; text-align: center;
    border-bottom: 1px solid var(--border); font-size: 0.85rem;
  }}
  th {{ background: var(--bg); color: var(--muted); font-size: 0.75rem;
       text-transform: uppercase; letter-spacing: 0.05em; position: sticky; top: 0; }}
  .test-name {{ text-align: left; font-family: monospace; font-size: 0.8rem; }}
  .model-col {{ writing-mode: vertical-rl; text-orientation: mixed;
                max-width: 2rem; height: 8rem; font-weight: 600; }}
  .pass {{ color: var(--green); font-weight: 700; }}
  .fail {{ color: var(--red); font-weight: 700; }}
  .na {{ color: var(--muted); }}
  .suite-header td {{
    text-align: left; font-weight: 700; font-size: 0.8rem;
    background: var(--bg); color: var(--accent); padding: 0.5rem 0.6rem;
    text-transform: uppercase; letter-spacing: 0.05em;
  }}
  tr:last-child td {{ border-bottom: none; }}
  footer {{ margin-top: 1.5rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
  footer a {{ color: var(--accent); text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
  <h1><a href="https://gptme.org">gptme</a> Per-Test Breakdown</h1>
  <p class="meta">{len(model_names)} models &middot; {len(test_names)} tests &middot; updated {now}</p>
  <table>
    <thead>
      <tr><th>Test</th>{th_models}</tr>
    </thead>
    <tbody>
        {rows_str}
    </tbody>
  </table>
  <footer>
    Generated by <a href="https://github.com/gptme/gptme">gptme</a> eval suite
    &middot; <code>python -m gptme.eval.leaderboard --per-test --format html</code>
  </footer>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Trend analysis — per-model pass-rate timelines with sparklines
#
# Complements gptme.eval.trends (per-test regression/improvement detection)
# by providing per-MODEL aggregate rate trajectories over time.
# ---------------------------------------------------------------------------

_RUN_DIR_RE = re.compile(r"^(\d{8})_(\d{6})Z$")


def _parse_run_date(run_dir: str) -> datetime | None:
    """Parse a YYYYMMDD_HHMMSSZ run directory name into a datetime."""
    m = _RUN_DIR_RE.match(run_dir)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}{m.group(2)}", "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def compute_rate_trends(
    results: list[dict],
    min_tests: int = 4,
    window_days: int = 90,
) -> dict:
    """Compute per-model pass-rate trajectories over time.

    Unlike ``gptme.eval.trends.compute_trends`` (which tracks per-test
    regressions/improvements), this function computes *aggregate* model
    pass rates per day, suitable for sparkline visualisation.

    Returns a dict with:
        - daily_rates: {model: [(date_str, pass_rate, passed, total), ...]}
        - regressions: [(model, test, last_pass_date, first_fail_date)]
        - improvements: [(model, test, last_fail_date, first_pass_date)]
        - overall_trend: {model: (slope_direction, recent_rate, oldest_rate)}
    """
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - window_days * 86400

    # Parse dates and filter to window
    dated_results = []
    for r in results:
        dt = _parse_run_date(r["run_dir"])
        if dt and dt.timestamp() >= cutoff:
            dated_results.append({**r, "_dt": dt, "_date": dt.strftime("%Y-%m-%d")})

    if not dated_results:
        return {
            "daily_rates": {},
            "regressions": [],
            "improvements": [],
            "overall_trend": {},
        }

    # Group by (model, format) -> date -> test -> passed
    model_fmt_daily: dict[tuple[str, str], dict[str, dict[str, bool]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for r in dated_results:
        key = (r["model"], r["format"])
        model_fmt_daily[key][r["_date"]][r["test"]] = r["passed"]

    # Pick best format per model (same logic as aggregate_results)
    best_fmt: dict[str, str] = {}
    fmt_test_counts: dict[tuple[str, str], int] = {}
    for (model, fmt), daily in model_fmt_daily.items():
        all_tests: set[str] = set()
        for tests in daily.values():
            all_tests.update(tests.keys())
        fmt_test_counts[(model, fmt)] = len(all_tests)
        current = best_fmt.get(model)
        if current is None or len(all_tests) > fmt_test_counts.get((model, current), 0):
            best_fmt[model] = fmt

    # Build daily rates for each model using best format
    daily_rates: dict[str, list[tuple[str, float, int, int]]] = {}
    for model, fmt in best_fmt.items():
        daily = model_fmt_daily[(model, fmt)]
        sorted_dates = sorted(daily.keys())
        rates = []
        for date_str in sorted_dates:
            tests = daily[date_str]
            if len(tests) < min_tests:
                continue
            passed = sum(1 for v in tests.values() if v)
            total = len(tests)
            rates.append((date_str, passed / total, passed, total))
        if rates:
            daily_rates[model] = rates

    # Detect regressions and improvements by comparing last two runs per test
    regressions: list[tuple[str, str, str, str]] = []
    improvements: list[tuple[str, str, str, str]] = []
    for model, fmt in best_fmt.items():
        daily = model_fmt_daily[(model, fmt)]
        sorted_dates = sorted(daily.keys())
        if len(sorted_dates) < 2:
            continue

        test_history: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        for date_str in sorted_dates:
            for test, passed in daily[date_str].items():
                test_history[test].append((date_str, passed))

        for test, history in test_history.items():
            if len(history) < 2:
                continue
            prev_date, prev_passed = history[-2]
            curr_date, curr_passed = history[-1]
            if prev_passed and not curr_passed:
                regressions.append((model, test, prev_date, curr_date))
            elif not prev_passed and curr_passed:
                improvements.append((model, test, prev_date, curr_date))

    # Overall trend direction per model
    overall_trend: dict[str, tuple[str, float, float]] = {}
    for model, rates in daily_rates.items():
        if len(rates) >= 2:
            oldest_rate = rates[0][1]
            recent_rate = rates[-1][1]
            if recent_rate > oldest_rate + 0.02:
                direction = "improving"
            elif recent_rate < oldest_rate - 0.02:
                direction = "declining"
            else:
                direction = "stable"
            overall_trend[model] = (direction, recent_rate, oldest_rate)

    return {
        "daily_rates": daily_rates,
        "regressions": regressions,
        "improvements": improvements,
        "overall_trend": overall_trend,
    }


def format_trends_html(trends: dict) -> str:
    """Render trend analysis as a self-contained HTML page."""
    daily_rates = trends["daily_rates"]
    regressions = trends["regressions"]
    improvements = trends["improvements"]
    overall_trend = trends["overall_trend"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Sort models by latest pass rate
    sorted_models = sorted(
        daily_rates.keys(),
        key=lambda m: daily_rates[m][-1][1] if daily_rates[m] else 0,
        reverse=True,
    )

    # Build sparkline data and trend rows
    trend_rows = []
    for model in sorted_models:
        rates = daily_rates[model]
        display_name = _html_escape(normalize_model(model))
        latest = rates[-1]
        rate_pct = f"{latest[1]:.0%}"
        passed_total = f"{latest[2]}/{latest[3]}"

        # Sparkline: normalize rates to 0-100 for SVG
        spark_points = []
        for i, (_, rate, _, _) in enumerate(rates):
            x = (i / max(len(rates) - 1, 1)) * 100
            y = 100 - rate * 100  # SVG y is top-down
            spark_points.append(f"{x:.1f},{y:.1f}")
        polyline = " ".join(spark_points)

        # Trend indicator
        trend_info = overall_trend.get(model, ("stable", 0, 0))
        direction = trend_info[0]
        if direction == "improving":
            trend_icon = "&#x25B2;"  # ▲
            trend_class = "trend-up"
        elif direction == "declining":
            trend_icon = "&#x25BC;"  # ▼
            trend_class = "trend-down"
        else:
            trend_icon = "&#x25CF;"  # ●
            trend_class = "trend-stable"

        # Delta
        if len(rates) >= 2:
            delta = rates[-1][1] - rates[0][1]
            delta_str = f"{delta:+.1%}"
        else:
            delta_str = "-"

        trend_rows.append(f"""        <tr>
          <td class='model-name'>{display_name}</td>
          <td class='rate'>{rate_pct}</td>
          <td class='passed'>{passed_total}</td>
          <td class='sparkline-cell'>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="sparkline">
              <polyline points="{polyline}" fill="none" stroke="var(--accent)" stroke-width="2"/>
            </svg>
          </td>
          <td class='{trend_class}'>{trend_icon} {delta_str}</td>
        </tr>""")

    trend_rows_str = "\n".join(trend_rows)

    # Regressions table
    regression_rows = []
    for model, test, last_pass, first_fail in sorted(
        regressions, key=lambda r: r[3], reverse=True
    )[:20]:
        display_name = _html_escape(normalize_model(model))
        regression_rows.append(
            f"<tr><td>{display_name}</td><td class='test-name'>{_html_escape(test)}</td>"
            f"<td class='pass'>{last_pass}</td><td class='fail'>{first_fail}</td></tr>"
        )
    regression_rows_str = (
        "\n        ".join(regression_rows)
        if regression_rows
        else "<tr><td colspan='4' class='na'>No regressions detected</td></tr>"
    )

    # Improvements table
    improvement_rows = []
    for model, test, last_fail, first_pass in sorted(
        improvements, key=lambda r: r[3], reverse=True
    )[:20]:
        display_name = _html_escape(normalize_model(model))
        improvement_rows.append(
            f"<tr><td>{display_name}</td><td class='test-name'>{_html_escape(test)}</td>"
            f"<td class='fail'>{last_fail}</td><td class='pass'>{first_pass}</td></tr>"
        )
    improvement_rows_str = (
        "\n        ".join(improvement_rows)
        if improvement_rows
        else "<tr><td colspan='4' class='na'>No improvements detected</td></tr>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gptme Eval Trends</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e;
    --green: #3fb950; --red: #f85149; --accent: #58a6ff;
    --yellow: #d29922;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); padding: 2rem 1rem;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h1 a {{ color: var(--accent); text-decoration: none; }}
  h2 {{ font-size: 1.1rem; margin: 2rem 0 0.75rem; color: var(--muted);
       text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.85rem; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .stats {{ display: flex; gap: 2rem; margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border);
           border-radius: 6px; padding: 0.75rem 1rem; min-width: 120px; }}
  .stat-value {{ font-size: 1.5rem; font-weight: 700; }}
  .stat-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
                 letter-spacing: 0.05em; }}
  table {{
    width: 100%; border-collapse: collapse; background: var(--surface);
    border-radius: 6px; overflow: hidden;
  }}
  th, td {{
    padding: 0.5rem 0.75rem; text-align: left;
    border-bottom: 1px solid var(--border); font-size: 0.85rem;
  }}
  th {{ background: var(--bg); color: var(--muted); font-size: 0.75rem;
       text-transform: uppercase; letter-spacing: 0.05em; }}
  .model-name {{ font-weight: 600; white-space: nowrap; }}
  .rate {{ font-family: monospace; font-weight: 700; }}
  .passed {{ font-family: monospace; color: var(--muted); }}
  .test-name {{ font-family: monospace; font-size: 0.8rem; }}
  .pass {{ color: var(--green); font-weight: 600; }}
  .fail {{ color: var(--red); font-weight: 600; }}
  .na {{ color: var(--muted); text-align: center; font-style: italic; }}
  .trend-up {{ color: var(--green); font-weight: 700; }}
  .trend-down {{ color: var(--red); font-weight: 700; }}
  .trend-stable {{ color: var(--yellow); }}
  .sparkline-cell {{ width: 150px; padding: 0.25rem 0.5rem; }}
  .sparkline {{ width: 100%; height: 24px; }}
  footer {{ color: var(--muted); font-size: 0.75rem; margin-top: 2rem;
            padding-top: 1rem; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<div class="container">
  <h1><a href="https://gptme.org">gptme</a> Eval Trends</h1>
  <p class="meta">Generated {now}</p>

  <div class="stats">
    <div class="stat">
      <div class="stat-value">{len(sorted_models)}</div>
      <div class="stat-label">Models Tracked</div>
    </div>
    <div class="stat">
      <div class="stat-value">{len(regressions)}</div>
      <div class="stat-label">Regressions</div>
    </div>
    <div class="stat">
      <div class="stat-value">{len(improvements)}</div>
      <div class="stat-label">Improvements</div>
    </div>
  </div>

  <h2>Pass Rate Over Time</h2>
  <table>
    <thead>
      <tr><th>Model</th><th>Rate</th><th>Passed</th><th>Trend</th><th>Change</th></tr>
    </thead>
    <tbody>
{trend_rows_str}
    </tbody>
  </table>

  <h2>Recent Regressions (was passing, now failing)</h2>
  <table>
    <thead>
      <tr><th>Model</th><th>Test</th><th>Last Pass</th><th>First Fail</th></tr>
    </thead>
    <tbody>
        {regression_rows_str}
    </tbody>
  </table>

  <h2>Recent Improvements (was failing, now passing)</h2>
  <table>
    <thead>
      <tr><th>Model</th><th>Test</th><th>Last Fail</th><th>First Pass</th></tr>
    </thead>
    <tbody>
        {improvement_rows_str}
    </tbody>
  </table>

  <footer>
    Generated by <code>gptme-eval --leaderboard --leaderboard-format html --trends</code>
  </footer>
</div>
</body>
</html>"""


def format_trends_markdown(trends: dict) -> str:
    """Render trend analysis as Markdown."""
    daily_rates = trends["daily_rates"]
    regressions = trends["regressions"]
    improvements = trends["improvements"]
    overall_trend = trends["overall_trend"]

    lines = ["# Eval Trends", ""]

    # Summary
    sorted_models = sorted(
        daily_rates.keys(),
        key=lambda m: daily_rates[m][-1][1] if daily_rates[m] else 0,
        reverse=True,
    )

    lines.append("## Pass Rate Trends")
    lines.append("")
    lines.append("| Model | Latest | Passed | Trend | Change |")
    lines.append("|-------|--------|--------|-------|--------|")
    for model in sorted_models:
        rates = daily_rates[model]
        display_name = normalize_model(model)
        latest = rates[-1]
        rate_pct = f"{latest[1]:.0%}"
        passed_total = f"{latest[2]}/{latest[3]}"
        trend_info = overall_trend.get(model, ("stable", 0, 0))
        direction = trend_info[0]
        icon = {"improving": "↑", "declining": "↓", "stable": "→"}.get(direction, "→")
        delta = rates[-1][1] - rates[0][1] if len(rates) >= 2 else 0
        delta_str = f"{delta:+.1%}" if len(rates) >= 2 else "-"
        lines.append(
            f"| {display_name} | {rate_pct} | {passed_total} | {icon} | {delta_str} |"
        )

    if regressions:
        lines.extend(["", "## Regressions", ""])
        lines.append("| Model | Test | Last Pass | First Fail |")
        lines.append("|-------|------|-----------|------------|")
        for model, test, last_pass, first_fail in sorted(
            regressions, key=lambda r: r[3], reverse=True
        )[:20]:
            lines.append(
                f"| {normalize_model(model)} | {test} | {last_pass} | {first_fail} |"
            )

    if improvements:
        lines.extend(["", "## Improvements", ""])
        lines.append("| Model | Test | Last Fail | First Pass |")
        lines.append("|-------|------|-----------|------------|")
        for model, test, last_fail, first_pass in sorted(
            improvements, key=lambda r: r[3], reverse=True
        )[:20]:
            lines.append(
                f"| {normalize_model(model)} | {test} | {last_fail} | {first_pass} |"
            )

    return "\n".join(lines)


def generate_leaderboard(
    results_dir: Path,
    output_format: str = "markdown",
    min_tests: int = 4,
) -> str:
    """Generate a leaderboard from eval results.

    Args:
        results_dir: Path to eval_results directory with timestamped subdirs.
        output_format: One of "rst", "csv", "markdown", "json", "html".
        min_tests: Minimum number of tests for a model to appear.

    Returns:
        Formatted leaderboard string.

    Raises:
        FileNotFoundError: If results_dir does not exist or contains no results.
        ValueError: If no models meet the min_tests threshold.
    """
    results = load_results(results_dir)
    if not results:
        raise FileNotFoundError(f"No eval results found in {results_dir}")

    ranked = aggregate_results(results, min_tests=min_tests)
    if not ranked:
        raise ValueError(f"No models with >= {min_tests} tests found.")

    formatters = {
        "rst": format_rst_table,
        "csv": format_csv_table,
        "markdown": format_markdown_table,
        "json": format_json,
        "html": format_html_page,
    }
    formatter = formatters.get(output_format)
    if formatter is None:
        raise ValueError(
            f"Unknown format: {output_format!r}. Valid formats: {list(formatters)}"
        )
    return formatter(ranked)


def main():
    parser = argparse.ArgumentParser(description="Generate eval leaderboard")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("eval_results"),
        help="Directory containing eval results",
    )
    parser.add_argument(
        "--format",
        choices=["rst", "csv", "markdown", "json", "html"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=4,
        help="Minimum number of tests for a model to be included (default: 4)",
    )
    parser.add_argument(
        "--per-test",
        action="store_true",
        help="Show per-test pass/fail breakdown instead of summary",
    )
    parser.add_argument(
        "--trends",
        action="store_true",
        help="Show pass-rate trends over time with regressions/improvements",
    )
    parser.add_argument(
        "--trend-days",
        type=int,
        default=90,
        help="Number of days to include in trend analysis (default: 90)",
    )
    args = parser.parse_args()

    try:
        results = load_results(args.results_dir)
        if not results:
            raise FileNotFoundError(f"No eval results found in {args.results_dir}")

        if args.trends:
            trends = compute_rate_trends(
                results,
                min_tests=args.min_tests,
                window_days=args.trend_days,
            )
            if not trends["daily_rates"]:
                raise ValueError("No trend data available in the specified window.")
            if args.format == "html":
                output = format_trends_html(trends)
            else:
                output = format_trends_markdown(trends)
        elif args.per_test:
            model_names, test_names, matrix = aggregate_per_test(
                results, min_tests=args.min_tests
            )
            if not model_names:
                raise ValueError(f"No models with >= {args.min_tests} tests found.")
            if args.format == "html":
                output = format_per_test_html(model_names, test_names, matrix)
            else:
                output = format_per_test_markdown(model_names, test_names, matrix)
        else:
            output = generate_leaderboard(
                results_dir=args.results_dir,
                output_format=args.format,
                min_tests=args.min_tests,
            )
    except (FileNotFoundError, ValueError) as e:
        # Print a placeholder instead of failing — allows docs builds to succeed
        # even when eval results are not available
        print(f"*No leaderboard data available ({e})*", file=sys.stderr)
        if args.format == "rst":
            print("*No eval results available. Run evals to populate the leaderboard.*")
        elif args.format == "html":
            print("<p><em>No eval results available.</em></p>")
        elif args.format == "json":
            print('{"models": [], "error": "' + str(e).replace('"', '\\"') + '"}')
        else:
            print("*No eval results available.*")
        return
    if args.format == "csv":
        print(output, end="")
    else:
        print(output)


if __name__ == "__main__":
    main()
