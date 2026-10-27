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
appimage="${appimages[0]}"
# `tauri build` binds its signatures to the app version (trusted comment
# `version:`), as on the .deb/.rpm. Carry the bundler's binding forward from its
# own signature, so `--config` overrides resolve exactly as they did for it.
version=""
if [[ -s "$appimage.sig" ]]; then
    version="$(base64 -d < "$appimage.sig" | sed -n 's/^trusted comment: .*\tversion:\([^\t]*\)$/\1/p')"
fi
sign_args=()
if [[ -n "$version" ]]; then
    sign_args=(--app-version "$version")
fi
# The bundled signature covers pre-patch bytes. Never let it survive a failed
# signing attempt. The CLI reads the key/password from the action's environment.
rm -f -- "$appimage.sig"
npm run tauri -- signer sign "${sign_args[@]}" "$appimage"
test -s "$appimage.sig"
