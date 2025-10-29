# Contributing Lessons

This guide explains how to contribute lessons to gptme's lesson system.

## What Makes a Good Lesson?

A good lesson:
- ✅ Addresses a **recurring pattern** (not a one-time fix)
- ✅ Has **clear detection signals** (observable symptoms)
- ✅ Provides a **minimal correct example** (2-10 lines)
- ✅ Is **concise** (30-50 lines for primary lesson)
- ✅ Links to **full context** if detailed (companion doc)

Avoid creating lessons for:
- ❌ One-time fixes that won't recur
- ❌ Subjective preferences without clear benefits
- ❌ Trivial patterns everyone knows
- ❌ Platform-specific hacks

## Lesson Categories

### Tools (`lessons/tools/`)
Constraints, limitations, and correct usage patterns for specific tools.

**Examples**:
- `shell-variable-syntax.md` - Always use `$` prefix
- `tmux-long-running-processes.md` - Use tmux for 120s+ processes
- `browser-interaction.md` - Read-only browser capabilities

**When to create**: Tool has limitations, common misuse patterns, or non-obvious best practices.

### Workflows (`lessons/workflows/`)
Best practices for development workflows and processes.

**Examples**:
- `git-worktree-workflow.md` - PR workflow with worktrees
- `test-builds-before-push.md` - Local testing before CI
- `efficient-task-selection.md` - Time-boxed decisions

**When to create**: Recurring workflow inefficiency or process improvement.

### Patterns (`lessons/patterns/`)
General patterns that span multiple tools or contexts.

**Examples**:
- `persistent-learning.md` - Persist insights in core files
- `parallel-subagent-workflow.md` - Tmux parallelization
- `knowledge-update-runs.md` - Batch information processing

**When to create**: Cross-cutting concern affecting multiple areas.

### Social (`lessons/social/`)
Guidelines for external communication and community interaction.

**Examples**:
- `twitter-best-practices.md` - Tweet format and style
- `github-issue-engagement.md` - Issue/PR etiquette

**When to create**: External interaction pattern with clear best practices.

### Strategic (`lessons/strategic/`)
Decision-making frameworks and strategic thinking patterns.

**Examples**:
- `miq-framework.md` - Most Important Question approach
- `bitter-lesson-principle.md` - Computation > domain knowledge

**When to create**: Strategic decision pattern with broad applicability.

## Lesson Format

### Primary Lesson Structure

```yaml
---
match:
  keywords: [keyword1, keyword2, keyword3]  # 3-5 keywords ideal
  tools: [tool1, tool2]                     # Optional: when tool-specific
---

# Lesson Title

## Rule
One-sentence imperative: what to do or avoid.

## Context
When this applies (trigger condition).

## Detection
Observable signals that indicate you need this rule:
- Symptom 1 (specific, measurable)
- Symptom 2 (error messages, behaviors)
- Symptom 3 (patterns in logs/output)

## Pattern
Show the correct approach with minimal example:
```language
[2-10 lines of code/command showing right way]
[Keep it minimal but complete enough to understand]
```

## Outcome
What happens when you follow this pattern:
- Benefit 1 (concrete, measurable)
- Benefit 2 (time saved, errors prevented)
- Benefit 3 (quality improved)

## Related
- Full context: knowledge/lessons/lesson-name.md (if companion exists)
- Other related lessons
```

### Companion Document Structure (Optional)

Create in `knowledge/lessons/lesson-name.md` when primary lesson needs expansion:

```markdown
# Lesson Name - Implementation Guide

## Overview
Brief summary of what this lesson addresses.

## Rationale
Full explanation of why this matters:
- Historical context
- Impact analysis (with numbers if possible)
- Cost of not following (examples from logs/issues)

## Detailed Examples
Multiple examples showing:
- Anti-pattern (what not to do)
- Recommended pattern (what to do)
- Real-world scenarios
- Edge cases

## Verification Strategies
How to measure if lesson is being followed:
- Metrics to track
- Tools to check adherence
- Success criteria

## Implementation Roadmap (Optional)
How to automate this into tools:
- Phase 1: Detection
- Phase 2: Guidance
- Phase 3: Automation
- Tool integration points

## Origin
When and why this lesson was created:
- Date: YYYY-MM-DD
- Trigger: Issue/session that identified pattern
- Impact: How many times pattern occurred

## Related
- Primary lesson: lessons/category/lesson-name.md
- Related issues/PRs
- Other related lessons
```

## Contribution Process

### 1. Identify Pattern

Before creating a lesson, ensure:
- [ ] Pattern has recurred 3+ times
- [ ] Clear before/after examples exist
- [ ] Observable symptoms can be documented
- [ ] Concrete benefits are measurable

### 2. Create Lesson Branch

```bash
# In gptme repository
cd /path/to/gptme
git worktree add worktree/lesson-name -b lesson-name origin/master
cd worktree/lesson-name
```

### 3. Write Primary Lesson

```bash
# Choose appropriate category
mkdir -p lessons/category/
nvim lessons/category/lesson-name.md

# Use template above
# Keep to 30-50 lines
# Focus on concise guidance
```

