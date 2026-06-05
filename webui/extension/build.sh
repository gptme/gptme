#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBUI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXT_DIR="$SCRIPT_DIR"
OUT_DIR="$EXT_DIR/dist"

# Handle optional --watch flag
WATCH=0
for arg in "$@"; do
  case "$arg" in
    --watch)
      WATCH=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

WATCH_PIDS=()

cleanup_watchers() {
  if [ "${#WATCH_PIDS[@]}" -gt 0 ]; then
    for pid in "${WATCH_PIDS[@]}"; do
      pkill -TERM -P "$pid" 2>/dev/null || true
    done
    kill "${WATCH_PIDS[@]}" 2>/dev/null || true
    wait "${WATCH_PIDS[@]}" 2>/dev/null || true
  fi
}

copy_static_assets() {
  mkdir -p "$OUT_DIR/options" "$OUT_DIR/icons"
  cp "$EXT_DIR/manifest.json" "$OUT_DIR/"
  cp "$EXT_DIR/options/options.html" "$OUT_DIR/options/"
  if [ -d "$WEBUI_DIR/public/icons" ]; then
    cp -R "$WEBUI_DIR/public/icons/." "$OUT_DIR/icons/"
  fi

  # Generate placeholder icons if none exist (Chrome rejects extensions with missing icon paths)
  for size in 16 48 128; do
    if [ ! -f "$OUT_DIR/icons/icon${size}.png" ]; then
      echo "⚠ No icon${size}.png — creating ${size}×${size} placeholder (replace with real icons)"
      python3 - "$size" "$OUT_DIR/icons/icon${size}.png" <<'PYEOF'
import struct, zlib, sys

def make_png(w, h, color=(100, 100, 200)):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    row = b'\x00' + bytes(color) * w
    idat = zlib.compress(row * h)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')

size = int(sys.argv[1])
with open(sys.argv[2], 'wb') as f:
    f.write(make_png(size, size))
PYEOF
    fi
  done
}

build_esbuild_once() {
  echo "→ Building extension worker + content script (esbuild)..."
  cd "$EXT_DIR"
  npx esbuild background.ts --bundle --outfile="$OUT_DIR/background.js" --platform=browser --format=esm --tsconfig=tsconfig.json
  npx esbuild content/content.ts --bundle --outfile="$OUT_DIR/content/content.js" --platform=browser --format=iife --tsconfig=tsconfig.json --external:chrome
  npx esbuild options/options.ts --bundle --outfile="$OUT_DIR/options/options.js" --platform=browser --format=iife --tsconfig=tsconfig.json
}

start_esbuild_watchers() {
  echo "→ Watching extension worker + content script (esbuild)..."
  cd "$EXT_DIR"
  npx esbuild background.ts --bundle --outfile="$OUT_DIR/background.js" --platform=browser --format=esm --tsconfig=tsconfig.json --watch=forever &
  WATCH_PIDS+=("$!")
  npx esbuild content/content.ts --bundle --outfile="$OUT_DIR/content/content.js" --platform=browser --format=iife --tsconfig=tsconfig.json --external:chrome --watch=forever &
  WATCH_PIDS+=("$!")
  npx esbuild options/options.ts --bundle --outfile="$OUT_DIR/options/options.js" --platform=browser --format=iife --tsconfig=tsconfig.json --watch=forever &
  WATCH_PIDS+=("$!")
}

echo "→ Building webui panel (Vite)..."
cd "$WEBUI_DIR"
if [ "$WATCH" -eq 1 ]; then
  copy_static_assets
  start_esbuild_watchers
  trap cleanup_watchers EXIT
  trap 'cleanup_watchers; exit 130' INT TERM
  cd "$WEBUI_DIR"
  VITE_EXTENSION_BUILD=1 npx vite build --outDir "$OUT_DIR/panel" --emptyOutDir --watch
else
  VITE_EXTENSION_BUILD=1 npx vite build --outDir "$OUT_DIR/panel" --emptyOutDir
  build_esbuild_once
  copy_static_assets

  echo "✓ Extension built to $OUT_DIR"
  echo "  Load $OUT_DIR in chrome://extensions (Developer mode → Load unpacked)"
fi
