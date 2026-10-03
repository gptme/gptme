"""
Restart the gptme process.

This tool allows restarting gptme from within a conversation, which can be useful
for applying configuration changes, reloading tools, or recovering from state issues.
"""

import importlib.util
import ipaddress
import logging
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

from ..hooks.confirm import confirm
from ..message import Message
from ..server.token_file import get_token_file, read_token_file
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Track if we've already triggered a restart in this process
_triggered_restart = False


# Flags that take a value (next argument)
_FLAGS_WITH_VALUES = {
    "--name",
    "-m",
    "--model",
    "-w",
    "--workspace",
    "--agent-path",
    "--system",
    "-t",
    "--tools",
    "--tool-format",
    "--context-mode",
    "--context-include",
    "--output-schema",
}


# ── Interface switching (/restart cli|tui|web) ───────────────────────────

Interface = Literal["cli", "tui"]
Target = Literal["cli", "tui", "web"]

#: Targets accepted by ``/restart <target>``, with completion descriptions.
RESTART_TARGETS: dict[str, str] = {
    "cli": "Restart this conversation in the plain CLI (gptme)",
    "tui": "Restart this conversation in the TUI (gptme-tui)",
    "web": "Close this session and open the conversation in the web UI",
}

DEFAULT_SERVER_PORT = 5700

# Launch-time flags that aren't persisted in the conversation's config.toml
# and mean the same in both interfaces, so they carry over when switching (as
# their long form). Everything else is either persisted (model, tools, tool
# format, agent path, allow-hosts; the workspace is the inherited cwd) or
# specific to one interface (--inline, --show-hidden, ...) and dropped.
_CARRY_OVER: dict[str, str] = {
    "-y": "--no-confirm",
    "--no-confirm": "--no-confirm",
    "-v": "--verbose",
    "--verbose": "--verbose",
}

# Per-interface flags that consume the next argument, needed to walk the
# *source* argv without mistaking a value for a flag. Note the `-n` ambiguity:
# `gptme -n` is --non-interactive (boolean), `gptme-tui -n` is --name.
_VALUE_FLAGS: dict[Interface, frozenset[str]] = {
    "cli": frozenset(
        {
            "--name",
            "-m",
            "--model",
            "-w",
            "--workspace",
            "--agent-path",
            "--output-format",
            "--system",
            "-t",
            "--tools",
            "--agent-profile",
            "--tool-format",
            "--context",
            "--context-include",
            "--output-schema",
            "--allow-hosts",
        }
    ),
    "tui": frozenset(
        {
            "-n",
            "--name",
            "-m",
            "--model",
            "-w",
            "--workspace",
            "-t",
            "--tools",
            "--tool-format",
        }
    ),
}
# Options with an optional value (`gptme-tui -r [NAME]`): a following
# non-flag argument is their value.
_OPTIONAL_VALUE_FLAGS: dict[Interface, frozenset[str]] = {
    "cli": frozenset(),
    "tui": frozenset({"-r", "--resume"}),
}

# interface: (console script, module for `python -m`)
_PROGRAMS: dict[Interface, tuple[str, str]] = {
    "cli": ("gptme", "gptme"),
    "tui": ("gptme-tui", "gptme.tui.main"),
}


class RestartError(Exception):
    """A requested restart/switch can't be performed; nothing was changed."""


def parse_restart_target(args: list[str]) -> Target | None:
    """Parse ``/restart`` arguments into a target (None: same interface)."""
    if not args:
        return None
    if len(args) > 1 or args[0] not in RESTART_TARGETS:
        raise RestartError(
            f"Unknown restart target: {' '.join(args)!r}. "
            f"Usage: /restart [{'|'.join(RESTART_TARGETS)}]"
        )
    target: Target = args[0]  # type: ignore[assignment]
    return target


def complete_restart(partial: str, prev_args: list[str]) -> list[tuple[str, str]]:
    """Completer for ``/restart`` arguments."""
    if prev_args:
        return []
    return [(t, d) for t, d in RESTART_TARGETS.items() if t.startswith(partial)]


def check_interface_available(target: Interface) -> None:
    """Raise RestartError if ``target`` can't run in this installation."""
    if target == "tui" and importlib.util.find_spec("textual") is None:
        raise RestartError(
            "The TUI requires the 'textual' package. "
            "Install with: pipx install 'gptme[tui]'"
        )


