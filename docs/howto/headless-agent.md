# Running gptme as a Headless Service

Run gptme as an autonomous headless agent that starts on boot, restarts automatically on failure, and logs all activity to durable session files. Perfect for long-running automation, periodic tasks, or unattended environments.

## Quick Start

Generate a ready-to-run service scaffold in three commands:

```bash
# 1. Install gptme (if not already installed)
pipx install gptme

# 2. Scaffold a headless service
gptme service init \
  --name my-agent \
  --backend anthropic \
  --model claude-opus-5 \
  --output ~/my-agent

# 3. Copy the service to systemd and start
cp ~/my-agent/my-agent.service ~/.config/systemd/user/
cp ~/my-agent/my-agent.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now my-agent.timer

# 4. Monitor
systemctl --user status my-agent.service
journalctl --user -u my-agent -f
```

The service runs on your configured schedule (default: hourly via `my-agent.timer`). Session logs are saved to `~/my-agent/sessions/` for inspection and replay.

## What Gets Generated

The `gptme service init` command creates a complete, self-contained agent directory:

```
~/my-agent/
├── gptme.toml              # Agent configuration (model, backend, tools, memory)
├── AGENTS.md               # Agent identity and operating guidelines
├── my-agent.service        # systemd service unit (hardened, runs as your user)
├── my-agent.timer          # systemd timer for recurring execution (optional)
├── my-agent-run.sh         # Startup script (bash wrapper, handles logging)
├── health-check.py         # Optional: JSON health endpoint (if --enable-health-check)
├── README.md               # Setup instructions and troubleshooting
└── sessions/               # Session logs (created on first run)
```

Each component is minimal and self-documenting — use them as starting points to customize your agent.

## Configuration Options

Pass arguments to `gptme service init` to customize the generated service:

### Essential Options

| Option | Default | Purpose |
|--------|---------|---------|
| `--name` | (required) | Agent name; used for service/timer/script names |
| `--output` | `~/gptme-agents/<name>` | Output directory (created if missing) |
| `--backend` | `anthropic` | LLM provider: `anthropic`, `openrouter`, `openai`, etc. |
| `--model` | `claude-opus-5` | Model ID for the backend |

### Scheduling & Execution

| Option | Default | Purpose |
|--------|---------|---------|
| `--timer-schedule` | `hourly` | Timer trigger: `hourly`, `daily`, `weekly`, `manual`, or cron expr |
| `--work-dir` | Agent output dir | Working directory where the agent runs |
| `--force` | (off) | Overwrite existing files without prompting |

### Advanced Options

| Option | Default | Purpose |
|--------|---------|---------|
| `--enable-health-check` | (off) | Generate `health-check.py` endpoint (listens on localhost:9000) |
| `--tools` | `default` | Comma-separated tool list: `bash`, `editor`, `web_search`, `computer` |
| `--memory-limit` | (none) | systemd MemoryMax (e.g., `512M`, `1G`) |
| `--cpu-quota` | (none) | systemd CPUQuota (e.g., `50%`, `1`) |

### Example: Customize for a Specific Task

```bash
# Daily agent that uses only bash and web search (lightweight)
gptme service init \
  --name daily-reporter \
  --timer-schedule daily \
  --tools bash,web_search \
  --model claude-sonnet-5 \
  --output ~/agents/daily-reporter

# Research agent with memory limit (sandboxed)
gptme service init \
  --name research-agent \
  --timer-schedule "0 9 * * *" \
  --memory-limit 2G \
  --cpu-quota 50% \
  --output ~/agents/research
```

## Running the Service

### Start on a Schedule (Recommended)

Use the systemd timer to run the agent periodically:

```bash
# Enable timer (starts now and on every boot)
systemctl --user enable --now ~/my-agent/my-agent.timer

# View next scheduled runs
systemctl --user list-timers my-agent.timer

# Stop the timer
systemctl --user stop my-agent.timer
```

### Run Once Manually

```bash
# Trigger the service immediately (ignoring the timer schedule)
systemctl --user start my-agent.service

# Watch output
journalctl --user -u my-agent -f

# Check the exit code
systemctl --user show my-agent.service -p Result
```

### Monitor Service Health

```bash
# Quick status
systemctl --user status my-agent.service

# Full logs (last 50 lines)
journalctl --user -u my-agent -n 50

# Follow live logs
journalctl --user -u my-agent -f

# View session records (if available)
ls -lh ~/my-agent/sessions/
cat ~/my-agent/sessions/latest/journal.md
```

## Configuring the Agent

Edit `gptme.toml` to customize behavior, tools, and context:

