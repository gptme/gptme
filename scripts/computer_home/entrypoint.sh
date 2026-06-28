#!/bin/bash
set -e

./start_all.sh
./novnc_startup.sh

# Start gptme server
python3 -m gptme.server --host 0.0.0.0 --port 8080 --tools ipython,computer,shell,vision --cors-origin '*'

# Keep the container running
tail -f /dev/null
