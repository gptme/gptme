#!/usr/bin/env bash
# Build the gptme-server sidecar binary for bundling with the Tauri app.
# Run from the tauri/ directory (or the repo root via `make tauri-build-sidecar`).
#
# Requires: pyinstaller, uv
# Output: tauri/bins/gptme-server-<triple>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAURI_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$TAURI_DIR")"
BINS_DIR="$TAURI_DIR/bins"

TRIPLE=$(rustc -Vv | grep host | cut -f2 -d' ')
OUT="$BINS_DIR/gptme-server-${TRIPLE}"

if [[ -d "$BINS_DIR" ]]; then
    echo "bins/ already exists, skipping sidecar build (delete bins/ to rebuild)"
    exit 0
fi

echo "Building gptme-server sidecar for $TRIPLE..."
mkdir -p "$BINS_DIR"

# Install gptme from local source into an isolated venv, then freeze with PyInstaller
cd "$REPO_ROOT"
uv pip install --quiet ".[server]"
uv run pyinstaller \
    --onefile \
    --name gptme-server \
    --distpath "$BINS_DIR" \
    gptme/server/__main__.py

# Rename to include target triple (Tauri sidecar convention)
mv "$BINS_DIR/gptme-server" "$OUT"
echo "Sidecar built: $OUT"
