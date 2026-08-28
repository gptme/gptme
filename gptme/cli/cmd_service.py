"""
Scaffold a persistent headless gptme agent as a systemd user service (Linux) or
launchd agent (macOS).

Generates the unit file(s), startup script, and skeleton config needed to
run a gptme agent on a timer — the pattern Bob uses for autonomous sessions —
without reverse-engineering an existing agent workspace.

For the full agent workspace template with examples and best practices, see:
https://github.com/gptme/gptme-agent-template

Usage:
    gptme service init --name myagent --model gpt-4o-mini --work-dir ~/gptme-agent
    gptme service init --name myagent --timer-schedule hourly --platform linux
    gptme service init --name myagent --output-dir ~/.config/systemd/user --platform linux
    gptme service init --name myagent --output-dir ~/Library/LaunchAgents --platform macos

The command writes files only; it does not install or start the service.
After generation (Linux), run:

    systemctl --user daemon-reload
    systemctl --user enable --now myagent.service   # or myagent.timer

After generation (macOS), run:

    launchctl load ~/Library/LaunchAgents/com.gptme.myagent.plist
"""

from __future__ import annotations

import logging
import platform
import re
import shlex
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

# XML 1.0 permits only #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF].
# Reject control characters and lone UTF-16 surrogates before generating launchd plists.
_XML10_FORBIDDEN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]")

import click

# Allowed characters for the agent name: matches systemd/launchd unit-name conventions.
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

logger = logging.getLogger(__name__)

# systemd disable/stop calls must not hang the CLI indefinitely.
_SYSTEMCTL_TIMEOUT_SEC = 30


def _escape_systemd_value(value: str) -> str:
    """Escape a value for safe interpolation into a systemd unit file.

    Doubles literal '%' so systemd doesn't expand it as a specifier (e.g. a
    work-dir of `/tmp/%h` would otherwise expand to the user's home dir), and
    rejects characters that systemd cannot represent safely in an executable
    path or that would terminate the current directive.
    """
    if "\n" in value or "\r" in value:
        raise click.BadParameter(
            f"{value!r} must not contain newlines (would corrupt the generated unit file)"
        )
    if any(char in value for char in ("\\", '"', "'")):
        raise click.BadParameter(
            f"{value!r} must not contain quotes or backslashes "
            "(unsupported in a systemd executable path)"
        )
    return value.replace("%", "%%")


def _escape_systemd_exec_value(value: str) -> str:
    """Escape a validated unit value for an ExecStart command line."""
    return _escape_systemd_value(value).replace("$", r"\x24")


SYSTEMD_SERVICE_TEMPLATE = """\
[Unit]
Description=gptme Autonomous Agent: {name}
Documentation=https://github.com/gptme/gptme#headless
After=network-online.target
# A session is one-shot, so a persistent failure (bad API key, exhausted quota,
# unusable prompt) would otherwise retry forever against a paid API. Give up
# after 3 failures in 10 minutes and let the timer try again on schedule.
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
WorkingDirectory={work_dir}
Environment=GPTME_AGENT_NAME={name}
Environment="GPTME_AGENT_MODEL={model}"
ExecStart=:"{exec_work_dir}/gptme-agent-run.sh"
StandardOutput=journal
StandardError=journal
Restart=on-failure
# RestartSteps is required for RestartMaxDelaySec to apply at all; without it
# systemd logs "has RestartMaxDelaySec= but no RestartSteps=. Ignoring" and
# retries at a flat RestartSec forever.
RestartSec=5
RestartSteps=5
RestartMaxDelaySec=60
# Kill the service if it runs longer than this. Prevents a hung LLM connection
# from keeping the unit active forever (blocking timer-triggered reruns).
# Adjust to your expected maximum session length.
RuntimeMaxSec=3600

[Install]
WantedBy=default.target
"""

SYSTEMD_TIMER_TEMPLATE = """\
[Unit]
Description=gptme Autonomous Agent Timer: {name}

[Timer]
OnCalendar={schedule}
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
"""

# OnCalendar expressions for the named schedules we support.
SCHEDULES: dict[str, str] = {
    "hourly": "*-*-* *:00:00",
    "daily": "*-*-* 00:00:00",
    "weekly": "Mon *-*-* 00:00:00",
}

LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.gptme.{name}</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>{work_dir}/gptme-agent-run.sh</string>
	</array>
	<key>WorkingDirectory</key>
	<string>{work_dir}</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>GPTME_AGENT_NAME</key>
		<string>{name}</string>
		<key>GPTME_AGENT_MODEL</key>
		<string>{model}</string>
	</dict>
	<key>StandardOutPath</key>
	<string>{work_dir}/logs/stdout.log</string>
	<key>StandardErrorPath</key>
	<string>{work_dir}/logs/stderr.log</string>
	<key>RunAtLoad</key>
	<{run_at_load}/>
	{schedule_section}
</dict>
</plist>
"""

# launchd schedule templates
LAUNCHD_SCHEDULE_SECTION = """\
	<key>StartInterval</key>
	<integer>{interval_seconds}</integer>
"""

LAUNCHD_SCHEDULE_INTERVALS: dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}

STARTUP_SCRIPT_TEMPLATE = """\
#!/bin/bash
# gptme autonomous agent startup script.
# Generated by: gptme service init --name {name}
set -euo pipefail

AGENT_NAME="{name}"
WORK_DIR={work_dir}
JOURNAL_DIR="$WORK_DIR/journal/$(date +%Y-%m-%d)"
PROMPT_FILE="$WORK_DIR/prompt.md"

mkdir -p "$JOURNAL_DIR" || {{ echo "gptme agent $AGENT_NAME: cannot create journal dir $JOURNAL_DIR" >&2; exit 1; }}
SESSION_ID=$(date +%Y%m%d-%H%M%S)-$$-$RANDOM
SESSION_LOG="$JOURNAL_DIR/session-$SESSION_ID.md"

{{
  echo "# Session $SESSION_ID"
  echo
  echo "Agent: $AGENT_NAME"
  echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
}} > "$SESSION_LOG"

if ! command -v gptme >/dev/null 2>&1; then
  echo "gptme agent $AGENT_NAME: 'gptme' not found on PATH" >&2
  echo "gptme not found on PATH; install gptme or set PATH in the service unit." \
    >> "$SESSION_LOG"
  exit 127
fi

if [ ! -f "$PROMPT_FILE" ]; then
  echo "gptme agent $AGENT_NAME: missing prompt file $PROMPT_FILE" >&2
  echo "Missing prompt file $PROMPT_FILE." >> "$SESSION_LOG"
  exit 66
fi
if [ ! -r "$PROMPT_FILE" ]; then
  echo "gptme agent $AGENT_NAME: prompt file $PROMPT_FILE is not readable" >&2
  echo "Prompt file not readable: $PROMPT_FILE (check permissions)." >> "$SESSION_LOG"
  exit 66
fi
if [ ! -s "$PROMPT_FILE" ]; then
  echo "gptme agent $AGENT_NAME: prompt file $PROMPT_FILE is empty" >&2
  echo "Prompt file is empty: $PROMPT_FILE (add your agent instructions)." >> "$SESSION_LOG"
  exit 66
fi
# Reject prompts whose first content line is a gptme in-chat command.
# _group_prompt_args strips the leading-newline guard before the chat loop
# dispatch: /shell, /python, /log, etc. as the first line would be dispatched
# as a command rather than sent to the model. /path/to/file-style strings are
# safe because their first word contains more than one slash.
_prompt_fw=$(grep -m1 '[^[:space:]]' "$PROMPT_FILE" | python3 -c "import sys; w=sys.stdin.read().split(); print(w[0] if w else '')")
if [ "${{_prompt_fw#/}}" != "$_prompt_fw" ] \
   && [ "$(printf '%s' "$_prompt_fw" | awk -F'/' '{{print NF-1}}')" -eq 1 ]; then
  echo "gptme agent $AGENT_NAME: prompt.md starts with '$_prompt_fw', a gptme in-chat command; it would be dispatched rather than sent to the model." >&2
  echo "Begin prompt.md with a description or heading (e.g. '# Task')." >&2
  echo "Prompt starts with in-chat command '$_prompt_fw'." >> "$SESSION_LOG"
  exit 66
fi
unset _prompt_fw

# Run one non-interactive gptme session. --non-interactive is passed as a flag
# because gptme reads the flag, not GPTME_NON_INTERACTIVE.
gptme_args=(--non-interactive --no-confirm --workspace "$WORK_DIR")
gptme_args+=(--name "$AGENT_NAME-$SESSION_ID")
if [ -n "${{GPTME_AGENT_MODEL:-}}" ]; then
  gptme_args+=(--model "$GPTME_AGENT_MODEL")
fi

echo "## Output" >> "$SESSION_LOG"
echo >> "$SESSION_LOG"

