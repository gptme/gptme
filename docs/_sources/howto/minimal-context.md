# Run gptme with minimal context

Use this pattern when you want a cheaper, tighter startup prompt for a
specialized task or an isolated automation run.

## Measure first

Start by checking where the prompt tokens actually go for your current setup:

```bash
gptme --show-prompt-stats
```

That prints per-section counts for the startup system prompt, including:

- `prompt_gptme` for the core assistant instructions
- `prompt_tools` for tool documentation
- `prompt_user` for user profile text
- `prompt_workspace` for AGENTS/CLAUDE files and configured prompt files
- `prompt_context_cmd_*` for dynamic `context_cmd` output

For most code tasks, `prompt_tools` is the biggest section by far. That means
the highest-leverage trim is usually tool scoping, not rewriting the base prompt.

## Use the three practical levers

### 1. Restrict tools

Only load the tools the task actually needs:

```bash
gptme --tools shell,read,patch,save "rename the public API and update tests"
```

If you only need a couple of extras on top of defaults, use additive syntax:

```bash
gptme --tools +browser "open the docs site and summarize the API changes"
```

### 2. Use the short system prompt

`--system short` keeps the same broad behavior but drops the longer wording and
tool examples:

```bash
gptme --system short --tools shell,read,patch,save "fix the failing test"
```

### 3. Trim workspace context when it is not helping

Use `--context` to control whether prompt files and `context_cmd` output are
included:

```bash
# Keep static prompt files, skip dynamic context_cmd output
gptme --context files "review this module"

# Keep dynamic context_cmd output, skip prompt files
gptme --context cmd "summarize the current repo status"
```

### 4. Skip workspace context entirely with `--no-workspace`

Use `--no-workspace` to skip all project-specific context (prompt files **and**
`context_cmd` output) in a single flag. Tools and the core assistant prompt are
still included — only the workspace layer is stripped:

```bash
gptme --no-workspace "summarize this file: src/parser.py"
```

This is equivalent to `--context` with no items specified, and it is the right
choice when:

- You are running a specialized one-shot command that should not load project
  instructions or dynamic context at all.
- You want to compare baseline model behavior without workspace prompt influence.
- You are inside a project with a heavy `context_cmd` and only need basic tools.

Combine it with other levers for the minimal possible prompt:

```bash
gptme \
  --no-workspace \
  --system short \
  --tools shell,read,patch,save \
  "apply this patch and run the tests"
```

## Good defaults for specialized sessions

For a focused coding run:

```bash
gptme \
  --system short \
  --tools shell,read,patch,save \
  --context files \
  "update the parser and make the tests pass"
```

For a constrained automation or factory-style cell:

```bash
gptme \
  --agent-profile isolated \
  --system short \
  --tools shell,read,patch,save,complete \
  --non-interactive \
  "apply the requested refactor and finish when tests pass"
```

`--agent-profile isolated` is useful when you want stricter behavior and a
hard tool subset, but remember that profiles currently **add** instructions.
They do not subtract the base `prompt_gptme` or workspace prompt.

## Iterate with stats

Compare the before/after prompt surface instead of guessing:

```bash
gptme --show-prompt-stats
gptme --show-prompt-stats --system short --tools shell,read,patch,save --context files
gptme --show-prompt-stats --no-workspace --system short --tools shell,read,patch,save
```

If `prompt_workspace` or `prompt_context_cmd_project` still dominates, the next
improvement is likely in your workspace prompt files or `gptme.toml`, not in the
core assistant prompt.
