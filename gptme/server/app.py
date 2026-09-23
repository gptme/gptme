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
# The computer-use VNC view, served when no modern web UI provides its own.
_computer_view_path = _root_path / "server" / "computer_view"
# Bundled modern webui (populated by `make bundle-webui` or the release workflow)
_bundled_webui_path = _root_path / "server" / "webui-dist"
media_path = _root_path.parent / "media"
atexit.register(_gptme_path_ctx.__exit__, None, None, None)


_WEBUI_MISSING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>gptme — web UI not bundled</title>
<style>
  :root { color-scheme: light dark; --fg: #1a1a1a; --muted: #5c5c5c; --bg: #fdfdfc;
          --card: #fff; --border: #e4e4e0; --accent: #5151f5; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #ececec; --muted: #a0a0a0; --bg: #16161a; --card: #1e1e24;
            --border: #2e2e36; --accent: #8f8fff; }
  }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: var(--bg); color: var(--fg);
         font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, sans-serif; }
  main { max-width: 32rem; margin: 2rem; padding: 2rem; background: var(--card);
         border: 1px solid var(--border); border-radius: 12px; }
  h1 { margin: 0 0 .5rem; font-size: 1.25rem; }
  p { margin: 0 0 1rem; color: var(--muted); }
  code { background: color-mix(in srgb, var(--fg) 8%, transparent);
         padding: .15em .4em; border-radius: 4px; font-size: .9em; }
  ul { margin: 0 0 1rem; padding-left: 1.2rem; }
  li { margin-bottom: .5rem; }
  a { color: var(--accent); }
</style>
</head>
<body>
<main>
  <h1>The web UI is not bundled in this install</h1>
  <p>The API is running normally at <code>/api</code> — only the browser
     interface is missing. This usually means you are running from a source
     checkout.</p>
  <ul>
    <li>Build and bundle it, from the repository root:
        <code>(cd webui &amp;&amp; npm run build) &amp;&amp; make bundle-webui</code></li>
    <li>Or point the server at an existing build:
        <code>GPTME_WEBUI_DIR=/path/to/dist</code></li>
    <li>Or install a release package, which ships the UI already built</li>
  </ul>
  <p><a href="https://gptme.org/docs/webui.html">Web UI documentation</a> ·
     <a href="https://gptme.org/docs/server.html">Server documentation</a></p>
</main>
</body>
</html>
"""


def webui_missing_response() -> flask.Response:
    """Explain how to get a web UI, instead of serving a stand-in interface."""
    return flask.Response(
        _WEBUI_MISSING_PAGE, status=503, content_type="text/html; charset=utf-8"
    )


def _resolve_static_folder(webui_dir: str | Path | None = None) -> Path | None:
    """Resolve which directory the web UI is served from.

    Precedence: explicit ``webui_dir`` argument > ``GPTME_WEBUI_DIR`` env var >
    bundled modern webui (``gptme/server/webui-dist/``). Returns ``None`` when
    no web UI is available, in which case the UI routes explain how to get one
    and the API keeps working. A configured directory must exist so that a typo
    fails loudly at startup instead of silently serving 404s.
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
    logger.warning(
        "No web UI bundled; serving API only. "
        "Run `make bundle-webui` or set GPTME_WEBUI_DIR to serve the web UI."
    )
    return None


def create_app(
    cors_origin: str | None = None,
    host: str = "127.0.0.1",
    webui_dir: str | Path | None = None,
    default_profile: str | None = None,
    allowed_hosts: list[str] | None = None,
) -> flask.Flask:
    """Create the Flask app.

    Args:
        cors_origin: CORS origin(s) to allow. Use '*' to allow all origins.
            A comma-separated string allows multiple origins, e.g.
            "tauri://localhost,http://tauri.localhost". Whitespace around
            entries is ignored.
        allowed_hosts: Extra hostnames to accept in the Host header when auth is
            explicitly disabled. Adds to the built-in
            localhost/127.0.0.1/[::1] allow-list. See init_host_validation.
        webui_dir: Optional directory containing a web UI build to serve
            instead of the bundled modern UI. Falls back to the
            ``GPTME_WEBUI_DIR`` environment variable, then to the bundled UI
            and finally the embedded legacy static fallback.
        default_profile: Optional profile name to apply to new conversations
            that don't specify a system prompt. The profile's system_prompt is
            injected as an additional system message when the conversation is
            created. Useful for specialized deployments (e.g. the computer-use
            Docker container) where every session should use a specific
            backend-selection policy without requiring the client to set it.
    """
    static_folder = _resolve_static_folder(webui_dir)
    app = flask.Flask(__name__, static_folder=static_folder)
    webui_available = static_folder is not None

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
    from .session_models import SessionManager

    # Always refresh the process-wide capture, including clearing it when
    # this app has no default. A later create_app() in the same process
    # (tests, embedded servers) must not inherit a stale previous model.
    if server_default_model:
        app.config["SERVER_DEFAULT_MODEL"] = server_default_model
        SessionManager.set_server_default_model(server_default_model.full)

        @app.before_request
        def propagate_default_model():
            """Propagate the server's default model to each request's ContextVar."""
            # Only set if not already set in this context
            if get_default_model() is None:
                set_default_model(server_default_model)
    else:
        app.config.pop("SERVER_DEFAULT_MODEL", None)
        SessionManager.set_server_default_model(None)

    # Register v2 API, workspace API, tasks API, and auth API
    # noreorder
    from .a2a_api import a2a_api  # fmt: skip
    from .api_v2 import v2_api  # fmt: skip
    from .artifacts_api import artifacts_api  # fmt: skip
    from .auth import auth_api  # fmt: skip
    from .computer_api import computer_api  # fmt: skip
    from .panels_api import panels_api  # fmt: skip
    from .preview_proxy_api import preview_proxy_api  # fmt: skip
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
    app.register_blueprint(preview_proxy_api)

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

    # Initialize auth (required by default for every bind address).
    from .auth import init_auth, init_host_validation, validate_host  # fmt: skip

    init_auth(host=host, display=False)

    # Configure and register optional Host-header validation for deployments that
    # explicitly disable bearer auth; see init_host_validation.
    # Registered as a before_request hook so it runs ahead of any route/auth.
    init_host_validation(host=host, allowed_hosts=allowed_hosts)
    app.before_request(validate_host)

    # Register Prometheus metrics middleware and /api/v0/metrics endpoint
    from .metrics import init_metrics  # fmt: skip

    init_metrics(app)

    # Register static file routes directly on the app
    @app.route("/")
    @app.route("/chat")
    def root():
        if not webui_available:
            return webui_missing_response()
        return app.send_static_file("index.html")

    @app.route("/computer")
    def computer():
        # A modern build ships its own computer-use view behind client-side
        # routing; without one, serve the standalone VNC page directly so the
        # computer-use container keeps working.
        if webui_available:
            return app.send_static_file("index.html")
        return flask.send_from_directory(_computer_view_path, "computer.html")

    @app.route("/favicon.png")
    def favicon():
        return flask.send_from_directory(media_path, "logo.png")

    if webui_available:
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
            assert static_folder is not None  # guarded by webui_available
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
