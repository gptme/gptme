#!/usr/bin/env bash
set -euo pipefail

WATCH="${1:-}"
OUTDIR="dist"

mkdir -p "$OUTDIR/sidepanel" "$OUTDIR/content" "$OUTDIR/options" "$OUTDIR/icons"

COMMON=(
  --bundle
  --sourcemap
  --target=chrome120
)
[[ "$WATCH" == "--watch" ]] && COMMON+=(--watch)

# Background: ESM module (MV3 service worker with type:module)
npx esbuild background.ts "${COMMON[@]}" --format=esm --outfile="$OUTDIR/background.js"

# Side panel: ESM (loaded via <script type="module">)
npx esbuild sidepanel/panel.ts "${COMMON[@]}" --format=esm --outfile="$OUTDIR/sidepanel/panel.js"

# Content script: IIFE (injected directly by Chrome, no module support)
npx esbuild content/content.ts "${COMMON[@]}" --format=iife --outfile="$OUTDIR/content/content.js"

# Options page: ESM
npx esbuild options/options.ts "${COMMON[@]}" --format=esm --outfile="$OUTDIR/options/options.js"

# Static files
cp manifest.json "$OUTDIR/manifest.json"
cp sidepanel/index.html "$OUTDIR/sidepanel/index.html"
cp sidepanel/panel.css "$OUTDIR/sidepanel/panel.css"
cp options/options.html "$OUTDIR/options/options.html"
cp -r icons/ "$OUTDIR/icons/" 2>/dev/null || true

echo "Build complete → $OUTDIR/"
