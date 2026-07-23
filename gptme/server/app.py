"""
Flask application factory for gptme server.
"""

import atexit
import logging
import os
from importlib import resources
from pathlib import Path

import flask
from flask_compress import Compress
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
    default_profile: str | None = None,
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
        default_profile: Optional profile name to apply to new conversations
            that don't specify a system prompt. The profile's system_prompt is
            injected as an additional system message when the conversation is
            created. Useful for specialized deployments (e.g. the computer-use
            Docker container) where every session should use a specific
            backend-selection policy without requiring the client to set it.
    """
    static_folder = _resolve_static_folder(webui_dir)
    app = flask.Flask(__name__, static_folder=static_folder)

    # Enable gzip compression on API responses (reduces bandwidth ~5-10x for JSON).
    # Compresses responses >= MIN_SIZE (default 500 bytes) for clients that send
    # Accept-Encoding: gzip. Flask-Compress handles content negotiation and
    # varies compression level on content-type.
    Compress(app)

    # Store server-level default profile so the conversation PUT endpoint can
    # inject the profile's system prompt when the client doesn't set one.
    if default_profile is not None:
        app.config["SERVER_DEFAULT_PROFILE"] = default_profile

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
    from .a2a_api import a2a_api  # fmt: skip
    from .api_v2 import v2_api  # fmt: skip
    from .artifacts_api import artifacts_api  # fmt: skip
    from .auth import auth_api  # fmt: skip
    from .computer_api import computer_api  # fmt: skip
    from .panels_api import panels_api  # fmt: skip
    from .skills_api import skills_api  # fmt: skip
    from .tasks_api import tasks_api  # fmt: skip
    from .tools_api import tools_api  # fmt: skip
    from .tts_api import tts_api  # fmt: skip
    from .workspace_api import workspace_api  # fmt: skip

    app.register_blueprint(a2a_api)
    app.register_blueprint(v2_api)
    app.register_blueprint(auth_api)
    app.register_blueprint(workspace_api)
    app.register_blueprint(tasks_api)
    app.register_blueprint(skills_api)
    app.register_blueprint(artifacts_api)
    app.register_blueprint(panels_api)
    app.register_blueprint(tools_api)
    app.register_blueprint(tts_api)
    app.register_blueprint(computer_api)

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
            # Similarly, wildcard CORS must not opt in to Private Network
            # Access — doing so lets *any* HTTPS page reach the local server,
            # defeating Chrome's PNA protection. Both flags require named origins.
            non_wildcard = "*" not in origins_list
            allow_credentials = non_wildcard
            CORS(
                app,
                resources={
                    r"/api/*": {
                        "origins": origins,
                        "supports_credentials": allow_credentials,
                        # Allow public-origin -> loopback requests. Chrome's
                        # Private Network Access policy blocks a secure public
                        # origin (e.g. https://chat.gptme.org) from fetching a
                        # local server (http://127.0.0.1:5700) unless the
                        # preflight response carries
                        # Access-Control-Allow-Private-Network: true. Passing
                        # a named --cors-origin is the user's opt-in for
                        # exactly that hosted-webui -> local-server workflow.
                        # Wildcard origins are excluded: any HTTPS page would
                        # then be able to reach loopback, bypassing PNA.
                        "allow_private_network": non_wildcard,
                    }
                },
            )

    # Initialize auth (defaults to local-only, no auth required)
    from .auth import init_auth, init_host_validation, validate_host_header  # fmt: skip

    init_auth(host=host, display=False)

    # DNS-rebinding protection: validate Host header on all /api/ requests.
    # CORS preflights block cross-origin requests from normal websites, but
    # DNS rebinding bypasses CORS: the attacker page rebinds its domain to
    # 127.0.0.1 and becomes same-origin with the local server, skipping
    # preflight entirely.  Checking the Host header stops this because the
    # rebinding page still sends its original domain name as Host.
    init_host_validation(bind_host=host)

    @app.before_request
    def _check_host():
        if flask.request.path.startswith("/api/"):
            return validate_host_header()

    # Register Prometheus metrics middleware and /api/v0/metrics endpoint
    from .metrics import init_metrics  # fmt: skip

    init_metrics(app)

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
        # API paths that reach here (unknown routes, bad URL segments) get a
        # JSON 404 so clients don't receive index.html as an API response.
        @app.route("/<path:path>")
        def spa_fallback(path: str):
            if path.startswith("api/"):
                return flask.Response(
                    response=flask.json.dumps({"error": "Not Found"}),
                    status=404,
                    content_type="application/json",
                )
            asset = static_folder / path
            if asset.is_file():
                return app.send_static_file(path)
            return app.send_static_file("index.html")

    # Register JSON error handlers so all API errors return JSON instead of HTML.
    # Without these, Flask's default handlers return HTML for 404/405/500 etc.,
    # which breaks webui clients that expect JSON from every /api/* response.
    from werkzeug.exceptions import HTTPException  # fmt: skip

    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException) -> flask.Response:
        return flask.Response(
            response=flask.json.dumps({"error": e.description}),
            status=e.code,
            content_type="application/json",
        )

    # Server confirmation hook is now registered via init_hooks(server=True)
    # in server/cli.py

    return app
