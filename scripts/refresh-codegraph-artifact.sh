#!/usr/bin/env bash
# Pre-commit hook: regenerate .gptme-codegraph-map.json when Python/TS/JS/Rust sources change.
# Silently skips if gptme-codegraph-commit-map is not installed.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
ARTIFACT="$REPO_ROOT/.gptme-codegraph-map.json"

if ! command -v gptme-codegraph-commit-map &>/dev/null; then
    exit 0
fi

gptme-codegraph-commit-map "$REPO_ROOT" --refresh >/dev/null
git -C "$REPO_ROOT" add "$ARTIFACT"
echo "Refreshed $ARTIFACT"
