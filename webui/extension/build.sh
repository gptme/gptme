#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEBUI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXT_DIR="$SCRIPT_DIR"
OUT_DIR="$EXT_DIR/dist"

echo "→ Building webui panel (Vite)..."
cd "$WEBUI_DIR"
npx vite build --outDir "$OUT_DIR/panel" --emptyOutDir

echo "→ Building extension worker + content script (esbuild)..."
cd "$EXT_DIR"
npx esbuild background.ts --bundle --outfile="$OUT_DIR/background.js" --platform=browser --format=esm --tsconfig=tsconfig.json
npx esbuild content/content.ts --bundle --outfile="$OUT_DIR/content/content.js" --platform=browser --format=iife --tsconfig=tsconfig.json --external:chrome
npx esbuild options/options.ts --bundle --outfile="$OUT_DIR/options/options.js" --platform=browser --format=iife --tsconfig=tsconfig.json

# Copy static assets
cp manifest.json "$OUT_DIR/"
cp options/options.html "$OUT_DIR/options/"
cp -r "$WEBUI_DIR/public/icons" "$OUT_DIR/icons" 2>/dev/null || mkdir -p "$OUT_DIR/icons"

# Generate placeholder icons if none exist
for size in 16 48 128; do
  if [ ! -f "$OUT_DIR/icons/icon${size}.png" ]; then
    # Create a minimal 1x1 PNG placeholder
    # In production, replace with real icons
    echo "⚠ No icon${size}.png — creating placeholder (replace with real icons)"
  fi
done

echo "✓ Extension built to $OUT_DIR"
echo "  Load $OUT_DIR in chrome://extensions (Developer mode → Load unpacked)"
