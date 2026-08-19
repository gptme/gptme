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
# PyInstaller on Windows emits gptme-server.exe; Tauri then expects
# gptme-server-<triple>.exe next to the un-suffixed name.
if [[ -f "${OUT}.exe" && ! -f "$OUT" ]]; then
    OUT="${OUT}.exe"
fi

sidecar_is_stale() {
    local sidecar="$1"
    # Rebuild when any gptme Python source is newer than the frozen binary.
    # `find -newer` is available in Git Bash on Windows.
    if [[ -n "$(find "$REPO_ROOT/gptme" -name '*.py' -newer "$sidecar" 2>/dev/null | head -n 1)" ]]; then
        return 0
    fi
    # Also rebuild when packaging/dependency config changes (pyproject.toml,
    # uv.lock) — these affect which packages get frozen into the sidecar even
    # when no .py source file changes.  poetry.lock is excluded: the install
    # path only consults uv.lock, so watching poetry.lock would trigger spurious
    # rebuilds whose result doesn't reflect the poetry.lock change.
    local config_files=("$REPO_ROOT/pyproject.toml")
    for f in "$REPO_ROOT/uv.lock"; do
        [[ -f "$f" ]] && config_files+=("$f")
    done
    for f in "${config_files[@]}"; do
        if [[ -f "$f" && "$f" -nt "$sidecar" ]]; then
            return 0
        fi
    done
    return 1
}

if [[ -f "$OUT" ]]; then
    if sidecar_is_stale "$OUT"; then
        echo "gptme source is newer than $OUT, rebuilding sidecar..."
        rm -f "$OUT"
    else
        echo "Sidecar already exists at $OUT and is up to date, skipping"
        exit 0
    fi
fi

echo "Building gptme-server sidecar for $TRIPLE..."
mkdir -p "$BINS_DIR"

# Install gptme from local source into a venv, then freeze with PyInstaller.
# When uv.lock exists, use uv sync (without --frozen) so the lock is updated
# if pyproject.toml changed since the last `uv lock` run.  --frozen would fail
# when pyproject.toml is newer than uv.lock (e.g. after adding a dep without
# running `uv lock`), breaking the very workflow that staleness detection exists
# to support.  uv sync still installs the exact pinned versions when the lock
# is already current, so the pin-enforcement goal is preserved.
# When uv.lock is absent, fall back to uv pip install (original path).
# pyinstaller is in [tool.poetry.group.dev.dependencies]; uv maps Poetry groups
# to --group NAME, so --group dev selects it.  server extras add Flask etc.
cd "$REPO_ROOT"
if [[ -f "uv.lock" ]]; then
    uv sync --extra server --group dev --quiet
else
    [[ -d ".venv" ]] || uv venv .venv
    uv pip install --quiet ".[server]" pyinstaller
fi
uv run pyinstaller \
    --onefile \
    --name gptme-server \
    --distpath "$BINS_DIR" \
    gptme/server/__main__.py

# Rename to include target triple (Tauri sidecar convention)
if [[ -f "$BINS_DIR/gptme-server.exe" ]]; then
    mv "$BINS_DIR/gptme-server.exe" "${BINS_DIR}/gptme-server-${TRIPLE}.exe"
    echo "Sidecar built: ${BINS_DIR}/gptme-server-${TRIPLE}.exe"
elif [[ -f "$BINS_DIR/gptme-server" ]]; then
    mv "$BINS_DIR/gptme-server" "$BINS_DIR/gptme-server-${TRIPLE}"
    echo "Sidecar built: $BINS_DIR/gptme-server-${TRIPLE}"
else
    echo "ERROR: PyInstaller did not produce gptme-server in $BINS_DIR" >&2
    exit 1
fi
