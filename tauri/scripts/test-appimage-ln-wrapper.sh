#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/appimage-bin/ln"
REAL_LN="$(command -v ln)"

if [[ ! -x "$WRAPPER" ]]; then
    echo "AppImage ln wrapper is missing or not executable: $WRAPPER" >&2
    exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

appimage_lib="$tmpdir/target/release/bundle/appimage/gptme-tauri.AppDir/usr/lib"
outside_lib="$tmpdir/outside/usr/lib"
mkdir -p "$appimage_lib" "$outside_lib" "$tmpdir/first" "$tmpdir/second"
touch "$tmpdir/first/im-test.so" "$tmpdir/second/im-test.so"

REAL_LN="$REAL_LN" "$WRAPPER" -s "$tmpdir/first/im-test.so" "$appimage_lib"
REAL_LN="$REAL_LN" "$WRAPPER" -s "$tmpdir/second/im-test.so" "$appimage_lib"
[[ "$(readlink "$appimage_lib/im-test.so")" == "$tmpdir/second/im-test.so" ]]

REAL_LN="$REAL_LN" "$WRAPPER" -s "$tmpdir/first/im-test.so" "$outside_lib"
if REAL_LN="$REAL_LN" "$WRAPPER" -s "$tmpdir/second/im-test.so" "$outside_lib" 2>/dev/null; then
    echo "wrapper unexpectedly forced a symlink outside an AppImage lib directory" >&2
    exit 1
fi
[[ "$(readlink "$outside_lib/im-test.so")" == "$tmpdir/first/im-test.so" ]]

echo "AppImage ln wrapper tests passed"
