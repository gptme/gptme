"""CLI commands for Claude Code hook installation and execution.

Provides `gptme-util hooks` subcommands for integrating gptme's lesson system
with Claude Code via hooks.

Subcommands:
- ``install``: Register gptme lesson hooks in a Claude Code settings.json
- ``run``: Execute lesson matching for a CC hook event (called by CC itself)
- ``status``: Show current hook installation state
- ``uninstall``: Remove gptme hooks from a Claude Code settings.json
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
import uuid
from pathlib import Path

import click

logger = logging.getLogger(__name__)

# Hook command that CC will invoke
_HOOK_COMMAND = "gptme-util hooks run"

# CC hook event types we register
_USERPROMPTSUBMIT = "UserPromptSubmit"
_PRETOOLUSE = "PreToolUse"

# Tools to match in PreToolUse (same as the existing hook)
_PRETOOLUSE_MATCHER = "Read|Bash|Grep|WebFetch|WebSearch"

# Timeout for hook execution (seconds)
_HOOK_TIMEOUT = 10

# Per-session dedup state (in tmpdir, cleared when system reboots)
_STATE_DIR = Path(tempfile.gettempdir()) / "gptme-lesson-hooks"

# Maximum lessons to inject per event
_MAX_USERPROMPTSUBMIT = 5
_MAX_PRETOOLUSE = 3

# Minimum seconds between PreToolUse lesson matches (throttle)
_PRETOOLUSE_COOLDOWN = 15


@click.group()
def hooks() -> None:
    """Integrate gptme lessons with Claude Code via hooks."""


@hooks.command("install")
@click.option(
    "--workspace",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Workspace directory (must contain gptme.toml). "
    "Defaults to current directory.",
)
@click.option(
    "--global",
    "global_install",
    is_flag=True,
    default=False,
    help="Install into ~/.claude/settings.json instead of workspace settings.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing gptme hook entries if already present.",
)
def install(workspace: Path, global_install: bool, force: bool) -> None:
    """Register gptme lesson injection hooks in a Claude Code settings.json.

    By default installs into WORKSPACE/.claude/settings.json.
    Use --global to install into ~/.claude/settings.json instead.

    After installation, Claude Code will automatically inject relevant gptme
    lessons as additionalContext when the prompt or tool inputs match lesson
    keywords.
    """
    workspace = workspace.resolve()

    if not global_install and not (workspace / "gptme.toml").exists():
        if not force:
            click.echo(
                f"⚠  No gptme.toml found in {workspace}. "
                "This workspace may not have a lessons directory.\n"
                "Continue anyway? (pass --force to skip this check)\n"
                "Hint: run from a directory containing gptme.toml, "
                "or use --global for the user-level settings.",
                err=True,
            )
            sys.exit(1)

    if global_install:
        settings_path = Path.home() / ".claude" / "settings.json"
    else:
        settings_path = workspace / ".claude" / "settings.json"

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing settings
    if settings_path.exists():
        try:
            settings: dict = json.loads(settings_path.read_text())
        except json.JSONDecodeError as e:
            click.echo(f"❌ Failed to parse {settings_path}: {e}", err=True)
            sys.exit(1)
    else:
        settings = {}

    hooks_cfg: dict = settings.setdefault("hooks", {})

    # Check if already installed (both hooks must be present to skip)
    prompt_installed = _is_hook_installed(hooks_cfg, _USERPROMPTSUBMIT)
    pretooluse_installed = _is_hook_installed(hooks_cfg, _PRETOOLUSE)
    if prompt_installed and pretooluse_installed and not force:
        click.echo(
            f"ℹ  gptme lesson hooks already present in {settings_path}.\n"
            "   Use --force to overwrite."
        )
        return

    # Build hook entries
    prompt_entry = {
        "hooks": [
            {
                "type": "command",
                "command": _HOOK_COMMAND,
                "timeout": _HOOK_TIMEOUT,
            }
        ]
    }
    pretooluse_entry = {
        "matcher": _PRETOOLUSE_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": _HOOK_COMMAND,
                "timeout": _HOOK_TIMEOUT,
            }
        ],
    }

    # Inject or replace
    _upsert_hook_entry(hooks_cfg, _USERPROMPTSUBMIT, prompt_entry, force)
    _upsert_hook_entry(hooks_cfg, _PRETOOLUSE, pretooluse_entry, force)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    click.echo(f"✅ gptme lesson hooks installed into {settings_path}")
    click.echo()
    click.echo("Hooks registered:")
    click.echo(f"  • UserPromptSubmit → {_HOOK_COMMAND}")
    click.echo(f"  • PreToolUse (matcher: {_PRETOOLUSE_MATCHER}) → {_HOOK_COMMAND}")
    click.echo()
    click.echo(
        "Lessons will be injected as additionalContext when your prompt or tool\n"
        "inputs match lesson keywords from your gptme workspace."
    )


@hooks.command("uninstall")
@click.option(
    "--workspace",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Workspace directory. Defaults to current directory.",
)
@click.option(
    "--global",
    "global_install",
    is_flag=True,
    default=False,
    help="Remove from ~/.claude/settings.json.",
)
def uninstall(workspace: Path, global_install: bool) -> None:
    """Remove gptme lesson injection hooks from a Claude Code settings.json."""
    workspace = workspace.resolve()

    if global_install:
        settings_path = Path.home() / ".claude" / "settings.json"
    else:
        settings_path = workspace / ".claude" / "settings.json"

    if not settings_path.exists():
        click.echo(f"ℹ  No settings file found at {settings_path}.")
        return

    try:
        settings: dict = json.loads(settings_path.read_text())
    except json.JSONDecodeError as e:
        click.echo(f"❌ Failed to parse {settings_path}: {e}", err=True)
        sys.exit(1)

    hooks_cfg = settings.get("hooks", {})
    changed = False

    for event in (_USERPROMPTSUBMIT, _PRETOOLUSE):
        entries = hooks_cfg.get(event, [])
        new_entries = [e for e in entries if not _is_gptme_entry(e)]
        if len(new_entries) < len(entries):
            hooks_cfg[event] = new_entries
            changed = True

    if not changed:
        click.echo(f"ℹ  No gptme hooks found in {settings_path}.")
        return

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    click.echo(f"✅ gptme lesson hooks removed from {settings_path}")


@hooks.command("status")
@click.option(
    "--workspace",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Workspace directory. Defaults to current directory.",
)
def status(workspace: Path) -> None:
    """Show gptme hook installation status for a workspace."""
    workspace = workspace.resolve()

    paths_to_check = [
        ("workspace", workspace / ".claude" / "settings.json"),
        ("global", Path.home() / ".claude" / "settings.json"),
    ]

    for scope, settings_path in paths_to_check:
        click.echo(f"[{scope}] {settings_path}")
        hooks_cfg: dict = {}
        if not settings_path.exists():
            click.echo("  ⚪ settings.json not found")
        else:
            try:
                settings: dict = json.loads(settings_path.read_text())
                hooks_cfg = settings.get("hooks", {})
            except json.JSONDecodeError:
                click.echo("  ❌ settings.json parse error")

        for event in (_USERPROMPTSUBMIT, _PRETOOLUSE):
            installed = _is_hook_installed(hooks_cfg, event)
            mark = "✅" if installed else "⚪"
            click.echo(f"  {mark} {event}")
        click.echo()


@hooks.command("run")
@click.option(
    "--workspace",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Workspace directory (must contain gptme.toml). "
    "Overrides auto-detection from cwd. "
    "Useful for testing or when running outside the workspace.",
    envvar="GPTME_WORKSPACE",
)
def run(workspace: Path | None = None) -> None:
    """Execute gptme lesson matching for a Claude Code hook event.

    Reads the CC hook event JSON from stdin and prints an additionalContext
    response. This is the command registered in settings.json by 'hooks install'.

    Event types handled:
    - UserPromptSubmit: matches against the user's prompt text
    - PreToolUse: matches against tool name, inputs, and recent transcript

    Output JSON format:
    {"additionalContext": "...", "continue": true}

    Lessons are matched using gptme's keyword/pattern system and workspace's
    gptme.toml [lessons] dirs. Already-injected lessons are tracked per session
    to avoid duplicates.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            # No input — return empty (CC may call hooks with empty stdin)
            _output_empty()
            return
        hook_input: dict = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to parse hook input: %s", e)
        _output_empty()
        return

    event_type = hook_input.get("hook_event_name", _USERPROMPTSUBMIT)
    # When CC omits session_id, generate a unique fallback so each anonymous
    # invocation gets its own isolated dedup state rather than sharing "unknown".
    session_id = hook_input.get("session_id") or f"anon-{uuid.uuid4().hex[:12]}"

    # Determine text to match against
    if event_type == _USERPROMPTSUBMIT:
        match_text = hook_input.get("prompt", "")
        max_lessons = _MAX_USERPROMPTSUBMIT
    elif event_type == _PRETOOLUSE:
        # Throttle: avoid matching too frequently during a session
        if _is_throttled(session_id):
            _output_empty()
            return
        match_text = _extract_pretooluse_text(hook_input)
        max_lessons = _MAX_PRETOOLUSE
    else:
        # Unknown event type — pass through
        _output_empty()
        return

    if not match_text.strip():
        _output_empty()
        return

    # Find workspace root (where gptme.toml lives)
    if workspace is not None and not workspace.exists():
        # GPTME_WORKSPACE or --workspace pointed to a stale/deleted path;
        # fall through to auto-detection so the hook always outputs valid JSON.
        logger.debug(
            "Workspace path %s does not exist; falling back to auto-detection",
            workspace,
        )
        workspace = None
    if workspace is None:
        workspace = _find_workspace()
    if workspace is None:
        logger.debug("No gptme.toml found; skipping lesson injection")
        _output_empty()
        return

    # Load lessons
    try:
        from ..lessons.index import LessonIndex
        from ..lessons.matcher import LessonMatcher, MatchContext

        lesson_dirs = _load_lesson_dirs(workspace)
        if not lesson_dirs:
            _output_empty()
            return

        index = LessonIndex(lesson_dirs=lesson_dirs)
        matcher = LessonMatcher()
        ctx = MatchContext(message=match_text)
        results = matcher.match(index.lessons, ctx)
    except Exception as e:
        logger.debug("Lesson matching error: %s", e)
        _output_empty()
        return

    if not results:
        _output_empty()
        return

    # Filter out already-injected lessons
    already_injected = _load_injected(session_id)
    new_results = [r for r in results if str(r.lesson.path) not in already_injected]

    if not new_results:
        _output_empty()
        return

    # Take top N
    new_results = new_results[:max_lessons]

    # Build context text
    parts = []
    for r in new_results:
        header = f"## {r.lesson.title}"
        body = r.lesson.body.strip()
        parts.append(f"{header}\n*Source: {r.lesson.path}*\n\n{body}")

    context = "\n\n---\n\n".join(parts)

    # Update state
    new_injected = already_injected | {str(r.lesson.path) for r in new_results}
    _save_injected(session_id, new_injected)

    # Update throttle timestamp for PreToolUse
    if event_type == _PRETOOLUSE:
        _update_throttle(session_id)

    output = {"additionalContext": context, "continue": True}
    print(json.dumps(output))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_gptme_entry(entry: dict) -> bool:
    """Return True if this hook entry was installed by gptme."""
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if _HOOK_COMMAND in cmd:
            return True
    return False


