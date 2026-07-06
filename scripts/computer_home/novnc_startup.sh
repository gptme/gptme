#!/bin/bash
echo "starting noVNC"

# Start noVNC with optimized settings
/opt/noVNC/utils/novnc_proxy \
    --vnc localhost:5900 \
    --listen 6080 \
    --web /opt/noVNC \
    --heartbeat 15 \
    --idle-timeout 0 \
    > /tmp/novnc.log 2>&1 &

# Wait for noVNC to start
timeout_ms=10000
elapsed_ms=0
poll_ms=100
while [ $elapsed_ms -lt $timeout_ms ]; do
    if netstat -tuln | grep -q ":6080 "; then
        break
    fi
    sleep 0.1
    elapsed_ms=$((elapsed_ms + poll_ms))
done

if [ $elapsed_ms -ge $timeout_ms ]; then
    echo "noVNC failed to start, log output:" >&2
    cat /tmp/novnc.log >&2
    exit 1
fi

echo "noVNC started successfully"