def _program_argv(target: Interface) -> list[str]:
    """Command prefix that launches ``target`` from this same installation.

    Prefers the console script next to the running interpreter (same venv),
    else ``python -m``. PATH isn't consulted: it may resolve to a different
    gptme install than the one running now.
    """
    script, module = _PROGRAMS[target]
    bindir = Path(sys.executable).parent
    for name in (script, f"{script}.exe"):
        candidate = bindir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    return [sys.executable, "-m", module]


def _carried_flags(argv: list[str], source: Interface) -> list[str]:
    """Flags in the source interface's argv that carry over to the other."""
    value_flags = _VALUE_FLAGS[source]
    optional_value_flags = _OPTIONAL_VALUE_FLAGS[source]
    carried: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg == "--":
            break  # the rest are prompts
        if not arg.startswith("-") or arg == "-" or "=" in arg:
            continue  # prompt, or --flag=value (none of those carry over)
        if arg in value_flags:
            i += 1  # skip its value
            continue
        if arg in optional_value_flags:
            if i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        # combined short boolean flags, e.g. `-yv`
        flags = [arg] if arg.startswith("--") else [f"-{c}" for c in arg[1:]]
        for flag in flags:
            mapped = _CARRY_OVER.get(flag)
            if mapped and mapped not in carried:
                carried.append(mapped)
    return carried


def build_restart_argv(
    target: Interface,
    source: Interface,
    conversation_name: str,
    argv: list[str] | None = None,
) -> list[str]:
    """Build the argv that reopens ``conversation_name`` in another interface.

    Model, tools, tool format etc. are persisted in the conversation's
    config.toml and the workspace is the cwd the new process inherits, so
    only non-persisted, interface-neutral flags are carried over.
    """
    check_interface_available(target)
    argv = sys.argv if argv is None else argv
    return [
        *_program_argv(target),
        *_carried_flags(argv, source),
        "--name",
        conversation_name,
    ]


