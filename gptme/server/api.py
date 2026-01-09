"""
Serve web UI and API for the application.

See here for instructions how to serve matplotlib figures:
 - https://matplotlib.org/stable/gallery/user_interfaces/web_application_server_sgskip.html
"""

import atexit
import io
import logging
from collections.abc import Generator
from contextlib import redirect_stdout
from datetime import datetime
from importlib import resources
from itertools import islice
from pathlib import Path

import flask
from dateutil.parser import isoparse
from flask import current_app, request
from flask_cors import CORS

from ..commands import execute_cmd
from ..config import get_config
from ..dirs import get_logs_dir
from ..llm import _stream
from ..llm.models import get_default_model
from ..logmanager import LogManager, get_user_conversations, prepare_messages
from ..message import Message
from ..tools import ToolUse, execute_msg, init_tools
from .auth import require_auth
from .openapi_docs import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    MessageCreateRequest,
    StatusResponse,
    api_doc,
    api_doc_simple,
)

logger = logging.getLogger(__name__)


def _is_debug_errors_enabled() -> bool:
    """Check if detailed error messages should be shown.

    When GPTME_DEBUG_ERRORS is set to '1', 'true', or 'yes' (case-insensitive),
    detailed error messages with exception information will be returned to clients.
    This is useful for development, testing, CI, and staging environments.

    In production, this should be disabled (default) to prevent information leakage.
    """
    import os

    return os.environ.get("GPTME_DEBUG_ERRORS", "").lower() in ("1", "true", "yes")


api = flask.Blueprint("api", __name__)


@api.route("/api")
@api_doc()  # All information will be auto-inferred
def api_root():
    """API root endpoint.

    Get basic API information and verify the server is running.
    """
    return flask.jsonify({"message": "Hello World!"})


@api.route("/api/conversations")
@api_doc_simple(
    responses={200: ConversationListResponse},
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "default": 100},
            "description": "Maximum number of conversations to return",
        }
    ],
    tags=["conversation"],
)
@require_auth
def api_conversations():
    """List conversations.

    Get a list of user conversations with metadata, optionally limited by count.
    """
    limit = int(request.args.get("limit", 100))
    conversations = list(islice(get_user_conversations(), limit))
    return flask.jsonify(conversations)


def _abs_to_rel_workspace(path: str | Path, workspace: Path) -> str:
    """Convert an absolute path to a relative path."""
    path = Path(path).resolve()
    if path.is_relative_to(workspace):
        return str(path.relative_to(workspace))
    return str(path)


@api.route("/api/conversations/<string:logfile>")
@api_doc_simple(
    responses={200: ConversationResponse, 404: ErrorResponse, 403: ErrorResponse}
)
@require_auth
def api_conversation(logfile: str):
    """Get conversation.

    Retrieve a conversation with all its messages and metadata.
    """
    init_tools(None)
    log = LogManager.load(logfile, lock=False)
    log_dict = log.to_dict(branches=True)
    # add workspace to response
    log_dict["workspace"] = str(log.workspace)
    # make all paths absolute or relative to workspace (no "../")
    for msg in log_dict["log"]:
        if files := msg.get("files"):
            msg["files"] = [_abs_to_rel_workspace(f, log.workspace) for f in files]
    return flask.jsonify(log_dict)


@api.route("/api/conversations/<string:logfile>/files/<path:filename>")
@api_doc_simple(
    responses={200: None, 403: ErrorResponse, 404: ErrorResponse},
)
@require_auth
def api_conversation_file(logfile: str, filename: str):
    """Get conversation file.

    Download a file from a conversation's workspace.
    Can only access files in the workspace.
    """
    log = LogManager.load(logfile, lock=False)
    workspace = Path(log.workspace).resolve()

    # Can be set to override workspace restriction
    allow_root = get_config().get_env_bool("GPTME_ALLOW_ROOT_FILES")

    # Resolve the full path, ensuring it stays within workspace
    try:
        if (workspace / filename).resolve().is_file():
            return flask.send_from_directory(workspace, filename)
        # NOTE: <path:filename> strips leading slashes, so we need to re-add them
        elif (path := Path("/") / filename).is_file():
            if not allow_root:
                raise ValueError("Access denied: Path outside workspace")
            return flask.send_file(path)
        else:
            return flask.jsonify({"error": "File not found"}), 404
    except (ValueError, RuntimeError) as e:
        logger.exception("Error accessing conversation file")
        error_msg = str(e) if _is_debug_errors_enabled() else "Access denied"
        return flask.jsonify({"error": error_msg}), 403


