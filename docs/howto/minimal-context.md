# Minimal context / token-efficient mode

gptme's default system prompt is comprehensive — designed for general-purpose
use with full tool access, workspace context, and detailed examples. For
specialized single-task sessions (factory cells, evals, CI runs, batch
processing), this full prompt is mostly waste — it loads tools and context the
session never uses, and the per-tool examples inflate the prompt without
improving output quality for capable models.

This guide covers how to assemble a minimal-context configuration for
token-efficient, task-focused sessions.

## Quick reference

| Lever | Effect | Typical saving |
|---|---|---|
| `--tools shell,read,patch` | Only load needed tools | -70-80% |
| `--system short` | Drop per-tool examples | -42% |
| `--non-interactive` (`-n`) | Skip user identity block | negligible base saving (*) |
| `--context files` | Skip `context_cmd` output | workspace-dependent |
| `--agent-profile isolated` | Hard-enforced tool subset + read-only guard | tool-scoping variant |

(*) `-n` does not skip workspace context (AGENTS.md, context files). Its base
prompt saving is small; the real non-interactive win is halting after the task
completes rather than dropping into an interactive loop.

## Measuring first: `--show-prompt-stats`

Before trimming, instrument. Run gptme with `--show-prompt-stats` to see a
per-section token breakdown:

```console
$ gptme -n --show-prompt-stats "count to 3"
=== System prompt token breakdown ===
  Section                     Tokens     Pct
  ------------------------- --------  ------
  Core (identity + tools)       8048   87.5%
  Workspace files                163    1.8%
  Chat history                   986   10.7%
  ------------------------- --------  ------
  Total                         9197
```

The example above (default, 20 tools, full prompt) shows 87.5% of the system
prompt is core identity + tool descriptions. This is the largest lever — the
more tools loaded, the larger the core block.

Now with 4 tools and short mode:

```console
$ gptme -n --show-prompt-stats --system short -t shell,read,patch,save "count to 3"
=== System prompt token breakdown ===
  Section                     Tokens     Pct
  ------------------------- --------  ------
  Core (identity + tools)       1611   58.3%
  Workspace files                163    5.9%
  Chat history                   986   35.7%
  ------------------------- --------  ------
  Total                         2760
```

Total drops from 9197 to 2760 (-70%) by cutting 16 unused tools and switching
to `--system short`.

## Recommended patterns

### Pattern 1: Isolated code cell (factory-style)

For a task that only needs to read files and produce a patch:

```bash
gptme -n --system short \
  --agent-profile isolated \
  -t shell,read,patch \
  "fix the bug in src/parser.py"
```

- `isolated` profile enforces tool scope and adds `read_only` + `no_network`
- `--system short` drops per-tool examples
- Only `shell`, `read`, and `patch` tools loaded

### Pattern 2: Minimal eval run

For benchmarks where every token counts:

```bash
gptme -n --system short \
  --context files \
  -t shell,save \
  "run the benchmark and save results to output.json"
```

- `--context files` skips `context_cmd` (project-specific dynamic context)
- Only `shell` and `save` tools loaded

### Pattern 3: Full workspace, minimal cost

When you need workspace context but want minimal prompt overhead:

```bash
gptme -n --system short \
  -t shell,read,patch,save,gh \
  "review PR #123 and post feedback"
```

- Still includes workspace files (AGENTS.md, README, pyproject.toml)
- But only loads 5 tools instead of 20

## Existing machinery

gptme has three independent levers already — minimal-context mode is about
combining them deliberately, not building new ones:

### Tool scoping via `--tools`

The `--tools` flag accepts an allowlist of tool names:

```bash
gptme -t shell,read,patch,save   # only these 4 tools
gptme -t none                    # no tools at all (chat-only)
```

Tool descriptions dominate the system prompt. Loading only the tools a session
needs is the single highest-impact lever — typically 70-80% reduction from
the full 20-tool default.

### System mode via `--system`

`--system full` (default) includes per-tool examples. `--system short` drops them
while keeping full tool *descriptions* — about 42% reduction with zero behavior
change for capable models.

### Agent profiles via `--agent-profile`

8 built-in profiles in `gptme/profiles.py`:

- `isolated` — read-only + no-network for sandboxed cells
- `verifier` — similar, for factory verification cells
- `developer` — full tool access, code-focused persona
- `explorer` — read-heavy, network-allowed
- `researcher` — broader tool access + network
- `browser-use` — browser tools only
- `computer-use` — desktop automation tools
- `default` — normal operation

Profiles enforce tool subsets and add behavior guards.

## What's not yet trimmable

The base prompt is assembled from four sections: `prompt_gptme` (core identity),
`prompt_user`, `prompt_tools`, and `prompt_workspace` (AGENTS.md, context files).

Profiles only *append* to the base. Two things can't currently be trimmed:

- **Core `prompt_gptme` block** — no "lean core" variant. The base identity
  instructions are shared across all profiles.
- **`prompt_workspace`** — no flag to skip AGENTS.md / workspace context
  injection. `--context files` controls which components are included but
  doesn't offer a "none" option.

A `minimal` profile or `--skip-workspace` flag would close these gaps if
measuring first shows worthwhile savings.

## Claude Code comparison

Claude Code's system prompt is larger in absolute terms but it's a fixed
harness — it doesn't expose per-session tool/context scoping the way gptme
can. gptme's advantage is that the levers already exist and are composable;
the remaining work is making them ergonomic and documented.

## Further reading

- [CLI reference](../cli.rst) — full flag documentation
- [Agent profiles](../agents.rst) — profile documentation
- [#862: minimal context mode tracking issue](https://github.com/ErikBjare/bob/issues/862)
