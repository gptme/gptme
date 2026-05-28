"""
Flask application factory for gptme server.
"""

import atexit
import logging
from importlib import resources
from pathlib import Path

import flask
from flask_cors import CORS

logger = logging.getLogger(__name__)

# Resolve static/media paths from the gptme package
_gptme_path_ctx = resources.as_file(resources.files("gptme"))
_root_path = _gptme_path_ctx.__enter__()
static_path = _root_path / "server" / "static"
media_path = _root_path.parent / "media"
atexit.register(_gptme_path_ctx.__exit__, None, None, None)


def create_app(
    cors_origin: str | None = None,
    host: str = "127.0.0.1",
    webui_dir: str | None = None,
) -> flask.Flask:
    """Create the Flask app.

    Args:
        cors_origin: CORS origin(s) to allow. Use '*' to allow all origins.
            A comma-separated string allows multiple origins, e.g.
            "tauri://localhost,http://tauri.localhost". Whitespace around
            entries is ignored.
        webui_dir: Directory to serve the web UI from. When set, overrides the
            legacy webui bundled in the package — point it at a built modern
            webui (``webui/dist`` from the gptme repo) to self-host it.
    """
    serve_path = static_path
    if webui_dir:
        candidate = Path(webui_dir).expanduser()
        if not (candidate / "index.html").is_file():
            raise ValueError(
                f"--webui-dir {candidate} does not contain an index.html; "
                "point it at a built web UI directory (e.g. webui/dist)."
            )
        serve_path = candidate
        logger.info("Serving web UI from %s", serve_path)

    # When serving a custom (Vite-built) webui, set static_url_path='' so that
    # asset URLs like /assets/index.js are served directly from the root of the
    # custom dir instead of requiring a /static/ prefix (Flask's default).
    app = flask.Flask(
        __name__,
        static_folder=serve_path,
        static_url_path="" if webui_dir else "/static",
    )

    # Capture the server's default model from the startup context
    # This is needed because ContextVar doesn't propagate across request contexts
    from ..llm.models import get_default_model, set_default_model

    server_default_model = get_default_model()
    if server_default_model:
        app.config["SERVER_DEFAULT_MODEL"] = server_default_model

        @app.before_request
        def propagate_default_model():
            """Propagate the server's default model to each request's ContextVar."""
            # Only set if not already set in this context
            if get_default_model() is None:
                set_default_model(server_default_model)

    # Register v2 API, workspace API, tasks API, and auth API
    # noreorder
    from .api_v2 import v2_api  # fmt: skip
    from .auth import auth_api  # fmt: skip
    from .tasks_api import tasks_api  # fmt: skip
    from .workspace_api import workspace_api  # fmt: skip

    app.register_blueprint(v2_api)
    app.register_blueprint(auth_api)
    app.register_blueprint(workspace_api)
    app.register_blueprint(tasks_api)

    # Register OpenAPI documentation
    from .openapi_docs import docs_api  # fmt: skip

    app.register_blueprint(docs_api)
    logger.info("OpenAPI documentation available at /api/docs/")

    if cors_origin:
        # Support comma-separated origins so the desktop sidecar can allow
        # multiple known webview origins (tauri://localhost on macOS/Linux,
        # http://tauri.localhost on Windows, etc.) in a single flag.
        origins_list = [o.strip() for o in cors_origin.split(",") if o.strip()]
        if origins_list:
            origins: str | list[str] = (
                origins_list[0] if len(origins_list) == 1 else origins_list
            )
            # Browsers reject credentials with a wildcard origin.
            allow_credentials = "*" not in origins_list
            CORS(
                app,
                resources={
                    r"/api/*": {
                        "origins": origins,
                        "supports_credentials": allow_credentials,
                    }
                },
            )

    # Initialize auth (defaults to local-only, no auth required)
    from .auth import init_auth  # fmt: skip

    init_auth(host=host, display=False)

    # Register static file routes directly on the app
    @app.route("/")
    def root():
        return app.send_static_file("index.html")

    @app.route("/computer")
    def computer():
        # Fall back to index.html for SPAs that don't bundle a computer.html
        static_dir = Path(app.static_folder) if app.static_folder else Path(static_path)
        if (static_dir / "computer.html").is_file():
            return app.send_static_file("computer.html")
        return app.send_static_file("index.html")

    @app.route("/chat")
    def chat():
        return app.send_static_file("index.html")

    @app.route("/favicon.png")
    def favicon():
        return flask.send_from_directory(media_path, "logo.png")

    # Server confirmation hook is now registered via init_hooks(server=True)
    # in server/cli.py

    return app
