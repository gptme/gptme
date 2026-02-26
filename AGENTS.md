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

## Performance

We track startup time and code size. See [docs/arewetiny.rst](docs/arewetiny.rst).

CI benchmarks enforce thresholds:
- Cold startup: <5s
- Warm startup: <2s

## Key Concepts

- **Tool**: A function the assistant can execute (shell, save, patch, etc.)
- **ToolUse**: Parsed representation of a tool invocation in a message
- **Message**: A single message in the conversation
- **LogManager**: Manages conversation history persistence
- **Step**: One LLM generation + tool execution cycle
- **Turn**: Complete user→assistant exchange (may include multiple steps)

See [docs/glossary.md](docs/glossary.md) for full terminology.

## Common Tasks

### Adding a new tool
1. Create `gptme/tools/toolname.py`
2. Implement `ToolSpec` with `execute()` function
3. Add tests in `tests/test_tools_toolname.py`

### Running the server
```bash
poetry run gptme-server --port 5000
```

### Building docs
```bash
make docs
```
