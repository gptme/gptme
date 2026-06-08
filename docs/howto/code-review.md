# How to Review Code

Use gptme to review a diff, a PR, or a file — with enough context to give meaningful feedback.

## Review your uncommitted changes

```bash
git diff | gptme 'review this diff for bugs, missing error handling, and style issues'
```

Add context by including related files:

```bash
git diff | gptme 'review this diff' src/api.py src/models.py
```

## Review a specific commit

```bash
git show HEAD | gptme 'review this commit'
```

## Review a GitHub PR

Pass the PR URL directly:

```bash
gptme 'review this PR for correctness and suggest improvements' https://github.com/owner/repo/pull/42
```

Or use the `gh` integration:

```bash
gptme 'review owner/repo/pull/42'
```

## Review a single file for issues

```bash
gptme 'review this file for bugs, security issues, and opportunities to simplify' src/auth.py
```

## Review with a checklist

Give gptme a specific checklist so the review is consistent:

```bash
gptme 'review this file and check: (1) no SQL injection, (2) all inputs validated, (3) errors logged, (4) no hardcoded secrets' src/db.py
```

## Ask follow-up questions

gptme maintains conversation context, so you can drill in:

```bash
git diff | gptme 'review this diff' - 'which of those issues is highest severity?' - 'show me a fix for that one'
```

## Write the review as a GitHub comment

```bash
git diff main..feature | gptme 'write a concise PR review comment in Markdown'
```

Then copy-paste it, or wire up `gh pr comment`.

## Tips

- **Include related files**: reviews are more accurate when gptme can see the context a change lives in.
- **Specify a focus**: "security review", "performance review", "API compatibility" all produce sharper output than a generic "review this".
- **Chain to a fix**: after a review, add ` - 'fix the most critical issue'` to act immediately.