def _is_hook_installed(hooks_cfg: dict, event: str) -> bool:
    """Return True if a gptme hook entry already exists for this event."""
    return any(_is_gptme_entry(e) for e in hooks_cfg.get(event, []))


def _upsert_hook_entry(
    hooks_cfg: dict, event: str, new_entry: dict, force: bool
) -> None:
    """Insert or replace a gptme hook entry for the given event."""
    entries: list[dict] = hooks_cfg.setdefault(event, [])
    if force:
        # Remove existing gptme entries before inserting the new one
        entries[:] = [e for e in entries if not _is_gptme_entry(e)]
    elif _is_hook_installed(hooks_cfg, event):
        # Already present for this event — skip to avoid duplicate entries
        return
    entries.append(new_entry)


def _output_empty() -> None:
    """Print a no-op response for CC hooks."""
    print(json.dumps({"continue": True}))


def _find_workspace() -> Path | None:
    """Walk up from cwd looking for gptme.toml."""
    cwd = Path.cwd()
    for p in [cwd, *cwd.parents]:
        if (p / "gptme.toml").exists():
            return p
    return None


def _load_lesson_dirs(workspace: Path) -> list[Path]:
    """Read [lessons] dirs from gptme.toml."""
    toml_path = workspace / "gptme.toml"
    if not toml_path.exists():
        return [workspace / "lessons"]
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
        raw_dirs = cfg.get("lessons", {}).get("dirs", ["lessons"])
        return [workspace / d for d in raw_dirs if isinstance(d, str)]
    except Exception:
        return [workspace / "lessons"]


