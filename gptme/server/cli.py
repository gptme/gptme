import json
import logging
from pathlib import Path

import click
from gptme.config import set_config_from_workspace

from ..init import init, init_logging
from .api import create_app

logger = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
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

    # If no subcommand was provided, default to serve
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


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
    )

    click.echo("Initialization complete, starting server")

    app = create_app(cors_origin=cors_origin)
    app.run(debug=debug, host=host, port=int(port))


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
