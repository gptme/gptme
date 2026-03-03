#!/bin/bash
# Setup script for gptme inside a Terminal-Bench container.
# Installs gptme into an isolated venv.
#
# Environment variables:
#   GPTME_VERSION  Pin to a specific git ref (branch, tag, or commit SHA).
#                  Defaults to "master". Set to a tagged release or commit SHA
#                  for reproducible benchmark runs, e.g. GPTME_VERSION=v0.22.0

set -e

GPTME_VERSION="${GPTME_VERSION:-master}"

# Install system dependencies
apt-get update -q
apt-get install -y --no-install-recommends python3 python3-pip python3-venv git

# Install uv via pip (avoids piped-curl; works on all Python 3.x installs)
python3 -m pip install --quiet uv
export PATH="$HOME/.local/bin:$PATH"

# Create venv and install gptme (Ubuntu 24.04+ requires isolated venv)
python3 -m venv /opt/gptme-venv
source /opt/gptme-venv/bin/activate
uv pip install "gptme @ git+https://github.com/gptme/gptme.git@${GPTME_VERSION}"

# Make gptme available globally
ln -sf /opt/gptme-venv/bin/gptme /usr/local/bin/gptme

# Verify
gptme --version
echo "gptme setup complete."
