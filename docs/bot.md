GitHub Bot
==========

The gptme GitHub bot lets you run gptme directly from GitHub issues and pull requests. Just comment `@gptme <your prompt>` and the bot will respond or make changes.

## Quick Start

Add this workflow to your repository at `.github/workflows/gptme-bot.yml`:

```yaml
name: gptme-bot

on:
  issue_comment:
    types: [created]

permissions: write-all

jobs:
  run-bot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gptme/gptme/.github/actions/bot@master
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          allowlist: "your-username"
```

Then comment `@gptme <prompt>` on any issue or PR!

## How It Works

The bot operates in two modes:

**Questions** - If you ask a question, the bot replies directly:
```text
@gptme What does this function do?
@gptme Explain the architecture of this project
@gptme How should I approach fixing issue #123?
```

**Changes** - If you request changes, the bot:
1. Checks out the appropriate branch (PR branch or creates new branch)
2. Runs gptme with your prompt
3. Commits any changes made
4. Pushes and creates a PR (if on an issue) or pushes to PR branch (if on a PR)

```text
@gptme Add tests for the utils module
@gptme Fix the typo in README.md
@gptme Implement the feature described in this issue
```

The bot uses an LLM to determine which mode based on your prompt.

## Configuration Options

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `openai_api_key` | OpenAI API key | No* | - |
| `anthropic_api_key` | Anthropic API key | No* | - |
| `model` | Model to use | No | `anthropic/claude-sonnet-4-20250514` |
| `github_token` | GitHub token for API access | Yes | - |
| `allowlist` | Comma-separated usernames allowed to trigger | Yes | `ErikBjare` |

\*At least one API key is required.

### Example with Custom Model

```yaml
- uses: gptme/gptme/.github/actions/bot@master
  with:
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    allowlist: "user1,user2,user3"
    model: "openai/gpt-4o"
```

## Best Practices

### Good Prompts

**For questions:**
- Be specific about what you want explained
- Reference files or functions by name
- Ask about design decisions or alternatives

```text
@gptme What does the `compress_context` function in context.py do?
@gptme Why does this project use SQLite instead of PostgreSQL?
```

**For changes:**
- Be clear about what you want changed
- Reference specific files or locations when possible
- Break complex changes into smaller prompts

```text
@gptme Add a docstring to the compress_context function
@gptme Add type hints to all functions in utils.py
@gptme Create a test file for the new feature in this PR
```

### Prompts to Avoid

- Very complex multi-step changes (break them up)
- Vague requests ("make this better")
- Large refactors spanning many files

## Security Considerations

1. **Allowlist** - Only users on the allowlist can trigger the bot
2. **Permissions** - The bot has `write-all` permissions, so protect your allowlist
3. **API Keys** - Store API keys as repository secrets, never in code
4. **Review Changes** - Always review bot-created PRs before merging

## Troubleshooting

### Bot doesn't respond

1. Check that the user is on the allowlist
2. Verify the workflow is enabled (Actions tab)
3. Check the workflow run logs for errors
4. Ensure API keys are configured as secrets

### Bot creates wrong changes

1. Be more specific in your prompt
2. Reference specific files and line numbers
3. Break complex requests into smaller steps

### Authentication errors

1. Verify `GITHUB_TOKEN` has necessary permissions
2. Check that API keys are valid and not expired
3. Ensure secrets are accessible to the workflow

## Local Testing

You can test the bot locally before deploying:

```bash
# Clone the repository
git clone https://github.com/your-org/your-repo
cd your-repo

# Test with a question
GITHUB_TOKEN=your_token \
GITHUB_REPOSITORY=your-org/your-repo \
ANTHROPIC_API_KEY=your_key \
python scripts/github_bot.py \
  --issue 123 \
  --comment-body "@gptme What is this project?" \
  --dry-run

# Test with changes
GITHUB_TOKEN=your_token \
GITHUB_REPOSITORY=your-org/your-repo \
ANTHROPIC_API_KEY=your_key \
python scripts/github_bot.py \
  --pr 456 \
  --comment-body "@gptme Fix the typo" \
  --workspace . \
  --dry-run
```

## Limitations

- **One-shot execution** - The bot runs once per comment, no multi-turn conversation
- **Timeout** - Commands time out after 2 minutes
- **Context** - The bot has access to the issue/PR context but limited file context
- **Complexity** - Works best for simple, well-defined tasks

## Examples in the Wild

The gptme project itself uses this bot. See examples:
- [Original implementation issue #16](https://github.com/gptme/gptme/issues/16)
- Search for "gptme-bot" in closed PRs to see bot-created changes

## Related

- [Automation](automation.rst) - Other ways to automate gptme
- [Server](server.rst) - Running gptme as a service
- [CLI Reference](cli.rst) - Command-line options