cd "$WORK_DIR"
# Prefix a newline so the raw CLI argument never matches a gptme-util subcommand
# or plugin name (checked before _group_prompt_args runs). _group_prompt_args
# strips leading whitespace, so the LLM receives the file content as-is.
# Single-slash commands (e.g. /shell) are rejected above before we reach here.
prompt_arg=$'\\n'"$(cat "$PROMPT_FILE")"
exit_code=0
gptme "${{gptme_args[@]}}" "$prompt_arg" >> "$SESSION_LOG" 2>&1 || exit_code=$?

{{
  echo
  echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Exit code: $exit_code"
}} >> "$SESSION_LOG"

echo "gptme agent {name}: session $SESSION_ID exited $exit_code (log: $SESSION_LOG)"
exit "$exit_code"
"""

PROMPT_MD_TEMPLATE = """\
# {name} — session prompt

This is the instruction {name} receives at the start of every headless session.
Edit it to describe the work the agent should do on each run.

Keep it short and concrete. A headless session has no human to ask, so state the
goal, where to look, and what "done" means.

## Task

Review the notes in this workspace and summarize anything that needs attention.
Write findings to `journal/` and stop.
"""

GPTME_TOML_TEMPLATE = """\
[agent]
name = "{name}"

[prompt]
files = [
  "AGENTS.md",
  "gptme.toml"
]

[lessons]
dirs = ["lessons", "skills"]
"""

AGENTS_MD_TEMPLATE = """\
# Agent Instructions for {name}

This file is auto-loaded by gptme and other agent runtimes.

## Role

{name} is a persistent headless gptme agent. It runs non-interactively from a
Linux systemd user unit or a macOS launchd agent.

`gptme service init` intentionally creates a minimal scaffold. For the full
batteries-included workspace (richer task loop, production run scripts,
monitoring, and service examples), use gptme-agent-template:
https://github.com/gptme/gptme-agent-template

## Core Rules

### 1. Absolute Paths

Use `git rev-parse --show-toplevel` for the repo root.

### 2. Conventional Commits

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation
- `refactor:` — code restructuring
- `test:` — tests
- `chore:` — maintenance

### 3. Stage Files Explicitly

Use `git add <files>`, never `git add .` or `git commit -a`.

### 4. Journal

Append-only logs in `journal/YYYY-MM-DD/`.
Never modify historical entries.
"""

HEALTH_CHECK_TEMPLATE = '''\
#!/usr/bin/env python3
"""Generated local health probe for the {name} systemd user service."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone


def check_service_status(service_name: str) -> dict[str, str]:
    """Return a JSON-serializable summary of a systemd user service."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                service_name,
                "--property=LoadState",
                "--property=ActiveState",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {{
            "service": service_name,
            "load_state": "unknown",
            "active_state": "unknown",
            "status": "error",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }}

    properties = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    load_state = properties.get("LoadState", "unknown")
    active_state = properties.get("ActiveState", "unknown")
    status = (
        "healthy"
        if result.returncode == 0 and active_state == "active"
        else "unhealthy"
    )
    return {{
        "service": service_name,
        "load_state": load_state,
        "active_state": active_state,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }}


def main() -> int:
    health = check_service_status("{name}.service")
    print(json.dumps(health, indent=2))
    return 0 if health["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

HEALTH_README_TEMPLATE = """\
# {name} health probe

`health-check.py` is a local command-line probe for the generated Linux systemd
user service. It does **not** start an HTTP server.

```bash
./health-check.py
```

The command prints JSON and exits 0 only while `{name}.service` is active. It
exits 1 for inactive, failed, missing, timed-out, or unreachable user managers,
which makes it suitable for local monitoring and cron checks.

Useful companion commands:

```bash
systemctl --user status {name}.service
journalctl --user -u {name}.service -n 50
systemctl --user start {name}.service
```

