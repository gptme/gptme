"""
Flask application factory for gptme server.
"""

import atexit
import logging
import os
from importlib import resources
from pathlib import Path

import flask
from flask_cors import CORS

logger = logging.getLogger(__name__)

# Resolve static/media paths from the gptme package
_gptme_path_ctx = resources.as_file(resources.files("gptme"))
_root_path = _gptme_path_ctx.__enter__()
static_path = _root_path / "server" / "static"
# Bundled modern webui (populated by `make bundle-webui` or the release workflow)
_bundled_webui_path = _root_path / "server" / "webui-dist"
media_path = _root_path.parent / "media"
atexit.register(_gptme_path_ctx.__exit__, None, None, None)


def _resolve_static_folder(webui_dir: str | Path | None = None) -> Path:
    """Resolve which directory the web UI is served from.

    Precedence: explicit ``webui_dir`` argument > ``GPTME_WEBUI_DIR`` env var >
    bundled modern webui (``gptme/server/webui-dist/``) >
    the embedded legacy static bundle. A configured directory must exist so
    that a typo fails loudly at startup instead of silently serving 404s.
    """
    candidate = webui_dir or os.environ.get("GPTME_WEBUI_DIR")
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_dir():
            raise FileNotFoundError(
                f"webui_dir does not exist or is not a directory: {path}"
            )
        return path
    # Prefer the bundled modern webui when it has been populated.
    if _bundled_webui_path.is_dir() and any(_bundled_webui_path.iterdir()):
        logger.debug("Serving bundled modern webui from %s", _bundled_webui_path)
        return _bundled_webui_path
    return static_path


def create_app(
    cors_origin: str | None = None,
    host: str = "127.0.0.1",
    webui_dir: str | Path | None = None,
) -> flask.Flask:
    """Create the Flask app.

    Args:
        cors_origin: CORS origin(s) to allow. Use '*' to allow all origins.
            A comma-separated string allows multiple origins, e.g.
            "tauri://localhost,http://tauri.localhost". Whitespace around
            entries is ignored.
        webui_dir: Optional directory containing a web UI build (e.g. the
            modern React webui's ``dist/``) to serve instead of the bundled
            legacy UI. Falls back to the ``GPTME_WEBUI_DIR`` environment
            variable, then to the embedded legacy static bundle.
    """
    static_folder = _resolve_static_folder(webui_dir)
    app = flask.Flask(__name__, static_folder=static_folder)

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

    # Track whether we're serving a custom webui build (not the legacy bundle).
    # Used below to gate SPA-specific route behaviour.
    is_custom_webui = static_folder != static_path

    # Register static file routes directly on the app
    @app.route("/")
    def root():
        return app.send_static_file("index.html")

    @app.route("/computer")
    def computer():
        # Legacy bundle ships computer.html; a custom React build does not —
        # fall back to index.html and let client-side routing take over.
        if is_custom_webui or not (static_folder / "computer.html").exists():
            return app.send_static_file("index.html")
        return app.send_static_file("computer.html")

    @app.route("/chat")
    def chat():
        return app.send_static_file("index.html")

    @app.route("/favicon.png")
    def favicon():
        return flask.send_from_directory(media_path, "logo.png")

    if is_custom_webui:
        # SPA catch-all: serve any unknown path as index.html so that React
        # Router deep-links (/settings, /conversations/xyz, …) work correctly.
        # Actual static assets (JS/CSS/images) are served first because their
        # paths exist in static_folder; only truly unknown paths fall through.
        @app.route("/<path:path>")
        def spa_fallback(path: str):
            asset = static_folder / path
            if asset.is_file():
                return app.send_static_file(path)
            return app.send_static_file("index.html")

    # Server confirmation hook is now registered via init_hooks(server=True)
    # in server/cli.py

    return app
