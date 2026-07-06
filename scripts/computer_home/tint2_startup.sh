#!/bin/bash
set -e

echo "Starting tint2..."
tint2 2>/tmp/tint2_stderr.log &

timeout_ms=30000
elapsed_ms=0
poll_ms=100
while [ $elapsed_ms -lt $timeout_ms ]; do
    if xdotool search --class "tint2" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
    elapsed_ms=$((elapsed_ms + poll_ms))
done

if [ $elapsed_ms -ge $timeout_ms ]; then
    echo "tint2 stderr output:" >&2
    cat /tmp/tint2_stderr.log >&2
    exit 1
fi

rm /tmp/tint2_stderr.log