@api.route("/api/conversations/<string:logfile>", methods=["PUT"])
@api_doc_simple(
    responses={200: StatusResponse, 400: ErrorResponse, 409: ErrorResponse},
    request_body=ConversationCreateRequest,
)
@require_auth
def api_conversation_put(logfile: str):
    """Create conversation.

    Create a new conversation with initial configuration and messages.
    The conversation will be stored with the specified logfile name.
    """
    from ..config import ChatConfig
    from ..prompts import get_prompt
    from ..tools import get_toolchain

    logdir = get_logs_dir() / logfile
    if logdir.exists():
        raise ValueError(f"Conversation already exists: {logdir.name}")

    req_json = flask.request.json or {}

    # Load or create chat config
    config_dict = req_json.get("config", {})
    config_dict["_logdir"] = logdir  # Pass logdir for "@log" workspace resolution
    request_config = ChatConfig.from_dict(config_dict)
    chat_config = ChatConfig.load_or_create(logdir, request_config)
    prompt = req_json.get("prompt", "full")

    # Start with system messages
    msgs = get_prompt(
        tools=[t for t in get_toolchain(chat_config.tools)],
        interactive=chat_config.interactive,
        tool_format=chat_config.tool_format or "markdown",
        model=chat_config.model,
        prompt=prompt,
        workspace=chat_config.workspace,
        agent_path=chat_config.agent,
    )

    # Add any additional messages from request
    valid_roles = ("system", "user", "assistant")
    for msg in req_json.get("messages", []):
        if msg.get("role") not in valid_roles:
            return (
                flask.jsonify(
                    {"error": f"Invalid role: {msg.get('role')}. Must be one of: {valid_roles}"}
                ),
                400,
            )
        timestamp: datetime = (
            isoparse(msg["timestamp"]) if "timestamp" in msg else datetime.now()
        )
        msgs.append(Message(msg["role"], msg["content"], timestamp=timestamp))

    logdir.mkdir(parents=True)
    log = LogManager(msgs, logdir=logdir)
    log.write()

    # Set tool allowlist to available tools if not provided
    if not chat_config.tools:
        chat_config.tools = [t.name for t in get_toolchain(None) if not t.is_mcp]

    if not chat_config.mcp:
        # load from user or project config
        from ..config import Config

        config = Config.from_workspace(chat_config.workspace)
        chat_config.mcp = config.mcp

    # Save the chat config
    chat_config.save()

    return {"status": "ok"}


@api.route(
    "/api/conversations/<string:logfile>",
    methods=["POST"],
)
@api_doc_simple(
    request_body=MessageCreateRequest,
    responses={200: StatusResponse, 400: ErrorResponse, 404: ErrorResponse},
)
@require_auth
def api_conversation_post(logfile: str):
    """Add message to conversation.

    Add a new message to an existing conversation.
    """
    req_json = flask.request.json
    # Validate request body (use proper checks, not assert which can be disabled with -O)
    if not req_json:
        return flask.jsonify({"error": "No JSON data provided"}), 400
    if "role" not in req_json or "content" not in req_json:
        return flask.jsonify({"error": "Missing required fields (role, content)"}), 400

    # Validate role against allowed values
    valid_roles = ("system", "user", "assistant")
    if req_json["role"] not in valid_roles:
        return (
            flask.jsonify(
                {"error": f"Invalid role: {req_json['role']}. Must be one of: {valid_roles}"}
            ),
            400,
        )

    branch = req_json.get("branch", "main")
    tool_allowlist = req_json.get("tools", None)
    init_tools(tool_allowlist)
    log = LogManager.load(logfile, branch=branch)
    msg = Message(
        req_json["role"], req_json["content"], files=req_json.get("files", [])
    )
    log.append(msg)
    del log  # close the file
    return {"status": "ok"}


# TODO: add support for confirmation
def confirm_func(msg: str) -> bool:
    return True