### 4. Create Companion (If Needed)

Only create companion doc if:
- Primary lesson would exceed 100 lines
- Detailed examples would help
- Implementation roadmap exists
- Complex rationale needs explanation

### 5. Test Lesson Inclusion

```bash
# Test keyword matching
gptme --show-hidden "message with keyword1 and keyword2"

# Verify in logs
grep "Including lesson" ~/.local/share/gptme/logs/*/conversation.jsonl

# Check context size
cat /tmp/context.txt | gptme-util tokens count
```

### 6. Document in README

Add to appropriate section in `docs/lessons/README.md`:

```markdown
### Category
- **lesson-name.md** - Brief description
```

### 7. Create Pull Request

```bash
git add lessons/category/lesson-name.md
git commit -m "docs(lessons): add lesson-name

- Rule: [one-sentence summary]
- Context: [when it applies]
- Benefits: [key benefits]"

git push origin lesson-name
gh pr create --title "docs(lessons): add lesson-name" \
  --body "Addresses recurring pattern where [description]

**Benefits**:
- [Benefit 1 with numbers if possible]
- [Benefit 2]

**Evidence**: [Link to issues/logs showing pattern]"
```

## Quality Standards

### Keyword Selection

Good keywords:
- ✅ **Specific**: "autonomous", "git worktree", "pytest"
- ✅ **Contextual**: Terms that naturally appear when pattern applies
- ✅ **3-5 per lesson**: Not too broad, not too narrow

Poor keywords:
- ❌ **Too generic**: "code", "file", "test"
- ❌ **Too many**: 10+ keywords (over-matching)
- ❌ **Synonyms**: "task", "work", "job" (pick one)

### Detection Signals

Good signals:
- ✅ **Observable**: Error messages, log patterns, behaviors
- ✅ **Specific**: "cd: No such file or directory"
- ✅ **Measurable**: "Spending 15+ minutes on selection"

Poor signals:
- ❌ **Vague**: "Things aren't working well"
- ❌ **Subjective**: "Code feels messy"
- ❌ **Unmeasurable**: "Sometimes there are issues"

### Code Examples

Good examples:
- ✅ **Minimal**: 2-10 lines showing pattern
- ✅ **Complete**: Can be copied and run
- ✅ **Contrast**: Show both wrong and right way

Poor examples:
- ❌ **Too long**: 50+ lines obscuring pattern
- ❌ **Incomplete**: Missing imports, setup, context
- ❌ **Abstract**: Pseudocode instead of real code

## Testing Requirements

Before submitting a lesson:

1. **Keyword Test**:
   ```bash
   # Verify lesson is included when keywords present
   gptme "message with lesson keyword" --show-hidden | grep "lesson-name"
   ```

2. **Size Test**:
   ```bash
   # Primary lesson should be 30-50 lines (100 max)
   wc -l lessons/category/lesson-name.md
   ```

3. **Format Test**:
   ```bash
   # Validate YAML frontmatter
   python -c "import yaml; yaml.safe_load(open('lessons/category/lesson-name.md').read().split('---')[1])"
   ```

4. **Link Test**:
   ```bash
   # Verify companion doc exists if referenced
   test -f knowledge/lessons/lesson-name.md || echo "Warning: Companion doc missing"
   ```

## Review Criteria

Maintainers will review lessons for:

1. **Pattern Validity**
   - Does this address a real recurring issue?
   - Is there evidence of the pattern (logs, issues)?
   - Will this prevent future occurrences?

2. **Quality**
   - Is the rule clear and actionable?
   - Are detection signals observable?
   - Is the example minimal and correct?
   - Is it concise enough for LLM context?

3. **Completeness**
   - Does it have all required sections?
   - Are benefits concrete and measurable?
   - Are related lessons linked?

4. **Testing**
   - Has the lesson been tested for inclusion?
   - Do keywords match appropriate contexts?
   - Is the context budget impact acceptable?

## Common Issues

### "Lesson Never Included"

**Problem**: Keywords don't match actual usage.

**Solution**:
```bash
# Search your conversations for actual terms used
grep -r "pattern description" ~/.local/share/gptme/logs/

# Update keywords to match real usage
```

### "Lesson Too Verbose"

**Problem**: Primary lesson exceeds 100 lines.

**Solution**:
- Extract detailed content to companion doc
- Keep primary to 30-50 lines
- Link to companion for full context

### "Pattern Too Specific"

**Problem**: Lesson only applies to one situation.

**Solution**:
- Generalize the pattern
- Or document in knowledge base instead
- Lessons are for recurring patterns only

## Getting Help

- **Questions**: Open discussion in [GitHub Discussions](https://github.com/gptme/gptme/discussions)
- **Issues**: Report problems with [GitHub Issues](https://github.com/gptme/gptme/issues)
- **Examples**: See existing lessons in `lessons/` directory

## Resources

- [Lesson System Architecture](../lessons/README.md)
- [Two-File Architecture](EXAMPLES.md#two-file-architecture)
- [Real-World Use Cases](EXAMPLES.md#real-world-use-cases)
- [Testing Guide](EXAMPLES.md#testing-your-lessons)
