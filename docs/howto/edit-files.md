# How to Edit Files

gptme edits files with surgical precision using its `patch` and `save` tools.
Use this guide when you want targeted edits — not a full rewrite.

## Edit a specific function

Point gptme at the file and describe the change:

```bash
gptme 'in utils.py, change parse_date to also accept ISO 8601 strings' utils.py
```

gptme will read the relevant section, apply a targeted patch, and show you the diff.

## Edit across multiple files

Pass multiple files and describe what spans them:

```bash
gptme 'rename the `config` parameter to `cfg` everywhere in these files' api.py client.py tests/test_api.py
```

gptme reads all three, makes consistent edits, and summarises the changes.

## Apply a diff you already have

Paste a diff and let gptme apply it:

```bash
git diff main..feature | gptme 'apply this diff to the current branch, resolving any conflicts'
```

## Fix a specific error

Pipe the error message and the file:

```bash
python myapp.py 2>&1 | gptme 'fix the error' myapp.py
```

## Add type annotations to an existing function

```bash
gptme 'add full type annotations to all public functions in this file' mymodule.py
```

## Review the edit before committing

gptme shows diffs by default. After reviewing:

```bash
gptme 'looks good, commit with a descriptive message'
```

Or chain it in one go:

```bash
gptme 'add type annotations to public functions in mymodule.py' - 'run mypy on it and fix any errors' - 'commit'
```

## Tips

- **Be specific about what to keep**: "change X to Y but keep Z" works well.
- **Use the file as context**: always pass the file as an argument; gptme won't assume it knows the current content.
- **Chain prompts** with ` - ` to edit → test → commit in one command.