# generate response
@api.route("/api/conversations/<string:logfile>/generate", methods=["POST"])
@api_doc_simple(
    request_body=GenerateRequest,
    responses={200: GenerateResponse, 400: ErrorResponse, 500: ErrorResponse},
)
@require_auth
def api_conversation_generate(logfile: str):
    """Generate response.

    Generate an AI response in the conversation, with optional streaming.
    """
    # get model or use server default
    req_json = flask.request.json or {}
    stream = req_json.get("stream", False)  # Default to no streaming (backward compat)
    default_model = get_default_model()
    # Get model from request first, fall back to default
    model = req_json.get("model")
    if model is None:
        # No model in request, use default
        if default_model is None:
            return flask.jsonify({"error": "No model available (none loaded and none specified in request)"}), 500
        model = default_model.full

    # load conversation
    # NOTE: we load without lock since otherwise we have issues with
    # re-entering for follow-up generate requests which may still keep the manager.
    manager = LogManager.load(
        logfile,
        branch=req_json.get("branch", "main"),
        lock=False,
    )

    # performs reduction/context trimming, if necessary
    msgs = prepare_messages(manager.log.messages)

    if not msgs:
        logger.error("No messages to process")
        return flask.jsonify({"error": "No messages to process"})

    if not stream:
        # Non-streaming response
        try:
            # Get complete response
            output = "".join(_stream(msgs, model, tools=None))

            # Store the message
            msg = Message("assistant", output)
            msg = msg.replace(quiet=True)
            manager.append(msg)

            # Execute any tools
            reply_msgs = list(execute_msg(msg, confirm_func))
            for reply_msg in reply_msgs:
                manager.append(reply_msg)

            # Return all messages
            response = [{"role": "assistant", "content": output, "stored": True}]
            response.extend(
                {"role": msg.role, "content": msg.content, "stored": True}
                for msg in reply_msgs
            )
            return flask.jsonify(response)

        except Exception as e:
            logger.exception("Error during generation")
            error_msg = str(e) if _is_debug_errors_enabled() else "An internal error occurred during generation"
            return flask.jsonify({"error": error_msg}), 500

    # Streaming response
    def generate() -> Generator[str, None, None]:
        # Start with an empty message
        output = ""
        try:
            logger.info(f"Starting generation for conversation {logfile}")

            # Prepare messages for the model
            if not msgs:
                logger.error("No messages to process")
                yield f"data: {flask.json.dumps({'error': 'No messages to process'})}\n\n"
                return

            # if prompt is a user-command, execute it
            last_msg = manager.log[-1]
            if last_msg.role == "user" and last_msg.content.startswith("/"):
                f = io.StringIO()
                print("Begin capturing stdout, to pass along command output.")
                with redirect_stdout(f):
                    resp = execute_cmd(manager.log[-1], manager, confirm_func)
                print("Done capturing stdout.")
                output = f.getvalue().strip()
                if resp and output:
                    print(f"Replying with command output: {output}")
                    manager.write()
                    yield f"data: {flask.json.dumps({'role': 'system', 'content': output, 'stored': False})}\n\n"
                    return

            # Stream tokens from the model
            logger.debug(f"Starting token stream with model {model}")
            for char in (
                char for chunk in _stream(msgs, model, tools=None) for char in chunk
            ):
                output += char
                # Send each token as a JSON event
                yield f"data: {flask.json.dumps({'role': 'assistant', 'content': char, 'stored': False})}\n\n"

                # Check for complete tool uses
                tooluses = list(ToolUse.iter_from_content(output))
                if tooluses and any(tooluse.is_runnable for tooluse in tooluses):
                    logger.debug("Found runnable tool use, breaking stream")
                    break

            # Store the complete message
            logger.debug(f"Storing complete message: {output[:100]}...")
            msg = Message("assistant", output)
            msg = msg.replace(quiet=True)
            manager.append(msg)
            yield f"data: {flask.json.dumps({'role': 'assistant', 'content': output, 'stored': True})}\n\n"

            # Execute any tools and stream their output
            tool_replies = list(execute_msg(msg, confirm_func))
            for reply_msg in tool_replies:
                logger.debug(
                    f"Tool output: {reply_msg.role} - {reply_msg.content[:100]}..."
                )
                manager.append(reply_msg)
                # Include files in the streamed response if present
                response_data = {
                    "role": reply_msg.role,
                    "content": reply_msg.content,
                    "files": [
                        _abs_to_rel_workspace(path, manager.workspace)
                        for path in reply_msg.files
                    ],
                    "stored": True,
                }
                yield f"data: {flask.json.dumps(response_data)}\n\n"

            # Check if we need to continue generating
            if tool_replies and any(
                tooluse.is_runnable
                for tooluse in ToolUse.iter_from_content(msg.content)
            ):
                # Generate new response after tool execution
                output = ""
                for char in (
                    char
                    for chunk in _stream(
                        prepare_messages(manager.log.messages), model, tools=None
                    )
                    for char in chunk
                ):
                    output += char
                    yield f"data: {flask.json.dumps({'role': 'assistant', 'content': char, 'stored': False})}\n\n"

                    # Check for complete tool uses
                    tooluses = list(ToolUse.iter_from_content(output))
                    if tooluses and any(tooluse.is_runnable for tooluse in tooluses):
                        break

                # Store the complete message
                msg = Message("assistant", output)
                msg = msg.replace(quiet=True)
                manager.append(msg)
                yield f"data: {flask.json.dumps({'role': 'assistant', 'content': output, 'stored': True})}\n\n"

                # Recursively handle any new tool uses
                if any(
                    tooluse.is_runnable for tooluse in ToolUse.iter_from_content(output)
                ):
                    yield from generate()

        except GeneratorExit:
            logger.info("Client disconnected during generation, interrupting")
            if output:
                output += "\n\n[interrupted]"
                msg = Message("assistant", output)
                msg = msg.replace(quiet=True)
                manager.append(msg)
            raise
        except Exception as e:
            logger.exception("Error during generation")
            error_msg = str(e) if _is_debug_errors_enabled() else "An internal error occurred during generation"
            yield f"data: {flask.json.dumps({'error': error_msg})}\n\n"
        finally:
            logger.info("Generation completed")

    return flask.Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable buffering in nginx
        },
    )


