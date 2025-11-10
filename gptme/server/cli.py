import json
import logging
from pathlib import Path

import click
from click_default_group import DefaultGroup

from gptme.config import set_config_from_workspace

from ..init import init, init_logging
from ..telemetry import init_telemetry, shutdown_telemetry
from .api import create_app
from .auth import get_server_token, init_auth

logger = logging.getLogger(__name__)


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
    help="Host to bind the server to.",
)
@click.option(
    "--port",
    default="5700",
    help="Port to run the server on.",
)
@click.option("--tools", default=None, help="Tools to enable, comma separated.")
@click.option(
    "--cors-origin",
    default=None,
    help="CORS origin to allow. Use '*' to allow all origins.",
)
def serve(
    debug: bool,
    verbose: bool,
    model: str | None,
    host: str,
    port: str,
    tools: str | None,
    cors_origin: str | None,
):  # pragma: no cover
    """
    Starts a server and web UI for gptme.

    Note that this is very much a work in progress, and is not yet ready for normal use.
    """
    init_logging(verbose)
    set_config_from_workspace(Path.cwd())
    init(
        model,
        interactive=False,
        tool_allowlist=None if tools is None else tools.split(","),
        tool_format="markdown",
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

    app = create_app(cors_origin=cors_origin, host=host)

    try:
        app.run(debug=debug, host=host, port=int(port))
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
