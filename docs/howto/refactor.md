# How to Refactor Code

Use gptme to rename symbols, extract functions, and restructure code across multiple files.

## Rename a function or variable everywhere

```bash
gptme 'rename the function `parse_config` to `load_config` across all Python files in src/' src/
```

gptme will search, edit, and verify the rename is consistent.

## Rename with a different signature

When the rename comes with a signature change:

```bash
gptme 'rename UserRecord to User and change its constructor to take keyword arguments only' \
  src/models.py src/api.py tests/test_models.py
```

## Extract a helper function

```bash
gptme 'the validation logic in process_order() is repeated; extract it into a validate_order() function' src/orders.py
```

## Split a large module

```bash
gptme 'this file is too large; split it into utils.py (pure helpers), models.py (dataclasses), and api.py (HTTP handlers)' src/monolith.py
```

## Change an API's interface

```bash
gptme 'change the Database.query() method to be async; update all callers' \
  src/db.py src/api.py src/worker.py tests/
```

## Remove deprecated code

```bash
gptme 'remove all code marked with the TODO: deprecated comment and update callers accordingly' src/
```

## Add a new parameter to a function with a default

```bash
gptme 'add a timeout parameter (default 30s) to the http_get() function and thread it through all callers' src/http.py src/client.py
```

## Restructure a directory

```bash
gptme 'move all test fixtures from tests/fixtures.py into individual files under tests/fixtures/; update imports'
```

## Check nothing broke after refactoring

Always end a refactor session with a test run:

```bash
gptme 'rename parse_config to load_config across src/' - 'run tests and fix any failures'
```

## Tips

- **Include all relevant files**: pass every file that contains the symbol being changed.
- **Verify with tests**: chain ` - 'run tests'` so gptme can catch regressions immediately.
- **One change at a time**: large refactors are more reliable when split into focused steps (rename first, then signature change).
- **Describe the intent**: "extract the validation logic" is better than just "move this code here"; gptme can find equivalent patterns you didn't enumerate.
