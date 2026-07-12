#!/usr/bin/env bash
# Build the Linux AppImage and patch linuxdeploy's incomplete libgcrypt bundle.
#
# Usage:
#   ./build-appimage.sh [tauri build args...]   -- full build + patch
#   ./build-appimage.sh --patch-only            -- patch an already-built AppImage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAURI_DIR="$(dirname "$SCRIPT_DIR")"
ARCH="$(uname -m)"
APPIMAGE_PLUGIN_URL="https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/continuous/linuxdeploy-plugin-appimage-${ARCH}.AppImage"

cd "$TAURI_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "AppImage builds are Linux-only" >&2
    exit 1
fi

PATCH_ONLY=false
if [[ "${1:-}" == "--patch-only" ]]; then
    PATCH_ONLY=true
    shift
fi

if [[ "$PATCH_ONLY" != "true" ]]; then
    NO_STRIP="${NO_STRIP:-true}" npm run tauri -- build "$@"
fi

APPDIR="$TAURI_DIR/src-tauri/target/release/bundle/appimage/gptme-tauri.AppDir"
APPIMAGE="$(find "$TAURI_DIR/src-tauri/target/release/bundle/appimage" -maxdepth 1 -name '*.AppImage' -print -quit)"

if [[ ! -d "$APPDIR" || -z "$APPIMAGE" ]]; then
    if [[ "$PATCH_ONLY" == "true" ]]; then
        # AppDir left by linuxdeploy must still be present for us to verify and
        # patch the bundle. Exit non-zero so callers don't re-upload an AppImage
        # that may be missing libgpg-error.so.0.
        echo "AppDir not found — cannot verify AppImage is patched" >&2
        exit 1
    fi
    echo "AppImage build did not produce expected AppDir/AppImage" >&2
    exit 1
fi

if [[ -f "$APPDIR/usr/lib/libgcrypt.so.20" && ! -e "$APPDIR/usr/lib/libgpg-error.so.0" ]]; then
    if command -v ldconfig >/dev/null 2>&1; then
        libgpg_error="$(ldconfig -p | awk '/libgpg-error.so.0 / { print $NF; exit }')"
    else
        libgpg_error="$(find /lib /usr/lib -name 'libgpg-error.so.0' -print -quit 2>/dev/null)"
    fi
    if [[ -z "$libgpg_error" || ! -f "$libgpg_error" ]]; then
        echo "libgcrypt was bundled but matching libgpg-error.so.0 was not found on this system" >&2
        exit 1
    fi

    echo "Bundling $(basename "$libgpg_error") to satisfy libgcrypt runtime dependency"
    cp -L "$libgpg_error" "$APPDIR/usr/lib/$(basename "$(readlink -f "$libgpg_error")")"
    ln -sf "$(basename "$(readlink -f "$libgpg_error")")" "$APPDIR/usr/lib/libgpg-error.so.0"

    plugin="${XDG_CACHE_HOME:-$HOME/.cache}/tauri/linuxdeploy-plugin-appimage-${ARCH}.AppImage"
    if [[ ! -x "$plugin" ]]; then
        mkdir -p "$(dirname "$plugin")"
        curl -L --fail -o "$plugin" "$APPIMAGE_PLUGIN_URL"
        chmod +x "$plugin"
    fi

    APPIMAGE_EXTRACT_AND_RUN=1 LDAI_OUTPUT="$APPIMAGE" "$plugin" --appdir="$APPDIR"
fi

echo "AppImage ready: $APPIMAGE"
