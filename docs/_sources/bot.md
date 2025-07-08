GitHub Bot
==========

One way to run gptme is as a GitHub bot.

The `gptme-bot` composite action is a GitHub Action that runs `gptme` in response to comments on GitHub issues or pull requests using the format `@gptme <prompt>`. It is designed to be used for tasks that gptme can perform with a one-shot prompt, such as answering questions, running commands and committing their results, creating files or making simple changes/additions (like write tests), and (potentially) automating code reviews.

## Usage

To use the `gptme-bot` composite action in your repo, you need to create a GitHub Actions workflow file that triggers the action in response to comments on issues or pull requests.

Here is an example workflow file that triggers the action in response to issue comments:

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
      - name: Checkout
        uses: actions/checkout@v4

      - name: run gptme-bot action
        uses: gptme/gptme/.github/actions/bot@master
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          allowlist: "erikbjare"
```

The `gptme-bot` action will then run the `gptme` command-line tool with the command specified in the comment, and perform actions based on the output of the tool.

If a question was asked, it will simply reply.

If a request was made it will check out the appropriate branch, install dependencies, run `gptme`, then commit and push any changes made. If the issue is a pull request, the bot will push changes directly to the pull request branch. If the issue is not a pull request, the bot will create a new pull request with the changes.

The feature was initially introduced in [#16](https://github.com/gptme/gptme/issues/16).
