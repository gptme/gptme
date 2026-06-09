# How to Automate Tasks

Use gptme to turn a multi-step procedure into a reusable script — or to run one-off automation
without writing the script yourself.

## Automate a git workflow

Tell gptme the full workflow and let it script it:

```bash
gptme 'write a shell script that: (1) fetches origin, (2) rebases master, (3) runs tests, (4) pushes if tests pass'
```

Or run it interactively without a script:

```bash
gptme 'fetch origin, rebase master, run tests, and push if everything is green'
```

## Batch process files

```bash
gptme 'for every .md file in docs/, check if the frontmatter has a "date" field; print a list of files that are missing it'
```

Or make changes:

```bash
gptme 'add "last_updated: $(date +%Y-%m-%d)" to the frontmatter of every .md file in docs/ that is missing it'
```

## Automate a release checklist

```bash
gptme 'run the release checklist: (1) bump version in pyproject.toml to 1.2.3, (2) update CHANGELOG.md, (3) commit, (4) tag v1.2.3, (5) push tag'
```

## Clean up a directory

```bash
gptme 'find all .pyc files and __pycache__ directories in this project and delete them'
```

## Generate a report from logs

```bash
cat logs/app.log | gptme 'summarize: how many errors per hour today? output as a markdown table'
```

## Write a cron-style automation script

```bash
gptme 'write a Python script I can run daily that: checks disk usage on /, emails me if it exceeds 80%, and logs the result to /var/log/disk-check.log'
```

## Set up a project scaffold

```bash
gptme 'scaffold a new Python package called "mylib" with: src layout, pyproject.toml, ruff config, GitHub Actions CI, and a basic README'
```

## Tips

- **Describe the full procedure**: gptme handles multi-step workflows — spell out each step.
- **Ask for idempotency**: "make this script safe to run multiple times" prevents accidental duplicates.
- **Test before scheduling**: add ` - 'dry-run this and show what would change'` before committing to a scheduled task.
- **Pipe real data**: `cat real-log.txt | gptme 'summarize'` gives much better results than a made-up example.
