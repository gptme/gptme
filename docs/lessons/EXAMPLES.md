# Lesson System Examples

This document provides real-world examples of lessons in action, demonstrating patterns and best practices.

## Two-File Architecture

Lessons can use a two-file architecture for optimal context efficiency:
- **Primary Lesson** (30-50 lines): Concise runtime guidance included in LLM context
- **Companion Doc** (unlimited): Full implementation details, examples, and background

### Example: Research When Stumbling

**Primary Lesson** (`lessons/workflow/research-when-stumbling.md`):
```yaml
---
match:
  keywords: [research, learning, stuck, api]
---

# Research When Stumbling

## Rule
When struggling with a task, use research tools after 2-3 failed attempts or 15-20 minutes.

## Context
During implementation, learning, or problem-solving when multiple attempts have failed.

## Detection
- Multiple failed attempts without new information
- Trying variations of same approach
- Building from scratch when solutions likely exist

## Pattern
```python
if attempts >= 2 or time_spent >= 15:
    search("how to solve X", "perplexity")
    implement_with_new_knowledge()
```

## Outcome
Rapid unblocking (5 min research > 30 min struggling)

## Related
- Full context: knowledge/lessons/research-when-stumbling.md
```

**Companion Doc** (`knowledge/lessons/research-when-stumbling.md`):
- Detailed rationale (why this matters)
- 5+ use cases with examples
- Verification strategies
- Implementation roadmap
- Best practices and time-boxing

**Benefits**:
- 79% reduction in primary lesson size
- Complete context preserved in companion
- LLM gets concise guidance, humans get full details

## Real-World Use Cases

### Use Case 1: API Learning

**Scenario**: Implementing GitHub API integration, uncertain about rate limits and authentication.

**Without Lesson**:
- 30 minutes trying different approaches
- Multiple failed attempts with errors
- Eventually gives up or wastes more time

**With Lesson**:
- Attempts fail twice (5 minutes)
- Lesson triggers: "research when stumbling"
- Quick Perplexity search (2 minutes)
- Find GitHub API docs and rate limit handling
- Implement correctly (10 minutes)
- **Time saved**: 13 minutes

### Use Case 2: Testing Best Practices

**Scenario**: Adding tests to codebase, unsure of patterns.

**Lesson Applied**: `test-builds-before-push.md`

**Pattern**:
```bash
# Test locally first
make build
pytest tests/

# If passes, then push
git push
```

**Result**:
- No CI spam from failed builds
- Faster feedback (local: seconds vs CI: minutes)
- Professional workflow

### Use Case 3: Git Workflow

**Scenario**: Working on external repository (gptme), need PR workflow.

**Lesson Applied**: `git-worktree-workflow.md`

**Pattern**:
```bash
# Check if worktree exists
git worktree list | grep feature-name

# Create if needed
git worktree add worktree/feature-name -b feature-name origin/master
cd worktree/feature-name

# Do work, PR, cleanup
```

**Result**:
- Parallel work on multiple features
- No duplicate worktrees
- Clean PR workflow

## Lesson Patterns

### Pattern 1: Tool Constraint Lessons

**Purpose**: Document tool limitations and correct usage patterns.

**Example**: `tmux-long-running-processes.md`

```yaml
---
match:
  keywords: [timeout, long-running, benchmark]
  tools: [shell]
---

# Rule
Always use tmux for processes exceeding 120-second shell timeout.

# Context
When running benchmarks, optimization, or long builds.

# Pattern
```bash
tmux new_session 'long-running-command'
tmux inspect_pane session_name
```
```

**When to use**: Tool has limitations (timeouts, memory, scope)

### Pattern 2: Workflow Optimization Lessons

**Purpose**: Prevent inefficient approaches, guide best practices.

**Example**: `efficient-task-selection.md`

