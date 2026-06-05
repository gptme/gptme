#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBUI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXT_DIR="$SCRIPT_DIR"
OUT_DIR="$EXT_DIR/dist"

# Handle optional --watch flag
WATCH=""
for arg in "$@"; do
  if [ "$arg" = "--watch" ]; then
    WATCH="--watch"
  fi
done

# When watching, build worker/content scripts first (Vite will spin forever in watch mode).
# When building normally, Vite runs first (--emptyOutDir clears dist/panel/ only, safe).
if [ -n "$WATCH" ]; then
  echo "→ Building extension worker + content script (esbuild)..."
  cd "$EXT_DIR"
  npx esbuild background.ts --bundle --outfile="$OUT_DIR/background.js" --platform=browser --format=esm --tsconfig=tsconfig.json
  npx esbuild content/content.ts --bundle --outfile="$OUT_DIR/content/content.js" --platform=browser --format=iife --tsconfig=tsconfig.json --external:chrome
  npx esbuild options/options.ts --bundle --outfile="$OUT_DIR/options/options.js" --platform=browser --format=iife --tsconfig=tsconfig.json
  cp manifest.json "$OUT_DIR/"
  cp options/options.html "$OUT_DIR/options/"
  cp -r "$WEBUI_DIR/public/icons" "$OUT_DIR/icons" 2>/dev/null || mkdir -p "$OUT_DIR/icons"
fi

echo "→ Building webui panel (Vite)..."
cd "$WEBUI_DIR"
VITE_EXTENSION_BUILD=1 npx vite build --outDir "$OUT_DIR/panel" --emptyOutDir $WATCH

if [ -z "$WATCH" ]; then
  echo "→ Building extension worker + content script (esbuild)..."
  cd "$EXT_DIR"
  npx esbuild background.ts --bundle --outfile="$OUT_DIR/background.js" --platform=browser --format=esm --tsconfig=tsconfig.json
  npx esbuild content/content.ts --bundle --outfile="$OUT_DIR/content/content.js" --platform=browser --format=iife --tsconfig=tsconfig.json --external:chrome
  npx esbuild options/options.ts --bundle --outfile="$OUT_DIR/options/options.js" --platform=browser --format=iife --tsconfig=tsconfig.json

  # Copy static assets
  cp manifest.json "$OUT_DIR/"
  cp options/options.html "$OUT_DIR/options/"
  cp -r "$WEBUI_DIR/public/icons" "$OUT_DIR/icons" 2>/dev/null || mkdir -p "$OUT_DIR/icons"

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

  echo "✓ Extension built to $OUT_DIR"
  echo "  Load $OUT_DIR in chrome://extensions (Developer mode → Load unpacked)"
fi
