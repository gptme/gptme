# How to Debug Python

Use gptme to reproduce an error, trace through it, and apply a fix — without leaving the terminal.

## Fix a failing test

Pass the test output and the relevant files:

```bash
python -m pytest tests/test_api.py -x 2>&1 | gptme 'fix the failing test' src/api.py tests/test_api.py
```

The `-x` flag stops at the first failure so the output is focused.

## Debug a traceback

Pipe the error and the source file:

```bash
python myapp.py 2>&1 | gptme 'explain this traceback and fix the root cause' myapp.py
```

## Reproduce and fix a bug from a description

```bash
gptme 'users report that login fails when the username contains a space; reproduce the bug and fix it' src/auth.py tests/test_auth.py
```

gptme will write a failing test, run it, trace the error, and apply the fix.

## Trace a performance issue

```bash
python -m cProfile -s cumulative myapp.py 2>&1 | gptme 'identify the bottleneck and suggest a fix' myapp.py
```

## Add debug logging and re-run

```bash
gptme 'add temporary debug logging to trace the request path, run the failing test, then remove the logging' src/handler.py
```

## Investigate a flaky test

```bash
gptme 'this test fails intermittently; find the source of flakiness and fix it' tests/test_cache.py src/cache.py
```

Useful pattern: tell gptme to run the test multiple times and look for patterns.

## Fix a type error found by mypy

```bash
mypy src/ 2>&1 | gptme 'fix all type errors' src/
```

## Tips

- **Run the test inside gptme**: `gptme 'run the tests and fix failures'` lets it iterate without you copy-pasting output.
- **Include the full error**: don't truncate the traceback — the line numbers matter.
- **Chain the fix with a test**: end with ` - 'run the tests again and confirm they pass'` to close the loop.