```toml
[system]
model = "claude-opus-5"      # Which model to use
backend = "anthropic"         # LLM provider

[tools]
enabled = ["bash", "editor"]  # Which tools are available
disabled = ["computer"]       # Explicitly disabled tools

[memory]
max_context = 32000           # Max prompt context (tokens)
max_history = 20              # Max historical messages to include

[task]
timeout = 600                 # Max seconds per task (0 = no limit)
work_dir = "/home/user/work"  # Where to execute tasks
```

See the main [gptme configuration guide](../config.md) for the full schema.

## Troubleshooting

### Service Won't Start

**Problem**: `systemctl start my-agent.service` fails or exits immediately.

**Solution**:
1. Check logs for the actual error:
   ```bash
   journalctl --user -u my-agent.service -n 20
   ```

2. Verify dependencies:
   ```bash
   # Confirm gptme is installed and in PATH
   which gptme
   gptme --version

   # Check if config files exist
   ls -la ~/my-agent/gptme.toml
   ```

3. Test the command manually:
   ```bash
   cd ~/my-agent
   gptme --config gptme.toml "hello"
   ```

### Service Runs but No Output

**Problem**: Service completes but session logs are empty or truncated.

**Solution**:
1. Check where logs are being written:
   ```bash
   ls -lh ~/my-agent/sessions/
   ```

2. Verify the agent isn't hitting a timeout:
   ```bash
   systemctl --user show my-agent.service -p ExecMainStatus
   # Status 0 = success, 124 = timeout
   ```

3. Check for API key / authentication issues:
   ```bash
   journalctl --user -u my-agent -n 100 | grep -i "auth\|key\|error"
   ```

### Timer Never Fires

**Problem**: `systemctl list-timers` shows the timer but it never runs.

**Solution**:
1. Verify the timer is enabled:
   ```bash
   systemctl --user is-enabled my-agent.timer
   # Should print "enabled"
   ```

2. Check if systemd user timers are active:
   ```bash
   systemctl --user status
   # Look for "active (running)"
   ```

3. Manually trigger to test:
   ```bash
   systemctl --user start my-agent.service
   ```

### Health Check Endpoint Not Responding

**Problem**: `curl localhost:9000` connection refused (if using `--enable-health-check`).

**Solution**:
1. Verify health-check.py is running:
   ```bash
   ps aux | grep health-check
   ```

2. Check logs for startup errors:
   ```bash
   journalctl --user -u my-agent.service | grep "health\|9000"
   ```

3. Test the endpoint:
   ```bash
   curl -v http://localhost:9000/health
   ```

## Examples

### Example 1: Daily Data Collection Agent

Collect data from a web API and save to a file every morning at 6am:

```bash
gptme service init \
  --name daily-data \
  --timer-schedule "0 6 * * *" \
  --model claude-opus-5 \
  --tools bash,web_search \
  --output ~/agents/daily-data
```

Then edit `~/agents/daily-data/AGENTS.md` to describe the task:

```markdown
## Operating Instructions

1. Fetch data from https://api.example.com/status
2. Parse JSON response
3. Append timestamp + result to ~/data.log
4. Alert if status != "healthy"
```

### Example 2: Automated Code Review (Nightly)

Run a gptme agent that reviews new pull requests every night:

```bash
gptme service init \
  --name pr-reviewer \
  --timer-schedule daily \
  --model claude-opus-5 \
  --output ~/agents/pr-reviewer
```

Edit the generated `AGENTS.md` and `gptme.toml` to add context about your repository, then configure it to:
- Clone the repo
- Fetch recent PRs
- Run code review on each
- Post feedback

### Example 3: Resource-Constrained Agent (Raspberry Pi)

Deploy a lightweight agent on a Raspberry Pi with CPU and memory limits:

```bash
gptme service init \
  --name rpi-agent \
  --model claude-haiku-4.5 \
  --backend openrouter \
  --memory-limit 256M \
  --cpu-quota 25% \
  --tools bash \
  --output ~/agents/rpi-agent
```

## Environment Variables

The generated service reads secrets from `~/.config/gptme/env` or `~/.env`:

```bash
# Create or edit the env file
cat > ~/.config/gptme/env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
EOF

# Restrict permissions
chmod 600 ~/.config/gptme/env
```

The systemd service automatically loads this file, so credentials are not exposed in the command line or process listing.

## See Also

- [Main configuration guide](../config.md) — Full `gptme.toml` schema and options
- [Agents documentation](../agents.md) — Creating custom agents and agent templates
- [Server documentation](../server.rst) — Running `gptme-server` for browser-based access
- [systemd user service guide](https://systemd.io/USER_SESSIOND/) — Deeper systemd details

## Feedback

If you encounter issues or have suggestions for the `gptme service init` command, open an issue on [GitHub](https://github.com/gptme/gptme/issues) or discuss on [Discord](https://discord.gg/NMaCmmkxWv).
