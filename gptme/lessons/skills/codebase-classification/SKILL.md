---
name: codebase-classification
description: Classify codebases before modification to choose appropriate development approach
---

# Codebase Classification Skill

Analyze and classify codebases before making changes to ensure appropriate development approach.

## Overview

Before modifying any codebase, classify it to determine whether to:
- **Follow existing patterns** (Disciplined)
- **Gradually improve** while following conventions (Transitional)
- **Propose improvements** to legacy patterns (Legacy)
- **Establish best practices** from scratch (Greenfield)

## Classification Types

### 1. Disciplined Codebase
**Signals:**
- Consistent code style (formatting, naming conventions)
- Comprehensive test coverage (>70%)
- Clear module boundaries and interfaces
- Type hints/annotations throughout
- Up-to-date dependencies
- Active CI/CD pipeline
- Good documentation (README, docstrings)

**Approach:** Follow existing patterns strictly. Don't introduce new conventions.

### 2. Transitional Codebase
**Signals:**
- Mixed code quality (some areas good, others not)
- Partial test coverage (30-70%)
- Some type hints, inconsistent usage
- Active development with modernization efforts
- Dependencies somewhat current

**Approach:** Follow existing conventions in touched areas. Propose improvements for new code.

### 3. Legacy Codebase
**Signals:**
- Inconsistent patterns across the codebase
- Minimal or no tests (<30% coverage)
- No type hints
- Outdated dependencies
- Complex, undocumented logic
- Possibly unmaintained

**Approach:** Be careful with changes. Add tests before modifying. Propose gradual improvements.

### 4. Greenfield Codebase
**Signals:**
- New project (<6 months old)
- Few files (<20 source files)
- No established patterns yet
- Minimal or no tests (but not legacy)
- Active initial development

**Approach:** Establish best practices from the start. Set up proper structure, testing, CI.

## Quick Classification Checklist

Run this analysis before making significant changes:

```txt
1. Check test coverage: Is there a test/ or tests/ directory? How comprehensive?
2. Check type hints: Are functions annotated? Is there py.typed or mypy config?
3. Check CI/CD: Is there .github/workflows/, .gitlab-ci.yml, or similar?
4. Check code style: Is there .pre-commit-config.yaml, ruff.toml, or similar?
5. Check dependencies: When was requirements.txt/pyproject.toml last updated?
6. Check documentation: Is there a comprehensive README? API docs?
```

## Decision Matrix

| Signal | Disciplined | Transitional | Legacy | Greenfield |
|--------|-------------|--------------|--------|------------|
| Test coverage | >70% | 30-70% | <30% | Varies (new) |
| Type hints | Comprehensive | Partial | None/minimal | Varies |
| CI/CD | Active | Present | None/broken | May be new |
| Code style | Consistent | Mixed | Inconsistent | Establishing |
| Dependencies | Current | Somewhat current | Outdated | Latest |
| Age | Any | Any | Usually old | <6 months |

## Behavior Guidelines

### When Disciplined
- Study existing patterns before writing new code
- Match naming conventions exactly
- Follow established module structure
- Add tests matching existing test style
- Don't propose architectural changes without strong justification

### When Transitional
- Follow patterns in the specific area you're modifying
- Match quality of surrounding code or slightly better
- Add tests for new functionality
- Document rationale for any pattern deviations

### When Legacy
- Add tests BEFORE modifying code
- Make minimal changes to achieve goal
- Document assumptions and findings
- Propose improvements as separate follow-up work
- Be extra careful with untested code paths

### When Greenfield
- Establish best practices immediately
- Set up proper project structure
- Configure linting, formatting, type checking
- Write tests for new functionality
- Create comprehensive documentation

## Examples

### Identifying Disciplined Codebase
```txt
$ ls -la
pyproject.toml          # Modern packaging
.pre-commit-config.yaml # Style enforcement
mypy.ini                # Type checking
.github/workflows/      # CI/CD

$ wc -l tests/**/*.py
2500 total              # Substantial tests

→ Classification: DISCIPLINED
→ Approach: Follow existing patterns strictly
```

### Identifying Legacy Codebase
```txt
$ ls -la
setup.py                # Old-style packaging
requirements.txt        # Pinned 3 years ago
# No tests directory
# No CI configuration

$ grep -r "def " src/ | head -5
def process_data(x):    # No type hints
def handle_input(data): # No docstrings

→ Classification: LEGACY
→ Approach: Careful changes, add tests first
```

## Integration

This skill helps agents:
1. Avoid imposing new patterns on well-structured codebases
2. Avoid perpetuating bad patterns in legacy codebases
3. Make appropriate improvement suggestions
4. Set up proper structure for new projects

## Related

- Tool: shell (for running analysis commands)
- Tool: read (for examining codebase structure)
