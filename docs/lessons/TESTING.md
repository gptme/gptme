# Lesson System Testing

This document covers testing and validation of lessons across different platforms, shells, and environments.

## Testing Pyramid
Lesson testing follows a pyramid approach:

```text
        /\
       /  \      Integration Tests (End-to-End)
      /    \     - Full gptme sessions with lessons
     /------\    - Real-world scenario validation
    /        \
   /          \  Functional Tests (Lesson Behavior)
  /            \ - Keyword matching verification
 /--------------\- Inclusion logic testing
/                \
\                / Unit Tests (Lesson Parsing)
 \              /  - YAML frontmatter validation
  \            /   - Markdown format checking
   \----------/    - Link validation
```

## Unit Testing

### Lesson Format Validation

Test individual lesson files for correct structure:

```python
# Test script: tests/test_lesson_format.py
import yaml
from pathlib import Path

def test_lesson_has_valid_frontmatter():
    """All lessons must have valid YAML frontmatter."""
    lessons_dir = Path("lessons")

    for lesson_file in lessons_dir.rglob("*.md"):
        with open(lesson_file) as f:
            content = f.read()

        # Extract frontmatter
        if not content.startswith("---"):
            assert False, f"{lesson_file}: Missing frontmatter"

        parts = content.split("---", 2)
        frontmatter = parts[1]

        # Parse YAML
        try:
            data = yaml.safe_load(frontmatter)
        except yaml.YAMLError as e:
            assert False, f"{lesson_file}: Invalid YAML: {e}"

        # Validate structure
        assert "match" in data, f"{lesson_file}: Missing 'match' key"
        match = data["match"]

        # Must have keywords or tools
        assert "keywords" in match or "tools" in match, \
            f"{lesson_file}: Must specify keywords or tools"

def test_lesson_has_required_sections():
    """Primary lessons must have required sections."""
    required = ["Rule", "Context", "Detection", "Pattern", "Outcome"]
    lessons_dir = Path("lessons")

    for lesson_file in lessons_dir.rglob("*.md"):
        with open(lesson_file) as f:
            content = f.read()

        for section in required:
            assert f"## {section}" in content, \
                f"{lesson_file}: Missing required section '{section}'"

def test_lesson_size():
    """Primary lessons should be 30-100 lines."""
    lessons_dir = Path("lessons")

    for lesson_file in lessons_dir.rglob("*.md"):
        with open(lesson_file) as f:
            lines = f.readlines()

        line_count = len(lines)
        if line_count > 150:
            print(f"Warning: {lesson_file} has {line_count} lines (consider companion doc)")
```

### Running Unit Tests

```bash
# Install dependencies
pip install pytest pyyaml

# Run unit tests
pytest tests/test_lesson_format.py -v

# Check specific lesson
python -m tests.test_lesson_format lessons/tools/shell-variable-syntax.md
```

## Functional Testing

### Keyword Matching Tests

Verify lessons are included when keywords are present:

```python
# Test script: tests/test_lesson_inclusion.py
from gptme.lessons import get_lessons

def test_keyword_matching():
    """Lessons should be included when keywords match."""
    # Get all lessons
    lessons = get_lessons()

    # Test specific lesson
    shell_lesson = next(l for l in lessons if "shell-variable-syntax" in l.path)

    # Should match when keyword present
    message = "I'm using bash and need to echo $VARIABLE"
    matches = [l for l in lessons if l.matches(message)]

    assert shell_lesson in matches, \
        "shell-variable-syntax should match message with 'bash' keyword"

def test_tool_matching():
    """Lessons should be included when tools are used."""
    lessons = get_lessons()

    # Find tmux lesson
    tmux_lesson = next(l for l in lessons if "tmux-long-running" in l.path)

    # Should match when tool present
    tools_used = ["shell", "tmux"]
    matches = [l for l in lessons if l.matches_tools(tools_used)]

    assert tmux_lesson in matches, \
        "tmux lesson should match when tmux tool is used"

def test_lesson_priority():
    """More specific keywords should take priority."""
    # Test that specific lessons aren't overshadowed by generic ones
    pass  # TODO: Implement priority logic
```

### Running Functional Tests

```bash
# Run inclusion tests
pytest tests/test_lesson_inclusion.py -v

# Test with real gptme session (integration)
gptme --show-hidden "test message with bash keyword" | grep "Including lesson"
```

## Integration Testing

### End-to-End Session Tests

Test lessons in real gptme sessions:

```bash
#!/bin/bash
# Test script: tests/integration/test_lesson_e2e.sh

# Test 1: Lesson included when keyword present
echo "Test 1: Keyword matching"
output=$(gptme -n "I need to use bash variables" --show-hidden 2>&1)
if echo "$output" | grep -q "shell-variable-syntax"; then
    echo "✓ Pass: Lesson included"
else
    echo "✗ Fail: Lesson not included"
    exit 1
fi

# Test 2: Lesson not included when keywords absent
echo "Test 2: No false positives"
output=$(gptme -n "Hello world" --show-hidden 2>&1)
if echo "$output" | grep -q "shell-variable-syntax"; then
    echo "✗ Fail: Lesson incorrectly included"
    exit 1
else
    echo "✓ Pass: No false inclusion"
fi

# Test 3: Multiple lessons can be included
echo "Test 3: Multiple lessons"
output=$(gptme -n "I'm using bash and tmux for long processes" --show-hidden 2>&1)
lesson_count=$(echo "$output" | grep -c "Including lesson")
if [ "$lesson_count" -ge 2 ]; then
    echo "✓ Pass: Multiple lessons included ($lesson_count)"
else
    echo "✗ Fail: Expected multiple lessons, got $lesson_count"
    exit 1
fi

echo "All tests passed!"
```

### Running Integration Tests

```bash
# Run end-to-end tests
bash tests/integration/test_lesson_e2e.sh

# Test with different models
GPTME_MODEL=anthropic/claude-sonnet-3-5-20240620 bash tests/integration/test_lesson_e2e.sh
```

## Cross-Platform Testing

### Platform-Specific Considerations

#### Linux Testing

```bash
# Ubuntu/Debian
docker run -v $(pwd):/workspace -w /workspace ubuntu:24.04 bash -c "
  apt-get update && apt-get install -y python3 python3-pip
  pip3 install gptme
  gptme -n 'test message' --show-hidden
"

# Fedora/RHEL
docker run -v $(pwd):/workspace -w /workspace fedora:latest bash -c "
  dnf install -y python3 python3-pip
  pip3 install gptme
  gptme -n 'test message' --show-hidden
"
```

#### macOS Testing

```bash
# Intel Mac
arch -x86_64 bash tests/integration/test_lesson_e2e.sh

# Apple Silicon
arch -arm64 bash tests/integration/test_lesson_e2e.sh

# Test with macOS default shell (zsh since Catalina)
zsh tests/integration/test_lesson_e2e.sh
```

#### Windows Testing

```powershell
# Windows PowerShell
$env:GPTME_MODEL = "anthropic/claude-sonnet-3-5-20240620"
python -m pytest tests/ -v

# Git Bash
bash tests/integration/test_lesson_e2e.sh

# WSL (Ubuntu)
wsl bash tests/integration/test_lesson_e2e.sh
```

### Shell Compatibility

Test lessons across different shells:

```bash
# Test with bash
bash -c "gptme -n 'test bash' --show-hidden"

# Test with zsh
zsh -c "gptme -n 'test zsh' --show-hidden"

# Test with fish
fish -c "gptme -n 'test fish' --show-hidden"

# Test with sh (POSIX)
sh -c "gptme -n 'test sh' --show-hidden"
```

### Editor Integration Testing

#### Cursor Editor

```bash
# Test .cursorrules conversion
cd /path/to/project/with/cursorrules

# Convert to lesson
python3 /path/to/gptme-contrib/cursorrules/cursorrules_parser.py \
  to-lesson .cursorrules .gptme/lessons/project-rules.md

# Verify lesson works
gptme -n "test project-specific rule" --show-hidden | grep "project-rules"
```

#### VS Code

```bash
# Test with VS Code terminal
code . -n
# In VS Code terminal:
gptme -n "test in vscode" --show-hidden
```

## Performance Testing

### Context Size Monitoring

Ensure lessons don't consume excessive context:

```python
# Test script: tests/test_lesson_performance.py
def test_lesson_context_size():
    """Lessons should not exceed context budget."""
    from gptme.lessons import get_lessons

    lessons = get_lessons()
    max_size = 150  # lines

    oversized = []
    for lesson in lessons:
        with open(lesson.path) as f:
            line_count = len(f.readlines())

        if line_count > max_size:
            oversized.append((lesson.path, line_count))

    if oversized:
        print("Oversized lessons (consider companion docs):")
        for path, size in oversized:
            print(f"  {path}: {size} lines")

def test_total_context_usage():
    """Total lesson context should stay within budget."""
    from gptme.lessons import get_lessons
    from gptme.util import count_tokens

    lessons = get_lessons()

    # Simulate including 5 lessons (typical max)
    sample_lessons = lessons[:5]
    total_content = "\n\n".join(l.content for l in sample_lessons)

    tokens = count_tokens(total_content)
    max_tokens = 10000  # 5% of typical 200k budget

    assert tokens < max_tokens, \
        f"Lessons use {tokens} tokens (max: {max_tokens})"
```

### Inclusion Speed

```python
def test_lesson_matching_speed():
    """Lesson matching should be fast (<100ms)."""
    import time
    from gptme.lessons import get_lessons

    lessons = get_lessons()
    message = "test message with bash and python keywords"

    start = time.time()
    matches = [l for l in lessons if l.matches(message)]
    elapsed = time.time() - start

    assert elapsed < 0.1, \
        f"Matching took {elapsed:.3f}s (should be <0.1s)"
```

