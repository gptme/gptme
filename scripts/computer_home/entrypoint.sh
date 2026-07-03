#!/bin/bash
set -e

./start_all.sh
./novnc_startup.sh

# Start gptme server with the computer-use profile as default so every new
# conversation automatically gets the structured-first, screenshot-fallback
# backend-selection policy — no per-session --agent-profile flag needed.
python3 -m gptme.server --host 0.0.0.0 --port 8080 --tools ipython,computer,browser,shell,vision --default-profile computer-use --cors-origin '*'

# Keep the container running
tail -f /dev/null
