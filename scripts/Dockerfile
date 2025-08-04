# Build stage
FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install poetry in its own venv
RUN python -m venv /opt/poetry && \
    /opt/poetry/bin/pip install poetry

# Copy files needed for building wheel
COPY pyproject.toml poetry.lock README.md ./
COPY gptme gptme/

# Install dependencies and export requirements for server
RUN /opt/poetry/bin/poetry self add poetry-plugin-export && \
    /opt/poetry/bin/poetry export --without-hashes --without dev -f requirements.txt -o requirements.txt && \
    /opt/poetry/bin/poetry export --without-hashes --without dev -E server -f requirements.txt -o requirements-server.txt && \
    /opt/poetry/bin/poetry build

# Final stage
FROM python:3.12-slim

# Install runtime dependencies and GitHub CLI
RUN apt-get update && apt-get install -y \
    git \
    tmux \
    curl \
    pandoc \
    gpg \
    pipx \
    tree \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy wheel and requirements, then install
COPY --from=builder /app/dist/gptme-*.whl /app/requirements*.txt /tmp/
RUN pip install /tmp/gptme-*.whl && rm /tmp/gptme-*.whl

# Create non-root user and workspace
RUN useradd -m appuser && \
    mkdir /workspace && \
    chown -R appuser:appuser /workspace

USER appuser
RUN pipx ensurepath

# pipx ensurepath is not working for the entrypoint which bypasses bash
# so if we want to extend Dockerfile with pipx-installed entrypoints,
# we need to manually add the pipx bin directory to PATH.
ENV PATH="/home/appuser/.local/bin:${PATH}"

git config --global user.name "gptme.ai"
git config --global user.email "noreply@gptme.ai"

WORKDIR /workspace

ENTRYPOINT ["gptme"]
