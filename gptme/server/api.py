"""
Serve web UI and API for the application.

See here for instructions how to serve matplotlib figures:
 - https://matplotlib.org/stable/gallery/user_interfaces/web_application_server_sgskip.html
"""

import atexit
import io
import logging
import os
from collections.abc import Generator
from contextlib import redirect_stdout
from datetime import datetime
from importlib import resources
from itertools import islice
from pathlib import Path

import flask
from flask import current_app, request
from flask_cors import CORS

from ..commands import execute_cmd
from ..dirs import get_logs_dir
from ..llm import _stream
from ..llm.models import get_default_model
from ..logmanager import LogManager, get_user_conversations, prepare_messages
from ..message import Message
from ..tools import ToolUse, execute_msg, init_tools

logger = logging.getLogger(__name__)

api = flask.Blueprint("api", __name__)


@api.route("/api")
def api_root():
    return flask.jsonify({"message": "Hello World!"})


@api.route("/api/conversations")
def api_conversations():
    limit = int(request.args.get("limit", 100))
    conversations = list(islice(get_user_conversations(), limit))
    return flask.jsonify(conversations)


@api.route("/api/conversations/<string:logfile>")
def api_conversation(logfile: str):
    """Get a conversation."""
    init_tools(None)  # FIXME: this is not thread-safe
    log = LogManager.load(logfile, lock=False)
    log_dict = log.to_dict(branches=True)
    # make all paths absolute or relative to workspace (no "../")
    for msg in log_dict["log"]:
        if files := msg.get("files"):
            msg["files"] = [
                (
                    str(path.relative_to(log.workspace))
                    if (path := Path(f).resolve()).is_relative_to(log.workspace)
                    else str(path)
                )
                for f in files
            ]
    return flask.jsonify(log_dict)


@api.route("/api/conversations/<string:logfile>/files/<path:filename>")
def api_conversation_file(logfile: str, filename: str):
    """
    Get a file from a conversation, path must be absolute or relative to workspace.
    Can only access files in the workspace.
    """
    log = LogManager.load(logfile, lock=False)
    workspace = Path(log.workspace).resolve()

    # Can be set to override workspace restriction
    allow_root = os.getenv("GPTME_ALLOW_ROOT_FILES", "").lower() in ["1", "true"]

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
        return flask.jsonify({"error": str(e)}), 403


@api.route("/api/conversations/<string:logfile>", methods=["PUT"])
def api_conversation_put(logfile: str):
    """Create or update a conversation."""
    msgs = []
    req_json = flask.request.json
    if req_json and "messages" in req_json:
        for msg in req_json["messages"]:
            timestamp: datetime = datetime.fromisoformat(msg["timestamp"])
            msgs.append(Message(msg["role"], msg["content"], timestamp=timestamp))

    logdir = get_logs_dir() / logfile
    if logdir.exists():
        raise ValueError(f"Conversation already exists: {logdir.name}")
    logdir.mkdir(parents=True)
    log = LogManager(msgs, logdir=logdir)
    log.write()
    return {"status": "ok"}


@api.route(
    "/api/conversations/<string:logfile>",
    methods=["POST"],
)
def api_conversation_post(logfile: str):
    """Post a message to the conversation."""
    req_json = flask.request.json
    branch = (req_json or {}).get("branch", "main")
    tool_allowlist = (req_json or {}).get("tools", None)
    init_tools(tool_allowlist)  # FIXME: this is not thread-safe
    log = LogManager.load(logfile, branch=branch)
    assert req_json
    assert "role" in req_json
    assert "content" in req_json
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
def api_conversation_generate(logfile: str):
    # get model or use server default
    req_json = flask.request.json or {}
    stream = req_json.get("stream", False)  # Default to no streaming (backward compat)
    default_model = get_default_model()
    assert (
        default_model is not None
    ), "No model loaded and no model specified in request"
    model = req_json.get("model", default_model.full)

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
            return flask.jsonify({"error": str(e)})

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
                yield f"data: {flask.json.dumps({'role': reply_msg.role, 'content': reply_msg.content, 'stored': True})}\n\n"

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
            yield f"data: {flask.json.dumps({'error': str(e)})}\n\n"
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


def create_app(cors_origin: str | None = None) -> flask.Flask:
    """Create the Flask app.

    Args:
        cors_origin: CORS origin to allow. Use '*' to allow all origins.
    """
    app = flask.Flask(__name__, static_folder=static_path)
    app.register_blueprint(api)

    # Register v2 API
    # noreorder
    from .api_v2 import v2_api  # fmt: skip

    app.register_blueprint(v2_api)

    if cors_origin:
        CORS(app, resources={r"/api/*": {"origins": cors_origin}})

    return app
