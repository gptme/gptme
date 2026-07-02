#!/bin/bash
set -e

./start_all.sh
./novnc_startup.sh

# Start gptme server with browser tool enabled for structured-first web interaction
# (browser provides snapshot_url/open_page/fill_element/click_element for the computer-use profile)
python3 -m gptme.server --host 0.0.0.0 --port 8080 --tools ipython,computer,browser,shell,vision --cors-origin '*'

# Keep the container running
tail -f /dev/null
