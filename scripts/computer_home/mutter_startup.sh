#!/bin/bash
set -e

echo "Starting mutter..."
XDG_SESSION_TYPE=x11 mutter --replace --sm-disable 2>/tmp/mutter_stderr.log &

# Use EWMH-standard check: _NET_SUPPORTING_WM_CHECK is set on the root window
# by any EWMH-compliant WM when it takes over management. This works with any
# WM and is authoritative — unlike searching for a specific window class.
#
# Falls back to xdotool class search if xprop is not available (minimal installs).
timeout_ms=30000
elapsed_ms=0
poll_ms=100

# Determine once which readiness probe to use (avoids a subprocess fork per iteration)
if command -v xprop >/dev/null 2>&1; then
    _use_xprop=1
else
    _use_xprop=0
fi

while [ $elapsed_ms -lt $timeout_ms ]; do
    if [ "$_use_xprop" -eq 1 ]; then
        if xprop -root _NET_SUPPORTING_WM_CHECK >/dev/null 2>&1; then
            break
        fi
    else
        if xdotool search --class "mutter" >/dev/null 2>&1; then
            break
        fi
    fi
    sleep 0.1
    elapsed_ms=$((elapsed_ms + poll_ms))
done

if [ $elapsed_ms -ge $timeout_ms ]; then
    echo "mutter failed to start within $((timeout_ms / 1000))s" >&2
    cat /tmp/mutter_stderr.log >&2
    exit 1
fi

echo "mutter ready after ${elapsed_ms}ms"
rm /tmp/mutter_stderr.log
