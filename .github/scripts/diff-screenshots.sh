#!/usr/bin/env bash
# Compare two directories of PNG screenshots using ImageMagick.
# Usage: diff-screenshots.sh <before_dir> <after_dir> <output_dir>
#
# Writes output_dir/summary.json with { total, changed, same, results: [...] }.
# Exits 0 always (caller checks summary.json).
set -euo pipefail

BEFORE_DIR="$1"
AFTER_DIR="$2"
OUTPUT_DIR="$3"

mkdir -p "$OUTPUT_DIR"

TOTAL=0
CHANGED=0
RESULTS="["
FIRST=1

# JSON-encode a string value (handles quotes, backslashes, control chars).
json_str() { python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$1"; }

for after_file in "$AFTER_DIR"/*.png; do
  [ -f "$after_file" ] || continue
  name=$(basename "$after_file")
  name_json=$(json_str "$name")
  before_file="$BEFORE_DIR/$name"
  TOTAL=$((TOTAL + 1))
  [ "$FIRST" -eq 0 ] && RESULTS="$RESULTS,"
  FIRST=0

  if [ ! -f "$before_file" ]; then
    echo "NEW: $name"
    cp "$after_file" "$OUTPUT_DIR/after-$name"
    RESULTS="$RESULTS{\"name\":$name_json,\"status\":\"new\",\"pixels\":0}"
    CHANGED=$((CHANGED + 1))
    continue
  fi

  # compare returns exit 1 when images differ; capture stderr (pixel count)
  diff_count=$(compare -metric AE "$before_file" "$after_file" \
    "$OUTPUT_DIR/diff-$name" 2>&1 || true)

  # Strip whitespace; non-numeric output means compare errored
  diff_count=$(echo "$diff_count" | tr -d '[:space:]' | grep -oE '^[0-9]+' || echo "0")

  if [ "${diff_count:-0}" -gt 0 ]; then
    echo "CHANGED: $name (${diff_count} pixels)"
    cp "$before_file" "$OUTPUT_DIR/before-$name"
    cp "$after_file" "$OUTPUT_DIR/after-$name"
    RESULTS="$RESULTS{\"name\":$name_json,\"status\":\"changed\",\"pixels\":$diff_count}"
    CHANGED=$((CHANGED + 1))
  else
    echo "SAME: $name"
    RESULTS="$RESULTS{\"name\":$name_json,\"status\":\"same\",\"pixels\":0}"
  fi
done

# Also report screenshots that were removed in the PR branch (exist only in before).
for before_file in "$BEFORE_DIR"/*.png; do
  [ -f "$before_file" ] || continue
  name=$(basename "$before_file")
  name_json=$(json_str "$name")
  after_file="$AFTER_DIR/$name"
  [ -f "$after_file" ] && continue  # already handled above
  TOTAL=$((TOTAL + 1))
  [ "$FIRST" -eq 0 ] && RESULTS="$RESULTS,"
  FIRST=0
  echo "REMOVED: $name"
  cp "$before_file" "$OUTPUT_DIR/before-$name"
  RESULTS="$RESULTS{\"name\":$name_json,\"status\":\"removed\",\"pixels\":0}"
  CHANGED=$((CHANGED + 1))
done

RESULTS="$RESULTS]"
SAME=$((TOTAL - CHANGED))

cat > "$OUTPUT_DIR/summary.json" <<EOF
{
  "total": $TOTAL,
  "changed": $CHANGED,
  "same": $SAME,
  "results": $RESULTS
}
EOF

echo ""
echo "Result: $CHANGED/$TOTAL screenshots changed"
