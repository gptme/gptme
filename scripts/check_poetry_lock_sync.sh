#!/usr/bin/env bash
# Check that poetry.lock is in sync with pyproject.toml
# This catches cases where dependencies were modified but lock file wasn't updated

set -e

# Skip if poetry.lock doesn't exist
if [ ! -f poetry.lock ]; then
    echo "Warning: poetry.lock not found, skipping sync check"
    exit 0
fi

# Check if poetry.lock is in sync with pyproject.toml
if ! poetry check --lock 2>&1; then
    echo ""
    echo "Error: poetry.lock is out of sync with pyproject.toml"
    echo "To fix: poetry lock --no-update"
    echo "Then stage the changes: git add poetry.lock"
    exit 1
fi

echo "✓ poetry.lock is in sync with pyproject.toml"
exit 0
