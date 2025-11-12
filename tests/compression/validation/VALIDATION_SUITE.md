# Context Compression Validation Suite

**Created**: 2025-11-12
**Phase**: Phase 2 Week 1 (Days 1-2 Complete)
**Status**: Test corpus and infrastructure ready
**Next**: Days 3-4 - Build evaluation infrastructure enhancements

## Overview

This validation suite measures task completion quality with context compression by running representative tasks with compressed vs original context.

**Goal**: Validate 95%+ completion rate at 30% compression

## Components

### 1. Test Corpus (17 tasks)

Located in `corpus/`, covers 5 categories:

- **Bug Fixes (4)**: Specific error identification and resolution
  - Calendar HTTPS fix
  - Twitter auth parameter fix
  - Email duplicate replies fix
  - CI timeout adjustment

- **Feature Implementation (4)**: New functionality development
  - Metrics dashboard
  - Input orchestrator service
  - Task queue integration
  - Preservation re-implementation

- **Documentation (3)**: Writing and updating docs
  - Context selector design docs
  - Phase 2 planning doc
  - Compression validation report

- **Testing (3)**: Test creation and validation
  - GEPA test validation
  - Context selector tests
  - Compression unit tests

- **Research/Analysis (3)**: Investigation and design
  - Compression design research
  - Token efficiency analysis
  - Hybrid compression research

Each task has:
- Clear success criteria
- Expected duration
- Representative prompts
- Required context files

### 2. Test Runner Script

Located in `scripts/run_validation.py`:

**Features**:
- Runs tasks with compressed/original context
- Checks success criteria automatically
- Collects detailed results
- Generates comparison reports

**Usage**:
```bash
# Run all tasks
python3 run_validation.py

# Run specific category
python3 run_validation.py --category bug-fix

# Run specific task
python3 run_validation.py --task task-001-calendar-https-fix

# Dry-run (show what would be run)
python3 run_validation.py --dry-run

# Test multiple compression ratios
python3 run_validation.py --compression-ratio None 0.15 0.3 0.5

# Adjust timeout
python3 run_validation.py --timeout 900
```

**Output**:
- Individual result files in `results/`
- Summary report JSON with metrics
- Success rates per configuration
- Criteria completion rates

### 3. Results Storage

Located in `results/`, stores:
- Individual task results (JSON)
- Summary reports (JSON)
- Workspace artifacts per task run

## Phase 2 Week 1 Progress

**Days 1-2: Test Corpus Creation ✅**
- [x] Extract 17 representative tasks
- [x] Cover diverse task types
- [x] Define clear success criteria
- [x] Create structured task definitions

**Days 3-4: Evaluation Infrastructure (Next)**
- [ ] Enhance automated test runner
- [ ] Implement result collector
- [ ] Create comparison reporter
- [ ] Add statistical analysis

**Day 5: Baseline Validation (After Days 3-4)**
- [ ] Run original context baseline
- [ ] Run compressed context tests (ratio=0.15)
- [ ] Analyze completion rates
- [ ] Generate validation report

## Task Selection Criteria

Tasks were selected based on:
1. **Real patterns**: From Nov 2025 autonomous runs
2. **Diversity**: 5 categories, 3 difficulty levels
3. **Measurability**: Clear, objective success criteria
4. **Representativeness**: Cover typical autonomous operations
5. **Executability**: Can run autonomously with `gptme -n`

## Success Metrics

**Target**: 95%+ completion rate at 30% compression

**Measured**:
- Success rate (task completed)
- Criteria completion rate (success criteria met)
- Duration (time to complete)
- Quality (output correctness)

**Comparison**:
- Original context: Expected 100% baseline
- Compressed (ratio=0.15): Target 95%+
- Statistical significance testing

## Implementation Details

**Test Runner Architecture**:
1. Load task definitions from YAML
2. Create temporary workspace per task+config
3. Configure compression via gptme.toml
4. Execute with `gptme -n` (non-interactive)
5. Check success criteria
6. Collect results
7. Generate comparison report

**Success Criteria Checks**:
- File existence (documentation, code files)
- Git commits (commit messages with keywords)
- Test results (pytest pass/fail)
- Keyword detection (output contains expected terms)

**Future Enhancements** (Days 3-4):
- More sophisticated criteria checking
- Context size measurements
- Quality scoring algorithms
- Statistical analysis tools
- Visualization of results

## Next Steps

1. **Complete Days 3-4**: Enhance evaluation infrastructure
   - Improve criteria checking (custom validators)
   - Add context size measurement
   - Implement statistical comparison
   - Create visualization tools

2. **Run Day 5 Validation**: Execute full test suite
   - Baseline with original context
   - Test with compressed context
   - Generate validation report

3. **Analyze Results**: Determine if 95% threshold met
   - If yes: Move to Week 2 improvements
   - If no: Investigate failures, adjust compression

4. **Document Findings**: Update Phase 2 plan based on results

## Files Created

- `corpus/README.md`: Corpus documentation
- `corpus/task-*.yaml`: 17 task definitions
- `scripts/run_validation.py`: Test runner (350 lines)
- `VALIDATION_SUITE.md`: This document

Total: 19 files, ~1,200 lines of infrastructure

## Related

- Task: `/home/bob/bob/tasks/implement-context-compression.md`
- Phase 2 Plan: `/home/bob/bob/knowledge/technical-designs/context-compression-phase2-plan.md`
- PR: gptme/gptme#834
- Issue: ErikBjare/bob#149