## Continuous Integration

### GitHub Actions Workflow

```yaml
# .github/workflows/lessons.yml
name: Lesson Tests

on: [push, pull_request]

jobs:
  test-lessons:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python-version: ['3.10', '3.11', '3.12']

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest pyyaml
          pip install -e .

      - name: Run unit tests
        run: pytest tests/test_lesson_format.py -v

      - name: Run functional tests
        run: pytest tests/test_lesson_inclusion.py -v

      - name: Run integration tests
        run: bash tests/integration/test_lesson_e2e.sh

      - name: Check performance
        run: pytest tests/test_lesson_performance.py -v
```

## Regression Testing

### Tracking Lesson Effectiveness

Monitor lessons to ensure they're preventing issues:

```python
# scripts/analyze_lesson_effectiveness.py
import re
from pathlib import Path
from collections import Counter

def analyze_logs():
    """Check if lessons prevented known issues."""
    logs_dir = Path.home() / ".local/share/gptme/logs"

    # Known patterns lessons should prevent
    patterns = {
        "shell-variable-syntax": r"bash:.*command not found",
        "tmux-long-running": r"Command timed out after 120",
        "directory-structure": r"No such file or directory.*Programming/",
    }

    results = Counter()

    for log_file in logs_dir.rglob("*.log"):
        content = log_file.read_text()

        for lesson, pattern in patterns.items():
            if re.search(pattern, content):
                results[lesson] += 1

    return results

if __name__ == "__main__":
    issues = analyze_logs()

    if issues:
        print("Lessons that didn't prevent issues:")
        for lesson, count in issues.items():
            print(f"  {lesson}: {count} occurrences")
    else:
        print("No lesson failures detected!")
```

## Manual Testing Checklist

Before releasing new lessons:

- [ ] **Format Validation**
  - [ ] Valid YAML frontmatter
  - [ ] All required sections present
  - [ ] Proper markdown formatting
  - [ ] Working links to related resources

- [ ] **Content Quality**
  - [ ] Clear, actionable rule
  - [ ] Observable detection signals
  - [ ] Minimal, correct example
  - [ ] Measurable benefits documented

- [ ] **Keyword Testing**
  - [ ] Keywords match actual usage
  - [ ] Lesson included in relevant contexts
  - [ ] No false positives

- [ ] **Cross-Platform**
  - [ ] Commands work on Linux
  - [ ] Commands work on macOS
  - [ ] Commands work on Windows (if applicable)

- [ ] **Integration**
  - [ ] Works in actual gptme sessions
  - [ ] Doesn't interfere with other lessons
  - [ ] Context size acceptable

## Troubleshooting

### "Lesson Not Being Included"

**Symptoms**: Lesson never appears in conversations despite matching keywords.

**Debug steps**:

```bash
# 1. Check lesson is indexed
gptme --show-hidden "test" 2>&1 | grep "Indexed.*lessons"

# 2. Check keywords
cat lessons/path/to/lesson.md | head -10

# 3. Test keyword matching manually
python3 -c "
from gptme.lessons import get_lessons
lessons = get_lessons()
lesson = next(l for l in lessons if 'lesson-name' in l.path)
print(f'Keywords: {lesson.keywords}')
print(f'Matches test: {lesson.matches(\"your test message\")}')
"

# 4. Check for parsing errors
python3 -c "
import yaml
with open('lessons/path/to/lesson.md') as f:
    content = f.read()
frontmatter = content.split('---', 2)[1]
print(yaml.safe_load(frontmatter))
"
```

### "Lesson Causing Performance Issues"

**Symptoms**: Slow context loading, high token usage.

**Debug steps**:

```bash
# Check lesson size
wc -l lessons/path/to/lesson.md

# Check total context size
gptme '/exit' > /tmp/context.txt
cat /tmp/context.txt | gptme-util tokens count

# Profile lesson inclusion
python3 -m cProfile -s cumtime -c "
from gptme.lessons import get_lessons
lessons = get_lessons()
[l.matches('test message') for l in lessons]
"
```

### "Lesson Conflicts with Others"

**Symptoms**: Contradictory guidance, unexpected behavior.

**Debug steps**:

```bash
# Find lessons with overlapping keywords
python3 -c "
from gptme.lessons import get_lessons
from collections import defaultdict

lessons = get_lessons()
keyword_map = defaultdict(list)

for lesson in lessons:
    for keyword in lesson.keywords:
        keyword_map[keyword].append(lesson.path)

# Find overlaps
for keyword, paths in keyword_map.items():
    if len(paths) > 1:
        print(f'{keyword}: {len(paths)} lessons')
        for path in paths:
            print(f'  - {path}')
"
```

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Documentation](https://docs.docker.com/)
- [Lesson System Architecture](README.md)
