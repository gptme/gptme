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
    # when no .py source file changes.  poetry.lock is intentionally excluded:
    # the install path only consults uv.lock (via uv sync --frozen), so watching
    # poetry.lock would trigger spurious rebuilds with an identical result.
    local config_files=("$REPO_ROOT/pyproject.toml")
    if [[ -f "$REPO_ROOT/uv.lock" ]]; then
        config_files+=("$REPO_ROOT/uv.lock")
    fi
    for f in "${config_files[@]}"; do
        if [[ -f "$f" && "$f" -nt "$sidecar" ]]; then
            return 0
        fi
    done
    # Detect install-path changes caused by uv.lock appearing or disappearing.
    # If uv.lock was present at last build but has since been deleted (or vice
    # versa), the install path changes (uv sync vs uv pip install), so the
    # sidecar would be built with different dependency resolution.  A marker
    # file records uv.lock presence at build time; any mismatch forces rebuild.
    local uvlock_marker="${sidecar}.uvlock-present"
    local uvlock_now=0
    if [[ -f "$REPO_ROOT/uv.lock" ]]; then uvlock_now=1; fi
    local uvlock_was=0
    if [[ -f "$uvlock_marker" ]]; then
        if [[ ! -r "$uvlock_marker" ]]; then
            return 0
        fi
        uvlock_was=$(<"$uvlock_marker")
        if [[ "$uvlock_was" != 0 && "$uvlock_was" != 1 ]]; then
            return 0
        fi
    fi
    if [[ "$uvlock_now" != "$uvlock_was" ]]; then
        return 0
    fi
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
# When uv.lock exists, use --frozen so the lock actually enforces the pinned
# versions without modifying the lock file. If the lock cannot satisfy the
# current pyproject.toml, stop and ask the developer to update it explicitly.
# When uv.lock is absent (e.g. fresh checkout — it is gitignored), fall back
# to uv pip install.  This path is NOT lock-pinned; prefer generating a uv.lock
# (`uv lock`) or committing it for reproducible sidecar builds.
# pyinstaller is in [tool.poetry.group.dev.dependencies]; uv maps Poetry groups
# to --group NAME, so --group dev selects it.  server extras add Flask etc.
cd "$REPO_ROOT"
if [[ -f "uv.lock" ]]; then
    if ! uv sync --frozen --extra server --group dev --quiet; then
        echo "ERROR: uv.lock cannot satisfy pyproject.toml." \
             "Run 'uv lock' to update it, then rebuild the sidecar." >&2
        exit 1
    fi
else
    if [[ -f "poetry.lock" ]]; then
        echo "WARNING: No uv.lock found (it is gitignored); install will not" \
             "enforce pinned versions from poetry.lock." \
             "Run 'uv lock' to generate a uv.lock for reproducible builds." >&2
    fi
    [[ -d ".venv" ]] || uv venv .venv
    uv pip install --quiet ".[server]" pyinstaller
fi
uv run pyinstaller \
    --onefile \
    --name gptme-server \
    --distpath "$BINS_DIR" \
    gptme/server/__main__.py

# Rename to include target triple (Tauri sidecar convention)
_uvlock_state=0; if [[ -f "$REPO_ROOT/uv.lock" ]]; then _uvlock_state=1; fi
if [[ -f "$BINS_DIR/gptme-server.exe" ]]; then
    mv "$BINS_DIR/gptme-server.exe" "${BINS_DIR}/gptme-server-${TRIPLE}.exe"
    echo "Sidecar built: ${BINS_DIR}/gptme-server-${TRIPLE}.exe"
    echo "$_uvlock_state" > "${BINS_DIR}/gptme-server-${TRIPLE}.exe.uvlock-present"
elif [[ -f "$BINS_DIR/gptme-server" ]]; then
    mv "$BINS_DIR/gptme-server" "$BINS_DIR/gptme-server-${TRIPLE}"
    echo "Sidecar built: $BINS_DIR/gptme-server-${TRIPLE}"
    echo "$_uvlock_state" > "$BINS_DIR/gptme-server-${TRIPLE}.uvlock-present"
else
    echo "ERROR: PyInstaller did not produce gptme-server in $BINS_DIR" >&2
    exit 1
fi
