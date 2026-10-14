#!/usr/bin/env bash
# Check that uv.lock is in sync with pyproject.toml
# This catches cases where dependencies were modified but lock file wasn't updated

set -e

if ! uv lock --check 2>&1; then
    echo ""
    echo "Error: uv.lock is out of sync with pyproject.toml"
    echo "To fix: uv lock"
    echo "Then stage the changes: git add uv.lock"
    exit 1
fi

echo "✓ uv.lock is in sync with pyproject.toml"
exit 0