def get_server_url() -> str:
    """Base URL of the local gptme-server.

    ``GPTME_SERVER_URL`` if set, else built from the environment the server
    itself reads (``GPTME_SERVER_HOST``/``GPTME_SERVER_PORT``, default
    127.0.0.1:5700).
    """
    if url := os.environ.get("GPTME_SERVER_URL"):
        return url.rstrip("/")
    host = os.environ.get("GPTME_SERVER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"  # a wildcard bind is reachable on loopback
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("GPTME_SERVER_PORT") or str(DEFAULT_SERVER_PORT)
    return f"http://{host}:{port}"


def _http_status(
    url: str, token: str | None = None, timeout: float = 2.0
) -> int | None:
    """GET ``url`` and return the HTTP status, or None if unreachable."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (OSError, ValueError):  # URLError, timeouts, bad URL
        return None


def _is_loopback_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def prepare_web_switch(conversation_name: str) -> str:
    """Check the web UI can open this conversation and return its URL.

    Raises RestartError (changing nothing) if no gptme-server is reachable,
    it doesn't serve the web UI, or it can't serve this conversation with the
    credentials the browser will get.
    """
    base = get_server_url()
    if _http_status(f"{base}/api/v2/version") != 200:
        raise RestartError(
            f"No gptme-server reachable at {base}. Start one with `gptme-server` "
            "(or set GPTME_SERVER_URL), then retry."
        )
    if _http_status(f"{base}/") != 200:
        raise RestartError(
            f"The gptme-server at {base} doesn't serve the web UI "
            "(see https://gptme.org/docs/webui.html)."
        )

    # Ask the destination server itself, with the credentials the browser
    # will get: it must find the conversation (same logs dir) and accept them.
    conv = quote(conversation_name, safe="")
    # Fall back to the token a local gptme-server shares when it generated one.
    # Only send it to a loopback server: GPTME_SERVER_URL may point elsewhere.
    token = os.environ.get("GPTME_SERVER_TOKEN") or (
        read_token_file() if _is_loopback_url(base) else None
    )
    status = _http_status(f"{base}/api/v2/conversations/{conv}?limit=1", token=token)
    if status in (401, 403):
        if not token:
            raise RestartError(
                f"The gptme-server at {base} requires authentication. Set "
                "GPTME_SERVER_TOKEN to its token (shown by `gptme-server token`), "
                "or start it with that variable set, then retry."
            )
        if os.environ.get("GPTME_SERVER_TOKEN"):
            raise RestartError(
                f"The gptme-server at {base} rejected GPTME_SERVER_TOKEN."
            )
        raise RestartError(
            f"The gptme-server at {base} rejected the token in {get_token_file()} "
            "(written by another gptme-server?). Set GPTME_SERVER_TOKEN to its "
            "token, then retry."
        )
    if status == 404:
        raise RestartError(
            f"The gptme-server at {base} can't find conversation "
            f"{conversation_name!r} (is it using a different logs directory?)."
        )
    if status != 200:
        raise RestartError(
            f"The gptme-server at {base} couldn't load conversation "
            f"{conversation_name!r} (HTTP {status})."
        )

    # The fragment (never sent to the server) makes the web UI select this
    # server, and sign in to it when a token is set: the same form
    # `gptme-server` prints. The UI strips it from the address bar.
    fragment = {"baseUrl": base}
    if token:
        fragment["userToken"] = token
    return f"{base}/chat/{conv}#{urlencode(fragment, quote_via=quote)}"


def open_web(url: str) -> None:
    """Release this session (conversation lock) and open ``url`` in a browser.

    The caller must exit right after: once the web UI has the conversation,
    this process must not write to it.
    """
    import webbrowser

    _cleanup_before_handover()
    display_url = url.split("#", 1)[0]  # don't echo the token on the happy path
    if webbrowser.open(url):
        print(f"Opened in the web UI: {display_url}")
    else:
        # The browser never opened, so the user has to copy the URL by hand —
        # that only works with the fragment (base URL + token) the web UI needs.
        print(f"Couldn't open a browser. Open this URL in the web UI:\n{url}")


def _cleanup_before_handover() -> None:
    """Run atexit handlers (releasing the LogManager lock) and flush output."""
    import atexit

    try:
        atexit._run_exitfuncs()
    except Exception as e:
        logger.warning(f"Error during atexit cleanup: {e}")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def _filter_same_interface_args(argv: list[str]) -> list[str]:
    """Rebuild the current command line for a same-interface restart.

    Drops prompts and flags whose values are persisted in the conversation's
    config.toml; keeps everything else.
    """
    # Filter out positional arguments (prompts) since they are already in the
    # conversation log (Issue #1011).
    # Use argv[0] (gptme script) not sys.executable (python interpreter)
    filtered_args = [argv[0]]  # Keep script name
    skip_next = False
    i = 1

    skip_value = False  # True when we need to skip the next value (for --name)
    while i < len(argv):
        arg = argv[i]

        if skip_next:
            # This arg is a value for the previous flag, keep it
            filtered_args.append(arg)
            skip_next = False
            i += 1
            continue

        if skip_value:
            # This arg is a value for --name/--resume, skip it
            skip_value = False
            i += 1
            continue

        if arg.startswith("-"):
            # This is a flag
            # Flags that are persisted to the chat config and loaded on resume
            # These should NOT be re-passed on restart since they're in the conversation config
            _PERSISTED_FLAGS = {
                "--name",  # we add explicitly with conversation name
                "-r",
                "--resume",  # boolean flags for resuming
                "-m",
                "--model",  # model is persisted
                "-t",
                "--tools",  # tools allowlist is persisted
                "--tool-format",  # tool format is persisted
                "--stream",
                "--no-stream",  # streaming preference
                "-n",
                "--non-interactive",  # interactive mode
                "--agent-path",  # agent path
                "-w",
                "--workspace",  # workspace path
                "--context-mode",  # context mode
                "--context-include",  # context includes
            }
            if arg in _PERSISTED_FLAGS:
                # Skip flags that are persisted to chat config
                if arg in _FLAGS_WITH_VALUES:
                    skip_value = True  # Also skip the value
                # Don't add to filtered_args
            elif arg in _FLAGS_WITH_VALUES:
                # Flag takes a value, keep flag and mark to keep next arg
                filtered_args.append(arg)
                skip_next = True
            elif "=" in arg:
                # Flag with inline value (--flag=value), keep it
                # But skip if it's a persisted flag (loaded from chat config)
                flag_name = arg.split("=")[0]
                if flag_name not in _PERSISTED_FLAGS:
                    filtered_args.append(arg)
            else:
                # Boolean flag (no value), keep it
                filtered_args.append(arg)
        # else: positional argument (prompt) - skip it (Issue #1011)

        i += 1

    return filtered_args


def _do_restart(
    conversation_name: str | None = None,
    target: Interface | None = None,
    source: Interface = "cli",
) -> None:
    """Restart gptme by manually triggering cleanup and then execing.

    This approach:
    1. Builds the new command line (raising RestartError before any cleanup)
    2. Manually triggers all atexit handlers, releasing the LogManager lock
    3. Directly replaces the current process with a new gptme instance
    4. Preserves stdin/stdout/stderr so terminal connection is maintained

    Args:
        conversation_name: The name of the current conversation to resume
        target: Interface to restart into; None (or ``source``) restarts the
            current one with its original command line
        source: The interface running now
    """
    if target is None or target == source:
        restart_args = _filter_same_interface_args(sys.argv)
        if conversation_name:
            # Add explicit --name to resume this conversation
            restart_args.extend(["--name", conversation_name])
    else:
        if not conversation_name:
            raise RestartError("No conversation to reopen in another interface.")
        restart_args = build_restart_argv(target, source, conversation_name)

    logger.info(f"Restarting with: {' '.join(restart_args)}")

    _cleanup_before_handover()

    # Replace current process with new gptme instance
    # stdin/stdout/stderr are preserved, so terminal connection is maintained
    os.execv(restart_args[0], restart_args)


def execute_restart(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute restart by confirming intent.

    The actual restart happens in the restart_hook (GENERATION_PRE),
    after all messages have been saved to the log.
    """
    global _triggered_restart

    if not confirm("Restart gptme? This will exit and restart the process."):
        yield Message("system", "Restart cancelled.")
        return

    # Mark that restart has been confirmed
    _triggered_restart = True

    # Just yield a confirmation message
    # The actual restart will happen in the GENERATION_PRE hook
    yield Message("system", "Restarted gptme. Resuming conversation...")


def restart_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """
    Hook that detects restart tool call and performs the restart.

    Runs at GENERATION_PRE (before generating response) to restart
    immediately after the restart tool is called.

    By this point, all messages (including the assistant's restart message
    and the system confirmation) have been saved to the log.
    """
    # Make function a generator for type checking
    if False:
        yield

    if not messages:
        return

    # Look for restart tool call in the last assistant message
    last_assistant_msg = next(
        (m for m in reversed(messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    # Check if the assistant called the restart tool
    from .base import ToolUse

    global _triggered_restart

    # Only proceed if restart was confirmed (flag set by execute_restart)
    if not _triggered_restart:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    for tool_use in tool_uses:
        if tool_use.tool == "restart":
            logger.info("Restart confirmed and detected, restarting now...")

            # Get conversation name
            conversation_name = None
            try:
                from ..logmanager import LogManager

                log_manager = LogManager.get_current_log()
                if log_manager:
                    conversation_name = log_manager.logfile.parent.name
                    logger.info(f"Restarting with conversation: {conversation_name}")

                    # Ensure everything is synced to disk
                    log_manager.write(sync=True)
            except Exception as e:
                logger.warning(f"Error preparing restart: {e}")

            # Perform the restart
            _do_restart(conversation_name)

            # This line should never be reached
            sys.exit(1)


tool = ToolSpec(
    name="restart",
    desc="Restart the gptme process",
    instructions="""
Restart the gptme process, useful for:
- Applying configuration changes that require a restart
- Reloading tools after code modifications
- Recovering from state issues
- Testing tool initialization

The restart preserves the current conversation by reloading it from disk.
All command-line arguments are preserved in the new process.

This tool is disabled by default and must be explicitly enabled with `--tools restart`.

### When to use restart

Use after modifying gptme configuration or tool files when a fresh process is needed to apply changes. Don't use as a workaround for unrelated errors — diagnose and fix the root cause instead.
""",
    examples="""
> User: restart gptme to apply the config changes
> Assistant: I'll restart gptme now.
```restart

```
> System: Restarting gptme...
(gptme restarts and conversation continues)

> User: can you restart?
> Assistant: I'll restart the gptme process.
```restart

```
> System: Restarting gptme...
""",
    execute=execute_restart,
    block_types=["restart"],
    disabled_by_default=True,
    hooks={
        "restart": (
            "generation.pre",  # HookType.GENERATION_PRE.value
            restart_hook,
            1000,  # High priority - restart before next generation
        ),
    },
)
