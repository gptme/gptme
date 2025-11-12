# Context Compression Test Corpus

This directory contains the test tasks used to validate context compression quality.

## Task Categories

1. **Bug Fixes** (4 tasks): Specific error identification and resolution
2. **Feature Implementation** (4 tasks): New functionality development
3. **Documentation** (3 tasks): Writing and updating docs
4. **Testing** (3 tasks): Test creation and validation
5. **Research/Analysis** (3 tasks): Investigation and design

Total: 17 tasks covering diverse autonomous work types

## Task Format

Each task is defined in YAML with:
```yaml
id: unique-task-id
category: bug-fix | feature | documentation | testing | research
difficulty: easy | medium | hard
description: Brief task description
prompt: Full autonomous prompt
context_files:
  - List of files needed in context
success_criteria:
  - Measurable success conditions
expected_duration: Minutes estimate
tags:
  - Categorization tags
```

## Selection Criteria

Tasks were selected based on:
- Real autonomous run patterns from Nov 2025
- Diverse work types and complexity levels
- Clear, measurable success criteria
- Representative of typical autonomous operations

## Usage

```bash
# List all tasks
ls -1 *.yaml

# Run validation suite
python ../scripts/run_validation.py --corpus .

# Test specific category
python ../scripts/run_validation.py --category bug-fix
```

## Success Metrics

Target: 95%+ completion rate with 30% compression
- Original context baseline: Expected 100%
- Compressed (ratio=0.15): Target 95%+
- Quality threshold: No critical failures
