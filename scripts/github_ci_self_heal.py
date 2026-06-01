#!/usr/bin/env python3
"""
CI self-heal script: analyze test failures and propose fixes using Claude.

Called by .github/workflows/self-heal.yml when CI tests fail on a PR.

Usage:
    github_ci_self_heal.py <failure_log> <pr_diff>

Output (stdout): markdown analysis suitable for posting as a PR comment.
"""

import sys
from pathlib import Path


def analyze_failure(failure_log: str, pr_diff: str) -> str:
    """Call Claude to analyze CI failure and propose a minimal fix."""
    import anthropic

    client = anthropic.Anthropic()

    # Trim inputs to stay within reasonable token limits
    failure_log = failure_log[-12000:] if len(failure_log) > 12000 else failure_log
    pr_diff = pr_diff[:8000] if len(pr_diff) > 8000 else pr_diff

    prompt = f"""A CI test failure occurred on a pull request. Analyze the root cause and propose a minimal fix.

## Test failure output (last portion):
```
{failure_log}
```

## Pull request diff:
```diff
{pr_diff}
```

Respond in this exact format:

**Root cause**: (1-2 sentences identifying exactly what broke and why)

**Proposed fix**: (minimal code change as a unified diff, or a clear explanation if the fix is not straightforward)

**Confidence**: high / medium / low — and why"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def main() -> int:
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <failure_log_path> <pr_diff_path>", file=sys.stderr
        )
        return 1

    failure_log_path = Path(sys.argv[1])
    pr_diff_path = Path(sys.argv[2])

    failure_log = (
        failure_log_path.read_text()
        if failure_log_path.exists()
        else "(no failure log available)"
    )
    pr_diff = (
        pr_diff_path.read_text() if pr_diff_path.exists() else "(no diff available)"
    )

    if not failure_log_path.exists():
        print(f"Warning: failure log not found at {failure_log_path}", file=sys.stderr)
    if not pr_diff_path.exists():
        print(f"Warning: PR diff not found at {pr_diff_path}", file=sys.stderr)

    analysis = analyze_failure(failure_log, pr_diff)
    print(analysis)
    return 0


if __name__ == "__main__":
    sys.exit(main())
