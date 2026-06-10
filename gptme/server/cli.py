import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

import click
from click_default_group import DefaultGroup

from gptme.config import set_config_from_workspace

from ..init import init, init_logging
from ..telemetry import init_telemetry, shutdown_telemetry
from .api_v2_sessions import set_shutdown_event
from .app import create_app
from .auth import get_server_token, init_auth
from .constants import _pick_fallback_model

logger = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still alive on this host.

    Uses kill(pid, 0) which sends no signal but checks for the existence and
    permission to signal the target. Returns False if the process is gone or
    if EPERM means the PID was recycled by a different user.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but we can't signal it — likely recycled to a different
        # user. Treat as dead so we don't keep watching a stale PID.
        return False


def _start_parent_death_watcher(
    watch_pid: int | None = None, poll_interval: float = 0.5
) -> None:
    """Spawn a daemon thread that exits the process when a watched PID dies.

    Tauri's macOS Cmd+Q can terminate the parent before its cleanup handlers
    dispatch SIGKILL to sidecars (gptme/gptme#2260). When that happens, the
    kernel reparents the orphan to PID 1 (launchd). We detect parent death and
    self-terminate via SIGTERM so server shutdown still runs.

    If `watch_pid` is given, we watch that specific PID (e.g. the Tauri grand-
    parent PID, which is needed for PyInstaller-bundled servers because the
    PyInstaller bootloader survives parent death and stays our `getppid()`).
    Otherwise we watch our direct parent.
    """
    if watch_pid is None:
        watch_pid = os.getppid()
    if watch_pid <= 1:
        # PID 0/1 means we're already orphaned or run directly under init —
        # there's nothing meaningful to watch.
        return

    initial_pid = watch_pid

    def _watcher() -> None:
        while True:
            time.sleep(poll_interval)
            if not _pid_alive(initial_pid):
                logger.warning(
                    "Watched PID %d is gone, shutting down gptme-server",
                    initial_pid,
                )
                # Send SIGTERM to ourselves so Flask's signal handlers run and
                # the `finally: shutdown_telemetry()` block fires.
                os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=_watcher, name="parent-death-watcher", daemon=True)
    thread.start()


@click.group(cls=DefaultGroup, default="serve", default_if_no_args=True)
def main():
    """gptme server commands."""
    # if flask not installed, ask the user to install `server` extras
    try:
        __import__("flask")
    except ImportError:
        logger.error(
            "gptme installed without needed extras for server. "
            "Install them with `pip install gptme[server]`"
        )
        exit(1)


