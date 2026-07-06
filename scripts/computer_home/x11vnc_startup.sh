#!/bin/bash
echo "starting vnc"

(x11vnc -display $DISPLAY \
    -forever \
    -shared \
    -rfbport 5900 \
    -nopw \
    2>/tmp/x11vnc_stderr.log) &

x11vnc_pid=$!

# Wait for x11vnc to start
timeout_ms=10000
elapsed_ms=0
poll_ms=100
while [ $elapsed_ms -lt $timeout_ms ]; do
    if netstat -tuln | grep -q ":5900 "; then
        break
    fi
    sleep 0.1
    elapsed_ms=$((elapsed_ms + poll_ms))
done

if [ $elapsed_ms -ge $timeout_ms ]; then
    echo "x11vnc failed to start, stderr output:" >&2
    cat /tmp/x11vnc_stderr.log >&2
    exit 1
fi

: > /tmp/x11vnc_stderr.log

# Monitor x11vnc process in the background
(
    while true; do
        if ! kill -0 $x11vnc_pid 2>/dev/null; then
            echo "x11vnc process crashed, restarting..." >&2
            if [ -f /tmp/x11vnc_stderr.log ]; then
                echo "x11vnc stderr output:" >&2
                cat /tmp/x11vnc_stderr.log >&2
                rm /tmp/x11vnc_stderr.log
            fi
            exec "$0"
        fi
        sleep 5
    done
) &
