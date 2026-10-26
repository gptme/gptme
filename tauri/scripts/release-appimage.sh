#!/usr/bin/env bash
# tauri-action tauriScript: finish byte mutations and signing BEFORE it uploads
# assets and generates latest.json. The action appends `build [args...]` here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [[ "${1:-}" != build ]]; then
    echo "Expected tauri-action build command" >&2
    exit 1
fi
if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
    echo "Linux release requires TAURI_SIGNING_PRIVATE_KEY" >&2
    exit 1
fi

npm run tauri -- "$@"
bash "$SCRIPT_DIR/build-appimage.sh" --patch-only

shopt -s nullglob
appimages=(src-tauri/target/release/bundle/appimage/*.AppImage)
if [[ "${#appimages[@]}" -ne 1 ]]; then
    echo "Expected exactly one final AppImage to sign" >&2
    exit 1
fi
# The bundled signature covers pre-patch bytes. Never let it survive a failed
# signing attempt. The CLI reads the key/password from the action's environment.
rm -f -- "${appimages[0]}.sig"
npm run tauri -- signer sign "${appimages[0]}"
test -s "${appimages[0]}.sig"