The probe is generated only for `--platform linux`. launchd users can inspect
`launchctl print gui/$(id -u)/com.gptme.{name}` instead.
"""


def _resolve_work_dir(work_dir: str) -> Path:
    """Resolve the agent work directory path (does not create it)."""
    return Path(work_dir).expanduser().resolve()


def _write_file(path: Path, content: str, force: bool = False) -> bool:
    """Write a file, skipping existing files unless --force is set.

    Returns True if the file was written, False if skipped.
    """
    if path.exists() and not force:
        click.echo(f"  Skipped existing {path} (use --force to overwrite)")
        return False
    path.write_text(content)
    click.echo(f"  Created {path}")
    return True


def _detect_platform() -> str:
    """Detect the current platform: 'macos' or 'linux'.

    Raises UsageError for platforms where neither systemd nor launchd applies
    (e.g. Windows, FreeBSD). Use --platform explicitly on those systems.
    """
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "linux"
    raise click.UsageError(
        f"Platform {system!r} is not supported by 'gptme service init'. "
        "Use --platform linux or --platform macos to generate service files "
        "for a supported platform."
    )


def _generate_launchd_plist(
    name: str,
    model: str,
    work_dir: str,
    timer_schedule: str,
) -> str:
    """Generate a launchd .plist file for macOS agents."""
    schedule_section = ""
    if timer_schedule != "on-demand":
        interval = LAUNCHD_SCHEDULE_INTERVALS.get(timer_schedule, 86400)
        schedule_section = LAUNCHD_SCHEDULE_SECTION.format(interval_seconds=interval)

    # on-demand = manual start only (RunAtLoad=false); scheduled = start on load too
    run_at_load = "false" if timer_schedule == "on-demand" else "true"

    return LAUNCHD_PLIST_TEMPLATE.format(
        name=_xml_escape(name),
        model=_xml_escape(model),
        work_dir=_xml_escape(work_dir),
        schedule_section=schedule_section,
        run_at_load=run_at_load,
    )


@click.group(
    name="service",
    help="Manage persistent headless gptme agent services.",
    context_settings={"auto_envvar_prefix": "GPTME"},
)
def cli() -> None:
    """Persistent headless agent service scaffolding.

    Run `gptme service init` to scaffold a systemd user service for a
    headless gptme agent.
    """


@cli.command(
    name="init",
    help=(
        "Scaffold a minimal persistent service for a headless gptme agent "
        "(systemd on Linux, launchd on macOS). For the full workspace template, "
        "see gptme/gptme-agent-template."
    ),
)
@click.option(
    "--name",
    "-n",
    type=str,
    required=True,
    help="Agent name (used for the service unit and agent identity).",
)
@click.option(
    "--model",
    "-m",
    type=str,
    default="gpt-4o-mini",
    show_default=True,
    help="Default model for the agent.",
)
@click.option(
    "--work-dir",
    "-d",
    type=str,
    default="~/gptme-agent",
    show_default=True,
    help="Agent work directory (created if missing).",
)
@click.option(
    "--output-dir",
    "-o",
    type=str,
    default=None,
    help="Directory to write service files. Defaults to ~/.config/systemd/user (Linux) or ~/Library/LaunchAgents (macOS).",
)
@click.option(
    "--timer-schedule",
    type=click.Choice(["hourly", "daily", "weekly", "on-demand"]),
    default="daily",
    show_default=True,
    help="Timer schedule. 'on-demand' writes no timer (manual start only).",
)
@click.option(
    "--platform",
    "platform_choice",
    type=click.Choice(["linux", "macos", "auto"]),
    default="auto",
    show_default=True,
    help="Target platform. 'auto' detects the current system.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing generated files.",
)
@click.option(
    "--enable-health-check",
    is_flag=True,
    help="Generate a local JSON health probe (Linux/systemd only).",
)
def init(
    name: str,
    model: str,
    work_dir: str,
    output_dir: str | None,
    timer_schedule: str,
    platform_choice: str,
    force: bool,
    enable_health_check: bool,
) -> None:
    """Scaffold a persistent service for a headless gptme agent.

    Generates, in the agent work directory:

        gptme.toml, AGENTS.md, prompt.md, gptme-agent-run.sh
        health-check.py, HEALTH.md       # Linux with --enable-health-check

    and, in the service output directory:

        Linux (systemd):
            {name}.service             # main service unit
            {name}.timer               # optional periodic timer

        macOS (launchd):
            com.gptme.{name}.plist     # launchd property list

    Examples:

        \b
        # Linux (systemd)
        gptme service init --name myagent --model gpt-4o-mini --work-dir ~/gptme-agent
        gptme service init --name myagent --enable-health-check
        systemctl --user daemon-reload
        systemctl --user enable --now myagent.timer

        # macOS (launchd)
        gptme service init --name myagent --platform macos --work-dir ~/gptme-agent
        launchctl load ~/Library/LaunchAgents/com.gptme.myagent.plist

    This command intentionally creates a minimal scaffold. For the full
    batteries-included agent workspace template, see:
    https://github.com/gptme/gptme-agent-template
    """
    if not _NAME_RE.match(name):
        raise click.BadParameter(
            f"Name {name!r} contains invalid characters. "
            "Only letters, digits, hyphens, and underscores are allowed.",
            param_hint="'--name'",
        )

    # Resolve platform (auto-detect if needed)
    platform_was_auto = platform_choice == "auto"
    if platform_was_auto:
        platform_choice = _detect_platform()
    if enable_health_check and platform_choice == "macos":
        raise click.UsageError(
            "--enable-health-check supports Linux systemd services only. "
            "Use 'launchctl print' to inspect a macOS launchd agent."
        )
    if platform_was_auto and platform_choice == "macos" and output_dir is None:
        # Inform users explicitly: on macOS we now default to launchd
        # instead of the old systemd default, so anyone with scripts
        # expecting ~/.config/systemd/user gets a clear heads-up.
        click.echo(
            "Note: macOS detected — generating a launchd plist in "
            "~/Library/LaunchAgents instead of a systemd unit. "
            "Pass --platform linux to generate systemd units for a "
            "Linux target.",
            err=True,
        )

    # Set default output directory based on platform
    if output_dir is None:
        if platform_choice == "macos":
            output_dir = "~/Library/LaunchAgents"
        else:
            output_dir = "~/.config/systemd/user"

    # Validate model consistently across platforms BEFORE creating any directories.
    # systemd rejects newlines/quotes/backslashes; launchd must too (else the same
    # input is silently truncated on macOS but rejected on Linux, which is confusing
    # and allows injection attempts to go unnoticed on macOS).
    if "\n" in model or "\r" in model:
        raise click.BadParameter(
            f"{model!r} must not contain newlines",
            param_hint="'--model'",
        )
    if any(ch in model for ch in ("\\", '"', "'")):
        raise click.BadParameter(
            f"{model!r} must not contain quotes or backslashes",
            param_hint="'--model'",
        )

    # Resolve work dir path for validation (no mkdir yet — avoid leaving behind
    # side-effect directories when subsequent validation raises BadParameter).
    work = _resolve_work_dir(work_dir)

    # Platform-specific service generation
    if platform_choice == "macos":
        # Reject model identifiers that contain XML 1.0-forbidden characters.
        # Silently stripping them would cause the agent to run with a different
        # (or nonexistent) model than the user requested.
        if _XML10_FORBIDDEN.search(model):
            raise click.BadParameter(
                f"Model {model!r} contains XML 1.0-forbidden characters "
                "(control characters or lone surrogates) that cannot be "
                "represented in a launchd plist. Use a valid model identifier.",
                param_hint="'--model'",
            )
        # Reject paths that contain XML 1.0-forbidden characters BEFORE creating dirs.
        # The work-dir path must match the one written to disk — silently sanitizing
        # it in the plist would cause launchd to reference a nonexistent directory.
        resolved_work = str(work)
        if _XML10_FORBIDDEN.search(resolved_work):
            raise click.BadParameter(
                f"Work directory {resolved_work!r} contains XML 1.0-forbidden "
                "characters that cannot be represented in a launchd plist. "
                "Use a path without control characters.",
                param_hint="'--work-dir'",
            )
    else:
        # Validate the work-dir for systemd BEFORE creating dirs — _escape_systemd_value
        # raises BadParameter for quotes/backslashes/newlines, and we must not leave
        # a stray empty directory behind when that validation fires.
        _escape_systemd_value(str(work))

    # All validation passed — now create directories and generate files.
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Scaffolding headless agent '{name}' ({platform_choice})...")

    if platform_choice == "macos":
        # launchd: create logs directory and plist
        logs_dir = work / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        plist_name = f"com.gptme.{name}.plist"
        _write_file(
            out_dir / plist_name,
            _generate_launchd_plist(
                name=name,
                model=model,
                work_dir=str(work),
                timer_schedule=timer_schedule,
            ),
            force=force,
        )
    else:
        # systemd: service unit + optional timer
        # Apply systemd-specific escaping only in this branch — these chars
        # are forbidden in systemd unit values but are valid in launchd plists.
        escaped_work_dir = _escape_systemd_value(str(work))
        escaped_exec_work_dir = _escape_systemd_exec_value(str(work))
        escaped_model = _escape_systemd_value(model)
        _write_file(
            out_dir / f"{name}.service",
            SYSTEMD_SERVICE_TEMPLATE.format(
                name=name,
                model=escaped_model,
                work_dir=escaped_work_dir,
                exec_work_dir=escaped_exec_work_dir,
            ),
            force=force,
        )

        # Timer (skip for on-demand; remove stale timer only with --force)
        if timer_schedule == "on-demand":
            stale_timer = out_dir / f"{name}.timer"
            if stale_timer.exists():
                if force:
                    # Disable the unit BEFORE removing the file so systemd can
                    # resolve and stop the unit while the file is still present.
                    click.echo(
                        f"  Disabling loaded timer unit {name}.timer (on-demand mode, --force)"
                    )
                    try:
                        result = subprocess.run(
                            [
                                "systemctl",
                                "--user",
                                "disable",
                                "--now",
                                f"{name}.timer",
                            ],
                            check=False,  # best-effort; unit may not be enabled
                            timeout=_SYSTEMCTL_TIMEOUT_SEC,
                        )
                    except OSError as exc:
                        click.echo(
                            f"  Warning: cannot run systemctl to disable {name}.timer: "
                            f"{exc}. Remove the timer file manually and run "
                            "'systemctl --user daemon-reload' to clear the unit.",
                            err=True,
                        )
                    except subprocess.TimeoutExpired:
                        click.echo(
                            f"  Warning: systemctl --user disable --now {name}.timer "
                            f"timed out after {_SYSTEMCTL_TIMEOUT_SEC}s; leaving the "
                            "timer file in place. Disable it manually.",
                            err=True,
                        )
                    else:
                        if result.returncode != 0:
                            click.echo(
                                f"  Warning: could not disable {name}.timer "
                                "(unit may not be enabled/loaded). "
                                "Periodic runs may continue until the next daemon-reload.",
                                err=True,
                            )
                        else:
                            stale_timer.unlink()
                            click.echo(f"  Removed stale timer file {stale_timer}")
                else:
                    click.echo(
                        f"  Warning: existing timer {stale_timer} preserved (use --force to remove)."
                    )
                    click.echo(
                        "  The scheduled runs remain active until you disable the timer:"
                    )
                    click.echo(f"    systemctl --user disable --now {name}.timer")
        else:
            schedule = SCHEDULES.get(timer_schedule, SCHEDULES["daily"])
            _write_file(
                out_dir / f"{name}.timer",
                SYSTEMD_TIMER_TEMPLATE.format(name=name, schedule=schedule),
                force=force,
            )

    # Work directory contents
    _write_file(
        work / "gptme.toml",
        GPTME_TOML_TEMPLATE.format(name=name),
        force=force,
    )
    _write_file(
        work / "AGENTS.md",
        AGENTS_MD_TEMPLATE.format(name=name),
        force=force,
    )
    _write_file(
        work / "prompt.md",
        PROMPT_MD_TEMPLATE.format(name=name),
        force=force,
    )

    # Startup script
    startup = work / "gptme-agent-run.sh"
    _write_file(
        startup,
        STARTUP_SCRIPT_TEMPLATE.format(name=name, work_dir=shlex.quote(str(work))),
        force=force,
    )
    startup.chmod(0o755)

    if enable_health_check:
        health_script = work / "health-check.py"
        if _write_file(
            health_script,
            HEALTH_CHECK_TEMPLATE.format(name=name),
            force=force,
        ):
            health_script.chmod(0o755)
        _write_file(
            work / "HEALTH.md",
            HEALTH_README_TEMPLATE.format(name=name),
            force=force,
        )

    click.echo()
    click.echo(f"✅ Initialized headless agent '{name}'")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  $EDITOR {work / 'prompt.md'}   # what the agent does each run")

    if platform_choice == "macos":
        plist_file = out_dir / f"com.gptme.{name}.plist"
        click.echo(f'  launchctl load "{plist_file}"')
        click.echo(f"  launchctl start com.gptme.{name}")
        click.echo(
            f"  log stream --predicate 'eventMessage contains[cd] \"{name}\"'  # follow logs"
        )
    else:
        click.echo("  systemctl --user daemon-reload")
        if timer_schedule != "on-demand":
            click.echo(f"  systemctl --user enable --now {name}.timer")
        click.echo(f"  systemctl --user start {name}.service   # first run")
        click.echo(f"  journalctl --user -u {name}.service -f  # follow logs")

    click.echo()
    click.echo("For the full batteries-included agent workspace template, see:")
    click.echo("  https://github.com/gptme/gptme-agent-template")


main = cli


if __name__ == "__main__":
    cli()
