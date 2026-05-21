#!/usr/bin/env bash

# Compatibility wrapper around the canonical GitHub PR formatter in
# gptme.util.gh.get_github_pr_content().
#
# Prefer `gh pr view <ref>` inside gptme. Keep this script around for terminal
# workflows that still want the same formatted output directly from a checkout.
#
# Example usage:
#   ./scripts/gh-pr-view-with-pr-comments.sh https://github.com/owner/repo/pull/123
#   ./scripts/gh-pr-view-with-pr-comments.sh owner/repo/pull/123

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <github-pr-url-or-owner/repo/pull/123>" >&2
    exit 1
fi

url=$1
if [[ $url != https://github.com/* ]]; then
    url="https://github.com/$url"
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" python3 - "$url" <<'PY'
import sys

try:
    from gptme.util.gh import get_github_pr_content
except ImportError as exc:
    print(
        "Error: Could not import gptme. Run this from a gptme checkout with dependencies installed "
        "(for example `uv sync`) or use `gh pr view <ref>` directly.",
        file=sys.stderr,
    )
    print(f"Import error: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

url = sys.argv[1]
content = get_github_pr_content(url)
if not content:
    print(
        "Error: Failed to fetch PR content. Make sure the URL is valid and `gh` is installed and authenticated.",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(content, end="" if content.endswith("\n") else "\n")
PY