```yaml
---
match:
  keywords: [autonomous, task selection, decision]
---

# Rule
Make task selection decisions within 10 minutes.

# Pattern
1. Check work queue (1 tool call)
2. Quick scan (3-5 tool calls)
3. Apply criteria: Not blocked? Concrete progress? Different?
4. Pick first match
```

**When to use**: Recurring inefficiency patterns detected

### Pattern 3: Safety Constraint Lessons

**Purpose**: Prevent dangerous operations in autonomous mode.

**Example**: `safe-operation-patterns.md`

```yaml
---
match:
  keywords: [autonomous, safety, operations]
---

# Rule
Classify operations as GREEN (safe), YELLOW (pattern), RED (human).

# Pattern
- GREEN: Code changes, tests, docs → Execute
- YELLOW: Social media, email → Follow pattern
- RED: Financial ops, major decisions → Escalate
```

**When to use**: Operation could cause harm without guardrails

## Creating Effective Lessons

### Good Lesson Characteristics

1. **Concise Rule**: One-sentence imperative
2. **Clear Context**: When this applies
3. **Observable Detection**: How to know you need it
4. **Minimal Pattern**: 2-10 lines showing correct approach
5. **Outcome**: What happens when you follow it
6. **Related Links**: Companion doc, related lessons

### Common Mistakes

❌ **Too Verbose**: 300-line lessons consuming excessive context
- Solution: Use two-file architecture (30-50 line primary)

❌ **Too Abstract**: "Consider doing X" without concrete example
- Solution: Show minimal correct code/command

❌ **Too Specific**: One-time fix that won't recur
- Solution: Only create lessons for patterns, not one-offs

❌ **Missing Detection**: No way to know when lesson applies
- Solution: List observable signals indicating need

### Lesson Lifecycle

1. **Identify Pattern**: Recurring issue or best practice
2. **Extract Minimal Rule**: One clear directive
3. **Create Primary Lesson**: 30-50 lines with pattern
4. **Create Companion** (optional): Full details, examples, rationale
5. **Test Inclusion**: Verify keywords trigger correctly
6. **Measure Impact**: Track if lesson prevents issues

## Testing Your Lessons

### Manual Testing

```bash
# 1. Create lesson with keywords
echo "---
match:
  keywords: [test, example]
---

# Test Lesson
This is a test lesson." > ~/.config/gptme/lessons/test.md

# 2. Start gptme and use keyword
gptme "test my lesson inclusion"

# 3. Check logs
gptme --show-hidden  # Look for "Including lesson: test"
```

### Automated Testing

```python
# Test lesson parsing
from gptme.lessons import Lesson

lesson = Lesson.from_file("test.md")
assert "test" in lesson.keywords
assert lesson.content.startswith("# Test Lesson")
```

### Integration Testing

```bash
# Test lesson inclusion across conversation
gptme << EOF
This message should not include test lesson
EOF

gptme << EOF
This message mentions test keyword and should include lesson
EOF

# Verify via logs
cat ~/.local/share/gptme/logs/*/conversation.jsonl | grep "test"
```

## Platform-Specific Considerations

### Linux
- Shell tool: bash by default
- Paths: Unix-style (`/home/user/`)
- Commands: Standard GNU tools

### macOS
- Shell tool: zsh by default (Catalina+)
- Paths: Unix-style (`/Users/user/`)
- Commands: BSD variants (differ from GNU)

### Windows
- Shell tool: PowerShell or bash (Git Bash/WSL)
- Paths: Windows-style (`C:\Users\user\`) or Unix (WSL)
- Commands: Windows or Unix depending on shell

### Cross-Platform Patterns

**Path handling**:
```python
# Good: cross-platform
from pathlib import Path
path = Path.home() / "workspace" / "file.txt"

# Bad: platform-specific
path = "/home/user/workspace/file.txt"  # Linux only
```

**Command availability**:
```bash
# Check before use
if command -v pytest &> /dev/null; then
    pytest tests/
fi
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on contributing lessons to gptme.