# Session state (per-session injected lessons set)


def _session_state_file(session_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{safe}.json"


def _load_injected(session_id: str) -> set[str]:
    sf = _session_state_file(session_id)
    try:
        return set(json.loads(sf.read_text()).get("injected", []))
    except Exception:
        return set()


def _save_injected(session_id: str, injected: set[str]) -> None:
    sf = _session_state_file(session_id)
    try:
        sf.write_text(json.dumps({"injected": sorted(injected)}))
    except Exception:
        pass


# Throttle helpers


def _throttle_file(session_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / f"{safe}.throttle"


def _is_throttled(session_id: str) -> bool:
    import time

    tf = _throttle_file(session_id)
    try:
        last = float(tf.read_text())
        return (time.time() - last) < _PRETOOLUSE_COOLDOWN
    except Exception:
        return False


def _update_throttle(session_id: str) -> None:
    import time

    tf = _throttle_file(session_id)
    try:
        tf.write_text(str(time.time()))
    except Exception:
        pass


def _extract_pretooluse_text(hook_input: dict) -> str:
    """Extract text to match from a PreToolUse event."""
    parts: list[str] = []

    tool_name = hook_input.get("tool_name", "")
    if tool_name:
        parts.append(tool_name)

    tool_input = hook_input.get("tool_input", {})
    if isinstance(tool_input, dict):
        parts.extend(v for v in tool_input.values() if isinstance(v, str))

    # Recent transcript (assistant + tool output) for context-aware matching
    transcript = hook_input.get("transcript", [])
    recent_text: list[str] = []
    for entry in transcript[-6:]:  # Last 6 entries
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role in ("assistant", "tool") and isinstance(content, str):
            recent_text.append(content[:500])  # Truncate long outputs
        elif role in ("assistant", "tool") and isinstance(content, list):
            recent_text.extend(
                str(block.get("text", ""))[:500]
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )

    parts.extend(recent_text)
    return "\n".join(parts)
