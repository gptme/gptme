# Agent Instructions for gptme

This file provides agent-specific guidance for working on gptme.
For general project information, see [README.md](README.md) and [docs](https://gptme.org/docs/).

## Git Workflow

- **Never push directly to master** - always use branches and PRs
- **Branch naming**: `feat/`, `fix/`, `docs/`, `refactor/` prefixes
- **Commit format**: Use [Conventional Commits](https://www.conventionalcommits.org/)
  - `feat:` for new features (not just docs)
  - `fix:` for bug fixes
  - `docs:` for documentation only
  - `refactor:`, `test:`, `chore:` as appropriate
- **Stage files explicitly**: Never use `git add .` or `git commit -a`
- **Create PRs**: Use `gh pr create` after pushing branch

## Code Style

- **Type hints**: All functions must have type annotations
- **Formatting**: `ruff format` and `ruff check` (run via pre-commit)
- **Type checking**: `mypy` must pass
- **KISS**: Keep it simple - avoid over-engineering
- **Small functions**: Refactor deeply nested code into smaller units
- **Minimal mocking**: Prefer integration tests over heavy mocking

## Testing

Run tests before submitting PRs:
```bash
make test           # Fast tests (excludes slow/eval)
make test SLOW=1    # Include slow tests
make typecheck      # mypy
make lint           # ruff + other checks
```

## Project Structure

Key directories:
- `gptme/` - Core library code
  - `gptme/cli/` - CLI entry points
  - `gptme/tools/` - Tool implementations
  - `gptme/llm/` - LLM provider integrations
  - `gptme/server/` - REST API server
- `tests/` - Test suite
- `docs/` - Sphinx documentation (RST + MD)
- `scripts/` - Build and utility scripts

## Core vs gptme-contrib

We aim to keep gptme core small and focused. See [docs/arewetiny.rst](docs/arewetiny.rst).

**Belongs in core (`gptme`):**
- Essential tools (shell, save, patch, browser, vision)
- Core infrastructure (chat loop, message handling, LLM providers)
- Features needed by most users

**Belongs in [gptme-contrib](https://github.com/gptme/gptme-contrib):**
- Specialized tools (Twitter/X, Discord, email)
- Experimental features
- Integrations with specific services
- Multi-agent patterns (consortium)

When in doubt, start in gptme-contrib. If widely adopted, consider upstreaming.

## Performance

We track startup time and code size. See [docs/arewetiny.rst](docs/arewetiny.rst).

CI benchmarks enforce startup thresholds.

## Key Concepts

- **Tool**: A function the assistant can execute (shell, save, patch, etc.)
- **ToolUse**: Parsed representation of a tool invocation in a message
- **Message**: A single message in the conversation
- **LogManager**: Manages conversation history persistence
- **Step**: One LLM generation + tool execution cycle
- **Turn**: Complete user→assistant exchange (may include multiple steps)

See [docs/glossary.md](docs/glossary.md) for full terminology.

## Memory

gptme uses a cross-harness memory store that is shared between gptme, Claude
Code, and Codex sessions. Memories are Claude Code–compatible Markdown files
in layered roots: project `memory/` when that directory exists, then the
Claude Code per-project directory, then agent/user roots.

### Recall at session start

Run recall once to surface the entries most relevant to the current task:

```bash
gptme-util memory recall "<one-line task description>" -k 5
```

If `gptme-rag` (with the `lexical` extra) is installed the TF-IDF backend is
used automatically; a deterministic token-overlap fallback is used otherwise.
The output line always names the backend that ran (`backend=tfidf` or
`backend=overlap`). Recall searches every layered root.

### Save a memory

When you learn something worth keeping across sessions — a user preference, a
project decision, a recurring pattern — persist it:

```bash
gptme-util memory save <slug> "<one-line description>" --type <type> <<'EOF'
<body — as many lines as useful>
EOF
```

Valid `--type` values: `user`, `feedback`, `project`, `reference`.

Default write root is project `memory/` if that directory exists, else
`~/.claude/projects/<workspace-hash>/memory/`. Existence is the only check —
an unwritable project directory is an error, not a fallback. `recall` (and
the Claude Code hook) search every layered root, so other harnesses see the
entry on their next recall. gptme's session-start workspace prompt and
Claude Code's native `MEMORY.md` auto-load only the CC root; pass
`--scope cc` when that auto-load path is required.

### Inspect and manage

```bash
gptme-util memory roots          # Which roots are active for this workspace
gptme-util memory list           # All living entries across all roots
gptme-util memory show <slug>    # Full text of one entry
gptme-util memory index --write  # Regenerate MEMORY.md in the write root
```

See [docs/memory.rst](docs/memory.rst) or <https://gptme.org/docs/memory.html>
for the full reference.

## Subsystem Guides

- [webui/AGENTS.md](webui/AGENTS.md) - Web UI architecture and gotchas

## Common Tasks

### Adding a new tool
1. Create `gptme/tools/toolname.py`
2. Implement `ToolSpec` with `execute()` function
3. Tools are auto-discovered - no manual registration needed
4. Add tests in `tests/test_tools_toolname.py`

### Running the server
```bash
uv run gptme-server --port 5000
```

### Working on the web UI
See [webui/AGENTS.md](webui/AGENTS.md) for full setup including dev servers, testing, and architecture notes.

### Building docs
```bash
make docs
```