gptme_path_ctx = resources.as_file(resources.files("gptme"))
root_path = gptme_path_ctx.__enter__()
static_path = root_path / "server" / "static"
media_path = root_path.parent / "media"
atexit.register(gptme_path_ctx.__exit__, None, None, None)


# serve index.html from the root
@api.route("/")
def root():
    return current_app.send_static_file("index.html")


# serve computer interface
@api.route("/computer")
def computer():
    return current_app.send_static_file("computer.html")


# serve chat interface (for embedding in computer view)
@api.route("/chat")
def chat():
    return current_app.send_static_file("index.html")


@api.route("/favicon.png")
def favicon():
    return flask.send_from_directory(media_path, "logo.png")


def create_app(cors_origin: str | None = None, host: str = "127.0.0.1") -> flask.Flask:
    """Create the Flask app.

    Args:
        cors_origin: CORS origin to allow. Use '*' to allow all origins.
    """
    app = flask.Flask(__name__, static_folder=static_path)
    app.register_blueprint(api)

    # Register v2 API, workspace API, and tasks API
    # noreorder
    from .api_v2 import v2_api  # fmt: skip
    from .tasks_api import tasks_api  # fmt: skip
    from .workspace_api import workspace_api  # fmt: skip

    app.register_blueprint(v2_api)
    app.register_blueprint(workspace_api)
    app.register_blueprint(tasks_api)

    # Register OpenAPI documentation
    from .openapi_docs import docs_api  # fmt: skip

    app.register_blueprint(docs_api)
    logger.info("OpenAPI documentation available at /api/docs/")

    if cors_origin:
        # Only allow credentials if a specific origin is set (not '*')
        allow_credentials = cors_origin != "*" if cors_origin else False
        CORS(
            app,
            resources={
                r"/api/*": {
                    "origins": cors_origin,
                    "supports_credentials": allow_credentials,
                }
            },
        )

    # Initialize auth (defaults to local-only, no auth required)
    from .auth import init_auth  # fmt: skip

    init_auth(host=host, display=False)

    return app
