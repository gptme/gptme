#!/bin/bash
# Setup script for gptme inside a Terminal-Bench container.
# Installs gptme from the latest master branch into an isolated venv.

set -e

# Install system dependencies
apt-get update -q
apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl git

# Install uv for fast package management
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Create venv and install gptme (Ubuntu 24.04+ requires isolated venv)
python3 -m venv /opt/gptme-venv
source /opt/gptme-venv/bin/activate
uv pip install 'gptme @ git+https://github.com/gptme/gptme.git@master'

# Make gptme available globally
ln -sf /opt/gptme-venv/bin/gptme /usr/local/bin/gptme

# Verify
gptme --version
echo "gptme setup complete."
