# Context Compression Evaluation Infrastructure

This directory contains infrastructure for validating context compression quality through automated testing.

## Overview

The evaluation system runs test cases from a corpus against gptme with both compressed and original contexts, measuring task completion rates to validate compression doesn't degrade quality.

**Current Status**: Day 3 complete - Infrastructure implemented and tested
**Next Steps**: Days 4-5 - Run baseline validation

## Architecture

### Components

1. **CorpusLoader**: Parses test cases from markdown corpus
2. **TestRunner**: Executes gptme with compression settings
3. **ResultCollector**: Validates outputs against verification criteria
4. **ComparisonReporter**: Generates statistical comparisons

### Test Corpus

Location: `/home/bob/bob/knowledge/technical/designs/compression-validation-test-corpus.md`

Contains 15 diverse test cases extracted from real Bob sessions (Nov 2025):
- Bug fixes (3 tests)
- Investigations (2 tests)
- Analysis tasks (2 tests)
- PR creation (3 tests)
- Implementation (3 tests)
- Design work (2 tests)

Each test case includes:
- Input (task description + context)
- Expected output (success criteria)
- Verification checklist (5-9 items)

## Usage

### List Test Cases

```bash
poetry run python3 tests/compression/evaluation.py --list-tests
```

### Run Baseline Tests (Original Context)

```bash
# Use poetry run to access dev dependencies (tomli/tomli_w)
poetry run python3 tests/compression/evaluation.py --run-baseline
```

Results saved to: `tests/compression/results/baseline_results.json`

### Run Compressed Tests

```bash
# Default ratio (0.15 = 85% compressed)
poetry run python3 tests/compression/evaluation.py --run-compressed

# Custom ratio
poetry run python3 tests/compression/evaluation.py --run-compressed --ratio 0.30
```

Results saved to: `tests/compression/results/compressed_results_RATIO.json`

### Generate Comparison Report

```bash
poetry run python3 tests/compression/evaluation.py --compare --ratio 0.15
```

Report saved to: `tests/compression/results/comparison_report_RATIO.md`

## Test Execution Flow

1. **Configure**: Temporarily modify gptme.toml with compression settings
2. **Execute**: Run gptme in non-interactive mode with test input
3. **Capture**: Collect output from gptme execution
4. **Verify**: Check output against verification checklist
5. **Score**: Calculate completion rate (0.0-1.0)
6. **Restore**: Restore original gptme.toml

## Success Criteria

- **Task Completion**: 95%+ completion rate at 30% compression
- **Quality**: No critical information loss
- **Reliability**: Consistent results across test runs

## Implementation Status

### Day 3 Complete ✅

- [x] Corpus loader with markdown parsing
- [x] Test case data structure
- [x] Test runner with compression configuration
- [x] Result collection and verification
- [x] Comparison reporter framework
- [x] CLI interface
- [x] Documentation

### Days 4-5: Baseline Validation (Planned)

- [ ] Run baseline tests (15 tests, original context)
- [ ] Run compressed tests (ratio=0.15)
- [ ] Generate comparison report
- [ ] Analyze results and identify issues
- [ ] Validate 95%+ completion rate

## Technical Details

### Compression Configuration

The test runner modifies `gptme.toml`:

```toml
[context_compression]
enabled = true  # or false for baseline
target_ratio = 0.15  # 0.15 = 85% compressed
```

### Result Format

```json
{
  "task_id": "test-01-bug-fix-pr29",
  "compression_ratio": 0.15,
  "success": true,
  "completion_rate": 1.0,
  "verified_items": ["item1", "item2", ...],
  "failed_items": [],
  "duration_seconds": 14.2
}
```

### Verification Logic

Each test has a verification checklist. Output is checked against each item:
- Simple substring matching for basic checks
- Can be enhanced with regex or semantic matching
- Completion rate = verified / total items

## Known Limitations

1. **Verification**: Currently uses simple substring matching
   - Enhancement: Add regex patterns, semantic similarity
2. **Timeout**: 5 minute timeout per test
   - Some tests may need more time
3. **Non-interactive**: Assumes gptme completes without user input
   - Tests must be self-contained

## Future Enhancements

- Semantic similarity for verification (sentence embeddings)
- Parallel test execution for speed
- Continuous validation in CI
- Historical tracking of compression quality
- Per-test-type analysis (bug fixes vs research)

## Related

- **Phase 2 Plan**: `/home/bob/bob/knowledge/technical/designs/context-compression-phase2-plan.md`
- **Test Corpus**: `/home/bob/bob/knowledge/technical/designs/compression-validation-test-corpus.md`
- **Task**: `/home/bob/bob/tasks/implement-context-compression.md`
- **Issue**: ErikBjare/bob#149
- **PR**: gptme/gptme#834