@main.command("serve")
@click.option("--debug", is_flag=True, help="Debug mode")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.option(
    "--model",
    default=None,
    help="Model to use by default, can be overridden in each request.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    envvar="GPTME_SERVER_HOST",
    help="Host to bind the server to.",
)
@click.option(
    "--port",
    default=5700,
    type=int,
    envvar="GPTME_SERVER_PORT",
    help="Port to run the server on.",
)
@click.option("--tools", default=None, help="Tools to enable, comma separated.")
@click.option(
    "--cors-origin",
    default=None,
    help=(
        "CORS origin(s) to allow. Use '*' to allow all origins. Pass a "
        "comma-separated list to allow multiple origins, e.g. "
        "'https://chat.gptme.org,tauri://localhost,http://tauri.localhost'. "
        "Set this to the origin of the web UI you load (for the hosted UI, "
        "'https://chat.gptme.org')."
    ),
)
@click.option(
    "--webui-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    envvar="GPTME_WEBUI_DIR",
    help=(
        "Directory containing a web UI build (e.g. the modern React webui's "
        "dist/) to serve instead of the bundled legacy UI. Can also be set "
        "via the GPTME_WEBUI_DIR environment variable."
    ),
)
@click.option(
    "--exit-on-parent-death",
    is_flag=True,
    default=False,
    help=(
        "Exit when the parent process dies. Useful when run as a sidecar "
        "(e.g. by gptme-tauri) to avoid orphaned servers when the parent "
        "exits without cleaning up children (gptme/gptme#2260)."
    ),
)
@click.option(
    "--watch-pid",
    type=int,
    default=None,
    help=(
        "PID to watch for liveness. If the PID disappears the server exits. "
        "Used by gptme-tauri to pass its own PID so PyInstaller-bundled servers "
        "can detect Tauri exit even when the bootloader survives reparenting."
    ),
)
@click.option(
    "--graceful-timeout",
    type=int,
    default=30,
    help=(
        "Seconds to wait for active SSE streams to drain after SIGTERM before "
        "forcing exit. Active streams receive a 'shutdown' event and are given "
        "this long to complete. Default: 30. Set to 0 to disable graceful drain "
        "(immediate exit on SIGTERM)."
    ),
)
def serve(
    debug: bool,
    verbose: bool,
    model: str | None,
    host: str,
    port: int,
    tools: str | None,
    cors_origin: str | None,
    webui_dir: Path | None,
    exit_on_parent_death: bool,
    watch_pid: int | None,
    graceful_timeout: int = 30,
):  # pragma: no cover
    """
    Starts a server and web UI for gptme.

    Note that this is very much a work in progress, and is not yet ready for normal use.
    """
    init_logging(verbose)
    set_config_from_workspace(Path.cwd())

    if exit_on_parent_death or watch_pid is not None:
        _start_parent_death_watcher(watch_pid=watch_pid)

    # Try to initialize with provided/configured model
    # If init fails due to missing model/API keys, use fallback
    try:
        init(
            model,
            interactive=False,
            tool_allowlist=None if tools is None else tools.split(","),
            tool_format="markdown",
            server=True,
        )
    except (ValueError, KeyError) as e:
        error_msg = str(e)
        is_config_error = (
            "No API key found" in error_msg
            or "No model specified" in error_msg
            or "not set in env or config" in error_msg
        )

        if not is_config_error:
            raise

        # Handle model configuration errors with fallback.
        # Pick a fallback that matches an available provider so we don't try
        # (and fail) to use Anthropic when the user only has e.g. OpenAI keys.
        fallback_model = _pick_fallback_model()
        logger.warning(
            f"No default model configured. Using fallback: {fallback_model}. "
            "Set MODEL environment variable or use --model flag for explicit configuration."
        )
        # require_llm=False: if the fallback provider also has no API key
        # (e.g. first-run Tauri with no keys configured), start the server
        # in degraded mode so the user can configure a provider via the UI.
        init(
            fallback_model,
            interactive=False,
            tool_allowlist=None if tools is None else tools.split(","),
            tool_format="markdown",
            server=True,
            require_llm=False,
        )

    # Initialize telemetry (server is API/WebUI driven, not CLI interactive)
    init_telemetry(
        service_name="gptme-server",
        enable_flask_instrumentation=True,
        interactive=None,
    )

    click.echo("Initialization complete, starting server")

    # Initialize authentication and display token
    init_auth(host=host, display=True)

    app = create_app(cors_origin=cors_origin, host=host, webui_dir=webui_dir)

    # Graceful shutdown: when SIGTERM arrives, signal all SSE streams to drain
    # (they check _shutdown_event on their next wait cycle) and call Flask's
    # shutdown to stop accepting new requests. We then wait for either the
    # Flask shutdown to complete or for the graceful_timeout to elapse.
    _graceful_done = threading.Event()

    def _sigterm_handler(signum: int, frame) -> None:
        logger.info("SIGTERM received, initiating graceful shutdown...")
        set_shutdown_event()
        app.shutdown_gracefully()
        _graceful_done.set()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        # Run Flask in a background thread so we can wait for shutdown in the
        # main thread. threaded=True is required for SSE streams to not block
        # each other.
        def _run_flask() -> None:
            app.run(debug=debug, host=host, port=port, threaded=True)

        _flask_thread = threading.Thread(
            target=_run_flask, name="flask-app", daemon=True
        )
        _flask_thread.start()

        click.echo(
            f"Server running on {host}:{port} "
            f"(graceful-timeout={graceful_timeout}s, Ctrl+C to stop)"
        )

        # Wait for SIGTERM-driven shutdown or Ctrl+C
        _graceful_done.wait(timeout=graceful_timeout if graceful_timeout > 0 else None)
        if not _graceful_done.is_set():
            logger.info("Graceful-timeout reached, exiting...")
    finally:
        shutdown_telemetry()


@main.command("token")
def show_token():
    """Display the server authentication token."""
    token = get_server_token()
    if token:
        click.echo("=" * 60)
        click.echo("gptme-server Authentication Token")
        click.echo("=" * 60)
        click.echo(f"Token: {token}")
        click.echo("")
        click.echo("Use this token in the Authorization header:")
        click.echo(f"  Authorization: Bearer {token}")
        click.echo("=" * 60)
    else:
        click.echo("=" * 60)
        click.echo("gptme-server Authentication")
        click.echo("=" * 60)
        click.echo("Authentication is DISABLED (no token configured)")
        click.echo("")
        click.echo(
            "To enable authentication, set the GPTME_SERVER_TOKEN environment variable:"
        )
        click.echo("  GPTME_SERVER_TOKEN=your-secret-token gptme-server serve")
        click.echo("=" * 60)


@main.command("openapi")
@click.option("-o", "--output", default="openapi.json", help="Output file path")
def generate_openapi(output: str):
    """Generate OpenAPI specification without starting server."""
    app = create_app()
    with app.app_context():
        from .openapi_docs import generate_openapi_spec

        spec = generate_openapi_spec()

        with open(output, "w") as f:
            json.dump(spec, f, indent=2)

        click.echo(f"OpenAPI specification generated: {output}")


if __name__ == "__main__":
    main()
